"""Detector orchestration of the optional passes (Presidio, LLM-NER).

The existing ``test_detect.py`` covers the regex-only lane. Here the optional passes are
switched on via monkeypatched settings and replaced with local fakes, so we can check the
fan-out, merge and failure-isolation behaviour without spaCy or a live provider.
"""

from __future__ import annotations

import pytest

import app.config as app_config
from app.redaction import detect as detect_mod
from app.redaction.detect import Detector
from app.schemas.entities import EntitySpan

TEXT = "Contact Ann Lee at ann@example.com please"


@pytest.fixture(autouse=True)
def _passes_off(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", False, raising=False)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", False, raising=False)


def _span(text: str, value: str, etype: str, source: str, score: float) -> EntitySpan:
    i = text.index(value)
    return EntitySpan(i, i + len(value), etype, value, score=score, source=source)


class _FakePresidio:
    def __init__(self, spans=None, available=True, exc=None):
        self.available = available
        self._spans = spans or []
        self._exc = exc
        self.calls: list[tuple[str, str]] = []

    def detect(self, text, language):
        self.calls.append((text, language))
        if self._exc:
            raise self._exc
        return list(self._spans)


async def test_llm_pass_spans_are_merged_with_regex(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", True)
    seen: list[str] = []

    async def fake_llm(text):
        seen.append(text)
        return [_span(text, "Ann Lee", "PERSON", "llm", 0.6)]

    monkeypatch.setattr(detect_mod, "_llm_ner", fake_llm)
    res = await Detector().detect(TEXT, pack_codes=["GENERIC"])
    types = {s.entity_type for s in res.spans}
    assert "PERSON" in types
    assert "EMAIL" in types  # regex pass still ran
    assert seen == [TEXT]
    starts = [s.start for s in res.spans]
    assert starts == sorted(starts)


async def test_llm_pass_not_called_when_disabled(monkeypatch):
    called = False

    async def fake_llm(text):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(detect_mod, "_llm_ner", fake_llm)
    await Detector().detect(TEXT, pack_codes=["GENERIC"])
    assert called is False


async def test_failing_llm_pass_does_not_break_detection(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", True)

    async def boom(text):
        raise RuntimeError("provider down")

    monkeypatch.setattr(detect_mod, "_llm_ner", boom)
    res = await Detector().detect(TEXT, pack_codes=["GENERIC"])
    assert {s.entity_type for s in res.spans} >= {"EMAIL"}
    assert all(s.source == "regex" for s in res.spans)


async def test_llm_pass_returning_none_contributes_nothing(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", True)

    async def none_llm(text):
        return None

    monkeypatch.setattr(detect_mod, "_llm_ner", none_llm)
    res = await Detector().detect(TEXT, pack_codes=[])
    assert res.spans == []


async def test_presidio_pass_used_when_enabled(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", True)
    fake = _FakePresidio([_span(TEXT, "Ann Lee", "PERSON", "presidio", 0.85)])
    det = Detector()
    monkeypatch.setattr(det, "_get_presidio", lambda: fake)
    res = await det.detect(TEXT, pack_codes=[], language="fr")
    assert [(s.entity_type, s.source) for s in res.spans] == [("PERSON", "presidio")]
    assert fake.calls == [(TEXT, "fr")]


async def test_presidio_unavailable_is_skipped(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", True)
    fake = _FakePresidio([_span(TEXT, "Ann Lee", "PERSON", "presidio", 0.85)], available=False)
    det = Detector()
    monkeypatch.setattr(det, "_get_presidio", lambda: fake)
    res = await det.detect(TEXT, pack_codes=[])
    assert res.spans == []
    assert fake.calls == []


async def test_presidio_exception_is_isolated(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", True)
    fake = _FakePresidio(exc=RuntimeError("spacy model missing"))
    det = Detector()
    monkeypatch.setattr(det, "_get_presidio", lambda: fake)
    res = await det.detect(TEXT, pack_codes=["GENERIC"])
    assert {s.entity_type for s in res.spans} >= {"EMAIL"}


def test_presidio_detector_is_built_once(monkeypatch):
    built: list[object] = []

    class _Counting:
        def __init__(self):
            built.append(self)

    monkeypatch.setattr(detect_mod, "PresidioDetector", _Counting)
    det = Detector()
    a = det._get_presidio()
    b = det._get_presidio()
    assert a is b
    assert len(built) == 1


async def test_overlapping_llm_and_presidio_collapse_to_one(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", True)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", True)
    fake = _FakePresidio([_span(TEXT, "Ann", "PERSON", "presidio", 0.85)])
    det = Detector()
    monkeypatch.setattr(det, "_get_presidio", lambda: fake)

    async def fake_llm(text):
        return [_span(text, "Ann Lee", "PERSON", "llm", 0.6)]

    monkeypatch.setattr(detect_mod, "_llm_ner", fake_llm)
    res = await det.detect(TEXT, pack_codes=[])
    assert len(res.spans) == 1
    for a in res.spans:
        for b in res.spans:
            assert a is b or not a.overlaps(b)


async def test_one_bad_regex_pack_does_not_sink_others(monkeypatch):
    class _Bad:
        def detect(self, text):
            raise ValueError("bad pattern")

    class _Good:
        def detect(self, text):
            return [_span(text, "Ann", "PERSON", "regex", 1.0)]

    monkeypatch.setattr(detect_mod, "_load_packs", lambda codes: [_Bad(), _Good()])
    res = await Detector().detect(TEXT, pack_codes=["X"])
    assert [s.text for s in res.spans] == ["Ann"]


async def test_regex_pass_disabled_when_loader_missing(monkeypatch):
    monkeypatch.setattr(detect_mod, "_load_packs", None)
    res = await Detector().detect(TEXT, pack_codes=["GENERIC"])
    assert res.spans == []
    assert res.text == TEXT
