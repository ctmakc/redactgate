"""Liveness and readiness probes.

``/healthz`` answers 200 unconditionally (process is up). ``/readyz`` runs a trivial
``SELECT 1`` against the database and returns 503 when the datastore is unreachable so an
orchestrator can hold traffic until dependencies are healthy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import get_session

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_SessionDep = Depends(get_session)


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: AsyncSession = _SessionDep) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any DB failure means "not ready"
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "detail": str(exc)},
        )
    return JSONResponse(status_code=200, content={"status": "ok"})
