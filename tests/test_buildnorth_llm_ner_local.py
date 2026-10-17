"""The LLM-NER pass reads RAW text, so it must run on a local model unless a cloud NER
provider is explicitly allowed (Build North fix, Oct 2026)."""

from __future__ import annotations

from app.config import settings
from app.redaction import llm_ner as ner_mod


class _Recorder:
    def __init__(self):
        self.calls = 0

    async def complete(self, payload):
        self.calls += 1
        return {"choices": [{"message": {"content": '{"entities":[{"type":"PERSON","text":"Ada Lovelace"}]}'}}]}


async def test_cloud_ner_provider_is_refused_by_default(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(settings, "llm_ner_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_ner_allow_cloud", False)
    monkeypatch.setattr(ner_mod, "get_provider", lambda name, s: rec)
    assert await ner_mod.llm_ner("Ada Lovelace called") == []
    assert rec.calls == 0  # raw text never left


async def test_local_ner_provider_runs(monkeypatch):
    rec = _Recorder()
    seen = {}
    monkeypatch.setattr(settings, "llm_ner_provider", "ollama")
    monkeypatch.setattr(ner_mod, "get_provider", lambda name, s: seen.setdefault("name", name) and rec)
    spans = await ner_mod.llm_ner("Ada Lovelace called")
    assert seen["name"] == "ollama"
    assert [(s.entity_type, s.text) for s in spans] == [("PERSON", "Ada Lovelace")]


async def test_cloud_ner_with_explicit_opt_in(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(settings, "llm_ner_provider", "anthropic")
    monkeypatch.setattr(settings, "llm_ner_allow_cloud", True)
    monkeypatch.setattr(ner_mod, "get_provider", lambda name, s: rec)
    assert len(await ner_mod.llm_ner("Ada Lovelace called")) == 1
    assert rec.calls == 1
