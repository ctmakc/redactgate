"""Optional LLM-backed NER pass.

When ``settings.enable_llm_ner`` is set, the detector adds a pass that asks the *configured
provider itself* to extract sensitive entities as structured JSON. This catches free-text
PII (names, addresses, account references) that the regex packs and Presidio miss.

Design constraints:
  * Reuses the same gateway the proxy uses (``get_provider(settings.ai_provider, settings)``)
    so no extra credentials / SDKs are needed.
  * Fully tolerant: ANY error (no provider, bad JSON, timeout, schema drift) -> ``[]``. The
    LLM pass must never break the request path.
  * Returns offsets relative to the exact ``text`` it was given; spans whose ``text`` can be
    re-located in the source are re-anchored, and bogus offsets are dropped.

SECURITY: this module sends ``text`` to the configured provider (that is the whole point of
RedactGate's redact-before-send flow, so this pass runs on the *raw* text inside the trust
boundary). It never logs entity values locally.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.config import settings
from app.gateway.base import get_provider
from app.schemas.entities import GENERIC_TYPES, EntitySpan

# Types we ask the model to surface. Generic NER types plus a few free-text identifiers.
_REQUESTED_TYPES = sorted(
    GENERIC_TYPES
    | {
        "ADDRESS",
        "PASSPORT",
        "DRIVER_LICENSE",
        "NATIONAL_ID",
        "TAX_ID",
        "DATE_OF_BIRTH",
        "ACCOUNT_NUMBER",
    }
)

_SYSTEM_PROMPT = (
    "You are a precise PII/financial entity extractor. Given a USER text, return ONLY a "
    "JSON object of the form {\"entities\": [{\"type\": <TYPE>, \"text\": <exact substring>}]}. "
    "Use these UPPER_SNAKE types when applicable: " + ", ".join(_REQUESTED_TYPES) + ". "
    "Copy each entity's text EXACTLY as it appears in the source (same casing/punctuation). "
    "Do not invent entities, do not include offsets, do not add commentary. "
    "If there are no entities, return {\"entities\": []}."
)

# Bound the prompt so a pathological input can't blow up the upstream call.
_MAX_INPUT_CHARS = 20_000


def _build_payload(text: str) -> dict[str, Any]:
    """Construct the OpenAI-style chat request for the NER pass."""
    return {
        "model": "",  # provider falls back to its configured default model
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text[:_MAX_INPUT_CHARS]},
        ],
        "temperature": 0.0,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_entities(raw: str | None) -> list[dict[str, Any]]:
    """Extract the ``entities`` list from a model reply that should be JSON. Tolerant."""
    if not raw:
        return []
    candidate = raw.strip()
    # Strip markdown fences if the model wrapped its JSON.
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        candidate = candidate.split("\n", 1)[-1] if "\n" in candidate else candidate
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError):
        match = _JSON_OBJ_RE.search(candidate)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("entities") or data.get("results") or []
    else:
        return []
    return [it for it in items if isinstance(it, dict)]


def _anchor_spans(text: str, items: list[dict[str, Any]]) -> list[EntitySpan]:
    """Re-locate each reported entity ``text`` within the source to produce valid offsets."""
    spans: list[EntitySpan] = []
    used: list[tuple[int, int]] = []
    for item in items:
        value = item.get("text")
        etype = item.get("type") or item.get("entity_type")
        if not isinstance(value, str) or not value or not isinstance(etype, str):
            continue
        norm_type = etype.strip().upper().replace(" ", "_")
        if not norm_type:
            continue
        # Find the first occurrence not already claimed by a previous span.
        search_from = 0
        while True:
            idx = text.find(value, search_from)
            if idx == -1:
                break
            end = idx + len(value)
            if any(idx < u_end and u_start < end for u_start, u_end in used):
                search_from = idx + 1
                continue
            used.append((idx, end))
            spans.append(
                EntitySpan(
                    start=idx,
                    end=end,
                    entity_type=norm_type,
                    text=value,
                    score=0.6,
                    source="llm",
                    jurisdiction=None,
                )
            )
            break
    return spans


async def llm_ner(text: str) -> list[EntitySpan]:
    """Ask the configured provider to extract entities from ``text``. Returns [] on any error."""
    if not text or not text.strip():
        return []
    try:
        provider = get_provider(settings.ai_provider, settings)
        payload = _build_payload(text)
        result = await provider.complete(payload)
    except Exception:  # noqa: BLE001 - LLM pass must never break the request path
        return []

    try:
        raw = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return []

    items = _parse_entities(raw if isinstance(raw, str) else None)
    if not items:
        return []
    try:
        return _anchor_spans(text, items)
    except Exception:  # noqa: BLE001
        return []
