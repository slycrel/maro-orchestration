from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _mkproj(root: Path, slug: str, content: str, priority: int = 0) -> None:
    project = root / "prototypes" / "poe-orchestration" / "projects" / slug
    project.mkdir(parents=True, exist_ok=True)
    (project / "NEXT.md").write_text(content, encoding="utf-8")
    (project / "PRIORITY").write_text(f"{priority}\n", encoding="utf-8")


def test_build_loop_shell_wrapper_runs_cli(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "build-loop.sh"

    repo_projects = repo_root / "projects"
    repo_projects.mkdir(exist_ok=True)
    repo_project = repo_projects / "repo-local-check"
    repo_project.mkdir(exist_ok=True)
    (repo_project / "NEXT.md").write_text("- [ ] repo local\n", encoding="utf-8")
    (repo_project / "PRIORITY").write_text("99\n", encoding="utf-8")

    repo_workers = repo_root / "workers"
    repo_workers.mkdir(exist_ok=True)
    worker = repo_workers / "done.sh"
    worker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s' \"${ORCH_ITEM_TEXT}\" > \"${ORCH_RUN_ARTIFACT_DIR}/item.txt\"\n",
        encoding="utf-8",
    )
    worker.chmod(0o755)

    env = os.environ.copy()
    env.pop("OPENCLAW_WORKSPACE", None)
    env.pop("POE_WORKSPACE", None)
    env.pop("WORKSPACE_ROOT", None)
    env.pop("POE_ORCH_ROOT", None)
    try:
        proc = subprocess.run(
            [str(script), "--max-runs", "1", "--worker-session", "done"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["status"] == "ok"
        assert payload["runs"] == 1
        assert payload["items"][0]["project"] == "repo-local-check"
    finally:
        for child in repo_project.iterdir():
            child.unlink()
        repo_project.rmdir()
        worker.unlink(missing_ok=True)


def test_build_loop_shell_wrapper_accepts_trailing_workspace_dir(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "build-loop.sh"

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    project = workspace_root / "prototypes" / "poe-orchestration" / "projects" / "workspace-local-check"
    project.mkdir(parents=True, exist_ok=True)
    (project / "NEXT.md").write_text("- [ ] workspace local\n", encoding="utf-8")
    (project / "PRIORITY").write_text("99\n", encoding="utf-8")

    workers = workspace_root / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True, exist_ok=True)
    worker = workers / "done.sh"
    worker.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s' \"${ORCH_ITEM_TEXT}\" > \"${ORCH_RUN_ARTIFACT_DIR}/item.txt\"\n",
        encoding="utf-8",
    )
    worker.chmod(0o755)

    env = os.environ.copy()
    env.pop("OPENCLAW_WORKSPACE", None)
    env.pop("POE_WORKSPACE", None)
    env.pop("WORKSPACE_ROOT", None)
    env.pop("POE_ORCH_ROOT", None)
    try:
        proc = subprocess.run(
            [str(script), "--max-runs", "1", "--worker-session", "done", "--format", "json", "."],
            cwd=workspace_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["status"] == "ok"
        assert payload["runs"] == 1
        assert payload["items"][0]["project"] == "workspace-local-check"
        assert payload["orch_root"] == str(workspace_root / "prototypes" / "poe-orchestration")
    finally:
        worker.unlink(missing_ok=True)
