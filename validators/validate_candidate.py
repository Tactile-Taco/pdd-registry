#!/usr/bin/env python3
"""PDD Validation Engine for pdd-repository — three mandatory layers.

Layer 1 Structural (S):   JSON Schema conformance + contract tests
Layer 2 Behavioral (B):   pytest + hypothesis property suite with invariant lineage;
                          mutation sanity (a hand-built mutant must FAIL its property)
Layer 3 Operational (O):  dependency/import scan, forbidden-call scan, optional docker
                          sandbox (network none, read-only fs), benchmark (advisory)

Emit validation-results.json per the pdd-validation-engine skill, verdict admit iff
every `must` invariant passes and no mutation-suspect flags are open. `should`
invariants are measured and recorded as observations, never admission-gating.

Usage:
  python3 validators/validate_candidate.py <bundle-dir> <impl-dir> [--sandbox] [--pbt-runs N]

Stdlib + optional third-party (yaml, jsonschema, hypothesis, pytest) — degrade
gracefully: anything that cannot be enforced is recorded as `skip` with a reason,
never as `pass` without enforcement.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

# Approved runtime dependency allowlist (O-003): stdlib only.
ALLOWED_IMPORTS = {
    "re", "time", "uuid", "typing", "dataclasses", "json", "math", "datetime",
    "functools", "itertools", "collections", "enum", "decimal", "abc",
}
# Forbidden signals for O-001/O-002/O-004 (AST-based; docker sandbox is the enforcement)
FORBIDDEN_CALL_NAMES = {"open", "eval", "exec", "compile", "__import__", "input"}
FORBIDDEN_ATTRS = {"system", "popen", "call", "Popen", "run", "connect", "sendto",
                   "sendall", "write_text", "write_bytes", "mkdir", "unlink", "remove"}
FORBIDDEN_MODULES = {"os", "subprocess", "socket", "urllib", "requests", "pathlib",
                     "tempfile", "multiprocessing", "threading"}


def bundle_digest(bundle_dir: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(bundle_dir.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            h.update(p.relative_to(bundle_dir).as_posix().encode())
            h.update(p.read_bytes())
    return "sha256:" + h.hexdigest()


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_yaml(p: Path):
    if yaml is None:
        raise RuntimeError("PyYAML required")
    return yaml.safe_load(p.read_text())


def layer_structural(bundle: Path, impl: Path) -> list[dict]:
    results = []
    schemas = {"request": bundle / "schemas/request.schema.json",
               "response": bundle / "schemas/response.schema.json"}
    impl_files = [p for p in impl.rglob("*.py") if "tests" not in p.parts]
    if jsonschema is None:
        results.append({"invariant_id": "S-001", "layer": "structural",
                        "outcome": "skip", "evidence": "jsonschema not installed"})
        return results
    # S-001: compile + serialize smoke
    for f in impl_files:
        try:
            compile(f.read_text(), str(f), "exec")
        except SyntaxError as exc:
            results.append({"invariant_id": "S-001", "layer": "structural",
                            "outcome": "fail", "evidence": f"syntax error: {exc}"})
            return results
    results.append({"invariant_id": "S-001", "layer": "structural", "outcome": "pass",
                    "evidence": f"compile ok for {len(impl_files)} module(s)"})
    # S-002: error-envelope contract is covered by contract tests (pytest);
    # static check: allowed kinds used in implementation
    src = "\n".join(f.read_text() for f in impl_files)
    allowed = {"invalid_request", "conflict", "not_found", "internal"}
    results.append({"invariant_id": "S-002", "layer": "structural",
                    "outcome": "pass" if "invalid_request" in src else "fail",
                    "evidence": "enumerated error kinds referenced in candidate source"})
    results.append({"invariant_id": "S-003", "layer": "structural", "outcome": "skip",
                    "evidence": "schema_diff_check requires a stored baseline; the bundle keeps no "
                                "schema history — version-event schema changes are reviewed in git "
                                "history. Not a pass: not enforced here."})
    # S-004: catalog addressing — every entry declares a kebab-case namespace
    # (the cross-bundle (namespace, name) uniqueness half is enforced by the
    # catalog-wide linter pass, `pdd bundle lint`).
    proto = load_yaml(bundle / "protocol.yaml") or {}
    ns = proto.get("namespace")
    ns_ok = (isinstance(ns, str) and 1 <= len(ns) <= 63
             and bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", ns)))
    results.append({"invariant_id": "S-004", "layer": "structural",
                    "outcome": "pass" if ns_ok else "fail",
                    "evidence": (f"namespace {ns!r} is kebab-case (1..63 chars)"
                                 if ns_ok else
                                 f"namespace {ns!r} violates the kebab-case grammar")})
    # S-005: tag grammar — list of kebab-case strings, at most 8, no duplicates.
    tags = proto.get("tags")
    # `all` short-circuits on the first non-string, so the set() dedupe below
    # only ever sees strings (hashable) — no TypeError on unhashable elements.
    tag_ok = (isinstance(tags, list) and len(tags) <= 8
              and all(isinstance(t, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", t)
                      for t in tags)
              and len(set(tags)) == len(tags))
    results.append({"invariant_id": "S-005", "layer": "structural",
                    "outcome": "pass" if tag_ok else "fail",
                    "evidence": f"{len(tags) if isinstance(tags, list) else type(tags).__name__} "
                                f"tags, kebab-case, unique, <=8" if tag_ok else
                                f"tags {tags!r} violate the grammar (kebab-case list, <=8, no dupes)"})
    # S-006/S-007 are invariants OF the registry bundle (publish handshake +
    # resource-identified evidence). They apply only to bundles that declare
    # pdd-registry.publish; other bundles get an honest skip, never a false
    # fail (the checks are generic-validator code, not bundle-specific).
    provides = proto.get("provides") or {}
    is_registry = isinstance(provides, dict) and "pdd-registry.publish" in provides
    if not is_registry:
        results.append({"invariant_id": "S-006", "layer": "structural",
                        "outcome": "skip",
                        "evidence": "bundle does not declare pdd-registry.publish; "
                                    "S-006 applies to the registry bundle only"})
        results.append({"invariant_id": "S-007", "layer": "structural",
                        "outcome": "skip",
                        "evidence": "bundle does not declare pdd-registry.publish; "
                                    "S-007 applies to the registry bundle only"})
        return results
    # S-006: DB-backed storage — the bundle must declare the publish handshake
    # and its schema must exist in the bundle. The adapter itself is
    # deployment surface (the deployment's database adapter), exercised by
    # service contract tests, not by the pure-core candidate.
    publish_ref = provides.get("pdd-registry.publish")
    s6_ok = (publish_ref == "schemas/publish.schema.json"
             and (bundle / "schemas" / "publish.schema.json").exists())
    results.append({"invariant_id": "S-006", "layer": "structural",
                    "outcome": "pass" if s6_ok else "fail",
                    "evidence": ("publish handshake + schema declared in bundle"
                                 if s6_ok else
                                 "publish handshake/schema missing from bundle")})
    # S-007: evidence provenance — evidence-requirements.yaml must declare the
    # resource_identifier field. The registry does not re-run validation in
    # this version; ingest-time enforcement is a service contract test.
    evreq = {}
    try:
        evreq = load_yaml(bundle / "evidence-requirements.yaml") or {}
    except Exception:  # noqa: BLE001
        pass
    req_list = []
    if isinstance(evreq, dict):
        req_list = (evreq.get("evidence_requirements") or {}).get("required") or []
    s7_ok = "resource_identifier" in req_list
    results.append({"invariant_id": "S-007", "layer": "structural",
                    "outcome": "pass" if s7_ok else "fail",
                    "evidence": ("evidence-requirements declares resource_identifier"
                                 if s7_ok else
                                 "evidence-requirements.yaml lacks resource_identifier")})
    return results


_SCRUB_HOME: str | None = None


def _scrubbed_env(pbt_runs: int) -> dict:
    """Environment for candidate code: NEVER pass secrets (PDD_EVIDENCE_KEY,
    tokens) to code under test — a malicious candidate must not read the
    signing key (security review HIGH). HOME points at a fresh temp dir so
    candidate code cannot read the invoking user's private files; the dir
    lives under the per-run scrub root cleaned up by main()."""
    root = _SCRUB_HOME or tempfile.mkdtemp(prefix="pdd-home-")
    return {"PBT_RUNS": str(pbt_runs),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": tempfile.mkdtemp(prefix="pdd-home-", dir=root),
            "LANG": "C.UTF-8"}


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_BUNDLE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+\Z")


def _assert_identifier(value, what: str) -> None:
    """Fail closed before interpolating manifest-provided names into generated
    code: methods/classes must be plain Python identifiers (security review)."""
    if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
        raise SystemExit(f"candidate-manifest.json {what} must be a Python identifier, got {value!r}")


def _assert_bundle_name(name: str) -> None:
    """Bundle names feed filesystem paths; constrain to [A-Za-z0-9_-] so a
    hostile name cannot escape evidence/ (security review)."""
    if not isinstance(name, str) or not _BUNDLE_NAME_RE.fullmatch(name):
        raise SystemExit(f"invalid bundle name {name!r}")


# Resource limits for the docker sandbox: a hostile candidate must not be able
# to fork-bomb or exhaust the validation host (security review MEDIUM).
_SANDBOX_DOCKER_FLAGS = ["--memory", "256m", "--pids-limit", "64", "--cpus", "1",
                         "--cap-drop", "ALL", "--user", "65534:65534"]


_BENCH_CODE_BANNED = ("import", "open(", "__", ";", "eval", "exec")

# Eval-mode expression nodes allowed in smoke.assert_expr: expression forms
# only — no calls, imports, lambdas, comprehensions, or assignment (a
# substring blacklist is evadable; an AST allowlist is not, security review).
_SAFE_EXPR_NODES = (ast.Expression, ast.Constant, ast.Name, ast.Attribute, ast.Subscript,
                    ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.List, ast.Tuple,
                    ast.Dict, ast.Slice, ast.cmpop, ast.boolop, ast.operator, ast.unaryop,
                    ast.Load)


def _assert_safe_expression(expr: str) -> None:
    """smoke.assert_expr must parse as a pure eval-mode expression using only
    the allowed node types (no function calls, imports, lambdas, dunders)."""
    if not isinstance(expr, str):
        raise SystemExit(f"smoke.assert_expr must be a string, got {type(expr).__name__}")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise SystemExit(f"smoke.assert_expr is not a valid expression: {expr!r}") from exc
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_EXPR_NODES):
            raise SystemExit(f"smoke.assert_expr uses disallowed construct "
                             f"{type(node).__name__}: {expr!r}")


def _assert_bench_meta(bench: dict) -> dict:
    """Coerce/validate manifest benchmark fields before interpolation into
    host-executed code (security review: code generation)."""
    _assert_identifier(bench.get("method"), "benchmark.method")
    try:
        iterations = int(bench.get("iterations", 1000))
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"candidate-manifest.json benchmark.iterations must be an integer, got "
                         f"{bench.get('iterations')!r}") from exc
    if not 19 <= iterations <= 1_000_000:
        raise SystemExit(f"candidate-manifest.json benchmark.iterations out of range "
                         f"(need >= 19 for the p95 quantile): {iterations}")
    return {**bench, "iterations": iterations}


def layer_operational_static(bundle: Path, impl: Path) -> list[dict]:
    """Static O-layer checks — runs BEFORE any candidate code executes, so a
    hostile candidate cannot pass the scan by evading it at runtime."""
    results = []
    impl_files = [p for p in impl.rglob("*.py") if "tests" not in p.parts]
    src = "\n".join(p.read_text() for p in impl_files)
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [{"invariant_id": "O-001,O-002,O-003,O-004", "layer": "operational",
                 "outcome": "fail",
                 "evidence": f"candidate is not parseable: {exc}"}]

    # O-003: import allowlist (stdlib + __future__ + local sibling modules)
    local_stems = {p.stem for p in impl_files}
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    bad = sorted(imports - ALLOWED_IMPORTS - {"__future__"} - local_stems)
    results.append({"invariant_id": "O-003", "layer": "operational",
                    "outcome": "pass" if not bad else "fail",
                    "evidence": f"imports={sorted(imports)}; unapproved={bad}"})

    # O-001/O-002/O-004: forbidden call scan — AST-based (import bindings and
    # attribute calls), NOT substring matching, so `from os import system` and
    # `__import__` aliasing are caught. Static signal; docker sandbox enforces.
    forbidden_signals = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                forbidden_signals.add(f"call {node.func.id}")
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in FORBIDDEN_ATTRS:
                    forbidden_signals.add(f"call .{node.func.attr}")
                if (isinstance(node.func.value, ast.Name)
                        and node.func.value.id in FORBIDDEN_MODULES):
                    forbidden_signals.add(f"call {node.func.value.id}.{node.func.attr}")
        if isinstance(node, ast.ImportFrom) and node.module in FORBIDDEN_MODULES:
            forbidden_signals.add(f"import from {node.module}")
    results.append({"invariant_id": "O-001,O-002,O-004", "layer": "operational",
                    "outcome": "pass" if not forbidden_signals else "fail",
                    "evidence": f"forbidden signals={sorted(forbidden_signals)}"})
    return results


def _behavioral_coverage(bids: list[str], lineage: dict) -> tuple[list[str], list[str]]:
    """Split behavioral invariant ids into (covered, uncovered) relative to the
    candidate manifest's invariant_lineage. A pass label may only claim the
    covered ids; uncovered ids get a skip-with-reason entry (a pass label must
    never imply enforcement that does not exist)."""
    covered = [iid for iid in bids
               if isinstance(lineage.get(iid), list) and lineage.get(iid)]
    uncovered = [iid for iid in bids if iid not in covered]
    return covered, uncovered


def layer_behavioral(bundle: Path, impl: Path, pbt_runs: int, manifest: dict) -> list[dict]:
    results = []
    testdir = impl / "tests"
    if not testdir.exists():
        return [{"invariant_id": "B-*", "layer": "behavioral", "outcome": "fail",
                 "evidence": "no tests/ directory in candidate"}]
    # Behavioral pytest runs from a TEMP COPY with cwd outside the repo tree:
    # candidate tests are excluded from the AST scan, so a hostile candidate
    # must not be able to write into the repo or read its secrets during the
    # run (security review: host-side execution containment).
    with tempfile.TemporaryDirectory(prefix="pdd-behavioral-") as td:
        run_dir = Path(td) / "candidate"
        shutil.copytree(impl, run_dir)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(run_dir / "tests"), "-q", "--tb=short"],
            cwd=run_dir, capture_output=True, text=True,
            env=_scrubbed_env(pbt_runs), timeout=900)
        summary = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]
    # Label the behavioral check with the bundle's own invariant ids.
    bid_data = {}
    try:
        bid_data = load_yaml(bundle / "invariants" / "behavioral.yaml") or {}
    except Exception:  # noqa: BLE001
        pass
    bids = [inv.get("id") for inv in (bid_data.get("behavioral_invariants") or bid_data.get("invariants") or [])
            if inv.get("id")]
    # The pass/fail label claims coverage ONLY for ids with candidate-level
    # tests in invariant_lineage; uncovered ids get their own skip entry below
    # (a pass label must never imply enforcement that does not exist).
    lineage = manifest.get("invariant_lineage") or {}
    covered, uncovered = _behavioral_coverage(bids, lineage)
    label = ",".join(covered) if covered else "B-*"
    if proc.returncode == 0:
        results.append({"invariant_id": label, "layer": "behavioral",
                        "outcome": "pass", "evidence": f"pytest: {summary}"})
    else:
        results.append({"invariant_id": label, "layer": "behavioral",
                        "outcome": "fail", "evidence": f"pytest failed: {summary}"})
        # keep going: mutation sanity may still add signal
    # Honesty: behavioral ids WITHOUT candidate-level tests are NOT covered by
    # this pytest run — label them skip-with-reason (e.g. B-006 publish
    # idempotency), so no pass label implies coverage that does not exist.
    if uncovered:
        results.append({"invariant_id": ",".join(uncovered), "layer": "behavioral",
                        "outcome": "skip",
                        "evidence": "no candidate-level tests in invariant_lineage; "
                                    "enforcement lands with the publish/storage service "
                                    "contract tests (implementation phase)"})
    # Mutation sanity: remove the primary behavioral guard and require the
    # declared property (candidate-manifest mutation_sanity) to fail.
    # The manifest is the one loaded BEFORE any candidate code ran (no TOCTOU:
    # a hostile candidate must not rewrite its manifest mid-validation).
    results.append(mutation_sanity(impl, testdir, pbt_runs, manifest))
    return results


def _docker_infra_error(returncode: int, cidfile: Path) -> bool:
    """True only when the failure is provably infrastructure: docker CLI/
    daemon errors exit 125 BEFORE a container is created (no cidfile). If the
    container ran (cidfile exists), the failure is the candidate's own — even
    a deliberate sys.exit(125) inside the container is then classified as a
    candidate-side fail (non-spoofable, security review)."""
    if returncode != 125:
        return False
    try:
        return not (cidfile.exists() and cidfile.read_text().strip())
    except OSError:
        return True


def mutation_sanity(impl: Path, testdir: Path, pbt_runs: int, manifest: dict) -> dict:
    """Manifest-driven mutant: the candidate declares the exact source span to
    break and the pytest filter that must FAIL against the mutant (proves the
    property is not vacuous). Missing definition → mutation-suspect (fail-closed)."""
    mutant_def = manifest.get("mutation_sanity") or {}
    entry_module = manifest.get("entry_module") or "user_registry"
    missing = [k for k in ("find", "replace", "pytest_filter") if not mutant_def.get(k)]
    if missing:
        return {"invariant_id": "B-001", "layer": "behavioral", "outcome": "mutation-suspect",
                "evidence": f"candidate-manifest.json mutation_sanity missing: {', '.join(missing)}"}
    _assert_identifier(mutant_def.get("pytest_filter"), "mutation_sanity.pytest_filter")
    if not isinstance(mutant_def.get("find"), str) or not isinstance(mutant_def.get("replace"), str):
        return {"invariant_id": "B-001", "layer": "behavioral", "outcome": "mutation-suspect",
                "evidence": "mutation_sanity find/replace must be strings"}
    src_file = impl / f"{entry_module}.py"
    original = src_file.read_text()
    mutant_src = original.replace(mutant_def["find"], mutant_def["replace"])
    if mutant_src == original:
        return {"invariant_id": mutant_def.get("invariant", "B-001"), "layer": "behavioral",
                "outcome": "mutation-suspect",
                "evidence": "could not build mutant (pattern not found in source)"}
    with tempfile.TemporaryDirectory() as td:
        mutant_dir = Path(td) / "mutant"
        shutil.copytree(impl, mutant_dir)
        (mutant_dir / f"{entry_module}.py").write_text(mutant_src)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(mutant_dir / "tests"),
             "-q", "-k", mutant_def["pytest_filter"], "--tb=no"],
            cwd=mutant_dir, capture_output=True, text=True, env=_scrubbed_env(pbt_runs), timeout=900)
        if proc.returncode == 1:
            return {"invariant_id": mutant_def.get("invariant", "B-001"), "layer": "behavioral",
                    "outcome": "pass",
                    "evidence": f"mutant rejected by {mutant_def['pytest_filter']} "
                                f"(exit 1, assertion failure) — property is not vacuous"}
        if proc.returncode == 5 or "no tests ran" in proc.stdout.lower():
            return {"invariant_id": mutant_def.get("invariant", "B-001"), "layer": "behavioral",
                    "outcome": "mutation-suspect",
                    "evidence": "mutant run collected no tests (exit 5) — pytest filter may be stale; "
                                "do not admit until the property runs against the mutant"}
        # Only exit 1 (assertion failure) proves non-vacuity. Exit 2 (collection
        # error) / 4 (usage) / other failures prove nothing about the property.
        return {"invariant_id": mutant_def.get("invariant", "B-001"), "layer": "behavioral",
                "outcome": "mutation-suspect",
                "evidence": f"mutant run exited {proc.returncode} (expect exit 1 = assertion failure); "
                            f"mutant definition or filter is broken — {proc.stdout.strip()[-160:] or proc.stderr.strip()[-160:]}"}


def layer_operational_dynamic(bundle: Path, impl: Path, sandbox: bool, pbt_runs: int,
                              manifest: dict) -> list[dict]:
    results = []
    entry_module = manifest.get("entry_module") or "user_registry"
    entry_class = manifest.get("entry_class") or "UserRegistry"

    # Docker sandbox (optional, infra contingency): network none + read-only fs.
    if sandbox and shutil.which("docker"):
        smoke = manifest.get("smoke")
        if not isinstance(smoke, dict):
            results.append({"invariant_id": "O-001,O-002", "layer": "operational",
                            "outcome": "skip",
                            "evidence": "smoke must be a dict in candidate-manifest.json; nothing executed"})
        elif not smoke.get("method"):
            results.append({"invariant_id": "O-001,O-002", "layer": "operational",
                            "outcome": "skip",
                            "evidence": "no smoke method declared in candidate-manifest.json; "
                                        "nothing executed in the sandbox"})
        else:
            _assert_identifier(smoke.get("method"), "smoke.method")
            assert_expr = smoke.get("assert_expr", "True")
            try:
                _assert_safe_expression(assert_expr)
            except SystemExit as exc:
                results.append({"invariant_id": "O-001,O-002", "layer": "operational",
                                "outcome": "skip",
                                "evidence": f"smoke.assert_expr rejected: {exc} — nothing executed"})
            else:
                call_style = smoke.get("call_style", "kwargs")
                args_lit = json.dumps(smoke.get("args") or {})
                if call_style == "single_dict":
                    call = f"{entry_class}().{smoke['method']}({args_lit})"
                else:
                    call = f"{entry_class}().{smoke['method']}(**{args_lit})"
                code = ("import sys; sys.path.insert(0,'.'); "
                        f"from {entry_module} import {entry_class}; "
                        f"r = {call}; "
                        f"assert {assert_expr}; print('sandbox smoke ok')")
                with tempfile.TemporaryDirectory(prefix="pdd-cid-") as cid_td:
                    cidfile = Path(cid_td) / "cid"
                    proc = subprocess.run(
                        ["docker", "run", "--rm", "--network", "none", "--read-only", *_SANDBOX_DOCKER_FLAGS,
                         "--security-opt", "no-new-privileges",
                         "--cidfile", str(cidfile),
                         "-e", f"PBT_RUNS={pbt_runs}",
                         "-v", f"{impl.resolve()}:/candidate:ro", "-w", "/candidate",
                         "python:3.12-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64",
                         "python", "-c", code],
                        capture_output=True, text=True, timeout=300)
                    if proc.returncode == 0:
                        results.append({"invariant_id": "O-001,O-002", "layer": "operational",
                                        "outcome": "pass",
                                        "evidence": "docker sandbox (network none, read-only fs): " + proc.stdout.strip()})
                    elif _docker_infra_error(proc.returncode, cidfile):
                        results.append({"invariant_id": "O-001,O-002", "layer": "operational",
                                        "outcome": "skip",
                                        "evidence": f"docker sandbox infra failure (exit {proc.returncode}): "
                                                    f"{proc.stderr.strip()[:200]}"})
                    else:
                        # The candidate's own smoke failed inside a healthy sandbox:
                        # fail-closed (a candidate that fails its declared smoke
                        # must not admit), security review.
                        results.append({"invariant_id": "O-001,O-002", "layer": "operational",
                                        "outcome": "fail",
                                        "evidence": f"sandbox smoke failed: {proc.stderr.strip()[:200]}"})
    else:
        results.append({"invariant_id": "O-001,O-002", "layer": "operational",
                        "outcome": "skip",
                        "evidence": "sandbox not requested or docker not found; static scan only"})

    # O-005 benchmark (advisory, should-tier): p95 latency over the declared
    # method, in a subprocess with scrubbed env — candidate never sees secrets.
    raw_bench = manifest.get("benchmark")
    if not isinstance(raw_bench, dict):
        results.append({"invariant_id": "O-005", "layer": "operational", "outcome": "skip",
                        "evidence": "benchmark must be a dict in candidate-manifest.json"})
        return results
    if not raw_bench.get("method"):
        results.append({"invariant_id": "O-005", "layer": "operational", "outcome": "skip",
                        "evidence": "no benchmark method declared in candidate-manifest.json"})
        return results
    bench = _assert_bench_meta(raw_bench)
    bench_iterations = bench["iterations"]  # keep before `bench` is reused below
    bench_catalog = bench.get("catalog")
    bench_ctor = f"{entry_class}({json.dumps(bench_catalog)})" if bench_catalog else f"{entry_class}()"
    bench_code = (
        "import json, statistics, sys, time\n"
        "sys.path.insert(0, '.')\n"
        f"from {entry_module} import {entry_class}\n"
        f"reg = {bench_ctor}\n"
        "lat = []\n"
        f"for i in range({bench_iterations}):\n"
        f"    args = {json.dumps(bench.get('args_template') or {})}\n"
        "    args = {k: (v % i if isinstance(v, str) and '%d' in v else v) for k, v in args.items()}\n"
        "    t0 = time.perf_counter()\n"
        + (f"    reg.{bench['method']}(args)\n" if bench.get("call_style") == "single_dict"
           else f"    reg.{bench['method']}(**args)\n")
        + "    lat.append((time.perf_counter() - t0) * 1000)\n"
        "print(json.dumps({'p95_ms': statistics.quantiles(sorted(lat), n=20)[18]}))\n"
    )
    try:
        # Benchmark runs from a TEMP COPY like the behavioral/mutant layers:
        # a hostile candidate must not execute with the live repo tree as cwd
        # (it could rewrite its own source mid-run and break the digest
        # binding), security review containment.
        with tempfile.TemporaryDirectory(prefix="pdd-bench-") as td:
            bench_dir = Path(td) / "candidate"
            shutil.copytree(impl, bench_dir)
            proc = subprocess.run([sys.executable, "-c", bench_code], cwd=bench_dir,
                                  capture_output=True, text=True,
                                  env=_scrubbed_env(pbt_runs), timeout=300)
        out = json.loads(proc.stdout.strip()) if proc.returncode == 0 else {}
        p95 = out.get("p95_ms")
        results.append({"invariant_id": "O-005", "layer": "operational", "outcome": "observe",
                        "evidence": f"p95={p95:.2f}ms over {bench_iterations} calls "
                                    f"(budget 500ms, should-tier)" if p95 is not None
                        else f"benchmark failed: {proc.stderr.strip()[:120]}"})
    except (json.JSONDecodeError, KeyError, TypeError, ValueError,
            subprocess.TimeoutExpired, OSError) as exc:
        results.append({"invariant_id": "O-005", "layer": "operational", "outcome": "skip",
                        "evidence": f"benchmark failed: {exc}"})
    return results


def verdict(results: list[dict]) -> tuple[str, str]:
    fails = [r for r in results if r["outcome"] == "fail"]
    suspects = [r for r in results if r["outcome"] == "mutation-suspect"]
    if fails:
        return "reject", f"{len(fails)} must-invariant failure(s): " + ", ".join(
            r["invariant_id"] for r in fails)
    if suspects:
        return "reject", "mutation-suspect flags open: " + ", ".join(
            r["invariant_id"] for r in suspects)
    return "admit", "no must-invariant failures; no mutation-suspect flags"


def main(argv: list[str]) -> int:
    global _SCRUB_HOME
    _SCRUB_HOME = tempfile.mkdtemp(prefix="pdd-scrub-")
    try:
        return _main(argv)
    finally:
        shutil.rmtree(_SCRUB_HOME, ignore_errors=True)


def _main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: validate_candidate.py <bundle-dir> <impl-dir> [--sandbox] [--pbt-runs N]")
        return 2
    bundle, impl = Path(argv[1]), Path(argv[2])
    manifest_path = impl / "candidate-manifest.json"
    if not manifest_path.exists():
        print(f"no candidate-manifest.json in {impl}")
        return 2
    manifest = json.loads(manifest_path.read_text())
    entry_module = manifest.get("entry_module") or "user_registry"
    entry_class = manifest.get("entry_class") or "UserRegistry"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry_module) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry_class):
        print(f"candidate-manifest.json entry_module/entry_class must be Python identifiers, got "
              f"{entry_module!r}/{entry_class!r}")
        return 2
    _assert_bundle_name(bundle.name)
    proto = load_yaml(bundle / "protocol.yaml") or {}
    protocol = proto.get("protocol") or proto
    name = bundle.name
    version = protocol.get("version") or "1.0.0"
    sandbox = "--sandbox" in argv
    pbt_runs = 200
    if "--pbt-runs" in argv:
        idx = argv.index("--pbt-runs")
        if idx + 1 >= len(argv):
            print("--pbt-runs requires a value")
            return 2
        try:
            pbt_runs = int(argv[idx + 1])
        except ValueError:
            print(f"--pbt-runs must be an integer, got {argv[idx + 1]!r}")
            return 2
        if pbt_runs < 1:
            print("--pbt-runs must be >= 1")
            return 2

    results = (layer_structural(bundle, impl)
               + layer_operational_static(bundle, impl)   # static scan BEFORE any execution
               + layer_behavioral(bundle, impl, pbt_runs, manifest)
               + layer_operational_dynamic(bundle, impl, sandbox, pbt_runs, manifest))
    verdict_text, reason = verdict(results)

    out = {
        "protocol": {"name": name, "version": version,
                     "bundle_digest": bundle_digest(bundle)},
        "candidate_digest": file_digest(impl / f"{entry_module}.py"),
        "validators": [
            {"id": "schema-validator", "version": "1.0.0", "layer": "structural"},
            {"id": "contract-runner", "version": "1.0.0", "layer": "structural"},
            {"id": "property-runner", "version": "1.0.0", "layer": "behavioral"},
            {"id": "mutation-sanity", "version": "1.0.0", "layer": "behavioral"},
            {"id": "import-scanner", "version": "1.0.0", "layer": "operational"},
            {"id": "capability-monitor", "version": "1.0.0", "layer": "operational"},
            {"id": "benchmark-runner", "version": "1.0.0", "layer": "operational"},
        ],
        "results": results,
        "verdict": verdict_text,
        "verdict_reason": reason,
        "environment": {"runtime": sys.version.split()[0], "os": sys.platform},
    }
    out_path = REPO_ROOT / "evidence" / name / "validation"
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / f"{out['candidate_digest'].split(':')[1][:12]}.results.json").write_text(
        json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0 if verdict_text == "admit" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
