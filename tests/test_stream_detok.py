"""Stream-safe de-tokenization.

``StreamDetokenizer`` re-inflates an SSE token stream chunk-by-chunk. Its invariant:

    "".join(push(c) for c in chunks) + flush() == vault.detokenize(full_text)

regardless of how the full text is chopped — including a chunk boundary that lands EXACTLY
inside a placeholder. These tests drive a redacted text through several chunk sizes and a
worst-case split, asserting the invariant each time.
"""

from __future__ import annotations

import pytest

from app.redaction.placeholders import PLACEHOLDER_RE
from app.schemas.entities import EntitySpan

SESSION = "stream-sess"


def spans_for_all(text, sub, etype):
    out = []
    start = 0
    while True:
        i = text.find(sub, start)
        if i == -1:
            break
        out.append(EntitySpan(i, i + len(sub), etype, sub))
        start = i + len(sub)
    return out


async def _redacted_text(vault):
    """Tokenize a multi-entity text and return (redacted, expected_detok)."""
    text = (
        "Hello John Smith, your SIN 046 454 286 and email john@x.com are noted. "
        "John Smith, please confirm john@x.com."
    )
    spans = (
        spans_for_all(text, "John Smith", "PERSON")
        + spans_for_all(text, "046 454 286", "SIN")
        + spans_for_all(text, "john@x.com", "EMAIL")
    )
    redacted = await vault.tokenize(text, spans, session_id=SESSION)
    assert "[[" in redacted  # there are placeholders to stream
    expected = await vault.detokenize(redacted, session_id=SESSION)
    assert expected == text  # round-trip sanity
    return redacted, expected


def _chunks(s, size):
    return [s[i : i + size] for i in range(0, len(s), size)]


async def _run_stream(vault, redacted, chunks):
    sd = vault.stream_detokenizer(SESSION)
    parts = []
    for c in chunks:
        parts.append(await sd.push(c))
    parts.append(await sd.flush())
    return "".join(parts)


@pytest.mark.parametrize("size", [1, 3, 7])
async def test_stream_matches_detokenize_for_chunk_sizes(vault, size):
    redacted, expected = await _redacted_text(vault)
    out = await _run_stream(vault, redacted, _chunks(redacted, size))
    assert out == expected


async def test_stream_single_giant_chunk(vault):
    redacted, expected = await _redacted_text(vault)
    out = await _run_stream(vault, redacted, [redacted])
    assert out == expected


async def test_stream_worst_case_split_inside_a_placeholder(vault):
    redacted, expected = await _redacted_text(vault)
    m = PLACEHOLDER_RE.search(redacted)
    assert m is not None
    # Split EXACTLY in the middle of the first placeholder.
    mid = (m.start() + m.end()) // 2
    assert m.start() < mid < m.end()
    chunks = [redacted[:mid], redacted[mid:]]
    out = await _run_stream(vault, redacted, chunks)
    assert out == expected


async def test_stream_split_right_after_double_bracket(vault):
    redacted, expected = await _redacted_text(vault)
    m = PLACEHOLDER_RE.search(redacted)
    # boundary right after the opening "[[" of a placeholder
    cut = m.start() + 2
    out = await _run_stream(vault, redacted, [redacted[:cut], redacted[cut:]])
    assert out == expected


async def test_stream_split_one_char_before_closing_brackets(vault):
    redacted, expected = await _redacted_text(vault)
    m = PLACEHOLDER_RE.search(redacted)
    cut = m.end() - 1  # just before the final ']'
    out = await _run_stream(vault, redacted, [redacted[:cut], redacted[cut:]])
    assert out == expected


async def test_stream_never_emits_partial_placeholder_chars(vault):
    # While a placeholder is mid-stream, the de-tokenizer must not leak its raw chars.
    # We feed char-by-char and assert no emitted fragment contains a stray "[[".
    redacted, expected = await _redacted_text(vault)
    sd = vault.stream_detokenizer(SESSION)
    emitted = []
    for ch in redacted:
        piece = await sd.push(ch)
        emitted.append(piece)
        # No emitted piece should ever contain a literal placeholder-opener — by the time
        # text is emitted it is either resolved or proven not to be a placeholder.
        assert "[[" not in piece
    emitted.append(await sd.flush())
    assert "".join(emitted) == expected


async def test_stream_with_trailing_partial_at_flush(vault):
    # If the stream ENDS mid-(unknown)-placeholder, flush must emit the held tail verbatim.
    sd = vault.stream_detokenizer(SESSION)
    out = await sd.push("tail text [[SIN_7f")  # never completed
    out += await sd.flush()
    assert out == "tail text [[SIN_7f"


async def test_stream_plain_text_passthrough(vault):
    sd = vault.stream_detokenizer(SESSION)
    full = "completely plain streaming text with no tokens"
    parts = [await sd.push(c) for c in _chunks(full, 5)]
    parts.append(await sd.flush())
    assert "".join(parts) == full


async def test_stream_independent_per_chunk_size_consistency(vault):
    # The output must be identical across all chunk sizes (and equal to detokenize).
    redacted, expected = await _redacted_text(vault)
    outs = []
    for size in (1, 2, 5, 11, 64):
        outs.append(await _run_stream(vault, redacted, _chunks(redacted, size)))
    assert all(o == expected for o in outs)
