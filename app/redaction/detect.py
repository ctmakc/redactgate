"""Detection orchestration.

The :class:`Detector` fans out three independent detection passes and merges their spans
into a single non-overlapping :class:`~app.schemas.entities.DetectionResult`:

* **regex packs** — jurisdiction-specific regulated-identifier patterns (always on; the
  zero-optional-dependency path);
* **Presidio NER** — generic entities (PERSON, EMAIL, ...) when ``settings.enable_presidio``;
* **LLM-NER** — provider-backed extraction when ``settings.enable_llm_ner``.

Passes run concurrently via :func:`asyncio.gather`. Every optional pass is wrapped so a
missing dependency or a runtime failure contributes ``[]`` instead of breaking detection —
the regex pass alone is always sufficient. No raw entity value is ever logged.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.redaction.merge import merge_spans
from app.redaction.normalize import normalize_text
from app.redaction.presidio_ner import PresidioDetector
from app.schemas.entities import DetectionResult, EntitySpan

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.redaction.regex_packs import Pack

logger = logging.getLogger(__name__)

# Optional sibling modules, written by the jurisdiction-packs agent. They are imported
# defensively so this module loads even if those modules are missing or error on import.
_load_packs: Callable[[list[str]], list[Pack]] | None
try:
    from app.redaction.regex_packs import load_packs as _load_packs
except Exception as exc:  # noqa: BLE001 - regex packs unavailable -> no regex pass
    logger.warning("regex_packs unavailable: %s", exc)
    _load_packs = None

_llm_ner: Callable[[str], Awaitable[Any]] | None
try:
    from app.redaction.llm_ner import llm_ner as _llm_ner
except Exception as exc:  # noqa: BLE001 - llm_ner unavailable -> LLM pass disabled
    logger.info("llm_ner unavailable: %s", exc)
    _llm_ner = None


# ── Encoded-payload pass ─────────────────────────────────────────────────────────
#
# SECURITY: a caller (or an injected document) can wrap PII in base64 so the regex packs
# never see it — ``U0lOIDE5MyA0NTYgNzg3`` is "SIN 193 456 787" — and every upstream LLM
# decodes base64 effortlessly. Each base64-looking token is decoded (standard and URL-safe
# alphabets, nested up to ``_B64_MAX_DEPTH``) and, if the decoded text contains a
# regex-detectable entity, the WHOLE token is emitted as a span of that entity type.

_B64_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_-]{16,}={0,2}(?![A-Za-z0-9+/_=-])")
_B64_MAX_TOKEN = 64_000  # bound per-token decode work
_B64_MAX_DEPTH = 2
_B64_SCORE = 0.96  # above plain regex (0.95): the token-level span must win any overlap


def _b64_decode_text(token: str) -> str | None:
    """Decode a base64/base64url token to printable text, or None if it isn't one."""
    if len(token) > _B64_MAX_TOKEN:
        return None
    body = token.rstrip("=").replace("-", "+").replace("_", "/")
    if len(body) % 4 == 1:
        return None
    try:
        raw = base64.b64decode(body + "=" * (-len(body) % 4), validate=True)
        text = raw.decode("utf-8")
    except (binascii.Error, ValueError):
        return None
    if not text or any(not (ch.isprintable() or ch.isspace()) for ch in text):
        return None
    return normalize_text(text)


def _encoded_spans(
    text: str, packs: list[Pack], *, depth: int = _B64_MAX_DEPTH
) -> list[EntitySpan]:
    """Spans covering base64 tokens in ``text`` whose decoded content holds an entity."""
    if depth <= 0 or len(text) < 16:
        return []
    out: list[EntitySpan] = []
    for m in _B64_TOKEN_RE.finditer(text):
        decoded = _b64_decode_text(m.group(0))
        if decoded is None:
            continue
        inner: list[EntitySpan] = []
        for pack in packs:
            inner.extend(pack.detect(decoded))
        if not inner:
            inner = _encoded_spans(decoded, packs, depth=depth - 1)
        if inner:
            out.append(
                EntitySpan(
                    start=m.start(),
                    end=m.end(),
                    entity_type=inner[0].entity_type,
                    text=m.group(0),
                    score=_B64_SCORE,
                    source="regex",
                    jurisdiction=inner[0].jurisdiction,
                )
            )
    return out


class Detector:
    """Multi-pass sensitive-entity detector.

    A single instance is cheap to keep around: it lazily constructs the (heavy) Presidio
    engine once on first use when ``settings.enable_presidio`` is set.
    """

    def __init__(self) -> None:
        self._presidio: PresidioDetector | None = None

    def _get_presidio(self) -> PresidioDetector:
        if self._presidio is None:
            self._presidio = PresidioDetector()
        return self._presidio

    async def detect(
        self,
        text: str,
        *,
        pack_codes: list[str],
        language: str = "en",
    ) -> DetectionResult:
        """Detect entities in ``text`` and return a merged, non-overlapping result.

        ``pack_codes`` selects which jurisdiction regex packs to run. The Presidio and
        LLM-NER passes are gated by ``settings.enable_presidio`` / ``settings.enable_llm_ner``
        respectively. Always returns a :class:`DetectionResult`, even on empty input.
        """
        if not text:
            return DetectionResult(text=text, spans=[])

        tasks: list[asyncio.Future[list[EntitySpan]] | asyncio.Task[list[EntitySpan]]] = []

        tasks.append(asyncio.ensure_future(self._run_regex(text, pack_codes)))

        if settings.enable_presidio:
            tasks.append(asyncio.ensure_future(self._run_presidio(text, language)))

        if settings.enable_llm_ner and _llm_ner is not None:
            tasks.append(asyncio.ensure_future(self._run_llm(text)))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        spans: list[EntitySpan] = []
        for res in results:
            if isinstance(res, BaseException):
                # A failing optional pass contributes nothing; never abort detection.
                logger.warning("detection pass failed: %s", res)
                continue
            spans.extend(res)

        return DetectionResult(text=text, spans=merge_spans(spans))

    # ── Individual passes (each self-contained & failure-isolated) ───────────────

    async def _run_regex(self, text: str, pack_codes: list[str]) -> list[EntitySpan]:
        if _load_packs is None or not pack_codes:
            return []

        def _work() -> list[EntitySpan]:
            out: list[EntitySpan] = []
            packs = _load_packs(pack_codes)
            for pack in packs:
                try:
                    out.extend(pack.detect(text))
                except Exception as exc:  # noqa: BLE001 - one bad pack must not sink the pass
                    logger.warning("regex pack detect failed: %s", exc)
            try:
                out.extend(_encoded_spans(text, packs))
            except Exception as exc:  # noqa: BLE001 - never sink the regex pass
                logger.warning("encoded-payload detect failed: %s", exc)
            return out

        return await asyncio.to_thread(_work)

    async def _run_presidio(self, text: str, language: str) -> list[EntitySpan]:
        detector = self._get_presidio()
        if not detector.available:
            return []
        # Presidio's analyze() is synchronous/CPU-bound — keep it off the event loop.
        return await asyncio.to_thread(detector.detect, text, language)

    async def _run_llm(self, text: str) -> list[EntitySpan]:
        if _llm_ner is None:
            return []
        result = await _llm_ner(text)
        return list(result) if result else []
