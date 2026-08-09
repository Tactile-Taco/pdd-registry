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
  pdd index                         build the registry catalog over pdd-bundles/*
  pdd search <query>                search the catalog (names, purpose, invariants, capabilities)

Stdlib only. Reuses the skill scripts under .reasonix/skills/.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# registry_index.py lives under src/ (shared with the HTTP service); adding it
# to sys.path keeps `pdd index`/`pdd search` on the same index as /search.
sys.path.insert(0, str(REPO_ROOT / "src"))
BUNDLES = REPO_ROOT / "pdd-bundles"
IMPLS = REPO_ROOT / "implementations"
EVIDENCE = REPO_ROOT / "evidence"
SKILLS = REPO_ROOT / ".reasonix" / "skills"

CHECK_BUNDLE = SKILLS / "pdd-protocol-author" / "scripts" / "check_bundle.py"
EVIDENCE_CHAIN = SKILLS / "pdd-evidence-keeper" / "scripts" / "evidence_chain.py"


_BUNDLE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _valid_bundle_name(name: str) -> bool:
    """Bundle names feed filesystem paths; constrain to [A-Za-z0-9_-] so a
    hostile name cannot escape pdd-bundles/ or evidence/ (security review)."""
    return isinstance(name, str) and bool(_BUNDLE_NAME_RE.fullmatch(name))


def _valid_sha256(digest: str | None) -> bool:
    """Digests feed evidence filenames; accept only the exact sha256:<hex>
    shape so a hostile results file cannot inject path separators (security
    review)."""
    return isinstance(digest, str) and bool(_SHA256_RE.fullmatch(digest))


def _bundle_digest(bundle_dir: Path) -> str:
    """Recompute the bundle digest exactly like validators/validate_candidate.py
    (sorted rglob, relative posix paths + bytes, .git excluded) so evidence
    build can prove the results file attests the bundle ON DISK."""
    try:
        h = hashlib.sha256()
        for p in sorted(bundle_dir.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                h.update(p.relative_to(bundle_dir).as_posix().encode())
                h.update(p.read_bytes())
    except OSError as exc:
        sys.exit(f"cannot digest bundle {bundle_dir.name}: {exc}")
    return "sha256:" + h.hexdigest()


def _assert_safe_expression(expr: str) -> None:
    """smoke.assert_expr must parse as a pure eval-mode expression using only
    the allowed node types (no function calls, imports, lambdas, dunders).
    Mirrors validators/validate_candidate.py (duplicated for the subprocess
    boundary; keep in lockstep)."""
    import ast as _ast

    if not isinstance(expr, str):
        raise SystemExit(f"smoke.assert_expr must be a string, got {type(expr).__name__}")
    try:
        tree = _ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise SystemExit(f"smoke.assert_expr is not a valid expression: {expr!r}") from exc
    safe_nodes = (_ast.Expression, _ast.Constant, _ast.Name, _ast.Attribute, _ast.Subscript,
                  _ast.Compare, _ast.BoolOp, _ast.BinOp, _ast.UnaryOp, _ast.List, _ast.Tuple,
                  _ast.Dict, _ast.Slice, _ast.cmpop, _ast.boolop, _ast.operator, _ast.unaryop,
                  _ast.Load)
    for node in _ast.walk(tree):
        if not isinstance(node, safe_nodes):
            raise SystemExit(f"smoke.assert_expr uses disallowed construct "
                             f"{type(node).__name__}: {expr!r}")


def bundle_dir(name: str) -> Path:
    if not _valid_bundle_name(name):
        sys.exit(f"invalid bundle name {name!r}")
    d = BUNDLES / name
    if not d.exists():
        sys.exit(f"no bundle named {name} under pdd-bundles/")
    return d


def default_impl(name: str) -> Path:
    if not _valid_bundle_name(name):
        sys.exit(f"invalid bundle name {name!r}")
    d = IMPLS / name
    if not d.exists():
        sys.exit(f"no implementations under implementations/{name}/")
    variants = sorted(d.iterdir())
    if not variants:
        sys.exit(f"implementations/{name}/ is empty")
    return variants[0]


def cmd_bundle_lint(argv: list[str]) -> int:
    target = argv[0] if argv else None
    if target is not None and not _valid_bundle_name(target):
        sys.exit(f"invalid bundle name {target!r}")
    dirs = [BUNDLES / target] if target else sorted(BUNDLES.iterdir())
    rc = 0
    for d in dirs:
        if not d.is_dir():
            continue
        r = subprocess.run([sys.executable, str(CHECK_BUNDLE), str(d)])
        rc |= r.returncode
    if target is None:
        # Cross-bundle S-004: (namespace, name) pairs must be unique. The
        # catalog mode re-runs per-bundle checks; cheap at this registry size
        # and keeps one entry point for the whole-catalog gate.
        r = subprocess.run([sys.executable, str(CHECK_BUNDLE), "--catalog", str(BUNDLES)])
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
    impl = Path(_flag_value(argv, "--impl")).resolve() if "--impl" in argv else default_impl(name)
    extra = ["--sandbox"] if "--sandbox" in argv else []
    runs = _flag_value(argv, "--pbt-runs")
    if runs:
        extra += ["--pbt-runs", runs]
    r = subprocess.run([sys.executable, "validators/validate_candidate.py",
                        str(bundle_dir(name)), str(impl), *extra], cwd=REPO_ROOT)
    return r.returncode


def _load_validation_results(path: Path) -> dict:
    """Load + shape-check a validation-results file (unauthenticated input).

    Fail-closed: unreadable/corrupt JSON, non-object roots, missing verdict,
    and malformed section shapes all exit cleanly BEFORE the caller can use
    any value (digests from this file feed evidence filenames).
    """
    try:
        results = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError):
        sys.exit(f"validation results {path.name} are unreadable or corrupt "
                 f"(not valid JSON) — run `pdd validate` first")
    if not isinstance(results, dict):
        sys.exit(f"validation results {path.name} are not a JSON object "
                 f"(got {type(results).__name__}) — run `pdd validate` first")
    if results.get("verdict") != "admit":
        sys.exit(f"cannot build admission evidence: verdict is {results.get('verdict')}")
    for key in ("protocol", "validators", "results"):
        if not isinstance(results.get(key), dict if key == "protocol" else list):
            sys.exit(f"validation results {path.name} carry a malformed "
                     f"{key!r} section (got {type(results.get(key)).__name__}) — "
                     f"run `pdd validate` first")
    return results


def cmd_evidence_build(argv: list[str]) -> int:
    name = argv[0]
    if "--impl" not in argv:
        sys.exit("evidence build requires --impl DIR")
    impl = Path(_flag_value(argv, "--impl")).resolve()
    # v1.2 S-007: the author's validator-loop execution record (http(s) URL
    # or urn: URN, e.g. a CI/CD results page) is bound into the signed
    # provenance as validation_resource.
    validation_resource = _flag_value(argv, "--validation-resource")
    if validation_resource is not None and not re.fullmatch(
            r"(https?://|urn:)[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+",
            validation_resource):
        sys.exit("--validation-resource must be an http(s) URL or urn: URN (S-007)")
    # --force: re-attest the CURRENT (impl, bundle) snapshot with new
    # provenance (e.g. a first-time validation_resource). A signed object
    # cannot be edited (signature + ledger digest), so a new object + ledger
    # block are appended; older objects and blocks stay (append-only).
    force = "--force" in argv
    proto = bundle_dir(name)
    manifest = json.loads((impl / "candidate-manifest.json").read_text())
    entry_module = manifest.get("entry_module")
    if not entry_module:
        sys.exit("candidate-manifest.json must declare `entry_module`")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry_module):
        sys.exit(f"entry_module must be a Python identifier, got {entry_module!r}")
    impl_src = impl / f"{entry_module}.py"
    impl_digest = "sha256:" + hashlib.sha256(impl_src.read_bytes()).hexdigest()
    # Version comes from the sealed protocol, not a hardcoded literal.
    import registry_index  # lazy: pyyaml, same index as pdd index/search
    _b = registry_index.load_bundle(proto)
    if not _b or "error" in _b:
        sys.exit(f"cannot read protocol for {name}: {(_b or {}).get('error', 'unparseable')} — "
                 f"refusing to sign evidence for an unknown version")
    version = _b.get("version")
    if not isinstance(version, str) or not version:
        sys.exit(f"protocol for {name} declares no version — refusing to sign evidence")
    # The attested results must be for THIS candidate: match by digest prefix.
    results_file = next(
        (EVIDENCE / name / "validation").glob(f"{impl_digest.split(':')[1][:12]}*.results.json"),
        None)
    if results_file is None:
        sys.exit("no validation results for this candidate digest — run `pdd validate` first")
    results = _load_validation_results(results_file)
    if results.get("candidate_digest") != impl_digest:
        sys.exit(f"candidate digest mismatch: results attest {results.get('candidate_digest')}, "
                 f"--impl is {impl_digest}; refusing to bind evidence to the wrong artifact")
    # The attested results must also be for THIS bundle: recompute the real
    # bundle digest and fail on stale results — the results file is an
    # unauthenticated input and the bundle digest feeds evidence filenames.
    real_bundle_digest = _bundle_digest(proto)
    attested_bundle = results["protocol"].get("bundle_digest")
    if not _valid_sha256(attested_bundle):
        sys.exit(f"invalid bundle_digest in validation results: {attested_bundle!r}")
    if attested_bundle != real_bundle_digest:
        sys.exit(f"stale validation results: they attest bundle digest "
                 f"{attested_bundle[:16]} but the bundle on disk is "
                 f"{real_bundle_digest[:16]} — re-run `pdd validate {name}` first")

    # Idempotency + version-event handling. Admission/discovery files are
    # keyed by BOTH the artifact digest and the bundle digest
    # ({impl[:16]}-{bundle[:12]}.{evidence,discovery}.json), so a protocol
    # version event writes a NEW file and never overwrites the attested
    # object of a previous version — old files and old ledger blocks both
    # stay (append-only, no silent overwrite). The idempotent-rebuild check:
    # the file for the CURRENT bundle digest exists on disk and matches the
    # ledger's latest attestation for this artifact.
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    stem = f"{impl_digest.split(':')[1][:16]}-{results['protocol']['bundle_digest'].split(':')[1][:12]}"
    # --force re-attestation of the SAME (impl, bundle) snapshot: the signed
    # object cannot be edited (signature + ledger digest), so a NEW object is
    # built — but it must never overwrite the attested object that occupies
    # the canonical stem name (append-only). The suffix is derived from the
    # new object's OWN digest (the object embeds a second-resolution
    # timestamp, so it is only known after the chain build); a counter
    # guards the pathological same-object case.
    adm_path = EVIDENCE / name / "admission" / f"{stem}.evidence.json"
    if ledger.exists():
        existing = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
        attested = [b for b in existing
                    if (b.get("observations") or {}).get("admission") == impl_digest
                    and (b.get("observations") or {}).get("evidence_digest")]
        if attested:
            # Legacy-name migration: an admission previously written as
            # {impl[:16]}.evidence.json is renamed to the stem-keyed name
            # ONLY when it attests the CURRENT bundle digest (bytes preserved
            # -> digest preserved -> ledger attestation still matches). A
            # legacy file attesting an OLDER bundle digest is left in place:
            # it is a superseded version object and the version-event branch
            # below builds the new stem file. The discovery log moves with
            # the renamed file (same base-name convention).
            legacy = EVIDENCE / name / "admission" / f"{impl_digest.split(':')[1][:16]}.evidence.json"
            if legacy.exists() and not adm_path.exists():
                try:
                    legacy_ev = json.loads(legacy.read_text())
                except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError):
                    print(f"FAIL: legacy admission file {legacy.name} is corrupt (unparseable JSON); "
                          f"refusing to migrate or overwrite")
                    return 1
                if not isinstance(legacy_ev, dict):
                    print(f"FAIL: legacy admission file {legacy.name} is corrupt (not a JSON object); "
                          f"refusing to migrate or overwrite")
                    return 1
                legacy_proto = legacy_ev.get("protocol")
                if not isinstance(legacy_proto, dict):
                    print(f"FAIL: legacy admission file {legacy.name} is corrupt "
                          f"(protocol section is {type(legacy_proto).__name__}); "
                          f"refusing to migrate or overwrite")
                    return 1
                legacy_bundle = legacy_proto.get("bundle_digest")
                if legacy_bundle == results["protocol"]["bundle_digest"]:
                    legacy.rename(adm_path)
                    legacy_disc = EVIDENCE / name / "discovery" / f"{impl_digest.split(':')[1][:16]}.discovery.json"
                    stem_disc = EVIDENCE / name / "discovery" / f"{stem}.discovery.json"
                    if legacy_disc.exists() and not stem_disc.exists():
                        legacy_disc.rename(stem_disc)
            if adm_path.exists():
                if force:
                    # Re-attestation: the canonical name is occupied by an
                    # attested object — never overwrite it (append-only); the
                    # digest-suffix logic below gives the new object its own
                    # name. The on-disk mismatch check is deliberately skipped:
                    # after a prior --force build the canonical file legitimately
                    # differs from the LATEST attestation (the suffixed one).
                    print(f"admission {stem} already attested and consistent; "
                          f"--force: re-attesting with the current provenance "
                          f"(building a NEW object; older objects + blocks stay, append-only)")
                else:
                    on_disk = "sha256:" + hashlib.sha256(adm_path.read_bytes()).hexdigest()
                    attested_digest = attested[-1]["observations"]["evidence_digest"]
                    if on_disk != attested_digest:
                        print(f"FAIL: attested evidence file differs from the ledger "
                              f"(on disk {on_disk} != attested {attested_digest}); "
                              f"after a --force re-attestation the canonical file "
                              f"legitimately differs — use --force to re-attest, "
                              f"or re-run validate then rebuild")
                        return 1
                    print(f"admission {stem} already attested and consistent; "
                          f"evidence snapshot preserved (re-verify with `pdd evidence verify`)")
                    return 0
            else:
                # An attestation exists for this artifact but no file for the
                # CURRENT bundle digest -> protocol version event (or the file
                # was removed): build a new object; any older version's file and
                # ledger block stay.
                print(f"admission for {impl_digest.split(':')[1][:16]} is attested under a different "
                      f"bundle digest (or its file was removed); building {stem}.evidence.json "
                      f"(older objects + ledger blocks stay, append-only)")

    evidence = {
        "protocol": {"name": name, "version": version,
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
    disc_content = json.dumps(evidence["discovery_log"], indent=2)
    disc_path = disc / f"{stem}.discovery.json"
    # The digest is content-based (name-independent): it can be computed in
    # memory, so the canonical file write is DEFERRED until the final stem is
    # known — a --force suffix must never touch the canonical discovery file
    # (the preserved canonical admission binds its existing digest).
    disc_digest = "sha256:" + hashlib.sha256(disc_content.encode()).hexdigest()
    provenance = {"manifest": manifest["artifact_id"],
                  "discovery_digest": disc_digest}
    if validation_resource is not None:
        provenance["validation_resource"] = validation_resource
    chain = subprocess.run(
        [sys.executable, str(EVIDENCE_CHAIN), "build",
         json.dumps(evidence["protocol"]), impl_digest,
         json.dumps(results["validators"]), json.dumps(results["results"]),
         json.dumps(provenance)],
        capture_output=True, text=True)
    if chain.returncode != 0:
        sys.exit(f"evidence build failed: {chain.stderr}")
    evidence_obj = json.loads(chain.stdout)

    # --force + occupied canonical name: give the new object its own name
    # (digest-suffixed) so the attested older object is never overwritten.
    # The discovery log is content-identical, so the suffixed discovery file
    # carries the same bytes (and the same digest the object binds).
    if force and (EVIDENCE / name / "admission" / f"{stem}.evidence.json").exists():
        base = stem
        stem += "-" + evidence_obj["digest"].split(":")[1][:8]
        n = 1
        while (EVIDENCE / name / "admission" / f"{stem}.evidence.json").exists():
            stem = f"{base}-{evidence_obj['digest'].split(':')[1][:8]}-{n}"
            n += 1
        print(f"--force: canonical name occupied; re-attesting as {stem}.evidence.json "
              f"(older objects + ledger blocks stay, append-only)")
        # Only the SUFFIXED discovery file is written: the preserved
        # canonical admission binds the canonical discovery's existing
        # digest, and touching that file would break its attestation.
        (disc / f"{stem}.discovery.json").write_text(disc_content)
    else:
        # Final stem is canonical: write the discovery log it binds.
        disc_path.write_text(disc_content)

    adm = EVIDENCE / name / "admission"
    adm.mkdir(parents=True, exist_ok=True)
    evidence_path = adm / f"{stem}.evidence.json"
    evidence_path.write_text(json.dumps(evidence_obj, indent=2))
    evidence_digest = "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()

    genesis = subprocess.run(
        [sys.executable, str(EVIDENCE_CHAIN), "append", str(ledger),
         json.dumps({"id": name, "version": version}),
         manifest["artifact_id"] + "@" + impl_digest.split(':')[1][:12],
         json.dumps({"admission": impl_digest, "evidence_digest": evidence_digest}),
         "attest-pass"],
        capture_output=True, text=True)
    if genesis.returncode != 0:
        sys.exit(f"genesis block failed: {genesis.stderr}")
    print(f"evidence built: admission/{stem}.evidence.json")
    print(f"genesis block appended to {ledger.relative_to(REPO_ROOT)}")
    return 0


def cmd_evidence_latest(argv: list[str]) -> int:
    """Print the admission evidence file attested by the LATEST ledger block
    (used by deploy/push.sh to seed the DB-backed registry: the newest
    signed evidence is the one to publish, and it is version-event-safe)."""
    if not argv:
        print("evidence latest requires a bundle name, e.g. `pdd evidence latest pdd-registry`")
        return 2
    name = argv[0]
    if not _valid_bundle_name(name):
        sys.exit(f"invalid bundle name {name!r}")
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    if not ledger.exists():
        sys.exit(f"no ledger at {ledger}")
    blocks = [json.loads(ln) for ln in ledger.read_text().splitlines() if ln.strip()]
    if not blocks:
        sys.exit(f"ledger {ledger} is empty")
    latest = (blocks[-1].get("observations") or {}).get("evidence_digest")
    if not latest:
        sys.exit(f"latest block of {ledger} has no evidence_digest")
    for path in sorted((EVIDENCE / name / "admission").glob("*.evidence.json")):
        if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == latest:
            print(path)
            return 0
    sys.exit(f"no admission file matches the latest attestation {latest}")


def cmd_evidence_staleness(argv: list[str]) -> int:
    """evidence staleness [bundle...]

    S-008 freshness gate (keyless): every sealed bundle's LATEST admission
    evidence must attest the CURRENT on-disk bundle digest. A bundle
    directory change without re-validate + re-attestation fails the gate,
    so CI can block a PR/deploy that would drift the registry's migrated
    record away from its signed evidence. Exit 0 = all fresh; exit 1 =
    drift; exit 2 = usage error."""
    names = argv or sorted(p.name for p in BUNDLES.glob("*")
                           if (p / "protocol.yaml").is_file())
    if not names:
        print("no bundles to check")
        return 2
    rc = 0
    for name in names:
        if not _valid_bundle_name(name):
            print(f"FAIL {name}: invalid bundle name")
            rc = 1
            continue
        ledger = EVIDENCE / name / "runtime-ledger.jsonl"
        if not ledger.exists():
            print(f"skip {name}: no evidence chain yet (bundle not attested)")
            continue
        try:
            blocks = [json.loads(ln) for ln in ledger.read_text().splitlines()
                      if ln.strip()]
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL {name}: cannot read ledger: {exc}")
            rc = 1
            continue
        if not blocks:
            print(f"FAIL {name}: empty ledger")
            rc = 1
            continue
        latest = (blocks[-1].get("observations") or {}).get("evidence_digest")
        if not latest:
            print(f"FAIL {name}: latest block has no evidence_digest")
            rc = 1
            continue
        adm = None
        adm_dir = EVIDENCE / name / "admission"
        if adm_dir.is_dir():
            for path in sorted(adm_dir.glob("*.evidence.json")):
                if "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == latest:
                    try:
                        adm = json.loads(path.read_text())
                    except json.JSONDecodeError:
                        adm = None
                    break
        if adm is None:
            print(f"FAIL {name}: latest attestation {latest[:16]} has no "
                  "readable admission file")
            rc = 1
            continue
        attested = (adm.get("protocol") or {}).get("bundle_digest")
        if not _valid_sha256(attested):
            print(f"FAIL {name}: admission attests invalid bundle_digest "
                  f"{attested!r}")
            rc = 1
            continue
        disk = _bundle_digest(BUNDLES / name)
        if attested != disk:
            print(f"FAIL {name}: evidence attests {attested[:16]} but the "
                  f"bundle on disk is {disk[:16]} — re-run `pdd validate "
                  f"{name}` + `pdd evidence build {name}` (S-008)")
            rc = 1
        else:
            print(f"OK {name}: evidence attests current digest {disk[:16]}")
    return rc


def cmd_evidence_verify(argv: list[str]) -> int:
    registry, argv = _remote_registry(argv)
    if registry:
        # Same command, new resource: verify the DB-backed registry's stored
        # evidence records (presence + resource_identifier + decision +
        # signature, S-007 honor system) over the registry's own endpoint.
        body = _registry_get(registry, "/evidence/verify")
        if argv:
            rows = [r for r in body.get("results", []) if r.get("bundle") == argv[0]]
            body["results"] = rows
        print(json.dumps(body, indent=2))
        # rc reflects VERIFICATION, not the endpoint's own ok flag: any
        # unverified record (honor-system signature check) fails the run.
        results = body.get("results") or []
        return 0 if body.get("ok") and results and all(
            r.get("verified") for r in results) else 1
    name = argv[0]
    if not _valid_bundle_name(name):
        sys.exit(f"invalid bundle name {name!r}")
    ledger = EVIDENCE / name / "runtime-ledger.jsonl"
    if not ledger.exists():
        sys.exit(f"no ledger at {ledger}")
    r = subprocess.run([sys.executable, str(EVIDENCE_CHAIN), "verify", str(ledger)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("FAIL: ledger verify subprocess failed: "
              + (r.stdout.strip()[:200] or r.stderr.strip()[:200]))
        return 1
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
            attested[obs["evidence_digest"]] = b
    for path in sorted((EVIDENCE / name / "admission").glob("*.evidence.json")):
        cur = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if cur not in attested:
            print(f"FAIL: {path.name} is not attested by the ledger (no matching evidence_digest)")
            rc = 1
            continue
        block = attested[cur]
        ev = json.loads(path.read_text())
        block_admission = (block.get("observations") or {}).get("admission")
        ev_artifact = (ev.get("implementation") or {}).get("artifact_digest")
        if block_admission != ev_artifact:
            print(f"FAIL: {path.name} attesting block binds admission {block_admission}, "
                  f"evidence object attests {ev_artifact}")
            rc = 1
            continue
        vr = subprocess.run([sys.executable, str(EVIDENCE_CHAIN), "verify-evidence", str(path)],
                            capture_output=True, text=True)
        if vr.returncode != 0:
            print(f"FAIL: {path.name} verify-evidence subprocess errored: "
                  f"{vr.stderr.strip() or vr.stdout.strip()[:200]}")
            rc = 1
            continue
        vres = json.loads(vr.stdout)
        if not vres["ok"]:
            print(f"FAIL: {path.name} digest/signature invalid ({vres['reason']})")
            rc = 1
        else:
            print(f"OK: {path.name} digest+signature valid, ledger-attested")
            # The discovery log is bound into the signed provenance: recompute
            # and compare. The discovery file shares the admission file's base
            # name with a .discovery.json suffix ({impl[:16]}-{bundle[:12]}),
            # so the lookup is an exact path, not a prefix glob.
            disc_digest = (ev.get("provenance") or {}).get("discovery_digest")
            if disc_digest:
                disc_file = EVIDENCE / name / "discovery" / (
                    path.name[:-len(".evidence.json")] + ".discovery.json")
                if not disc_file.exists():
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
    impl = Path(_flag_value(argv, "--impl")).resolve() if "--impl" in argv else default_impl(name)
    if shutil.which("docker") and "--sandbox" in argv:
        manifest = json.loads((impl / "candidate-manifest.json").read_text())
        entry_module = manifest.get("entry_module")
        entry_class = manifest.get("entry_class")
        smoke = manifest.get("smoke") or {}
        if not (entry_module and entry_class and smoke.get("method")):
            print("candidate-manifest.json must declare entry_module/entry_class/smoke")
            return 1
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry_module) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry_class) \
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", smoke.get("method")):
            print(f"entry_module/entry_class/smoke.method must be Python identifiers, got "
                  f"{entry_module!r}/{entry_class!r}/{smoke.get('method')!r}")
            return 1
        assert_expr = smoke.get("assert_expr", "True")
        # AST allowlist gate, mirroring validators/validate_candidate.py
        # (substring blacklists are evadable; an eval-mode AST allowlist is not).
        try:
            _assert_safe_expression(assert_expr)
        except SystemExit as exc:
            print(f"smoke.assert_expr rejected: {exc}")
            return 1
        args_lit = json.dumps(smoke.get("args") or {})
        if smoke.get("call_style") == "single_dict":
            call = f"{entry_class}().{smoke['method']}({args_lit})"
        else:
            call = f"{entry_class}().{smoke['method']}(**{args_lit})"
        code = ("import sys; sys.path.insert(0,'.'); "
                f"from {entry_module} import {entry_class}; "
                f"r = {call}; "
                f"assert {assert_expr}; print('run: ok')")
        r = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--read-only",
             "--memory", "256m", "--pids-limit", "64", "--cpus", "1",
             "--cap-drop", "ALL", "--user", "65534:65534",
             "--security-opt", "no-new-privileges",
             "-v", f"{impl.resolve()}:/candidate:ro", "-w", "/candidate",
             "python:3.12-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64",
             "python", "-c", code],
            capture_output=True, text=True, timeout=300)
        print(r.stdout.strip() or r.stderr.strip()[:500])
        return r.returncode
    print("pdd run --sandbox requires docker; refusing a local (unsandboxed) smoke run")
    return 1


def _remote_registry(argv: list[str]) -> tuple[str | None, list[str]]:
    """Extract --registry <pdd+http(s)://host/> (or $PDD_REGISTRY) from argv.

    Returns (registry_url, remaining_argv). The resource identifier scheme
    is pdd+http(s):// (v1.2): the CLI talks to a registry server whose
    catalog is served from the backing database on the mini-pc."""
    rest: list[str] = []
    url = os.environ.get("PDD_REGISTRY")
    i = 0
    while i < len(argv):
        if argv[i] == "--registry":
            if i + 1 >= len(argv):
                sys.exit("--registry requires a value, e.g. "
                         "--registry pdd+https://registry.example")
            url = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if url is not None and not (url.startswith("pdd+http://")
                                or url.startswith("pdd+https://")):
        sys.exit(f"invalid registry resource identifier {url!r} "
                 f"(expected pdd+http(s)://<host>/... )")
    return url, rest


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never follow redirects for registry fetches (security review LOW):
    the resource identifier IS the registry — a redirect (or a file://
    redirect under the default opener's FileHandler) must not be followed,
    or the Authorization header could leak to another target / local files
    could be read."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _strict_opener():
    """urllib opener WITHOUT FileHandler/FTPHandler/ProxyHandler/cookies:
    only http(s), with redirects disabled."""
    import urllib.request as _ur
    opener = _ur.OpenerDirector()
    opener.add_handler(_ur.UnknownHandler())
    opener.add_handler(_ur.HTTPHandler())
    opener.add_handler(_ur.HTTPSHandler())
    opener.add_handler(_ur.HTTPErrorProcessor())
    opener.add_handler(_ur.HTTPDefaultErrorHandler())
    opener.add_handler(_NoRedirectHandler())
    return opener


def _registry_get(url: str, path: str) -> dict:
    """GET a read endpoint on a remote registry (stdlib urllib only)."""
    import urllib.error
    import urllib.parse
    http_url = url[len("pdd+"):].rstrip("/") + path
    try:
        with _strict_opener().open(http_url, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        sys.exit(f"registry error {err.code} for {path} "
                 f"(redirects are disabled for pdd+ registries): "
                 f"{err.read().decode()[:300]}")
    except urllib.error.URLError as err:
        sys.exit(f"cannot reach registry {http_url!r}: {err.reason}")
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        sys.exit(f"registry returned non-JSON for {path}: {err}")


def _registry_post(url: str, path: str, payload: dict) -> dict:
    """POST a write endpoint on a remote registry (publish handshake). The
    bearer token (PDD_PUBLISH_TOKEN env, set in the registry pod / by the
    deploy runner) authenticates the publish — required since the security
    review; without it the registry rejects the write."""
    import urllib.error
    http_url = url[len("pdd+"):].rstrip("/") + path
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("PDD_PUBLISH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(  # noqa: S310
        http_url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with _strict_opener().open(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        sys.exit(f"publish rejected ({err.code}): {err.read().decode()[:300]}")
    except urllib.error.URLError as err:
        sys.exit(f"cannot reach registry {http_url!r}: {err.reason}")
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        sys.exit(f"registry returned non-JSON for {path}: {err}")


def cmd_index(argv: list[str]) -> int:
    registry, argv = _remote_registry(argv)
    if registry:
        # Same command, new resource: the remote registry serves the catalog
        # from its backing database (S-006) with the same JSON shape.
        body = _registry_get(registry, "/bundles")
        print(json.dumps({"bundles": body.get("bundles", [])}, indent=2))
        return 0
    from registry_index import catalog_json, load_catalog  # lazy: keeps other cmds stdlib-only
    catalog = load_catalog(BUNDLES)
    errors = [b for b in catalog if "error" in b]
    if errors:
        for b in errors:
            print(f"ERROR: {b['name']}: {b['error']}", file=sys.stderr)
        return 1
    print(json.dumps(catalog_json(catalog), indent=2))
    return 0


def cmd_search(argv: list[str]) -> int:
    registry, argv = _remote_registry(argv)
    if not argv:
        print("search requires a query, e.g. `pdd search idempotent`")
        print(__doc__)
        return 2
    query = argv[0]
    if registry:
        import urllib.parse
        body = _registry_get(registry, f"/search?q={urllib.parse.quote(query)}")
        print(json.dumps({"query": query, "count": body.get("count", 0),
                          "results": body.get("results", [])}, indent=2))
        return 0 if body.get("results") else 1
    from registry_index import load_catalog, search  # lazy: keeps other cmds stdlib-only
    catalog = load_catalog(BUNDLES)
    errors = [b for b in catalog if "error" in b]
    if errors:
        for b in errors:
            print(f"ERROR: {b['name']}: {b['error']}", file=sys.stderr)
        return 1
    results = search(catalog, query)
    print(json.dumps({"query": query, "count": len(results), "results": results}, indent=2))
    return 0 if results else 1


def cmd_publish(argv: list[str]) -> int:
    """publish <bundle-dir> --evidence <file> --registry pdd+http://<host>/

    Publishes a bundle + its signed evidence to a DB-backed registry (v1.2
    publish handshake, B-006 idempotent / S-007 resource_identifier)."""
    registry, argv = _remote_registry(argv)
    if not argv or registry is None:
        print("publish requires a bundle dir and --registry, e.g.")
        print("  pdd publish pdd-bundles/pdd-registry \\")
        print("    --evidence evidence.json --registry pdd+http://m6/registry")
        return 2
    evidence_file = None
    rest = []
    i = 0
    while i < len(argv):
        if argv[i] == "--evidence" and i + 1 < len(argv):
            evidence_file = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if evidence_file is None:
        print("publish requires --evidence <file>")
        return 2
    if not rest:
        print("publish requires a bundle dir, e.g. pdd-bundles/pdd-registry")
        return 2
    bundle_dir = Path(rest[0])
    try:
        ev = json.loads(Path(evidence_file).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"cannot read evidence file {evidence_file}: {exc}")
        return 2
    if not isinstance(ev, dict):
        print(f"evidence file {evidence_file} must contain a JSON object")
        return 2
    if not bundle_dir.is_dir() or not (bundle_dir / "protocol.yaml").exists():
        print(f"not a bundle directory: {bundle_dir}")
        return 2
    from registry_index import load_bundle  # lazy: pyyaml
    b = load_bundle(bundle_dir)
    if "error" in b:
        sys.exit(f"cannot read bundle: {b['error']}")
    payload = {"bundle": {
        "namespace": b.get("namespace"), "name": b["name"],
        "version": b.get("version"), "status": b.get("status"),
        "digest": _bundle_digest(bundle_dir),
        "purpose": b.get("purpose"), "tags": b.get("tags") or [],
        "depends_on": b.get("depends_on") or [],
        "provides": b.get("provides") or {},
        "invariants": b.get("invariants") or {},
        "capabilities": b.get("capabilities") or {},
        "boundary": b.get("boundary") or {},
    }, "evidence": {
        "artifact_id": ev.get("artifact_id") or b.get("name"),
        "resource_identifier": (
            ev.get("resource_identifier")
            or (ev.get("provenance") or {}).get("validation_resource")
            or sys.exit("evidence file must contain resource_identifier "
                        "(S-007: http(s) URL or urn: URN of the validator "
                        "loop record; admission evidence objects carry it "
                        "under provenance.validation_resource)")),
        "decision": ev.get("decision", "attest-pass"),
        "signed_object": ev.get("signed_object", ev),
        "digest": ev.get("digest", ""),
    }}
    body = _registry_post(registry, "/publish", payload)
    print(json.dumps(body, indent=2))
    return 0 if body.get("ok") else 1


COMMANDS = {
    "bundle": {"lint": cmd_bundle_lint, "seal": cmd_bundle_seal},
    "validate": cmd_validate,
    "evidence": {"build": cmd_evidence_build, "verify": cmd_evidence_verify,
                 "latest": cmd_evidence_latest,
                 "staleness": cmd_evidence_staleness},
    "run": cmd_run,
    "publish": cmd_publish,
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
    if head == "publish":
        if not rest:
            print("publish requires a bundle dir and --registry, e.g.")
            print("  pdd publish pdd-bundles/pdd-registry \\")
            print("    --evidence evidence.json --registry pdd+http://m6/registry")
            return 2
        return COMMANDS[head](rest)
    if head == "index":
        return cmd_index(rest)
    if head == "search":
        return cmd_search(rest)
    print(f"unknown command: {head}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
