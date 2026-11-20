"""Regression tests for the 2026-11-20 code-review bug hunt (branch credits/repo-bughunt-1120-0417).

Each test pins one confirmed defect. All run offline: no DB, no Redis, no upstream LLM.
"""

from __future__ import annotations

import io
import re
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_proxy_e2e import SIN, audit, fake_provider, make_client, store  # noqa: F401

# ── 1. EMAIL regex was quadratic (ReDoS) ───────────────────────────────────────────


def _email_pattern():
    from app.redaction.regex_packs import load_packs

    pack = load_packs(["GENERIC"])[0]
    return next(p for p in pack.patterns if p.entity_type == "EMAIL")


@pytest.mark.parametrize("unit", ["1-", "a.", "b."])
def test_email_regex_is_linear_on_adversarial_input(unit):
    # 80k chars took ~5 s before the fix (O(n^2)); a 2 MB body would pin a CPU for an hour.
    pat = _email_pattern()
    text = unit * 40_000
    t0 = time.perf_counter()
    list(pat.regex.finditer(text))
    assert time.perf_counter() - t0 < 1.0


def test_email_regex_still_matches_real_addresses():
    pat = _email_pattern()
    for addr in ["john.smith@example.com", "a+b_c%d@sub-1.example.co.uk", "x@y.io"]:
        m = pat.regex.search(f"mail {addr}, thanks")
        assert m is not None and m.group(0) == addr
    local64 = "a" * 64
    assert pat.regex.search(f"{local64}@example.com").group(0) == f"{local64}@example.com"


# ── 2. Azure deployment (caller-controlled model) was path/query-injected ─────────


def test_azure_rejects_path_injection_deployment():
    from app.config import settings
    from app.gateway.azure import AzureOpenAIProvider
    from app.gateway.base import ProviderError

    p = AzureOpenAIProvider(settings)
    for bad in ["../../evil", "a/b", "x?api-version=1", "has space", "a#b", "a%2Fb", "gpt-4o\n"]:
        with pytest.raises(ProviderError):
            p._deployment_for({"model": bad})
    assert p._deployment_for({"model": "gpt-4o.mini_v2"}) == "gpt-4o.mini_v2"
    assert p._deployment_for({"model": ""}) == settings.azure_openai_deployment


def test_gemini_rejects_trailing_newline_model():
    from app.config import settings
    from app.gateway.base import ProviderError
    from app.gateway.gemini import GeminiProvider

    # ``$`` also matches before a trailing newline; the check must be a full match.
    with pytest.raises(ProviderError):
        GeminiProvider(settings)._resolve_model({"model": "gemini-2.5-flash\n"})


# ── 3. Streaming generator yielded inside `finally` → RuntimeError, audit skipped ──


async def test_stream_client_disconnect_closes_cleanly_and_still_audits(monkeypatch):
    import app.routes.proxy as px

    class P:
        name = "fake"

        async def stream(self, payload):
            for i in range(5):
                yield {"choices": [{"index": 0, "delta": {"content": f"t{i}"}}]}

    class D:
        async def push(self, s):
            return s

        async def flush(self):
            return ""

    audited: list[dict] = []

    async def fake_audit(**kw):
        audited.append(kw)

    monkeypatch.setattr(px, "_safe_audit_fresh", fake_audit)

    class Ctx:
        session_id = "s"
        entity_counts = {"SIN": 1}

    class A:
        team_id = "t"

    gen = px._stream_response(
        detok=D(), provider=P(), sanitized={}, ctx=Ctx(), route="/v1/chat/completions",
        provider_name="fake", auth=A(), started=time.perf_counter(),
    )
    first = await gen.__anext__()
    assert first.startswith(b"data: ")
    # Starlette closes the body iterator when the client goes away.
    await gen.aclose()  # raised "async generator ignored GeneratorExit" before the fix
    assert len(audited) == 1
    assert audited[0]["entity_counts"] == {"SIN": 1}


async def test_stream_full_run_emits_done_once_and_audits_once(monkeypatch):
    import app.routes.proxy as px

    class P:
        name = "fake"

        async def stream(self, payload):
            yield {"choices": [{"index": 0, "delta": {"content": "hi"}}]}

    class D:
        async def push(self, s):
            return s

        async def flush(self):
            return ""

    audited: list[dict] = []

    async def fake_audit(**kw):
        audited.append(kw)

    monkeypatch.setattr(px, "_safe_audit_fresh", fake_audit)

    class Ctx:
        session_id = "s"
        entity_counts: dict = {}

    class A:
        team_id = "t"

    out = [
        c
        async for c in px._stream_response(
            detok=D(), provider=P(), sanitized={}, ctx=Ctx(), route="r",
            provider_name="fake", auth=A(), started=time.perf_counter(),
        )
    ]
    assert out[-1] == b"data: [DONE]\n\n"
    assert out.count(b"data: [DONE]\n\n") == 1
    assert len(audited) == 1


# ── 4. Responses-API text fields that bypassed redaction ───────────────────────────


def test_responses_instructions_and_tool_outputs_are_extracted():
    from app.schemas.openai import extract_texts, inject_texts

    payload = {
        "model": "m",
        "instructions": f"Client SIN {SIN}",
        "input": [
            {"type": "function_call", "call_id": "c1", "name": "f", "arguments": f'{{"sin": "{SIN}"}}'},
            {"type": "function_call_output", "call_id": "c1", "output": f"record for SIN {SIN}"},
        ],
    }
    texts = extract_texts(payload)
    assert sum(SIN in t for t in texts) == 3
    out = inject_texts(payload, ["X"] * len(texts))
    assert SIN not in repr(out)
    assert payload["instructions"] == f"Client SIN {SIN}"  # caller's payload untouched


def test_responses_instructions_redacted_e2e(make_client, fake_provider):  # noqa: F811
    client = make_client()
    resp = client.post(
        "/v1/responses",
        json={
            "model": "gpt",
            "instructions": f"You assist the client with SIN {SIN}.",
            "input": [
                {"type": "function_call_output", "call_id": "c1", "output": f"SIN on file: {SIN}"},
                {"role": "user", "content": "hello"},
            ],
        },
    )
    assert resp.status_code == 200
    sent = fake_provider.received_payloads[-1]
    assert SIN not in repr(sent)
    assert "[[SIN_" in sent["instructions"]
    assert "[[SIN_" in sent["input"][0]["output"]


# ── 5. Unhandled-exception handler echoed str(exc) to the client ─────────────────────


def test_unhandled_error_does_not_leak_exception_text(make_client, fake_provider):  # noqa: F811
    async def boom(payload):
        raise RuntimeError("INTERNAL-DETAIL postgresql://user:pw@db/x")

    fake_provider.complete = boom
    client = make_client()
    client = TestClient(client.app, raise_server_exceptions=False)
    r = client.post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 500
    assert "INTERNAL-DETAIL" not in r.text
    assert "pw@db" not in r.text
    assert r.json()["error"]["message"]


# ── 6. /readyz echoed the raw DB exception (DSN/user) unauthenticated ────────────────


def test_readyz_does_not_leak_db_error():
    from app.db import get_session
    from app.main import create_app

    class BadSession:
        async def execute(self, *_a, **_k):
            raise OSError('password authentication failed for user "redactgate" at db:5432')

    async def bad_session():
        yield BadSession()

    app = create_app()
    app.dependency_overrides[get_session] = bad_session
    r = TestClient(app).get("/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "unavailable"
    assert "redactgate" not in r.text and "5432" not in r.text


# ── 7. Admin token compared with != (timing side channel) ───────────────────────────


async def test_require_admin_uses_constant_time_compare(monkeypatch):
    import hmac

    from fastapi import HTTPException

    import app.routes.admin as admin
    from app.config import settings

    calls: list[tuple] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(settings, "admin_token", "s3cret-token")
    monkeypatch.setattr(admin.hmac, "compare_digest", spy)
    await admin.require_admin("s3cret-token")
    with pytest.raises(HTTPException) as ei:
        await admin.require_admin("wrong")
    assert ei.value.status_code == 401
    with pytest.raises(HTTPException):
        await admin.require_admin(None)
    with pytest.raises(HTTPException):
        await admin.require_admin("s3cret-tokén")  # non-ASCII must 401, not 500
    assert len(calls) >= 2


# ── 8. Chunked (no Content-Length) bodies bypassed max_body_bytes ────────────────────


def test_chunked_oversized_body_rejected(monkeypatch):
    from app.config import settings
    from app.main import create_app

    monkeypatch.setattr(settings, "max_body_bytes", 1000)
    client = TestClient(create_app())

    def gen():
        yield b'{"model":"m","messages":[{"role":"user","content":"'
        for _ in range(50):
            yield b"x" * 100
        yield b'"}]}'

    r = client.post("/v1/chat/completions", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_chunked_small_body_still_passes(make_client, fake_provider):  # noqa: F811
    client = make_client()

    def gen():
        yield b'{"model":"m","messages":[{"role":"user",'
        yield f'"content":"SIN {SIN}"}}]}}'.encode()

    r = client.post("/v1/chat/completions", content=gen(), headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    assert SIN in r.json()["choices"][0]["message"]["content"]
    assert SIN not in repr(fake_provider.received_payloads[-1])


# ── 9. LLM-NER entity types were not coerced to the placeholder grammar ──────────────


async def test_llm_ner_types_are_placeholder_safe(vault):
    from app.redaction.llm_ner import _anchor_spans
    from app.redaction.placeholders import PLACEHOLDER_RE

    text = "Born 1990-01-02, mail jo@x.io, id ZZ-77, name Олена"
    items = [
        {"type": "date-of-birth", "text": "1990-01-02"},
        {"type": "e.mail", "text": "jo@x.io"},
        {"type": "X" * 120, "text": "ZZ-77"},
        {"type": "ім'я", "text": "Олена"},
    ]
    spans = _anchor_spans(text, items)
    assert len(spans) == 4
    for sp in spans:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]{0,39}", sp.entity_type), sp.entity_type
    sid = "sess-1"
    red = await vault.tokenize(text, spans, session_id=sid)
    assert len(PLACEHOLDER_RE.findall(red)) == 4
    assert await vault.detokenize(red, session_id=sid) == text


# ── 10. Desktop: password change was not atomic ──────────────────────────────────────


def test_desktop_change_password_is_atomic(tmp_path, monkeypatch):
    from app.desktop.vaultdb import LabelVault

    v = LabelVault(tmp_path / "v.db")
    v.initialize("old-password")
    real = LabelVault._set_meta
    n = {"i": 0}

    def flaky(self, k, val):
        n["i"] += 1
        if n["i"] == 2:  # crash between writing the new salt and the new wrapped key
            raise OSError("disk full")
        return real(self, k, val)

    monkeypatch.setattr(LabelVault, "_set_meta", flaky)
    with pytest.raises(OSError):
        v.change_password("old-password", "new-password")
    monkeypatch.setattr(LabelVault, "_set_meta", real)
    v.close()

    v2 = LabelVault(tmp_path / "v.db")
    v2.unlock("old-password")  # BadPassword before the fix: vault bricked
    assert v2.unlocked
    v2.close()


# ── 11. Desktop: negative Content-Length read the socket until EOF ───────────────────


def test_desktop_body_rejects_negative_content_length():
    from app.desktop.server import Handler

    class H(Handler):
        def __init__(self, headers, body):  # bypass BaseHTTPRequestHandler socket setup
            self.headers = headers
            self.rfile = io.BytesIO(body)

    with pytest.raises(ValueError):
        H({"Content-Length": "-1"}, b"x" * 10)._body()
    with pytest.raises(ValueError):
        H({"Content-Length": "abc"}, b"")._body()
    assert H({"Content-Length": "3"}, b"abcdef")._body() == b"abc"
    assert H({}, b"zzz")._body() == b""


def test_desktop_static_rejects_sibling_prefix_dir(tmp_path, monkeypatch):
    import app.desktop.server as srv

    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "index.html").write_text("ok")
    (tmp_path / "ui-private").mkdir()
    (tmp_path / "ui-private" / "secret.txt").write_text("SECRET")
    monkeypatch.setattr(srv, "res_dir", lambda: tmp_path)

    sent: list[tuple] = []

    class H(srv.Handler):
        def __init__(self):
            pass

        def _send(self, code, body, ctype, extra=None):
            sent.append((code, body))

        def _err(self, code, msg, kind="error"):
            sent.append((code, msg.encode()))

    H()._static("/../ui-private/secret.txt")
    assert sent[-1][0] == 404 and b"SECRET" not in sent[-1][1]
    H()._static("/index.html")
    assert sent[-1] == (200, b"ok")



async def test_stream_cancelled_mid_upstream_still_audits(monkeypatch):
    """Client disconnect = Starlette cancels the response task while the generator is
    awaiting upstream. The audit write must survive that cancellation (shielded)."""
    import anyio

    import app.routes.proxy as px

    started = anyio.Event()

    class P:
        name = "fake"

        async def stream(self, payload):
            yield {"choices": [{"index": 0, "delta": {"content": "a"}}]}
            started.set()
            await anyio.sleep(30)  # upstream stalls; the client hangs up meanwhile
            yield {"choices": [{"index": 0, "delta": {"content": "b"}}]}

    class D:
        async def push(self, s):
            return s

        async def flush(self):
            return ""

    audited: list[dict] = []

    async def fake_audit(**kw):
        await anyio.sleep(0)  # a real DB write awaits; must not be re-cancelled
        audited.append(kw)

    monkeypatch.setattr(px, "_safe_audit_fresh", fake_audit)

    class Ctx:
        session_id = "s"
        entity_counts = {"EMAIL": 2}

    class A:
        team_id = "t"

    gen = px._stream_response(
        detok=D(), provider=P(), sanitized={}, ctx=Ctx(), route="r",
        provider_name="fake", auth=A(), started=time.perf_counter(),
    )

    async def consume():
        async for _ in gen:
            pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(consume)
        await started.wait()
        tg.cancel_scope.cancel()
    await gen.aclose()
    assert len(audited) == 1 and audited[0]["entity_counts"] == {"EMAIL": 2}
