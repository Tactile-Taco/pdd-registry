"""Tests for per-model baseline helpers (normalize_model / detect_model)."""

from baselines import BaselineStore, detect_model, normalize_model


def test_normalize_model_collapses_provider_qualifiers():
    assert normalize_model("deepseek/deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_model("deepseek-deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_model("bifrost-deepseek/deepseek/deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_model("deepseek-flash") == "deepseek-flash"  # distinct naming era
    assert normalize_model("kimi-k2.6") == "kimi-k2.6"
    assert normalize_model("google/gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"
    assert normalize_model("") == "unknown"
    assert normalize_model(None) == "unknown"


def test_detect_model_reasonix_filename():
    assert detect_model("reasonix", "20260806-102851.043852311-deepseek-deepseek-v4-flash.events.jsonl", []) == "deepseek-v4-flash"
    assert detect_model("reasonix", "20260805-233727.257784963-deepseek-flash-recovery-b853fd56f440a61a.events.jsonl", []) == "deepseek-flash"
    assert detect_model("reasonix", "20260805-135303.825912714-deepseek-v4-flash.events.jsonl", []) == "deepseek-v4-flash"
    assert detect_model("reasonix", "sa_20260811_185108_000000000_08c455a53ed8.events.jsonl", []) == "unknown"
    assert detect_model("reasonix", "20260809-130531.585938110-session.events.jsonl", []) == "unknown"


def test_detect_model_hermes_omp_claude():
    lines = ['{"role": "assistant", "content": "x", "model": "kimi-k2.6"}']
    assert detect_model("hermes", "f.jsonl", lines) == "kimi-k2.6"
    omp = ['{"type": "title", "title": ""}',
           '{"type": "model_change", "model": "bifrost-deepseek/deepseek/deepseek-v4-flash"}']
    assert detect_model("omp", "f.jsonl", omp) == "deepseek-v4-flash"
    claude = ['{"type": "user", "message": {"id": "m1", "model": "claude-sonnet-4-5", "content": "hi"}}']
    assert detect_model("claude", "f.jsonl", claude) == "claude-sonnet-4-5"
    assert detect_model("kimi", "f.jsonl", ["{\"role\":\"user\"}"]) == "unknown"


def test_baseline_store_accumulate_merge_stats(tmp_path):
    p = tmp_path / "baselines.json"
    store = BaselineStore(str(p))
    store.add_sample("deepseek-v4-flash", "uncertainty_density", 1.0)
    store.add_sample("deepseek-v4-flash", "uncertainty_density", 3.0)
    store.merge("deepseek-v4-flash", "uncertainty_density", 2, 8.0, 40.0,
                sources={"reasonix": 2})
    st = store.stats("deepseek-v4-flash", "uncertainty_density")
    # 4 samples: 1,3 (add_sample) + 2,6 (merge sum=8, sum_sq=40) -> mean 3.0, var 3.5
    assert st["n"] == 4
    assert abs(st["mean"] - 3.0) < 1e-9
    assert abs(st["std"] - 3.5 ** 0.5) < 1e-9
    assert store.sources_for("deepseek-v4-flash") == {"reasonix": 2}
    assert store.stats("nope", "uncertainty_density") is None
    assert store.deviation("deepseek-v4-flash", "uncertainty_density", 5.0) is not None
    assert store.deviation("nope", "uncertainty_density", 1.0) is None
