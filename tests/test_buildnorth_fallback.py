"""Policy-safe provider fallback and the explain view (Build North, Oct 2026)."""

from __future__ import annotations

import pytest

from app.config import settings as cfg
from app.gateway.base import Provider, ProviderError, register_provider, reset_provider_cache
from app.gateway.openai import build_completion
from app.schemas.entities import PolicyDecision, PolicyMode
from tests.test_proxy_e2e import SIN, audit, fake_provider, make_client, store  # noqa: F401


class DownProvider(Provider):
    name = "down"

    async def complete(self, payload):
        raise ProviderError("upstream error: credit balance is too low", status_code=400, provider="down")

    async def stream(self, payload):  # pragma: no cover - not used
        raise ProviderError("down", provider="down")
        yield {}


class LocalEcho(Provider):
    name = "localecho"
    seen: list[dict] = []

    async def complete(self, payload):
        LocalEcho.seen.append(payload)
        user = [m["content"] for m in payload["messages"] if m["role"] == "user"][-1]
        return build_completion(model="local-model", content="local: " + user)

    async def stream(self, payload):  # pragma: no cover
        yield {}


@pytest.fixture
def providers(monkeypatch):
    import app.gateway.base as gw

    register_provider("down", lambda s: DownProvider(s))
    register_provider("localecho", lambda s: LocalEcho(s))
    reset_provider_cache()
    LocalEcho.seen = []
    monkeypatch.setattr(cfg, "ai_fallback_providers", "localecho")
    yield
    gw._REGISTRY.pop("down", None)
    gw._REGISTRY.pop("localecho", None)
    reset_provider_cache()


def _client_with_primary_down(make_client, decision=None):  # noqa: F811
    from app import deps
    from app.gateway.base import get_provider

    client = make_client(decision)
    client.app.dependency_overrides[deps.get_active_provider] = lambda: get_provider("down", cfg)
    return client


def test_fallback_serves_request_when_primary_fails(providers, make_client, audit):  # noqa: F811
    client = _client_with_primary_down(make_client)
    resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": f"SIN {SIN}"}]})
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == f"local: SIN {SIN}"
    # the fallback still only saw placeholders
    assert SIN not in LocalEcho.seen[-1]["messages"][-1]["content"]
    assert audit.events[-1]["provider"] == "localecho"


def test_fallback_never_widens_the_policy_allow_list(providers, make_client):  # noqa: F811
    decision = PolicyDecision(mode=PolicyMode.TOKENIZE, blocked=False, allowed_providers=["down"])
    client = _client_with_primary_down(make_client, decision)
    resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 400  # primary's own error surfaces
    assert LocalEcho.seen == []  # forbidden fallback was never called


def test_explain_block_shows_what_left_the_perimeter(make_client, fake_provider, store, audit):  # noqa: F811
    from app.routes.proxy import process_payload

    decision = PolicyDecision(mode=PolicyMode.TOKENIZE, blocked=False)
    from app.auth import AuthContext

    async def run():
        return await process_payload(
            {"messages": [{"role": "user", "content": f"Client SIN {SIN}, email a@b.ca"}]},
            "/admin/playground",
            store=store,
            audit_sink=audit,
            decision=decision,
            provider=fake_provider,
            auth=AuthContext(team_id="t", policy_id=None, api_key_id=None),
            explain=True,
        )

    import asyncio
    import json

    resp = asyncio.run(run())
    body = json.loads(resp.body)
    rg = body["redactgate"]
    upstream = rg["upstream_messages"][-1]["content"]
    assert SIN not in upstream and "a@b.ca" not in upstream
    assert "[[SIN_" in rg["upstream_answer"]
    assert SIN in body["choices"][0]["message"]["content"]
    spans = rg["inputs"][0]["spans"]
    text = rg["inputs"][0]["text"]
    assert {text[s["start"]:s["end"]] for s in spans} >= {SIN, "a@b.ca"}
    assert rg["entity_counts"]["SIN"] == 1
