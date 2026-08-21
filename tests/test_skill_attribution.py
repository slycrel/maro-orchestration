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


class TestAttributionIsOneTransaction:
    """Adversarial r16 (four seats, probed): the seam applied a manifest
    with a per-id loop, so once the recorder started raising (r15), a
    mid-list failure became a reachable partial batch — id A committed,
    id B failed, the marker never written, and the retry credited A
    twice. The seam now calls the batch recorder: every id commits in
    one write or none do, so a retry after a failed batch counts each
    skill exactly once."""

    def test_a_failed_batch_commits_nothing_and_retry_counts_once(
            self, monkeypatch, tmp_path, caplog):
        import logging
        import skills as sk
        _setup(monkeypatch, tmp_path)
        rd = _seed_run_manifest(monkeypatch, tmp_path,
                                skill_ids=["sk-a", "sk-b"])
        record_outcome("goal", "done", "it worked", loop_id="lp-batch")

        real = sk._write_skill_stats
        def boom(*a, **k):
            raise OSError("simulated ENOSPC")
        monkeypatch.setattr(sk, "_write_skill_stats", boom)
        with caplog.at_level(logging.WARNING):
            assert _stamp("lp-batch", achieved=True).status == "updated"
        # Nothing committed, no marker, and the failure is
        # operator-visible (WARNING, not the old DEBUG).
        for sid in ("sk-a", "sk-b"):
            st = get_skill_stats(sid)
            assert st is None or st.injected_runs == 0, sid
        assert not (rd / "source" / "skill_attribution.json").exists()
        assert "attribution failed" in caplog.text

        # Retry with the disk healed: each id counted exactly ONCE.
        monkeypatch.setattr(sk, "_write_skill_stats", real)
        assert _stamp("lp-batch", achieved=True).status == "updated"
        for sid in ("sk-a", "sk-b"):
            assert get_skill_stats(sid).injected_runs == 1, sid
        assert (rd / "source" / "skill_attribution.json").exists()

    def test_the_seam_uses_the_batch_recorder(self):
        """Structural pin: the per-id loop was the defect."""
        import inspect
        import memory_ledger as ml
        src = inspect.getsource(
            ml._maybe_record_skill_injection_outcomes)
        import re
        assert "record_skill_injection_outcomes" in src
        # The singular per-id recorder must not be called here at ANY
        # indentation (r17: the r16 pattern-match was indentation-bound
        # and a re-nested per-id loop walked past it).
        assert not re.search(r"record_skill_injection_outcome\(", src)


class TestTheMarkerIsProofNotPresence:
    """Adversarial r17 (three seats, probed): the seam trusted
    marker.exists() — a zero-byte or copied marker silently suppressed a
    whole run's verdicts; the check ran outside any lock, so two live
    stampers could both pass it and double-apply; and a marker-write
    failure after the batch committed was reported with the pre-commit
    "NOT recorded" message. The check→batch→marker section now runs
    under the stats lock, a marker only counts when its content matches
    this run, and the two failure legs report what the store holds."""

    def test_a_second_stamp_is_a_no_op(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "ok", loop_id="lp-idem")
        assert _stamp("lp-idem", achieved=True).status == "updated"
        assert _stamp("lp-idem", achieved=True).status == "updated"
        assert get_skill_stats("sk-a").injected_runs == 1

    def test_an_invalid_marker_is_unknown_not_reapplied(
            self, monkeypatch, tmp_path, caplog):
        import logging
        _setup(monkeypatch, tmp_path)
        rd = _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "ok", loop_id="lp-forge")
        # A zero-byte marker: crash-torn or hand-created.
        (rd / "source" / "skill_attribution.json").write_text("")
        with caplog.at_level(logging.WARNING):
            assert _stamp("lp-forge", achieved=True).status == "updated"
        # NOT silently suppressed — and NOT auto-re-applied either (the
        # batch may already be in the store): announced as UNKNOWN.
        st = get_skill_stats("sk-a")
        assert st is None or st.injected_runs == 0
        assert "UNKNOWN" in caplog.text
        assert "skill_attribution.json" in caplog.text

    def test_a_mismatched_marker_is_unknown(
            self, monkeypatch, tmp_path, caplog):
        import json
        import logging
        _setup(monkeypatch, tmp_path)
        rd = _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "ok", loop_id="lp-copied")
        # A marker copied from ANOTHER run: valid JSON, wrong loop.
        (rd / "source" / "skill_attribution.json").write_text(json.dumps({
            "loop_id": "some-other-run", "goal_achieved": True,
            "skill_ids": ["sk-a"]}))
        with caplog.at_level(logging.WARNING):
            _stamp("lp-copied", achieved=True)
        st = get_skill_stats("sk-a")
        assert st is None or st.injected_runs == 0
        assert "UNKNOWN" in caplog.text

    def test_marker_write_failure_reports_the_commit_honestly(
            self, monkeypatch, tmp_path, caplog):
        import logging
        import file_lock as fl
        _setup(monkeypatch, tmp_path)
        rd = _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "ok", loop_id="lp-marker")

        real_aw = fl.atomic_write
        def boom(path, content, **kw):
            if getattr(path, "name", "") == "skill_attribution.json":
                raise OSError("simulated ENOSPC")
            return real_aw(path, content, **kw)
        monkeypatch.setattr(fl, "atomic_write", boom)
        with caplog.at_level(logging.WARNING):
            assert _stamp("lp-marker", achieved=True).status == "updated"
        # The batch COMMITTED — the message must say so, not "NOT
        # recorded" (the pre-commit message would invite manual repair
        # against false state).
        assert get_skill_stats("sk-a").injected_runs == 1
        assert not (rd / "source" / "skill_attribution.json").exists()
        assert "SUCCEEDED" in caplog.text
        assert "NOT written" in caplog.text
        assert "verdict NOT recorded" not in caplog.text

    def test_the_check_batch_marker_section_holds_the_stats_lock(self):
        """Structural pin: the marker check must live INSIDE the
        locked_write(_skill_stats_path()) section — outside it, two
        live stampers both see no marker and double-apply."""
        import inspect
        import memory_ledger as ml
        src = inspect.getsource(ml._maybe_record_skill_injection_outcomes)
        lock_at = src.index("with locked_write(_skill_stats_path()")
        check_at = src.index("if marker.exists():")
        batch_at = src.index("record_skill_injection_outcomes(skill_ids")
        assert lock_at < check_at < batch_at


class TestManifestIdsAreAdmittedNotCoerced:
    """Adversarial r17 (two seats, probed): `str(entry.get("id"))`
    minted stats identities "True" and "7" out of malformed manifest
    rows — laundered evidence that then blocked reprocessing via the
    marker. A manifest id must BE a non-empty string; anything else is
    excluded and announced."""

    def test_non_string_ids_are_excluded_and_announced(
            self, monkeypatch, tmp_path, caplog):
        import json
        import logging
        _setup(monkeypatch, tmp_path)
        run_dir = tmp_path / "runs" / "test-run"
        (run_dir / "source").mkdir(parents=True, exist_ok=True)
        with open(run_dir / "source" / "skills_manifest.jsonl", "w") as f:
            f.write(json.dumps({"skills": [
                {"id": True}, {"id": 7}, {"id": ""}, {"id": "ok"},
                "not-a-dict"]}) + "\n")
        monkeypatch.setattr(runs_module, "resolve_run_dir",
                            lambda ref: run_dir)
        record_outcome("goal", "done", "ok", loop_id="lp-coerce")
        with caplog.at_level(logging.WARNING):
            assert _stamp("lp-coerce", achieved=True).status == "updated"
        assert get_skill_stats("ok").injected_runs == 1
        for minted in ("True", "7"):
            assert get_skill_stats(minted) is None, minted
        assert "without a string id" in caplog.text

    def test_the_sibling_reader_rejects_them_too(self, tmp_path):
        import json
        run_dir = tmp_path / "run"
        (run_dir / "source").mkdir(parents=True)
        with open(run_dir / "source" / "skills_manifest.jsonl", "w") as f:
            f.write(json.dumps({"skills": [
                {"id": True}, {"id": 7}, {"id": "ok"}]}) + "\n")
        assert runs_module.read_injected_skill_ids(run_dir) == {"ok"}


class TestACorrectedVerdictIsAnnouncedNotAbsorbed:
    """Adversarial r18 (three seats, HIGH, all probed): the marker
    predicate checked goal_achieved was A bool, never THE verdict this
    stamp computed — so a legitimately corrected verdict (re-stamp via
    stamp_outcome_verdict, decree 2026-08-10) was absorbed as
    already-attributed, silently, and skill stats kept the stale
    verdict forever. A correction still must not auto-re-apply (the
    committed batch cannot be decremented here) — but it is announced,
    never absorbed."""

    def test_a_flip_restamp_warns_and_does_not_reapply(
            self, monkeypatch, tmp_path, caplog):
        import logging
        _setup(monkeypatch, tmp_path)
        _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "x", loop_id="lp-flip")
        assert _stamp("lp-flip", achieved=True).status == "updated"
        s1 = get_skill_stats("sk-a")
        assert (s1.injected_runs, s1.injected_successes) == (1, 1)
        with caplog.at_level(logging.WARNING):
            _stamp("lp-flip", achieved=False)
        s2 = get_skill_stats("sk-a")
        # NOT re-applied in either direction — no double count, no
        # silent decrement.
        assert (s2.injected_runs, s2.injected_successes) == (1, 1)
        assert any("corrected verdict does NOT auto-adjust"
                   in r.getMessage() for r in caplog.records)

    def test_a_matching_restamp_stays_silent(
            self, monkeypatch, tmp_path, caplog):
        import logging
        _setup(monkeypatch, tmp_path)
        _seed_run_manifest(monkeypatch, tmp_path, skill_ids=["sk-a"])
        record_outcome("goal", "done", "x", loop_id="lp-same")
        assert _stamp("lp-same", achieved=True).status == "updated"
        with caplog.at_level(logging.WARNING):
            _stamp("lp-same", achieved=True)
        s = get_skill_stats("sk-a")
        assert (s.injected_runs, s.injected_successes) == (1, 1)
        assert not any("corrected verdict" in r.getMessage()
                       for r in caplog.records)
        assert not any("attribution marker" in r.getMessage()
                       for r in caplog.records)
