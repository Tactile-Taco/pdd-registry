"""Seed per-model uncertainty baselines from the archived transcripts.

Runs the fixed (0.2.0) chunker + uncertainty pass over every archived
transcript, aggregates `uncertainty_density` (density_per_1k per chunk) per
model, and seeds the runner's BaselineStore (store/baselines.json). Deterministic,
LLM-free. Run from the pdd-repository root:

    python3 scripts/build_baselines.py --archive ~/.annotation-backlog/archive \\
        --store ~/.annotation-backlog/store

Per-model, not per-harness: marker densities differ per model family (dialect),
so deviations are normalized against the same model's own baseline. The
transcript source (harness) distribution is recorded per model for diagnostics.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_IMPL = os.path.join(_REPO, "implementations")
for _name in ("_pipeline_common", "transcript-chunking", "uncertainty-pass"):
    _p = os.path.join(_IMPL, _name, "python-stdlib")
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import transcript_chunking as tc  # noqa: E402
import uncertainty_pass as uc  # noqa: E402
from baselines import BaselineStore, detect_model, is_synthetic  # noqa: E402
from common import list_transcripts  # noqa: E402

SOURCES = ["reasonix", "omp", "claude", "codex", "kimi", "hermes"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--store", required=True)
    ap.add_argument("--target-chars", type=int, default=80000)
    args = ap.parse_args()

    store = BaselineStore(os.path.join(args.store, "baselines.json"))
    agg: dict[str, dict] = defaultdict(lambda: {"n": 0, "sum": 0.0, "sum_sq": 0.0})
    srcs: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    per_model_files: dict[str, int] = defaultdict(int)
    files = 0
    errors = 0

    for source in SOURCES:
        for path in list_transcripts(source, archive_base=args.archive):
            filename = os.path.basename(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
            except Exception:
                continue
            files += 1
            model = detect_model(source, filename, lines)
            if is_synthetic(model):
                continue  # synthetic/test fixtures (e.g. "<synthetic>") are dropped
            try:
                turns = tc.render_turns(source, lines)
                if not turns:
                    continue
                _render, chunks = tc.build_chunks(turns, args.target_chars)
                resp = uc.run(source, filename, {"chunks": chunks}, turns)
            except Exception as e:  # noqa: BLE001 — skip unparseable transcripts
                errors += 1
                continue
            for chunk in resp.get("density", []):
                d = float(chunk["density_per_1k"])
                a = agg[model]
                a["n"] += 1
                a["sum"] += d
                a["sum_sq"] += d * d
                srcs[model][source] += 1
            per_model_files[model] += 1

    for model, a in agg.items():
        store.merge(model, "uncertainty_density", a["n"], a["sum"], a["sum_sq"],
                    sources=dict(srcs[model]))

    print(f"transcripts scanned: {files}  (skipped/unparseable: {errors})")
    print(f"{'model':28s} {'chunks':>6s} {'files':>6s} {'mean':>8s} {'std':>8s}  sources")
    for model in sorted(agg, key=lambda m: -agg[m]["n"]):
        a = agg[model]
        n = a["n"]
        mean = a["sum"] / n if n else 0.0
        var = max(0.0, a["sum_sq"] / n - mean * mean)
        std = var ** 0.5
        src = ",".join(f"{s}:{c}" for s, c in sorted(srcs[model].items()))
        print(f"{model:28s} {n:6d} {per_model_files[model]:6d} {mean:8.4f} {std:8.4f}  {src}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
