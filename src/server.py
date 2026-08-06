"""pdd service — stdlib HTTP service exposing the pdd-repository registry state.

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

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8080"))
ROOT = Path(os.environ.get("PDD_ROOT", "/opt/pdd"))
BUNDLES = ROOT / "pdd-bundles"
EVIDENCE = ROOT / "evidence"
SKILLS = ROOT / ".reasonix" / "skills"
PDD = ROOT / "scripts" / "pdd.py"

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
    if yaml is not None:
        return registry_index.load_catalog(BUNDLES)
    return [{"name": p.get("name"), "version": p.get("version"), "status": p.get("status"),
             "depends_on": [], "provides": {}}
            for p in (_load_protocol(f) for f in sorted(BUNDLES.glob("*/protocol.yaml")))]


def _catalog_strict() -> list[dict]:
    """v2 views require structured YAML — raise a clear error when absent."""
    if yaml is None:
        raise RuntimeError("pyyaml is required for v2 catalog views (pinned in the Dockerfile)")
    return registry_index.load_catalog(BUNDLES)


def _find_bundle(catalog: list[dict], name: str) -> dict | None:
    for b in catalog:
        if b.get("name") == name:
            return b
    return None


def _bundles() -> list[dict]:
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
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, status=500)

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
            # Broken bundle (missing/unparseable protocol.yaml): surface the
            # catalog error instead of crashing on missing keys.
            self._json({"error": b["error"]}, status=500)
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

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(),
                                               self.log_date_time_string(),
                                               fmt % args))


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
