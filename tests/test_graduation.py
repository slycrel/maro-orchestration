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

    def test_suggestion_includes_verify_pattern(self, tmp_path, monkeypatch):
        """Suggestions written by run_graduation should include verify_pattern."""
        sug_path = self._setup(tmp_path, monkeypatch, "token_explosion")
        import graduation
        graduation.run_graduation(min_count=3)
        data = json.loads(sug_path.read_text().strip())
        assert "verify_pattern" in data
        assert len(data["verify_pattern"]) > 0

    def test_suggestion_includes_expected_signal(self, tmp_path, monkeypatch):
        """VERIFY_LEARN_ARC V1: every graduation template declares a
        behavioral expectation naming its own failure class, so V2's future
        cadence verdict has something concrete to check."""
        sug_path = self._setup(tmp_path, monkeypatch, "retry_churn")
        import graduation
        graduation.run_graduation(min_count=3)
        data = json.loads(sug_path.read_text().strip())
        assert "expected_signal" in data
        assert data["expected_signal"] == [
            {"metric": "failure_class_rate", "class": "retry_churn", "direction": "down"}
        ]

    def test_every_template_declares_expected_signal(self):
        """Row-shape unit: no template should be exempt from the V1 contract."""
        import graduation
        for fc, template in graduation._GRADUATION_TEMPLATES.items():
            sig = template.get("expected_signal")
            assert sig, f"{fc} template has no expected_signal"
            assert sig[0]["class"] == fc
            assert sig[0]["direction"] in ("up", "down")


# ---------------------------------------------------------------------------
# verify_graduation_rules
# ---------------------------------------------------------------------------

class TestVerifyGraduationRules:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        import graduation
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: tmp_path / "missing.jsonl")
        assert graduation.verify_graduation_rules() == []

    def test_skips_entries_without_verify_pattern(self, tmp_path, monkeypatch):
        import graduation
        sug_path = _write_suggestions(tmp_path, [
            {"failure_pattern": "graduation:adapter_timeout", "category": "observation"}
            # no verify_pattern field
        ])
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        results = graduation.verify_graduation_rules()
        assert results == []

    def test_passing_verify_pattern(self, tmp_path, monkeypatch):
        """A pattern that matches (echo something) should pass."""
        import graduation
        sug_path = _write_suggestions(tmp_path, [
            {
                "failure_pattern": "graduation:adapter_timeout",
                "category": "observation",
                "verify_pattern": "echo found_it",
                "applied": True,
                "suggestion_id": "grad-pass",
            }
        ])
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        results = graduation.verify_graduation_rules()
        assert len(results) == 1
        assert results[0]["failure_class"] == "adapter_timeout"
        assert results[0]["suggestion_id"] == "grad-pass"
        assert results[0]["passed"] is True
        assert results[0]["structural_only"] is True
        assert "found_it" in results[0]["output"]

    def test_failing_verify_pattern(self, tmp_path, monkeypatch):
        """A pattern with no output should fail."""
        import graduation
        sug_path = _write_suggestions(tmp_path, [
            {
                "failure_pattern": "graduation:token_explosion",
                "category": "prompt_tweak",
                "verify_pattern": "grep -n 'THIS_STRING_DOES_NOT_EXIST_ANYWHERE' /dev/null 2>/dev/null",
                "applied": True,
            }
        ])
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        results = graduation.verify_graduation_rules()
        assert len(results) == 1
        assert results[0]["passed"] is False

    def test_deduplicates_same_failure_class(self, tmp_path, monkeypatch):
        """Multiple suggestions for same failure_class produce only one verify result."""
        import graduation
        sug_path = _write_suggestions(tmp_path, [
            {"failure_pattern": "graduation:adapter_timeout", "verify_pattern": "echo a", "applied": True},
            {"failure_pattern": "graduation:adapter_timeout", "verify_pattern": "echo b", "applied": True},
        ])
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        results = graduation.verify_graduation_rules()
        assert len(results) == 1
        assert results[0]["output"] == "b"  # newest record wins

    def test_skips_pending_held_and_reverted_rows(self, tmp_path, monkeypatch):
        import graduation
        sug_path = _write_suggestions(tmp_path, [
            {"failure_pattern": "graduation:adapter_timeout", "verify_pattern": "echo pending", "applied": False},
            {"failure_pattern": "graduation:token_explosion", "verify_pattern": "echo held", "applied": False, "status": "held_for_review"},
            {"failure_pattern": "graduation:retry_churn", "verify_pattern": "echo reverted", "applied": False, "status": "reverted"},
        ])
        monkeypatch.setattr(graduation, "_suggestions_path", lambda: sug_path)
        assert graduation.verify_graduation_rules() == []

    def test_cadence_pass_emits_events_and_notifies_only_failures(
        self, tmp_path, monkeypatch
    ):
        import graduation
        captured = []
        notices = []
        monkeypatch.setattr(graduation, "verify_graduation_rules", lambda lookback=200: [
            {"suggestion_id": "ok", "failure_class": "adapter_timeout", "category": "observation", "applied_manually": False, "verify_pattern": "echo ok", "passed": True, "output": "ok", "structural_only": True},
            {"suggestion_id": "bad", "failure_class": "token_explosion", "category": "prompt_tweak", "applied_manually": True, "verify_pattern": "false", "passed": False, "output": "missing", "structural_only": True},
        ])
        monkeypatch.setattr("captains_log.log_event", lambda event_type, **kwargs: captured.append((event_type, kwargs)))
        monkeypatch.setattr("telegram_listener.telegram_notify", lambda message: notices.append(message) or True)
        monkeypatch.setattr(graduation, "_verification_state_path", lambda: tmp_path / "verify-state.json")

        results = graduation.run_graduation_verification(notify=True)

        assert len(results) == 2
        assert len(captured) == 2
        assert all(event == "GRADUATION_VERIFIED" for event, _ in captured)
        assert len(notices) == 1
        assert "token_explosion" in notices[0]
        assert "not an automatic regression verdict" in notices[0]

    def test_cadence_side_effects_only_on_state_transition(
        self, tmp_path, monkeypatch
    ):
        import graduation
        captured = []
        notices = []
        passed = {"value": False}

        def _results(lookback=200):
            return [{
                "suggestion_id": "g1",
                "failure_class": "retry_churn",
                "category": "observation",
                "applied_manually": False,
                "applied_at": "2026-07-14T00:00:00+00:00",
                "verify_pattern": "false",
                "passed": passed["value"],
                "output": "missing",
                "structural_only": True,
            }]

        monkeypatch.setattr(graduation, "verify_graduation_rules", _results)
        monkeypatch.setattr(graduation, "_verification_state_path", lambda: tmp_path / "verify-state.json")
        monkeypatch.setattr("captains_log.log_event", lambda event_type, **kwargs: captured.append((event_type, kwargs)))
        monkeypatch.setattr("telegram_listener.telegram_notify", lambda message: notices.append(message) or True)

        graduation.run_graduation_verification(notify=True)
        graduation.run_graduation_verification(notify=True)
        assert len(captured) == 1
        assert len(notices) == 1

        passed["value"] = True
        graduation.run_graduation_verification(notify=True)
        assert len(captured) == 2
        assert len(notices) == 1

    def test_failed_notification_delivery_retries_without_relogging_event(
        self, tmp_path, monkeypatch
    ):
        import graduation
        captured = []
        attempts = []
        delivered = {"value": False}
        result = {
            "suggestion_id": "g1", "failure_class": "retry_churn",
            "category": "observation", "applied_manually": False,
            "applied_at": "2026-07-14T00:00:00+00:00",
            "verify_pattern": "false", "passed": False,
            "output": "missing", "structural_only": True,
        }
        monkeypatch.setattr(graduation, "verify_graduation_rules", lambda lookback=200: [result])
        monkeypatch.setattr(graduation, "_verification_state_path", lambda: tmp_path / "verify-state.json")
        monkeypatch.setattr("captains_log.log_event", lambda event_type, **kwargs: captured.append(event_type))
        monkeypatch.setattr(
            "telegram_listener.telegram_notify",
            lambda message: attempts.append(message) or delivered["value"],
        )

        graduation.run_graduation_verification(notify=True)
        delivered["value"] = True
        graduation.run_graduation_verification(notify=True)

        assert captured == ["GRADUATION_VERIFIED"]
        assert len(attempts) == 2
