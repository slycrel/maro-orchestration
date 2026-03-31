"""Tests for Phase 46: Intervention Graduation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_diagnosis(failure_class: str, loop_id: str = "abc12345", evidence=None) -> dict:
    return {
        "loop_id": loop_id,
        "failure_class": failure_class,
        "severity": "warning",
        "evidence": evidence or [f"step blocked: {failure_class}"],
        "recommendation": "fix it",
        "total_tokens": 10000,
        "total_elapsed_ms": 5000,
        "steps_done": 3,
        "steps_blocked": 1,
        "steps_total": 4,
    }


def _write_diagnoses(tmp_path, entries):
    p = tmp_path / "diagnoses.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return p


def _write_suggestions(tmp_path, entries):
    p = tmp_path / "suggestions.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return p


# ---------------------------------------------------------------------------
# scan_candidates
# ---------------------------------------------------------------------------

class TestScanCandidates:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        import graduation
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: tmp_path / "missing.jsonl")
        assert graduation.scan_candidates() == []

    def test_below_threshold_not_returned(self, tmp_path, monkeypatch):
        import graduation
        entries = [_make_diagnosis("adapter_timeout", f"loop{i}") for i in range(2)]
        diag_path = _write_diagnoses(tmp_path, entries)
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)

        candidates = graduation.scan_candidates(min_count=3)
        assert candidates == []

    def test_at_threshold_returned(self, tmp_path, monkeypatch):
        import graduation
        entries = [_make_diagnosis("adapter_timeout", f"loop{i}") for i in range(3)]
        diag_path = _write_diagnoses(tmp_path, entries)
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)

        candidates = graduation.scan_candidates(min_count=3)
        assert len(candidates) == 1
        assert candidates[0].failure_class == "adapter_timeout"
        assert candidates[0].count == 3

    def test_healthy_excluded(self, tmp_path, monkeypatch):
        import graduation
        entries = [_make_diagnosis("healthy", f"loop{i}") for i in range(5)]
        diag_path = _write_diagnoses(tmp_path, entries)
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)

        candidates = graduation.scan_candidates(min_count=3)
        assert candidates == []

    def test_unknown_class_excluded(self, tmp_path, monkeypatch):
        import graduation
        entries = [_make_diagnosis("some_unknown_failure", f"loop{i}") for i in range(5)]
        diag_path = _write_diagnoses(tmp_path, entries)
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)

        candidates = graduation.scan_candidates(min_count=3)
        assert candidates == []

    def test_multiple_classes_sorted_by_count(self, tmp_path, monkeypatch):
        import graduation
        entries = (
            [_make_diagnosis("adapter_timeout", f"a{i}") for i in range(5)] +
            [_make_diagnosis("constraint_false_positive", f"b{i}") for i in range(3)]
        )
        diag_path = _write_diagnoses(tmp_path, entries)
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)

        candidates = graduation.scan_candidates(min_count=3)
        assert len(candidates) == 2
        assert candidates[0].failure_class == "adapter_timeout"
        assert candidates[0].count == 5
        assert candidates[1].count == 3

    def test_collects_evidence_samples(self, tmp_path, monkeypatch):
        import graduation
        entries = [
            _make_diagnosis("token_explosion", f"loop{i}", evidence=[f"evidence_{i}"])
            for i in range(4)
        ]
        diag_path = _write_diagnoses(tmp_path, entries)
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)

        candidates = graduation.scan_candidates(min_count=3)
        assert len(candidates) == 1
        assert len(candidates[0].evidence_samples) <= 3  # capped at 3

    def test_loop_ids_captured(self, tmp_path, monkeypatch):
        import graduation
        entries = [_make_diagnosis("retry_churn", f"loop{i}") for i in range(4)]
        diag_path = _write_diagnoses(tmp_path, entries)
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)

        candidates = graduation.scan_candidates(min_count=3)
        assert candidates[0].loop_ids  # non-empty


# ---------------------------------------------------------------------------
# _already_proposed
# ---------------------------------------------------------------------------

class TestAlreadyProposed:
    def test_false_when_no_file(self, tmp_path, monkeypatch):
        import graduation
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: tmp_path / "missing.jsonl")
        assert graduation._already_proposed("adapter_timeout") is False

    def test_false_when_not_present(self, tmp_path, monkeypatch):
        import graduation
        sug_path = _write_suggestions(tmp_path, [
            {"failure_pattern": "graduation:constraint_false_positive", "category": "new_guardrail"}
        ])
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        assert graduation._already_proposed("adapter_timeout") is False

    def test_true_when_present(self, tmp_path, monkeypatch):
        import graduation
        sug_path = _write_suggestions(tmp_path, [
            {"failure_pattern": "graduation:adapter_timeout", "category": "observation"}
        ])
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        assert graduation._already_proposed("adapter_timeout") is True


# ---------------------------------------------------------------------------
# run_graduation
# ---------------------------------------------------------------------------

class TestRunGraduation:
    def _setup(self, tmp_path, monkeypatch, failure_class, count=4):
        import graduation
        entries = [_make_diagnosis(failure_class, f"loop{i}") for i in range(count)]
        diag_path = _write_diagnoses(tmp_path, entries)
        sug_path = tmp_path / "suggestions.jsonl"
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        return sug_path

    def test_dry_run_writes_nothing(self, tmp_path, monkeypatch):
        sug_path = self._setup(tmp_path, monkeypatch, "adapter_timeout")
        import graduation
        n = graduation.run_graduation(min_count=3, dry_run=True)
        assert n == 0
        assert not sug_path.exists()

    def test_writes_suggestion_when_candidate_found(self, tmp_path, monkeypatch):
        sug_path = self._setup(tmp_path, monkeypatch, "adapter_timeout")
        import graduation
        n = graduation.run_graduation(min_count=3, dry_run=False)
        assert n == 1
        assert sug_path.exists()
        lines = [l for l in sug_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert "graduation:adapter_timeout" in data["failure_pattern"]
        assert data["applied"] is False
        assert data["confidence"] > 0

    def test_no_duplicate_proposals(self, tmp_path, monkeypatch):
        sug_path = self._setup(tmp_path, monkeypatch, "adapter_timeout")
        import graduation

        # First run: writes 1
        n1 = graduation.run_graduation(min_count=3)
        assert n1 == 1

        # Second run: already proposed, writes 0
        n2 = graduation.run_graduation(min_count=3)
        assert n2 == 0

    def test_zero_when_below_threshold(self, tmp_path, monkeypatch):
        sug_path = self._setup(tmp_path, monkeypatch, "adapter_timeout", count=2)
        import graduation
        n = graduation.run_graduation(min_count=3)
        assert n == 0

    def test_suggestion_fields_valid(self, tmp_path, monkeypatch):
        sug_path = self._setup(tmp_path, monkeypatch, "constraint_false_positive")
        import graduation
        graduation.run_graduation(min_count=3)
        data = json.loads(sug_path.read_text().strip())
        assert data["suggestion_id"].startswith("grad-")
        assert data["category"] in ("observation", "prompt_tweak", "new_guardrail", "skill_pattern")
        assert len(data["suggestion"]) <= 500
        assert data["outcomes_analyzed"] >= 3
        assert "generated_at" in data

    def test_multiple_classes_all_written(self, tmp_path, monkeypatch):
        import graduation
        entries = (
            [_make_diagnosis("adapter_timeout", f"a{i}") for i in range(4)] +
            [_make_diagnosis("token_explosion", f"b{i}") for i in range(4)]
        )
        diag_path = _write_diagnoses(tmp_path, entries)
        sug_path = tmp_path / "suggestions.jsonl"
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: diag_path)
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)

        n = graduation.run_graduation(min_count=3)
        assert n == 2
        lines = [l for l in sug_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_no_candidates_returns_zero(self, tmp_path, monkeypatch):
        import graduation
        monkeypatch.setattr(graduation, "_diagnoses_path", lambda: tmp_path / "missing.jsonl")
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: tmp_path / "sug.jsonl")
        assert graduation.run_graduation() == 0
