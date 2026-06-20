"""Shared detection / policy data shapes — the contract between detection, vault,
policy and audit modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

DetectionSource = Literal["regex", "presidio", "llm", "manual"]


@dataclass(slots=True)
class EntitySpan:
    """A detected sensitive entity within a single text string.

    ``start``/``end`` are character offsets (Python slice semantics) into the text the
    detector was given. ``entity_type`` is an UPPER_SNAKE code (e.g. SIN, IBAN, PERSON).
    """

    start: int
    end: int
    entity_type: str
    text: str
    score: float = 1.0
    source: DetectionSource = "regex"
    jurisdiction: str | None = None

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: EntitySpan) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(slots=True)
class DetectionResult:
    """Result of running the multi-pass detector over one text string."""

    text: str
    spans: list[EntitySpan] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.spans:
            out[s.entity_type] = out.get(s.entity_type, 0) + 1
        return out


class PolicyMode(str, Enum):  # noqa: UP042  (str+Enum is intentional; StrEnum changes str())
    TOKENIZE = "tokenize"
    MASK = "mask"
    HARD_BLOCK = "hard_block"


@dataclass(slots=True)
class PolicyDecision:
    """Outcome of evaluating a policy against detected entity types."""

    mode: PolicyMode
    blocked: bool
    blocked_types: list[str] = field(default_factory=list)
    allowed_providers: list[str] = field(default_factory=list)
    redact_types: list[str] | None = None  # None = redact everything detected

    def should_redact(self, entity_type: str) -> bool:
        if self.redact_types is None:
            return True
        return entity_type in self.redact_types


# Canonical entity-type catalogue (extended by jurisdiction packs). Generic NER types
# come from Presidio/spaCy; the rest are regulated-identifier codes from packs.
GENERIC_TYPES: set[str] = {
    "PERSON",
    "ORG",
    "LOCATION",
    "EMAIL",
    "PHONE",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "DATE_TIME",
    "URL",
    "IBAN",
    "BANK_ACCOUNT",
}
