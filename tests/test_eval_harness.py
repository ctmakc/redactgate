"""Benchmark harness (eval/harness.py): golden loading, detection scoring, the LLM judge and
the redact → answer → re-inflate fidelity loop — all with a local fake provider."""

from __future__ import annotations

import json
import re

import pytest

from app import config as app_config
from app.schemas.entities import EntitySpan
from eval import harness as H

# ── golden sets ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", H._bundled_sets())
def test_bundled_golden_sets_are_well_formed(name):
    rows = H.load_golden(name)
    assert rows, name
    for row in rows:
        text = row["text"]
        assert isinstance(row["question"], str) and row["question"].strip()
        prev_end = -1
        for e in sorted(row["entities"], key=lambda e: e["start"]):
            assert 0 <= e["start"] < e["end"] <= len(text)
            value = text[e["start"]:e["end"]]
            assert value == value.strip() and value, (name, e)
            assert e["start"] >= prev_end, f"overlapping labels in {name}: {e}"
            prev_end = e["end"]
            assert re.fullmatch(r"[A-Z_]+", e["type"])


def test_bundled_sets_cover_three_jurisdictions():
    assert set(H._bundled_sets()) >= {"ca", "us", "eu"}


def test_load_golden_by_name_path_and_bare_filename(tmp_path, monkeypatch):
    by_name = H.load_golden("ca")
    assert H.load_golden(H.GOLDEN_DIR / "ca.jsonl") == by_name
    monkeypatch.chdir(tmp_path)  # a relative file name not found in cwd falls back to GOLDEN_DIR
    assert H.load_golden("ca.jsonl") == by_name
    f = tmp_path / "mine.jsonl"
    f.write_text('\n{"text": "a", "entities": [], "question": "q"}\n\n', encoding="utf-8")
    assert H.load_golden(f) == [{"text": "a", "entities": [], "question": "q"}]


# ── detection scoring ─────────────────────────────────────────────────────


def span(t, s, e):
    return EntitySpan(s, e, t, "x" * (e - s), 0.9, "regex")


def test_score_detection_requires_type_and_overlap():
    labeled = [{"type": "SIN", "start": 10, "end": 21}, {"type": "EMAIL", "start": 30, "end": 40}]
    detected = [span("SIN", 12, 20), span("PHONE", 30, 40), span("EMAIL", 41, 50)]
    assert H._score_detection(labeled, detected) == (1, 2, 3)


def test_score_detection_one_detection_cannot_satisfy_two_labels():
    labeled = [{"type": "PERSON", "start": 0, "end": 4}, {"type": "PERSON", "start": 5, "end": 8}]
    detected = [span("PERSON", 0, 8)]
    assert H._score_detection(labeled, detected) == (1, 2, 1)


def test_score_detection_touching_spans_do_not_overlap():
    assert H._score_detection([{"type": "SSN", "start": 0, "end": 5}], [span("SSN", 5, 9)]) == (0, 1, 1)


def test_score_detection_empty():
    assert H._score_detection([], []) == (0, 0, 0)


async def test_run_eval_regex_only_is_hermetic(monkeypatch):
    async def no_provider():  # pragma: no cover - must not be reached with provider=""
        raise AssertionError("fidelity must not run")

    monkeypatch.setattr(H, "_try_get_provider", no_provider)
    res = await H.run_eval("us", provider="")
    assert res["n"] == len(H.load_golden("us"))
    assert 0.5 < res["recall"] <= 1.0 and 0.5 < res["precision"] <= 1.0
    assert res["answer_fidelity"] is None


async def test_run_eval_falls_back_to_packs_without_detector(monkeypatch, tmp_path):
    async def none():
        return None

    monkeypatch.setattr(H, "_build_detector", none)
    f = tmp_path / "g.jsonl"
    text = "SSN 123-45-6789 on file."
    f.write_text(json.dumps({"text": text, "entities": [{"type": "SSN", "start": 4, "end": 15}],
                             "question": "q"}), encoding="utf-8")
    res = await H.run_eval(str(f), provider="")
    assert res == {"recall": 1.0, "precision": 1.0, "answer_fidelity": None, "n": 1}


async def test_run_eval_with_nothing_labeled_or_detected(tmp_path):
    f = tmp_path / "g.jsonl"
    f.write_text(json.dumps({"text": "nothing sensitive", "entities": [], "question": "q"}), encoding="utf-8")
    res = await H.run_eval(str(f), provider="")
    assert res["recall"] == 0.0 and res["precision"] == 0.0


async def test_run_eval_only_attempts_fidelity_for_the_configured_provider(monkeypatch):
    calls = []

    async def fake_fidelity(rows, detector):
        calls.append(len(rows))
        return 0.5

    monkeypatch.setattr(H, "_answer_fidelity", fake_fidelity)
    monkeypatch.setattr(app_config.settings, "ai_provider", "ollama", raising=False)
    assert (await H.run_eval("ca", provider="openai"))["answer_fidelity"] is None
    assert (await H.run_eval("ca", provider="ollama"))["answer_fidelity"] == 0.5
    assert calls == [len(H.load_golden("ca"))]


# ── judge + fidelity with a fake provider ─────────────────────────────────


class EchoProvider:
    """Answers questions by echoing the TEXT it was given; judges by exact equality."""

    def __init__(self, judge_reply=None, fail_on=None):
        self.judge_reply = judge_reply
        self.fail_on = fail_on
        self.seen: list[str] = []

    def default_model(self):
        return "fake-model"

    async def complete(self, payload):
        msgs = payload["messages"]
        if msgs[0]["role"] == "system":  # judge call
            ref, cand = re.match(r"REFERENCE:\n(.*)\n\nCANDIDATE:\n(.*)", msgs[1]["content"], re.S).groups()
            reply = self.judge_reply or json.dumps({"score": 1.0 if ref == cand else 0.0})
            return {"choices": [{"message": {"content": reply}}]}
        content = msgs[0]["content"]
        self.seen.append(content)
        if self.fail_on and self.fail_on in content:
            raise RuntimeError("upstream down")
        context = content.split("TEXT:\n", 1)[1].split("\n\nQUESTION:", 1)[0]
        return {"choices": [{"message": {"content": f"Answer based on: {context}"}}]}


@pytest.mark.parametrize(
    "reply,score",
    [('{"score": 0.75}', 0.75), ('Result -> {"score":1}', 1.0), ('{"score": 7.5}', 1.0),
     ('{"score": .5}', 0.5), ("no score", None), ("", None)],
)
async def test_judge_parses_and_clamps(reply, score):
    assert await H._judge(EchoProvider(judge_reply=reply or " "), "m", "a", "b") == score


async def test_answer_fidelity_round_trip_is_perfect_with_echo_provider(monkeypatch):
    p = EchoProvider()

    async def get():
        return p

    monkeypatch.setattr(H, "_try_get_provider", get)
    rows = H.load_golden("ca")[:3]
    detector = await H._build_detector()
    assert await H._answer_fidelity(rows, detector) == 1.0
    # the redacted prompts never carried the labelled raw values
    redacted = p.seen[1::2]
    for row, prompt in zip(rows, redacted, strict=True):
        for e in row["entities"]:
            if e["type"] in ("SIN", "SSN", "EMAIL"):
                assert row["text"][e["start"]:e["end"]] not in prompt
        assert "[[" in prompt


async def test_answer_fidelity_skips_failed_rows_and_returns_none_when_nothing_scored(monkeypatch):
    rows = [{"text": "SSN 123-45-6789", "question": "q"}, {"text": "nothing", "question": "q"}]
    p = EchoProvider(fail_on="TEXT:\n")

    async def get():
        return p

    monkeypatch.setattr(H, "_try_get_provider", get)
    assert await H._answer_fidelity(rows, None) is None


async def test_answer_fidelity_without_provider_is_none(monkeypatch):
    async def none():
        return None

    monkeypatch.setattr(H, "_try_get_provider", none)
    assert await H._answer_fidelity([{"text": "t", "question": "q"}], None) is None


async def test_try_get_provider_unknown_name_is_none(monkeypatch):
    monkeypatch.setattr(app_config.settings, "ai_provider", "definitely-not-registered", raising=False)
    assert await H._try_get_provider() is None


async def test_detect_via_detector_falls_back_on_errors():
    class Broken:
        async def detect(self, text, **kw):
            raise RuntimeError("x")

    assert await H._detect_via_detector(Broken(), "t") is None
    spans = await H._detect(Broken(), "SSN 123-45-6789")
    assert [s.entity_type for s in spans] == ["SSN"]


def test_ask_payload_is_deterministic_and_non_streaming():
    p = H._ask_payload("m", "Who?", "ctx")
    assert p["stream"] is False and p["temperature"] == 0.0 and p["model"] == "m"
    assert "TEXT:\nctx" in p["messages"][0]["content"] and p["messages"][0]["content"].endswith("QUESTION: Who?")


def test_fmt_pct():
    assert H._fmt_pct(0.5).strip() == "50.00%"
    assert H._fmt_pct(None).strip() == "n/a"


async def test_main_scorecard_prints_every_set_without_values(capsys):
    await H._main()
    out = capsys.readouterr().out
    for name in H._bundled_sets():
        assert re.search(rf"^{name}\s+\d+", out, re.M)
    assert "mean" in out
    for row in H.load_golden("ca"):
        for e in row["entities"]:
            assert row["text"][e["start"]:e["end"]] not in out
