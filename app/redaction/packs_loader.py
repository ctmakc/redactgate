"""Persist shipped jurisdiction packs into the ``jurisdiction_pack`` table.

Called from app startup / migrations to make the in-repo YAML packs queryable by the
admin API and referenceable by policies. The YAML files remain the source of truth for
detection; the DB row is metadata (``code``, ``name``, ``entity_types``, ``version`` and a
JSON ``definition`` of the raw patterns).

SECURITY: pack definitions contain only regex/metadata — never real entity values.
"""

from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JurisdictionPack
from app.redaction.regex_packs import all_pack_meta


async def sync_packs_to_db(session: AsyncSession) -> int:
    """Upsert every shipped pack into ``jurisdiction_pack``; return the number processed.

    Idempotent: keyed on the unique ``code`` column with ``ON CONFLICT DO UPDATE`` so
    re-running refreshes ``name``/``entity_types``/``version``/``definition`` in place.
    """
    metas = all_pack_meta()
    if not metas:
        return 0

    table = JurisdictionPack.__table__
    for meta in metas:
        stmt = pg_insert(table).values(
            code=meta["code"],
            name=meta["name"],
            entity_types=meta["entity_types"],
            version=meta["version"],
            definition=meta["definition"],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.code],
            set_={
                "name": stmt.excluded.name,
                "entity_types": stmt.excluded.entity_types,
                "version": stmt.excluded.version,
                "definition": stmt.excluded.definition,
            },
        )
        await session.execute(stmt)

    await session.commit()
    return len(metas)
