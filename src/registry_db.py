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
import json
import re
import sqlite3
import threading
from typing import Any, Optional

RESOURCE_ID_RE = re.compile(r"^(https?://|urn:)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")
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
  artifact_id         TEXT NOT NULL,
  resource_identifier TEXT NOT NULL,
  decision            TEXT NOT NULL,
  signed_object       TEXT NOT NULL,
  digest              TEXT NOT NULL,
  published_at        TEXT NOT NULL,
  PRIMARY KEY (namespace, name, version, artifact_id, digest)
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
    }


@_serialized
def list_catalog(conn) -> list[dict]:
    """All bundle records as catalog entries (registry_index shape), ordered
    by (namespace, name, version) — deterministic (S-006 consistent read)."""
    cur = _exec(conn,
        "SELECT * FROM bundles ORDER BY namespace, name, version")
    return [_entry_from_row(r) for r in cur.fetchall()]


@_serialized
def publish(conn, bundle: dict, evidence: dict) -> dict:
    """Idempotent publish (B-006). Validates shape + digests + S-007
    resource_identifier, inserts atomically; re-publishing the same
    (namespace, name, version, digest) is a no-op returning the record."""
    _validate_bundle(bundle)
    _validate_evidence(evidence)
    ns, name, version = bundle["namespace"], bundle["name"], bundle["version"]
    digest = bundle["digest"]
    now = bundle.get("published_at") or evidence.get("published_at") or ""
    # ON CONFLICT DO NOTHING: portable to sqlite3 (>=3.24) AND postgres
    # (INSERT OR IGNORE is sqlite-only). The PK IS the idempotency key
    # (namespace, name, version, digest) — B-006.
    _exec(conn,
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
    _exec(conn,
        "INSERT INTO evidence "
        "(namespace, name, version, artifact_id, resource_identifier, "
        " decision, signed_object, digest, published_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        (ns, name, version, evidence["artifact_id"],
         evidence["resource_identifier"], evidence["decision"],
         json.dumps(evidence.get("signed_object", {})),
         evidence.get("digest", ""), now))
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
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", bundle["namespace"]):
        raise ValueError("bundle.namespace must be kebab-case")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", bundle["version"]):
        raise ValueError("bundle.version must be semver x.y.z")
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


@_serialized
def evidence_records(conn, name: str, namespace: str | None = None) -> list[dict]:
    """Evidence records for one bundle. The PK includes namespace (S-004
    permits the same name in different namespaces) — filter by namespace
    when the caller has it, or rows from distinct namespaces mix."""
    if namespace is None:
        cur = _exec(conn,
            "SELECT * FROM evidence WHERE name = ? "
            "ORDER BY published_at, digest", (name,))
    else:
        cur = _exec(conn,
            "SELECT * FROM evidence WHERE name = ? AND namespace = ? "
            "ORDER BY published_at, digest", (name, namespace))
    return [dict(r) for r in cur.fetchall()]


@_serialized
def ledger_blocks(conn, name: str) -> list[dict]:
    cur = _exec(conn,
        "SELECT block FROM ledger WHERE bundle_ref = ? "
        "ORDER BY seq, block_digest",
        (name,))
    # r["block"] works for both sqlite3.Row and psycopg dict_row (r[0] would
    # KeyError on dict rows — psycopg rows are dicts, not tuples).
    return [json.loads(r["block"]) for r in cur.fetchall()]


@_serialized
def get_bundle(conn, name: str, namespace: str | None = None) -> Optional[dict]:
    """Newest version record of a bundle. ORDER BY on TEXT sorts lexically
    ('1.10.0' < '1.9.0'), so the semver max is computed in Python; the
    namespace filter keeps S-004's same-name-different-namespace rows apart."""
    if namespace is None:
        cur = _exec(conn, "SELECT * FROM bundles WHERE name = ?", (name,))
    else:
        cur = _exec(conn,
            "SELECT * FROM bundles WHERE name = ? AND namespace = ?",
            (name, namespace))
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
        return (0, 0, 0)  # unparseable versions sort last, never crash
