"""Unit tests for the Validation Engine's security guards (pdd.engine).

The engine lives in the pdd-cli package (single source — the linter, engine,
and evidence chain were folded out of this repo into it). Runs inside the
service suite (`pytest src/tests`) without the evidence key.
"""

from pdd import engine as vc  # noqa: E402


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


# --- single-source gate (no duplicated guard in the CLI path) -----------------

def test_cli_smoke_path_uses_the_engine_guard():
    """The old pdd.py duplicated _assert_safe_expression across the subprocess
    boundary; the CLI now imports the engine's guard — verify the workflow
    run command resolves to the very same function object."""
    import inspect
    from pdd.workflow import run as run_mod
    src = inspect.getsource(run_mod)
    assert "from ..engine import _assert_safe_expression" in src
    # and the engine's guard still bites on the hostile forms
    for bad in ("__import__('os')", "open('/etc/passwd')", "f()"):
        assert _raises_systemexit(vc._assert_safe_expression, bad), bad
