"""Tests for the containerized-executor C1 surface — constants, config,
docker probes, operator instructions, and doctor's container rows.

No docker dependency: the subprocess boundary is mocked entirely, so this
runs identically in CI whether or not docker is installed (design:
docs/CONTAINER_EXECUTOR_DESIGN.md, C1 "no docker dependency in CI").
"""

from __future__ import annotations

import subprocess
import sys
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
    def test_default_image_encodes_cli_pin(self):
        # The tag encodes the CLI pin so it's auditable (design §3).
        assert ce.DEFAULT_IMAGE == f"maro-executor:{ce.CLAUDE_CLI_VERSION}"

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
        assert f"{ce.AUTH_VOLUME}:{ce.AUTH_MOUNT}" in cmd
        assert f"HOME={ce.CONTAINER_HOME}" in cmd
        assert "maro-executor:test" in cmd
        assert ce.DEFAULT_NETWORK in cmd
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

    def test_login_command_uses_auth_volume(self, monkeypatch):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)
        cmd = ce.login_command()
        assert f"{ce.AUTH_VOLUME}:{ce.AUTH_MOUNT}" in cmd
        assert "claude /login" in cmd

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

    def test_off_returns_none_without_probing_docker(self, monkeypatch):
        self._mode(monkeypatch, "off")
        probed = {"hit": False}
        def probe():
            probed["hit"] = True
            return (True, "x")
        monkeypatch.setattr(ce, "docker_probe", probe)
        assert ce.resolve_container_run(no_tools=False) is None
        assert probed["hit"] is False  # off short-circuits before any docker cost

    def test_no_tools_stays_host_even_when_on(self, monkeypatch):
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        assert ce.resolve_container_run(no_tools=True) is None

    def test_on_with_docker_returns_name(self, monkeypatch):
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "_current_loop_id", lambda: "wily-glen")
        name = ce.resolve_container_run(no_tools=False)
        assert name and name.startswith("maro-exec-wily-glen-")

    def test_on_without_docker_degrades_to_host_warning_once(self, monkeypatch, caplog):
        self._mode(monkeypatch, "on")
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "docker binary not found on PATH"))
        with caplog.at_level("WARNING"):
            assert ce.resolve_container_run(no_tools=False) is None
            assert ce.resolve_container_run(no_tools=False) is None  # second call
        warns = [r for r in caplog.records if "docker is unavailable" in r.message]
        assert len(warns) == 1  # once per process (docker state is cached)

    def test_require_without_docker_refuses(self, monkeypatch):
        self._mode(monkeypatch, "require")
        monkeypatch.setattr(ce, "docker_probe", lambda: (False, "docker binary not found on PATH"))
        with pytest.raises(ce.ContainerUnavailable):
            ce.resolve_container_run(no_tools=False)

    def test_require_with_docker_returns_name(self, monkeypatch):
        self._mode(monkeypatch, "require")
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "_current_loop_id", lambda: "run1")
        assert ce.resolve_container_run(no_tools=False).startswith("maro-exec-run1-")

    def test_docker_probed_once_and_cached(self, monkeypatch):
        self._mode(monkeypatch, "on")
        calls = {"n": 0}
        def probe():
            calls["n"] += 1
            return (True, "docker 24")
        monkeypatch.setattr(ce, "docker_probe", probe)
        monkeypatch.setattr(ce, "_current_loop_id", lambda: "r")
        ce.resolve_container_run(no_tools=False)
        ce.resolve_container_run(no_tools=False)
        assert calls["n"] == 1  # per-process cache, not per-call


class TestContainerName:
    def test_prefix_and_seq(self):
        assert ce.container_name("abc", 5) == "maro-exec-abc-5"

    def test_sanitizes_illegal_chars(self):
        n = ce.container_name("a/b c:d", 0)
        assert n.startswith("maro-exec-")
        for bad in ("/", " ", ":"):
            assert bad not in n


class TestBuildRunCommand:
    def _build(self, monkeypatch, **overrides):
        monkeypatch.setattr(ce, "get", lambda k, d=None: d)  # network default, image default
        kw = dict(name="maro-exec-x-0", workdir="/w",
                  mounts=[("/w", "rw"), ("/ref", "ro")],
                  worker_env={"MARO_WORKER_RUN": "1", "MARO_ALLOW_MAIN_PUSH": "1"},
                  owner_pid=4242)
        kw.update(overrides)
        return ce.build_run_command(["claude", "-p", "--verbose"], **kw)

    def test_docker_run_skeleton(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert cmd[:6] == ["docker", "run", "--rm", "-i", "--init", "--name"]
        assert cmd[6] == "maro-exec-x-0"

    def test_bind_mounts_same_path_both_sides(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert "/w:/w:rw" in cmd
        assert "/ref:/ref:ro" in cmd

    def test_auth_volume_and_home(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert f"{ce.AUTH_VOLUME}:{ce.AUTH_MOUNT}" in cmd
        assert f"HOME={ce.CONTAINER_HOME}" in cmd

    def test_owner_pid_label(self, monkeypatch):
        cmd = self._build(monkeypatch)
        assert "maro.owner_pid=4242" in cmd

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

    def test_image_then_inner_cmd_are_last(self, monkeypatch):
        cmd = self._build(monkeypatch)
        i = cmd.index(ce.DEFAULT_IMAGE)
        assert cmd[i:] == [ce.DEFAULT_IMAGE, "claude", "-p", "--verbose"]


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
    def test_kills_only_dead_owner(self, monkeypatch):
        ps_out = "maro-exec-a-0\t100\nmaro-exec-b-0\t200\n"
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout=ps_out))
        killed: list = []
        monkeypatch.setattr(ce, "kill_container", lambda n: killed.append(n))
        result = ce.sweep_stranded_containers(pid_alive=lambda p: p == 100)
        assert result == ["maro-exec-b-0"]  # live owner 100 untouched
        assert killed == ["maro-exec-b-0"]

    def test_missing_owner_label_is_reaped(self, monkeypatch):
        monkeypatch.setattr(ce.subprocess, "run", _fake_run(0, stdout="maro-exec-c-0\t\n"))
        killed: list = []
        monkeypatch.setattr(ce, "kill_container", lambda n: killed.append(n))
        result = ce.sweep_stranded_containers(pid_alive=lambda p: True)
        assert result == ["maro-exec-c-0"]  # unattributable → safer killed

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
        import llm
        monkeypatch.setattr(llm, "build_adapter", lambda *a, **k: _FakeAdapter())

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
