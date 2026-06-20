"""Provider gateway package.

Importing this package self-registers every available adapter via ``register_provider``.
Adapters are imported defensively so a single broken/incomplete adapter cannot prevent
the others from registering.
"""

from __future__ import annotations

import logging

from app.gateway.base import (  # noqa: F401
    Provider,
    ProviderError,
    available_providers,
    get_provider,
    register_provider,
    reset_provider_cache,
)

log = logging.getLogger("redactgate.gateway")

# Each adapter module calls register_provider(...) at import time.
for _mod in ("anthropic", "openai", "gemini", "ollama", "azure", "bedrock", "do_genai"):
    try:
        __import__(f"app.gateway.{_mod}")
    except Exception as exc:  # noqa: BLE001
        log.debug("gateway adapter %s not loaded: %s", _mod, exc)

__all__ = [
    "Provider",
    "ProviderError",
    "available_providers",
    "get_provider",
    "register_provider",
    "reset_provider_cache",
]
