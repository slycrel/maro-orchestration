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
