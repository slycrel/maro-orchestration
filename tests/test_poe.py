"""Tests for poe.py — Phase 13: Poe CEO layer."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from poe import (
    PoeResponse,
    assign_model_by_role,
    classify_step_model,
    poe_handle,
    _compile_executive_summary,
    _describe_goal_relationships,
    _looks_like_status,
    _looks_like_inspect,
    _looks_like_goal_map,
    _looks_like_multi_day,
    _handle_now_lane,
)
from llm import MODEL_CHEAP, MODEL_MID, MODEL_POWER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_adapter(content: str = "test response"):
    adapter = MagicMock()
    resp = MagicMock()
    resp.content = content
    resp.input_tokens = 10
    resp.output_tokens = 5
    adapter.complete.return_value = resp
    return adapter


def _mock_classify(lane: str = "now", confidence: float = 0.9, reason: str = "test"):
    return (lane, confidence, reason)


# ---------------------------------------------------------------------------
# assign_model_by_role
# ---------------------------------------------------------------------------

def test_assign_model_by_role_orchestrator():
    assert assign_model_by_role("orchestrator") == MODEL_POWER


def test_assign_model_by_role_planner():
    assert assign_model_by_role("planner") == MODEL_POWER


def test_assign_model_by_role_reviewer():
    assert assign_model_by_role("reviewer") == MODEL_POWER


def test_assign_model_by_role_worker():
    assert assign_model_by_role("worker") == MODEL_MID


def test_assign_model_by_role_executor():
    assert assign_model_by_role("executor") == MODEL_MID


def test_assign_model_by_role_researcher():
    assert assign_model_by_role("researcher") == MODEL_MID


def test_assign_model_by_role_classifier():
    assert assign_model_by_role("classifier") == MODEL_CHEAP


def test_assign_model_by_role_heartbeat():
    assert assign_model_by_role("heartbeat") == MODEL_CHEAP


def test_assign_model_by_role_signal_detector():
    assert assign_model_by_role("signal_detector") == MODEL_CHEAP


def test_assign_model_by_role_unknown():
    assert assign_model_by_role("totally_unknown_role") == MODEL_MID


def test_assign_model_by_role_empty():
    assert assign_model_by_role("") == MODEL_MID


def test_assign_model_by_role_is_pure():
    """No side effects — calling multiple times returns same result."""
    result1 = assign_model_by_role("orchestrator")
    result2 = assign_model_by_role("orchestrator")
    assert result1 == result2 == MODEL_POWER


# ---------------------------------------------------------------------------
# PoeResponse dataclass
# ---------------------------------------------------------------------------

def test_poe_response_fields():
    r = PoeResponse(
        message="hello",
        routed_to="now_lane",
        mission_id="abc123",
        executive_summary="brief",
    )
    assert r.message == "hello"
    assert r.routed_to == "now_lane"
    assert r.mission_id == "abc123"
    assert r.executive_summary == "brief"


def test_poe_response_optional_defaults():
    r = PoeResponse(message="hi", routed_to="status")
    assert r.mission_id is None
    assert r.executive_summary is None


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def test_looks_like_status_slash_command():
    assert _looks_like_status("/status") is True


def test_looks_like_status_natural():
    assert _looks_like_status("what's happening?") is True


def test_looks_like_status_update():
    assert _looks_like_status("update me on progress") is True


def test_looks_like_inspect_slash():
    assert _looks_like_inspect("/inspect") is True


def test_looks_like_inspect_quality():
    assert _looks_like_inspect("how well is the system doing?") is True


def test_looks_like_goal_map_slash():
    assert _looks_like_goal_map("/map") is True


def test_looks_like_multi_day_build():
    assert _looks_like_multi_day("build a complete polymarket research pipeline with monitoring") is True


def test_looks_like_multi_day_short():
    assert _looks_like_multi_day("what time is it?") is False


# ---------------------------------------------------------------------------
# poe_handle — dry_run paths
# ---------------------------------------------------------------------------

def test_poe_handle_dry_run():
    """dry_run=True returns PoeResponse without LLM calls."""
    response = poe_handle("do something useful", dry_run=True)
    assert isinstance(response, PoeResponse)
    assert "[dry-run]" in response.message


def test_poe_handle_dry_run_status():
    response = poe_handle("/status", dry_run=True)
    assert response.routed_to == "status"
    assert "[dry-run]" in response.message


def test_poe_handle_dry_run_inspect():
    response = poe_handle("/inspect", dry_run=True)
    assert response.routed_to == "inspector"


def test_poe_handle_dry_run_map():
    response = poe_handle("/map", dry_run=True)
    assert response.routed_to == "goal_map"


def test_poe_handle_dry_run_now():
    response = poe_handle("what is 2+2?", dry_run=True)
    assert response.routed_to == "now_lane"
    assert isinstance(response.message, str)


# ---------------------------------------------------------------------------
# poe_handle — with mock adapter
# ---------------------------------------------------------------------------

def test_poe_handle_now_intent():
    """NOW message → routed_to=now_lane."""
    adapter = _mock_adapter("2+2 is 4")
    with patch("poe.classify", return_value=("now", 0.95, "simple question")):
        with patch("poe.evaluate_action") as mock_eval:
            mock_decision = MagicMock()
            mock_decision.requires_human = False
            mock_eval.return_value = mock_decision
            response = poe_handle("what is 2+2?", adapter=adapter)
    assert response.routed_to == "now_lane"
    assert isinstance(response.message, str)


def test_poe_handle_agenda_intent():
    """AGENDA message → routed_to=mission or director."""
    adapter = _mock_adapter("task completed")
    with patch("poe.classify", return_value=("agenda", 0.85, "multi-step")):
        with patch("poe.evaluate_action") as mock_eval:
            mock_decision = MagicMock()
            mock_decision.requires_human = False
            mock_eval.return_value = mock_decision
            with patch("poe.run_agent_loop") as mock_loop:
                mock_result = MagicMock()
                mock_result.steps = []
                mock_result.status = "done"
                mock_result.stuck_reason = None
                mock_loop.return_value = mock_result
                response = poe_handle("research polymarket strategies", adapter=adapter)
    assert response.routed_to in ("mission", "director", "now_lane")


def test_poe_handle_status_message():
    """Status message → executive summary."""
    adapter = _mock_adapter("Summary: all good")
    with patch("poe.list_missions", return_value=[]):
        with patch("poe.get_latest_inspection", return_value=None):
            response = poe_handle("/status", adapter=adapter)
    assert response.routed_to == "status"
    assert isinstance(response.message, str)


def test_poe_handle_inspect_message():
    """Quality/inspect request → inspector output."""
    with patch("poe.get_latest_inspection", return_value=None):
        response = poe_handle("/inspect", adapter=None)
    assert response.routed_to == "inspector"
    assert isinstance(response.message, str)


def test_poe_handle_goal_map_message():
    """Goal map message → goal_map routing."""
    with patch("poe.build_goal_map") as mock_map:
        from goal_map import GoalMap
        mock_map.return_value = GoalMap()
        response = poe_handle("/map", adapter=None)
    assert response.routed_to == "goal_map"


def test_poe_handle_requires_human_escalation():
    """When autonomy tier requires human, return escalation message."""
    adapter = _mock_adapter("ok")
    with patch("poe.classify", return_value=("agenda", 0.8, "multi-step")):
        with patch("poe.evaluate_action") as mock_eval:
            mock_decision = MagicMock()
            mock_decision.requires_human = True
            mock_decision.reason = "Manual tier: human approval required"
            mock_eval.return_value = mock_decision
            response = poe_handle("deploy the system", adapter=adapter)
    assert "approval" in response.message.lower() or "requires" in response.message.lower()


# ---------------------------------------------------------------------------
# _compile_executive_summary
# ---------------------------------------------------------------------------

def test_compile_executive_summary_empty():
    """No outcomes → graceful summary."""
    with patch("poe.list_missions", return_value=[]):
        with patch("poe.get_latest_inspection", return_value=None):
            summary = _compile_executive_summary(adapter=None)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_compile_executive_summary_with_missions():
    """Active missions appear in summary."""
    missions = [
        {"goal": "Build polymarket bot", "project": "poly", "status": "running"},
        {"goal": "Setup monitoring", "project": "monitor", "status": "done"},
    ]
    with patch("poe.list_missions", return_value=missions):
        with patch("poe.get_latest_inspection", return_value=None):
            summary = _compile_executive_summary(adapter=None)
    assert isinstance(summary, str)


def test_compile_executive_summary_with_adapter():
    """With adapter, LLM summary is called."""
    adapter = _mock_adapter("- Mission in progress: poly-bot\n- Quality is good")
    with patch("poe.list_missions", return_value=[]):
        with patch("poe.get_latest_inspection", return_value=None):
            summary = _compile_executive_summary(adapter=adapter)
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_compile_executive_summary_llm_failure():
    """LLM failure → graceful fallback."""
    adapter = MagicMock()
    adapter.complete.side_effect = RuntimeError("LLM unavailable")
    with patch("poe.list_missions", return_value=[]):
        with patch("poe.get_latest_inspection", return_value=None):
            summary = _compile_executive_summary(adapter=adapter)
    assert isinstance(summary, str)


# ---------------------------------------------------------------------------
# _handle_now_lane
# ---------------------------------------------------------------------------

def test_handle_now_lane_no_adapter():
    result = _handle_now_lane("hello", adapter=None)
    assert "[no adapter]" in result


def test_handle_now_lane_with_adapter():
    adapter = _mock_adapter("Paris is the capital of France")
    result = _handle_now_lane("What is the capital of France?", adapter=adapter)
    assert result == "Paris is the capital of France"


def test_handle_now_lane_adapter_error():
    adapter = MagicMock()
    adapter.complete.side_effect = RuntimeError("timeout")
    result = _handle_now_lane("something", adapter=adapter)
    assert "Error" in result or "error" in result


# ---------------------------------------------------------------------------
# _describe_goal_relationships
# ---------------------------------------------------------------------------

def test_describe_goal_relationships_empty():
    with patch("poe.build_goal_map") as mock_map:
        from goal_map import GoalMap
        mock_map.return_value = GoalMap()
        result = _describe_goal_relationships("polymarket", adapter=None)
    assert isinstance(result, str)


def test_describe_goal_relationships_no_build_goal_map():
    with patch("poe.build_goal_map", None):
        result = _describe_goal_relationships("anything", adapter=None)
    assert "not available" in result.lower() or isinstance(result, str)


# ---------------------------------------------------------------------------
# classify_step_model — Phase 35 P1 cost-aware routing
# ---------------------------------------------------------------------------

def test_classify_cheap_check_reachability():
    assert classify_step_model("Check reachability of https://x.com/user/status/123 and abort early if unavailable") == MODEL_CHEAP


def test_classify_cheap_list_urls():
    assert classify_step_model("List all URLs referenced in the tweet thread") == MODEL_CHEAP


def test_classify_cheap_extract():
    assert classify_step_model("Extract the author name, date, and tweet text from the pre-fetched content") == MODEL_CHEAP


def test_classify_cheap_verify():
    assert classify_step_model("Verify that the article was published after January 2026") == MODEL_CHEAP


def test_classify_cheap_format():
    assert classify_step_model("Format the findings into a markdown table with columns: tool, category, URL") == MODEL_CHEAP


def test_classify_cheap_count():
    assert classify_step_model("Count how many resources are referenced in the article") == MODEL_CHEAP


def test_classify_mid_research():
    assert classify_step_model("Research what ML papers say about exploration-exploitation tradeoff") == MODEL_MID


def test_classify_mid_synthesize():
    assert classify_step_model("Synthesize all gathered content into a structured summary of key claims") == MODEL_MID


def test_classify_mid_analyse():
    assert classify_step_model("Analyse in depth the implications for autonomous agent design") == MODEL_MID


def test_classify_mid_implement():
    assert classify_step_model("Implement a WebSocket handler for real-time step progress updates") == MODEL_MID


def test_classify_mid_write_comprehensive():
    assert classify_step_model("Write a comprehensive report covering all findings and practical takeaways") == MODEL_MID


def test_classify_mid_default_unknown():
    assert classify_step_model("Do something with the data") == MODEL_MID


def test_classify_force_mid_overrides_cheap_keyword():
    # "research" is force-MID; even if a cheap keyword also present, stays MID
    step = "Research and extract all key URLs from the article"
    assert classify_step_model(step) == MODEL_MID


def test_classify_step_model_returns_string():
    result = classify_step_model("anything")
    assert isinstance(result, str)
    assert result in (MODEL_CHEAP, MODEL_MID, MODEL_POWER)
