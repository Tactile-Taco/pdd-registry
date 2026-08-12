"""Fleet memory sync — the default-way Letta memory loop for fleet outputs.

The Letta agents are experiential by default: each has a git-backed MemFS
(memory/system/persona.md, human.md) that the App Server loads into context
per conversation, and the agents evolve those files as they work. What the
fleet adds is the reverse direction: the meta-agent's SYSTEM MEMORIES and
PROCESS SKILLS (design: they live in Letta memory, never the canonical skills
repo) are written into its MemFS so they persist and load on the next
conversation. Transport mirrors bootstrap.py: base64 over ssh, git commit as
the audit trail; dry_run renders without touching the remote.
"""

from __future__ import annotations

import base64
import datetime
import re
import subprocess

# Same safety constraint as bootstrap: ids become paths/shell literals.
SAFE_ID_RE = re.compile(r"^[a-z0-9-]+$")

MEMORIES_HEADER = """\
---
description: Durable system memories maintained by the meta-agent. These are
principles and patterns that hold across sessions; never per-session trivia.
---

"""

PROCESS_HEADER = """\
---
description: Fleet process skills — how the fleet agents operate. These live
in Letta memory by design and must NOT be synced to the canonical skills repo.
---

"""


def _as_bullet(value: str) -> str:
    return value.strip().replace("\n", " ")


def render_memories(memories: list[dict], period: str | None = None) -> str:
    """memories: [{"key": ..., "value": ...}, ...] -> markdown for MemFS."""
    lines = [MEMORIES_HEADER.rstrip()]
    lines.append(f"## Memories ({period or datetime.date.today().isoformat()})")
    lines.append("")
    for m in memories or []:
        key = _as_bullet(str(m.get("key", "memory")))
        value = _as_bullet(str(m.get("value", "")))
        lines.append(f"- **{key}**: {value}")
    return "\n".join(lines) + "\n"


def render_process_skills(updates: list[dict]) -> str:
    """process_updates: [proposal dicts (kind process-skill)] -> markdown."""
    lines = [PROCESS_HEADER.rstrip()]
    lines.append(f"## Process skills (updated {datetime.date.today().isoformat()})")
    lines.append("")
    for u in updates or []:
        title = _as_bullet(str(u.get("description") or u.get("title")
                              or u.get("proposal_id", "process-skill")))
        lines.append(f"### {title}")
        body = str(u.get("body") or "").strip()
        if body:
            lines.append(body)
        reasoning = _as_bullet(str(u.get("reasoning") or ""))
        if reasoning:
            lines.append("")
            lines.append(f"Reasoning: {reasoning}")
        lines.append("")
    return "\n".join(lines) + "\n"


def sync_memfs(host: str, agent_id: str, files: dict[str, str],
               *, dry_run: bool = False) -> list[str]:
    """Write `files` (name -> content) into the agent's MemFS system dir on
    the remote and commit. Returns the written file names. Reversible via git
    history; never touches anything outside the agent's memory dir."""
    if not SAFE_ID_RE.match(agent_id):
        raise ValueError(f"unsafe agent id: {agent_id!r}")
    for name in files:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            raise ValueError(f"unsafe memory file name: {name!r}")
    written = []
    mem_path = f"~/.letta/lc-local-backend/memfs/{agent_id}/memory"
    if dry_run:
        return [f"{name} (dry-run)" for name in files]
    cmd = [f"mkdir -p {mem_path}/system"]
    for name, content in files.items():
        b64 = base64.b64encode(content.encode()).decode()
        cmd.append(f"echo {b64} | base64 -d > {mem_path}/system/{name}")
        written.append(name)
    cmd.append(f"cd {mem_path} && git init -q && git add -A && "
               f"(git diff --cached --quiet || git -c user.name=memory-plane "
               f"-c user.email=fleet@local commit -q -m 'fleet memory sync')")
    out = subprocess.run(["ssh", host, " && ".join(cmd)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"memory sync failed: {out.stderr[:400]}")
    return written
