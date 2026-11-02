"""Proxy authentication through the REAL route + REAL ``get_auth`` dependency.

Only the DB session is faked (returns argon2-hashed api_key rows in memory). The key
property under test: a request without a valid key must never reach the upstream
provider — so no prompt text (redacted or not) leaves the perimeter.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

import app.auth as auth_mod
from app import deps
from app.auth import hash_key, make_key
from app.config import settings as cfg
from app.db import get_session
from app.gateway.base import Provider, get_provider, register_provider, reset_provider_cache
from app.gateway.openai import build_completion
from app.redaction.store import InMemoryTokenStore
from app.schemas.entities import PolicyDecision, PolicyMode

TEAM = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
POLICY = uuid.UUID("00000000-0000-0000-0000-0000000000cc")


class _Row:
    def __init__(self, raw: str, *, revoked: bool = False) -> None:
        self.id = uuid.uuid4()
        self.team_id = TEAM
        self.key_hash = hash_key(raw)
        self.revoked_at = "2024-01-01" if revoked else None


class _Team:
    default_policy_id = POLICY


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _stmt):
        return _Result([r for r in self.rows if r.revoked_at is None])

    async def get(self, _model, _pk):
        return _Team()


class _RecordingProvider(Provider):
    name = "recording"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.calls: list[dict] = []

    async def complete(self, payload):
        self.calls.append(payload)
        return build_completion(model="m", content="ok")

    async def stream(self, payload):  # pragma: no cover
        self.calls.append(payload)
        if False:
            yield {}


GOOD = make_key()
REVOKED = make_key()


@pytest.fixture
def auth_client(monkeypatch):
    import app.gateway.base as gw_base
    from app.main import create_app

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)
    register_provider("recording", lambda s: _RecordingProvider(s))
    reset_provider_cache()
    provider = get_provider("recording", cfg)
    session = _Session([_Row(GOOD), _Row(REVOKED, revoked=True)])
    seen_auth: list = []

    async def _fake_session():
        yield session

    store = InMemoryTokenStore()

    class _Sink:
        async def record(self, **kw):
            seen_auth.append(kw)

    app = create_app()
    app.dependency_overrides[get_session] = _fake_session
    app.dependency_overrides[deps.get_token_store] = lambda: store
    app.dependency_overrides[deps.get_audit_sink] = lambda: _Sink()
    app.dependency_overrides[deps.get_policy_decision] = lambda: PolicyDecision(
        mode=PolicyMode.TOKENIZE, blocked=False
    )
    app.dependency_overrides[deps.get_active_provider] = lambda: provider
    try:
        yield TestClient(app, raise_server_exceptions=False), provider, seen_auth
    finally:
        gw_base._REGISTRY.pop("recording", None)
        reset_provider_cache()


BODY = {"model": "m", "messages": [{"role": "user", "content": "SIN 193 456 787"}]}


def test_valid_key_is_accepted_and_audited_under_its_team(auth_client):
    client, provider, audit = auth_client
    resp = client.post(
        "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {GOOD}"}
    )
    assert resp.status_code == 200
    assert len(provider.calls) == 1
    assert "193 456 787" not in repr(provider.calls[0])
    assert audit[-1]["team_id"] == str(TEAM)
    assert audit[-1]["api_key_id"] is not None


def test_bearer_scheme_is_case_insensitive(auth_client):
    client, provider, _ = auth_client
    resp = client.post(
        "/v1/chat/completions", json=BODY, headers={"Authorization": f"bearer   {GOOD}"}
    )
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": f"Basic {GOOD}"},
        {"Authorization": GOOD},
        {"Authorization": "Bearer rg-not-a-real-key"},
        {"Authorization": f"Bearer {REVOKED}"},
        {"Authorization": f"Bearer {GOOD}x"},
        {"X-Api-Key": GOOD},
    ],
    ids=["none", "empty", "no-token", "basic", "no-scheme", "unknown", "revoked", "suffix", "wrong-header"],
)
def test_bad_credentials_never_reach_upstream(auth_client, headers):
    client, provider, audit = auth_client
    resp = client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert resp.status_code != 200
    assert provider.calls == []
    assert audit == []


@pytest.mark.parametrize("path", ["/v1/chat/completions", "/v1/responses"])
def test_both_proxy_routes_require_auth(auth_client, path):
    client, provider, _ = auth_client
    resp = client.post(path, json={"model": "m", "input": "hi"})
    assert resp.status_code != 200
    assert provider.calls == []


# ── Status codes / envelopes ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer rg-not-a-real-key"}, {"Authorization": f"Bearer {REVOKED}"}],
    ids=["missing", "unknown", "revoked"],
)
def test_bad_credentials_return_401_openai_envelope(auth_client, headers):
    client, _, _ = auth_client
    resp = client.post("/v1/chat/completions", json=BODY, headers=headers)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"
    err = resp.json()["error"]
    assert err["code"] == "invalid_api_key"
    assert err["message"] == "invalid api key"


def test_unhandled_errors_do_not_echo_internal_details(auth_client):
    client, provider, _ = auth_client
    app = client.app

    def _boom():
        raise RuntimeError("postgresql://redactgate:hunter2@db:5432/x SELECT ... 193 456 787")

    app.dependency_overrides[deps.get_policy_decision] = _boom
    resp = client.post(
        "/v1/chat/completions", json=BODY, headers={"Authorization": f"Bearer {GOOD}"}
    )
    assert resp.status_code == 500
    assert "hunter2" not in resp.text
    assert "193 456 787" not in resp.text
    assert resp.json()["error"]["code"] == "internal_error"
    assert provider.calls == []
