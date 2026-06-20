# RedactGate — Security Posture

RedactGate is a privacy firewall: its entire value is that **raw PII never reaches the
upstream LLM**, the reversible vault is sound, and the audit log is tamper-evident and
contains zero raw values. Security defects are treated as product-breaking.

## Reporting

Please report vulnerabilities privately to the maintainer (see repo profile) rather than
opening a public issue. We aim to acknowledge within a few days.

## Threat model

- **Untrusted:** the content of every proxied request (prompt text, tool-call arguments,
  Responses-API input) — it may contain PII and may be adversarially crafted to evade
  detection.
- **Trusted:** the operator who holds the master keys and admin token, and the Postgres
  instance (encrypted-at-rest values still require the master key to read).
- **Goals:** (1) no raw regulated identifier leaves the perimeter un-tokenized; (2) the
  vault is reversible only with the per-session key; (3) the audit log is append-only,
  hash-chained, and records only entity *type counts*; (4) fail-closed in production.

## 2026-06-20 adversarial audit

A multi-agent adversarial audit swept eight attack surfaces (leakage, crypto, authz, audit
integrity, streaming, ReDoS/SSRF/SQLi, provider adapters, concurrency); every finding was
independently verified against the code. **13 confirmed findings — all fixed or documented
below**, each with a regression test (`tests/test_security_fixes.py`).

| # | Sev | Issue | Status |
|---|-----|-------|--------|
| 1 | crit | `/v1/responses` nested message input not extracted → whole payload forwarded raw | **Fixed** — unified `_map_texts` walker recurses Responses message items |
| 2 | crit | `tool_calls[].function.arguments` / `function_call.arguments` not redacted | **Fixed** — walker now redacts tool-call argument strings |
| 3 | crit | No Unicode normalization → full-width / NBSP / zero-width evasion | **Fixed** — NFKC + invisible-char strip before detection (`normalize.py`) |
| 4 | crit | Deterministic dev-fallback keys usable in prod | **Fixed** — `runtime_problems()` aborts boot in prod without real keys |
| 5 | crit | Empty `admin_token` silently opened the admin API in prod | **Fixed** — `require_admin` fail-closed outside `dev`; prod refuses to boot |
| 6 | crit | Concurrent same-team appends forked the audit hash-chain | **Fixed** — per-team `pg_advisory_xact_lock` serializes append |
| 8 | high | `TRUNCATE` bypassed the append-only trigger | **Fixed** — statement-level TRUNCATE guard + REVOKE (migration 003) |
| 10 | high | No request body size limit (DoS) | **Fixed** — `max_body_bytes` middleware → 413 |
| 11 | med | Placeholder suffix derived from value fingerprint (leaked 24 bits) | **Fixed** — random per-session token suffix |
| 12 | med | Wildcard CORS on a localhost/LAN firewall | **Fixed** — `cors_origins` allow-list, no `*` default |
| 13 | low | Caller `model` path-injected into Gemini upstream URL | **Fixed** — strict model-name charset validation |
| 9 | high | Audit writes swallowed silently; no sequence anchor | **Partially fixed** — failures now logged loudly; chain-fork fixed (#6). Per-team monotonic sequence for gap-detection is roadmap. |
| 7 | high | Admin API not tenant-scoped (one global token) | **Documented limitation** (below) |

## Known limitations / roadmap

- **Single global admin token (OSS self-host tier).** v0.1 uses one `ADMIN_TOKEN` and the
  admin API is not tenant-scoped. This is acceptable for the typical single-org self-host
  deployment. The managed/multi-tenant tier requires per-tenant admin credentials and
  org-scoped admin queries — tracked for the hosted product.
- **Audit is best-effort at the edge.** A request is sent upstream before its audit row is
  committed; if the audit write fails it is now logged loudly but the request is not
  retroactively blocked. A configurable fail-closed mode and a per-team monotonic sequence
  number (so an auditor can detect a *missing* event, not just a tampered one) are planned.

## Hardening defaults

- Production (`ENVIRONMENT=prod`) refuses to start without explicit `VAULT_MASTER_KEY`,
  `FINGERPRINT_HMAC_KEY`, `AUDIT_HMAC_KEY`, `ADMIN_TOKEN`, and `REQUIRE_API_KEY=true`.
- API keys are argon2-hashed; raw entity values are AES-256-GCM encrypted at rest under a
  per-session key and never logged or persisted in plaintext.
- The audit log stores entity type→count maps only; the row-level + statement-level triggers
  block UPDATE/DELETE/TRUNCATE.
