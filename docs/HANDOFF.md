# System Handoff — Memory-Plane / Transcript-Annotation Pipeline

_Last updated: 2026-08-13._ Written for a NEW session to pick up where the
previous one left off. The prior session's sandbox/workspace is retired; this
doc captures everything needed to operate the system without rediscovering it.

---

## 1. What this system does (30-second overview)

Agent session transcripts (from multiple harnesses) are archived, then annotated
by a **sealed PDD pipeline** into **reflection packets** (chunking → uncertainty
markers → topic/transition → topic-flow → topic-graph → packet). A **Letta agent
fleet** on the M6 workstation digests those packets — in a one-shot **backlog
survey** pass over the historical corpus, and an ongoing trigger-gated **full
fleet** pass — to produce case studies, reflections, retrospectives, and
**skill-improvement proposals**. Approved proposals are auto-pushed to the
canonical skills repo.

Three key design rules the fleet operates under:
- **Skill generality** — skills stay general-purpose; project-specific
  experiential detail lives in case studies/retrospectives, never in the skills.
- **Skill-improvement synthesis** — after each reflection/retrospective, the
  agent must propose a skill change (or make an explicit disciplined
  `no-proposal` / `naturally-hard` verdict).
- **Model selection** — free-first failover chain per pass, ending on deepseek
  as the paid backup; hard-reasoning synthesis uses deepseek directly.

---

## 2. Repositories

| Repo | Remote | Purpose |
|---|---|---|
| `pdd-repository` | `https://github.com/Tactile-Taco/pdd-repository.git` | Main code: sealed bundles + implementations + memory-plane fleet + backlog/survey runners. **This doc lives here: `docs/HANDOFF.md`** |
| `pdd-registry` | `https://github.com/Tactile-Taco/pdd-registry.git` | Canonical PDD bundle registry: `pdd-bundles/` (9 sealed) + `evidence/` + `registry-catalog.json` (via `pdd index`). |
| skills | `/home/TacticalTaco/skills` (git, origin `https://github.com/Tactile-Taco/skills.git`) | Canonical skills store (`skills/` → 6 skills) + the skill-sync system (`sync/`). |

`pdd-repository` is checked out at `/home/TacticalTaco/.reasonix/global-workspace/pdd-repository`, branch `main`.

---

## 3. The 7 sealed PDD bundles + implementations

Located under `pdd-repository/pdd-bundles/` (protocols) and
`pdd-repository/implementations/<bundle>/python-stdlib/` (attested candidate cores).
All **sealed** (Validator Loop admit → signed evidence → seal).

| Bundle | Role |
|---|---|
| `transcript-chunking` | Render any harness transcript → canonical turns → strict-partition chunk map. **v0.2.0**: dedups reasonix append-archive duplicates. |
| `annotation-store` | Append-only sidecar annotation overlay (never writes the archive). |
| `uncertainty-pass` | LLM-free marker-density annotation. **v0.2.0**: word-boundary matching (no `plan` in `executing-plans`) + contention events. |
| `topic-transition-pass` | LLM topics + transitions (free-first). |
| `topic-flow-review` | LLM flow narrative + findings; mechanical fallback. |
| `topic-graph` | Index-first cross-session topic graph (local embeddings). |
| `reflection-packet` | Distilled packet: overview, tension summary, topic flow, heatmap (raw or baseline-deviation z-score), provenance. |

Registry tooling: `scripts/pdd.py` (CLI), `scripts/build_baselines.py`,
`.reasonix/skills/pdd-protocol-author/` (linter). Gates: `make lint` (9/9),
`python3 -m pytest implementations/`.

**PDD loop** (after any bundle change): `pdd validate <b> --pbt-runs 200` →
`pdd evidence build/verify <b>` (needs `PDD_EVIDENCE_KEY`) → `pdd bundle seal <b>`.

---

## 4. The pipeline + data layout

**Backlog runner** (`implementations/backlog-runner/python-stdlib/backlog_runner.py`):
per transcript runs chunk → uncertainty → topic-transition → flow-review →
topic-graph → packet. **Resumable** via checkpoint journal; paced + 429 backoff.

**Data** (all under `/home/TacticalTaco/.annotation-backlog/`):
- `archive/{reasonix,omp,claude,codex,kimi,hermes}/` — transcript mirror (pulled
  read-only from M6). **Static snapshot** — see "Known issues".
- `store/` — `journal.json`, `cost-ledger.jsonl`, `baselines.json`, `packets/`,
  `chunk-store/`, `topic-graph/`, `<source>/<file>.annotations.jsonl`.
- `fleet.db` — sqlite artifact store (artifacts, proposals, votes, state incl.
  `survey.*` job keys + trigger floors).
- `backlog_status.py`, `monitor.log`.

**Cost**: backlog backfill was **$0.068 total** (759 packets). Survey digestion
~$0.1–0.5 on deepseek-v4-flash.

---

## 5. The Letta fleet (on M6)

- **Letta App Server** on M6: user systemd service `letta-app-server.service`,
  binds `127.0.0.1:4500`, exposed via Tailscale Serve `https://$M6_TAILSCALE_DNS:4500`.
  OpenAI-compat `/v1/chat/completions` (each agent appears as a model) + `/v1/models`.
- **Agents** (provisioned by `pdd-repository/memory-plane/bootstrap.py`):
  `case-study-curator`, `reflection`, `retrospective`, `meta-agent`
  (+ pre-existing `memory-manager`). Standing process lives in the agent's
  `agent.json` `system` field + git-backed MemFS
  (`~/.letta/lc-local-backend/agents/<b64>.json`, `~/.letta/lc-local-backend/memfs/<id>/memory/`).
- **Gotcha**: the `letta` CLI cannot manage this server (401/404 protocol
  mismatch). Provision by writing the registry JSON + MemFS directly, then
  restart the service. Unit must bind `127.0.0.1:4500` (a `0.0.0.0` bind +
  serve rule collides with EADDRINUSE on restart).
- **Drivers** (in `pdd-repository/memory-plane/`):
  - `run_fleet.py` — **full fleet** (trigger-gated: reflection ≥5 days AND ≥5 MB,
    retrospectives at checkpoints, meta cadence). Service `fleet.service` (hourly).
  - `backlog_digestion.py` — **backlog survey** (one-shot kickstart over the
    whole backlog: top-75 case studies, count-bounded reflection batches,
    retrospectives over concluded clusters, one meta pass). Service
    `backlog-digestion.service`. Paced (1.5s) + 429/5xx backoff; resumable.

---

## 6. Services (user systemd)

| Service | Role | State |
|---|---|---|
| `transcript-annotation.service` | backlog runner (packets) | active |
| `fleet.service` | full fleet (hourly) | active |
| `backlog-digestion.service` | backlog survey (one-shot, resumable) | ran / see status |
| `transcript-annotation-monitor.timer` | hourly `backlog_status.py` → `monitor.log` | active |
| `skill-sync-daemon.service` | canonical ⇄ harness skill sync | active |
| `skill-push-notify.service` | desktop toast on skill push | active |

Common ops: `systemctl --user status/is-active/restart <svc>`,
`journalctl --user -u <svc> -f`. Secrets in `~/.config/transcript-annotation.env`
(`BIFROST_KEY`, `LETTA_APP_SERVER_TOKEN`; chmod 600).

---

## 7. Secrets (Infisical)

Project **misc-secrets**, id `5598630f-4109-47d9-bbfb-91bac16ac92c` (env `prod`,
unless noted):
- `LETTA_APP_SERVER_TOKEN` — Bearer for the M6 Letta `/v1` surface.
- `BIFROST_AGENT_VIRTUAL_KEY` — Bifrost LLM gateway key (laptop; also
  auto-resolved by the runners via Infisical).
- `BIFROST_KEY`, `ANYROUTER_API_KEY` (dev env), `PDD_EVIDENCE_KEY`.
- Machine addresses: `M6_TAILSCALE_DNS`, `M6_OPENCLAW_TOKEN`, etc.

Fetch: `infisical secrets get <KEY> --projectId 5598630f-... --env prod --plain --silent`
or `eval "$(infisical export --projectId 5598630f-... --env prod --format=dotenv-eval --silent)"`.

**Model chain** (in `transcript-annotation.env`): free `nousresearch/google/gemini-3.1-flash-lite`
→ `anyrouter/free` → paid `deepseek/deepseek-v4-flash`. Bifrost at
`https://agent-workstation.tail4904d2.ts.net:10000/v1`; AnyRouter at
`https://api.anyrouter.dev/v1` (Cloudflare-fronted — must send a browser UA).

---

## 8. Current status (as of writing)

- **Backlog**: 759/759 packets done, $0.068. Journal `steady`.
- **Per-model baselines** seeded: `deepseek-v4-flash`, `deepseek-flash`,
  `kimi-k2.6`, `unknown` (kimi-source keyed by session date → kimi-k2.6/k3;
  `<synthetic>` fixtures dropped).
- **Backlog survey digestion**: **in progress** — 80/92 jobs done (75/75
  case studies + 5/16 reflections), 5 `no-proposal` proposals so far, meta
  memory seeded. Check: `sqlite3 fleet.db "SELECT key FROM state WHERE key LIKE
  'survey.%' AND value='done'"` (count) — done when it reaches 92 / service goes
  `inactive (success)`.
- **Full fleet**: armed, no artifacts yet (waiting on the ≥5-day trigger floor).
- **Skills**: 6 canonical; sync daemon active; last push was the origin-aware
  propagation fix.

---

## 9. How a new session resumes

1. `cd ~/.reasonix/global-workspace/pdd-repository && git pull` (this doc + code).
2. Check status: `python3 ~/.annotation-backlog/backlog_status.py`.
3. Confirm services: `systemctl --user is-active transcript-annotation fleet
   backlog-digestion skill-sync-daemon`.
4. If the survey is still running, wait for it to finish, then review
   `fleet.db` proposals (any `new-skill`/`edit-skill` → peer review → push).
5. For operational details, read the **m6-agent-workstation** skill
   (`~/.reasonix/skills/m6-agent-workstation/SKILL.md`) — it documents addresses,
   Letta, Bifrost, the backlog/fleet/monitors, troubleshooting.
6. To change a bundle: follow the PDD loop (§3).

---

## 10. Known issues / pending work

- **Archive mirror is static.** `~/.annotation-backlog/archive/` was pulled once
  from M6; new transcripts aren't flowing in. Wire a refresh into the existing
  hourly transcript-sync (or a periodic rsync) so the backlog runner sees new
  sessions. (Until then the backlog runner is idle.)
- **Survey completion**: verify it reaches 92/92; review the produced
  reflections + any proposals.
- **ImpossibleBench trace collection**: for under-covered models, a small
  subset of `fjzzq2002/impossible_livecodebench` (cached) can elicit
  reasoning-trace uncertainty data to enrich baselines. Don't run full suites.
- **`unknown` baseline** is large (reasonix `sa_` subagent files); consider a
  finer key if desired.
- **skill-usage signal** (for the case-study trigger: skill-usage ∩ hot-patch)
  is not yet produced — case-study trigger currently uses hot-patch alone.
- **0 retrospective jobs** so far — needs a concluded cluster ≥5 sessions.
