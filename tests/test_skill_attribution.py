"""Run-verdict skill attribution (2026-07-29 measurement-honesty fix).

The seam: memory_ledger.stamp_outcome_verdict → _maybe_record_skill_injection
_outcomes. When a FULL-trust goal verdict lands, every skill in the run's
source/skills_manifest.jsonl (written at injection time — skills that
ACTUALLY entered a prompt) gets an injected_runs/injected_successes count
with the run verdict as the label. Contrast with the legacy per-step
counters, which credit keyword-matched bystanders with step completions at
a ~1.0 base rate — the inflation that starved the router.

Same harness idioms as test_contradiction_wiring.py: the emitter resolves
the STAMPED loop's dir via runs.resolve_run_dir (durable join, chunk-4
review F6), so these tests patch the resolver; the manifest is seeded
through the real writer (runs.append_skills_manifest) so the record shape
stays honest.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import runs as runs_module
from memory_ledger import record_outcome, stamp_outcome_verdict
from skills import get_skill_stats, get_all_skill_stats


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _seed_run_manifest(monkeypatch, tmp_path, *, skill_ids=None,
                       write_manifest=True):
    """Seed a run dir with a skills manifest via the REAL writer, and patch
    the durable resolver the seam uses to find it."""
    run_dir = tmp_path / "runs" / "test-run"
    (run_dir / "source").mkdir(parents=True, exist_ok=True)
    if write_manifest and skill_ids:
        monkeypatch.setattr(runs_module, "current_run_dir",
                            lambda: run_dir)
        runs_module.append_skills_manifest(
            [{"id": sid, "name": sid, "content_hash": "", "variant_of": None,
              "tier": "provisional"} for sid in skill_ids],
            stage="decompose")
    monkeypatch.setattr(runs_module, "resolve_run_dir", lambda ref: run_dir)
    return run_dir


def _stamp(loop_id, *, achieved, confidence=0.9):
    return stamp_outcome_verdict(
        loop_id, goal_achieved=achieved, goal_verdict_source="closure",
        goal_verdict_confidence=confidence,
    )


class TestVerdictSeamAttribution:
    def test_full_trust_success_attributes_to_manifest_skills(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        rd = _seed_run_manifest(monkeypatch, tmp_path,
                                skill_ids=["sk-a", "sk-b"])
        record_outcome("goal", "done", "it worked", loop_id="lp-s1")
        assert _stamp("lp-s1", achieved=True).status == "updated"

        for sid in ("sk-a", "sk-b"):
            stats = get_skill_stats(sid)
            assert stats is not None, sid
            assert stats.injected_runs == 1
            assert stats.injected_successes == 1
            assert stats.injected_success_rate == 1.0
            # Legacy counters untouched — regimes never bleed.
            assert stats.total_uses == 0

        marker = json.loads(
            (rd / "source" / "skill_attribution.json").read_text())
        assert marker["loop_id"] == "lp-s1"
        assert marker["goal_achieved"] is True
        assert sorted(marker["skill_ids"]) == ["sk-a", "sk-b"]

    def test_full_trust_failure_counts_as_injected_failure(
            self, monkeypatch, tmp_path):
        """The whole point: a skill in the prompt of a FAILED run finally
        gets a negative label (the legacy path only fired on hard-block)."""
        _setup(monkeypatch, tmp_path)
        _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "looked done, wasn't",
                       loop_id="lp-s2")
        _stamp("lp-s2", achieved=False)
        stats = get_skill_stats("sk-a")
        assert stats is not None
        assert stats.injected_runs == 1
        assert stats.injected_successes == 0
        assert stats.injected_success_rate == 0.0

    def test_restamp_is_idempotent(self, monkeypatch, tmp_path):
        """Verdicts are re-stampable (audit_repair / audit_policy); the
        attribution marker must make the second stamp a no-op."""
        _setup(monkeypatch, tmp_path)
        _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "s", loop_id="lp-s3")
        _stamp("lp-s3", achieved=True)
        assert _stamp("lp-s3", achieved=True).status == "updated"
        stats = get_skill_stats("sk-a")
        assert stats.injected_runs == 1

    def test_duplicate_manifest_rows_dedup_to_one_count(
            self, monkeypatch, tmp_path):
        """Injection can happen more than once per run (decompose, replans);
        a skill present in several manifest records still counts ONE run."""
        _setup(monkeypatch, tmp_path)
        run_dir = _seed_run_manifest(monkeypatch, tmp_path,
                                     skill_ids=["sk-a"])
        monkeypatch.setattr(runs_module, "current_run_dir",
                            lambda: run_dir)
        runs_module.append_skills_manifest(
            [{"id": "sk-a", "name": "sk-a"}], stage="replan")
        record_outcome("goal", "done", "s", loop_id="lp-s4")
        _stamp("lp-s4", achieved=True)
        stats = get_skill_stats("sk-a")
        assert stats.injected_runs == 1

    def test_directional_verdict_records_nothing(self, monkeypatch, tmp_path):
        """Era-10 law pin: learning counters consume verdicts only through
        verdict_trust — a low-confidence False may flavor, never gate."""
        _setup(monkeypatch, tmp_path)
        rd = _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "s", loop_id="lp-s5")
        _stamp("lp-s5", achieved=False, confidence=0.4)
        assert get_all_skill_stats() == []
        assert not (rd / "source" / "skill_attribution.json").exists()

    def test_unjudged_verdict_records_nothing(self, monkeypatch, tmp_path):
        """goal_achieved=None (closure_unverifiable) = unjudged — nothing
        to attribute."""
        _setup(monkeypatch, tmp_path)
        _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "s", loop_id="lp-s6")
        stamp_outcome_verdict(
            "lp-s6", goal_achieved=None,
            goal_verdict_source="closure_unverifiable")
        assert get_all_skill_stats() == []

    def test_missing_manifest_records_nothing(self, monkeypatch, tmp_path):
        """A run that injected no skills produces no counts — absence of
        evidence stays absence, never a phantom row."""
        _setup(monkeypatch, tmp_path)
        _seed_run_manifest(monkeypatch, tmp_path, write_manifest=False)
        record_outcome("goal", "done", "s", loop_id="lp-s7")
        assert _stamp("lp-s7", achieved=True).status == "updated"
        assert get_all_skill_stats() == []

    def test_no_run_dir_degrades_gracefully(self, monkeypatch, tmp_path):
        """A loop the run index can't resolve — the stamp must still land."""
        _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(runs_module, "resolve_run_dir", lambda ref: None)
        record_outcome("goal", "done", "s", loop_id="lp-s8")
        assert _stamp("lp-s8", achieved=True).status == "updated"
        assert get_all_skill_stats() == []
