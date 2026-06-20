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


async def apply_migrations() -> list[str]:
    """Run all migration files. Returns the list of applied filenames.

    Each file is a multi-statement SQL script (incl. a PL/pgSQL trigger with ``$$``
    bodies), so we run it through the raw asyncpg connection's simple-query protocol —
    SQLAlchemy/asyncpg's default prepared-statement path rejects multiple commands.
    """
    applied: list[str] = []
    engine = get_engine()
    for path in migration_files():
        sql = path.read_text(encoding="utf-8")
        async with engine.connect() as conn:
            raw = await conn.get_raw_connection()
            # underlying asyncpg.Connection — execute() supports multi-statement scripts
            await raw.driver_connection.execute(sql)
        applied.append(path.name)
    return applied
