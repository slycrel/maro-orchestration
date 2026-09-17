"""Tests for the containerized-executor C1 surface — constants, config,
docker probes, operator instructions, and doctor's container rows.

No docker dependency: the subprocess boundary is mocked entirely, so this
runs identically in CI whether or not docker is installed (design:
docs/CONTAINER_EXECUTOR_DESIGN.md, C1 "no docker dependency in CI").
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import container_exec as ce


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_run(returncode=0, stdout="", stderr=""):
    """A subprocess.run stand-in returning a fixed CompletedProcess-shape."""
    def run(cmd, **kwargs):
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


def _capturing_run(store, returncode=0, stdout="", stderr=""):
    """subprocess.run stand-in that records the command vector into `store`."""
    def run(cmd, **kwargs):
        store["cmd"] = cmd
        store["kwargs"] = kwargs
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return run


# ---------------------------------------------------------------------------
# Constants — the image identity is internally consistent
# ---------------------------------------------------------------------------

class TestConstants:
    def test_default_image_encodes_cli_pin_and_contents_revision(self):
        # The tag encodes the CLI pin so it's auditable (design §3), AND a
        # contents revision — without it, changing the toolset and rebuilding
        # makes one tag name two different images (Jeremy 2026-08-06).
        assert ce.DEFAULT_IMAGE == (
            f"maro-executor:{ce.CLAUDE_CLI_VERSION}-r{ce.IMAGE_REVISION}")

    def test_image_revision_is_a_positive_int(self):
        assert isinstance(ce.IMAGE_REVISION, int) and ce.IMAGE_REVISION >= 1

    def test_dockerfile_changes_require_an_image_revision_bump(self):
        """Census tripwire: the tag's honesty depends on a human bump.

        The tag promises "this name is exactly one image". Nothing enforces
        that promise at build time, so it is enforced here: edit the
        Dockerfile without bumping IMAGE_REVISION (or bumping the CLI pin,
        which mints a fresh tag namespace) and this fails with instructions.

        Same shape as the pyproject py-modules census in test_packaging.py —
        a constant that must track a file, checked by hashing the file.
        """
        import hashlib
        from pathlib import Path

        dockerfile = (Path(__file__).resolve().parent.parent
                      / "deploy" / "docker" / "Dockerfile.executor")
        assert dockerfile.exists(), dockerfile
        digest = hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:16]

        # (CLAUDE_CLI_VERSION, IMAGE_REVISION) -> Dockerfile digest.
        # Add a row when you bump; do not edit an existing row's digest.
        KNOWN = {
            ("2.1.210", 1): "fbcb4ad9b4ffb9aa",
            ("2.1.210", 2): "1e0cf1a80909d799",   # + python3-pytest
            ("2.1.210", 3): "4d4cc6d24e36487d",   # + baked maro verbs (src
            # tree + maro-read/maro-fetch shims + python3-yaml/-requests)
        }
        key = (ce.CLAUDE_CLI_VERSION, ce.IMAGE_REVISION)
        expected = KNOWN.get(key)
        assert expected is not None, (
            f"no recorded digest for {key}. If you changed the CLI pin or "
            f"bumped IMAGE_REVISION, add ({key[0]!r}, {key[1]}): "
            f"{digest!r} to KNOWN."
        )
        assert digest == expected, (
            f"Dockerfile.executor changed ({digest} != {expected}) but the "
            f"image tag did not: {ce.DEFAULT_IMAGE} would now name a second, "
            f"different image. Bump IMAGE_REVISION in src/container_exec.py "
            f"and add ({ce.CLAUDE_CLI_VERSION!r}, {ce.IMAGE_REVISION + 1}): "
            f"{digest!r} to KNOWN."
        )

    def test_auth_mount_under_container_home(self):
        assert ce.AUTH_MOUNT == f"{ce.CONTAINER_HOME}/.claude"

    def test_name_prefix_is_greppable(self):
        # The stranded-container sweep filters on this exact prefix.
        assert ce.NAME_PREFIX == "maro-exec-"


# ---------------------------------------------------------------------------
# Config normalization
# ---------------------------------------------------------------------------

class TestContainerMode:
    def _patch_cfg(self, monkeypatch, value):
        monkeypatch.setattr(ce, "get", lambda k, d=None: value if k == "executor.container" else d)

    def test_default_is_off(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        assert ce.container_mode() == "off"

    @pytest.mark.parametrize("val", ["on", "require", "off"])
    def test_valid_modes_passthrough(self, monkeypatch, val):
        self._patch_cfg(monkeypatch, val)
        assert ce.container_mode() == val

    def test_case_and_whitespace_normalized(self, monkeypatch):
        self._patch_cfg(monkeypatch, "  ON ")
        assert ce.container_mode() == "on"

    def test_bool_true_is_on(self, monkeypatch):
        self._patch_cfg(monkeypatch, True)
        assert ce.container_mode() == "on"

    def test_bool_false_is_off(self, monkeypatch):
        self._patch_cfg(monkeypatch, False)
        assert ce.container_mode() == "off"

    def test_unknown_fails_safe_to_off(self, monkeypatch):
        # An unrecognized mode must never silently enable/require containers.
        self._patch_cfg(monkeypatch, "sandboxed")
        assert ce.container_mode() == "off"

    def test_raw_preserves_configured_value(self, monkeypatch):
        self._patch_cfg(monkeypatch, "sandboxed")
        assert ce.container_mode_raw() == "sandboxed"


class TestContainerImage:
    def test_default(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        assert ce.container_image() == ce.DEFAULT_IMAGE

    def test_config_override(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: "custom:v9" if k == "executor.container_image" else d)
        assert ce.container_image() == "custom:v9"

    def test_empty_override_falls_back(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: "" if k == "executor.container_image" else d)
        assert ce.container_image() == ce.DEFAULT_IMAGE


# ---------------------------------------------------------------------------
# Docker probes — every "docker not present" shape maps to a clear reason
# ---------------------------------------------------------------------------

class TestDockerProbe:
    def test_daemon_reachable(self, monkeypatch):
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout="24.0.5\n"))
        ok, detail = ce.docker_probe()
        assert ok and "24.0.5" in detail

    def test_binary_missing(self, monkeypatch):
        def boom(cmd, **kw):
            raise FileNotFoundError()
        monkeypatch.setattr(ce.subprocess, "run", boom)
        ok, detail = ce.docker_probe()
        assert not ok and "not found" in detail

    def test_daemon_wedged_times_out(self, monkeypatch):
        def boom(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, ce._PROBE_TIMEOUT_S)
        monkeypatch.setattr(ce.subprocess, "run", boom)
        ok, detail = ce.docker_probe()
        assert not ok and "timed out" in detail

    def test_daemon_down_surfaces_stderr_reason(self, monkeypatch):
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(
            1, stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock\nIs the daemon running?"))
        ok, detail = ce.docker_probe()
        assert not ok and detail == "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"


class TestImageProbe:
    def test_present(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout="[{}]"))
        ok, detail = ce.image_probe()
        assert ok and detail == ce.DEFAULT_IMAGE

    def test_absent_points_at_setup(self, monkeypatch):
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(1, stderr="No such image"))
        ok, detail = ce.image_probe("maro-executor:test")
        assert not ok and "maro-executor:test" in detail and "container-setup" in detail


class TestAuthVolumeProbe:
    def test_present(self, monkeypatch):
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout="[{}]"))
        ok, detail = ce.auth_volume_probe()
        assert ok and ce.AUTH_VOLUME in detail

    def test_absent(self, monkeypatch):
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(1, stderr="no such volume"))
        ok, detail = ce.auth_volume_probe()
        assert not ok and "container-setup" in detail


class TestLoginProbe:
    def test_builds_expected_command_vector(self, monkeypatch):
        store: dict = {}
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)  # network → default bridge
        monkeypatch.setattr(ce.subprocess, "run", _capturing_run(store, 0, stdout="ok"))
        ok, detail = ce.login_probe("maro-executor:test")
        cmd = store["cmd"]
        assert ok
        assert cmd[0] == "docker" and "run" in cmd and "--rm" in cmd
        assert f"type=volume,source={ce.AUTH_VOLUME},target={ce.AUTH_MOUNT}" in cmd
        assert f"HOME={ce.CONTAINER_HOME}" in cmd
        assert "maro-executor:test" in cmd
        assert ce.DEFAULT_NETWORK in cmd
        # probes as the SAME uid the executor uses (not root) — else it would
        # falsely certify root-owned auth the real worker can't read.
        import os as _os
        assert cmd[cmd.index("--user") + 1] == f"{_os.getuid()}:{_os.getgid()}"
        # ends with the cheap no-tools probe call
        assert cmd[-5:] == ["claude", "-p", "ok", "--tools", ""]

    def test_failure_reported(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(1, stderr="Invalid API key"))
        ok, detail = ce.login_probe("maro-executor:test")
        assert not ok and "login failed" in detail


# ---------------------------------------------------------------------------
# Operator instructions
# ---------------------------------------------------------------------------

class TestInstructions:
    def test_build_command_pins_version_and_tag(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        cmd = ce.build_command()
        assert f"CLAUDE_CLI_VERSION={ce.CLAUDE_CLI_VERSION}" in cmd
        assert f"-t {ce.DEFAULT_IMAGE}" in cmd
        assert "Dockerfile.executor" in cmd

    def test_login_command_uses_auth_volume_and_matching_uid(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        cmd = ce.login_command()
        assert f"type=volume,source={ce.AUTH_VOLUME},target={ce.AUTH_MOUNT}" in cmd
        assert "claude /login" in cmd
        # seeds the volume as the SAME uid the executor runs as, so auth files
        # stay readable/refreshable at run time.
        assert "--user $(id -u):$(id -g)" in cmd

    def test_setup_walkthrough_covers_both_steps(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        text = ce.container_setup_instructions()
        assert "docker build" in text
        assert "claude /login" in text
        assert "executor.container: off" in text  # names the default posture
        assert "npm view @anthropic-ai/claude-code version" in text  # re-pin path


# ---------------------------------------------------------------------------
# doctor container rows — integration (LLM probe faked, no network/spend)
# ---------------------------------------------------------------------------

class _FakeResp:
    content = "ok"


class _FakeAdapter:
    def complete(self, *a, **k):
        return _FakeResp()


# ===========================================================================
# C2 — the wrap: decision, command construction, kill, stranded sweep
# ===========================================================================

@pytest.fixture(autouse=True)
def _reset_container_caches():
    ce.reset_container_caches()
    yield
    ce.reset_container_caches()


class TestResolveContainerRun:
    def _mode(self, monkeypatch, value):
        monkeypatch.setattr(ce, "get", lambda k, d=None: value if k == "executor.container" else d)

    def test_not_executor_stays_host(self, monkeypatch):
        # Non-executor calls (verify/quality-gate/refinement/planning/probes)
        # never containerize even with docker up + mode on.
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        assert ce.resolve_container_run(no_tools=False, executor=False) is None

    def test_off_returns_none_without_probing_docker(self, monkeypatch):
        self._mode(monkeypatch, "off")
        probed = {"hit": False}
        def probe():
            probed["hit"] = True
            return (True, "x")
        monkeypatch.setattr(ce, "docker_probe", probe)
        assert ce.resolve_container_run(no_tools=False, executor=True) is None
        assert probed["hit"] is False  # off short-circuits before any docker cost

    def test_no_tools_stays_host_even_when_on(self, monkeypatch):
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        assert ce.resolve_container_run(no_tools=True, executor=True) is None

    def test_on_with_docker_returns_name(self, monkeypatch):
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "_current_loop_id", lambda: "wily-glen")
        name = ce.resolve_container_run(no_tools=False, executor=True)
        assert name and name.startswith("maro-exec-wily-glen-")

    def test_docker_probed_fresh_every_call(self, monkeypatch):
        # No availability cache: each executor call reflects the CURRENT daemon
        # state (so on->degrade stays honest if docker dies mid-process).
        self._mode(monkeypatch, "on")
        calls = {"n": 0}
        def probe():
            calls["n"] += 1
            return (True, "docker 24")
        monkeypatch.setattr(ce, "docker_probe", probe)
        monkeypatch.setattr(ce, "_current_loop_id", lambda: "r")
        ce.resolve_container_run(no_tools=False, executor=True)
        ce.resolve_container_run(no_tools=False, executor=True)
        assert calls["n"] == 2

    def test_on_without_docker_degrades_to_host_warning_throttled(self, monkeypatch, caplog):
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "docker binary not found on PATH"))
        with caplog.at_level("WARNING"):
            assert ce.resolve_container_run(no_tools=False, executor=True) is None
            assert ce.resolve_container_run(no_tools=False, executor=True) is None  # second call
        warns = [r for r in caplog.records if "container lane is unavailable" in r.message]
        assert len(warns) == 1  # throttled within the 60s window

    def test_require_without_docker_refuses(self, monkeypatch):
        self._mode(monkeypatch, "require")
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "docker binary not found on PATH"))
        with pytest.raises(ce.ContainerUnavailable):
            ce.resolve_container_run(no_tools=False, executor=True)

    def test_require_with_docker_returns_name(self, monkeypatch):
        self._mode(monkeypatch, "require")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "_current_loop_id", lambda: "run1")
        assert ce.resolve_container_run(no_tools=False, executor=True).startswith("maro-exec-run1-")

    def test_require_with_tripped_breaker_raises_the_auth_subtype(self, monkeypatch):
        # Docker is up, the session is dead: the refusal must be the
        # ContainerAuthExpired subtype (carrying the container_auth marker
        # llm_errors keys on) so the run PAUSES typed instead of churning
        # blocked-step retries (2026-09-13). Docker-down stays the base type.
        self._mode(monkeypatch, "require")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "auth_breaker_blocks",
                            lambda: "Failed to authenticate: OAuth session expired")
        with pytest.raises(ce.ContainerAuthExpired) as ei:
            ce.resolve_container_run(no_tools=False, executor=True)
        assert isinstance(ei.value, ce.ContainerUnavailable)
        assert ei.value.maro_error_class == "container_auth"
        assert "re-seed" in str(ei.value) and ce.AUTH_VOLUME in str(ei.value)
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "docker down"))
        with pytest.raises(ce.ContainerUnavailable) as ei2:
            ce.resolve_container_run(no_tools=False, executor=True)
        assert not isinstance(ei2.value, ce.ContainerAuthExpired)

    def test_on_with_tripped_breaker_still_degrades(self, monkeypatch):
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "auth_breaker_blocks", lambda: "not logged in")
        assert ce.resolve_container_run(no_tools=False, executor=True) is None


class TestContainerName:
    def test_prefix_loop_and_components(self):
        n = ce.container_name("abc", 5)
        # maro-exec-<loop>-<pid>-<seq>: PID makes it unique across processes.
        assert n.startswith("maro-exec-abc-")
        assert n.endswith("-5")
        assert str(__import__("os").getpid()) in n

    def test_sanitizes_illegal_chars(self):
        n = ce.container_name("a/b c:d", 0)
        assert n.startswith("maro-exec-")
        leaked = [c for c in ("/", " ", ":") if c in n]
        assert not leaked, f"illegal chars survived sanitization: {leaked} in {n!r}"


class TestBuildRunCommand:
    def _build(self, monkeypatch, inner=("claude", "-p", "--verbose"), **overrides):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)  # network default, image default
        kw = dict(name="maro-exec-x-0", workdir="/w",
                  mounts=[("/w", "rw"), ("/ref", "ro")],
                  worker_env={"MARO_WORKER_RUN": "1", "MARO_ALLOW_MAIN_PUSH": "1"},
                  owner_pid=4242)
        kw.update(overrides)
        return ce.build_run_command(list(inner), **kw)

    def test_docker_run_skeleton(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert cmd[:6] == ["docker", "run", "--rm", "-i", "--init", "--name"]
        assert cmd[6] == "maro-exec-x-0"

    def test_bind_mounts_are_colon_safe_mount_specs(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert "type=bind,source=/w,target=/w" in cmd
        assert "type=bind,source=/ref,target=/ref,readonly" in cmd

    def test_host_path_with_colon_is_not_ambiguous(self, monkeypatch):
        cmd = self._build(monkeypatch, mounts=[("/tmp/a:b/proj", "rw")])
        assert "type=bind,source=/tmp/a:b/proj,target=/tmp/a:b/proj" in cmd

    def test_auth_volume_and_home(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert f"type=volume,source={ce.AUTH_VOLUME},target={ce.AUTH_MOUNT}" in cmd
        assert f"HOME={ce.CONTAINER_HOME}" in cmd

    def test_runs_as_invoking_uid(self, monkeypatch):
        cmd = self._build(monkeypatch)
        import os as _os
        assert "--user" in cmd
        assert cmd[cmd.index("--user") + 1] == f"{_os.getuid()}:{_os.getgid()}"

    def test_owner_pid_label(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert "maro.owner_pid=4242" in cmd

    def test_owner_start_label_when_available(self, monkeypatch):
        monkeypatch.setattr(ce, "process_start_token", lambda pid: "birth-token")
        cmd = self._build(monkeypatch)
        assert "maro.owner_start=birth-token" in cmd

    def test_worker_env_passed_through(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert "MARO_WORKER_RUN=1" in cmd
        assert "MARO_ALLOW_MAIN_PUSH=1" in cmd

    def test_workdir_flag(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert cmd[cmd.index("-w") + 1] == "/w"

    def test_network_default(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert cmd[cmd.index("--network") + 1] == ce.DEFAULT_NETWORK

    def test_inner_binary_basenamed_to_container_path(self, monkeypatch):
        # A host-resolved claude path must become bare `claude` (on the image
        # PATH) — the host path doesn't exist inside the container.
        cmd = self._build(monkeypatch, inner=["/opt/homebrew/bin/claude", "-p", "--verbose"])
        i = cmd.index(ce.DEFAULT_IMAGE)
        assert cmd[i:] == [ce.DEFAULT_IMAGE, "claude", "-p", "--verbose"]
        assert "/opt/homebrew/bin/claude" not in cmd

    def test_scratch_dir_bound_at_container_tmp(self, monkeypatch):
        # The per-run scratch dir is bound at /tmp (fixed target, NOT identity-
        # mapped) so cross-step /tmp scratch persists within a run.
        cmd = self._build(monkeypatch, scratch_dir="/ws/runs/abc-frog/scratch")
        assert "type=bind,source=/ws/runs/abc-frog/scratch,target=/tmp" in cmd
        # rw (no ,readonly) — the worker writes here.
        assert "type=bind,source=/ws/runs/abc-frog/scratch,target=/tmp,readonly" not in cmd

    def test_no_scratch_mount_when_scratch_dir_none(self, monkeypatch):
        # No run dir → /tmp stays ephemeral (default), no bind added.
        cmd = self._build(monkeypatch)  # scratch_dir defaults to None
        assert not any(str(x).endswith("target=/tmp") for x in cmd)


class TestRunScratchDir:
    def test_provisions_scratch_under_run_dir(self, monkeypatch, tmp_path):
        rd = tmp_path / "runs" / "abc-frog"
        rd.mkdir(parents=True)
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)  # knob defaults True
        import runs as _runs
        monkeypatch.setattr(_runs, "current_run_dir", lambda: rd)
        got = ce.run_scratch_dir()
        assert got == str((rd / "scratch").resolve())
        assert (rd / "scratch").is_dir()  # created

    def test_none_when_no_active_run_dir(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        import runs as _runs
        monkeypatch.setattr(_runs, "current_run_dir", lambda: None)
        assert ce.run_scratch_dir() is None

    def test_disabled_by_config_knob(self, monkeypatch, tmp_path):
        rd = tmp_path / "runs" / "abc-frog"
        rd.mkdir(parents=True)
        monkeypatch.setattr(
            ce, "get",
            lambda k, d=None: False if k == "executor.container_run_scratch" else d)
        import runs as _runs
        monkeypatch.setattr(_runs, "current_run_dir", lambda: rd)
        assert ce.run_scratch_dir() is None
        assert not (rd / "scratch").exists()  # not provisioned when off


class TestKillContainer:
    def test_calls_docker_kill(self, monkeypatch):
        store: dict = {}
        monkeypatch.setattr(ce.subprocess, "run", _capturing_run(store, 0))
        ce.kill_container("maro-exec-x-0")
        assert store["cmd"] == ["docker", "kill", "maro-exec-x-0"]

    def test_empty_name_is_noop(self, monkeypatch):
        calls = {"n": 0}
        def run(cmd, **kw):
            calls["n"] += 1
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        monkeypatch.setattr(ce.subprocess, "run", run)
        ce.kill_container("")
        assert calls["n"] == 0

    def test_swallows_missing_docker(self, monkeypatch):
        def boom(cmd, **kw):
            raise FileNotFoundError()
        monkeypatch.setattr(ce.subprocess, "run", boom)
        ce.kill_container("maro-exec-x-0")  # must not raise


class TestSweepStrandedContainers:
    def test_filters_by_our_ownership_label(self, monkeypatch):
        # Ownership is decided by the maro.owner_pid LABEL, never the name.
        store: dict = {}
        monkeypatch.setattr(ce.subprocess, "run", _capturing_run(store, 0, stdout=""))
        ce.sweep_stranded_containers(pid_alive=lambda p: True)
        assert "label=maro.owner_pid" in store["cmd"]
        assert f"name={ce.NAME_PREFIX}" not in " ".join(store["cmd"])  # not name-substring

    def test_kills_only_dead_owner(self, monkeypatch):
        ps_out = "maro-exec-a-0\t100\nmaro-exec-b-0\t200\n"
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout=ps_out))
        killed: list = []
        monkeypatch.setattr(ce, "kill_container", lambda n: killed.append(n))
        result = ce.sweep_stranded_containers(pid_alive=lambda p: p == 100)
        assert result == ["maro-exec-b-0"]  # live owner 100 untouched
        assert killed == ["maro-exec-b-0"]

    def test_kills_reused_pid_with_mismatched_birth_token(self, monkeypatch):
        ps_out = "maro-exec-reused-0\t100\toriginal-birth\n"
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout=ps_out))
        killed: list = []
        monkeypatch.setattr(ce, "kill_container", lambda n: killed.append(n))

        result = ce.sweep_stranded_containers(
            pid_alive=lambda p: True,
            process_token=lambda p: "reused-birth",
        )

        assert result == ["maro-exec-reused-0"]
        assert killed == result

    def test_legacy_live_pid_without_birth_token_is_spared(self, monkeypatch):
        ps_out = "maro-exec-legacy-0\t100\t\n"
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout=ps_out))
        killed: list = []
        monkeypatch.setattr(ce, "kill_container", lambda n: killed.append(n))

        result = ce.sweep_stranded_containers(
            pid_alive=lambda p: True,
            process_token=lambda p: "different-birth",
        )

        assert result == []
        assert killed == []

    def test_missing_owner_label_is_skipped_not_killed(self, monkeypatch):
        # A container without a parseable owner label is NOT ours to reap —
        # skip it (never kill on ambiguity; the old code killed these, which
        # combined with substring name matching could destroy 3rd-party ones).
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout="maro-exec-c-0\t\n"))
        killed: list = []
        monkeypatch.setattr(ce, "kill_container", lambda n: killed.append(n))
        result = ce.sweep_stranded_containers(pid_alive=lambda p: True)
        assert result == []
        assert killed == []

    def test_non_prefixed_name_is_skipped(self, monkeypatch):
        # Defense in depth: even a labelled container not matching our name
        # prefix is left alone.
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout="someone-else\t999\n"))
        killed: list = []
        monkeypatch.setattr(ce, "kill_container", lambda n: killed.append(n))
        result = ce.sweep_stranded_containers(pid_alive=lambda p: False)
        assert result == []

    def test_docker_absent_returns_empty(self, monkeypatch):
        def boom(cmd, **kw):
            raise FileNotFoundError()
        monkeypatch.setattr(ce.subprocess, "run", boom)
        assert ce.sweep_stranded_containers() == []

    def test_docker_nonzero_returns_empty(self, monkeypatch):
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(1, stderr="daemon down"))
        assert ce.sweep_stranded_containers() == []


class TestDoctorContainerRows:
    def _fake_llm(self, monkeypatch):
        import bughunter
        import llm
        monkeypatch.setattr(
            llm,
            "detect_backends",
            lambda: [("subprocess", True, "test stub")],
        )
        monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _FakeAdapter())
        monkeypatch.setattr(
            bughunter,
            "run_bughunter",
            lambda: bughunter.BughunterReport(files_scanned=0),
        )

    def test_off_shows_single_info_row(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)  # all defaults → off
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "Container executor" in out
        assert "executor.container=off" in out
        # off → nothing probed
        assert "Container image" not in out

    def test_unrecognized_mode_reported_as_off(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "sandbox" if k == "executor.container" else d)
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "unrecognized" in out and "'sandbox'" in out

    def test_on_without_docker_degrades_loudly(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "on" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "docker binary not found on PATH"))
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "Container executor (on)" in out
        assert "DEGRADE to host/fence-only" in out

    def test_require_without_docker_says_refuse(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "require" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "docker binary not found on PATH"))
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "Container executor (require)" in out
        assert "REFUSE" in out

    def test_on_with_docker_probes_image_and_volume(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "on" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24.0.5"))
        monkeypatch.setattr(ce, "image_probe", lambda img=None: (True, "maro-executor:x"))
        monkeypatch.setattr(ce, "auth_volume_probe", lambda: (True, "volume present"))
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "Container image" in out
        assert "Container auth volume" in out
        assert "Container auth breaker" in out

    def test_tripped_breaker_row_says_reseed(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "on" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24.0.5"))
        monkeypatch.setattr(ce, "image_probe", lambda img=None: (True, "maro-executor:x"))
        monkeypatch.setattr(ce, "auth_volume_probe", lambda: (True, "volume present"))
        monkeypatch.setattr(
            ce, "auth_breaker_state",
            lambda: ({"tripped_at": 1.0, "reason": "oauth session expired"},
                     "tripped"))
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "TRIPPED" in out and "re-seed" in out

    def test_breaker_row_renders_with_docker_down(self, monkeypatch, tmp_path, capsys):
        # The breaker is a file read — a tripped lane must not hide behind a
        # down daemon (review 2026-08-13): the re-seed action is the same.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "on" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "daemon not running"))
        monkeypatch.setattr(
            ce, "auth_breaker_state",
            lambda: ({"tripped_at": 1.0, "reason": "oauth session expired"},
                     "tripped"))
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "Container auth breaker" in out
        assert "TRIPPED" in out

    def test_unreadable_breaker_row_is_loud(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "on" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "image_probe", lambda img=None: (True, "img"))
        monkeypatch.setattr(ce, "auth_volume_probe", lambda: (True, "vol"))
        monkeypatch.setattr(ce, "auth_breaker_state", lambda: (None, "unreadable"))
        self._fake_llm(monkeypatch)
        from doctor import run_doctor
        run_doctor()
        out = capsys.readouterr().out
        assert "UNREADABLE" in out


# ---------------------------------------------------------------------------
# C3 — run-level container gate + fence → mount-map translation
# ---------------------------------------------------------------------------

def R(p):
    """realpath — build_mount_map resolves symlinks, so expected paths must too."""
    return os.path.realpath(str(p))


class TestContainerConfigured:
    """The config-intent gate for self-dev clone provisioning — deliberately no
    docker probe, so the decision can't race the daemon (adversarial-review A)."""

    def test_off_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(ce, "container_mode", lambda: "off")
        assert ce.container_configured() is False

    def test_on_is_configured(self, monkeypatch):
        monkeypatch.setattr(ce, "container_mode", lambda: "on")
        assert ce.container_configured() is True

    def test_require_is_configured(self, monkeypatch):
        monkeypatch.setattr(ce, "container_mode", lambda: "require")
        assert ce.container_configured() is True

    def test_does_not_probe_docker(self, monkeypatch):
        monkeypatch.setattr(ce, "container_mode", lambda: "on")
        def _boom():
            raise AssertionError("container_configured must not probe docker")
        monkeypatch.setattr(ce, "docker_probe", _boom)
        assert ce.container_configured() is True


class TestContainerSuppression:
    def test_suppressed_run_stays_on_host(self, monkeypatch):
        ce.reset_container_caches()
        monkeypatch.setattr(ce, "get", lambda k, d=None: "on" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        # Without suppression an executor call would containerize...
        assert ce.resolve_container_run(no_tools=False, executor=True) is not None
        # ...suppression forces host (never mounts the live repo rw).
        ce.set_container_suppressed(True)
        assert ce.resolve_container_run(no_tools=False, executor=True) is None
        ce.reset_container_caches()  # also clears suppression
        assert ce.container_suppressed() is False


class TestBuildMountMap:
    @pytest.fixture(autouse=True)
    def _scope_defaults_to_tmp(self, tmp_path, monkeypatch):
        # Containment whitelist (C4-BOX 2026-07-15): a goal-declared rw root is
        # mounted only within the workspace subtree + write_fence_allow. These
        # tests build rw roots under tmp_path, so default the workspace scope to
        # tmp_path — the scope filter is then transparent and each test exercises
        # its intended translation/dedup/forbidden behavior. Tests that assert a
        # scope/forbidden rejection override workspace_root or pass
        # write_scope_roots explicitly.
        import config as cfg
        monkeypatch.setattr(cfg, "workspace_root", lambda: tmp_path)

    def test_cwd_only_is_single_rw_mount(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        assert ce.build_mount_map(str(cwd)) == [(R(cwd), "rw")]

    def test_rw_root_outside_write_scope_dropped(self, tmp_path, monkeypatch):
        # THE containment fix: a goal that declares a host path OUTSIDE the
        # workspace subtree (and not in write_fence_allow) gets it dropped, not
        # mounted — the acceptance-probe scenario in miniature. Without this, a
        # hostile goal naming ~/.ssh or a secret file would ride goal_declared_roots
        # straight into an rw bind.
        import config as cfg
        ws = tmp_path / "ws"; ws.mkdir()
        monkeypatch.setattr(cfg, "workspace_root", lambda: ws)
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)  # no write_fence_allow
        cwd = ws / "run"; cwd.mkdir()
        secret = tmp_path / "host-secret"; secret.mkdir()
        (secret / "token.txt").write_text("s")
        out = ce.build_mount_map(str(cwd), rw_roots=[str(secret / "token.txt")])
        assert out == [(R(cwd), "rw")]
        assert all("host-secret" not in p for p, _ in out)

    def test_rw_root_in_write_fence_allow_kept(self, tmp_path, monkeypatch):
        # The explicit escape hatch: an out-of-workspace root listed in
        # validate.write_fence_allow IS mounted (operator opt-in).
        import config as cfg
        ws = tmp_path / "ws"; ws.mkdir()
        monkeypatch.setattr(cfg, "workspace_root", lambda: ws)
        allowed = tmp_path / "allowed"; allowed.mkdir()
        monkeypatch.setattr(
            ce, "get",
            lambda k, d=None: [str(allowed)] if k == "validate.write_fence_allow" else d)
        cwd = ws / "run"; cwd.mkdir()
        target = allowed / "data"; target.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(target)])
        assert (R(target), "rw") in out

    def test_write_scope_roots_param_injects_scope(self, tmp_path):
        # The injectable param: a root under an injected scope is kept; one
        # outside every scope is dropped.
        cwd = tmp_path / "run"; cwd.mkdir()
        inside = tmp_path / "scope" / "data"; inside.mkdir(parents=True)
        outside = tmp_path / "elsewhere"; outside.mkdir()
        out = ce.build_mount_map(
            str(cwd), rw_roots=[str(inside), str(outside)],
            write_scope_roots=[str(tmp_path / "scope"), str(cwd)])
        assert (R(inside), "rw") in out
        assert all("elsewhere" not in p for p, _ in out)

    def test_cwd_realpath_resolved_for_workdir_match(self, tmp_path):
        # A symlinked cwd resolves to its target (what docker binds + -w names).
        real = tmp_path / "real"; real.mkdir()
        link = tmp_path / "link"; link.symlink_to(real)
        out = ce.build_mount_map(str(link))
        assert out == [(R(real), "rw")]

    def test_rw_root_added(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        extra = tmp_path / "data"; extra.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(extra)])
        assert (R(extra), "rw") in out
        assert out[0] == (R(cwd), "rw")  # cwd first

    def test_missing_rw_root_mounts_existing_parent_not_created(self, tmp_path):
        # A declared-but-not-yet-created path ("write the report to X") gets
        # its existing parent mounted so the worker can create it — but the
        # missing path itself is never created host-side (purity holds).
        cwd = tmp_path / "proj"; cwd.mkdir()
        data = tmp_path / "data"; data.mkdir()
        missing = data / "report.md"
        out = ce.build_mount_map(str(cwd), rw_roots=[str(missing)])
        assert (R(data), "rw") in out
        assert all(p != R(missing) for p, _ in out)
        assert not missing.exists()  # pure — never created

    def test_rw_root_with_missing_parent_is_dropped(self, tmp_path):
        # No existing directory within ONE level → dropped (loudly), never
        # walked further up and never created.
        cwd = tmp_path / "proj"; cwd.mkdir()
        missing = tmp_path / "nodir" / "report.md"
        out = ce.build_mount_map(str(cwd), rw_roots=[str(missing)])
        assert out == [(R(cwd), "rw")]
        assert not missing.parent.exists()

    def test_file_rw_root_mounts_parent_dir(self, tmp_path):
        # The fence authorizes exact file paths; the mount translation binds
        # the file's parent dir (single-file binds detach on atomic renames).
        # C4-BOX burn-in finding 2026-07-14 (run 585f95f2).
        cwd = tmp_path / "proj"; cwd.mkdir()
        data = tmp_path / "data"; data.mkdir()
        f = data / "input.txt"; f.write_text("x")
        out = ce.build_mount_map(str(cwd), rw_roots=[str(f)])
        assert (R(data), "rw") in out
        assert all(p != R(f) for p, _ in out)

    def test_multiple_file_roots_same_dir_dedup_to_one_mount(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        data = tmp_path / "data"; data.mkdir()
        a = data / "a.py"; a.write_text("x")
        b = data / "b.json"; b.write_text("y")
        out = ce.build_mount_map(str(cwd), rw_roots=[str(a), str(b), str(data / "new.json")])
        assert out.count((R(data), "rw")) == 1
        assert len(out) == 2  # cwd + the one shared parent

    def test_file_root_translation_still_respects_forbidden(self, tmp_path, monkeypatch):
        # A file root whose parent IS (or contains) a forbidden root must not
        # smuggle that parent in via translation.
        ws = tmp_path / "ws"; ws.mkdir()
        import config as cfg
        monkeypatch.setattr(cfg, "workspace_root", lambda: ws)
        cwd = tmp_path / "proj"; cwd.mkdir()
        deep_file = tmp_path / "ws-sibling.txt"  # parent = tmp_path, contains ws
        out = ce.build_mount_map(str(cwd), rw_roots=[str(deep_file)])
        assert out == [(R(cwd), "rw")]

    def test_rw_root_equal_to_cwd_deduped(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(cwd)])
        assert out == [(R(cwd), "rw")]

    def test_rw_root_nested_under_cwd_deduped(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        child = cwd / "sub"; child.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(child)])
        assert out == [(R(cwd), "rw")]

    def test_normpath_dedups_against_cwd(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        weird = str(cwd) + "/../proj"  # normalizes to cwd
        out = ce.build_mount_map(str(cwd), rw_roots=[weird])
        assert out == [(R(cwd), "rw")]

    def test_ro_mount_added(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        ref = tmp_path / "ref"; ref.mkdir()
        out = ce.build_mount_map(str(cwd), ro_mounts=[str(ref)])
        assert (R(ref), "ro") in out

    def test_missing_ro_mount_skipped(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        out = ce.build_mount_map(str(cwd), ro_mounts=[str(tmp_path / "absent")])
        assert out == [(R(cwd), "rw")]

    def test_ro_nested_under_rw_parent_dropped(self, tmp_path):
        # A rw parent already grants write (hence read) to the child — the ro
        # mount is meaningless and dropped.
        cwd = tmp_path / "proj"; cwd.mkdir()
        child = cwd / "ref"; child.mkdir()
        out = ce.build_mount_map(str(cwd), ro_mounts=[str(child)])
        assert out == [(R(cwd), "rw")]

    def test_rw_nested_under_ro_parent_kept(self, tmp_path):
        # A ro parent does NOT cover a rw child — the nested rw mount stays.
        parent = tmp_path / "ref"; parent.mkdir()
        child = parent / "writable"; child.mkdir()
        cwd = tmp_path / "proj"; cwd.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(child)], ro_mounts=[str(parent)])
        assert (R(child), "rw") in out
        assert (R(parent), "ro") in out

    def test_dedup_is_order_independent(self, tmp_path):
        # Child listed BEFORE its parent: the parent still subsumes the child.
        cwd = tmp_path / "proj"; cwd.mkdir()
        parent = tmp_path / "data"; parent.mkdir()
        child = parent / "sub"; child.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(child), str(parent)])
        assert (R(parent), "rw") in out
        assert (R(child), "rw") not in out

    def test_empty_and_none_entries_ignored(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=["", None], ro_mounts=[None, ""])
        assert out == [(R(cwd), "rw")]

    def test_comma_path_skipped(self, tmp_path):
        cwd = tmp_path / "proj"; cwd.mkdir()
        weird = tmp_path / "a,b"; weird.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(weird)])
        assert out == [(R(cwd), "rw")]  # comma path dropped (docker --mount CSV)

    def test_workspace_root_is_forbidden(self, tmp_path, monkeypatch):
        # A rw root that IS the workspace root is refused (orchestration mount).
        ws = tmp_path / "ws"; ws.mkdir()
        import config as cfg
        monkeypatch.setattr(cfg, "workspace_root", lambda: ws)
        cwd = tmp_path / "proj"; cwd.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(ws)])
        assert out == [(R(cwd), "rw")]

    def test_workspace_ancestor_is_forbidden(self, tmp_path, monkeypatch):
        # Mounting a PARENT of the workspace would expose it too.
        ws = tmp_path / "home" / "ws"; ws.mkdir(parents=True)
        import config as cfg
        monkeypatch.setattr(cfg, "workspace_root", lambda: ws)
        cwd = tmp_path / "proj"; cwd.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(tmp_path / "home")])
        assert out == [(R(cwd), "rw")]

    def test_forbidden_roots_param_excludes_live_repo(self, tmp_path):
        cwd = tmp_path / "clone"; cwd.mkdir()
        repo = tmp_path / "live-repo"; repo.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=[str(repo)], forbidden_roots=[str(repo)])
        assert (R(repo), "rw") not in out
        assert out == [(R(cwd), "rw")]

    def test_tmp_root_is_forbidden(self, tmp_path):
        # Bare host /tmp must never be a mount; a specific subdir is fine.
        cwd = tmp_path / "proj"; cwd.mkdir()
        out = ce.build_mount_map(str(cwd), rw_roots=["/tmp"])
        assert (R("/tmp"), "rw") not in out


class TestIntrospectionProvision:
    """Decree 2026-07-18 ("Install in the container only for the runs that
    need access"): introspection-shaped runs get ro run records + maro source
    in the executor container; everything else keeps blind isolation."""

    @pytest.fixture(autouse=True)
    def _clean_flag(self):
        ce.reset_container_caches()
        yield
        ce.reset_container_caches()

    def test_unflagged_run_gets_none(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        assert ce.introspection_provision() is None

    def test_config_gate_off_gets_none(self, monkeypatch):
        ce.set_introspection_run(True)
        monkeypatch.setattr(
            ce, "get",
            lambda k, d=None: False if k == "executor.introspection_access" else d)
        assert ce.introspection_provision() is None

    def test_flagged_run_gets_runs_dir_ro_plus_source(self, tmp_path, monkeypatch):
        import config as cfg
        runs = tmp_path / "runs"; runs.mkdir()
        monkeypatch.setattr(cfg, "workspace_root", lambda: tmp_path)
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        ce.set_introspection_run(True)
        out = ce.introspection_provision()
        assert out is not None
        assert R(runs) in out["ro_mounts"]
        assert out["env"]["MARO_INTROSPECTION"] == "1"
        assert out["env"]["MARO_INTROSPECTION_RUNS"] == R(runs)
        # maro source rides along for the best-effort in-container CLI
        src_dir = os.path.dirname(os.path.realpath(ce.__file__))
        assert src_dir in out["ro_mounts"]
        assert out["env"]["PYTHONPATH"] == src_dir

    def test_mount_view_marker_names_the_blind_spots(self, tmp_path, monkeypatch):
        """Mount-blindness fix (d9607baa's sibling find): the env carries a
        ground-truth description of the partial view so a worker never has
        to report its blinkered view as host fact."""
        import config as cfg
        runs = tmp_path / "runs"; runs.mkdir()
        monkeypatch.setattr(cfg, "workspace_root", lambda: tmp_path)
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        ce.set_introspection_run(True)
        out = ce.introspection_provision()
        view = out["env"]["MARO_MOUNT_VIEW"]
        assert view.startswith("partial:")
        assert ".git" in view

    def test_workspace_root_itself_never_mounted(self, tmp_path, monkeypatch):
        # runs/ is a workspace DESCENDANT; the root (memory/, config, secrets)
        # must never appear in the provision list.
        import config as cfg
        runs = tmp_path / "runs"; runs.mkdir()
        monkeypatch.setattr(cfg, "workspace_root", lambda: tmp_path)
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        ce.set_introspection_run(True)
        out = ce.introspection_provision()
        assert R(tmp_path) not in out["ro_mounts"]

    def test_missing_runs_dir_fails_closed(self, tmp_path, monkeypatch):
        # All-or-nothing (adversarial-review 2026-07-18, consensus): no run
        # records → NO provisioning at all. A source-only mount with
        # MARO_INTROSPECTION=1 would claim introspection while reproducing
        # the records-blind failure this feature exists to fix.
        import config as cfg
        monkeypatch.setattr(cfg, "workspace_root", lambda: tmp_path)  # no runs/
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        ce.set_introspection_run(True)
        assert ce.introspection_provision() is None

    def test_symlinked_runs_dir_fails_closed(self, tmp_path, monkeypatch):
        # A symlinked runs/ (→ memory/, secrets/, anywhere) must not ride the
        # introspection grant past the workspace-descendant allowance.
        import config as cfg
        memory = tmp_path / "memory"; memory.mkdir()
        (tmp_path / "runs").symlink_to(memory)
        monkeypatch.setattr(cfg, "workspace_root", lambda: tmp_path)
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        ce.set_introspection_run(True)
        assert ce.introspection_provision() is None

    def test_reset_clears_flag(self):
        ce.set_introspection_run(True)
        assert ce.introspection_run() is True
        ce.reset_container_caches()
        assert ce.introspection_run() is False

    def test_provision_survives_forbidden_filter(self, tmp_path, monkeypatch):
        # Defense in depth: the provision output still passes build_mount_map's
        # forbidden filter — a bug here cannot smuggle the workspace root in.
        import config as cfg
        runs = tmp_path / "runs"; runs.mkdir()
        monkeypatch.setattr(cfg, "workspace_root", lambda: tmp_path)
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        ce.set_introspection_run(True)
        out = ce.introspection_provision()
        cwd = tmp_path / "scratch"; cwd.mkdir()
        mounts = ce.build_mount_map(str(cwd), ro_mounts=out["ro_mounts"])
        assert (R(runs), "ro") in mounts
        assert (R(tmp_path), "ro") not in mounts
        assert (R(tmp_path), "rw") not in mounts


class TestAuthBreaker:
    """Container auth breaker (2026-08-13) — auth failure as a degrade
    condition, the outage shape the docker probe can't see."""

    @pytest.fixture(autouse=True)
    def _isolated_breaker(self, tmp_path, monkeypatch):
        self.path = tmp_path / "container_auth_breaker.json"
        self.notify_path = tmp_path / "container_auth_notified.json"
        monkeypatch.setattr(ce, "_auth_breaker_path", lambda: self.path)
        monkeypatch.setattr(ce, "_auth_notify_path", lambda: self.notify_path)
        monkeypatch.setattr(ce, "_MEM_AUTH_BREAKER", None)

    def _mode(self, monkeypatch, value):
        monkeypatch.setattr(ce, "get", lambda k, d=None: value if k == "executor.container" else d)

    def _trip(self, monkeypatch, mode="on", detail="OAuth session expired and could not be refreshed"):
        self._mode(monkeypatch, mode)
        import notify
        monkeypatch.setattr(notify, "emit", lambda *a, **k: True)
        ce.note_container_failure(detail)

    # -- signature matching -------------------------------------------------

    @pytest.mark.parametrize("text", [
        "OAuth session expired and could not be refreshed",
        "Not logged in · Please run /login",
        "oauth token revoked",
        "API Error: authentication_error",
        # Wording variants (skeptic review 2026-08-13 finding 1): the CLI's
        # phrasing is not under our control — close variants must trip too.
        "OAuth session has expired. Please run /login again",
        "OAuth token has expired",
        "OAuth access token expired",
    ])
    def test_auth_error_text_matches_cli_login_failures(self, text):
        assert ce.is_auth_error_text(text) is True

    def test_auth_marker_past_byte_300_still_trips(self, monkeypatch):
        # Finding 1's other half: the seam now hands the breaker more than
        # the 300-char display detail, so a marker after diagnostics
        # preamble must still trip.
        self._trip(monkeypatch,
                   detail=("diagnostic preamble " * 20)
                   + "OAuth session expired")
        assert ce.auth_breaker_snapshot() is not None

    @pytest.mark.parametrize("text", [
        "fetch failed with 403 Forbidden",   # worker-work HTTP error, not the lane
        "401 from upstream API in step output",
        "rate limit exceeded",
        "",
    ])
    def test_auth_error_text_ignores_non_lane_errors(self, text):
        assert ce.is_auth_error_text(text) is False

    # -- trip ---------------------------------------------------------------

    def test_note_failure_trips_on_auth_error(self, monkeypatch):
        self._trip(monkeypatch)
        state = ce.auth_breaker_snapshot()
        assert state is not None
        assert "OAuth session expired" in state["reason"]

    def test_note_failure_ignores_non_auth_error(self, monkeypatch):
        self._trip(monkeypatch, detail="claude subprocess failed (rc=1): boom")
        assert ce.auth_breaker_snapshot() is None

    def test_second_trip_does_not_renotify(self, monkeypatch):
        self._mode(monkeypatch, "on")
        import notify
        calls = []
        monkeypatch.setattr(notify, "emit", lambda et, p, **k: calls.append(et))
        ce.note_container_failure("oauth session expired")
        ce.note_container_failure("oauth session expired")
        assert calls == ["backend_actionable"]

    def test_flap_does_not_renotify_within_window(self, monkeypatch):
        # Skeptic review 2026-08-13 finding 3: a false clear (credentials
        # touched without a real re-seed) re-trips on the next call. The
        # notify throttle lives in a sidecar that SURVIVES the clear, so a
        # trip→clear→trip flap sends one Telegram, not one per cycle.
        # (emit returns True = DELIVERED; the sidecar stamps only on
        # delivery under the merged delivery-guarantee semantics.)
        self._mode(monkeypatch, "on")
        import notify
        calls = []
        monkeypatch.setattr(notify, "emit",
                            lambda et, p, **k: calls.append(et) or True)
        ce.note_container_failure("oauth session expired")
        ce.clear_auth_breaker("false clear")
        ce.note_container_failure("oauth session expired")
        assert ce.auth_breaker_snapshot() is not None  # re-tripped...
        assert calls == ["backend_actionable"]          # ...but one notify
        # The suppressed re-trip is marked notified — the retry lane must
        # not fire either (composition with the delivery guarantee).
        assert ce.auth_breaker_snapshot().get("notified") is True

    def test_flap_renotifies_after_window(self, monkeypatch):
        self._mode(monkeypatch, "on")
        import notify, json, time as _time
        calls = []
        monkeypatch.setattr(notify, "emit",
                            lambda et, p, **k: calls.append(et) or True)
        ce.note_container_failure("oauth session expired")
        ce.clear_auth_breaker("false clear")
        self.notify_path.write_text(json.dumps(
            {"at": _time.time() - ce._AUTH_NOTIFY_TTL_S - 1}) + "\n")
        ce.note_container_failure("oauth session expired")
        assert calls == ["backend_actionable", "backend_actionable"]

    def test_broken_notify_throttle_fails_open_to_notifying(self, monkeypatch):
        # A broken throttle must not silence a real outage.
        self._mode(monkeypatch, "on")
        import notify
        calls = []
        monkeypatch.setattr(notify, "emit",
                            lambda et, p, **k: calls.append(et) or True)
        self.notify_path.write_text("not json")
        ce.note_container_failure("oauth session expired")
        assert calls == ["backend_actionable"]

    def test_trip_with_broken_store_still_notifies_and_degrades(self, monkeypatch):
        # Review 2026-08-13: a persist failure used to swallow the notify
        # entirely AND leave the lane containerizing — the original silent
        # outage. Now: never raises, still emits the actionable event, and
        # degrades process-locally via the in-memory fallback breaker.
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "_trip_auth_breaker",
                            lambda r: (_ for _ in ()).throw(OSError("disk")))
        import notify
        emitted = []
        monkeypatch.setattr(notify, "emit", lambda et, p, **k: emitted.append(et) or True)
        ce.note_container_failure("oauth session expired")  # must not raise
        assert emitted == ["backend_actionable"]
        assert ce.auth_breaker_blocks() is not None  # mem fallback degrades
        # A second failure in the same process doesn't re-notify.
        ce.note_container_failure("oauth session expired")
        assert emitted == ["backend_actionable"]

    def test_failed_notify_is_retried_on_recheck_cadence(self, monkeypatch):
        # Review 2026-08-13: "one notification" used to mean "at most one
        # ATTEMPT" — a transient Telegram outage forfeited it forever.
        import notify
        sent = {"ok": False}
        emitted = []
        monkeypatch.setattr(notify, "emit",
                            lambda et, p, **k: emitted.append(et) or sent["ok"])
        self._mode(monkeypatch, "on")
        ce.note_container_failure("oauth session expired")
        assert emitted == ["backend_actionable"]  # attempted, delivery failed
        assert ce.auth_breaker_snapshot().get("notified") is False
        # Recheck window elapses, probe still bad → retry fires and succeeds.
        self._age_state()
        monkeypatch.setattr(ce, "_reseed_probe", lambda t: (False, "still wiped"))
        sent["ok"] = True
        assert ce.auth_breaker_blocks() is not None
        assert emitted == ["backend_actionable", "backend_actionable"]
        assert ce.auth_breaker_snapshot().get("notified") is True
        # Delivered — the next recheck does not emit again.
        self._age_state()
        assert ce.auth_breaker_blocks() is not None
        assert len(emitted) == 2

    def test_trip_reason_is_secret_scrubbed(self, monkeypatch):
        # Auth error text can embed credential-shaped material; it must not
        # reach the marker or the notification (review 2026-08-13).
        import notify
        payloads = []
        monkeypatch.setattr(notify, "emit",
                            lambda et, p, **k: payloads.append(p) or True)
        self._mode(monkeypatch, "on")
        ce.note_container_failure(
            "authentication_error: invalid bearer token sk-ant-abcdef1234567890XYZ")
        state = ce.auth_breaker_snapshot()
        assert "sk-ant-" not in state["reason"]
        assert "[REDACTED]" in state["reason"]
        assert all("sk-ant-" not in str(p) for p in payloads)

    def test_clear_if_leaves_a_newer_trip_alone(self, monkeypatch):
        # A probe vouches only for the trip it read. A NEW trip landing while
        # the probe ran must survive the clear (review 2026-08-13).
        import json
        self._trip(monkeypatch)
        old_tripped_at = ce.auth_breaker_snapshot()["tripped_at"]
        newer = {"tripped_at": old_tripped_at + 60.0, "reason": "new failure",
                 "last_recheck": 0.0, "notified": True}
        self.path.write_text(json.dumps(newer))
        ce._clear_auth_breaker_if(old_tripped_at, "probe ok")
        assert ce.auth_breaker_snapshot() is not None  # newer trip survives
        ce._clear_auth_breaker_if(old_tripped_at + 60.0, "probe ok")
        assert ce.auth_breaker_snapshot() is None  # matching trip clears

    def test_recheck_touch_does_not_resurrect_cleared_breaker(self, monkeypatch):
        # A stale failed probe must not rewrite state a peer already cleared
        # (review 2026-08-13: the resurrection race).
        self._trip(monkeypatch)
        tripped_at = ce.auth_breaker_snapshot()["tripped_at"]
        self.path.unlink()  # peer cleared while our probe ran
        ce._touch_auth_breaker_recheck(tripped_at)
        assert ce.auth_breaker_snapshot() is None

    def test_unreadable_marker_is_distinguished_from_clear(self):
        state, status = ce.auth_breaker_state()
        assert (state, status) == (None, "clear")
        self.path.write_text("{not json")
        state, status = ce.auth_breaker_state()
        assert state is None and status == "unreadable"

    # -- resolve integration ------------------------------------------------

    def test_on_tripped_degrades_to_host(self, monkeypatch, caplog):
        self._trip(monkeypatch)
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        ce.reset_container_caches()
        with caplog.at_level("WARNING"):
            assert ce.resolve_container_run(no_tools=False, executor=True) is None
        assert any("auth breaker" in r.message for r in caplog.records)

    def test_require_tripped_refuses(self, monkeypatch):
        self._trip(monkeypatch, mode="require")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        with pytest.raises(ce.ContainerUnavailable, match="auth breaker"):
            ce.resolve_container_run(no_tools=False, executor=True)

    def test_require_suppressed_refuses_not_host(self, monkeypatch):
        # require means isolation-or-nothing: a suppressed run (scratch clone
        # unavailable) must REFUSE, never silently execute on the host
        # (review 2026-08-13 — the suppression early-return bypassed require).
        self._mode(monkeypatch, "require")
        monkeypatch.setattr(ce, "container_suppressed", lambda: True)
        with pytest.raises(ce.ContainerUnavailable, match="suppressed"):
            ce.resolve_container_run(no_tools=False, executor=True)

    def test_on_suppressed_still_degrades_to_host(self, monkeypatch):
        # 'on' keeps the C3 doctrine: suppressed → host under the write-fence.
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "container_suppressed", lambda: True)
        assert ce.resolve_container_run(no_tools=False, executor=True) is None

    def test_fresh_trip_blocks_without_docker_recheck(self, monkeypatch):
        # last_recheck is stamped at trip time, so the resolve path pays no
        # docker cost until the TTL elapses.
        self._trip(monkeypatch)
        probed = {"hit": False}
        monkeypatch.setattr(ce, "_reseed_probe",
                            lambda t: probed.update(hit=True) or (False, "x"))
        assert ce.auth_breaker_blocks() is not None
        assert probed["hit"] is False

    # -- recheck / self-clear -----------------------------------------------

    def _age_state(self):
        import json
        state = json.loads(self.path.read_text())
        state["last_recheck"] = 0.0
        self.path.write_text(json.dumps(state))

    def test_recheck_clears_on_reseed(self, monkeypatch):
        self._trip(monkeypatch)
        self._age_state()
        monkeypatch.setattr(ce, "_reseed_probe", lambda t: (True, "re-seeded"))
        assert ce.auth_breaker_blocks() is None
        assert ce.auth_breaker_snapshot() is None  # state file removed

    def test_recheck_still_bad_stays_tripped_and_rearms_ttl(self, monkeypatch):
        self._trip(monkeypatch)
        self._age_state()
        monkeypatch.setattr(ce, "_reseed_probe", lambda t: (False, "still wiped"))
        assert ce.auth_breaker_blocks() is not None
        state = ce.auth_breaker_snapshot()
        assert state["last_recheck"] > 0.0  # TTL re-armed — no per-call docker spin

    def test_blocks_fails_open_on_broken_store(self, monkeypatch):
        self.path.write_text("{not json")
        assert ce.auth_breaker_blocks() is None  # bad session just re-trips

    # -- reseed probe parsing -----------------------------------------------

    def _probe_out(self, mtime, has_refresh):
        # The probe is SHAPE-only — the four-integer frame the shared reader
        # prints (mtime, exact-field refresh flag, two expiries); credential
        # bytes never transit to the host (reviews 2026-08-13, 2026-09-13).
        return f"{mtime} {1 if has_refresh else 0} 1791000000 1789312634"

    def test_reseed_probe_accepts_fresh_live_creds(self, monkeypatch):
        monkeypatch.setattr(ce, "_run",
                            lambda cmd, t: (True, self._probe_out(2000, True)))
        ok, detail = ce._reseed_probe(tripped_at=1000)
        assert ok is True and "re-seeded" in detail

    def test_reseed_probe_never_cats_credentials(self, monkeypatch):
        # The docker command itself must not copy credential bytes out of
        # the volume (review 2026-08-13: the old probe cat'd the full file).
        seen = {}
        monkeypatch.setattr(
            ce, "_run",
            lambda cmd, t: seen.update(cmd=cmd) or (True, self._probe_out(2000, True)))
        ce._reseed_probe(tripped_at=1000)
        shell = " ".join(seen["cmd"])
        assert "cat " not in shell
        assert "readonly" in shell and "print(" in seen["cmd"][-1]
        assert "accessToken" not in seen["cmd"][-1]

    def test_reseed_probe_rejects_a_null_or_absent_token_field(self, monkeypatch):
        # Review 2026-09-13: `grep -c refreshToken` counted key TEXT, so a
        # wiped file keeping `"refreshToken": null` (or only the sibling
        # refreshTokenExpiresAt key) read as re-seeded. The shared reader
        # reports the exact field; the probe must honour it.
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (True, {"mtime": 2000, "has_refresh": False,
                                            "refresh_expires_at": 1791000000, "access_expires_at": 0}))
        ok, detail = ce._reseed_probe(tripped_at=1000)
        assert ok is False and "no refresh token" in detail
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (False, {"detail": "credentials expiry probe failed: docker down"}))
        ok, detail = ce._reseed_probe(tripped_at=1000)
        assert ok is False and "docker down" in detail

    def test_reseed_probe_rejects_wiped_creds(self, monkeypatch):
        # The CLI wipes refreshToken after a failed refresh — that file is
        # exactly the tripped state, however fresh its mtime.
        monkeypatch.setattr(ce, "_run",
                            lambda cmd, t: (True, self._probe_out(2000, False)))
        ok, _ = ce._reseed_probe(tripped_at=1000)
        assert ok is False

    def test_reseed_probe_rejects_unchanged_creds(self, monkeypatch):
        # Intact-looking but server-side-revoked creds must not clear the
        # breaker (would flap trip/clear forever) — only a file NEWER than
        # the trip counts as a re-seed.
        monkeypatch.setattr(ce, "_run",
                            lambda cmd, t: (True, self._probe_out(500, True)))
        ok, _ = ce._reseed_probe(tripped_at=1000)
        assert ok is False

    def test_reseed_probe_handles_docker_failure(self, monkeypatch):
        monkeypatch.setattr(ce, "_run", lambda cmd, t: (False, "daemon wedged"))
        ok, _ = ce._reseed_probe(tripped_at=1000)
        assert ok is False

    def test_clear_is_idempotent(self):
        ce.clear_auth_breaker()  # no state file — must not raise


class TestBakedVerbs:
    """r3 decree chunk (2026-08-13): the image bakes the maro verbs;
    hosted-free keys arrive as ENV at spin-up, never in the image."""

    def _image(self, monkeypatch, tag):
        monkeypatch.setattr(
            ce, "get",
            lambda k, d=None: tag if k == "executor.container_image" else d)

    def test_default_image_bakes_verbs(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        assert ce.IMAGE_REVISION >= ce._VERBS_BAKED_FROM_REVISION
        assert ce.image_bakes_verbs() is True

    def test_pre_r3_image_does_not(self, monkeypatch):
        self._image(monkeypatch, "maro-executor:2.1.210-r2")
        assert ce.image_bakes_verbs() is False

    def test_custom_image_is_conservative(self, monkeypatch):
        # Advertising a verb into an image that lacks it is the A/B-4
        # confound; an unrecognized tag answers False.
        self._image(monkeypatch, "my-own-executor:latest")
        assert ce.image_bakes_verbs() is False

    def test_never_raises(self, monkeypatch):
        monkeypatch.setattr(ce, "container_image",
                            lambda: (_ for _ in ()).throw(RuntimeError()))
        assert ce.image_bakes_verbs() is False

    # -- spin-up env injection ---------------------------------------------

    def test_passthrough_env_renders_bare_e_flags(self):
        # Bare -e NAME: the value rides the docker CLIENT's env, never the
        # argv — keys must not appear in host process listings.
        cmd = ce.build_run_command(
            ["claude", "-p", "x"], name="t",
            passthrough_env=["GEMINI_API_KEY", "GROQ_API_KEY"])
        joined = " ".join(cmd)
        assert "-e GEMINI_API_KEY" in joined
        assert "-e GROQ_API_KEY" in joined
        assert "GEMINI_API_KEY=" not in joined
        assert "GROQ_API_KEY=" not in joined

    def test_container_env_empty_without_host_consent(self, monkeypatch):
        import hosted_free as hf
        monkeypatch.setattr(ce, "image_bakes_verbs", lambda: True)
        monkeypatch.setattr(hf, "hosted_free_enabled", lambda: False)
        monkeypatch.setenv("GROQ_API_KEY", "k")
        assert ce.hosted_free_container_env() == {}

    def test_container_env_empty_without_baked_verbs(self, monkeypatch):
        import hosted_free as hf
        monkeypatch.setattr(ce, "image_bakes_verbs", lambda: False)
        monkeypatch.setattr(hf, "hosted_free_enabled", lambda: True)
        monkeypatch.setenv("GROQ_API_KEY", "k")
        assert ce.hosted_free_container_env() == {}

    def test_container_env_carries_keys_and_consent_flag(self, monkeypatch):
        import hosted_free as hf
        monkeypatch.setattr(ce, "image_bakes_verbs", lambda: True)
        monkeypatch.setattr(hf, "hosted_free_enabled", lambda: True)
        monkeypatch.setattr(hf, "_load_env", lambda: {})
        monkeypatch.setenv("GROQ_API_KEY", "groq-secret")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        env = ce.hosted_free_container_env()
        assert env["GROQ_API_KEY"] == "groq-secret"
        assert env["MARO_HOSTED_FREE_ENABLED"] == "1"
        assert "GEMINI_API_KEY" not in env

    def test_container_env_empty_without_any_key(self, monkeypatch):
        import hosted_free as hf
        monkeypatch.setattr(ce, "image_bakes_verbs", lambda: True)
        monkeypatch.setattr(hf, "hosted_free_enabled", lambda: True)
        monkeypatch.setattr(hf, "_load_env", lambda: {})
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        # No keys -> no consent flag either: the flag transports a
        # capability, not a bare opinion.
        assert ce.hosted_free_container_env() == {}


class TestEnforceBackendContainerContract:
    """Executor-lane backend gate (2026-08-13 review residual, fixed
    2026-08-15): resolve_container_run lives inside the subprocess
    adapter, so any other backend serving executor=True never made a
    container decision — require was silently void, on was invisibly
    host-run."""

    class _Backend:
        def __init__(self, name, capable):
            self.backend = name
            self.container_capable = capable

    def _mode(self, monkeypatch, value):
        monkeypatch.setattr(
            ce, "get",
            lambda k, d=None: value if k == "executor.container" else d)

    def test_require_incapable_refuses(self, monkeypatch):
        self._mode(monkeypatch, "require")
        with pytest.raises(ce.ContainerUnavailable) as exc:
            ce.enforce_backend_container_contract(
                self._Backend("codex", False), executor=True)
        assert "codex" in str(exc.value)
        assert "require" in str(exc.value)

    def test_require_capable_passes(self, monkeypatch):
        self._mode(monkeypatch, "require")
        ce.enforce_backend_container_contract(
            self._Backend("subprocess", True), executor=True)

    def test_non_executor_never_gated(self, monkeypatch):
        # Utility/orchestration calls are not the executor lane — the
        # contract must not capture them even at require + incapable.
        self._mode(monkeypatch, "require")
        ce.enforce_backend_container_contract(
            self._Backend("openrouter", False), executor=False)

    def test_off_is_a_noop(self, monkeypatch):
        self._mode(monkeypatch, "off")
        ce.enforce_backend_container_contract(
            self._Backend("codex", False), executor=True)

    def test_on_incapable_warns_visibly_and_throttled(
            self, monkeypatch, caplog):
        # SF-6: the difference between sandboxed and not must be
        # visible — but once per throttle window, not once per step.
        import logging
        self._mode(monkeypatch, "on")
        ce.reset_container_caches()
        with caplog.at_level(logging.WARNING, logger="maro.container_exec"):
            ce.enforce_backend_container_contract(
                self._Backend("codex", False), executor=True)
            ce.enforce_backend_container_contract(
                self._Backend("codex", False), executor=True)
        warns = [r for r in caplog.records
                 if "cannot containerize" in r.getMessage()]
        assert len(warns) == 1
        assert "host" in warns[0].getMessage()

    def test_missing_capability_attr_reads_as_incapable(self, monkeypatch):
        # Fail closed by construction: an adapter that never declared
        # capability is refused under require, not waved through.
        self._mode(monkeypatch, "require")
        with pytest.raises(ce.ContainerUnavailable):
            ce.enforce_backend_container_contract(
                SimpleNamespace(backend="mystery"), executor=True)


class TestMountRefusalNamesTheActualOutcome:
    """A path outside the write scope has two very different fates, and the
    log line used to describe both the same way. Found 2026-08-16: a
    reference corpus was ro-mounted and working while the line said it had
    been refused — the operator believed the line, and the same text goes
    into the worker's step context."""

    def _map(self, tmp_path, ro):
        """Drive the real write-scope knob rather than patching internals."""
        ws = tmp_path / "ws"
        (ws / "run").mkdir(parents=True)
        ref = tmp_path / "ref"
        ref.mkdir()
        mounts = ce.build_mount_map(
            str(ws / "run"),
            rw_roots=[str(ref)],
            ro_mounts=[str(ref)] if ro else [],
            write_scope_roots=[str(ws)])
        return ref, mounts

    def test_a_readable_path_is_reported_as_readable_not_refused(
            self, tmp_path, caplog):
        with caplog.at_level(logging.INFO):
            ref, mounts = self._map(tmp_path, ro=True)
        assert (str(ref), "ro") in [(os.path.realpath(h), m) for h, m in mounts]
        text = caplog.text
        assert "mounted READ-ONLY" in text
        assert "reads work, writes do not" in text
        assert "cannot READ it at all" not in text

    def test_an_unmountable_path_says_the_container_cannot_read_it(
            self, tmp_path, caplog):
        with caplog.at_level(logging.INFO):
            ref, mounts = self._map(tmp_path, ro=False)
        assert str(ref) not in [os.path.realpath(h) for h, _ in mounts]
        assert "cannot READ it at all" in caplog.text
        assert "container_extra_mounts for read access" in caplog.text


class TestAuthLiveness:
    """Heartbeat-recorded session expiry (2026-09-13): the breaker is
    reactive, the refresh token's own expiry is knowable in advance."""

    DAY = 86400.0

    def _ws(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "require" if k == "executor.container" else d)

    def test_verdict_levels(self):
        now = 1_800_000_000.0
        rec = lambda exp, has=True: {"checked_at": now, "ok": True, "has_refresh": has,
                                     "refresh_expires_at": exp}
        assert ce.auth_liveness_verdict(None, now=now)[0] == "unknown"
        assert ce.auth_liveness_verdict({"checked_at": now, "ok": False,
                                         "detail": "probe failed"}, now=now) == ("unknown", "probe failed")
        assert ce.auth_liveness_verdict(rec(now + 20 * self.DAY), now=now)[0] == "ok"
        level, detail = ce.auth_liveness_verdict(rec(now + 2 * self.DAY), now=now)
        assert level == "warn" and "re-seed" in detail and ce.AUTH_VOLUME in detail
        # Boundary: exactly the warn margin still warns; a hair over does not.
        assert ce.auth_liveness_verdict(rec(now + 3 * self.DAY), now=now)[0] == "warn"
        assert ce.auth_liveness_verdict(rec(now + 3 * self.DAY + 1), now=now)[0] == "ok"
        assert ce.auth_liveness_verdict(rec(now - 1), now=now)[0] == "expired"
        assert ce.auth_liveness_verdict(rec(now + 20 * self.DAY, has=False), now=now)[0] == "expired"
        assert ce.auth_liveness_verdict(rec(0), now=now)[0] == "unknown"

    def test_refresh_records_probe_and_honours_ttl(self, monkeypatch, tmp_path):
        self._ws(monkeypatch, tmp_path)
        calls = {"n": 0}
        def probe():
            calls["n"] += 1
            return True, {"mtime": 1, "has_refresh": True,
                          "refresh_expires_at": 2_000_000_000, "access_expires_at": 1_900_000_000}
        monkeypatch.setattr(ce, "_credentials_expiry_probe", probe)
        rec = ce.refresh_auth_liveness()
        assert rec["ok"] and rec["refresh_expires_at"] == 2_000_000_000
        assert ce._auth_liveness_path().exists()
        assert ce.auth_liveness_state()["refresh_expires_at"] == 2_000_000_000
        # Fresh record: no second docker run within the TTL.
        ce.refresh_auth_liveness()
        assert calls["n"] == 1
        ce.refresh_auth_liveness(force=True)
        assert calls["n"] == 2
        ce.refresh_auth_liveness(max_age_s=0)
        assert calls["n"] == 3

    def test_refresh_records_a_failed_probe_as_unknown_not_ok(self, monkeypatch, tmp_path):
        self._ws(monkeypatch, tmp_path)
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (False, {"detail": "credentials expiry probe failed: docker down"}))
        rec = ce.refresh_auth_liveness()
        assert rec["ok"] is False
        assert ce.auth_liveness_verdict(rec)[0] == "unknown"

    def test_refresh_is_a_noop_when_lane_off(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "off" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (_ for _ in ()).throw(AssertionError("must not probe")))
        assert ce.refresh_auth_liveness() is None
        assert not ce._auth_liveness_path().exists()

    def test_expiry_probe_parses_timestamps_only(self, monkeypatch):
        # The probe's contract: four integers, nothing else leaves the container.
        seen = {}
        def run(cmd, timeout):
            seen["cmd"] = cmd
            return True, "1789283834 1 1791000000 1789312634\n"
        monkeypatch.setattr(ce, "_run", run)
        ok, info = ce._credentials_expiry_probe()
        assert ok and info == {"mtime": 1789283834, "has_refresh": True,
                               "refresh_expires_at": 1791000000, "access_expires_at": 1789312634}
        assert "readonly" in " ".join(seen["cmd"])
        assert "print(" in seen["cmd"][-1] and "refreshToken" in seen["cmd"][-1]
        assert "accessToken" not in seen["cmd"][-1]
        monkeypatch.setattr(ce, "_run", lambda cmd, timeout: (True, "garbage"))
        ok, info = ce._credentials_expiry_probe()
        assert not ok and "unparseable" in info["detail"]


class TestAuthLivenessHardening:
    """Review 2026-09-13 fixes: strict wire frame, validated record, failed
    probes keep the last good sample, one probe per cadence under overlap."""

    DAY = 86400.0

    def _ws(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "require" if k == "executor.container" else d)

    @pytest.mark.parametrize("wire", [
        "1 1 2 3\nextra line",          # two lines
        "UNEXPECTED\n1 1 2 3",           # junk prefix
        "1 2 2 3",                       # flag outside {0,1}
        "1 -1 2 3",                      # negative flag
        "1 1 2",                         # three fields
        "1 1 2 3 4",                     # five fields
        "garbage",
        "",
    ])
    def test_wire_frame_is_exactly_four_integers(self, monkeypatch, wire):
        monkeypatch.setattr(ce, "_run", lambda cmd, timeout: (True, wire))
        ok, info = ce._credentials_expiry_probe()
        assert ok is False and "unparseable" in info["detail"]

    def test_the_container_script_reports_shape_only_and_never_raises(self, tmp_path):
        # Run the exact generated program against synthetic credential files:
        # it prints the frame (never a traceback carrying file contents) for
        # live, wiped, null-token, string-typed and structurally wrong files.
        import subprocess, sys, json as _json
        import re as _re
        seen = {}
        ce._run = ce._run  # (module attr; not patched here)
        real_run = ce._run
        try:
            def cap(cmd, timeout):
                seen["script"] = cmd[-1]
                return (True, "1 1 2 3")
            ce._run = cap
            ce._credentials_expiry_probe()
        finally:
            ce._run = real_run
        script = seen["script"].replace(f"{ce.AUTH_MOUNT}/.credentials.json", str(tmp_path / "c.json"))
        cases = {
            "live": {"claudeAiOauth": {"refreshToken": "rt-secret-bytes", "refreshTokenExpiresAt": 1791000000000, "expiresAt": 1789312634000}},
            "null": {"claudeAiOauth": {"refreshToken": None, "refreshTokenExpiresAt": 1791000000000}},
            "empty": {"claudeAiOauth": {"refreshToken": "", "refreshTokenExpiresAt": 1791000000000}},
            "string_ts": {"claudeAiOauth": {"refreshToken": "x", "refreshTokenExpiresAt": "1791000000000"}},
            "bad_ts": {"claudeAiOauth": {"refreshToken": "x", "refreshTokenExpiresAt": "soon"}},
            "huge_ts": {"claudeAiOauth": {"refreshToken": "x", "refreshTokenExpiresAt": 10 ** 400}},
        }
        out = {}
        for name, doc in cases.items():
            p = tmp_path / "c.json"
            p.write_text(_json.dumps(doc))
            r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
            assert r.returncode == 0 and r.stderr == "", (name, r.stderr)
            assert _re.fullmatch(r"[0-9]+ [01] [0-9]+ [0-9]+\n", r.stdout), (name, r.stdout)
            assert "rt-secret-bytes" not in r.stdout
            out[name] = r.stdout.split()
        assert out["live"][1:] == ["1", "1791000000", "1789312634"]
        assert out["null"][1] == "0" and out["empty"][1] == "0"
        assert out["string_ts"][1:3] == ["1", "1791000000"]
        assert out["bad_ts"][1:3] == ["1", "0"] and out["huge_ts"][1:3] == ["1", "0"]
        # Review round 2: a file that is not the CLI's shape is a FAILED
        # observation (fixed message, exit 1, nothing on stdout) — not the
        # zero frame, which read as "session wiped" and dropped last_good.
        failures = {
            "list": _json.dumps({"claudeAiOauth": ["nope"]}),
            "no_oauth": _json.dumps({"other": {"refreshToken": "rt-secret-bytes"}}),
            "not_json": "{{{ rt-secret-bytes",
        }
        for name, text in failures.items():
            (tmp_path / "c.json").write_text(text)
            r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
            assert r.returncode == 1 and r.stdout == "", (name, r.stdout)
            assert r.stderr.strip() == "credentials file missing, unreadable or not the CLI shape", (name, r.stderr)
            assert "rt-secret-bytes" not in r.stderr and "Traceback" not in r.stderr
        (tmp_path / "c.json").unlink()
        r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
        assert r.returncode == 1 and "Traceback" not in r.stderr and r.stdout == ""

    @pytest.mark.parametrize("row", [
        {"checked_at": 1.8e9, "ok": "false", "has_refresh": "false", "refresh_expires_at": 2e9},
        {"checked_at": 1.8e9, "ok": True, "has_refresh": "false", "refresh_expires_at": 2e9},
        {"checked_at": float("nan"), "ok": True, "has_refresh": True, "refresh_expires_at": 2e9},
        {"checked_at": float("inf"), "ok": True, "has_refresh": True, "refresh_expires_at": 2e9},
        {"checked_at": 1.8e9, "ok": True, "has_refresh": True, "refresh_expires_at": float("nan")},
        {"checked_at": 1.8e9, "ok": True, "has_refresh": True, "refresh_expires_at": 1e300},
        {"checked_at": 1.8e9 + 10 * 86400, "ok": True, "has_refresh": True, "refresh_expires_at": 2e9},  # future
        {"checked_at": 0, "ok": True, "has_refresh": True, "refresh_expires_at": 2e9},
        {"checked_at": True, "ok": True, "has_refresh": True, "refresh_expires_at": 2e9},
        {"checked_at": 1.8e9, "ok": True, "refresh_expires_at": 2e9},          # has_refresh missing
        ["not", "a", "dict"],
        None,
    ])
    def test_invalid_records_read_as_absent(self, row):
        now = 1.8e9 + 60
        assert ce._valid_liveness_record(row, now=now) is None
        assert ce.auth_liveness_verdict(row, now=now)[0] == "unknown"

    def test_invalid_file_is_absent_and_refreshed(self, monkeypatch, tmp_path):
        self._ws(monkeypatch, tmp_path)
        p = ce._auth_liveness_path(); p.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        # future checked_at: must not suppress the refresh
        p.write_text(_json.dumps({"checked_at": 4e9, "ok": True, "has_refresh": True, "refresh_expires_at": 2e9}))
        assert ce.auth_liveness_state() is None
        calls = {"n": 0}
        def probe():
            calls["n"] += 1
            return True, {"mtime": 1, "has_refresh": True, "refresh_expires_at": 2_000_000_000, "access_expires_at": 0}
        monkeypatch.setattr(ce, "_credentials_expiry_probe", probe)
        rec = ce.refresh_auth_liveness()
        assert calls["n"] == 1 and rec["ok"] is True

    def test_a_stale_record_is_unknown_not_current(self):
        now = 1.8e9
        rec = {"checked_at": now - 3 * self.DAY, "ok": True, "has_refresh": True, "refresh_expires_at": now + 20 * self.DAY}
        assert ce._valid_liveness_record(rec, now=now) is not None      # readable ...
        assert ce.auth_liveness_verdict(rec, now=now)[0] == "unknown"   # ... but no longer evidence
        rec_fresh = dict(rec, checked_at=now - self.DAY)
        assert ce.auth_liveness_verdict(rec_fresh, now=now)[0] == "ok"

    def test_a_failed_probe_keeps_the_last_good_sample(self, monkeypatch, tmp_path):
        # Review 2026-09-13: a docker outage overwrote the expiry sample and
        # the health lane narrated "recovered". The warning must survive.
        self._ws(monkeypatch, tmp_path)
        now = time.time()
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (True, {"mtime": 1, "has_refresh": True,
                                            "refresh_expires_at": int(now + 2 * self.DAY), "access_expires_at": 0}))
        first = ce.refresh_auth_liveness(force=True)
        assert ce.auth_liveness_verdict(first)[0] == "warn"
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (False, {"detail": "credentials expiry probe failed: docker down"}))
        second = ce.refresh_auth_liveness(force=True)
        assert second["ok"] is False and second["last_good"]["refresh_expires_at"] == first["refresh_expires_at"]
        level, detail = ce.auth_liveness_verdict(ce.auth_liveness_state())
        assert level == "warn" and "last good sample" in detail and "docker down" in detail
        # a third failure carries the same last_good forward (no nesting)
        third = ce.refresh_auth_liveness(force=True)
        assert third["last_good"] == second["last_good"] and "last_good" not in third["last_good"]
        # a real recovery replaces it: fresh sample, no last_good
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (True, {"mtime": 2, "has_refresh": True,
                                            "refresh_expires_at": int(now + 29 * self.DAY), "access_expires_at": 0}))
        fourth = ce.refresh_auth_liveness(force=True)
        assert "last_good" not in fourth and ce.auth_liveness_verdict(fourth)[0] == "ok"

    def test_a_failed_probe_with_no_prior_sample_is_unknown(self, monkeypatch, tmp_path):
        self._ws(monkeypatch, tmp_path)
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (False, {"detail": "credentials expiry probe failed: docker down"}))
        rec = ce.refresh_auth_liveness()
        assert "last_good" not in rec and ce.auth_liveness_verdict(rec)[0] == "unknown"

    def test_overlapping_refreshes_probe_once(self, monkeypatch, tmp_path):
        # Review 2026-09-13: two heartbeats read the same absent record and
        # both launched docker. The record's lock serializes check+probe+write.
        import threading
        self._ws(monkeypatch, tmp_path)
        calls = {"n": 0}
        started = threading.Event()
        def probe():
            calls["n"] += 1
            started.set()
            time.sleep(0.3)
            return True, {"mtime": 1, "has_refresh": True, "refresh_expires_at": 2_000_000_000, "access_expires_at": 0}
        monkeypatch.setattr(ce, "_credentials_expiry_probe", probe)
        results = []
        t1 = threading.Thread(target=lambda: results.append(ce.refresh_auth_liveness()))
        t1.start(); started.wait(2)
        t2 = threading.Thread(target=lambda: results.append(ce.refresh_auth_liveness()))
        t2.start(); t1.join(5); t2.join(5)
        assert calls["n"] == 1
        assert len(results) == 2 and all(r and r["ok"] for r in results)

    def test_a_failed_persist_is_warned_not_hidden(self, monkeypatch, tmp_path, caplog):
        self._ws(monkeypatch, tmp_path)
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (True, {"mtime": 1, "has_refresh": False, "refresh_expires_at": 0, "access_expires_at": 0}))
        import file_lock
        monkeypatch.setattr(file_lock, "atomic_write", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        with caplog.at_level("WARNING"):
            assert ce.refresh_auth_liveness(force=True) is None
        assert any("liveness refresh failed" in r.message for r in caplog.records)


class TestRoundTwoHardening:
    """Review round 2 (2026-09-13): the fixes' own gaps."""

    DAY = 86400.0

    def _ws(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(ce, "get", lambda k, d=None: "require" if k == "executor.container" else d)

    def test_reseed_probe_rejects_an_expired_refresh_token(self, monkeypatch):
        # A restored backup: newer mtime, token present, but the reader
        # says the token itself is already past its expiry — not a re-seed.
        now = time.time()
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (True, {"mtime": int(now), "has_refresh": True,
                                            "refresh_expires_at": int(now - 3600), "access_expires_at": 0}))
        ok, detail = ce._reseed_probe(tripped_at=now - 600)
        assert ok is False and "expired" in detail and "not re-seeded" in detail
        # unknown expiry (0) stays shape-only (the pre-round-2 contract)
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: (True, {"mtime": int(now), "has_refresh": True,
                                            "refresh_expires_at": 0, "access_expires_at": 0}))
        assert ce._reseed_probe(tripped_at=now - 600)[0] is True

    def test_an_unreadable_credentials_file_keeps_last_good(self, monkeypatch, tmp_path):
        # The container script exits 1 with a fixed message; the host must
        # record a FAILED probe (last_good kept) — not "wiped".
        self._ws(monkeypatch, tmp_path)
        now = time.time()
        monkeypatch.setattr(ce, "_run", lambda cmd, timeout: (True, f"{int(now)} 1 {int(now + 2 * self.DAY)} 0"))
        first = ce.refresh_auth_liveness(force=True)
        assert ce.auth_liveness_verdict(first)[0] == "warn"
        monkeypatch.setattr(ce, "_run", lambda cmd, timeout: (False, "credentials file missing, unreadable or not the CLI shape"))
        second = ce.refresh_auth_liveness(force=True)
        assert second["ok"] is False and second["last_good"]["has_refresh"] is True
        level, detail = ce.auth_liveness_verdict(second)
        assert level == "warn" and "unreadable" in detail

    def test_huge_json_integer_reads_as_absent_and_is_refreshed(self, monkeypatch, tmp_path):
        # math.isfinite(10**400) raises OverflowError; the validator must be
        # total, and an invalid file must be REPLACED by the next refresh.
        import json as _json
        self._ws(monkeypatch, tmp_path)
        p = ce._auth_liveness_path(); p.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for field in ("checked_at", "refresh_expires_at", "access_expires_at", "mtime"):
            rec = {"checked_at": now - 60, "ok": True, "has_refresh": True,
                   "refresh_expires_at": now + 10 * self.DAY, "access_expires_at": 0, "mtime": 1}
            rec[field] = 10 ** 400
            p.write_text(_json.dumps(rec))
            assert ce.auth_liveness_state() is None, field
            assert ce.auth_liveness_verdict(ce.auth_liveness_state())[0] == "unknown"
        # nested: a huge value inside last_good drops the sample, not the record
        rec = {"checked_at": now - 60, "ok": False, "detail": "x",
               "last_good": {"checked_at": now - 3600, "ok": True, "has_refresh": True,
                             "refresh_expires_at": 10 ** 400}}
        p.write_text(_json.dumps(rec))
        got = ce.auth_liveness_state()
        assert got is not None and "last_good" not in got
        calls = {"n": 0}
        def probe():
            calls["n"] += 1
            return True, {"mtime": 1, "has_refresh": True, "refresh_expires_at": int(now + 20 * self.DAY), "access_expires_at": 0}
        monkeypatch.setattr(ce, "_credentials_expiry_probe", probe)
        p.write_text(_json.dumps({"checked_at": 10 ** 400, "ok": True, "has_refresh": True, "refresh_expires_at": 1}))
        fresh = ce.refresh_auth_liveness()
        assert calls["n"] == 1 and fresh["ok"] is True and ce.auth_liveness_verdict(fresh)[0] == "ok"

    def test_last_good_nests_one_level_only(self):
        now = time.time()
        inner = {"checked_at": now - 7200, "ok": True, "has_refresh": True, "refresh_expires_at": now + self.DAY}
        mid = dict(inner, checked_at=now - 3600, last_good=inner)
        rec = {"checked_at": now - 60, "ok": False, "detail": "x", "last_good": mid}
        got = ce._valid_liveness_record(rec, now=now)
        assert got["last_good"]["checked_at"] == mid["checked_at"] and "last_good" not in got["last_good"]

    def test_refresh_never_runs_unlocked_under_fail_open(self, monkeypatch, tmp_path):
        # MARO_FILELOCK_FAIL_OPEN=1 makes the DEFAULT lock contract proceed
        # unlocked on contention; the recorder requires the lock instead —
        # a busy lock means no probe, no write, the on-disk record returned.
        import threading
        from file_lock import locked_write
        self._ws(monkeypatch, tmp_path)
        monkeypatch.setenv("MARO_FILELOCK_FAIL_OPEN", "1")
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "0.2")
        path = ce._auth_liveness_path(); path.parent.mkdir(parents=True, exist_ok=True)
        calls = {"n": 0}
        monkeypatch.setattr(ce, "_credentials_expiry_probe",
                            lambda: calls.__setitem__("n", calls["n"] + 1) or
                            (True, {"mtime": 1, "has_refresh": True, "refresh_expires_at": 2_000_000_000, "access_expires_at": 0}))
        held = threading.Event(); release = threading.Event()
        def holder():
            with locked_write(path):
                held.set(); release.wait(5)
        t = threading.Thread(target=holder); t.start(); held.wait(2)
        try:
            assert ce.refresh_auth_liveness(force=True) is None
            assert calls["n"] == 0 and not path.exists()
        finally:
            release.set(); t.join(5)
        assert ce.refresh_auth_liveness(force=True)["ok"] is True and calls["n"] == 1
