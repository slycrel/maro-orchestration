"""Tests for bootstrap.run_smoke_test.

Not a --dry-run: proves a fresh install's configured LLM backend actually
works (real API call), which handle's own --dry-run mode (canned response,
no API call) can't verify. See BACKLOG.md "1.0 install trial residuals" —
the docstring/help text previously called this "dry-run", which it never
was; this file guards the corrected wording doesn't regress and that the
subprocess is never invoked with --dry-run.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import bootstrap


def test_smoke_test_invokes_handle_without_dry_run(monkeypatch):
    """The whole point is a live call — must never pass --dry-run."""
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_run)
    assert bootstrap.run_smoke_test() is True
    assert "--dry-run" not in captured["cmd"]


def test_smoke_test_reports_failure_on_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap.subprocess, "run",
        lambda cmd, **kwargs: MagicMock(returncode=1, stdout="", stderr="No LLM backend available"),
    )
    assert bootstrap.run_smoke_test() is False
    assert "No LLM backend available" in capsys.readouterr().err


def test_smoke_test_docstring_is_honest_about_live_call():
    doc = bootstrap.run_smoke_test.__doc__ or ""
    assert "dry-run" not in doc.lower() or "not a --dry-run" in doc.lower()
    assert "live" in doc.lower()
