import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import cli as _cli_module


class _RunResult:
    """Mimics subprocess.CompletedProcess enough for existing test assertions."""
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _resolve_display(tmp_path, rel: str):
    """Resolve a relative_display_path() value against the test workspace."""
    if rel.startswith("~workspace/"):
        return tmp_path / rel[len("~workspace/"):]
    return tmp_path / "prototypes" / "maro-orchestration" / rel


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

    r = _run(tmp_path, "cycle", "--project", "demo", "--worker", "director", "--source", "test-run")
    assert r.returncode == 0
    assert "started run_id=" in r.stdout
    run_id = next(part.split("=", 1)[1] for part in r.stdout.split() if part.startswith("run_id="))

    status_path = tmp_path / "output" / "operator-status.json"
    assert status_path.exists()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["queue"]["doing"] == 1
    assert status["active_projects"] == ["demo"]

    r = _run(tmp_path, "finish", run_id, "--status", "done", "--note", "verified")
    assert r.returncode == 0
    assert "finished run_id=" in r.stdout

    run_artifact = tmp_path / "output" / "runs" / f"{run_id}.json"
    payload = json.loads(run_artifact.read_text(encoding="utf-8"))
    assert payload["status"] == "done"
    assert payload["note"] == "verified"

    r = _run(tmp_path, "opstatus")
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

    runs_dir = tmp_path / "output" / "runs"
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
    next_path = tmp_path / "projects" / "demo" / "NEXT.md"
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

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
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


def test_cli_tick_worker_session_manifest_aliases(tmp_path):
    r = _run(tmp_path, "init", "demo", "Session", "manifest", "--priority", "1")
    assert r.returncode == 0

    worker_dir = tmp_path / "prototypes" / "maro-orchestration" / "workers" / "demo"
    worker_dir.mkdir(parents=True)
    workdir = worker_dir / "nested"
    workdir.mkdir()
    script = workdir / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s" "$ORCH_WORKER_TOKEN" > "$ORCH_RUN_ARTIFACT_DIR/token.txt"\n'
        'printf "%s" "$ORCH_SESSION_WORKING_DIR" > "$ORCH_RUN_ARTIFACT_DIR/cwd.txt"\n'
        'printf "%s" "$ORCH_SESSION_PAYLOAD_PATH" > "$ORCH_RUN_ARTIFACT_DIR/payload-path.txt"\n'
        'printf "%s" "$ORCH_SESSION_RESULT" > "$ORCH_RUN_ARTIFACT_DIR/result-path.txt"\n'
        'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
        '{"status":"done","note":"alias worker","artifact_path":"$ORCH_RUN_ARTIFACT_PATH"}\n'
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    manifest = worker_dir / "alias.json"
    manifest.write_text(
        json.dumps(
            {
                "command": "run.sh",
                "cwd": "nested",
                "env": {"ORCH_WORKER_TOKEN": "alias-ok"},
                "timeout": 15,
                "payload": "req/payload.json",
                "result": "resp/result.json",
            }
        ),
        encoding="utf-8",
    )

    r = _run(tmp_path, "tick", "--project", "demo", "--worker-session", str(manifest))
    assert r.returncode == 0
    assert "execution=done validation=done" in r.stdout

    run_line = next(line for line in r.stdout.splitlines() if "run_id=" in line)
    run_id = run_line.split("run_id=", 1)[1].split()[0]
    artifact_dir = tmp_path / "output" / "runs" / run_id
    assert (artifact_dir / "token.txt").read_text(encoding="utf-8") == "alias-ok"
    assert (artifact_dir / "cwd.txt").read_text(encoding="utf-8") == str(workdir)
    assert (artifact_dir / "req" / "payload.json").exists()
    assert (artifact_dir / "resp" / "result.json").exists()
    assert (artifact_dir / "payload-path.txt").read_text(encoding="utf-8") == str(artifact_dir / "req" / "payload.json")
    assert (artifact_dir / "result-path.txt").read_text(encoding="utf-8") == str(artifact_dir / "resp" / "result.json")


def test_cli_tick_worker_session_manifest_args_arrays(tmp_path):
    r = _run(tmp_path, "init", "demo", "Session", "argv", "--priority", "1")
    assert r.returncode == 0

    worker_dir = tmp_path / "prototypes" / "maro-orchestration" / "workers" / "argv"
    worker_dir.mkdir(parents=True)
    script = worker_dir / "worker.py"
    script.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['ORCH_RUN_ARTIFACT_DIR']).joinpath('argv.txt').write_text(' '.join(sys.argv), encoding='utf-8')\n"
        "Path(os.environ['ORCH_SESSION_RESULT_PATH']).write_text(json.dumps({'status':'done','note':'argv worker','artifact_path':os.environ['ORCH_RUN_ARTIFACT_PATH']}), encoding='utf-8')\n",
        encoding="utf-8",
    )

    manifest = worker_dir / "argv.json"
    manifest.write_text(
        json.dumps(
            {
                "cmd": "python3",
                "arguments": ["worker.py", "--mode", "cli"],
            }
        ),
        encoding="utf-8",
    )

    r = _run(tmp_path, "tick", "--project", "demo", "--worker-session", str(manifest))
    assert r.returncode == 0
    assert "execution=done validation=done" in r.stdout

    run_line = next(line for line in r.stdout.splitlines() if "run_id=" in line)
    run_id = run_line.split("run_id=", 1)[1].split()[0]
    artifact_dir = tmp_path / "output" / "runs" / run_id
    assert (artifact_dir / "argv.txt").read_text(encoding="utf-8") == "worker.py --mode cli"


def test_cli_loop_worker_session_manifest_args_arrays(tmp_path):
    r = _run(tmp_path, "init", "demo", "Session", "loop", "--priority", "1")
    assert r.returncode == 0

    worker_dir = tmp_path / "prototypes" / "maro-orchestration" / "workers" / "loop-argv"
    worker_dir.mkdir(parents=True)
    script = worker_dir / "worker.py"
    script.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['ORCH_RUN_ARTIFACT_DIR']).joinpath('argv-loop.txt').write_text(' '.join(sys.argv), encoding='utf-8')\n"
        "Path(os.environ['ORCH_SESSION_RESULT_PATH']).write_text(json.dumps({'status':'done','note':'loop argv worker','artifact_path':os.environ['ORCH_RUN_ARTIFACT_PATH']}), encoding='utf-8')\n",
        encoding="utf-8",
    )

    manifest = worker_dir / "argv-loop.json"
    manifest.write_text(
        json.dumps(
            {
                "cmd": "python3",
                "args": ["worker.py", "--mode", "loop"],
            }
        ),
        encoding="utf-8",
    )

    r = _run(tmp_path, "loop", "--project", "demo", "--max-runs", "1", "--worker-session", str(manifest))
    assert r.returncode == 0
    assert "runs=1" in r.stdout

    runs_dir = tmp_path / "output" / "runs"
    run_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "argv-loop.txt").read_text(encoding="utf-8") == "worker.py --mode loop"


def test_cli_build_loop_runs_worker_session(tmp_path):
    r = _run(tmp_path, "init", "demo", "Build", "loop", "--priority", "1")
    assert r.returncode == 0

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
    workers.mkdir(parents=True, exist_ok=True)
    script = workers / "done.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s' \"${ORCH_ITEM_TEXT}\" > \"${ORCH_RUN_ARTIFACT_DIR}/item.txt\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    r = _run(tmp_path, "build-loop", "--project", "demo", "--max-runs", "1", "--worker-session", "done")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["status"] == "ok"
    assert payload["runs"] == 1
    assert payload["items"][0]["project"] == "demo"
    heartbeat_run = _resolve_display(tmp_path, payload["heartbeat_run_path"])
    assert heartbeat_run.exists()
    record = json.loads(heartbeat_run.read_text(encoding="utf-8"))
    assert record["status"] == "ok"
    assert record["runs"] == 1
    assert "stdout_excerpt" in record
    assert record["stderr_excerpt"] == ""


def test_cli_build_loop_path_format(tmp_path):
    r = _run(tmp_path, "build-loop", "--format", "path")
    assert r.returncode == 0
    assert r.stdout.strip().endswith("build-loop-status.json")


def test_cli_build_loop_idle_writes_heartbeat_run(tmp_path):
    r = _run(tmp_path, "build-loop")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["status"] == "idle"
    assert payload["reason"] == "no_work"
    heartbeat_run = _resolve_display(tmp_path, payload["heartbeat_run_path"])
    assert heartbeat_run.exists()
    record = json.loads(heartbeat_run.read_text(encoding="utf-8"))
    assert record["status"] == "idle"
    assert record["reason"] == "no_work"
    assert record["exit_code"] == 0


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

    runs_dir = tmp_path / "output" / "runs"
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

    run_r = _run(tmp_path, "cycle", "--project", "demo")
    assert run_r.returncode == 1
    assert "run=(none)" in run_r.stdout

    tick_r = _run(tmp_path, "tick", "--project", "demo")
    assert tick_r.returncode == 1
    assert "tick=(none)" in tick_r.stdout

    loop_r = _run(tmp_path, "loop", "--project", "demo", "--max-runs", "2")
    assert loop_r.returncode == 1
    assert "loop=(none)" in loop_r.stdout


def test_legacy_loop_commands_warn_deprecated(tmp_path):
    """plan/tick/loop are the superseded pre-agent_loop executor (BACKLOG
    orch.py-split item, Jeremy-confirmed unused 2026-07-09) — each must warn
    on stderr while still functioning during the deprecation window."""
    _run(tmp_path, "init", "demo")
    for cmd, args in (
        ("plan", ("plan", "no-such-project", "some", "goal")),
        ("tick", ("tick", "--project", "demo")),
        ("loop", ("loop", "--project", "demo", "--max-runs", "1")),
    ):
        r = _run(tmp_path, *args)
        assert f"`maro {cmd}` is deprecated" in r.stderr, cmd


class TestClosureVerdictPass:
    """BACKLOG #18: `maro run`/`maro resume` run the same closure/verdict
    path as maro-handle (honesty-only — verdict stamped on the outcomes row,
    done demoted on judged contradiction, absent verdict = done-unverified)."""

    class _Step:
        def __init__(self, status="done", result="did the thing"):
            self.status = status
            self.result = result

    class _Verdict:
        def __init__(self, complete, confidence=0.9, judged=True,
                     checks_run=3, checks_passed=1, summary="gaps found"):
            self.complete = complete
            self.confidence = confidence
            self.judged = judged
            self.checks_run = checks_run
            self.checks_passed = checks_passed
            self.gaps = []
            self.summary = summary
            self.inconclusive_count = 0

    def _result(self, status="done"):
        class _R:
            pass
        r = _R()
        r.status = status
        r.steps = [self._Step()]
        r.loop_id = "loopXYZ1"
        r.project = "proj-x"
        r.stuck_reason = None
        return r

    def _patch(self, monkeypatch, verdict, annotations):
        import director as _director
        import llm as _llm
        import memory as _memory
        from memory_ledger import OutcomeVerdictStampResult
        monkeypatch.setattr(_llm, "build_adapter", lambda **kw: object())
        monkeypatch.setattr(_director, "verify_goal_completion",
                            lambda *a, **kw: verdict)
        monkeypatch.setattr(
            _memory, "stamp_outcome_verdict",
            lambda loop_id, **kw: (
                annotations.append((loop_id, {
                    key: value for key, value in kw.items()
                    if key != "max_attempts"
                })),
                OutcomeVerdictStampResult(status="updated", attempts=1),
            )[1])

    def test_judged_contradiction_demotes_done(self, monkeypatch):
        ann = []
        self._patch(monkeypatch, self._Verdict(complete=False), ann)
        r = self._result("done")
        v = _cli_module._closure_verdict_pass("the goal", r)
        assert v is not None
        assert r.status == "incomplete"
        assert "closure verification" in r.stuck_reason
        assert ann == [("loopXYZ1", {
            "goal_achieved": False,
            "goal_verdict_source": "closure",
            "goal_verdict_confidence": 0.9,
        })]

    def test_complete_verdict_keeps_done(self, monkeypatch):
        ann = []
        self._patch(monkeypatch, self._Verdict(complete=True, checks_passed=3), ann)
        r = self._result("done")
        _cli_module._closure_verdict_pass("the goal", r)
        assert r.status == "done"
        assert ann[0][1]["goal_achieved"] is True

    def test_unjudged_verdict_never_demotes(self, monkeypatch):
        ann = []
        self._patch(monkeypatch,
                    self._Verdict(complete=False, judged=False), ann)
        r = self._result("done")
        _cli_module._closure_verdict_pass("the goal", r)
        assert r.status == "done"
        assert ann[0][1]["goal_achieved"] is None
        assert ann[0][1]["goal_verdict_source"] == "closure_unverifiable"

    def test_low_confidence_records_but_keeps_done(self, monkeypatch):
        ann = []
        self._patch(monkeypatch,
                    self._Verdict(complete=False, confidence=0.5), ann)
        r = self._result("done")
        _cli_module._closure_verdict_pass("the goal", r)
        assert r.status == "done"
        assert ann[0][1]["goal_achieved"] is False  # recorded, not demoting

    def test_adapter_failure_leaves_run_unverified(self, monkeypatch, capsys):
        import llm as _llm
        monkeypatch.setattr(_llm, "build_adapter",
                            lambda **kw: (_ for _ in ()).throw(RuntimeError("no lane")))
        r = self._result("done")
        assert _cli_module._closure_verdict_pass("the goal", r) is None
        assert r.status == "done"
        assert "done-unverified" in capsys.readouterr().err

    def test_dry_run_skips(self, monkeypatch):
        r = self._result("done")
        assert _cli_module._closure_verdict_pass("g", r, dry_run=True) is None

    def test_no_done_steps_skips(self, monkeypatch):
        r = self._result("done")
        r.steps = [self._Step(status="blocked")]
        assert _cli_module._closure_verdict_pass("g", r) is None

    def test_zero_checks_records_nothing(self, monkeypatch):
        # The fail-open null verdict (checks_run=0) must not bless the run.
        ann = []
        self._patch(monkeypatch,
                    self._Verdict(complete=True, checks_run=0, checks_passed=0), ann)
        r = self._result("done")
        _cli_module._closure_verdict_pass("the goal", r)
        assert ann == []
        assert r.status == "done"


# ---------------------------------------------------------------------------
# `maro viz search` — BACKLOG #17, goal search in the run visualization
# ---------------------------------------------------------------------------

def _make_search_run(tmp_path, handle_id, prompt, *, lane="agenda", success_class=None):
    """Build a run-dir directly under the test workspace (OPENCLAW_WORKSPACE,
    same env var _run() sets) so `runs.runs_root()` finds it."""
    import runs
    prev = os.environ.get("OPENCLAW_WORKSPACE")
    os.environ["OPENCLAW_WORKSPACE"] = str(tmp_path)
    try:
        rd = runs.create_run_dir(handle_id, prompt=prompt, lane=lane)
        runs.write_metadata(rd, handle_id=handle_id, prompt=prompt, lane=lane, status="done")
        if success_class is not None:
            (rd / "run_card.json").write_text(json.dumps({"status": "done", "success_class": success_class}))
    finally:
        if prev is None:
            os.environ.pop("OPENCLAW_WORKSPACE", None)
        else:
            os.environ["OPENCLAW_WORKSPACE"] = prev
    return rd


def test_cli_viz_search_filters_by_goal_text(tmp_path):
    _make_search_run(tmp_path, "vsr00001", "Fix the flaky login test")
    _make_search_run(tmp_path, "vsr00002", "Research polymarket edges")

    r = _run(tmp_path, "viz", "search", "--goal", "login")
    assert r.returncode == 0
    assert "vsr00001" in r.stdout
    assert "vsr00002" not in r.stdout


def test_cli_viz_search_filters_by_status_and_lane(tmp_path):
    _make_search_run(tmp_path, "vsr00003", "Curated success", lane="agenda", success_class="success")
    _make_search_run(tmp_path, "vsr00004", "Curated failure", lane="now", success_class="failed")

    r = _run(tmp_path, "viz", "search", "--status", "success")
    assert r.returncode == 0
    assert "vsr00003" in r.stdout
    assert "vsr00004" not in r.stdout

    r = _run(tmp_path, "viz", "search", "--lane", "now")
    assert "vsr00004" in r.stdout
    assert "vsr00003" not in r.stdout


def test_cli_viz_search_json_format(tmp_path):
    _make_search_run(tmp_path, "vsr00005", "JSON output check", success_class="success")

    r = _run(tmp_path, "viz", "search", "--goal", "json output", "--format", "json")
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert len(payload) == 1
    assert payload[0]["handle_id"] == "vsr00005"
    assert payload[0]["success_class"] == "success"


def test_cli_viz_search_no_matches(tmp_path):
    _make_search_run(tmp_path, "vsr00006", "Some goal")
    r = _run(tmp_path, "viz", "search", "--goal", "nothing-matches-this")
    assert r.returncode == 0
    assert "no matching runs" in r.stdout


# ---------------------------------------------------------------------------
# BACKLOG hist-r2-02: `maro handle --persona <name>` CLI parity with the
# persona:<name>: prompt-prefix path
# ---------------------------------------------------------------------------

def test_cli_handle_persona_flag_threads_through(tmp_path):
    """--persona is forwarded to handle.handle() as the `persona` kwarg —
    same effect as typing a persona:<name>: prefix, but via a real CLI flag."""
    import handle as _handle_mod

    captured = {}

    def _fake_handle(msg, **kwargs):
        captured.update(kwargs)
        return _handle_mod.HandleResult(
            handle_id="test", lane="now", lane_confidence=1.0,
            classification_reason="stub", message=msg, status="done", result="ok",
        )

    with patch("handle.handle", side_effect=_fake_handle):
        r = _run(tmp_path, "handle", "do", "the", "thing", "--persona", "builder")
    assert r.returncode == 0
    assert captured.get("persona") == "builder"


def test_cli_handle_without_persona_flag_passes_none(tmp_path):
    """No --persona given -> handle() gets persona=None, not a missing kwarg."""
    import handle as _handle_mod

    captured = {}

    def _fake_handle(msg, **kwargs):
        captured.update(kwargs)
        return _handle_mod.HandleResult(
            handle_id="test", lane="now", lane_confidence=1.0,
            classification_reason="stub", message=msg, status="done", result="ok",
        )

    with patch("handle.handle", side_effect=_fake_handle):
        r = _run(tmp_path, "handle", "do", "the", "thing")
    assert r.returncode == 0
    assert captured.get("persona") is None


# ---------------------------------------------------------------------------
# BACKLOG #18 residual: the `maro run` CLI lane owns a run-dir + attribution
# capture (was: escaped it entirely, invisible to inspect-run/viz search).
# ---------------------------------------------------------------------------

def _only_run_dir(tmp_path):
    """The single per-run dir created under the test workspace runs/."""
    root = tmp_path / "runs"
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / "metadata.json").is_file()]
    assert len(dirs) == 1, f"expected 1 run-dir, found {[d.name for d in dirs]}"
    return dirs[0]


def test_cli_run_creates_run_dir_with_attribution(tmp_path):
    r = _run(tmp_path, "run", "write a short poem about the sea",
             "--dry-run", "--format", "json")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    loop_id = out["loop_id"]

    rd = _only_run_dir(tmp_path)
    meta = json.loads((rd / "metadata.json").read_text())
    # Lane stamped, loop_id linked, origin captured — full attribution.
    assert meta["lane"] == "agenda"
    assert meta["loop_id"] == loop_id
    assert meta["status"] == "done"
    assert meta["origin"]["source"] == "cli-run"
    assert (rd / "source" / "environment.json").is_file()
    assert (rd / "source" / "prompt.txt").is_file()
    # run_card written by close_run's curation step.
    assert (rd / "run_card.json").is_file()
    # Loop artifacts landed inside the run-dir (the pin flowed through).
    assert list((rd / "build").glob(f"loop-{loop_id}-*"))


def test_cli_run_is_inspectable_by_loop_id_and_handle_id(tmp_path):
    r = _run(tmp_path, "run", "write a short poem", "--dry-run", "--format", "json")
    assert r.returncode == 0
    loop_id = json.loads(r.stdout)["loop_id"]
    rd = _only_run_dir(tmp_path)
    handle_id = rd.name.split("-", 1)[0]

    # inspect-run resolves the run-dir by BOTH ids (no more E_RUN_NOT_FOUND).
    for ref in (loop_id, handle_id):
        view = _run(tmp_path, "inspect-run", ref)
        assert view.returncode == 0, ref
        assert f"loop_id={loop_id}" in view.stdout
        assert "attribution.environment=True" in view.stdout

    jview = _run(tmp_path, "inspect-run", loop_id, "--format", "json")
    payload = json.loads(jview.stdout)
    assert payload["metadata"]["loop_id"] == loop_id
    assert payload["attribution"]["environment"] is True


def test_cli_inspect_run_dir_fallback_unknown_id(tmp_path):
    (tmp_path / "runs").mkdir(parents=True, exist_ok=True)
    r = _run(tmp_path, "inspect-run", "deadbeef")
    assert r.returncode != 0 or "E_RUN_NOT_FOUND" in r.stdout


def test_cli_run_visible_to_viz_search(tmp_path):
    r = _run(tmp_path, "run", "catalogue the tides", "--dry-run", "--format", "json")
    assert r.returncode == 0
    search = _run(tmp_path, "viz", "search", "--goal", "tides", "--format", "json")
    assert search.returncode == 0
    results = json.loads(search.stdout)
    assert any("tides" in (s.get("goal") or "") for s in results)
