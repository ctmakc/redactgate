export const meta = {
  name: 'redactgate-security-audit',
  description: 'Adversarial security + correctness audit of RedactGate: fan out reviewers per attack dimension, then adversarially verify each finding',
  phases: [
    { title: 'Audit', detail: 'leakage, crypto, authz, audit-integrity, streaming, dos, adapters, correctness' },
    { title: 'Verify', detail: 'adversarially confirm or refute each finding against the real code' },
  ],
}

const ROOT = '/data/projects/redactgate'
const PY = `${ROOT}/.venv/bin/python`

const BASE = `You are a senior security/correctness reviewer auditing RedactGate — a self-hosted PII-redaction FIREWALL that sits between staff and cloud LLMs. Threat model: the WHOLE point is that raw PII (SIN, IBAN, email, card, names…) must NEVER reach the upstream LLM, the reversible vault must be sound, the audit log must be tamper-evident and contain zero raw values, and tenants must be isolated. A single redaction-bypass or audit-integrity hole is CRITICAL.

PROJECT ROOT: ${ROOT} (READ-ONLY — do NOT edit any file). Read the real code. A venv is at ${ROOT}/.venv; you MAY run ${PY} for a quick proof-of-concept repro, but never modify the repo. Read ${ROOT}/AGENTS.md for the module map.

Report ONLY genuine security or correctness defects (critical/high/medium) with a CONCRETE exploit or failing scenario and an exact file + line. Skip style/nits. For each finding give: title, severity, file, line, a precise description of the bug, a concrete exploit/repro (how an attacker or input triggers it), and a fix. Be specific — "could be unsafe" is not a finding; "input X reaches upstream un-redacted because Y at file:line" is. Your final message is consumed as structured data.`

const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    dimension: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        properties: {
          title: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          file: { type: 'string' },
          line: { type: 'string' },
          description: { type: 'string' },
          exploit: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['title', 'severity', 'file', 'line', 'description', 'exploit', 'fix'],
      },
    },
  },
  required: ['dimension', 'findings'],
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['real', 'false_positive', 'uncertain'] },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    reasoning: { type: 'string' },
    repro: { type: 'string', description: 'concrete reproduction or proof it is real, or why it is not' },
  },
  required: ['verdict', 'severity', 'reasoning'],
}

const DIMENSIONS = [
  {
    key: 'leakage',
    prompt: `${BASE}

DIMENSION — REDACTION BYPASS / PII LEAKAGE (the #1 risk). Find ANY path by which raw PII reaches the upstream provider un-redacted. Read app/schemas/openai.py (extract_texts/inject_texts), app/redaction/pipeline.py, app/redaction/detect.py, app/redaction/regex_packs.py, app/redaction/merge.py, app/redaction/vault.py, app/routes/proxy.py.
Hunt specifically for: text-bearing fields NOT walked by extract_texts (tool/function call arguments, tool results, 'name', nested content parts, system messages, the /v1/responses 'input' variants, assistant content, multi-part arrays); detection gaps where a real entity isn't caught and so isn't tokenized; span-offset bugs in tokenize (right-to-left replacement, overlapping spans) that corrupt or skip redaction; merge dropping a true entity so it leaks; unicode/normalization/zero-width tricks that evade regex; case where inject_texts reassembles wrong; a detected-but-not-redacted path (policy filter_spans removing a span that then leaks); and any place the ORIGINAL payload (not sanitized) could be forwarded.`,
  },
  {
    key: 'crypto',
    prompt: `${BASE}

DIMENSION — VAULT CRYPTO & KEY HANDLING. Read app/redaction/vault.py, app/redaction/store.py, app/redaction/pg_store.py, app/config.py.
Check: AES-GCM nonce generation/uniqueness (nonce reuse under same DEK = catastrophic); DEK derivation (HKDF salt=session_id — is session_id unique/unguessable? collision across sessions?); fingerprint HMAC (collision → wrong value restored? cross-session leakage?); placeholder allocation collision handling; whether a placeholder from session A can ever decrypt with session B's key; the dev-fallback keys in config.key_bytes (could prod accidentally use them?); whether raw values are ever logged or persisted in plaintext; ciphertext format (nonce||ct) parsing correctness.`,
  },
  {
    key: 'authz',
    prompt: `${BASE}

DIMENSION — AUTH, ADMIN GATING, TENANT ISOLATION, POLICY BYPASS. Read app/auth.py, app/deps.py, app/routes/admin.py, app/routes/proxy.py, app/redaction/policy.py.
Check: API key verification (argon2 — timing? iterating all keys? revoked keys honored?); the require_api_key=false default path (does it silently disable auth in prod?); admin endpoints gating on X-Admin-Token when admin_token is EMPTY (does empty token allow everyone in prod?); tenant isolation (can team A read team B's audit/policies/sessions? does /admin/* or /admin/audit filter by tenant at all?); policy hard_block bypass; provider allow-list bypass; whether auth failures leak info.`,
  },
  {
    key: 'audit',
    prompt: `${BASE}

DIMENSION — AUDIT INTEGRITY & ZERO-RAW-VALUE GUARANTEE. Read app/audit.py, app/routes/proxy.py, app/routes/admin.py, migrations/001_init.sql.
Check: hash-chain correctness (canonical_json determinism, prev_hash linkage, per-team chains, race between concurrent requests appending to the same team chain producing a forked/invalid chain); tamper-evidence (can a row be altered/deleted? the append-only trigger — does it cover TRUNCATE? bulk ops?); whether entity_counts or any audit field can ever contain a RAW value (e.g. an entity TYPE that is actually a value, or error messages embedding PII); the _safe_audit/_safe_audit_fresh swallow-all (does a failure silently skip auditing a request that DID leak-redact? acceptable?); whether prompt/response text is ever written to audit.`,
  },
  {
    key: 'streaming',
    prompt: `${BASE}

DIMENSION — STREAMING SSE RE-INFLATION & LIFECYCLE. Read app/routes/proxy.py (_stream_response), app/redaction/vault.py (StreamDetokenizer, stream_detokenizer_prepared, trailing_partial_len), app/redaction/placeholders.py.
Check: can a placeholder split across SSE chunks ever be emitted half-substituted (leaking part of a placeholder, or worse, failing to substitute so the upstream's echoed placeholder reaches the client — though that's not a PII leak, a real value leak is)? buffer bound (MAX_PLACEHOLDER_LEN) vs an adversarial stream that never closes a '[[' (infinite buffer growth = DoS)? the pre-resolved snapshot — does it ever miss a placeholder (so a real value fails to restore — correctness) or conversely is there any path where the UN-sanitized text streams? error path in the generator leaking? the flush() correctness.`,
  },
  {
    key: 'dos',
    prompt: `${BASE}

DIMENSION — ReDoS / INJECTION / RESOURCE EXHAUSTION / SSRF. Read app/packs/*.yaml, app/redaction/regex_packs.py, app/routes/proxy.py, app/routes/admin.py, app/gateway/*.py, app/redaction/llm_ner.py.
Check: catastrophic-backtracking regexes in the jurisdiction packs (craft an input that hangs detection); unbounded request body size (huge payload → memory/CPU blowup in detect/tokenize); SQL injection in admin audit search / policy create (are params bound?); SSRF via provider base_url or model fields (can a caller redirect the upstream call to an internal address?); header injection into upstream; the LLM-NER pass feeding attacker text to a model and trusting JSON back; per-request unbounded entity counts.`,
  },
  {
    key: 'adapters',
    prompt: `${BASE}

DIMENSION — PROVIDER ADAPTERS (error & leakage paths). Read app/gateway/base.py and every app/gateway/{openai,anthropic,gemini,ollama,azure,bedrock,do_genai}.py.
Check: error paths that echo the SANITIZED-or-RAW request back to the client or logs (could a ProviderError message embed the prompt?); whether any adapter forwards fields that bypass the sanitized payload; streaming chunk parsing that could desync; default-model resolution sending to an unintended model; missing timeout (hang); whether a non-200 upstream with a body containing the prompt is surfaced; auth header handling; the azure/bedrock 'not configured' paths.`,
  },
  {
    key: 'correctness',
    prompt: `${BASE}

DIMENSION — CORE CORRECTNESS & CONCURRENCY. Read app/redaction/vault.py (tokenize/detokenize), app/redaction/merge.py, app/redaction/pipeline.py, app/db.py, app/migrate.py, app/redaction/pg_store.py.
Check: referential-consistency edge cases (same value different type; overlapping entities of different types; a value that is a substring of another); merge resolver correctness on adjacent/nested/zero-length spans; tokenize right-to-left offset math with multi-byte chars; pg_store ON CONFLICT race under concurrent requests in one session; get_session commit/rollback interaction with the streaming fresh-session audit (double-write? lost write?); migration advisory-lock edge cases; idempotency; any await missing / coroutine-not-awaited; session expiry/purge (is expired vault data ever used or never purged?).`,
  },
]

function verifyPrompt(f, dim) {
  return `${BASE}

ADVERSARIALLY VERIFY this finding reported during the "${dim}" audit. Your DEFAULT is skepticism: mark false_positive unless you can show it is genuinely exploitable/wrong in the ACTUAL code. Read the cited file and surrounding code; if useful, run a quick ${PY} PoC (read-only). Then decide.

FINDING:
  title: ${f.title}
  claimed severity: ${f.severity}
  location: ${f.file}:${f.line}
  description: ${f.description}
  claimed exploit: ${f.exploit}
  proposed fix: ${f.fix}

Return verdict=real ONLY if the code truly behaves as claimed and it is a genuine security/correctness defect (give a concrete repro). verdict=false_positive if the code already handles it, the exploit doesn't actually work, or it's out of threat model. verdict=uncertain only if genuinely undecidable after real effort. Adjust severity to the true impact.`
}

// ── Orchestration: find (per dimension) → adversarially verify each finding ──
phase('Audit')
log(`Auditing ${DIMENSIONS.length} attack dimensions, then adversarially verifying each finding…`)

const results = await pipeline(
  DIMENSIONS,
  (d) => agent(d.prompt, { label: `audit:${d.key}`, phase: 'Audit', schema: FINDINGS_SCHEMA }),
  (review, d) => {
    const findings = (review && review.findings) || []
    if (!findings.length) return []
    return parallel(
      findings.map((f) => () =>
        agent(verifyPrompt(f, d.key), { label: `verify:${d.key}`, phase: 'Verify', schema: VERDICT_SCHEMA })
          .then((v) => ({ ...f, dimension: d.key, verdict: v }))
          .catch(() => null),
      ),
    )
  },
)

const all = results.flat().filter(Boolean)
const confirmed = all.filter((f) => f.verdict && f.verdict.verdict === 'real')
const uncertain = all.filter((f) => f.verdict && f.verdict.verdict === 'uncertain')
const sevRank = { critical: 0, high: 1, medium: 2, low: 3 }
confirmed.sort((a, b) => (sevRank[a.verdict.severity] ?? 9) - (sevRank[b.verdict.severity] ?? 9))

log(`Audit complete: ${all.length} raw findings → ${confirmed.length} CONFIRMED real, ${uncertain.length} uncertain.`)
log(`Confirmed by severity: ${['critical', 'high', 'medium', 'low'].map((s) => `${s}:${confirmed.filter((f) => f.verdict.severity === s).length}`).join('  ')}`)

return {
  confirmed: confirmed.map((f) => ({
    severity: f.verdict.severity, title: f.title, file: f.file, line: f.line,
    dimension: f.dimension, description: f.description, exploit: f.exploit, fix: f.fix,
    repro: f.verdict.repro, reasoning: f.verdict.reasoning,
  })),
  uncertain: uncertain.map((f) => ({ title: f.title, file: f.file, dimension: f.dimension, reasoning: f.verdict.reasoning })),
  totals: { raw: all.length, confirmed: confirmed.length, uncertain: uncertain.length },
}
