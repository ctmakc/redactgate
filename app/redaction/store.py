"""Token-store abstraction — separates the vault's *algorithm* (referential-consistency
tokenization, AES-GCM crypto) from *persistence* so the engine is unit-testable without
a database.

``Vault`` (app/redaction/vault.py) is constructed with a ``TokenStore``. Unit tests use
``InMemoryTokenStore``; production uses ``PostgresTokenStore`` (app/redaction/pg_store.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class TokenRecord:
    session_id: str
    placeholder: str
    entity_type: str
    value_ciphertext: bytes
    value_fingerprint: str
    occurrences: int = 1


class TokenStore(Protocol):
    """Persistence interface for the reversible token vault.

    Implementations MUST enforce, per session:
      * one record per ``value_fingerprint`` (referential consistency — same value maps
        to the same placeholder), and
      * one record per ``placeholder``.
    """

    async def get_by_fingerprint(
        self, session_id: str, fingerprint: str
    ) -> TokenRecord | None: ...

    async def get_by_placeholder(
        self, session_id: str, placeholder: str
    ) -> TokenRecord | None: ...

    async def put(self, record: TokenRecord) -> None: ...

    async def bump_occurrence(self, session_id: str, fingerprint: str) -> None: ...

    async def all_for_session(self, session_id: str) -> list[TokenRecord]: ...


class InMemoryTokenStore:
    """Reference, dependency-free TokenStore for unit tests and air-gapped single-process
    runs. Not durable across restarts."""

    def __init__(self) -> None:
        # (session_id, fingerprint) -> record  and  (session_id, placeholder) -> record
        self._by_fp: dict[tuple[str, str], TokenRecord] = {}
        self._by_ph: dict[tuple[str, str], TokenRecord] = {}

    async def get_by_fingerprint(self, session_id, fingerprint):
        return self._by_fp.get((session_id, fingerprint))

    async def get_by_placeholder(self, session_id, placeholder):
        return self._by_ph.get((session_id, placeholder))

    async def put(self, record: TokenRecord) -> None:
        self._by_fp[(record.session_id, record.value_fingerprint)] = record
        self._by_ph[(record.session_id, record.placeholder)] = record

    async def bump_occurrence(self, session_id, fingerprint) -> None:
        rec = self._by_fp.get((session_id, fingerprint))
        if rec is not None:
            rec.occurrences += 1

    async def all_for_session(self, session_id) -> list[TokenRecord]:
        return [r for (sid, _), r in self._by_fp.items() if sid == session_id]
