"""DB-backed storage adapter for the pdd registry (v1.2, S-006).

The catalog and evidence records live in a backing database (PostgreSQL in
production; SQLite for dev/tests — same portable SQL, both via parameterized
queries). The adapter materializes catalog entries in the SAME shape as
registry_index.load_bundle, so the server's existing search/filter/view code
runs unchanged on DB-backed data (parity with the filesystem path).

Contract notes:
- S-006: reads are transactionally consistent (single transaction per
  materialization); publish commits atomically.
- B-006: publish is idempotent by (namespace, name, version, digest) — the
  primary key; re-publishing the same record is a no-op, different digests
  create distinct version records, never a silent overwrite.
- S-007: every evidence record carries a resource_identifier (http(s) URL or
  URN) pointing at the author's validator-loop execution record; ingest
  rejects records without one.

Only stdlib (sqlite3, json, re) is required for the SQLite path; psycopg is
imported lazily for the PostgreSQL path so dev/test needs no extra deps.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
import sqlite3
import threading
import time
from typing import Any, Optional

# Total length capped at 2048 via the lookahead anchor — the {1,2048} on the
# post-prefix portion alone would accept up to 2056 chars (schema maxLength
# caps the FULL string; belt/schema parity).
RESOURCE_ID_RE = re.compile(r"^(?=.{1,2048}$)(https?://|urn:)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# The server shares ONE connection across request threads (ThreadingHTTPServer).
# psycopg3 raises "connection already in use" on concurrent ops and a shared
# sqlite3 connection interleaves transactions — serialize every public
# operation per process (the registry is low-QPS; one pod, one process).
_OP_LOCK = threading.RLock()


def _serialized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _OP_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _adapt_sql(sql: str, postgres: bool) -> str:
    """sqlite3 uses '?' placeholders; psycopg uses '%s'. The adapter keeps
    one portable SQL string (with '?') and adapts at execution time."""
    return sql.replace("?", "%s") if postgres else sql

SCHEMA = """
CREATE TABLE IF NOT EXISTS bundles (
  namespace      TEXT NOT NULL,
  name           TEXT NOT NULL,
  version        TEXT NOT NULL,
  status         TEXT NOT NULL,
  digest         TEXT NOT NULL,
  address        TEXT NOT NULL,
  purpose        TEXT NOT NULL,
  tags           TEXT NOT NULL DEFAULT '[]',
  depends_on     TEXT NOT NULL DEFAULT '[]',
  provides       TEXT NOT NULL DEFAULT '{}',
  invariants     TEXT NOT NULL DEFAULT '{}',
  capabilities   TEXT NOT NULL DEFAULT '{}',
  boundary       TEXT NOT NULL DEFAULT '{}',
  published_at   TEXT NOT NULL,
  PRIMARY KEY (namespace, name, version, digest)
);
CREATE TABLE IF NOT EXISTS evidence (
  namespace           TEXT NOT NULL,
  name                TEXT NOT NULL,
  version             TEXT NOT NULL,
  bundle_digest       TEXT NOT NULL,
  artifact_id         TEXT NOT NULL,
  resource_identifier TEXT NOT NULL,
  decision            TEXT NOT NULL,
  signed_object       TEXT NOT NULL,
  digest              TEXT NOT NULL,
  published_at        TEXT NOT NULL,
  -- bundle_digest in the PK: B-006 'never a silent overwrite' — a
  -- same-(namespace, name, version) re-publish with a DIFFERENT bundle
  -- digest must get its OWN evidence row (and never serve the old
  -- evidence that attests the old digest)
  PRIMARY KEY (namespace, name, version, bundle_digest, artifact_id, digest)
);
CREATE TABLE IF NOT EXISTS ledger (
  bundle_ref   TEXT NOT NULL,
  block        TEXT NOT NULL,
  block_digest TEXT PRIMARY KEY,
  -- monotonic append sequence, writer-set, no default: a forgotten seq
  -- fails loudly instead of silently losing insertion order
  seq          INTEGER NOT NULL
);
"""


def connect(url: str):
    """Open a connection for a database URL.

    - ``sqlite:///path`` or ``sqlite:///:memory:`` -> stdlib sqlite3
    - ``postgresql://user:pass@host:5432/db`` -> psycopg (lazy import)
    """
    if url.startswith("sqlite://"):
        # sqlite:///:memory: -> ":memory:"; sqlite:///path -> "/path"
        path = url[len("sqlite:///"):]
        # check_same_thread=False: the server/CLI use the connection from
        # request threads; access is serialized (commit-per-write), which is
        # the documented safe pattern for sqlite across threads. The
        # production dialect is PostgreSQL; sqlite is dev/test only.
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    if url.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg  # noqa: PLC0415
            from psycopg.rows import dict_row  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError("psycopg is required for the PostgreSQL path "
                               "(pip install psycopg[binary])") from exc
        conn = psycopg.connect(url, row_factory=dict_row)
        return conn
    raise ValueError(f"unsupported database URL scheme: {url.split(':')[0]!r}")


@_serialized
def init_schema(conn) -> None:
    # execute one statement at a time (portable across sqlite3/psycopg)
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)
    # COMMIT BEFORE the migration ALTER: psycopg runs every statement in ONE
    # transaction — on a fresh PostgreSQL database the duplicate-column
    # rollback below would otherwise discard the CREATE TABLEs just issued
    # (sqlite autocommits DDL, which is why tests need a txn-emulating conn
    # to catch this). The tables must persist before the ALTER's rollback
    # path can run.
    conn.commit()
    # v1.2 migration: the evidence table gained bundle_digest (B-006
    # never-silent-overwrite). Legacy tables get the column backfilled with
    # '' — legacy rows keep working (unscoped lookups include them) and new
    # publishes carry the real digest. The ALTER fails when the column
    # already exists (both dialects) — that is the fresh-table normal path.
    try:
        _exec(conn,
              "ALTER TABLE evidence ADD COLUMN "
              "bundle_digest TEXT NOT NULL DEFAULT ''")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "duplicate column" not in msg and "already exists" not in msg:
            raise  # a genuine ALTER failure must not be swallowed
        conn.rollback()  # duplicate column: already migrated (fresh-table path)
    conn.commit()


def _j(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _exec(conn, sql: str, params: tuple = ()):
    """Execute with the dialect's placeholder style (one portable SQL)."""
    postgres = not isinstance(conn, sqlite3.Connection)
    return conn.execute(_adapt_sql(sql, postgres), params)


def _entry_from_row(row) -> dict:
    """Materialize a bundles row in registry_index.load_bundle's shape."""
    from registry_index import LAYERS
    invariants = json.loads(row["invariants"])
    return {
        "name": row["name"],
        "version": row["version"],
        "status": row["status"],
        "namespace": row["namespace"],
        "tags": json.loads(row["tags"]),
        "address": row["address"],
        "purpose": row["purpose"],
        "boundary": json.loads(row["boundary"]),
        "depends_on": json.loads(row["depends_on"]),
        "provides": json.loads(row["provides"]),
        # views iterate every layer; published bundles may omit empty layers
        "invariants": {layer: invariants.get(layer, []) for layer in LAYERS},
        "capabilities": json.loads(row["capabilities"]),
        "digest": row["digest"],
    }


@_serialized
def list_catalog(conn) -> list[dict]:
    """All bundle records as catalog entries (registry_index shape), ordered
    by (namespace, name, version) — deterministic (S-006 consistent read)."""
    cur = _exec(conn,
        "SELECT * FROM bundles ORDER BY namespace, name, version")
    return [_entry_from_row(r) for r in cur.fetchall()]


def _block_digest(block: dict) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps({k: v for k, v in block.items() if k != "digest"},
                   sort_keys=True).encode()).hexdigest()


@_serialized
def _append_ledger_block(conn, namespace: str, name: str, version: str,
                         bundle_digest: str, artifact_id: str,
                         resource_id: str, evidence_digest: str,
                         now: str) -> None:
    """Append one hash-chained block to the registry-side ledger (S-006:
    the DB-mode /bundles/{name}/ledger route's producer). Chained by sha256
    (previous block digest + seq); the author-side evidence chain carries
    the HMAC signatures — this is the registry's append-only event log, not
    a signed chain. Called from publish() on ANY write event: the first
    publish of a (namespace, name, version, digest) record OR an evidence
    insert for an existing record (B-006: identical re-publishes insert
    nothing and append nothing). artifact_id + evidence_digest are part of
    the block so same-second writes cannot collide on the block_digest PK
    (security review LOW)."""
    prev = "sha256:" + "0" * 64
    seq = 1
    cur = _exec(conn, "SELECT block_digest, seq FROM ledger ORDER BY seq DESC LIMIT 1")
    row = cur.fetchone()
    if row:
        prev = row["block_digest"]
        seq = row["seq"] + 1
    block = {"previous": prev, "namespace": namespace, "name": name,
             "version": version, "bundle_digest": bundle_digest,
             "artifact_id": artifact_id,
             "resource_identifier": resource_id,
             "evidence_digest": evidence_digest, "published_at": now}
    block["digest"] = _block_digest(block)
    # bundle_ref is namespace-qualified: S-004 permits the same name in
    # different namespaces, and each (namespace, name) keeps its own block
    # run. seq is a GLOBAL monotonic counter across all bundle_refs (one
    # append-only log); previous-links hash-chain every block in order.
    _exec(conn,
          "INSERT INTO ledger (bundle_ref, block, block_digest, seq) "
          "VALUES (?, ?, ?, ?)",
          (f"{namespace}/{name}", json.dumps(block), block["digest"], seq))


@_serialized
def publish(conn, bundle: dict, evidence: dict) -> dict:
    """Idempotent publish (B-006). Validates shape + digests + S-007
    resource_identifier, inserts atomically; re-publishing the same
    (namespace, name, version, digest) is a no-op returning the record.

    Failure-safe: the server shares ONE connection — a mid-transaction
    failure must roll back, or psycopg fails every later request with
    InFailedSqlTransaction (sqlite stays in an open transaction)."""
    _validate_bundle(bundle)
    _validate_evidence(evidence)
    try:
        return _publish_unlocked(conn, bundle, evidence)
    except BaseException:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — the original error matters more
            pass
        raise


def _publish_unlocked(conn, bundle: dict, evidence: dict) -> dict:
    ns, name, version = bundle["namespace"], bundle["name"], bundle["version"]
    digest = bundle["digest"]
    # published_at is ALWAYS server-stamped: the strict publish schema
    # forbids client-supplied timestamps (additionalProperties: false), so
    # a client-side fallback would be dead code and diverge from the schema.
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # ON CONFLICT DO NOTHING: portable to sqlite3 (>=3.24) AND postgres
    # (INSERT OR IGNORE is sqlite-only). The PK IS the idempotency key
    # (namespace, name, version, digest) — B-006.
    cur = _exec(conn,
        "INSERT INTO bundles "
        "(namespace, name, version, status, digest, address, purpose, tags, "
        " depends_on, provides, invariants, capabilities, boundary, published_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        (ns, name, version, bundle["status"], digest,
         bundle.get("address") or f"{ns}/{name}",
         bundle.get("purpose", ""),
         _j(bundle.get("tags", [])), _j(bundle.get("depends_on", [])),
         _j(bundle.get("provides", {})), _j(bundle.get("invariants", {})),
         _j(bundle.get("capabilities", {})), _j(bundle.get("boundary", {})),
         now))
    first_publish = cur.rowcount > 0
    ev_cur = _exec(conn,
        "INSERT INTO evidence "
        "(namespace, name, version, bundle_digest, artifact_id, "
        " resource_identifier, decision, signed_object, digest, published_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        (ns, name, version, digest, evidence["artifact_id"],
         evidence["resource_identifier"], evidence["decision"],
         json.dumps(evidence.get("signed_object", {})),
         evidence.get("digest", ""), now))
    first_evidence = ev_cur.rowcount > 0
    if first_publish or first_evidence:
        # Registry-side ledger block per WRITE EVENT: first publish of a
        # bundle record, OR an evidence insert for an existing record (a
        # new artifact_id is a change B-006's no-op wording does not
        # cover). Identical re-publishes insert nothing and append nothing.
        _append_ledger_block(conn, ns, name, version, digest,
                             evidence["artifact_id"],
                             evidence["resource_identifier"],
                             evidence.get("digest", ""), now)
    conn.commit()
    cur = _exec(conn,
        "SELECT * FROM bundles WHERE namespace = ? AND name = ? "
        "AND version = ? AND digest = ?", (ns, name, version, digest))
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("publish did not persist (schema not initialized?)")
    return {"ok": True, "bundle": _entry_from_row(row), "error": None}


def _validate_bundle(bundle: dict) -> None:
    if not isinstance(bundle, dict):
        raise ValueError("bundle must be an object")
    for key in ("namespace", "name", "version", "status", "digest"):
        if not isinstance(bundle.get(key), str) or not bundle[key]:
            raise ValueError(f"bundle.{key} must be a non-empty string")
    if not isinstance(bundle.get("purpose"), str) or not bundle["purpose"]:
        raise ValueError("bundle.purpose must be a non-empty string "
                         "(publish.schema.json requires it; the adapter belt "
                         "enforces it without jsonschema)")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bundle["name"]):
        raise ValueError("bundle.name must match ^[A-Za-z0-9_-]+$")
    # JSON-object fields: the adapter belt must type-check independently of
    # the jsonschema layer (which may be absent in stripped environments).
    for key in ("provides", "invariants", "capabilities", "boundary"):
        if not isinstance(bundle.get(key), dict):
            raise ValueError(f"bundle.{key} must be an object")
    if not isinstance(bundle.get("depends_on"), list):
        raise ValueError("bundle.depends_on must be an array")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", bundle["namespace"]):
        raise ValueError("bundle.namespace must be kebab-case")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", bundle["version"]):
        raise ValueError("bundle.version must be semver x.y.z")
    if bundle["status"] not in ("sealed", "review", "draft", "deprecated"):
        raise ValueError("bundle.status must be one of "
                         "sealed/review/draft/deprecated")
    if not _SHA256_RE.fullmatch(bundle["digest"]):
        raise ValueError("bundle.digest must be sha256:<64 hex>")
    tags = bundle.get("tags") or []
    if (not isinstance(tags, list) or len(tags) > 8
            or len(set(tags)) != len(tags)
            or any(not isinstance(t, str)
                   or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", t)
                   for t in tags)):
        raise ValueError("bundle.tags must be a kebab-case list, <=8, no dupes")


def _validate_evidence(evidence: dict) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be an object")
    for key in ("artifact_id", "resource_identifier", "decision"):
        if not isinstance(evidence.get(key), str) or not evidence[key]:
            raise ValueError(f"evidence.{key} must be a non-empty string")
    if not RESOURCE_ID_RE.fullmatch(evidence["resource_identifier"]):
        raise ValueError("evidence.resource_identifier must be an http(s) "
                         "URL or urn: URN (S-007)")
    if evidence["decision"] != "attest-pass":
        raise ValueError("evidence.decision must be 'attest-pass' (the only "
                         "admission decision in this version)")
    if evidence.get("digest") and not _SHA256_RE.fullmatch(evidence["digest"]):
        raise ValueError("evidence.digest must be sha256:<64 hex> when present")
    if not isinstance(evidence.get("signed_object"), dict):
        raise ValueError("evidence.signed_object must be an object")


@_serialized
def evidence_records(conn, name: str, namespace: str | None = None,
                     bundle_digest: str | None = None) -> list[dict]:
    """Evidence records for one bundle. The PK includes namespace + bundle
    digest (S-004 same-name namespaces; B-006 never-silent-overwrite) —
    filter by both when the caller has them, or rows from distinct
    namespaces/versions mix."""
    if bundle_digest is not None and namespace is None:
        raise ValueError("bundle_digest scope requires a namespace "
                         "(digests are only unique within a namespace)")
    if namespace is None and bundle_digest is None:
        cur = _exec(conn,
            "SELECT * FROM evidence WHERE name = ? "
            "ORDER BY published_at, digest", (name,))
    elif bundle_digest is None:
        cur = _exec(conn,
            "SELECT * FROM evidence WHERE name = ? AND namespace = ? "
            "ORDER BY published_at, digest", (name, namespace))
    else:
        cur = _exec(conn,
            "SELECT * FROM evidence WHERE name = ? AND namespace = ? "
            "AND bundle_digest = ? ORDER BY published_at, digest",
            (name, namespace, bundle_digest))
    return [dict(r) for r in cur.fetchall()]


@_serialized
def ledger_blocks(conn, bundle_ref: str) -> list[dict]:
    """Blocks of one (namespace/name) chain (bundle_ref is
    namespace-qualified — S-004 keeps same-name chains apart)."""
    cur = _exec(conn,
        "SELECT block FROM ledger WHERE bundle_ref = ? "
        "ORDER BY seq, block_digest",
        (bundle_ref,))
    # r["block"] works for both sqlite3.Row and psycopg dict_row (r[0] would
    # KeyError on dict rows — psycopg rows are dicts, not tuples).
    return [json.loads(r["block"]) for r in cur.fetchall()]


@_serialized
def verify_ledger_chain(conn) -> dict:
    """Verify the registry-side ledger (S-009 append-only + tamper
    detection). The ledger is ONE global append-only log: `seq` is a global
    monotonic counter, each block's digest is the sha256 of its own fields
    (excluding 'digest'), and each block's `previous` must equal the digest
    of the preceding block (zero hash for the genesis block). Any modified,
    deleted, or reordered block anywhere breaks the chain and is reported
    at the first divergent seq. Returns {"ok", "blocks", "seq", "reason"}.
    """
    rows = _exec(conn, "SELECT block, block_digest, seq FROM ledger ORDER BY seq")
    blocks = [dict(r) for r in rows.fetchall()]
    expected_prev = "sha256:" + "0" * 64
    expected_seq = 1
    for b in blocks:
        if b["seq"] != expected_seq:
            return {"ok": False, "blocks": len(blocks), "seq": b["seq"],
                    "reason": "ledger seq is not contiguous (a block was "
                              "deleted or reordered)"}
        try:
            parsed = json.loads(b["block"])
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "blocks": len(blocks), "seq": b["seq"],
                    "reason": "block JSON is unparseable"}
        recomputed = _block_digest(parsed)
        if recomputed != b["block_digest"] or parsed.get("digest") != recomputed:
            return {"ok": False, "blocks": len(blocks), "seq": b["seq"],
                    "reason": "block content does not match the stored "
                              "block_digest (tampered)"}
        if parsed.get("previous") != expected_prev:
            return {"ok": False, "blocks": len(blocks), "seq": b["seq"],
                    "reason": "previous-link broken (reorder or deletion)"}
        expected_prev = recomputed
        expected_seq += 1
    return {"ok": True, "blocks": len(blocks), "seq": None, "reason": None}


@_serialized
def get_bundle(conn, name: str, namespace: str | None = None) -> Optional[dict]:
    """Newest version record of a bundle. ORDER BY on TEXT sorts lexically
    ('1.10.0' < '1.9.0'), so the semver max is computed in Python; the SQL
    ORDER BY version, digest only breaks TIES deterministically (two records
    with the same semver version and different digests — the max() picks
    the first, which the deterministic order fixes). The namespace filter
    keeps S-004's same-name-different-namespace rows apart."""
    if namespace is None:
        cur = _exec(conn,
            "SELECT * FROM bundles WHERE name = ? "
            "ORDER BY version, digest", (name,))
    else:
        cur = _exec(conn,
            "SELECT * FROM bundles WHERE name = ? AND namespace = ? "
            "ORDER BY version, digest", (name, namespace))
    rows = cur.fetchall()
    if not rows:
        return None
    best = max(rows, key=lambda r: _semver_key(r["version"]))
    return _entry_from_row(best)


def _semver_key(version: str) -> tuple[int, int, int]:
    parts = str(version).split(".")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return (0, 0, 0)  # unparseable versions sort first; unreachable in
                          # practice (_validate_bundle enforces semver)
