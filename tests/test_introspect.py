"""Tests for Phase 44: introspect.py — self-reflection / failure classifier."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from introspect import (
    LoopDiagnosis,
    LensResult,
    StepProfile,
    AggregatedDiagnosis,
    RecoveryPlan,
    diagnose_loop,
    diagnose_latest,
    save_diagnosis,
    load_diagnoses,
    run_lenses,
    aggregate_lenses,
    plan_recovery,
    plan_recovery_all,
    get_lens_registry,
    _build_step_profiles,
    FAILURE_CLASSES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_events(tmp_path, events):
    """Write events to a fake events.jsonl."""
    path = tmp_path / "memory" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _make_step_event(loop_id, step_idx, status, tokens_in=100, tokens_out=50, elapsed_ms=5000, step="do something"):
    return {
        "event_type": "step_done" if status == "done" else "step_stuck",
        "loop_id": loop_id,
        "step_idx": step_idx,
        "step": step,
        "status": status,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "elapsed_ms": elapsed_ms,
    }


def _make_loop_done(loop_id, status="done", detail=""):
    return {
        "event_type": "loop_done",
        "loop_id": loop_id,
        "status": status,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Healthy loop
# ---------------------------------------------------------------------------

def test_healthy_loop(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop01", 1, "done"),
        _make_step_event("loop01", 2, "done"),
        _make_step_event("loop01", 3, "done"),
        _make_loop_done("loop01", "done"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop01")
    assert diag.failure_class == "healthy"
    assert diag.severity == "info"
    assert diag.steps_done == 3
    assert diag.steps_blocked == 0


# ---------------------------------------------------------------------------
# Setup failure
# ---------------------------------------------------------------------------

def test_setup_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop02", 1, "blocked", tokens_in=0, tokens_out=0, elapsed_ms=200),
        _make_loop_done("loop02", "stuck", "adapter error"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop02")
    assert diag.failure_class == "setup_failure"
    assert diag.severity == "critical"


# ---------------------------------------------------------------------------
# Adapter timeout
# ---------------------------------------------------------------------------

def test_adapter_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop03", 1, "done"),
        _make_step_event("loop03", 2, "blocked", tokens_in=0, tokens_out=0, elapsed_ms=305000),
        _make_loop_done("loop03", "stuck"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop03")
    assert diag.failure_class == "adapter_timeout"
    assert diag.severity == "critical"


# ---------------------------------------------------------------------------
# Decomposition too broad
# ---------------------------------------------------------------------------

def test_decomposition_too_broad(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop04", 1, "done", tokens_in=10000, tokens_out=2000),
        _make_step_event("loop04", 2, "done", tokens_in=250000, tokens_out=5000, elapsed_ms=130000),
        _make_step_event("loop04", 3, "done"),
        _make_loop_done("loop04", "done"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop04")
    assert diag.failure_class == "decomposition_too_broad"
    assert "250" in str(diag.evidence) or "255000" in str(diag.evidence)


# ---------------------------------------------------------------------------
# Token explosion
# ---------------------------------------------------------------------------

def test_token_explosion(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop05", 1, "done", tokens_in=5000, tokens_out=1000),
        _make_step_event("loop05", 2, "done", tokens_in=5000, tokens_out=1000),
        _make_step_event("loop05", 3, "done", tokens_in=50000, tokens_out=5000),  # 9x growth
        _make_loop_done("loop05", "done"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop05")
    assert diag.failure_class == "token_explosion"


# ---------------------------------------------------------------------------
# Budget exhaustion
# ---------------------------------------------------------------------------

def test_budget_exhaustion(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop06", i, "done") for i in range(1, 6)
    ] + [
        _make_loop_done("loop06", "stuck", "hit max_iterations=20 before completing all steps"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop06")
    assert diag.failure_class == "budget_exhaustion"
    assert "max_iterations" in str(diag.evidence)


# ---------------------------------------------------------------------------
# Constraint false positive
# ---------------------------------------------------------------------------

def test_constraint_false_positive(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop07", 1, "done"),  # step 1 succeeds
        _make_step_event("loop07", 2, "blocked", tokens_in=0, tokens_out=0, elapsed_ms=50),
        _make_step_event("loop07", 3, "blocked", tokens_in=0, tokens_out=0, elapsed_ms=30),
        _make_loop_done("loop07", "stuck"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop07")
    assert diag.failure_class == "constraint_false_positive"


# ---------------------------------------------------------------------------
# Empty model output
# ---------------------------------------------------------------------------

def test_empty_model_output(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("loop08", 1, "done"),
        _make_step_event("loop08", 2, "blocked", tokens_in=500, tokens_out=10, elapsed_ms=3000),
        _make_step_event("loop08", 3, "blocked", tokens_in=500, tokens_out=10, elapsed_ms=3000),
        _make_loop_done("loop08", "stuck"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_loop("loop08")
    assert diag.failure_class == "empty_model_output"


# ---------------------------------------------------------------------------
# No events
# ---------------------------------------------------------------------------

def test_no_events_returns_artifact_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    diag = diagnose_loop("nonexistent")
    assert diag.failure_class == "artifact_missing"


# ---------------------------------------------------------------------------
# Persistence roundtrip
# ---------------------------------------------------------------------------

def test_save_and_load_diagnoses(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._diagnoses_path", lambda: tmp_path / "memory" / "diagnoses.jsonl")
    diag = LoopDiagnosis(
        loop_id="test01",
        failure_class="healthy",
        severity="info",
        steps_done=3,
        steps_total=3,
    )
    save_diagnosis(diag)
    loaded = load_diagnoses()
    assert len(loaded) == 1
    assert loaded[0].loop_id == "test01"
    assert loaded[0].failure_class == "healthy"


# ---------------------------------------------------------------------------
# diagnose_latest
# ---------------------------------------------------------------------------

def test_diagnose_latest(tmp_path, monkeypatch):
    monkeypatch.setattr("introspect._events_path", lambda: tmp_path / "memory" / "events.jsonl")
    events = [
        _make_step_event("older", 1, "done"),
        _make_loop_done("older", "done"),
        _make_step_event("newer", 1, "done"),
        _make_step_event("newer", 2, "done"),
        _make_loop_done("newer", "done"),
    ]
    _write_events(tmp_path, events)
    diag = diagnose_latest()
    assert diag is not None
    assert diag.loop_id == "newer"


# ---------------------------------------------------------------------------
# LoopDiagnosis.summary
# ---------------------------------------------------------------------------

def test_diagnosis_summary():
    diag = LoopDiagnosis(
        loop_id="x",
        failure_class="budget_exhaustion",
        severity="warning",
        recommendation="increase max_iterations",
        total_tokens=500000,
    )
    s = diag.summary()
    assert "budget_exhaustion" in s
    assert "500000" in s


# ---------------------------------------------------------------------------
# Taxonomy completeness
# ---------------------------------------------------------------------------

def test_failure_classes_documented():
    """Every failure class in the taxonomy has a description."""
    assert len(FAILURE_CLASSES) >= 10
    assert "healthy" in FAILURE_CLASSES
    assert "setup_failure" in FAILURE_CLASSES
    assert "budget_exhaustion" in FAILURE_CLASSES


# ---------------------------------------------------------------------------
# Multi-Lens Introspection
# ---------------------------------------------------------------------------

def _make_profiles(specs):
    """Build StepProfile list from (idx, status, tokens, elapsed_ms) tuples."""
    return [
        StepProfile(step_idx=s[0], text=f"step {s[0]}", status=s[1], tokens=s[2], elapsed_ms=s[3])
        for s in specs
    ]


def test_lens_registry_has_four_builtin():
    registry = get_lens_registry()
    names = registry.list()
    assert "cost" in names
    assert "architecture" in names
    assert "operator" in names
    assert "forensics" in names


def test_cost_lens_flags_expensive_step():
    profiles = _make_profiles([
        (1, "done", 5000, 3000),
        (2, "done", 200000, 120000),  # 97% of tokens
        (3, "done", 3000, 2000),
    ])
    diag = LoopDiagnosis(loop_id="x", failure_class="healthy", severity="info")
    results = run_lenses(diag, profiles)
    cost_result = next((r for r in results if r.lens_name == "cost"), None)
    assert cost_result is not None
    assert cost_result.action is not None
    assert any("step 2" in f.lower() or "Step 2" in f for f in cost_result.findings)


def test_operator_lens_flags_blocked_time():
    profiles = _make_profiles([
        (1, "done", 5000, 5000),
        (2, "blocked", 0, 300000),  # 5 min blocked
        (3, "done", 5000, 5000),
    ])
    diag = LoopDiagnosis(loop_id="x", failure_class="healthy", severity="info")
    results = run_lenses(diag, profiles)
    op_result = next((r for r in results if r.lens_name == "operator"), None)
    assert op_result is not None
    assert any("blocked" in f.lower() for f in op_result.findings)


def test_forensics_lens_identifies_failure_transition():
    profiles = _make_profiles([
        (1, "done", 5000, 3000),
        (2, "done", 5000, 3000),
        (3, "blocked", 0, 100),
    ])
    diag = LoopDiagnosis(loop_id="x", failure_class="setup_failure", severity="critical")
    results = run_lenses(diag, profiles)
    forensics = next((r for r in results if r.lens_name == "forensics"), None)
    assert forensics is not None
    assert any("last successful" in f.lower() for f in forensics.findings)


def test_architecture_lens_flags_uneven_steps():
    # avg = (2K+2K+2K+500K)/4 = 126.5K, outlier > 126.5K*3 = 379.5K and > 50K
    profiles = _make_profiles([
        (1, "done", 2000, 1000),
        (2, "done", 2000, 1000),
        (3, "done", 2000, 1000),
        (4, "done", 500000, 100000),  # clearly an outlier
    ])
    diag = LoopDiagnosis(loop_id="x", failure_class="healthy", severity="info")
    results = run_lenses(diag, profiles)
    arch = next((r for r in results if r.lens_name == "architecture"), None)
    assert arch is not None
    assert any("uneven" in f.lower() or "3x" in f for f in arch.findings)


def test_run_lenses_returns_only_active():
    """Lenses with no findings are filtered out."""
    profiles = _make_profiles([
        (1, "done", 5000, 3000),
        (2, "done", 5000, 3000),
        (3, "done", 5000, 3000),
    ])
    diag = LoopDiagnosis(loop_id="x", failure_class="healthy", severity="info")
    results = run_lenses(diag, profiles)
    # All results should have at least one finding
    for r in results:
        assert len(r.findings) > 0


def test_custom_lens_registration():
    """Custom lenses can be registered and run."""
    registry = get_lens_registry()

    def _custom(diag, profiles):
        return LensResult(lens_name="custom", findings=["custom finding"], action="do custom thing")

    registry.register("custom_test", _custom, cost="free")
    assert "custom_test" in registry.list()

    profiles = _make_profiles([(1, "done", 5000, 3000)])
    diag = LoopDiagnosis(loop_id="x", failure_class="healthy", severity="info")
    result = registry.run("custom_test", diag, profiles)
    assert result.findings == ["custom finding"]

    # Clean up
    del registry._lenses["custom_test"]
    del registry._costs["custom_test"]


# ---------------------------------------------------------------------------
# Execution lens (wraps failure classifier)
# ---------------------------------------------------------------------------

def test_execution_lens_surfaces_failure_class():
    profiles = _make_profiles([
        (1, "done", 5000, 3000),
        (2, "blocked", 0, 100),
        (3, "blocked", 0, 100),
    ])
    diag = LoopDiagnosis(
        loop_id="x", failure_class="constraint_false_positive", severity="warning",
        evidence=["2 steps blocked with 0 tokens"],
        recommendation="Review constraint patterns",
        steps_done=1, steps_blocked=2, steps_total=3,
    )
    results = run_lenses(diag, profiles)
    exec_result = next((r for r in results if r.lens_name == "execution"), None)
    assert exec_result is not None
    assert exec_result.action is not None


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def test_aggregate_combines_actions():
    diag = LoopDiagnosis(
        loop_id="x", failure_class="decomposition_too_broad", severity="warning",
        evidence=["Step 4 consumed 332K tokens"],
        recommendation="Split step 4",
    )
    lens_results = [
        LensResult(lens_name="cost", findings=["step 4 expensive"], action="Split step 4 into smaller substeps", confidence=0.8),
        LensResult(lens_name="operator", findings=["step 4 took 127s"], action="Split slow steps into smaller units", confidence=0.8),
        LensResult(lens_name="forensics", findings=["last good: step 3"], confidence=0.5),
    ]
    agg = aggregate_lenses(diag, lens_results)
    assert agg.confidence > 0.7
    assert agg.lens_agreement >= 2
    assert "split" in agg.primary_action.lower()


def test_aggregate_no_actions_falls_back():
    diag = LoopDiagnosis(loop_id="x", failure_class="healthy", severity="info")
    lens_results = [
        LensResult(lens_name="cost", findings=["avg 5K tokens/step"], confidence=0.2),
    ]
    agg = aggregate_lenses(diag, lens_results)
    assert agg.lens_agreement == 0
    assert agg.confidence <= 0.5


def test_aggregate_summary():
    diag = LoopDiagnosis(loop_id="x", failure_class="budget_exhaustion", severity="warning")
    lens_results = [
        LensResult(lens_name="execution", findings=["budget hit"], action="increase max_iterations", confidence=0.9),
    ]
    agg = aggregate_lenses(diag, lens_results)
    assert "budget_exhaustion" in agg.summary()


# ---------------------------------------------------------------------------
# Registry includes execution lens
# ---------------------------------------------------------------------------

def test_registry_includes_execution_and_quality():
    registry = get_lens_registry()
    names = registry.list()
    assert "execution" in names
    assert "quality" in names


# ---------------------------------------------------------------------------
# Recovery Planner (Phase 45)
# ---------------------------------------------------------------------------

def test_recovery_plan_for_decomposition_too_broad():
    diag = LoopDiagnosis(loop_id="x", failure_class="decomposition_too_broad", severity="warning")
    plan = plan_recovery(diag)
    assert plan is not None
    assert plan.auto_apply is True
    assert plan.risk == "low"
    assert "max_steps" in plan.params


def test_recovery_plan_for_budget_exhaustion():
    diag = LoopDiagnosis(loop_id="x", failure_class="budget_exhaustion", severity="warning")
    plan = plan_recovery(diag)
    assert plan is not None
    assert plan.auto_apply is True
    assert plan.params.get("max_iterations", 0) > 40


def test_recovery_plan_for_adapter_timeout():
    diag = LoopDiagnosis(loop_id="x", failure_class="adapter_timeout", severity="critical")
    plan = plan_recovery(diag)
    assert plan is not None
    assert plan.auto_apply is False  # needs human review
    assert plan.risk == "medium"


def test_recovery_plan_for_healthy_returns_none():
    diag = LoopDiagnosis(loop_id="x", failure_class="healthy", severity="info")
    plan = plan_recovery(diag)
    assert plan is None


def test_recovery_plan_all_failure_classes_covered():
    """Every non-healthy failure class has at least one recovery plan."""
    for fc in FAILURE_CLASSES:
        if fc == "healthy" or fc == "artifact_missing":
            continue
        diag = LoopDiagnosis(loop_id="x", failure_class=fc, severity="warning")
        plans = plan_recovery_all(diag)
        assert len(plans) >= 1, f"No recovery plan for {fc}"


def test_recovery_plan_all_returns_list():
    diag = LoopDiagnosis(loop_id="x", failure_class="constraint_false_positive", severity="warning")
    plans = plan_recovery_all(diag)
    assert isinstance(plans, list)
    assert all(isinstance(p, RecoveryPlan) for p in plans)
