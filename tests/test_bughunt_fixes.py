"""Regression tests for the 2026-11 bug-hunt fixes (offline: no DB, no network).

Each test fails on the pre-fix code and passes after the fix.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.auth import AuthContext
from app.redaction.pipeline import PipelineContext, RedactionPipeline
from app.redaction.store import InMemoryTokenStore
from app.redaction.vault import Vault
from app.schemas.entities import EntitySpan, PolicyDecision, PolicyMode

KEY = b"redactgate-test-key-0123456789abc"[:32]


# ── 1. auth failures surface as 401, not 500 ────────────────────────────────────


def _client_with_db_stub(monkeypatch, *, require_api_key: bool) -> TestClient:
    from app.config import settings
    from app.db import get_session
    from app.main import create_app

    monkeypatch.setattr(settings, "require_api_key", require_api_key)
    app = create_app()

    async def _no_db():
        yield None  # authenticate() rejects a missing header before touching the session

    app.dependency_overrides[get_session] = _no_db
    return TestClient(app, raise_server_exceptions=False)


def test_missing_api_key_returns_401_not_500(monkeypatch):
    client = _client_with_db_stub(monkeypatch, require_api_key=True)
    resp = client.post("/v1/chat/completions", json={"messages": []})
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "invalid api key"


def test_unhandled_error_does_not_echo_exception_text(monkeypatch):
    from app.main import create_app

    app = create_app()

    @app.get("/__boom")
    async def _boom():
        raise RuntimeError("password=hunter2 host=db.internal")

    resp = TestClient(app, raise_server_exceptions=False).get("/__boom")
    assert resp.status_code == 500
    assert "hunter2" not in resp.text
    assert resp.json()["error"]["message"] == "internal server error"


# ── 2. streaming generator: client disconnect must not crash & must audit ──────


class _Detok:
    async def push(self, d):
        return d

    async def flush(self):
        return ""


class _StreamProvider:
    async def stream(self, payload):
        for i in range(5):
            yield {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": f"t{i}"}}]}


async def _drive(monkeypatch, consume_all: bool):
    from app.routes import proxy

    audited: list[dict] = []

    async def _fake_audit(**kw):
        audited.append(kw)

    monkeypatch.setattr(proxy, "_safe_audit_fresh", _fake_audit)
    gen = proxy._stream_response(
        detok=_Detok(),
        provider=_StreamProvider(),
        sanitized={},
        ctx=PipelineContext(session_id="s1", entity_counts={"SIN": 1}),
        route="/v1/chat/completions",
        provider_name="fake",
        auth=AuthContext("t", None, None),
        started=0.0,
    )
    out = []
    if consume_all:
        async for b in gen:
            out.append(b)
    else:
        out.append(await gen.__anext__())
        await gen.aclose()  # what Starlette does when the client disconnects
    return out, audited


async def test_stream_client_disconnect_closes_cleanly_and_audits(monkeypatch):
    _, audited = await _drive(monkeypatch, consume_all=False)
    assert len(audited) == 1
    assert audited[0]["entity_counts"] == {"SIN": 1}


async def test_stream_normal_completion_ends_with_done_and_audits_once(monkeypatch):
    out, audited = await _drive(monkeypatch, consume_all=True)
    assert out[-1] == b"data: [DONE]\n\n"
    assert sum(1 for b in out if b == b"data: [DONE]\n\n") == 1
    assert len(audited) == 1


# ── 3. admin token compared in constant time (and still correct) ────────────────


async def test_require_admin_uses_compare_digest(monkeypatch):
    import hmac as _hmac

    from fastapi import HTTPException

    from app.config import settings
    from app.routes import admin

    monkeypatch.setattr(settings, "admin_token", "s3cret")
    calls = []
    real = _hmac.compare_digest

    def _spy(a, b):
        calls.append(1)
        return real(a, b)

    monkeypatch.setattr(admin.hmac, "compare_digest", _spy)
    assert await admin.require_admin("s3cret") is None
    for bad in (None, "", "s3cre", "s3cret!", "S3CRET"):
        with pytest.raises(HTTPException) as e:
            await admin.require_admin(bad)
        assert e.value.status_code == 401
    assert calls, "token must be compared with hmac.compare_digest"


# ── 4. tool-call arguments are re-inflated in non-stream completions ────────────


async def test_reinflate_restores_tool_call_arguments():
    store = InMemoryTokenStore()
    vault = Vault(store, master_key=KEY, fingerprint_key=KEY)
    sid = await store.create_session()
    text = "SIN 193 456 787"
    sanitized = await vault.tokenize(
        text, [EntitySpan(4, 15, "SIN", "193 456 787")], session_id=sid
    )
    placeholder = sanitized[4:]
    pipe = RedactionPipeline(
        store=store,
        vault=vault,
        detector=None,
        audit_sink=None,
        decision=PolicyDecision(mode=PolicyMode.TOKENIZE, blocked=False),
        auth=AuthContext("t", None, None),
        provider=None,
    )
    completion = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "lookup", "arguments": f'{{"sin": "{placeholder}"}}'}}
                    ],
                    "function_call": {"name": "legacy", "arguments": f'{{"sin": "{placeholder}"}}'},
                }
            }
        ]
    }
    out = await pipe.reinflate(completion, PipelineContext(session_id=sid))
    msg = out["choices"][0]["message"]
    assert msg["tool_calls"][0]["function"]["arguments"] == '{"sin": "193 456 787"}'
    assert msg["function_call"]["arguments"] == '{"sin": "193 456 787"}'


# ── 5. create_policy validates input instead of 500-ing in the DB ───────────────


class _NoTouchSession:
    def add(self, *_a, **_k):  # pragma: no cover - must not be reached
        raise AssertionError("invalid input reached the DB session")


@pytest.mark.parametrize(
    "body,detail",
    [
        ({"org_id": "not-a-uuid", "name": "p"}, "org_id must be a UUID"),
        ({"org_id": "00000000-0000-0000-0000-0000000000aa", "name": "p", "mode": "yolo"}, "mode"),
    ],
)
async def test_create_policy_rejects_bad_input_with_400(body, detail):
    from fastapi import HTTPException

    from app.routes.admin import create_policy

    with pytest.raises(HTTPException) as e:
        await create_policy(None, _NoTouchSession(), body)
    assert e.value.status_code == 400
    assert detail in e.value.detail


# ── 6. Gemini API key never appears in the request URL / httpx logs ─────────────


async def test_gemini_key_not_in_url_or_logs(caplog):
    from app.config import Settings
    from app.gateway.gemini import GeminiProvider

    s = Settings(gemini_api_key="SECRET-GK", gemini_base_url="https://gemini.test/v1beta",
                 gemini_default_model="gemini-test")
    native = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
    chunk = 'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},"finishReason":"STOP"}]}\n\n'
    with caplog.at_level(logging.INFO, logger="httpx"), respx.mock:
        r1 = respx.post("https://gemini.test/v1beta/models/gemini-test:generateContent").mock(
            return_value=httpx.Response(200, json=native)
        )
        r2 = respx.post(
            "https://gemini.test/v1beta/models/gemini-test:streamGenerateContent"
        ).mock(return_value=httpx.Response(200, text=chunk))
        p = GeminiProvider(s)
        await p.complete({"model": "", "messages": [{"role": "user", "content": "x"}]})
        _ = [c async for c in p.stream({"model": "", "messages": [{"role": "user", "content": "x"}]})]
    for route in (r1, r2):
        req = route.calls.last.request
        assert "SECRET-GK" not in str(req.url)
        assert req.headers["x-goog-api-key"] == "SECRET-GK"
    assert r2.calls.last.request.url.params["alt"] == "sse"
    assert "SECRET-GK" not in caplog.text
