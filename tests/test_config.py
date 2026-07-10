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
    """Default workspace root is ~/.maro/workspace."""
    for var in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
        monkeypatch.delenv(var, raising=False)
    assert workspace_root() == Path.home() / ".maro" / "workspace"


def test_workspace_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    assert workspace_root() == tmp_path


def test_memory_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    p = memory_dir()
    assert p == tmp_path / "memory"
    assert p.is_dir()


def test_output_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    p = output_dir()
    assert p == tmp_path / "output"
    assert p.is_dir()


def test_projects_dir_creates(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
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
        config._config_cache_key = None

    def test_workspace_overrides_user(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None
        config._config_cache_key = None

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
        config._config_cache_key = None

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
        config._config_cache_key = None

        user_cfg = tmp_path / "user.yml"
        ws_cfg = tmp_path / "ws.yml"
        user_cfg.write_text(yaml.dump({"val": 1}))
        ws_cfg.write_text("")

        monkeypatch.setattr(config, "_user_config_path", lambda: user_cfg)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: ws_cfg)

        cfg1 = load_config(reload=True)
        cfg2 = load_config()  # cached, file unchanged — should NOT re-read
        assert cfg2["val"] == 1
        assert cfg2 is cfg1  # same object — proves it came from cache, not a re-read

    def test_cache_auto_invalidates_on_file_mtime_change(self, tmp_path, monkeypatch):
        """A long-running process (heartbeat/daemon) must see an operator's
        config edit without needing a restart — this is what used to require
        reload=True everywhere; see BACKLOG.md 1.0 install trial residuals."""
        import os
        import config
        config._config_cache = None
        config._config_cache_key = None

        user_cfg = tmp_path / "user.yml"
        ws_cfg = tmp_path / "ws.yml"
        user_cfg.write_text(yaml.dump({"val": 1}))
        ws_cfg.write_text("")

        monkeypatch.setattr(config, "_user_config_path", lambda: user_cfg)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: ws_cfg)

        load_config(reload=True)
        user_cfg.write_text(yaml.dump({"val": 2}))
        # Force a distinct mtime — some filesystems have 1s+ mtime
        # granularity, and this test must not depend on wall-clock timing.
        new_mtime = user_cfg.stat().st_mtime + 5
        os.utime(user_cfg, (new_mtime, new_mtime))

        cfg = load_config()  # no reload= — must still see the edit
        assert cfg["val"] == 2

    def test_reload_clears_cache(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None
        config._config_cache_key = None

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

    def test_workspace_path_change_invalidates_cache(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None
        config._config_cache_key = None

        user_cfg = tmp_path / "user.yml"
        ws_one = tmp_path / "ws-one.yml"
        ws_two = tmp_path / "ws-two.yml"
        user_cfg.write_text(yaml.dump({"navigator": {"act_dispatch": False}}))
        ws_one.write_text(yaml.dump({"navigator": {"act_dispatch": True}}))
        ws_two.write_text(yaml.dump({"navigator": {"act_dispatch": False}}))

        monkeypatch.setattr(config, "_user_config_path", lambda: user_cfg)
        current = {"path": ws_one}
        monkeypatch.setattr(config, "_workspace_config_path", lambda: current["path"])

        first = load_config(reload=True)
        current["path"] = ws_two
        second = load_config()

        assert first["navigator"]["act_dispatch"] is True
        assert second["navigator"]["act_dispatch"] is False


# ---------------------------------------------------------------------------
# get() dot-path access
# ---------------------------------------------------------------------------

class TestGet:

    def setup_method(self):
        import config
        config._config_cache = None
        config._config_cache_key = None

    def test_simple_key(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None
        config._config_cache_key = None

        p = tmp_path / "cfg.yml"
        p.write_text(yaml.dump({"yolo": True}))
        monkeypatch.setattr(config, "_user_config_path", lambda: p)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: tmp_path / "nope.yml")

        assert get("yolo") is True

    def test_nested_key(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None
        config._config_cache_key = None

        p = tmp_path / "cfg.yml"
        p.write_text(yaml.dump({"model": {"advisor_tier": "power"}}))
        monkeypatch.setattr(config, "_user_config_path", lambda: p)
        monkeypatch.setattr(config, "_workspace_config_path", lambda: tmp_path / "nope.yml")

        assert get("model.advisor_tier") == "power"

    def test_missing_key_returns_default(self, tmp_path, monkeypatch):
        import config
        config._config_cache = None
        config._config_cache_key = None

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


class TestWorkspacePinLayout:
    """BACKLOG #-1 (2026-07-03): a pinned MARO_WORKSPACE means the workspace
    IS that path — orch_items' memory/projects/output resolvers must agree
    with config's (no prototype-layout split-brain). Legacy vars keep the
    prototype layout."""

    def _clear_ws_vars(self, monkeypatch):
        for var in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT",
                    "MARO_ORCH_ROOT", "MARO_MEMORY_DIR"):
            monkeypatch.delenv(var, raising=False)

    def test_maro_workspace_pin_uses_workspace_layout(self, monkeypatch, tmp_path):
        self._clear_ws_vars(monkeypatch)
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import orch_items
        assert orch_items.memory_dir() == tmp_path / "memory"
        assert orch_items.projects_root() == tmp_path / "projects"
        assert orch_items.output_root() == tmp_path / "output"

    def test_maro_workspace_pin_agrees_with_config(self, monkeypatch, tmp_path):
        self._clear_ws_vars(monkeypatch)
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import config as config_mod
        import orch_items
        assert orch_items.memory_dir() == config_mod.memory_dir()
        assert orch_items.projects_root() == config_mod.projects_dir()
        assert orch_items.output_root() == config_mod.output_dir()

    def test_legacy_openclaw_pin_keeps_prototype_layout(self, monkeypatch, tmp_path):
        self._clear_ws_vars(monkeypatch)
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import orch_items
        proto = tmp_path / "prototypes" / "maro-orchestration"
        assert orch_items.memory_dir() == proto / "memory"
        assert orch_items.projects_root() == proto / "projects"
        assert orch_items.output_root() == proto / "output"

    def test_maro_workspace_wins_over_legacy_var(self, monkeypatch, tmp_path):
        self._clear_ws_vars(monkeypatch)
        maro_ws = tmp_path / "maro-ws"
        legacy_ws = tmp_path / "legacy-ws"
        monkeypatch.setenv("MARO_WORKSPACE", str(maro_ws))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(legacy_ws))
        import orch_items
        assert orch_items.memory_dir() == maro_ws / "memory"
        assert orch_items.projects_root() == maro_ws / "projects"
        assert orch_items.output_root() == maro_ws / "output"

    def test_background_log_lands_in_canonical_memory(self, monkeypatch, tmp_path):
        self._clear_ws_vars(monkeypatch)
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import background
        assert background._bg_log_path() == tmp_path / "memory" / "background-tasks.jsonl"


class TestResolveArtifactPath:
    """resolve_artifact_path() is the inverse of relative_display_path() —
    consumers must not re-anchor stored artifact paths on orch_root directly."""

    def test_workspace_form(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import orch_items
        resolved = orch_items.resolve_artifact_path("~workspace/output/runs/run-x")
        assert resolved == tmp_path / "output" / "runs" / "run-x"

    def test_orch_root_relative_form(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import orch_items
        resolved = orch_items.resolve_artifact_path("output/runs/run-x")
        assert resolved == orch_items.orch_root() / "output" / "runs" / "run-x"

    def test_absolute_form(self, monkeypatch, tmp_path):
        import orch_items
        target = tmp_path / "somewhere" / "run-y"
        assert orch_items.resolve_artifact_path(str(target)) == target

    def test_round_trips_display_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import orch_items
        real = orch_items.runs_root() / "run-z"
        real.mkdir(parents=True, exist_ok=True)
        display = orch_items.relative_display_path(real)
        assert orch_items.resolve_artifact_path(display) == real


# ---------------------------------------------------------------------------
# user/ docs lane — user_file() resolution (workspace overlay > repo template)
# ---------------------------------------------------------------------------

class TestUserFileResolution:
    """config.user_file(): <workspace>/user/<name> wins over <repo>/user/<name>.

    conftest's autouse fixture points MARO_WORKSPACE at tmp_path, so the
    workspace overlay for these tests is tmp_path/user/.
    """

    def test_workspace_overlay_wins(self, tmp_path, monkeypatch):
        import config as config_mod
        repo_user = tmp_path / "repo" / "user"
        repo_user.mkdir(parents=True)
        (repo_user / "GOALS.md").write_text("shipped template")
        monkeypatch.setattr(config_mod, "repo_user_dir", lambda: repo_user)

        overlay = tmp_path / "user"
        overlay.mkdir()
        (overlay / "GOALS.md").write_text("operator goals")

        resolved = config_mod.user_file("GOALS.md")
        assert resolved == overlay / "GOALS.md"
        assert resolved.read_text() == "operator goals"

    def test_falls_back_to_repo_template(self, tmp_path, monkeypatch):
        import config as config_mod
        repo_user = tmp_path / "repo" / "user"
        repo_user.mkdir(parents=True)
        (repo_user / "CONTEXT.md").write_text("shipped template")
        monkeypatch.setattr(config_mod, "repo_user_dir", lambda: repo_user)

        resolved = config_mod.user_file("CONTEXT.md")
        assert resolved == repo_user / "CONTEXT.md"

    def test_none_when_neither_exists(self, tmp_path, monkeypatch):
        import config as config_mod
        monkeypatch.setattr(
            config_mod, "repo_user_dir", lambda: tmp_path / "no-such-dir"
        )
        assert config_mod.user_file("SIGNALS.md") is None

    def test_load_user_config_reads_workspace_overlay(self, tmp_path, monkeypatch):
        """SF-5 residual closed 2026-07-10: handle._load_user_config resolves
        via user_file(), so an operator's workspace CONFIG.md is honored
        (previously the repo copy was read directly and workspace edits were
        silently ignored)."""
        import config as config_mod
        from handle import _load_user_config
        monkeypatch.setattr(config_mod, "repo_user_dir", lambda: tmp_path / "empty")
        overlay = tmp_path / "user"
        overlay.mkdir()
        (overlay / "CONFIG.md").write_text("yolo: true  # comment\nmax_steps: 5\n")
        cfg = _load_user_config()
        assert cfg.get("yolo") == "true"
        assert cfg.get("max_steps") == "5"

    def test_repo_templates_ship_no_personal_data(self):
        """The shipped user/ templates must stay neutral (SF-5/docs-02):
        no operator identity or personal details in the repo copies."""
        import config as config_mod
        for name in ("GOALS.md", "CONTEXT.md", "SIGNALS.md", "CONFIG.md"):
            path = config_mod.repo_user_dir() / name
            assert path.exists(), f"shipped template missing: {name}"
            text = path.read_text(encoding="utf-8").lower()
            for marker in ("jeremy", "slycrel", "retatrutide", "tirzepatide",
                           "bpc-157", "epitalon", "edgar_allen_bot"):
                assert marker not in text, f"personal data marker '{marker}' in user/{name}"
