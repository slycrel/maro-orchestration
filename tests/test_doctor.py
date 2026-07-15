"""Tests for doctor.py — environment health check."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from doctor import (
    run_doctor, _check, cleanup_workspace_skills, _skill_hash_is_stale,
    _scan_config_paths,
)
from skill_types import Skill, compute_skill_hash, skill_to_dict


class _DoctorResponse:
    content = "ok"


class _DoctorAdapter:
    def complete(self, *args, **kwargs):
        return _DoctorResponse()


@pytest.fixture(autouse=True)
def _stub_llm_health(monkeypatch):
    """Doctor output tests exercise doctor, not installed CLI authentication."""
    import bughunter
    import llm

    monkeypatch.setattr(
        llm,
        "detect_backends",
        lambda: [("subprocess", True, "test stub")],
    )
    monkeypatch.setattr(llm, "build_adapter", lambda *args, **kwargs: _DoctorAdapter())
    monkeypatch.setattr(
        bughunter,
        "run_bughunter",
        lambda: bughunter.BughunterReport(files_scanned=0),
    )


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

    def test_baseline_report_contains_required_surfaces(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        result = run_doctor()
        output = capsys.readouterr().out

        assert isinstance(result, bool)
        for required in (
            "Tool registry",
            "skills",
            "bughunter",
            "checks passed",
            "Escalation file surface",
            "Escalation push lane",
            "Config paths on this box",
            "Stale machine state",
            "Memory index sync",
        ):
            assert required.lower() in output.lower()
        assert str(tmp_path / "output" / "escalations.jsonl") in output

    def test_stale_machine_state_detected_but_not_failing(self, capsys, monkeypatch, tmp_path):
        # A restored workspace's traveled state must be surfaced, but never
        # as a hard FAIL — a live running box legitimately has these too.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = tmp_path / "memory"
        mem.mkdir(parents=True)
        (mem / "jobs.json").write_text("[]")
        (mem / "heartbeat-state.json").write_text("{}")
        (tmp_path / "telegram_offset.txt").write_text("123")
        (mem / "foo.lock").write_text("")
        run_doctor()
        captured = capsys.readouterr()
        assert "✓ Stale machine state" in captured.out
        assert "jobs.json" in captured.out
        assert "heartbeat-state.json" in captured.out
        assert "telegram_offset.txt" in captured.out
        assert "foo.lock" in captured.out

# ---------------------------------------------------------------------------
# _scan_config_paths — burned-in absolute paths from another machine
# ---------------------------------------------------------------------------

class TestScanConfigPaths:
    def test_existing_path_not_flagged(self, tmp_path):
        cfg = {"some": {"dir": str(tmp_path)}}
        assert _scan_config_paths(cfg) == []

    def test_missing_absolute_path_flagged(self):
        cfg = {"notify": {"binary": "/definitely/not/a/real/path/xyz123"}}
        missing = _scan_config_paths(cfg)
        assert len(missing) == 1
        assert "notify.binary=/definitely/not/a/real/path/xyz123" in missing[0]

    def test_missing_home_relative_path_flagged(self):
        cfg = {"scratch": "~/definitely-not-a-real-dir-xyz123"}
        missing = _scan_config_paths(cfg)
        assert len(missing) == 1
        assert missing[0].startswith("scratch=")

    def test_command_with_args_not_flagged_even_if_binary_missing(self):
        # Path-shaped heuristic deliberately skips anything with whitespace —
        # a shell command's argv, not a bare path.
        cfg = {"notify": {"command": "/definitely/not/real/bin --flag value"}}
        assert _scan_config_paths(cfg) == []

    def test_non_path_strings_not_flagged(self):
        cfg = {"model": {"default_tier": "cheap"}, "yolo": False, "n": 3}
        assert _scan_config_paths(cfg) == []

    def test_nested_dict_dotted_key(self, tmp_path):
        cfg = {"a": {"b": {"c": "/definitely/not/a/real/path/xyz123"}}}
        missing = _scan_config_paths(cfg)
        assert missing[0].startswith("a.b.c=")


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
