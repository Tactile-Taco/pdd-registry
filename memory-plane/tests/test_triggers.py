"""Trigger layer tests: hot-patch detection and per-agent firing on
synthetic store state."""

from __future__ import annotations

import json
import os
import time

from conftest import make_graph, make_packet

from memory_plane.store import ArtifactStore
from memory_plane.triggers import TriggerEvaluator, hot_patches


def _ev(store_dir, db, **kw):
    return TriggerEvaluator(store_dir, db,
                            cadence_mb=0.001, retro_mb=0.001, **kw)


def test_hot_patches_requires_contiguous_run(tmp_path):
    p = {"packet": {"heatmap": {"matrix": {"cells": [
        [0.1, 1.6, 1.7, 1.8, 0.2, 1.9, 2.0],          # run of 3 then 2
        [1.6, None, 1.7, 0.1, 1.6, 0.1, 1.6],          # null breaks a run
        [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    ]}}}}
    patches = hot_patches(p, z=1.5, run=3)
    assert patches == [(0, [1, 2, 3])]
    assert hot_patches(p, z=1.5, run=4) == []


def test_case_study_fires_on_hot_packet(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl",
                cells=[[0.1, 2.0, 2.1, 2.2, 0.1]])
    db = ArtifactStore(":memory:")
    fired = _ev(str(tmp_path), db).evaluate()
    assert "case-study" in fired
    assert "hot-patch" in fired["case-study"][0]
    db.close()


def test_no_fire_without_hot_patch(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1, 0.2, 0.3, 0.1]])
    db = ArtifactStore(":memory:")
    fired = _ev(str(tmp_path), db).evaluate()
    assert "case-study" not in fired
    db.close()


def test_reflection_requires_cadence_and_data(tmp_path):
    # old state, but no new data since -> no reflection
    db = ArtifactStore(":memory:")
    db.set_state("reflection.ts", str(time.time() - 6 * 86400))
    p = make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]])
    db.set_state("reflection.bytes", str(os.path.getsize(p)))
    fired = _ev(str(tmp_path), db).evaluate()
    assert "reflection" not in fired

    # new data arrives -> fires (pad the second packet past the MB floor)
    p2 = make_packet(tmp_path, "reasonix", "s2.jsonl", cells=[[0.1]])
    with open(p2, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["pad"] = "x" * 4096
    with open(p2, "w", encoding="utf-8") as f:
        json.dump(data, f)
    fired = _ev(str(tmp_path), db).evaluate()
    assert "reflection" in fired
    assert "cadence" in fired["reflection"][0]
    db.close()


def test_reflection_first_run_on_corpus(tmp_path):
    p = make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]])
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["pad"] = "x" * 4096
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    db = ArtifactStore(":memory:")
    fired = _ev(str(tmp_path), db).evaluate()
    assert "reflection" in fired
    assert "first reflection" in fired["reflection"][0]
    db.close()


def test_concluded_cluster_fires_reflection_and_retro(tmp_path):
    sessions = [f"reasonix-s{i}" for i in range(6)]
    node_ids = [f"{s}::t1" for s in sessions]
    edges = [(node_ids[i], node_ids[i + 1], "similar") for i in range(5)]
    for i, s in enumerate(sessions):
        path = make_packet(tmp_path, "reasonix", f"s{i}.jsonl", cells=[[0.1]])
        old = time.time() - 15 * 86400
        os.utime(path, (old, old))
    make_graph(str(tmp_path), edges=edges, sessions=sessions)
    db = ArtifactStore(":memory:")
    fired = _ev(str(tmp_path), db).evaluate()
    assert "reflection" in fired
    assert any("concluded topic cluster" in r for r in fired["reflection"])
    assert "retrospective" in fired
    assert "cluster-concluded" in fired["retrospective"][0]
    db.close()


def test_heatmap_anomaly_retrospective(tmp_path):
    make_packet(tmp_path, "reasonix", "s1.jsonl",
                cells=[[2.0, 2.0, 2.0, 2.0, 2.0]])
    db = ArtifactStore(":memory:")
    fired = _ev(str(tmp_path), db).evaluate()
    assert "retrospective" in fired
    assert "heatmap-anomaly" in fired["retrospective"][0]
    db.close()


def test_retro_volume_floor(tmp_path):
    db = ArtifactStore(":memory:")
    db.set_state("retrospective.ts", str(time.time() - 86400))
    p = make_packet(tmp_path, "reasonix", "s1.jsonl", cells=[[0.1]])
    # bump the "total" beyond the old baseline by rewriting a bigger file
    with open(p, "a", encoding="utf-8") as f:
        f.write(" " * 4096)  # invalid JSON, but size is what matters here
    fired = _ev(str(tmp_path), db).evaluate()
    assert "retrospective" in fired
    assert "volume-floor" in fired["retrospective"][0]
    db.close()


def test_meta_fires_on_proposal_accumulation(tmp_path, store):
    store.set_state("meta.ts", str(time.time()))
    for i in range(3):
        store.add_proposal({"proposal_id": f"p{i}", "kind": "no-proposal",
                            "judgement": "naturally-hard",
                            "reasoning": "inherently hard", "motivated_by": []},
                           "ref-1")
    fired = _ev(str(tmp_path), store).evaluate()
    assert "meta" in fired
    assert "proposals accumulated" in fired["meta"][0]
