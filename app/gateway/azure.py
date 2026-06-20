"""Azure OpenAI adapter.

Azure exposes an OpenAI-compatible chat-completions API, but the URL embeds the
*deployment* name and an ``api-version`` query parameter, and auth is the ``api-key``
header (not Bearer). The request URL is::

    {endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...

If the endpoint or api-key is not configured, the adapter still registers (so the
provider list is stable) but raises ``ProviderError("azure not configured", 400)`` when
actually invoked.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import Settings
from app.gateway.base import Provider, ProviderError, register_provider
from app.gateway.openai import is_generic_model, iter_sse_data


class AzureOpenAIProvider(Provider):
    name = "azure"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._endpoint = settings.azure_openai_endpoint.rstrip("/")
        self._api_key = settings.azure_openai_api_key
        self._deployment = settings.azure_openai_deployment
        self._api_version = settings.azure_openai_api_version

    def default_model(self) -> str:
        return self._deployment

    def _require_configured(self) -> None:
        if not self._endpoint or not self._api_key:
            raise ProviderError("azure not configured", status_code=400, provider=self.name)

    def _deployment_for(self, payload: dict[str, Any]) -> str:
        model = payload.get("model")
        if not is_generic_model(model):
            return str(model)
        return self._deployment

    def _url(self, deployment: str) -> str:
        return (
            f"{self._endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={self._api_version}"
        )

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "api-key": self._api_key}

    def _body(self, payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        body = dict(payload)
        body.pop("model", None)  # deployment carries the model on Azure
        body["stream"] = stream
        return body

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_configured()
        deployment = self._deployment_for(payload)
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                resp = await client.post(
                    self._url(deployment),
                    json=self._body(payload, stream=False),
                    headers=self._headers(),
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
        self._require_configured()
        deployment = self._deployment_for(payload)
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST",
                    self._url(deployment),
                    json=self._body(payload, stream=True),
                    headers=self._headers(),
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


register_provider("azure", lambda s: AzureOpenAIProvider(s))
