#!/usr/bin/env python3
"""Fleet runner CLI.

Usage:
  python3 run_fleet.py --store ./annotation-store --db fleet.db \
      --backend letta --skills-repo /home/TacticalTaco/skills --once
  python3 run_fleet.py --store ... --backend direct --once --dry-run

Backends: letta (M6 App Server), direct (Bifrost free-first chain), stub.
Env: LETTA_APP_SERVER_TOKEN / LETTA_BASE_URL, BIFROST_KEY / BIFROST_URL /
MODEL_CHAIN.
"""

from __future__ import annotations

import argparse
import sys

from memory_plane.client import make_client
from memory_plane.fleet import FleetRunner, run_forever


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run-fleet")
    ap.add_argument("--store", default="./annotation-store",
                    help="pipeline store dir (packets/, topic-graph/, ...)")
    ap.add_argument("--db", default="fleet.db", help="sqlite artifact store")
    ap.add_argument("--backend", default="letta",
                    choices=["letta", "direct", "stub"])
    ap.add_argument("--skills-repo", default="/home/TacticalTaco/skills",
                    help="canonical skills repo for proposal pushes (None disables)")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--watch-seconds", type=int, default=3600)
    ap.add_argument("--dry-run", action="store_true",
                    help="no model calls beyond triggers? (no: dry-run only "
                         "skips review+push; use --backend stub for offline)")
    ap.add_argument("--sync-memory", action="store_true",
                    help="write meta-agent system memories + process skills "
                         "into its Letta MemFS on the M6 (default-way memory)")
    ap.add_argument("--memfs-host", default="m6")
    args = ap.parse_args(argv)

    client = make_client(args.backend)
    runner = FleetRunner(args.store, client, db_path=args.db,
                         skills_repo=args.skills_repo, dry_run=args.dry_run,
                         sync_memory=args.sync_memory,
                         memfs_host=args.memfs_host)
    try:
        if args.once:
            print(__import__("json").dumps(runner.run_once(), sort_keys=True,
                                           default=str))
            return 0
        run_forever(runner, watch_seconds=args.watch_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        runner.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
