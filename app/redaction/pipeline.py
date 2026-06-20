"""Redaction pipeline — the integration core tying detection, policy, the vault and the
provider together for a single request lifecycle.

Flow (``sanitize_request``):
    1. extract every redactable text from the OpenAI-style payload (stable walk order),
    2. open ONE redaction session so every text shares a token namespace
       (referential consistency across the whole request),
    3. detect entities in each text concurrently,
    4. filter spans through the policy and evaluate hard-block over the union of types,
    5. tokenize each text under the shared session id,
    6. rebuild the payload and return it with a :class:`PipelineContext`.

Re-inflation (``reinflate`` / ``reinflate_stream``) swaps placeholders back to their real
values using the same session id.

SECURITY: this module never logs, prints or persists a raw entity value. Only entity
*type counts* are carried on the context for the audit sink.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.config import Settings, settings
from app.schemas.openai import (
    HardBlockError,
    extract_completion_text,
    extract_texts,
    inject_texts,
    set_completion_text,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.auth import AuthContext
    from app.deps import DBAuditSink
    from app.gateway.base import Provider
    from app.redaction.store import TokenStore
    from app.redaction.vault import StreamDetokenizer, Vault
    from app.schemas.entities import DetectionResult, EntitySpan, PolicyDecision


@dataclass(slots=True)
class PipelineContext:
    """Carries per-request redaction state from sanitize → provider → reinflate.

    Holds only non-sensitive metadata: the session id (an opaque scope key), entity
    *type counts*, a blocked flag, and the merged spans (offsets/types — used for
    diagnostics, never persisted to audit).
    """

    session_id: str
    entity_counts: dict[str, int] = field(default_factory=dict)
    blocked: bool = False
    spans: list[EntitySpan] = field(default_factory=list)


class RedactionPipeline:
    """Stateless-per-request orchestrator. One instance is built per request from the
    injected dependencies (see :func:`build_pipeline`)."""

    def __init__(
        self,
        *,
        store: TokenStore,
        vault: Vault,
        detector: Any,
        audit_sink: DBAuditSink | None,
        decision: PolicyDecision,
        auth: AuthContext,
        provider: Provider,
        cfg: Settings = settings,
    ) -> None:
        self.store = store
        self.vault = vault
        self.detector = detector
        self.audit_sink = audit_sink
        self.decision = decision
        self.auth = auth
        self.provider = provider
        self.settings = cfg

    # ── Request sanitisation ────────────────────────────────────────────────────

    async def sanitize_request(
        self, payload: dict[str, Any], *, route: str
    ) -> tuple[dict[str, Any], PipelineContext]:
        """Detect + tokenize every redactable text in ``payload`` under one session.

        Raises :class:`HardBlockError` if the policy forbids a detected entity type; the
        caller writes a *blocked* audit event and returns 422.
        """
        texts = extract_texts(payload)

        # Open one session so the same value maps to the same placeholder everywhere in
        # this request (referential consistency).
        session_id = await self.store.create_session(
            team_id=self.auth.team_id, policy_id=self.auth.policy_id
        )
        ctx = PipelineContext(session_id=session_id)

        if not texts:
            return payload, ctx

        # Detect concurrently. Each detect() call sees a single text string, so span
        # offsets are local to that text.
        pack_codes = self.settings.pack_codes
        results: list[DetectionResult] = await asyncio.gather(
            *(self.detector.detect(t, pack_codes=pack_codes) for t in texts)
        )

        # Filter spans through the policy and collect the union of surviving types.
        from app.redaction.policy import evaluate, filter_spans

        filtered: list[list[EntitySpan]] = []
        detected_types: set[str] = set()
        all_spans: list[EntitySpan] = []
        for res in results:
            spans = filter_spans(self.decision, res.spans)
            filtered.append(spans)
            for sp in spans:
                detected_types.add(sp.entity_type)
            all_spans.extend(spans)

        # Hard-block evaluation over the union of detected types (may raise).
        try:
            evaluate(self.decision, detected_types)
        except HardBlockError:
            ctx.blocked = True
            ctx.spans = all_spans
            ctx.entity_counts = _count_types(all_spans)
            raise

        # Tokenize each text under the SAME session id.
        sanitized_texts: list[str] = []
        for text, spans in zip(texts, filtered, strict=True):
            if spans:
                sanitized_texts.append(
                    await self.vault.tokenize(text, spans, session_id=session_id)
                )
            else:
                sanitized_texts.append(text)

        sanitized_payload = inject_texts(payload, sanitized_texts)

        ctx.spans = all_spans
        ctx.entity_counts = _count_types(all_spans)
        return sanitized_payload, ctx

    # ── Response re-inflation ───────────────────────────────────────────────────

    async def reinflate(self, completion: dict[str, Any], ctx: PipelineContext) -> dict[str, Any]:
        """Swap placeholders in the assistant message back to their real values."""
        text = extract_completion_text(completion)
        if text:
            restored = await self.vault.detokenize(text, session_id=ctx.session_id)
            set_completion_text(completion, restored)
        return completion

    def reinflate_stream(self, ctx: PipelineContext) -> StreamDetokenizer:
        """Return a stream de-tokenizer that resolves via live store lookups.

        Safe only while the backing store stays alive for the whole stream. The DB-backed
        proxy route must use ``prepare_stream_reinflation`` instead, because its SSE
        generator outlives the request DB session.
        """
        return self.vault.stream_detokenizer(ctx.session_id)

    async def prepare_stream_reinflation(self, ctx: PipelineContext) -> StreamDetokenizer:
        """Pre-resolve this request's session into an in-memory de-tokenizer.

        Call this in the request handler (while the DB session is alive); the returned
        de-tokenizer needs no further store access, so it is safe to use from the SSE
        response generator after the request session has been torn down.
        """
        return await self.vault.stream_detokenizer_prepared(ctx.session_id)


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _count_types(spans: list[EntitySpan]) -> dict[str, int]:
    """Aggregate entity *type counts* (no raw values)."""
    out: dict[str, int] = {}
    for sp in spans:
        out[sp.entity_type] = out.get(sp.entity_type, 0) + 1
    return out


def build_pipeline(
    *,
    store: TokenStore,
    vault: Vault,
    detector: Any,
    audit_sink: DBAuditSink | None,
    decision: PolicyDecision,
    auth: AuthContext,
    provider: Provider,
    cfg: Settings = settings,
) -> RedactionPipeline:
    """Module-level builder assembling a :class:`RedactionPipeline` from injected deps."""
    return RedactionPipeline(
        store=store,
        vault=vault,
        detector=detector,
        audit_sink=audit_sink,
        decision=decision,
        auth=auth,
        provider=provider,
        cfg=cfg,
    )
