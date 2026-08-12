# Backlog Execution Plan — temporary kickstart process

One-shot process to seed the system from the historical corpus so the primary process
(see `memory-plane-design.md`) starts with a real base. It runs once, is bounded, and
exits. The archive is the M6 `transcript-archive` (759 files, 237 MB, immutable).

## Goal

Annotation store populated, topic-graph pre-seeded with lifecycle states, knowledge
store seeded with case studies/reflections/retrospectives, and a minimal memory base.
Skill workspace left intact for the primary process.

## Locked decisions

- **Last 5-10 transcripts** (by timestamp) → **normal agent digestion**, exactly as
  the primary process does it. Validates the pipeline end-to-end.
- **Everything older** → accelerated path (phases below).
- **Free models for topic/transition across the backlog** — near-$0 cost, at the price
  of wall-clock (rate limits → budget days, not hours). Background job, acceptable.
- **Agent digestion is the only paid slice** (~$5-15 one-time) — or free-first if
  preferred.
- **K = 75** case-study candidates (default).
- **No mass skill edits.** Backlog skill proposals flow through the same peer-review
  gate, biased toward few/high-confidence historical edits.
- **Full provenance** on every backlog artifact (same evidence links).

## Phases

- **A. Bulk pipeline** — run all 7 bundles over the whole archive. LLM-free passes
  (chunking, uncertainty, packet build) cost $0; topic/transition uses free models.
- **B. Pre-seed + rank** — build topic-graph with cluster lifecycle states; rank
  sessions by the same triggers the primary process uses (skill-usage ∩ hot-patch,
  cluster maturity). Output: ranked candidate list.
- **C. Accelerated digestion** — the 4 agents in survey mode:
  - case-study curator over top-K candidates (each = goal + progress + friction);
  - reflection agent over chronological ~5 MB batches (≈50 reflections), instead of
    waiting 5 days;
  - retrospective agent at accelerated annotation checkpoints (major clusters
    concluding, aggregated heatmap anomalies);
  - meta-agent last, observing Phase C and producing seeded memories.
- **D. Handoff** — seeded memories written to Letta in one controlled pass (distilled
  from artifacts, never raw transcripts — anti-pollution rule). Pre-populate cluster
  lifecycle, knowledge store, skill workspace. Primary process takes over; temporary
  process exits.

## Runner / resumability (how it survives interruption)

- **Checkpoint ledger** (the real insurance): per-file state `pending | done | failed`
  + pass version + retry count. On any interruption, re-running the same command
  skips `done` files and retries `failed` with backoff. Same journal pattern as the
  skill-sync system.
- **Passes are idempotent + resumable by file** — the draft bundles already key
  everything by file, and annotation-store supersede makes re-runs safe.
- **systemd user service**, not tmux/nohup: `Restart=on-failure RestartSec=30`, logs
  to journald. No terminal dependency. Monitor with `systemctl --user status
  <backlog-service>` and `journalctl --user -u <backlog-service> -f`. On reboot the
  service restarts and resumes from the ledger.
- **Run on the always-on box**: the archive is on the M6 — run the runner there,
  calling Bifrost on the laptop over tailnet. If running on the laptop instead,
  ensure the machine cannot suspend mid-run (the one failure mode systemd can't fix).
- **Rate-limit handling**: 429/rate-limit backoff + retry is mandatory for the
  free-model path. Failover stays free-first with deepseek as backup only.

## Cost / time estimate

| Item | Cost | Wall-clock (parallelized) |
|---|---|---|
| LLM-free passes (chunking, uncertainty, packet) | $0 | hours |
| topic/transition over 237 MB (free models) | ~$0 | days |
| embeddings for topic-graph (local) | $0 | hours |
| agent digestion (top-K + ~50 reflections + retrospectives + meta) | ~$5-15 | ~1-2 days |
| **Total** | **~$5-15 one-time** | **~2-3 days** |
