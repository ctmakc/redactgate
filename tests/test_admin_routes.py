"""Admin API routes via TestClient with the DB session replaced by a scripted fake.

No Postgres: ``get_session`` is overridden with a fake whose ``execute`` returns queued
results in call order, so we test the gate, validation and JSON shaping of each route.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_session
from app.routes.admin import router

TOKEN = "admin-test-token"


class _Result:
    def __init__(self, value):
        self._v = value

    def scalar_one(self):
        return self._v

    def scalar_one_or_none(self):
        return self._v

    def all(self):
        return list(self._v)

    def scalars(self):
        return self

    # .scalars().all() reuses all()


class FakeSession:
    def __init__(self, results=()):
        self.results = list(results)
        self.statements: list = []
        self.added: list = []
        self.commits = 0

    async def execute(self, stmt, *a, **kw):
        self.statements.append(stmt)
        return _Result(self.results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.UUID(int=7)


@pytest.fixture
def admin_token(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", TOKEN)
    monkeypatch.setattr(settings, "environment", "test")


def _client(session: FakeSession) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    return TestClient(app)


H = {"X-Admin-Token": TOKEN}


# ── gate ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/admin/stats", "/admin/audit", "/admin/policies", "/admin/benchmark"])
def test_routes_reject_missing_or_wrong_token(admin_token, path):
    c = _client(FakeSession())
    assert c.get(path).status_code == 401
    assert c.get(path, headers={"X-Admin-Token": "nope"}).status_code == 401


def test_routes_fail_closed_without_token_outside_dev(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "")
    monkeypatch.setattr(settings, "environment", "test")
    assert _client(FakeSession()).get("/admin/policies").status_code == 503


def test_create_policy_gate_runs_before_body_handling(admin_token):
    s = FakeSession()
    r = _client(s).post("/admin/policies", json={"org_id": "o", "name": "n"})
    assert r.status_code == 401
    assert s.added == []


# ── /stats ────────────────────────────────────────────────────────────────────────


def test_stats_aggregates_counts(admin_token):
    s = FakeSession(
        [
            10,  # total requests
            3,  # blocked
            [("openai", 7), ("ollama", 3)],  # by provider
            [{"SIN": 2, "EMAIL": 1}, {"SIN": "3"}, None, {"BAD": "x"}, "garbage"],
            4,  # sessions
            Decimal("12.6"),  # median latency
        ]
    )
    r = _client(s).get("/admin/stats", headers=H)
    assert r.status_code == 200
    assert r.json() == {
        "requests": 10,
        "blocked": 3,
        "sessions": 4,
        "median_latency_ms": 13,
        "entities_by_type": {"SIN": 5, "EMAIL": 1},
        "by_provider": {"openai": 7, "ollama": 3},
    }


def test_stats_empty_db(admin_token):
    s = FakeSession([0, 0, [], [], 0, None])
    body = _client(s).get("/admin/stats", headers=H).json()
    assert body["median_latency_ms"] is None
    assert body["entities_by_type"] == {} and body["by_provider"] == {}


# ── /audit ────────────────────────────────────────────────────────────────────────


def _event(i, **over):
    base = dict(
        id=i,
        team_id=uuid.UUID(int=1),
        session_id=None,
        route="/v1/chat/completions",
        provider="openai",
        entity_counts={"SIN": 1},
        blocked=False,
        prompt_tokens=5,
        completion_tokens=2,
        latency_ms=30,
        event_hash="h" * 64,
        created_at=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC),
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_audit_list_shapes_items(admin_token):
    sid = uuid.UUID(int=99)
    s = FakeSession([2, [_event(2, session_id=sid), _event(1, created_at=None)]])
    r = _client(s).get("/admin/audit?limit=2&offset=0", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["limit"] == 2 and body["offset"] == 0
    first, second = body["items"]
    assert first["session_id"] == str(sid)
    assert first["team_id"] == str(uuid.UUID(int=1))
    assert first["created_at"] == "2026-01-02T03:04:05+00:00"
    assert second["session_id"] is None and second["created_at"] is None
    # counts only — no raw text fields leak through
    assert set(first) == {
        "id", "team_id", "session_id", "route", "provider", "entity_counts", "blocked",
        "prompt_tokens", "completion_tokens", "latency_ms", "event_hash", "created_at",
    }


def test_audit_team_filter_applies_to_both_queries(admin_token):
    s = FakeSession([0, []])
    _client(s).get("/admin/audit?team=t-1", headers=H)
    compiled = [str(st) for st in s.statements]
    assert all("team_id" in sql for sql in compiled)


@pytest.mark.parametrize("qs", ["limit=0", "limit=501", "offset=-1", "limit=abc"])
def test_audit_pagination_validation(admin_token, qs):
    s = FakeSession()
    assert _client(s).get(f"/admin/audit?{qs}", headers=H).status_code == 422
    assert s.statements == []


# ── /policies ─────────────────────────────────────────────────────────────────────


def test_list_policies_serializes(admin_token):
    p = SimpleNamespace(
        id=uuid.UUID(int=5), org_id=uuid.UUID(int=6), name="Strict", mode="hard_block",
        pack_ids=[uuid.UUID(int=8)], blocked_types=None, allowed_providers=["openai"],
        created_at=None,
    )
    body = _client(FakeSession([[p]])).get("/admin/policies", headers=H).json()
    assert body == {
        "items": [
            {
                "id": str(uuid.UUID(int=5)),
                "org_id": str(uuid.UUID(int=6)),
                "name": "Strict",
                "mode": "hard_block",
                "pack_ids": [str(uuid.UUID(int=8))],
                "blocked_types": [],
                "allowed_providers": ["openai"],
                "created_at": None,
            }
        ]
    }


@pytest.mark.parametrize("body", [{}, {"org_id": "o"}, {"name": "n"}, {"org_id": "", "name": "n"}])
def test_create_policy_requires_org_and_name(admin_token, body):
    s = FakeSession()
    r = _client(s).post("/admin/policies", json=body, headers=H)
    assert r.status_code == 400
    assert s.added == [] and s.commits == 0


def test_create_policy_defaults_and_persists(admin_token):
    s = FakeSession()
    org = str(uuid.UUID(int=3))
    r = _client(s).post("/admin/policies", json={"org_id": org, "name": "Default"}, headers=H)
    assert r.status_code == 201
    out = r.json()
    assert out["mode"] == "tokenize"
    assert out["pack_ids"] == [] and out["blocked_types"] == [] and out["allowed_providers"] == []
    assert out["id"] == str(uuid.UUID(int=7))
    assert s.commits == 1 and len(s.added) == 1


def test_create_policy_passes_lists_through(admin_token):
    s = FakeSession()
    r = _client(s).post(
        "/admin/policies",
        json={"org_id": "o", "name": "n", "mode": "mask",
              "blocked_types": ["SIN", "IBAN"], "allowed_providers": ["ollama"]},
        headers=H,
    )
    assert r.status_code == 201
    pol = s.added[0]
    assert pol.mode == "mask"
    assert pol.blocked_types == ["SIN", "IBAN"]
    assert pol.allowed_providers == ["ollama"]


@pytest.mark.skip(
    reason="bug: POST /admin/policies does not validate list fields — a string "
    "blocked_types/allowed_providers is split into single characters by list()"
)
def test_create_policy_rejects_string_instead_of_list(admin_token):
    s = FakeSession()
    r = _client(s).post(
        "/admin/policies",
        json={"org_id": "o", "name": "n", "blocked_types": "SIN", "allowed_providers": "openai"},
        headers=H,
    )
    assert r.status_code in (400, 422)
    assert s.added == []


@pytest.mark.skip(
    reason="bug: POST /admin/policies accepts any mode string; only the DB CHECK rejects "
    "it later, so the client gets a 500 instead of a 400/422"
)
def test_create_policy_rejects_unknown_mode(admin_token):
    s = FakeSession()
    r = _client(s).post("/admin/policies", json={"org_id": "o", "name": "n", "mode": "yolo"},
                        headers=H)
    assert r.status_code in (400, 422)
    assert s.added == []


# ── /benchmark ────────────────────────────────────────────────────────────────────


def test_benchmark_floats_and_nulls(admin_token):
    r1 = SimpleNamespace(
        id=uuid.UUID(int=1), pack_id=None, provider="openai", golden_set="ca",
        recall=Decimal("0.9123"), precision=None, answer_fidelity=Decimal("1.0000"),
        created_at=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
    )
    body = _client(FakeSession([[r1]])).get("/admin/benchmark?limit=1", headers=H).json()
    item = body["items"][0]
    assert item["pack_id"] is None
    assert item["recall"] == pytest.approx(0.9123)
    assert item["precision"] is None
    assert item["answer_fidelity"] == 1.0
    assert item["created_at"].startswith("2026-05-01")


def test_benchmark_limit_bounds(admin_token):
    assert _client(FakeSession()).get("/admin/benchmark?limit=201", headers=H).status_code == 422
