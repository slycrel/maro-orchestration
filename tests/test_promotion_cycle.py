"""Tests for Phase 56: promotion cycle (standing rules + decision journal)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import memory as mem_module
import knowledge_lens as _kl_module
from memory import (
    Decision,
    Hypothesis,
    StandingRule,
    check_contradiction,
    contradict_pattern,
    inject_decisions,
    inject_standing_rules,
    load_hypotheses,
    load_standing_rules,
    observe_pattern,
    record_decision,
    search_decisions,
    RULE_PROMOTE_CONFIRMATIONS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def tmp_memory(tmp_path, monkeypatch):
    """Redirect all memory file paths to a temp directory."""
    monkeypatch.setattr(mem_module, "_memory_dir", lambda: tmp_path)
    monkeypatch.setattr(_kl_module, "_memory_dir", lambda: tmp_path)
    yield tmp_path


# ---------------------------------------------------------------------------
# StandingRule dataclass
# ---------------------------------------------------------------------------


class TestStandingRule:
    def test_roundtrip(self):
        r = StandingRule(
            rule_id="abc", rule="Always verify.", source_lesson_id="l1",
            domain="research", confirmations=3, contradictions=0,
            promoted_at="2026-04-01", last_applied="",
        )
        r2 = StandingRule.from_dict(r.to_dict())
        assert r2.rule_id == "abc"
        assert r2.rule == "Always verify."
        assert r2.confirmations == 3

    def test_from_dict_missing_fields(self):
        # Should not raise on missing optional fields
        r = StandingRule.from_dict({"rule_id": "x", "rule": "r", "source_lesson_id": "",
                                     "domain": "", "confirmations": 1, "contradictions": 0,
                                     "promoted_at": "2026-01-01"})
        assert r.last_applied == ""


# ---------------------------------------------------------------------------
# Hypothesis dataclass
# ---------------------------------------------------------------------------


class TestHypothesis:
    def test_roundtrip(self):
        h = Hypothesis(
            hyp_id="hx", lesson="Use Jina for fetches.", domain="fetch",
            confirmations=1, contradictions=0, source_lesson_ids=["l1"],
            first_seen="2026-04-01", last_seen="2026-04-01",
        )
        h2 = Hypothesis.from_dict(h.to_dict())
        assert h2.hyp_id == "hx"
        assert h2.lesson == "Use Jina for fetches."


# ---------------------------------------------------------------------------
# observe_pattern — hypothesis creation and promotion
# ---------------------------------------------------------------------------


class TestObservePattern:
    def test_first_observation_creates_hypothesis(self, tmp_path):
        result = observe_pattern("Always validate before writing.", "build")
        assert result is None  # not yet promoted
        hyps = load_hypotheses()
        assert len(hyps) == 1
        assert hyps[0].confirmations == 1

    def test_second_observation_promotes_if_threshold_is_2(self, tmp_path):
        assert RULE_PROMOTE_CONFIRMATIONS == 2
        observe_pattern("Always validate before writing.", "build")
        rule = observe_pattern("Always validate before writing.", "build")
        assert rule is not None
        assert rule.rule == "Always validate before writing."
        # Hypothesis removed
        assert load_hypotheses() == []
        # Rule persisted
        rules = load_standing_rules()
        assert len(rules) == 1
        assert rules[0].confirmations == 2

    def test_different_lesson_creates_separate_hypothesis(self, tmp_path):
        observe_pattern("Lesson A.", "research")
        observe_pattern("Lesson B.", "research")
        hyps = load_hypotheses()
        assert len(hyps) == 2

    def test_source_lesson_id_tracked(self, tmp_path):
        observe_pattern("Always verify.", "ops", source_lesson_id="l1")
        rule = observe_pattern("Always verify.", "ops", source_lesson_id="l2")
        assert rule is not None
        assert rule.source_lesson_id == "l1"

    def test_returns_rule_on_promotion(self, tmp_path):
        observe_pattern("Reuse existing adapters.", "llm")
        rule = observe_pattern("Reuse existing adapters.", "llm")
        assert isinstance(rule, StandingRule)
        assert rule.domain == "llm"


# ---------------------------------------------------------------------------
# contradict_pattern
# ---------------------------------------------------------------------------


class TestContradictPattern:
    def test_contradicts_hypothesis(self, tmp_path):
        observe_pattern("Cache LLM responses.", "perf")
        result = contradict_pattern("Cache LLM responses.", "perf")
        assert result is True
        hyps = load_hypotheses()
        # contradictions == confirmations == 1 → NOT yet demoted (need contradictions > confirmations)
        assert len(hyps) == 1
        assert hyps[0].contradictions == 1

    def test_contradicts_hypothesis_demotes_when_contradictions_exceed(self, tmp_path):
        observe_pattern("Cache LLM responses.", "perf")
        contradict_pattern("Cache LLM responses.", "perf")  # contradictions == confirmations
        contradict_pattern("Cache LLM responses.", "perf")  # contradictions > confirmations → demote
        hyps = load_hypotheses()
        assert len(hyps) == 0

    def test_contradicts_standing_rule(self, tmp_path):
        observe_pattern("Always use JSON output.", "format")
        observe_pattern("Always use JSON output.", "format")  # promotes
        rules = load_standing_rules()
        assert len(rules) == 1
        result = contradict_pattern("Always use JSON output.", "format")
        assert result is True
        rules = load_standing_rules()
        assert rules[0].contradictions == 1

    def test_no_match_returns_false(self, tmp_path):
        assert not contradict_pattern("Something never said.", "ops")

    def test_contradiction_hits_standing_rule_not_hypothesis_after_promotion(self, tmp_path):
        observe_pattern("Cache responses.", "perf")
        observe_pattern("Cache responses.", "perf")  # promotes → rule now, hypothesis removed
        # contradiction hits the rule (not the hypothesis, which is gone)
        assert contradict_pattern("Cache responses.", "perf")
        rules = load_standing_rules()
        assert rules[0].contradictions == 1


# ---------------------------------------------------------------------------
# check_contradiction
# ---------------------------------------------------------------------------


class TestCheckContradiction:
    def test_detects_always_vs_never(self, tmp_path):
        """'Always verify' contradicts 'Never verify'."""
        rules = [StandingRule(
            rule_id="r1", rule="Always verify output before returning",
            source_lesson_id="", domain="ops", confirmations=3,
            contradictions=0, promoted_at="2026-01-01",
        )]
        result = check_contradiction("Never verify output before returning", rules)
        assert result is not None
        assert result.rule_id == "r1"

    def test_detects_skip_vs_require(self, tmp_path):
        """'Skip validation' contradicts 'Require validation'."""
        rules = [StandingRule(
            rule_id="r2", rule="Always require validation on research tasks",
            source_lesson_id="", domain="research", confirmations=2,
            contradictions=0, promoted_at="2026-01-01",
        )]
        result = check_contradiction("Skip validation on research tasks", rules)
        assert result is not None

    def test_no_contradiction_different_topics(self, tmp_path):
        """Rules about different topics are not contradictions."""
        rules = [StandingRule(
            rule_id="r3", rule="Always verify output before returning",
            source_lesson_id="", domain="ops", confirmations=3,
            contradictions=0, promoted_at="2026-01-01",
        )]
        result = check_contradiction("Never deploy on Friday", rules)
        assert result is None

    def test_no_contradiction_same_direction(self, tmp_path):
        """Rules in the same direction are not contradictions."""
        rules = [StandingRule(
            rule_id="r4", rule="Always verify output",
            source_lesson_id="", domain="ops", confirmations=3,
            contradictions=0, promoted_at="2026-01-01",
        )]
        result = check_contradiction("Always validate output", rules)
        assert result is None

    def test_empty_rules_no_contradiction(self, tmp_path):
        assert check_contradiction("anything", []) is None


class TestPromotionContradictionGate:
    """observe_pattern blocks promotion when candidate contradicts existing rule."""

    def test_promotion_blocked_by_contradiction(self, tmp_path):
        """Hypothesis matching existing rule in opposite direction is blocked."""
        # Create a standing rule first
        observe_pattern("Always verify results", "ops")
        observe_pattern("Always verify results", "ops")  # promoted
        rules = load_standing_rules()
        assert len(rules) == 1

        # Try to promote a contradicting hypothesis
        observe_pattern("Never verify results", "ops")
        observe_pattern("Never verify results", "ops")  # would promote, but blocked
        rules = load_standing_rules()
        assert len(rules) == 1  # still just the original
        assert rules[0].rule == "Always verify results"

    def test_promotion_succeeds_without_contradiction(self, tmp_path):
        """Non-contradicting hypothesis promotes normally."""
        observe_pattern("Always verify results", "ops")
        observe_pattern("Always verify results", "ops")
        observe_pattern("Log all errors to disk", "ops")
        observe_pattern("Log all errors to disk", "ops")
        rules = load_standing_rules()
        assert len(rules) == 2


# ---------------------------------------------------------------------------
# load_standing_rules / inject_standing_rules
# ---------------------------------------------------------------------------


class TestStandingRulesInjection:
    def test_empty_returns_empty_string(self):
        assert inject_standing_rules() == ""

    def test_rules_injected_with_header(self, tmp_path):
        observe_pattern("Always fetch via Jina.", "fetch")
        observe_pattern("Always fetch via Jina.", "fetch")
        result = inject_standing_rules()
        assert "Standing Rules" in result
        assert "Always fetch via Jina." in result

    def test_domain_filter(self, tmp_path):
        observe_pattern("Rule for ops.", "ops")
        observe_pattern("Rule for ops.", "ops")
        observe_pattern("Rule for research.", "research")
        observe_pattern("Rule for research.", "research")
        ops_only = inject_standing_rules(domain="ops")
        assert "Rule for ops." in ops_only
        assert "Rule for research." not in ops_only


# ---------------------------------------------------------------------------
# Decision journal
# ---------------------------------------------------------------------------


class TestRecordDecision:
    def test_records_and_persists(self, tmp_path):
        d = record_decision(
            "Use TF-IDF for lesson ranking.",
            "Sklearn unavailable; pure stdlib TF-IDF is sufficient for current corpus size.",
            domain="memory",
            alternatives=["sklearn TF-IDF", "embedding similarity"],
            trade_offs="No semantic understanding; purely lexical.",
        )
        assert d.decision_id
        path = tmp_path / "decisions.jsonl"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["decision"] == "Use TF-IDF for lesson ranking."

    def test_multiple_decisions(self, tmp_path):
        record_decision("Decision A.", "Reason A.", domain="ops")
        record_decision("Decision B.", "Reason B.", domain="memory")
        path = tmp_path / "decisions.jsonl"
        lines = [l for l in path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2


class TestSearchDecisions:
    def test_finds_relevant_decision(self, tmp_path):
        record_decision("Use Jina prefix for web fetches.", "Jina strips paywalls.", domain="fetch")
        record_decision("Use short-term memory for session context.", "Avoids repeated lookups.", domain="memory")
        results = search_decisions("fetch web pages with Jina")
        assert len(results) >= 1
        assert any("Jina" in r.decision for r in results)

    def test_empty_journal_returns_empty(self):
        assert search_decisions("anything") == []

    def test_domain_filter(self, tmp_path):
        record_decision("Fetch rule.", "Fetch reason.", domain="fetch")
        record_decision("Memory rule.", "Memory reason.", domain="memory")
        results = search_decisions("context", domain="memory")
        assert all(r.domain == "memory" or r.domain == "" for r in results)


class TestInjectDecisions:
    def test_empty_returns_empty_string(self):
        assert inject_decisions("any goal") == ""

    def test_injects_header_and_decision(self, tmp_path):
        record_decision("Always verify outputs.", "Hallucination risk.", domain="research")
        result = inject_decisions("research goal about facts")
        assert "Prior Decisions" in result
        assert "Always verify outputs." in result

    def test_alternatives_included(self, tmp_path):
        record_decision("Use Jina.", "Reliable.", domain="fetch", alternatives=["raw curl", "scrapy"])
        result = inject_decisions("fetch a web page")
        assert "raw curl" in result or "scrapy" in result


# ---------------------------------------------------------------------------
# Decision dataclass
# ---------------------------------------------------------------------------


class TestDecision:
    def test_roundtrip(self):
        d = Decision(
            decision_id="d1", domain="ops", decision="Deploy nightly.",
            alternatives=["weekly", "on-demand"], rationale="Fresh data daily.",
            trade_offs="More compute.", recorded_at="2026-04-01", goal_context="",
        )
        d2 = Decision.from_dict(d.to_dict())
        assert d2.decision_id == "d1"
        assert d2.alternatives == ["weekly", "on-demand"]
