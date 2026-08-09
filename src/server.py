"""pdd service — stdlib HTTP service exposing the pdd-repository registry state.

Client surface (see docs/service-features-v1.md and -v2.md):
  GET /healthz                    liveness
  GET /bundles                    registry index: [{name, version, status}] (v1)
  GET /bundles?status=&depends_on=&namespace=&tag=  filtered index (v2 + v3 namespace/tag)
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

import hmac
import json
import os
import subprocess
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8080"))
ROOT = Path(os.environ.get("PDD_ROOT", "/opt/pdd"))
BUNDLES = ROOT / "pdd-bundles"
EVIDENCE = ROOT / "evidence"
SKILLS = ROOT / ".reasonix" / "skills"
PDD = ROOT / "scripts" / "pdd.py"
# v1.2: DB-backed serving (S-006). When PDD_DATABASE_URL is set, the catalog,
# evidence, and ledger views are served from the backing database
# (PostgreSQL in production, sqlite:// for dev/tests) instead of the
# filesystem layout. Unset = the legacy filesystem path (backwards compat).
DATABASE_URL = os.environ.get("PDD_DATABASE_URL")

# registry_index.py lives in this same directory (src/); make it importable no
# matter how the module is loaded (python3 src/server.py, pytest, kubectl exec).
sys.path.insert(0, str(Path(__file__).resolve().parent))

import registry_index  # noqa: E402  (shared catalog/search with scripts/pdd.py)
import registry_db  # noqa: E402  (DB-backed storage adapter, v1.2 S-006)

_db_conn = None
_db_lock = threading.Lock()


def _db():
    """Lazy shared database connection for the DB-backed path (init guarded
    by a lock — the server is threaded). The global is set only after the
    schema is initialized, so a failed init retries on the next request."""
    global _db_conn
    if _db_conn is None:
        with _db_lock:
            if _db_conn is None:
                conn = registry_db.connect(DATABASE_URL)
                registry_db.init_schema(conn)
                _db_conn = conn
    return _db_conn

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
    genuinely need structured YAML (fail-closed: explicit error, no fake data).
    v1.2: with PDD_DATABASE_URL set, the catalog is materialized from the
    backing database (S-006) in the same entry shape as the filesystem path."""
    if DATABASE_URL:
        return registry_db.list_catalog(_db())
    if yaml is not None:
        return registry_index.load_catalog(BUNDLES)
    return [{"name": p.get("name"), "version": p.get("version"), "status": p.get("status"),
             "depends_on": [], "provides": {}}
            for p in (_load_protocol(f) for f in sorted(BUNDLES.glob("*/protocol.yaml")))]


def _catalog_strict() -> list[dict]:
    """v2 views require structured YAML — raise a clear error when absent.
    v1.2: with PDD_DATABASE_URL set, the DB is the structured source."""
    if DATABASE_URL:
        return registry_db.list_catalog(_db())
    if yaml is None:
        raise RuntimeError("pyyaml is required for v2 catalog views (pinned in the Dockerfile)")
    return registry_index.load_catalog(BUNDLES)


def _find_bundle(catalog: list[dict], name: str) -> dict | None:
    for b in catalog:
        if b.get("name") == name:
            return b
    return None


def _bundles() -> list[dict]:
    """The bundle name/version list for the evidence routes. In DB mode the
    catalog is the database (S-006: serving must not require the on-disk
    layout — a bundle published from outside the pod's image must be
    visible); otherwise the filesystem bundle layout."""
    if DATABASE_URL:
        return [{"name": b["name"], "version": b.get("version"),
                 "namespace": b.get("namespace")}
                for b in registry_db.list_catalog(_db())]
    result = []
    for proto in sorted(BUNDLES.glob("*/protocol.yaml")):
        p = _load_protocol(proto)
        result.append({"name": p.get("name"), "version": p.get("version"),
                       "status": p.get("status")})
    return result


def _ledger_valid(name: str) -> bool:
    """Real ledger verification (chain-link + HMAC), same as /evidence/verify;
    fail-closed: any failure (missing key, missing skills, bad chain) is False."""
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    verify_script = SKILLS / "pdd-evidence-keeper" / "scripts" / "evidence_chain.py"
    if not ledger.exists() or not verify_script.exists():
        return False
    try:
        lv = subprocess.run(
            [sys.executable, str(verify_script), "verify", str(ledger)],
            capture_output=True, text=True, timeout=60, cwd=ROOT)
        return json.loads(lv.stdout).get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def _verify_bundle(name: str) -> dict:
    if not SKILLS.exists() or not EVIDENCE.joinpath(name).exists():
        return {"bundle": name, "ok": False, "reason": "no evidence dir in container"}
    proc = subprocess.run([sys.executable, str(PDD), "evidence", "verify", name],
                          capture_output=True, text=True, timeout=120, cwd=ROOT)
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
    verify_script = SKILLS / "pdd-evidence-keeper" / "scripts" / "evidence_chain.py"
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
            vp = subprocess.run(
                [sys.executable, str(verify_script), "verify-evidence", str(f)],
                capture_output=True, text=True, timeout=60, cwd=ROOT)
            try:
                sig_ok = json.loads(vp.stdout).get("ok") is True
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


def _db_evidence_verify(name: str, namespace: str | None = None) -> list[dict]:
    """DB-backed evidence verification (v1.2, S-007): the registry stores the
    author's signed evidence records; verification is limited to presence,
    resource_identifier format, decision, and signature — the honor system.
    The registry does NOT re-run validation."""
    rows = registry_db.evidence_records(_db(), name, namespace)
    out = []
    import tempfile
    import importlib.util as _iu
    # Load the verifier once (exec_module re-executes on every call — the
    # per-row load in the original code re-read the module each time).
    chain = None
    try:
        spec = _iu.spec_from_file_location(
            "evidence_chain_db",
            SKILLS / "pdd-evidence-keeper" / "scripts" / "evidence_chain.py")
        chain = _iu.module_from_spec(spec)
        spec.loader.exec_module(chain)
    except Exception:  # noqa: BLE001 — per-record 'unavailable' below
        chain = None
    for row in rows:
        row_ok, reason = True, None
        if not registry_db.RESOURCE_ID_RE.fullmatch(row["resource_identifier"]):
            row_ok, reason = False, "resource_identifier format"
        if row["decision"] != "attest-pass":
            row_ok, reason = False, "decision"
        if row_ok and chain is not None:
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                    f.write(row["signed_object"])
                    tmp = f.name
                try:
                    ver = chain.verify_evidence_object(tmp)
                    if not ver.get("ok"):
                        row_ok, reason = False, ver.get("reason", "signature")
                finally:
                    os.unlink(tmp)
            except Exception:  # noqa: BLE001
                row_ok, reason = False, "verification unavailable"
        elif row_ok:
            row_ok, reason = False, "verification unavailable"
        out.append({"bundle": name, "artifact_id": row["artifact_id"],
                    "resource_identifier": row["resource_identifier"],
                    "decision": row["decision"], "signature_valid": row_ok,
                    "verified": row_ok, "reason": reason})
    return out


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """v1.2 publish handshake (B-006/S-007): POST /publish with
        {bundle, evidence} validates against the publish schema and the
        storage adapter, then persists idempotently. Only available in
        DB-backed mode (PDD_DATABASE_URL) — the filesystem path is
        author-side (git + CLI) and never accepts writes over HTTP."""
        try:
            parsed = urlparse(self.path)
            if parsed.path != "/publish":
                self._json({"error": {"kind": "not_found",
                                      "message": "unknown route"}}, status=404)
                return
            if not DATABASE_URL:
                self._json({"error": {"kind": "internal",
                                      "message": "publish requires PDD_DATABASE_URL "
                                                 "(DB-backed mode)"}}, status=500)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8 * 1024 * 1024:
                self._json({"error": {"kind": "invalid_request",
                                      "message": "publish payload too large "
                                                 "(max 8 MiB)"}}, status=400)
                return
            payload = json.loads(self.rfile.read(length).decode() or "{}")
            if not isinstance(payload, dict):
                self._json({"error": {"kind": "invalid_request",
                                      "message": "publish body must be a JSON "
                                                 "object {bundle, evidence}"}},
                           status=400)
                return
            # Publish authn (security review HIGH): any client that can reach
            # the Ingress may write catalog rows — require the shared-secret
            # bearer token (PDD_PUBLISH_TOKEN env from the pdd-publish-token
            # Secret; push.sh seeds with it). Fail closed when unset.
            expected = os.environ.get("PDD_PUBLISH_TOKEN")
            if not expected:
                self._json({"error": {"kind": "internal",
                                      "message": "publish disabled: "
                                                 "PDD_PUBLISH_TOKEN is not set"}},
                           status=500)
                return
            auth = self.headers.get("Authorization", "")
            supplied = auth[7:] if auth.startswith("Bearer ") else ""
            # hmac.compare_digest raises TypeError on non-ASCII str (headers
            # are latin-1-decoded): map that to 401, never a 500.
            try:
                token_ok = bool(supplied) and hmac.compare_digest(supplied, expected)
            except TypeError:
                token_ok = False
            if not token_ok:
                self._json({"error": {"kind": "invalid_request",
                                      "message": "publish requires a valid "
                                                 "Authorization: Bearer token"}},
                           status=401)
                return
            # Belt: adapter shape checks always run; suspenders: strict schema
            # validation when jsonschema is available.
            try:
                import jsonschema  # noqa: PLC0415
                schema = json.loads(
                    (Path(__file__).resolve().parent.parent
                     / "pdd-bundles" / "pdd-registry" / "schemas"
                     / "publish.schema.json").read_text())
                jsonschema.validate(payload, schema)
            except ImportError:
                pass
            except Exception as exc:  # noqa: BLE001 — ValidationError -> 400
                self._json({"error": {"kind": "invalid_request",
                                      "message": f"publish rejected: {exc}"}},
                           status=400)
                return
            record = registry_db.publish(_db(), payload["bundle"],
                                         payload["evidence"])
            self._json({"ok": True, "record": record["bundle"]})
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            self._json({"error": {"kind": "invalid_request",
                                  "message": f"publish rejected: {exc}"}},
                       status=400)
        except Exception:  # noqa: BLE001 — never leak internals to clients
            traceback.print_exc()
            self._json({"error": {"kind": "internal",
                                  "message": "internal error (see server log)"}},
                       status=500)

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
                namespace = (query.get("namespace") or [None])[0]
                tag = (query.get("tag") or [None])[0]
                if status is not None:
                    bundles = [b for b in bundles if b.get("status") == status]
                if depends is not None:
                    bundles = [b for b in bundles if depends in (b.get("depends_on") or [])]
                if namespace is not None:
                    # S-004: exact namespace match (namespace/name addressing).
                    bundles = [b for b in bundles if b.get("namespace") == namespace]
                if tag is not None:
                    # S-005: exact tag membership, not substring.
                    bundles = [b for b in bundles if tag in (b.get("tags") or [])]
                self._json({"bundles": bundles})
                return
            if path == "/evidence/verify":
                if DATABASE_URL:
                    # One row per (name, namespace), not per version record
                    # (_bundles() lists every published version).
                    seen = set()
                    targets = []
                    for b in _bundles():
                        key = (b["name"], b.get("namespace"))
                        if key not in seen:
                            seen.add(key)
                            targets.append(b)
                    self._json({"ok": True,
                                "results": [r for b in targets
                                            for r in _db_evidence_verify(
                                                b["name"], b.get("namespace"))]})
                else:
                    self._json({"results": [_verify_bundle(b["name"]) for b in _bundles()]})
                return
            if path == "/evidence/admission":
                if DATABASE_URL:
                    all_adm = []
                    seen = set()
                    for b in _bundles():
                        key = (b["name"], b.get("namespace"))
                        if key in seen:
                            continue
                        seen.add(key)
                        for row in registry_db.evidence_records(
                                _db(), b["name"], b.get("namespace")):
                            all_adm.append({
                                "bundle": row["name"], "version": row["version"],
                                "artifact_id": row["artifact_id"],
                                "resource_identifier": row["resource_identifier"],
                                "decision": row["decision"],
                                "digest": row["digest"]})
                    self._json({"admissions": all_adm})
                else:
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
        if DATABASE_URL:
            # DB mode: the semver-max record (registry_db.get_bundle sorts in
            # Python — lexical TEXT order would serve '1.9.0' over '1.10.0').
            b = registry_db.get_bundle(_db(), name)
        else:
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
                "namespace": b.get("namespace"), "tags": b.get("tags") or [],
                "address": b.get("address"),
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
            if DATABASE_URL:
                # DB-backed ledger view: the registry's own append-only
                # event log (blocks appended by publish()). bundle_ref is
                # namespace-qualified so each (namespace, name) sees its own
                # contiguous seq run; the previous-links form one global
                # hash chain. Address here is name-keyed (v3 surface) —
                # same-name bundles in different namespaces resolve via the
                # semver-max record, matching the FS-mode /bundles/{name}.
                # limit semantics match the filesystem mode: None = all,
                # limit>0 = last N, limit=0 = zero blocks (a -0 slice would
                # return everything — never slice with limit=0).
                b = registry_db.get_bundle(_db(), name)
                if b is None:
                    self._json({"error": f"no bundle named {name}"}, status=404)
                    return
                blocks = registry_db.ledger_blocks(
                    _db(), f"{b['namespace']}/{name}")
                if limit is None:
                    shown = blocks
                elif limit > 0:
                    shown = blocks[-limit:]
                else:
                    shown = []
                self._json({"ok": True, "blocks": shown, "count": len(blocks)})
                return
            view = registry_index.ledger_view(EVIDENCE, name, limit)
            view["verified"] = _ledger_valid(name)
            self._json(view)
            return
        self._json({"error": "not found"}, status=404)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(),
                                               self.log_date_time_string(),
                                               fmt % args))


if __name__ == "__main__":
    # Threading: subprocess routes (/evidence/*, /bundles/{name}/ledger) can
    # take seconds; one slow request must not block /healthz for every client.
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
