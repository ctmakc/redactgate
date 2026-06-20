"""Gateway adapter tests — each provider's upstream HTTP is mocked with ``respx`` and we
assert the adapter normalizes the native response into the canonical OpenAI
``chat.completion`` / ``chat.completion.chunk`` shape, and raises ``ProviderError`` on a
500 upstream.

Pure unit lane: no network, no DB. ``respx`` intercepts ``httpx.AsyncClient`` calls.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import Settings
from app.gateway.anthropic import AnthropicProvider
from app.gateway.azure import AzureOpenAIProvider
from app.gateway.base import ProviderError
from app.gateway.bedrock import BedrockProvider
from app.gateway.do_genai import DoGenAIProvider
from app.gateway.gemini import GeminiProvider
from app.gateway.ollama import OllamaProvider
from app.gateway.openai import OpenAIProvider

# ── Helpers ──────────────────────────────────────────────────────────────────────


def _payload(model: str = "gpt-test", *, stream: bool = False) -> dict:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are terse."},
            {"role": "user", "content": "Hello [[SIN_abcd]] world"},
        ],
        "stream": stream,
        "temperature": 0.2,
        "max_tokens": 64,
    }


def _settings(**over) -> Settings:
    base = dict(
        environment="test",
        openai_api_key="sk-test",
        openai_base_url="https://api.openai.test/v1",
        openai_default_model="gpt-4o-mini",
        anthropic_api_key="ak-test",
        anthropic_base_url="https://api.anthropic.test",
        anthropic_default_model="claude-test",
        gemini_api_key="gk-test",
        gemini_base_url="https://gemini.test/v1beta",
        gemini_default_model="gemini-test",
        ollama_base_url="http://ollama.test:11434",
        ollama_default_model="llama-test",
        do_genai_api_key="do-test",
        do_genai_base_url="https://do.test/v1",
        do_genai_default_model="do-default",
    )
    base.update(over)
    return Settings(**base)


def _assert_openai_completion(out: dict, *, content: str) -> None:
    assert out["object"] == "chat.completion"
    assert isinstance(out["id"], str) and out["id"]
    assert out["choices"][0]["message"]["role"] == "assistant"
    assert out["choices"][0]["message"]["content"] == content
    assert "finish_reason" in out["choices"][0]


def _sse(*objs: dict) -> str:
    return "".join(f"data: {json.dumps(o)}\n\n" for o in objs) + "data: [DONE]\n\n"


async def _collect(aiter) -> list[dict]:
    return [c async for c in aiter]


# ── OpenAI passthrough ─────────────────────────────────────────────────────────


@respx.mock
async def test_openai_complete_passthrough():
    s = _settings()
    raw = {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi back"},
             "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    route = respx.post("https://api.openai.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=raw)
    )
    out = await OpenAIProvider(s).complete(_payload())
    assert route.called
    _assert_openai_completion(out, content="hi back")
    # Authorization + model + stream=False forwarded.
    sent = json.loads(route.calls.last.request.content)
    assert sent["stream"] is False
    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


@respx.mock
async def test_openai_generic_model_falls_back_to_default():
    s = _settings()
    respx.post("https://api.openai.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                         "finish_reason": "stop"}],
        })
    )
    await OpenAIProvider(s).complete(_payload(model="default"))
    sent = json.loads(respx.calls.last.request.content)
    assert sent["model"] == "gpt-4o-mini"


@respx.mock
async def test_openai_stream_yields_chunks():
    s = _settings()
    body = _sse(
        {"id": "c", "object": "chat.completion.chunk", "model": "gpt-4o-mini",
         "choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]},
        {"id": "c", "object": "chat.completion.chunk", "model": "gpt-4o-mini",
         "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
    )
    respx.post("https://api.openai.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    )
    chunks = await _collect(OpenAIProvider(s).stream(_payload(stream=True)))
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "Hello"


@respx.mock
async def test_openai_500_raises_provider_error():
    s = _settings()
    respx.post("https://api.openai.test/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(ProviderError) as ei:
        await OpenAIProvider(s).complete(_payload())
    assert ei.value.status_code == 500
    assert ei.value.provider == "openai"


@respx.mock
async def test_openai_invalid_json_raises():
    s = _settings()
    respx.post("https://api.openai.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, text="not json", headers={"content-type": "application/json"})
    )
    with pytest.raises(ProviderError):
        await OpenAIProvider(s).complete(_payload())


# ── Ollama ─────────────────────────────────────────────────────────────────────


@respx.mock
async def test_ollama_complete_maps_to_openai_shape():
    s = _settings()
    native = {
        "model": "llama-test",
        "message": {"role": "assistant", "content": "ollama says hi"},
        "done": True,
        "prompt_eval_count": 11,
        "eval_count": 4,
    }
    route = respx.post("http://ollama.test:11434/api/chat").mock(
        return_value=httpx.Response(200, json=native)
    )
    out = await OllamaProvider(s).complete(_payload())
    assert route.called
    _assert_openai_completion(out, content="ollama says hi")
    assert out["usage"]["prompt_tokens"] == 11
    assert out["usage"]["completion_tokens"] == 4
    # No Authorization header for Ollama.
    assert "authorization" not in {k.lower() for k in route.calls.last.request.headers}


@respx.mock
async def test_ollama_stream_ndjson_to_chunks():
    s = _settings()
    lines = (
        json.dumps({"model": "llama-test", "message": {"content": "Hel"}, "done": False}) + "\n"
        + json.dumps({"model": "llama-test", "message": {"content": "lo"}, "done": False}) + "\n"
        + json.dumps({"model": "llama-test", "message": {"content": ""}, "done": True}) + "\n"
    )
    respx.post("http://ollama.test:11434/api/chat").mock(
        return_value=httpx.Response(200, text=lines)
    )
    chunks = await _collect(OllamaProvider(s).stream(_payload(stream=True)))
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "Hello"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@respx.mock
async def test_ollama_500_raises():
    s = _settings()
    respx.post("http://ollama.test:11434/api/chat").mock(
        return_value=httpx.Response(500, text="down")
    )
    with pytest.raises(ProviderError) as ei:
        await OllamaProvider(s).complete(_payload())
    assert ei.value.provider == "ollama"


# ── Anthropic ──────────────────────────────────────────────────────────────────


@respx.mock
async def test_anthropic_complete_translates_messages_response():
    s = _settings()
    native = {
        "model": "claude-test",
        "content": [{"type": "text", "text": "claude reply"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 9, "output_tokens": 3},
    }
    route = respx.post("https://api.anthropic.test/v1/messages").mock(
        return_value=httpx.Response(200, json=native)
    )
    out = await AnthropicProvider(s).complete(_payload())
    assert route.called
    _assert_openai_completion(out, content="claude reply")
    assert out["choices"][0]["finish_reason"] == "stop"
    sent = json.loads(route.calls.last.request.content)
    # system pulled out, only user/assistant in messages, max_tokens present.
    assert sent["system"] == "You are terse."
    assert all(m["role"] in ("user", "assistant") for m in sent["messages"])
    assert sent["max_tokens"] == 64
    assert route.calls.last.request.headers["x-api-key"] == "ak-test"


@respx.mock
async def test_anthropic_stream_sse_to_chunks():
    s = _settings()
    body = (
        'data: {"type":"message_start"}\n\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}\n\n'
        'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n'
        'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
        'data: {"type":"message_stop"}\n\n'
    )
    respx.post("https://api.anthropic.test/v1/messages").mock(
        return_value=httpx.Response(200, text=body)
    )
    chunks = await _collect(AnthropicProvider(s).stream(_payload(stream=True)))
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "Hello"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@respx.mock
async def test_anthropic_500_raises():
    s = _settings()
    respx.post("https://api.anthropic.test/v1/messages").mock(
        return_value=httpx.Response(500, text="overloaded")
    )
    with pytest.raises(ProviderError) as ei:
        await AnthropicProvider(s).complete(_payload())
    assert ei.value.provider == "anthropic"
    assert ei.value.status_code == 500


# ── Gemini ─────────────────────────────────────────────────────────────────────


@respx.mock
async def test_gemini_complete_translates_generate_content():
    s = _settings()
    native = {
        "candidates": [
            {"content": {"parts": [{"text": "gemini "}, {"text": "reply"}]},
             "finishReason": "STOP"}
        ],
        "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2},
    }
    route = respx.post(
        "https://gemini.test/v1beta/models/gemini-test:generateContent"
    ).mock(return_value=httpx.Response(200, json=native))
    out = await GeminiProvider(s).complete(_payload(model="auto"))
    assert route.called
    _assert_openai_completion(out, content="gemini reply")
    # API key travels as a query parameter.
    assert "key=gk-test" in str(route.calls.last.request.url)
    sent = json.loads(route.calls.last.request.content)
    assert sent["systemInstruction"]["parts"][0]["text"] == "You are terse."


@respx.mock
async def test_gemini_stream_sse_to_chunks():
    s = _settings()
    body = (
        'data: {"candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}\n\n'
        'data: {"candidates":[{"content":{"parts":[{"text":"lo"}]},"finishReason":"STOP"}]}\n\n'
    )
    respx.post(
        "https://gemini.test/v1beta/models/gemini-test:streamGenerateContent"
    ).mock(return_value=httpx.Response(200, text=body))
    chunks = await _collect(GeminiProvider(s).stream(_payload(model="auto", stream=True)))
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert text == "Hello"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


@respx.mock
async def test_gemini_500_raises():
    s = _settings()
    respx.post(
        "https://gemini.test/v1beta/models/gemini-test:generateContent"
    ).mock(return_value=httpx.Response(500, text="quota"))
    with pytest.raises(ProviderError) as ei:
        await GeminiProvider(s).complete(_payload(model="auto"))
    assert ei.value.provider == "gemini"


# ── DigitalOcean GenAI (OpenAI-compatible) ─────────────────────────────────────


@respx.mock
async def test_do_genai_passthrough_complete():
    s = _settings()
    raw = {
        "id": "c", "object": "chat.completion", "model": "do-default",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "do reply"},
                     "finish_reason": "stop"}],
    }
    route = respx.post("https://do.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=raw)
    )
    out = await DoGenAIProvider(s).complete(_payload(model="default"))
    assert route.called
    _assert_openai_completion(out, content="do reply")
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "do-default"  # generic -> default
    assert route.calls.last.request.headers["authorization"] == "Bearer do-test"


@respx.mock
async def test_do_genai_500_raises():
    s = _settings()
    respx.post("https://do.test/v1/chat/completions").mock(
        return_value=httpx.Response(503, text="unavailable")
    )
    with pytest.raises(ProviderError) as ei:
        await DoGenAIProvider(s).complete(_payload())
    assert ei.value.provider == "do-genai"


# ── Azure: "not configured" path + configured passthrough ──────────────────────


async def test_azure_not_configured_raises():
    s = _settings(azure_openai_endpoint="", azure_openai_api_key="")
    with pytest.raises(ProviderError) as ei:
        await AzureOpenAIProvider(s).complete(_payload())
    assert ei.value.status_code == 400
    assert "not configured" in str(ei.value)
    assert ei.value.provider == "azure"


async def test_azure_not_configured_stream_raises():
    s = _settings(azure_openai_endpoint="", azure_openai_api_key="")
    with pytest.raises(ProviderError):
        # Consuming the async generator triggers the configuration check.
        await _collect(AzureOpenAIProvider(s).stream(_payload(stream=True)))


@respx.mock
async def test_azure_configured_passthrough():
    s = _settings(
        azure_openai_endpoint="https://az.test",
        azure_openai_api_key="az-key",
        azure_openai_deployment="dep1",
    )
    raw = {
        "id": "c", "object": "chat.completion", "model": "dep1",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "azure reply"},
                     "finish_reason": "stop"}],
    }
    route = respx.post(
        "https://az.test/openai/deployments/dep1/chat/completions"
    ).mock(return_value=httpx.Response(200, json=raw))
    out = await AzureOpenAIProvider(s).complete(_payload(model="default"))
    assert route.called
    _assert_openai_completion(out, content="azure reply")
    # api-version query + api-key header + model stripped from body.
    assert "api-version=" in str(route.calls.last.request.url)
    assert route.calls.last.request.headers["api-key"] == "az-key"
    sent = json.loads(route.calls.last.request.content)
    assert "model" not in sent


# ── Bedrock: "not configured" when boto3/creds absent ──────────────────────────


async def test_bedrock_not_configured_raises():
    s = _settings()
    # boto3 is not installed in the unit lane -> _client() raises "not configured".
    with pytest.raises(ProviderError) as ei:
        await BedrockProvider(s).complete({"model": "anthropic.claude-x", "messages": []})
    assert ei.value.status_code == 400
    assert "not configured" in str(ei.value)
    assert ei.value.provider == "bedrock"


async def test_bedrock_rejects_non_anthropic_model():
    s = _settings()
    with pytest.raises(ProviderError) as ei:
        await BedrockProvider(s).complete({"model": "meta.llama-3", "messages": []})
    assert ei.value.status_code == 400
    assert "anthropic" in str(ei.value)


# ── default_model contract for every adapter ───────────────────────────────────


def test_default_model_reported_per_provider():
    s = _settings()
    assert OpenAIProvider(s).default_model() == "gpt-4o-mini"
    assert OllamaProvider(s).default_model() == "llama-test"
    assert AnthropicProvider(s).default_model() == "claude-test"
    assert GeminiProvider(s).default_model() == "gemini-test"
    assert DoGenAIProvider(s).default_model() == "do-default"
