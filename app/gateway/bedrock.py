"""AWS Bedrock adapter (thin).

Bedrock requires the optional ``boto3`` dependency and AWS credentials, neither of which
is a hard requirement of RedactGate. ``boto3`` is therefore imported lazily *inside* the
request methods. For ``anthropic.*`` models this adapter calls ``invoke_model`` with the
Anthropic Messages body and translates the result to the OpenAI shape.

If ``boto3`` is missing or no credentials are available, the adapter still registers but
raises ``ProviderError("bedrock not configured", 400)`` when invoked. The blocking boto3
call is run in a thread so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from app.config import Settings
from app.gateway.base import Provider, ProviderError, register_provider
from app.gateway.openai import build_chunk, build_completion, is_generic_model, new_id

_DEFAULT_MAX_TOKENS = 1024

_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
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


def _anthropic_body(payload: dict[str, Any]) -> dict[str, Any]:
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
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "messages": messages,
        "max_tokens": payload.get("max_tokens") or _DEFAULT_MAX_TOKENS,
    }
    if system_chunks:
        body["system"] = "\n\n".join(system_chunks)
    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    return body


class BedrockProvider(Provider):
    name = "bedrock"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._region = settings.bedrock_region
        self._default_model = settings.bedrock_default_model

    def default_model(self) -> str:
        return self._default_model

    def _resolve_model(self, payload: dict[str, Any]) -> str:
        model = payload.get("model")
        return self._default_model if is_generic_model(model) else str(model)

    def _client(self) -> Any:
        try:
            import boto3  # noqa: PLC0415  (lazy: boto3 is an optional dependency)
        except ImportError as exc:
            raise ProviderError(
                "bedrock not configured", status_code=400, provider=self.name
            ) from exc
        try:
            return boto3.client("bedrock-runtime", region_name=self._region)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                "bedrock not configured", status_code=400, provider=self.name
            ) from exc

    def _invoke(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        client = self._client()
        try:
            resp = client.invoke_model(modelId=model, body=json.dumps(body))
            return json.loads(resp["body"].read())
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(str(exc), status_code=502, provider=self.name) from exc

    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        model = self._resolve_model(payload)
        if not model.startswith("anthropic."):
            raise ProviderError(
                f"bedrock adapter supports anthropic.* models only, got '{model}'",
                status_code=400,
                provider=self.name,
            )
        data = await asyncio.to_thread(self._invoke, model, _anthropic_body(payload))
        text_parts = [
            blk.get("text", "")
            for blk in (data.get("content") or [])
            if isinstance(blk, dict) and blk.get("type") == "text"
        ]
        usage = data.get("usage") or {}
        return build_completion(
            model=model,
            content="".join(text_parts),
            finish_reason=_STOP_MAP.get(data.get("stop_reason") or "", "stop"),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
        )

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        # Emit the full completion as a single chunk + a terminating chunk. Bedrock's
        # native streaming (invoke_model_with_response_stream) is intentionally not wired
        # here to keep this adapter thin and dependency-light.
        completion = await self.complete(payload)
        model = completion["model"]
        completion_id = new_id()
        content = completion["choices"][0]["message"]["content"]
        finish = completion["choices"][0]["finish_reason"]
        if content:
            yield build_chunk(model=model, content=content, completion_id=completion_id)
        yield build_chunk(model=model, finish_reason=finish, completion_id=completion_id)


register_provider("bedrock", lambda s: BedrockProvider(s))
