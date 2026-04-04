"""Tests for doctor.py — environment health check."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from doctor import run_doctor, _check


# ---------------------------------------------------------------------------
# _check helper
# ---------------------------------------------------------------------------

class TestCheck:
    def test_ok_result(self, capsys):
        result = _check("my check", True, "all good")
        captured = capsys.readouterr()
        assert result["ok"] is True
        assert result["label"] == "my check"
        assert "✓" in captured.out

    def test_fail_result(self, capsys):
        result = _check("my check", False, "broken")
        captured = capsys.readouterr()
        assert result["ok"] is False
        assert "✗" in captured.out

    def test_detail_included(self, capsys):
        _check("x", True, "some detail")
        captured = capsys.readouterr()
        assert "some detail" in captured.out

    def test_no_detail(self, capsys):
        _check("x", True)
        captured = capsys.readouterr()
        assert "x" in captured.out


# ---------------------------------------------------------------------------
# run_doctor — integration (mock heavy dependencies)
# ---------------------------------------------------------------------------

class TestRunDoctor:
    """Test that run_doctor runs without error and returns a bool."""

    def test_returns_bool(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        # run_doctor may fail checks (e.g. no LLM key) but always returns a bool
        result = run_doctor()
        assert isinstance(result, bool)

    def test_phase41_tools_checked(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        # Patch heavy LLM call
        with patch("doctor.sys") as mock_sys:
            mock_sys.version_info = (3, 14, 0, "final", 0)
            mock_sys.path = sys.path
            try:
                run_doctor()
            except Exception:
                pass
        captured = capsys.readouterr()
        # Should mention tool registry and curated skills in output
        assert "Tool registry" in captured.out or "tool" in captured.out.lower()

    def test_curated_skills_check_in_output(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        try:
            run_doctor()
        except Exception:
            pass
        captured = capsys.readouterr()
        assert "skills" in captured.out.lower()

    def test_step_event_bus_check_in_output(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        try:
            run_doctor()
        except Exception:
            pass
        captured = capsys.readouterr()
        assert "event bus" in captured.out.lower() or "step" in captured.out.lower()

    def test_bughunter_check_in_output(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        try:
            run_doctor()
        except Exception:
            pass
        captured = capsys.readouterr()
        assert "bughunter" in captured.out.lower() or "bugh" in captured.out.lower()

    def test_summary_line_printed(self, capsys, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        try:
            run_doctor()
        except Exception:
            pass
        captured = capsys.readouterr()
        # Summary line should contain "/14 checks passed" or similar fraction
        assert "checks passed" in captured.out
