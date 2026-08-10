"""Contract tests for the taxonomy/web-service vocabulary validator."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from web_service_vocab import validate_against, VOCABULARY, TEMPLATE_IDS  # noqa: E402


def test_B001_known_components_pass():
    assert validate_against({"api": "surface", "database": "state",
                             "observability": "telemetry"}) == []


def test_B001_unknown_component_reported():
    errors = validate_against({"apii": "typo", "api": "ok"})
    assert "unknown component: apii" in errors
    assert len(errors) == 1


def test_B001_unknown_template_ref_reported():
    errors = validate_against({"api": "x"}, template_refs=["S-001", "S-999"])
    assert "unknown template reference: S-999" in errors
    assert len(errors) == 1


def test_B001_non_dict_components_fail():
    assert validate_against(None) == ["components must be a dict"]
    assert validate_against(["api"]) == ["components must be a dict"]


def test_B001_deterministic_and_non_mutating():
    components = {"api": "x", "zzz": "bad"}
    first = validate_against(components)
    second = validate_against(components)
    assert first == second and components == {"api": "x", "zzz": "bad"}


def test_vocabulary_shape():
    assert len(VOCABULARY) == 12
    assert len(TEMPLATE_IDS) == 5
