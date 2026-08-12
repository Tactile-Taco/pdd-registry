"""Memory sync tests: render functions (pure) and the MemFS write path
(dry-run; the live ssh path is exercised by the integration deployment)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from memory_plane.memory import (  # noqa: E402
    render_memories, render_process_skills, sync_memfs)


def test_render_memories_sections_and_entries():
    md = render_memories(
        [{"key": "free models first", "value": "Use free models for cheap passes."},
         {"key": "deepseek for synthesis", "value": "Hard reasoning goes deepseek."}],
        period="2026-08-12")
    assert "## Memories (2026-08-12)" in md
    assert "**free models first**: Use free models for cheap passes." in md
    assert "**deepseek for synthesis**" in md
    assert md.startswith("---")


def test_render_memories_empty_and_newline_collapsed():
    assert "## Memories" in render_memories([])
    md = render_memories([{"key": "k", "value": "line1\nline2\nline3"}])
    assert "line1 line2 line3" in md
    assert "\nline2" not in md


def test_render_process_skills_sections():
    md = render_process_skills([
        {"proposal_id": "ps-1", "description": "Add a review checklist",
         "reasoning": "votes were inconsistent",
         "body": "Always verify grounding before voting."},
    ])
    assert "### Add a review checklist" in md
    assert "Always verify grounding before voting." in md
    assert "Reasoning: votes were inconsistent" in md
    assert "must NOT be synced" in md  # process skills stay in memory


def test_sync_memfs_dry_run_writes_nothing():
    written = sync_memfs("m6", "agent-meta",
                         {"memories.md": render_memories([])},
                         dry_run=True)
    assert written == ["memories.md (dry-run)"]


def test_sync_memfs_rejects_unsafe_ids_and_names():
    with pytest.raises(ValueError):
        sync_memfs("m6", "../escape", {"memories.md": "x"}, dry_run=True)
    with pytest.raises(ValueError):
        sync_memfs("m6", "agent-meta", {"a/b.md": "x"}, dry_run=True)
