"""Span overlap resolution — ``merge_spans``.

The detector produces a flat, possibly-overlapping list of spans across regex/Presidio/LLM
passes. ``merge_spans`` must collapse each overlapping cluster to a single winner chosen by
``(score desc, length desc, source priority)`` and return a start-sorted, non-overlapping
list. These tests pin that contract and the post-conditions.
"""

from __future__ import annotations

from app.redaction.merge import merge_spans
from app.schemas.entities import EntitySpan


def span(start, end, *, etype="X", score=1.0, source="regex"):
    return EntitySpan(start, end, etype, "x" * (end - start), score=score, source=source)


def _assert_non_overlapping_sorted(out):
    # start-sorted
    starts = [s.start for s in out]
    assert starts == sorted(starts)
    # pairwise non-overlapping
    for a, b in zip(out, out[1:], strict=False):
        assert a.end <= b.start, f"{(a.start, a.end)} overlaps {(b.start, b.end)}"


# ── trivial / structural ───────────────────────────────────────────────────────


def test_empty_input_returns_empty():
    assert merge_spans([]) == []


def test_single_span_passthrough():
    s = span(0, 5)
    out = merge_spans([s])
    assert out == [s]


def test_disjoint_spans_all_preserved_and_sorted():
    spans = [span(20, 25), span(0, 5), span(10, 15)]
    out = merge_spans(spans)
    assert [(s.start, s.end) for s in out] == [(0, 5), (10, 15), (20, 25)]
    _assert_non_overlapping_sorted(out)


def test_adjacent_touching_spans_are_not_overlapping():
    # end == next.start means they touch but do not overlap (slice semantics).
    out = merge_spans([span(0, 5), span(5, 10)])
    assert len(out) == 2
    _assert_non_overlapping_sorted(out)


# ── overlap resolution by score ────────────────────────────────────────────────


def test_overlap_resolved_by_higher_score():
    low = span(0, 10, score=0.4)
    high = span(5, 15, score=0.9)
    out = merge_spans([low, high])
    assert len(out) == 1
    assert out[0] is high


def test_overlap_resolved_by_higher_score_regardless_of_input_order():
    high = span(5, 15, score=0.9)
    low = span(0, 10, score=0.4)
    out_a = merge_spans([low, high])
    out_b = merge_spans([high, low])
    assert out_a == out_b == [high]


# ── overlap resolution by length (tie on score) ────────────────────────────────


def test_overlap_tie_score_resolved_by_longer_span():
    short = span(0, 4, score=0.8)
    longer = span(2, 12, score=0.8)
    out = merge_spans([short, longer])
    assert out == [longer]


# ── overlap resolution by source priority (tie on score+length) ────────────────


def test_overlap_tie_score_and_length_resolved_by_source_priority():
    # same window, same score, same length -> regex (3) beats presidio (1)
    pres = span(0, 8, score=0.7, source="presidio")
    rgx = span(0, 8, score=0.7, source="regex")
    out = merge_spans([pres, rgx])
    assert len(out) == 1
    assert out[0].source == "regex"


def test_manual_source_outranks_regex_on_full_tie():
    rgx = span(0, 6, score=0.5, source="regex")
    man = span(0, 6, score=0.5, source="manual")
    out = merge_spans([rgx, man])
    assert out[0].source == "manual"


# ── containment ────────────────────────────────────────────────────────────────


def test_contained_lower_score_span_is_dropped():
    outer = span(0, 20, score=0.9)
    inner = span(5, 10, score=0.5)
    out = merge_spans([outer, inner])
    assert out == [outer]


def test_high_score_inner_span_evicts_lower_score_outer():
    # An inner span with a strictly higher score wins even though it is shorter.
    outer = span(0, 20, score=0.3)
    inner = span(5, 10, score=0.95)
    out = merge_spans([outer, inner])
    assert out == [inner]


def test_one_dominant_span_evicts_several_smaller_overlappers():
    big = span(0, 30, score=0.99)
    smalls = [span(2, 6, score=0.4), span(8, 12, score=0.5), span(20, 28, score=0.45)]
    out = merge_spans(smalls + [big])
    assert out == [big]


# ── duplicates ─────────────────────────────────────────────────────────────────


def test_identical_position_duplicates_collapse_to_one():
    a = span(3, 9, score=0.8, source="regex")
    b = span(3, 9, score=0.8, source="regex")
    out = merge_spans([a, b])
    assert len(out) == 1


# ── mixed cluster + disjoint survivors ─────────────────────────────────────────


def test_mixed_clusters_keep_disjoint_winners():
    # Cluster 1 around [0,15): high-score 0..10 wins over 5..15.
    # Cluster 2 around [40,60): single span survives untouched.
    spans = [
        span(0, 10, score=0.9, etype="A"),
        span(5, 15, score=0.6, etype="B"),
        span(40, 60, score=0.7, etype="C"),
    ]
    out = merge_spans(spans)
    assert [(s.start, s.end, s.entity_type) for s in out] == [
        (0, 10, "A"),
        (40, 60, "C"),
    ]
    _assert_non_overlapping_sorted(out)


def test_chain_of_overlaps_collapses_to_global_best():
    # A staircase of overlapping spans; the single highest-score one should win the chain.
    spans = [
        span(0, 6, score=0.5),
        span(4, 10, score=0.95),  # best
        span(8, 14, score=0.6),
        span(12, 18, score=0.55),
    ]
    out = merge_spans(spans)
    # the winner (4,10) is kept; nothing overlapping it survives
    assert any(s.start == 4 and s.end == 10 for s in out)
    _assert_non_overlapping_sorted(out)


def test_output_is_always_non_overlapping_and_sorted_for_random_clusters():
    spans = [
        span(0, 4, score=0.2),
        span(2, 9, score=0.8),
        span(7, 11, score=0.3),
        span(11, 13, score=0.9),
        span(30, 35, score=0.4),
        span(33, 40, score=0.41),
        span(50, 51, score=1.0),
    ]
    out = merge_spans(spans)
    _assert_non_overlapping_sorted(out)
    # nothing fabricated: every surviving span was in the input
    assert all(s in spans for s in out)


def test_merge_does_not_mutate_input_list_order_semantics():
    spans = [span(5, 15, score=0.6), span(0, 10, score=0.9)]
    snapshot = list(spans)
    merge_spans(spans)
    # input list object identity/order preserved (merge sorts a copy)
    assert spans == snapshot
