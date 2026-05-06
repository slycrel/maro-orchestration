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

    _mkproj(tmp_path, "demo", "- [ ] first task\n", priority=1)
    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
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
    env["OPENCLAW_WORKSPACE"] = str(tmp_path)
    proc = subprocess.run(
        [str(script), "--project", "demo", "--max-runs", "1", "--worker-session", "done"],
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
    assert payload["items"][0]["project"] == "demo"
