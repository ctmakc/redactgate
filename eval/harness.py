"""Detection-quality and answer-fidelity benchmark harness.

Two things are measured against the bundled golden sets (``eval/golden/*.jsonl``):

* **recall / precision** of the regex-only detection path. A true positive is a detected
  span that shares an entity *type* with a labeled span and *overlaps* it positionally
  (Python-slice overlap). This path requires **no** optional dependencies — it builds the
  regex jurisdiction packs and nothing else.
* **answer_fidelity** (0..1) — only when a provider is configured *and* reachable: the
  golden ``question`` is asked once on the *raw* text and once on the *redacted -> complete
  -> reinflated* round-trip, and an LLM judge scores how faithfully the round-tripped answer
  matches the raw one. Any failure (no provider, network error, judge error) yields ``None``
  for fidelity and never fails the run.

Security: this module never logs, prints or persists a raw entity value. Only entity-type
counts and aggregate float scores cross any boundary.

Run standalone for a regex-only scorecard::

    python -m eval.harness
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.schemas.entities import EntitySpan

if TYPE_CHECKING:  # avoid importing concurrent/optional modules at module import time
    from app.gateway.base import Provider

# Directory holding the bundled golden fixtures.
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# Pack codes used for the regex-only detection pass. Mirrors settings.pack_codes but is
# kept independent so the harness is deterministic regardless of deployment config.
DEFAULT_PACK_CODES = ["GENERIC", "CA", "US", "EU", "UA", "IRCC"]

# A short, judge-shaped prompt. The judge returns a single float in [0, 1].
_JUDGE_SYSTEM = (
    "You are a strict evaluator. You are given a REFERENCE answer and a CANDIDATE answer "
    "to the same question. Reply with ONLY a JSON object {\"score\": <float 0..1>} where "
    "1.0 means the candidate conveys the same factual content as the reference and 0.0 "
    "means it is unrelated or contradictory. Do not add commentary."
)
_SCORE_RE = re.compile(r'"score"\s*:\s*([0-9]*\.?[0-9]+)')


# ── Golden loading ──────────────────────────────────────────────────────────────


def load_golden(path: str | Path) -> list[dict[str, Any]]:
    """Load a golden JSONL fixture.

    Each non-empty line is ``{"text": str, "entities": [{"type","start","end"}], "question": str}``.
    Accepts either a bare set name (``"ca"`` -> ``eval/golden/ca.jsonl``) or a full path.
    """
    p = Path(path)
    if not p.suffix:
        p = GOLDEN_DIR / f"{p.name}.jsonl"
    if not p.is_absolute() and not p.exists():
        candidate = GOLDEN_DIR / p.name
        if candidate.exists():
            p = candidate
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# ── Detection scoring ───────────────────────────────────────────────────────────


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _score_detection(
    labeled: list[dict[str, Any]], detected: list[EntitySpan]
) -> tuple[int, int, int]:
    """Return (true_positives, total_labeled, total_detected).

    A labeled span counts as found if some detected span shares its type and overlaps it.
    Each detected span may satisfy at most one labeled span (greedy, type+overlap match),
    so precision is not inflated by many detections covering one label.
    """
    used: set[int] = set()
    tp = 0
    for lab in labeled:
        lab_type = lab["type"]
        lab_start = int(lab["start"])
        lab_end = int(lab["end"])
        for i, det in enumerate(detected):
            if i in used:
                continue
            if det.entity_type != lab_type:
                continue
            if _spans_overlap(lab_start, lab_end, det.start, det.end):
                used.add(i)
                tp += 1
                break
    return tp, len(labeled), len(detected)


async def _build_detector() -> Any | None:
    """Lazily construct a regex-pack ``Detector``.

    Imports the (separately-owned) detection module only when needed, so importing this
    harness never requires it to be present. Tolerates a few plausible constructor shapes.
    Returns ``None`` if the detection module is unavailable.
    """
    try:
        from app.redaction.detect import Detector  # type: ignore[import-not-found]
    except Exception:
        return None
    for kwargs in ({"settings": settings}, {}):
        try:
            return Detector(**kwargs)
        except TypeError:
            continue
        except Exception:
            return None
    try:
        return Detector(settings)  # positional fallback
    except Exception:
        return None


async def _detect_via_detector(detector: Any, text: str) -> list[EntitySpan] | None:
    """Run a ``Detector`` regex-only pass; return spans or ``None`` on failure."""
    try:
        result = await detector.detect(text, pack_codes=DEFAULT_PACK_CODES, language="en")
    except TypeError:
        try:
            result = await detector.detect(text, pack_codes=DEFAULT_PACK_CODES)
        except Exception:
            return None
    except Exception:
        return None
    spans = getattr(result, "spans", result)
    return list(spans) if spans is not None else []


def _detect_via_packs(text: str) -> list[EntitySpan] | None:
    """Fallback regex detection straight from the jurisdiction packs.

    Used when ``app.redaction.detect.Detector`` is not importable (e.g. running the eval in
    isolation while that module is owned by another component). Returns ``None`` if the pack
    loader is also unavailable.
    """
    try:
        from app.redaction.regex_packs import load_packs  # type: ignore[import-not-found]
    except Exception:
        return None
    try:
        packs = load_packs(DEFAULT_PACK_CODES)
    except Exception:
        return None
    spans: list[EntitySpan] = []
    for pack in packs:
        try:
            spans.extend(pack.detect(text))
        except Exception:
            continue
    return spans


async def _detect(detector: Any | None, text: str) -> list[EntitySpan]:
    if detector is not None:
        spans = await _detect_via_detector(detector, text)
        if spans is not None:
            return spans
    spans = _detect_via_packs(text)
    return spans if spans is not None else []


# ── Answer fidelity (optional, provider-gated) ──────────────────────────────────


async def _try_get_provider() -> Provider | None:
    """Return the configured provider instance, importing adapters lazily, or ``None``."""
    try:
        import app.gateway  # noqa: F401  (registers adapters as a side effect)
        from app.gateway.base import get_provider
    except Exception:
        return None
    try:
        return get_provider(settings.ai_provider, settings)
    except Exception:
        return None


def _ask_payload(model: str, question: str, context: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": f"Using only this text, answer concisely.\n\nTEXT:\n{context}\n\nQUESTION: {question}",
            }
        ],
        "stream": False,
        "temperature": 0.0,
    }


async def _complete_text(provider: Provider, payload: dict[str, Any]) -> str | None:
    from app.schemas.openai import extract_completion_text

    try:
        resp = await provider.complete(payload)
    except Exception:
        return None
    return extract_completion_text(resp)


async def _judge(provider: Provider, model: str, reference: str, candidate: str) -> float | None:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {
                "role": "user",
                "content": f"REFERENCE:\n{reference}\n\nCANDIDATE:\n{candidate}",
            },
        ],
        "stream": False,
        "temperature": 0.0,
    }
    text = await _complete_text(provider, payload)
    if not text:
        return None
    m = _SCORE_RE.search(text)
    if not m:
        return None
    try:
        score = float(m.group(1))
    except ValueError:
        return None
    return max(0.0, min(1.0, score))


async def _answer_fidelity(
    rows: list[dict[str, Any]], detector: Any | None
) -> float | None:
    """Mean LLM-judged fidelity of the redact->complete->reinflate round-trip.

    Returns ``None`` on *any* unmet precondition or failure — never raises into the caller.
    """
    provider = await _try_get_provider()
    if provider is None:
        return None
    try:
        from app.redaction.store import InMemoryTokenStore
        from app.redaction.vault import Vault  # type: ignore[import-not-found]
    except Exception:
        return None

    model = ""
    try:
        model = provider.default_model() or settings.ai_provider
    except Exception:
        model = settings.ai_provider

    master = settings.key_bytes("vault_master_key")
    fpk = settings.key_bytes("fingerprint_hmac_key")

    scores: list[float] = []
    for row in rows:
        text = row["text"]
        question = row["question"]
        try:
            store = InMemoryTokenStore()
            vault = Vault(store, master_key=master, fingerprint_key=fpk)
            session_id = await store.create_session()
            spans = await _detect(detector, text)
            redacted = await vault.tokenize(text, spans, session_id=session_id)

            raw_answer = await _complete_text(provider, _ask_payload(model, question, text))
            red_answer = await _complete_text(
                provider, _ask_payload(model, question, redacted)
            )
            if raw_answer is None or red_answer is None:
                continue
            reinflated = await vault.detokenize(red_answer, session_id=session_id)
            score = await _judge(provider, model, raw_answer, reinflated)
        except Exception:
            continue
        if score is not None:
            scores.append(score)

    if not scores:
        return None
    return sum(scores) / len(scores)


# ── Public entry point ──────────────────────────────────────────────────────────


async def run_eval(golden_set: str, provider: str) -> dict[str, Any]:
    """Evaluate the regex detection (and optionally answer fidelity) over a golden set.

    ``golden_set`` is a set name (``"ca"``) or path to a JSONL fixture. ``provider`` is the
    provider name to use for the optional fidelity pass — fidelity is attempted only when
    that provider is the configured one *and* reachable; otherwise ``answer_fidelity`` is
    ``None``. Returns ``{"recall", "precision", "answer_fidelity", "n"}``.
    """
    rows = load_golden(golden_set)
    detector = await _build_detector()

    total_tp = 0
    total_labeled = 0
    total_detected = 0
    for row in rows:
        spans = await _detect(detector, row["text"])
        tp, n_lab, n_det = _score_detection(row.get("entities", []), spans)
        total_tp += tp
        total_labeled += n_lab
        total_detected += n_det

    recall = (total_tp / total_labeled) if total_labeled else 0.0
    precision = (total_tp / total_detected) if total_detected else 0.0

    fidelity: float | None = None
    if provider and provider == settings.ai_provider:
        fidelity = await _answer_fidelity(rows, detector)

    return {
        "recall": recall,
        "precision": precision,
        "answer_fidelity": fidelity,
        "n": len(rows),
    }


# ── Standalone scorecard ────────────────────────────────────────────────────────


def _bundled_sets() -> list[str]:
    return sorted(p.stem for p in GOLDEN_DIR.glob("*.jsonl"))


def _fmt_pct(x: float | None) -> str:
    return f"{x * 100:6.2f}%" if x is not None else "    n/a"


async def _main() -> None:
    sets = _bundled_sets()
    print("RedactGate detection scorecard (regex-only, fidelity disabled)")
    print(f"packs: {', '.join(DEFAULT_PACK_CODES)}")
    print("-" * 58)
    print(f"{'set':<10}{'n':>5}{'recall':>12}{'precision':>14}{'fidelity':>12}")
    print("-" * 58)
    agg_recall: list[float] = []
    agg_prec: list[float] = []
    for name in sets:
        # provider="" => never attempts fidelity (always None) for a hermetic scorecard.
        res = await run_eval(name, provider="")
        print(
            f"{name:<10}{res['n']:>5}"
            f"{_fmt_pct(res['recall']):>12}"
            f"{_fmt_pct(res['precision']):>14}"
            f"{_fmt_pct(res['answer_fidelity']):>12}"
        )
        agg_recall.append(res["recall"])
        agg_prec.append(res["precision"])
    print("-" * 58)
    if sets:
        mr = sum(agg_recall) / len(agg_recall)
        mp = sum(agg_prec) / len(agg_prec)
        print(f"{'mean':<10}{'':>5}{_fmt_pct(mr):>12}{_fmt_pct(mp):>14}{'    n/a':>12}")


if __name__ == "__main__":
    asyncio.run(_main())
