"""pdd service — stdlib HTTP service exposing the pdd-repository registry state.

Client surface (see docs/service-features-v1.md):
  GET /healthz                    liveness
  GET /bundles                    registry index: [{name, version, status}]
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

PORT = int(os.environ.get("PORT", "8080"))
ROOT = Path(os.environ.get("PDD_ROOT", "/opt/pdd"))
BUNDLES = ROOT / "pdd-bundles"
EVIDENCE = ROOT / "evidence"
SKILLS = ROOT / ".reasonix" / "skills"
PDD = ROOT / "scripts" / "pdd.py"

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


def _bundles() -> list[dict]:
    result = []
    for proto in sorted(BUNDLES.glob("*/protocol.yaml")):
        p = _load_protocol(proto)
        result.append({"name": p.get("name"), "version": p.get("version"),
                       "status": p.get("status")})
    return result


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
    for f in sorted(adm_dir.glob("*.evidence.json")):
        try:
            ev = json.loads(f.read_text())
            result.append({
                "bundle": name,
                "file": f.name,
                "artifact_digest": (ev.get("implementation") or {}).get("artifact_digest"),
                "decision": ev.get("decision"),
                "verified": (ev.get("provenance") or {}).get("discovery_digest") is not None,
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
            if self.path == "/healthz":
                body = b"pdd-service: ok\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/bundles":
                self._json({"bundles": _bundles()})
                return
            if self.path == "/evidence/verify":
                self._json({"results": [_verify_bundle(b["name"]) for b in _bundles()]})
                return
            if self.path == "/evidence/admission":
                all_adm = []
                for b in _bundles():
                    all_adm.extend(_admission(b["name"]))
                self._json({"admissions": all_adm})
                return
            self._json({"error": "not found"}, status=404)
        except Exception as exc:  # noqa: BLE001
            self._json({"error": str(exc)}, status=500)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(),
                                               self.log_date_time_string(),
                                               fmt % args))


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
