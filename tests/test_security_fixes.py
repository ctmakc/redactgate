"""Regression tests for the confirmed security-audit findings (2026-06-20).

Each test pins a fix so the corresponding bypass/weakness cannot silently return. See
docs/SECURITY.md for the finding catalogue.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re

import pytest

import app.main as main_mod  # import once under the test env (runs create_app() cleanly)
from app.schemas.entities import EntitySpan
from app.schemas.openai import extract_texts, inject_texts

PH_RE = re.compile(r"\[\[[A-Z][A-Z0-9_]*_[0-9a-f]{4,12}\]\]")


# ── #1 /v1/responses nested message input must be redacted ─────────────────────────


def test_responses_nested_message_input_is_extracted():
    payload = {
        "model": "gpt-4o",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "SIN 130 692 502"}]}
        ],
    }
    assert extract_texts(payload) == ["SIN 130 692 502"]
    out = inject_texts(payload, ["[[SIN_ab12]]"])
    assert out["input"][0]["content"][0]["text"] == "[[SIN_ab12]]"
    # original not mutated
    assert payload["input"][0]["content"][0]["text"] == "SIN 130 692 502"


def test_responses_string_and_flat_part_still_work():
    assert extract_texts({"input": "hello"}) == ["hello"]
    assert extract_texts({"input": [{"type": "input_text", "text": "flat"}]}) == ["flat"]


# ── #2 tool_calls / function_call arguments must be redacted ───────────────────────


def test_tool_call_arguments_are_extracted_and_injected():
    payload = {
        "model": "x",
        "messages": [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function",
                     "function": {"name": "lookup", "arguments": '{"sin": "130692502"}'}}
                ],
            },
            {"role": "user", "content": "go"},
        ],
    }
    texts = extract_texts(payload)
    assert '{"sin": "130692502"}' in texts and "go" in texts
    out = inject_texts(payload, ["[[ARGS]]", "[[GO]]"])
    assert out["messages"][0]["tool_calls"][0]["function"]["arguments"] == "[[ARGS]]"


def test_legacy_function_call_arguments_extracted():
    payload = {"messages": [{"role": "assistant", "function_call": {"name": "f", "arguments": "a@b.com"}}]}
    assert extract_texts(payload) == ["a@b.com"]


def test_extract_inject_roundtrip_length_guard():
    payload = {"messages": [{"role": "user", "content": "x"}]}
    with pytest.raises(ValueError):
        inject_texts(payload, ["a", "b"])  # wrong count


# ── #3 Unicode / zero-width evasion must be normalized away ────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "SIN １３０ ６９２ ５０２",  # full-width digits
        "SIN 130 692 502",  # NBSP separators
        "card 4111​1111​1111​1111",  # zero-width split
    ],
)
def test_normalization_defeats_evasion(raw):
    import asyncio

    from app.redaction.detect import Detector
    from app.redaction.normalize import normalize_text

    det = Detector()
    # raw (un-normalized) evades regex detection...
    raw_spans = asyncio.run(det.detect(raw, pack_codes=["GENERIC", "CA", "US", "EU", "UA"]))
    assert len(raw_spans.spans) == 0
    # ...but normalization (what the pipeline does first) restores detectability.
    norm = normalize_text(raw)
    spans = asyncio.run(det.detect(norm, pack_codes=["GENERIC", "CA", "US", "EU", "UA"]))
    assert len(spans.spans) >= 1


def test_normalize_is_idempotent_and_total():
    from app.redaction.normalize import normalize_text

    s = "Café  ​ normal"
    assert normalize_text(normalize_text(s)) == normalize_text(s)
    assert "​" not in normalize_text(s)


# ── #4 production refuses insecure dev-fallback keys / open admin ──────────────────


def test_runtime_problems_blocks_insecure_prod():
    from app.config import Settings

    insecure = Settings(environment="prod", vault_master_key="", fingerprint_hmac_key="",
                        audit_hmac_key="", admin_token="", require_api_key=True)
    probs = insecure.runtime_problems()
    assert any("vault_master_key" in p for p in probs)
    assert any("admin_token" in p for p in probs)

    k = base64.b64encode(b"x" * 32).decode()
    secure = Settings(environment="prod", vault_master_key=k, fingerprint_hmac_key=k,
                      audit_hmac_key=k, admin_token="strong", require_api_key=True)
    assert secure.runtime_problems() == []


def test_create_app_refuses_insecure_prod(monkeypatch):
    from app.config import settings as live

    monkeypatch.setattr(live, "environment", "prod")
    monkeypatch.setattr(live, "vault_master_key", "")
    monkeypatch.setattr(live, "admin_token", "")
    with pytest.raises(RuntimeError):
        main_mod.create_app()


# ── #5 empty admin token is fail-closed outside dev ───────────────────────────────


async def test_require_admin_fail_closed():
    from fastapi import HTTPException

    from app.config import settings
    from app.routes.admin import require_admin

    orig_token, orig_env = settings.admin_token, settings.environment
    try:
        settings.admin_token = ""
        settings.environment = "test"
        with pytest.raises(HTTPException) as e:
            await require_admin(None)
        assert e.value.status_code == 503
        settings.environment = "dev"
        assert await require_admin(None) is None  # dev stays open
        settings.admin_token = "secret"
        with pytest.raises(HTTPException) as e2:
            await require_admin("wrong")
        assert e2.value.status_code == 401
        assert await require_admin("secret") is None
    finally:
        settings.admin_token, settings.environment = orig_token, orig_env


# ── #11 placeholder suffix is random, not derived from the value fingerprint ───────


async def test_placeholder_not_derived_from_fingerprint(master_key, fingerprint_key):
    from app.redaction.store import InMemoryTokenStore
    from app.redaction.vault import Vault

    v = Vault(InMemoryTokenStore(), master_key=master_key, fingerprint_key=fingerprint_key)
    sid, val = "sess-1", "193 456 787"
    out = await v.tokenize(f"SIN {val}.", [EntitySpan(4, 15, "SIN", val)], session_id=sid)
    fp = hmac.new(fingerprint_key, f"{sid}:SIN:{val}".encode(), hashlib.sha256).hexdigest()
    # The OLD scheme used the fingerprint prefix as the placeholder suffix — must NOT recur.
    assert f"[[SIN_{fp[:6]}]]" not in out
    # Referential consistency still holds: same value, same session → same placeholder.
    out2 = await v.tokenize(f"again {val}!", [EntitySpan(6, 17, "SIN", val)], session_id=sid)
    assert PH_RE.search(out).group(0) == PH_RE.search(out2).group(0)


# ── #12 / #10 CORS is not wildcard, and oversized bodies are rejected ──────────────


def test_cors_is_not_wildcard():
    from app.config import settings

    assert "*" not in settings.cors_origin_list


def test_oversized_body_rejected(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "max_body_bytes", 1000)
    client = TestClient(create_app())
    big = "x" * 5000
    r = client.post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": big}]})
    assert r.status_code == 413


# ── #13 gemini rejects a path-injection model name ────────────────────────────────


def test_gemini_rejects_malicious_model():
    from app.config import settings
    from app.gateway.base import ProviderError
    from app.gateway.gemini import GeminiProvider

    p = GeminiProvider(settings)
    for bad in ["../../evil", "a:b", "a/b", "has space", "x?y=1"]:
        with pytest.raises(ProviderError):
            p._resolve_model({"model": bad})
    assert p._resolve_model({"model": "gemini-2.5-flash"}) == "gemini-2.5-flash"
    assert p._resolve_model({"model": ""}) == settings.gemini_default_model


# ── #8 the append-only audit log resists TRUNCATE (needs live PG) ──────────────────

from tests.conftest import integration_only  # noqa: E402


@integration_only()
async def test_audit_truncate_is_blocked():
    from sqlalchemy import text

    from app.db import session_scope

    with pytest.raises(Exception):  # noqa: B017 - any DB error; the trigger RAISEs
        async with session_scope() as s:
            await s.execute(text("TRUNCATE audit_event"))
            await s.commit()


# ── #6 concurrent appends to one team keep a single valid hash-chain (needs live PG)


@integration_only()
async def test_concurrent_audit_keeps_valid_chain():
    import asyncio
    import uuid

    from sqlalchemy import text

    from app.audit import record_event, verify_chain
    from app.db import session_scope

    org_id, team_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with session_scope() as s:
        await s.execute(text("INSERT INTO org(id, name) VALUES (:o, 'cc')"), {"o": org_id})
        await s.execute(
            text("INSERT INTO team(id, org_id, name) VALUES (:t, :o, 'cc')"),
            {"t": team_id, "o": org_id},
        )
        await s.commit()

    async def one() -> None:
        async with session_scope() as s:
            await record_event(
                s, team_id=team_id, route="/r", provider="p",
                entity_counts={"SIN": 1}, blocked=False,
            )
            await s.commit()

    # 12 racing appenders — the per-team advisory lock must serialize them into one chain.
    await asyncio.gather(*[one() for _ in range(12)])

    async with session_scope() as s:
        assert await verify_chain(s, team_id) is True
        n = (
            await s.execute(
                text("SELECT count(*) FROM audit_event WHERE team_id = :t"), {"t": team_id}
            )
        ).scalar()
    assert n == 12
