"""Unit tests for the tamper-evident audit log (``app.audit``).

DB-less surface:
  * ``canonical_json`` / ``compute_event_hash`` determinism and sensitivity to any field
  * ``InMemoryAuditSink`` builds a valid hash chain across N events
  * ``verify()`` detects mutation of a middle event
  * audit payload stores only ``type -> int`` counts, never raw entity values

The DB-backed ``record_event`` / ``verify_chain`` need Postgres; those are exercised under
``@integration_only`` (skipped in the unit lane).
"""

from __future__ import annotations

import copy

import pytest

from app.audit import (
    GENESIS_HASH,
    InMemoryAuditSink,
    _event_payload,
    canonical_json,
    compute_event_hash,
)
from tests.conftest import integration_only

_KEY = b"audit-test-key-0123456789abcdef0"[:32]
_KEY2 = b"different-audit-key-0123456789ab"[:32]


def _record_args(**over):
    base = dict(
        team_id="team-A",
        api_key_id="key-1",
        session_id="sess-1",
        route="/v1/chat/completions",
        provider="openai",
        entity_counts={"EMAIL": 2, "SIN": 1},
        blocked=False,
        prompt_tokens=10,
        completion_tokens=20,
        latency_ms=42,
    )
    base.update(over)
    return base


# ── canonical_json ──────────────────────────────────────────────────────────────


def test_canonical_json_is_order_independent():
    a = {"b": 1, "a": 2, "nested": {"y": 1, "x": 2}}
    b = {"a": 2, "nested": {"x": 2, "y": 1}, "b": 1}
    assert canonical_json(a) == canonical_json(b)


def test_canonical_json_is_compact_and_sorted():
    out = canonical_json({"b": 1, "a": 2})
    assert out == '{"a":2,"b":1}'  # sorted keys, no spaces


def test_canonical_json_distinguishes_different_payloads():
    assert canonical_json({"a": 1}) != canonical_json({"a": 2})


# ── compute_event_hash ──────────────────────────────────────────────────────────


def test_compute_event_hash_is_deterministic():
    payload = {"a": 1, "b": [1, 2, 3]}
    h1 = compute_event_hash(GENESIS_HASH, payload, _KEY)
    h2 = compute_event_hash(GENESIS_HASH, dict(payload), _KEY)
    assert h1 == h2


def test_compute_event_hash_is_hex_sha256_length():
    h = compute_event_hash(GENESIS_HASH, {"a": 1}, _KEY)
    assert len(h) == 64
    int(h, 16)  # valid lowercase hex


def test_compute_event_hash_changes_with_prev_hash():
    payload = {"a": 1}
    h1 = compute_event_hash(GENESIS_HASH, payload, _KEY)
    h2 = compute_event_hash("f" * 64, payload, _KEY)
    assert h1 != h2


def test_compute_event_hash_changes_with_payload():
    h1 = compute_event_hash(GENESIS_HASH, {"a": 1}, _KEY)
    h2 = compute_event_hash(GENESIS_HASH, {"a": 2}, _KEY)
    assert h1 != h2


def test_compute_event_hash_changes_with_key():
    payload = {"a": 1}
    h1 = compute_event_hash(GENESIS_HASH, payload, _KEY)
    h2 = compute_event_hash(GENESIS_HASH, payload, _KEY2)
    assert h1 != h2


@pytest.mark.parametrize(
    "field, new_value",
    [
        ("team_id", "team-B"),
        ("api_key_id", "key-2"),
        ("session_id", "sess-2"),
        ("route", "/v1/responses"),
        ("provider", "anthropic"),
        ("entity_counts", {"EMAIL": 3}),
        ("blocked", True),
        ("prompt_tokens", 11),
        ("completion_tokens", 21),
        ("latency_ms", 43),
    ],
)
def test_changing_any_business_field_changes_chain_hash(field, new_value):
    """Every committed field must affect the resulting event hash."""
    base_payload = _event_payload(**_record_args())
    changed_payload = _event_payload(**_record_args(**{field: new_value}))

    assert base_payload != changed_payload  # sanity: the field actually changed
    h_base = compute_event_hash(GENESIS_HASH, base_payload, _KEY)
    h_changed = compute_event_hash(GENESIS_HASH, changed_payload, _KEY)
    assert h_base != h_changed


# ── InMemoryAuditSink chain ─────────────────────────────────────────────────────


async def test_first_event_chains_from_genesis():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    rec = await sink.record(**_record_args())
    assert rec["prev_hash"] == GENESIS_HASH
    assert sink.verify("team-A") is True


async def test_chain_links_n_events():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    recs = []
    for i in range(5):
        recs.append(await sink.record(**_record_args(session_id=f"s{i}", latency_ms=i)))

    # Each event's prev_hash equals the previous event's event_hash.
    assert recs[0]["prev_hash"] == GENESIS_HASH
    for prev, cur in zip(recs, recs[1:], strict=False):
        assert cur["prev_hash"] == prev["event_hash"]
    assert sink.last_hash("team-A") == recs[-1]["event_hash"]
    assert sink.verify("team-A") is True


async def test_chain_is_independent_per_team():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    await sink.record(**_record_args(team_id="team-A"))
    rec_b = await sink.record(**_record_args(team_id="team-B"))
    # team-B's first event still chains from genesis, not team-A's hash.
    assert rec_b["prev_hash"] == GENESIS_HASH
    assert sink.verify("team-A") is True
    assert sink.verify("team-B") is True
    assert len(sink.events_for("team-A")) == 1
    assert len(sink.events_for("team-B")) == 1


async def test_events_property_aggregates_all_teams():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    await sink.record(**_record_args(team_id="team-A"))
    await sink.record(**_record_args(team_id="team-B"))
    assert len(sink.events) == 2


async def test_last_hash_genesis_when_empty():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    assert sink.last_hash("nobody") == GENESIS_HASH


# ── verify(): tamper detection ──────────────────────────────────────────────────


async def test_verify_detects_mutation_of_middle_event_field():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    for i in range(5):
        await sink.record(**_record_args(session_id=f"s{i}"))
    assert sink.verify("team-A") is True

    # Mutate a business field of the middle event WITHOUT recomputing its hash.
    sink._events["team-A"][2]["provider"] = "tampered-provider"
    assert sink.verify("team-A") is False


async def test_verify_detects_mutated_entity_counts():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    for i in range(3):
        await sink.record(**_record_args(session_id=f"s{i}"))
    sink._events["team-A"][1]["entity_counts"] = {"EMAIL": 99}
    assert sink.verify("team-A") is False


async def test_verify_detects_swapped_event_hash():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    for i in range(3):
        await sink.record(**_record_args(session_id=f"s{i}"))
    # Forge the stored event_hash of an event.
    sink._events["team-A"][1]["event_hash"] = "a" * 64
    assert sink.verify("team-A") is False


async def test_verify_detects_reordered_events():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    for i in range(3):
        await sink.record(**_record_args(session_id=f"s{i}"))
    chain = sink._events["team-A"]
    chain[0], chain[1] = chain[1], chain[0]
    assert sink.verify("team-A") is False


async def test_verify_detects_deleted_event():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    for i in range(4):
        await sink.record(**_record_args(session_id=f"s{i}"))
    # Removing a middle event breaks the prev_hash linkage of the next.
    del sink._events["team-A"][1]
    assert sink.verify("team-A") is False


async def test_verify_with_wrong_key_fails():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    await sink.record(**_record_args())
    # A verifier with a different key cannot validate the chain.
    other = InMemoryAuditSink(hmac_key=_KEY2)
    other._events = sink._events
    assert other.verify("team-A") is False


async def test_verify_empty_chain_is_trivially_valid():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    assert sink.verify("team-A") is True


# ── SECURITY: counts only, no raw values ────────────────────────────────────────


async def test_event_stores_only_type_to_int_counts():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    rec = await sink.record(**_record_args(entity_counts={"EMAIL": 2, "SIN": 1}))
    counts = rec["entity_counts"]
    assert counts == {"EMAIL": 2, "SIN": 1}
    for k, v in counts.items():
        assert isinstance(k, str)
        assert isinstance(v, int)


async def test_event_counts_coerced_to_int_and_str():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    # Non-int-ish / non-str-ish values get normalized by _event_payload.
    rec = await sink.record(**_record_args(entity_counts={"EMAIL": True}))
    counts = rec["entity_counts"]
    assert list(counts.keys()) == ["EMAIL"]
    assert all(isinstance(v, int) for v in counts.values())


async def test_event_payload_contains_no_raw_value_leak():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    rec = await sink.record(**_record_args(entity_counts={"SIN": 1}))
    # The serialized event must not contain anything resembling a raw value beyond
    # the structural fields; entity_counts holds counts only.
    serialized = canonical_json(
        {k: v for k, v in rec.items() if k not in ("prev_hash", "event_hash")}
    )
    assert "046 454 286" not in serialized
    assert "john.smith@example.com" not in serialized


async def test_record_does_not_mutate_caller_entity_counts():
    sink = InMemoryAuditSink(hmac_key=_KEY)
    original = {"EMAIL": 2}
    snapshot = copy.deepcopy(original)
    await sink.record(**_record_args(entity_counts=original))
    assert original == snapshot


# ── DB-backed path (Postgres) ───────────────────────────────────────────────────


@integration_only()
async def test_record_event_and_verify_chain_db():  # pragma: no cover - needs Postgres
    from app.audit import record_event, verify_chain
    from app.db import session_scope

    async with session_scope() as session:
        await record_event(
            session,
            team_id="team-A",
            route="/v1/chat/completions",
            provider="openai",
            entity_counts={"EMAIL": 1},
            blocked=False,
        )
        assert await verify_chain(session, "team-A") is True
