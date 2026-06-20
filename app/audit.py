"""Tamper-evident audit log — a per-team hash chain over redaction events.

Every proxied call appends an :class:`~app.models.AuditEvent` whose ``event_hash`` is an
HMAC over ``prev_hash || canonical_json(payload)``. Because each link folds in the prior
hash, any retroactive edit to an event (or a deletion/reorder) breaks every subsequent
link, which :func:`verify_chain` detects.

SECURITY: the audit payload contains entity *type counts* only — never a raw entity
value. ``entity_counts`` is ``{TYPE: int}``. Nothing in this module logs, prints, or
persists a sensitive value.
"""

from __future__ import annotations

import hmac
import json
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol

from app.config import settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models import AuditEvent

# Chain anchor: the synthetic "previous hash" of a team's very first event.
GENESIS_HASH = "0" * 64


# ── Pure hashing primitives ─────────────────────────────────────────────────────


def canonical_json(payload: dict) -> str:
    """Deterministic JSON encoding used as the hashed message body.

    Keys are sorted and separators are compact so the same logical payload always
    produces byte-identical output regardless of dict insertion order.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_event_hash(prev_hash: str, payload: dict, hmac_key: bytes) -> str:
    """HMAC-SHA256 over ``prev_hash || canonical_json(payload)`` as lowercase hex."""
    msg = (prev_hash + canonical_json(payload)).encode()
    return hmac.new(hmac_key, msg, sha256).hexdigest()


def _event_payload(
    *,
    team_id: str,
    api_key_id: str | None,
    session_id: str | None,
    route: str,
    provider: str,
    entity_counts: dict[str, int],
    blocked: bool,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    latency_ms: int | None,
) -> dict:
    """Build the business-field dict that the chain hash commits to.

    Only type counts are included for entities — never raw values.
    """
    return {
        "team_id": str(team_id),
        "api_key_id": None if api_key_id is None else str(api_key_id),
        "session_id": None if session_id is None else str(session_id),
        "route": route,
        "provider": provider,
        "entity_counts": {str(k): int(v) for k, v in (entity_counts or {}).items()},
        "blocked": bool(blocked),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
    }


def _audit_key() -> bytes:
    return settings.key_bytes("audit_hmac_key")


# ── DB-backed chain ─────────────────────────────────────────────────────────────


async def last_hash(session, team_id) -> str:
    """Return the most recent ``event_hash`` for ``team_id``, else :data:`GENESIS_HASH`."""
    from sqlalchemy import select

    from app.models import AuditEvent

    result = await session.execute(
        select(AuditEvent.event_hash)
        .where(AuditEvent.team_id == team_id)
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row if row is not None else GENESIS_HASH


async def record_event(
    session,
    *,
    team_id,
    api_key_id=None,
    session_id=None,
    route: str,
    provider: str,
    entity_counts: dict[str, int],
    blocked: bool,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
) -> AuditEvent:
    """Append a new audit event, linking it into the team's hash chain.

    Serialized per team with a transaction-scoped advisory lock so concurrent requests
    cannot interleave the (read last_hash → compute → INSERT) sequence and fork the chain.
    The lock auto-releases on commit/rollback of the surrounding transaction.
    """
    from sqlalchemy import text

    from app.models import AuditEvent

    # Per-team critical section (no-op on non-Postgres backends, e.g. a hypothetical sqlite).
    try:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:t))"), {"t": str(team_id)}
        )
    except Exception:  # noqa: BLE001 - lock is a hardening measure, never fatal
        pass

    payload = _event_payload(
        team_id=team_id,
        api_key_id=api_key_id,
        session_id=session_id,
        route=route,
        provider=provider,
        entity_counts=entity_counts,
        blocked=blocked,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
    )
    prev = await last_hash(session, team_id)
    event_hash = compute_event_hash(prev, payload, _audit_key())

    event = AuditEvent(
        team_id=team_id,
        api_key_id=api_key_id,
        session_id=session_id,
        route=route,
        provider=provider,
        entity_counts=payload["entity_counts"],
        blocked=bool(blocked),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        prev_hash=prev,
        event_hash=event_hash,
    )
    session.add(event)
    await session.flush()
    return event


async def verify_chain(session, team_id) -> bool:
    """Recompute the team's chain in creation order; return False on any mismatch.

    A mismatch means an event was altered, deleted, reordered, or inserted out of band —
    i.e. tampering. The first event must chain from :data:`GENESIS_HASH`.
    """
    from sqlalchemy import select

    from app.models import AuditEvent

    result = await session.execute(
        select(AuditEvent).where(AuditEvent.team_id == team_id).order_by(AuditEvent.id.asc())
    )
    events = result.scalars().all()

    key = _audit_key()
    prev = GENESIS_HASH
    for event in events:
        payload = _event_payload(
            team_id=event.team_id,
            api_key_id=event.api_key_id,
            session_id=event.session_id,
            route=event.route,
            provider=event.provider,
            entity_counts=event.entity_counts,
            blocked=event.blocked,
            prompt_tokens=event.prompt_tokens,
            completion_tokens=event.completion_tokens,
            latency_ms=event.latency_ms,
        )
        if event.prev_hash != prev:
            return False
        expected = compute_event_hash(prev, payload, key)
        if event.event_hash != expected:
            return False
        prev = event.event_hash
    return True


# ── Sink abstraction (lets routes record events without a DB in tests) ──────────


class AuditSink(Protocol):
    """Minimal interface routes use to emit an audit event.

    Implementations compute the same hash chain; the DB-backed one persists, the
    in-memory one keeps a list so the proxy can be tested without Postgres.
    """

    async def record(
        self,
        *,
        team_id,
        api_key_id=None,
        session_id=None,
        route: str,
        provider: str,
        entity_counts: dict[str, int],
        blocked: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> object: ...


class DBAuditSink:
    """:class:`AuditSink` backed by a live SQLAlchemy session via :func:`record_event`."""

    def __init__(self, session) -> None:
        self._session = session

    async def record(
        self,
        *,
        team_id,
        api_key_id=None,
        session_id=None,
        route: str,
        provider: str,
        entity_counts: dict[str, int],
        blocked: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> AuditEvent:
        return await record_event(
            self._session,
            team_id=team_id,
            api_key_id=api_key_id,
            session_id=session_id,
            route=route,
            provider=provider,
            entity_counts=entity_counts,
            blocked=blocked,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )


class InMemoryAuditSink:
    """DB-less :class:`AuditSink` that maintains the identical hash chain in memory.

    Each recorded event is a plain dict with ``prev_hash``/``event_hash`` populated the
    same way :func:`record_event` does, so :meth:`verify` exercises the same tamper logic
    as :func:`verify_chain`. Useful for testing routes and for air-gapped single-process
    runs.
    """

    def __init__(self, hmac_key: bytes | None = None) -> None:
        self._key = hmac_key if hmac_key is not None else _audit_key()
        # team_id (as str) -> ordered list of event dicts
        self._events: dict[str, list[dict]] = {}

    @property
    def events(self) -> list[dict]:
        """All recorded events across teams, in insertion order per team."""
        out: list[dict] = []
        for team_events in self._events.values():
            out.extend(team_events)
        return out

    def events_for(self, team_id) -> list[dict]:
        return list(self._events.get(str(team_id), []))

    def last_hash(self, team_id) -> str:
        chain = self._events.get(str(team_id))
        return chain[-1]["event_hash"] if chain else GENESIS_HASH

    async def record(
        self,
        *,
        team_id,
        api_key_id=None,
        session_id=None,
        route: str,
        provider: str,
        entity_counts: dict[str, int],
        blocked: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> dict:
        payload = _event_payload(
            team_id=team_id,
            api_key_id=api_key_id,
            session_id=session_id,
            route=route,
            provider=provider,
            entity_counts=entity_counts,
            blocked=blocked,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        prev = self.last_hash(team_id)
        event_hash = compute_event_hash(prev, payload, self._key)
        record = {
            **payload,
            "prev_hash": prev,
            "event_hash": event_hash,
        }
        self._events.setdefault(str(team_id), []).append(record)
        return record

    def verify(self, team_id) -> bool:
        """Recompute the in-memory chain for ``team_id``; False on any mismatch."""
        prev = GENESIS_HASH
        for event in self._events.get(str(team_id), []):
            payload = {
                k: v
                for k, v in event.items()
                if k not in ("prev_hash", "event_hash")
            }
            if event["prev_hash"] != prev:
                return False
            expected = compute_event_hash(prev, payload, self._key)
            if event["event_hash"] != expected:
                return False
            prev = event["event_hash"]
        return True
