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
