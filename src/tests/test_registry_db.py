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
    # the registry-side ledger gains exactly one block per first publish
    blocks = registry_db.ledger_blocks(conn, "pdd/pdd-registry")
    assert len(blocks) == 1
    assert blocks[0]["bundle_digest"] == "sha256:" + "a" * 64
    assert blocks[0]["previous"].startswith("sha256:")


def test_B006_publish_idempotent_by_digest(conn):
    """Same (namespace, name, version, digest) twice -> one record; a
    different digest for the same (namespace, name, version) is a distinct
    version record, never a silent overwrite."""
    registry_db.publish(conn, _bundle(), _evidence())
    again = registry_db.publish(conn, _bundle(), _evidence())
    assert again["ok"] is True
    assert len(registry_db.list_catalog(conn)) == 1  # no duplicate
    assert len(registry_db.ledger_blocks(conn, "pdd/pdd-registry")) == 1  # B-006: no-op
    # distinct digest -> distinct record (new version of the same bundle)
    registry_db.publish(conn, _bundle(digest="sha256:" + "b" * 64), _evidence())
    catalog = registry_db.list_catalog(conn)
    assert len(catalog) == 2
    digests = {b["version"] for b in catalog}
    assert digests == {"1.2.0"}
    assert len(registry_db.ledger_blocks(conn, "pdd/pdd-registry")) == 2  # new block


def test_S007_evidence_requires_resource_identifier(conn):
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(resource_identifier=""))
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(resource_identifier="not-a-url"))
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(resource_identifier="ftp://x"))
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(decision="attest-fail"))
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
        registry_db.publish(conn, _bundle(status="bogus"), _evidence())
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(purpose=""), _evidence())
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(depends_on="not-a-list"), _evidence())
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


def test_namespace_keeps_same_name_apart(conn):
    """S-004 permits the same name in different namespaces: evidence and
    bundle lookups must not mix rows across namespaces."""
    registry_db.publish(conn, _bundle(), _evidence(artifact_id="a"))
    registry_db.publish(conn, _bundle(namespace="other",
                                      digest="sha256:" + "d" * 64),
                        _evidence(artifact_id="b"))
    # namespace-scoped evidence lookup returns only that namespace's rows
    rows = registry_db.evidence_records(conn, "pdd-registry", "pdd")
    assert [r["artifact_id"] for r in rows] == ["a"]
    rows = registry_db.evidence_records(conn, "pdd-registry", "other")
    assert [r["artifact_id"] for r in rows] == ["b"]
    assert len(registry_db.evidence_records(conn, "pdd-registry")) == 2
    # get_bundle picks the newest version within the requested namespace
    b = registry_db.get_bundle(conn, "pdd-registry", "pdd")
    assert b["namespace"] == "pdd"
    b = registry_db.get_bundle(conn, "pdd-registry", "other")
    assert b["namespace"] == "other"


def test_get_bundle_semver_ordering(conn):
    """'1.10.0' must sort AFTER '1.9.0' (lexical TEXT order would invert)."""
    for v, d in (("1.9.0", "e" * 64), ("1.10.0", "f" * 64), ("1.2.0", "a" * 64)):
        registry_db.publish(conn, _bundle(version=v, digest="sha256:" + d),
                            _evidence())
    assert registry_db.get_bundle(conn, "pdd-registry")["version"] == "1.10.0"


def test_all_public_ops_serialized():
    """The module contract: every public DB op is @_serialized (the server
    shares ONE connection across threads — a concurrent publish must never
    race the ledger seq or hit psycopg's 'connection already in use')."""
    import inspect
    for fn_name in ("init_schema", "list_catalog", "publish",
                    "evidence_records", "ledger_blocks", "get_bundle",
                    "verify_ledger_chain"):
        fn = getattr(registry_db, fn_name)
        assert getattr(fn, "__wrapped__", None) is not None, fn_name
    # helpers that must NOT take the lock (stateless) or are internal
    assert getattr(registry_db._block_digest, "__wrapped__", None) is None
    assert getattr(registry_db._append_ledger_block, "__wrapped__", None) is not None


def test_publish_rolls_back_on_failure(conn, monkeypatch):
    """A mid-transaction publish failure must roll back: the server shares
    ONE connection — an aborted transaction would fail every later request
    (psycopg InFailedSqlTransaction / sqlite open transaction)."""
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("forced ledger failure")
    monkeypatch.setattr(registry_db, "_append_ledger_block", boom)
    with pytest.raises(RuntimeError):
        registry_db.publish(conn, _bundle(), _evidence())
    # the shared connection is still usable and nothing was persisted
    assert registry_db.list_catalog(conn) == []
    assert registry_db.publish(conn, _bundle(), _evidence())["ok"] is True
    assert len(registry_db.list_catalog(conn)) == 1


def test_evidence_attributed_per_bundle_digest(conn):
    """B-006 never-silent-overwrite: a same-(namespace, name, version)
    re-publish with a DIFFERENT bundle digest must get its OWN evidence row
    — the new record must never serve the old evidence (which attests the
    old digest)."""
    registry_db.publish(conn, _bundle(digest="sha256:" + "a" * 64),
                        _evidence(artifact_id="e1"))
    registry_db.publish(conn, _bundle(digest="sha256:" + "b" * 64),
                        _evidence(artifact_id="e2"))
    rows = registry_db.evidence_records(conn, "pdd-registry", "pdd")
    assert len(rows) == 2  # both evidence rows present, none dropped
    by_digest = {r["bundle_digest"]: r["artifact_id"] for r in rows}
    assert by_digest == {"sha256:" + "a" * 64: "e1",
                         "sha256:" + "b" * 64: "e2"}
    # scope by bundle digest returns only that record's evidence
    scoped = registry_db.evidence_records(
        conn, "pdd-registry", "pdd", "sha256:" + "a" * 64)
    assert [r["artifact_id"] for r in scoped] == ["e1"]


def test_get_bundle_tie_breaks_deterministically(conn):
    """Two records with the same semver version (different digests) must
    resolve deterministically — the SQL ORDER BY version, digest fixes the
    cursor order the Python max() depends on."""
    registry_db.publish(conn, _bundle(version="1.2.0",
                                      digest="sha256:" + "a" * 64), _evidence())
    registry_db.publish(conn, _bundle(version="1.2.0",
                                      digest="sha256:" + "b" * 64), _evidence())
    first = registry_db.get_bundle(conn, "pdd-registry")["digest"]
    for _ in range(5):
        assert registry_db.get_bundle(conn, "pdd-registry")["digest"] == first


def test_publish_server_stamps_published_at(conn):
    """published_at is server-stamped when neither side carries one (an
    empty timestamp would make ordering/auditing meaningless)."""
    registry_db.publish(conn, _bundle(), _evidence())
    ev = registry_db.evidence_records(conn, "pdd-registry", "pdd")
    assert ev[0]["published_at"] != ""
    assert ev[0]["published_at"].endswith("Z")


def test_evidence_digest_scope_requires_namespace(conn):
    """Digests are only unique within a namespace: a bundle_digest scope
    without a namespace is a ValueError, not a silent empty result."""
    with pytest.raises(ValueError):
        registry_db.evidence_records(conn, "pdd-registry", bundle_digest="sha256:x")


def test_init_schema_migrates_legacy_evidence_table():
    """A legacy evidence table (pre-bundle_digest) is migrated in place:
    the column is backfilled with '' and old rows stay readable."""
    conn = registry_db.connect("sqlite:///:memory:")
    conn.execute("CREATE TABLE evidence ("
                 "namespace TEXT NOT NULL, name TEXT NOT NULL, "
                 "version TEXT NOT NULL, artifact_id TEXT NOT NULL, "
                 "resource_identifier TEXT NOT NULL, decision TEXT NOT NULL, "
                 "signed_object TEXT NOT NULL, digest TEXT NOT NULL, "
                 "published_at TEXT NOT NULL)")
    conn.execute("INSERT INTO evidence (namespace, name, version, artifact_id, "
                 "resource_identifier, decision, signed_object, digest, "
                 "published_at) VALUES ('pdd', 'pdd-registry', '1.0.0', 'a', "
                 "'https://ci.example/runs/1', 'attest-pass', '{}', '', '')")
    conn.commit()
    registry_db.init_schema(conn)
    rows = registry_db.evidence_records(conn, "pdd-registry")
    assert len(rows) == 1  # legacy row migrated and readable
    assert rows[0]["bundle_digest"] == ""  # backfilled
    conn.close()


def test_init_schema_re_raises_genuine_alter_failures(conn, monkeypatch):
    """Only duplicate-column errors are swallowed by the migration; a
    genuine ALTER failure (lock, disk) must re-raise, not rollback-silently."""
    real_exec = registry_db._exec

    def fake_exec(c, sql, params=()):
        if sql.startswith("ALTER TABLE"):
            raise RuntimeError("simulated disk failure")
        return real_exec(c, sql, params)
    monkeypatch.setattr(registry_db, "_exec", fake_exec)
    with pytest.raises(RuntimeError):
        registry_db.init_schema(conn)


def test_init_schema_commits_before_alter():
    """Fresh-DB emulation of psycopg transaction semantics (one transaction
    per connection: DDL is NOT autocommitted after an explicit BEGIN): the
    SCHEMA CREATEs must persist before the duplicate-column ALTER rollback —
    otherwise a fresh PostgreSQL database ends with zero tables and every
    route 500s. With sqlite's DDL autocommit this bug is invisible, which is
    why the txn is held open here."""
    real = registry_db.connect("sqlite:///:memory:")
    real.execute("BEGIN")  # hold one transaction: DDL participates in it
    registry_db.init_schema(real)
    # the tables survived the ALTER's rollback path and are usable
    registry_db.publish(real, _bundle(), _evidence())
    assert len(registry_db.list_catalog(real)) == 1
    real.close()


def test_evidence_routes_dedupe_includes_digest(db_client):
    """Security-review MEDIUM: two bundle records with the same
    (name, namespace, version) but different digests must BOTH be verified
    — the dedupe key includes the digest."""
    for d, art in (("a" * 64, "e1"), ("b" * 64, "e2")):
        status, _ = db_client("/publish", {"bundle": _bundle(digest="sha256:" + d),
                                           "evidence": _evidence(artifact_id=art)})
        assert status == 200
    _, body = db_client("/evidence/verify")
    assert len(body["results"]) == 2  # both records, both digests
    _, body = db_client("/evidence/admission")
    assert len(body["admissions"]) == 2


def test_cli_strict_opener_rejects_redirects_and_file_urls():
    """Security-review LOW: the registry fetcher never follows redirects
    and has no file:// handler (a malicious registry redirect must not leak
    the Authorization header or read local files)."""
    import importlib.util as _u
    import urllib.error

    spec = _u.spec_from_file_location("pdd_cli", ROOT / "scripts" / "pdd.py")
    pdd = _u.module_from_spec(spec)
    spec.loader.exec_module(pdd)
    opener = pdd._strict_opener()
    # file:// is not handled at all
    with pytest.raises(urllib.error.URLError):
        opener.open("file:///etc/passwd")
    # a redirect is surfaced as an HTTPError (302), never followed
    import threading
    import urllib.request
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Redirector(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            opener.open(f"http://127.0.0.1:{httpd.server_address[1]}/x")
        assert exc.value.code == 302
        # the CLI's fetch path surfaces the redirect with the explanatory
        # note (pinned: a message regression would fail here)
        with pytest.raises(SystemExit) as exc2:
            pdd._registry_get(
                f"pdd+http://127.0.0.1:{httpd.server_address[1]}", "/x")
        assert "redirects are disabled" in str(exc2.value)
    finally:
        httpd.shutdown()


def test_evidence_only_republish_appends_ledger_block(conn):
    """A re-publish inserting a NEW evidence row (different artifact_id for
    the same bundle record) is a write event — the ledger records it;
    identical re-publishes stay no-ops (B-006)."""
    registry_db.publish(conn, _bundle(), _evidence(artifact_id="e1"))
    assert len(registry_db.ledger_blocks(conn, "pdd/pdd-registry")) == 1
    registry_db.publish(conn, _bundle(), _evidence(artifact_id="e2"))
    assert len(registry_db.ledger_blocks(conn, "pdd/pdd-registry")) == 2
    registry_db.publish(conn, _bundle(), _evidence(artifact_id="e2"))
    assert len(registry_db.ledger_blocks(conn, "pdd/pdd-registry")) == 2  # no-op


# --- S-009: registry-side ledger durability / append-only -------------------


def test_S009_ledger_chain_verifies_after_publish(conn):
    """S-009 positive: after publishes the registry-side ledger chain
    verifies — every block's digest matches its content and the
    previous-links form one unbroken chain."""
    registry_db.publish(conn, _bundle(version="1.2.0",
                                      digest="sha256:" + "a" * 64), _evidence())
    registry_db.publish(conn, _bundle(version="1.3.0",
                                      digest="sha256:" + "b" * 64), _evidence())
    res = registry_db.verify_ledger_chain(conn)
    assert res["ok"] is True and res["blocks"] == 2


def test_S009_ledger_tamper_modify_detected(conn):
    """S-009: rewriting a stored block's content via raw SQL is detected —
    the recomputed digest no longer matches the stored block_digest."""
    registry_db.publish(conn, _bundle(), _evidence())
    blocks = registry_db.ledger_blocks(conn, "pdd/pdd-registry")
    tampered = dict(blocks[0])
    tampered["bundle_digest"] = "sha256:" + "f" * 64
    conn.execute("UPDATE ledger SET block = ? WHERE seq = ?",
                 (json.dumps(tampered), 1))
    conn.commit()
    res = registry_db.verify_ledger_chain(conn)
    assert res["ok"] is False
    assert res["seq"] == 1
    assert "block_digest" in res["reason"]


def test_S009_ledger_tamper_delete_detected(conn):
    """S-009: deleting a stored block breaks the chain — the seq sequence
    is no longer contiguous and the next block's previous-link dangles.
    (Tail-truncation — deleting the LAST block — is outside a hash chain's
    detection envelope: without an external anchor the remaining chain
    still verifies. The adapter's append-only surface, which exposes no
    delete path at all, is the enforcement for that.)"""
    for d in ("a" * 64, "b" * 64, "c" * 64):
        registry_db.publish(conn, _bundle(digest="sha256:" + d), _evidence())
    assert registry_db.verify_ledger_chain(conn)["ok"] is True
    conn.execute("DELETE FROM ledger WHERE seq = 2")
    conn.commit()
    res = registry_db.verify_ledger_chain(conn)
    assert res["ok"] is False
    assert res["seq"] == 3
    assert "contiguous" in res["reason"]


def test_S009_ledger_tamper_reorder_detected(conn):
    """S-009: reordering stored blocks (seq swap) breaks the chain — the
    block now first no longer links to the zero genesis hash."""
    registry_db.publish(conn, _bundle(), _evidence())
    registry_db.publish(conn, _bundle(digest="sha256:" + "b" * 64), _evidence())
    conn.execute("UPDATE ledger SET seq = 99 WHERE seq = 2")
    conn.execute("UPDATE ledger SET seq = 2 WHERE seq = 1")
    conn.execute("UPDATE ledger SET seq = 1 WHERE seq = 99")
    conn.commit()
    assert registry_db.verify_ledger_chain(conn)["ok"] is False


def test_S009_adapter_ledger_surface_is_append_only(conn):
    """S-009 honesty: the adapter's ledger surface is append-only — no
    public function updates, deletes, or removes stored blocks; the only
    ledger write is the internal append."""
    public = {n for n in dir(registry_db) if not n.startswith("_")}
    writes = {n for n in public
              if any(k in n.lower() for k in ("update", "delete", "remove"))}
    assert writes == set()
    assert callable(registry_db._append_ledger_block)
    # an empty ledger is trivially consistent (verify never fails-closed
    # on "no blocks")
    assert registry_db.verify_ledger_chain(conn)["ok"] is True


# --- S-010: version-event preservation (lossless migration) -----------------


def test_S010_version_event_preserves_prior_versions(conn):
    """S-010 positive: publishing a NEW VERSION for an existing
    (namespace, name) preserves the prior version's catalog record, its
    evidence rows, and its ledger blocks — both stay queryable and the
    chain still verifies (lossless migration)."""
    registry_db.publish(conn, _bundle(version="1.2.0",
                                      digest="sha256:" + "a" * 64),
                        _evidence(artifact_id="e1"))
    registry_db.publish(conn, _bundle(version="1.3.0",
                                      digest="sha256:" + "b" * 64),
                        _evidence(artifact_id="e2"))
    # both catalog records queryable via list_catalog
    catalog = registry_db.list_catalog(conn)
    assert {(b["version"], b["digest"]) for b in catalog} == {
        ("1.2.0", "sha256:" + "a" * 64), ("1.3.0", "sha256:" + "b" * 64)}
    # get_bundle resolves the newest; the old record is still in the table
    newest = registry_db.get_bundle(conn, "pdd-registry")
    assert newest["version"] == "1.3.0" and newest["digest"] == "sha256:" + "b" * 64
    # both evidence rows exist, each attributed to its own bundle digest
    ev = registry_db.evidence_records(conn, "pdd-registry", "pdd")
    assert {(r["version"], r["bundle_digest"], r["artifact_id"]) for r in ev} == {
        ("1.2.0", "sha256:" + "a" * 64, "e1"),
        ("1.3.0", "sha256:" + "b" * 64, "e2")}
    # ledger has blocks for BOTH versions; the full chain still verifies
    blocks = registry_db.ledger_blocks(conn, "pdd/pdd-registry")
    assert [b["version"] for b in blocks] == ["1.2.0", "1.3.0"]
    assert registry_db.verify_ledger_chain(conn)["ok"] is True
    # row-level: nothing overwritten or deleted
    rows = conn.execute("SELECT version, digest FROM bundles ORDER BY version")
    assert [tuple(r) for r in rows.fetchall()] == [
        ("1.2.0", "sha256:" + "a" * 64), ("1.3.0", "sha256:" + "b" * 64)]


def test_S010_different_digest_same_version_preserves_prior(conn):
    """S-010: a re-publish of the same version with a DIFFERENT bundle
    digest (the B-006 never-silent-overwrite case) also preserves the prior
    record, its evidence row, and its ledger block."""
    registry_db.publish(conn, _bundle(digest="sha256:" + "a" * 64),
                        _evidence(artifact_id="e1"))
    registry_db.publish(conn, _bundle(digest="sha256:" + "b" * 64),
                        _evidence(artifact_id="e2"))
    assert len(registry_db.list_catalog(conn)) == 2
    assert len(registry_db.evidence_records(conn, "pdd-registry", "pdd")) == 2
    blocks = registry_db.ledger_blocks(conn, "pdd/pdd-registry")
    assert [b["bundle_digest"] for b in blocks] == [
        "sha256:" + "a" * 64, "sha256:" + "b" * 64]
    assert registry_db.verify_ledger_chain(conn)["ok"] is True


def test_resource_identifier_length_cap(conn):
    """The belt mirrors the schema's 2048-char maxLength on the FULL string
    (lookahead-anchored — a 2056-char http id must be rejected too)."""
    with pytest.raises(ValueError):
        registry_db.publish(conn, _bundle(), _evidence(
            resource_identifier="https://x/" + "a" * 2048))  # 2056 total
    # exactly 2048 total is accepted
    registry_db.publish(conn, _bundle(), _evidence(
        resource_identifier="https://x/" + "a" * 2038))  # 2048 total
    assert len(registry_db.list_catalog(conn)) == 1


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
    monkeypatch.setenv("PDD_PUBLISH_TOKEN", "test-token")  # publish authn
    server._db_conn = conn
    httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def request(path, payload=None, token="test-token"):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if token is not None and payload is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(base + path, data=data, headers=headers,
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


def test_publish_requires_bearer_token(db_client):
    """Publish authn (security review HIGH): without a valid bearer token the
    registry rejects the write — no fake catalog rows from the tailnet."""
    payload = {"bundle": _bundle(), "evidence": _evidence()}
    status, body = db_client("/publish", payload, token=None)
    assert status == 401
    assert body["error"]["kind"] == "invalid_request"
    status, body = db_client("/publish", payload, token="wrong-token")
    assert status == 401
    # non-ASCII token must 401, never 500 (compare_digest TypeError guard)
    status, body = db_client("/publish", payload, token="tökén")
    assert status == 401
    # nothing was written
    _, body = db_client("/bundles")
    assert body["bundles"] == []
    # the valid token still works
    status, _ = db_client("/publish", payload)
    assert status == 200


def test_publish_body_cap_rejects_nonpositive_length(db_client):
    """Content-Length must be within (0, 8 MiB] — a 0/-1 length must not
    reach the reader (security review MEDIUM: rfile.read(-1) reads to EOF)."""
    import http.client as _hc
    from urllib.parse import urlparse

    u = urlparse(db_client.base_url)
    conn = _hc.HTTPConnection(u.hostname, u.port, timeout=5)
    conn.request("POST", "/publish", body=b"{}",
                 headers={"Authorization": "Bearer test-token",
                          "Content-Type": "application/json",
                          "Content-Length": "0"})
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 400
    conn.close()


def test_publish_endpoint_idempotent(db_client):
    payload = {"bundle": _bundle(), "evidence": _evidence()}
    status, _ = db_client("/publish", payload)
    assert status == 200
    status, _ = db_client("/publish", payload)
    assert status == 200
    _, body = db_client("/bundles")
    assert len(body["bundles"]) == 1


def test_db_mode_bundle_route_semver_max(db_client):
    """DB-mode /bundles/{name} serves the semver-max record (1.10.0 over
    1.9.0 — lexical TEXT order would invert)."""
    for v, d in (("1.9.0", "e" * 64), ("1.10.0", "f" * 64)):
        status, _ = db_client("/publish", {"bundle": _bundle(version=v,
                                                              digest="sha256:" + d),
                                           "evidence": _evidence()})
        assert status == 200
    status, body = db_client("/bundles/pdd-registry")
    assert status == 200
    assert body["version"] == "1.10.0"


def test_db_mode_evidence_routes_dedupe_versions(db_client):
    """DB-mode /evidence/verify + /evidence/admission list one row per
    bundle RECORD (name, namespace, version, digest) — every published
    record's own evidence, no duplicates."""
    for v, d in (("1.2.0", "a" * 64), ("1.3.0", "b" * 64)):
        status, _ = db_client("/publish", {"bundle": _bundle(version=v,
                                                              digest="sha256:" + d),
                                           "evidence": _evidence()})
        assert status == 200
    _, body = db_client("/evidence/admission")
    assert len(body["admissions"]) == 2  # two version records
    _, body = db_client("/evidence/verify")
    assert len(body["results"]) == 2


def test_publish_body_must_be_object(db_client):
    """A non-dict JSON body (e.g. []) is a 400 invalid_request — never a
    jsonschema-dependent 500 (the isinstance guard runs before any schema
    work)."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(db_client.base_url + "/publish",
                                 data=b"[]",
                                 headers={"Authorization": "Bearer test-token",
                                          "Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            status, body = resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        status, body = err.code, json.loads(err.read().decode())
    assert status == 400
    assert body["error"]["kind"] == "invalid_request"


def test_publish_token_checked_before_body_read(db_client):
    """Wrong token + oversized Content-Length must 401 WITHOUT reading the
    body (thread-starve hardening): the header check runs first, so a client
    that never sends its claimed body cannot pin a handler."""
    import http.client as _hc
    from urllib.parse import urlparse

    u = urlparse(db_client.base_url)
    conn = _hc.HTTPConnection(u.hostname, u.port, timeout=5)
    conn.putrequest("POST", "/publish")
    conn.putheader("Authorization", "Bearer wrong-token")
    conn.putheader("Content-Length", "999999999")  # claim a huge body...
    conn.endheaders()  # ...but send NO body at all
    resp = conn.getresponse()
    resp.read()
    assert resp.status == 401
    conn.close()


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
    # evidence verify against the registry (S-007 honor-system surface)
    rc = pdd.cmd_evidence_verify(["--registry", registry])
    out = capsys.readouterr().out
    assert rc == 1  # the seeded stub object is not signed -> not verified
    assert '"results"' in out and '"verified": false' in out
    # invalid resource identifier fails closed
    with pytest.raises(SystemExit):
        pdd.cmd_search(["--registry", "ftp://x", "engine"])


def test_cli_publish_uses_validation_resource_fallback(db_client, capsys, monkeypatch, tmp_path):
    """`pdd publish` accepts an admission evidence object whose
    resource_identifier comes from provenance.validation_resource (S-007)
    — the shape push.sh seeds the DB with."""
    import importlib.util as _u

    spec = _u.spec_from_file_location("pdd_cli", ROOT / "scripts" / "pdd.py")
    pdd = _u.module_from_spec(spec)
    spec.loader.exec_module(pdd)
    monkeypatch.setattr(pdd, "REPO_ROOT", ROOT)
    monkeypatch.setattr(pdd, "EVIDENCE", ROOT / "evidence")
    monkeypatch.setattr(pdd, "BUNDLES", ROOT / "pdd-bundles")
    # an admission-shaped evidence file: no top-level resource_identifier,
    # only provenance.validation_resource (as the signed admission objects)
    ev_file = tmp_path / "evidence.json"
    ev_file.write_text(json.dumps({
        "artifact_id": "user-registry-python-stdlib",
        "decision": "attest-pass",
        "provenance": {"validation_resource": "https://ci.example/runs/7"},
        "digest": "sha256:" + "c" * 64,
    }))
    rc = pdd.cmd_publish([str(ROOT / "pdd-bundles" / "user-registry"),
                          "--evidence", str(ev_file),
                          "--registry", "pdd+" + db_client.base_url])
    out = capsys.readouterr().out
    assert rc == 0 and '"ok": true' in out
    status, body = db_client("/bundles?namespace=user")
    assert status == 200
    assert [b["name"] for b in body["bundles"]] == ["user-registry"]


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
    # ledger route in DB mode: publish() appends one registry-side block
    # per first publish (hash-chained, S-006) — no KeyError on dict rows
    status, body = db_client("/bundles/pdd-registry/ledger")
    assert status == 200
    assert body["count"] == 1 and len(body["blocks"]) == 1
    assert body["blocks"][0]["name"] == "pdd-registry"
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
    assert status == 200  # publish appends ledger block seq 1
    conn = server._db()
    for i in (2, 3, 4):
        conn.execute(
            "INSERT INTO ledger (bundle_ref, block, block_digest, seq) "
            "VALUES (?, ?, ?, ?)",
            ("pdd/pdd-registry", json.dumps({"i": i}), f"digest{i}", i))
    conn.commit()
    _, body = db_client("/bundles/pdd-registry/ledger")
    assert body["count"] == 4
    assert [b["i"] for b in body["blocks"][1:]] == [2, 3, 4]  # direct rows after the publish block
    _, body = db_client("/bundles/pdd-registry/ledger?limit=2")
    assert [b["i"] for b in body["blocks"]] == [3, 4]
    _, body = db_client("/bundles/pdd-registry/ledger?limit=0")
    assert body["count"] == 4 and body["blocks"] == []


def test_S009_http_publish_tamper_detected(db_client):
    """HTTP-level S-009: a /publish lands ledger blocks in the DB; a
    raw-SQL tamper of a stored block is detected by the chain verification
    over the server's shared connection."""
    status, _ = db_client("/publish", {"bundle": _bundle(),
                                       "evidence": _evidence()})
    assert status == 200
    conn = server._db()
    assert registry_db.verify_ledger_chain(conn)["ok"] is True
    blocks = registry_db.ledger_blocks(conn, "pdd/pdd-registry")
    tampered = dict(blocks[0])
    tampered["bundle_digest"] = "sha256:" + "f" * 64
    conn.execute("UPDATE ledger SET block = ? WHERE seq = ?",
                 (json.dumps(tampered), 1))
    conn.commit()
    assert registry_db.verify_ledger_chain(conn)["ok"] is False


def test_S010_http_version_event_preserves_prior_versions(db_client):
    """HTTP-level S-010: publishing a new version over /publish keeps the
    old catalog record, its evidence, and its ledger blocks live — /bundles
    lists both, the ledger holds both blocks, /evidence/admission serves
    both records, and the chain still verifies."""
    for v, d, art in (("1.2.0", "a" * 64, "e1"), ("1.3.0", "b" * 64, "e2")):
        status, _ = db_client("/publish", {"bundle": _bundle(version=v,
                                                              digest="sha256:" + d),
                                           "evidence": _evidence(artifact_id=art)})
        assert status == 200
    _, body = db_client("/bundles")
    assert [b["version"] for b in body["bundles"]] == ["1.2.0", "1.3.0"]
    _, body = db_client("/bundles/pdd-registry/ledger")
    assert body["count"] == 2
    assert [b["version"] for b in body["blocks"]] == ["1.2.0", "1.3.0"]
    _, body = db_client("/evidence/admission")
    assert len(body["admissions"]) == 2
    assert registry_db.verify_ledger_chain(server._db())["ok"] is True


def test_receipt_observation_valid_and_malformed(db_client):
    """Validator-attestation observation (taxonomy/validator-receipt):
    an author receipt inside signed_object is parsed and reported; a
    malformed receipt is an observation, not a verification failure; no
    receipt -> receipt: null (S-007 additive, honor system intact)."""
    import json as _json
    good = {"validator_receipt": {
        "provider": "github-actions-run", "repository": "a/b", "run_id": 7,
        "workflow": "pdd-validator-loop", "conclusion": "success",
        "started_at": "2026-08-10T00:00:00Z",
        "artifacts": [{"name": "r", "digest": "sha256:" + "a" * 64}]}}
    bad = {"validator_receipt": {"provider": "gitlab-ci"}}
    registry_db.publish(server._db(), _bundle(), _evidence(
        artifact_id="receipt-good", digest="sha256:" + "1" * 64,
        signed_object=good))
    registry_db.publish(server._db(), _bundle(), _evidence(
        artifact_id="receipt-bad", digest="sha256:" + "2" * 64,
        signed_object=bad))
    registry_db.publish(server._db(), _bundle(), _evidence(
        artifact_id="receipt-none", digest="sha256:" + "3" * 64,
        signed_object={"ok": True}))
    status, body = db_client("/evidence/verify?bundle=pdd-registry")
    assert status == 200
    by_artifact = {r["artifact_id"]: r for r in body["results"]}
    assert by_artifact["receipt-good"]["receipt"]["valid"] is True
    assert by_artifact["receipt-bad"]["receipt"]["valid"] is False
    assert by_artifact["receipt-bad"]["receipt"]["errors"]
    assert by_artifact["receipt-none"]["receipt"] is None
