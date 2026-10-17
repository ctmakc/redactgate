"""Regression tests for the Codex review findings on PR #1 (Build North, Oct 2026).

1. a fallback provider must get its OWN model, never the caller's foreign model name;
2. (playground quota atomicity lives in test_buildnorth_tenants_integration.py — needs Postgres);
3. birth dates written in words stay redacted, including with Presidio enabled;
4. lowercase Canadian postal codes are caught.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.config import settings as cfg
from app.gateway.base import Provider, ProviderError, register_provider, reset_provider_cache
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
