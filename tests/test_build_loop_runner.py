from __future__ import annotations

from pathlib import Path

import orch
from build_loop_runner import build_loop_lock_path, build_loop_status_path, run_build_loop, _try_lock


def _mkproj(root: Path, slug: str, content: str, priority: int = 0) -> None:
    project = root / "prototypes" / "poe-orchestration" / "projects" / slug
    project.mkdir(parents=True, exist_ok=True)
    (project / "NEXT.md").write_text(content, encoding="utf-8")
    (project / "PRIORITY").write_text(f"{priority}\n", encoding="utf-8")


def _mk_worker(root: Path, name: str = "done") -> Path:
    workers = root / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True, exist_ok=True)
    script = workers / f"{name}.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s' \"${ORCH_ITEM_TEXT}\" > \"${ORCH_RUN_ARTIFACT_DIR}/item.txt\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_run_build_loop_idle_when_no_work(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    summary = run_build_loop(worker_session="missing-ok-because-idle")

    assert summary["status"] == "idle"
    assert summary["reason"] == "no_work"
    assert summary["runs"] == 0
    assert build_loop_status_path().exists()


def test_run_build_loop_executes_claimed_work(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mk_worker(tmp_path)
    _mkproj(tmp_path, "demo", "- [ ] first task\n", priority=1)
    item_index = 0

    summary = run_build_loop(worker="fake", worker_session="done", max_runs=1)

    assert summary["status"] == "ok"
    assert summary["runs"] == 1
    item = orch.get_item("demo", item_index)
    assert item.state == orch.STATE_DONE

    artifact_rel = orch.load_run_record(summary["run_ids"][0]).artifact_path
    assert artifact_rel
    artifact_root = (tmp_path / "prototypes" / "poe-orchestration" / artifact_rel)
    assert (artifact_root / "item.txt").read_text(encoding="utf-8") == "first task"


def test_run_build_loop_reports_busy_when_lock_is_held(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mk_worker(tmp_path)
    _mkproj(tmp_path, "demo", "- [ ] first task\n", priority=1)
    item_index = 0

    with _try_lock(build_loop_lock_path()) as acquired:
        assert acquired is True
        summary = run_build_loop(worker="fake", worker_session="done", max_runs=1)

    assert summary["status"] == "busy"
    assert summary["reason"] == "lock_held"
    item = orch.get_item("demo", item_index)
    assert item.state == orch.STATE_TODO
