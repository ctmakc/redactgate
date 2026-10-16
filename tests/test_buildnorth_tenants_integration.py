"""Tenant-scoped admin API against a live Postgres (RUN_INTEGRATION=1).

Covers the Clerk org -> tenant mapping, cross-tenant isolation of policies, per-team stats,
hash-chain verification and the playground daily cap (Build North, Oct 2026).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from tests.conftest import integration_only

TOKEN = "it-admin-token"


@pytest.fixture
def client(monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(settings, "admin_token", TOKEN)
    with TestClient(create_app()) as c:
        c.headers.update({"X-Admin-Token": TOKEN})
        yield c


def _resolve(client, ext: str, name: str = "Firm") -> dict:
    r = client.post("/admin/tenants/resolve", json={"external_id": ext, "name": name})
    assert r.status_code == 200, r.text
    return r.json()


@integration_only()
def test_resolve_is_idempotent_and_renames(client):
    ext = f"org_it_{uuid.uuid4().hex[:10]}"
    a = _resolve(client, ext, "Maple LLP")
    b = _resolve(client, ext, "Maple Tax LLP")
    assert a["team_id"] == b["team_id"] and a["policy_id"] == b["policy_id"]
    assert b["name"] == "Maple Tax LLP"


@integration_only()
def test_admin_token_required(client):
    r = client.post(
        "/admin/tenants/resolve",
        json={"external_id": "org_x"},
        headers={"X-Admin-Token": "wrong"},
    )
    assert r.status_code == 401


@integration_only()
def test_policies_are_isolated_per_tenant(client):
    t1 = _resolve(client, f"org_it_{uuid.uuid4().hex[:10]}")
    t2 = _resolve(client, f"org_it_{uuid.uuid4().hex[:10]}")
    r = client.post(
        f"/admin/teams/{t1['team_id']}/policies",
        json={"name": "Block SIN", "mode": "tokenize", "blocked_types": ["sin"]},
    )
    assert r.status_code == 201 and r.json()["active"] is True
    pol = r.json()
    assert pol["blocked_types"] == ["SIN"]
    # tenant 2 cannot see or activate tenant 1's policy
    names2 = [p["name"] for p in client.get(f"/admin/teams/{t2['team_id']}/policies").json()["items"]]
    assert "Block SIN" not in names2
    r = client.put(f"/admin/teams/{t2['team_id']}/policy", json={"policy_id": pol["id"]})
    assert r.status_code == 404


@integration_only()
def test_team_stats_and_chain_verify_on_empty_tenant(client):
    t = _resolve(client, f"org_it_{uuid.uuid4().hex[:10]}")
    s = client.get(f"/admin/teams/{t['team_id']}/stats").json()
    assert s["requests"] == 0 and s["entities_by_type"] == {}
    v = client.get(f"/admin/teams/{t['team_id']}/audit/verify").json()
    assert v == {"ok": True, "events": 0, "checked_at": v["checked_at"]}


@integration_only()
def test_playground_runs_pipeline_and_caps(client, monkeypatch):
    from app import deps
    from tests.test_proxy_e2e import SIN, FakeProvider

    fake = FakeProvider(settings)
    client.app.dependency_overrides  # noqa: B018 - app built per test
    monkeypatch.setattr(deps, "get_active_provider", lambda: fake)
    monkeypatch.setattr(settings, "playground_daily_limit", 2)
    t = _resolve(client, f"org_it_{uuid.uuid4().hex[:10]}")
    body = {"team_id": t["team_id"], "prompt": f"Client SIN {SIN}"}
    r = client.post("/admin/playground", json=body)
    assert r.status_code == 200, r.text
    rg = r.json()["redactgate"]
    assert SIN not in rg["upstream_messages"][-1]["content"]
    assert SIN in r.json()["choices"][0]["message"]["content"]
    assert r.headers["X-Playground-Remaining"] == "1"
    assert client.post("/admin/playground", json=body).status_code == 200
    assert client.post("/admin/playground", json=body).status_code == 429
    s = client.get(f"/admin/teams/{t['team_id']}/stats").json()
    assert s["requests"] == 2 and s["entities_by_type"].get("SIN") == 2
    assert client.get(f"/admin/teams/{t['team_id']}/audit/verify").json()["ok"] is True
