"""pdd service — stdlib HTTP service exposing the pdd-registry registry state.

Client surface (see docs/service-features-v1.md and -v2.md):
  GET /healthz                    liveness
  GET /bundles                    registry index: [{name, version, status}] (v1)
  GET /bundles?status=&depends_on=  filtered index (v2)
  GET /bundles/{name}             full bundle summary (v2)
  GET /bundles/{name}/invariants?severity=  structured S/B/O invariant view (v2)
  GET /bundles/{name}/capabilities         capability manifest view (v2)
  GET /bundles/{name}/ledger?limit=N       ledger blocks view (v2)
  GET /search?q=                  ranked catalog search (v2)
  GET /evidence/verify            per-bundle ledger + evidence verification
  GET /evidence/admission         admitted artifact digests + decisions

Runs with cwd=/opt/pdd in the container; PDD_EVIDENCE_KEY must be present for
evidence verification (fail-closed: no key -> explicit ok:false, never a fake pass).
"""

import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Evidence primitives come from the pdd-cli package (single source; the old
# .reasonix skill scripts were folded into it, byte-compatible).
from pdd import evidence as pdd_evidence  # noqa: E402

PORT = int(os.environ.get("PORT", "8080"))
ROOT = Path(os.environ.get("PDD_ROOT", "/opt/pdd"))
BUNDLES = ROOT / "pdd-bundles"
EVIDENCE = ROOT / "evidence"
SKILLS = ROOT / ".reasonix" / "skills"
PDD = "pdd"
# Server-owned published-bundle store (catalog merge source; git stays the
# author-side distribution layer). PUBLISHED/<name>/ holds the latest version;
# PUBLISHED/<name>/<version>/ keeps immutable version snapshots.
PUBLISHED = ROOT / "published"
# SQLite metadata: publish idempotency + submission history (stdlib sqlite3).
DB_PATH = ROOT / "pdd.db"
PUBLISH_TOKEN_ENV = "PDD_PUBLISH_TOKEN"
# Registry-owned namespaces require HMAC-signed evidence (cannot be squatted
# by a token holder with a stub object).
REGISTRY_OWNED_NAMESPACES = ("pdd", "user", "taxonomy")
_BUNDLE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+$")
# Serializes per-name evidence/bundle writes so concurrent publishes cannot
# race the ledger chain-link or file layout.
_publish_lock = threading.Lock()

# registry_index.py lives in this same directory (src/); make it importable no
# matter how the module is loaded (python3 src/server.py, pytest, kubectl exec).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import registry_index  # noqa: E402  (shared catalog/search with scripts/pdd.py)

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def _load_protocol(path: Path) -> dict:
    if yaml is not None:
        data = yaml.safe_load(path.read_text())
        return data.get("protocol", {})
    # naive fallback: name/version/status from the first lines
    out = {}
    for line in path.read_text().splitlines():
        for key in ("name", "version", "status"):
            if line.strip().startswith(key + ":"):
                out[key] = line.split(":", 1)[1].strip()
    return out


def _catalog() -> list[dict]:
    """Shared registry index catalog; degrades to the v1 naive parse if pyyaml
    is missing (basic /bundles still works), else raises for the v2 views that
    genuinely need structured YAML (fail-closed: explicit error, no fake data)."""
    catalogs = []
    if yaml is not None:
        catalogs.append(registry_index.load_catalog(BUNDLES))
        if PUBLISHED.is_dir():
            catalogs.append(registry_index.load_catalog(PUBLISHED))
    else:
        for base in (BUNDLES, PUBLISHED):
            if not base.is_dir():
                continue
            catalogs.append([{"name": p.get("name"), "version": p.get("version"), "status": p.get("status"),
                              "depends_on": [], "provides": {}}
                             for p in (_load_protocol(f) for f in sorted(base.glob("*/protocol.yaml")))])
    return _merge_catalogs(catalogs)


def _catalog_strict() -> list[dict]:
    """v2 views require structured YAML — raise a clear error when absent."""
    if yaml is None:
        raise RuntimeError("pyyaml is required for v2 catalog views (pinned in the Dockerfile)")
    catalogs = [registry_index.load_catalog(BUNDLES)]
    if PUBLISHED.is_dir():
        catalogs.append(registry_index.load_catalog(PUBLISHED))
    return _merge_catalogs(catalogs)


def _merge_catalogs(catalogs: list[list[dict]]) -> list[dict]:
    """Union of catalog entries by name; git-checkout entries win on exact
    name (publish refuses to shadow them, so duplicates here are errors)."""
    merged: dict[str, dict] = {}
    for catalog in catalogs:
        for b in catalog:
            name = b.get("name")
            if name and name not in merged:
                merged[name] = b
    return list(merged.values())


def _find_bundle(catalog: list[dict], name: str) -> dict | None:
    for b in catalog:
        if b.get("name") == name:
            return b
    return None


def _bundles() -> list[dict]:
    result = []
    for base in (BUNDLES, PUBLISHED):
        if not base.is_dir():
            continue
        for proto in sorted(base.glob("*/protocol.yaml")):
            p = _load_protocol(proto)
            result.append({"name": p.get("name"), "version": p.get("version"),
                           "status": p.get("status")})
    return result


def _ledger_valid(name: str) -> bool:
    """Real ledger verification (chain-link + HMAC), same as /evidence/verify;
    fail-closed: any failure (missing key, missing ledger, bad chain) is False."""
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    if not ledger.exists():
        return False
    try:
        return pdd_evidence.verify_ledger(ledger).get("ok") is True
    except (SystemExit, Exception):  # noqa: BLE001
        return False


def _verify_bundle(name: str) -> dict:
    bundle_dir = BUNDLES / name
    if not bundle_dir.is_dir():
        bundle_dir = PUBLISHED / name
    if not bundle_dir.is_dir() or not EVIDENCE.joinpath(name).exists():
        return {"bundle": name, "ok": False, "reason": "no evidence dir in container"}
    proc = subprocess.run(
        ["pdd", "workflow", "evidence", "verify", str(bundle_dir),
         "--evidence-dir", str(EVIDENCE)],
        capture_output=True, text=True, timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    try:
        return {"bundle": name, "ok": proc.returncode == 0, "output": out.strip()[:2000]}
    except Exception as exc:  # noqa: BLE001
        return {"bundle": name, "ok": False, "reason": str(exc)}


def _admission(name: str) -> list[dict]:
    result = []
    adm_dir = EVIDENCE / name / "admission"
    if not adm_dir.exists():
        return result
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    # Authenticate the ledger first (chain-link + HMAC): attestation joins
    # against an unverified ledger would be a forged-verified claim.
    ledger_valid = _ledger_valid(name)
    blocks = []
    if ledger.exists():
        try:
            blocks = [json.loads(ln) for ln in ledger.read_text().splitlines() if ln.strip()]
        except Exception:  # noqa: BLE001
            blocks = []
    for f in sorted(adm_dir.glob("*.evidence.json")):
        try:
            ev = json.loads(f.read_text())
            artifact = (ev.get("implementation") or {}).get("artifact_digest")
            # Real verification: HMAC signature + digest of the evidence object,
            # then ledger attestation for the same artifact digest (only when
            # the ledger itself verified).
            try:
                sig_ok = pdd_evidence.verify_evidence_object(f).get("ok") is True
            except Exception:  # noqa: BLE001
                sig_ok = False
            ledger_attested = False
            decision = None
            if ledger_valid:
                for b in blocks:
                    obs = b.get("observations") or {}
                    if obs.get("admission") == artifact:
                        ledger_attested = True
                        decision = b.get("decision")
                        break
            result.append({
                "bundle": name,
                "file": f.name,
                "artifact_digest": artifact,
                "decision": decision,
                "signature_valid": sig_ok,
                "ledger_attested": ledger_attested,
                "ledger_valid": ledger_valid,
                "verified": bool(sig_ok and ledger_attested),
            })
        except Exception as exc:  # noqa: BLE001
            result.append({"bundle": name, "file": f.name, "error": str(exc)})
    return result


# ---------------------------------------------------------------------------
# POST /publish — the only write surface (bearer-token authenticated).
# Content-addressed immutable files (evidence objects, discovery logs, ledger
# blocks) + a SQLite metadata layer for idempotency, mirroring the OCI
# distribution model (immutable digest-keyed blobs + mutable metadata index).

def _canon(x) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()


def _evidence_digest_ok(evidence: dict) -> tuple[bool, str]:
    """Recompute digest + HMAC of an evidence object (same scheme as the
    evidence chain). Registry-owned namespaces MUST HMAC-verify (publish 400
    on failure); author namespaces fall back to structural attestation but the
    digest recompute is always enforced (a tampered object is invalid)."""
    body = dict(evidence)
    declared = body.pop("digest", None)
    body.pop("signature", None)
    if not isinstance(declared, str) or not declared.startswith("sha256:"):
        return False, "evidence missing valid digest"
    recomputed = "sha256:" + hashlib.sha256(_canon(body)).hexdigest()
    if recomputed != declared:
        return False, f"evidence digest mismatch (declared {declared}, recomputed {recomputed})"
    return True, "digest ok"


def _evidence_hmac_ok(evidence: dict) -> tuple[bool, str]:
    key = os.environ.get("PDD_EVIDENCE_KEY")
    if not key:
        return False, "PDD_EVIDENCE_KEY not set on the server (fail-closed)"
    body = dict(evidence)
    declared = body.pop("digest", None)
    sig = body.pop("signature", None)
    expected = "hmac-sha256:" + hmac.new(key.encode(), str(declared).encode(),
                                         hashlib.sha256).hexdigest()
    if sig != expected:
        return False, "evidence signature does not verify"
    return True, "signature ok"


def _bundle_digest_of(files: dict) -> str:
    """Same hashing as the validation engine's bundle_digest: sorted relpaths +
    content, sha256 over the whole tree."""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode())
        h.update(files[rel].encode())
    return "sha256:" + h.hexdigest()


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS submissions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " idem_key TEXT NOT NULL UNIQUE,"
            " namespace TEXT NOT NULL,"
            " name TEXT NOT NULL,"
            " version TEXT NOT NULL,"
            " bundle_digest TEXT NOT NULL,"
            " evidence_digest TEXT NOT NULL,"
            " status TEXT NOT NULL,"
            " created_at TEXT NOT NULL)")


def _submit_exists(idem_key: str) -> bool:
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            row = conn.execute("SELECT 1 FROM submissions WHERE idem_key = ?",
                               (idem_key,)).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _record_submission(idem_key: str, namespace: str, name: str, version: str,
                       bundle_digest: str, evidence_digest: str) -> None:
    with sqlite3.connect(DB_PATH, timeout=10) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO submissions"
            " (idem_key, namespace, name, version, bundle_digest, evidence_digest,"
            "  status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'published', ?)",
            (idem_key, namespace, name, version, bundle_digest, evidence_digest,
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))


def _handle_publish(payload: dict) -> tuple[int, dict]:
    """Validate and store a submission. Returns (http_status, response)."""
    # --- structural validation (S-001: request schema shape) ---
    if not isinstance(payload, dict):
        return 400, {"status": "error", "error": {"code": "invalid_request",
                                                  "message": "body must be a JSON object"}}
    namespace = payload.get("namespace")
    name = payload.get("name")
    version = payload.get("version")
    bundle = payload.get("bundle")
    evidence = payload.get("evidence")
    if not all(isinstance(x, str) and x for x in (namespace, name, version)):
        return 400, {"status": "error", "error": {"code": "invalid_request",
                                                  "message": "namespace/name/version must be non-empty strings"}}
    if not _BUNDLE_NAME_RE.fullmatch(name):
        return 400, {"status": "error", "error": {"code": "invalid_request",
                                                  "message": f"invalid bundle name {name!r}"}}
    if not isinstance(bundle, dict) or not bundle:
        return 400, {"status": "error", "error": {"code": "invalid_request",
                                                  "message": "bundle must be a non-empty {relpath: content} object"}}
    if not isinstance(evidence, dict):
        return 400, {"status": "error", "error": {"code": "invalid_request",
                                                  "message": "evidence must be an object"}}
    for field in ("protocol", "implementation", "digest", "signature"):
        if field not in evidence:
            return 400, {"status": "error", "error": {"code": "invalid_request",
                                                      "message": f"evidence missing required field {field!r}"}}
    if "protocol.yaml" not in bundle:
        return 400, {"status": "error", "error": {"code": "invalid_request",
                                                  "message": "bundle must include protocol.yaml"}}

    # --- digest binding: evidence must attest the submitted bundle ---
    bundle_digest = _bundle_digest_of(bundle)
    attested = (evidence.get("protocol") or {}).get("bundle_digest")
    if attested != bundle_digest:
        return 400, {"status": "error", "error": {"code": "conflict",
                                                  "message": f"evidence attests {attested}, submitted bundle is {bundle_digest}"}}

    # --- evidence integrity ---
    digest_ok, digest_msg = _evidence_digest_ok(evidence)
    if not digest_ok:
        return 400, {"status": "error", "error": {"code": "invalid_request", "message": digest_msg}}
    owned = namespace in REGISTRY_OWNED_NAMESPACES
    hmac_ok, hmac_msg = _evidence_hmac_ok(evidence)
    if owned and not hmac_ok:
        return 400, {"status": "error", "error": {"code": "invalid_request",
                                                  "message": f"registry-owned namespace requires HMAC-signed evidence: {hmac_msg}"}}

    evidence_digest = "sha256:" + hashlib.sha256(_canon(evidence)).hexdigest()
    idem_key = "|".join((namespace, name, version, bundle_digest, evidence_digest))

    with _publish_lock:
        if _submit_exists(idem_key):
            return 200, {"status": "already-published", "name": name, "version": version,
                         "bundle_digest": bundle_digest, "evidence_digest": evidence_digest}
        # Name collision with a git-checkout bundle (different digest) is a
        # conflict: the catalog is live from pdd-bundles/*, so publishing a
        # shadowing version of an authored bundle would corrupt the views.
        for existing in _catalog_strict():
            if existing.get("name") == name and existing.get("bundle_digest") != bundle_digest:
                return 409, {"status": "error", "error": {"code": "conflict",
                                                          "message": f"bundle name {name!r} already exists in the catalog with a different digest"}}
        rc, resp = _store_submission(namespace, name, version, bundle, evidence,
                                     bundle_digest, evidence_digest, idem_key, hmac_ok)
        return rc, resp


def _store_submission(namespace, name, version, bundle, evidence,
                      bundle_digest, evidence_digest, idem_key, hmac_ok) -> tuple[int, dict]:
    """Content-addressed storage + ledger append + sqlite record."""
    artifact_digest = (evidence.get("implementation") or {}).get("artifact_digest") or ""
    prefix16 = evidence_digest.split(":")[1][:16]

    # Evidence files (the server verifies from exactly this layout).
    adm = EVIDENCE / name / "admission"
    adm.mkdir(parents=True, exist_ok=True)
    adm_file = adm / f"{prefix16}.evidence.json"
    adm_file.write_text(json.dumps(evidence, indent=2))
    disc = EVIDENCE / name / "discovery"
    disc.mkdir(parents=True, exist_ok=True)
    disc_file = disc / f"{prefix16}.discovery.json"
    disc_file.write_text(json.dumps(evidence.get("discovery_log") or {}, indent=2))
    val = EVIDENCE / name / "validation"
    val.mkdir(parents=True, exist_ok=True)
    cand_prefix = artifact_digest.split(":")[1][:12] if artifact_digest else prefix16[:12]
    val_file = val / f"{cand_prefix}.results.json"
    val_file.write_text(json.dumps({
        "protocol": evidence.get("protocol"),
        "candidate_digest": artifact_digest,
        "validators": evidence.get("validators"),
        "results": evidence.get("results"),
        "verdict": evidence.get("decision", "admit"),
    }, indent=2))

    # Ledger append (server holds the evidence key; fail-closed if absent).
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    try:
        pdd_evidence.append_block(
            ledger, json.dumps({"id": name, "version": version}),
            f"{name}@{artifact_digest.split(':')[1][:12] if artifact_digest else 'unknown'}",
            {"admission": artifact_digest, "evidence_digest": evidence_digest},
            "attest-pass")
    except SystemExit as exc:
        return 500, {"status": "error", "error": {"code": "internal",
                                                  "message": f"ledger append failed: {exc}"}}

    # Bundle store: immutable version snapshot + live latest for the catalog.
    snap = PUBLISHED / namespace / name / version
    for rel, content in bundle.items():
        target = snap / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    latest = PUBLISHED / name
    if latest.exists():
        shutil.rmtree(latest)
    shutil.copytree(snap, latest)

    _init_db()
    _record_submission(idem_key, namespace, name, version, bundle_digest, evidence_digest)
    return 201, {"status": "published", "name": name, "version": version,
                 "bundle_digest": bundle_digest, "evidence_digest": evidence_digest,
                 "namespace": namespace}


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if path == "/healthz":
                body = b"pdd-service: ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/bundles":
                catalog = _catalog()
                bundles = [registry_index.summary(b) for b in catalog
                           if "error" not in b]
                status = (query.get("status") or [None])[0]
                depends = (query.get("depends_on") or [None])[0]
                if status is not None:
                    bundles = [b for b in bundles if b.get("status") == status]
                if depends is not None:
                    bundles = [b for b in bundles if depends in (b.get("depends_on") or [])]
                self._json({"bundles": bundles})
                return
            if path == "/evidence/verify":
                self._json({"results": [_verify_bundle(b["name"]) for b in _bundles()]})
                return
            if path == "/evidence/admission":
                all_adm = []
                for b in _bundles():
                    all_adm.extend(_admission(b["name"]))
                self._json({"admissions": all_adm})
                return
            if path == "/search":
                q = (query.get("q") or [""])[0].strip()
                if not q:
                    self._json({"error": "missing query parameter q"}, status=400)
                    return
                results = registry_index.search(_catalog_strict(), q)
                self._json({"query": q, "count": len(results), "results": results})
                return
            if path.startswith("/bundles/"):
                self._bundle_route(path, query)
                return
            self._json({"error": "not found"}, status=404)
        except Exception:  # noqa: BLE001
            # Never echo exception internals (paths, YAML parser details) to
            # clients — log them and return a generic 500.
            traceback.print_exc()
            self._json({"error": "internal error"}, status=500)

    def _bundle_route(self, path: str, query: dict):
        """/bundles/{name} and /bundles/{name}/{invariants|capabilities|ledger}."""
        parts = [p for p in path.split("/") if p]
        if len(parts) not in (2, 3):
            self._json({"error": "not found"}, status=404)
            return
        name = parts[1]
        b = _find_bundle(_catalog_strict(), name)
        if b is None:
            self._json({"error": f"no bundle named {name}"}, status=404)
            return
        if "error" in b:
            # Broken bundle (missing/unparseable protocol.yaml): log the
            # catalog error, surface a generic 500 (no parser internals).
            print(f"catalog error for bundle {name}: {b['error']}", file=sys.stderr)
            self._json({"error": f"bundle {name} is broken (see server log)"}, status=500)
            return
        if len(parts) == 2:
            self._json({
                "name": b["name"], "version": b.get("version"), "status": b.get("status"),
                "purpose": b.get("purpose"), "boundary": b.get("boundary", {}),
                "depends_on": b.get("depends_on", []), "provides": b.get("provides", {}),
                "invariant_ids": {layer: [it["id"] for it in b["invariants"][layer]]
                                  for layer in registry_index.LAYERS},
            })
            return
        sub = parts[2]
        if sub == "invariants":
            severity = (query.get("severity") or [None])[0]
            if severity is not None and severity not in registry_index.SEVERITIES:
                self._json({"error": f"severity must be one of {list(registry_index.SEVERITIES)}"},
                           status=400)
                return
            self._json({"bundle": name, "invariants": registry_index.invariants_view(b, severity)})
            return
        if sub == "capabilities":
            self._json({"bundle": name, "capabilities": b.get("capabilities", {})})
            return
        if sub == "ledger":
            limit = None
            raw = (query.get("limit") or [None])[0]
            if raw is not None:
                try:
                    limit = int(raw)
                except ValueError:
                    self._json({"error": f"limit must be an integer, got {raw!r}"}, status=400)
                    return
                if limit < 0:
                    self._json({"error": "limit must be >= 0"}, status=400)
                    return
            view = registry_index.ledger_view(EVIDENCE, name, limit)
            view["verified"] = _ledger_valid(name)
            self._json(view)
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            if parsed.path != "/publish":
                self._json({"error": "not found"}, status=404)
                return
            token = os.environ.get(PUBLISH_TOKEN_ENV)
            if not token:
                self._json({"status": "error",
                            "error": {"code": "internal",
                                      "message": "publish not configured (no PDD_PUBLISH_TOKEN)"}},
                           status=500)
                return
            auth = self.headers.get("Authorization", "")
            provided = auth[7:] if auth.startswith("Bearer ") else ""
            if not provided or not hmac.compare_digest(provided, token):
                self._json({"status": "error",
                            "error": {"code": "unauthorized", "message": "invalid bearer token"}},
                           status=401)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 32 * 1024 * 1024:
                self._json({"status": "error",
                            "error": {"code": "invalid_request", "message": "bad Content-Length"}},
                           status=400)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode())
            except (ValueError, UnicodeDecodeError):
                self._json({"status": "error",
                            "error": {"code": "invalid_request", "message": "body is not valid JSON"}},
                           status=400)
                return
            status, resp = _handle_publish(payload)
            self._json(resp, status=status)
        except Exception:  # noqa: BLE001
            # Never echo exception internals (paths, parser details) to clients.
            traceback.print_exc()
            self._json({"status": "error",
                        "error": {"code": "internal", "message": "internal error"}}, status=500)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(),
                                               self.log_date_time_string(),
                                               fmt % args))


if __name__ == "__main__":
    # Threading: subprocess routes (/evidence/*, /bundles/{name}/ledger) can
    # take seconds; one slow request must not block /healthz for every client.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
