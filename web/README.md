# RedactGate — Admin Console

A self-contained **Next.js 15 (App Router)** admin UI for [RedactGate](../README.md), the
self-hosted PII/financial-redaction firewall. It consumes RedactGate's `/admin/*` JSON API
and renders a clean operator console.

> RedactGate stores **entity type counts only** — raw detected values are never persisted,
> returned, or rendered. Everything in this UI is counts/metadata.

## Sign-in, firms and roles (Clerk)

Every route requires a signed-in [Clerk](https://clerk.com) user. A firm is a Clerk
**organization**; the console maps the active organization to a RedactGate tenant on the
server (`POST /admin/tenants/resolve`), so the browser never chooses a tenant id.

| Clerk role | Can |
|---|---|
| `org:admin` | playground, create / enforce policies, read the audit trail |
| `org:compliance` (custom role) | read the audit trail, verify the hash chain, read policies |
| `org:member` | playground, dashboard |

Role checks run server-side in pages and server actions (`lib/session.ts`). Without Clerk
keys the console builds and serves only `/setup`.

## Pages

| Route | Purpose |
|---|---|
| `/` | Firm dashboard: redaction volume by type, hard-blocks, per-provider routing, chain check. |
| `/playground` | Send a synthetic note through the gateway to Claude: original with detected values, the exact payload the model received, the answer with values restored, and the audit row written. |
| `/audit` | Paginated, hash-chained audit ledger for the firm; chain verified on load. |
| `/policies` | Firm policies; admins create and enforce, compliance officers read. |
| `/benchmark` | Recall / precision / answer-fidelity scorecard. |
| `/sign-in`, `/sign-up`, `/firm` | Clerk sign-in, sign-up, firm selection or creation. |

## Configuration

See `.env.example`: `REDACTGATE_API_BASE`, `REDACTGATE_ADMIN_TOKEN` (server only),
`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`. All admin API calls are made
server-side; there is no browser-facing proxy to the admin API.

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
