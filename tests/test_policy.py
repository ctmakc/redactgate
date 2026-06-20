"""Unit tests for the policy engine (``app.redaction.policy``).

Covers the pure, DB-less surface:
  * ``evaluate`` hard-block semantics (mode==HARD_BLOCK or detected ∈ blocked_types)
  * ``filter_spans`` redact_types honoring (None == redact everything)
  * ``provider_allowed`` allow-list membership (empty list == allow all)

All helpers here are pure w.r.t. entity *values*; tests only exercise types/modes and
never need a database.
"""

from __future__ import annotations

import pytest

from app.redaction.policy import evaluate, filter_spans, provider_allowed
from app.schemas.entities import EntitySpan, PolicyDecision, PolicyMode
from app.schemas.openai import HardBlockError


def _decision(
    *,
    mode: PolicyMode = PolicyMode.TOKENIZE,
    blocked_types: list[str] | None = None,
    allowed_providers: list[str] | None = None,
    redact_types: list[str] | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        mode=mode,
        blocked=False,
        blocked_types=blocked_types or [],
        allowed_providers=allowed_providers or [],
        redact_types=redact_types,
    )


def _span(entity_type: str, start: int = 0, end: int = 4) -> EntitySpan:
    return EntitySpan(start=start, end=end, entity_type=entity_type, text="xxxx")


# ── evaluate: hard block ────────────────────────────────────────────────────────


def test_evaluate_tokenize_no_blocked_types_passes():
    decision = _decision(mode=PolicyMode.TOKENIZE)
    # Should not raise even though entities were detected.
    assert evaluate(decision, {"SIN", "EMAIL"}) is None


def test_evaluate_mask_mode_does_not_hard_block():
    decision = _decision(mode=PolicyMode.MASK)
    assert evaluate(decision, {"PERSON"}) is None


def test_evaluate_hard_block_mode_always_raises():
    decision = _decision(mode=PolicyMode.HARD_BLOCK)
    with pytest.raises(HardBlockError):
        evaluate(decision, {"PERSON"})


def test_evaluate_hard_block_mode_raises_even_with_no_detections():
    # mode forces the block regardless of which (if any) types matched.
    decision = _decision(mode=PolicyMode.HARD_BLOCK)
    with pytest.raises(HardBlockError):
        evaluate(decision, set())


def test_evaluate_blocked_type_intersection_raises():
    decision = _decision(mode=PolicyMode.TOKENIZE, blocked_types=["CREDIT_CARD"])
    with pytest.raises(HardBlockError):
        evaluate(decision, {"EMAIL", "CREDIT_CARD"})


def test_evaluate_non_matching_blocked_types_passes():
    decision = _decision(mode=PolicyMode.TOKENIZE, blocked_types=["CREDIT_CARD"])
    # detected types disjoint from blocked_types -> allowed
    assert evaluate(decision, {"EMAIL", "PHONE"}) is None


def test_evaluate_blocked_types_with_empty_detection_passes():
    decision = _decision(mode=PolicyMode.TOKENIZE, blocked_types=["SSN"])
    assert evaluate(decision, set()) is None


def test_evaluate_offending_list_is_sorted_intersection():
    decision = _decision(
        mode=PolicyMode.TOKENIZE, blocked_types=["SSN", "SIN", "EIN"]
    )
    with pytest.raises(HardBlockError) as exc:
        evaluate(decision, {"EIN", "SIN", "EMAIL"})
    # Only the intersection, sorted, and never including non-blocked detected types.
    assert exc.value.blocked_types == ["EIN", "SIN"]
    assert "EMAIL" not in exc.value.blocked_types


def test_evaluate_hard_block_mode_offending_falls_back_to_all_detected():
    # HARD_BLOCK with no specific blocked_types match -> offending = all detected, sorted.
    decision = _decision(mode=PolicyMode.HARD_BLOCK, blocked_types=[])
    with pytest.raises(HardBlockError) as exc:
        evaluate(decision, {"EMAIL", "PERSON"})
    assert exc.value.blocked_types == ["EMAIL", "PERSON"]


def test_evaluate_hard_block_mode_prefers_specific_intersection():
    # When HARD_BLOCK *and* a specific blocked type matches, the offending list is the
    # intersection (more precise than "everything detected").
    decision = _decision(mode=PolicyMode.HARD_BLOCK, blocked_types=["SIN"])
    with pytest.raises(HardBlockError) as exc:
        evaluate(decision, {"SIN", "EMAIL"})
    assert exc.value.blocked_types == ["SIN"]


# ── filter_spans: redact_types ──────────────────────────────────────────────────


def test_filter_spans_none_redacts_everything():
    decision = _decision(redact_types=None)
    spans = [_span("SIN"), _span("EMAIL"), _span("PERSON")]
    kept = filter_spans(decision, spans)
    assert kept == spans
    assert [s.entity_type for s in kept] == ["SIN", "EMAIL", "PERSON"]


def test_filter_spans_empty_redact_list_redacts_nothing():
    # redact_types == [] means "redact only these (none)" -> empty result.
    decision = _decision(redact_types=[])
    spans = [_span("SIN"), _span("EMAIL")]
    assert filter_spans(decision, spans) == []


def test_filter_spans_subset_keeps_only_listed_types():
    decision = _decision(redact_types=["EMAIL"])
    spans = [_span("SIN"), _span("EMAIL"), _span("PHONE")]
    kept = filter_spans(decision, spans)
    assert [s.entity_type for s in kept] == ["EMAIL"]


def test_filter_spans_preserves_order_and_duplicates():
    decision = _decision(redact_types=["EMAIL", "SIN"])
    spans = [
        _span("EMAIL", 0, 4),
        _span("PHONE", 5, 9),
        _span("SIN", 10, 14),
        _span("EMAIL", 15, 19),
    ]
    kept = filter_spans(decision, spans)
    assert [(s.entity_type, s.start) for s in kept] == [
        ("EMAIL", 0),
        ("SIN", 10),
        ("EMAIL", 15),
    ]


def test_filter_spans_accepts_any_iterable():
    decision = _decision(redact_types=["SIN"])
    gen = (s for s in [_span("SIN"), _span("EMAIL")])
    kept = filter_spans(decision, gen)
    assert [s.entity_type for s in kept] == ["SIN"]


def test_filter_spans_empty_input():
    decision = _decision(redact_types=None)
    assert filter_spans(decision, []) == []


# ── provider_allowed: allow-list ────────────────────────────────────────────────


def test_provider_allowed_empty_list_allows_all():
    decision = _decision(allowed_providers=[])
    for provider in ("openai", "anthropic", "gemini", "ollama", "anything"):
        assert provider_allowed(decision, provider) is True


def test_provider_allowed_membership_true():
    decision = _decision(allowed_providers=["openai", "anthropic"])
    assert provider_allowed(decision, "openai") is True
    assert provider_allowed(decision, "anthropic") is True


def test_provider_allowed_membership_false():
    decision = _decision(allowed_providers=["openai"])
    assert provider_allowed(decision, "gemini") is False
    assert provider_allowed(decision, "ollama") is False


def test_provider_allowed_is_case_sensitive():
    decision = _decision(allowed_providers=["openai"])
    # membership is exact; case mismatch is not allowed.
    assert provider_allowed(decision, "OpenAI") is False
