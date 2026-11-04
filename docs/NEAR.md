# RedactGate × NEAR: audit anchor

Built at **NEAR HACKATHON 2026** (Build Beyond Boundaries), track **Privacy & Private AI**.

## Problem

Accountants, tax firms and immigration consultants paste client data into cloud LLMs.
RedactGate sits in front of the LLM: it detects SINs, SSNs, business numbers, emails,
phones and similar identifiers, swaps them for reversible tokens, and writes an audit
event for every request (entity *type counts* only, never values).

That audit log is an HMAC hash chain. It catches accidental corruption and outside
attackers. It cannot protect against the people who run the gateway: anyone holding
`AUDIT_HMAC_KEY` can edit an old event ("no SIN was sent that day") and recompute every
later hash. The local verifier then reports a perfectly valid chain.

## What NEAR adds

Every few minutes the RedactGate worker writes the current head of each team's chain to a
small NEAR contract: `(stream, events, head)`. The contract is append-only (the event
count must grow) and only authorized writers can call it. After a head is on NEAR, any
rewrite of the events it covers changes the hash at that position, and the auditor's
check fails with `head_mismatch`, even when the forger used the real HMAC key.

What goes on chain: an opaque stream tag (HMAC of the team id), the event count and the
64-hex head. Prompts, entity values, entity counts and team names stay inside the
perimeter. The gateway signs with a NEAR **function-call access key** that may only call
`anchor` on the contract and cannot transfer funds.

```
staff prompt ─▶ RedactGate (detect → tokenize → upstream LLM → re-inflate)
                    │
                    └─ audit_event  prev_hash ─▶ event_hash  (HMAC chain, local DB)
                                                     │  every N min (arq cron)
                                                     ▼
                          NEAR contract anchor.redactgate.testnet
                          anchor(stream, events, head)   get_latest(stream)
                                                     ▲
auditor: exported chain + get_latest  ───────────────┘  → ok | head_mismatch | truncated | broken_link | bad_hash
```

## Live deployment (testnet)

| What | Where |
|---|---|
| Contract | [`anchor.redactgate.testnet`](https://testnet.nearblocks.io/address/anchor.redactgate.testnet) |
| Deploy tx | [Cigf4h4d…](https://testnet.nearblocks.io/txns/Cigf4h4dfyXUW6ZigQP5MgWrs8GYi3ETveHgdF1691yf) |
| Anchor-only key added | [B6T2fscL…](https://testnet.nearblocks.io/txns/B6T2fscL9HziYMxjRNG4YPmCYesZ1x82MGn9pkayPZD1) |
| Demo anchor | see the latest run in [`docs/near-demo-proof.json`](near-demo-proof.json) |

Read the contract yourself, no account needed:

```bash
curl -s https://rpc.testnet.near.org -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,
 "method":"query","params":{"request_type":"call_function","finality":"final",
 "account_id":"anchor.redactgate.testnet","method_name":"total_anchors","args_base64":"e30="}}'
```

## Judge path: run it in five minutes

Requirements: Python 3.12, internet. No wallet app, no Rust, no Docker.

```bash
git clone -b near-hackathon https://github.com/ctmakc/redactgate && cd redactgate
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"

pytest -q tests/test_near_anchor.py      # 14 tests: borsh layout, signing, tamper check, cron
make near-setup                          # free testnet account via faucet + deploy + anchor-only key → .env.near
make near-demo                           # redact 3 prompts, anchor head on NEAR, catch an insider rewrite
```

`make near-demo` prints the NearBlocks link of the anchor transaction and exits 0 only if
(a) the honest chain matches the on-chain head and (b) the forged chain fails with
`head_mismatch`. To check against our deployed contract instead of your own, set the
variables from `.env.example` by hand (you will need your own writer key; reads work
without one).

Contract tests (optional, needs Rust 1.86 + `wasm32-unknown-unknown`):

```bash
make near-contract-test   # 5 unit tests: append-only, writer allow-list, head format
make near-contract        # rebuilds contracts/audit-anchor/res/audit_anchor.wasm
```

## Use it in a RedactGate deployment

```bash
NEAR_ANCHOR_ENABLED=true
NEAR_NETWORK=testnet
NEAR_CONTRACT_ID=anchor.<you>.testnet
NEAR_SIGNER_ID=<you>.testnet
NEAR_PRIVATE_KEY=ed25519:...        # the function-call key from near_setup.py
NEAR_ANCHOR_INTERVAL_MIN=10
```

- worker: `arq app.worker.WorkerSettings` runs `anchor_audit_heads` every N minutes; teams
  whose chain did not grow are skipped (idempotent);
- by hand: `python -m app.anchor run-once`;
- audit: `python -m app.anchor verify --team <uuid>` → JSON verdict, exit 2 on mismatch;
- status: `python -m app.anchor status --team <uuid>`.

## Code map (all new in this hackathon)

| Path | What |
|---|---|
| `contracts/audit-anchor/src/lib.rs` | NEAR contract (near-sdk 5): `anchor`, `get_latest`, `get_anchor`, writer allow-list, NEP-297 `EVENT_JSON` logs |
| `app/anchor/near.py` | NEAR JSON-RPC client: base58, borsh `Transaction`, ed25519 signing, view calls (httpx + PyNaCl, ~250 lines) |
| `app/anchor/service.py` | stream tags, anchoring pass over the DB, `check_chain_against_anchor` |
| `app/anchor/__main__.py` | CLI `run-once / verify / status` |
| `app/worker.py` | arq cron job `anchor_audit_heads` |
| `scripts/near_setup.py` | faucet account + atomic create/deploy/init + function-call key |
| `scripts/near_anchor_demo.py` | end-to-end demo used in the video |
| `tests/test_near_anchor.py` | 14 tests, no network |

Full diff against the pre-hackathon baseline:
<https://github.com/ctmakc/redactgate/compare/pre-hackathon...near-hackathon>

## Disclosure

- **Baseline.** RedactGate itself (redaction engine, vault, HMAC audit chain, proxy, console)
  existed before the hackathon; tag `pre-hackathon` marks it (commit `408495c`,
  17 Oct 2026). Everything listed in the code map above was written from 4 Nov 2026 on
  branch `near-hackathon`.
- **Earlier events.** The RedactGate core was entered in Build North (Devpost, Oct 2026). The
  NEAR anchor is new and was not part of any other submission. RedactGate has no product
  on another chain.
- **Libraries.** near-sdk 5 (contract), httpx, PyNaCl, existing RedactGate dependencies. No
  templates.
- **AI assistance.** Code, tests and docs in this branch were written with Claude Code (Anthropic)
  and reviewed by the author.
- **License.** AGPL-3.0-or-later (see `LICENSE`).
