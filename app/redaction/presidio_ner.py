"""Presidio-backed generic NER pass.

Presidio (and its spaCy backend) is a *heavy, optional* dependency installed via the
``ner`` extra. This module lazy-imports it: if it is absent the detector degrades to a
no-op (``available == False``, ``detect`` returns ``[]``) so the regex-only path keeps
working with zero optional dependencies.

Presidio entity types are mapped onto RedactGate's canonical codes; everything else is
ignored. No raw entity value is ever logged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.schemas.entities import EntitySpan

if TYPE_CHECKING:  # pragma: no cover - typing only
    from presidio_analyzer import AnalyzerEngine

logger = logging.getLogger(__name__)

# Presidio entity type -> RedactGate canonical code. Anything not listed is dropped.
_TYPE_MAP: dict[str, str] = {
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CREDIT_CARD",
    "IBAN_CODE": "IBAN",
    "LOCATION": "LOCATION",
    "ORGANIZATION": "ORG",
    "IP_ADDRESS": "IP_ADDRESS",
    "URL": "URL",
    "DATE_TIME": "DATE_TIME",
}


class PresidioDetector:
    """Generic NER detector wrapping ``presidio_analyzer.AnalyzerEngine``.

    Construction never raises: if Presidio (or its model backend) is unavailable the
    detector is marked unavailable and :meth:`detect` returns an empty list.
    """

    def __init__(self) -> None:
        self.available: bool = False
        self._analyzer: AnalyzerEngine | None = None
        try:
            from presidio_analyzer import AnalyzerEngine

            self._analyzer = AnalyzerEngine()
            self.available = True
        except Exception as exc:  # noqa: BLE001 - any import/model failure disables the pass
            logger.info("Presidio NER unavailable, skipping NER pass: %s", exc)
            self._analyzer = None
            self.available = False

    def detect(self, text: str, language: str = "en") -> list[EntitySpan]:
        """Run Presidio over ``text`` and return mapped, canonical :class:`EntitySpan`s.

        Returns ``[]`` when Presidio is unavailable or analysis fails. Unmapped Presidio
        entity types are silently skipped.
        """
        if not self.available or self._analyzer is None or not text:
            return []
        try:
            results: list[Any] = self._analyzer.analyze(text=text, language=language)
        except Exception as exc:  # noqa: BLE001 - a failing optional pass contributes []
            logger.warning("Presidio analysis failed: %s", exc)
            return []

        spans: list[EntitySpan] = []
        for res in results:
            mapped = _TYPE_MAP.get(getattr(res, "entity_type", ""))
            if mapped is None:
                continue
            start = int(getattr(res, "start", 0))
            end = int(getattr(res, "end", 0))
            if end <= start:
                continue
            spans.append(
                EntitySpan(
                    start=start,
                    end=end,
                    entity_type=mapped,
                    text=text[start:end],
                    score=float(getattr(res, "score", 1.0)),
                    source="presidio",
                )
            )
        return spans
