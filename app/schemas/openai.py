"""OpenAI-compatible request/response shapes + text extract/inject helpers.

The canonical internal format for RedactGate is the **OpenAI Chat Completions** schema.
Every provider adapter accepts a (sanitized) OpenAI-style request dict and returns
OpenAI-style output, so re-inflation always operates on a single known shape.

The ``extract_texts`` / ``inject_texts`` pair is contract-critical and implemented
concretely here (not delegated) because span positions depend on a stable walk order:

    texts = extract_texts(payload)          # deterministic order
    sanitized = [redact(t) for t in texts]  # same length, same order
    new_payload = inject_texts(payload, sanitized)

Both functions walk the SAME fields in the SAME order. ``inject_texts`` requires
``len(new_texts) == len(extract_texts(payload))`` or it raises.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

# ── Lenient request models (a faithful proxy must pass unknown fields through) ──


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    # content may be a plain string OR a list of typed parts (OpenAI vision/parts form)
    content: Any | None = None
    name: str | None = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class ResponsesRequest(BaseModel):
    """Minimal model for the newer /v1/responses endpoint."""

    model_config = ConfigDict(extra="allow")
    model: str
    input: Any
    stream: bool = False


# ── Text walk: which fields carry redactable user text ─────────────────────────
#
# SECURITY: every text field that could carry user PII MUST be walked, or it reaches
# the upstream LLM un-redacted. We cover: message `content` (str or text/​input/​output
# parts), assistant `tool_calls[].function.arguments` and legacy `function_call.arguments`
# (JSON strings that routinely embed PII), and the /v1/responses `input` (a string, a flat
# list of text parts, OR a list of message objects with nested `content`).
#
# ``extract_texts`` and ``inject_texts`` are BOTH built on the single ``_map_texts`` walker
# so they can never drift out of alignment (a drift = either a leak or a corrupted payload).

_TEXT_PART_TYPES = ("text", "input_text", "output_text")


def _walk_content(content: Any, fn: Callable[[str], str]) -> Any:
    """Apply ``fn`` to redactable text in a message ``content`` (str or list of parts).

    List parts are mutated in place; a string content returns the transformed string.
    """
    if isinstance(content, str):
        return fn(content)
    if isinstance(content, list):
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") in _TEXT_PART_TYPES
                and isinstance(part.get("text"), str)
            ):
                part["text"] = fn(part["text"])
    return content


def _walk_message(msg: Any, fn: Callable[[str], str]) -> None:
    """Apply ``fn`` to every redactable text in one message dict (in place)."""
    if not isinstance(msg, dict):
        return
    if "content" in msg:
        msg["content"] = _walk_content(msg.get("content"), fn)
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            func = tc.get("function")
            if isinstance(func, dict) and isinstance(func.get("arguments"), str):
                func["arguments"] = fn(func["arguments"])
    fc = msg.get("function_call")
    if isinstance(fc, dict) and isinstance(fc.get("arguments"), str):
        fc["arguments"] = fn(fc["arguments"])


def _map_texts(payload: dict[str, Any], fn: Callable[[str], str]) -> dict[str, Any]:
    """Deep-copy ``payload`` and apply ``fn`` to every redactable text in a fixed order.

    The ONE source of truth for what counts as redactable text — both reading
    (``extract_texts``) and rewriting (``inject_texts``) walk through here."""
    out = copy.deepcopy(payload)
    for msg in out.get("messages") or []:
        _walk_message(msg, fn)
    inp = out.get("input")
    if isinstance(inp, str):
        out["input"] = fn(inp)
    elif isinstance(inp, list):
        for item in inp:
            if not isinstance(item, dict):
                continue
            if "content" in item or "tool_calls" in item:
                # a Responses-API message object: {role, content: str|parts, tool_calls?}
                _walk_message(item, fn)
            elif item.get("type") in _TEXT_PART_TYPES and isinstance(item.get("text"), str):
                item["text"] = fn(item["text"])  # flat input_text part
            elif isinstance(item.get("text"), str):
                item["text"] = fn(item["text"])  # bare {text: ...}
    return out


def extract_texts(payload: dict[str, Any]) -> list[str]:
    """Pull every redactable text string out of an OpenAI-style request payload."""
    texts: list[str] = []

    def _collect(t: str) -> str:
        texts.append(t)
        return t

    _map_texts(payload, _collect)
    return texts


def inject_texts(payload: dict[str, Any], new_texts: list[str]) -> dict[str, Any]:
    """Return a deep copy of ``payload`` with redactable texts replaced in walk order."""
    expected = len(extract_texts(payload))
    if len(new_texts) != expected:
        raise ValueError(
            f"inject_texts length mismatch: got {len(new_texts)}, expected {expected}"
        )
    it = iter(new_texts)
    return _map_texts(payload, lambda _t: next(it))


# ── Response text access (for re-inflation of model output) ────────────────────


def extract_completion_text(chunk: dict[str, Any]) -> str | None:
    """Best-effort: pull assistant text from a non-stream chat.completion dict."""
    try:
        return chunk["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def extract_delta_text(chunk: dict[str, Any]) -> str | None:
    """Best-effort: pull the incremental text from a chat.completion.chunk dict."""
    try:
        return chunk["choices"][0]["delta"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return None


def set_completion_text(chunk: dict[str, Any], text: str) -> None:
    chunk["choices"][0]["message"]["content"] = text


def set_delta_text(chunk: dict[str, Any], text: str) -> None:
    chunk["choices"][0]["delta"]["content"] = text


# ── Error envelope (OpenAI-shaped) ─────────────────────────────────────────────


class OpenAIError(BaseModel):
    message: str
    type: str = "redactgate_error"
    code: str | None = None
    param: str | None = None


def error_body(message: str, *, type_: str = "redactgate_error", code: str | None = None) -> dict:
    return {"error": OpenAIError(message=message, type=type_, code=code).model_dump()}


class HardBlockError(Exception):
    """Raised when policy refuses a call because a forbidden entity type appeared."""

    def __init__(self, blocked_types: list[str]):
        self.blocked_types = blocked_types
        super().__init__(f"hard-blocked entity types: {', '.join(sorted(set(blocked_types)))}")
