"""Admin / dashboard API (JSON only — consumed by the Next.js UI).

Every route is gated by the ``X-Admin-Token`` header when ``settings.admin_token`` is set;
when it is empty (dev), access is open. Queries are deliberately simple and async.

SECURITY: these endpoints surface entity *type counts* and aggregate stats only — never a
raw entity value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from sqlalchemy import func, select

from app.config import settings
from app.db import get_session
from app.models import AuditEvent, EvalRun, Policy, RedactionSession

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin")


async def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    """Gate admin routes on the configured token.

    Fail-closed when no token is configured: only the ``dev`` environment leaves the admin
    API open. In ``test``/``prod`` an unset ``admin_token`` returns 503 rather than silently
    granting access (prod is additionally blocked from booting by ``runtime_problems``)."""
    if not settings.admin_token:
        if settings.environment == "dev":
            return
        raise HTTPException(status_code=503, detail="admin API not configured (set ADMIN_TOKEN)")
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="invalid or missing admin token")


# Module-level dependency singletons (FastAPI `Depends`/`Body` idiom; ruff B008-clean).
_AdminDep = Depends(require_admin)
_SessionDep = Depends(get_session)
_BodyDep = Body(...)


@router.get("/stats")
async def stats(
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
) -> dict[str, Any]:
    """Aggregate totals: request count, entities redacted by type, blocked count, by provider."""
    total_requests = (
        await session.execute(select(func.count()).select_from(AuditEvent))
    ).scalar_one()

    blocked_count = (
        await session.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.blocked.is_(True))
        )
    ).scalar_one()

    by_provider_rows = (
        await session.execute(
            select(AuditEvent.provider, func.count()).group_by(AuditEvent.provider)
        )
    ).all()
    by_provider = {provider: count for provider, count in by_provider_rows}

    # Entities redacted by type — summed across every audit event's counts blob.
    entities_by_type: dict[str, int] = {}
    counts_rows = (await session.execute(select(AuditEvent.entity_counts))).scalars().all()
    for counts in counts_rows:
        if isinstance(counts, dict):
            for etype, n in counts.items():
                try:
                    entities_by_type[etype] = entities_by_type.get(etype, 0) + int(n)
                except (TypeError, ValueError):
                    continue

    sessions = (
        await session.execute(select(func.count()).select_from(RedactionSession))
    ).scalar_one()
    median_latency_ms = (
        await session.execute(
            select(func.percentile_cont(0.5).within_group(AuditEvent.latency_ms.asc())).where(
                AuditEvent.latency_ms.is_not(None)
            )
        )
    ).scalar_one_or_none()

    return {
        "requests": int(total_requests),
        "blocked": int(blocked_count),
        "sessions": int(sessions),
        "median_latency_ms": (round(float(median_latency_ms)) if median_latency_ms is not None else None),
        "entities_by_type": entities_by_type,
        "by_provider": by_provider,
    }


@router.get("/audit")
async def audit(
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    team: str | None = Query(None),
) -> dict[str, Any]:
    """Paginated audit list (counts only — never raw values)."""
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc())
    count_stmt = select(func.count()).select_from(AuditEvent)
    if team:
        stmt = stmt.where(AuditEvent.team_id == team)
        count_stmt = count_stmt.where(AuditEvent.team_id == team)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()

    items = [
        {
            "id": ev.id,
            "team_id": str(ev.team_id),
            "session_id": str(ev.session_id) if ev.session_id else None,
            "route": ev.route,
            "provider": ev.provider,
            "entity_counts": ev.entity_counts,
            "blocked": ev.blocked,
            "prompt_tokens": ev.prompt_tokens,
            "completion_tokens": ev.completion_tokens,
            "latency_ms": ev.latency_ms,
            "event_hash": ev.event_hash,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        }
        for ev in rows
    ]
    return {"total": int(total), "limit": limit, "offset": offset, "items": items}


@router.get("/policies")
async def list_policies(
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
) -> dict[str, Any]:
    rows = (
        await session.execute(select(Policy).order_by(Policy.created_at.desc()))
    ).scalars().all()
    items = [
        {
            "id": str(p.id),
            "org_id": str(p.org_id),
            "name": p.name,
            "mode": p.mode,
            "pack_ids": [str(pid) for pid in (p.pack_ids or [])],
            "blocked_types": list(p.blocked_types or []),
            "allowed_providers": list(p.allowed_providers or []),
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in rows
    ]
    return {"items": items}


@router.post("/policies", status_code=201)
async def create_policy(
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
    body: dict[str, Any] = _BodyDep,
) -> dict[str, Any]:
    """Create a policy. Expects ``org_id``, ``name`` and optional mode/packs/blocked/allowed."""
    org_id = body.get("org_id")
    name = body.get("name")
    if not org_id or not name:
        raise HTTPException(status_code=400, detail="org_id and name are required")

    policy = Policy(
        org_id=org_id,
        name=name,
        mode=body.get("mode", "tokenize"),
        pack_ids=list(body.get("pack_ids") or []),
        blocked_types=list(body.get("blocked_types") or []),
        allowed_providers=list(body.get("allowed_providers") or []),
    )
    session.add(policy)
    await session.commit()
    await session.refresh(policy)
    return {
        "id": str(policy.id),
        "org_id": str(policy.org_id),
        "name": policy.name,
        "mode": policy.mode,
        "pack_ids": [str(pid) for pid in (policy.pack_ids or [])],
        "blocked_types": list(policy.blocked_types or []),
        "allowed_providers": list(policy.allowed_providers or []),
    }


@router.get("/benchmark")
async def benchmark(
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """Latest eval_run rows (fidelity / recall / precision benchmark results)."""
    rows = (
        await session.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit))
    ).scalars().all()
    items = [
        {
            "id": str(r.id),
            "pack_id": str(r.pack_id) if r.pack_id else None,
            "provider": r.provider,
            "golden_set": r.golden_set,
            "recall": float(r.recall) if r.recall is not None else None,
            "precision": float(r.precision) if r.precision is not None else None,
            "answer_fidelity": float(r.answer_fidelity) if r.answer_fidelity is not None else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
    return {"items": items}
