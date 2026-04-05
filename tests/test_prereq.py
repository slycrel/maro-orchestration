"""Tests for prereq.py — Phase 27 per-step knowledge prerequisite checking."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# detect_knowledge_topics
# ---------------------------------------------------------------------------

class TestDetectKnowledgeTopics:
    def _call(self, text):
        from prereq import detect_knowledge_topics
        return detect_knowledge_topics(text)

    def test_learn_about_pattern(self):
        topics = self._call("Learn about Japanese writing systems")
        assert any("Japanese" in t for t in topics)

    def test_research_pattern(self):
        topics = self._call("Research stroke order in kanji calligraphy")
        assert any("stroke order" in t for t in topics)

    def test_understand_pattern(self):
        topics = self._call("Understand the Black-Scholes model for options pricing")
        assert any("Black-Scholes" in t for t in topics)

    def test_study_pattern(self):
        topics = self._call("Study reinforcement learning reward functions")
        assert any("reinforcement learning" in t for t in topics)

    def test_non_knowledge_step_returns_empty(self):
        topics = self._call("Build the REST API endpoint for user auth")
        assert topics == []

    def test_deploy_step_returns_empty(self):
        topics = self._call("Deploy the container to production")
        assert topics == []

    def test_caps_at_three(self):
        # Multiple knowledge patterns in one step — should cap at 3
        text = "Learn about X, research Y, study Z, understand W"
        topics = self._call(text)
        assert len(topics) <= 3

    def test_topic_truncated_at_60_chars(self):
        long_topic = "a" * 100
        topics = self._call(f"Learn about {long_topic}")
        assert all(len(t) <= 60 for t in topics)

    def test_empty_input(self):
        assert self._call("") == []

    def test_short_topic_skipped(self):
        topics = self._call("Learn about it")
        # "it" is 2 chars — below the 3-char threshold
        assert topics == [] or all(len(t) > 3 for t in topics)


# ---------------------------------------------------------------------------
# check_prerequisites — graveyard resurrection path
# ---------------------------------------------------------------------------

class TestCheckPrerequisitesGraveyard:
    def _call(self, steps, **kw):
        from prereq import check_prerequisites
        return check_prerequisites(steps, goal_id="test-loop", **kw)

    def test_empty_steps_returns_empty(self):
        result = self._call([])
        assert result == {}

    def test_no_knowledge_steps_returns_empty(self):
        result = self._call(["Build the API", "Deploy to prod", "Write tests"])
        assert result == {}

    def test_graveyard_hit_injects_context(self, monkeypatch, tmp_path):
        """When graveyard has a matching lesson, it gets injected for that step."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        # Plant a graveyard lesson
        from memory import record_tiered_lesson, MemoryTier
        record_tiered_lesson("kanji stroke order goes top-to-bottom", "research", "done", "test", tier=MemoryTier.MEDIUM)
        # Force score into graveyard range (0.2–0.4)
        from memory import load_tiered_lessons, _rewrite_tiered_lessons
        lessons = load_tiered_lessons(MemoryTier.MEDIUM, min_score=0.0)
        for l in lessons:
            l.score = 0.3
        _rewrite_tiered_lessons(MemoryTier.MEDIUM, lessons)

        result = self._call(["Learn about kanji calligraphy technique"])
        assert 0 in result
        assert "kanji" in result[0].lower() or "stroke" in result[0].lower()

    def test_graveyard_miss_no_sub_goals_returns_empty(self, monkeypatch, tmp_path):
        """Graveyard miss + knowledge_sub_goals=False → no context injected."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        result = self._call(
            ["Learn about options Greeks and Black-Scholes"],
            knowledge_sub_goals=False,
        )
        assert result == {}

    def test_step_index_preserved(self, monkeypatch, tmp_path):
        """Context is keyed by step index (0-based), not step 0 always."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, MemoryTier, load_tiered_lessons, _rewrite_tiered_lessons
        record_tiered_lesson("kanji fundamentals: stroke order, radicals", "research", "done", "test", tier=MemoryTier.MEDIUM)
        lessons = load_tiered_lessons(MemoryTier.MEDIUM, min_score=0.0)
        for l in lessons:
            l.score = 0.3
        _rewrite_tiered_lessons(MemoryTier.MEDIUM, lessons)

        steps = [
            "Build the API endpoint",           # idx 0 — no knowledge needed
            "Learn about kanji writing systems", # idx 1 — needs knowledge
        ]
        result = self._call(steps)
        # Context should be keyed to idx 1, not idx 0
        assert 0 not in result
        assert 1 in result

    def test_multiple_steps_with_knowledge(self, monkeypatch, tmp_path):
        """Multiple knowledge steps each get their context."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, MemoryTier, load_tiered_lessons, _rewrite_tiered_lessons
        record_tiered_lesson("kanji: Japanese character writing", "research", "done", "test", tier=MemoryTier.MEDIUM)
        record_tiered_lesson("origami: paper folding technique", "research", "done", "test", tier=MemoryTier.MEDIUM)
        lessons = load_tiered_lessons(MemoryTier.MEDIUM, min_score=0.0)
        for l in lessons:
            l.score = 0.3
        _rewrite_tiered_lessons(MemoryTier.MEDIUM, lessons)

        steps = [
            "Learn about kanji strokes",
            "Research origami fold patterns",
        ]
        result = self._call(steps)
        # At least one should get context (both topics match graveyard)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# check_prerequisites — sub-loop spawn path
# ---------------------------------------------------------------------------

class TestCheckPrerequisitesSubLoop:
    def test_sub_loop_spawned_on_graveyard_miss(self, monkeypatch, tmp_path):
        """With knowledge_sub_goals=True and graveyard empty, sub-loop is spawned."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        spawned = []

        def _fake_run_agent_loop(goal, **kw):
            spawned.append(goal)
            result = MagicMock()
            result.steps = [MagicMock(status="done", result="Key fact: X is Y")]
            result.stuck_reason = ""
            return result

        monkeypatch.setattr("prereq.agent_loop", MagicMock(), raising=False)

        from prereq import check_prerequisites
        fake_adapter = MagicMock()

        with patch("prereq._spawn_knowledge_sub_loop") as mock_spawn:
            mock_spawn.return_value = "Key fact: kanji uses stroke order"
            result = check_prerequisites(
                ["Learn about kanji calligraphy"],
                goal_id="parent-loop",
                adapter=fake_adapter,
                continuation_depth=0,
                knowledge_sub_goals=True,
            )

        mock_spawn.assert_called_once()
        assert 0 in result
        assert "kanji" in result[0].lower() or "Key fact" in result[0]

    def test_sub_loop_not_spawned_at_depth_1(self, monkeypatch, tmp_path):
        """Sub-loops must not spawn further sub-loops (depth guard)."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        with patch("prereq._spawn_knowledge_sub_loop") as mock_spawn:
            from prereq import check_prerequisites
            check_prerequisites(
                ["Learn about quantum computing"],
                goal_id="sub-loop",
                adapter=MagicMock(),
                continuation_depth=1,  # inside a sub-loop
                knowledge_sub_goals=True,
            )

        mock_spawn.assert_not_called()

    def test_sub_loop_not_spawned_when_disabled(self, monkeypatch, tmp_path):
        """knowledge_sub_goals=False prevents sub-loop spawning."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        with patch("prereq._spawn_knowledge_sub_loop") as mock_spawn:
            from prereq import check_prerequisites
            check_prerequisites(
                ["Research quantum entanglement"],
                goal_id="test",
                adapter=MagicMock(),
                continuation_depth=0,
                knowledge_sub_goals=False,
            )

        mock_spawn.assert_not_called()


# ---------------------------------------------------------------------------
# _extract_sub_loop_summary
# ---------------------------------------------------------------------------

class TestExtractSubLoopSummary:
    def test_returns_last_done_step_result(self):
        from prereq import _extract_sub_loop_summary
        result = MagicMock()
        result.steps = [
            MagicMock(status="done", result="First result"),
            MagicMock(status="done", result="Last result"),
        ]
        result.stuck_reason = ""
        assert _extract_sub_loop_summary(result) == "Last result"

    def test_falls_back_to_stuck_reason(self):
        from prereq import _extract_sub_loop_summary
        result = MagicMock()
        result.steps = [MagicMock(status="stuck", result="")]
        result.stuck_reason = "Could not acquire"
        assert "Could not acquire" in _extract_sub_loop_summary(result)

    def test_empty_on_no_data(self):
        from prereq import _extract_sub_loop_summary
        result = MagicMock()
        result.steps = []
        result.stuck_reason = ""
        assert _extract_sub_loop_summary(result) == ""

    def test_truncates_long_result(self):
        from prereq import _extract_sub_loop_summary
        result = MagicMock()
        result.steps = [MagicMock(status="done", result="x" * 1000)]
        assert len(_extract_sub_loop_summary(result)) <= 500


# ---------------------------------------------------------------------------
# agent_loop integration — prereq context injected (dry_run, no actual LLM)
# ---------------------------------------------------------------------------

class TestAgentLoopPrereqIntegration:
    def test_loop_accepts_knowledge_sub_goals_param(self, monkeypatch, tmp_path):
        """run_agent_loop(knowledge_sub_goals=True) should not raise."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setattr("agent_loop.set_loop_running", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr("agent_loop.clear_loop_running", lambda *a, **kw: None, raising=False)

        from agent_loop import run_agent_loop
        result = run_agent_loop(
            "Learn about kanji calligraphy",
            dry_run=True,
            knowledge_sub_goals=True,
        )
        assert result.status in ("done", "stuck", "interrupted", "error")

    def test_prereq_context_does_not_break_dry_run(self, monkeypatch, tmp_path):
        """Prereq check is skipped in dry_run mode — loop completes normally."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setattr("agent_loop.set_loop_running", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr("agent_loop.clear_loop_running", lambda *a, **kw: None, raising=False)

        from agent_loop import run_agent_loop
        result = run_agent_loop(
            "Research quantum computing fundamentals",
            dry_run=True,  # prereq disabled in dry_run
        )
        assert result.status in ("done", "stuck", "interrupted", "error")
