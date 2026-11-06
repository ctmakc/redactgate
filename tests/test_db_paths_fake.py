"""DB-backed code paths exercised against a scripted fake session (no Postgres):

* ``app.audit.record_event`` / ``last_hash`` / ``verify_chain``
* ``app.redaction.policy.resolve_policy``
* small helpers in ``app.deps``
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app import audit as audit_mod
from app.audit import GENESIS_HASH, compute_event_hash, last_hash, record_event, verify_chain
from app.redaction.policy import resolve_policy
from app.schemas.entities import PolicyMode


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one_or_none(self):
        return self._v

    def scalars(self):
        return self

    def all(self):
        return list(self._v)


class FakeSession:
    """``execute`` pops queued values; a value that is an Exception is raised instead."""

    def __init__(self, results=()):
        self.results = list(results)
        self.statements: list = []
        self.added: list = []
        self.flushes = 0

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        v = self.results.pop(0)
        if isinstance(v, Exception):
            raise v
        return _Result(v)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1


TEAM = uuid.UUID(int=42)


# ── last_hash ─────────────────────────────────────────────────────────────────────


async def test_last_hash_genesis_when_no_rows():
    assert await last_hash(FakeSession([None]), TEAM) == GENESIS_HASH


async def test_last_hash_returns_row():
    assert await last_hash(FakeSession(["ab" * 32]), TEAM) == "ab" * 32


# ── record_event ──────────────────────────────────────────────────────────────────


async def test_record_event_links_to_previous_hash():
    prev = "cd" * 32
    s = FakeSession([None, prev])  # advisory lock, then last_hash
    ev = await record_event(
        s, team_id=TEAM, route="/v1/chat/completions", provider="openai",
        entity_counts={"SIN": 2}, blocked=0, latency_ms=12,
    )
    assert s.added == [ev] and s.flushes == 1
    assert ev.prev_hash == prev
    assert ev.blocked is False
    payload = audit_mod._event_payload(
        team_id=TEAM, api_key_id=None, session_id=None, route="/v1/chat/completions",
        provider="openai", entity_counts={"SIN": 2}, blocked=False, prompt_tokens=None,
        completion_tokens=None, latency_ms=12,
    )
    assert ev.event_hash == compute_event_hash(prev, payload, audit_mod._audit_key())
    # advisory lock is keyed by the team id
    assert "pg_advisory_xact_lock" in str(s.statements[0])


async def test_record_event_survives_lock_failure():
    s = FakeSession([RuntimeError("not postgres"), None])
    ev = await record_event(
        s, team_id=TEAM, route="r", provider="p", entity_counts={}, blocked=True,
    )
    assert ev.prev_hash == GENESIS_HASH
    assert ev.blocked is True


async def test_record_event_coerces_counts_to_int():
    s = FakeSession([None, None])
    ev = await record_event(
        s, team_id=TEAM, route="r", provider="p", entity_counts={"SIN": "3"}, blocked=False,
    )
    assert ev.entity_counts == {"SIN": 3}


# ── verify_chain ──────────────────────────────────────────────────────────────────


async def _build_chain(n: int) -> list:
    events = []
    prev = None
    for i in range(n):
        s = FakeSession([None, prev])
        ev = await record_event(
            s, team_id=TEAM, route="r", provider="openai",
            entity_counts={"EMAIL": i}, blocked=False, prompt_tokens=i,
        )
        ev.id = i + 1
        prev = ev.event_hash
        events.append(ev)
    return events


async def test_verify_chain_accepts_untampered_chain():
    events = await _build_chain(3)
    assert await verify_chain(FakeSession([events]), TEAM) is True


async def test_verify_chain_empty_is_valid():
    assert await verify_chain(FakeSession([[]]), TEAM) is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evs: setattr(evs[1], "provider", "anthropic"),
        lambda evs: setattr(evs[1], "entity_counts", {"EMAIL": 999}),
        lambda evs: setattr(evs[2], "blocked", True),
        lambda evs: setattr(evs[0], "prev_hash", "f" * 64),
        lambda evs: setattr(evs[2], "event_hash", "0" * 64),
        lambda evs: evs.pop(1),
        lambda evs: evs.reverse(),
    ],
    ids=["field", "counts", "blocked", "prev", "hash", "delete", "reorder"],
)
async def test_verify_chain_detects_tampering(mutate):
    events = await _build_chain(3)
    mutate(events)
    assert await verify_chain(FakeSession([events]), TEAM) is False


# ── resolve_policy ────────────────────────────────────────────────────────────────


async def test_resolve_policy_none_id_is_fail_safe_default():
    s = FakeSession()
    d = await resolve_policy(s, None)
    assert d.mode is PolicyMode.TOKENIZE
    assert d.blocked_types == [] and d.allowed_providers == [] and d.redact_types is None
    assert s.statements == []  # no DB round trip


async def test_resolve_policy_missing_row_is_default():
    d = await resolve_policy(FakeSession([None]), "pid")
    assert d.mode is PolicyMode.TOKENIZE


async def test_resolve_policy_maps_row():
    row = SimpleNamespace(mode="hard_block", blocked_types=("SIN",), allowed_providers=None)
    d = await resolve_policy(FakeSession([row]), "pid")
    assert d.mode is PolicyMode.HARD_BLOCK
    assert d.blocked is False
    assert d.blocked_types == ["SIN"]
    assert d.allowed_providers == []
    assert d.redact_types is None


# ── deps helpers ──────────────────────────────────────────────────────────────────


def test_build_vault_uses_configured_keys(monkeypatch):
    from app.config import settings
    from app.deps import build_vault
    from app.redaction.store import InMemoryTokenStore
    from app.redaction.vault import Vault

    v = build_vault(InMemoryTokenStore(), cfg=settings)
    assert isinstance(v, Vault)


async def test_build_vault_roundtrip(sample_pii_text):
    from app.config import settings
    from app.deps import build_vault, get_detector
    from app.redaction.store import InMemoryTokenStore

    store = InMemoryTokenStore()
    vault = build_vault(store, cfg=settings)
    det = get_detector()
    res = await det.detect(sample_pii_text, pack_codes=["GENERIC", "CA", "EU"])
    sid = await store.create_session(team_id="t", policy_id=None)
    red = await vault.tokenize(sample_pii_text, res.spans, session_id=sid)
    assert "john.smith@example.com" not in red
    assert await vault.detokenize(red, session_id=sid) == sample_pii_text


def test_get_active_provider_unknown_raises(monkeypatch):
    from app.config import settings
    from app.deps import get_active_provider
    from app.gateway.base import ProviderError

    monkeypatch.setattr(settings, "ai_provider", "does-not-exist")
    with pytest.raises(ProviderError):
        get_active_provider()


async def test_db_audit_sink_delegates_to_record_event():
    from app.deps import DBAuditSink

    s = FakeSession([None, None])
    ev = await DBAuditSink(s).record(
        team_id=TEAM, api_key_id=None, session_id=None, route="r", provider="p",
        entity_counts={"PHONE": 1}, blocked=False,
    )
    assert ev.entity_counts == {"PHONE": 1}
    assert s.added == [ev]
