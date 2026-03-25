"""Tests for Phase 19: sprint_contract.py

All tests use mock adapters or heuristic mode — no real API calls.
Filesystem I/O uses tmp_path fixtures.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from sprint_contract import (
    ContractGrade,
    SprintContract,
    grade_contract,
    load_contracts,
    negotiate_contract,
    save_contract,
    save_grade,
    _heuristic_criteria,
    _heuristic_grade,
)
from llm import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


class _CriteriaAdapter:
    """Returns a valid criteria JSON."""

    def complete(self, messages, **kwargs):
        payload = {
            "success_criteria": [
                "Research output contains quantified findings",
                "At least 3 sources cited in the result",
                "Findings stored in an artifact file",
            ],
            "acceptance_keywords": ["research", "findings", "analysis", "data"],
        }
        return LLMResponse(
            content=json.dumps(payload),
            stop_reason="end_turn",
            input_tokens=60,
            output_tokens=40,
        )


class _BadJsonAdapter:
    """Returns garbage JSON."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content="not json {{ garbage",
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )


class _GradePassAdapter:
    """Returns a grade JSON with all criteria passing."""

    def complete(self, messages, **kwargs):
        payload = {
            "criteria_results": [
                {"criterion": "Output is non-empty", "passed": True, "evidence": "Result has 200 chars."},
                {"criterion": "Cites sources", "passed": True, "evidence": "References found."},
            ],
            "overall_feedback": "All criteria met. Work is thorough.",
        }
        return LLMResponse(
            content=json.dumps(payload),
            stop_reason="end_turn",
            input_tokens=80,
            output_tokens=60,
        )


class _GradeFailAdapter:
    """Returns a grade JSON with all criteria failing."""

    def complete(self, messages, **kwargs):
        payload = {
            "criteria_results": [
                {"criterion": "Output is non-empty", "passed": False, "evidence": "Result is empty."},
                {"criterion": "Cites sources", "passed": False, "evidence": "No references found."},
            ],
            "overall_feedback": "No criteria met.",
        }
        return LLMResponse(
            content=json.dumps(payload),
            stop_reason="end_turn",
            input_tokens=80,
            output_tokens=60,
        )


class _EmptyCriteriaAdapter:
    """Returns empty criteria list — should trigger heuristic fallback."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content=json.dumps({"success_criteria": [], "acceptance_keywords": []}),
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=10,
        )


# ---------------------------------------------------------------------------
# Test: negotiate_contract — heuristic mode
# ---------------------------------------------------------------------------

def test_negotiate_contract_heuristic(monkeypatch, tmp_path):
    """No adapter → SprintContract with generic heuristic criteria."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = negotiate_contract(
        feature_title="Build a data pipeline for Polymarket",
        mission_goal="Automate trading research",
        adapter=None,
    )
    assert isinstance(contract, SprintContract)
    assert contract.negotiated_by == "heuristic"
    assert len(contract.success_criteria) >= 2
    assert len(contract.acceptance_keywords) >= 1
    assert contract.contract_id
    assert contract.feature_title == "Build a data pipeline for Polymarket"
    assert contract.mission_goal == "Automate trading research"


def test_negotiate_contract_returns_valid_ids(monkeypatch, tmp_path):
    """Contract IDs are non-empty strings."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = negotiate_contract("Research strategies", "Win at Polymarket")
    assert len(contract.contract_id) == 8
    assert contract.feature_id  # auto-generated
    assert contract.created_at


def test_negotiate_contract_with_mock_adapter(monkeypatch, tmp_path):
    """LLM returns valid JSON criteria — used directly."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = negotiate_contract(
        feature_title="Research winning strategies",
        mission_goal="Polymarket research",
        adapter=_CriteriaAdapter(),
    )
    assert contract.negotiated_by == "llm"
    assert len(contract.success_criteria) == 3
    assert "Research output contains quantified findings" in contract.success_criteria
    assert "research" in contract.acceptance_keywords


def test_negotiate_contract_bad_json(monkeypatch, tmp_path):
    """LLM returns bad JSON → falls back to heuristic."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = negotiate_contract(
        feature_title="Build something",
        mission_goal="Deploy it",
        adapter=_BadJsonAdapter(),
    )
    assert contract.negotiated_by == "heuristic"
    assert len(contract.success_criteria) >= 2


def test_negotiate_contract_empty_criteria_fallback(monkeypatch, tmp_path):
    """LLM returns empty criteria list → falls back to heuristic."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = negotiate_contract(
        feature_title="Do something",
        mission_goal="Accomplish it",
        adapter=_EmptyCriteriaAdapter(),
    )
    assert contract.negotiated_by == "heuristic"
    assert len(contract.success_criteria) >= 2


def test_negotiate_contract_milestone_context(monkeypatch, tmp_path):
    """Milestone title is accepted without error."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = negotiate_contract(
        feature_title="Finalize report",
        mission_goal="Quarterly review",
        milestone_title="Phase 2 completion",
    )
    assert contract.mission_goal == "Quarterly review"


def test_negotiate_contract_feature_id_passed(monkeypatch, tmp_path):
    """Explicit feature_id is preserved."""
    _setup_workspace(monkeypatch, tmp_path)
    fid = str(uuid.uuid4())[:8]
    contract = negotiate_contract(
        feature_title="Test feature",
        mission_goal="Test mission",
        feature_id=fid,
    )
    assert contract.feature_id == fid


# ---------------------------------------------------------------------------
# Test: grade_contract — heuristic mode
# ---------------------------------------------------------------------------

def test_grade_contract_heuristic_pass(monkeypatch, tmp_path):
    """Keywords present in work result → passed=True (heuristic)."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="abc12345",
        feature_id="feat0001",
        feature_title="Analyze trading data",
        mission_goal="Build research tool",
        success_criteria=["Output is non-empty and contains substantive content"],
        acceptance_keywords=["trading", "analysis", "data"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="heuristic",
    )
    grade = grade_contract(
        contract,
        "Completed trading analysis: found 3 patterns in the data with strong signals.",
    )
    assert isinstance(grade, ContractGrade)
    assert grade.contract_id == "abc12345"
    assert grade.feature_id == "feat0001"
    assert grade.passed is True
    assert grade.score > 0.5


def test_grade_contract_heuristic_fail(monkeypatch, tmp_path):
    """Empty work result → passed=False."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="xyz99999",
        feature_id="feat0002",
        feature_title="Build pipeline",
        mission_goal="Automate research",
        success_criteria=["Output is non-empty and contains substantive content"],
        acceptance_keywords=["pipeline", "data"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="heuristic",
    )
    grade = grade_contract(contract, "")
    assert grade.passed is False
    assert grade.score == 0.0


def test_grade_contract_heuristic_no_keywords(monkeypatch, tmp_path):
    """Keywords absent from result → lower score."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="kwd00001",
        feature_id="feat0003",
        feature_title="Research Polymarket patterns",
        mission_goal="Win at prediction markets",
        success_criteria=["Output is non-empty and contains substantive content"],
        acceptance_keywords=["polymarket", "prediction", "pattern", "market"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="heuristic",
    )
    # Result doesn't contain any of the keywords
    grade = grade_contract(contract, "I did some work today and it was fine.")
    # Score will be lower because keywords are missing; passed depends on criteria
    assert isinstance(grade.score, float)
    assert 0.0 <= grade.score <= 1.0


def test_grade_contract_with_mock_adapter(monkeypatch, tmp_path):
    """LLM grading adapter returns pass results."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="llm00001",
        feature_id="feat0004",
        feature_title="Research strategies",
        mission_goal="Win at Polymarket",
        success_criteria=["Output is non-empty", "Cites sources"],
        acceptance_keywords=["strategy", "market"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="llm",
    )
    grade = grade_contract(
        contract,
        "Analyzed 10 wallets with citations to market data",
        adapter=_GradePassAdapter(),
    )
    assert grade.passed is True
    assert grade.score == 1.0
    assert len(grade.criteria_results) == 2
    assert all(cr["passed"] for cr in grade.criteria_results)


def test_grade_contract_with_fail_adapter(monkeypatch, tmp_path):
    """LLM grading adapter returns fail results."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="llm00002",
        feature_id="feat0005",
        feature_title="Build pipeline",
        mission_goal="Automate research",
        success_criteria=["Output is non-empty", "Cites sources"],
        acceptance_keywords=["pipeline"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="llm",
    )
    grade = grade_contract(
        contract,
        "Tried but could not complete",
        adapter=_GradeFailAdapter(),
    )
    assert grade.passed is False
    assert grade.score == 0.0


def test_grade_contract_score_range(monkeypatch, tmp_path):
    """Score is always 0.0–1.0 regardless of input."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="range001",
        feature_id="feat0006",
        feature_title="Do something",
        mission_goal="Accomplish it",
        success_criteria=["Output is non-empty", "No errors in result"],
        acceptance_keywords=["something"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="heuristic",
    )
    for work_result in ["", "Hello world", "x" * 10000, "error: something went wrong"]:
        grade = grade_contract(contract, work_result)
        assert 0.0 <= grade.score <= 1.0


def test_grade_empty_result(monkeypatch, tmp_path):
    """Empty work result always → passed=False."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="empty001",
        feature_id="feat0007",
        feature_title="Build thing",
        mission_goal="Make it work",
        success_criteria=["Output is non-empty and contains substantive content"],
        acceptance_keywords=["build", "thing"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="heuristic",
    )
    grade = grade_contract(contract, "")
    assert grade.passed is False


# ---------------------------------------------------------------------------
# Test: persistence
# ---------------------------------------------------------------------------

def test_save_load_contract_roundtrip(monkeypatch, tmp_path):
    """save_contract + load_contracts roundtrip through contracts.jsonl."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    project = "test-project"
    contract = SprintContract(
        contract_id="save0001",
        feature_id="feat0008",
        feature_title="Test roundtrip",
        mission_goal="Verify persistence",
        success_criteria=["Criterion A", "Criterion B"],
        acceptance_keywords=["test", "roundtrip"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="heuristic",
    )
    save_contract(contract, project)
    loaded = load_contracts(project)
    assert len(loaded) == 1
    c = loaded[0]
    assert c.contract_id == "save0001"
    assert c.feature_title == "Test roundtrip"
    assert c.success_criteria == ["Criterion A", "Criterion B"]
    assert c.acceptance_keywords == ["test", "roundtrip"]


def test_save_multiple_contracts(monkeypatch, tmp_path):
    """Multiple contracts saved → multiple loaded."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    project = "test-multi"
    for i in range(3):
        contract = SprintContract(
            contract_id=f"multi{i:03d}",
            feature_id=f"feat{i:04d}",
            feature_title=f"Feature {i}",
            mission_goal="Test",
            success_criteria=["Criterion 1"],
            acceptance_keywords=["test"],
            created_at="2026-01-01T00:00:00Z",
            negotiated_by="heuristic",
        )
        save_contract(contract, project)
    loaded = load_contracts(project)
    assert len(loaded) == 3


def test_load_contracts_empty(monkeypatch, tmp_path):
    """load_contracts on non-existent project → empty list."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    loaded = load_contracts("nonexistent-project")
    assert loaded == []


def test_grade_contract_with_bad_json_adapter_falls_back(monkeypatch, tmp_path):
    """LLM grade returns bad JSON → falls back to heuristic."""
    _setup_workspace(monkeypatch, tmp_path)
    contract = SprintContract(
        contract_id="fbj00001",
        feature_id="feat0009",
        feature_title="Build fallback test",
        mission_goal="Test heuristic fallback",
        success_criteria=["Output is non-empty and contains substantive content"],
        acceptance_keywords=["fallback", "test"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="llm",
    )
    grade = grade_contract(
        contract,
        "Fallback test completed with all results.",
        adapter=_BadJsonAdapter(),
    )
    assert isinstance(grade, ContractGrade)
    assert 0.0 <= grade.score <= 1.0


def test_sprint_contract_to_dict_roundtrip(monkeypatch, tmp_path):
    """to_dict / from_dict roundtrip."""
    contract = SprintContract(
        contract_id="dict0001",
        feature_id="feat0010",
        feature_title="Dict test",
        mission_goal="Test serialization",
        success_criteria=["A", "B"],
        acceptance_keywords=["dict"],
        created_at="2026-01-01T00:00:00Z",
        negotiated_by="llm",
    )
    d = contract.to_dict()
    restored = SprintContract.from_dict(d)
    assert restored.contract_id == contract.contract_id
    assert restored.success_criteria == contract.success_criteria


def test_contract_grade_to_dict_roundtrip(monkeypatch, tmp_path):
    """ContractGrade to_dict / from_dict roundtrip."""
    grade = ContractGrade(
        contract_id="grade001",
        feature_id="feat0011",
        passed=True,
        criteria_results=[{"criterion": "test", "passed": True, "evidence": "ok"}],
        score=0.85,
        feedback="Looking good.",
        graded_at="2026-01-01T00:00:00Z",
    )
    d = grade.to_dict()
    restored = ContractGrade.from_dict(d)
    assert restored.contract_id == "grade001"
    assert restored.passed is True
    assert restored.score == pytest.approx(0.85)


def test_heuristic_criteria_extracts_keywords(monkeypatch, tmp_path):
    """_heuristic_criteria extracts nouns from feature title."""
    criteria, keywords = _heuristic_criteria("Build a research pipeline", "Automate Polymarket")
    assert len(criteria) >= 2
    # "research" and "pipeline" should be in keywords
    assert any(kw in ["research", "pipeline", "build"] for kw in keywords)
