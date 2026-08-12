"""Backlog runner for the transcript-annotation pipeline.

Implements the backlog-execution-plan: resumable per-file checkpointing
(CheckpointJournal), LLM cost ledger, free-first router failover, watch or
once mode. Archives stay read-only; everything else lands under the store dir.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _name in ("_pipeline_common", "transcript-chunking", "annotation-store",
              "uncertainty-pass", "topic-transition-pass", "topic-flow-review",
              "topic-graph", "reflection-packet"):
    _p = os.path.join(_HERE, "..", _name, "python-stdlib")
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from common import ARCHIVE_BASE, list_transcripts  # noqa: E402
from journal import CheckpointJournal  # noqa: E402
from ledger import CostLedger  # noqa: E402
from router import ModelRouter, StubRouter  # noqa: E402

SOURCES = ["reasonix", "omp", "claude", "codex", "kimi", "hermes"]


def required_pass_versions() -> dict:
    import transcript_chunking
    import uncertainty_pass
    import topic_transition_pass
    import topic_flow_review
    return {
        "transcript-chunking": transcript_chunking.PASS_VERSION,
        "uncertainty-pass": uncertainty_pass.PASS_VERSION,
        "topic-transition-pass": topic_transition_pass.PASS_VERSION,
        "topic-flow-review": topic_flow_review.PASS_VERSION,
    }


def process_file(source: str, filename: str, archive_base: str, store_dir: str,
                 router, graph, out_dir: str) -> None:
    """Run the full pass pipeline for one transcript file. Raises on failure."""
    from transcript_chunking import run as chunk_run
    from uncertainty_pass import run as unc_run
    from topic_transition_pass import run as tt_run
    from topic_flow_review import run as fw_run
    from topic_graph import TopicGraph
    from reflection_packet import build as packet_build

    cs = os.path.join(store_dir, "chunk-store")
    resp = chunk_run(source, filename, archive_base, chunk_store=cs)
    rid = resp["render_id"]
    unc_run(source, filename, render_id=rid, store_dir=store_dir, chunk_store=cs,
            lexicon_version_="")
    tt_resp = tt_run(source, filename, render_id=rid, store_dir=store_dir,
                     chunk_store=cs, router=router)
    fw_resp = fw_run(source, filename, render_id=rid, store_dir=store_dir,
                     chunk_store=cs, router=router)
    graph.add_session(source, filename,
                      topics=[{"topic_id": t["topic_id"], "label": t["label"],
                               "intensity": t["intensity"]} for t in tt_resp["topics"]])
    packet_build(source, filename, render_id=rid, store_dir=store_dir,
                 chunk_store=cs, out_dir=out_dir)
    _ = fw_resp


def run_once(sources: list[str], archive_base: str, store_dir: str,
             journal: CheckpointJournal, ledger: CostLedger,
             router, max_attempts: int = 3, limit: int | None = None,
             required_versions: dict | None = None) -> dict:
    from topic_graph import TopicGraph

    graph = TopicGraph(os.path.join(store_dir, "topic-graph"))
    out_dir = os.path.join(store_dir, "packets")
    os.makedirs(out_dir, exist_ok=True)
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
                process_file(source, filename, archive_base, store_dir,
                             router, graph, out_dir)
                journal.mark_done(source, filename, required)
                stats["processed"] += 1
            except Exception as e:  # noqa: BLE001 — journal the failure, keep going
                journal.mark_failed(source, filename, str(e))
                stats["failed"] += 1
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backlog-runner")
    ap.add_argument("--sources", default="all",
                    help="comma-separated sources or 'all'")
    ap.add_argument("--archive-base", default=ARCHIVE_BASE)
    ap.add_argument("--store-dir", default=os.environ.get("ANNOTATION_STORE", "./annotation-store"))
    ap.add_argument("--journal", default=None, help="checkpoint journal path (default <store>/journal.json)")
    ap.add_argument("--ledger", default=None, help="cost ledger path (default <store>/cost-ledger.jsonl)")
    ap.add_argument("--once", action="store_true", help="process the backlog once and exit")
    ap.add_argument("--watch", action="store_true", help="watch loop (--once wins if both)")
    ap.add_argument("--watch-seconds", type=int, default=600)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--stub", action="store_true", help="use StubRouter (offline, for tests/dry-run)")
    args = ap.parse_args(argv)

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
