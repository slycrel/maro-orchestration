"""Fail-visible tests for run_agent_loop's mandatory execution-fence setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_container_suppression():
    yield
    import container_exec
    container_exec.reset_container_caches()


def _assert_fence_refusal(result, needle: str) -> None:
    assert result.status == "stuck"
    assert result.steps == []
    assert "execution fence setup failed" in result.stuck_reason
    assert needle in result.stuck_reason


def test_project_directory_creation_failure_refuses_before_decomposition(monkeypatch):
    import agent_loop

    class FailingFenceRoot:
        def __truediv__(self, _name):
            return self

        def mkdir(self, **_kwargs):
            raise OSError("cannot create fence directory")

    monkeypatch.setattr(agent_loop, "_project_dir_root", lambda: FailingFenceRoot())
    result = agent_loop.run_agent_loop(
        "write an artifact", project="fence-mkdir-failure", dry_run=True,
    )
    _assert_fence_refusal(result, "cannot create fence directory")


def test_subprocess_cwd_binding_failure_refuses_before_decomposition(monkeypatch):
    import agent_loop
    import llm

    def fail_cwd(_path):
        raise RuntimeError("cwd context unavailable")

    monkeypatch.setattr(llm, "set_default_subprocess_cwd", fail_cwd)
    result = agent_loop.run_agent_loop(
        "write an artifact", project="fence-cwd-failure", dry_run=True,
    )
    _assert_fence_refusal(result, "cwd context unavailable")


def test_writable_root_policy_binding_failure_refuses_before_decomposition(monkeypatch):
    import agent_loop
    import llm

    def fail_policy(_roots):
        raise RuntimeError("rw-root context unavailable")

    monkeypatch.setattr(llm, "set_default_container_rw_roots", fail_policy)
    result = agent_loop.run_agent_loop(
        "write an artifact", project="fence-policy-failure", dry_run=True,
    )
    _assert_fence_refusal(result, "rw-root context unavailable")


def test_declared_root_discovery_failure_degrades_to_cwd_only(monkeypatch):
    import agent_loop
    import artifact_check
    import llm

    bound = []
    real_setter = llm.set_default_container_rw_roots

    def fail_discovery(_goal):
        raise RuntimeError("could not parse declared roots")

    def record_policy(roots):
        bound.append(list(roots))
        real_setter(roots)

    monkeypatch.setattr(artifact_check, "goal_declared_roots", fail_discovery)
    monkeypatch.setattr(llm, "set_default_container_rw_roots", record_policy)
    result = agent_loop.run_agent_loop(
        "write an artifact", project="fence-root-discovery", dry_run=True,
    )

    assert result.status == "done"
    assert bound[-1] == []


def test_clone_setup_exception_suppresses_container_and_continues_on_host(monkeypatch):
    import agent_loop
    import container_exec
    import worktree

    def fail_git_probe(_path):
        raise RuntimeError("git probe failed")

    monkeypatch.setattr(container_exec, "container_configured", lambda: True)
    monkeypatch.setattr(worktree, "is_git_repo", fail_git_probe)
    result = agent_loop.run_agent_loop(
        "write an artifact", project="fence-clone-setup", dry_run=True,
    )

    assert result.status == "done"
    assert container_exec.container_suppressed() is True
