"""SQLAlchemy ORM models mirroring migrations/001_init.sql.

The SQL migration is the canonical DDL (and the grant artifact); these models map onto
it for the app. Keep the two in sync — see tests/test_schema_parity if present.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


TS = TIMESTAMP(timezone=True)


class Org(Base):
    __tablename__ = "org"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())

    teams: Mapped[list[Team]] = relationship(back_populates="org", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "team"
    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    default_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())

    org: Mapped[Org] = relationship(back_populates="teams")


class ApiKey(Base):
    __tablename__ = "api_key"
    id: Mapped[uuid.UUID] = _uuid_pk()
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("team.id", ondelete="CASCADE"), nullable=False
    )
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(TS)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


class JurisdictionPack(Base):
    __tablename__ = "jurisdiction_pack"
    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False, default="1.0.0")
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)


class Policy(Base):
    __tablename__ = "policy"
    __table_args__ = (
        CheckConstraint("mode IN ('tokenize','mask','hard_block')", name="policy_mode_chk"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("org.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    pack_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False, default="tokenize")
    blocked_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    allowed_providers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


class RedactionSession(Base):
    __tablename__ = "redaction_session"
    id: Mapped[uuid.UUID] = _uuid_pk()
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team.id"), nullable=False)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy.id"), nullable=False)
    document_hash: Mapped[str | None] = mapped_column(Text)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(TS, nullable=False)

    tokens: Mapped[list[TokenMap]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class TokenMap(Base):
    __tablename__ = "token_map"
    __table_args__ = (
        UniqueConstraint("session_id", "value_fingerprint", name="uq_tokenmap_fp"),
        UniqueConstraint("session_id", "placeholder", name="uq_tokenmap_ph"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("redaction_session.id", ondelete="CASCADE"), nullable=False, index=True
    )
    placeholder: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    value_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    value_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())

    session: Mapped[RedactionSession] = relationship(back_populates="tokens")


class AuditEvent(Base):
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("team.id"), nullable=False)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_key.id"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("redaction_session.id"))
    route: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    entity_counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prev_hash: Mapped[str] = mapped_column(Text, nullable=False)
    event_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


class EvalRun(Base):
    __tablename__ = "eval_run"
    id: Mapped[uuid.UUID] = _uuid_pk()
    pack_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jurisdiction_pack.id"))
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    golden_set: Mapped[str] = mapped_column(Text, nullable=False)
    recall: Mapped[float | None] = mapped_column(Numeric(5, 4))
    precision: Mapped[float | None] = mapped_column(Numeric(5, 4))
    answer_fidelity: Mapped[float | None] = mapped_column(Numeric(5, 4))
    created_at: Mapped[dt.datetime] = mapped_column(TS, server_default=func.now())


# Silence "imported but unused" for re-exported types kept for callers' convenience.
_ = (String, BigInteger)
