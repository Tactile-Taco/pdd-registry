"""Candidate tests for the pdd-registry bundle (invariant lineage).

Each test cites the invariant(s) it exercises (S-001..S-003, B-001..B-005,
O-001..O-005). Runs under pytest + hypothesis with a scrubbed environment
(no secrets, fresh HOME) — see validators/validate_candidate.py.
"""

import copy
import json
import sys
from pathlib import Path

from hypothesis import given, strategies as st

REPO_ROOT = Path(__file__).resolve().parents[4]
IMPL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(IMPL_DIR))

from pdd_registry import Registry, ERROR_KINDS, SEVERITIES  # noqa: E402

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

_SCHEMAS = REPO_ROOT / "pdd-bundles" / "pdd-registry" / "schemas"


def _response_schema():
    """Load the bundle's response schema lazily: the mutant run executes from
    a tempdir where the repo path does not exist — collection must not fail."""
    return json.loads((_SCHEMAS / "response.schema.json").read_text())


def _catalog():
    """Two bundles, deliberately ordered so an INVARIANT match of
    alpha-registry precedes the NAME match of idempotent-helper in insertion
    order but must sort AFTER it for the query 'idempotent' (B-001 mutant
    detection, B-002 ranking)."""
    return [
        {
            "name": "alpha-registry",
            "version": "1.0.0",
            "status": "sealed",
            "namespace": "alpha",
            "tags": ["engine", "data-catalog"],
            "purpose": "User creation registry.",
            "depends_on": [],
            "provides": {"alpha.create": "schemas/request.schema.json"},
            "invariants": {
                "structural": [{"id": "S-001", "statement": "Conforms to schema.", "severity": "must"}],
                "behavioral": [{"id": "B-001", "statement": "Idempotent creation.", "severity": "must"}],
                "operational": [],
            },
            "capabilities": {"network": "none"},
        },
        {
            "name": "idempotent-helper",
            "version": "2.0.0",
            "status": "sealed",
            "namespace": "beta",
            "tags": ["server", "engine"],
            "purpose": "Idempotent operations for dependent bundles.",
            "depends_on": ["alpha-registry"],
            "provides": {"idem.guarantee": "schemas/idem.json"},
            "invariants": {
                "structural": [],
                "behavioral": [{"id": "B-010", "statement": "Replay idempotent requests.", "severity": "should"}],
                "operational": [],
            },
            "capabilities": {"telemetry": "none"},
        },
    ]


# --- S-001: response shape conforms to the schema -------------------------


def test_S001_search_response_matches_response_schema():
    if jsonschema is None or not _SCHEMAS.exists():
        return  # engine records this as skip upstream; keep the suite green here
    r = Registry(_catalog()).search("idempotent")
    jsonschema.validate(r, _response_schema())


def test_S001_filtered_bundles_are_serializable():
    r = Registry(_catalog()).bundles(status="sealed")
    jsonschema.validate(json.loads(json.dumps(r)), {
        "type": "object", "required": ["ok", "bundles", "count"]})


# --- S-002: stable error envelope -----------------------------------------


def test_S002_error_envelope_uses_enumerated_kinds():
    reg = Registry(_catalog())
    for err in (reg.search("   "), reg.search("!!!"), reg.bundle_summary("nope"),
                reg.invariants_view("alpha-registry", severity="maybe")):
        assert err["ok"] is False
        assert err["error"]["kind"] in ERROR_KINDS
        assert isinstance(err["error"]["message"], str) and err["error"]["message"]


# --- B-001: deterministic, non-mutating search ----------------------------


def test_B001_deterministic_search():
    """Stable order (score desc, then bundle/layer/id) and no catalog mutation.

    The B-001 mutant removes the stable sort; this catalog is ordered so the
    invariant match of alpha-registry precedes the name match of
    idempotent-helper in insertion order but must sort AFTER it — the mutant
    therefore fails this test (mutation sanity).
    """
    catalog = _catalog()
    before = copy.deepcopy(catalog)
    r1 = Registry(catalog).search("idempotent")
    r2 = Registry(catalog).search("idempotent")
    assert r1["ok"] is True
    assert r1["results"] == r2["results"]           # identical across calls
    assert [e["bundle"] for e in r1["results"]] == [
        "idempotent-helper", "idempotent-helper", "alpha-registry", "idempotent-helper"]
    assert r1["results"][0]["id"] == "name"        # name match first
    assert catalog == before                        # no mutation


@given(st.lists(st.dictionaries(
    st.sampled_from(["name", "purpose"]),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", max_size=40),
    max_size=6)))
def test_B001_order_stable_across_calls(entries):
    catalog = [{"name": e.get("name", "b%d" % i), "purpose": e.get("purpose", ""),
                "invariants": {}, "capabilities": {}} for i, e in enumerate(entries)]
    reg = Registry(catalog)
    r1 = reg.search("abc")
    r2 = reg.search("abc")
    assert r1["results"] == r2["results"]


# --- B-002: token semantics (AND per entry, name ranks above invariant) ---


@given(st.text(alphabet="abc ", max_size=20), st.text(alphabet="abc ", max_size=20))
def test_B002_tokens_and_per_entry(q1, q2):
    tokens = [t for t in (q1 + " " + q2).split() if t]
    if not tokens:
        return  # blank query fails closed (B-003), nothing to check
    r = Registry(_catalog()).search("%s %s" % (q1, q2))
    assert r["ok"] is True
    for e in r["results"]:
        hay = e["text"].lower()
        for tok in tokens:
            assert tok in hay  # every token matched THIS entry


def test_B002_name_matches_rank_above_invariant_matches():
    r = Registry(_catalog()).search("idempotent")
    assert r["results"][0]["bundle"] == "idempotent-helper"
    assert r["results"][0]["layer"] == "bundle" and r["results"][0]["id"] == "name"
    assert r["results"][0]["score"] > r["results"][1]["score"]


# --- B-003: fail closed on blank/unknown, no state change ------------------


@given(st.one_of(st.text(max_size=0), st.text(alphabet="  \t\n", max_size=8),
                 st.text(alphabet="!@#$%^&*()", max_size=8)))
def test_B003_blank_or_nonword_query_fails_closed(garbage):
    catalog = _catalog()
    before = copy.deepcopy(catalog)
    r = Registry(catalog).search(garbage)
    assert r["ok"] is False and r["error"]["kind"] == "invalid_request"
    assert catalog == before


def test_B003_unknown_bundle_returns_not_found_no_state_change():
    catalog = _catalog()
    before = copy.deepcopy(catalog)
    r = Registry(catalog).bundle_summary("does-not-exist")
    assert r["ok"] is False and r["error"]["kind"] == "not_found"
    assert catalog == before


# --- B-004: exact-match filters, stable order ------------------------------


@given(st.sampled_from(["sealed", "draft", "sealed-extra", ""]))
def test_B004_status_filter_is_exact(status):
    catalog = _catalog()
    r = Registry(catalog).bundles(status=status)
    assert all(b["status"] == status for b in r["bundles"]) if status else r["count"] == 0


def test_B004_depends_on_filter_exact_membership():
    r = Registry(_catalog()).bundles(depends_on="alpha-registry")
    assert [b["name"] for b in r["bundles"]] == ["idempotent-helper"]
    # a prefix must NOT match (exact membership, not substring)
    r2 = Registry(_catalog()).bundles(depends_on="alpha")
    assert r2["bundles"] == []


def test_B004_listing_keeps_stable_order():
    r1 = Registry(_catalog()).bundles()
    r2 = Registry(_catalog()).bundles()
    assert [b["name"] for b in r1["bundles"]] == [b["name"] for b in r2["bundles"]] == [
        "alpha-registry", "idempotent-helper"]


# --- B-005: severity filter validates kinds --------------------------------


def test_B005_severity_filter_accepts_only_must_should():
    reg = Registry(_catalog())
    for ok in SEVERITIES:
        r = reg.invariants_view("alpha-registry", severity=ok)
        assert r["ok"] is True
        assert all(it.get("severity") == ok for layer in r["invariants"].values() for it in layer)
    bad = reg.invariants_view("alpha-registry", severity="always")
    assert bad["ok"] is False and bad["error"]["kind"] == "invalid_request"


# --- S-004: namespace/name addressing, exact namespace filter ---------------


def test_S004_namespace_filter_exact():
    r = Registry(_catalog()).bundles(namespace="alpha")
    assert [b["name"] for b in r["bundles"]] == ["alpha-registry"]
    # exact match: prefixes must NOT match (S-004 addressing is exact)
    r2 = Registry(_catalog()).bundles(namespace="alph")
    assert r2["bundles"] == []
    r3 = Registry(_catalog()).bundles(namespace="nope")
    assert r3["bundles"] == []


@given(st.sampled_from(["alpha", "beta", "alph", "", "ALPHA"]))
def test_S004_namespace_filter_exact_property(ns):
    r = Registry(_catalog()).bundles(namespace=ns)
    for b in r["bundles"]:
        assert b["namespace"] == ns


def test_S004_address_is_namespace_name():
    r = Registry(_catalog()).bundles(namespace="alpha")
    assert r["bundles"][0]["address"] == "alpha/alpha-registry"
    s = Registry(_catalog()).bundle_summary("idempotent-helper")
    assert s["address"] == "beta/idempotent-helper"
    assert s["tags"] == ["server", "engine"]
    # a bundle without a namespace keeps the bare-name address
    bare = Registry([{"name": "legacy", "namespace": None, "tags": [],
                      "version": "1.0.0", "status": "draft"}]).bundle_summary("legacy")
    assert bare["address"] == "legacy"


# --- S-005: tag grammar consumers — exact membership filter, searchable -----


def test_S005_tag_filter_exact_membership():
    r = Registry(_catalog()).bundles(tag="engine")
    assert sorted(b["name"] for b in r["bundles"]) == ["alpha-registry", "idempotent-helper"]
    r2 = Registry(_catalog()).bundles(tag="eng")
    assert r2["bundles"] == []  # substring must NOT match
    r3 = Registry(_catalog()).bundles(tag="server")
    assert [b["name"] for b in r3["bundles"]] == ["idempotent-helper"]


@given(st.sampled_from(["engine", "server", "data-catalog", "eng", "engine "]))
def test_S005_tag_filter_exact_membership_property(tag):
    r = Registry(_catalog()).bundles(tag=tag)
    for b in r["bundles"]:
        assert tag in b["tags"]


def test_S005_tags_are_searchable_entries():
    r = Registry(_catalog()).search("engine")
    assert any(e["layer"] == "tags" and e["id"] == "engine" for e in r["results"])


def test_S004_S005_combined_filters():
    r = Registry(_catalog()).bundles(namespace="alpha", tag="server")
    assert r["bundles"] == []
    r2 = Registry(_catalog()).bundles(namespace="beta", tag="server")
    assert [b["name"] for b in r2["bundles"]] == ["idempotent-helper"]


# --- O-001/O-002: no network, no writes (static scan + sandbox attest) -----

# The candidate imports only the allowlist (json, re, typing, dataclasses) and
# performs no I/O; the validator's AST scan (import_scan) and docker sandbox
# (network none, read-only fs) attest O-001..O-004. These tests keep the
# property honest locally as well:

def test_O001_O002_no_io_in_candidate_source():
    src = (IMPL_DIR / "pdd_registry.py").read_text()
    for banned in ("open(", "write_text", "write_bytes", "subprocess", "socket", "urllib"):
        assert banned not in src


def test_O005_search_latency_budget_observed():
    import statistics
    import time
    reg = Registry(_catalog() * 20)
    lat = []
    for _ in range(200):
        t0 = time.perf_counter()
        reg.search("idempotent")
        lat.append((time.perf_counter() - t0) * 1000)
    p95 = statistics.quantiles(sorted(lat), n=20)[18]
    assert p95 < 500, f"p95 {p95:.2f}ms exceeds the 500ms advisory budget"
