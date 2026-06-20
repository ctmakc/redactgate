"""Tiny forward-only SQL migration runner.

Applies every ``migrations/*.sql`` in lexical order inside one transaction each. The DDL
is written idempotently (CREATE ... IF NOT EXISTS / ON CONFLICT DO NOTHING) so re-running
on each boot is safe and avoids needing Alembic for this scope.
"""

from __future__ import annotations

import pathlib

from app.db import get_engine

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def migration_files() -> list[pathlib.Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


# Arbitrary constant key for the migration advisory lock (shared by all workers).
_MIGRATION_LOCK_KEY = 727274


async def apply_migrations() -> list[str]:
    """Run all migration files under a Postgres advisory lock. Returns applied filenames.

    Each file is a multi-statement SQL script (incl. a PL/pgSQL trigger with ``$$``
    bodies), so we run it through the raw asyncpg connection's simple-query protocol —
    SQLAlchemy/asyncpg's default prepared-statement path rejects multiple commands.

    A session-level advisory lock serializes startup across gunicorn workers (every worker
    runs the lifespan): without it, concurrent ``CREATE TABLE`` / trigger DDL deadlocks.
    The first worker applies; the rest wait, then re-run the idempotent DDL as no-ops. All
    files run on the ONE locked connection so the critical section is truly serialized.
    """
    applied: list[str] = []
    engine = get_engine()
    async with engine.connect() as conn:
        raw = (await conn.get_raw_connection()).driver_connection
        await raw.execute(f"SELECT pg_advisory_lock({_MIGRATION_LOCK_KEY})")
        try:
            for path in migration_files():
                await raw.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
        finally:
            await raw.execute(f"SELECT pg_advisory_unlock({_MIGRATION_LOCK_KEY})")
    return applied
