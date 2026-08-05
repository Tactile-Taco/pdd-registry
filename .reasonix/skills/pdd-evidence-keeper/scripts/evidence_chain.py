#!/usr/bin/env python3
# Evidence Chain + Dynamic Evidence Ledger primitives (HMAC-signed, SHA-256 chained).
import hashlib, hmac, json, os, sys, time
from pathlib import Path

KEY_ENV = "PDD_EVIDENCE_KEY"
KEY = os.environ.get(KEY_ENV)
if not KEY:
    sys.exit(
        f"error: {KEY_ENV} is not set (or is empty); refusing to sign or verify evidence (fail closed). "
        "Export the same key used at signing time (local dev: any non-empty value; "
        "CI: repository secret).")
KEY = KEY.encode()

def canon(x): return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()
def digest_bytes(b): return "sha256:" + hashlib.sha256(b).hexdigest()
def digest_obj(x): return digest_bytes(canon(x))
def sign(d): return "hmac-sha256:" + hmac.new(KEY, d.encode(), hashlib.sha256).hexdigest()

def build_evidence(protocol, impl_digest, validators, results, meta):
    body = {"protocol": protocol, "implementation": {"artifact_digest": impl_digest},
            "validators": validators, "results": results,
            "provenance": {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **meta}}
    body["digest"] = digest_obj(body)
    body["signature"] = sign(body["digest"])
    return body

def append_block(ledger_path, protocol, impl_version, observations, decision):
    p = Path(ledger_path); prev = "sha256:" + "0" * 64
    if p.exists() and p.stat().st_size:
        prev = json.loads(p.read_text().strip().splitlines()[-1])["digest"]
    block = {"previous": prev, "protocol": protocol, "implementation_version": impl_version,
             "observations": observations, "decision": decision,
             "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    block["digest"] = digest_obj(block); block["signature"] = sign(block["digest"])
    with p.open("a") as f: f.write(json.dumps(block) + "\n")
    return block

def verify_ledger(ledger_path):
    prev = "sha256:" + "0" * 64
    lines = [ln for ln in Path(ledger_path).read_text().strip().splitlines() if ln.strip()] if Path(ledger_path).exists() else []
    if not lines:
        return {"ok": False, "blocks": 0, "reason": "empty-ledger"}
    n = 0
    for i, line in enumerate(lines):
        b = json.loads(line)
        if b["previous"] != prev: return {"ok": False, "diverged_at": i, "reason": "chain-link"}
        d = dict(b); dg = d.pop("digest"); d.pop("signature", None)
        if digest_obj(d) != dg: return {"ok": False, "diverged_at": i, "reason": "digest"}
        if not hmac.compare_digest(sign(dg), b.get("signature", "")): return {"ok": False, "diverged_at": i, "reason": "signature"}
        prev = dg; n = i + 1
    return {"ok": True, "blocks": n}


def verify_evidence_object(path):
    """Recompute digest + signature of one admission evidence object."""
    b = json.loads(Path(path).read_text())
    d = dict(b); dg = d.pop("digest"); d.pop("signature", None)
    if digest_obj(d) != dg:
        return {"ok": False, "reason": "digest"}
    if not hmac.compare_digest(sign(dg), b.get("signature", "")):
        return {"ok": False, "reason": "signature"}
    return {"ok": True, "digest": dg}

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "append":
        print(json.dumps(append_block(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4],
                                      json.loads(sys.argv[5]), sys.argv[6])))
    elif cmd == "verify":
        print(json.dumps(verify_ledger(sys.argv[2])))
    elif cmd == "verify-evidence":
        print(json.dumps(verify_evidence_object(sys.argv[2])))
    elif cmd == "build":
        print(json.dumps(build_evidence(json.loads(sys.argv[2]), sys.argv[3],
                                        json.loads(sys.argv[4]), json.loads(sys.argv[5]),
                                        json.loads(sys.argv[6] if len(sys.argv) > 6 else "{}"))))
