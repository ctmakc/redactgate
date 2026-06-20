"""Unit tests for API-key authentication (``app.auth``).

DB-less surface:
  * ``hash_key`` / ``verify_key`` round-trip + negative cases
  * ``make_key`` prefix + uniqueness
  * ``authenticate`` single-tenant path (``require_api_key=False`` -> default team, no header)
  * ``authenticate`` bearer parsing / 401 behaviour, exercised with a fake async session so
    no Postgres is needed.

The genuinely DB-backed ``ensure_default_api_key`` is exercised under ``@integration_only``.
"""

from __future__ import annotations

import uuid

import pytest

from app.auth import (
    DEFAULT_POLICY_ID,
    DEFAULT_TEAM_ID,
    AuthContext,
    authenticate,
    hash_key,
    make_key,
    verify_key,
)
from app.gateway.base import ProviderError
from tests.conftest import integration_only

# ── make_key ────────────────────────────────────────────────────────────────────


def test_make_key_has_rg_prefix():
    key = make_key()
    assert key.startswith("rg-")
    assert len(key) > len("rg-")


def test_make_key_is_unique():
    keys = {make_key() for _ in range(200)}
    assert len(keys) == 200


def test_make_key_body_is_urlsafe():
    body = make_key()[len("rg-"):]
    # urlsafe base64 alphabet (token_urlsafe): letters, digits, '-', '_'
    assert all(c.isalnum() or c in "-_" for c in body)


# ── hash_key / verify_key round-trip ────────────────────────────────────────────


def test_hash_key_roundtrip():
    raw = make_key()
    hashed = hash_key(raw)
    assert hashed != raw  # never store cleartext
    assert verify_key(hashed, raw) is True


def test_hash_key_is_argon2_format():
    hashed = hash_key("rg-secret")
    assert hashed.startswith("$argon2")


def test_verify_key_wrong_key_fails():
    hashed = hash_key("rg-correct-key")
    assert verify_key(hashed, "rg-wrong-key") is False


def test_verify_key_empty_raw_fails():
    hashed = hash_key("rg-something")
    assert verify_key(hashed, "") is False


def test_verify_key_garbage_hash_returns_false_not_raises():
    # A malformed hash string must be handled gracefully (no exception).
    assert verify_key("not-a-real-argon2-hash", "rg-key") is False
    assert verify_key("", "rg-key") is False


def test_hash_key_uses_random_salt():
    raw = make_key()
    h1 = hash_key(raw)
    h2 = hash_key(raw)
    # Per-row random salt -> different encodings, both verify.
    assert h1 != h2
    assert verify_key(h1, raw) is True
    assert verify_key(h2, raw) is True


# ── authenticate: single-tenant (require_api_key=False) ─────────────────────────


async def test_authenticate_no_api_key_required_returns_default_team(monkeypatch):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", False)
    ctx = await authenticate(None, session=None)  # no header, no DB needed
    assert isinstance(ctx, AuthContext)
    assert ctx.team_id == DEFAULT_TEAM_ID
    assert ctx.policy_id == DEFAULT_POLICY_ID
    assert ctx.api_key_id is None


async def test_authenticate_disabled_ignores_provided_header(monkeypatch):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", False)
    # Even with a bogus header, single-tenant mode returns default tenant.
    ctx = await authenticate("Bearer rg-whatever", session=None)
    assert ctx.team_id == DEFAULT_TEAM_ID
    assert ctx.api_key_id is None


# ── authenticate: require_api_key=True (fake session, no Postgres) ──────────────


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _FakeTeam:
    def __init__(self, team_id, default_policy_id):
        self.id = team_id
        self.default_policy_id = default_policy_id


class _FakeApiKey:
    def __init__(self, *, id, team_id, key_hash, revoked_at=None):
        self.id = id
        self.team_id = team_id
        self.key_hash = key_hash
        self.revoked_at = revoked_at


class _FakeSession:
    """Minimal async session: returns the configured non-revoked keys and team."""

    def __init__(self, *, api_keys, team):
        self._api_keys = api_keys
        self._team = team

    async def execute(self, _stmt):
        # authenticate selects non-revoked api_key rows.
        return _FakeResult([k for k in self._api_keys if k.revoked_at is None])

    async def get(self, _model, _pk):
        return self._team


async def test_authenticate_valid_key_resolves_team_and_policy(monkeypatch):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)

    raw = make_key()
    team_id = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
    policy_id = uuid.UUID("00000000-0000-0000-0000-0000000000cc")
    key_id = uuid.uuid4()
    key = _FakeApiKey(id=key_id, team_id=team_id, key_hash=hash_key(raw))
    session = _FakeSession(api_keys=[key], team=_FakeTeam(team_id, policy_id))

    ctx = await authenticate(f"Bearer {raw}", session)
    assert ctx.team_id == str(team_id)
    assert ctx.policy_id == str(policy_id)
    assert ctx.api_key_id == str(key_id)


async def test_authenticate_missing_header_raises_401(monkeypatch):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)
    session = _FakeSession(api_keys=[], team=None)
    with pytest.raises(ProviderError) as exc:
        await authenticate(None, session)
    assert exc.value.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "rg-no-bearer-scheme",          # missing scheme
        "Basic rg-some-key",            # wrong scheme
        "Bearer",                       # no token
        "Bearer    ",                   # whitespace-only token
        "",                             # empty header
    ],
)
async def test_authenticate_malformed_header_raises_401(monkeypatch, header):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)
    session = _FakeSession(api_keys=[], team=None)
    with pytest.raises(ProviderError) as exc:
        await authenticate(header, session)
    assert exc.value.status_code == 401


async def test_authenticate_unknown_key_raises_401(monkeypatch):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)
    team_id = uuid.uuid4()
    stored = _FakeApiKey(id=uuid.uuid4(), team_id=team_id, key_hash=hash_key(make_key()))
    session = _FakeSession(api_keys=[stored], team=_FakeTeam(team_id, None))
    with pytest.raises(ProviderError) as exc:
        await authenticate(f"Bearer {make_key()}", session)  # a different, unknown key
    assert exc.value.status_code == 401


async def test_authenticate_bearer_scheme_is_case_insensitive(monkeypatch):
    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)
    raw = make_key()
    team_id = uuid.uuid4()
    key = _FakeApiKey(id=uuid.uuid4(), team_id=team_id, key_hash=hash_key(raw))
    session = _FakeSession(api_keys=[key], team=_FakeTeam(team_id, None))
    ctx = await authenticate(f"bearer {raw}", session)  # lowercase scheme
    assert ctx.team_id == str(team_id)
    # team has no default policy -> policy_id is None
    assert ctx.policy_id is None


async def test_authenticate_revoked_key_is_ignored(monkeypatch):
    import datetime as dt

    import app.auth as auth_mod

    monkeypatch.setattr(auth_mod.settings, "require_api_key", True)
    raw = make_key()
    team_id = uuid.uuid4()
    revoked = _FakeApiKey(
        id=uuid.uuid4(),
        team_id=team_id,
        key_hash=hash_key(raw),
        revoked_at=dt.datetime(2020, 1, 1),
    )
    # _FakeSession filters out revoked rows (mirrors the WHERE revoked_at IS NULL clause).
    session = _FakeSession(api_keys=[revoked], team=_FakeTeam(team_id, None))
    with pytest.raises(ProviderError) as exc:
        await authenticate(f"Bearer {raw}", session)
    assert exc.value.status_code == 401


# ── ensure_default_api_key (DB) ─────────────────────────────────────────────────


@integration_only()
async def test_ensure_default_api_key_db():  # pragma: no cover - needs Postgres
    from app.auth import ensure_default_api_key
    from app.db import session_scope

    async with session_scope() as session:
        raw = await ensure_default_api_key(session)
        if raw is not None:
            assert raw.startswith("rg-")
    async with session_scope() as session:
        # Idempotent: a second call returns None (key already exists).
        assert await ensure_default_api_key(session) is None
