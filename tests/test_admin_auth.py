"""Every admin route is gated by X-Admin-Token (no DB: the session is never reached)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.routes.admin as admin_mod
from app.db import get_session

ROUTES = [
    ("GET", "/admin/stats"),
    ("GET", "/admin/audit"),
    ("GET", "/admin/policies"),
    ("POST", "/admin/policies"),
    ("GET", "/admin/benchmark"),
]


@pytest.fixture
def admin_client(monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(admin_mod.settings, "admin_token", "test-admin-token")
    reached: list[bool] = []

    async def _no_db():
        reached.append(True)
        raise AssertionError("DB session must not be opened for unauthenticated calls")
        yield  # pragma: no cover

    app = create_app()
    app.dependency_overrides[get_session] = _no_db
    return TestClient(app, raise_server_exceptions=False), reached


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Admin-Token": "wrong"}, {"X-Admin-Token": "test-admin-tokenX"}, {"X-Admin-Token": ""}],
    ids=["none", "wrong", "suffix", "empty"],
)
def test_admin_routes_reject_missing_or_wrong_token(admin_client, method, path, headers):
    client, reached = admin_client
    resp = client.request(method, path, headers=headers, json={})
    assert resp.status_code == 401
    assert reached == []


def test_admin_token_uses_constant_time_compare(monkeypatch):
    import asyncio
    import hmac

    calls: list[tuple] = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(admin_mod.settings, "admin_token", "s3cret")
    monkeypatch.setattr(admin_mod.hmac, "compare_digest", spy)
    assert asyncio.run(admin_mod.require_admin("s3cret")) is None
    assert calls == [(b"s3cret", b"s3cret")]


def test_admin_token_non_ascii_header_is_rejected_not_crashing(monkeypatch):
    import asyncio

    from fastapi import HTTPException

    monkeypatch.setattr(admin_mod.settings, "admin_token", "s3cret")
    with pytest.raises(HTTPException) as e:
        asyncio.run(admin_mod.require_admin("sécret"))
    assert e.value.status_code == 401
