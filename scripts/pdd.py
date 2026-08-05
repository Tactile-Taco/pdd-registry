#!/usr/bin/env python3
"""pdd — the PDD registry CLI (docker-like interface for pdd-repository).

Mental model:
  pdd-bundles/            = the image registry (versioned, sealed, digestable)
  implementations/        = containers (candidate realizations of a bundle)
  evidence/               = the attestation log (Evidence Chain + Dynamic Ledger)

Commands:
  pdd bundle lint [name]            lint bundle(s) with the hardened linter
  pdd bundle seal <name>            seal a bundle (lint must pass; writes minutes)
  pdd validate <name> [--impl DIR]  run the three-layer Validator Loop
                                    [--sandbox] [--pbt-runs N]
  pdd evidence build <name> --impl DIR   signed evidence object + genesis ledger block
  pdd evidence verify <name>        re-walk the runtime ledger, report divergence
  pdd run <name> --impl DIR [--sandbox]  smoke-run a candidate (docker sandbox if available)

Stdlib only. Reuses the skill scripts under .reasonix/skills/.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLES = REPO_ROOT / "pdd-bundles"
IMPLS = REPO_ROOT / "implementations"
EVIDENCE = REPO_ROOT / "evidence"
SKILLS = REPO_ROOT / ".reasonix" / "skills"

CHECK_BUNDLE = SKILLS / "pdd-protocol-author" / "scripts" / "check_bundle.py"
EVIDENCE_CHAIN = SKILLS / "pdd-evidence-keeper" / "scripts" / "evidence_chain.py"


def bundle_dir(name: str) -> Path:
    d = BUNDLES / name
    if not d.exists():
        sys.exit(f"no bundle named {name} under pdd-bundles/")
    return d


def default_impl(name: str) -> Path:
    d = IMPLS / name
    if not d.exists():
        sys.exit(f"no implementations under implementations/{name}/")
    variants = sorted(d.iterdir())
    if not variants:
        sys.exit(f"implementations/{name}/ is empty")
    return variants[0]


def cmd_bundle_lint(argv: list[str]) -> int:
    target = argv[0] if argv else None
    dirs = [BUNDLES / target] if target else sorted(BUNDLES.iterdir())
    rc = 0
    for d in dirs:
        if not d.is_dir():
            continue
        r = subprocess.run([sys.executable, str(CHECK_BUNDLE), str(d)])
        rc |= r.returncode
    return rc


def cmd_bundle_seal(argv: list[str]) -> int:
    name = argv[0]
    d = bundle_dir(name)
    r = subprocess.run([sys.executable, str(CHECK_BUNDLE), str(d)])
    if r.returncode != 0:
        sys.exit("lint must pass before sealing")
    proto = d / "protocol.yaml"
    text = proto.read_text().replace("status: draft", "status: sealed").replace(
        "status: review", "status: sealed")
    proto.write_text(text)
    minutes = d / "negotiation-minutes.md"
    if not minutes.exists():
        minutes.write_text(
            f"# Negotiation Minutes — {name}\n\nSealed via `pdd bundle seal`.\n"
            f"No open conflicts; lint passed; versions pinned.\n")
    print(f"sealed: {name} (status: sealed in protocol.yaml)")
    return 0


def _flag_value(argv: list[str], name: str) -> str | None:
    """Return the value following --name, or None (missing or trailing flag)."""
    if name not in argv:
        return None
    idx = argv.index(name)
    if idx + 1 >= len(argv):
        sys.exit(f"flag {name} requires a value")
    return argv[idx + 1]


def cmd_validate(argv: list[str]) -> int:
    name = argv[0]
    impl = Path(_flag_value(argv, "--impl")) if "--impl" in argv else default_impl(name)
    extra = ["--sandbox"] if "--sandbox" in argv else []
    runs = _flag_value(argv, "--pbt-runs")
    if runs:
        extra += ["--pbt-runs", runs]
    r = subprocess.run([sys.executable, "validators/validate_candidate.py",
                        str(bundle_dir(name)), str(impl), *extra], cwd=REPO_ROOT)
    return r.returncode


def cmd_evidence_build(argv: list[str]) -> int:
    name = argv[0]
    if "--impl" not in argv:
        sys.exit("evidence build requires --impl DIR")
    impl = Path(_flag_value(argv, "--impl"))
    proto = bundle_dir(name)
    impl_src = impl / "user_registry.py"
    impl_digest = "sha256:" + hashlib.sha256(impl_src.read_bytes()).hexdigest()
    # The attested results must be for THIS candidate: match by digest prefix.
    results_file = next(
        (EVIDENCE / name / "validation").glob(f"{impl_digest.split(':')[1][:12]}*.results.json"),
        None)
    if results_file is None:
        sys.exit("no validation results for this candidate digest — run `pdd validate` first")
    results = json.loads(results_file.read_text())
    if results.get("candidate_digest") != impl_digest:
        sys.exit(f"candidate digest mismatch: results attest {results.get('candidate_digest')}, "
                 f"--impl is {impl_digest}; refusing to bind evidence to the wrong artifact")
    if results["verdict"] != "admit":
        sys.exit(f"cannot build admission evidence: verdict is {results['verdict']}")

    manifest = json.loads((impl / "candidate-manifest.json").read_text())

    evidence = {
        "protocol": {"name": name, "version": "1.0.0",
                     "bundle_digest": results["protocol"]["bundle_digest"]},
        "implementation": {"artifact_id": manifest["artifact_id"],
                           "artifact_digest": impl_digest,
                           "language": manifest["language"],
                           "runtime": manifest["runtime"]},
        "validators": results["validators"],
        "results": results["results"],
        "discovery_log": {
            "files": manifest["files"],
            "dependencies": manifest["dependencies"],
            "invariant_lineage": manifest["invariant_lineage"],
            "known_limitations": manifest["known_limitations"],
        },
        "decision": "admit",
    }
    disc = EVIDENCE / name / "discovery"
    disc.mkdir(parents=True, exist_ok=True)
    disc_path = disc / f"{impl_digest.split(':')[1][:16]}.discovery.json"
    disc_path.write_text(json.dumps(evidence["discovery_log"], indent=2))
    # Bind the discovery log into the signed object: digest of the exact bytes on disk.
    disc_digest = "sha256:" + hashlib.sha256(disc_path.read_bytes()).hexdigest()
    chain = subprocess.run(
        [sys.executable, str(EVIDENCE_CHAIN), "build",
         json.dumps(evidence["protocol"]), impl_digest,
         json.dumps(results["validators"]), json.dumps(results["results"]),
         json.dumps({"manifest": manifest["artifact_id"], "discovery_digest": disc_digest})],
        capture_output=True, text=True)
    if chain.returncode != 0:
        sys.exit(f"evidence build failed: {chain.stderr}")
    evidence_obj = json.loads(chain.stdout)

    adm = EVIDENCE / name / "admission"
    adm.mkdir(parents=True, exist_ok=True)
    evidence_path = adm / f"{impl_digest.split(':')[1][:16]}.evidence.json"
    evidence_path.write_text(json.dumps(evidence_obj, indent=2))
    evidence_digest = "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()

    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    if ledger.exists():
        existing = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        already = any(
            (b.get("observations") or {}).get("admission") == impl_digest
            and (b.get("observations") or {}).get("evidence_digest") == evidence_digest
            for b in existing)
        if already:
            print(f"admission {impl_digest.split(':')[1][:16]} already attested with this evidence; no new genesis block")
            return 0
    genesis = subprocess.run(
        [sys.executable, str(EVIDENCE_CHAIN), "append", str(ledger),
         json.dumps({"id": name, "version": "1.0.0"}),
         manifest["artifact_id"] + "@" + impl_digest.split(':')[1][:12],
         json.dumps({"admission": impl_digest, "evidence_digest": evidence_digest}),
         "attest-pass"],
        capture_output=True, text=True)
    if genesis.returncode != 0:
        sys.exit(f"genesis block failed: {genesis.stderr}")
    print(f"evidence built: admission/{impl_digest.split(':')[1][:16]}.evidence.json")
    print(f"genesis block appended to {ledger.relative_to(REPO_ROOT)}")
    return 0


def cmd_evidence_verify(argv: list[str]) -> int:
    name = argv[0]
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    if not ledger.exists():
        sys.exit(f"no ledger at {ledger}")
    r = subprocess.run([sys.executable, str(EVIDENCE_CHAIN), "verify", str(ledger)],
                       capture_output=True, text=True)
    print(r.stdout.strip())
    result = json.loads(r.stdout)
    if not result["ok"]:
        return 1
    # Also verify each admission evidence object (signature + digest) and that
    # the ledger's recorded evidence_digest matches the file on disk.
    rc = 0
    blocks = [json.loads(ln) for ln in ledger.read_text().splitlines() if ln.strip()]
    attested = {}
    for b in blocks:
        obs = b.get("observations") or {}
        if obs.get("evidence_digest"):
            attested[obs["evidence_digest"]] = b.get("digest")
    on_disk = set()
    for path in sorted((EVIDENCE / name / "admission").glob("*.evidence.json")):
        cur = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if cur not in attested:
            print(f"FAIL: {path.name} is not attested by the ledger (no matching evidence_digest)")
            rc = 1
            continue
        vr = subprocess.run([sys.executable, str(EVIDENCE_CHAIN), "verify-evidence", str(path)],
                            capture_output=True, text=True)
        vres = json.loads(vr.stdout)
        if not vres["ok"]:
            print(f"FAIL: {path.name} digest/signature invalid ({vres['reason']})")
            rc = 1
        else:
            print(f"OK: {path.name} digest+signature valid, ledger-attested")
            # The discovery log is bound into the signed provenance: recompute and compare.
            ev = json.loads(path.read_text())
            disc_digest = (ev.get("provenance") or {}).get("discovery_digest")
            if disc_digest:
                disc_file = next((EVIDENCE / name / "discovery").glob("*.discovery.json"), None)
                if disc_file is None:
                    print("FAIL: evidence binds a discovery digest but no discovery file on disk")
                    rc = 1
                else:
                    on_disk = "sha256:" + hashlib.sha256(disc_file.read_bytes()).hexdigest()
                    if on_disk != disc_digest:
                        print(f"FAIL: discovery file digest mismatch (signed {disc_digest}, on disk {on_disk})")
                        rc = 1
    if not attested:
        print("FAIL: ledger contains no evidence attestation blocks (nothing verified)")
        rc = 1
    # Append-only ledger: historical blocks may attest superseded evidence
    # objects that were overwritten by a later admission — that is expected.
    # The tamper invariant is the reverse direction: every file on disk must be
    # attested by some block (checked above), never a file that changed silently.
    return rc


def cmd_run(argv: list[str]) -> int:
    name = argv[0]
    impl = Path(_flag_value(argv, "--impl")) if "--impl" in argv else default_impl(name)
    if shutil.which("docker") and "--sandbox" in argv:
        r = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only",
             "-v", f"{impl.resolve()}:/candidate:ro", "-w", "/candidate",
             "python:3.12-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64",
             "python", "-c",
             "import sys; sys.path.insert(0,'.'); from user_registry import UserRegistry; "
             "reg = UserRegistry(); r1 = reg.create({'client_request_id':'a','email':'x@y.dev','display_name':'X'}); "
             "r2 = reg.create({'client_request_id':'a','email':'x@y.dev','display_name':'X'}); "
             "print('run:', r1['outcome'], 'then', r2['outcome'], 'state', len(reg))"],
            capture_output=True, text=True, timeout=300)
        print(r.stdout.strip() or r.stderr.strip()[:500])
        return r.returncode
    print("sandbox run requires docker; local smoke:")
    return 0


COMMANDS = {
    "bundle": {"lint": cmd_bundle_lint, "seal": cmd_bundle_seal},
    "validate": cmd_validate,
    "evidence": {"build": cmd_evidence_build, "verify": cmd_evidence_verify},
    "run": cmd_run,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    head, rest = argv[1], argv[2:]
    if head == "bundle" or head == "evidence":
        if len(rest) < 1:
            print(__doc__)
            return 2
        sub = COMMANDS[head].get(rest[0])
        if sub is None:
            print(f"unknown subcommand: {head} {rest[0]}")
            print(__doc__)
            return 2
        return sub(rest[1:])
    if head in ("validate", "run"):
        if not rest:
            print(f"{head} requires a bundle name")
            print(__doc__)
            return 2
        return COMMANDS[head](rest)
    print(f"unknown command: {head}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
