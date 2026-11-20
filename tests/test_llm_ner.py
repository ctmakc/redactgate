"""LLM-NER pass (app.redaction.llm_ner) and the optional passes of the Detector.

The provider is a local fake object — nothing is sent anywhere.
"""

from __future__ import annotations

import json

import pytest

from app import config as app_config
from app.redaction import detect as detect_module
from app.redaction import llm_ner as M
from app.schemas.entities import EntitySpan


class FakeProvider:
    def __init__(self, content=None, *, raise_exc: Exception | None = None, raw=None):
        self.content = content
        self.raise_exc = raise_exc
        self.raw = raw
        self.payloads: list[dict] = []

    async def complete(self, payload):
        self.payloads.append(payload)
        if self.raise_exc:
            raise self.raise_exc
        if self.raw is not None:
            return self.raw
        return {"choices": [{"message": {"role": "assistant", "content": self.content}}]}


@pytest.fixture()
def provider(monkeypatch):
    holder = {}

    def install(p):
        holder["p"] = p
        monkeypatch.setattr(M, "get_provider", lambda name, settings: p)
        return p

    return install


# ── parsing ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        '{"entities": [{"type": "PERSON", "text": "Jane Roe"}]}',
        '```json\n{"entities": [{"type": "PERSON", "text": "Jane Roe"}]}\n```',
        '```\n{"entities": [{"type": "PERSON", "text": "Jane Roe"}]}\n```',
        'Sure! Here you go: {"entities": [{"type": "PERSON", "text": "Jane Roe"}]} Hope it helps.',
        '[{"type": "PERSON", "text": "Jane Roe"}]',
        '{"results": [{"type": "PERSON", "text": "Jane Roe"}]}',
    ],
)
def test_parse_entities_tolerates_common_reply_shapes(raw):
    assert M._parse_entities(raw) == [{"type": "PERSON", "text": "Jane Roe"}]


@pytest.mark.parametrize("raw", [None, "", "no json here", "{broken", '"just a string"', "42",
                                 '{"entities": null}', '{"other": 1}'])
def test_parse_entities_returns_empty_on_garbage(raw):
    assert M._parse_entities(raw) == []


def test_parse_entities_drops_non_dict_items():
    raw = json.dumps({"entities": ["Jane", 3, None, {"type": "EMAIL", "text": "a@b.co"}]})
    assert M._parse_entities(raw) == [{"type": "EMAIL", "text": "a@b.co"}]


# ── anchoring ──────────────────────────────────────────────────────────────


def test_anchor_spans_relocates_text_and_normalises_type():
    text = "Call Jane Roe at the Main St office."
    spans = M._anchor_spans(text, [{"type": "person", "text": "Jane Roe"},
                                   {"entity_type": "street address", "text": "Main St"}])
    assert [(s.entity_type, text[s.start:s.end], s.source) for s in spans] == [
        ("PERSON", "Jane Roe", "llm"), ("STREET_ADDRESS", "Main St", "llm")]
    assert all(s.score == 0.6 for s in spans)


def test_anchor_spans_repeated_value_claims_successive_occurrences():
    text = "Jane met Jane."
    spans = M._anchor_spans(text, [{"type": "PERSON", "text": "Jane"}] * 3)
    assert [(s.start, s.end) for s in spans] == [(0, 4), (9, 13)]  # third has nowhere left to go


def test_anchor_spans_skips_hallucinated_and_malformed_items():
    text = "Only Jane here."
    items = [{"type": "PERSON", "text": "Bob"}, {"type": "PERSON", "text": ""}, {"type": "PERSON"},
             {"type": "  ", "text": "Jane"}, {"type": 5, "text": "Jane"}, {"text": "Jane"}]
    assert M._anchor_spans(text, items) == []


def test_anchor_spans_does_not_overlap_an_earlier_claim():
    text = "Jane Roe"
    spans = M._anchor_spans(text, [{"type": "PERSON", "text": "Jane Roe"}, {"type": "PERSON", "text": "Roe"}])
    assert [(s.start, s.end) for s in spans] == [(0, 8)]


# ── full pass with a fake provider ─────────────────────────────────────────


async def test_llm_ner_happy_path_and_payload_shape(provider):
    p = provider(FakeProvider('{"entities": [{"type": "PERSON", "text": "Jane Roe"}]}'))
    spans = await M.llm_ner("Patient Jane Roe called.")
    assert [(s.entity_type, s.start, s.end) for s in spans] == [("PERSON", 8, 16)]
    sent = p.payloads[0]
    assert sent["temperature"] == 0.0 and sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0]["role"] == "system" and "JSON" in sent["messages"][0]["content"]
    assert sent["messages"][1] == {"role": "user", "content": "Patient Jane Roe called."}


async def test_llm_ner_truncates_huge_input(provider):
    p = provider(FakeProvider('{"entities": []}'))
    await M.llm_ner("x" * (M._MAX_INPUT_CHARS + 500))
    assert len(p.payloads[0]["messages"][1]["content"]) == M._MAX_INPUT_CHARS


async def test_llm_ner_blank_input_does_not_call_provider(provider):
    p = provider(FakeProvider("{}"))
    assert await M.llm_ner("   ") == []
    assert p.payloads == []


@pytest.mark.parametrize(
    "fake",
    [
        FakeProvider(raise_exc=RuntimeError("upstream down")),
        FakeProvider(raw={"choices": []}),
        FakeProvider(raw={"unexpected": True}),
        FakeProvider(content=None),
        FakeProvider(content=[{"type": "text"}]),
        FakeProvider(content="not json"),
    ],
)
async def test_llm_ner_never_raises(provider, fake):
    provider(fake)
    assert await M.llm_ner("Jane Roe") == []


async def test_llm_ner_unknown_provider_is_empty(monkeypatch):
    monkeypatch.setattr(app_config.settings, "ai_provider", "definitely-not-registered", raising=False)
    assert await M.llm_ner("Jane Roe") == []


# ── Detector optional passes ───────────────────────────────────────────────


async def test_detector_merges_llm_pass_with_regex(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", False, raising=False)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", True, raising=False)

    async def fake_llm(text):
        i = text.index("Jane Roe")
        return [EntitySpan(i, i + 8, "PERSON", "Jane Roe", 0.6, "llm")]

    monkeypatch.setattr(detect_module, "_llm_ner", fake_llm)
    text = "Jane Roe, SSN 123-45-6789."
    res = await detect_module.Detector().detect(text, pack_codes=["US"])
    assert {(s.entity_type, text[s.start:s.end]) for s in res.spans} == {
        ("PERSON", "Jane Roe"), ("SSN", "123-45-6789")}


async def test_detector_survives_failing_optional_passes(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", True, raising=False)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", True, raising=False)

    async def boom(text):
        raise RuntimeError("llm exploded")

    class BrokenPresidio:
        available = True

        def detect(self, text, language):
            raise RuntimeError("presidio exploded")

    monkeypatch.setattr(detect_module, "_llm_ner", boom)
    d = detect_module.Detector()
    d._presidio = BrokenPresidio()
    res = await d.detect("SSN 123-45-6789", pack_codes=["US"])
    assert [s.entity_type for s in res.spans] == ["SSN"]


async def test_detector_skips_unavailable_presidio(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", True, raising=False)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", False, raising=False)

    class Unavailable:
        available = False

        def detect(self, text, language):  # pragma: no cover - must not be called
            raise AssertionError("called")

    d = detect_module.Detector()
    d._presidio = Unavailable()
    res = await d.detect("nothing here", pack_codes=["US"])
    assert res.spans == []


async def test_detector_presidio_spans_are_merged(monkeypatch):
    monkeypatch.setattr(app_config.settings, "enable_presidio", True, raising=False)
    monkeypatch.setattr(app_config.settings, "enable_llm_ner", False, raising=False)
    seen = {}

    class FakePresidio:
        available = True

        def detect(self, text, language):
            seen["language"] = language
            return [EntitySpan(0, 4, "PERSON", "Jane", 0.85, "presidio")]

    d = detect_module.Detector()
    d._presidio = FakePresidio()
    res = await d.detect("Jane wrote.", pack_codes=[], language="fr")
    assert [(s.entity_type, s.source) for s in res.spans] == [("PERSON", "presidio")]
    assert seen["language"] == "fr"


async def test_detector_empty_text_and_no_packs():
    d = detect_module.Detector()
    assert (await d.detect("", pack_codes=["US"])).spans == []
    assert (await d.detect("SSN 123-45-6789", pack_codes=[])).spans == []
