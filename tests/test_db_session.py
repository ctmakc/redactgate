"""Regression: the request-scoped session must COMMIT on a clean exit.

Without this, every proxied request's writes — including the hash-chained audit trail —
are silently rolled back and nothing is ever persisted for real traffic.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import integration_only


@integration_only()
async def test_get_session_commits_on_clean_exit():
    from sqlalchemy import text

    from app.db import get_session, session_scope

    org_id = str(uuid.uuid4())

    # Drive the dependency generator: get a session, write, then finalize it (which must
    # commit on a clean exit) — WITHOUT calling session.commit() ourselves.
    agen = get_session()
    session = await agen.__anext__()
    await session.execute(
        text("INSERT INTO org(id, name) VALUES (:o, 'commit-regression')"), {"o": org_id}
    )
    with pytest.raises(StopAsyncIteration):
        await agen.__anext__()  # finalize -> commit-on-exit

    # A brand-new session must see the committed row.
    async with session_scope() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM org WHERE id = :o"), {"o": org_id})
        ).scalar()
    assert n == 1


@integration_only()
async def test_get_session_rolls_back_on_error():
    from sqlalchemy import text

    from app.db import get_session, session_scope

    org_id = str(uuid.uuid4())
    agen = get_session()
    session = await agen.__anext__()
    await session.execute(
        text("INSERT INTO org(id, name) VALUES (:o, 'rollback-regression')"), {"o": org_id}
    )
    # Inject an error into the dependency body -> must roll back.
    with pytest.raises(RuntimeError):
        await agen.athrow(RuntimeError("boom"))

    async with session_scope() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM org WHERE id = :o"), {"o": org_id})
        ).scalar()
    assert n == 0
