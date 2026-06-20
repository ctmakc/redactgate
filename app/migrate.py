"""Tiny forward-only SQL migration runner.

Applies every ``migrations/*.sql`` in lexical order inside one transaction each. The DDL
is written idempotently (CREATE ... IF NOT EXISTS / ON CONFLICT DO NOTHING) so re-running
on each boot is safe and avoids needing Alembic for this scope.
"""

from __future__ import annotations

import pathlib

from sqlalchemy import text

from app.db import get_engine

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations"


def migration_files() -> list[pathlib.Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def apply_migrations() -> list[str]:
    """Run all migration files. Returns the list of applied filenames."""
    applied: list[str] = []
    engine = get_engine()
    for path in migration_files():
        sql = path.read_text(encoding="utf-8")
        async with engine.begin() as conn:
            # asyncpg can run multi-statement scripts via exec_driver_sql
            await conn.exec_driver_sql(sql)
        applied.append(path.name)
    return applied
