# RedactGate

> A self-hosted PII / financial-redaction firewall that lets regulated businesses use **any** cloud LLM without ever leaking raw client data.

RedactGate sits between your staff and every cloud LLM. It strips, **reversibly tokenizes**, and audits every sensitive entity before the API call leaves your perimeter — then re-inflates the model's answer on the way back. One drop-in OpenAI-compatible endpoint. AGPL-3.0. `docker compose up`.

```
Client (OpenAI SDK / Cursor / LibreChat)  ──base_url=http://localhost:8088/v1──▶  RedactGate
   detect (regex packs + Presidio + optional LLM-NER)  →  reversibly tokenize (AES-256-GCM vault)
   →  forward sanitized payload to AI_PROVIDER  →  re-inflate response  →  hash-chained audit
```

## Why it exists

A tax associate pasting a client's bank statement into ChatGPT has no safe alternative. RedactGate is that alternative: the raw PII never crosses the wire, redacted entities are reversibly tokenized in a local vault, **referential consistency** is preserved so answer quality survives, and every request is logged (entity *counts* only, never values) for a compliance officer to defend in an audit.

## Features (MVP)

- **Drop-in OpenAI-compatible proxy** — `POST /v1/chat/completions`, `/v1/responses`. The provider behind it is chosen by config, not the caller.
- **Multi-pass redaction engine** — regex jurisdiction packs + Presidio/spaCy + optional structured LLM-NER, merged by a confidence-weighted resolver.
- **Reversible token vault** — `Acme Ltd → [[ORG_7f3a]]`, AES-256-GCM, per-session DEK, referential consistency (same value → same placeholder within a session).
- **Stream-safe re-inflation** — placeholders are swapped back mid-SSE-stream, never emitted half-substituted.
- **Per-jurisdiction packs** — CA (SIN/BN/GST/NEQ), US (SSN/EIN/ITIN), EU (IBAN/VAT), UA (EDRPOU/ІПН), IRCC (UCI) — en/uk/ru/fr aware.
- **Hash-chained audit log** — append-only, zero-raw-value, one-click compliance export.
- **Policy engine** — tokenize / mask / hard-block per team & route; provider allow-lists.
- **Multi-provider gateway** — `AI_PROVIDER` ∈ anthropic, openai, gemini, azure, bedrock, do-genai, ollama.
- **Fidelity benchmark** — golden-set recall + LLM-judge answer-fidelity scorecard.
- **Local fallback** — `AI_PROVIDER=ollama` + regex/Presidio-only = fully air-gapped, no cloud, no key.

## Quickstart

```bash
cp .env.example .env
# generate the three 32-byte keys:
python -c "import os,base64;print('VAULT_MASTER_KEY='+base64.b64encode(os.urandom(32)).decode())" >> .env
python -c "import os,base64;print('FINGERPRINT_HMAC_KEY='+base64.b64encode(os.urandom(32)).decode())" >> .env
python -c "import os,base64;print('AUDIT_HMAC_KEY='+base64.b64encode(os.urandom(32)).decode())" >> .env

docker compose up -d            # postgres + redis + api + worker
# air-gapped local model:  docker compose --profile ollama up -d
# admin UI:                docker compose --profile web up -d   # http://localhost:3088
```

Point any OpenAI client at it:

```bash
curl http://localhost:8088/v1/chat/completions \
  -H "Authorization: Bearer $REDACTGATE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Summarize: SIN 046 454 286, John Smith."}]}'
# upstream sees [[SIN_xxxx]] / [[PERSON_xxxx]] — you get the real answer back.
```

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # add ".[dev,ner]" for Presidio
pytest                            # unit lane: no DB needed
RUN_INTEGRATION=1 pytest -m integration   # needs a live Postgres
uvicorn app.main:app --reload --port 8000
```

## Architecture

| Layer | Choice |
|---|---|
| Proxy / API | FastAPI (async streaming, OpenAI-schema parity) |
| Detection | regex packs + Presidio/spaCy + optional LLM-NER |
| Vault & audit | Postgres 16, AES-256-GCM, hash-chained append-only audit |
| Gateway | multi-provider adapter (`AI_PROVIDER` switch) |
| Admin UI | Next.js 16 (App Router) + Tailwind + shadcn/ui |
| Queue / eval | arq (Redis) for async fidelity runs |
| License | AGPL-3.0 |

See [`AGENTS.md`](AGENTS.md) for the module map and the contract between components, and `migrations/001_init.sql` for the canonical schema.

## License

AGPL-3.0-or-later. Commercial / AGPL-exemption licensing available for closed deployments — see `LICENSE`.
