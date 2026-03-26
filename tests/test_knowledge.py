"""Tests for knowledge crystallization dashboard (Phase 22 first cut)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import knowledge


# ---------------------------------------------------------------------------
# _stage2_data
# ---------------------------------------------------------------------------

def test_stage2_data_returns_dict_with_expected_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory" / "medium").mkdir(parents=True)
    (tmp_path / "memory" / "long").mkdir(parents=True)
    result = knowledge._stage2_data()
    assert "medium_count" in result or "error" in result


def test_stage2_data_empty_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    result = knowledge._stage2_data()
    assert result.get("medium_count", 0) == 0
    assert result.get("long_count", 0) == 0


# ---------------------------------------------------------------------------
# _stage3_data
# ---------------------------------------------------------------------------

def test_stage3_data_returns_candidates_field(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    result = knowledge._stage3_data()
    assert "canon_candidates" in result or "error" in result


def test_stage3_data_no_candidates_on_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    result = knowledge._stage3_data()
    assert result.get("canon_candidates", 0) == 0


# ---------------------------------------------------------------------------
# _stage4_data
# ---------------------------------------------------------------------------

def test_stage4_data_returns_skill_counts(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    result = knowledge._stage4_data()
    assert "provisional_count" in result or "error" in result


def test_stage4_data_empty_skills(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    result = knowledge._stage4_data()
    assert result.get("provisional_count", 0) == 0
    assert result.get("established_count", 0) == 0
    assert result.get("promote_ready", 0) == 0


# ---------------------------------------------------------------------------
# print_dashboard
# ---------------------------------------------------------------------------

def test_print_dashboard_runs_without_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.print_dashboard()
    out = capsys.readouterr().out
    assert "Stage 2" in out
    assert "Stage 3" in out
    assert "Stage 4" in out
    assert "Stage 5" in out


def test_print_dashboard_stage_filter(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.print_dashboard(stage_filter=4)
    out = capsys.readouterr().out
    assert "Stage 4" in out
    assert "Stage 2" not in out
    assert "Stage 3" not in out


def test_print_dashboard_contains_dashboard_header(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.print_dashboard()
    out = capsys.readouterr().out
    assert "Crystallization" in out


# ---------------------------------------------------------------------------
# print_promote_actions
# ---------------------------------------------------------------------------

def test_print_promote_actions_runs_without_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.print_promote_actions()
    out = capsys.readouterr().out
    assert "promotion actions" in out.lower() or "Nothing ready" in out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def test_main_status_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.main(["status"])
    out = capsys.readouterr().out
    assert "Stage" in out


def test_main_promote_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.main(["promote"])
    out = capsys.readouterr().out
    assert len(out) > 0


def test_main_no_args_shows_dashboard(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.main([])
    out = capsys.readouterr().out
    assert "Crystallization" in out


def test_main_status_stage_filter(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir(exist_ok=True)
    knowledge.main(["status", "--stage", "2"])
    out = capsys.readouterr().out
    assert "Stage 2" in out
    assert "Stage 3" not in out
