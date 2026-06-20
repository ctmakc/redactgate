"""API-key authentication (module §7).

Resolves an inbound ``Authorization: Bearer <key>`` header into an ``AuthContext``
(team + default policy + api-key id). Keys are stored only as argon2 hashes; the raw
key is shown exactly once at creation time. When ``settings.require_api_key`` is False
the proxy runs single-tenant against the seeded default team/policy.

SECURITY: this module never logs or persists a raw key. ``ensure_default_api_key``
*returns* the raw dev key to its caller (the lifespan) so the operator can copy it from
logs — it is never written to the database in cleartext.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.base import ProviderError
from app.models import ApiKey, Team

# Seeded deterministic tenant (see migrations/002_seed.sql).
DEFAULT_TEAM_ID = "00000000-0000-0000-0000-0000000000bb"
DEFAULT_POLICY_ID = "00000000-0000-0000-0000-0000000000cc"

_KEY_PREFIX = "rg-"
_ph = PasswordHasher()


@dataclass(slots=True)
class AuthContext:
    team_id: str
    policy_id: str | None
    api_key_id: str | None


def make_key() -> str:
    """Mint a fresh raw API key (``rg-<urlsafe-token>``)."""
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    """Return an argon2 hash of a raw key. Hashes are per-row (random salt)."""
    return _ph.hash(raw)


def verify_key(hashed: str, raw: str) -> bool:
    """Verify a raw key against an argon2 hash; False on any mismatch/parse error."""
    try:
        return _ph.verify(hashed, raw)
    except (Argon2Error, ValueError, TypeError):
        return False


def _parse_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def authenticate(authorization: str | None, session: AsyncSession) -> AuthContext:
    """Resolve an Authorization header into an ``AuthContext``.

    When ``require_api_key`` is False, returns the seeded default tenant without touching
    the DB. Otherwise parses the bearer token and argon2-verifies it against every
    non-revoked ``api_key`` row. Raises ``ProviderError(401)`` on any failure.
    """
    if not settings.require_api_key:
        return AuthContext(DEFAULT_TEAM_ID, DEFAULT_POLICY_ID, None)

    raw = _parse_bearer(authorization)
    if raw is None:
        raise ProviderError("invalid api key", status_code=401)

    rows = (
        await session.execute(select(ApiKey).where(ApiKey.revoked_at.is_(None)))
    ).scalars().all()
    for row in rows:
        if verify_key(row.key_hash, raw):
            team = await session.get(Team, row.team_id)
            policy_id = str(team.default_policy_id) if team and team.default_policy_id else None
            return AuthContext(str(row.team_id), policy_id, str(row.id))

    raise ProviderError("invalid api key", status_code=401)


async def ensure_default_api_key(session: AsyncSession) -> str | None:
    """Idempotently create a dev API key for the seeded default team.

    Returns the RAW key exactly once (so the lifespan can surface it in logs) when a key
    was created; returns ``None`` if the default team already has at least one key. Only
    the argon2 hash is persisted.
    """
    existing = (
        await session.execute(
            select(ApiKey.id).where(ApiKey.team_id == DEFAULT_TEAM_ID).limit(1)
        )
    ).first()
    if existing is not None:
        return None

    raw = make_key()
    session.add(ApiKey(team_id=DEFAULT_TEAM_ID, key_hash=hash_key(raw), label="default-dev"))
    await session.commit()
    return raw
