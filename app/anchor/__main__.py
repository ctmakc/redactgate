"""CLI: ``python -m app.anchor {run-once|verify --team <uuid>|status --team <uuid>}``."""

from __future__ import annotations

import argparse
import asyncio
import json


async def _main(argv: list[str] | None = None) -> int:
    from app.anchor.service import (
        AnchorConfig,
        latest_anchor,
        run_anchor_pass,
        stream_tag,
        verify_team,
    )
    from app.config import settings
    from app.db import session_scope

    p = argparse.ArgumentParser(prog="python -m app.anchor")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run-once", help="anchor every team whose audit chain grew")
    v = sub.add_parser("verify", help="check a team's chain against its NEAR anchor")
    v.add_argument("--team", required=True)
    s = sub.add_parser("status", help="show the latest NEAR anchor for a team")
    s.add_argument("--team", required=True)
    args = p.parse_args(argv)

    cfg = AnchorConfig.from_settings()
    if args.cmd == "status":
        stream = stream_tag(args.team, settings.key_bytes("audit_hmac_key"))
        print(json.dumps({"stream": stream,
                          "anchor": await latest_anchor(cfg.client(), cfg, stream)}, indent=2))
        return 0
    async with session_scope() as session:
        if args.cmd == "run-once":
            print(json.dumps(await run_anchor_pass(session, cfg, note="cli"), indent=2))
            return 0
        res = await verify_team(session, args.team, cfg)
        print(json.dumps(res.to_dict(), indent=2))
        return 0 if res.ok else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
