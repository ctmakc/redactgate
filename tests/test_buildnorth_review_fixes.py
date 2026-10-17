"""Regression tests for the Codex review findings on PR #1 (Build North, Oct 2026).

1. a fallback provider must get its OWN model, never the caller's foreign model name;
2. (playground quota atomicity lives in test_buildnorth_tenants_integration.py — needs Postgres);
3. birth dates written in words stay redacted, including with Presidio enabled;
4. lowercase Canadian postal codes are caught.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from app import config as app_config
from app.config import settings as cfg
from app.gateway.base import Provider, ProviderError, register_provider, reset_provider_cache
from app.redaction.detect import Detector
from app.redaction.regex_packs import load_packs
from tests.test_proxy_e2e import SIN, audit, fake_provider, make_client, store  # noqa: F401

# ── 1. fallback model resolution ──────────────────────────────────────────────────


class DownPrimary(Provider):
    name = "downprimary"

    def default_model(self) -> str:
        return "claude-opus-5-5"

    async def complete(self, payload):
        raise ProviderError("upstream error: credit balance is too low", status_code=400, provider=self.name)

    async def stream(self, payload):  # pragma: no cover - not used
        raise ProviderError("down", provider=self.name)
        yield {}


@pytest.fixture
def primary_down_ollama_fallback(monkeypatch):
    import app.gateway.base as gw

    register_provider("downprimary", lambda s: DownPrimary(s))
    monkeypatch.setattr(cfg, "ai_fallback_providers", "ollama")
    monkeypatch.setattr(cfg, "ollama_base_url", "http://ollama.test:11434")
    monkeypatch.setattr(cfg, "ollama_default_model", "qwen2.5:latest")
    reset_provider_cache()
    yield
    gw._REGISTRY.pop("downprimary", None)
    reset_provider_cache()


@respx.mock
def test_fallback_gets_its_own_model_not_the_callers(primary_down_ollama_fallback, make_client):  # noqa: F811
    """README payload asks for gpt-4o-mini; with the primary down, Ollama must get its default."""
    from app import deps
    from app.gateway.base import get_provider

    def ollama_chat(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["model"] != "qwen2.5:latest":  # what a real Ollama does for an unknown model
            return httpx.Response(404, json={"error": f"model '{body['model']}' not found"})
        user = body["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={"model": body["model"], "message": {"role": "assistant", "content": "ok: " + user}, "done": True},
        )

    route = respx.post("http://ollama.test:11434/api/chat").mock(side_effect=ollama_chat)
    client = make_client()
    client.app.dependency_overrides[deps.get_active_provider] = lambda: get_provider("downprimary", cfg)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": f"SIN {SIN}"}]},
    )
    assert resp.status_code == 200, resp.text
    assert json.loads(route.calls.last.request.content)["model"] == "qwen2.5:latest"
    assert resp.json()["choices"][0]["message"]["content"] == f"ok: SIN {SIN}"


# ── 3. birth dates in words ───────────────────────────────────────────────────────

WORD_DOBS = [
    ("date of birth: March 3, 1980", "March 3, 1980"),
    ("DOB Mar. 3 1980", "Mar. 3 1980"),
    ("born on 3 March 1980 in Ottawa", "3 March 1980"),
    ("born on the 3rd of March, 1980", "3rd of March, 1980"),
    ("birthdate: September 21st, 1975", "September 21st, 1975"),
    ("date de naissance : 3 mars 1980", "3 mars 1980"),
    ("née le 1er août 1979", "1er août 1979"),
    ("dob: 3/7/1980", "3/7/1980"),
]


def _generic(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for pack in load_packs(["GENERIC"]):
        for sp in pack.detect(text):
            out.setdefault(sp.entity_type, []).append(sp.text)
    return out


@pytest.mark.parametrize(("text", "value"), WORD_DOBS)
def test_dob_written_in_words_is_detected(text, value):
    assert value in _generic(text).get("DATE_OF_BIRTH", []), _generic(text)


@pytest.mark.parametrize(
    "text",
    [
        "the return was filed on March 3, 1980",  # no birth cue
        "meeting on 3 March 2026",
        "tax year 2025 ends December 31, 2025",
    ],
)
def test_word_dates_without_birth_cue_are_left_alone(text):
    assert "DATE_OF_BIRTH" not in _generic(text)


def _presidio_available() -> bool:
    from app.redaction.presidio_ner import PresidioDetector

    return PresidioDetector().available


@pytest.mark.skipif(not _presidio_available(), reason="presidio/spaCy not installed")
@pytest.mark.parametrize(("text", "value"), WORD_DOBS[:5])
def test_dob_in_words_redacted_with_presidio_enabled(monkeypatch, text, value):
    """The demo runs with ENABLE_PRESIDIO=true; the date must still be a DOB span there."""
    monkeypatch.setattr(app_config.settings, "enable_presidio", True, raising=False)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", False, raising=False)
    result = asyncio.run(Detector().detect(f"Client file. {text}.", pack_codes=["GENERIC", "CA"]))
    dob = [s.text for s in result.spans if s.entity_type == "DATE_OF_BIRTH"]
    assert value in dob, [(s.entity_type, s.text, s.source) for s in result.spans]


# ── 4. lowercase postal codes ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "value"),
    [("lives in toronto m5j 2g2", "m5j 2g2"), ("Code: k1a0b1", "k1a0b1"), ("H2X-1Y4 Montreal", "H2X-1Y4")],
)
def test_postal_code_any_case(text, value):
    found: dict[str, list[str]] = {}
    for pack in load_packs(["CA"]):
        for sp in pack.detect(text):
            found.setdefault(sp.entity_type, []).append(sp.text)
    assert value in found.get("POSTAL_CODE", []), found
