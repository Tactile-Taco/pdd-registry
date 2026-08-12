"""Annotation-derived trigger evaluation for the fleet.

Deterministic functions over the pipeline's store dir (packets/, topic-graph/,
annotation files) — the same store the backlog runner writes. All signals are
computed from data the sealed bundles already produce; the v0.2 protocol flags
(skill-usage layer, cluster lifecycle in the packet, flow_graph) are NOT
required. Substitutions vs the design doc:
  - skill-usage signal does not exist yet -> case-study trigger uses hot-patch
    alone (documented in README).
  - cluster dormancy uses packet file mtimes as the per-session activity proxy
    (the graph has no per-node timestamps yet).
"""

from __future__ import annotations

import json
import os
import re
import time


def session_key(source: str, filename: str) -> str:
    """Same normalization as the topic-graph bundle (keep in sync)."""
    stem = re.sub(r"[^a-z0-9]+", "-", filename.rsplit(".", 1)[0].lower()).strip("-")
    stem = re.sub(r"-{2,}", "-", stem) or "session"
    return f"{source}-{stem}"


def load_packets(store_dir: str) -> list[dict]:
    packets = []
    pdir = os.path.join(store_dir, "packets")
    if not os.path.isdir(pdir):
        return packets
    for fn in sorted(os.listdir(pdir)):
        if not fn.endswith(".packet.json"):
            continue
        path = os.path.join(pdir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                packets.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue  # a partial write; skip, journal will catch up later
    return packets


def load_graph(store_dir: str) -> dict:
    path = os.path.join(store_dir, "topic-graph", "topic-graph.json")
    if not os.path.exists(path):
        return {"nodes": {}, "edges": [], "sessions": []}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"nodes": {}, "edges": [], "sessions": []}


def hot_patches(packet: dict, z: float = 1.5, run: int = 3) -> list[tuple[int, list[int]]]:
    """Contiguous runs of >= `run` cells with |deviation| > z in one heatmap
    row (a hot region, not a single spike). Returns [(row_idx, [col_idx...])]."""
    hm = (packet.get("packet") or {}).get("heatmap") or {}
    matrix = hm.get("matrix") or {}
    cells = matrix.get("cells") or []
    out = []
    for r, row in enumerate(cells):
        cols: list[int] = []
        for c, val in enumerate(row):
            if val is None or abs(val) <= z:
                cols = []
                continue
            cols.append(c)
            if len(cols) == run:
                out.append((r, list(cols)))
    return out


def has_hot_patch(packet: dict, z: float = 1.5, run: int = 3) -> bool:
    return bool(hot_patches(packet, z=z, run=run))


def _packet_meta(packet: dict) -> dict:
    p = packet.get("packet") or {}
    sess = p.get("session") or {}
    return {
        "source": sess.get("source", ""),
        "filename": sess.get("filename", ""),
        "key": session_key(sess.get("source", ""), sess.get("filename", "")),
        "fidelity_class": sess.get("fidelity_class", "lossy"),
    }


def _clusters(graph: dict) -> list[list[str]]:
    """Connected components over 'similar' edges (union-find)."""
    nodes = list((graph.get("nodes") or {}).keys())
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in graph.get("edges") or []:
        if e.get("type") != "similar":
            continue
        a, b = e.get("from_node_id"), e.get("to_node_id")
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    comps: dict[str, list[str]] = {}
    for n in nodes:
        comps.setdefault(find(n), []).append(n)
    return list(comps.values())


class TriggerEvaluator:
    """Fires fleet agents from store-dir state + the artifact-store run state.

    Returns {agent_id: [reason, ...]}.
    """

    def __init__(self, store_dir: str, state,
                 *, hot_z: float = 1.5, hot_run: int = 3,
                 cadence_days: float = 5.0, cadence_mb: float = 5.0,
                 retro_mb: float = 25.0, cluster_min: int = 6,
                 dormant_days: float = 14.0, meta_proposals: int = 3,
                 meta_days: float = 7.0) -> None:
        self.store_dir = store_dir
        self.state = state  # ArtifactStore (get_state/set_state)
        self.hot_z, self.hot_run = hot_z, hot_run
        self.cadence_days, self.cadence_mb = cadence_days, cadence_mb
        self.retro_mb = retro_mb
        self.cluster_min, self.dormant_days = cluster_min, dormant_days
        self.meta_proposals, self.meta_days = meta_proposals, meta_days

    # -- signals ------------------------------------------------------------
    def _new_bytes_since(self, last_key: str) -> float:
        last_ts = float(self.state.get_state(last_key + ".ts") or 0.0)
        last_bytes = float(self.state.get_state(last_key + ".bytes") or 0.0)
        total = 0.0
        pdir = os.path.join(self.store_dir, "packets")
        if os.path.isdir(pdir):
            for fn in os.listdir(pdir):
                if fn.endswith(".packet.json"):
                    try:
                        total += os.path.getsize(os.path.join(pdir, fn))
                    except OSError:
                        pass
        # New data = current total minus what the last run accounted for
        # (state stores the baseline snapshot at the last run).
        return max(0.0, total - last_bytes) if last_ts > 0 else total

    def _newest_packet_ts(self) -> float:
        pdir = os.path.join(self.store_dir, "packets")
        newest = 0.0
        if os.path.isdir(pdir):
            for fn in os.listdir(pdir):
                if fn.endswith(".packet.json"):
                    try:
                        newest = max(newest, os.path.getmtime(os.path.join(pdir, fn)))
                    except OSError:
                        pass
        return newest

    def _concluded_clusters(self, packets: list[dict]) -> list[dict]:
        """Clusters with >= cluster_min sessions whose last activity is older
        than dormant_days (v1 dormancy proxy: packet mtime)."""
        graph = load_graph(self.store_dir)
        meta = {p["packet"]["session"]["filename"]: p for p in packets}
        activity: dict[str, float] = {}
        for p in packets:
            m = _packet_meta(p)
            path = os.path.join(self.store_dir, "packets",
                                f"{m['source']}-{m['filename']}.packet.json")
            try:
                activity[m["key"]] = max(activity.get(m["key"], 0.0),
                                         os.path.getmtime(path))
            except OSError:
                pass
        out = []
        for comp in _clusters(graph):
            session_keys = {n.split("::")[0] for n in comp if "::" in n}
            if len(session_keys) < self.cluster_min:
                continue
            last = max((activity.get(k, 0.0) for k in session_keys), default=0.0)
            if last and (time.time() - last) / 86400.0 >= self.dormant_days:
                out.append({"cluster": sorted(session_keys),
                            "session_count": len(session_keys),
                            "last_activity": last})
        return out

    def _heatmap_anomaly(self, packets: list[dict]) -> float:
        vals = []
        for p in packets:
            hm = (p.get("packet") or {}).get("heatmap") or {}
            cells = (hm.get("matrix") or {}).get("cells") or []
            for row in cells:
                vals.extend(v for v in row if v is not None)
        if not vals:
            return 0.0
        return sum(abs(v) for v in vals) / len(vals)

    # -- evaluation -----------------------------------------------------------
    def evaluate(self) -> dict[str, list[str]]:
        packets = load_packets(self.store_dir)
        fired: dict[str, list[str]] = {a: [] for a in ("case-study", "reflection",
                                                       "retrospective", "meta")}
        now = time.time()

        # case-study curator: hot-patch in any packet (v1; skill-usage signal
        # is a future pipeline flag).
        for p in packets:
            if has_hot_patch(p, z=self.hot_z, run=self.hot_run):
                m = _packet_meta(p)
                fired["case-study"].append(
                    f"hot-patch in {m['source']}/{m['filename']} "
                    f"(z>{self.hot_z} over >= {self.hot_run} cells)")

        # reflection: cadence (>= cadence_days) AND new data (>= cadence_mb),
        # or a concluded long-running topic.
        last_ref = float(self.state.get_state("reflection.ts") or 0.0)
        new_bytes = self._new_bytes_since("reflection")
        if last_ref > 0 and (now - last_ref) / 86400.0 >= self.cadence_days \
                and new_bytes >= self.cadence_mb * 1024 * 1024:
            fired["reflection"].append(
                f"cadence: {(now - last_ref) / 86400.0:.1f}d elapsed, "
                f"{new_bytes / 1e6:.1f} MB new packets")
        elif last_ref == 0 and new_bytes >= self.cadence_mb * 1024 * 1024:
            fired["reflection"].append("first reflection: corpus present")
        concluded = self._concluded_clusters(packets)
        if concluded:
            fired["reflection"].append(
                f"concluded topic cluster: {concluded[0]['session_count']} sessions "
                f"dormant >= {self.dormant_days}d")

        # retrospective checkpoints: major cluster concluded, heatmap anomaly,
        # or the ~25 MB volume floor.
        if any(c["session_count"] >= self.cluster_min for c in concluded):
            fired["retrospective"].append(
                f"cluster-concluded checkpoint ({concluded[0]['session_count']} sessions)")
        anomaly = self._heatmap_anomaly(packets)
        if anomaly > self.hot_z:
            fired["retrospective"].append(
                f"heatmap-anomaly checkpoint (mean |z|={anomaly:.2f} > {self.hot_z})")
        retro_bytes = self._new_bytes_since("retrospective")
        if retro_bytes >= self.retro_mb * 1024 * 1024:
            fired["retrospective"].append(
                f"volume-floor checkpoint ({retro_bytes / 1e6:.1f} MB)")

        # meta-agent: proposal accumulation or cadence.
        proposed = sum(1 for p in self.state.proposals("proposed")
                       if p.get("status") == "proposed")
        last_meta = float(self.state.get_state("meta.ts") or 0.0)
        if proposed >= self.meta_proposals:
            fired["meta"].append(f"{proposed} proposals accumulated")
        elif last_meta > 0 and (now - last_meta) / 86400.0 >= self.meta_days:
            fired["meta"].append(f"cadence: {(now - last_meta) / 86400.0:.1f}d since last")
        elif last_meta == 0 and proposed > 0:
            fired["meta"].append("first meta cycle: proposals exist")

        return {k: v for k, v in fired.items() if v}
