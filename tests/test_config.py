"""Tests for config.py — YAML config loading, path resolution, merge behavior."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from config import (
    _load_yaml,
    load_config,
    get,
    config_paths,
    workspace_root,
    memory_dir,
    output_dir,
    projects_dir,
    secrets_dir,
)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def test_workspace_root_default(monkeypatch):
    """Default workspace root is ~/.poe/workspace."""
    for var in ("POE_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    assert workspace_root() == Path.home() / ".poe" / "workspace"


def test_workspace_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    assert workspace_root() == tmp_path


def test_memory_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    p = memory_dir()
    assert p == tmp_path / "memory"
    assert p.is_dir()


def test_output_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    p = output_dir()
    assert p == tmp_path / "output"
    assert p.is_dir()


def test_projects_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    p = projects_dir()
    assert p == tmp_path / "projects"
    assert p.is_dir()


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def test_load_yaml_missing_file(tmp_path):
    assert _load_yaml(tmp_path / "nonexistent.yml") == {}


def test_load_yaml_valid(tmp_path):
    p = tmp_path / "test.yml"
    p.write_text(yaml.dump({"key": "value", "nested": {"a": 1}}))
    result = _load_yaml(p)
    assert result == {"key": "value", "nested": {"a": 1}}


def test_load_yaml_invalid(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("{{invalid yaml content")
    assert _load_yaml(p) == {}


def test_load_yaml_non_dict(tmp_path):
    p = tmp_path / "list.yml"
    p.write_text("- a\n- b\n- c\n")
    assert _load_yaml(p) == {}


# ---------------------------------------------------------------------------
# Config merging
# ---------------------------------------------------------------------------

class TestConfigMerge:

    def setup_method(self):
        import config
        config._config_cache = None  # reset cache between tests

    def test_workspace_overrides_user(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None

        user_cfg = tmp_path / "user.yml"
        ws_cfg = tmp_path / "ws.yml"
        user_cfg.write_text(yaml.dump({"yolo": False, "verbose": True}))
        ws_cfg.write_text(yaml.dump({"yolo": True}))

        monkeypatch.setattr(config, "_user_config_path", lambda: user_cfg)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: ws_cfg)

        cfg = load_config(reload=True)
        assert cfg["yolo"] is True      # workspace overrides
        assert cfg["verbose"] is True    # user value preserved

    def test_nested_merge(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None

        user_cfg = tmp_path / "user.yml"
        ws_cfg = tmp_path / "ws.yml"
        user_cfg.write_text(yaml.dump({"model": {"default_tier": "cheap", "advisor_tier": "power"}}))
        ws_cfg.write_text(yaml.dump({"model": {"default_tier": "mid"}}))

        monkeypatch.setattr(config, "_user_config_path", lambda: user_cfg)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: ws_cfg)

        cfg = load_config(reload=True)
        assert cfg["model"]["default_tier"] == "mid"     # workspace overrides
        assert cfg["model"]["advisor_tier"] == "power"    # user value preserved

    def test_cache_is_used(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None

        user_cfg = tmp_path / "user.yml"
        ws_cfg = tmp_path / "ws.yml"
        user_cfg.write_text(yaml.dump({"val": 1}))
        ws_cfg.write_text("")

        monkeypatch.setattr(config, "_user_config_path", lambda: user_cfg)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: ws_cfg)

        cfg1 = load_config(reload=True)
        user_cfg.write_text(yaml.dump({"val": 2}))
        cfg2 = load_config()  # cached — should NOT re-read
        assert cfg2["val"] == 1

    def test_reload_clears_cache(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None

        user_cfg = tmp_path / "user.yml"
        ws_cfg = tmp_path / "ws.yml"
        user_cfg.write_text(yaml.dump({"val": 1}))
        ws_cfg.write_text("")

        monkeypatch.setattr(config, "_user_config_path", lambda: user_cfg)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: ws_cfg)

        load_config(reload=True)
        user_cfg.write_text(yaml.dump({"val": 2}))
        cfg = load_config(reload=True)
        assert cfg["val"] == 2


# ---------------------------------------------------------------------------
# get() dot-path access
# ---------------------------------------------------------------------------

class TestGet:

    def setup_method(self):
        import config
        config._config_cache = None

    def test_simple_key(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None

        p = tmp_path / "cfg.yml"
        p.write_text(yaml.dump({"yolo": True}))
        monkeypatch.setattr(config, "_user_config_path", lambda: p)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: tmp_path / "nope.yml")

        assert get("yolo") is True

    def test_nested_key(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None

        p = tmp_path / "cfg.yml"
        p.write_text(yaml.dump({"model": {"advisor_tier": "power"}}))
        monkeypatch.setattr(config, "_user_config_path", lambda: p)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: tmp_path / "nope.yml")

        assert get("model.advisor_tier") == "power"

    def test_missing_key_returns_default(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None

        p = tmp_path / "cfg.yml"
        p.write_text(yaml.dump({"a": 1}))
        monkeypatch.setattr(config, "_user_config_path", lambda: p)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: tmp_path / "nope.yml")

        assert get("nonexistent.deep.key", "FALLBACK") == "FALLBACK"


# ---------------------------------------------------------------------------
# config_paths diagnostics
# ---------------------------------------------------------------------------

def test_config_paths_returns_dict():
    result = config_paths()
    assert "user" in result
    assert "workspace" in result
    assert "user_exists" in result
    assert "workspace_exists" in result
