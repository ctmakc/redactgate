"""Every free-text field the OpenAI-compatible adapters forward must be redacted.

The ``openai`` adapter forwards the sanitized payload dict verbatim, so these tests drive
the REAL proxy route + REAL ``OpenAIProvider`` and inspect the exact JSON that hits the
(``respx``-mocked) upstream. No network.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import deps
from app.auth import AuthContext
from app.config import Settings
from app.gateway.openai import OpenAIProvider, build_completion
from app.redaction.store import InMemoryTokenStore
from app.schemas.entities import PolicyDecision, PolicyMode
from app.schemas.openai import extract_texts, inject_texts

UPSTREAM = "https://api.openai.test/v1"
SIN = "193 456 787"
EMAIL = "jane.doe@example.org"
CARD = "4111 1111 1111 1111"
SSN = "123-45-6789"
RAW = (SIN, EMAIL, CARD, SSN, "193456787", "4111111111111111")


@pytest.fixture
def client():
    from app.main import create_app

    provider = OpenAIProvider(
        Settings(openai_base_url=UPSTREAM, openai_api_key="test-not-a-key")
    )
    store = InMemoryTokenStore()

    class _Sink:
        async def record(self, **_kw):
            return None

    app = create_app()
    app.dependency_overrides[deps.get_token_store] = lambda: store
    app.dependency_overrides[deps.get_audit_sink] = lambda: _Sink()
    app.dependency_overrides[deps.get_policy_decision] = lambda: PolicyDecision(
        mode=PolicyMode.TOKENIZE, blocked=False
    )
    app.dependency_overrides[deps.get_active_provider] = lambda: provider
    app.dependency_overrides[deps.get_auth] = lambda: AuthContext("t", "p", "k")
    return TestClient(app)


def _assert_no_raw(body: str) -> None:
    for raw in RAW:
        assert raw not in body, f"raw value {raw!r} reached the upstream: {body}"


@pytest.mark.parametrize(
    ("label", "extra"),
    [
        ("instructions", {"instructions": f"Customer SIN is {SIN}."}),
        ("user", {"user": EMAIL}),
        ("prediction str", {"prediction": {"type": "content", "content": f"card {CARD}"}}),
        (
            "prediction parts",
            {"prediction": {"type": "content", "content": [{"type": "text", "text": SSN + " ok"}]}},
        ),
    ],
)
@respx.mock
def test_top_level_text_fields_are_redacted_on_the_wire(client, label, extra):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=build_completion(model="m", content="ok"))
    )
    body = {"model": "m", "messages": [{"role": "user", "content": "hello"}], **extra}
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200, resp.text
    wire = route.calls.last.request.content.decode()
    _assert_no_raw(wire)
    assert "[[" in wire, label


@respx.mock
def test_responses_tool_items_are_redacted_on_the_wire(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=build_completion(model="m", content="ok"))
    )
    body = {
        "model": "m",
        "input": [
            {"role": "user", "content": "look up the customer"},
            {"type": "function_call", "call_id": "c1", "name": "lookup", "arguments": json.dumps({"ssn": SSN})},
            {"type": "function_call_output", "call_id": "c1", "output": json.dumps({"email": EMAIL, "sin": SIN})},
            {
                "type": "function_call_output",
                "call_id": "c2",
                "output": [{"type": "input_text", "text": f"card {CARD}"}],
            },
        ],
    }
    resp = client.post("/v1/responses", json=body)
    assert resp.status_code == 200, resp.text
    wire = route.calls.last.request.content.decode()
    _assert_no_raw(wire)
    sent = json.loads(wire)
    # Structure is preserved: arguments/output are still valid JSON strings.
    args = json.loads(sent["input"][1]["arguments"])
    assert args["ssn"].startswith("[[SSN_")
    out = json.loads(sent["input"][2]["output"])
    assert out["email"].startswith("[[EMAIL_") and out["sin"].startswith("[[SIN_")


@respx.mock
def test_refusal_parts_are_redacted_on_the_wire(client):
    route = respx.post(f"{UPSTREAM}/chat/completions").mock(
        return_value=httpx.Response(200, json=build_completion(model="m", content="ok"))
    )
    body = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": [{"type": "refusal", "refusal": f"I won't email {EMAIL}"}]},
            {"role": "user", "content": "why?"},
        ],
    }
    assert client.post("/v1/chat/completions", json=body).status_code == 200
    _assert_no_raw(route.calls.last.request.content.decode())


def test_walk_order_is_stable_and_inject_roundtrips():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "a"}],
        "input": [{"type": "function_call_output", "output": "b"}],
        "instructions": "c",
        "user": "d",
        "prediction": {"content": "e"},
    }
    assert extract_texts(payload) == ["a", "b", "c", "d", "e"]
    out = inject_texts(payload, ["A", "B", "C", "D", "E"])
    assert out["messages"][0]["content"] == "A"
    assert out["input"][0]["output"] == "B"
    assert (out["instructions"], out["user"], out["prediction"]["content"]) == ("C", "D", "E")
    assert payload["instructions"] == "c"  # original untouched (deep copy)


def test_non_string_fields_are_ignored():
    payload = {"model": "m", "user": 123, "instructions": None, "prediction": "nope"}
    assert extract_texts(payload) == []
    assert inject_texts(payload, []) == payload
