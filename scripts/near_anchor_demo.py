#!/usr/bin/env python3
"""End-to-end demo: redact → audit chain → notarize head on NEAR → catch an insider rewrite.

    set -a; . ./.env.near; set +a           # from scripts/near_setup.py
    python scripts/near_anchor_demo.py      # add --out proof.json to keep the evidence

Runs the real RedactGate regex detection and AES-GCM token vault (no LLM, no database)
and writes to the real NEAR testnet contract named by NEAR_CONTRACT_ID.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.anchor.near import NearClient  # noqa: E402
from app.anchor.service import (  # noqa: E402
    AnchorConfig,
    anchor_head,
    check_chain_against_anchor,
    latest_anchor,
    stream_tag,
)
from app.audit import GENESIS_HASH, InMemoryAuditSink, compute_event_hash  # noqa: E402
from app.redaction.detect import Detector  # noqa: E402
from app.redaction.store import InMemoryTokenStore  # noqa: E402
from app.redaction.vault import Vault  # noqa: E402

PROMPTS = [
    "Summarize this T4 slip: SIN 193 456 787, employee email jane.t@gmail.com.",
    "Draft a GST/HST filing reminder for BN 123456782RT0001, reply to omar.h@outlook.com.",
    "Client called from 613-555-0142 about her US return, SSN 123-45-6789.",
]

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "r": "\033[31m", "y": "\033[33m",
     "c": "\033[36m", "x": "\033[0m"}
if not sys.stdout.isatty() and not os.environ.get("FORCE_COLOR"):
    C = dict.fromkeys(C, "")


def step(n: int, title: str) -> None:
    print(f"\n{C['b']}{C['c']}[{n}] {title}{C['x']}")


def pause(s: float) -> None:
    time.sleep(s * float(os.environ.get("DEMO_PACE", "1")))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="write a JSON proof (tx hashes, anchors, results)")
    args = ap.parse_args()

    cfg = AnchorConfig.from_settings()
    client = NearClient(network=cfg.network)
    audit_key = os.urandom(32)
    vault_key, fp_key = os.urandom(32), os.urandom(32)
    team_id = str(uuid.uuid4())
    stream = stream_tag(team_id, audit_key)
    proof: dict = {"contract": cfg.contract_id, "network": cfg.network, "stream": stream}

    step(1, "Staff prompts pass through RedactGate")
    detector = Detector()
    vault = Vault(InMemoryTokenStore(), master_key=vault_key, fingerprint_key=fp_key)
    sink = InMemoryAuditSink(hmac_key=audit_key)
    for text in PROMPTS:
        res = await detector.detect(text, pack_codes=["GENERIC", "CA", "US"])
        redacted = await vault.tokenize(text, res.spans, session_id="demo")
        counts: dict[str, int] = {}
        for s in res.spans:
            counts[s.entity_type] = counts.get(s.entity_type, 0) + 1
        ev = await sink.record(team_id=team_id, route="/v1/chat/completions",
                               provider="anthropic", entity_counts=counts, blocked=False)
        print(f"  {C['d']}staff   :{C['x']} {text}")
        print(f"  {C['d']}upstream:{C['x']} {C['g']}{redacted}{C['x']}")
        print(f"  {C['d']}audit   : {json.dumps(counts)}  hash {ev['event_hash'][:16]}…{C['x']}")
        pause(0.6)
    events = sink.events_for(team_id)
    head = events[-1]["event_hash"]

    step(2, f"Notarize the audit-chain head on NEAR {cfg.network}")
    print(f"  contract {C['b']}{cfg.contract_id}{C['x']}  stream {stream}")
    print("  on chain: only an opaque stream tag, the event count and the head hash")
    r = await anchor_head(client, cfg, stream=stream, events=len(events), head=head,
                          note="demo")
    print(f"  tx {C['y']}{r['explorer']}{C['x']}")
    proof["anchor_tx"] = r["explorer"]

    step(3, "Read the anchor back from the contract (view call, anyone can do this)")
    on_chain = await latest_anchor(client, cfg, stream)
    print(f"  events={on_chain['events']}  head={on_chain['head'][:16]}…  "
          f"block={on_chain['block_height']}  writer={on_chain['writer']}")
    proof["anchor"] = on_chain

    step(4, "Auditor checks the exported chain against NEAR")
    ok = check_chain_against_anchor(events, on_chain, hmac_key=audit_key)
    print(f"  {C['g']}{C['b']}PASS{C['x']}  {ok.detail}")
    proof["honest_check"] = ok.to_dict()
    pause(0.8)

    step(5, "An insider with the HMAC key hides that a SIN went to the cloud")
    forged = [dict(e) for e in events]
    forged[0]["entity_counts"] = {k: v for k, v in forged[0]["entity_counts"].items()
                                  if k != "SIN"} or {"EMAIL": 1}
    prev = GENESIS_HASH
    for e in forged:  # recompute every hash with the real key
        payload = {k: v for k, v in e.items() if k not in ("prev_hash", "event_hash")}
        e["prev_hash"], e["event_hash"] = prev, compute_event_hash(prev, payload, audit_key)
        prev = e["event_hash"]
    print(f"  event 1 audit edited: {json.dumps(events[0]['entity_counts'])} → "
          f"{json.dumps(forged[0]['entity_counts'])}")
    local_only = check_chain_against_anchor(forged, None, hmac_key=audit_key)
    print(f"  HMAC chain alone: {C['y']}links and hashes all valid{C['x']} "
          f"({local_only.status}) — the edit is invisible")
    bad = check_chain_against_anchor(forged, on_chain, hmac_key=audit_key)
    print(f"  against NEAR:     {C['r']}{C['b']}FAIL {bad.status}{C['x']}  {bad.detail}")
    proof["insider_check"] = bad.to_dict()

    if args.out:
        Path(args.out).write_text(json.dumps(proof, indent=2))
        print(f"\n{C['d']}proof written to {args.out}{C['x']}")
    return 0 if ok.ok and bad.status == "head_mismatch" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
