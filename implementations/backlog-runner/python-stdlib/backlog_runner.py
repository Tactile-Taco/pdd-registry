"""Backlog runner for the transcript-annotation pipeline (deployment surface).

Wires the attested pure cores to the real world: archive reads, chunk-store /
annotation-store / graph / packet persistence, checkpoint journal, cost
ledger, free-first router failover. Nothing here is part of the attested
candidate surface (see each bundle's known_limitations).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_IMPL = os.path.dirname(os.path.dirname(_HERE))  # implementations/ (no ".." traversal)
for _name in ("_pipeline_common", "transcript-chunking", "annotation-store",
              "uncertainty-pass", "topic-transition-pass", "topic-flow-review",
              "topic-graph", "reflection-packet"):
    _p = os.path.join(_IMPL, _name, "python-stdlib")
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from common import list_transcripts  # noqa: E402
from journal import CheckpointJournal  # noqa: E402
from ledger import CostLedger  # noqa: E402
from router import ModelRouter, StubRouter  # noqa: E402

import transcript_chunking as tc  # noqa: E402
import uncertainty_pass as uc  # noqa: E402
import topic_transition_pass as tt  # noqa: E402
import topic_flow_review as fw  # noqa: E402
import reflection_packet as pkt  # noqa: E402
from annotation_store import AnnotationCore  # noqa: E402
from topic_graph import TopicGraphCore  # noqa: E402

SOURCES = ["reasonix", "omp", "claude", "codex", "kimi", "hermes"]


def required_pass_versions() -> dict:
    return {
        "transcript-chunking": tc.PASS_VERSION,
        "uncertainty-pass": uc.PASS_VERSION,
        "topic-transition-pass": tt.PASS_VERSION,
        "topic-flow-review": fw.PASS_VERSION,
    }


# --------------------------------------------------------------------------
# I/O adapters (deployment surface)
# --------------------------------------------------------------------------

def _atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".swp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class StoreIO:
    """Annotation-store + chunk-store persistence (runner-owned)."""

    def __init__(self, store_dir: str) -> None:
        self.store_dir = store_dir
        self.chunk_store = os.path.join(store_dir, "chunk-store")

    def ann_path(self, source: str, filename: str) -> str:
        return os.path.join(self.store_dir, source, filename + ".annotations.jsonl")

    def load_records(self, source: str, filename: str) -> list[dict]:
        p = self.ann_path(source, filename)
        if not os.path.exists(p):
            return []
        out = []
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def append_records(self, source: str, filename: str, pass_id: str,
                       pass_version: str, records: list[dict]) -> None:
        p = self.ann_path(source, filename)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        stamped = [{"pass_id": pass_id, "pass_version": pass_version, **r} for r in records]
        batch = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in stamped)
        with open(p, "a", encoding="utf-8") as f:
            f.write(batch)
            f.flush()
            os.fsync(f.fileno())

    def write_chunk_store(self, source: str, filename: str, resp: dict,
                          turns: list[dict], render: str) -> None:
        out_dir = os.path.join(self.chunk_store, source)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, filename + ".render.jsonl"), "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n")
        with open(os.path.join(out_dir, filename + ".chunkmap.json"), "w", encoding="utf-8") as f:
            json.dump({"render_id": resp["render_id"], "chunks": resp["chunks"],
                       "render_sha256": resp["render_sha256"]},
                      f, ensure_ascii=False, sort_keys=True)


class GraphIO:
    def __init__(self, store_dir: str, edge_threshold: float = 0.7) -> None:
        self.path = os.path.join(store_dir, "topic-graph", "topic-graph.json")
        self.log_path = os.path.join(store_dir, "topic-graph", "topic-graph.log")
        self.edge_threshold = edge_threshold
        self.core = TopicGraphCore(edge_threshold=edge_threshold)
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                snap = json.load(f)
            self.core.version = snap.get("version", 1)
            self.core.nodes = snap.get("nodes", {})
            self.core.edges = snap.get("edges", [])
            self.core.sessions = snap.get("sessions", [])

    def add_session(self, source: str, filename: str, topics: list[dict]) -> dict:
        resp = self.core.add_session(source, filename, topics)
        _atomic_write(self.path, json.dumps({
            "version": self.core.version, "nodes": self.core.nodes,
            "edges": self.core.edges, "sessions": self.core.sessions,
        }, ensure_ascii=False, sort_keys=True, indent=1))
        if self.core.migration_hits:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("\n".join(self.core.migration_log) + "\n")
        return resp


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------

def process_file(source: str, filename: str, archive_base: str, store_dir: str,
                 router, graph: GraphIO, target_chars: int = 80000) -> None:
    with open(os.path.join(archive_base, source, filename), "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    io = StoreIO(store_dir)
    turns = tc.render_turns(source, lines)
    render, chunks = tc.build_chunks(turns, target_chars)
    resp = tc.build_response(source, filename, turns, render, chunks)
    io.write_chunk_store(source, filename, resp, turns, render)
    chunk_map = {"chunks": chunks}
    rid = resp["render_id"]

    # uncertainty (LLM-free)
    uc_resp = uc.run(source, filename, chunk_map, turns)
    io.append_records(source, filename, uc.PASS_ID, uc.PASS_VERSION, uc_resp["records"])

    # topic + transitions (LLM)
    tt_resp = tt.run(source, filename, chunk_map, turns, router=router)
    io.append_records(source, filename, tt.PASS_ID, tt.PASS_VERSION, tt_resp["records"])

    # flow review (LLM) over the accumulated records
    core = AnnotationCore()
    core.records = io.load_records(source, filename)
    topics = core.query(source, filename, layer="topic")["records"]
    transitions = core.query(source, filename, layer="transition")["records"]
    contention = core.query(source, filename, layer="contention")["records"]
    fw_resp = fw.run(source, filename, chunk_map, topics, transitions,
                     contention, router=router)
    io.append_records(source, filename, fw.PASS_ID, fw.PASS_VERSION, fw_resp["records"])

    # graph + packet
    graph.add_session(source, filename,
                      topics=[{"topic_id": t["payload"]["topic_id"],
                               "label": t["payload"]["label"],
                               "intensity": t["payload"].get("intensity", 0.0)}
                              for t in topics])
    core.records = io.load_records(source, filename)
    records_by_layer = {layer: core.query(source, filename, layer=layer)["records"]
                        for layer in ("uncertainty", "contention", "topic",
                                      "transition", "topic-flow")}
    packet = pkt.build(source, filename, rid, chunk_map, turns, records_by_layer)
    packets_dir = os.path.join(store_dir, "packets")
    os.makedirs(packets_dir, exist_ok=True)
    _atomic_write(os.path.join(packets_dir, f"{source}-{filename}.packet.json"),
                  json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=1))


def run_once(sources: list[str], archive_base: str, store_dir: str,
             journal: CheckpointJournal, ledger: CostLedger,
             router, max_attempts: int = 3, limit: int | None = None,
             required_versions: dict | None = None) -> dict:
    graph = GraphIO(store_dir)
    required = required_versions if required_versions is not None else required_pass_versions()

    stats = {"processed": 0, "skipped": 0, "failed": 0, "exhausted": 0}
    for source in sources:
        for path in list_transcripts(source, archive_base=archive_base):
            if limit is not None and stats["processed"] + stats["failed"] >= limit:
                return stats
            filename = os.path.basename(path)
            if journal.is_done(source, filename, required):
                stats["skipped"] += 1
                continue
            if not journal.needs_work(source, filename, required, max_attempts):
                stats["exhausted"] += 1
                continue
            try:
                process_file(source, filename, archive_base, store_dir, router, graph)
                journal.mark_done(source, filename, required)
                stats["processed"] += 1
            except Exception as e:  # noqa: BLE001 — journal the failure, keep going
                journal.mark_failed(source, filename, str(e))
                stats["failed"] += 1
    return stats


def _resolve_bifrost_key() -> str:
    """BIFROST_KEY from env, else the Infisical credential used by the
    skill-sync system (BIFROST_AGENT_VIRTUAL_KEY). Never hardcoded."""
    key = os.environ.get("BIFROST_KEY", "")
    if key:
        return key
    try:
        out = subprocess.run(
            ["infisical", "secrets", "get", "BIFROST_AGENT_VIRTUAL_KEY",
             "--projectId", "5598630f-4109-47d9-bbfb-91bac16ac92c",
             "--env", "prod", "--plain", "--silent"],
            capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 — no key is a valid degraded state
        pass
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backlog-runner")
    ap.add_argument("--sources", default="all")
    ap.add_argument("--archive-base", default=os.environ.get("TRANSCRIPT_ARCHIVE", "/home/tacticaltaco/transcript-archive"))
    ap.add_argument("--store-dir", default=os.environ.get("ANNOTATION_STORE", "./annotation-store"))
    ap.add_argument("--journal", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--watch-seconds", type=int, default=600)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--stub", action="store_true")
    args = ap.parse_args(argv)

    if not args.stub:
        os.environ.setdefault("BIFROST_KEY", _resolve_bifrost_key())

    sources = SOURCES if args.sources == "all" else [s.strip() for s in args.sources.split(",")]
    store = args.store_dir
    os.makedirs(store, exist_ok=True)
    journal = CheckpointJournal(args.journal or os.path.join(store, "journal.json"))
    ledger = CostLedger(args.ledger or os.path.join(store, "cost-ledger.jsonl"))
    router = StubRouter(default={"topics": [], "transitions": [],
                                 "narrative": "", "findings": []}) if args.stub else ModelRouter(ledger=ledger)

    while True:
        stats = run_once(sources, args.archive_base, store, journal, ledger,
                         router, max_attempts=args.max_attempts, limit=args.limit)
        print(json.dumps({"stats": stats, "ledger": ledger.totals()}, sort_keys=True))
        if args.once or not args.watch:
            return 0
        time.sleep(args.watch_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
