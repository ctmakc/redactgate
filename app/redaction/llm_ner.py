"""Optional LLM-backed NER pass.

When ``settings.enable_llm_ner`` is set, the detector adds a pass that asks the *configured
provider itself* to extract sensitive entities as structured JSON. This catches free-text
PII (names, addresses, account references) that the regex packs and Presidio miss.

Design constraints:
  * Reuses the gateway providers (``settings.llm_ner_provider``, falling back to
    ``settings.ai_provider``) so no extra credentials / SDKs are needed. Pointing
    ``llm_ner_provider`` at a local model (e.g. ``apertus`` on llama.cpp) keeps this
    raw-text pass on the machine even when answers come from a cloud provider.
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
    "You find personal and confidential data in client documents written in German, French, "
    "Italian or English. Return ONLY a JSON object of the form "
    "{\"entities\": [{\"type\": <TYPE>, \"text\": <exact substring>}]}. "
    "Use these UPPER_SNAKE types: PERSON (full names of people), ORG (companies, banks, "
    "authorities), ADDRESS (street and number, postcode and town), and when applicable "
    + ", ".join(t for t in _REQUESTED_TYPES if t not in {"PERSON", "ORG", "ADDRESS"})
    + ". Copy each entity's text EXACTLY as it appears in the source (same casing and "
    "punctuation). List every person, company and address in order of appearance and "
    "mention each distinct text only once. Skip "
    "anything already written as a [[TYPE_hex]] placeholder. Do not invent entities, do "
    "not include offsets, do not add commentary. "
    "If there are no entities, return {\"entities\": []}."
)

# One synthetic worked example. Small local models (Apertus 8B on llama.cpp) follow the
# output contract much more reliably with a single demonstration than with rules alone.
_EXAMPLE_USER = "Kundin: Anna Beispiel, Seestrasse 4, 3000 Bern, arbeitet bei Beispiel AG."
_EXAMPLE_ASSISTANT = json.dumps(
    {
        "entities": [
            {"type": "PERSON", "text": "Anna Beispiel"},
            {"type": "ADDRESS", "text": "Seestrasse 4, 3000 Bern"},
            {"type": "ORG", "text": "Beispiel AG"},
        ]
    },
    ensure_ascii=False,
)

# JSON schema for servers with grammar-constrained decoding (llama.cpp, vLLM). It pins the
# output to the contract above, so an 8B model cannot drift into prose.
_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            # Hard cap: an 8B model can fall into a repetition loop; the grammar then
            # closes the array instead of running into max_tokens with broken JSON.
            "maxItems": 48,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": _REQUESTED_TYPES},
                    "text": {"type": "string"},
                },
                "required": ["type", "text"],
            },
        }
    },
    "required": ["entities"],
}

# Providers whose servers honour ``response_format: json_schema`` with a grammar.
_SCHEMA_PROVIDERS = {"apertus"}

# Bound the prompt so a pathological input can't blow up the upstream call.
_MAX_INPUT_CHARS = 20_000


def _build_payload(text: str, *, structured: bool = False) -> dict[str, Any]:
    """Construct the OpenAI-style chat request for the NER pass."""
    response_format: dict[str, Any] = (
        {"type": "json_schema", "json_schema": {"name": "entities", "schema": _ENTITY_SCHEMA}}
        if structured
        else {"type": "json_object"}
    )
    return {
        "model": "",  # provider falls back to its configured default model
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _EXAMPLE_USER},
            {"role": "assistant", "content": _EXAMPLE_ASSISTANT},
            {"role": "user", "content": text[:_MAX_INPUT_CHARS]},
        ],
        "temperature": 0.0,
        "max_tokens": 1024,
        "response_format": response_format,
    }


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
# One complete {"type": ..., "text": ...} item, used to salvage a reply that was cut off at
# max_tokens (the full JSON is then invalid, but every finished item is still usable).
_ITEM_RE = re.compile(
    r'\{\s*"type"\s*:\s*"(?P<type>[^"\\]+)"\s*,\s*"text"\s*:\s*"(?P<text>(?:[^"\\]|\\.)*)"\s*\}'
)


def _salvage_items(raw: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for m in _ITEM_RE.finditer(raw):
        try:
            text = json.loads(f'"{m.group("text")}"')
        except ValueError:
            continue
        items.append({"type": m.group("type"), "text": text})
    return items


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
            return _salvage_items(candidate)
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return _salvage_items(candidate)
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
        provider_name = settings.llm_ner_provider or settings.ai_provider
        provider = get_provider(provider_name, settings)
        payload = _build_payload(text, structured=provider_name in _SCHEMA_PROVIDERS)
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
