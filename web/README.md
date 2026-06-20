# RedactGate — Admin Console

A self-contained **Next.js 15 (App Router)** admin UI for [RedactGate](../README.md), the
self-hosted PII/financial-redaction firewall. It consumes RedactGate's `/admin/*` JSON API
and renders a clean operator console.

> RedactGate stores **entity type counts only** — raw detected values are never persisted,
> returned, or rendered. Everything in this UI is counts/metadata.

## Pages

| Route | Purpose |
|---|---|
| `/` | Dashboard — redaction volume, entity-type breakdown, hard-block count, per-provider routing, latest benchmark score. |
| `/audit` | Searchable, paginated, hash-chained audit ledger (route, provider, entity counts, blocked, latency, time). |
| `/policies` | List policies and create one (name, mode `tokenize`/`mask`/`hard_block`, blocked types, allowed providers). |
| `/benchmark` | Recall / precision / answer-fidelity scorecard per provider and golden-set pack. |

## Configuration

| Env var | Scope | Meaning |
|---|---|---|
| `REDACTGATE_API_BASE` | server | Base URL of the RedactGate FastAPI service (e.g. `http://localhost:8080`). |
| `REDACTGATE_ADMIN_TOKEN` | server | Sent as `X-Admin-Token`. **Never** shipped to the browser. |
| `NEXT_PUBLIC_API_BASE` | client | Optional. Defaults to the in-app `/api/proxy` route handler, which injects the admin token server-side. |

Copy `.env.example` to `.env` and fill it in. Server Components fetch directly from
`REDACTGATE_API_BASE`; the few client-side calls go through `/api/proxy/admin/*`, where the
admin token is attached server-side so it never enters the browser bundle.

## Develop

```bash
npm install
npm run dev      # http://localhost:3000
```

## Build & run

```bash
npm run build
npm run start    # next start -p 3000
```

## Docker

```bash
docker build -t redactgate-admin ./web
docker run --rm -p 3000:3000 \
  -e REDACTGATE_API_BASE=http://host.docker.internal:8080 \
  -e REDACTGATE_ADMIN_TOKEN=your-admin-token \
  redactgate-admin
```

The image is a multi-stage `node:22-alpine` build using Next.js `output: "standalone"`,
running as a non-root user and serving on port 3000.

## Notes

- No heavy chart library — the dashboard uses CSS bars (`components/BarList.tsx`).
- Tokens/placeholders are rendered in a monospace style to match the `[[TYPE_hex]]` grammar.
- If the API is unreachable, every page degrades to a coherent empty shell with a banner
  rather than crashing.
