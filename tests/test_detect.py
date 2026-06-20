"""Unit tests for the Detector orchestration layer (regex-only lane).

These exercise ``Detector().detect`` with Presidio and LLM-NER disabled (the unit lane),
so detection is purely the zero-dependency regex packs merged into a non-overlapping
result. No DB, no Redis, no network.
"""

from __future__ import annotations

import asyncio

import pytest

from app import config as app_config
from app.redaction import detect as detect_module
from app.redaction.detect import Detector
from app.schemas.entities import DetectionResult, EntitySpan

ALL_PACKS = ["CA", "US", "EU", "UA", "IRCC"]

# A mixed paragraph where every regulated identifier is validator-passing, so the
# regex-only path detects all of them. Offsets verified independently.
MIXED_TEXT = (
    "Client John Smith. SIN 193 456 787. SSN 123-45-6789. EIN 12-3456789. "
    "IBAN GB82 WEST 1234 5698 7654 32. ІПН 1234567890. UCI 1234 5678."
)


@pytest.fixture(autouse=True)
def _regex_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the optional passes off so the lane is deterministic regardless of env."""
    monkeypatch.setattr(app_config.settings, "enable_presidio", False, raising=False)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", False, raising=False)


def _run(coro):
    return asyncio.run(coro)


class TestRegexOnlyConfig:
    def test_optional_passes_disabled_in_unit_lane(self) -> None:
        assert app_config.settings.enable_presidio is False
        assert app_config.settings.enable_llm_ner is False

    def test_regex_pack_loader_wired(self) -> None:
        # The detector must have successfully imported the regex pack loader.
        assert detect_module._load_packs is not None


class TestMixedParagraph:
    def test_finds_expected_entity_types(self) -> None:
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=ALL_PACKS))
        assert isinstance(result, DetectionResult)
        found = {s.entity_type for s in result.spans}
        # One representative regulated identifier from each of the five packs.
        assert {"SIN", "SSN", "EIN", "IBAN", "IPN", "UCI"}.issubset(found)

    def test_all_spans_are_regex_sourced(self) -> None:
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=ALL_PACKS))
        assert result.spans
        for s in result.spans:
            assert s.source == "regex"
            assert s.jurisdiction in set(ALL_PACKS)

    def test_span_offsets_slice_back_to_text(self) -> None:
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=ALL_PACKS))
        for s in result.spans:
            assert MIXED_TEXT[s.start : s.end] == s.text

    def test_spans_are_merged_non_overlapping(self) -> None:
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=ALL_PACKS))
        ordered = sorted(result.spans, key=lambda s: (s.start, s.end))
        for a, b in zip(ordered, ordered[1:], strict=False):
            assert a.end <= b.start, f"overlap between {a} and {b}"

    def test_spans_returned_sorted_by_start(self) -> None:
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=ALL_PACKS))
        starts = [s.start for s in result.spans]
        assert starts == sorted(starts)

    def test_result_text_preserved(self) -> None:
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=ALL_PACKS))
        assert result.text == MIXED_TEXT

    def test_counts_reflect_spans(self) -> None:
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=ALL_PACKS))
        counts = result.counts()
        assert sum(counts.values()) == len(result.spans)
        assert counts.get("IBAN") == 1


class TestOverlapResolution:
    def test_overlapping_pack_types_collapse_to_one(self) -> None:
        # "GST 123456789 RT 0001" matches BOTH BUSINESS_NUMBER and GST_HST over the
        # same span; the merge step must keep exactly one winner for that region.
        text = "GST 123456789 RT 0001"
        result = _run(Detector().detect(text, pack_codes=["CA"]))
        ordered = sorted(result.spans, key=lambda s: (s.start, s.end))
        for a, b in zip(ordered, ordered[1:], strict=False):
            assert a.end <= b.start
        # Both candidate types occupy the same offsets, so only one span survives there.
        covering = [s for s in result.spans if s.start <= 4 and s.end >= 21]
        assert len(covering) == 1


class TestEmptyAndCleanInput:
    def test_empty_string_yields_no_spans(self) -> None:
        result = _run(Detector().detect("", pack_codes=ALL_PACKS))
        assert isinstance(result, DetectionResult)
        assert result.spans == []
        assert result.text == ""

    def test_clean_text_yields_no_spans(self) -> None:
        clean = "Just a normal sentence with no identifiers at all."
        result = _run(Detector().detect(clean, pack_codes=ALL_PACKS))
        assert result.spans == []

    def test_empty_pack_codes_yields_no_spans(self) -> None:
        # No packs selected -> the regex pass contributes nothing (and no optional passes).
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=[]))
        assert result.spans == []


class TestPackScoping:
    def test_only_selected_packs_run(self) -> None:
        # Only the US pack is selected, so the IBAN / SIN / etc. are not detected even
        # though they appear in the text.
        result = _run(Detector().detect(MIXED_TEXT, pack_codes=["US"]))
        found = {s.entity_type for s in result.spans}
        assert found <= {"SSN", "EIN", "ITIN"}
        assert "IBAN" not in found
        assert "SIN" not in found

    def test_returns_entityspans(self) -> None:
        result = _run(Detector().detect("SSN 123-45-6789", pack_codes=["US"]))
        assert all(isinstance(s, EntitySpan) for s in result.spans)
        assert result.spans  # non-empty for a valid SSN
