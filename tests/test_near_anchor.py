"""NEAR audit anchoring: encoding, signing and the tamper check (no network)."""

from __future__ import annotations

import base64
import hashlib
import json

import httpx
import pytest
import respx
from nacl.signing import VerifyKey

from app.anchor import near
from app.anchor.service import (
    AnchorConfig,
    anchor_head,
    check_chain_against_anchor,
    stream_tag,
)
from app.audit import InMemoryAuditSink

KEY = b"k" * 32


async def _chain(n: int, key: bytes = KEY) -> list[dict]:
    sink = InMemoryAuditSink(hmac_key=key)
    for i in range(n):
        await sink.record(team_id="team-1", route="/v1/chat/completions", provider="ollama",
                          entity_counts={"SIN": i % 2, "EMAIL": 1}, blocked=False)
    return sink.events_for("team-1")


def _rechain(events: list[dict], key: bytes) -> list[dict]:
    """What an insider with AUDIT_HMAC_KEY can do: recompute every hash after an edit."""
    from app.audit import GENESIS_HASH, compute_event_hash

    prev, out = GENESIS_HASH, []
    for ev in events:
        payload = {k: v for k, v in ev.items() if k not in ("prev_hash", "event_hash")}
        h = compute_event_hash(prev, payload, key)
        out.append({**payload, "prev_hash": prev, "event_hash": h})
        prev = h
    return out


def test_base58_roundtrip():
    for raw in (b"", b"\0\0abc", hashlib.sha256(b"x").digest(), bytes(range(64))):
        assert near.b58decode(near.b58encode(raw)) == raw
    assert near.b58encode(b"\0" * 32) == "1" * 32


def test_keypair_string_roundtrip_and_signature():
    kp = near.KeyPair.generate()
    again = near.KeyPair.from_string(kp.secret_key_str)
    assert again.public == kp.public and kp.public_key_str.startswith("ed25519:")
    tx = near.serialize_transaction(
        signer_id="a.testnet", public_key=kp.public, nonce=7, receiver_id="b.testnet",
        block_hash=b"\1" * 32, actions=[near.function_call("anchor", {"x": 1})],
    )
    signed = near.sign_transaction(tx, kp)
    assert signed[: len(tx)] == tx and signed[len(tx)] == 0
    VerifyKey(kp.public).verify(hashlib.sha256(tx).digest(), signed[len(tx) + 1 :])


def test_transaction_borsh_layout():
    pk = b"\2" * 32
    tx = near.serialize_transaction(
        signer_id="ab", public_key=pk, nonce=1, receiver_id="c", block_hash=b"\3" * 32,
        actions=[near.transfer(5)],
    )
    expected = (
        b"\2\0\0\0ab" + b"\0" + pk + (1).to_bytes(8, "little") + b"\1\0\0\0c" + b"\3" * 32
        + (1).to_bytes(4, "little") + b"\3" + (5).to_bytes(16, "little")
    )
    assert tx == expected


def test_function_call_key_layout():
    pk = b"\4" * 32
    act = near.add_function_call_key(pk, "anchor.x.testnet", ["anchor"])
    assert act[0] == 5 and act[1] == 0 and act[2:34] == pk
    assert act[34:42] == b"\0" * 8  # nonce
    assert act[42] == 0 and act[43] == 0  # FunctionCall permission, allowance None


def test_stream_tag_is_opaque_and_stable():
    t = stream_tag("4f6c2a10-team", KEY)
    assert t == stream_tag("4f6c2a10-team", KEY)
    assert t != stream_tag("other", KEY)
    assert "team" not in t and t.startswith("rg_") and len(t) == 27


async def test_untouched_chain_matches_anchor():
    ev = await _chain(5)
    anchor = {"events": 5, "head": ev[-1]["event_hash"], "block_height": 1}
    res = check_chain_against_anchor(ev, anchor, hmac_key=KEY)
    assert res.ok, res.detail


async def test_chain_that_grew_after_anchor_still_matches():
    ev = await _chain(8)
    anchor = {"events": 5, "head": ev[4]["event_hash"], "block_height": 1}
    assert check_chain_against_anchor(ev, anchor, hmac_key=KEY).ok


async def test_insider_rewrite_passes_hmac_but_fails_anchor():
    ev = await _chain(5)
    anchor = {"events": 5, "head": ev[-1]["event_hash"], "block_height": 42}
    forged = [dict(e) for e in ev]
    forged[1]["entity_counts"] = {"EMAIL": 1}  # hide that a SIN went out
    forged = _rechain(forged, KEY)
    # the HMAC chain alone is fooled ...
    assert check_chain_against_anchor(forged, None, hmac_key=KEY).status == "no_anchor"
    # ... the NEAR anchor is not
    res = check_chain_against_anchor(forged, anchor, hmac_key=KEY)
    assert res.status == "head_mismatch" and res.first_bad_position == 5


async def test_naive_edit_is_caught_by_hash_check():
    ev = await _chain(3)
    ev[0] = {**ev[0], "blocked": True}
    res = check_chain_against_anchor(ev, None, hmac_key=KEY)
    assert res.status == "bad_hash" and res.first_bad_position == 1


async def test_deleted_event_breaks_link_and_truncation_detected():
    ev = await _chain(4)
    assert check_chain_against_anchor(ev[:1] + ev[2:], None).status == "broken_link"
    anchor = {"events": 4, "head": ev[-1]["event_hash"], "block_height": 1}
    assert check_chain_against_anchor(ev[:3], anchor).status == "truncated"


@respx.mock
async def test_anchor_head_signs_and_sends_function_call():
    kp = near.KeyPair.generate()
    cfg = AnchorConfig("testnet", "anchor.x.testnet", "x.testnet", kp.secret_key_str)
    sent = {}

    def rpc(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["method"] == "query":
            return httpx.Response(200, json={"result": {
                "nonce": 10, "block_hash": near.b58encode(b"\x09" * 32)}})
        sent["tx"] = base64.b64decode(body["params"]["signed_tx_base64"])
        ret = base64.b64encode(json.dumps({"events": 3}).encode()).decode()
        return httpx.Response(200, json={"result": {"status": {"SuccessValue": ret}}})

    respx.post(near.RPC_URLS["testnet"]).mock(side_effect=rpc)
    out = await anchor_head(near.NearClient(), cfg, stream="rg_abc", events=3, head="a" * 64)
    assert out["anchor"] == {"events": 3}
    assert out["explorer"].startswith("https://testnet.nearblocks.io/txns/")
    tx = sent["tx"]
    assert b"anchor.x.testnet" in tx and b'"head":"' + b"a" * 64 in tx
    assert (11).to_bytes(8, "little") in tx  # nonce + 1


async def test_run_anchor_pass_anchors_only_grown_chains(monkeypatch):
    from app.anchor import service

    kp = near.KeyPair.generate()
    cfg = AnchorConfig("testnet", "anchor.x.testnet", "x.testnet", kp.secret_key_str)
    key = service.settings.key_bytes("audit_hmac_key")

    async def fake_heads(session):
        return [("team-a", 4, "a" * 64), ("team-b", 2, "b" * 64)]

    on_chain = {stream_tag("team-a", key): {"events": 4, "head": "a" * 64},
                stream_tag("team-b", key): {"events": 1, "head": "c" * 64}}
    written = []

    async def fake_latest(client, cfg_, stream):
        return on_chain.get(stream)

    async def fake_anchor(client, cfg_, *, stream, events, head, note=None):
        written.append((stream, events, head, note))
        return {"tx_hash": "T", "explorer": "E", "anchor": None}

    monkeypatch.setattr(service, "team_heads", fake_heads)
    monkeypatch.setattr(service, "latest_anchor", fake_latest)
    monkeypatch.setattr(service, "anchor_head", fake_anchor)
    out = await service.run_anchor_pass(None, cfg, client=near.NearClient())
    assert written == [(stream_tag("team-b", key), 2, "b" * 64, "cron")]
    assert out[0]["skipped"] == "up to date"


def test_worker_registers_anchor_job_only_when_enabled(monkeypatch):
    from app import worker

    monkeypatch.setattr(worker.settings, "near_anchor_enabled", False)
    assert worker._anchor_cron_jobs() == []
    monkeypatch.setattr(worker.settings, "near_anchor_enabled", True)
    monkeypatch.setattr(worker.settings, "near_anchor_interval_min", 15)
    jobs = worker._anchor_cron_jobs()
    assert len(jobs) == 1 and jobs[0].minute == {0, 15, 30, 45}


def test_config_rejects_missing_near_settings(monkeypatch):
    from app.anchor import service

    monkeypatch.setattr(service.settings, "near_contract_id", "")
    with pytest.raises(RuntimeError, match="NEAR_CONTRACT_ID"):
        AnchorConfig.from_settings(service.settings)
