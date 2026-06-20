"""Async background worker (arq) for RedactGate.

Hosts long-running / scheduled jobs that should not block the request path — currently the
fidelity-evaluation job, which runs the detection + answer-fidelity benchmark for a
jurisdiction pack against a provider and records an ``eval_run`` row.

Importing this module is cheap and side-effect-free: ``redis``/``arq`` connection objects
are built lazily (inside ``WorkerSettings``/the task body), so ``import app.worker`` works
with Redis down and pulls in the heavy detection stack only when a job actually runs.

Run the worker with::

    arq app.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.config import settings


def _redis_settings() -> RedisSettings:
    """Parse arq ``RedisSettings`` from ``settings.redis_url``.

    ``RedisSettings.from_dsn`` only parses the DSN — it opens no connection — so calling
    this never requires Redis to be reachable.
    """
    return RedisSettings.from_dsn(settings.redis_url)


async def run_fidelity_eval(
    ctx: dict[str, Any], pack_code: str, provider: str
) -> dict[str, Any]:
    """Run the fidelity/detection benchmark and persist an ``eval_run`` row.

    ``pack_code`` selects the bundled golden set (e.g. ``"ca"``/``"us"``/``"eu"``);
    ``provider`` is the provider used for the optional answer-fidelity pass. Heavy imports
    (the eval harness, ORM models, DB session) are deferred to here so module import stays
    light. Returns the harness result dict (also persisted).
    """
    # Lazy imports — keep `import app.worker` free of DB/eval/redis dependencies.
    from app.db import session_scope
    from app.models import EvalRun
    from eval.harness import run_eval

    golden_set = (pack_code or "").strip().lower()
    result = await run_eval(golden_set, provider)

    recall = result.get("recall")
    precision = result.get("precision")
    fidelity = result.get("answer_fidelity")

    async with session_scope() as session:
        session.add(
            EvalRun(
                pack_id=None,
                provider=provider,
                golden_set=golden_set,
                recall=recall,
                precision=precision,
                answer_fidelity=fidelity,
            )
        )
        await session.commit()

    return result


class WorkerSettings:
    """arq worker configuration.

    arq reads job/connection config off this class's ``__dict__`` (not an instance), so
    ``redis_settings`` and ``functions`` are plain class attributes. Building
    ``redis_settings`` parses the DSN only and does not connect to Redis.
    """

    functions = [run_fidelity_eval]
    redis_settings: RedisSettings = _redis_settings()

