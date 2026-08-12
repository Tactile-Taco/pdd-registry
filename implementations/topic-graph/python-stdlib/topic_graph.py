"""topic-graph implementation (draft bundle candidate).

Cross-session topic graph, index-first and incremental: add_session() only
touches the new session's topics (O(N) similarity scan against existing
nodes), embeddings are local + deterministic (Jaccard over label tokens), no
LLM reasoning anywhere. index_size is observable and a migration log fires
past the configured threshold.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Optional

from common import (
    bundle_schema_path,
    session_key,
    sha256_json,
    validate_against_schema,
)

PASS_ID = "topic-graph"
PASS_VERSION = "0.1.0-draft"

MIGRATION_THRESHOLD = 100_000


def _tokens(label: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", label.casefold()))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return round(inter / union, 6) if union else 0.0


class TopicGraph:
    def __init__(self, store_dir: str, edge_threshold: float = 0.7,
                 migration_threshold: int = MIGRATION_THRESHOLD) -> None:
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)
        self.path = os.path.join(store_dir, "topic-graph.json")
        self.log_path = os.path.join(store_dir, "topic-graph.log")
        self.edge_threshold = edge_threshold
        self.migration_threshold = migration_threshold
        self.migration_hits = 0
        self._data: dict = {"version": 1, "nodes": {}, "edges": [], "sessions": []}
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)

    # -- state helpers ----------------------------------------------------------

    @property
    def nodes(self) -> dict:
        return self._data["nodes"]

    @property
    def edges(self) -> list[dict]:
        return self._data["edges"]

    @property
    def index_size(self) -> int:
        return len(self.nodes) + len(self.edges)

    def _save(self) -> None:
        d = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".graph-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, sort_keys=True, indent=1)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _check_migration(self) -> None:
        if self.index_size > self.migration_threshold:
            self.migration_hits += 1
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"migration: index_size {self.index_size} > threshold "
                        f"{self.migration_threshold} (version {self._data['version']})\n")

    # -- add session ------------------------------------------------------------

    def add_session(self, source: str, filename: str, topics: list[dict],
                    edge_threshold: Optional[float] = None) -> dict:
        thr = self.edge_threshold if edge_threshold is None else edge_threshold
        key = session_key(source, filename)
        added_nodes = added_edges = 0

        if key not in self._data["sessions"]:
            token_cache = {nid: _tokens(n["label"]) for nid, n in self.nodes.items()}
            for t in topics:
                nid = f"{key}::{t['topic_id']}"
                if nid in self.nodes:
                    continue  # node-identity: idempotent per node
                label = str(t["label"])
                label_tokens = _tokens(label)
                self.nodes[nid] = {"label": label,
                                   "intensity": float(t.get("intensity", 0.0))}
                added_nodes += 1
                for other, toks in token_cache.items():
                    sim = jaccard(label_tokens, toks)
                    if sim >= thr:
                        a, b = sorted((nid, other))
                        self.edges.append({"from_node_id": a, "to_node_id": b,
                                           "type": "similar", "similarity": sim})
                        added_edges += 1
                token_cache[nid] = label_tokens
            self._data["sessions"].append(key)

        if added_nodes or added_edges:
            self._data["version"] += 1
        self._save()
        self._check_migration()

        response = {
            "graph_version": self._data["version"],
            "added_nodes": added_nodes,
            "added_edges": added_edges,
            "edges": list(self.edges),
            "index_size": self.index_size,
            "index_sha256": sha256_json({
                "nodes": sorted(self.nodes),
                "edges": sorted((e["from_node_id"], e["to_node_id"], e["type"])
                                for e in self.edges),
            }),
        }
        errs = validate_against_schema(response, bundle_schema_path(PASS_ID, "response.schema.json"))
        if errs:
            raise RuntimeError(f"topic-graph response failed schema: {errs}")
        return response


def run(source: str, filename: str, topics: list[dict], store_dir: str,
        edge_threshold: Optional[float] = None,
        migration_threshold: int = MIGRATION_THRESHOLD) -> dict:
    g = TopicGraph(store_dir, edge_threshold=edge_threshold or 0.7,
                   migration_threshold=migration_threshold)
    return g.add_session(source, filename, topics, edge_threshold=edge_threshold)
