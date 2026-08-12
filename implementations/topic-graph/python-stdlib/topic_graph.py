"""topic-graph — attested candidate core (pure, self-contained).

Cross-session topic graph: incremental index-first adds, deterministic local
embeddings (Jaccard over label tokens), typed edges, observable index_size,
migration threshold observer. No I/O — the runner persists the snapshot.
"""

import json
import re
from typing import Dict, List, Optional

from _hash import sha256_json

ERROR_KINDS = ("invalid_request", "conflict", "not_found", "internal")

PASS_ID = "topic-graph"
PASS_VERSION = "0.1.0-draft"

MIGRATION_THRESHOLD = 100_000
_EDGE_TYPES = ("similar", "revival", "overlap")


def _tokens(label: str) -> set:
    return set(re.findall(r"[a-z0-9]+", label.casefold()))


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = len(a | b)
    if not union:
        return 0.0
    return round(len(a & b) / union, 6)


def session_key(source: str, filename: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", filename.rsplit(".", 1)[0].lower()).strip("-")
    stem = re.sub(r"-{2,}", "-", stem) or "session"
    return f"{source}-{stem}"


class TopicGraphCore:
    def __init__(self, edge_threshold: float = 0.7,
                 migration_threshold: int = MIGRATION_THRESHOLD) -> None:
        self.version = 1
        self.nodes: Dict[str, dict] = {}
        self.edges: List[dict] = []
        self.sessions: List[str] = []
        self.edge_threshold = edge_threshold
        self.migration_threshold = migration_threshold
        self.migration_hits = 0
        self.migration_log: List[str] = []

    @property
    def index_size(self) -> int:
        return len(self.nodes) + len(self.edges)

    def _check_migration(self) -> None:
        if self.index_size > self.migration_threshold:
            self.migration_hits += 1
            self.migration_log.append(
                f"migration: index_size {self.index_size} > threshold "
                f"{self.migration_threshold} (version {self.version})")

    def add_session(self, source: str, filename: str, topics: List[dict],
                    edge_threshold: Optional[float] = None) -> dict:
        thr = self.edge_threshold if edge_threshold is None else edge_threshold
        key = session_key(source, filename)
        added_nodes = added_edges = 0

        if key not in self.sessions:
            token_cache = {nid: _tokens(n["label"]) for nid, n in self.nodes.items()}
            for t in topics:
                nid = f"{key}::{t['topic_id']}"
                if nid in self.nodes:
                    continue
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
            self.sessions.append(key)

        if added_nodes or added_edges:
            self.version += 1
        self._check_migration()

        return {
            "graph_version": self.version,
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
