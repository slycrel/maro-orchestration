"""Tests for Phase 2: intent.py (NOW/AGENDA classifier)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from intent import classify, _heuristic_classify


# ---------------------------------------------------------------------------
# Heuristic classifier
# ---------------------------------------------------------------------------

class TestHeuristicNOW:
    def test_simple_question(self):
        lane, conf, reason = _heuristic_classify("what time is it?")
        assert lane == "now"
        assert conf >= 0.5

    def test_short_message(self):
        lane, _, _ = _heuristic_classify("hello")
        assert lane == "now"

    def test_write_haiku(self):
        lane, _, _ = _heuristic_classify("write a haiku about the ocean")
        assert lane == "now"

    def test_translate(self):
        lane, _, _ = _heuristic_classify("translate this to Spanish: hello world")
        assert lane == "now"

    def test_quick_summary(self):
        lane, _, _ = _heuristic_classify("summarize this paragraph quickly")
        assert lane == "now"

    def test_factual_question(self):
        lane, _, _ = _heuristic_classify("who is Elon Musk?")
        assert lane == "now"


class TestHeuristicAGENDA:
    def test_research(self):
        lane, conf, reason = _heuristic_classify("research winning polymarket prediction strategies")
        assert lane == "agenda"
        assert conf >= 0.5

    def test_build(self):
        lane, _, _ = _heuristic_classify("build a research report on competitor pricing")
        assert lane == "agenda"

    def test_analyze(self):
        lane, _, _ = _heuristic_classify("analyze the X posting patterns of top crypto accounts")
        assert lane == "agenda"

    def test_monitor(self):
        lane, _, _ = _heuristic_classify("monitor BTC price movements and alert on unusual patterns")
        assert lane == "agenda"

    def test_deep_dive(self):
        lane, _, _ = _heuristic_classify("deep dive into Polymarket resolution patterns")
        assert lane == "agenda"


class TestHeuristicEdgeCases:
    def test_empty_string(self):
        # Should not crash
        lane, conf, reason = _heuristic_classify("")
        assert lane in ("now", "agenda")
        assert 0.0 <= conf <= 1.0

    def test_very_long_agenda(self):
        lane, _, _ = _heuristic_classify(
            "research and analyze and compare and evaluate all major prediction markets"
        )
        assert lane == "agenda"

    def test_confidence_range(self):
        for msg in ["hi", "research X", "build Y", "what is Z?", "monitor W"]:
            _, conf, _ = _heuristic_classify(msg)
            assert 0.0 <= conf <= 1.0


# ---------------------------------------------------------------------------
# classify() with dry_run
# ---------------------------------------------------------------------------

def test_classify_dry_run_short():
    lane, conf, reason = classify("what time is it?", dry_run=True)
    assert lane == "now"
    assert isinstance(conf, float)
    assert isinstance(reason, str)


def test_classify_dry_run_research():
    lane, conf, reason = classify("research polymarket strategies", dry_run=True)
    assert lane == "agenda"


def test_classify_returns_tuple():
    result = classify("hello", dry_run=True)
    assert len(result) == 3
    lane, conf, reason = result
    assert lane in ("now", "agenda")
    assert 0.0 <= conf <= 1.0
    assert len(reason) > 0


def test_classify_falls_back_on_adapter_error():
    """If LLM call fails, falls back to heuristic without raising."""
    class FailAdapter:
        def complete(self, *args, **kwargs):
            raise RuntimeError("API down")

    lane, conf, reason = classify("research X", adapter=FailAdapter())
    assert lane in ("now", "agenda")
    assert 0.0 <= conf <= 1.0


# ---------------------------------------------------------------------------
# check_goal_clarity
# ---------------------------------------------------------------------------

from intent import check_goal_clarity


def test_clarity_dry_run_returns_clear():
    result = check_goal_clarity("research X", dry_run=True)
    assert result["clear"] is True
    assert result["question"] == ""


def test_clarity_no_adapter_returns_clear():
    result = check_goal_clarity("research X", adapter=None)
    assert result["clear"] is True


def test_clarity_very_short_goal_returns_clear():
    # < 4 words: skip check entirely
    result = check_goal_clarity("go", adapter=None)
    assert result["clear"] is True


def test_clarity_adapter_error_returns_clear():
    """Adapter failure must not block execution."""
    class FailAdapter:
        def complete(self, *args, **kwargs):
            raise RuntimeError("API down")

    result = check_goal_clarity("research winning polymarket strategies", adapter=FailAdapter())
    assert result["clear"] is True


def test_clarity_unclear_response_parsed():
    import json

    class ClarifyAdapter:
        class _Resp:
            content = json.dumps({"clear": False, "question": "What market type?"})
            input_tokens = 10
            output_tokens = 20
            tool_calls = []

        def complete(self, *args, **kwargs):
            return self._Resp()

    result = check_goal_clarity("research the optimal strategy now", adapter=ClarifyAdapter())
    assert result["clear"] is False
    assert "market" in result["question"].lower() or len(result["question"]) > 0


def test_clarity_clear_response_parsed():
    import json

    class ClearAdapter:
        class _Resp:
            content = json.dumps({"clear": True, "question": ""})
            input_tokens = 10
            output_tokens = 20
            tool_calls = []

        def complete(self, *args, **kwargs):
            return self._Resp()

    result = check_goal_clarity("research winning polymarket prediction market strategies", adapter=ClearAdapter())
    assert result["clear"] is True


def test_clarity_malformed_json_returns_clear():
    """Malformed LLM response must not block execution."""
    class BadJsonAdapter:
        class _Resp:
            content = "not json at all"
            input_tokens = 10
            output_tokens = 5
            tool_calls = []

        def complete(self, *args, **kwargs):
            return self._Resp()

    result = check_goal_clarity("research X Y Z W", adapter=BadJsonAdapter())
    assert result["clear"] is True


# ---------------------------------------------------------------------------
# rewrite_imperative_goal (Bitter Lesson goal rewriter)
# ---------------------------------------------------------------------------

from intent import rewrite_imperative_goal, _is_imperative_heavy


class TestIsImperativeHeavy:
    def test_outcome_goal_not_imperative(self):
        # Pure outcome goal — should not be flagged
        assert not _is_imperative_heavy("Research winning Polymarket prediction strategies and summarize top 3")

    def test_then_sequence_is_imperative(self):
        assert _is_imperative_heavy(
            "First check the repo then run the tests then commit all changes and push to the main branch"
        )

    def test_step_sequence_is_imperative(self):
        assert _is_imperative_heavy(
            "Step 1: clone the repo. Step 2: install all deps. Step 3: run smoke tests and verify."
        )

    def test_start_by_is_imperative(self):
        assert _is_imperative_heavy(
            "Start by reading the README, then carefully audit the test coverage, and finally run the suite"
        )

    def test_short_goal_never_imperative(self):
        # Under 15 words — never flagged regardless of content
        assert not _is_imperative_heavy("First install then run")

    def test_proceed_to_is_imperative(self):
        assert _is_imperative_heavy(
            "Proceed to analyze the Polymarket data carefully, then generate a detailed summary report for the team"
        )


class TestRewriteImperativeGoal:
    def test_returns_original_when_no_imperative(self):
        # Outcome-focused goal — should pass through unchanged
        goal = "Research winning Polymarket strategies and summarize key findings"
        assert rewrite_imperative_goal(goal) == goal

    def test_returns_original_when_dry_run(self):
        goal = "First do X then do Y then check Z for the final output"
        assert rewrite_imperative_goal(goal, dry_run=True) == goal

    def test_returns_original_when_no_adapter(self):
        goal = "First do X then do Y then check Z for the final output and commit"
        assert rewrite_imperative_goal(goal, adapter=None) == goal

    def test_llm_rewrite_accepted_when_changed(self):
        import json

        class RewriteAdapter:
            class _Resp:
                content = json.dumps({"rewritten": "Achieve X outcome given Y context", "changed": True})
                input_tokens = 30
                output_tokens = 20
                tool_calls = []

            def complete(self, *args, **kwargs):
                return self._Resp()

        goal = "First do X, then do Y, then check Z for the final output and commit changes"
        result = rewrite_imperative_goal(goal, adapter=RewriteAdapter())
        assert result == "Achieve X outcome given Y context"
        assert result != goal

    def test_llm_unchanged_returns_original(self):
        import json

        class UnchangedAdapter:
            class _Resp:
                content = json.dumps({"rewritten": "original goal unchanged", "changed": False})
                input_tokens = 30
                output_tokens = 15
                tool_calls = []

            def complete(self, *args, **kwargs):
                return self._Resp()

        goal = "First do X then do Y then check Z and finalize the whole thing properly"
        result = rewrite_imperative_goal(goal, adapter=UnchangedAdapter())
        assert result == goal

    def test_llm_bad_json_returns_original(self):
        class BadAdapter:
            class _Resp:
                content = "not json"
                input_tokens = 5
                output_tokens = 5
                tool_calls = []

            def complete(self, *args, **kwargs):
                return self._Resp()

        goal = "First do X then do Y then check Z and commit all the results back to main"
        result = rewrite_imperative_goal(goal, adapter=BadAdapter())
        assert result == goal

    def test_llm_exception_returns_original(self):
        class CrashAdapter:
            def complete(self, *args, **kwargs):
                raise RuntimeError("adapter exploded")

        goal = "First do X then do Y then check Z and push to origin main"
        result = rewrite_imperative_goal(goal, adapter=CrashAdapter())
        assert result == goal

    def test_rewritten_goal_too_short_ignored(self):
        import json

        class TinyAdapter:
            class _Resp:
                content = json.dumps({"rewritten": "Hi", "changed": True})
                input_tokens = 5
                output_tokens = 5
                tool_calls = []

            def complete(self, *args, **kwargs):
                return self._Resp()

        goal = "First do X then do Y then check Z and commit and push to origin main branch"
        result = rewrite_imperative_goal(goal, adapter=TinyAdapter())
        assert result == goal  # too short — ignored
