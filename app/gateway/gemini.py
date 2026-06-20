"""Google Gemini adapter.

Translates the canonical OpenAI chat payload to the Gemini ``generateContent`` shape:

- system messages become a top-level ``systemInstruction``.
- remaining messages map to ``contents[{role: user|model, parts:[{text}]}]``.
- ``temperature`` / ``max_tokens`` map into ``generationConfig``.
- the API key travels as a ``?key=`` query parameter.
- non-stream: ``candidates[0].content.parts[].text`` is concatenated back.
- stream: ``:streamGenerateContent?alt=sse`` yields ``data:`` SSE lines, each a
  partial ``generateContent`` response whose parts become chunk deltas.
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

# Strict model-name charset — the model is interpolated into the upstream URL path.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Gemini finishReason -> OpenAI finish_reason
_FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
}


def _content_to_text(content: Any) -> str:
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


def _translate(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Split into (systemInstruction | None, contents list)."""
    system_chunks: list[str] = []
    contents: list[dict[str, Any]] = []
    for msg in payload.get("messages", []) or []:
        role = msg.get("role")
        text = _content_to_text(msg.get("content"))
        if role == "system":
            if text:
                system_chunks.append(text)
            continue
        g_role = "model" if role == "assistant" else "user"
        contents.append({"role": g_role, "parts": [{"text": text}]})
    system = None
    if system_chunks:
        system = {"parts": [{"text": "\n\n".join(system_chunks)}]}
    return system, contents


def _parts_text(candidate: dict[str, Any]) -> str:
    parts = ((candidate.get("content") or {}).get("parts")) or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._base_url = settings.gemini_base_url.rstrip("/")
        self._api_key = settings.gemini_api_key
        self._default_model = settings.gemini_default_model

    def default_model(self) -> str:
        return self._default_model

    def _resolve_model(self, payload: dict[str, Any]) -> str:
        model = payload.get("model")
        if is_generic_model(model):
            return self._default_model
        resolved = str(model)
        # SECURITY: the model is interpolated into the upstream URL path
        # (/models/{model}:generateContent). Reject anything outside a strict model-name
        # charset so a caller cannot path-traverse or redirect the request.
        if not _MODEL_RE.match(resolved):
            raise ProviderError(
                f"invalid model name '{resolved}'", status_code=400, provider=self.name
            )
        return resolved

    def _body(self, payload: dict[str, Any]) -> dict[str, Any]:
        system, contents = _translate(payload)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = system
        gen_config: dict[str, Any] = {}
        if payload.get("temperature") is not None:
            gen_config["temperature"] = payload["temperature"]
        if payload.get("max_tokens") is not None:
            gen_config["maxOutputTokens"] = payload["max_tokens"]
        if gen_config:
            body["generationConfig"] = gen_config
        return body

    def _params(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        params = {"key": self._api_key}
        if extra:
            params.update(extra)
        return params

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._resolve_model(payload)
        url = f"{self._base_url}/models/{model}:generateContent"
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                resp = await client.post(
                    url,
                    json=self._body(payload),
                    params=self._params(),
                    headers={"Content-Type": "application/json"},
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
        candidates = data.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        usage = data.get("usageMetadata") or {}
        return build_completion(
            model=model,
            content=_parts_text(candidate),
            finish_reason=_FINISH_MAP.get(candidate.get("finishReason") or "", "stop"),
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
        )

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        model = self._resolve_model(payload)
        url = f"{self._base_url}/models/{model}:streamGenerateContent"
        completion_id = new_id()
        finish_reason = "stop"
        async with httpx.AsyncClient(timeout=self.settings.upstream_timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST",
                    url,
                    json=self._body(payload),
                    params=self._params({"alt": "sse"}),
                    headers={"Content-Type": "application/json"},
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
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        candidates = data.get("candidates") or []
                        if not candidates:
                            continue
                        candidate = candidates[0]
                        text = _parts_text(candidate)
                        if text:
                            yield build_chunk(
                                model=model, content=text, completion_id=completion_id
                            )
                        reason = candidate.get("finishReason")
                        if reason:
                            finish_reason = _FINISH_MAP.get(reason, "stop")
                    yield build_chunk(
                        model=model, finish_reason=finish_reason, completion_id=completion_id
                    )
            except httpx.HTTPError as exc:
                raise ProviderError(str(exc), status_code=502, provider=self.name) from exc


register_provider("gemini", lambda s: GeminiProvider(s))
