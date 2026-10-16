"""Anthropic Messages API adapter.

Translates the canonical OpenAI chat payload to Anthropic's ``/v1/messages`` shape and
back:

- ``system`` messages are pulled out into the top-level ``system`` field.
- remaining messages map to ``{role, content}`` (only ``user``/``assistant`` roles).
- ``max_tokens`` is required by the API; default to 4096 when absent (current models think
  adaptively and thinking tokens count against ``max_tokens``).
- sampling knobs (``temperature``/``top_p``) are forwarded only to models that still accept
  them; Claude 4.7+ / 5.x reject them with a 400, so they are dropped there.
- OpenAI ``reasoning_effort`` maps to ``output_config.effort``; ``stop`` to ``stop_sequences``.
- non-stream: text blocks are joined into the assistant message content (thinking blocks
  are skipped).
- stream: Anthropic SSE events (``content_block_delta`` -> ``text_delta``) become chunk
  deltas; ``message_delta.stop_reason`` becomes the final ``finish_reason``; token usage
  from ``message_start``/``message_delta`` is attached to the final chunk; an in-stream
  ``error`` event raises ``ProviderError``.
- ``stop_reason: refusal`` maps to ``finish_reason: content_filter``.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.gateway.base import Provider, ProviderError, register_provider
from app.gateway.openai import build_chunk, build_completion, is_generic_model, new_id

_DEFAULT_MAX_TOKENS = 4096

# Models that still accept temperature/top_p: Claude 3.x and Claude 4.0-4.6 (incl. dated
# snapshots). Claude 4.7+, 5.x and Fable reject sampling parameters with a 400.
_SAMPLING_MODELS = re.compile(r"^claude-(3[\w.-]*|(opus|sonnet|haiku)-4(-[0-6])?(-\d{8})?)$")

_EFFORTS = {"low", "medium", "high", "xhigh", "max"}

# Anthropic error.type -> HTTP status we surface (529 "overloaded" is not a standard code).
_ERROR_STATUS = {
    "overloaded_error": 503,
    "rate_limit_error": 429,
    "authentication_error": 502,
    "permission_error": 502,
    "api_error": 502,
}

# Anthropic stop_reason -> OpenAI finish_reason
_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
    "pause_turn": "stop",
}


def accepts_sampling(model: str) -> bool:
    """True when ``model`` still accepts ``temperature``/``top_p``."""
    return bool(_SAMPLING_MODELS.match(model))


def _error_message(raw: str | bytes) -> tuple[str, str | None]:
    """Extract (message, error.type) from an Anthropic error body, tolerating non-JSON."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    try:
        err = (json.loads(text) or {}).get("error") or {}
        return str(err.get("message") or text)[:300], err.get("type")
    except (json.JSONDecodeError, ValueError, AttributeError):
        return text[:300], None


def _raise_upstream(status: int, raw: str | bytes) -> None:
    message, etype = _error_message(raw)
    code = _ERROR_STATUS.get(etype or "", status if status != 529 else 503)
    # Upstream credentials/permission problems are OUR misconfiguration, not the caller's.
    if status in (401, 403):
        code = 502
    raise ProviderError(f"upstream error: {message}", status_code=code, provider="anthropic")


def _content_to_text(content: Any) -> str:
    """Flatten OpenAI message content (str or text-parts) into a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text"):
                if isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "".join(parts)
    return ""


def _translate(payload: dict[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    """Split into (system_prompt, anthropic_messages)."""
    system_chunks: list[str] = []
    messages: list[dict[str, Any]] = []
    for msg in payload.get("messages", []) or []:
        role = msg.get("role")
        text = _content_to_text(msg.get("content"))
        if role == "system":
            if text:
                system_chunks.append(text)
            continue
        out_role = "assistant" if role == "assistant" else "user"
        messages.append({"role": out_role, "content": text})
    system = "\n\n".join(system_chunks) if system_chunks else None
    return system, messages


class AnthropicProvider(Provider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._base_url = settings.anthropic_base_url.rstrip("/")
        self._api_key = settings.anthropic_api_key
        self._default_model = settings.anthropic_default_model

    def default_model(self) -> str:
        return self._default_model

    def _resolve_model(self, payload: dict[str, Any]) -> str:
        model = payload.get("model")
        # The provider is chosen by gateway config, not the caller: an OpenAI SDK client
        # asking for "gpt-4o-mini" gets the configured Claude model instead of a 404.
        if is_generic_model(model) or not str(model).startswith("claude"):
            return self._default_model
        return str(model)

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
        }

    def _body(self, payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        system, messages = _translate(payload)
        body: dict[str, Any] = {
            "model": self._resolve_model(payload),
            "messages": messages,
            "max_tokens": payload.get("max_tokens") or _DEFAULT_MAX_TOKENS,
            "stream": stream,
        }
        if system:
            body["system"] = system
        if accepts_sampling(body["model"]):
            for knob in ("temperature", "top_p"):
                if payload.get(knob) is not None:
                    body[knob] = payload[knob]
        effort = payload.get("reasoning_effort")
        if isinstance(effort, str) and effort.lower() in _EFFORTS:
            body["output_config"] = {"effort": effort.lower()}
        stop = payload.get("stop")
        if isinstance(stop, str):
            stop = [stop]
        if isinstance(stop, list) and stop:
            body["stop_sequences"] = [str(x) for x in stop][:4]
        return body

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/v1/messages"
        model = self._resolve_model(payload)
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                resp = await client.post(
                    url, json=self._body(payload, stream=False), headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise ProviderError(str(exc), status_code=502, provider=self.name) from exc
            if resp.status_code >= 400:
                _raise_upstream(resp.status_code, resp.text)
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise ProviderError(
                    "invalid JSON from upstream", status_code=502, provider=self.name
                ) from exc
        text_parts = [
            blk.get("text", "")
            for blk in (data.get("content") or [])
            if isinstance(blk, dict) and blk.get("type") == "text"
        ]
        usage = data.get("usage") or {}
        return build_completion(
            model=data.get("model") or model,
            content="".join(text_parts),
            finish_reason=_STOP_MAP.get(data.get("stop_reason") or "", "stop"),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
        )

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        url = f"{self._base_url}/v1/messages"
        model = self._resolve_model(payload)
        completion_id = new_id()
        finish_reason = "stop"
        prompt_tokens = 0
        completion_tokens = 0
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST", url, json=self._body(payload, stream=True), headers=self._headers()
                ) as resp:
                    if resp.status_code >= 400:
                        _raise_upstream(resp.status_code, await resp.aread())
                    async for raw in resp.aiter_lines():
                        line = raw.strip()
                        if not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        etype = event.get("type")
                        if etype == "error":
                            err = event.get("error") or {}
                            raise ProviderError(
                                f"upstream error: {str(err.get('message') or 'stream error')[:300]}",
                                status_code=_ERROR_STATUS.get(err.get("type") or "", 502),
                                provider=self.name,
                            )
                        if etype == "message_start":
                            usage = (event.get("message") or {}).get("usage") or {}
                            prompt_tokens = int(usage.get("input_tokens") or 0)
                        elif etype == "content_block_delta":
                            delta = event.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                text = delta.get("text") or ""
                                if text:
                                    yield build_chunk(
                                        model=model,
                                        content=text,
                                        completion_id=completion_id,
                                    )
                        elif etype == "message_delta":
                            stop = (event.get("delta") or {}).get("stop_reason")
                            if stop:
                                finish_reason = _STOP_MAP.get(stop, "stop")
                            usage = event.get("usage") or {}
                            if usage.get("output_tokens") is not None:
                                completion_tokens = int(usage["output_tokens"])
                        elif etype == "message_stop":
                            break
                    final = build_chunk(
                        model=model,
                        finish_reason=finish_reason,
                        completion_id=completion_id,
                    )
                    final["usage"] = {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    }
                    yield final
            except httpx.HTTPError as exc:
                raise ProviderError(str(exc), status_code=502, provider=self.name) from exc


register_provider("anthropic", lambda s: AnthropicProvider(s))
