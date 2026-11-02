"""Gateway URL-construction hardening (respx-mocked; no network, no real keys)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.config import Settings
from app.gateway.azure import AzureOpenAIProvider
from app.gateway.base import ProviderError


def _azure() -> AzureOpenAIProvider:
    return AzureOpenAIProvider(
        Settings(
            environment="test",
            azure_openai_endpoint="https://az.test",
            azure_openai_api_key="test-not-a-key",
            azure_openai_deployment="dep1",
        )
    )


def _payload(model: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.parametrize(
    "model",
    [
        "../../admin",
        "dep1/../../x",
        "dep1?api-version=1999-01-01",
        "dep1#frag",
        "dep%2F..%2Fx",
        "dep 1",
        "a" * 65,
        "dep1..x",
        "/abs",
    ],
)
async def test_azure_rejects_path_injection_in_model(model):
    with respx.mock(assert_all_called=False) as router:
        catch_all = router.route(host="az.test").mock(return_value=httpx.Response(200, json={}))
        with pytest.raises(ProviderError) as ei:
            await _azure().complete(_payload(model))
    assert ei.value.status_code == 400
    assert not catch_all.called  # nothing left the gateway


async def test_azure_stream_rejects_path_injection():
    with respx.mock(assert_all_called=False) as router:
        catch_all = router.route(host="az.test").mock(return_value=httpx.Response(200))
        with pytest.raises(ProviderError):
            async for _ in _azure().stream(_payload("../x")):
                pass
    assert not catch_all.called


@pytest.mark.parametrize("model", ["gpt-4o", "my_deploy.v2", "Dep-01"])
async def test_azure_accepts_legit_deployment_names(model):
    with respx.mock() as router:
        route = router.post(f"https://az.test/openai/deployments/{model}/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "c",
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
                },
            )
        )
        out = await _azure().complete(_payload(model))
    assert route.called
    assert out["choices"][0]["message"]["content"] == "ok"
