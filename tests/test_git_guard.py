"""Guard-liveness tripwire: the worker push guard must be installed AND armed.

History (2026-07-08): `core.hooksPath` in .git/config still pointed at the
pre-rename absolute repo path (openclaw-orchestration). Git silently treats a
missing hooksPath directory as "no hooks", so every hook — including the
worker push guard shipped in cfab080 — was dead for the 13 days after the
2026-06-25 rename. A maro worker committed and pushed to main during a §7 A/B
run (c8fe130) before anyone noticed. These tests make that failure mode loud:
they assert the guard is present, reachable (no stale hooksPath shadowing it),
in sync with its source, and actually blocks what it claims to block.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLED_HOOK = REPO_ROOT / ".git" / "hooks" / "pre-push"
SOURCE_HOOK = REPO_ROOT / "scripts" / "hooks" / "pre-push"

pytestmark = pytest.mark.skipif(
    not (REPO_ROOT / ".git").is_dir(), reason="not a git checkout"
)

MAIN_PUSH_LINE = (
    "refs/heads/main 1111111111111111111111111111111111111111 "
    "refs/heads/main 2222222222222222222222222222222222222222\n"
)
BRANCH_PUSH_LINE = (
    "refs/heads/work/topic 1111111111111111111111111111111111111111 "
    "refs/heads/work/topic 2222222222222222222222222222222222222222\n"
)


def _run_hook(stdin: str, *, worker: bool) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if k not in ("MARO_WORKER_RUN", "MARO_ALLOW_MAIN_PUSH")}
    if worker:
        env["MARO_WORKER_RUN"] = "1"
    return subprocess.run(
        [str(INSTALLED_HOOK), "origin", "git@github.com:example/repo.git"],
        input=stdin, env=env, capture_output=True, text=True, timeout=10,
    )


def test_hook_installed_and_executable():
    assert INSTALLED_HOOK.is_file(), (
        "worker push guard missing — run scripts/install-git-hooks.sh")
    assert os.access(INSTALLED_HOOK, os.X_OK), (
        "pre-push hook present but not executable — git skips it silently")


def test_no_stale_hooks_path_shadowing_the_guard():
    # A hooksPath pointing at a missing dir disables ALL hooks with no
    # warning (this exact bug shipped a worker commit to main on 2026-07-08).
    proc = subprocess.run(
        ["git", "config", "--get", "core.hooksPath"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return  # unset — git uses .git/hooks, which the tests above cover
    hooks_dir = Path(proc.stdout.strip())
    if not hooks_dir.is_absolute():
        hooks_dir = REPO_ROOT / hooks_dir
    assert hooks_dir.is_dir(), (
        f"core.hooksPath={proc.stdout.strip()} does not exist — every git "
        "hook (incl. the worker push guard) is silently disabled. "
        "`git config --unset core.hooksPath` restores .git/hooks.")
    guard = hooks_dir / "pre-push"
    assert guard.is_file() and os.access(guard, os.X_OK), (
        f"core.hooksPath={hooks_dir} lacks an executable pre-push guard")


def test_installed_hook_matches_source():
    assert INSTALLED_HOOK.read_text() == SOURCE_HOOK.read_text(), (
        "installed pre-push drifted from scripts/hooks/pre-push — "
        "run scripts/install-git-hooks.sh")


def test_worker_push_to_main_is_blocked():
    proc = _run_hook(MAIN_PUSH_LINE, worker=True)
    assert proc.returncode != 0, "guard let a worker push to main"
    assert "blocked" in proc.stderr


def test_worker_push_to_work_branch_is_allowed():
    proc = _run_hook(BRANCH_PUSH_LINE, worker=True)
    assert proc.returncode == 0, proc.stderr


def test_human_push_to_main_is_allowed():
    proc = _run_hook(MAIN_PUSH_LINE, worker=False)
    assert proc.returncode == 0, proc.stderr
