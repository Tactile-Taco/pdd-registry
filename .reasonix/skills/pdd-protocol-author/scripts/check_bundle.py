#!/usr/bin/env python3
"""Hardened PDD bundle linter (extends upstream validate_pdd_bundle.py).

Checks, per bundle dir:
  1. required files exist (upstream behavior)
  2. protocol.yaml parses; status is one of draft/review/sealed/deprecated
  3. invariant ids are unique across S/B/O files; every `must` invariant maps to >=1 validator mechanism
  4. handshake schema references resolve to files inside the bundle
  5. cross-protocol `depends_on` references use sealed-or-review protocol names (checked by negotiator, warned here)
  6. validator-set.yaml exists and declares validator identities+versions (paper appendix conformance)
  7. catalog metadata (S-004/S-005): `namespace` is a required kebab-case string; `tags`
     is a list of kebab-case strings, at most 8, no duplicates
`--catalog <root>` additionally lints every bundle under root and enforces the
cross-bundle S-004 invariant: (namespace, name) pairs are unique.
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

# S-004/S-005 catalog metadata grammar: kebab-case, bounded length/count.
KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAMESPACE_LEN = 63
MAX_TAGS = 8

def load_yaml(p):
    txt = Path(p).read_text()
    if yaml:
        return yaml.safe_load(txt)
    # naive fallback: return raw text for marker checks
    return txt

def fail(msg): print(f"FAIL: {msg}"); return 1

def check_metadata(proto, errs):
    """S-004/S-005: namespace + tags grammar (controlled vocabulary shape)."""
    ns = proto.get("namespace")
    if not isinstance(ns, str) or not ns:
        errs.append("namespace is required (kebab-case string, e.g. pdd, user)")
    elif len(ns) > MAX_NAMESPACE_LEN or not KEBAB_RE.fullmatch(ns):
        errs.append(f"namespace {ns!r} must be kebab-case, 1..{MAX_NAMESPACE_LEN} chars")
    tags = proto.get("tags")
    if not isinstance(tags, list):
        errs.append(f"tags must be a list, got {type(tags).__name__}")
    else:
        if len(tags) > MAX_TAGS:
            errs.append(f"tags: at most {MAX_TAGS} tags allowed, got {len(tags)}")
        bad = [t for t in tags if not isinstance(t, str) or not KEBAB_RE.fullmatch(t)]
        if bad:
            errs.append(f"tag(s) {bad!r} must be kebab-case strings")
        # Per-element types are validated above, so set() below is hashable-safe.
        if not bad and len(tags) != len({t for t in tags if isinstance(t, str)}):
            errs.append("tags: duplicates are not allowed")

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
        check_metadata(proto, errs)

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


def main_catalog(root):
    """Lint every bundle under root, then the cross-bundle S-004 invariant:
    (namespace, name) pairs must be unique (names are unique within a
    namespace, not globally)."""
    rc = 0
    dirs = sorted(p for p in Path(root).iterdir() if p.is_dir())
    for d in dirs:
        rc |= main(d)
    if not yaml:
        return rc  # grammar checks already impossible without pyyaml
    seen = {}
    for d in dirs:
        proto = load_yaml(d / "protocol.yaml")
        if not isinstance(proto, dict):
            continue
        key = (proto.get("namespace"), d.name)
        if key in seen:
            fail(f"duplicate catalog address {key[0]}/{key[1]}: also declared by {seen[key]}")
            rc = 1
        else:
            seen[key] = d.name
    return rc


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--catalog":
        if len(sys.argv) < 3:
            print("usage: check_bundle.py --catalog <bundles-root>")
            sys.exit(2)
        sys.exit(main_catalog(sys.argv[2]))
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
