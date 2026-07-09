"""Tests for bootstrap.write_starter_config — the first-run config template.

1.0 posture (2026-07-09): `maro-bootstrap install` seeds a commented
~/.maro/config.yml so a fresh install discovers the load-bearing knobs
(backend order, spend caps, notify channel) without reading source.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from bootstrap import render_starter_config, write_starter_config


def _user_cfg_path() -> Path:
    from config import config_paths
    return Path(config_paths()["user"])


def test_writes_template_when_absent(tmp_path, monkeypatch):
    written = write_starter_config()
    assert written is not None
    assert written == _user_cfg_path()
    assert written.read_text() == render_starter_config()


def test_never_overwrites_existing(tmp_path, monkeypatch):
    cfg = _user_cfg_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("budget:\n  daily_usd: 3.0\n")
    assert write_starter_config() is None
    assert "daily_usd: 3.0" in cfg.read_text()


def test_template_is_valid_yaml_and_all_commented(tmp_path, monkeypatch):
    import yaml
    # Fully commented template must parse to nothing — defaults apply.
    assert yaml.safe_load(render_starter_config()) is None


def test_template_documents_the_load_bearing_keys():
    rendered = render_starter_config()
    for key in ("backend_order", "per_run_usd", "daily_usd", "notify",
                "TELEGRAM_BOT_TOKEN", "DEFAULTS.md"):
        assert key in rendered, f"starter template lost mention of {key}"


def test_template_values_match_code_constants():
    """The seeded template must state the REAL defaults — a fresh user reads
    the template, not loop_init.py. Interpolated at render time; this pins
    the wiring so a refactor can't silently decouple them."""
    from llm import DEFAULT_BACKEND_ORDER
    from loop_init import DEFAULT_PER_RUN_USD, DEFAULT_DAILY_USD
    rendered = render_starter_config()
    assert f"per_run_usd: {DEFAULT_PER_RUN_USD:.1f}" in rendered
    assert f"daily_usd: {DEFAULT_DAILY_USD:.1f}" in rendered
    assert ", ".join(DEFAULT_BACKEND_ORDER) in rendered
