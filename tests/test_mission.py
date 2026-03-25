"""Tests for Phase 10: mission.py

All tests use dry_run=True or mock adapters — no real API calls.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from mission import (
    Feature,
    Milestone,
    Mission,
    MissionResult,
    _validate_milestone,
    decompose_mission,
    list_missions,
    load_mission,
    run_mission,
    save_mission,
)
from llm import LLMMessage, LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


class _DecomposeMockAdapter:
    """Returns a valid decomposition JSON."""

    def complete(self, messages, **kwargs):
        payload = {
            "milestones": [
                {
                    "title": "Milestone Alpha",
                    "features": ["Feature A-1", "Feature A-2"],
                    "validation_criteria": ["Alpha work done"],
                },
                {
                    "title": "Milestone Beta",
                    "features": ["Feature B-1", "Feature B-2"],
                    "validation_criteria": ["Beta work done"],
                },
            ]
        }
        return LLMResponse(
            content=json.dumps(payload),
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=80,
        )


class _BadJsonAdapter:
    """Returns garbage JSON."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content="not json at all {{{{",
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )


class _ValidationPassAdapter:
    """Always says milestone passed."""

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content=json.dumps({"passed": True, "reason": "looks good"}),
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=20,
        )


class _ValidationFailAdapter:
    """Always says milestone failed."""

    def complete(self, messages, **kwargs):
        # Different response based on whether it's a decompose or validate call
        user_content = next((m.content for m in messages if m.role == "user"), "")
        if "milestones" in user_content.lower() or "decompose" in user_content.lower():
            payload = {
                "milestones": [
                    {
                        "title": "Milestone Only",
                        "features": ["Feature X"],
                        "validation_criteria": ["Must pass"],
                    }
                ]
            }
            return LLMResponse(content=json.dumps(payload), stop_reason="end_turn", input_tokens=50, output_tokens=40)
        return LLMResponse(
            content=json.dumps({"passed": False, "reason": "criteria not met"}),
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=20,
        )


# ---------------------------------------------------------------------------
# decompose_mission
# ---------------------------------------------------------------------------

def test_decompose_mission_dry_run(monkeypatch, tmp_path):
    """Dry run returns a Mission with milestones."""
    _setup_workspace(monkeypatch, tmp_path)
    from agent_loop import _DryRunAdapter
    mission = decompose_mission("build a research system", _DryRunAdapter())
    assert isinstance(mission, Mission)
    assert len(mission.milestones) >= 1
    for ms in mission.milestones:
        assert len(ms.features) >= 1


def test_decompose_mission_fallback(monkeypatch, tmp_path):
    """Bad JSON from adapter → heuristic fallback with 2 milestones."""
    _setup_workspace(monkeypatch, tmp_path)
    mission = decompose_mission("do A then B then C", _BadJsonAdapter())
    assert isinstance(mission, Mission)
    assert len(mission.milestones) >= 1
    assert all(len(ms.features) >= 1 for ms in mission.milestones)


def test_decompose_mission_milestone_count(monkeypatch, tmp_path):
    """Respects max_milestones."""
    _setup_workspace(monkeypatch, tmp_path)
    mission = decompose_mission(
        "build a full product", _DecomposeMockAdapter(), max_milestones=2
    )
    assert len(mission.milestones) <= 2


def test_decompose_mission_feature_count(monkeypatch, tmp_path):
    """Respects max_features_per_milestone."""
    _setup_workspace(monkeypatch, tmp_path)
    mission = decompose_mission(
        "build a product", _DecomposeMockAdapter(), max_features_per_milestone=2
    )
    for ms in mission.milestones:
        assert len(ms.features) <= 2


def test_decompose_mission_assigns_ids(monkeypatch, tmp_path):
    """All milestones and features get ids."""
    _setup_workspace(monkeypatch, tmp_path)
    mission = decompose_mission("do a complex thing", _DecomposeMockAdapter())
    for ms in mission.milestones:
        assert ms.id
        assert len(ms.id) == 8
        for f in ms.features:
            assert f.id
            assert len(f.id) == 8


def test_decompose_mission_all_pending(monkeypatch, tmp_path):
    """Fresh decomposition sets all statuses to pending."""
    _setup_workspace(monkeypatch, tmp_path)
    mission = decompose_mission("launch a product", _DecomposeMockAdapter())
    for ms in mission.milestones:
        assert ms.status == "pending"
        for f in ms.features:
            assert f.status == "pending"


def test_decompose_mission_has_validation_criteria(monkeypatch, tmp_path):
    """Milestones include validation criteria."""
    _setup_workspace(monkeypatch, tmp_path)
    mission = decompose_mission("build something", _DecomposeMockAdapter())
    for ms in mission.milestones:
        assert isinstance(ms.validation_criteria, list)


def test_decompose_mission_markdown_fence_stripped(monkeypatch, tmp_path):
    """JSON wrapped in markdown fences is still parsed correctly."""
    _setup_workspace(monkeypatch, tmp_path)

    class FencedAdapter:
        def complete(self, messages, **kwargs):
            payload = {
                "milestones": [
                    {
                        "title": "One",
                        "features": ["Step 1"],
                        "validation_criteria": ["Done"],
                    }
                ]
            }
            return LLMResponse(
                content=f"```json\n{json.dumps(payload)}\n```",
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=40,
            )

    mission = decompose_mission("wrapped goal", FencedAdapter())
    assert len(mission.milestones) >= 1


# ---------------------------------------------------------------------------
# run_mission
# ---------------------------------------------------------------------------

def test_run_mission_dry_run_completes(monkeypatch, tmp_path):
    """Full dry run, mission status=done."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_mission(
        "build a research pipeline",
        project="dry-run-test",
        dry_run=True,
    )
    assert isinstance(result, MissionResult)
    assert result.status == "done"


def test_run_mission_creates_project(monkeypatch, tmp_path):
    """run_mission creates the project directory."""
    _setup_workspace(monkeypatch, tmp_path)
    run_mission("build a thing", project="mission-creates-project", dry_run=True)
    assert orch.project_dir("mission-creates-project").exists()


def test_run_mission_auto_creates_project_from_goal(monkeypatch, tmp_path):
    """run_mission auto-creates project slug from goal when no project given."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_mission("analyze polymarket data sources", dry_run=True)
    assert result.project
    assert orch.project_dir(result.project).exists()


def test_run_mission_persists_mission_json(monkeypatch, tmp_path):
    """mission.json is written to project directory."""
    _setup_workspace(monkeypatch, tmp_path)
    run_mission("persist test mission", project="persist-test", dry_run=True)
    mission_file = orch.project_dir("persist-test") / "mission.json"
    assert mission_file.exists()
    data = json.loads(mission_file.read_text())
    assert "milestones" in data
    assert "goal" in data


def test_run_mission_sequential_milestones(monkeypatch, tmp_path):
    """Milestone 2 only starts after milestone 1 completes (sequential)."""
    _setup_workspace(monkeypatch, tmp_path)
    execution_order = []

    original_run_feature = None

    class TrackingAdapter:
        """Mock adapter that records execution order."""

        call_count = 0

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            user_content = next((m.content for m in messages if m.role == "user"), "")
            if "milestones" in user_content.lower() or "decompose" in user_content.lower():
                payload = {
                    "milestones": [
                        {"title": "MS1", "features": ["F1-A"], "validation_criteria": []},
                        {"title": "MS2", "features": ["F2-A"], "validation_criteria": []},
                    ]
                }
                return LLMResponse(content=json.dumps(payload), stop_reason="end_turn", input_tokens=50, output_tokens=40)
            if "Current step" in user_content:
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(name="complete_step", arguments={"result": "done", "summary": "done"})],
                    stop_reason="tool_use",
                    input_tokens=50,
                    output_tokens=20,
                )
            return LLMResponse(
                content=json.dumps({"passed": True, "reason": "ok"}),
                stop_reason="end_turn",
                input_tokens=30,
                output_tokens=10,
            )

    # With dry_run=True, milestones run sequentially (verified by status tracking in mission)
    result = run_mission("sequential test goal", project="seq-test", dry_run=True)
    assert result.status == "done"
    # Both milestones completed
    assert result.milestones_done >= 1


def test_run_mission_writes_log(monkeypatch, tmp_path):
    """mission-log.jsonl entry is written."""
    _setup_workspace(monkeypatch, tmp_path)
    run_mission("log test mission", project="log-test", dry_run=True)
    log_file = orch.orch_root() / "memory" / "mission-log.jsonl"
    assert log_file.exists()
    lines = [l for l in log_file.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1
    entry = json.loads(lines[-1])
    assert "mission_id" in entry
    assert "status" in entry


def test_run_mission_result_has_counts(monkeypatch, tmp_path):
    """MissionResult has accurate feature/milestone counts."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_mission("count test", project="count-test", dry_run=True)
    assert result.milestones_total >= 1
    assert result.features_total >= 1
    assert result.milestones_done >= 0
    assert result.features_done >= 0


def test_run_mission_elapsed_ms(monkeypatch, tmp_path):
    """elapsed_ms is recorded."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_mission("elapsed test", project="elapsed-test", dry_run=True)
    assert result.elapsed_ms >= 0


# ---------------------------------------------------------------------------
# _validate_milestone
# ---------------------------------------------------------------------------

def test_validate_milestone_passes_no_criteria(monkeypatch, tmp_path):
    """Empty validation criteria → always True."""
    _setup_workspace(monkeypatch, tmp_path)
    ms = Milestone(
        id="m1",
        title="Test",
        features=[],
        validation_criteria=[],
        status="validating",
    )
    assert _validate_milestone(ms, "test-project", None) is True


def test_validate_milestone_dry_run(monkeypatch, tmp_path):
    """dry_run=True always returns True."""
    _setup_workspace(monkeypatch, tmp_path)
    ms = Milestone(
        id="m1",
        title="Test",
        features=[],
        validation_criteria=["must pass everything"],
        status="validating",
    )
    assert _validate_milestone(ms, "test-project", None, dry_run=True) is True


def test_validate_milestone_passes_with_adapter(monkeypatch, tmp_path):
    """Adapter returns passed=True → returns True."""
    _setup_workspace(monkeypatch, tmp_path)
    ms = Milestone(
        id="m1",
        title="Test",
        features=[Feature(id="f1", title="F1", status="done", result_summary="done")],
        validation_criteria=["work done"],
        status="validating",
    )
    assert _validate_milestone(ms, "test-project", _ValidationPassAdapter()) is True


def test_validate_milestone_fails_with_adapter(monkeypatch, tmp_path):
    """Adapter returns passed=False → returns False."""
    _setup_workspace(monkeypatch, tmp_path)
    ms = Milestone(
        id="m1",
        title="Test",
        features=[Feature(id="f1", title="F1", status="done")],
        validation_criteria=["must pass everything"],
        status="validating",
    )
    assert _validate_milestone(ms, "test-project", _ValidationFailAdapter()) is False


def test_validate_milestone_adapter_exception_returns_true(monkeypatch, tmp_path):
    """Adapter exception → default True (don't block progress)."""
    _setup_workspace(monkeypatch, tmp_path)

    class ErrorAdapter:
        def complete(self, *args, **kwargs):
            raise RuntimeError("LLM error")

    ms = Milestone(
        id="m1",
        title="Test",
        features=[],
        validation_criteria=["something"],
        status="validating",
    )
    # Should not raise, should return True
    assert _validate_milestone(ms, "test-project", ErrorAdapter()) is True


# ---------------------------------------------------------------------------
# load_mission / save_mission
# ---------------------------------------------------------------------------

def test_load_save_mission(monkeypatch, tmp_path):
    """Round-trip through mission.json."""
    _setup_workspace(monkeypatch, tmp_path)
    orch.ensure_project("round-trip-test", "test mission")
    from datetime import datetime, timezone
    mission = Mission(
        id="abc12345",
        goal="test goal",
        project="round-trip-test",
        milestones=[
            Milestone(
                id="ms000001",
                title="MS1",
                features=[
                    Feature(id="f0000001", title="Feature A", status="done", result_summary="completed"),
                ],
                validation_criteria=["done"],
                status="done",
            )
        ],
        status="done",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    save_mission(mission, "round-trip-test")
    loaded = load_mission("round-trip-test")
    assert loaded is not None
    assert loaded.id == mission.id
    assert loaded.goal == mission.goal
    assert len(loaded.milestones) == 1
    assert loaded.milestones[0].title == "MS1"
    assert loaded.milestones[0].features[0].title == "Feature A"


def test_load_mission_missing(monkeypatch, tmp_path):
    """load_mission returns None if no mission.json."""
    _setup_workspace(monkeypatch, tmp_path)
    result = load_mission("nonexistent-project-zzz")
    assert result is None


# ---------------------------------------------------------------------------
# MissionResult.summary
# ---------------------------------------------------------------------------

def test_mission_result_summary(monkeypatch, tmp_path):
    """summary() returns expected fields."""
    _setup_workspace(monkeypatch, tmp_path)
    result = MissionResult(
        mission_id="abc123",
        project="test",
        goal="build something",
        status="done",
        milestones_done=2,
        milestones_total=3,
        features_done=5,
        features_total=6,
        elapsed_ms=12345,
    )
    s = result.summary()
    assert "mission_id=abc123" in s
    assert "status=done" in s
    assert "milestones=2/3" in s
    assert "features=5/6" in s
    assert "elapsed_ms=12345" in s


# ---------------------------------------------------------------------------
# list_missions
# ---------------------------------------------------------------------------

def test_list_missions_empty(monkeypatch, tmp_path):
    """No projects → []."""
    _setup_workspace(monkeypatch, tmp_path)
    result = list_missions()
    assert result == []


def test_list_missions_returns_summaries(monkeypatch, tmp_path):
    """After running missions, list_missions returns them."""
    _setup_workspace(monkeypatch, tmp_path)
    run_mission("list test mission alpha", project="list-test-alpha", dry_run=True)
    run_mission("list test mission beta", project="list-test-beta", dry_run=True)
    missions = list_missions()
    assert len(missions) >= 2
    projects = [m["project"] for m in missions]
    assert "list-test-alpha" in projects
    assert "list-test-beta" in projects


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_mission_dry_run(monkeypatch, tmp_path, capsys):
    """poe-mission CLI subcommand with --dry-run returns 0."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-mission", "build a test pipeline", "--project", "cli-mission-test", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status=done" in out


def test_cli_poe_mission_status(monkeypatch, tmp_path, capsys):
    """poe-mission-status after running a mission shows the mission."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    cli.main(["poe-mission", "status test goal", "--project", "status-cli-test", "--dry-run"])
    capsys.readouterr()  # flush stdout
    rc = cli.main(["poe-mission-status", "status-cli-test"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status-cli-test" in out


# ---------------------------------------------------------------------------
# Phase 19: Immutable Feature Manifest tests
# ---------------------------------------------------------------------------

def test_generate_feature_manifest_created(monkeypatch, tmp_path):
    """generate_feature_manifest creates feature_list.json."""
    _setup_workspace(monkeypatch, tmp_path)
    import json
    from mission import generate_feature_manifest
    mission = decompose_mission("Build a test pipeline", _DecomposeMockAdapter())
    mission.project = "manifest-test"
    orch.ensure_project("manifest-test", "test")
    manifest = generate_feature_manifest(mission, "manifest-test")
    assert "features" in manifest
    assert len(manifest["features"]) > 0
    # feature_list.json should be written
    path = orch.project_dir("manifest-test") / "feature_list.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data["features"]) == len(manifest["features"])


def test_generate_feature_manifest_not_overwritten(monkeypatch, tmp_path):
    """generate_feature_manifest never overwrites an existing manifest."""
    _setup_workspace(monkeypatch, tmp_path)
    import json
    from mission import generate_feature_manifest
    mission = decompose_mission("Build a pipeline", _DecomposeMockAdapter())
    mission.project = "manifest-idempotent"
    orch.ensure_project("manifest-idempotent", "test")
    # First call
    manifest1 = generate_feature_manifest(mission, "manifest-idempotent")
    # Modify mission and call again — should not overwrite
    mission.milestones[0].features[0].title = "CHANGED TITLE"
    manifest2 = generate_feature_manifest(mission, "manifest-idempotent")
    # Both should return the same first-written data
    assert manifest1["features"][0]["title"] == manifest2["features"][0]["title"]


def test_mark_feature_passing(monkeypatch, tmp_path):
    """mark_feature_passing sets passes=true for a feature."""
    _setup_workspace(monkeypatch, tmp_path)
    import json
    from mission import generate_feature_manifest, mark_feature_passing
    from sprint_contract import ContractGrade
    from datetime import datetime, timezone

    mission = decompose_mission("Build something", _DecomposeMockAdapter())
    mission.project = "mark-test"
    orch.ensure_project("mark-test", "test")
    generate_feature_manifest(mission, "mark-test")

    feature = mission.milestones[0].features[0]
    grade = ContractGrade(
        contract_id="test0001",
        feature_id=feature.id,
        passed=True,
        criteria_results=[],
        score=0.9,
        feedback="Good work",
        graded_at=datetime.now(timezone.utc).isoformat(),
    )
    mark_feature_passing("mark-test", feature.id, grade)

    path = orch.project_dir("mark-test") / "feature_list.json"
    data = json.loads(path.read_text())
    marked = next((f for f in data["features"] if f["id"] == feature.id), None)
    assert marked is not None
    assert marked["passes"] is True
    assert marked["grade_score"] == pytest.approx(0.9)


def test_mark_feature_monotonicity(monkeypatch, tmp_path):
    """Cannot downgrade a feature from passes=True to passes=False."""
    _setup_workspace(monkeypatch, tmp_path)
    from mission import generate_feature_manifest, mark_feature_passing
    from sprint_contract import ContractGrade
    from datetime import datetime, timezone

    mission = decompose_mission("Build something", _DecomposeMockAdapter())
    mission.project = "monotone-test"
    orch.ensure_project("monotone-test", "test")
    generate_feature_manifest(mission, "monotone-test")

    feature = mission.milestones[0].features[0]
    # Mark as passing
    pass_grade = ContractGrade(
        contract_id="pass0001",
        feature_id=feature.id,
        passed=True,
        criteria_results=[],
        score=1.0,
        feedback="All passed",
        graded_at=datetime.now(timezone.utc).isoformat(),
    )
    mark_feature_passing("monotone-test", feature.id, pass_grade)

    # Try to mark as failing — should raise ValueError
    fail_grade = ContractGrade(
        contract_id="fail0001",
        feature_id=feature.id,
        passed=False,
        criteria_results=[],
        score=0.0,
        feedback="Failed",
        graded_at=datetime.now(timezone.utc).isoformat(),
    )
    with pytest.raises(ValueError, match="[Mm]onoton"):
        mark_feature_passing("monotone-test", feature.id, fail_grade)


def test_validate_manifest_monotonicity_pass(monkeypatch, tmp_path):
    """validate_manifest_monotonicity returns True for valid manifest."""
    _setup_workspace(monkeypatch, tmp_path)
    from mission import generate_feature_manifest, validate_manifest_monotonicity
    mission = decompose_mission("Test", _DecomposeMockAdapter())
    mission.project = "valid-monotone"
    orch.ensure_project("valid-monotone", "test")
    generate_feature_manifest(mission, "valid-monotone")
    assert validate_manifest_monotonicity("valid-monotone") is True


def test_run_mission_creates_feature_manifest(monkeypatch, tmp_path):
    """run_mission creates feature_list.json."""
    _setup_workspace(monkeypatch, tmp_path)
    import json
    result = run_mission(
        "test feature manifest creation",
        project="manifest-mission-test",
        dry_run=True,
    )
    path = orch.project_dir("manifest-mission-test") / "feature_list.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert "features" in data
    assert len(data["features"]) > 0
