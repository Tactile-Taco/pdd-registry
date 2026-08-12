#!/usr/bin/env python3
"""Provision the memory-plane fleet agents on the M6 Letta App Server.

Writes, for each agent in AGENT_DEFS:
  ~/.letta/lc-local-backend/agents/<base64(id)>.json   (registry entry)
  ~/.letta/lc-local-backend/memfs/<id>/memory/          (git-backed MemFS:
     system/persona.md, system/human.md)

then restarts letta-app-server.service and verifies /v1/models lists every
agent. Idempotent (overwrites registry + memfs, commits). The registry JSON
mirrors the memory-manager template exactly (model + model_settings).

Usage:
  python3 bootstrap.py --dry-run      # print steps only
  python3 bootstrap.py                # provision + restart + verify
  python3 bootstrap.py --host m6

Run from the laptop over ssh (the M6 has no GitHub credentials but this only
touches local files + the user systemd service). Reversible: delete the
registry JSONs and memfs dirs, restart the service.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time

from memory_plane.agent_defs import AGENT_DEFS, LETTA_MODEL

# Same model_settings as the memory-manager template (captured from the M6).
MODEL_SETTINGS = {
    "provider_type": "openai-compatible",
    "context_window_limit": 272000,
    "max_tokens": 128000,
    "parallel_tool_calls": True,
}

HUMAN_MD = """\
---
description: What I know about the person I work with.
---
I'm a fleet agent of the memory plane. The fleet digests distilled reflection
packets to improve skills over time.
"""


def _run(cmd: list[str]) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:2])} failed: {out.stderr[:400]}")
    return out.stdout


def registry_entry(a: dict) -> dict:
    return {
        "id": a["id"],
        "name": a["name"],
        "description": a["description"],
        "model": a.get("model", LETTA_MODEL),
        "model_settings": MODEL_SETTINGS,
        "system": a["system"],
        "tags": a.get("tags", ["memory-plane", "fleet"]),
    }


def provision(host: str = "m6", dry_run: bool = False) -> None:
    agents_dir = "~/.letta/lc-local-backend/agents"
    memfs_dir = "~/.letta/lc-local-backend/memfs"
    for a in AGENT_DEFS:
        b64 = base64.b64encode(a["id"].encode()).decode()
        reg_path = f"{agents_dir}/{b64}.json"
        mem_path = f"{memfs_dir}/{a['id']}/memory"
        persona = ("---\ndescription: " + a["description"] + "\n---\n\n"
                   + a.get("persona_md", "") + "\n")
        if dry_run:
            print(f"[dry-run] write {reg_path} ({a['name']})")
            print(f"[dry-run] write {mem_path}/system/persona.md")
            print(f"[dry-run] write {mem_path}/system/human.md")
            continue
        # base64 transport: the contents contain quotes/newlines that would
        # mangle shell quoting otherwise.
        reg_b64 = base64.b64encode(json.dumps(registry_entry(a)).encode()).decode()
        persona_b64 = base64.b64encode(persona.encode()).decode()
        human_b64 = base64.b64encode(HUMAN_MD.encode()).decode()
        _run(["ssh", host,
              f"mkdir -p {mem_path}/system && "
              f"echo {reg_b64} | base64 -d > {reg_path} && "
              f"echo {persona_b64} | base64 -d > {mem_path}/system/persona.md && "
              f"echo {human_b64} | base64 -d > {mem_path}/system/human.md && "
              f"cd {mem_path} && git init -q 2>/dev/null; "
              f"git add -A && git -c user.name=memory-plane -c user.email=fleet@local "
              f"commit -q -m 'provision {a['name']}' 2>/dev/null; true"])
        print(f"provisioned {a['name']} ({a['id']})")
    if dry_run:
        print("[dry-run] systemctl --user restart letta-app-server.service")
        print("[dry-run] verify: GET /v1/models")
        return
    _run(["ssh", host, "systemctl --user restart letta-app-server.service"])
    print("restarted letta-app-server.service")


def verify(host: str = "m6", token: str | None = None) -> None:
    """Check /v1/models lists all fleet agents (run from the laptop)."""
    if not token:
        token = os.environ.get("LETTA_APP_SERVER_TOKEN", "")
    dns = os.environ.get("M6_TAILSCALE_DNS",
                         "agent-workstation.tail4904d2.ts.net")
    import urllib.request
    req = urllib.request.Request(
        f"https://{dns}:4500/v1/models",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        models = json.loads(resp.read().decode())["data"]
    ids = {m["id"] for m in models}
    missing = [a["name"] for a in AGENT_DEFS if a["id"] not in ids]
    if missing:
        raise RuntimeError(f"agents missing from /v1/models: {missing}")
    print(f"OK: {len(ids)} agents on the server: "
          + ", ".join(sorted(m["id"] for m in models)))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bootstrap-fleet")
    ap.add_argument("--host", default="m6")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args(argv)
    if args.verify_only:
        verify(args.host)
        return 0
    provision(args.host, dry_run=args.dry_run)
    if not args.dry_run:
        time.sleep(3)
        verify(args.host)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
