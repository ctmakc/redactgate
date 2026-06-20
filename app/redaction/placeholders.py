"""Placeholder grammar — shared by the vault (tokenize) and re-inflation (detokenize).

A placeholder looks like ``[[TYPE_hex]]`` e.g. ``[[SIN_7f3a]]`` or ``[[PERSON_0a1b2c]]``.
The grammar is fixed so the stream-safe de-tokenizer can detect a placeholder that is
split across two SSE chunks: it buffers any trailing partial that *could* still become a
placeholder (see ``MAX_PLACEHOLDER_LEN`` and ``trailing_partial_len``).
"""

from __future__ import annotations

import re

# entity types are UPPER_SNAKE; token suffix is 4–12 lowercase hex chars.
PLACEHOLDER_RE = re.compile(r"\[\[([A-Z][A-Z0-9_]*)_([0-9a-f]{4,12})\]\]")

# A placeholder can be at most this many chars (used to size the stream ring buffer).
# [[ + type(<=40) + _ + hex(<=12) + ]] -> generous upper bound.
MAX_PLACEHOLDER_LEN = 64


def make_placeholder(entity_type: str, token_hex: str) -> str:
    return f"[[{entity_type}_{token_hex}]]"


def find_placeholders(text: str) -> list[tuple[int, int, str, str]]:
    """Return [(start, end, entity_type, token_hex)] for every placeholder in text."""
    return [
        (m.start(), m.end(), m.group(1), m.group(2)) for m in PLACEHOLDER_RE.finditer(text)
    ]


# Matches a *partial* placeholder anchored at the END of a buffer — i.e. a prefix of a
# possible placeholder that is not yet complete. Used by the streaming de-tokenizer to
# decide how many trailing chars to hold back rather than emit.
_PARTIAL_TAIL_RE = re.compile(r"\[(?:\[[A-Z0-9_]*(?:_[0-9a-f]*)?\]?)?$")


def trailing_partial_len(text: str) -> int:
    """Length of a trailing substring that might be the start of a placeholder.

    Returns 0 if the buffer cannot end inside a placeholder. The streaming de-tokenizer
    emits ``text[:-n]`` and keeps ``text[-n:]`` for the next chunk (n may be 0).
    """
    if not text:
        return 0
    # Fast path: a lone trailing '[' or "[[".
    m = _PARTIAL_TAIL_RE.search(text)
    if m:
        return len(text) - m.start()
    return 0
