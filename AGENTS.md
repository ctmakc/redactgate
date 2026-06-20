# RedactGate — module map & contracts

This file is the single source of truth for how the codebase fits together. Every module
owns a disjoint set of files and must conform to the interfaces below. **Do not edit files
outside your assigned set.** The "spine" files (already written) define the contracts —
import from them, never redefine them.

## Spine (DO NOT MODIFY — import only)

| File | Provides |
|---|---|
| `app/config.py` | `settings`, `get_settings()`, `Settings.key_bytes(field)` |
| `app/schemas/openai.py` | `extract_texts`, `inject_texts`, `extract_completion_text`, `extract_delta_text`, `set_completion_text`, `set_delta_text`, `error_body`, `HardBlockError`, request models |
| `app/schemas/entities.py` | `EntitySpan`, `DetectionResult`, `PolicyDecision`, `PolicyMode`, `GENERIC_TYPES` |
| `app/redaction/placeholders.py` | `make_placeholder`, `PLACEHOLDER_RE`, `find_placeholders`, `trailing_partial_len`, `MAX_PLACEHOLDER_LEN` |
| `app/redaction/store.py` | `TokenStore` protocol, `TokenRecord`, `InMemoryTokenStore` |
| `app/gateway/base.py` | `Provider` ABC, `ProviderError`, `register_provider`, `get_provider`, `available_providers` |
| `app/db.py` | `get_session`, `session_scope`, `get_engine` |
| `app/models.py` | SQLAlchemy ORM models |
| `app/migrate.py` | `apply_migrations()` |
| `app/main.py` | app factory (wires routers, lifespan) |

## EntitySpan (the detection contract)

```python
EntitySpan(start: int, end: int, entity_type: str, text: str,
           score: float = 1.0, source: str = "regex", jurisdiction: str | None = None)
```
`start`/`end` are Python-slice char offsets into the single text string the detector saw.

## Placeholder grammar

`[[TYPE_hex]]` e.g. `[[SIN_7f3a]]`. `TYPE` is UPPER_SNAKE; `hex` is 4–12 lowercase hex.
Use `make_placeholder(entity_type, token_hex)` and `PLACEHOLDER_RE`. Never hand-format.

---

## Module assignments

### 1. Gateway adapters — `app/gateway/{anthropic,openai,gemini,ollama,azure,bedrock,do_genai}.py`
Implement `Provider` (from `app.gateway.base`). Each module ends with
`register_provider("<name>", lambda s: <Cls>(s))`. Input = sanitized OpenAI chat payload
dict; output:
- `async def complete(payload) -> dict` — an OpenAI `chat.completion` dict.
- `def stream(payload) -> AsyncIterator[dict]` — yields OpenAI `chat.completion.chunk` dicts.
Translate to/from native APIs with `httpx.AsyncClient`. On HTTP error raise `ProviderError`.
`openai` and `do_genai` are near-passthrough (OpenAI-compatible). `ollama` uses its
`/api/chat`. `anthropic` uses `/v1/messages`. `gemini` uses `:generateContent`/`:streamGenerateContent`.
`azure`/`bedrock` may be thin stubs that raise `ProviderError("not configured")` if creds absent.
Use `self.settings` for keys/base-urls/default models. If `payload["model"]` looks generic,
fall back to the provider's configured default model.

### 2. Detection — `app/redaction/detect.py`, `app/redaction/presidio_ner.py`, `app/redaction/merge.py`
- `merge.py`: `merge_spans(spans: list[EntitySpan]) -> list[EntitySpan]` — sort, resolve
  overlaps by (score desc, length desc), drop contained/overlapping losers. Pure, no I/O.
- `presidio_ner.py`: `class PresidioDetector` with `detect(text, language="en") -> list[EntitySpan]`.
  Lazy-import presidio inside `__init__`; if unavailable, `available=False` and `detect`
  returns `[]`. Map Presidio types → our types (PERSON, EMAIL_ADDRESS→EMAIL, etc).
- `detect.py`: `class Detector` orchestrates passes concurrently with `asyncio.gather`:
  `async def detect(self, text: str, *, pack_codes: list[str], language: str = "en") -> DetectionResult`.
  Passes: (a) regex packs (from packs module), (b) Presidio if `settings.enable_presidio`,
  (c) LLM-NER if `settings.enable_llm_ner` (see module 3 for the LLM-NER helper). Merge via
  `merge_spans`. Must work regex-only with zero optional deps.

### 3. Jurisdiction packs — `app/packs/*.yaml`, `app/redaction/regex_packs.py`, `app/redaction/packs_loader.py`, `app/redaction/llm_ner.py`
- `app/packs/{ca,us,eu,ua,ircc}.yaml`: each pack = `code`, `name`, `entity_types`, and
  `patterns: [{type, regex, validator?, flags?}]`. Include CA: SIN (with Luhn validator),
  BN/CRA, GST/HST, NEQ; US: SSN, EIN, ITIN; EU: IBAN (mod-97 validator), VAT; UA: EDRPOU,
  ІПН/IPN, IBAN-UA; IRCC: UCI. Cover en/uk/ru/fr keyword cues where relevant.
- `regex_packs.py`: `load_packs(codes) -> list[Pack]`; `class Pack` with
  `detect(text) -> list[EntitySpan]` running each pattern + optional validator (a function
  name resolved from `app/redaction/validators.py`). YOU also create `app/redaction/validators.py`
  with `luhn`, `iban_mod97`, `sin_check`, etc. — pure functions `(str) -> bool`.
- `packs_loader.py`: `sync_packs_to_db(session)` upserts pack metadata into `jurisdiction_pack`.
- `llm_ner.py`: `async def llm_ner(text) -> list[EntitySpan]` — calls the active provider with
  a JSON-schema/function-calling request returning `EntitySpan[]`; tolerant of failure (returns []).

### 4. Vault — `app/redaction/vault.py`, `app/redaction/pg_store.py`
- `vault.py`: 
  ```python
  class Vault:
      def __init__(self, store: TokenStore, *, master_key: bytes, fingerprint_key: bytes): ...
      async def tokenize(self, text: str, spans: list[EntitySpan], *, session_id: str) -> str
      async def detokenize(self, text: str, *, session_id: str) -> str
      def stream_detokenizer(self, session_id: str) -> "StreamDetokenizer"
  ```
  - DEK derivation is deterministic: `dek = HKDF-SHA256(master_key, salt=session_id.encode(), info=b"redactgate-dek-v1")`.
  - fingerprint = `hmac_sha256(fingerprint_key, f"{session_id}:{entity_type}:{value}")` hex.
  - token_hex = first 6 chars of fingerprint; on placeholder collision within session, extend by 2.
  - Referential consistency: before creating a placeholder, `store.get_by_fingerprint`; if hit,
    reuse its placeholder and `bump_occurrence`. Same value ⇒ same placeholder within a session.
  - tokenize replaces spans right-to-left (so offsets stay valid), AES-256-GCM encrypts the
    real value with the session DEK, persists a `TokenRecord`.
  - detokenize swaps every `PLACEHOLDER_RE` match back to its decrypted real value.
  - `StreamDetokenizer.push(chunk: str) -> str` and `.flush() -> str`: holds a tail buffer of
    up to `MAX_PLACEHOLDER_LEN` using `trailing_partial_len`, only emits fully-resolved text.
- `pg_store.py`: `class PostgresTokenStore(TokenStore)` over an `AsyncSession` + a
  `create_session(team_id, policy_id, document_hash) -> session_id` helper that writes
  `redaction_session` (store the wrapped DEK envelope for at-rest/rotation; DEK itself is
  derived). Must satisfy the same uniqueness guarantees as `InMemoryTokenStore`.

### 5. Policy — `app/redaction/policy.py`
`async def resolve_policy(session, policy_id) -> PolicyDecision` and
`def evaluate(decision: PolicyDecision, detected_types: set[str]) -> None` that raises
`HardBlockError(blocked)` when `decision.mode == HARD_BLOCK` or a detected type ∈ blocked_types.
Also `def filter_spans(decision, spans) -> list[EntitySpan]` honoring `should_redact`.

### 6. Audit — `app/audit.py`
- Pure: `compute_event_hash(prev_hash: str, payload: dict, hmac_key: bytes) -> str` =
  `hmac_sha256(hmac_key, prev_hash + canonical_json(payload))` hex. Deterministic canonical JSON.
- `async def record_event(session, *, team_id, api_key_id, session_id, route, provider,
  entity_counts, blocked, prompt_tokens, completion_tokens, latency_ms) -> AuditEvent` —
  reads the last `event_hash` for the team (or genesis `"0"*64`), computes the chain link,
  inserts. `async def verify_chain(session, team_id) -> bool`.

### 7. Auth — `app/auth.py`
- `@dataclass AuthContext(team_id, policy_id, api_key_id)`.
- `async def authenticate(authorization: str | None, session) -> AuthContext` — parse
  `Bearer <key>`, argon2-verify against `api_key.key_hash`; if `settings.require_api_key` is
  False, return the seeded default team/policy (`...bb` / `...cc`). Raise `ProviderError(401)`
  on failure.
- `def hash_key(raw: str) -> str` / `def make_key() -> str` (prefix `rg-`).
- `async def ensure_default_api_key(session) -> str | None` — idempotently create a dev key
  for the default team if none exists (used by main lifespan). Return the raw key once.

### 8. Pipeline + routes — `app/redaction/pipeline.py`, `app/routes/{health,proxy,admin}.py`
- `pipeline.py`: ties it together. `class RedactionPipeline` with
  `async def process_request(payload, *, auth, route) -> tuple[dict, PipelineCtx]` (detect →
  policy → tokenize all texts under one session_id → return sanitized payload + ctx with
  session_id, entity_counts, detector spans) and
  `async def reinflate_response(resp_dict, ctx) -> dict` and a streaming variant using
  `Vault.stream_detokenizer`.
- `routes/health.py`: `router` — `GET /healthz` (always 200), `GET /readyz` (checks DB).
- `routes/proxy.py`: `router` — `POST /v1/chat/completions` and `POST /v1/responses`
  (Depends(get_session), parse Authorization, run pipeline, pick provider via
  `get_provider(settings.ai_provider, settings)` honoring policy allow-list, stream or not,
  re-inflate, audit). `GET /v1/models` lists configured providers' default models. On
  `HardBlockError` return 422 with `error_body` and write a `blocked` audit event.
- `routes/admin.py`: `router` — `GET /admin/stats`, `GET /admin/audit` (search/paginate),
  `GET /admin/policies` + `POST /admin/policies`, `GET /admin/benchmark`, gated by
  `settings.admin_token` (header `X-Admin-Token`). JSON only (the Next.js UI consumes these).

### 9. Worker + eval — `app/worker.py`, `eval/harness.py`, `eval/golden/*.jsonl`, `app/routes` (none)
- `app/worker.py`: `WorkerSettings` for arq with a `run_fidelity_eval` task.
- `eval/harness.py`: `run_eval(golden_set, provider) -> dict(recall, precision, answer_fidelity)`.
  recall/precision computed against labeled golden spans; answer_fidelity via an LLM-judge
  comparing redacted-roundtrip answer vs raw answer (tolerant; returns None if no provider).
- `eval/golden/*.jsonl`: labeled fixtures — each line `{"text":..., "entities":[{type,start,end}], "question":...}`.

### 10. Tests — `tests/test_*.py`
Cover: placeholders (partial-tail buffering), merge resolver, each validator (luhn/iban/sin),
regex packs (positive+negative per type), vault referential consistency + roundtrip +
crypto (cannot read value from store alone), stream detokenizer with placeholder split across
chunks, policy hard-block + filter, audit hash-chain + tamper detection, extract/inject_texts,
gateway adapters against `respx`-mocked upstreams, proxy route end-to-end with a fake provider,
auth argon2. Target 100+ tests, all green in the unit lane (no DB). Use the `vault` fixture
and `InMemoryTokenStore`. Integration tests that need Postgres get `@integration_only()`.

## Conventions
- Python 3.12, `from __future__ import annotations`, type hints, `ruff` clean.
- All I/O is async. No blocking calls in request path.
- Never log or persist raw entity values. Audit stores counts/types only.
- Regex-only path must work with zero optional dependencies installed.
