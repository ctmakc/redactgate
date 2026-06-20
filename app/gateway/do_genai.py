"""DigitalOcean GenAI adapter.

DigitalOcean's inference endpoint is OpenAI-compatible, so this reuses the OpenAI
passthrough machinery (``{base_url}/chat/completions`` + Bearer auth + ``data:`` SSE)
but reads its credentials/base-url/default-model from the ``do_genai_*`` settings.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.gateway.base import Provider, ProviderError, register_provider
from app.gateway.openai import iter_sse_data, resolve_model


class DoGenAIProvider(Provider):
    name = "do-genai"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._base_url = settings.do_genai_base_url.rstrip("/")
        self._api_key = settings.do_genai_api_key
        self._default_model = settings.do_genai_default_model

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


register_provider("do-genai", lambda s: DoGenAIProvider(s))
