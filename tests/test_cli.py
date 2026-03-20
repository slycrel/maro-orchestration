import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, *args):
    env = os.environ.copy()
    env["OPENCLAW_WORKSPACE"] = str(tmp_path)
    return subprocess.run(["python3", "src/cli.py", *args], cwd=ROOT, env=env, capture_output=True, text=True)


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
