"""Key-free contract tests for the v1.2 DB-backed registry storage.

Covers S-006 (transactional consistent materialization, sqlite :memory:
dialect of the same portable SQL used against PostgreSQL), S-007 (evidence
resource_identifier enforced on ingest) and B-006 (idempotent publish by
(namespace, name, version, digest)).

Run: python3 -m pytest src/tests/test_registry_db.py -q
"""

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pdd_server", ROOT / "src" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)
server.ROOT = ROOT
server.BUNDLES = ROOT / "pdd-bundles"
server.EVIDENCE = ROOT / "evidence"
server.SKILLS = ROOT / ".reasonix" / "skills"

import registry_db  # noqa: E402 (src/ on sys.path via server import)


def _bundle(**overrides) -> dict:
    b = {
        "namespace": "pdd", "name": "pdd-registry", "version": "1.2.0",
        "status": "sealed", "digest": "sha256:" + "a" * 64,
        "purpose": "catalog search and read views",
        "tags": ["engine"], "depends_on": [], "provides": {},
        "invariants": {"structural": [{"id": "S-001"}]},
        "capabilities": {}, "boundary": {"in_scope": []},
    }
    b.update(overrides)
    return b


def _evidence(**overrides) -> dict:
    e = {
        "artifact_id": "pdd-registry-python-stdlib",
        "resource_identifier": "https://github.com/example/repo/actions/runs/42",
        "decision": "attest-pass",
        "signed_object": {"ok": True},
        "digest": "",
    }
    e.update(overrides)
    return e


@pytest.fixture
def conn():
    c = registry_db.connect("sqlite:///:memory:")
    registry_db.init_schema(c)
    yield c
    c.close()


def test_schema_init_and_empty_catalog(conn):
    assert registry_db.list_catalog(conn) == []


def test_publish_roundtrip(conn):
    rec = registry_db.publish(conn, _bundle(), _evidence())
    assert rec["ok"] is True
    catalog = registry_db.list_catalog(conn)
    assert len(catalog) == 1
    entry = catalog[0]
    assert entry["name"] == "pdd-registry"
    assert entry["namespace"] == "pdd"
    assert entry["tags"] == ["engine"]
    assert entry["address"] == "pdd/pdd-registry"
    assert entry["version"] == "1.2.0"
    ev = registry_db.evidence_records(conn, "pdd-registry")
    assert len(ev) == 1
    assert ev[0]["resource_identifier"].startswith("https://")


def test_B006_publish_idempotent_by_digest(conn):
    """Same (namespace, name, version, digest) twice -> one record; a
    different digest for the same (namespace, name, version) is a distinct
    version record, never a silent overwrite."""
    registry_db.publish(conn, _bundle(), _evidence())
    again = registry_db.publish(conn, _bundle(), _evidence())
    assert again["ok"] is True
    assert len(registry_db.list_catalog(conn)) == 1  # no duplicate
    # distinct digest -> distinct record (new version of the same bundle)
    registry_db.publish(conn, _bundle(digest="sha256:" + "b" * 64), _evidence())
    catalog = registry_db.list_catalog(conn)
    assert len(catalog) == 2
    digests = {b["version"] for b in catalog}
    assert digests == {"1.2.0"}


def test_S007_evidence_requires_resource_identifier(conn):
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(resource_identifier=""))
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(resource_identifier="not-a-url"))
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(resource_identifier="ftp://x"))
    # valid http(s) and urn: forms are accepted
    registry_db.publish(conn, _bundle(), _evidence(resource_identifier="https://ci.example/runs/1"))
    registry_db.publish(conn, _bundle(digest="sha256:" + "b" * 64),
                        _evidence(resource_identifier="urn:pdd:run:42"))
    assert len(registry_db.list_catalog(conn)) == 2


def test_publish_rejects_malformed_bundle(conn):
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(digest="md5:xyz"), _evidence())
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(namespace="Bad_NS"), _evidence())
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(tags=["engine", "engine"]), _evidence())
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(tags=[1]), _evidence())
    assert registry_db.list_catalog(conn) == []


def test_get_bundle_and_consistent_read(conn):
    registry_db.publish(conn, _bundle(), _evidence())
    b = registry_db.get_bundle(conn, "pdd-registry")
    assert b["version"] == "1.2.0"
    assert registry_db.get_bundle(conn, "nope") is None


def test_unsupported_url_scheme_rejected():
    with pytest.raises(ValueError):
        registry_db.connect("mysql://x/y")


def test_adapt_sql_placeholder_translation():
    """The adapter keeps ONE portable SQL string ('?' placeholders) and
    translates to '%s' for psycopg at execution time — sqlite and postgres
    must never diverge (blocking review finding: psycopg rejects '?')."""
    sql = "INSERT INTO t (a, b) VALUES (?, ?) ON CONFLICT DO NOTHING"
    assert registry_db._adapt_sql(sql, postgres=False) == sql
    adapted = registry_db._adapt_sql(sql, postgres=True)
    assert adapted == "INSERT INTO t (a, b) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    # no placeholders -> unchanged
    assert registry_db._adapt_sql("SELECT 1", postgres=True) == "SELECT 1"


def test_publish_uses_conflict_free_idempotency(conn):
    """INSERT ... ON CONFLICT DO NOTHING (not sqlite-only INSERT OR IGNORE)
    keeps B-006 idempotency on BOTH dialects."""
    payload = {"bundle": _bundle(), "evidence": _evidence()}
    registry_db.publish(conn, payload["bundle"], payload["evidence"])
    registry_db.publish(conn, payload["bundle"], payload["evidence"])
    assert len(registry_db.list_catalog(conn)) == 1


def test_entry_fills_missing_invariant_layers(conn):
    """Views iterate every invariant layer; a published bundle may omit empty
    layers — materialization must fill them (no KeyError on the server)."""
    b = _bundle()
    b["invariants"] = {"structural": [{"id": "S-001"}]}  # no behavioral/operational
    registry_db.publish(conn, b, _evidence())
    entry = registry_db.list_catalog(conn)[0]
    assert entry["invariants"]["structural"][0]["id"] == "S-001"
    assert entry["invariants"]["behavioral"] == []
    assert entry["invariants"]["operational"] == []


# --- HTTP surface: publish handshake + DB-backed reads ----------------------


@pytest.fixture
def db_client(monkeypatch):
    """Live HTTP client against the real Handler in DB-backed mode."""
    conn = registry_db.connect("sqlite:///:memory:")
    registry_db.init_schema(conn)
    monkeypatch.setattr(server, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PDD_EVIDENCE_KEY", "test-key")  # evidence verify path
    server._db_conn = conn
    httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def request(path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST" if payload is not None else "GET")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode())

    request.base_url = base  # exposed for CLI remote tests
    yield request
    httpd.shutdown()
    conn.close()
    monkeypatch.setattr(server, "DATABASE_URL", None)
    server._db_conn = None


def test_publish_endpoint_then_db_backed_reads(db_client):
    status, body = db_client("/publish", {"bundle": _bundle(),
                                          "evidence": _evidence()})
    assert status == 200 and body["ok"] is True
    # same command surface, new resource: /bundles now serves from the DB
    status, body = db_client("/bundles")
    assert status == 200
    names = [b["name"] for b in body["bundles"]]
    assert names == ["pdd-registry"]
    row = body["bundles"][0]
    assert row["address"] == "pdd/pdd-registry"
    status, body = db_client("/bundles?namespace=pdd&tag=engine")
    assert status == 200 and len(body["bundles"]) == 1
    status, body = db_client("/search?q=idempotent")
    assert status == 200  # searchable from the DB materialization


def test_publish_endpoint_rejects_bad_evidence(db_client):
    status, body = db_client("/publish", {"bundle": _bundle(),
                                          "evidence": _evidence(
                                              resource_identifier="nope")})
    assert status == 400
    assert body["error"]["kind"] == "invalid_request"


def test_publish_endpoint_idempotent(db_client):
    payload = {"bundle": _bundle(), "evidence": _evidence()}
    status, _ = db_client("/publish", payload)
    assert status == 200
    status, _ = db_client("/publish", payload)
    assert status == 200
    _, body = db_client("/bundles")
    assert len(body["bundles"]) == 1


def test_publish_unavailable_in_filesystem_mode():
    """Without PDD_DATABASE_URL the publish endpoint fails closed (S-002
    error envelope, kind=internal) — the filesystem path is author-side and
    never accepts writes over HTTP."""
    assert server.DATABASE_URL is None  # env not set in the test process
    httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/publish",
                                     data=json.dumps({"bundle": {}, "evidence": {}}).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req) as resp:
            status, body = resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        status, body = err.code, json.loads(err.read().decode())
    finally:
        httpd.shutdown()
    assert status == 500
    assert body["error"]["kind"] == "internal"
    assert "PDD_DATABASE_URL" in body["error"]["message"]


def test_cli_remote_commands_same_surface(db_client, capsys, monkeypatch):
    """The CLI runs the SAME commands against the pdd+http:// resource
    identifier (v1.2): `pdd search <q> --registry ...` and
    `pdd index --registry ...` hit the DB-backed registry server."""
    import importlib.util as _u

    status, _ = db_client("/publish", {"bundle": _bundle(),
                                       "evidence": _evidence()})
    assert status == 200
    spec = _u.spec_from_file_location("pdd_cli", ROOT / "scripts" / "pdd.py")
    pdd = _u.module_from_spec(spec)
    spec.loader.exec_module(pdd)
    registry = "pdd+" + db_client.base_url
    # search hits the tag entry (same command, new resource)
    rc = pdd.cmd_search(["--registry", registry, "engine"])
    out = capsys.readouterr().out
    assert rc == 0 and '"bundle": "pdd-registry"' in out and '"layer": "tags"' in out
    # index lists the DB-backed catalog with namespace/address
    rc = pdd.cmd_index(["--registry", registry])
    out = capsys.readouterr().out
    assert rc == 0 and '"namespace": "pdd"' in out and '"address"' in out
    # invalid resource identifier fails closed
    with pytest.raises(SystemExit):
        pdd.cmd_search(["--registry", "ftp://x", "engine"])


def test_db_mode_evidence_and_ledger_routes(db_client):
    """DB-backed /evidence/verify, /evidence/admission and
    /bundles/{name}/ledger must serve from the database (would have caught
    the psycopg dict-row KeyError on the ledger route)."""
    status, _ = db_client("/publish", {"bundle": _bundle(),
                                       "evidence": _evidence()})
    assert status == 200
    status, body = db_client("/evidence/admission")
    assert status == 200
    assert len(body["admissions"]) == 1
    adm = body["admissions"][0]
    assert adm["resource_identifier"].startswith("https://")
    assert adm["decision"] == "attest-pass"
    # ledger route in DB mode (ledger table empty until the deployment
    # records blocks): honest empty list, no KeyError on dict rows
    status, body = db_client("/bundles/pdd-registry/ledger")
    assert status == 200
    assert body["count"] == 0 and body["blocks"] == []
    # evidence/verify in DB mode: signature check over the stored object —
    # the stored signed_object is a stub here, so it must NOT report a fake
    # pass (honor-system honesty).
    status, body = db_client("/evidence/verify")
    assert status == 200
    rows = body["results"]
    assert len(rows) == 1
    assert rows[0]["resource_identifier"].startswith("https://")
    assert rows[0]["verified"] is False  # stub object, no fake pass


def test_db_mode_ledger_limit_semantics(db_client):
    """DB-mode ledger limit semantics match filesystem mode: None = all,
    limit>0 = last N, limit=0 = zero blocks (a -0 slice would return all)."""
    status, _ = db_client("/publish", {"bundle": _bundle(),
                                       "evidence": _evidence()})
    assert status == 200
    conn = server._db()
    for i in (1, 2, 3):
        conn.execute(
            "INSERT INTO ledger (bundle_ref, block, block_digest, seq) "
            "VALUES (?, ?, ?, ?)",
            ("pdd-registry", json.dumps({"i": i}), f"digest{i}", i))
    conn.commit()
    _, body = db_client("/bundles/pdd-registry/ledger")
    assert body["count"] == 3 and [b["i"] for b in body["blocks"]] == [1, 2, 3]
    _, body = db_client("/bundles/pdd-registry/ledger?limit=2")
    assert [b["i"] for b in body["blocks"]] == [2, 3]
    _, body = db_client("/bundles/pdd-registry/ledger?limit=0")
    assert body["count"] == 3 and body["blocks"] == []
