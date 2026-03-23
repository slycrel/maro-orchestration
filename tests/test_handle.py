"""Tests for Phase 2: handle.py (unified entry point, NOW/AGENDA routing)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from handle import handle, HandleResult


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# HandleResult.format()
# ---------------------------------------------------------------------------

def test_handle_result_format_text():
    r = HandleResult(
        handle_id="abc123",
        lane="now",
        lane_confidence=0.9,
        classification_reason="simple",
        message="hi",
        status="done",
        result="hello",
    )
    text = r.format("text")
    assert "handle_id=abc123" in text
    assert "lane=now" in text
    assert "hello" in text


def test_handle_result_format_json():
    r = HandleResult(
        handle_id="abc123",
        lane="agenda",
        lane_confidence=0.75,
        classification_reason="research task",
        message="research X",
        status="done",
        result="findings",
        project="my-project",
    )
    data = json.loads(r.format("json"))
    assert data["handle_id"] == "abc123"
    assert data["lane"] == "agenda"
    assert data["project"] == "my-project"
    assert data["result"] == "findings"


# ---------------------------------------------------------------------------
# NOW lane (dry_run)
# ---------------------------------------------------------------------------

def test_handle_now_lane_dry_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("what time is it?", dry_run=True)
    assert isinstance(result, HandleResult)
    assert result.lane == "now"
    assert result.status == "done"
    assert result.result != ""


def test_handle_now_forced(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("research polymarket strategies", dry_run=True, force_lane="now")
    assert result.lane == "now"
    assert result.lane_confidence == 1.0


def test_handle_now_writes_artifact(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("write a haiku", dry_run=True)
    # Artifact path should be set
    assert result.artifact_path is not None


# ---------------------------------------------------------------------------
# AGENDA lane (dry_run)
# ---------------------------------------------------------------------------

def test_handle_agenda_lane_dry_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("research winning polymarket strategies", dry_run=True)
    assert isinstance(result, HandleResult)
    assert result.lane == "agenda"
    assert result.status == "done"
    assert result.project is not None
    assert result.loop_result is not None


def test_handle_agenda_forced(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("what is 2+2?", dry_run=True, force_lane="agenda")
    assert result.lane == "agenda"
    assert result.status == "done"


def test_handle_agenda_creates_project(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("analyze competitor pricing strategies", dry_run=True, project="comp-pricing")
    assert orch.project_dir("comp-pricing").exists()


def test_handle_agenda_result_has_content(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("build a research report on X strategies", dry_run=True)
    assert len(result.result) > 0


# ---------------------------------------------------------------------------
# Auto-classification routing
# ---------------------------------------------------------------------------

def test_handle_routes_simple_to_now(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("write a haiku about the moon", dry_run=True)
    # Heuristic should route this to NOW
    assert result.lane == "now"


def test_handle_routes_research_to_agenda(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("research and analyze polymarket prediction patterns", dry_run=True)
    assert result.lane == "agenda"


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------

def test_handle_tracks_tokens(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = handle("what is 2+2?", dry_run=True, force_lane="now")
    assert result.tokens_in >= 0
    assert result.tokens_out >= 0


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_handle_now(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-handle", "what is 2 plus 2?", "--dry-run", "--lane", "now"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lane=now" in out


def test_cli_poe_handle_agenda(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-handle", "research polymarket strategies", "--dry-run", "--lane", "agenda"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "lane=agenda" in out


def test_cli_poe_handle_json(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-handle", "hello", "--dry-run", "--lane", "now", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "handle_id" in data
    assert data["lane"] == "now"
