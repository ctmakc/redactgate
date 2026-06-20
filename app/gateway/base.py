"""Provider gateway contract.

Every upstream LLM provider implements ``Provider``. Input is a *sanitized*
OpenAI-style chat payload; output is OpenAI-shaped so re-inflation is provider-agnostic.

Adapters live in app/gateway/<name>.py and register via ``register_provider``. The
factory ``get_provider(name)`` returns a configured singleton.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Callable

from app.config import Settings


class ProviderError(Exception):
    """Upstream/provider failure. ``status_code`` surfaces to the client envelope."""

    def __init__(self, message: str, *, status_code: int = 502, provider: str = ""):
        self.status_code = status_code
        self.provider = provider
        super().__init__(message)


class Provider(ABC):
    """Adapter from the canonical OpenAI chat schema to a concrete provider.

    Implementations translate to/from native formats but always accept and emit the
    OpenAI shape. ``complete`` returns a full ``chat.completion`` dict; ``stream`` yields
    ``chat.completion.chunk`` dicts (NOT raw SSE lines — the route serializes them).
    """

    name: str

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    async def complete(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Non-streaming completion. Returns an OpenAI chat.completion dict."""

    @abstractmethod
    def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """Streaming completion. Async-iterates OpenAI chat.completion.chunk dicts."""

    def default_model(self) -> str:
        """Provider's configured default model, used if the request model is generic."""
        return ""

    async def aclose(self) -> None:  # pragma: no cover - lifecycle hook
        return None


# ── Registry / factory ─────────────────────────────────────────────────────────

_REGISTRY: dict[str, Callable[[Settings], Provider]] = {}
_INSTANCES: dict[str, Provider] = {}


def register_provider(name: str, factory: Callable[[Settings], Provider]) -> None:
    _REGISTRY[name] = factory


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def get_provider(name: str, settings: Settings) -> Provider:
    if name not in _REGISTRY:
        raise ProviderError(
            f"unknown or unconfigured provider '{name}'", status_code=400, provider=name
        )
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name](settings)
    return _INSTANCES[name]


def reset_provider_cache() -> None:
    """Drop cached instances (used in tests when settings change)."""
    _INSTANCES.clear()
