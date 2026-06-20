"""Shared pytest fixtures. Unit tests run with no DB/Redis — the engine is exercised
against ``InMemoryTokenStore``. Integration tests (marked ``integration``) require a live
Postgres and are skipped unless RUN_INTEGRATION=1.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("REQUIRE_API_KEY", "false")
os.environ.setdefault("ENABLE_PRESIDIO", "false")
os.environ.setdefault("ENABLE_LLM_NER", "false")

# 32-byte base64 test keys (NOT secrets — test-only, deterministic).
import base64  # noqa: E402

_TESTKEY = base64.b64encode(b"redactgate-test-key-0123456789abc"[:32]).decode()
os.environ.setdefault("VAULT_MASTER_KEY", _TESTKEY)
os.environ.setdefault("FINGERPRINT_HMAC_KEY", _TESTKEY)
os.environ.setdefault("AUDIT_HMAC_KEY", _TESTKEY)


@pytest.fixture
def master_key() -> bytes:
    return b"redactgate-test-key-0123456789abc"[:32]


@pytest.fixture
def fingerprint_key() -> bytes:
    return b"fingerprint-test-key-0123456789ab"[:32]


@pytest.fixture
def vault(master_key, fingerprint_key):
    """A ready-to-use Vault backed by an in-memory store.

    Skips cleanly until app.redaction.vault.Vault exists (so the spine commits green
    before the implementation lands)."""
    pytest.importorskip("app.redaction.vault")
    from app.redaction.store import InMemoryTokenStore
    from app.redaction.vault import Vault

    return Vault(InMemoryTokenStore(), master_key=master_key, fingerprint_key=fingerprint_key)


@pytest.fixture
def sample_pii_text() -> str:
    return (
        "Please review the file for John Smith. His SIN is 046 454 286 and his "
        "business number is 123456789 RC0001. Wire the refund to IBAN "
        "GB82 WEST 1234 5698 7654 32. Email john.smith@example.com if needed. "
        "John Smith also asked about his second account."
    )


def integration_only():
    return pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION") != "1",
        reason="set RUN_INTEGRATION=1 (needs live Postgres) to run",
    )


@pytest.fixture(autouse=True)
def _reset_db_engine_singleton():
    """Reset the global async engine after every test.

    pytest-asyncio (function-scoped loops) gives each async test a fresh event loop, but
    ``app.db`` caches a single ``AsyncEngine``. An engine built on test A's loop would be
    reused on test B's closed loop -> "attached to a different loop" / "Event loop is
    closed". Nulling the singletons forces each DB-touching test to build a fresh engine
    on its own loop. No-op for the unit lane (no engine is ever created)."""
    yield
    import app.db as _db

    _db._engine = None
    _db._sessionmaker = None
