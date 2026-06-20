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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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

# We redact text in message content (str or text-parts). We deliberately DO NOT
# redact role/name/tool schemas. Walk order: messages in order; within a message,
# string content first, else each text part in order.


def _message_text_parts(content: Any) -> list[tuple[str, Any]]:
    """Return [(kind, locator)] describing extractable text in a message's content.

    kind == "str"  -> the whole content is a string
    kind == "part" -> locator is the index into the content list of a text part
    """
    out: list[tuple[str, Any]] = []
    if isinstance(content, str):
        out.append(("str", None))
    elif isinstance(content, list):
        for i, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                if isinstance(part.get("text"), str):
                    out.append(("part", i))
    return out


def extract_texts(payload: dict[str, Any]) -> list[str]:
    """Pull every redactable text string out of an OpenAI-style request payload."""
    texts: list[str] = []
    for msg in payload.get("messages", []) or []:
        content = msg.get("content")
        for kind, loc in _message_text_parts(content):
            if kind == "str":
                texts.append(content)
            else:
                texts.append(content[loc]["text"])
    # /v1/responses style: top-level `input` may be a string or list of parts
    inp = payload.get("input")
    if isinstance(inp, str):
        texts.append(inp)
    elif isinstance(inp, list):
        for part in inp:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return texts


def inject_texts(payload: dict[str, Any], new_texts: list[str]) -> dict[str, Any]:
    """Return a deep copy of ``payload`` with redactable texts replaced in walk order."""
    expected = len(extract_texts(payload))
    if len(new_texts) != expected:
        raise ValueError(
            f"inject_texts length mismatch: got {len(new_texts)}, expected {expected}"
        )
    out = copy.deepcopy(payload)
    it = iter(new_texts)
    for msg in out.get("messages", []) or []:
        content = msg.get("content")
        for kind, loc in _message_text_parts(content):
            if kind == "str":
                msg["content"] = next(it)
            else:
                content[loc]["text"] = next(it)
    inp = out.get("input")
    if isinstance(inp, str):
        out["input"] = next(it)
    elif isinstance(inp, list):
        for part in inp:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                part["text"] = next(it)
    return out


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
