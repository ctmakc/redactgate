"""Health-probe route tests.

``GET /healthz`` must answer 200 unconditionally (no DB) via the FastAPI TestClient.
``GET /readyz`` touches the DB, so its success path is integration-only; here we only
assert it does not raise and returns a JSON status (200 or 503 depending on DB presence).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.health import router as health_router


@pytest.fixture
def client() -> TestClient:
    # Build a minimal app wired with ONLY the health router so /healthz never depends on
    # DB-touching lifespan startup (the real app factory wires migrations on boot).
    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app)


def test_healthz_returns_200_ok(client: TestClient):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_healthz_has_no_db_dependency(client: TestClient):
    # Called repeatedly with no datastore configured — must stay green every time.
    for _ in range(3):
        assert client.get("/healthz").status_code == 200


def test_readyz_returns_json_status(client: TestClient):
    # No live Postgres in the unit lane -> readiness probe reports "unavailable" (503),
    # but it must degrade gracefully (never 500 / never raise).
    resp = client.get("/readyz")
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "status" in body
    if resp.status_code == 503:
        assert body["status"] == "unavailable"


def test_healthz_via_real_app_factory():
    # The production app factory must also expose /healthz (router actually wired).
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        resp = c.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_root_lists_providers():
    from app.main import create_app

    app = create_app()
    with TestClient(app) as c:
        resp = c.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "RedactGate"
        # adapters self-register on import; the active provider is surfaced.
        assert isinstance(body["providers"], list)
        assert "active_provider" in body
