from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import build_loop_runner as blr


def test_run_build_loop_sets_poe_yolo_for_autonomous_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("POE_YOLO", raising=False)

    repo = tmp_path / "prototypes" / "poe-orchestration"
    (repo / "output").mkdir(parents=True, exist_ok=True)

    next_item = SimpleNamespace(index=1, text="ambiguous task")
    observed = {}

    monkeypatch.setattr(blr, "select_next_item", lambda project: next_item)
    monkeypatch.setattr(blr, "worker_session_bridge", lambda worker_session, timeout_seconds=None: object())

    def _fake_run_loop(**kwargs):
        observed["poe_yolo"] = os.environ.get("POE_YOLO")
        return []

    monkeypatch.setattr(blr, "run_loop", _fake_run_loop)

    summary = blr.run_build_loop(project="demo", worker_session="handle", max_runs=1)

    assert summary["status"] == "idle"
    assert observed["poe_yolo"] == "true"
    assert os.environ.get("POE_YOLO") is None


def test_run_build_loop_passes_bounded_session_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("POE_BUILD_LOOP_SESSION_TIMEOUT_SECONDS", "12.5")

    repo = tmp_path / "prototypes" / "poe-orchestration"
    (repo / "output").mkdir(parents=True, exist_ok=True)

    next_item = SimpleNamespace(index=1, text="potentially hanging task")
    observed = {}

    monkeypatch.setattr(blr, "select_next_item", lambda project: next_item)

    def _fake_worker_session_bridge(worker_session, timeout_seconds=None):
        observed["worker_session"] = worker_session
        observed["timeout_seconds"] = timeout_seconds
        return object()

    monkeypatch.setattr(blr, "worker_session_bridge", _fake_worker_session_bridge)
    monkeypatch.setattr(blr, "run_loop", lambda **kwargs: [])

    summary = blr.run_build_loop(project="demo", worker_session="handle", max_runs=1)

    assert summary["status"] == "idle"
    assert observed["worker_session"] == "handle"
    assert observed["timeout_seconds"] == 12.5


def test_run_build_loop_writes_running_status_before_work(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    repo = tmp_path / "prototypes" / "poe-orchestration"
    (repo / "output").mkdir(parents=True, exist_ok=True)

    next_item = SimpleNamespace(index=1, text="active task")

    monkeypatch.setattr(blr, "select_next_item", lambda project: next_item)
    monkeypatch.setattr(blr, "worker_session_bridge", lambda worker_session, timeout_seconds=None: object())

    def _fake_run_loop(**kwargs):
        status = json.loads(blr.build_loop_status_path().read_text(encoding="utf-8"))
        assert status["status"] == "running"
        assert status["reason"] == "lock_acquired"
        assert status["selected_project"] == "demo"
        return []

    monkeypatch.setattr(blr, "run_loop", _fake_run_loop)

    summary = blr.run_build_loop(project="demo", worker_session="handle", max_runs=1)

    assert summary["status"] == "idle"


def test_run_build_loop_busy_preserves_existing_status(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    repo = tmp_path / "prototypes" / "poe-orchestration"
    output_dir = repo / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    next_item = SimpleNamespace(index=1, text="queued task")
    existing_status = {
        "status": "running",
        "reason": "lock_acquired",
        "started_at": "2026-06-21T23:45:47Z",
        "finished_at": None,
        "project": None,
        "selected_project": "demo",
        "worker": "handle",
        "worker_session": "handle",
        "runs": 0,
        "orch_root": str(repo),
    }
    blr.build_loop_status_path().write_text(json.dumps(existing_status, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(blr, "select_global_next", lambda: ("demo", next_item))

    class _BusyLock:
        def __enter__(self):
            return False

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(blr, "_try_lock", lambda path: _BusyLock())

    summary = blr.run_build_loop(worker_session="handle", max_runs=1)

    assert summary["status"] == "busy"
    preserved = json.loads(blr.build_loop_status_path().read_text(encoding="utf-8"))
    assert preserved == existing_status


def test_run_build_loop_busy_when_worker_session_already_active(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    repo = tmp_path / "prototypes" / "poe-orchestration"
    (repo / "output").mkdir(parents=True, exist_ok=True)

    next_item = SimpleNamespace(index=1, text="queued task")

    monkeypatch.setattr(blr, "select_global_next", lambda: ("demo", next_item))
    monkeypatch.setattr(blr, "_worker_session_already_active", lambda worker_session: True)

    summary = blr.run_build_loop(worker_session="handle", max_runs=1)

    assert summary["status"] == "busy"
    assert summary["reason"] == "worker_session_active"
    status = json.loads(blr.build_loop_status_path().read_text(encoding="utf-8"))
    assert status["status"] == "busy"
    assert status["reason"] == "worker_session_active"


def test_run_build_loop_interrupt_cleans_up_running_items(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    repo = tmp_path / "prototypes" / "poe-orchestration"
    (repo / "output").mkdir(parents=True, exist_ok=True)

    next_item = SimpleNamespace(index=1, text="interruptible task")
    cleaned = []

    monkeypatch.setattr(blr, "select_next_item", lambda project: next_item)
    monkeypatch.setattr(blr, "worker_session_bridge", lambda worker_session, timeout_seconds=None: object())
    monkeypatch.setattr(
        blr,
        "_load_run_records",
        lambda: [SimpleNamespace(run_id="run-123", status="running", source="build-loop")],
    )
    monkeypatch.setattr(
        blr,
        "finalize_run",
        lambda run_id, status, note=None: cleaned.append((run_id, status, note)),
    )

    def _boom(**kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(blr, "run_loop", _boom)

    summary = blr.run_build_loop(project="demo", worker_session="handle", max_runs=1)

    assert summary["status"] == "interrupted"
    assert summary["reason"] == "keyboard_interrupt"
    assert cleaned == [("run-123", "blocked", "build loop interrupted: keyboard_interrupt")]
    status = json.loads(blr.build_loop_status_path().read_text(encoding="utf-8"))
    assert status["status"] == "interrupted"
    assert status["reason"] == "keyboard_interrupt"


def test_main_returns_130_for_interrupted_summary(monkeypatch):
    monkeypatch.setattr(
        blr,
        "run_build_loop",
        lambda **kwargs: {"status": "interrupted", "reason": "sigterm"},
    )
    assert blr.main(["--format", "json"]) == 130
