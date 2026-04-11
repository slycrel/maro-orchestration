"""Tests for compact_ab.py — compact notation A/B test harness."""

import json
import pytest


@pytest.fixture(autouse=True)
def _use_tmp_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))


class TestLoadCompactSkill:
    def test_loads_skill_content(self):
        from compact_ab import _load_compact_skill
        text = _load_compact_skill()
        assert "Compact Notation" in text
        assert "ok" in text
        assert "err" in text

    def test_strips_frontmatter(self):
        from compact_ab import _load_compact_skill
        text = _load_compact_skill()
        assert "---" not in text
        assert "always_inject" not in text


class TestABRound:
    def test_dry_run_completes(self):
        from compact_ab import run_ab_test
        report = run_ab_test(
            steps=["Analyze the top 3 nootropics"],
            dry_run=True,
            rounds=1,
        )
        assert len(report.rounds) == 1
        assert report.rounds[0].control_status == "done"
        assert report.rounds[0].treatment_status == "done"

    def test_dry_run_multiple_steps(self):
        from compact_ab import run_ab_test
        report = run_ab_test(
            steps=["Step A", "Step B"],
            dry_run=True,
            rounds=1,
        )
        assert len(report.rounds) == 2

    def test_dry_run_multiple_rounds(self):
        from compact_ab import run_ab_test
        report = run_ab_test(
            steps=["Single step"],
            dry_run=True,
            rounds=3,
        )
        assert len(report.rounds) == 3

    def test_report_has_summary(self):
        from compact_ab import run_ab_test
        report = run_ab_test(steps=["test"], dry_run=True)
        summary = report.summary()
        assert "Compact Notation A/B Test" in summary
        assert "VERDICT" in summary

    def test_report_model_field(self):
        from compact_ab import run_ab_test
        report = run_ab_test(steps=["test"], dry_run=True)
        assert report.model == "dry-run"


class TestSaveReport:
    def test_saves_json(self, tmp_path):
        from compact_ab import run_ab_test, save_report
        report = run_ab_test(steps=["test"], dry_run=True)
        path = save_report(report, path=tmp_path / "test_report.json")
        assert path.exists()
        data = json.loads(path.read_text())
        assert "summary" in data
        assert "rounds" in data
        assert "avg_reduction_pct" in data


class TestDefaultSteps:
    def test_default_steps_nonempty(self):
        from compact_ab import DEFAULT_STEPS
        assert len(DEFAULT_STEPS) >= 3

    def test_default_steps_run_dry(self):
        from compact_ab import run_ab_test
        report = run_ab_test(dry_run=True, rounds=1)
        assert len(report.rounds) == len(__import__("compact_ab").DEFAULT_STEPS)


class TestQualityNotes:
    def test_no_quality_notes_on_dry_run(self):
        from compact_ab import run_ab_test
        report = run_ab_test(steps=["test"], dry_run=True)
        # Dry run produces identical outputs so no content loss
        assert not any("content loss" in n for n in report.quality_notes)
