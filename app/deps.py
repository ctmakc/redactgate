"""FastAPI dependency providers for the request pipeline.

These are intentionally thin so tests can override them with in-memory variants via
``app.dependency_overrides``. Heavy modules (vault, pg_store, audit, auth, policy) are
imported *lazily* inside the dependency functions so this module imports cleanly even
while sibling modules are still being written, and so a unit-test lane that overrides a
dependency never triggers an unwanted import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Depends, Request

from app.config import Settings, settings
from app.db import get_session

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.auth import AuthContext
    from app.gateway.base import Provider
    from app.redaction.store import TokenStore
    from app.redaction.vault import Vault
    from app.schemas.entities import PolicyDecision


# ── Audit sink ──────────────────────────────────────────────────────────────────


class DBAuditSink:
    """Adapter over ``app.audit.record_event`` so the pipeline/route depends on a small,
    overridable interface rather than the module function directly.

    SECURITY: only entity *type counts* (plus route/provider/usage/latency) are recorded —
    never a raw entity value.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        team_id: str | None,
        api_key_id: str | None,
        session_id: str | None,
        route: str,
        provider: str,
        entity_counts: dict[str, int],
        blocked: bool,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        latency_ms: int | None = None,
    ) -> Any:
        from app.audit import record_event

        return await record_event(
            self._session,
            team_id=team_id,
            api_key_id=api_key_id,
            session_id=session_id,
            route=route,
            provider=provider,
            entity_counts=entity_counts,
            blocked=blocked,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )


# ── Request-scoped dependencies ──────────────────────────────────────────────────

# Module-level dependency singletons. Using these as parameter defaults keeps the FastAPI
# `Depends(...)` idiom while satisfying ruff B008 ("read the default from a module-level
# singleton variable").
_SessionDep = Depends(get_session)


async def get_token_store(
    session: AsyncSession = _SessionDep,
) -> AsyncIterator[TokenStore]:
    """Yield a request-scoped Postgres-backed token store."""
    from app.redaction.pg_store import PostgresTokenStore

    yield PostgresTokenStore(session)


async def get_audit_sink(
    session: AsyncSession = _SessionDep,
) -> AsyncIterator[DBAuditSink]:
    """Yield a request-scoped audit sink (counts only)."""
    yield DBAuditSink(session)


async def get_auth(
    request: Request,
    session: AsyncSession = _SessionDep,
) -> AuthContext:
    """Authenticate the bearer token on the request and return the auth context."""
    from app.auth import authenticate

    return await authenticate(request.headers.get("authorization"), session)


_AuthDep = Depends(get_auth)


async def get_policy_decision(
    auth: AuthContext = _AuthDep,
    session: AsyncSession = _SessionDep,
) -> PolicyDecision:
    """Resolve the effective policy for the authenticated team."""
    from app.redaction.policy import resolve_policy

    return await resolve_policy(session, auth.policy_id)


def get_active_provider() -> Provider:
    """Return the configured active provider instance (cached by the gateway factory)."""
    import app.gateway  # noqa: F401  (self-registers adapters on import)
    from app.gateway.base import get_provider

    return get_provider(settings.ai_provider, settings)


# ── Non-request helpers (the store is request-scoped, so the vault is *built*) ──────


def build_vault(store: TokenStore, *, cfg: Settings = settings) -> Vault:
    """Construct a :class:`Vault` around a request-scoped store using the configured keys."""
    from app.redaction.vault import Vault

    return Vault(
        store,
        master_key=cfg.key_bytes("vault_master_key"),
        fingerprint_key=cfg.key_bytes("fingerprint_hmac_key"),
    )


def get_detector(cfg: Settings = settings) -> Any:
    """Build the multi-pass detector (regex-only with zero optional deps).

    ``Detector`` reads configuration from ``app.config`` internally and takes no
    constructor args; ``cfg`` is accepted for DI/override symmetry but not passed.
    """
    from app.redaction.detect import Detector

    return Detector()
