"""OpenAI-compatible passthrough adapter.

The canonical internal format already IS the OpenAI Chat Completions schema, so this
adapter is a near-passthrough: it forwards the sanitized payload to
``{base_url}/chat/completions`` and (for streaming) parses ``data: {...}`` SSE lines.

The small OpenAI-shape builders / SSE helpers here are shared (by import) with the
other adapters in this package; they intentionally live in this file rather than a new
spine module.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.gateway.base import Provider, ProviderError, register_provider

# ── Shared OpenAI-shape builders ───────────────────────────────────────────────

_GENERIC_MODELS = {
    "",
    "default",
    "auto",
    "gpt",
    "model",
    "redactgate",
    "redactgate-default",
}


def new_id() -> str:
    """Stable, deterministic-shaped chat id."""
    return f"chatcmpl-{uuid.uuid4().hex}"


def is_generic_model(model: str | None) -> bool:
    return (model or "").strip().lower() in _GENERIC_MODELS


def build_completion(
    *,
    model: str,
    content: str,
    finish_reason: str = "stop",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    completion_id: str | None = None,
) -> dict[str, Any]:
    """Assemble a full OpenAI ``chat.completion`` dict."""
    return {
        "id": completion_id or new_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def build_chunk(
    *,
    model: str,
    content: str | None = None,
    finish_reason: str | None = None,
    completion_id: str,
    role: str | None = None,
) -> dict[str, Any]:
    """Assemble a single OpenAI ``chat.completion.chunk`` dict."""
    delta: dict[str, Any] = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


async def iter_sse_data(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Parse an ``data: {json}\\n\\n`` SSE stream into JSON objects.

    Skips ``[DONE]`` sentinels and blank/comment lines. Tolerates non-JSON keepalives.
    """
    async for raw in response.aiter_lines():
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def resolve_model(payload: dict[str, Any], default: str) -> str:
    model = payload.get("model")
    if is_generic_model(model):
        return default
    return str(model)


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._base_url = settings.openai_base_url.rstrip("/")
        self._api_key = settings.openai_api_key
        self._default_model = settings.openai_default_model

    def default_model(self) -> str:
        return self._default_model

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _body(self, payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        body = dict(payload)
        body["model"] = resolve_model(payload, self._default_model)
        body["stream"] = stream
        return body

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
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
                return resp.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise ProviderError(
                    "invalid JSON from upstream", status_code=502, provider=self.name
                ) from exc

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        url = f"{self._base_url}/chat/completions"
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
                    async for chunk in iter_sse_data(resp):
                        yield chunk
            except httpx.HTTPError as exc:
                raise ProviderError(str(exc), status_code=502, provider=self.name) from exc


register_provider("openai", lambda s: OpenAIProvider(s))
