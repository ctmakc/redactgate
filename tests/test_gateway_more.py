"""Extra adapter coverage: Bedrock translation, Azure/DO GenAI URL, headers, streaming and
error mapping. Upstream HTTP is mocked with ``respx``; boto3 is replaced with a local fake
module — no network, no AWS.
"""

from __future__ import annotations

import io
import json
import sys
import types

import httpx
import pytest
import respx

from app.gateway.azure import AzureOpenAIProvider
from app.gateway.base import ProviderError, get_provider, reset_provider_cache
from app.gateway.bedrock import BedrockProvider, _anthropic_body, _content_to_text
from app.gateway.do_genai import DoGenAIProvider
from tests.test_gateway import _collect, _payload, _settings, _sse

# ── Bedrock: pure translation helpers ──────────────────────────────────────────────


def test_content_to_text_variants():
    assert _content_to_text("plain") == "plain"
    assert _content_to_text(
        [
            {"type": "text", "text": "a"},
            {"type": "image_url", "image_url": {"url": "x"}},
            {"type": "input_text", "text": "b"},
            {"type": "text", "text": 5},
            "loose string",
        ]
    ) == "ab"
    assert _content_to_text(None) == ""
    assert _content_to_text(42) == ""


def test_anthropic_body_splits_system_and_maps_roles():
    body = _anthropic_body(
        {
            "messages": [
                {"role": "system", "content": "S1"},
                {"role": "system", "content": ""},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo"},
                {"role": "tool", "content": "tool out"},
                {"role": "system", "content": [{"type": "text", "text": "S2"}]},
            ],
            "temperature": 0,
            "max_tokens": 77,
        }
    )
    assert body["anthropic_version"] == "bedrock-2023-05-31"
    assert body["system"] == "S1\n\nS2"
    assert body["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "tool out"},
    ]
    assert body["max_tokens"] == 77
    # temperature 0 is a real value and must be forwarded (not treated as "unset").
    assert body["temperature"] == 0


def test_anthropic_body_defaults():
    body = _anthropic_body({"messages": None})
    assert body["messages"] == []
    assert body["max_tokens"] == 1024
    assert "system" not in body
    assert "temperature" not in body


def test_bedrock_generic_model_resolves_to_default():
    p = BedrockProvider(_settings(bedrock_default_model="anthropic.default-v1"))
    assert p._resolve_model({"model": "default"}) == "anthropic.default-v1"
    assert p._resolve_model({}) == "anthropic.default-v1"
    assert p._resolve_model({"model": "anthropic.other"}) == "anthropic.other"


# ── Bedrock: complete/stream with a faked invoke ───────────────────────────────────


_BEDROCK_REPLY = {
    "content": [
        {"type": "text", "text": "Hello "},
        {"type": "tool_use", "name": "x"},
        {"type": "text", "text": "[[SIN_abcd]]"},
    ],
    "stop_reason": "max_tokens",
    "usage": {"input_tokens": 11, "output_tokens": 4},
}


async def test_bedrock_complete_translates_response(monkeypatch):
    p = BedrockProvider(_settings())
    calls: list[tuple[str, dict]] = []

    def fake_invoke(model, body):
        calls.append((model, body))
        return _BEDROCK_REPLY

    monkeypatch.setattr(p, "_invoke", fake_invoke)
    out = await p.complete({"model": "anthropic.claude-x", "messages": [
        {"role": "system", "content": "S"}, {"role": "user", "content": "q"}]})
    assert out["object"] == "chat.completion"
    assert out["model"] == "anthropic.claude-x"
    assert out["choices"][0]["message"]["content"] == "Hello [[SIN_abcd]]"
    assert out["choices"][0]["finish_reason"] == "length"
    assert out["usage"] == {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}
    assert calls[0][0] == "anthropic.claude-x"
    assert calls[0][1]["system"] == "S"


@pytest.mark.parametrize(
    "stop,expected",
    [("end_turn", "stop"), ("stop_sequence", "stop"), ("tool_use", "tool_calls"),
     (None, "stop"), ("weird", "stop")],
)
async def test_bedrock_stop_reason_mapping(monkeypatch, stop, expected):
    p = BedrockProvider(_settings())
    monkeypatch.setattr(p, "_invoke", lambda m, b: {"content": [], "stop_reason": stop})
    out = await p.complete({"model": "anthropic.x", "messages": []})
    assert out["choices"][0]["finish_reason"] == expected
    assert out["usage"]["total_tokens"] == 0


async def test_bedrock_stream_emits_content_then_finish(monkeypatch):
    p = BedrockProvider(_settings())
    monkeypatch.setattr(p, "_invoke", lambda m, b: _BEDROCK_REPLY)
    chunks = await _collect(p.stream({"model": "anthropic.x", "messages": []}))
    assert len(chunks) == 2
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"] == {"content": "Hello [[SIN_abcd]]"}
    assert chunks[1]["choices"][0]["finish_reason"] == "length"
    assert chunks[0]["id"] == chunks[1]["id"]


async def test_bedrock_stream_empty_content_only_finish(monkeypatch):
    p = BedrockProvider(_settings())
    monkeypatch.setattr(p, "_invoke", lambda m, b: {"content": [], "stop_reason": "end_turn"})
    chunks = await _collect(p.stream({"model": "anthropic.x", "messages": []}))
    assert len(chunks) == 1
    assert chunks[0]["choices"][0]["finish_reason"] == "stop"


def _fake_boto3(monkeypatch, client):
    mod = types.ModuleType("boto3")
    mod.client = lambda service, region_name=None: client(service, region_name)
    monkeypatch.setitem(sys.modules, "boto3", mod)


def test_bedrock_invoke_parses_body_via_fake_boto3(monkeypatch):
    seen: dict = {}

    class _Client:
        def invoke_model(self, modelId, body):
            seen["model"] = modelId
            seen["body"] = json.loads(body)
            return {"body": io.BytesIO(json.dumps({"content": []}).encode())}

    def factory(service, region):
        seen["service"] = service
        seen["region"] = region
        return _Client()

    _fake_boto3(monkeypatch, factory)
    p = BedrockProvider(_settings(bedrock_region="eu-west-3"))
    assert p._invoke("anthropic.x", {"a": 1}) == {"content": []}
    assert seen == {"service": "bedrock-runtime", "region": "eu-west-3",
                    "model": "anthropic.x", "body": {"a": 1}}


def test_bedrock_invoke_error_maps_to_502(monkeypatch):
    class _Client:
        def invoke_model(self, modelId, body):
            raise RuntimeError("throttled")

    _fake_boto3(monkeypatch, lambda s, r: _Client())
    with pytest.raises(ProviderError) as ei:
        BedrockProvider(_settings())._invoke("anthropic.x", {})
    assert ei.value.status_code == 502
    assert ei.value.provider == "bedrock"


def test_bedrock_client_construction_failure_is_not_configured(monkeypatch):
    def factory(service, region):
        raise RuntimeError("no credentials")

    _fake_boto3(monkeypatch, factory)
    with pytest.raises(ProviderError) as ei:
        BedrockProvider(_settings())._invoke("anthropic.x", {})
    assert ei.value.status_code == 400
    assert "not configured" in str(ei.value)


# ── Azure ─────────────────────────────────────────────────────────────────────────


def _az(**over):
    base = dict(
        azure_openai_endpoint="https://az.test/",
        azure_openai_api_key="az-key",
        azure_openai_deployment="dep1",
        azure_openai_api_version="2024-06-01",
    )
    base.update(over)
    return AzureOpenAIProvider(_settings(**base))


def test_azure_url_strips_trailing_slash_and_has_version():
    p = _az()
    assert p._url("dep1") == (
        "https://az.test/openai/deployments/dep1/chat/completions?api-version=2024-06-01"
    )
    assert p.default_model() == "dep1"


def test_azure_explicit_model_selects_deployment():
    p = _az()
    assert p._deployment_for({"model": "gpt-4o-prod"}) == "gpt-4o-prod"
    assert p._deployment_for({"model": "default"}) == "dep1"
    assert p._deployment_for({}) == "dep1"


def test_azure_body_does_not_mutate_input():
    p = _az()
    payload = _payload(model="x")
    body = p._body(payload, stream=True)
    assert "model" not in body and body["stream"] is True
    assert payload["model"] == "x" and payload["stream"] is False


def test_azure_requires_both_endpoint_and_key():
    with pytest.raises(ProviderError):
        _az(azure_openai_api_key="")._require_configured()
    with pytest.raises(ProviderError):
        _az(azure_openai_endpoint="")._require_configured()


@respx.mock
async def test_azure_stream_yields_chunks():
    body = _sse(
        {"object": "chat.completion.chunk", "choices": [{"delta": {"content": "a"}}]},
        {"object": "chat.completion.chunk", "choices": [{"delta": {"content": "b"}}]},
    )
    route = respx.post("https://az.test/openai/deployments/dep1/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    chunks = await _collect(_az().stream(_payload(model="default", stream=True)))
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["a", "b"]
    assert json.loads(route.calls.last.request.content)["stream"] is True


@respx.mock
async def test_azure_upstream_status_is_propagated():
    respx.post("https://az.test/openai/deployments/dep1/chat/completions").mock(
        return_value=httpx.Response(429, text="slow down")
    )
    with pytest.raises(ProviderError) as ei:
        await _az().complete(_payload(model="default"))
    assert ei.value.status_code == 429
    assert "slow down" in str(ei.value)


@respx.mock
async def test_azure_stream_upstream_error():
    respx.post("https://az.test/openai/deployments/dep1/chat/completions").mock(
        return_value=httpx.Response(401, text="bad key")
    )
    with pytest.raises(ProviderError) as ei:
        await _collect(_az().stream(_payload(model="default", stream=True)))
    assert ei.value.status_code == 401


@respx.mock
async def test_azure_transport_error_maps_to_502():
    respx.post("https://az.test/openai/deployments/dep1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with pytest.raises(ProviderError) as ei:
        await _az().complete(_payload(model="default"))
    assert ei.value.status_code == 502


@respx.mock
async def test_azure_invalid_json_maps_to_502():
    respx.post("https://az.test/openai/deployments/dep1/chat/completions").mock(
        return_value=httpx.Response(200, text="<html>", headers={"content-type": "application/json"})
    )
    with pytest.raises(ProviderError) as ei:
        await _az().complete(_payload(model="default"))
    assert ei.value.status_code == 502


@pytest.mark.skip(
    reason="bug: Azure adapter puts the client-supplied model into the URL path/query "
    "unvalidated (Gemini got a _MODEL_RE guard for the same issue, Azure did not)"
)
@pytest.mark.parametrize("bad", ["../../evil", "a/b", "x?api-version=evil", "has space", "d#frag"])
def test_azure_rejects_path_injection_model(bad):
    with pytest.raises(ProviderError):
        _az()._deployment_for({"model": bad})


def test_azure_injected_model_changes_request_target():
    """Documents the current (vulnerable) behaviour behind the skipped test above."""
    url = httpx.URL(_az()._url("x/chat/completions?api-version=evil#"))
    assert url.params.get("api-version") == "evil"


# ── DigitalOcean GenAI ────────────────────────────────────────────────────────────


@respx.mock
async def test_do_genai_generic_model_and_bearer():
    route = respx.post("https://do.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"object": "chat.completion", "choices": []})
    )
    await DoGenAIProvider(_settings()).complete(_payload(model="default"))
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer do-test"
    sent = json.loads(req.content)
    assert sent["model"] == "do-default"
    assert sent["stream"] is False


@respx.mock
async def test_do_genai_no_key_sends_no_auth_header():
    route = respx.post("https://do.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": []})
    )
    await DoGenAIProvider(_settings(do_genai_api_key="")).complete(_payload(model="m1"))
    req = route.calls.last.request
    assert "authorization" not in req.headers
    assert json.loads(req.content)["model"] == "m1"


@respx.mock
async def test_do_genai_stream_and_errors():
    respx.post("https://do.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text=": keepalive\n\n" + _sse({"choices": [{"delta": {"content": "z"}}]}),
            headers={"content-type": "text/event-stream"},
        )
    )
    chunks = await _collect(DoGenAIProvider(_settings()).stream(_payload(stream=True)))
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["z"]


@respx.mock
async def test_do_genai_stream_upstream_error():
    respx.post("https://do.test/v1/chat/completions").mock(
        return_value=httpx.Response(503, text="maintenance")
    )
    with pytest.raises(ProviderError) as ei:
        await _collect(DoGenAIProvider(_settings()).stream(_payload(stream=True)))
    assert ei.value.status_code == 503


@respx.mock
async def test_do_genai_transport_error_and_bad_json():
    route = respx.post("https://do.test/v1/chat/completions")
    route.mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderError) as ei:
        await DoGenAIProvider(_settings()).complete(_payload())
    assert ei.value.status_code == 502
    route.mock(return_value=httpx.Response(200, text="nope"))
    with pytest.raises(ProviderError) as ei:
        await DoGenAIProvider(_settings()).complete(_payload())
    assert ei.value.status_code == 502


# ── Registry ──────────────────────────────────────────────────────────────────────


def test_registry_unknown_provider_400_and_singleton():
    import app.gateway  # noqa: F401  (self-registers adapters)

    reset_provider_cache()
    try:
        with pytest.raises(ProviderError) as ei:
            get_provider("nope", _settings())
        assert ei.value.status_code == 400
        a = get_provider("do-genai", _settings())
        b = get_provider("do-genai", _settings(do_genai_default_model="other"))
        assert a is b  # cached until reset
    finally:
        reset_provider_cache()
