"""HTTP route modules.

Each submodule exposes a ``router`` (an :class:`fastapi.APIRouter`) that ``app.main``
wires into the application:

  * :mod:`app.routes.health` — ``GET /healthz``, ``GET /readyz``
  * :mod:`app.routes.proxy`  — ``POST /v1/chat/completions``, ``POST /v1/responses``, ``GET /v1/models``
  * :mod:`app.routes.admin`  — ``/admin/*`` dashboards, audit, policies, benchmarks
"""

from __future__ import annotations

__all__: list[str] = ["admin", "health", "proxy"]
