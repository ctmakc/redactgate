"""FastAPI application factory — wires routers, gateway adapters, and startup migrations.

Router contract (implemented by their respective modules):
  * app.routes.health  -> router         (GET /healthz, GET /readyz)
  * app.routes.proxy   -> router         (POST /v1/chat/completions, /v1/responses; GET /v1/models)
  * app.routes.admin   -> router         (GET /admin/* dashboards & audit/policy APIs)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.schemas.openai import error_body

logging.basicConfig(level=settings.log_level)
log = logging.getLogger("redactgate")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Register provider adapters.
    import app.gateway  # noqa: F401  (self-registers adapters)

    # Apply migrations + seed best-effort (don't crash the process if DB is down so
    # /healthz still answers and the operator can see the error).
    try:
        from app.migrate import apply_migrations

        applied = await apply_migrations()
        log.info("migrations applied: %s", applied)
        from app.db import session_scope

        try:
            from app.auth import ensure_default_api_key

            async with session_scope() as s:
                key = await ensure_default_api_key(s)
                if key:
                    log.info("default dev API key ensured (label=default)")
        except Exception as exc:  # noqa: BLE001
            log.warning("default api key bootstrap skipped: %s", exc)

        try:
            from app.db import session_scope as _ss
            from app.redaction.packs_loader import sync_packs_to_db

            async with _ss() as s:
                await sync_packs_to_db(s)
        except Exception as exc:  # noqa: BLE001
            log.debug("pack sync skipped: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.error("startup migration/bootstrap failed: %s", exc)

    yield

    from app.db import dispose_engine

    await dispose_engine()


def create_app() -> FastAPI:
    # Fail-closed: refuse to boot a production process with the insecure dev-fallback
    # keys or an unguarded admin API.
    problems = settings.runtime_problems()
    if problems:
        raise RuntimeError("insecure configuration:\n  - " + "\n  - ".join(problems))

    app = FastAPI(
        title="RedactGate",
        version=__version__,
        description="Self-hosted PII/financial-redaction firewall in front of any cloud LLM.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Admin-Token"],
    )

    @app.middleware("http")
    async def _limit_body_size(request, call_next):
        # Reject oversized bodies before they reach the (CPU-heavy) detection path.
        cl = request.headers.get("content-length")
        if cl is not None:
            try:
                if int(cl) > settings.max_body_bytes:
                    return JSONResponse(
                        status_code=413,
                        content=error_body("request body too large", code="payload_too_large"),
                    )
            except ValueError:
                pass
        return await call_next(request)

    # Wire routers defensively — a not-yet-implemented router should not stop boot.
    for module_path, attr in (
        ("app.routes.health", "router"),
        ("app.routes.proxy", "router"),
        ("app.routes.admin", "router"),
    ):
        try:
            mod = __import__(module_path, fromlist=[attr])
            app.include_router(getattr(mod, attr))
        except Exception as exc:  # noqa: BLE001
            log.warning("router %s not wired: %s", module_path, exc)

    @app.exception_handler(Exception)
    async def _unhandled(_request, exc: Exception):  # pragma: no cover
        log.exception("unhandled error")
        return JSONResponse(status_code=500, content=error_body(str(exc)))

    @app.get("/")
    async def root():
        from app.gateway import available_providers

        return {
            "service": "RedactGate",
            "version": __version__,
            "providers": available_providers(),
            "active_provider": settings.ai_provider,
        }

    return app


app = create_app()
