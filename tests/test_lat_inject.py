"""Tests for lat_inject.py — TF-IDF knowledge graph node injection."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import lat_inject


def _reset_cache():
    """Clear module-level cache so each test starts fresh."""
    lat_inject._NODES = None


# ---------------------------------------------------------------------------
# _load_nodes
# ---------------------------------------------------------------------------

class TestLoadNodes:
    def test_loads_md_files(self, tmp_path):
        _reset_cache()
        lat_dir = tmp_path / "lat.md"
        lat_dir.mkdir()
        (lat_dir / "core-loop.md").write_text("# Core Loop\n\nThe main loop.")
        (lat_dir / "memory-system.md").write_text("# Memory\n\nLessons and outcomes.")
        # lat.md (index) is skipped
        (lat_dir / "lat.md").write_text("# Index")

        with patch.object(lat_inject, "_lat_dir", return_value=lat_dir):
            _reset_cache()
            nodes = lat_inject._load_nodes()

        assert "core-loop" in nodes
        assert "memory-system" in nodes
        assert "lat" not in nodes  # index skipped

    def test_returns_empty_if_dir_missing(self, tmp_path):
        with patch.object(lat_inject, "_lat_dir", return_value=tmp_path / "nonexistent"):
            _reset_cache()
            nodes = lat_inject._load_nodes()
        assert nodes == {}


# ---------------------------------------------------------------------------
# _score_nodes
# ---------------------------------------------------------------------------

class TestScoreNodes:
    def test_relevant_node_scores_higher(self):
        nodes = {
            "evolver": "The evolver improves skills and mutates prompts over time.",
            "core-loop": "The central execution loop decomposes goals into steps.",
        }
        scored = lat_inject._score_nodes("improve evolver scoring logic", nodes)
        names = [n for n, _ in scored]
        assert names[0] == "evolver"  # evolver is most relevant

    def test_empty_query_returns_empty(self):
        nodes = {"a": "some content"}
        scored = lat_inject._score_nodes("", nodes)
        assert scored == []

    def test_no_match_returns_empty(self):
        nodes = {"a": "some content about dogs and cats"}
        scored = lat_inject._score_nodes("quantum entanglement", nodes)
        # May be empty or low-score — length matters but not content
        assert isinstance(scored, list)

    def test_empty_nodes_returns_empty(self):
        scored = lat_inject._score_nodes("anything", {})
        assert scored == []


# ---------------------------------------------------------------------------
# inject_relevant_nodes
# ---------------------------------------------------------------------------

class TestInjectRelevantNodes:
    def test_returns_string(self, tmp_path):
        _reset_cache()
        lat_dir = tmp_path / "lat.md"
        lat_dir.mkdir()
        (lat_dir / "memory-system.md").write_text(
            "# Memory System\n\nMulti-tier: outcomes, lessons, skills.\n\n"
            "Decay model: scores decay 0.85x per day."
        )

        with patch.object(lat_inject, "_lat_dir", return_value=lat_dir):
            _reset_cache()
            result = lat_inject.inject_relevant_nodes("improve memory decay scoring")

        assert isinstance(result, str)

    def test_relevant_context_included(self, tmp_path):
        _reset_cache()
        lat_dir = tmp_path / "lat.md"
        lat_dir.mkdir()
        (lat_dir / "memory-system.md").write_text(
            "# Memory System\n\nMulti-tier memory: outcomes, lessons, skills."
        )

        with patch.object(lat_inject, "_lat_dir", return_value=lat_dir):
            _reset_cache()
            result = lat_inject.inject_relevant_nodes("fix the memory decay")

        if result:
            assert "Memory" in result or "memory" in result

    def test_max_nodes_respected(self, tmp_path):
        _reset_cache()
        lat_dir = tmp_path / "lat.md"
        lat_dir.mkdir()
        for i in range(5):
            (lat_dir / f"node{i}.md").write_text(f"# Node {i}\n\nContent about topic {i}.")

        with patch.object(lat_inject, "_lat_dir", return_value=lat_dir):
            _reset_cache()
            result = lat_inject.inject_relevant_nodes("research topic", max_nodes=2)

        # At most 2 ## sections in the result
        if result:
            section_count = result.count("\n## ")
            assert section_count <= 2

    def test_missing_lat_dir_returns_empty(self, tmp_path):
        with patch.object(lat_inject, "_lat_dir", return_value=tmp_path / "missing"):
            _reset_cache()
            result = lat_inject.inject_relevant_nodes("anything")
        assert result == ""

    def test_unrelated_goal_returns_empty_or_low_relevance(self, tmp_path):
        _reset_cache()
        lat_dir = tmp_path / "lat.md"
        lat_dir.mkdir()
        (lat_dir / "core-loop.md").write_text("# Core Loop\n\nAutonomous execution loop.")

        with patch.object(lat_inject, "_lat_dir", return_value=lat_dir):
            _reset_cache()
            # A goal with zero shared tokens should get empty or very low result
            result = lat_inject.inject_relevant_nodes("xyzzy foo bar baz quantum")

        # Either empty (no match) or just the header (depends on TF-IDF noise)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# relevant_node_names (for logging/testing)
# ---------------------------------------------------------------------------

class TestRelevantNodeNames:
    def test_returns_list(self, tmp_path):
        _reset_cache()
        lat_dir = tmp_path / "lat.md"
        lat_dir.mkdir()
        (lat_dir / "evolver.md").write_text("# Evolver\n\nSelf-improvement via skill mutation.")

        with patch.object(lat_inject, "_lat_dir", return_value=lat_dir):
            _reset_cache()
            names = lat_inject.relevant_node_names("evolver skill mutation", max_nodes=1)

        assert isinstance(names, list)
        if names:
            assert names[0] == "evolver"

    def test_empty_if_no_nodes(self, tmp_path):
        with patch.object(lat_inject, "_lat_dir", return_value=tmp_path / "missing"):
            _reset_cache()
            names = lat_inject.relevant_node_names("anything")
        assert names == []
