"""Anthropic Messages API adapter.

Translates the canonical OpenAI chat payload to Anthropic's ``/v1/messages`` shape and
back:

- ``system`` messages are pulled out into the top-level ``system`` field.
- remaining messages map to ``{role, content}`` (only ``user``/``assistant`` roles).
- ``max_tokens`` is required by the API; default to 1024 when absent.
- non-stream: ``response.content[0].text`` becomes the assistant message content.
- stream: Anthropic SSE events (``content_block_delta`` -> ``text_delta``) become chunk
  deltas; ``message_delta.stop_reason`` becomes the final ``finish_reason``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.gateway.base import Provider, ProviderError, register_provider
from app.gateway.openai import build_chunk, build_completion, is_generic_model, new_id

_DEFAULT_MAX_TOKENS = 1024

# Anthropic stop_reason -> OpenAI finish_reason
_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


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
        return self._default_model if is_generic_model(model) else str(model)

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
        if payload.get("temperature") is not None:
            body["temperature"] = payload["temperature"]
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
                raise ProviderError(
                    f"upstream error: {resp.text[:200]}",
                    status_code=resp.status_code,
                    provider=self.name,
                )
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
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST", url, json=self._body(payload, stream=True), headers=self._headers()
                ) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        raise ProviderError(
                            f"upstream error: {body[:200]!r}",
                            status_code=resp.status_code,
                            provider=self.name,
                        )
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
                        if etype == "content_block_delta":
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
                        elif etype == "message_stop":
                            break
                    yield build_chunk(
                        model=model,
                        finish_reason=finish_reason,
                        completion_id=completion_id,
                    )
            except httpx.HTTPError as exc:
                raise ProviderError(str(exc), status_code=502, provider=self.name) from exc


register_provider("anthropic", lambda s: AnthropicProvider(s))
