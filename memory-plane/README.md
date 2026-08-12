# Memory Plane — the fleet

The Letta agent fleet that digests the annotation pipeline's reflection
packets into case studies, reflections, retrospectives, system memories, and
**skill-improvement proposals** (the synthesis step). Consumer side of
`docs/memory-plane-design.md`; packets come from the sealed
transcript-annotation bundles via the backlog runner's store dir.

## Layout

```
memory-plane/
  memory_plane/
    agent_defs.py   — the 4 agents: standing process (system prompt), task
                      templates, output schemas; the skill-improvement
                      synthesis step lives in reflection + retrospective
    triggers.py     — annotation-derived triggers (deterministic, tested)
    store.py        — sqlite artifact store + provenance edges + proposals
    proposals.py    — proposal validation (grounding rule, frontmatter,
                      disciplined no-proposal verdicts)
    review.py       — peer review: votes from the other fleet agents,
                      unanimous-approve tally (fail-closed parsing)
    push.py         — canonical skills repo push (frontmatter + Provenance,
                      git commit + push; dry-run)
    client.py       — Letta backend (M6 App Server) | direct Bifrost chain |
                      stub (tests)
    fleet.py        — orchestrator: triggers → agent run → artifact →
                      proposals → review → push
  bootstrap.py      — provision the 4 agents on the M6 Letta App Server
  run_fleet.py      — CLI (--once / --watch)
  tests/            — 47 tests; all model calls stubbed, no network
```

## PDD boundary (read this first)

The fleet is a **client system outside the PDD Validator Loop**. Agents are
stochastic; they are validated by schema-shaped output contracts (required
keys + type, one retry with error feedback) and by peer review — NOT by the
invariant/evidence machinery that gates sealed bundles. Everything
deterministic here (triggers, store, validation, review tally, git push) IS
unit-tested. This is a deliberate boundary decision, not a shortcut: an
attested agent is not an achievable or useful target.

## Triggers (v1 signals, no protocol changes)

The sealed bundles do not yet emit the v0.2 flags the design mentions
(skill-usage layer, cluster lifecycle in the packet, flow_graph). v1 computes
equivalent signals from what the bundles DO emit:

| Design trigger | v1 signal |
|---|---|
| case study: skill-usage ∩ hot-patch | hot-patch only (heatmap cells z>1.5 over ≥3 contiguous cells); skill-usage is a future pipeline flag |
| reflection: ≥5 d AND ≥5 MB, or concluded topic | same cadence floor; "concluded" = graph cluster (similar-edge component) of ≥6 sessions with no packet activity ≥14 d (mtime proxy) |
| retrospective: cluster concluded / heatmap anomaly / ~25 MB | all three, computed from graph + heatmap cells + packet bytes |
| meta: cadence / accumulation | ≥3 open proposals or ≥7 d since last meta |

Floors are stored in the artifact-store `state` table, so runs are resumable
and idempotent (a second run the same day does not re-fire).

## Backends

- **letta** (default): drives the Letta agents on the M6 App Server
  (OpenAI-compat `/v1/chat/completions`, `model=<agent-id>`). The agent's
  standing process lives in its system prompt (provisioned by
  `bootstrap.py`); per-run task context (artifact refs + instructions) goes
  in the user message.
- **direct**: falls back to the Bifrost gateway with the free-first failover
  chain (nousresearch → deepseek backup). The standing process is prepended
  to the task. Useful when the Letta server is down or for dry validation.
- **stub**: scripted responses; used by the test suite.

Secrets: `LETTA_APP_SERVER_TOKEN` / `LETTA_BASE_URL` (letta),
`BIFROST_KEY` / `BIFROST_URL` / `MODEL_CHAIN` (direct). All from Infisical
(misc-secrets project `5598630f-4109-47d9-bbfb-91bac16ac92c`) unless set in
the environment.

## Deploying the agents (one-time)

```bash
eval "$(infisical export --projectId 5598630f-4109-47d9-bbfb-91bac16ac92c \
  --env prod --format=dotenv-eval --silent)"
python3 bootstrap.py --dry-run     # review the plan
python3 bootstrap.py               # provision on m6 + restart service + verify
```

`bootstrap.py` writes each agent's registry entry
(`~/.letta/lc-local-backend/agents/<b64(id)>.json`) and its git-backed MemFS
(`memfs/<id>/memory/` with `system/persona.md` + `system/human.md`), restarts
`letta-app-server.service`, and verifies `/v1/models` lists every agent.
Reversible: delete the registry JSONs + memfs dirs, restart the service.

## Running the fleet

```bash
python3 run_fleet.py --store ../annotation-store --db fleet.db \
    --backend letta --skills-repo /home/TacticalTaco/skills --once
python3 run_fleet.py --store ... --backend direct --dry-run   # offline sanity
```

`--watch` repeats on an interval (default 3600 s). The runner is meant to
pair with the backlog runner's `--watch` mode; a systemd user unit can wrap
it on the M6 or the laptop (see `runner/` in the repo root for the pattern).

## Skill push flow

Approved proposals (unanimous peer review) are committed + pushed to the
canonical skills repo (`/home/TacticalTaco/skills`), where the existing
sync pipeline (`sync/sync-skills.sh`) distributes them to harnesses. New
skills carry mandatory frontmatter (name + description) and a
`## Provenance` section citing the motivating artifacts (the grounding
rule). Process-skill proposals update fleet memory only — never the repo.
