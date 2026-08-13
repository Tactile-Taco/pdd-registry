"""Tests for router JSON-shape robustness (_parse_json_object + retry)."""

import pytest

from router import ModelRouter, RouterError, StubRouter, _parse_json_object


class _ScriptedCall:
    """ModelRouter whose _call returns scripted responses (no network)."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def _call(self, model, messages, max_tokens):
        t = self.texts.pop(0) if self.texts else "{}"
        self.calls.append((model, messages))
        return {"text": t, "model": model, "tokens_in": 10, "tokens_out": 5,
                "cost_usd": 0.0}


def _router_with_script(texts):
    scr = _ScriptedCall(texts)
    r = ModelRouter(model="m", base_url="http://x", api_key="k", ledger=None)
    r._call = scr._call  # monkeypatch the network call
    r._scripted = scr
    return r


def test_parse_json_object_dict_and_array():
    obj, ok = _parse_json_object('{"a": 1}')
    assert ok is True and obj == {"a": 1}
    obj, ok = _parse_json_object('[{"a": 1}]')   # array is NOT an object
    assert ok is False and obj is None
    obj, ok = _parse_json_object('not json')
    assert ok is False and obj is None
    obj, ok = _parse_json_object('```json\n{"a": 2}\n```')
    assert ok is True and obj == {"a": 2}


def test_stub_complete_json_rejects_array():
    stub = StubRouter(replies={"task": '[{"a": 1}]'})
    with pytest.raises(RouterError, match="non-object"):
        stub.complete_json("do the task")


def test_modelrouter_complete_json_retries_once_on_array():
    # first response is an array, second a dict -> recovers within one attempt
    r = _router_with_script(['[{"oops": 1}]', '{"narrative": "ok", "findings": []}'])
    out = r.complete_json("analyze")
    assert out == {"narrative": "ok", "findings": []}
    assert len(r._scripted.calls) == 2  # one retry


def test_modelrouter_complete_json_raises_after_retry():
    r = _router_with_script(['[1]', '[2]'])
    with pytest.raises(RouterError, match="non-object JSON after retry"):
        r.complete_json("analyze")


def test_modelrouter_complete_json_passes_dict_through():
    r = _router_with_script(['{"topics": [], "transitions": []}'])
    out = r.complete_json("extract")
    assert out == {"topics": [], "transitions": []}
    assert len(r._scripted.calls) == 1  # no retry needed
