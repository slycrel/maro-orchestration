"""Tests for Phase 21: Production Readiness — Bootstrap + Decoupling + macOS.

Covers: config.py path resolution, bootstrap workspace creation,
service file generation (systemd + launchd), smoke test wiring.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import config
import bootstrap


# ---------------------------------------------------------------------------
# config.py — workspace_root resolution
# ---------------------------------------------------------------------------

def test_workspace_root_poe_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    # Reload to pick up env — workspace_root() reads os.environ at call time
    result = config.workspace_root()
    assert result == tmp_path.resolve()


def test_workspace_root_openclaw_workspace(monkeypatch, tmp_path):
    monkeypatch.delenv("POE_WORKSPACE", raising=False)
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    result = config.workspace_root()
    assert result == tmp_path.resolve()


def test_workspace_root_workspace_root(monkeypatch, tmp_path):
    monkeypatch.delenv("POE_WORKSPACE", raising=False)
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    result = config.workspace_root()
    assert result == tmp_path.resolve()


def test_workspace_root_priority_poe_over_openclaw(monkeypatch, tmp_path):
    poe_dir = tmp_path / "poe"
    openclaw_dir = tmp_path / "openclaw"
    poe_dir.mkdir()
    openclaw_dir.mkdir()
    monkeypatch.setenv("POE_WORKSPACE", str(poe_dir))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(openclaw_dir))
    result = config.workspace_root()
    assert result == poe_dir.resolve()


def test_workspace_root_default_no_env(monkeypatch):
    monkeypatch.delenv("POE_WORKSPACE", raising=False)
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    result = config.workspace_root()
    assert result == Path.home() / ".poe" / "workspace"


# ---------------------------------------------------------------------------
# config.py — credentials_env_file
# ---------------------------------------------------------------------------

def test_credentials_env_file_poe_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / "my.env"
    env_file.write_text("KEY=val\n")
    monkeypatch.setenv("POE_ENV_FILE", str(env_file))
    result = config.credentials_env_file()
    assert result == env_file


def test_credentials_env_file_workspace_local(monkeypatch, tmp_path):
    monkeypatch.delenv("POE_ENV_FILE", raising=False)
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    local = tmp_path / "secrets" / ".env"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("KEY=val\n")
    result = config.credentials_env_file()
    assert result == local


def test_load_credentials_env_parses_file(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("POE_ENV_FILE", raising=False)
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / ".env").write_text("API_KEY=abc123\nOTHER=xyz\n# comment=ignored\n")
    result = config.load_credentials_env()
    assert result["API_KEY"] == "abc123"
    assert result["OTHER"] == "xyz"
    assert "comment" not in result


def test_load_credentials_env_empty_when_no_file(monkeypatch, tmp_path):
    """When no local secrets file exists and legacy path is patched away, returns {}."""
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("POE_ENV_FILE", raising=False)
    # Patch credentials_env_file to return a non-existent path so no fallback
    with patch.object(config, "credentials_env_file", return_value=tmp_path / "nonexistent.env"):
        result = config.load_credentials_env()
    assert result == {}


# ---------------------------------------------------------------------------
# config.py — openclaw_cfg_path
# ---------------------------------------------------------------------------

def test_openclaw_cfg_path_env_override(monkeypatch, tmp_path):
    cfg = tmp_path / "custom.json"
    monkeypatch.setenv("OPENCLAW_CFG", str(cfg))
    result = config.openclaw_cfg_path()
    assert result == cfg


def test_openclaw_cfg_path_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OPENCLAW_CFG", None)
        result = config.openclaw_cfg_path()
        assert result == Path.home() / ".openclaw" / "openclaw.json"


def test_load_openclaw_cfg_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_CFG", str(tmp_path / "nonexistent.json"))
    result = config.load_openclaw_cfg()
    assert result == {}


def test_load_openclaw_cfg_valid_json(monkeypatch, tmp_path):
    cfg = tmp_path / "openclaw.json"
    cfg.write_text(json.dumps({"gateway_url": "ws://localhost:1234"}))
    monkeypatch.setenv("OPENCLAW_CFG", str(cfg))
    result = config.load_openclaw_cfg()
    assert result["gateway_url"] == "ws://localhost:1234"


# ---------------------------------------------------------------------------
# bootstrap.py — create_workspace_dirs
# ---------------------------------------------------------------------------

def test_create_workspace_dirs_creates_all(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    result = bootstrap.create_workspace_dirs(ws)
    assert result == ws
    for sub in bootstrap._WORKSPACE_SUBDIRS:
        assert (ws / sub).is_dir(), f"Missing: {sub}"


def test_create_workspace_dirs_idempotent(monkeypatch, tmp_path):
    ws = tmp_path / "ws"
    bootstrap.create_workspace_dirs(ws)
    bootstrap.create_workspace_dirs(ws)  # should not raise
    assert ws.is_dir()


def test_create_workspace_dirs_uses_config_default(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path / "poe"))
    result = bootstrap.create_workspace_dirs()
    assert result == (tmp_path / "poe").resolve()
    assert result.is_dir()


# ---------------------------------------------------------------------------
# bootstrap.py — write_service_files
# ---------------------------------------------------------------------------

def test_write_service_files_linux_creates_systemd(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap, "deploy_dir", lambda: tmp_path / "deploy")
    written = bootstrap.write_service_files(tmp_path / "ws")
    assert len(written) == len(bootstrap._SERVICES)
    for path in written:
        assert path.suffix == ".service"
        content = path.read_text()
        assert "ExecStart=" in content
        assert "POE_WORKSPACE=" in content


def test_write_service_files_macos_creates_launchd(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bootstrap, "deploy_dir", lambda: tmp_path / "deploy")
    written = bootstrap.write_service_files(tmp_path / "ws")
    assert len(written) == len(bootstrap._SERVICES)
    for path in written:
        assert path.suffix == ".plist"
        content = path.read_text()
        assert "<plist" in content
        assert "POE_WORKSPACE" in content


def test_systemd_service_content(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap, "deploy_dir", lambda: tmp_path / "deploy")
    ws = tmp_path / "ws"
    written = bootstrap.write_service_files(ws)
    heartbeat = next(p for p in written if "heartbeat" in str(p))
    content = heartbeat.read_text()
    assert "[Unit]" in content
    assert "[Service]" in content
    assert "[Install]" in content
    assert str(ws) in content


def test_launchd_plist_content(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(bootstrap, "deploy_dir", lambda: tmp_path / "deploy")
    ws = tmp_path / "ws"
    written = bootstrap.write_service_files(ws)
    heartbeat = next(p for p in written if "heartbeat" in str(p))
    content = heartbeat.read_text()
    assert "<?xml" in content
    assert "<key>Label</key>" in content
    assert "<key>KeepAlive</key>" in content
    assert str(ws) in content


# ---------------------------------------------------------------------------
# bootstrap.py — status
# ---------------------------------------------------------------------------

def test_show_status_runs_without_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    bootstrap.create_workspace_dirs(tmp_path)
    bootstrap.show_status()
    out = capsys.readouterr().out
    assert str(tmp_path) in out
    assert "memory" in out


# ---------------------------------------------------------------------------
# bootstrap.py — smoke test
# ---------------------------------------------------------------------------

def test_run_smoke_test_missing_handle(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bootstrap, "_SRC_DIR", tmp_path)
    result = bootstrap.run_smoke_test()
    assert result is False


# ---------------------------------------------------------------------------
# service file workspace injection
# ---------------------------------------------------------------------------

def test_service_files_inject_correct_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(bootstrap, "deploy_dir", lambda: tmp_path / "deploy")
    ws = tmp_path / "my-custom-workspace"
    written = bootstrap.write_service_files(ws)
    for path in written:
        assert str(ws) in path.read_text()
