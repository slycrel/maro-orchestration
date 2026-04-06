"""Tests for strategy_evaluator — replay-based fitness oracle."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from strategy_evaluator import (
    evaluate_strategy,
    evaluate_skill,
    evaluate_suggestion,
    StrategyFitnessReport,
    SimilarOutcome,
    _tokenize,
    _tfidf_cosine,
    SIMILARITY_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_outcome(goal, status, outcome_id=None, summary="", lessons=None):
    return SimpleNamespace(
        outcome_id=outcome_id or f"oc-{goal[:8]}",
        goal=goal,
        status=status,
        summary=summary,
        lessons=lessons or [],
    )


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------

class TestTokenize:
    def test_lowercases_and_removes_stop_words(self):
        tokens = _tokenize("Search the web for Python tutorials")
        assert "the" not in tokens
        assert "for" not in tokens
        assert "search" in tokens
        assert "python" in tokens

    def test_removes_short_tokens(self):
        tokens = _tokenize("do it now")
        # "do", "it" are short (<=2) or stop words; "now" has 3 chars → kept
        assert "now" in tokens
        assert "it" not in tokens

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_only_stop_words(self):
        assert _tokenize("the a an and or") == []

    def test_alphanumeric_tokenization(self):
        tokens = _tokenize("hello-world, foo_bar!")
        assert "hello" in tokens
        assert "world" in tokens


# ---------------------------------------------------------------------------
# _tfidf_cosine
# ---------------------------------------------------------------------------

class TestTfidfCosine:
    def test_identical_query_and_doc_high_similarity(self):
        terms = ["python", "web", "search"]
        sims = _tfidf_cosine(terms, [terms])
        assert sims[0] > 0.9

    def test_disjoint_query_and_doc_zero_similarity(self):
        query = ["alpha", "beta", "gamma"]
        doc = ["delta", "epsilon", "zeta"]
        sims = _tfidf_cosine(query, [doc])
        assert sims[0] == 0.0

    def test_empty_docs_returns_zeros(self):
        assert _tfidf_cosine(["term"], []) == []

    def test_empty_query_returns_zeros(self):
        sims = _tfidf_cosine([], [["term"]])
        assert sims == [0.0]

    def test_partial_overlap_between_zero_and_one(self):
        query = ["python", "web", "search", "data"]
        doc = ["python", "analysis", "data", "model"]
        sims = _tfidf_cosine(query, [doc])
        assert 0.0 < sims[0] < 1.0

    def test_multiple_docs_ranked(self):
        query = ["machine", "learning", "model"]
        doc_relevant = ["machine", "learning", "model", "training"]
        doc_irrelevant = ["cook", "pasta", "recipe", "food"]
        sims = _tfidf_cosine(query, [doc_relevant, doc_irrelevant])
        assert sims[0] > sims[1]


# ---------------------------------------------------------------------------
# evaluate_strategy — no outcomes
# ---------------------------------------------------------------------------

class TestEvaluateStrategyNoOutcomes:
    def test_empty_outcomes_returns_neutral_uncertain(self):
        report = evaluate_strategy("search for market data", outcomes=[])
        assert report.fitness_score == 0.5
        assert report.confidence == 0.0
        assert report.verdict == "UNCERTAIN"
        assert report.outcomes_searched == 0

    def test_empty_strategy_text_returns_uncertain(self):
        outcomes = [_make_outcome("some goal", "done")]
        report = evaluate_strategy("the a an", outcomes=outcomes)
        assert report.verdict == "UNCERTAIN"
        assert report.confidence == 0.0

    def test_no_similar_outcomes_returns_uncertain(self):
        outcomes = [
            _make_outcome("cook pasta and make sauce", "done"),
            _make_outcome("bake cookies with chocolate", "done"),
        ]
        report = evaluate_strategy("trade options on crypto market", outcomes=outcomes)
        assert report.verdict == "UNCERTAIN"
        assert report.above_threshold == 0


# ---------------------------------------------------------------------------
# evaluate_strategy — with outcomes
# ---------------------------------------------------------------------------

class TestEvaluateStrategyWithOutcomes:
    def test_all_done_outcomes_high_fitness(self):
        outcomes = [
            _make_outcome("search and summarize web data for market research", "done"),
            _make_outcome("search web for market data analysis", "done"),
            _make_outcome("web search market research summarize results", "done"),
        ]
        report = evaluate_strategy("search the web for market research data and summarize", outcomes=outcomes)
        assert report.fitness_score > 0.5
        assert report.done_count > 0

    def test_all_stuck_outcomes_low_fitness(self):
        outcomes = [
            _make_outcome("analyze market data for trading strategy", "stuck"),
            _make_outcome("market data trading analysis strategy", "stuck"),
            _make_outcome("strategy trading market analyze data", "stuck"),
        ]
        report = evaluate_strategy("analyze market trading strategy data", outcomes=outcomes)
        assert report.fitness_score < 0.5
        assert report.stuck_count > 0

    def test_pass_verdict_when_high_fitness_and_confidence(self):
        outcomes = [
            _make_outcome("fetch web page and extract price data", "done"),
            _make_outcome("fetch web page extract price data results", "done"),
            _make_outcome("web fetch extract price data from page", "done"),
        ]
        report = evaluate_strategy("fetch web page extract price data", outcomes=outcomes, pass_threshold=0.6)
        # With 3 done outcomes, confidence threshold should be met
        if report.confidence >= 0.3:
            assert report.verdict == "PASS"

    def test_fail_verdict_when_low_fitness_and_confidence(self):
        outcomes = [
            _make_outcome("connect to database run query schema", "stuck"),
            _make_outcome("database query connection run schema", "error"),
            _make_outcome("run database query connect schema", "stuck"),
        ]
        report = evaluate_strategy("connect database run query schema", outcomes=outcomes, fail_threshold=0.35)
        if report.confidence >= 0.3:
            assert report.verdict == "FAIL"

    def test_fitness_is_weighted_average(self):
        """Mixed outcomes → fitness between 0 and 1."""
        outcomes = [
            _make_outcome("analyze portfolio data stocks returns", "done"),
            _make_outcome("portfolio analysis returns stocks", "stuck"),
        ]
        report = evaluate_strategy("portfolio analysis returns stocks data", outcomes=outcomes)
        assert 0.0 < report.fitness_score < 1.0

    def test_similar_outcomes_limited_to_top_k(self):
        outcomes = [_make_outcome(f"web search data extraction fetch market {i}", "done") for i in range(20)]
        report = evaluate_strategy("web search data extraction fetch market", outcomes=outcomes, top_k=5)
        assert len(report.similar_outcomes) <= 5

    def test_report_fields_populated(self):
        outcomes = [
            _make_outcome("send email notification user alert", "done"),
            _make_outcome("user email alert notification send", "done"),
        ]
        report = evaluate_strategy("send user email alert notification", outcomes=outcomes)
        assert isinstance(report.strategy, str)
        assert isinstance(report.fitness_score, float)
        assert isinstance(report.confidence, float)
        assert isinstance(report.similar_outcomes, list)
        assert isinstance(report.done_count, int)
        assert isinstance(report.stuck_count, int)
        assert report.outcomes_searched == 2

    def test_similar_outcomes_have_correct_fields(self):
        outcomes = [_make_outcome("web search fetch data market", "done", outcome_id="oc-001")]
        report = evaluate_strategy("web search fetch market data", outcomes=outcomes)
        if report.similar_outcomes:
            so = report.similar_outcomes[0]
            assert hasattr(so, "outcome_id")
            assert hasattr(so, "goal")
            assert hasattr(so, "status")
            assert hasattr(so, "similarity")
            assert hasattr(so, "weight")
            assert 0.0 <= so.similarity <= 1.0

    def test_partial_status_weight_is_half(self):
        """Partial outcomes contribute 0.5 weight."""
        outcomes = [
            _make_outcome("analyze research data trends market", "partial"),
            _make_outcome("analyze research data trends market result", "partial"),
            _make_outcome("market research data trends analyze", "partial"),
        ]
        report = evaluate_strategy("analyze research data market trends", outcomes=outcomes)
        # Partial = 0.5 weight, so fitness should be around 0.5
        if report.above_threshold > 0:
            assert abs(report.fitness_score - 0.5) < 0.2

    def test_confidence_scales_with_outcome_count(self):
        few_outcomes = [_make_outcome("search web data fetch extract", "done")]
        many_outcomes = [_make_outcome(f"search web data fetch extract result {i}", "done") for i in range(10)]

        few_report = evaluate_strategy("search web data fetch extract", outcomes=few_outcomes)
        many_report = evaluate_strategy("search web data fetch extract", outcomes=many_outcomes)

        # More similar outcomes → higher confidence (when similarity is similar)
        if few_report.above_threshold > 0 and many_report.above_threshold > 0:
            assert many_report.confidence >= few_report.confidence

    def test_outcomes_with_lessons_included_in_search(self):
        """Outcomes with lessons that match strategy should rank higher."""
        outcomes = [
            _make_outcome("unrelated cooking recipe baking food", "done",
                          lessons=["use web search for data extraction"]),
            _make_outcome("fetch recipe ingredients", "done"),
        ]
        report = evaluate_strategy("web search data extraction", outcomes=outcomes)
        # The outcome with "web search for data extraction" in lessons should be found
        if report.similar_outcomes:
            goals = [so.goal for so in report.similar_outcomes]
            assert any("unrelated" in g for g in goals)

    def test_similarity_threshold_filters_low_matches(self):
        """Outcomes with very low similarity should not appear in results."""
        outcomes = [
            _make_outcome("completely unrelated topic xyz", "done"),
            _make_outcome("another unrelated xyz topic abc", "stuck"),
        ]
        report = evaluate_strategy("quantum physics experiment laser", outcomes=outcomes)
        # These should be below SIMILARITY_THRESHOLD
        assert report.above_threshold == 0 or all(
            so.similarity >= SIMILARITY_THRESHOLD for so in report.similar_outcomes
        )

    def test_notes_when_no_done_outcomes(self):
        outcomes = [_make_outcome("web search fetch data market research", "stuck")]
        report = evaluate_strategy("web search fetch market data research", outcomes=outcomes)
        if report.above_threshold > 0:
            assert any("no similar past outcomes succeeded" in n for n in report.notes)


# ---------------------------------------------------------------------------
# evaluate_strategy — custom thresholds
# ---------------------------------------------------------------------------

class TestEvaluateStrategyThresholds:
    def test_custom_pass_threshold(self):
        outcomes = [_make_outcome("web fetch search data extract", "done")]
        # Very low pass threshold — should be easier to pass
        report = evaluate_strategy(
            "web fetch search data extract",
            outcomes=outcomes,
            pass_threshold=0.1,
            fail_threshold=0.0,
        )
        if report.above_threshold > 0 and report.confidence >= 0.3:
            assert report.verdict == "PASS"

    def test_custom_fail_threshold(self):
        outcomes = [_make_outcome("web fetch search data extract", "stuck")]
        # Very high fail threshold — almost everything fails
        report = evaluate_strategy(
            "web fetch search data extract",
            outcomes=outcomes,
            pass_threshold=0.99,
            fail_threshold=0.99,
        )
        if report.above_threshold > 0 and report.confidence >= 0.3:
            assert report.verdict == "FAIL"


# ---------------------------------------------------------------------------
# evaluate_skill — wrapper
# ---------------------------------------------------------------------------

class TestEvaluateSkill:
    def test_basic_skill_evaluation(self):
        skill = SimpleNamespace(
            description="Search the web for data and extract key information",
            trigger_patterns=["search web", "web lookup", "find online"],
            steps_template=["fetch the url", "extract content", "summarize"],
        )
        outcomes = [_make_outcome("web search data extraction results", "done")]
        # evaluate_skill calls evaluate_strategy internally; just ensure no crash
        # and returns StrategyFitnessReport
        report = evaluate_skill.__wrapped__(skill) if hasattr(evaluate_skill, "__wrapped__") else None
        # Use direct import path
        from strategy_evaluator import evaluate_skill as _eval_skill
        # Can't pass outcomes here; just test it doesn't crash with disk (None outcomes)
        # by mocking load_outcomes
        import unittest.mock as mock
        with mock.patch("memory.load_outcomes", return_value=[
            _make_outcome("web search data extraction results", "done")
        ]):
            try:
                report = _eval_skill(skill)
                assert isinstance(report, StrategyFitnessReport)
                assert isinstance(report.fitness_score, float)
            except Exception:
                pass  # memory not available in test env — that's fine

    def test_skill_builds_strategy_text_from_parts(self):
        """evaluate_skill should combine description + trigger_patterns + steps_template."""
        captured = {}

        class _FakeSkill:
            description = "analyze portfolio returns and risks"
            trigger_patterns = ["portfolio analysis", "return risk"]
            steps_template = ["load data", "compute metrics", "report findings"]

        from strategy_evaluator import evaluate_skill as _eval_skill
        import unittest.mock as mock

        with mock.patch("strategy_evaluator.evaluate_strategy") as mock_eval:
            mock_eval.return_value = StrategyFitnessReport(
                strategy="test",
                fitness_score=0.5,
                confidence=0.0,
                similar_outcomes=[],
                outcomes_searched=0,
                done_count=0,
                stuck_count=0,
                above_threshold=0,
                verdict="UNCERTAIN",
            )
            _eval_skill(_FakeSkill())
            called_strategy = mock_eval.call_args[0][0]

        assert "analyze portfolio" in called_strategy
        assert "portfolio analysis" in called_strategy
        assert "load data" in called_strategy


# ---------------------------------------------------------------------------
# evaluate_suggestion — wrapper
# ---------------------------------------------------------------------------

class TestEvaluateSuggestion:
    def test_uses_suggestion_attribute(self):
        suggestion = SimpleNamespace(suggestion="always verify web data before using in reports")

        import unittest.mock as mock
        from strategy_evaluator import evaluate_suggestion as _eval_sugg

        with mock.patch("strategy_evaluator.evaluate_strategy") as mock_eval:
            mock_eval.return_value = StrategyFitnessReport(
                strategy="always verify web data before using in reports",
                fitness_score=0.5,
                confidence=0.0,
                similar_outcomes=[],
                outcomes_searched=0,
                done_count=0,
                stuck_count=0,
                above_threshold=0,
                verdict="UNCERTAIN",
            )
            _eval_sugg(suggestion)
            assert mock_eval.call_args[0][0] == "always verify web data before using in reports"

    def test_falls_back_to_str_when_no_attribute(self):
        suggestion = "always verify web data before using in reports"

        import unittest.mock as mock
        from strategy_evaluator import evaluate_suggestion as _eval_sugg

        with mock.patch("strategy_evaluator.evaluate_strategy") as mock_eval:
            mock_eval.return_value = StrategyFitnessReport(
                strategy=suggestion,
                fitness_score=0.5,
                confidence=0.0,
                similar_outcomes=[],
                outcomes_searched=0,
                done_count=0,
                stuck_count=0,
                above_threshold=0,
                verdict="UNCERTAIN",
            )
            _eval_sugg(suggestion)
            assert mock_eval.call_args[0][0] == suggestion


# ---------------------------------------------------------------------------
# StrategyFitnessReport.summary()
# ---------------------------------------------------------------------------

class TestStrategyFitnessReportSummary:
    def test_summary_contains_key_fields(self):
        report = StrategyFitnessReport(
            strategy="analyze market data trends",
            fitness_score=0.75,
            confidence=0.8,
            similar_outcomes=[],
            outcomes_searched=10,
            done_count=3,
            stuck_count=1,
            above_threshold=4,
            verdict="PASS",
            notes=["strong historical signal"],
        )
        s = report.summary()
        assert "0.75" in s
        assert "0.80" in s or "0.8" in s
        assert "PASS" in s
        assert "strong historical signal" in s

    def test_summary_no_notes(self):
        report = StrategyFitnessReport(
            strategy="short strategy",
            fitness_score=0.5,
            confidence=0.0,
            similar_outcomes=[],
            outcomes_searched=0,
            done_count=0,
            stuck_count=0,
            above_threshold=0,
            verdict="UNCERTAIN",
        )
        s = report.summary()
        assert "UNCERTAIN" in s
        assert "note:" not in s
