"""Contract tests for the taxonomy/ai-agent vocabulary validator."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_agent_vocab import validate_against, VOCABULARY, TEMPLATE_IDS  # noqa: E402


def test_B001_known_components_pass():
    assert validate_against({"tool-runtime": "exec", "memory": "state",
                             "guardrails": "safety"}) == []


def test_B001_unknown_component_reported():
    errors = validate_against({"toolruntime": "typo", "memory": "ok"})
    assert "unknown component: toolruntime" in errors
    assert len(errors) == 1


def test_B001_unknown_template_ref_reported():
    errors = validate_against({"memory": "x"}, template_refs=["S-003", "S-900"])
    assert "unknown template reference: S-900" in errors


def test_B001_non_dict_components_fail():
    assert validate_against([]) == ["components must be a dict"]


def test_B001_deterministic_and_non_mutating():
    components = {"memory": "x", "wat": "bad"}
    first = validate_against(components)
    second = validate_against(components)
    assert first == second and components == {"memory": "x", "wat": "bad"}


def test_vocabulary_shape():
    assert len(VOCABULARY) == 8
    assert len(TEMPLATE_IDS) == 5
