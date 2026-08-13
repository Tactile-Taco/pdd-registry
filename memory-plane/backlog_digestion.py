"""Backlog survey-mode digestion — temporary kickstart (docs/backlog-execution-plan.md Phase C).

Drives the 4 fleet agents over the accumulated backlog packets in accelerated
"survey" mode to seed the knowledge store + memory base. Distinct from the full
fleet runner (memory_plane.fleet), which is the ongoing trigger-gated process.

Survey jobs:
  case-study    top-K candidate packets (hot-patch + cluster-maturity ranked)
                -> case-study curator, one case study per candidate
  reflection    packets grouped chronologically into ~batch_mb batches
                -> reflection agent, one reflection per batch
  retrospective concluded clusters / heatmap anomalies -> retrospective agent
  meta          once at the end -> meta-agent system memories + process skills

Resumable: completed job keys are recorded in the ArtifactStore state; a
restart skips them. Rate-limit-safe: calls are paced and retry with exponential
backoff on 429/5xx, so the high-volume survey run never floods a provider.

Reuses memory_plane.fleet (FleetRunner, shape validation, proposal pipeline),
memory_plane.agent_defs (roles + task templates), memory_plane.triggers
(selection/ranking helpers), and memory_plane.client (letta/direct/stub).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request  # noqa: F401 (re-exported exception types)

from memory_plane.agent_defs import agent_def, render_task
from memory_plane.client import make_client
from memory_plane.fleet import (  # noqa: F401
    AGENT_NAME_TO_ID, FleetRunner, extract_json, shape_errors)
from memory_plane.memory import (  # noqa: F401
    render_memories, render_process_skills, sync_memfs)
from memory_plane.triggers import _clusters, _packet_meta, load_graph, load_packets, session_key  # noqa: F401


def summarize_packet(packet: dict, verbose: bool = True) -> str:
    """Compact one-line summary of a packet for a survey task."""
    p = packet.get("packet") or {}
    sess = p.get("session") or {}
    ov = p.get("overview") or {}
    tension = p.get("tension_summary") or []
    narrative = (p.get("topic_flow") or {}).get("narrative", "")
    hm = (p.get("heatmap") or {}).get("matrix") or {}
    cells = hm.get("cells") or []
    hot = sum(1 for row in cells for v in row if v is not None and abs(v) > 1.5)
    parts = [
        "packet source={source} filename={filename} fidelity={fid} "
        "turns={turns} chunks={chunks} hot_cells={hot}".format(
            source=sess.get("source"), filename=sess.get("filename"),
            fid=sess.get("fidelity_class"), turns=ov.get("turn_count"),
            chunks=ov.get("chunk_count"), hot=hot),
    ]
    if verbose:
        if tension:
            parts.append("tension: " + json.dumps(tension[:2], ensure_ascii=False)[:500])
        if narrative:
            parts.append("topic flow: " + str(narrative)[:400])
    return " | ".join(parts)


class BacklogSurvey(FleetRunner):
    """Survey-mode digestion over the accumulated backlog packets."""

    def __init__(self, store_dir: str, client, db_path: str = "fleet.db",
                 *, skills_repo: str | None = None, dry_run: bool = False,
                 pace: float = 1.5, backoff_max: float = 60.0,
                 top_k: int = 75, batch_mb: float = 5.0,
                 packets_per_batch: int = 50,
                 do_case_study: bool = True, do_reflection: bool = True,
                 do_retrospective: bool = True, do_meta: bool = True,
                 cluster_min: int = 5, sync_memory: bool = False,
                 memfs_host: str = "m6") -> None:
        super().__init__(store_dir, client, db_path=db_path,
                         skills_repo=skills_repo, dry_run=dry_run,
                         sync_memory=sync_memory, memfs_host=memfs_host)
        self.pace = pace
        self.backoff_max = backoff_max
        self._backoff = pace
        self.top_k = top_k
        self.batch_mb = batch_mb
        self.packets_per_batch = packets_per_batch
        self.do_case_study = do_case_study
        self.do_reflection = do_reflection
        self.do_retrospective = do_retrospective
        self.do_meta = do_meta
        self.cluster_min = cluster_min

    # -- paced, rate-limit-safe call -----------------------------------------
    def _chat(self, agent_name: str, task: str, system: str | None) -> str:
        while True:
            try:
                out = self.client.chat(agent_name, task, system=system)
                self._backoff = self.pace
                return out
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, self.backoff_max)
                    continue
                raise
            except (urllib.error.URLError, OSError):
                time.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, self.backoff_max)
                continue
            finally:
                time.sleep(self.pace)

    # -- per-job task building ------------------------------------------------
    def _survey_ctx(self) -> dict:
        return {
            "period": time.strftime("%Y-%m-%d", time.gmtime()),
            "case_study_refs": ", ".join(self._case_study_candidates()) or "(none)",
            "skill_list": ", ".join(self._skill_list()) or "(none)",
            "associated_skills": "; ".join(self._associated_skills()) or "(none)",
            "reflection_refs": ", ".join(
                a["artifact_id"] for a in self.store.artifacts("reflection", limit=10)),
            "retrospective_refs": ", ".join(
                a["artifact_id"] for a in self.store.artifacts("retrospective", limit=10)),
            "proposal_outcomes": "\n".join(self._proposal_outcomes()) or "(none)",
            "process_skills": "\n".join(self._process_skills()) or "(none)",
        }

    def _render_survey_task(self, agent_name: str, packet_summaries: list[str],
                            *, reasons: list[str], candidate_ids: str = "(none)",
                            checkpoint_desc: str = "", extra: dict | None = None) -> str:
        agent = agent_def(AGENT_NAME_TO_ID[agent_name])
        ctx = {
            **self._survey_ctx(),
            "packet_summaries": "\n".join(packet_summaries) or "(none)",
            "candidate_ids": candidate_ids,
            "checkpoint_desc": checkpoint_desc,
            **({"trigger_reasons": "; ".join(reasons)} if reasons else {}),
        }
        if extra:
            ctx.update(extra)
        task = render_task(AGENT_NAME_TO_ID[agent_name], **ctx)
        if reasons:
            task += "\n\nSurvey trigger reasons: " + "; ".join(reasons)
        return task

    # -- run an agent job (shape-validate + store, like fleet.run_agent) ------
    def run_agent_task(self, agent_name: str, task: str, reasons: list[str]) -> dict:
        agent_id = AGENT_NAME_TO_ID[agent_name]
        agent = agent_def(agent_id)
        raw = self._chat(agent["name"], task, system=agent["system"])
        artifact = None
        for attempt in range(2):
            try:
                artifact = extract_json(raw)
                errs = shape_errors(artifact, agent)
                if not errs:
                    break
                if attempt == 0:
                    raw = self._chat(
                        agent["name"],
                        task + "\n\nYour previous response failed validation: "
                        + "; ".join(errs) + ". Respond with ONLY the JSON object.",
                        system=agent["system"])
                else:
                    raise ValueError("; ".join(errs))
            except (ValueError, json.JSONDecodeError):
                if attempt == 0:
                    raw = self._chat(
                        agent["name"],
                        task + "\n\nYour previous response was not valid JSON. "
                        "Respond with ONLY the JSON object.",
                        system=agent["system"])
                else:
                    raise
        artifact.setdefault("artifact_id", f"{agent_name}-{int(time.time() * 1000)}")
        artifact["agent"] = agent_id
        artifact["model"] = agent.get("model")
        artifact["trigger_reasons"] = reasons
        artifact.setdefault("evidence_links", [])
        artifact["survey"] = True
        self.store.add_artifact(artifact)
        return artifact

    # -- survey job selection -------------------------------------------------
    def _case_study_jobs(self, packets: list[dict]) -> list[tuple]:
        """Rank by hot-cell density, then cluster maturity; top-K candidates."""
        graph = load_graph(self.store_dir)
        clusters = _clusters(graph)
        cluster_size = {}
        for cl in clusters:
            for nid in cl:
                cluster_size[nid] = len(cl)
        scored = []
        for p in packets:
            m = _packet_meta(p)
            key = m["key"]
            hot = sum(1 for row in (p.get("packet") or {}).get("heatmap", {})
                      .get("matrix", {}).get("cells", []) for v in row
                      if v is not None and abs(v) > 1.5)
            cs = cluster_size.get(key, 1)
            scored.append((hot, cs, key, p))
        # prefer hot sessions; tie-break by cluster size, then key (stable)
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        return scored[: self.top_k]

    def _reflection_jobs(self, packets: list[dict]) -> list[tuple]:
        """Chronological packets grouped into batches bounded by packet COUNT
        (distilled packets are small, so byte-batching alone collapses them)
        and by an upper byte cap (for large single sessions)."""
        ordered = sorted(packets, key=lambda p: self._packet_path_mtime(p))
        batches, cur, cur_bytes = [], [], 0.0
        target = self.batch_mb * 1024 * 1024
        for p in ordered:
            b = self._packet_size(p)
            if cur and (len(cur) >= self.packets_per_batch
                        or cur_bytes + b > target):
                batches.append(cur)
                cur, cur_bytes = [], 0.0
            cur.append(p)
            cur_bytes += b
        if cur:
            batches.append(cur)
        return batches

    def _packet_path_mtime(self, packet: dict) -> float:
        m = _packet_meta(packet)
        p = os.path.join(self.store_dir, "packets",
                         f"{m['source']}-{m['filename']}.packet.json")
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0.0

    def _packet_size(self, packet: dict) -> float:
        m = _packet_meta(packet)
        p = os.path.join(self.store_dir, "packets",
                         f"{m['source']}-{m['filename']}.packet.json")
        try:
            return float(os.path.getsize(p))
        except OSError:
            return float(len(json.dumps(packet)))

    def _retrospective_jobs(self, packets: list[dict]) -> list[tuple]:
        """One job per concluded cluster (>= cluster_min sessions)."""
        by_key = {_packet_meta(p)["key"]: p for p in packets}
        graph = load_graph(self.store_dir)
        jobs = []
        for i, cl in enumerate(_clusters(graph)):
            members = [k for k in cl if k in by_key]
            if len(members) < self.cluster_min:
                continue
            jobs.append((members, cl))
        return jobs

    # -- main survey loop -----------------------------------------------------
    def survey_jobs(self):
        packets = load_packets(self.store_dir)
        if not packets:
            return []
        jobs = []
        if self.do_case_study:
            for hot, cs, key, p in self._case_study_jobs(packets):
                m = _packet_meta(p)
                jobs.append((f"case-study:{key}", "case-study",
                             self._render_survey_task(
                                 "case-study",
                                 [summarize_packet(p)],
                                 reasons=[f"top case-study candidate (hot={hot}, cluster={cs})"],
                                 candidate_ids=self._candidate_ids(p)), "case-study"))
        if self.do_reflection:
            for bi, batch in enumerate(self._reflection_jobs(packets)):
                keys = ",".join(_packet_meta(p)["key"] for p in batch)
                jobs.append((f"reflection:{bi}:{len(batch)}", "reflection",
                             self._render_survey_task(
                                 "reflection",
                                 [summarize_packet(p) for p in batch],
                                 reasons=[f"chronological batch #{bi} ({len(batch)} packets)"]),
                             "reflection"))
        if self.do_retrospective:
            for members, cl in self._retrospective_jobs(packets):
                jkey = "retrospective:" + ",".join(members[:3])
                sel = [by for by in packets if _packet_meta(by)["key"] in members]
                jobs.append((jkey, "retrospective",
                             self._render_survey_task(
                                 "retrospective",
                                 [summarize_packet(p) for p in sel],
                                 reasons=[f"concluded cluster ({len(members)} sessions)"]),
                             "retrospective"))
        if self.do_meta:
            jobs.append(("meta:once", "meta",
                         self._render_survey_task("meta", [],
                                                  reasons=["backlog survey complete"]),
                         "system-memory"))
        return jobs

    def _candidate_ids(self, packet: dict) -> str:
        csc = (packet.get("packet") or {}).get("case_study_candidates") or []
        ids = [c.get("candidate_id") or c.get("annotation_id")
               for c in csc if isinstance(c, dict)]
        return ", ".join(x for x in ids if x) or "(none)"

    def run_survey(self) -> dict:
        jobs = self.survey_jobs()
        stats = {"jobs_total": len(jobs), "done": 0, "skipped": 0, "errors": [],
                 "proposals": [], "agents_run": {}}
        for job_key, agent_name, task, art_type in jobs:
            if self.store.get_state(f"survey.{job_key}") == "done":
                stats["skipped"] += 1
                continue
            try:
                artifact = self.run_agent_task(agent_name, task,
                                               [f"survey job {job_key}"])
                stats["done"] += 1
                stats["agents_run"][job_key] = artifact["artifact_id"]
                if art_type in ("reflection", "retrospective"):
                    author = AGENT_NAME_TO_ID.get(agent_name, agent_name)
                    stats["proposals"].extend(
                        self.handle_proposals(artifact, author))
                elif art_type == "system-memory" and self.sync_memory:
                    try:
                        stats["memory_sync"] = sync_memfs(
                            self.memfs_host, AGENT_NAME_TO_ID[agent_name],
                            {"memories.md": render_memories(
                                artifact.get("memories") or [],
                                (artifact.get("period") or {}).get("to")),
                             "process-skills.md": render_process_skills(
                                artifact.get("process_updates") or [])},
                            dry_run=self.dry_run)
                    except Exception as e:  # noqa: BLE001
                        stats["errors"].append(f"memory sync: {str(e)[:200]}")
                self.store.set_state(f"survey.{job_key}", "done")
            except Exception as e:  # noqa: BLE001 — keep going, journalable
                stats["errors"].append(f"{job_key}: {str(e)[:300]}")
        return stats


def run_forever(runner: BacklogSurvey, watch_seconds: int = 900) -> None:
    while True:
        stats = runner.run_survey()
        print(json.dumps(stats, sort_keys=True, default=str))
        time.sleep(watch_seconds)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="backlog-digestion")
    ap.add_argument("--store", default=os.environ.get(
        "ANNOTATION_STORE", "/home/TacticalTaco/.annotation-backlog/store"))
    ap.add_argument("--db", default="/home/TacticalTaco/.annotation-backlog/fleet.db")
    ap.add_argument("--backend", default="letta", choices=["letta", "direct", "stub"])
    ap.add_argument("--skills-repo", default="/home/TacticalTaco/skills")
    ap.add_argument("--pace", type=float, default=1.5, help="seconds between agent calls")
    ap.add_argument("--top-k", type=int, default=75)
    ap.add_argument("--batch-mb", type=float, default=5.0)
    ap.add_argument("--packets-per-batch", type=int, default=50)
    ap.add_argument("--cluster-min", type=int, default=5)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--watch-seconds", type=int, default=900)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sync-memory", action="store_true")
    ap.add_argument("--no-case-study", action="store_true")
    ap.add_argument("--no-reflection", action="store_true")
    ap.add_argument("--no-retrospective", action="store_true")
    ap.add_argument("--no-meta", action="store_true")
    args = ap.parse_args(argv)

    client = make_client(args.backend)
    runner = BacklogSurvey(
        args.store, client, db_path=args.db, skills_repo=args.skills_repo,
        dry_run=args.dry_run, pace=args.pace, top_k=args.top_k,
        batch_mb=args.batch_mb, packets_per_batch=args.packets_per_batch,
        cluster_min=args.cluster_min,
        do_case_study=not args.no_case_study,
        do_reflection=not args.no_reflection,
        do_retrospective=not args.no_retrospective,
        do_meta=not args.no_meta, sync_memory=args.sync_memory)
    try:
        if args.once:
            print(json.dumps(runner.run_survey(), sort_keys=True, default=str))
            return 0
        run_forever(runner, watch_seconds=args.watch_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
