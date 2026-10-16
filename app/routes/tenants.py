"""Tenant-scoped admin API used by the Clerk-protected console (Build North, Oct 2026).

The Next.js console authenticates people with Clerk. Each Clerk *organization* (one firm)
maps to one RedactGate org + team. The console's server resolves the signed-in user's
active organization and role, then calls these endpoints with the server-only
``X-Admin-Token``. The browser never sees the token and never chooses a tenant id: the
console derives ``external_id`` from the verified Clerk session.

SECURITY: like the rest of ``/admin``, responses carry entity *type counts* and metadata
only. The playground returns the caller's own text plus what was sent upstream, and
persists nothing beyond the normal counts-only audit event.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import func, select

from app.config import settings
from app.db import get_session
from app.models import AuditEvent, Org, Policy, RedactionSession, Team
from app.routes.admin import require_admin

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin")

_AdminDep = Depends(require_admin)
_SessionDep = Depends(get_session)
_BodyDep = Body(...)

PLAYGROUND_ROUTE = "/admin/playground"
MAX_PROMPT_CHARS = 8_000

PLAYGROUND_SYSTEM = (
    "You assist staff at a Canadian professional-services firm (accounting, law or a "
    "clinic). Sensitive values in the user's text were replaced by placeholders such as "
    "[[SIN_1a2b3c]] or [[EMAIL_9f8e7d]] before the text reached you. Treat each "
    "placeholder as the real value: when the answer needs that value, copy the placeholder "
    "exactly as written, and never guess what is behind it. Answer concisely."
)


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid {what}") from exc


def _policy_json(p: Policy) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "org_id": str(p.org_id),
        "name": p.name,
        "mode": p.mode,
        "blocked_types": list(p.blocked_types or []),
        "allowed_providers": list(p.allowed_providers or []),
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


async def _team(session: AsyncSession, team_id: str) -> Team:
    team = await session.get(Team, _uuid(team_id, "team_id"))
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")
    return team


@router.post("/tenants/resolve")
async def resolve_tenant(
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
    body: dict[str, Any] = _BodyDep,
) -> dict[str, Any]:
    """Idempotently map an external (Clerk) organization id to an org + team + policy.

    First call creates the org, a default *tokenize* policy and one team; later calls
    return the same ids. The display name is refreshed when it changes.
    """
    external_id = str(body.get("external_id") or "").strip()
    name = str(body.get("name") or "").strip()[:120] or "Firm"
    if not external_id or len(external_id) > 128:
        raise HTTPException(status_code=400, detail="external_id is required")

    org = (
        await session.execute(select(Org).where(Org.external_id == external_id))
    ).scalar_one_or_none()
    if org is None:
        org = Org(name=name, external_id=external_id)
        session.add(org)
        await session.flush()
        policy = Policy(
            org_id=org.id,
            name="Default: tokenize everything",
            mode="tokenize",
            pack_ids=[],
            blocked_types=[],
            allowed_providers=[],
        )
        session.add(policy)
        await session.flush()
        team = Team(org_id=org.id, name=name, default_policy_id=policy.id)
        session.add(team)
        await session.commit()
    else:
        if org.name != name:
            org.name = name
        team = (
            await session.execute(
                select(Team).where(Team.org_id == org.id).order_by(Team.created_at).limit(1)
            )
        ).scalar_one()
        await session.commit()
    return {
        "org_id": str(org.id),
        "team_id": str(team.id),
        "policy_id": str(team.default_policy_id) if team.default_policy_id else None,
        "name": org.name,
    }


@router.get("/teams/{team_id}/policies")
async def team_policies(
    team_id: str,
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
) -> dict[str, Any]:
    """The team's org policies, with the one currently enforced for the team flagged."""
    team = await _team(session, team_id)
    rows = (
        await session.execute(
            select(Policy).where(Policy.org_id == team.org_id).order_by(Policy.created_at.desc())
        )
    ).scalars().all()
    return {
        "active_policy_id": str(team.default_policy_id) if team.default_policy_id else None,
        "items": [_policy_json(p) for p in rows],
    }


@router.post("/teams/{team_id}/policies", status_code=201)
async def create_team_policy(
    team_id: str,
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
    body: dict[str, Any] = _BodyDep,
) -> dict[str, Any]:
    """Create a policy in the team's org and (optionally) make it the enforced one."""
    team = await _team(session, team_id)
    name = str(body.get("name") or "").strip()[:120]
    mode = body.get("mode", "tokenize")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if mode not in ("tokenize", "mask", "hard_block"):
        raise HTTPException(status_code=400, detail="invalid mode")
    policy = Policy(
        org_id=team.org_id,
        name=name,
        mode=mode,
        pack_ids=[],
        blocked_types=[str(t).upper()[:40] for t in (body.get("blocked_types") or [])][:50],
        allowed_providers=[str(p)[:20] for p in (body.get("allowed_providers") or [])][:10],
    )
    session.add(policy)
    await session.flush()
    if body.get("activate", True):
        team.default_policy_id = policy.id
    await session.commit()
    await session.refresh(policy)
    out = _policy_json(policy)
    out["active"] = team.default_policy_id == policy.id
    return out


@router.put("/teams/{team_id}/policy")
async def activate_team_policy(
    team_id: str,
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
    body: dict[str, Any] = _BodyDep,
) -> dict[str, Any]:
    """Switch the policy enforced for this team. The policy must belong to the team's org."""
    team = await _team(session, team_id)
    policy = await session.get(Policy, _uuid(body.get("policy_id"), "policy_id"))
    if policy is None or policy.org_id != team.org_id:
        raise HTTPException(status_code=404, detail="policy not found for this team")
    team.default_policy_id = policy.id
    await session.commit()
    return {"team_id": str(team.id), "active_policy_id": str(policy.id)}


@router.get("/teams/{team_id}/stats")
async def team_stats(
    team_id: str,
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
) -> dict[str, Any]:
    """Same shape as ``/admin/stats`` but for one team only."""
    team = await _team(session, team_id)
    scope = AuditEvent.team_id == team.id
    total = (await session.execute(select(func.count()).select_from(AuditEvent).where(scope))).scalar_one()
    blocked = (
        await session.execute(
            select(func.count()).select_from(AuditEvent).where(scope, AuditEvent.blocked.is_(True))
        )
    ).scalar_one()
    by_provider = {
        p: c
        for p, c in (
            await session.execute(
                select(AuditEvent.provider, func.count()).where(scope).group_by(AuditEvent.provider)
            )
        ).all()
    }
    entities: dict[str, int] = {}
    for counts in (await session.execute(select(AuditEvent.entity_counts).where(scope))).scalars():
        if isinstance(counts, dict):
            for etype, n in counts.items():
                try:
                    entities[etype] = entities.get(etype, 0) + int(n)
                except (TypeError, ValueError):
                    continue
    median = (
        await session.execute(
            select(func.percentile_cont(0.5).within_group(AuditEvent.latency_ms.asc())).where(
                scope, AuditEvent.latency_ms.is_not(None)
            )
        )
    ).scalar_one_or_none()
    sessions = (
        await session.execute(
            select(func.count()).select_from(RedactionSession).where(RedactionSession.team_id == team.id)
        )
    ).scalar_one()
    return {
        "requests": int(total),
        "blocked": int(blocked),
        "sessions": int(sessions),
        "median_latency_ms": round(float(median)) if median is not None else None,
        "entities_by_type": entities,
        "by_provider": by_provider,
    }


@router.get("/teams/{team_id}/audit/verify")
async def verify_team_chain(
    team_id: str,
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
) -> dict[str, Any]:
    """Recompute the team's hash chain. ``ok: false`` means an event was altered or removed."""
    from app.audit import verify_chain

    team = await _team(session, team_id)
    count = (
        await session.execute(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.team_id == team.id)
        )
    ).scalar_one()
    ok = await verify_chain(session, str(team.id))
    return {"ok": bool(ok), "events": int(count), "checked_at": dt.datetime.now(dt.UTC).isoformat()}


@router.post("/playground")
async def playground(
    _: None = _AdminDep,
    session: AsyncSession = _SessionDep,
    body: dict[str, Any] = _BodyDep,
    explain: bool = Query(True),
) -> Any:
    """Run one prompt through the full firewall on behalf of a team and explain it.

    Uses the exact proxy pipeline (detect -> policy -> tokenize -> provider -> re-inflate ->
    audit). Capped per team per 24 h so a public demo cannot drain the provider budget.
    """
    from fastapi.responses import JSONResponse

    from app.auth import AuthContext
    from app.deps import DBAuditSink, get_active_provider
    from app.redaction.pg_store import PostgresTokenStore
    from app.redaction.policy import resolve_policy
    from app.routes.proxy import process_payload
    from app.schemas.openai import error_body

    team = await _team(session, str(body.get("team_id") or ""))
    prompt = str(body.get("prompt") or "")
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(status_code=413, detail=f"prompt longer than {MAX_PROMPT_CHARS} chars")

    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=24)
    used = (
        await session.execute(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.team_id == team.id,
                AuditEvent.route == PLAYGROUND_ROUTE,
                AuditEvent.created_at >= since,
            )
        )
    ).scalar_one()
    if used >= settings.playground_daily_limit:
        return JSONResponse(
            status_code=429,
            content=error_body(
                f"playground limit reached ({settings.playground_daily_limit} runs per 24 h)",
                code="playground_limit",
            ),
        )

    policy_id = str(team.default_policy_id) if team.default_policy_id else None
    auth = AuthContext(team_id=str(team.id), policy_id=policy_id, api_key_id=None)
    decision = await resolve_policy(session, policy_id)
    payload = {
        "model": "default",
        "messages": [
            {"role": "system", "content": PLAYGROUND_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
    }
    response = await process_payload(
        payload,
        PLAYGROUND_ROUTE,
        store=PostgresTokenStore(session),
        audit_sink=DBAuditSink(session),
        decision=decision,
        provider=get_active_provider(),
        auth=auth,
        explain=explain,
    )
    await session.commit()
    if hasattr(response, "headers"):
        response.headers["X-Playground-Remaining"] = str(
            max(0, settings.playground_daily_limit - int(used) - 1)
        )
    return response
