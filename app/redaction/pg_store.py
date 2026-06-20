"""PostgreSQL-backed :class:`TokenStore` over an async SQLAlchemy session.

Production persistence for the vault. Maps the in-memory ``TokenRecord`` contract onto the
``token_map`` / ``redaction_session`` tables (app/models.py), relying on the
``UNIQUE(session_id, value_fingerprint)`` and ``UNIQUE(session_id, placeholder)``
constraints for referential consistency and idempotency.

SECURITY: only ciphertext (``value_ciphertext``) and the keyed ``value_fingerprint`` are
persisted — never a raw entity value.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import RedactionSession, TokenMap
from app.redaction.store import TokenRecord

# Seeded default tenant (migrations/002_seed.sql) — used in single-tenant dev when no
# team/policy is supplied.
_DEFAULT_TEAM_ID = "00000000-0000-0000-0000-0000000000bb"
_DEFAULT_POLICY_ID = "00000000-0000-0000-0000-0000000000cc"


def _to_record(row: TokenMap) -> TokenRecord:
    return TokenRecord(
        session_id=str(row.session_id),
        placeholder=row.placeholder,
        entity_type=row.entity_type,
        value_ciphertext=bytes(row.value_ciphertext),
        value_fingerprint=row.value_fingerprint,
        occurrences=row.occurrences,
    )


class PostgresTokenStore:
    """Durable :class:`TokenStore` backed by an :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(
        self,
        *,
        team_id: str | None = None,
        policy_id: str | None = None,
        document_hash: str | None = None,
        wrapped_dek: bytes = b"",
    ) -> str:
        """Insert a ``redaction_session`` row and return its id as a string."""
        now = dt.datetime.now(dt.UTC)
        row = RedactionSession(
            team_id=uuid.UUID(team_id or _DEFAULT_TEAM_ID),
            policy_id=uuid.UUID(policy_id or _DEFAULT_POLICY_ID),
            document_hash=document_hash,
            wrapped_dek=wrapped_dek,
            expires_at=now + dt.timedelta(hours=settings.session_ttl_hours),
        )
        self._session.add(row)
        await self._session.flush()
        return str(row.id)

    async def get_by_fingerprint(
        self, session_id: str, fingerprint: str
    ) -> TokenRecord | None:
        stmt = select(TokenMap).where(
            TokenMap.session_id == uuid.UUID(session_id),
            TokenMap.value_fingerprint == fingerprint,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def get_by_placeholder(
        self, session_id: str, placeholder: str
    ) -> TokenRecord | None:
        stmt = select(TokenMap).where(
            TokenMap.session_id == uuid.UUID(session_id),
            TokenMap.placeholder == placeholder,
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _to_record(row) if row is not None else None

    async def put(self, record: TokenRecord) -> None:
        """Insert a token record, idempotent on ``UNIQUE(session_id, fingerprint)``.

        Uses ``ON CONFLICT DO NOTHING`` so a concurrent insert of the same value (same
        fingerprint) does not raise — referential consistency holds regardless of which
        writer won the race.
        """
        stmt = (
            pg_insert(TokenMap)
            .values(
                session_id=uuid.UUID(record.session_id),
                placeholder=record.placeholder,
                entity_type=record.entity_type,
                value_ciphertext=record.value_ciphertext,
                value_fingerprint=record.value_fingerprint,
                occurrences=record.occurrences,
            )
            .on_conflict_do_nothing(
                index_elements=[TokenMap.session_id, TokenMap.value_fingerprint]
            )
        )
        await self._session.execute(stmt)

    async def bump_occurrence(self, session_id: str, fingerprint: str) -> None:
        stmt = (
            update(TokenMap)
            .where(
                TokenMap.session_id == uuid.UUID(session_id),
                TokenMap.value_fingerprint == fingerprint,
            )
            .values(occurrences=TokenMap.occurrences + 1)
        )
        await self._session.execute(stmt)

    async def all_for_session(self, session_id: str) -> list[TokenRecord]:
        stmt = select(TokenMap).where(TokenMap.session_id == uuid.UUID(session_id))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_record(r) for r in rows]
