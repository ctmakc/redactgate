"""Notarize RedactGate audit-chain heads on NEAR and check a chain against them.

Why: the audit chain is an HMAC chain. It catches accidental corruption and outsiders,
but an insider who holds ``AUDIT_HMAC_KEY`` can rewrite an event and recompute every
later hash — :func:`app.audit.verify_chain` would still say ``True``. Writing the chain
head to a NEAR contract every few minutes fixes a point in time that nobody at the
operator can move afterwards: if anchored history is rewritten, the head at the anchored
position no longer matches the one on chain.

What leaves the perimeter: an opaque stream tag (HMAC of the team id), the event count
and the 64-hex head. Never team names, prompts, entity values or entity counts.
"""

from __future__ import annotations

import hmac
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any

from app.anchor.near import KeyPair, NearClient, function_call, success_json
from app.audit import GENESIS_HASH, compute_event_hash
from app.config import Settings, settings


def stream_tag(team_id: Any, key: bytes) -> str:
    """Opaque, stable per-team stream id for the contract (no team id on chain)."""
    mac = hmac.new(key, b"redactgate-near-anchor:" + str(team_id).encode(), sha256)
    return "rg_" + mac.hexdigest()[:24]


@dataclass(slots=True)
class AnchorConfig:
    network: str
    contract_id: str
    signer_id: str
    private_key: str

    @classmethod
    def from_settings(cls, cfg: Settings = settings) -> AnchorConfig:
        missing = [
            n for n in ("near_contract_id", "near_signer_id", "near_private_key")
            if not getattr(cfg, n)
        ]
        if missing:
            raise RuntimeError("NEAR anchoring is not configured: set " + ", ".join(
                m.upper() for m in missing))
        return cls(
            network=cfg.near_network,
            contract_id=cfg.near_contract_id,
            signer_id=cfg.near_signer_id,
            private_key=cfg.near_private_key,
        )

    def client(self) -> NearClient:
        return NearClient(network=self.network)


async def anchor_head(
    client: NearClient, cfg: AnchorConfig, *, stream: str, events: int, head: str,
    note: str | None = None,
) -> dict:
    """Write one head to the contract; returns ``{tx_hash, explorer, anchor}``."""
    key = KeyPair.from_string(cfg.private_key)
    res = await client.send(
        signer_id=cfg.signer_id,
        key=key,
        receiver_id=cfg.contract_id,
        actions=[function_call(
            "anchor", {"stream": stream, "events": events, "head": head, "note": note})],
    )
    h = res["_tx_hash"]
    return {"tx_hash": h, "explorer": client.explorer_tx(h), "anchor": success_json(res)}


async def latest_anchor(client: NearClient, cfg: AnchorConfig, stream: str) -> dict | None:
    return await client.view(cfg.contract_id, "get_latest", {"stream": stream})


# ── verification ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class AnchorCheck:
    status: str  # "ok" | "no_anchor" | "broken_link" | "truncated" | "head_mismatch" | "bad_hash"
    detail: str
    local_events: int
    anchored_events: int | None = None
    anchored_head: str | None = None
    local_head_at_anchor: str | None = None
    first_bad_position: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        return asdict(self)


_HASHED_FIELDS = (
    "team_id", "api_key_id", "session_id", "route", "provider", "entity_counts",
    "blocked", "prompt_tokens", "completion_tokens", "latency_ms",
)


def check_chain_against_anchor(
    events: list[dict], anchor: dict | None, *, hmac_key: bytes | None = None
) -> AnchorCheck:
    """Compare an exported chain (oldest first) with an on-chain anchor.

    1. every ``prev_hash`` links to the previous ``event_hash`` (no deletions/reorders);
    2. with ``hmac_key``: every ``event_hash`` is recomputed from the event fields;
    3. the chain still has at least ``anchor.events`` events, and the hash at that
       position equals ``anchor.head`` — the part an insider with the key cannot fake.
    """
    n = len(events)
    prev = GENESIS_HASH
    for i, ev in enumerate(events, start=1):
        if ev.get("prev_hash") != prev:
            return AnchorCheck("broken_link", f"event {i} does not link to event {i - 1}", n,
                               first_bad_position=i)
        if hmac_key is not None:
            payload = {k: ev.get(k) for k in _HASHED_FIELDS}
            payload["entity_counts"] = {
                str(k): int(v) for k, v in (payload["entity_counts"] or {}).items()}
            for k in ("team_id", "api_key_id", "session_id"):
                if payload[k] is not None:
                    payload[k] = str(payload[k])
            payload["blocked"] = bool(payload["blocked"])
            if compute_event_hash(prev, payload, hmac_key) != ev.get("event_hash"):
                return AnchorCheck("bad_hash", f"event {i} hash does not match its content", n,
                                   first_bad_position=i)
        prev = ev.get("event_hash")

    if not anchor:
        return AnchorCheck("no_anchor", "no anchor on chain for this stream yet", n)
    a_events, a_head = int(anchor["events"]), anchor["head"]
    if n < a_events:
        return AnchorCheck("truncated", f"chain has {n} events, {a_events} were anchored", n,
                           a_events, a_head)
    local = events[a_events - 1].get("event_hash")
    if local != a_head:
        return AnchorCheck(
            "head_mismatch",
            f"event {a_events} hash differs from the head notarized on NEAR at block "
            f"{anchor.get('block_height')}: anchored history was rewritten",
            n, a_events, a_head, local, first_bad_position=a_events)
    return AnchorCheck(
        "ok", f"{a_events} of {n} events match the NEAR anchor (block "
        f"{anchor.get('block_height')})", n, a_events, a_head, local)


# ── DB-backed passes (worker / CLI) ─────────────────────────────────────────────


async def team_heads(session) -> list[tuple[Any, int, str]]:
    """``[(team_id, event_count, head_hash)]`` for every team with audit events."""
    from sqlalchemy import func, select

    from app.models import AuditEvent

    counts = (
        await session.execute(
            select(AuditEvent.team_id, func.count(), func.max(AuditEvent.id))
            .group_by(AuditEvent.team_id)
        )
    ).all()
    out = []
    for team_id, count, max_id in counts:
        head = (
            await session.execute(select(AuditEvent.event_hash).where(AuditEvent.id == max_id))
        ).scalar_one()
        out.append((team_id, int(count), head))
    return out


async def run_anchor_pass(session, cfg: AnchorConfig | None = None, *, note: str = "cron",
                          client: NearClient | None = None) -> list[dict]:
    """Anchor every team whose chain grew since its last anchor. Idempotent."""
    cfg = cfg or AnchorConfig.from_settings()
    client = client or cfg.client()
    key = settings.key_bytes("audit_hmac_key")
    results = []
    for team_id, count, head in await team_heads(session):
        stream = stream_tag(team_id, key)
        last = await latest_anchor(client, cfg, stream)
        if last and int(last["events"]) >= count:
            results.append({"stream": stream, "events": count, "skipped": "up to date"})
            continue
        r = await anchor_head(client, cfg, stream=stream, events=count, head=head, note=note)
        results.append({"stream": stream, "events": count, **r})
    return results


async def export_team_events(session, team_id) -> list[dict]:
    from sqlalchemy import select

    from app.models import AuditEvent

    rows = (
        await session.execute(
            select(AuditEvent).where(AuditEvent.team_id == team_id).order_by(AuditEvent.id)
        )
    ).scalars().all()
    return [
        {**{k: getattr(r, k) for k in _HASHED_FIELDS}, "prev_hash": r.prev_hash,
         "event_hash": r.event_hash}
        for r in rows
    ]


async def verify_team(session, team_id, cfg: AnchorConfig | None = None,
                      client: NearClient | None = None) -> AnchorCheck:
    cfg = cfg or AnchorConfig.from_settings()
    client = client or cfg.client()
    key = settings.key_bytes("audit_hmac_key")
    events = await export_team_events(session, team_id)
    anchor = await latest_anchor(client, cfg, stream_tag(team_id, key))
    return check_chain_against_anchor(events, anchor, hmac_key=key)
