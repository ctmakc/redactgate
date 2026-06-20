"""End-to-end proxy tests with NO database / redis.

We exercise the *real* FastAPI route (``app.main.create_app``), the *real* detection
pipeline (regex packs), the *real* :class:`Vault` over an ``InMemoryTokenStore`` and the
*real* policy engine. Only the persistence/auth/provider seams are swapped:

  * ``get_token_store``    -> a shared ``InMemoryTokenStore``
  * ``get_audit_sink``     -> ``InMemoryAuditSink`` (records counts only)
  * ``get_policy_decision``-> a per-test ``PolicyDecision``
  * ``get_active_provider``-> a registered ``FakeProvider`` that ECHOES the (already
                              sanitized) user content, so we can prove placeholders were
                              sent upstream and real values come back to the client
  * ``get_auth``           -> a fixed ``AuthContext``

This proves the full firewall contract: detect -> policy -> tokenize -> upstream ->
re-inflate, end to end, with the same token namespace across the whole request.

These two route bugs found during test authoring are now FIXED in the implementation:
``app.deps.get_detector`` no longer mis-passes ``cfg`` to the no-arg ``Detector``, and
``app.routes.proxy._stream_response`` now awaits the async ``StreamDetokenizer``.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.auth import AuthContext
from app.config import settings as cfg
from app.gateway.base import Provider, get_provider, register_provider, reset_provider_cache
from app.gateway.openai import build_chunk, build_completion, new_id
from app.redaction.detect import Detector
from app.redaction.store import InMemoryTokenStore
from app.schemas.entities import PolicyDecision, PolicyMode

# A SIN that PASSES the strict ``sin_check`` validator (does not start with 0/8, Luhn-ok),
# so the *real* regex CA pack actually detects it. 046 454 286 is intentionally rejected
# by the validator, so we use a detectable one.
SIN = "193 456 787"
IBAN = "GB82 WEST 1234 5698 7654 32"

PLACEHOLDER_RE = re.compile(r"\[\[[A-Z][A-Z0-9_]*_[0-9a-f]{4,12}\]\]")


# ── Test doubles ────────────────────────────────────────────────────────────────


class InMemoryAuditSink:
    """Audit sink that records only the metadata the real sink would (counts, no values)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record(self, **kwargs) -> None:
        self.events.append(kwargs)


class FakeProvider(Provider):
    """Echoes the LAST user message back as the assistant reply.

    Because the pipeline sanitizes the payload BEFORE the provider sees it, the echoed
    content contains placeholders — exactly what a real upstream would receive. The route
    then re-inflates that into the client response, so a round-trip of placeholders proves
    the firewall both redacts outbound and restores inbound.
    """

    name = "fake"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.received_payloads: list[dict] = []

    def _last_user(self, payload: dict) -> str:
        users = [m["content"] for m in payload.get("messages", []) if m.get("role") == "user"]
        return users[-1] if users else ""

    async def complete(self, payload: dict) -> dict:
        self.received_payloads.append(payload)
        return build_completion(model="fake", content="Reply about " + self._last_user(payload))

    async def stream(self, payload: dict):
        self.received_payloads.append(payload)
        content = "Reply about " + self._last_user(payload)
        cid = new_id()
        # Yield in pieces, deliberately splitting a placeholder across two chunks so the
        # stream de-tokenizer's tail-buffering is exercised.
        for piece in _split_breaking_a_placeholder(content):
            yield build_chunk(model="fake", content=piece, completion_id=cid)
        yield build_chunk(model="fake", finish_reason="stop", completion_id=cid)


def _split_breaking_a_placeholder(text: str) -> list[str]:
    """Split ``text`` into chunks where at least one placeholder straddles a boundary."""
    m = PLACEHOLDER_RE.search(text)
    if not m:
        # No placeholder -> arbitrary 3-way split.
        n = max(1, len(text) // 3)
        return [text[:n], text[n : 2 * n], text[2 * n :]]
    s, e = m.start(), m.end()
    mid = s + 4  # inside the "[[TY" prefix
    inner = e - 1  # just before the closing "]"
    return [text[:mid], text[mid:inner], text[inner:]]


# ── App wiring ───────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_provider():
    # Register under the gateway registry too (so app.gateway.base.get_provider("fake")
    # resolves if anything reads from it). Reset the instance cache so our settings stick.
    # Clean up afterwards so this never leaks into other agents' concurrently-run suites.
    import app.gateway.base as gw_base

    register_provider("fake", lambda s: FakeProvider(s))
    reset_provider_cache()
    provider = get_provider("fake", cfg)
    try:
        yield provider
    finally:
        gw_base._REGISTRY.pop("fake", None)
        reset_provider_cache()


@pytest.fixture
def store() -> InMemoryTokenStore:
    return InMemoryTokenStore()


@pytest.fixture
def audit() -> InMemoryAuditSink:
    return InMemoryAuditSink()


def _tokenize_decision() -> PolicyDecision:
    return PolicyDecision(mode=PolicyMode.TOKENIZE, blocked=False)


@pytest.fixture
def make_client(store, audit, fake_provider, monkeypatch):
    """Factory: build a TestClient with all seams overridden and a given policy decision."""
    from app.main import create_app

    def _factory(decision: PolicyDecision | None = None) -> TestClient:
        app = create_app()
        app.dependency_overrides[deps.get_token_store] = lambda: store
        app.dependency_overrides[deps.get_audit_sink] = lambda: audit
        app.dependency_overrides[deps.get_policy_decision] = lambda: decision or _tokenize_decision()
        app.dependency_overrides[deps.get_active_provider] = lambda: fake_provider
        app.dependency_overrides[deps.get_auth] = lambda: AuthContext(
            team_id="team-bb", policy_id="policy-cc", api_key_id="key-1"
        )
        return TestClient(app)

    return _factory


# ── Non-streaming end-to-end ─────────────────────────────────────────────────────


def test_provider_receives_placeholders_not_raw_values(make_client, fake_provider):
    client = make_client()
    body = {
        "model": "gpt",
        "messages": [
            {"role": "user", "content": f"Client SIN {SIN}. Wire to IBAN {IBAN}."},
        ],
    }
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200

    # (a) The provider must NOT have seen the raw SIN or IBAN.
    sent = fake_provider.received_payloads[-1]
    sent_user = sent["messages"][-1]["content"]
    assert SIN not in sent_user
    assert IBAN not in sent_user
    assert "[[SIN_" in sent_user
    assert "[[IBAN_" in sent_user


def test_client_response_is_reinflated_with_real_values(make_client):
    client = make_client()
    body = {"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}."}]}
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    # (b) The client got the REAL value back (re-inflated), with no placeholder leaking.
    assert SIN in content
    assert "[[SIN_" not in content


def test_repeated_value_uses_one_consistent_placeholder(make_client, fake_provider):
    client = make_client()
    body = {
        "model": "gpt",
        "messages": [
            {"role": "user", "content": f"Verify SIN {SIN}, then re-verify SIN {SIN} again."},
        ],
    }
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200

    # (c) The same value mapped to ONE placeholder across the request.
    sent_user = fake_provider.received_payloads[-1]["messages"][-1]["content"]
    sin_phs = re.findall(r"\[\[SIN_[0-9a-f]+\]\]", sent_user)
    assert len(sin_phs) == 2
    assert len(set(sin_phs)) == 1

    # And it round-trips fully back to the real value (appearing twice in the reply).
    content = resp.json()["choices"][0]["message"]["content"]
    assert content.count(SIN) == 2


def test_same_value_across_separate_messages_shares_placeholder(make_client, fake_provider):
    client = make_client()
    body = {
        "model": "gpt",
        "messages": [
            {"role": "system", "content": f"Account SIN is {SIN}."},
            {"role": "user", "content": f"Does SIN {SIN} match my file?"},
        ],
    }
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    sent = fake_provider.received_payloads[-1]
    all_text = " ".join(m["content"] for m in sent["messages"])
    sin_phs = set(re.findall(r"\[\[SIN_[0-9a-f]+\]\]", all_text))
    # One session spans the whole request -> one placeholder for the same value.
    assert len(sin_phs) == 1
    assert SIN not in all_text


def test_response_envelope_is_openai_shaped(make_client):
    client = make_client()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "usage" in body


def test_payload_without_pii_passes_through_unchanged(make_client, fake_provider):
    client = make_client()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": "What is the capital of France?"}]},
    )
    assert resp.status_code == 200
    sent_user = fake_provider.received_payloads[-1]["messages"][-1]["content"]
    assert sent_user == "What is the capital of France?"
    assert not PLACEHOLDER_RE.search(sent_user)


def test_audit_event_records_counts_not_values(make_client, audit):
    client = make_client()
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    assert resp.status_code == 200
    assert audit.events, "expected an audit event to be written"
    ev = audit.events[-1]
    assert ev["blocked"] is False
    assert ev["route"] == "/v1/chat/completions"
    # Counts only — the raw SIN must never appear anywhere in the audit payload.
    assert "SIN" in ev["entity_counts"]
    assert ev["entity_counts"]["SIN"] == 1
    assert SIN not in json.dumps(ev, default=str)


def test_responses_route_also_redacts(make_client, fake_provider):
    client = make_client()
    resp = client.post(
        "/v1/responses",
        json={"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    assert resp.status_code == 200
    sent_user = fake_provider.received_payloads[-1]["messages"][-1]["content"]
    assert SIN not in sent_user
    assert "[[SIN_" in sent_user


def test_responses_route_redacts_nested_input_shape(make_client, fake_provider):
    """The standard OpenAI Responses shape (input=[{role,content:[{type:input_text,text}]}])
    — the one every official SDK emits — must be redacted. This was a CRITICAL bypass."""
    client = make_client()
    resp = client.post(
        "/v1/responses",
        json={
            "model": "gpt",
            "input": [
                {"role": "user", "content": [{"type": "input_text", "text": f"SIN {SIN}"}]}
            ],
        },
    )
    assert resp.status_code == 200
    sent_text = fake_provider.received_payloads[-1]["input"][0]["content"][0]["text"]
    assert SIN not in sent_text  # raw PII must NOT reach upstream
    assert "[[SIN_" in sent_text


def test_tool_call_arguments_are_redacted_e2e(make_client, fake_provider):
    """PII inside an assistant tool-call's JSON arguments must be redacted before upstream."""
    client = make_client()
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt",
            "messages": [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "f", "arguments": f'{{"sin": "{SIN}"}}'}}
                    ],
                },
                {"role": "user", "content": "continue"},
            ],
        },
    )
    assert resp.status_code == 200
    args = fake_provider.received_payloads[-1]["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert SIN not in args
    assert "[[SIN_" in args


# ── Hard-block policy -> 422 ─────────────────────────────────────────────────────


def test_hard_block_mode_returns_422(make_client):
    decision = PolicyDecision(mode=PolicyMode.HARD_BLOCK, blocked=True)
    client = make_client(decision)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "hard_blocked"
    # No upstream call leaked anything.
    assert SIN not in json.dumps(body)


def test_blocked_type_returns_422(make_client):
    decision = PolicyDecision(mode=PolicyMode.TOKENIZE, blocked=False, blocked_types=["SIN"])
    client = make_client(decision)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "hard_blocked"


def test_hard_block_writes_blocked_audit_event(make_client, audit):
    decision = PolicyDecision(mode=PolicyMode.HARD_BLOCK, blocked=True)
    client = make_client(decision)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    assert resp.status_code == 422
    assert audit.events, "a blocked audit event must be written"
    assert audit.events[-1]["blocked"] is True


def test_provider_not_called_on_hard_block(make_client, fake_provider):
    decision = PolicyDecision(mode=PolicyMode.HARD_BLOCK, blocked=True)
    client = make_client(decision)
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    # Upstream must never be invoked when the request is hard-blocked.
    assert fake_provider.received_payloads == []


# ── Provider allow-list ──────────────────────────────────────────────────────────


def test_provider_not_in_allowlist_is_rejected(make_client, fake_provider):
    decision = PolicyDecision(
        mode=PolicyMode.TOKENIZE, blocked=False, allowed_providers=["openai"]
    )
    client = make_client(decision)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "provider_not_allowed"
    assert fake_provider.received_payloads == []


def test_provider_in_allowlist_is_permitted(make_client):
    decision = PolicyDecision(
        mode=PolicyMode.TOKENIZE, blocked=False, allowed_providers=["fake"]
    )
    client = make_client(decision)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200


# ── Streaming ────────────────────────────────────────────────────────────────────


async def test_stream_pipeline_reassembles_split_placeholder(store):
    """The streaming CONTRACT, verified at the pipeline layer (route-independent).

    A placeholder split across SSE chunks must reassemble to the fully re-inflated text.
    This is the behaviour the proxy's streaming path is *supposed* to deliver (the route
    itself is currently broken — see the xfail below and impl_bugs_found).
    """
    from app.redaction.vault import Vault

    vault = Vault(
        store,
        master_key=cfg.key_bytes("vault_master_key"),
        fingerprint_key=cfg.key_bytes("fingerprint_hmac_key"),
    )
    detector = Detector()
    text = f"SIN {SIN} is on file."
    res = await detector.detect(text, pack_codes=["CA"])
    session_id = await store.create_session()
    sanitized = await vault.tokenize(text, res.spans, session_id=session_id)
    assert SIN not in sanitized and "[[SIN_" in sanitized

    # Simulate the upstream streaming the SANITIZED text back in placeholder-splitting chunks.
    pieces = _split_breaking_a_placeholder(sanitized)
    detok = vault.stream_detokenizer(session_id)
    out = ""
    for piece in pieces:
        out += await detok.push(piece)
    out += await detok.flush()

    # Reassembled stream equals the fully re-inflated (original) text.
    assert out == text
    assert SIN in out
    assert "[[SIN_" not in out


def test_streaming_route_reinflates_full_text(make_client):
    client = make_client()
    body = {
        "model": "gpt",
        "messages": [{"role": "user", "content": f"SIN {SIN} confirmed."}],
        "stream": True,
    }
    with client.stream("POST", "/v1/chat/completions", json=body) as resp:
        assert resp.status_code == 200
        raw = b"".join(resp.iter_bytes()).decode()

    # Reassemble streamed deltas; this is what SHOULD hold once the route awaits the detok.
    reassembled = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        chunk = json.loads(data)
        try:
            reassembled += chunk["choices"][0]["delta"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            pass
    assert SIN in reassembled
    assert "[[SIN_" not in reassembled
