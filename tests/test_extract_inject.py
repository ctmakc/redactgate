"""Text walk: ``extract_texts`` / ``inject_texts``.

These two functions are contract-critical: spans computed over ``extract_texts(payload)`` are
re-applied in the SAME order by ``inject_texts``. The walk covers string content, OpenAI
"parts" content, and the /v1/responses ``input`` field, and must never mutate the caller's
payload.
"""

from __future__ import annotations

import copy

import pytest

from app.schemas.openai import extract_texts, inject_texts

# ── string content ─────────────────────────────────────────────────────────────


def test_extract_string_content_in_order():
    payload = {
        "model": "gpt-x",
        "messages": [
            {"role": "system", "content": "be helpful"},
            {"role": "user", "content": "hello there"},
        ],
    }
    assert extract_texts(payload) == ["be helpful", "hello there"]


def test_inject_string_content_replaces_in_walk_order():
    payload = {
        "model": "gpt-x",
        "messages": [
            {"role": "system", "content": "A"},
            {"role": "user", "content": "B"},
        ],
    }
    out = inject_texts(payload, ["A!", "B!"])
    assert [m["content"] for m in out["messages"]] == ["A!", "B!"]


def test_roundtrip_extract_then_inject_identity():
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
        ],
    }
    texts = extract_texts(payload)
    out = inject_texts(payload, texts)
    assert out == payload
    assert out is not payload


# ── parts content (vision / typed parts) ───────────────────────────────────────


def test_extract_text_parts_only_text_kinds():
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
                    {"type": "input_text", "text": "and this too"},
                ],
            }
        ],
    }
    assert extract_texts(payload) == ["describe this", "and this too"]


def test_inject_text_parts_preserves_non_text_parts():
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "a"},
                    {"type": "image_url", "image_url": {"url": "u"}},
                    {"type": "input_text", "text": "b"},
                ],
            }
        ],
    }
    out = inject_texts(payload, ["A", "B"])
    parts = out["messages"][0]["content"]
    assert parts[0]["text"] == "A"
    assert parts[1] == {"type": "image_url", "image_url": {"url": "u"}}
    assert parts[2]["text"] == "B"


def test_mixed_string_and_parts_walk_order():
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "sys"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "p1"},
                    {"type": "text", "text": "p2"},
                ],
            },
            {"role": "user", "content": "tail"},
        ],
    }
    assert extract_texts(payload) == ["sys", "p1", "p2", "tail"]
    out = inject_texts(payload, ["SYS", "P1", "P2", "TAIL"])
    assert out["messages"][0]["content"] == "SYS"
    assert [p["text"] for p in out["messages"][1]["content"]] == ["P1", "P2"]
    assert out["messages"][2]["content"] == "TAIL"


# ── /v1/responses input field ──────────────────────────────────────────────────


def test_extract_responses_string_input():
    payload = {"model": "m", "input": "redact me"}
    assert extract_texts(payload) == ["redact me"]


def test_extract_responses_list_input():
    payload = {
        "model": "m",
        "input": [
            {"type": "input_text", "text": "first"},
            {"type": "input_image", "image_url": "x"},
            {"type": "input_text", "text": "second"},
        ],
    }
    assert extract_texts(payload) == ["first", "second"]


def test_inject_responses_string_input():
    payload = {"model": "m", "input": "secret"}
    out = inject_texts(payload, ["REDACTED"])
    assert out["input"] == "REDACTED"


def test_inject_responses_list_input():
    payload = {
        "model": "m",
        "input": [
            {"type": "input_text", "text": "a"},
            {"type": "input_image", "image_url": "x"},
            {"type": "input_text", "text": "b"},
        ],
    }
    out = inject_texts(payload, ["A", "B"])
    assert out["input"][0]["text"] == "A"
    assert out["input"][1] == {"type": "input_image", "image_url": "x"}
    assert out["input"][2]["text"] == "B"


def test_messages_and_responses_input_both_walked():
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "msg"}],
        "input": "inp",
    }
    assert extract_texts(payload) == ["msg", "inp"]
    out = inject_texts(payload, ["MSG", "INP"])
    assert out["messages"][0]["content"] == "MSG"
    assert out["input"] == "INP"


# ── length-mismatch raises ─────────────────────────────────────────────────────


def test_inject_too_few_texts_raises():
    payload = {"model": "m", "messages": [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}]}
    with pytest.raises(ValueError):
        inject_texts(payload, ["only-one"])


def test_inject_too_many_texts_raises():
    payload = {"model": "m", "messages": [{"role": "user", "content": "a"}]}
    with pytest.raises(ValueError):
        inject_texts(payload, ["a", "extra"])


def test_inject_mismatch_message_mentions_counts():
    payload = {"model": "m", "messages": [{"role": "user", "content": "a"}]}
    with pytest.raises(ValueError, match="length mismatch"):
        inject_texts(payload, [])


# ── original payload not mutated ───────────────────────────────────────────────


def test_inject_does_not_mutate_original_string_content():
    payload = {"model": "m", "messages": [{"role": "user", "content": "orig"}]}
    snapshot = copy.deepcopy(payload)
    inject_texts(payload, ["changed"])
    assert payload == snapshot


def test_inject_does_not_mutate_original_parts_content():
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "orig"}]},
        ],
    }
    snapshot = copy.deepcopy(payload)
    out = inject_texts(payload, ["changed"])
    assert payload == snapshot  # caller's nested list untouched
    assert out["messages"][0]["content"][0]["text"] == "changed"


def test_inject_does_not_mutate_original_responses_input_list():
    payload = {
        "model": "m",
        "input": [{"type": "input_text", "text": "orig"}],
    }
    snapshot = copy.deepcopy(payload)
    inject_texts(payload, ["changed"])
    assert payload == snapshot


# ── edge cases ─────────────────────────────────────────────────────────────────


def test_extract_empty_payload_is_empty():
    assert extract_texts({"model": "m"}) == []
    assert extract_texts({"model": "m", "messages": []}) == []


def test_extract_skips_none_content():
    payload = {
        "model": "m",
        "messages": [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "t"}]},
            {"role": "user", "content": "real"},
        ],
    }
    assert extract_texts(payload) == ["real"]


def test_inject_into_empty_is_noop_copy():
    payload = {"model": "m", "messages": []}
    out = inject_texts(payload, [])
    assert out == payload and out is not payload


def test_part_without_text_key_is_skipped():
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text"},  # malformed: no 'text' value
                    {"type": "text", "text": "kept"},
                ],
            }
        ],
    }
    assert extract_texts(payload) == ["kept"]
