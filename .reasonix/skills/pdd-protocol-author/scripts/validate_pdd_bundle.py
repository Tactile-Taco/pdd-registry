#!/usr/bin/env python3
"""Lightweight structural validator for PDD bundle folders.

This avoids external dependencies so it can run in minimal Python environments.
It checks that required files exist and contain expected top-level markers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


REQUIRED_FILES = {
    "protocol.yaml": ["protocol:", "handshakes:", "invariants:"],
    "capability-manifest.yaml": ["capabilities:"],
    "invariants/structural.yaml": ["structural_invariants:"],
    "invariants/behavioral.yaml": ["behavioral_invariants:"],
    "invariants/operational.yaml": ["operational_invariants:"],
    "validators/validation-plan.yaml": ["validation_plan:"],
    "ambiguity-log.md": ["Resolved Assumptions", "Open Questions"],
    "evidence-requirements.yaml": ["evidence_requirements:"],
}

JSON_SCHEMA_FILES = [
    "schemas/request.schema.json",
    "schemas/response.schema.json",
]


def fail(message: str) -> None:
    print(f"[FAIL] {message}")


def ok(message: str) -> None:
    print(f"[OK] {message}")


def validate(bundle_dir: Path) -> int:
    errors = 0
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        fail(f"Bundle directory not found: {bundle_dir}")
        return 1

    for rel, markers in REQUIRED_FILES.items():
        path = bundle_dir / rel
        if not path.exists():
            fail(f"Missing required file: {rel}")
            errors += 1
            continue
        text = path.read_text(encoding="utf-8")
        missing = [marker for marker in markers if marker not in text]
        if missing:
            fail(f"{rel} missing marker(s): {', '.join(missing)}")
            errors += 1
        else:
            ok(rel)

    for rel in JSON_SCHEMA_FILES:
        path = bundle_dir / rel
        if not path.exists():
            fail(f"Missing schema file: {rel}")
            errors += 1
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            fail(f"{rel} is not valid JSON: {exc}")
            errors += 1
            continue
        if data.get("type") != "object":
            fail(f"{rel} should define an object schema")
            errors += 1
        else:
            ok(rel)

    if errors:
        fail(f"{errors} issue(s) found")
        return 1
    ok("PDD bundle structure is valid")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: validate_pdd_bundle.py <bundle-dir>")
        return 2
    return validate(Path(argv[1]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
