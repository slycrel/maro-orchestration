import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import cli as _cli_module


class _RunResult:
    """Mimics subprocess.CompletedProcess enough for existing test assertions."""
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _run(tmp_path, *args):
    """Run cli.main() in-process with OPENCLAW_WORKSPACE pointed at tmp_path."""
    prev = os.environ.get("OPENCLAW_WORKSPACE")
    os.environ["OPENCLAW_WORKSPACE"] = str(tmp_path)
    out = io.StringIO()
    err = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = _cli_module.main(list(args))
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    finally:
        if prev is None:
            os.environ.pop("OPENCLAW_WORKSPACE", None)
        else:
            os.environ["OPENCLAW_WORKSPACE"] = prev
    return _RunResult(rc or 0, out.getvalue(), err.getvalue())


def test_cli_init_next_done_report(tmp_path):
    r = _run(tmp_path, "init", "demo", "Ship", "it", "--priority", "2")
    assert r.returncode == 0
    r = _run(tmp_path, "next", "--project", "demo")
    assert "Define success criteria" in r.stdout
    r = _run(tmp_path, "done", "demo")
    assert r.returncode == 0
    out = tmp_path / "report.md"
    r = _run(tmp_path, "report", "--project", "demo", "--out", str(out))
    assert r.returncode == 0
    assert out.exists()


def test_cli_salvage_empty(tmp_path):
    r = _run(tmp_path, "salvage")
    assert r.returncode == 0
    assert "active_count=0" in r.stdout
    assert "pending_count=0" in r.stdout
    assert "salvage=(none)" in r.stdout



def test_cli_enqueue_project_task(tmp_path):
    r = _run(tmp_path, "init", "demo", "Queue", "adapter", "--priority", "1")
    assert r.returncode == 0

    queued = _run(
        tmp_path,
        "enqueue",
        "demo",
        "Draft",
        "queue",
        "adapter",
        "integration",
        "--lane",
        "manual",
        "--source",
        "orch-test",
        "--reason",
        "queue adapter smoke",
    )
    assert queued.returncode == 0
    assert "type=project_task" in queued.stdout
    assert "job_id=" in queued.stdout

    # FileTaskStore: one JSON file per task in output/queues/tasks/
    tasks_dir = tmp_path / "output" / "queues" / "tasks"
    assert tasks_dir.exists(), f"tasks dir missing; stdout={queued.stdout}"
    task_files = list(tasks_dir.glob("*.json"))
    assert len(task_files) == 1
    import json as _json
    task = _json.loads(task_files[0].read_text(encoding="utf-8"))
    assert task["lane"] == "manual"
    assert task["reason"] == "queue adapter smoke"


def test_cli_enqueue_default_reason_uses_payload(tmp_path):
    """When --reason is not provided, reason should be the constructed payload."""
    _run(tmp_path, "init", "demo", "Queue", "test", "--priority", "1")

    queued = _run(
        tmp_path, "enqueue", "demo", "Do", "the", "thing",
        "--lane", "manual", "--source", "orch-test",
    )
    assert queued.returncode == 0

    tasks_dir = tmp_path / "output" / "queues" / "tasks"
    task_files = list(tasks_dir.glob("*.json"))
    assert len(task_files) == 1
    import json as _json
    task = _json.loads(task_files[0].read_text(encoding="utf-8"))
    assert task["reason"] == "project=demo :: Do the thing"


def test_cli_run_start_finish_status(tmp_path):
    r = _run(tmp_path, "init", "demo", "Build", "loop", "--priority", "5")
    assert r.returncode == 0

    r = _run(tmp_path, "run", "--project", "demo", "--worker", "director", "--source", "test-run")
    assert r.returncode == 0
    assert "started run_id=" in r.stdout
    run_id = next(part.split("=", 1)[1] for part in r.stdout.split() if part.startswith("run_id="))

    status_path = tmp_path / "prototypes" / "poe-orchestration" / "output" / "operator-status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["queue"]["doing"] == 1
    assert status["active_projects"] == ["demo"]

    r = _run(tmp_path, "finish", run_id, "--status", "done", "--note", "verified")
    assert r.returncode == 0
    assert "finished run_id=" in r.stdout

    run_artifact = tmp_path / "prototypes" / "poe-orchestration" / "output" / "runs" / f"{run_id}.json"
    payload = json.loads(run_artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "done"
    assert payload["note"] == "verified"

    r = _run(tmp_path, "status")
    assert r.returncode == 0
    status = json.loads(r.stdout)
    assert status["queue"]["doing"] == 0
    assert status["queue"]["done"] >= 1


def test_cli_plan_and_loop(tmp_path):
    r = _run(tmp_path, "init", "demo", "Autonomy", "lane", "--priority", "1")
    assert r.returncode == 0
    r = _run(tmp_path, "plan", "demo", "Draft a plan. Execute the first patch. Verify it.", "--max-steps", "3")
    assert r.returncode == 0
    assert "steps=" in r.stdout

    r = _run(tmp_path, "loop", "--project", "demo", "--max-runs", "3", "--source", "cli-loop", "--worker", "director")
    assert r.returncode == 0
    assert "runs=" in r.stdout



def test_cli_loop_continue_on_blocked_option(tmp_path):
    r = _run(tmp_path, "init", "demo", "Block", "continue", "--priority", "1")
    assert r.returncode == 0
    r = _run(tmp_path, "plan", "demo", "First, then second", "--max-steps", "2")
    assert r.returncode == 0

    default = _run(
        tmp_path,
        "loop",
        "--project",
        "demo",
        "--max-runs",
        "3",
        "--exec-cmd",
        "true",
        "--require-artifact",
        "result.txt",
        "--require-nonempty",
    )
    assert default.returncode == 0
    assert "runs=1" in default.stdout

    continued = _run(
        tmp_path,
        "loop",
        "--project",
        "demo",
        "--max-runs",
        "2",
        "--exec-cmd",
        "true",
        "--require-artifact",
        "result.txt",
        "--require-nonempty",
        "--continue-on-blocked",
    )
    assert continued.returncode == 0
    assert "runs=2" in continued.stdout


def test_cli_tick_exec_cmd(tmp_path):
    r = _run(tmp_path, "init", "demo", "Exec", "bridge", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf "%s" "$ORCH_PROJECT" > "$ORCH_RUN_ARTIFACT_DIR/project.txt"',
    )
    assert r.returncode == 0
    assert "execution=done validation=done" in r.stdout

    runs_dir = tmp_path / "prototypes" / "poe-orchestration" / "output" / "runs"
    run_dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "project.txt").read_text(encoding="utf-8") == "demo"


def test_cli_tick_exec_cmd_x_capture(tmp_path):
    r = _run(tmp_path, "init", "demo", "Exec", "capture", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf "%s" "this page isn\'t working" >&2',
    )
    assert r.returncode == 0
    assert "execution=done validation=retry" in r.stdout


def test_cli_tick_max_retry_streak_blocks_repeated_retries(tmp_path):
    r = _run(tmp_path, "init", "demo", "Retry", "guard", "--priority", "1")
    assert r.returncode == 0
    next_path = tmp_path / "prototypes" / "poe-orchestration" / "projects" / "demo" / "NEXT.md"
    next_path.write_text("- [ ] first\n", encoding="utf-8")

    first = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        "true",
        "--review-cmd",
        'printf \'{"status":"retry","note":"manual check"}\'',
        "--disable-x-capture",
        "--disable-artifact-progress",
        "--max-retry-streak",
        "2",
    )
    assert first.returncode == 0
    assert "execution=done validation=retry" in first.stdout

    second = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        "true",
        "--review-cmd",
        'printf \'{"status":"retry","note":"manual check"}\'',
        "--disable-x-capture",
        "--disable-artifact-progress",
        "--max-retry-streak",
        "2",
    )
    assert second.returncode == 0
    assert "execution=done validation=blocked" in second.stdout
    assert "retry streak reached 2 attempts" in second.stdout



def test_cli_salvage_lists_active_runs(tmp_path):
    r = _run(tmp_path, "init", "demo", "Exec", "capture", "--priority", "1")
    assert r.returncode == 0
    tick = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf "%s" "this page isn\'t working" >&2',
    )
    assert tick.returncode == 0

    text_view = _run(tmp_path, "salvage")
    assert text_view.returncode == 0
    assert "active_count=1" in text_view.stdout
    assert "pending_count=1" in text_view.stdout
    assert "kind=auth" in text_view.stdout
    assert "project=demo" in text_view.stdout

    json_view = _run(tmp_path, "salvage", "--format", "json")
    assert json_view.returncode == 0
    payload = json.loads(json_view.stdout)
    assert payload["active_count"] == 1
    assert payload["pending_count"] == 1
    assert payload["active_runs"][0]["first_kind"] == "auth"



def test_cli_tick_session_cmd(tmp_path):
    r = _run(tmp_path, "init", "demo", "Session", "bridge", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--session-cmd",
        'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
        '{"status":"done","note":"session complete","artifact_path":"output/runs/$ORCH_RUN_ID"}\n'
        "EOF",
    )
    assert r.returncode == 0
    assert "execution=done validation=done" in r.stdout


def test_cli_tick_worker_session(tmp_path):
    r = _run(tmp_path, "init", "demo", "Session", "worker", "--priority", "1")
    assert r.returncode == 0

    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True)
    script = workers / "handle"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
        '{"status":"done","note":"cli worker","artifact_path":"$ORCH_RUN_ARTIFACT_PATH"}\n'
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    r = _run(tmp_path, "tick", "--project", "demo", "--worker-session", "handle")
    assert r.returncode == 0
    assert "execution=done validation=done" in r.stdout


def test_cli_tick_session_cmd_markers_trigger_retries(tmp_path):
    r = _run(tmp_path, "init", "demo", "Session", "salvage", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--session-cmd",
        'echo "This page isn’t working for now"',
    )
    assert r.returncode == 0
    assert "execution=done validation=retry" in r.stdout


def test_cli_tick_require_artifact(tmp_path):
    r = _run(tmp_path, "init", "demo", "Validator", "bridge", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf payload > "$ORCH_RUN_ARTIFACT_DIR/result.txt"',
        "--require-artifact",
        "result.txt",
        "--require-nonempty",
    )
    assert r.returncode == 0
    assert "execution=done validation=done" in r.stdout



def test_cli_tick_require_artifact_blocks_missing(tmp_path):
    r = _run(tmp_path, "init", "demo", "Validator", "block", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        "true",
        "--require-artifact",
        "result.txt",
        "--require-nonempty",
    )
    assert r.returncode == 0
    assert "execution=done validation=blocked" in r.stdout



def test_cli_loop_accepts_artifact_progress_options(tmp_path):
    r = _run(tmp_path, "init", "demo", "Stale", "progress", "--priority", "1")
    assert r.returncode == 0

    loop = _run(
        tmp_path,
        "loop",
        "--project",
        "demo",
        "--max-runs",
        "1",
        "--exec-cmd",
        'printf same > "$ORCH_RUN_ARTIFACT_DIR/result.txt"',
        "--artifact-progress-window",
        "3",
        "--artifact-progress-max-attempts",
        "4",
    )
    assert loop.returncode == 0
    assert "runs=1" in loop.stdout



def test_cli_loop_can_disable_stale_artifact_progress_detection(tmp_path):
    r = _run(tmp_path, "init", "demo", "Disable", "stale", "--priority", "1")
    assert r.returncode == 0

    loop = _run(
        tmp_path,
        "loop",
        "--project",
        "demo",
        "--max-runs",
        "1",
        "--exec-cmd",
        'printf same > "$ORCH_RUN_ARTIFACT_DIR/result.txt"',
        "--disable-artifact-progress",
    )
    assert loop.returncode == 0
    assert "runs=1" in loop.stdout



def test_cli_tick_review_cmd(tmp_path):
    r = _run(tmp_path, "init", "demo", "Reviewer", "bridge", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"',
        "--review-cmd",
        'test -s "$ORCH_RUN_ARTIFACT_DIR/result.txt" && printf pass > "$ORCH_REVIEW_ARTIFACT_DIR/verdict.txt"',
    )
    assert r.returncode == 0
    assert "execution=done validation=done" in r.stdout

    runs_dir = tmp_path / "prototypes" / "poe-orchestration" / "output" / "runs"
    run_dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "review" / "verdict.txt").read_text(encoding="utf-8") == "pass"
    assert (run_dirs[0] / "validation-summary.json").exists()



def test_cli_tick_review_timeout_blocks_and_records_trace(tmp_path):
    r = _run(tmp_path, "init", "demo", "Reviewer", "timeout", "--priority", "1")
    assert r.returncode == 0
    tick = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"',
        "--review-cmd",
        "sleep 1",
        "--review-timeout",
        "0.01",
    )
    assert tick.returncode == 0
    assert "execution=done validation=blocked" in tick.stdout
    run_id = next(part.split("=", 1)[1] for part in tick.stdout.split() if part.startswith("run_id="))

    inspect_json = _run(tmp_path, "inspect-run", run_id, "--format", "json")
    assert inspect_json.returncode == 0
    payload = json.loads(inspect_json.stdout)
    assert payload["validation_summary"]["validation"]["status"] == "blocked"
    trace = payload["validation_summary"].get("validation_trace")
    assert isinstance(trace, list)
    assert any(event.get("bridge") == "review-command" for event in trace)


import pytest
@pytest.mark.slow
def test_cli_smoke_script(tmp_path):
    env = os.environ.copy()
    env["TMPDIR"] = str(tmp_path)
    r = subprocess.run(["bash", "scripts/smoke.sh"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0
    assert "smoke=ok" in r.stdout
    assert "tick_run_id=" in r.stdout



def test_cli_inspect_run(tmp_path):
    r = _run(tmp_path, "init", "demo", "Inspect", "run", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"',
        "--require-artifact",
        "result.txt",
        "--require-nonempty",
    )
    assert r.returncode == 0
    run_id = next(part.split("=", 1)[1] for part in r.stdout.split() if part.startswith("run_id="))

    text_view = _run(tmp_path, "inspect-run", run_id)
    assert text_view.returncode == 0
    assert "validation_status=done" in text_view.stdout

    json_view = _run(tmp_path, "inspect-run", run_id, "--format", "json")
    assert json_view.returncode == 0
    payload = json.loads(json_view.stdout)
    assert payload["run"]["run_id"] == run_id
    assert payload["validation_summary"]["validation"]["status"] == "done"
    assert payload["salvage_summary"] is None



def test_cli_inspect_run_includes_salvage_summary(tmp_path):
    r = _run(tmp_path, "init", "demo", "Inspect", "salvage", "--priority", "1")
    assert r.returncode == 0
    r = _run(
        tmp_path,
        "tick",
        "--project",
        "demo",
        "--exec-cmd",
        'printf "%s" "this page isn\'t working" >&2',
    )
    assert r.returncode == 0
    run_id = next(part.split("=", 1)[1] for part in r.stdout.split() if part.startswith("run_id="))

    text_view = _run(tmp_path, "inspect-run", run_id)
    assert text_view.returncode == 0
    assert "salvage_path=" in text_view.stdout
    assert "salvage_kind=auth" in text_view.stdout

    json_view = _run(tmp_path, "inspect-run", run_id, "--format", "json")
    assert json_view.returncode == 0
    payload = json.loads(json_view.stdout)
    assert payload["run"]["run_id"] == run_id
    assert payload["salvage_summary"]["path"].endswith("x-capture-salvage.json")
    assert payload["salvage_summary"]["matches"][0]["kind"] == "auth"



def test_cli_empty_paths(tmp_path):
    r = _run(tmp_path, "init", "demo", "Empty", "paths", "--priority", "1")
    assert r.returncode == 0

    # Drain the default checklist.
    for _ in range(3):
        done = _run(tmp_path, "done", "demo")
        assert done.returncode == 0

    next_r = _run(tmp_path, "next", "--project", "demo")
    assert next_r.returncode == 1
    assert "next=(none)" in next_r.stdout

    run_r = _run(tmp_path, "run", "--project", "demo")
    assert run_r.returncode == 1
    assert "run=(none)" in run_r.stdout

    tick_r = _run(tmp_path, "tick", "--project", "demo")
    assert tick_r.returncode == 1
    assert "tick=(none)" in tick_r.stdout

    loop_r = _run(tmp_path, "loop", "--project", "demo", "--max-runs", "2")
    assert loop_r.returncode == 1
    assert "loop=(none)" in loop_r.stdout
