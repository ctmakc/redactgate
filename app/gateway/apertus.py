"""Apertus adapter (swiss-ai Apertus behind an OpenAI-compatible server).

Apertus is served by any OpenAI-compatible runtime: llama.cpp ``llama-server`` on local
GGUF weights (the air-gapped default), vLLM, or a hosted endpoint. The wire format is the
OpenAI one, so this adapter reuses :class:`OpenAIProvider` and adds two things:

* configuration from ``APERTUS_*`` or the Hack Apertus names ``LLM_BASE_URL`` /
  ``LLM_NAME`` / ``LLM_API_KEY``;
* a short system instruction that tells the model the ``[[TYPE_hex]]`` tokens are opaque
  placeholders to copy verbatim, with one correct and three wrong forms. The instruction
  is only added when the sanitized payload actually contains placeholders;
* placeholder repair on non-streaming answers: an 8B model sometimes writes a token it was
  given in a degraded form (``AHV c4857b``, ``AHV_c4857b``, ``[AHV_c4857b]``). Any such form
  whose hex code belongs to a placeholder that was in the request is rewritten to the
  canonical ``[[TYPE_hex]]`` so the gateway can re-inflate it. Only codes the model was
  shown are ever repaired, so the step cannot create a mapping that did not exist.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings
from app.gateway.base import register_provider
from app.gateway.openai import OpenAIProvider
from app.redaction.placeholders import PLACEHOLDER_RE

PLACEHOLDER_INSTRUCTION = (
    "Personal data in this conversation is protected: each sensitive value was replaced by "
    "a token such as [[AHV_3f9a1c]] or [[IBAN_77e0d2]]. A token IS the value. Whenever you "
    "mention that value, write the complete token exactly as it appears, with the double "
    "square brackets, the type and the code. Correct: 'AHV-Nr. [[AHV_3f9a1c]]'. Wrong: "
    "'AHV 3f9a1c', 'AHV_3f9a1c', '[AHV_3f9a1c]'. Never invent new tokens and never ask for "
    "the hidden values."
)

# A degraded token: optional brackets, optional TYPE followed by "_" or a space, then the
# hex code. Bounded so it never matches inside a longer word or hex run.
_DEGRADED_RE = re.compile(
    r"(?<![\w\[])\[{0,2}(?:(?P<type>[A-Z][A-Z0-9_]*?)(?P<sep>[_ ]))?(?P<hex>[0-9a-f]{6,12})\]{0,2}(?![\w\]])"
)


def _known_placeholders(messages: list[Any]) -> dict[str, tuple[str, str]]:
    """Map token hex -> (entity_type, canonical placeholder) for every placeholder sent."""
    known: dict[str, tuple[str, str]] = {}
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        texts: list[str] = []
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
        for text in texts:
            for m in PLACEHOLDER_RE.finditer(text):
                known[m.group(2)] = (m.group(1), m.group(0))
    return known


def repair_placeholders(text: str, known: dict[str, tuple[str, str]]) -> tuple[str, int]:
    """Rewrite degraded forms of known tokens to canonical ``[[TYPE_hex]]``.

    Canonical placeholders are left untouched. A degraded form is repaired only when its
    hex code is one the model was given and, if a type is written, the type matches.
    Returns the repaired text and the number of repairs.
    """
    if not known or not text:
        return text, 0
    repaired = 0
    out: list[str] = []
    pos = 0
    for canon in PLACEHOLDER_RE.finditer(text):
        segment, count = _repair_segment(text[pos : canon.start()], known)
        out.append(segment)
        out.append(canon.group(0))
        repaired += count
        pos = canon.end()
    segment, count = _repair_segment(text[pos:], known)
    out.append(segment)
    return "".join(out), repaired + count


def _repair_segment(segment: str, known: dict[str, tuple[str, str]]) -> tuple[str, int]:
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        hit = known.get(m.group("hex"))
        if hit is None:
            return m.group(0)
        etype, canonical = hit
        written_type = m.group("type")
        if written_type is not None and written_type != etype:
            return m.group(0)
        count += 1
        # "AHV c4857b" reads as a label plus a value: keep the label so the restored text
        # says "AHV 756.…". "AHV_c4857b" / "[AHV_c4857b]" are mangled tokens: replace whole.
        if written_type is not None and m.group("sep") == " " and not m.group(0).startswith("["):
            return f"{written_type} {canonical}"
        return canonical

    return _DEGRADED_RE.sub(_sub, segment), count


def _has_placeholder(messages: list[Any]) -> bool:
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and PLACEHOLDER_RE.search(content):
            return True
        if isinstance(content, list):
            for part in content:
                text = part.get("text") if isinstance(part, dict) else None
                if isinstance(text, str) and PLACEHOLDER_RE.search(text):
                    return True
    return False


def with_placeholder_instruction(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` with the placeholder instruction in the system turn."""
    messages = list(payload.get("messages") or [])
    if not _has_placeholder(messages):
        return payload
    body = dict(payload)
    if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
        first = dict(messages[0])
        existing = first.get("content")
        if isinstance(existing, str):
            first["content"] = f"{PLACEHOLDER_INSTRUCTION}\n\n{existing}"
            messages[0] = first
        else:
            messages.insert(0, {"role": "system", "content": PLACEHOLDER_INSTRUCTION})
    else:
        messages.insert(0, {"role": "system", "content": PLACEHOLDER_INSTRUCTION})
    body["messages"] = messages
    return body


class ApertusProvider(OpenAIProvider):
    name = "apertus"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._base_url = settings.apertus_base_url.rstrip("/")
        self._api_key = settings.apertus_api_key
        self._default_model = settings.apertus_default_model
        self.last_repairs = 0  # degraded tokens repaired in the last non-streaming answer

    def _body(self, payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        return super()._body(with_placeholder_instruction(payload), stream=stream)

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._repair(await super().complete(payload), payload)

    def _repair(self, result: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """Apply :func:`repair_placeholders` to every choice of a non-streaming answer."""
        known = _known_placeholders(list(payload.get("messages") or []))
        self.last_repairs = 0
        if not known:
            return result
        for choice in result.get("choices") or []:
            message = choice.get("message") if isinstance(choice, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                fixed, n = repair_placeholders(content, known)
                if n:
                    message["content"] = fixed
                    self.last_repairs += n
        return result


register_provider("apertus", lambda s: ApertusProvider(s))
