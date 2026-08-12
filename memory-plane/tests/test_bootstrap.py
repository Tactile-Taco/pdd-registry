"""Bootstrap safety tests: agent id/name constraints and registry shape."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_plane.agent_defs import AGENT_DEFS, LETTA_MODEL  # noqa: E402
from bootstrap import MODEL_SETTINGS, SAFE_NAME_RE, registry_entry  # noqa: E402


def test_all_agent_ids_and_names_are_safe():
    for a in AGENT_DEFS:
        assert SAFE_NAME_RE.match(a["id"]), a["id"]
        assert SAFE_NAME_RE.match(a["name"]), a["name"]


def test_safe_name_regex_rejects_hostile_input():
    for bad in ("../../x", "a b", "a/b", "a\\b", "x;rm -rf /", "x$(id)", "x`id`"):
        assert not SAFE_NAME_RE.match(bad), bad


def test_registry_entry_shape_matches_server_template():
    a = AGENT_DEFS[0]
    e = registry_entry(a)
    assert set(e) == {"id", "name", "description", "model", "model_settings",
                      "system", "tags"}
    assert e["model"] == LETTA_MODEL
    assert e["model_settings"] == MODEL_SETTINGS
    assert e["id"] == a["id"]
    assert isinstance(e["tags"], list)
