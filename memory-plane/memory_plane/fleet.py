"""Fleet orchestrator: triggers -> agent runs -> artifacts -> proposals ->
peer review -> skill push.

The fleet is a CLIENT outside the PDD Validator Loop: agents are stochastic,
so the contract is schema-shaped JSON output validated here (required keys +
type), with ONE retry per agent run. Everything else (trigger evaluation,
store, validation, review tally, push mechanics) is deterministic and tested.
"""

from __future__ import annotations

import json
import os
import re
import time

from .agent_defs import AGENT_DEFS, agent_def, render_task
from .memory import render_memories, render_process_skills, sync_memfs
from .proposals import ARTIFACT_ID_RE, extract_proposals, validate_proposal
from .push import SkillRepo
from .review import run_review
from .store import ArtifactStore
from .triggers import TriggerEvaluator, load_packets, _packet_meta, hot_patches

AGENT_NAME_TO_ID = {
    "case-study": "agent-case-study-curator",
    "reflection": "agent-reflection",
    "retrospective": "agent-retrospective",
    "meta": "agent-meta",
}


def extract_json(raw: str) -> dict:
    """Tolerant extraction of the first balanced JSON object."""
    start = raw.find("{")
    if start == -1:
        raise ValueError("no JSON object in agent output")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:i + 1])
    raise ValueError("unbalanced JSON in agent output")


def shape_errors(artifact: dict, agent: dict) -> list[str]:
    schema = agent.get("output_schema") or {}
    errs = []
    for key in schema.get("required", []):
        if key not in artifact:
            errs.append(f"missing required field: {key}")
    if "type" in schema and artifact.get("type") != schema["type"]:
        errs.append(f"type must be {schema['type']!r}, got {artifact.get('type')!r}")
    aid = artifact.get("artifact_id")
    if aid and not ARTIFACT_ID_RE.match(aid):
        errs.append(f"invalid artifact_id {aid!r}: only [A-Za-z0-9._-] allowed")
    return errs


class FleetRunner:
    def __init__(self, store_dir: str, client, db_path: str = "fleet.db",
                 *, skills_repo: str | None = None, dry_run: bool = False,
                 evaluator: TriggerEvaluator | None = None,
                 sync_memory: bool = False, memfs_host: str = "m6") -> None:
        self.store_dir = store_dir
        self.client = client
        self.store = ArtifactStore(db_path)
        self.evaluator = evaluator or TriggerEvaluator(store_dir, self.store)
        self.dry_run = dry_run
        self.skill_repo = SkillRepo(skills_repo, dry_run=dry_run) if skills_repo else None
        self.sync_memory = sync_memory
        self.memfs_host = memfs_host
        self.stats: dict = {}

    def close(self) -> None:
        self.store.close()

    # -- context builders ----------------------------------------------------
    def _packet_summaries(self) -> list[str]:
        out = []
        packets = load_packets(self.store_dir)
        for p in packets:
            inner = p.get("packet") or {}
            sess = inner.get("session") or {}
            ov = inner.get("overview") or {}
            tension = inner.get("tension_summary") or []
            narrative = (inner.get("topic_flow") or {}).get("narrative", "")
            hm = (inner.get("heatmap") or {}).get("matrix") or {}
            patches = hot_patches(p)
            out.append(
                "packet source={source} filename={filename} fidelity={fidelity} "
                "turns={turns} chunks={chunks} | tension: {tension} | "
                "topic flow: {narrative} | hot patches: {patches}".format(
                    source=sess.get("source"), filename=sess.get("filename"),
                    fidelity=sess.get("fidelity_class"),
                    turns=ov.get("turn_count"), chunks=ov.get("chunk_count"),
                    tension=json.dumps(tension[:3], ensure_ascii=False)[:600],
                    narrative=str(narrative)[:500],
                    patches=len(patches)))
        return out

    def _case_study_candidates(self) -> list[str]:
        return [a["artifact_id"] for a in self.store.artifacts("case-study", limit=50)]

    def _skill_list(self) -> list[str]:
        if self.skill_repo is None:
            return []
        skills_dir = os.path.join(self.skill_repo.repo_dir, "skills")
        if not os.path.isdir(skills_dir):
            return []
        return sorted(d for d in os.listdir(skills_dir)
                      if os.path.isdir(os.path.join(skills_dir, d)))

    def _associated_skills(self) -> list[str]:
        out = []
        for skill in self._skill_list():
            links = self.store.influenced_skills(skill)
            if links:
                out.append(f"{skill} (influenced by "
                           f"{', '.join(l['artifact_id'][:8] for l in links[:3])})")
        return out

    def _proposal_outcomes(self) -> list[str]:
        rows = [p for p in self.store.proposals()
                if p.get("status") in ("approved", "held", "pushed")]
        return [f"{p['id']} [{p['status']}] kind={p['kind']} skill={p.get('skill_name')}"
                for p in rows[-10:]]

    def _process_skills(self) -> list[str]:
        rows = [p for p in self.store.proposals() if p.get("kind") == "process-skill"]
        return [f"{p['id']}: {p.get('reasoning', '')[:200]}" for p in rows[-10:]]

    # -- task building ---------------------------------------------------------
    def build_task(self, agent_name: str, reasons: list[str]) -> str:
        agent = agent_def(AGENT_NAME_TO_ID[agent_name])
        ctx = {
            "period": time.strftime("%Y-%m-%d", time.gmtime()),
            "packet_summaries": "\n".join(self._packet_summaries()) or "(none)",
            "case_study_refs": ", ".join(self._case_study_candidates()) or "(none)",
            "candidate_ids": ", ".join(self._case_study_candidates()) or "(none)",
            "skill_list": ", ".join(self._skill_list()) or "(none)",
            "associated_skills": "; ".join(self._associated_skills()) or "(none)",
            "reflection_refs": ", ".join(
                a["artifact_id"] for a in self.store.artifacts("reflection", limit=10)),
            "retrospective_refs": ", ".join(
                a["artifact_id"] for a in self.store.artifacts("retrospective", limit=10)),
            "proposal_outcomes": "\n".join(self._proposal_outcomes()) or "(none)",
            "process_skills": "\n".join(self._process_skills()) or "(none)",
            "checkpoint_desc": "; ".join(reasons),
            "trigger_reasons": "; ".join(reasons),
        }
        return render_task(AGENT_NAME_TO_ID[agent_name], **ctx) + (
            "\n\nTrigger reasons: " + "; ".join(reasons))

    # -- agent run -------------------------------------------------------------
    def run_agent(self, agent_name: str, reasons: list[str]) -> dict:
        agent_id = AGENT_NAME_TO_ID[agent_name]
        agent = agent_def(agent_id)
        task = self.build_task(agent_name, reasons)

        raw = self.client.chat(agent["name"], task, system=agent["system"])
        artifact = None
        retried = False
        for attempt in range(2):
            try:
                artifact = extract_json(raw)
                errs = shape_errors(artifact, agent)
                if not errs:
                    break
                if attempt == 0:
                    raw = self.client.chat(
                        agent["name"],
                        task + "\n\nYour previous response failed validation: "
                        + "; ".join(errs) + ". Respond with ONLY the JSON object.",
                        system=agent["system"])
                    retried = True
                else:
                    raise ValueError("; ".join(errs))
            except (ValueError, json.JSONDecodeError):
                if attempt == 0:
                    raw = self.client.chat(
                        agent["name"],
                        task + "\n\nYour previous response was not valid JSON. "
                        "Respond with ONLY the JSON object.",
                        system=agent["system"])
                    retried = True
                else:
                    raise

        artifact.setdefault("artifact_id",
                            f"{agent_name}-{int(time.time() * 1000)}")
        artifact["agent"] = agent_id
        artifact["model"] = agent.get("model")
        artifact["retried"] = retried
        artifact["trigger_reasons"] = reasons
        artifact.setdefault("evidence_links", [])
        self.store.add_artifact(artifact)
        return artifact

    # -- proposal pipeline ------------------------------------------------------
    def handle_proposals(self, artifact: dict, author: str) -> list[dict]:
        results = []
        for p in extract_proposals(artifact):
            errs = validate_proposal(p)
            if errs:
                results.append({"proposal_id": p["proposal_id"],
                                "status": "invalid", "errors": errs})
                continue
            self.store.add_proposal(p, artifact["artifact_id"])
            kind = p.get("kind")
            if kind in ("new-skill", "edit-skill"):
                if self.dry_run:
                    self.store.set_proposal_status(p["proposal_id"], "proposed")
                    results.append({"proposal_id": p["proposal_id"],
                                    "status": "proposed (dry-run)"})
                    continue
                review = run_review(p, self.client, self.store, author=author)
                if review["verdict"] == "approved" and self.skill_repo is not None:
                    try:
                        pushed = self.skill_repo.apply(p)
                        self.store.set_proposal_status(p["proposal_id"], "pushed")
                        for m in p.get("motivated_by") or []:
                            self.store.link_skill(
                                m["artifact_id"], p["skill_name"],
                                "## Provenance", m.get("impact", ""))
                        results.append({"proposal_id": p["proposal_id"],
                                        "status": "pushed",
                                        "commit": pushed.get("commit")})
                    except Exception as e:  # noqa: BLE001 — record, don't crash
                        self.store.set_proposal_status(p["proposal_id"], "held")
                        results.append({"proposal_id": p["proposal_id"],
                                        "status": "held (push failed)",
                                        "error": str(e)[:300]})
                else:
                    if review["verdict"] == "approved" and self.skill_repo is None:
                        status = "approved (no skills repo configured)"
                    else:
                        status = review.get("verdict", "held")
                    results.append({"proposal_id": p["proposal_id"],
                                    "status": status,
                                    "reasons": review.get("reasons", [])})
            else:
                # process-skill / no-proposal: recorded, no repo push.
                self.store.set_proposal_status(p["proposal_id"], "proposed")
                results.append({"proposal_id": p["proposal_id"],
                                "status": "recorded", "kind": kind})
        return results

    # -- main loop ----------------------------------------------------------------
    def run_once(self) -> dict:
        try:
            fired = self.evaluator.evaluate()
        except Exception as e:  # noqa: BLE001 — corrupt state must not kill the loop
            return {"triggers": {}, "agents_run": {}, "proposals": [],
                    "errors": [f"trigger evaluation failed: {str(e)[:300]}"]}
        stats: dict = {"triggers": fired, "agents_run": {}, "proposals": [],
                       "errors": []}
        for agent_name, reasons in fired.items():
            try:
                artifact = self.run_agent(agent_name, reasons)
                stats["agents_run"][agent_name] = {
                    "artifact_id": artifact["artifact_id"],
                    "retried": artifact.get("retried", False)}
                if artifact.get("type") in ("reflection", "retrospective"):
                    author = AGENT_NAME_TO_ID.get(agent_name, agent_name)
                    stats["proposals"].extend(
                        self.handle_proposals(artifact, author))
                elif artifact.get("type") == "system-memory" and self.sync_memory:
                    # Default-way memory: write the meta-agent's system
                    # memories + process skills into its Letta MemFS.
                    files = {
                        "memories.md": render_memories(
                            artifact.get("memories") or [],
                            (artifact.get("period") or {}).get("to")),
                        "process-skills.md": render_process_skills(
                            artifact.get("process_updates") or []),
                    }
                    try:
                        written = sync_memfs(self.memfs_host,
                                             AGENT_NAME_TO_ID[agent_name],
                                             files, dry_run=self.dry_run)
                        stats["memory_sync"] = written
                    except Exception as e:  # noqa: BLE001 — record, keep going
                        stats["errors"].append(
                            f"memory sync failed: {str(e)[:300]}")
            except Exception as e:  # noqa: BLE001 — keep the loop going
                stats["errors"].append(f"{agent_name}: {str(e)[:300]}")

        # advance trigger floors for the agents that actually ran
        now = str(time.time())
        if "reflection" in stats["agents_run"]:
            self.store.set_state("reflection.ts", now)
        if "retrospective" in stats["agents_run"]:
            self.store.set_state("retrospective.ts", now)
        if "meta" in stats["agents_run"]:
            self.store.set_state("meta.ts", now)
        total = 0.0
        pdir = os.path.join(self.store_dir, "packets")
        if os.path.isdir(pdir):
            for fn in os.listdir(pdir):
                if fn.endswith(".packet.json"):
                    try:
                        total += os.path.getsize(os.path.join(pdir, fn))
                    except OSError:
                        pass
        for agent in ("reflection", "retrospective"):
            self.store.set_state(f"{agent}.bytes", str(total))
        return stats


def run_forever(runner: FleetRunner, watch_seconds: int = 3600) -> None:
    while True:
        stats = runner.run_once()
        print(json.dumps(stats, sort_keys=True, default=str))
        time.sleep(watch_seconds)
