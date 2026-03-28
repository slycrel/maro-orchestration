"""Tests for Phase 44: introspect.py — self-reflection / failure classifier."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from introspect import (
    LoopDiagnosis,
    diagnose_loop,
    diagnose_latest,
    save_diagnosis,
    load_diagnoses,
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
