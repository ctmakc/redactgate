"""Policy engine — turns a stored ``policy`` row into a runtime :class:`PolicyDecision`
and applies it to a set of detected entity types / spans.

The decision is the bridge between detection and the vault: it decides whether the call
is hard-blocked, which entity types to redact, and which providers are allowed. All of
these helpers are *pure* with respect to entity values — they only ever see entity
*types*, never raw values, so nothing here can leak sensitive data.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.schemas.entities import EntitySpan, PolicyDecision, PolicyMode
from app.schemas.openai import HardBlockError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_policy(session: AsyncSession, policy_id: str | None) -> PolicyDecision:
    """Load a policy row and build its :class:`PolicyDecision`.

    If ``policy_id`` is ``None`` (or the row is missing) a default *tokenize-everything*
    decision is returned so the proxy fails safe (redact rather than pass through).
    """
    if policy_id is None:
        return _default_decision()

    # Imported lazily so the regex-only / DB-less path never imports SQLAlchemy models.
    from sqlalchemy import select

    from app.models import Policy

    row = (
        await session.execute(select(Policy).where(Policy.id == policy_id))
    ).scalar_one_or_none()
    if row is None:
        return _default_decision()

    return PolicyDecision(
        mode=PolicyMode(row.mode),
        blocked=False,
        blocked_types=list(row.blocked_types or []),
        allowed_providers=list(row.allowed_providers or []),
        redact_types=None,
    )


def _default_decision() -> PolicyDecision:
    """The fail-safe default: tokenize everything, block nothing, allow any provider."""
    return PolicyDecision(
        mode=PolicyMode.TOKENIZE,
        blocked=False,
        blocked_types=[],
        allowed_providers=[],
        redact_types=None,
    )


def evaluate(decision: PolicyDecision, detected_types: set[str]) -> None:
    """Enforce the hard-block dimension of a policy.

    Raises :class:`HardBlockError` when the policy mode is ``HARD_BLOCK`` or when any
    detected entity type intersects the policy's ``blocked_types``. The raised list is the
    sorted intersection (the offending types), falling back to all detected types when the
    mode itself forced the block but no specific type matched.
    """
    blocked_hit = detected_types & set(decision.blocked_types)
    if decision.mode == PolicyMode.HARD_BLOCK or blocked_hit:
        offending = sorted(blocked_hit) if blocked_hit else sorted(detected_types)
        raise HardBlockError(offending)


def filter_spans(
    decision: PolicyDecision, spans: Iterable[EntitySpan]
) -> list[EntitySpan]:
    """Keep only spans whose entity type the decision says to redact."""
    return [span for span in spans if decision.should_redact(span.entity_type)]


def provider_allowed(decision: PolicyDecision, provider: str) -> bool:
    """True if the provider is permitted (empty allow-list ⇒ any provider allowed)."""
    return not decision.allowed_providers or provider in decision.allowed_providers
