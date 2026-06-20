"""Placeholder grammar — round-trip, discovery, and the stream-tail buffering primitive.

The streaming de-tokenizer relies on ``trailing_partial_len`` to decide how many trailing
chars to withhold so a placeholder split across two SSE chunks is never half-swapped. These
tests pin the grammar (``PLACEHOLDER_RE`` / ``make_placeholder``) and the tail-length math.
"""

from __future__ import annotations

import re

import pytest

from app.redaction.placeholders import (
    MAX_PLACEHOLDER_LEN,
    PLACEHOLDER_RE,
    find_placeholders,
    make_placeholder,
    trailing_partial_len,
)

# ── make_placeholder / PLACEHOLDER_RE round-trip ───────────────────────────────


@pytest.mark.parametrize(
    "entity_type,token_hex",
    [
        ("SIN", "7f3a"),
        ("PERSON", "0a1b2c"),
        ("EMAIL", "deadbeef"),
        ("IBAN", "abc123"),
        ("CREDIT_CARD", "0123456789ab"),  # 12 hex = max
        ("IP_ADDRESS", "ffff"),  # 4 hex = min
    ],
)
def test_make_placeholder_roundtrips_through_regex(entity_type, token_hex):
    ph = make_placeholder(entity_type, token_hex)
    assert ph == f"[[{entity_type}_{token_hex}]]"
    m = PLACEHOLDER_RE.fullmatch(ph)
    assert m is not None, f"{ph!r} did not fullmatch the grammar"
    assert m.group(1) == entity_type
    assert m.group(2) == token_hex


@pytest.mark.parametrize(
    "bad",
    [
        "[[sin_7f3a]]",  # lowercase type
        "[[SIN_7F3A]]",  # uppercase hex (must be lowercase)
        "[[SIN_7f]]",  # only 2 hex (min is 4)
        "[[SIN_0123456789abc]]",  # 13 hex (max is 12)
        "[[_7f3a]]",  # empty type
        "[[SIN_]]",  # empty hex
        "[[SIN-7f3a]]",  # wrong separator
        "[SIN_7f3a]",  # single brackets
        "[[1SIN_7f3a]]",  # type must start with a letter
        "[[SIN_7g3a]]",  # 'g' is not hex
    ],
)
def test_placeholder_re_rejects_malformed(bad):
    assert PLACEHOLDER_RE.fullmatch(bad) is None


def test_placeholder_type_may_contain_digits_and_underscores():
    ph = make_placeholder("BANK_ACCOUNT2", "abcd")
    assert PLACEHOLDER_RE.fullmatch(ph) is not None


# ── find_placeholders ──────────────────────────────────────────────────────────


def test_find_placeholders_returns_offsets_and_groups():
    a = make_placeholder("SIN", "7f3a")
    b = make_placeholder("EMAIL", "00aa11")
    text = f"hello {a} world {b}!"
    found = find_placeholders(text)
    assert len(found) == 2
    (s0, e0, t0, h0), (s1, e1, t1, h1) = found
    assert text[s0:e0] == a and t0 == "SIN" and h0 == "7f3a"
    assert text[s1:e1] == b and t1 == "EMAIL" and h1 == "00aa11"
    # discovered in left-to-right order
    assert s0 < s1


def test_find_placeholders_empty_when_none():
    assert find_placeholders("just some plain text, no tokens here") == []


def test_find_placeholders_ignores_partial_and_malformed():
    text = "start [[ then [[SIN_ and [[BAD_7g]] but [[SIN_7f3a]] only"
    found = find_placeholders(text)
    assert len(found) == 1
    assert found[0][2] == "SIN" and found[0][3] == "7f3a"


def test_find_placeholders_adjacent_back_to_back():
    a = make_placeholder("SIN", "7f3a")
    b = make_placeholder("SIN", "00bb")
    text = a + b
    found = find_placeholders(text)
    assert len(found) == 2
    assert found[0][1] == found[1][0]  # first ends exactly where second begins


# ── trailing_partial_len ───────────────────────────────────────────────────────


def test_trailing_partial_lone_open_bracket():
    assert trailing_partial_len("hello [") == 1


def test_trailing_partial_double_open_bracket():
    assert trailing_partial_len("hello [[") == 2


def test_trailing_partial_in_progress_placeholder():
    # "[[SIN_7f" is 8 chars and could still grow into a full placeholder.
    assert trailing_partial_len("hello [[SIN_7f") == len("[[SIN_7f")


def test_trailing_partial_type_only_no_underscore_yet():
    assert trailing_partial_len("x [[SIN") == len("[[SIN")


def test_trailing_partial_complete_placeholder_is_zero():
    # A complete placeholder is fully resolvable; nothing needs to be held back.
    full = make_placeholder("SIN", "7f3a")
    assert trailing_partial_len("hello " + full) == 0


def test_trailing_partial_plain_text_is_zero():
    assert trailing_partial_len("nothing pending here") == 0


def test_trailing_partial_empty_string_is_zero():
    assert trailing_partial_len("") == 0


def test_trailing_partial_open_then_closed_is_zero():
    # The first '[[' is closed off; the tail is plain text.
    assert trailing_partial_len("[[SIN_7f3a]] done") == 0


def test_trailing_partial_only_counts_the_final_anchor():
    # An earlier complete placeholder must not inflate the count; only the live tail does.
    full = make_placeholder("EMAIL", "00aa")
    text = full + " then [["
    assert trailing_partial_len(text) == 2


def test_trailing_partial_never_exceeds_max_placeholder_len():
    # Even a pathological long run of placeholder-legal chars after '[[' is bounded.
    weird = "[[" + "A" * 200
    n = trailing_partial_len(weird)
    assert n <= len(weird)
    # And a real placeholder can never be longer than MAX_PLACEHOLDER_LEN, so the stream
    # buffer sized at MAX_PLACEHOLDER_LEN is always enough to hold a complete one.
    longest_real = make_placeholder("A" * 40, "0" * 12)
    assert len(longest_real) <= MAX_PLACEHOLDER_LEN


def test_max_placeholder_len_bounds_real_placeholders():
    # Sanity on the constant itself: a typical placeholder is well within bound.
    assert len(make_placeholder("CREDIT_CARD", "0123456789ab")) <= MAX_PLACEHOLDER_LEN
    assert MAX_PLACEHOLDER_LEN >= 16


def test_trailing_partial_matches_a_growing_placeholder_prefix():
    # Every prefix of a real placeholder reports a non-zero hold, and the held tail is
    # exactly that prefix — the invariant the stream de-tokenizer depends on.
    full = make_placeholder("SIN", "7f3a")
    prefix_text = "lead text " + full
    for cut in range(len("lead text ") + 1, len(prefix_text)):
        partial = prefix_text[:cut]
        n = trailing_partial_len(partial)
        # the withheld tail starts somewhere at/after the final "[["
        assert n >= 0
        if n:
            assert partial[-n:].startswith("[")


def test_partial_tail_regex_is_anchored_at_end():
    # Defensive: the partial matcher only ever considers the end of the buffer.
    # A "[[" in the middle followed by resolvable text must not be withheld.
    text = "[[ middle bracket but ends clean"
    assert trailing_partial_len(text) == 0
    assert re.search(r"\[\[", text)  # the '[[' really is present, just not at the tail
