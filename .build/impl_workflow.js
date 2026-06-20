export const meta = {
  name: 'redactgate-implement',
  description: 'Implement RedactGate modules in parallel against the frozen contract spine, then author the test suite',
  phases: [
    { title: 'Implement', detail: 'gateway, detection, packs, vault, policy+audit, auth, pipeline+routes, worker+eval, web UI' },
    { title: 'Tests', detail: 'engine, packs+detect, policy+audit+auth, gateway+routes test suites' },
  ],
}

const ROOT = '/data/projects/redactgate'
const PY = `${ROOT}/.venv/bin/python`
const PYTEST = `${ROOT}/.venv/bin/pytest`
const RUFF = `${ROOT}/.venv/bin/ruff`

const PREAMBLE = `You are implementing ONE module group of RedactGate — a self-hosted PII/financial-redaction firewall (a FastAPI OpenAI-compatible proxy that detects, reversibly tokenizes, and audits sensitive entities before any cloud-LLM call, then re-inflates the answer).

PROJECT ROOT: ${ROOT}
A ready Python 3.12 venv with all deps is at ${ROOT}/.venv — ALWAYS use:
  - ${PY} for python
  - ${PYTEST} for pytest
  - ${RUFF} for ruff
NEVER create a new venv, never reinstall packages, never run pip.

STEP 1 — read these first (they are the contract): ${ROOT}/AGENTS.md (module map + interfaces), and the spine files it lists (app/config.py, app/schemas/openai.py, app/schemas/entities.py, app/redaction/placeholders.py, app/redaction/store.py, app/gateway/base.py, app/db.py, app/models.py).

HARD RULES:
- The spine files are FROZEN. Import from them; NEVER edit them.
- Write ONLY the files assigned to you below. Other agents are writing the other modules concurrently — do not touch their files (no edits outside your set).
- Python 3.12: start every module with \`from __future__ import annotations\`; full type hints; all I/O async; ruff-clean (line-length 100; rules E,F,I,UP,B).
- SECURITY: never log, print, or persist a raw entity value. Audit/metrics store entity TYPE COUNTS only.
- The regex-only detection path MUST work with zero optional dependencies (presidio/spacy may be absent).

STEP 3 — self-verify before returning: run \`${PY} -c "import <your.modules>"\` for each file and \`${RUFF} check <your files>\`; fix everything you can. Your final message is consumed as STRUCTURED DATA (not shown to a human) — return exactly the requested schema.`

const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    module: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    public_symbols: { type: 'array', items: { type: 'string' } },
    self_check: { type: 'string', description: 'result of import + ruff self-check' },
    issues: { type: 'string', description: "contract ambiguities/problems or 'none'" },
  },
  required: ['module', 'files_written', 'self_check', 'issues'],
}

const TEST_SCHEMA = {
  type: 'object', additionalProperties: false,
  properties: {
    suite: { type: 'string' },
    files_written: { type: 'array', items: { type: 'string' } },
    tests_passing: { type: 'integer' },
    tests_failing: { type: 'integer' },
    impl_bugs_found: { type: 'string', description: "describe any implementation bugs your tests revealed, or 'none'" },
    notes: { type: 'string' },
  },
  required: ['suite', 'files_written', 'tests_passing', 'tests_failing', 'impl_bugs_found'],
}

// ─────────────────────────────── Implementation agents ───────────────────────────────

const IMPL = [
  {
    key: 'gateway',
    prompt: `${PREAMBLE}

YOUR MODULE — Multi-provider gateway adapters. Files to write:
  app/gateway/anthropic.py, app/gateway/openai.py, app/gateway/gemini.py,
  app/gateway/ollama.py, app/gateway/azure.py, app/gateway/bedrock.py, app/gateway/do_genai.py

Each implements \`Provider\` (from app.gateway.base) and ends with
  register_provider("<name>", lambda s: <Cls>(s))
Input to complete()/stream() is a SANITIZED OpenAI chat payload dict (keys: model, messages, stream, temperature, max_tokens, ...). Output MUST be OpenAI-shaped:
  - async def complete(payload) -> dict        # full chat.completion dict: {id, object:"chat.completion", created:int(time.time()), model, choices:[{index,message:{role:"assistant",content},finish_reason}], usage:{prompt_tokens,completion_tokens,total_tokens}}
  - def stream(payload) -> AsyncIterator[dict]  # yields chat.completion.chunk dicts: {id,object:"chat.completion.chunk",created,model,choices:[{index,delta:{content},finish_reason}]}
Use httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds). Raise gateway.base.ProviderError(msg, status_code=..., provider=self.name) on HTTP/parse errors. If the request "model" is empty/generic, fall back to the provider's configured default model from settings; implement default_model().

Per-provider native translation:
  - openai.py & do_genai.py: OpenAI-compatible passthrough — POST {base_url}/chat/completions with Authorization: Bearer <key>; for stream, parse "data: {...}\\n\\n" SSE lines (skip "[DONE]") and yield parsed chunks. do_genai uses do_genai_* settings.
  - ollama.py: POST {ollama_base_url}/api/chat with {model,messages,stream}. Non-stream returns one JSON; stream yields JSON-per-line ({message:{content},done}). Map to OpenAI shape. No API key.
  - anthropic.py: POST {anthropic_base_url}/v1/messages with headers x-api-key, anthropic-version: 2023-06-01. Translate: pull system messages into top-level "system"; map remaining to {role,content}; require max_tokens (default 1024). Non-stream: response.content[0].text -> choices. Stream: parse Anthropic SSE events (content_block_delta -> text_delta) -> chunk deltas.
  - gemini.py: POST {gemini_base_url}/models/{model}:generateContent?key=... (and :streamGenerateContent?alt=sse for stream). Translate messages -> contents[{role:user|model, parts:[{text}]}] (system -> systemInstruction). Map candidates[0].content.parts[].text back.
  - azure.py: OpenAI-compatible against azure_openai_endpoint with deployment + api-version + api-key header. If azure_openai_endpoint/api_key empty, still register but raise ProviderError("azure not configured", status_code=400) when called.
  - bedrock.py: thin — try optional boto3/bedrock-runtime invoke_model for anthropic.* models; if boto3 missing or creds absent, register but raise ProviderError("bedrock not configured", status_code=400) when called. Do NOT add boto3 as a hard dependency (lazy import inside the method).

Make the OpenAI-shaped builders small shared helpers if useful (keep them inside your files; do not add new shared spine modules). Provide stable, deterministic ids using uuid4 hex with an "chatcmpl-" prefix.

Return the IMPL schema. self_check must show: ${PY} -c "import app.gateway" (which imports all adapters) succeeds and ruff is clean.`,
  },
  {
    key: 'detection',
    prompt: `${PREAMBLE}

YOUR MODULE — Detection orchestration. Files: app/redaction/detect.py, app/redaction/presidio_ner.py, app/redaction/merge.py
Contracts (AGENTS.md §2):
  - merge.py: \`def merge_spans(spans: list[EntitySpan]) -> list[EntitySpan]\` — pure. Sort by start; when spans overlap, keep the winner by (score desc, length desc, source priority regex>llm>presidio) and drop overlapped losers; spans that are fully contained in a kept span are dropped. Return non-overlapping, start-sorted.
  - presidio_ner.py: \`class PresidioDetector\` — \`__init__\` lazily imports presidio_analyzer; set self.available=False and log if missing. \`def detect(self, text: str, language: str = "en") -> list[EntitySpan]\` returns [] if unavailable. Map Presidio entity types -> our codes (PERSON->PERSON, EMAIL_ADDRESS->EMAIL, PHONE_NUMBER->PHONE, CREDIT_CARD->CREDIT_CARD, IBAN_CODE->IBAN, LOCATION->LOCATION, ORGANIZATION->ORG, IP_ADDRESS->IP_ADDRESS, URL->URL, DATE_TIME->DATE_TIME). source="presidio", score from result.score.
  - detect.py: \`class Detector\` with \`async def detect(self, text: str, *, pack_codes: list[str], language: str = "en") -> DetectionResult\`. Run passes CONCURRENTLY with asyncio.gather: (a) regex packs via app.redaction.regex_packs.load_packs(pack_codes) -> each pack.detect(text) [run sync detect in a thread or just call — they're pure/fast, fine to call directly]; (b) Presidio if settings.enable_presidio; (c) LLM-NER via app.redaction.llm_ner.llm_ner(text) if settings.enable_llm_ner. Collect all spans, merge_spans, return DetectionResult(text=text, spans=merged). Import settings from app.config. Be resilient: a failing optional pass contributes []. MUST work regex-only.
NOTE: app.redaction.regex_packs and app.redaction.llm_ner are written by another agent per AGENTS.md §3 — import them by the documented names; guard the llm_ner/presidio imports so detect.py imports even if those modules error.

Return the IMPL schema. self_check: ${PY} -c "import app.redaction.detect, app.redaction.merge, app.redaction.presidio_ner" + ruff.`,
  },
  {
    key: 'packs',
    prompt: `${PREAMBLE}

YOUR MODULE — Jurisdiction packs + regex detection + validators + LLM-NER. Files:
  app/packs/ca.yaml, app/packs/us.yaml, app/packs/eu.yaml, app/packs/ua.yaml, app/packs/ircc.yaml
  app/redaction/regex_packs.py, app/redaction/validators.py, app/redaction/packs_loader.py, app/redaction/llm_ner.py
Contracts (AGENTS.md §3):
  - validators.py: pure functions (str)->bool: luhn(s), iban_mod97(s), sin_check(s) (CA SIN = Luhn over 9 digits), abn? not needed. Add ein/ssn format checks if helpful. Strip spaces/hyphens inside validators.
  - YAML packs: each file has keys: code, name, entity_types (list), patterns: [{type, regex, validator(optional, name in validators.py), flags(optional e.g. "IGNORECASE"), description(optional)}]. Cover:
      CA: SIN (\\\\b\\\\d{3}[ -]?\\\\d{3}[ -]?\\\\d{3}\\\\b + validator sin_check), BN/CRA business number (9 digits + optional program code like RC0001), GST_HST, NEQ (10 digits, Quebec).
      US: SSN (\\\\b\\\\d{3}-\\\\d{2}-\\\\d{4}\\\\b), EIN (\\\\b\\\\d{2}-\\\\d{7}\\\\b), ITIN (9\\\\d\\\\d like 9XX-7X-XXXX).
      EU: IBAN (general IBAN regex + validator iban_mod97), VAT (EU VAT number patterns).
      UA: EDRPOU (\\\\b\\\\d{8}\\\\b ЄДРПОУ), IPN/ІПН (\\\\b\\\\d{10}\\\\b), IBAN_UA (UA\\\\d{2}...).
      IRCC: UCI (\\\\b\\\\d{8,10}\\\\b near UCI keyword), application numbers.
    Use word boundaries; prefer slightly conservative regex to limit false positives; include en/uk/ru/fr keyword cues in descriptions. Avoid catastrophic backtracking.
  - regex_packs.py: \`class Pack\` (fields code,name,entity_types,patterns) with \`def detect(self, text: str) -> list[EntitySpan]\`: for each pattern, finditer; if a validator is named, only emit spans where validator(match)==True; EntitySpan(start,end,entity_type=type,text=match,score=0.95,source="regex",jurisdiction=code). \`def load_packs(codes: list[str]) -> list[Pack]\` loads YAML from app/packs/ (use importlib.resources or Path(__file__).parent.parent/"packs"). Cache loaded packs. \`def all_pack_meta() -> list[dict]\` returns metadata for the DB loader.
  - packs_loader.py: \`async def sync_packs_to_db(session) -> int\` upserts each pack's {code,name,entity_types,definition=<patterns as json>} into jurisdiction_pack (ON CONFLICT (code) DO UPDATE). Use SQLAlchemy. Return count.
  - llm_ner.py: \`async def llm_ner(text: str) -> list[EntitySpan]\` — build an OpenAI-style request asking the model to return EntitySpan[] as JSON (use tools/function-calling or JSON mode), call app.gateway.get_provider(settings.ai_provider, settings).complete(payload), parse, map to EntitySpan(source="llm"). Tolerant: any error -> return []. This is only invoked when settings.enable_llm_ner.

Write a quick inline self-test in your shell (not committed) to confirm a few positive/negative regex matches and validators. Return IMPL schema. self_check: ${PY} -c "import app.redaction.regex_packs as r, app.redaction.validators, app.redaction.packs_loader; r.load_packs(['CA','US','EU','UA','IRCC'])" + ruff.`,
  },
  {
    key: 'vault',
    prompt: `${PREAMBLE}

YOUR MODULE — the reversible token vault (THE core IP). Files: app/redaction/vault.py, app/redaction/pg_store.py
Contracts (AGENTS.md §4) — implement EXACTLY:
  class Vault:
      def __init__(self, store: TokenStore, *, master_key: bytes, fingerprint_key: bytes)
      async def tokenize(self, text: str, spans: list[EntitySpan], *, session_id: str) -> str
      async def detokenize(self, text: str, *, session_id: str) -> str
      def stream_detokenizer(self, session_id: str) -> StreamDetokenizer
  Crypto/algorithm:
   - DEK derivation (deterministic): dek = HKDF(SHA256, length=32, salt=session_id.encode(), info=b"redactgate-dek-v1").derive(master_key). Use cryptography.hazmat HKDF + AESGCM.
   - value fingerprint = hmac.new(fingerprint_key, f"{session_id}:{entity_type}:{value}".encode(), sha256).hexdigest()
   - token_hex = fingerprint[:6]; if make_placeholder(type,token_hex) already maps (in this session) to a DIFFERENT fingerprint, extend by 2 hex chars until unique (use store.get_by_placeholder to check).
   - REFERENTIAL CONSISTENCY: in tokenize, process spans RIGHT-TO-LEFT (descending start) so offsets stay valid. For each span: fp=fingerprint(value); existing=await store.get_by_fingerprint(session_id,fp); if existing -> reuse existing.placeholder and await store.bump_occurrence; else compute placeholder, ciphertext=AESGCM(dek).encrypt(nonce, value.encode(), None) stored as nonce+ct, await store.put(TokenRecord(...)). Replace text[span.start:span.end] with the placeholder. Same value in the same session ALWAYS yields the same placeholder.
   - detokenize: for each PLACEHOLDER_RE match, await store.get_by_placeholder(session_id, match); if found, decrypt (split nonce/ct, AESGCM(dek).decrypt) and substitute the real value; unknown placeholders are left as-is. Use make_placeholder/PLACEHOLDER_RE from app.redaction.placeholders.
  class StreamDetokenizer:
      def __init__(self, vault, session_id)
      async def push(self, chunk: str) -> str   # append to buffer; detokenize the safe prefix; HOLD a trailing partial (use trailing_partial_len + keep up to MAX_PLACEHOLDER_LEN so a placeholder split across chunks is never emitted half-swapped); return only fully-resolved emitted text
      async def flush(self) -> str               # detokenize+emit whatever remains in the buffer
    Correctness: concatenation of all push() returns + flush() == detokenize(full_text). A placeholder split across two/three chunks must round-trip exactly once.
  pg_store.py: class PostgresTokenStore(TokenStore) over an AsyncSession (constructor takes the session). create_session inserts a redaction_session row (team_id/policy_id may be None in single-tenant dev -> use the seeded defaults '...bb'/'...cc' if None; expires_at = now + settings.session_ttl_hours) and returns str(id). get_by_fingerprint/get_by_placeholder/put/bump_occurrence/all_for_session map to token_map with the unique constraints. Use SQLAlchemy select/insert; handle the UNIQUE(session_id,value_fingerprint) for idempotency.

CRITICAL test you must satisfy (the unit test agent will assert it): given InMemoryTokenStore, tokenizing "John Smith ... John Smith" yields the SAME placeholder for both occurrences; detokenize restores both; and a TokenRecord's value_ciphertext alone (without the key) does NOT contain the plaintext bytes.

Return IMPL schema. self_check: ${PY} -c "import app.redaction.vault, app.redaction.pg_store" + ruff + a tiny inline asyncio roundtrip using InMemoryTokenStore.`,
  },
  {
    key: 'policy_audit',
    prompt: `${PREAMBLE}

YOUR MODULE — policy engine + hash-chained audit. Files: app/redaction/policy.py, app/audit.py
Contracts (AGENTS.md §5,§6):
  policy.py:
   - async def resolve_policy(session, policy_id) -> PolicyDecision : load the policy row; build PolicyDecision(mode=PolicyMode(row.mode), blocked=False, blocked_types=row.blocked_types, allowed_providers=row.allowed_providers, redact_types=None). If policy_id is None -> a default tokenize decision.
   - def evaluate(decision: PolicyDecision, detected_types: set[str]) -> None : if decision.mode == HARD_BLOCK OR detected_types & set(decision.blocked_types) -> raise HardBlockError(sorted(list(detected_types & set(decision.blocked_types)) or list(detected_types))).
   - def filter_spans(decision, spans) -> list[EntitySpan] : keep spans where decision.should_redact(span.entity_type).
   - def provider_allowed(decision, provider: str) -> bool : True if allowed_providers empty or provider in it.
  audit.py:
   - def canonical_json(payload: dict) -> str : json.dumps(payload, sort_keys=True, separators=(",",":"), default=str).
   - def compute_event_hash(prev_hash: str, payload: dict, hmac_key: bytes) -> str : hmac_sha256(hmac_key, (prev_hash + canonical_json(payload)).encode()).hexdigest().
   - GENESIS_HASH = "0"*64.
   - async def last_hash(session, team_id) -> str : the most recent audit_event.event_hash for the team, else GENESIS_HASH.
   - async def record_event(session, *, team_id, api_key_id, session_id, route, provider, entity_counts: dict, blocked: bool, prompt_tokens=None, completion_tokens=None, latency_ms=None) -> AuditEvent : compute payload dict (the business fields), prev=await last_hash, event_hash=compute_event_hash(prev,payload, settings.key_bytes("audit_hmac_key")), insert AuditEvent, flush, return it. NEVER include raw values — entity_counts is {TYPE: int}.
   - async def verify_chain(session, team_id) -> bool : recompute the chain in created order and confirm each event_hash matches; return False on any mismatch (tamper detection).
   - Provide an AuditSink Protocol + DBAuditSink(session) (calls record_event) + InMemoryAuditSink (keeps a list, computes the same hash chain in memory) so routes can be tested without a DB.

Return IMPL schema. self_check: import both modules + ruff + an inline test that compute_event_hash is deterministic and a tampered middle event makes the in-memory chain verify() False.`,
  },
  {
    key: 'auth',
    prompt: `${PREAMBLE}

YOUR MODULE — authentication. File: app/auth.py
Contracts (AGENTS.md §7):
  - from dataclasses import dataclass; @dataclass(slots=True) class AuthContext: team_id: str; policy_id: str | None; api_key_id: str | None
  - def hash_key(raw: str) -> str : argon2 (argon2.PasswordHasher) hash.
  - def verify_key(hashed: str, raw: str) -> bool : argon2 verify, False on mismatch.
  - def make_key() -> str : "rg-" + secrets.token_urlsafe(32).
  - DEFAULT_TEAM_ID="00000000-0000-0000-0000-0000000000bb"; DEFAULT_POLICY_ID="00000000-0000-0000-0000-0000000000cc".
  - async def authenticate(authorization: str | None, session) -> AuthContext : if not settings.require_api_key -> return AuthContext(DEFAULT_TEAM_ID, DEFAULT_POLICY_ID, None). Else parse "Bearer <key>"; load non-revoked api_key rows and verify_key against each key_hash (argon2 hashes are per-row, so you must select candidates — keep it simple: iterate non-revoked keys; for scale, fine for MVP). On match return AuthContext(team_id, team.default_policy_id, api_key_id). On failure raise app.gateway.base.ProviderError("invalid api key", status_code=401).
  - async def ensure_default_api_key(session) -> str | None : if the default team has no api_key row, create one with hash_key(make_key()) labelled "default-dev" and return the RAW key once (so the operator can copy it from logs); else return None. Use the seeded default team id.

Return IMPL schema. self_check: ${PY} -c "import app.auth" + ruff + inline test that hash_key/verify_key round-trip and a wrong key fails.`,
  },
  {
    key: 'pipeline_routes',
    prompt: `${PREAMBLE}

YOUR MODULE — the redaction pipeline, injectable deps, and the HTTP routes (the integration layer). Files:
  app/redaction/pipeline.py, app/deps.py, app/routes/__init__.py, app/routes/health.py, app/routes/proxy.py, app/routes/admin.py
Design for TESTABILITY WITHOUT A DATABASE via FastAPI dependency overrides.

app/deps.py — FastAPI dependencies (so tests override them with in-memory variants):
  - async def get_token_store(session = Depends(get_session)) : yield PostgresTokenStore(session)  (import lazily)
  - async def get_audit_sink(session = Depends(get_session)) : yield DBAuditSink(session)  (from app.audit)
  - async def get_auth(request: Request, session = Depends(get_session)) -> AuthContext : authenticate(request.headers.get("authorization"), session)
  - async def get_policy_decision(auth = Depends(get_auth), session = Depends(get_session)) -> PolicyDecision : resolve_policy(session, auth.policy_id)
  - def get_active_provider() -> Provider : get_provider(settings.ai_provider, settings)
  - def get_vault() -> Vault : Vault(store?, ...) — NOTE the store is request-scoped, so instead expose a builder: def build_vault(store) -> Vault using settings.key_bytes("vault_master_key") and settings.key_bytes("fingerprint_hmac_key").

app/redaction/pipeline.py:
  class PipelineContext: session_id, entity_counts(dict), blocked(bool), spans(list)
  class RedactionPipeline:
      def __init__(self, *, store, vault, detector, audit_sink, decision, auth, provider)
      async def sanitize_request(self, payload: dict, *, route: str) -> tuple[dict, PipelineContext]:
          texts = extract_texts(payload); open one session_id = await store.create_session(team_id=auth.team_id, policy_id=auth.policy_id);
          detect each text concurrently (Detector.detect with settings pack_codes); apply policy.filter_spans; evaluate hard-block over the union of detected types (raise HardBlockError -> caller writes a blocked audit and returns 422); tokenize each text with the SAME session_id (referential consistency across the whole request); rebuild payload via inject_texts. Aggregate entity_counts. Return (sanitized_payload, ctx).
      async def reinflate(self, completion: dict, ctx) -> dict : detokenize the assistant message text (extract_completion_text/set_completion_text) with ctx.session_id.
      def reinflate_stream(self, ctx) -> StreamDetokenizer : vault.stream_detokenizer(ctx.session_id); the route uses it to detokenize each delta's content (extract_delta_text/set_delta_text) and must flush at end.
  Provide a module-level helper to build a RedactionPipeline from the injected deps.

app/routes/health.py: router = APIRouter(); GET /healthz -> {"status":"ok"} always; GET /readyz -> checks DB via a trivial SELECT 1 (200 ok / 503 if down).
app/routes/proxy.py: router = APIRouter();
  POST /v1/chat/completions and POST /v1/responses : Depends on get_token_store,get_audit_sink,get_policy_decision,get_active_provider,get_auth. Build pipeline; sanitize; if provider not allowed by policy -> 400. If stream requested -> StreamingResponse of SSE: for each upstream chunk, detokenize delta via the stream detokenizer, re-serialize as "data: {json}\\n\\n", end with "data: [DONE]\\n\\n", flush detokenizer; else await provider.complete, reinflate, write audit (entity counts only, latency, token usage), return JSON. On HardBlockError -> write a blocked audit event and return 422 with error_body. On ProviderError -> return its status_code with error_body.
  GET /v1/models : list {id: provider default models} for configured providers.
  IMPORTANT: import HardBlockError from app.schemas.openai and ProviderError from app.gateway.base.
app/routes/admin.py: router = APIRouter(prefix="/admin"); gate every route on header X-Admin-Token == settings.admin_token (if settings.admin_token set; if empty, allow in dev). Endpoints (DB-backed, JSON):
  GET /admin/stats (totals: requests, entities redacted by type, blocked count, by provider), GET /admin/audit?limit&offset&team (paginated audit list, counts only), GET /admin/policies + POST /admin/policies (create), GET /admin/benchmark (latest eval_run rows). Keep queries simple and async.

Return IMPL schema. self_check: ${PY} -c "import app.deps, app.redaction.pipeline, app.routes.proxy, app.routes.admin, app.routes.health; import app.main" succeeds (app.main wires the routers) + ruff. (You may not be able to hit the DB — import-level success + ruff is the bar here; the test agent will exercise behavior with overrides.)`,
  },
  {
    key: 'worker_eval',
    prompt: `${PREAMBLE}

YOUR MODULE — async worker + fidelity benchmark harness. Files:
  app/worker.py, eval/__init__.py, eval/harness.py, eval/golden/ca.jsonl, eval/golden/us.jsonl, eval/golden/eu.jsonl
Contracts (AGENTS.md §9):
  - app/worker.py: arq WorkerSettings with redis_settings from settings.redis_url; a task async def run_fidelity_eval(ctx, pack_code, provider) that calls eval.harness.run_eval and persists an eval_run row (use session_scope). Keep imports lazy so importing app.worker doesn't require redis to be up.
  - eval/harness.py:
      def load_golden(path) -> list[dict]   # each line {"text":..., "entities":[{"type","start","end"}], "question":...}
      async def run_eval(golden_set: str, provider: str) -> dict  # returns {"recall":float,"precision":float,"answer_fidelity":float|None, "n":int}
        Build a Detector (regex packs), detect entities per text, compare detected spans vs labeled entities (a TP = same type and overlapping span). Compute recall=TP/total_labeled, precision=TP/total_detected. answer_fidelity: if a provider is configured & reachable, run the question through redact->complete->reinflate and an LLM-judge comparing to the raw answer (score 0..1); on any failure return None for fidelity (do NOT fail the run).
      A \`if __name__ == "__main__"\` that runs run_eval over the bundled golden sets (regex-only, fidelity=None) and prints a scorecard table.
  - eval/golden/*.jsonl: 8-15 realistic labeled lines each, with CORRECT character offsets for the entities (compute them carefully — the test/eval depends on exact offsets). Use synthetic but realistic values (e.g. CA SIN "046 454 286" passes Luhn; EU IBAN "GB82 WEST 1234 5698 7654 32"). Include uk/ru/fr examples for EU/UA where natural. Verify offsets with a quick python script before finalizing (text[start:end] == the entity).

Return IMPL schema. self_check: ${PY} -m eval.harness runs and prints a scorecard; ${PY} -c "import app.worker" succeeds; ruff clean; AND confirm every golden line's offsets are exact (text[e.start:e.end] matches).`,
  },
  {
    key: 'web_ui',
    prompt: `${PREAMBLE}

YOUR MODULE — the Next.js 16 admin UI. Directory: ${ROOT}/web (you own everything under web/).
This is a self-contained Next.js App Router app (TypeScript + Tailwind) consuming RedactGate's /admin/* JSON API. Do NOT run create-next-app; write the files directly so it's deterministic. Use Node at the system node (v22).

Build a clean, professional admin console with these pages (App Router, app/):
  - / (Dashboard): redaction volume, entity-type breakdown, blocked count, per-provider routing, latest benchmark score — cards + a simple bar list (no heavy chart lib; CSS bars or a tiny inline SVG).
  - /audit : searchable, paginated audit table (route, provider, entity counts, blocked, latency, time). Counts only — make clear raw values are never stored.
  - /policies : list policies + a form to create one (name, mode tokenize/mask/hard_block, blocked_types, allowed_providers).
  - /benchmark : recall / precision / answer-fidelity scorecard table with provider + pack.
Implement a small API client in web/lib/api.ts that reads REDACTGATE_API_BASE (server) / NEXT_PUBLIC_API_BASE (client) and sends X-Admin-Token from an env var. Use Server Components for data fetch where possible. Tailwind config + globals.css. A shared layout with a left nav (Dashboard / Audit / Policies / Benchmark) and the RedactGate wordmark. Aim for a credible, minimal, modern look (slate/indigo, good spacing, monospace for tokens/placeholders). Include a web/Dockerfile (multi-stage, node:22-alpine, next build, next start -p 3000) and web/.dockerignore and web/README.md.

package.json: next (latest 15/16 — use "next":"^15" if 16 unavailable; set it so npm install resolves), react, react-dom, typescript, @types/*, tailwindcss, postcss, autoprefixer. tsconfig.json, next.config.mjs, postcss.config.mjs, tailwind.config.ts.

Attempt: cd ${ROOT}/web && npm install (allow a few minutes) && npm run build. If npm install or build cannot complete in your environment, STILL leave a complete, coherent source tree and report the exact failure in notes — do not leave it half-written.

Return the IMPL schema (module:"web_ui"); self_check = result of npm install/build or the precise reason it couldn't run; issues = anything blocking.`,
  },
]

// ─────────────────────────────── Test-author agents ───────────────────────────────

const TEST_PREAMBLE = `You are writing the pytest suite for RedactGate AFTER the implementation has landed. PROJECT ROOT: ${ROOT}. Use ${PYTEST} and ${PY} from the existing venv — never reinstall.
Read ${ROOT}/AGENTS.md and the relevant implementation files first. tests/conftest.py ALREADY EXISTS (provides fixtures: vault, master_key, fingerprint_key, sample_pii_text, integration_only()) — use it; do NOT overwrite conftest.
Write ONLY your assigned test files (others write theirs concurrently). Tests must pass in the UNIT lane with NO database/redis (use InMemoryTokenStore, FastAPI dependency_overrides, and respx for HTTP). Mark anything needing Postgres with @integration_only().
Run your suite with ${PYTEST} -q <your files>; iterate until green. If a test reveals a genuine IMPLEMENTATION bug, do NOT edit the implementation (another agent may be touching it) — capture it precisely in impl_bugs_found. Aim for thorough, meaningful tests (not trivial asserts). Return the TEST schema.`

const TESTS = [
  {
    key: 'engine',
    prompt: `${TEST_PREAMBLE}

YOUR SUITE — the redaction engine. Files: tests/test_placeholders.py, tests/test_merge.py, tests/test_vault.py, tests/test_stream_detok.py, tests/test_extract_inject.py
Cover:
  - placeholders: make_placeholder/PLACEHOLDER_RE round-trip; find_placeholders; trailing_partial_len for "[", "[[", "[[SIN_7f", a complete placeholder (->0), plain text (->0), and that MAX_PLACEHOLDER_LEN bounds it.
  - merge_spans: overlapping spans resolved by score/length; contained spans dropped; disjoint preserved; output start-sorted & non-overlapping.
  - vault (use the \`vault\` fixture): referential consistency (repeated value -> identical placeholder; bump occurrences), full tokenize/detokenize round-trip restores original, different sessions -> independent placeholders, ciphertext-in-store does NOT contain plaintext bytes (decrypt only with key), unknown placeholder left untouched by detokenize.
  - stream detok: for a text containing several placeholders, feed it through StreamDetokenizer in chunk sizes 1, 3, 7 and a worst-case split EXACTLY inside a placeholder; assert concat(push...)+flush == vault.detokenize(full).
  - extract/inject_texts: string content, parts content, /v1/responses input, length-mismatch raises, original payload not mutated.
Target 30+ tests. Return TEST schema.`,
  },
  {
    key: 'packs_detect',
    prompt: `${TEST_PREAMBLE}

YOUR SUITE — packs, validators, detection. Files: tests/test_validators.py, tests/test_regex_packs.py, tests/test_detect.py
Cover:
  - validators: luhn (known valid/invalid), iban_mod97 (valid GB IBAN vs corrupted), sin_check (valid CA SIN "046 454 286" valid; a wrong checksum invalid).
  - regex_packs: load_packs for CA/US/EU/UA/IRCC loads; for each major entity type at least one POSITIVE (detected, correct type & offsets) and one NEGATIVE (similar-looking but invalid -> not detected, especially validator-gated SIN/IBAN). Assert spans carry jurisdiction + source="regex".
  - detect: Detector().detect(text, pack_codes=[...]) regex-only (settings.enable_presidio/enable_llm_ner False) finds the expected entity TYPES in a mixed paragraph; spans are merged (non-overlapping); works with an empty/clean string (no spans).
Use the real YAML packs. Target 25+ tests. Return TEST schema.`,
  },
  {
    key: 'policy_audit_auth',
    prompt: `${TEST_PREAMBLE}

YOUR SUITE — policy, audit, auth. Files: tests/test_policy.py, tests/test_audit.py, tests/test_auth.py
Cover:
  - policy: evaluate() raises HardBlockError when mode==HARD_BLOCK or a detected type is in blocked_types; filter_spans honors redact_types (None=all); provider_allowed (empty allows all, otherwise membership).
  - audit: compute_event_hash deterministic & changes when any field changes; InMemoryAuditSink builds a valid hash chain across N events; verify() detects tampering when a middle event's fields are mutated; entity_counts only stores type->int (no raw values). (DB-backed record_event/verify_chain -> @integration_only.)
  - auth: hash_key/verify_key round-trip; wrong key fails; make_key has rg- prefix & uniqueness; authenticate with require_api_key=False returns the default team without a header (monkeypatch settings or use the test env).
Use monkeypatch for settings flags where needed. Target 25+ tests. Return TEST schema.`,
  },
  {
    key: 'gateway_routes',
    prompt: `${TEST_PREAMBLE}

YOUR SUITE — gateway adapters + HTTP routes end-to-end. Files: tests/test_gateway.py, tests/test_health.py, tests/test_proxy_e2e.py
Cover:
  - gateway (respx): mock each provider's upstream HTTP and assert the adapter returns a correctly-shaped OpenAI chat.completion dict (and chunk dicts for stream) for at least openai, ollama, anthropic, gemini. Assert ProviderError raised on a 500 upstream. do/azure/bedrock: assert "not configured" ProviderError path where applicable.
  - health: GET /healthz returns 200 ok via fastapi TestClient.
  - proxy_e2e (the key integration test, NO DB):
      * register a FAKE provider via app.gateway.base.register_provider("fake", ...) whose complete() echoes back the (already-sanitized) user content so you can assert placeholders were sent upstream and real values come back; its stream() yields the content in pieces (including a piece that splits a placeholder).
      * Use app.main.create_app() (or app) + app.dependency_overrides to inject get_token_store->InMemoryTokenStore, get_audit_sink->InMemoryAuditSink, get_policy_decision->a default tokenize PolicyDecision, get_active_provider->the fake provider, get_auth->a default AuthContext. Set settings.require_api_key False / ai_provider "fake" as needed (monkeypatch).
      * POST /v1/chat/completions with a body containing PII ("SIN 046 454 286", "John Smith" twice); assert: (a) the content the fake provider RECEIVED contained placeholders not the raw SIN/name, (b) the response the CLIENT got has the real values re-inflated, (c) repeated "John Smith" used one consistent placeholder.
      * A streaming request: assert the reassembled streamed content equals the fully re-inflated text (placeholder split across chunks handled).
      * A hard-block policy: POST returns 422.
  Keep these robust to minor signature differences by reading the actual pipeline/deps code first.
Target 25+ tests. If the route/deps wiring differs from AGENTS.md, adapt the test to the real code and note any true bug in impl_bugs_found. Return TEST schema.`,
  },
]

// ─────────────────────────────── Orchestration ───────────────────────────────

phase('Implement')
log(`Implementing ${IMPL.length} module groups in parallel against the frozen spine…`)
const implResults = await parallel(
  IMPL.map((m) => () => agent(m.prompt, { label: `impl:${m.key}`, phase: 'Implement', schema: IMPL_SCHEMA }))
)
const impl = implResults.filter(Boolean)
log(`Implement done: ${impl.length}/${IMPL.length} returned. Files: ${impl.reduce((n, r) => n + (r.files_written?.length || 0), 0)}`)
const implIssues = impl.filter((r) => r.issues && r.issues.toLowerCase() !== 'none')
if (implIssues.length) log(`Contract issues flagged: ${implIssues.map((r) => r.module).join(', ')}`)

phase('Tests')
log(`Authoring ${TESTS.length} test suites in parallel…`)
const testResults = await parallel(
  TESTS.map((t) => () => agent(t.prompt, { label: `test:${t.key}`, phase: 'Tests', schema: TEST_SCHEMA }))
)
const tests = testResults.filter(Boolean)
const totalPass = tests.reduce((n, r) => n + (r.tests_passing || 0), 0)
const totalFail = tests.reduce((n, r) => n + (r.tests_failing || 0), 0)
const bugs = tests.filter((r) => r.impl_bugs_found && r.impl_bugs_found.toLowerCase() !== 'none')
log(`Tests authored: ${totalPass} passing / ${totalFail} failing across ${tests.length} suites; ${bugs.length} suites flagged impl bugs.`)

return {
  implemented: impl,
  impl_issues: implIssues.map((r) => ({ module: r.module, issues: r.issues })),
  tests: tests,
  totals: { passing: totalPass, failing: totalFail },
  impl_bugs: bugs.map((r) => ({ suite: r.suite, bugs: r.impl_bugs_found })),
}
