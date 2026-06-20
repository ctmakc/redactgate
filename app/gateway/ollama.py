"""Ollama adapter.

Talks to a local/remote Ollama server's ``/api/chat`` endpoint. No API key. The native
request is ``{model, messages, stream}``; the non-stream response is a single JSON object
and the stream is newline-delimited JSON objects (``{message:{content}, done}``). Both are
mapped back to the OpenAI shape.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.gateway.base import Provider, ProviderError, register_provider
from app.gateway.openai import build_chunk, build_completion, is_generic_model, new_id


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._default_model = settings.ollama_default_model

    def default_model(self) -> str:
        return self._default_model

    def _resolve_model(self, payload: dict[str, Any]) -> str:
        model = payload.get("model")
        return self._default_model if is_generic_model(model) else str(model)

    def _body(self, payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self._resolve_model(payload),
            "messages": payload.get("messages", []),
            "stream": stream,
        }
        options: dict[str, Any] = {}
        if payload.get("temperature") is not None:
            options["temperature"] = payload["temperature"]
        if payload.get("max_tokens") is not None:
            options["num_predict"] = payload["max_tokens"]
        if options:
            body["options"] = options
        return body

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/api/chat"
        model = self._resolve_model(payload)
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                resp = await client.post(url, json=self._body(payload, stream=False))
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
        content = ((data.get("message") or {}).get("content")) or ""
        return build_completion(
            model=data.get("model") or model,
            content=content,
            finish_reason="stop" if data.get("done", True) else "length",
            prompt_tokens=int(data.get("prompt_eval_count") or 0),
            completion_tokens=int(data.get("eval_count") or 0),
        )

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        url = f"{self._base_url}/api/chat"
        model = self._resolve_model(payload)
        completion_id = new_id()
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST", url, json=self._body(payload, stream=True)
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
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        text = (data.get("message") or {}).get("content")
                        done = bool(data.get("done"))
                        if text:
                            yield build_chunk(
                                model=data.get("model") or model,
                                content=text,
                                completion_id=completion_id,
                            )
                        if done:
                            yield build_chunk(
                                model=data.get("model") or model,
                                finish_reason="stop",
                                completion_id=completion_id,
                            )
            except httpx.HTTPError as exc:
                raise ProviderError(str(exc), status_code=502, provider=self.name) from exc


register_provider("ollama", lambda s: OllamaProvider(s))
