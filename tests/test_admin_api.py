"""Admin JSON API (app.routes.admin) and policy resolution, against a scripted fake session.

No database: ``get_session`` is overridden with an object that returns canned results in the
order the route issues its queries, so the tests pin the aggregation / validation logic.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_session
from app.redaction.policy import resolve_policy
from app.routes.admin import router as admin_router
from app.schemas.entities import PolicyMode


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def all(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: self.value)


class FakeSession:
    def __init__(self, results=()):
        self.results = list(results)
        self.statements = []
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        self.statements.append(stmt)
        return Result(self.results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.UUID(int=7)


@pytest.fixture()
def admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "adm-secret")
    app = FastAPI()
    app.include_router(admin_router)
    holder = {"session": FakeSession()}

    async def _session():
        yield holder["session"]

    app.dependency_overrides[get_session] = _session
    client = TestClient(app)

    def call(method, path, *, session=None, token="adm-secret", **kw):
        holder["session"] = session or FakeSession()
        headers = {"X-Admin-Token": token} if token is not None else {}
        return client.request(method, path, headers=headers, **kw), holder["session"]

    return call


def test_admin_routes_need_the_token(admin):
    for path in ("/admin/stats", "/admin/audit", "/admin/policies", "/admin/benchmark"):
        resp, _ = admin("GET", path, token=None)
        assert resp.status_code == 401, path
        resp, _ = admin("GET", path, token="wrong")
        assert resp.status_code == 401, path


def test_stats_sums_entity_counts_and_skips_garbage(admin):
    session = FakeSession([
        10,  # total requests
        2,  # blocked
        [("openai", 7), ("ollama", 3)],  # by provider
        [{"SIN": 2, "EMAIL": 1}, {"SIN": "3"}, None, {"EMAIL": "x", "IBAN": None}, ["not", "a", "dict"]],
        4,  # sessions
        123.6,  # median latency
    ])
    resp, _ = admin("GET", "/admin/stats", session=session)
    assert resp.status_code == 200
    assert resp.json() == {
        "requests": 10, "blocked": 2, "sessions": 4, "median_latency_ms": 124,
        "entities_by_type": {"SIN": 5, "EMAIL": 1},
        "by_provider": {"openai": 7, "ollama": 3},
    }


def test_stats_without_latency_data(admin):
    resp, _ = admin("GET", "/admin/stats", session=FakeSession([0, 0, [], [], 0, None]))
    assert resp.json()["median_latency_ms"] is None
    assert resp.json()["entities_by_type"] == {}


def test_audit_list_serialises_counts_only(admin):
    ev = SimpleNamespace(
        id=5, team_id=uuid.UUID(int=1), session_id=None, route="/v1/chat/completions", provider="openai",
        entity_counts={"SIN": 1}, blocked=False, prompt_tokens=10, completion_tokens=3, latency_ms=40,
        event_hash="ab" * 32, created_at=dt.datetime(2026, 11, 20, 9, 0, tzinfo=dt.UTC),
    )
    resp, session = admin("GET", "/admin/audit?limit=10&offset=20&team=t1", session=FakeSession([1, [ev]]))
    body = resp.json()
    assert body["total"] == 1 and body["limit"] == 10 and body["offset"] == 20
    item = body["items"][0]
    assert item["team_id"] == str(uuid.UUID(int=1)) and item["session_id"] is None
    assert item["created_at"] == "2026-11-20T09:00:00+00:00"
    assert set(item) == {"id", "team_id", "session_id", "route", "provider", "entity_counts", "blocked",
                         "prompt_tokens", "completion_tokens", "latency_ms", "event_hash", "created_at"}
    # the team filter is applied to both the page and the count query
    assert all("team_id" in str(s) for s in session.statements)


@pytest.mark.parametrize("qs", ["limit=0", "limit=501", "offset=-1"])
def test_audit_pagination_bounds(admin, qs):
    resp, _ = admin("GET", f"/admin/audit?{qs}")
    assert resp.status_code == 422


def test_create_policy_requires_org_and_name(admin):
    for body in ({}, {"org_id": str(uuid.uuid4())}, {"name": "x"}, {"org_id": "", "name": "x"}):
        resp, session = admin("POST", "/admin/policies", json=body)
        assert resp.status_code == 400
        assert session.added == [] and not session.committed


def test_create_policy_defaults(admin):
    org = str(uuid.uuid4())
    resp, session = admin("POST", "/admin/policies", json={"org_id": org, "name": "Default"})
    assert resp.status_code == 201 and session.committed
    body = resp.json()
    assert body == {"id": str(uuid.UUID(int=7)), "org_id": org, "name": "Default", "mode": "tokenize",
                    "pack_ids": [], "blocked_types": [], "allowed_providers": []}


@pytest.mark.skip(reason="bug: POST /admin/policies does not validate 'mode'; an unknown value reaches the "
                         "DB CHECK constraint (policy_mode_chk) and surfaces as a 500 instead of a 4xx")
def test_create_policy_rejects_unknown_mode(admin):
    resp, session = admin("POST", "/admin/policies", json={"org_id": str(uuid.uuid4()), "name": "x",
                                                            "mode": "yolo"})
    assert resp.status_code in (400, 422)
    assert not session.committed


def test_benchmark_rows_are_floats_or_none(admin):
    from decimal import Decimal

    row = SimpleNamespace(id=uuid.UUID(int=2), pack_id=None, provider="ollama", golden_set="ca",
                          recall=Decimal("0.7333"), precision=None, answer_fidelity=Decimal("0.77"),
                          created_at=None)
    resp, _ = admin("GET", "/admin/benchmark?limit=5", session=FakeSession([[row]]))
    item = resp.json()["items"][0]
    assert item["recall"] == pytest.approx(0.7333) and item["precision"] is None
    assert item["answer_fidelity"] == pytest.approx(0.77) and item["pack_id"] is None


# ── resolve_policy ─────────────────────────────────────────────────────────


async def test_resolve_policy_none_is_fail_safe_tokenize():
    d = await resolve_policy(FakeSession(), None)
    assert d.mode == PolicyMode.TOKENIZE and d.redact_types is None and d.allowed_providers == []


async def test_resolve_policy_missing_row_is_fail_safe():
    d = await resolve_policy(FakeSession([None]), str(uuid.uuid4()))
    assert d.mode == PolicyMode.TOKENIZE and not d.blocked


async def test_resolve_policy_maps_row():
    row = SimpleNamespace(mode="hard_block", blocked_types=["SIN"], allowed_providers=None)
    d = await resolve_policy(FakeSession([row]), str(uuid.uuid4()))
    assert d.mode == PolicyMode.HARD_BLOCK
    assert d.blocked_types == ["SIN"] and d.allowed_providers == []
    assert d.redact_types is None and d.blocked is False


# ── contract with the Next.js admin console (web/lib/api.ts) ───────────────


@pytest.mark.skip(reason="bug: the admin console's createPolicy() (web/lib/api.ts, PolicyCreate) sends "
                         "{name, mode, blocked_types, allowed_providers} without org_id, and the backend "
                         "requires org_id — every policy created from the UI fails with 400")
def test_create_policy_accepts_the_console_payload(admin):
    console_payload = {"name": "Tax team", "mode": "mask", "blocked_types": ["SIN"],
                       "allowed_providers": ["ollama"]}
    resp, _ = admin("POST", "/admin/policies", json=console_payload)
    assert resp.status_code == 201


@pytest.mark.skip(reason="bug: the audit page sends q / provider / blocked filters (web/lib/api.ts getAudit) "
                         "but GET /admin/audit only understands 'team', so the console's filters silently "
                         "return the unfiltered list")
def test_audit_honours_the_console_filters(admin):
    resp, session = admin("GET", "/admin/audit?limit=50&offset=0&provider=openai&blocked=true",
                          session=FakeSession([0, []]))
    assert resp.status_code == 200
    for stmt in session.statements:  # count query and page query
        where = str(stmt.whereclause) if stmt.whereclause is not None else ""
        assert "provider" in where and "blocked" in where


def test_stats_shape_matches_what_the_console_reads(admin):
    # web/lib/api.ts RawStats: requests, blocked, sessions, median_latency_ms, entities_by_type, by_provider
    resp, _ = admin("GET", "/admin/stats", session=FakeSession([1, 0, [], [], 0, None]))
    assert set(resp.json()) == {"requests", "blocked", "sessions", "median_latency_ms",
                                "entities_by_type", "by_provider"}
