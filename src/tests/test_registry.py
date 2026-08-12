"""Tests for the registry server v2 surface (docs/service-features-v2.md).

Covers src/registry_index.py (shared catalog/search used by both the CLI and
the HTTP service) and the v2 HTTP routes of src/server.py: /search,
filtered /bundles, /bundles/{name}, /invariants, /capabilities, /ledger.

Unlike test_server.py these tests do NOT require PDD_EVIDENCE_KEY: the ledger
route reports `verified` fail-closed (False without the key), and everything
else is read-only catalog data. Run: python3 -m pytest src/tests -q
"""

import importlib.util
import json
import sys
import tempfile
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(ROOT / "src"))
import registry_index  # noqa: E402

_spec = importlib.util.spec_from_file_location("pdd_server", ROOT / "src" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
server.ROOT = ROOT
server.SKILLS = ROOT / ".reasonix" / "skills"
server.BUNDLES = ROOT / "pdd-bundles"
server.EVIDENCE = ROOT / "evidence"
server.PDD = ROOT / "scripts" / "pdd.py"


@pytest.fixture
def client():
    """A live HTTP client against the real Handler on an ephemeral port."""
    httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, _decode(err.read())

    yield get
    httpd.shutdown()
    thread.join(timeout=5)


def _decode(raw: bytes):
    """JSON responses parse as dicts; /healthz is plain text."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode()


# --- registry_index (the shared index) ------------------------------------


def test_index_catalog_shape():
    catalog = registry_index.load_catalog(ROOT / "pdd-bundles")
    # The registry now carries the 9 sealed bundles (7 transcript-annotation
    # bundles were added on top of pdd-registry + user-registry).
    assert sorted(b["name"] for b in catalog) == [
        "annotation-store", "pdd-registry", "reflection-packet",
        "topic-flow-review", "topic-graph", "topic-transition-pass",
        "transcript-chunking", "uncertainty-pass", "user-registry",
    ]
    b = next(b for b in catalog if b["name"] == "user-registry")
    assert b["status"] == "sealed"
    assert b["version"] == "1.0.0"
    assert set(b["invariants"]) == {"structural", "behavioral", "operational"}
    assert b["invariants"]["structural"][0]["id"] == "S-001"
    assert "network" in b["capabilities"]


def test_search_ranks_purpose_over_invariant():
    catalog = registry_index.load_catalog(ROOT / "pdd-bundles")
    res = registry_index.search(catalog, "idempotent")
    ids = [(r["layer"], r["id"]) for r in res]
    assert ("bundle", "purpose") in ids
    assert ("behavioral", "B-001") in ids
    # field weight: purpose (5) outranks invariant text (3)
    assert res[0]["layer"] == "bundle" and res[0]["id"] == "purpose"
    assert all(r["bundle"] == "user-registry" for r in res)


def test_search_requires_all_tokens():
    catalog = registry_index.load_catalog(ROOT / "pdd-bundles")
    # AND semantics: both tokens must appear in one entry.
    assert registry_index.search(catalog, "user registry") != []
    # "idempotent" and "network" never co-occur in one entry.
    assert registry_index.search(catalog, "idempotent network") == []
    assert registry_index.search(catalog, "user zzzznoop") == []


def test_search_empty_and_missing_query():
    catalog = registry_index.load_catalog(ROOT / "pdd-bundles")
    assert registry_index.search(catalog, "") == []
    assert registry_index.search(catalog, "  !!  ") == []


def test_invariants_view_severity_filter():
    catalog = registry_index.load_catalog(ROOT / "pdd-bundles")
    view = registry_index.invariants_view(catalog[0], severity="must")
    assert all(it["severity"] == "must"
               for layer in view.values() for it in layer)
    # operational has one `should` (O-005) — must-filter drops it. Derived
    # from the bundle itself so the test stays valid as the registry grows.
    op = catalog[0]["invariants"]["operational"]
    assert len(view["operational"]) == sum(1 for it in op if it["severity"] == "must")
    assert len(view["operational"]) < len(op)


def test_load_bundle_tolerates_null_sections(tmp_path):
    """A bundle with null invariant/capability sections must load, not crash."""
    bdir = tmp_path / "null-bundle"
    bdir.mkdir()
    (bdir / "protocol.yaml").write_text(
        "protocol:\n  name: null-bundle\n  version: 1.0.0\n  status: draft\n"
        "purpose: null\n")
    (bdir / "invariants").mkdir()
    (bdir / "invariants" / "structural.yaml").write_text("structural_invariants:\n")
    (bdir / "capability-manifest.yaml").write_text("capabilities:\n")
    b = registry_index.load_bundle(bdir)
    assert "error" not in b
    assert b["invariants"]["structural"] == []
    assert b["capabilities"] == {}


def test_ledger_view_limit():
    view = registry_index.ledger_view(ROOT / "evidence", "user-registry", limit=1)
    assert view["count"] == 1
    assert len(view["blocks"]) == 1
    assert view["blocks"][0]["decision"] == "attest-pass"
    full = registry_index.ledger_view(ROOT / "evidence", "user-registry")
    assert full["count"] == 1 and len(full["blocks"]) == 1
    # limit=0 means zero blocks (never "all" via the -0 slice)
    zero = registry_index.ledger_view(ROOT / "evidence", "user-registry", limit=0)
    assert zero["count"] == 1 and zero["blocks"] == []


# --- HTTP routes (v2) ------------------------------------------------------


def test_get_healthz(client):
    status, body = client("/healthz")
    assert status == 200
    assert body.strip() == "pdd-service: ok"


def test_get_bundles_filtered(client):
    status, body = client("/bundles?status=sealed")
    assert status == 200
    assert sorted(b["name"] for b in body["bundles"]) == [
        "annotation-store", "pdd-registry", "reflection-packet",
        "topic-flow-review", "topic-graph", "topic-transition-pass",
        "transcript-chunking", "uncertainty-pass", "user-registry",
    ]
    status, body = client("/bundles?status=draft")
    assert status == 200
    assert body["bundles"] == []
    status, body = client("/bundles?depends_on=user-registry")
    assert status == 200
    assert body["bundles"] == []  # no cross-bundle dependency graph yet


def test_get_bundle_summary(client):
    status, body = client("/bundles/user-registry")
    assert status == 200
    assert body["name"] == "user-registry"
    assert body["status"] == "sealed"
    assert "idempotent" in (body["purpose"] or "").lower()
    assert "B-001" in body["invariant_ids"]["behavioral"]
    assert "user-registry.create" in body["provides"]


def test_get_bundle_invariants(client):
    status, body = client("/bundles/user-registry/invariants")
    assert status == 200
    assert set(body["invariants"]) == {"structural", "behavioral", "operational"}
    assert body["invariants"]["behavioral"][0]["id"] == "B-001"
    status, body = client("/bundles/user-registry/invariants?severity=should")
    assert status == 200
    # only O-005 is `should`
    ids = [it["id"] for layer in body["invariants"].values() for it in layer]
    assert ids == ["O-005"]


def test_get_bundle_capabilities(client):
    status, body = client("/bundles/user-registry/capabilities")
    assert status == 200
    caps = body["capabilities"]
    assert caps["network"]["outbound_allowed"] is False
    assert caps["filesystem"]["write_allowed"] is False


def test_get_bundle_ledger(client):
    status, body = client("/bundles/user-registry/ledger?limit=1")
    assert status == 200
    assert body["count"] == 1
    assert len(body["blocks"]) == 1
    assert body["blocks"][0]["decision"] == "attest-pass"
    # fail-closed: `verified` is a real verification result, never assumed
    assert body["verified"] in (True, False)
    # limit=0 -> no blocks; negative / non-integer limit -> 400
    status, body = client("/bundles/user-registry/ledger?limit=0")
    assert status == 200 and body["blocks"] == []
    status, _ = client("/bundles/user-registry/ledger?limit=-1")
    assert status == 400
    status, _ = client("/bundles/user-registry/ledger?limit=abc")
    assert status == 400


def test_search_route(client):
    status, body = client("/search?q=idempotent")
    assert status == 200
    assert body["count"] >= 1
    assert body["results"][0]["bundle"] == "user-registry"
    ids = [(r["layer"], r["id"]) for r in body["results"]]
    assert ("behavioral", "B-001") in ids


def test_search_route_missing_q(client):
    status, body = client("/search")
    assert status == 400
    assert "q" in body["error"]


def test_bundle_route_unknown_name(client):
    status, body = client("/bundles/nope")
    assert status == 404
    assert "nope" in body["error"]


def test_bundle_route_bad_subpath(client):
    status, _ = client("/bundles/user-registry/doesnotexist")
    assert status == 404


def test_route_not_found(client):
    status, _ = client("/nonsense")
    assert status == 404


# --- review hardening (post-mutation review findings) ----------------------


def test_load_bundle_normalizes_depends_on_and_capabilities(tmp_path):
    """A string depends_on and a list capabilities must be normalized so the
    /bundles?depends_on= filter stays exact-membership and search/index never
    crash on `.keys()` of a list."""
    bdir = tmp_path / "shape-bundle"
    bdir.mkdir()
    (bdir / "protocol.yaml").write_text(
        "protocol:\n  name: shape-bundle\n  version: 1.0.0\n  status: draft\n"
        "depends_on: user-registry\n")  # string, not list
    (bdir / "invariants").mkdir()
    (bdir / "invariants" / "structural.yaml").write_text("structural_invariants:\n")
    (bdir / "capability-manifest.yaml").write_text("capabilities:\n  - one\n  - two\n")
    b = registry_index.load_bundle(bdir)
    assert "error" not in b
    assert b["depends_on"] == ["user-registry"]
    assert b["capabilities"] == {}


def test_ledger_view_rejects_escaping_names():
    """ledger_view must never read outside evidence_root, even when called
    directly (the HTTP layer already constrains names to real bundles)."""
    for bad in ("..", "../evidence", "user-registry/../..", "a/b"):
        view = registry_index.ledger_view(ROOT / "evidence", bad)
        assert view["error"] == "invalid bundle name"
        assert view["blocks"] == []


def test_bundle_route_broken_bundle(client, monkeypatch):
    """A broken bundle (unparseable protocol.yaml) must yield a generic 500
    (no YAML parser internals echoed), not a KeyError crash."""
    with tempfile.TemporaryDirectory() as td:
        bdir = Path(td) / "broken-bundle"
        bdir.mkdir()
        (bdir / "protocol.yaml").write_text("protocol: [unclosed\n")
        monkeypatch.setattr(server, "BUNDLES", Path(td))
        status, body = client("/bundles/broken-bundle")
        assert status == 500
        assert "broken" in body["error"]
        # other bundles are still served from the real catalog
        monkeypatch.setattr(server, "BUNDLES", ROOT / "pdd-bundles")
        status, body = client("/bundles/user-registry")
        assert status == 200 and body["name"] == "user-registry"


def test_bundles_filter_depends_on_positive(client, monkeypatch):
    """A bundle with a string depends_on must match the filter exactly after
    normalization (no substring matching)."""
    with tempfile.TemporaryDirectory() as td:
        bdir = Path(td) / "dependent-bundle"
        bdir.mkdir()
        (bdir / "protocol.yaml").write_text(
            "protocol:\n  name: dependent-bundle\n  version: 1.0.0\n  status: draft\n"
            "depends_on: user-registry\n")  # string form, normalized to [..]
        monkeypatch.setattr(server, "BUNDLES", Path(td))
        status, body = client("/bundles?depends_on=user-registry")
        assert status == 200
        assert [b["name"] for b in body["bundles"]] == ["dependent-bundle"]
        # exact membership: a prefix must NOT match
        status, body = client("/bundles?depends_on=user")
        assert status == 200 and body["bundles"] == []


def test_load_catalog_missing_dir(tmp_path):
    """A missing bundles dir yields an empty catalog, not an exception."""
    assert registry_index.load_catalog(tmp_path / "does-not-exist") == []
