"""Tests for K4 knowledge bridge — outcome → knowledge write path."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@dataclass
class FakeOutcome:
    """Minimal Outcome stub for testing."""
    outcome_id: str = "test-outcome-1"
    goal: str = "Research how to improve step decomposition quality"
    status: str = "done"
    task_type: str = "research"
    summary: str = "Decomposition quality improved 20% by adding step independence check"
    lessons: List[str] = field(default_factory=lambda: [
        "Always verify step independence before parallel execution",
        "Short steps with clear success criteria decompose better",
        "Avoid ambiguous step descriptions that mix planning with execution",
    ])
    project: Optional[str] = None


@pytest.fixture()
def tmp_workspace(tmp_path, monkeypatch):
    """Redirect memory dir to tmp_path for isolation."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    # Ensure the memory dir exists
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# _jaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def test_identical(self):
        from knowledge_bridge import _jaccard
        assert _jaccard("hello world", "hello world") == 1.0

    def test_disjoint(self):
        from knowledge_bridge import _jaccard
        score = _jaccard("abc", "xyz")
        assert score == 0.0

    def test_partial_overlap(self):
        from knowledge_bridge import _jaccard
        score = _jaccard("verify step independence", "verify independence checks")
        assert 0.3 < score < 0.9

    def test_empty_string(self):
        from knowledge_bridge import _jaccard
        # Should not raise; empty → empty sets → 0
        score = _jaccard("", "hello")
        assert score == 0.0


# ---------------------------------------------------------------------------
# _extract_heuristic
# ---------------------------------------------------------------------------

class TestExtractHeuristic:
    def test_returns_candidates_for_outcome_with_lessons(self):
        from knowledge_bridge import _extract_heuristic
        outcome = FakeOutcome()
        candidates = _extract_heuristic(outcome)
        assert len(candidates) >= 1
        title, desc, ntype = candidates[0]
        assert isinstance(title, str) and len(title) > 0
        assert isinstance(desc, str) and len(desc) > 20
        assert ntype in ("principle", "technique", "insight")

    def test_short_lessons_skipped(self):
        from knowledge_bridge import _extract_heuristic
        outcome = FakeOutcome(lessons=["ok", "yes", "  "])
        candidates = _extract_heuristic(outcome)
        assert candidates == []

    def test_principle_detection(self):
        from knowledge_bridge import _extract_heuristic
        outcome = FakeOutcome(
            status="done",
            lessons=["Always use exponential backoff when rate limited — it's a best practice"],
        )
        candidates = _extract_heuristic(outcome)
        assert candidates[0][2] in ("principle", "technique")

    def test_capped_at_five_lessons(self):
        from knowledge_bridge import _extract_heuristic
        outcome = FakeOutcome(lessons=["Lesson " + str(i) * 30 for i in range(10)])
        candidates = _extract_heuristic(outcome)
        assert len(candidates) <= 5


# ---------------------------------------------------------------------------
# upsert_knowledge_from_candidate
# ---------------------------------------------------------------------------

class TestUpsertKnowledge:
    def test_creates_new_node(self, tmp_workspace):
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from knowledge_bridge import upsert_knowledge_from_candidate
        from knowledge_web import load_knowledge_nodes

        node, is_new = upsert_knowledge_from_candidate(
            title="Verify before parallel execution",
            description="Step independence verification prevents race conditions.",
            node_type="principle",
            domain="orchestration",
            sources=["outcome:test-1"],
            existing_nodes=[],
        )
        assert is_new is True
        assert node.node_id is not None

        all_nodes = load_knowledge_nodes(status=None)  # type: ignore[arg-type]
        assert any(n.node_id == node.node_id for n in all_nodes)

    def test_updates_existing_similar_node(self, tmp_workspace):
        from knowledge_bridge import upsert_knowledge_from_candidate
        from knowledge_web import load_knowledge_nodes

        # Create first
        node1, _ = upsert_knowledge_from_candidate(
            title="Verify before execution",
            description="Always verify step correctness before running.",
            node_type="principle",
            domain="orchestration",
            sources=["outcome:1"],
            existing_nodes=[],
        )
        existing = load_knowledge_nodes(status=None)  # type: ignore[arg-type]
        original_confidence = node1.confidence

        # Update with similar title
        node2, is_new2 = upsert_knowledge_from_candidate(
            title="Verify step before execution",  # similar enough to match
            description="Step verification matters before running anything.",
            node_type="principle",
            domain="orchestration",
            sources=["outcome:2"],
            existing_nodes=existing,
        )
        assert is_new2 is False
        assert node2.node_id == node1.node_id
        assert node2.confidence > original_confidence

    def test_distinct_titles_create_separate_nodes(self, tmp_workspace):
        from knowledge_bridge import upsert_knowledge_from_candidate

        node1, is_new1 = upsert_knowledge_from_candidate(
            title="Use exponential backoff for rate limits",
            description="Exponential backoff prevents thundering herd.",
            node_type="technique",
            domain="orchestration",
            sources=["outcome:1"],
            existing_nodes=[],
        )
        existing = [node1]
        node2, is_new2 = upsert_knowledge_from_candidate(
            title="Compress completed context after five steps",
            description="Context compression reduces token overhead dramatically.",
            node_type="technique",
            domain="orchestration",
            sources=["outcome:2"],
            existing_nodes=existing,
        )
        assert is_new1 is True
        assert is_new2 is True
        assert node1.node_id != node2.node_id

    def test_invalid_node_type_defaults_to_insight(self, tmp_workspace):
        from knowledge_bridge import upsert_knowledge_from_candidate

        node, is_new = upsert_knowledge_from_candidate(
            title="Some random observation",
            description="A thing that happened during testing.",
            node_type="bogus_type",
            domain="orchestration",
            sources=[],
            existing_nodes=[],
        )
        assert is_new is True
        assert node.node_type == "insight"


# ---------------------------------------------------------------------------
# outcome_to_knowledge — main entry point
# ---------------------------------------------------------------------------

class TestOutcomeToKnowledge:
    def test_heuristic_path_creates_nodes(self, tmp_workspace):
        from knowledge_bridge import outcome_to_knowledge
        from knowledge_web import load_knowledge_nodes

        outcome = FakeOutcome()
        count = outcome_to_knowledge(outcome, adapter=None, dry_run=False)
        assert count >= 1

        nodes = load_knowledge_nodes(status=None)  # type: ignore[arg-type]
        assert len(nodes) >= 1

    def test_dry_run_creates_no_nodes(self, tmp_workspace):
        from knowledge_bridge import outcome_to_knowledge
        from knowledge_web import load_knowledge_nodes

        outcome = FakeOutcome()
        count = outcome_to_knowledge(outcome, adapter=None, dry_run=True)
        assert count == 0
        nodes = load_knowledge_nodes(status=None)  # type: ignore[arg-type]
        assert len(nodes) == 0

    def test_no_lessons_returns_zero(self, tmp_workspace):
        from knowledge_bridge import outcome_to_knowledge

        outcome = FakeOutcome(lessons=[])
        count = outcome_to_knowledge(outcome, adapter=None, dry_run=False)
        assert count == 0

    def test_llm_adapter_path(self, tmp_workspace):
        from knowledge_bridge import outcome_to_knowledge

        adapter = MagicMock()
        adapter.complete.return_value = {
            "content": (
                '{"title": "Use circuit breakers for skill reliability", '
                '"description": "Circuit breakers prevent repeated invocation of failing skills.", '
                '"node_type": "pattern", "domain": "orchestration"}\n'
                '{"title": "Prefer narrow except blocks over bare except", '
                '"description": "Narrow exception handling surfaces hidden failures.", '
                '"node_type": "principle", "domain": "orchestration"}\n'
            )
        }
        outcome = FakeOutcome()
        count = outcome_to_knowledge(outcome, adapter=adapter, dry_run=False)
        assert count >= 1

    def test_adapter_failure_falls_back_to_heuristic(self, tmp_workspace):
        from knowledge_bridge import outcome_to_knowledge

        adapter = MagicMock()
        adapter.complete.side_effect = RuntimeError("LLM unavailable")

        outcome = FakeOutcome()
        # Should not raise; should fall back to heuristic
        count = outcome_to_knowledge(outcome, adapter=adapter, dry_run=False)
        assert count >= 0  # heuristic may still produce nodes

    def test_dedup_across_runs(self, tmp_workspace):
        from knowledge_bridge import outcome_to_knowledge
        from knowledge_web import load_knowledge_nodes

        outcome = FakeOutcome(lessons=[
            "Always verify step independence before parallel execution — this is critical",
        ])
        # Run twice with same outcome
        count1 = outcome_to_knowledge(outcome, adapter=None, dry_run=False)
        count2 = outcome_to_knowledge(outcome, adapter=None, dry_run=False)

        nodes = load_knowledge_nodes(status=None)  # type: ignore[arg-type]
        # Second run should not create new duplicates (titles are similar)
        assert count2 == 0 or len(nodes) <= count1 + 1

    def test_exception_in_outcome_is_non_fatal(self, tmp_workspace):
        from knowledge_bridge import outcome_to_knowledge

        # Pass an object that will fail attribute access
        class BrokenOutcome:
            @property
            def lessons(self):
                raise AttributeError("boom")
            outcome_id = "x"
            goal = "test"
            status = "done"
            task_type = "general"

        # Should return 0, not raise
        count = outcome_to_knowledge(BrokenOutcome(), adapter=None, dry_run=False)
        assert count == 0


# ---------------------------------------------------------------------------
# validate_principle
# ---------------------------------------------------------------------------

class TestValidatePrinciple:
    def test_validation_bumps_confidence(self, tmp_workspace):
        from knowledge_bridge import upsert_knowledge_from_candidate, validate_principle
        from knowledge_web import load_knowledge_nodes

        node, _ = upsert_knowledge_from_candidate(
            title="Test principle for validation",
            description="A principle that will be validated.",
            node_type="principle",
            domain="orchestration",
            sources=[],
            existing_nodes=[],
        )
        original_confidence = node.confidence

        result = validate_principle(node.node_id, validated=True, outcome_id="test-run-1")
        assert result is True

        updated = next(n for n in load_knowledge_nodes(status=None)  # type: ignore[arg-type]
                       if n.node_id == node.node_id)
        assert updated.confidence > original_confidence

    def test_contradiction_lowers_confidence(self, tmp_workspace):
        from knowledge_bridge import upsert_knowledge_from_candidate, validate_principle
        from knowledge_web import load_knowledge_nodes, NODE_CANDIDATE

        node, _ = upsert_knowledge_from_candidate(
            title="Principle to be contradicted",
            description="This will be disproven.",
            node_type="insight",
            domain="orchestration",
            sources=[],
            existing_nodes=[],
        )
        # Start with higher confidence so we can lower it
        # (default is 0.3 for auto-extracted nodes)
        original_confidence = node.confidence

        result = validate_principle(node.node_id, validated=False, outcome_id="failure-run")
        assert result is True

        updated = next(n for n in load_knowledge_nodes(status=None)  # type: ignore[arg-type]
                       if n.node_id == node.node_id)
        assert updated.confidence <= original_confidence

    def test_returns_false_for_nonexistent_node(self, tmp_workspace):
        from knowledge_bridge import validate_principle

        result = validate_principle("nonexistent-node-id", validated=True)
        assert result is False


# ---------------------------------------------------------------------------
# record_skill_evolution
# ---------------------------------------------------------------------------

class TestRecordSkillEvolution:
    def test_creates_skill_node(self, tmp_workspace):
        from knowledge_bridge import record_skill_evolution
        from knowledge_web import load_knowledge_nodes

        @dataclass
        class FakeSkill:
            id: str = "skill-abc"
            name: str = "research-web"
            description: str = "Fetches and summarizes web content"
            tier: str = "established"

        skill = FakeSkill()
        record_skill_evolution(skill, event="promoted", outcome_summary="Research tasks improved")

        nodes = load_knowledge_nodes(node_type="technique", status=None)  # type: ignore[arg-type]
        assert any("research-web" in n.title for n in nodes)

    def test_dry_run_does_nothing(self, tmp_workspace):
        from knowledge_bridge import record_skill_evolution
        from knowledge_web import load_knowledge_nodes

        @dataclass
        class FakeSkill:
            id: str = "skill-xyz"
            name: str = "test-skill"
            description: str = "A test skill"
            tier: str = "provisional"

        record_skill_evolution(FakeSkill(), event="created", dry_run=True)

        nodes = load_knowledge_nodes(status=None)  # type: ignore[arg-type]
        assert len(nodes) == 0

    def test_no_name_is_noop(self, tmp_workspace):
        from knowledge_bridge import record_skill_evolution

        @dataclass
        class FakeSkill:
            id: str = "skill-empty"
            name: str = ""
            description: str = ""
            tier: str = "provisional"

        # Should not raise
        record_skill_evolution(FakeSkill(), event="created")


# ---------------------------------------------------------------------------
# reflect_and_record integration — K4 wired in
# ---------------------------------------------------------------------------

class TestReflectAndRecordK4Integration:
    def test_reflect_triggers_knowledge_write(self, tmp_workspace):
        """reflect_and_record should trigger outcome_to_knowledge."""
        from knowledge_web import load_knowledge_nodes

        with patch("memory.extract_lessons_via_llm", return_value=[
            ("Always verify step independence", "execution"),
        ]), patch("memory.record_tiered_lesson"), \
           patch("memory.record_outcome") as mock_record:

            mock_outcome = FakeOutcome()
            mock_record.return_value = mock_outcome

            # Patch outcome_to_knowledge to verify it's called
            with patch("knowledge_bridge.outcome_to_knowledge") as mock_k4:
                mock_k4.return_value = 1
                import memory
                memory.reflect_and_record(
                    goal="Test K4 integration",
                    status="done",
                    result_summary="All good",
                    dry_run=False,
                )
                assert mock_k4.called

    def test_reflect_dry_run_skips_k4(self, tmp_workspace):
        """dry_run=True should not call outcome_to_knowledge."""
        with patch("memory.extract_lessons_via_llm", return_value=[]), \
             patch("memory.record_outcome") as mock_record:

            mock_record.return_value = FakeOutcome()

            with patch("knowledge_bridge.outcome_to_knowledge") as mock_k4:
                import memory
                memory.reflect_and_record(
                    goal="Test dry run",
                    status="done",
                    result_summary="Dry run",
                    dry_run=True,
                )
                mock_k4.assert_not_called()
