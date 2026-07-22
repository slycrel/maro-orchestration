"""Tests for lens_ablation — the era-04 triad divergence harness."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lens_ablation import (
    ArmResult,
    SeatReading,
    _COSTUME_FRAMINGS,
    _fake_steps,
    _reading_from_data,
    concern_overlap,
    render_report,
    run_costume_arm,
    run_evidence_arm,
)


@pytest.fixture(autouse=True)
def _hosted_free_off(monkeypatch):
    import hosted_free
    monkeypatch.setattr(hosted_free, "available", lambda: False)


def _adapter(content):
    resp = SimpleNamespace(content=content, input_tokens=5, output_tokens=5)
    a = MagicMock()
    a.complete.return_value = resp
    a.model_key = "stub-model"
    a._active_provider = ""
    return a


class TestOverlapMath:
    def test_identical_is_one(self):
        assert concern_overlap(["missing error handling"],
                               ["missing error handling"]) == 1.0

    def test_disjoint_is_zero(self):
        assert concern_overlap(["missing citations everywhere"],
                               ["wrong dosage numbers"]) == 0.0

    def test_both_empty_is_agreement(self):
        assert concern_overlap([], []) == 1.0

    def test_one_empty_is_zero(self):
        assert concern_overlap(["something concrete"], []) == 0.0


class TestArmResult:
    def _seat(self, name, concerns):
        return SeatReading(seat=name, verdict="WEAK", concerns=concerns)

    def test_mean_overlap_needs_two_seats(self):
        arm = ArmResult(arm="x", seats=[self._seat("a", ["c"])])
        assert arm.mean_pairwise_overlap is None

    def test_identical_seats_read_as_costume(self):
        seats = [self._seat(n, ["the evidence base is thin and unsourced"])
                 for n in ("a", "b", "c")]
        arm = ArmResult(arm="x", seats=seats)
        assert arm.mean_pairwise_overlap == 1.0
        assert all(v == 0 for v in arm.distinct_catches.values())

    def test_divergent_seats_have_distinct_catches(self):
        seats = [
            self._seat("a", ["missing citation for mortality statistic"]),
            self._seat("b", ["dosage arithmetic contradicts earlier table"]),
        ]
        arm = ArmResult(arm="x", seats=seats)
        assert arm.mean_pairwise_overlap < 0.2
        assert arm.distinct_catches == {"a": 1, "b": 1}

    def test_errored_seats_excluded(self):
        seats = [self._seat("a", ["c1"]),
                 SeatReading(seat="b", verdict="(no verdict)", error="boom")]
        arm = ArmResult(arm="x", seats=seats)
        assert arm.mean_pairwise_overlap is None


class TestReadings:
    def test_string_concerns(self):
        r = _reading_from_data(
            "s", {"verdict": "weak",
                  "concerns": ["FINDING[PHANTOM_SYMBOL] cites missing file"]},
            "src", "")
        assert r.verdict == "WEAK"
        assert r.finding_codes == ["PHANTOM_SYMBOL"]

    def test_dict_concerns_use_claim(self):
        r = _reading_from_data(
            "s", {"verdict": "WEAK",
                  "concerns": [{"claim": "cfg missing", "settled_by_command": "x"}]},
            "src", "")
        assert r.concerns == ["cfg missing"]

    def test_unparsable_is_error(self):
        r = _reading_from_data("s", None, "src", "")
        assert r.error


class TestArms:
    _WEAK = '{"verdict": "WEAK", "concerns": ["thin evidence"], "most_critical_gap": "g"}'

    def test_costume_arm_runs_three_seats(self):
        adapter = _adapter(self._WEAK)
        arm = run_costume_arm("goal", _fake_steps("output"), adapter=adapter)
        assert [s.seat for s in arm.seats] == [n for n, _ in _COSTUME_FRAMINGS]
        assert arm.calls == 3
        assert adapter.complete.call_count == 3

    def test_evidence_arm_flags_degenerate_transcript(self):
        adapter = _adapter(self._WEAK)
        arm = run_evidence_arm("goal", _fake_steps("output"), adapter=adapter)
        assert len(arm.seats) == 3
        assert "degenerates" in arm.degenerate_note

    def test_evidence_arm_no_note_with_real_steps(self):
        adapter = _adapter(self._WEAK)
        steps = [SimpleNamespace(index=i, status="done", text=f"s{i}", result=f"r{i}")
                 for i in (1, 2, 3)]
        arm = run_evidence_arm("goal", steps, adapter=adapter)
        assert arm.degenerate_note == ""


class TestReport:
    def test_report_names_arms_and_reading(self):
        seats = [SeatReading(seat=n, verdict="WEAK",
                             concerns=["identical concern about thin evidence"])
                 for n in ("a", "b")]
        arm = ArmResult(arm="costume", seats=seats, calls=2)
        report = render_report("my goal", [arm])
        assert "## Arm: costume (2 calls)" in report
        assert "near-identical" in report

    def test_report_surfaces_degenerate_note(self):
        arm = ArmResult(arm="evidence",
                        seats=[SeatReading(seat="a", verdict="STRONG")],
                        calls=1, degenerate_note="transcript degenerate")
        report = render_report("g", [arm])
        assert "NOTE: transcript degenerate" in report
