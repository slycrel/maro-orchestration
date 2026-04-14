"""Tests for doctor.py — environment health check."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from doctor import run_doctor, _check, cleanup_workspace_skills, _skill_hash_is_stale
from skill_types import Skill, compute_skill_hash, skill_to_dict


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


# ---------------------------------------------------------------------------
# cleanup_workspace_skills — stale hash detection and dedup
# ---------------------------------------------------------------------------

def _make_skill(skill_id: str, name: str, correct_hash: bool = True) -> dict:
    """Build a minimal valid skill dict, optionally with a wrong stored hash."""
    skill = Skill(
        id=skill_id,
        name=name,
        description="test description",
        trigger_patterns=[],
        steps_template=["step 1"],
        source_loop_ids=[],
        created_at="2026-01-01T00:00:00Z",
        use_count=0,
        success_rate=1.0,
        content_hash="",
        tier="provisional",
        utility_score=1.0,
        failure_notes=[],
        consecutive_failures=0,
        consecutive_successes=0,
        circuit_state="closed",
        optimization_objective="",
        island="",
        variant_of=None,
        variant_wins=0,
        variant_losses=0,
    )
    d = skill_to_dict(skill)
    d["content_hash"] = compute_skill_hash(skill) if correct_hash else "aaaaaaaaaaaaaaaa"
    return d


class TestSkillHashStale:
    def test_correct_hash_not_stale(self):
        d = _make_skill("sk001", "real skill", correct_hash=True)
        assert not _skill_hash_is_stale(d)

    def test_wrong_hash_is_stale(self):
        d = _make_skill("sk002", "test fixture", correct_hash=False)
        assert _skill_hash_is_stale(d)

    def test_no_hash_not_stale(self):
        d = _make_skill("sk003", "no hash skill", correct_hash=True)
        d["content_hash"] = ""
        assert not _skill_hash_is_stale(d)


class TestCleanupWorkspaceSkills:
    def _write_skills(self, path: Path, skills: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(s) for s in skills) + "\n", encoding="utf-8")

    def test_stale_hash_skills_are_removed(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        good = _make_skill("skgood", "real skill", correct_hash=True)
        stale = _make_skill("skbad", "test fixture", correct_hash=False)
        self._write_skills(skills_file, [good, stale])

        cleanup_workspace_skills(skills_path=skills_file)

        remaining = [json.loads(l) for l in skills_file.read_text().splitlines() if l.strip()]
        assert len(remaining) == 1
        assert remaining[0]["id"] == "skgood"
        captured = capsys.readouterr()
        assert "stale" in captured.out.lower()
        assert "skbad" in captured.out

    def test_duplicates_deduped_after_stale_removal(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        # Two copies of the same good skill (same content_hash)
        dup1 = _make_skill("sk-a", "skill alpha", correct_hash=True)
        dup2 = dict(dup1)
        dup2["id"] = "sk-b"
        dup2["use_count"] = 5  # higher score → should be kept
        stale = _make_skill("sk-c", "test fixture", correct_hash=False)
        self._write_skills(skills_file, [dup1, dup2, stale])

        cleanup_workspace_skills(skills_path=skills_file)

        remaining = [json.loads(l) for l in skills_file.read_text().splitlines() if l.strip()]
        assert len(remaining) == 1
        assert remaining[0]["id"] == "sk-b"  # higher score kept

    def test_no_stale_no_dups_reports_clean(self, tmp_path, capsys):
        skills_file = tmp_path / "skills.jsonl"
        good = _make_skill("skgood", "real skill", correct_hash=True)
        self._write_skills(skills_file, [good])

        cleanup_workspace_skills(skills_path=skills_file)

        captured = capsys.readouterr()
        assert "no stale" in captured.out.lower()
        assert "no duplicates" in captured.out.lower()
