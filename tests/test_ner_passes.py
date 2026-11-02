"""Optional NER passes (names, addresses, ...) exercised with fakes — no spaCy, no network.

Names and free-text addresses are not regex-detectable; RedactGate relies on the optional
Presidio pass or the LLM-NER pass for them. These tests pin the contract of both:
type mapping, offset handling, tolerance of failure, and that the detector merges their
spans with the regex pass so PERSON/ADDRESS values get tokenized end to end.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

import app.redaction.detect as detect_mod
import app.redaction.llm_ner as llm_mod
from app.redaction.detect import Detector
from app.redaction.presidio_ner import PresidioDetector
from app.schemas.entities import EntitySpan

TEXT = "Patient John Smith lives at 42 Wallaby Way, Sydney. Email js@example.com."


@dataclass
class _Res:
    entity_type: str
    start: int
    end: int
    score: float = 0.85


def _span_of(sub: str, etype: str, score: float = 0.85) -> _Res:
    i = TEXT.index(sub)
    return _Res(etype, i, i + len(sub), score)


class _FakeAnalyzer:
    results: list[_Res] = []
    raise_on_analyze = False

    def analyze(self, *, text: str, language: str):  # noqa: ARG002
        if self.raise_on_analyze:
            raise RuntimeError("model crashed")
        return list(self.results)


@pytest.fixture
def fake_presidio(monkeypatch):
    mod = types.ModuleType("presidio_analyzer")
    mod.AnalyzerEngine = _FakeAnalyzer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "presidio_analyzer", mod)
    _FakeAnalyzer.results = [
        _span_of("John Smith", "PERSON"),
        _span_of("42 Wallaby Way, Sydney", "LOCATION"),
        _span_of("js@example.com", "EMAIL_ADDRESS", 1.0),
        _Res("US_DRIVER_LICENSE", 0, 7),  # unmapped -> dropped
        _Res("PERSON", 5, 5),  # empty span -> dropped
    ]
    _FakeAnalyzer.raise_on_analyze = False
    return _FakeAnalyzer


def test_presidio_unavailable_degrades_to_noop(monkeypatch):
    monkeypatch.setitem(sys.modules, "presidio_analyzer", None)  # import -> ImportError
    det = PresidioDetector()
    assert det.available is False
    assert det.detect(TEXT) == []


def test_presidio_maps_types_and_drops_unknown(fake_presidio):
    det = PresidioDetector()
    assert det.available
    spans = det.detect(TEXT)
    got = {(s.entity_type, s.text) for s in spans}
    assert got == {
        ("PERSON", "John Smith"),
        ("LOCATION", "42 Wallaby Way, Sydney"),
        ("EMAIL", "js@example.com"),
    }
    assert all(s.source == "presidio" for s in spans)


def test_presidio_analyze_failure_returns_empty(fake_presidio):
    fake_presidio.raise_on_analyze = True
    assert PresidioDetector().detect(TEXT) == []


async def test_detector_merges_presidio_with_regex(fake_presidio, monkeypatch):
    monkeypatch.setattr(detect_mod.settings, "enable_presidio", True)
    res = await Detector().detect(TEXT, pack_codes=["GENERIC"])
    got = {(s.entity_type, s.text) for s in res.spans}
    assert ("PERSON", "John Smith") in got
    assert ("LOCATION", "42 Wallaby Way, Sydney") in got
    # Email found by both passes collapses to a single span (regex wins on priority/score).
    emails = [s for s in res.spans if s.text == "js@example.com"]
    assert len(emails) == 1


async def test_names_and_addresses_are_tokenized_when_ner_enabled(
    fake_presidio, monkeypatch, vault
):
    monkeypatch.setattr(detect_mod.settings, "enable_presidio", True)
    res = await Detector().detect(TEXT, pack_codes=["GENERIC"])
    out = await vault.tokenize(TEXT, res.spans, session_id="s-ner")
    for raw in ("John Smith", "Wallaby", "Sydney", "js@example.com"):
        assert raw not in out
    assert await vault.detokenize(out, session_id="s-ner") == TEXT


# ── LLM-NER ─────────────────────────────────────────────────────────────────────


def test_parse_entities_tolerates_fences_and_prose():
    raw = 'Sure!\n```json\n{"entities": [{"type": "PERSON", "text": "John Smith"}]}\n```'
    assert llm_mod._parse_entities(raw) == [{"type": "PERSON", "text": "John Smith"}]
    assert llm_mod._parse_entities('[{"type": "ADDRESS", "text": "x"}]') == [
        {"type": "ADDRESS", "text": "x"}
    ]
    assert llm_mod._parse_entities("not json at all") == []
    assert llm_mod._parse_entities(None) == []
    assert llm_mod._parse_entities('{"entities": "nope"}') == []


def test_anchor_spans_relocates_and_drops_hallucinations():
    text = "John met John Smith at 1 Main St."
    spans = llm_mod._anchor_spans(
        text,
        [
            {"type": "person", "text": "John"},
            {"type": "PERSON", "text": "John"},  # second occurrence, not the first again
            {"type": "street address", "text": "1 Main St"},
            {"type": "PERSON", "text": "Alice"},  # not in source -> dropped
            {"type": "PERSON"},  # malformed -> dropped
        ],
    )
    assert [(s.entity_type, s.start, s.end) for s in spans] == [
        ("PERSON", 0, 4),
        ("PERSON", 9, 13),
        ("STREET_ADDRESS", 23, 32),
    ]
    assert all(text[s.start : s.end] == s.text for s in spans)


class _FakeLLMProvider:
    def __init__(self, content=None, exc=None):
        self.content = content
        self.exc = exc
        self.calls: list[dict] = []

    async def complete(self, payload):
        self.calls.append(payload)
        if self.exc:
            raise self.exc
        return {"choices": [{"message": {"content": self.content}}]}


async def test_llm_ner_happy_path(monkeypatch):
    prov = _FakeLLMProvider('{"entities": [{"type": "ADDRESS", "text": "42 Wallaby Way"}]}')
    monkeypatch.setattr(llm_mod, "get_provider", lambda *_a, **_k: prov)
    spans = await llm_mod.llm_ner(TEXT)
    assert [(s.entity_type, s.text, s.source) for s in spans] == [
        ("ADDRESS", "42 Wallaby Way", "llm")
    ]
    sent = prov.calls[0]
    assert sent["temperature"] == 0.0
    assert sent["messages"][0]["role"] == "system"


@pytest.mark.parametrize(
    "prov",
    [
        _FakeLLMProvider(exc=RuntimeError("timeout")),
        _FakeLLMProvider(content="garbage"),
        _FakeLLMProvider(content=None),
    ],
)
async def test_llm_ner_never_raises(monkeypatch, prov):
    monkeypatch.setattr(llm_mod, "get_provider", lambda *_a, **_k: prov)
    assert await llm_mod.llm_ner(TEXT) == []


async def test_llm_ner_skips_blank_input(monkeypatch):
    prov = _FakeLLMProvider("{}")
    monkeypatch.setattr(llm_mod, "get_provider", lambda *_a, **_k: prov)
    assert await llm_mod.llm_ner("   ") == []
    assert prov.calls == []


async def test_detector_isolates_failing_llm_pass(monkeypatch):
    async def boom(_text):
        raise RuntimeError("provider down")

    monkeypatch.setattr(detect_mod.settings, "enable_llm_ner", True)
    monkeypatch.setattr(detect_mod, "_llm_ner", boom)
    res = await Detector().detect("SIN 193 456 787", pack_codes=["CA"])
    assert [(s.entity_type, s.text) for s in res.spans] == [("SIN", "193 456 787")]


async def test_detector_regex_beats_lower_score_llm_overlap(monkeypatch):
    async def fake(text):
        i = text.index("193")
        return [EntitySpan(i, i + 11, "NATIONAL_ID", text[i : i + 11], 0.6, "llm")]

    monkeypatch.setattr(detect_mod.settings, "enable_llm_ner", True)
    monkeypatch.setattr(detect_mod, "_llm_ner", fake)
    res = await Detector().detect("SIN 193 456 787", pack_codes=["CA"])
    assert [(s.entity_type, s.source) for s in res.spans] == [("SIN", "regex")]
