"""Tests for the worker git push guard (scripts/hooks/pre-push).

Governance event 2026-06-11: a vague goal pipeline-executed into an
unreviewed push to origin/main authored as the owner. The guard makes the
policy mechanical: Poe-spawned subprocesses (POE_WORKER_RUN=1, set by the
subprocess adapter) may push work branches but not the default branch,
unless explicitly authorized (POE_ALLOW_MAIN_PUSH=1).
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SRC = REPO_ROOT / "scripts" / "hooks" / "pre-push"


def _git(args, cwd, env=None):
    full_env = dict(os.environ)
    # Hermetic identity; never touch user config.
    full_env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
    })
    if env:
        full_env.update(env)
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=full_env,
        capture_output=True, text=True, timeout=30,
    )


@pytest.fixture()
def repo_pair(tmp_path):
    """A bare origin + a clone with the pre-push hook installed and one commit on main."""
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", "-b", "main", str(origin)], cwd=tmp_path)
    clone = tmp_path / "clone"
    _git(["clone", str(origin), str(clone)], cwd=tmp_path)
    (clone / "f.txt").write_text("x")
    _git(["add", "."], cwd=clone)
    _git(["commit", "-m", "init"], cwd=clone)
    _git(["branch", "-M", "main"], cwd=clone)
    hook_dst = clone / ".git" / "hooks" / "pre-push"
    hook_dst.write_text(HOOK_SRC.read_text())
    hook_dst.chmod(0o755)
    return clone


class TestPushGuard:
    def test_human_push_to_main_allowed(self, repo_pair):
        r = _git(["push", "origin", "main"], cwd=repo_pair,
                 env={"POE_WORKER_RUN": "", "POE_ALLOW_MAIN_PUSH": ""})
        assert r.returncode == 0, r.stderr

    def test_worker_push_to_main_blocked(self, repo_pair):
        r = _git(["push", "origin", "main"], cwd=repo_pair,
                 env={"POE_WORKER_RUN": "1", "POE_ALLOW_MAIN_PUSH": ""})
        assert r.returncode != 0
        assert "worker pushes to the default branch are blocked" in r.stderr

    def test_worker_push_to_work_branch_allowed(self, repo_pair):
        r = _git(["push", "origin", "HEAD:work/topic"], cwd=repo_pair,
                 env={"POE_WORKER_RUN": "1", "POE_ALLOW_MAIN_PUSH": ""})
        assert r.returncode == 0, r.stderr

    def test_authorized_worker_push_to_main_allowed(self, repo_pair):
        r = _git(["push", "origin", "main"], cwd=repo_pair,
                 env={"POE_WORKER_RUN": "1", "POE_ALLOW_MAIN_PUSH": "1"})
        assert r.returncode == 0, r.stderr

    def test_worker_push_to_master_blocked(self, repo_pair):
        r = _git(["push", "origin", "HEAD:master"], cwd=repo_pair,
                 env={"POE_WORKER_RUN": "1", "POE_ALLOW_MAIN_PUSH": ""})
        assert r.returncode != 0


class TestSubprocessEnvMarker:
    def test_adapter_subprocess_env_carries_worker_marker(self, monkeypatch):
        """_run_subprocess_safe marks children as POE_WORKER_RUN=1."""
        import llm
        captured = {}

        class _FakeProc:
            pid = 99999
            returncode = 0
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeProc()

        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        llm._run_subprocess_safe(["true"], timeout=5)
        assert captured["env"] is not None
        assert captured["env"].get("POE_WORKER_RUN") == "1"
        assert "POE_ALLOW_MAIN_PUSH" not in captured["env"]

    def test_config_allow_main_push_sets_bypass(self, monkeypatch):
        import llm
        captured = {}

        class _FakeProc:
            pid = 99999
            returncode = 0
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured["env"] = kwargs.get("env")
            return _FakeProc()

        monkeypatch.setattr("subprocess.Popen", _fake_popen)
        monkeypatch.setattr(
            "config.get",
            lambda key, default=None: True if key == "workers.allow_main_push" else default,
        )
        llm._run_subprocess_safe(["true"], timeout=5)
        assert captured["env"].get("POE_ALLOW_MAIN_PUSH") == "1"
