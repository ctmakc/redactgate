"""Apertus adapter + Apertus-backed LLM-NER pass (upstream mocked with respx, no network)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import Settings
from app.gateway.apertus import (
    PLACEHOLDER_INSTRUCTION,
    ApertusProvider,
    with_placeholder_instruction,
)
from app.gateway.base import ProviderError, available_providers

BASE = "http://apertus.test:8080/v1"


def _settings(**env: str) -> Settings:
    values = {"APERTUS_BASE_URL": BASE, "environment": "test"}
    values.update(env)
    return Settings(**values)


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "created": 0,
        "model": "swiss-ai/Apertus-v1.5-8B",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }


def test_apertus_is_registered() -> None:
    assert "apertus" in available_providers()


def test_hack_apertus_env_names_are_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://judge.test/v1")
    monkeypatch.setenv("LLM_NAME", "swiss-ai/Apertus-v1.5-70B")
    monkeypatch.setenv("LLM_API_KEY", "judge-key")
    s = Settings(environment="test")
    assert s.apertus_base_url == "http://judge.test/v1"
    assert s.apertus_default_model == "swiss-ai/Apertus-v1.5-70B"
    assert s.apertus_api_key == "judge-key"


def test_placeholder_instruction_only_when_placeholders_present() -> None:
    plain = {"messages": [{"role": "user", "content": "Hallo"}]}
    assert with_placeholder_instruction(plain) is plain

    redacted = {"messages": [{"role": "user", "content": "AHV [[AHV_3f9a1c]] prüfen"}]}
    out = with_placeholder_instruction(redacted)
    assert out["messages"][0] == {"role": "system", "content": PLACEHOLDER_INSTRUCTION}
    assert out["messages"][1] == redacted["messages"][0]
    assert len(redacted["messages"]) == 1  # caller's payload is not mutated


def test_placeholder_instruction_merges_into_existing_system_turn() -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "Antworte auf Deutsch."},
            {"role": "user", "content": [{"type": "text", "text": "IBAN [[IBAN_0a1b2c]]"}]},
        ]
    }
    out = with_placeholder_instruction(payload)
    assert len(out["messages"]) == 2
    assert out["messages"][0]["content"].startswith(PLACEHOLDER_INSTRUCTION)
    assert out["messages"][0]["content"].endswith("Antworte auf Deutsch.")


@respx.mock
async def test_complete_forwards_to_openai_compatible_endpoint() -> None:
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("Grüezi [[PERSON_00aa11]]"))
    )
    provider = ApertusProvider(_settings(APERTUS_API_KEY="k1"))
    out = await provider.complete(
        {"model": "", "messages": [{"role": "user", "content": "Hallo [[PERSON_00aa11]]"}]}
    )
    assert out["choices"][0]["message"]["content"] == "Grüezi [[PERSON_00aa11]]"
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "swiss-ai/Apertus-v1.5-8B"  # generic model -> configured default
    assert sent["stream"] is False
    assert sent["messages"][0]["role"] == "system"
    assert route.calls.last.request.headers["Authorization"] == "Bearer k1"


@respx.mock
async def test_upstream_error_raises_provider_error() -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(503, text="busy"))
    with pytest.raises(ProviderError):
        await ApertusProvider(_settings()).complete({"messages": [{"role": "user", "content": "x"}]})


# ── LLM-NER on a local Apertus ────────────────────────────────────────────────


@respx.mock
async def test_llm_ner_uses_dedicated_local_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config as app_config
    from app.gateway.base import reset_provider_cache
    from app.redaction import llm_ner as ner_mod

    text = "Mandantin: Lea Muster, Bahnhofstrasse 12, 8001 Zürich."
    reply = {
        "entities": [
            {"type": "PERSON", "text": "Lea Muster"},
            {"type": "ADDRESS", "text": "Bahnhofstrasse 12, 8001 Zürich"},
            {"type": "PERSON", "text": "Not In Text"},
        ]
    }
    route = respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion(json.dumps(reply, ensure_ascii=False)))
    )
    local = _settings()
    monkeypatch.setattr(app_config.settings, "ai_provider", "openai", raising=False)
    monkeypatch.setattr(app_config.settings, "llm_ner_provider", "apertus", raising=False)
    monkeypatch.setattr(app_config.settings, "apertus_base_url", local.apertus_base_url, raising=False)
    reset_provider_cache()
    try:
        spans = await ner_mod.llm_ner(text)
    finally:
        reset_provider_cache()

    assert [(s.entity_type, s.text) for s in spans] == [
        ("PERSON", "Lea Muster"),
        ("ADDRESS", "Bahnhofstrasse 12, 8001 Zürich"),
    ]
    assert all(text[s.start : s.end] == s.text and s.source == "llm" for s in spans)
    sent = json.loads(route.calls.last.request.content)
    # grammar-constrained output for the llama.cpp / vLLM server
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["messages"][-1]["content"] == text


def test_llm_ner_payload_defaults_to_json_object() -> None:
    from app.redaction.llm_ner import _build_payload

    assert _build_payload("x")["response_format"] == {"type": "json_object"}
    structured = _build_payload("x", structured=True)["response_format"]
    assert structured["json_schema"]["schema"]["required"] == ["entities"]


# ── placeholder repair (degraded tokens from a small model) ──────────────────

from app.gateway.apertus import repair_placeholders  # noqa: E402

_KNOWN = {
    "c4857b": ("AHV", "[[AHV_c4857b]]"),
    "cb8c34": ("IBAN", "[[IBAN_cb8c34]]"),
    "bcc49f": ("DATE_OF_BIRTH", "[[DATE_OF_BIRTH_bcc49f]]"),
}


@pytest.mark.parametrize(
    "text,expected,n",
    [
        ("Lea, AHV c4857b, IBAN cb8c34;", "Lea, AHV [[AHV_c4857b]], IBAN [[IBAN_cb8c34]];", 2),
        ("AHV_c4857b fehlt", "[[AHV_c4857b]] fehlt", 1),
        ("[AHV_c4857b]", "[[AHV_c4857b]]", 1),
        ("[[AHV_c4857b]", "[[AHV_c4857b]]", 1),
        ("DATE_OF_BIRTH_bcc49f", "[[DATE_OF_BIRTH_bcc49f]]", 1),
        ("Nummer c4857b.", "Nummer [[AHV_c4857b]].", 1),
    ],
)
def test_degraded_tokens_are_repaired(text: str, expected: str, n: int) -> None:
    assert repair_placeholders(text, _KNOWN) == (expected, n)


@pytest.mark.parametrize(
    "text",
    [
        "[[AHV_c4857b]] bleibt",  # already canonical
        "IBAN c4857b",  # type label does not match the token's type
        "Code abcdef und facade",  # hex-looking words the model was never given
        "xc4857b und c4857bx",  # embedded in a longer word
    ],
)
def test_repair_leaves_other_text_alone(text: str) -> None:
    assert repair_placeholders(text, _KNOWN) == (text, 0)


@respx.mock
async def test_complete_repairs_tokens_it_was_given() -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json=_completion("AHV c4857b, IBAN ffffff"))
    )
    provider = ApertusProvider(_settings())
    out = await provider.complete(
        {"messages": [{"role": "user", "content": "AHV [[AHV_c4857b]] prüfen"}]}
    )
    # c4857b was in the request -> repaired; ffffff never was -> untouched
    assert out["choices"][0]["message"]["content"] == "AHV [[AHV_c4857b]], IBAN ffffff"
    assert provider.last_repairs == 1
