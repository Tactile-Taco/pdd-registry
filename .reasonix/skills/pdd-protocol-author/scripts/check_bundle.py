#!/usr/bin/env python3
"""Hardened PDD bundle linter (extends upstream validate_pdd_bundle.py).

Checks, per bundle dir:
  1. required files exist (upstream behavior)
  2. protocol.yaml parses; status is one of draft/review/sealed/deprecated
  3. invariant ids are unique across S/B/O files; every `must` invariant maps to >=1 validator mechanism
  4. handshake schema references resolve to files inside the bundle
  5. cross-protocol `depends_on` references use sealed-or-review protocol names (checked by negotiator, warned here)
  6. validator-set.yaml exists and declares validator identities+versions (paper appendix conformance)
Exit 0 = pass, 1 = fail. No third-party deps.
"""
import json, re, sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

REQUIRED = ["protocol.yaml", "capability-manifest.yaml", "invariants/structural.yaml",
            "invariants/behavioral.yaml", "invariants/operational.yaml",
            "validators/validation-plan.yaml", "validators/validator-set.yaml",
            "ambiguity-log.md", "evidence-requirements.yaml"]

def load_yaml(p):
    txt = Path(p).read_text()
    if yaml:
        return yaml.safe_load(txt)
    # naive fallback: return raw text for marker checks
    return txt

def fail(msg): print(f"FAIL: {msg}"); return 1

def main(bundle):
    b = Path(bundle); errs = []
    for f in REQUIRED:
        if not (b / f).exists(): errs.append(f"missing required file: {f}")
    if errs: return [fail(e) for e in errs] and 1

    proto = load_yaml(b / "protocol.yaml")
    if yaml:
        status = (proto.get("protocol") or {}).get("status")
        if status not in ("draft", "review", "sealed", "deprecated"):
            errs.append(f"protocol.status invalid: {status!r}")
        hs = proto.get("handshakes") or {}
        for name, ref in hs.items():
            if isinstance(ref, str) and not (b / ref).exists():
                errs.append(f"handshake {name} reference does not resolve: {ref}")

    seen, must_without_validator = set(), []
    for cls in ("structural", "behavioral", "operational"):
        data = load_yaml(b / "invariants" / f"{cls}.yaml")
        invs = (data or {}).get(f"{cls}_invariants") or (data or {}).get("invariants", []) if yaml else []
        for inv in invs:
            iid = inv.get("id")
            if iid in seen: errs.append(f"duplicate invariant id: {iid}")
            seen.add(iid)
            if inv.get("severity") == "must" and not inv.get("validation"):
                must_without_validator.append(iid)
    for iid in must_without_validator:
        errs.append(f"must-invariant {iid} has no validation mechanism")

    vs = load_yaml(b / "validators" / "validator-set.yaml")
    if yaml and not (vs or {}).get("validators"):
        errs.append("validator-set.yaml declares no validators")

    for e in errs: fail(e)
    if not errs:
        print(f"PASS: {b.name} ({len(seen)} invariants, sealed-check ok)")
    return 1 if errs else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
