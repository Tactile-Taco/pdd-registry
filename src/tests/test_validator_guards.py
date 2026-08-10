"""Unit tests for the Validation Engine's security guards
(validators/validate_candidate.py + scripts/pdd.py hardening).

Runs inside the service suite (`pytest src/tests`) without the evidence key.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "validators"))

import validate_candidate as vc  # noqa: E402


def _raises_systemexit(fn, *args):
    try:
        fn(*args)
    except SystemExit:
        return True
    return False


# --- identifier gate --------------------------------------------------------


def test_assert_identifier_accepts_plain_identifiers():
    for ok in ("foo", "foo_bar", "_x", "Registry"):
        vc._assert_identifier(ok, "test")  # must not raise


def test_assert_identifier_rejects_hostile_names():
    for bad in ("foo\n", "a.b", "../x", "import os", "", "a-b", "1abc"):
        assert _raises_systemexit(vc._assert_identifier, bad, "test"), bad


# --- bundle-name gate --------------------------------------------------------


def test_assert_bundle_name_rejects_paths():
    for bad in ("..", "../evil", "a/b", "a\\b", "a\n", "", "a b"):
        assert _raises_systemexit(vc._assert_bundle_name, bad), bad


def test_assert_bundle_name_accepts_slugs():
    for ok in ("user-registry", "pdd-registry", "x", "a_b_1"):
        vc._assert_bundle_name(ok)


# --- benchmark meta gate -----------------------------------------------------


def test_assert_bench_meta_iterations_bounds():
    for bad in (1, 5, 18, 1_000_001, -19):
        assert _raises_systemexit(vc._assert_bench_meta, {"method": "search", "iterations": bad}), bad


def test_assert_bench_meta_coerces_and_accepts():
    b = vc._assert_bench_meta({"method": "search", "iterations": "100", "catalog": []})
    assert b["iterations"] == 100


def test_assert_bench_meta_rejects_bad_method_and_type():
    assert _raises_systemexit(vc._assert_bench_meta, {"method": "search()", "iterations": 100})
    assert _raises_systemexit(vc._assert_bench_meta, {"method": "a.b", "iterations": 100})
    assert _raises_systemexit(vc._assert_bench_meta, {"method": "search", "iterations": "abc"})


# --- docker infra classification ---------------------------------------------


def test_docker_infra_error_cidfile_semantics(tmp_path):
    no_cid = tmp_path / "missing"
    with_cid = tmp_path / "cid"
    with_cid.write_text("abc123\n")
    assert vc._docker_infra_error(125, no_cid) is True     # docker CLI failed, no container
    assert vc._docker_infra_error(125, with_cid) is False  # container ran: candidate-side
    assert vc._docker_infra_error(1, no_cid) is False      # non-125: candidate-side
    assert vc._docker_infra_error(0, no_cid) is False


# --- smoke assert_expr AST allowlist -----------------------------------------


def test_assert_safe_expression_accepts_benign_forms():
    for ok in ("r['ok'] is True", "r['count'] >= 0", "r.ok", "1 < 2 and r['ok']",
               "not r['error']", "r['results'][0]['score'] > 3", "r.__class__"):
        vc._assert_safe_expression(ok)  # must not raise


def test_assert_safe_expression_rejects_calls_imports_and_dunders():
    for bad in ("sys.modules['os'].system('id')", "open('/etc/passwd')", "f()",
                "__import__('os')", "lambda: 1", "[x for x in r]", "r.__class__()",
                "exec('1')", "r; open('x')", ""):
        assert _raises_systemexit(vc._assert_safe_expression, bad), bad


def test_assert_safe_expression_rejects_non_string():
    assert _raises_systemexit(vc._assert_safe_expression, 42)


# --- pdd.py duplicate gate (subprocess boundary) ------------------------------

def test_pdd_assert_safe_expression_matches_validator():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import pdd as pdd_cli  # noqa: E402

    for ok in ("r['ok'] is True", "r['count'] >= 0"):
        pdd_cli._assert_safe_expression(ok)
    for bad in ("sys.modules['os'].system('id')", "open('/etc/passwd')", "f()"):
        assert _raises_systemexit(pdd_cli._assert_safe_expression, bad), bad


# --- language-agnostic candidate harness -------------------------------------

def test_test_command_shape_validation():
    assert vc._test_command({"test_command": ["sh", "tests/run.sh"]}) == ["sh", "tests/run.sh"]
    assert vc._test_command({}) is None
    assert _raises_systemexit(vc._test_command, {"test_command": "sh x"})       # not a list
    assert _raises_systemexit(vc._test_command, {"test_command": []})           # empty
    assert _raises_systemexit(vc._test_command, {"test_command": ["sh", "a\nb"]})  # newline


def test_behavioral_layer_runs_test_command(tmp_path):
    impl = tmp_path / "cand"
    (impl / "tests").mkdir(parents=True)
    (impl / "tests" / "run.sh").write_text(
        "#!/bin/sh\necho custom-harness-ok\nexit 0\n")
    manifest = {"language": "shell", "invariant_lineage": {},
                "test_command": ["sh", "tests/run.sh"]}
    results = vc.layer_behavioral(REPO_ROOT / "pdd-bundles" / "taxonomy-web-service",
                                  impl, 10, manifest)
    passes = [r for r in results if r["outcome"] == "pass"]
    assert any("custom-harness-ok" in r["evidence"] and "sh tests/run.sh:" in r["evidence"]
               for r in passes), results


def test_behavioral_default_python_path_keeps_pytest_label(tmp_path):
    # Back-compat regression guard: the default python path must still be
    # labeled "pytest:" in its evidence (prior evidence files rely on it).
    impl = tmp_path / "cand"
    (impl / "tests").mkdir(parents=True)
    (impl / "tests" / "test_x.py").write_text("def test_ok():\n    assert True\n")
    results = vc.layer_behavioral(REPO_ROOT / "pdd-bundles" / "taxonomy-web-service",
                                  impl, 10, {"invariant_lineage": {}})
    passes = [r for r in results if r["outcome"] == "pass"]
    assert any(r["evidence"].startswith("pytest:") for r in passes), results


def test_behavioral_layer_reports_failed_test_command(tmp_path):
    impl = tmp_path / "cand"
    (impl / "tests").mkdir(parents=True)
    (impl / "tests" / "run.sh").write_text("#!/bin/sh\necho boom; exit 3\n")
    manifest = {"language": "shell", "invariant_lineage": {},
                "test_command": ["sh", "tests/run.sh"]}
    results = vc.layer_behavioral(REPO_ROOT / "pdd-bundles" / "taxonomy-web-service",
                                  impl, 10, manifest)
    assert any(r["outcome"] == "fail" for r in results), results


def test_operational_static_skips_non_python():
    results = vc.layer_operational_static(
        REPO_ROOT / "pdd-bundles" / "taxonomy-web-service",
        REPO_ROOT / "implementations" / "taxonomy-web-service" / "shell-stdlib",
        {"language": "shell"})
    assert results and results[0]["outcome"] == "skip"
    assert "no Python AST surface" in results[0]["evidence"]


def test_mutation_sanity_skips_non_python(tmp_path):
    impl = tmp_path / "cand"
    result = vc.mutation_sanity(impl, impl / "tests", 10,
                                {"language": "shell", "mutation_sanity": {}})
    assert result["outcome"] == "skip"
    assert "Python-shaped" in result["evidence"]


def test_candidate_digest_confinement_and_empty_list(tmp_path):
    (tmp_path / "a.sh").write_text("one\n")
    # traversal: ".." must fail closed, never hash host files
    assert _raises_systemexit(vc._candidate_digest, tmp_path,
                              {"files": ["../outside.sh"]}, "entry")
    assert _raises_systemexit(vc._candidate_digest, tmp_path,
                              {"files": ["/etc/hostname"]}, "entry")
    # missing declared file -> fail closed
    assert _raises_systemexit(vc._candidate_digest, tmp_path,
                              {"files": ["a.sh", "nope.sh"]}, "entry")
    # empty list == absent -> python fallback or clean error
    (tmp_path / "entry.py").write_text("x = 1\n")
    assert vc._candidate_digest(tmp_path, {"files": []}, "entry").startswith("sha256:")
    assert _raises_systemexit(vc._candidate_digest, tmp_path, {"files": []}, None)


def test_candidate_digest_from_manifest_files(tmp_path):
    (tmp_path / "a.sh").write_text("one\n")
    (tmp_path / "b.sh").write_text("two\n")
    manifest = {"files": ["a.sh", "b.sh"]}
    d = vc._candidate_digest(tmp_path, manifest, "entry")
    # order-sensitive, content-based: touching b.sh changes the digest
    (tmp_path / "b.sh").write_text("two!\n")
    assert vc._candidate_digest(tmp_path, manifest, "entry") != d
    # back-compat fallback: no files list -> entry module digest
    (tmp_path / "entry.py").write_text("x = 1\n")
    assert vc._candidate_digest(tmp_path, {}, "entry").startswith("sha256:")
