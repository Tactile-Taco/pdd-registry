#!/usr/bin/env python3
# Cross-bundle compatibility checker for PDD contract negotiation.
# Scans a protocols/ dir, builds the depends_on graph, verifies that every
# consumed handshake is provided, and that dependency protocols exist.
# Emits compatibility-report.json. Exit 1 on any open conflict.
import json, sys
from pathlib import Path
try:
    import yaml
except ImportError:
    print("FAIL: pyyaml required"); sys.exit(1)

def main(root):
    root = Path(root); bundles = {}
    for d in sorted(root.iterdir()):
        p = d / "protocol.yaml"
        if p.exists():
            data = yaml.safe_load(p.read_text())
            bundles[data["protocol"]["name"]] = (d, data)
    conflicts = []
    for name, (d, data) in bundles.items():
        proto = data["protocol"]
        for dep in (data.get("depends_on") or []):
            if dep not in bundles:
                conflicts.append({"protocol": name, "type": "missing-dependency",
                                  "detail": "depends on unknown protocol %s" % dep})
        for c in (data.get("consumes") or []):
            owner = None
            for n2, (_, d2) in bundles.items():
                if c in (d2.get("provides") or {}):
                    owner = n2; break
            if owner is None:
                conflicts.append({"protocol": name, "type": "unprovided-handshake",
                                  "detail": "consumes %s but no bundle provides it" % c})
    report = {"protocols": sorted(bundles), "conflicts": conflicts,
              "sealable": not conflicts}
    (root / "compatibility-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    sys.exit(1 if conflicts else 0)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
