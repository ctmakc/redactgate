"""The OpenAI-compatible proxy surface.

``POST /v1/chat/completions`` and ``POST /v1/responses`` run the full firewall:
detect → policy → tokenize → upstream call → re-inflate → audit. ``GET /v1/models`` lists
the configured providers' default models.

SECURITY: audit events carry entity *type counts*, token usage and latency only — never a
raw entity value, and never the redacted/clear payload text.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.deps import (
    DBAuditSink,
    build_vault,
    get_active_provider,
    get_audit_sink,
    get_auth,
    get_detector,
    get_policy_decision,
    get_token_store,
)
from app.gateway.base import ProviderError
from app.redaction.pipeline import PipelineContext, build_pipeline
from app.schemas.openai import (
    HardBlockError,
    error_body,
    extract_delta_text,
    set_delta_text,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.auth import AuthContext
    from app.gateway.base import Provider
    from app.redaction.store import TokenStore
    from app.schemas.entities import PolicyDecision

router = APIRouter()

# Module-level dependency singletons (keeps the FastAPI `Depends` idiom; ruff B008-clean).
_StoreDep = Depends(get_token_store)
_AuditDep = Depends(get_audit_sink)
_PolicyDep = Depends(get_policy_decision)
_ProviderDep = Depends(get_active_provider)
_AuthDep = Depends(get_auth)


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _provider_allowed(decision: PolicyDecision, provider_name: str) -> bool:
    """Empty allow-list means *all providers permitted*; otherwise must be listed."""
    allowed = getattr(decision, "allowed_providers", None) or []
    return not allowed or provider_name in allowed


def _provider_name(provider: Provider) -> str:
    return getattr(provider, "name", None) or settings.ai_provider


def _usage(completion: dict[str, Any]) -> tuple[int | None, int | None]:
    usage = completion.get("usage") if isinstance(completion, dict) else None
    if not isinstance(usage, dict):
        return None, None
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


_log = logging.getLogger("redactgate.proxy")

async def _safe_audit(
    audit_sink: DBAuditSink | None,
    *,
    auth: AuthContext,
    session_id: str | None,
    route: str,
    provider: str,
    entity_counts: dict[str, int],
    blocked: bool,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
) -> None:
    """Best-effort audit write — auditing must never break the proxy response path."""
    if audit_sink is None:
        return
    try:
        await audit_sink.record(
            team_id=auth.team_id,
            api_key_id=getattr(auth, "api_key_id", None),
            session_id=session_id,
            route=route,
            provider=provider,
            entity_counts=entity_counts,
            blocked=blocked,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
    except Exception:  # noqa: BLE001 - never fatal to the response path
        # Loud log: a dropped audit event means a (redacted) request went unrecorded.
        _log.warning("audit write failed for route=%s provider=%s — event NOT recorded", route, provider)
        return


async def _safe_audit_fresh(
    *,
    auth: AuthContext,
    session_id: str | None,
    route: str,
    provider: str,
    entity_counts: dict[str, int],
    blocked: bool,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    latency_ms: int | None = None,
) -> None:
    """Audit on a FRESH committed session.

    The streaming SSE generator runs after the request session has been committed and
    closed, so it cannot reuse the request-bound sink. We open our own session, write the
    hash-chained event, and commit. If the ``session_id`` FK is not yet visible we retry
    once unlinked so the request is still audited (auditing must never be skipped)."""
    from app.audit import record_event
    from app.db import session_scope

    for link in (session_id, None):
        try:
            async with session_scope() as s:
                await record_event(
                    s,
                    team_id=auth.team_id,
                    api_key_id=getattr(auth, "api_key_id", None),
                    session_id=link,
                    route=route,
                    provider=provider,
                    entity_counts=entity_counts,
                    blocked=blocked,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                )
                await s.commit()
            return
        except Exception:  # noqa: BLE001 - try unlinked, then give up
            continue
    _log.warning("streaming audit write failed for route=%s provider=%s — event NOT recorded", route, provider)


async def _read_payload(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if not isinstance(body, dict):
        raise ProviderError("request body must be a JSON object", status_code=400)
    return body


def _sse_chunk(chunk: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n".encode()


# ── Core handler ─────────────────────────────────────────────────────────────────


async def _handle(
    request: Request,
    route: str,
    *,
    store: TokenStore,
    audit_sink: DBAuditSink,
    decision: PolicyDecision,
    provider: Provider,
    auth: AuthContext,
) -> Any:
    provider_name = _provider_name(provider)

    # Policy provider allow-list gate (before any upstream work).
    if not _provider_allowed(decision, provider_name):
        return JSONResponse(
            status_code=400,
            content=error_body(
                f"provider '{provider_name}' is not permitted by policy",
                code="provider_not_allowed",
            ),
        )

    try:
        payload = await _read_payload(request)
    except ProviderError as exc:
        return JSONResponse(status_code=exc.status_code, content=error_body(str(exc)))

    vault = build_vault(store, cfg=settings)
    detector = get_detector(settings)
    pipeline = build_pipeline(
        store=store,
        vault=vault,
        detector=detector,
        audit_sink=audit_sink,
        decision=decision,
        auth=auth,
        provider=provider,
    )

    started = time.perf_counter()

    # ── Sanitize (may hard-block) ──
    try:
        sanitized, ctx = await pipeline.sanitize_request(payload, route=route)
    except HardBlockError as exc:
        await _safe_audit(
            audit_sink,
            auth=auth,
            session_id=None,
            route=route,
            provider=provider_name,
            entity_counts={t: 1 for t in sorted(set(exc.blocked_types))},
            blocked=True,
        )
        return JSONResponse(
            status_code=422,
            content=error_body(
                "request blocked by redaction policy",
                code="hard_blocked",
            ),
        )

    stream_requested = bool(payload.get("stream"))

    if stream_requested:
        # Pre-resolve the session token map NOW, while the request DB session is alive —
        # the SSE generator below runs after this handler returns and the session is gone.
        detok = await pipeline.prepare_stream_reinflation(ctx)
        return StreamingResponse(
            _stream_response(
                detok=detok,
                provider=provider,
                sanitized=sanitized,
                ctx=ctx,
                route=route,
                provider_name=provider_name,
                auth=auth,
                started=started,
            ),
            media_type="text/event-stream",
        )

    # ── Non-streaming ──
    try:
        completion = await provider.complete(sanitized)
    except ProviderError as exc:
        await _safe_audit(
            audit_sink,
            auth=auth,
            session_id=ctx.session_id,
            route=route,
            provider=provider_name,
            entity_counts=ctx.entity_counts,
            blocked=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(str(exc), code="provider_error"),
        )

    reinflated = await pipeline.reinflate(completion, ctx)
    prompt_tokens, completion_tokens = _usage(reinflated)
    await _safe_audit(
        audit_sink,
        auth=auth,
        session_id=ctx.session_id,
        route=route,
        provider=provider_name,
        entity_counts=ctx.entity_counts,
        blocked=False,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    return JSONResponse(status_code=200, content=reinflated)


async def _stream_response(
    *,
    detok: Any,
    provider: Provider,
    sanitized: dict[str, Any],
    ctx: PipelineContext,
    route: str,
    provider_name: str,
    auth: AuthContext,
    started: float,
) -> Any:
    """Generator yielding SSE bytes; detokenizes each delta and flushes at the end.

    ``detok`` is a PRE-RESOLVED ``StreamDetokenizer`` (built in the handler while the DB
    session was alive) so this generator needs no store access after the request closes.
    """
    try:
        async for chunk in provider.stream(sanitized):
            delta = extract_delta_text(chunk)
            if delta:
                out = await detok.push(delta)
                set_delta_text(chunk, out)
            yield _sse_chunk(chunk)
        # Flush any buffered tail (a placeholder split across the last chunks).
        tail = await detok.flush()
        if tail:
            yield _sse_chunk(
                {
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": tail}, "finish_reason": None}],
                }
            )
    except ProviderError as exc:
        # Surface upstream failures inline in the SSE stream (status already 200).
        yield _sse_chunk(error_body(str(exc), code="provider_error"))
    finally:
        yield b"data: [DONE]\n\n"

    # Fresh session: the request session is already committed/closed by the time this
    # generator finishes streaming.
    await _safe_audit_fresh(
        auth=auth,
        session_id=ctx.session_id,
        route=route,
        provider=provider_name,
        entity_counts=ctx.entity_counts,
        blocked=False,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


# ── Routes ───────────────────────────────────────────────────────────────────────


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    store: TokenStore = _StoreDep,
    audit_sink: DBAuditSink = _AuditDep,
    decision: PolicyDecision = _PolicyDep,
    provider: Provider = _ProviderDep,
    auth: AuthContext = _AuthDep,
) -> Any:
    return await _handle(
        request,
        "/v1/chat/completions",
        store=store,
        audit_sink=audit_sink,
        decision=decision,
        provider=provider,
        auth=auth,
    )


@router.post("/v1/responses")
async def responses(
    request: Request,
    store: TokenStore = _StoreDep,
    audit_sink: DBAuditSink = _AuditDep,
    decision: PolicyDecision = _PolicyDep,
    provider: Provider = _ProviderDep,
    auth: AuthContext = _AuthDep,
) -> Any:
    return await _handle(
        request,
        "/v1/responses",
        store=store,
        audit_sink=audit_sink,
        decision=decision,
        provider=provider,
        auth=auth,
    )


@router.get("/v1/models")
async def list_models() -> dict[str, Any]:
    """List each configured provider's default model in the OpenAI `models` shape."""
    import app.gateway  # noqa: F401  (self-registers adapters)
    from app.gateway.base import available_providers, get_provider

    data: list[dict[str, Any]] = []
    for name in available_providers():
        try:
            provider = get_provider(name, settings)
            model = provider.default_model()
        except Exception:  # noqa: BLE001 - skip providers that cannot be instantiated
            model = ""
        if model:
            data.append({"id": model, "object": "model", "owned_by": name})
    return {"object": "list", "data": data}
