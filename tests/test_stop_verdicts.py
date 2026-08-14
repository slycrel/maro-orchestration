"""Chunk-9 #4 (2026-07-27): stop-verdict split — a typed verdict for WHY a
run ended rides beside status (§13b: every stop carries evidence and a
type-derived reopen condition; COMPOUND_THINKING_DESIGN + stop-path survey
2026-07-23).

Pins: the vocabulary module, the first-write-wins stamping rail
(LoopContext → LoopResult → metadata.json → outcome row), the post-hoc
stamps (handle demotion, director escalation close), and the four
raw-status consumers that must stop treating external interrupts as goal
evidence (outcome_policy, recall repeat-guard, strategy_evaluator,
attribution).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stop_verdicts import (
    EXTERNAL_INTERRUPT,
    GOAL_VERDICTS,
    INTERRUPT_STATUSES,
    LOST_THE_PLOT,
    NOT_WORTH_IT,
    OUT_OF_BUDGET,
    THESIS_REFUTED,
    VALID_STOP_VALUES,
)


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_four_goal_verdicts_plus_interrupt_marker(self):
        # Jeremy's decree (GOAL_BRAIN 2026-07-27 item 5): external-interrupt
        # is NOT a fifth verdict — the four verdicts are observations about
        # the map, an interrupt is an event about the run. The marker shares
        # the field (with first-write-wins precedence giving evidence-backed
        # verdicts priority) but stays outside the verdict taxonomy.
        assert GOAL_VERDICTS == frozenset(
            (OUT_OF_BUDGET, THESIS_REFUTED, NOT_WORTH_IT, LOST_THE_PLOT))
        assert EXTERNAL_INTERRUPT not in GOAL_VERDICTS
        assert VALID_STOP_VALUES == GOAL_VERDICTS | {EXTERNAL_INTERRUPT}

    def test_interrupt_status_family(self):
        # The four statuses the survey found falling into the "unknown"
        # success_class hole.
        assert INTERRUPT_STATUSES == frozenset(
            ("interrupted", "stranded", "refused_busy", "clarification_needed"))


# ---------------------------------------------------------------------------
# The stamping rail: LoopContext → LoopResult → metadata → outcome row
# ---------------------------------------------------------------------------


class TestStampRail:
    def test_ctx_stamp_first_write_wins(self):
        from loop_types import LoopContext
        ctx = LoopContext(loop_id="lp-1", goal="g")
        ctx.stamp_stop(OUT_OF_BUDGET, "hit the cap")
        ctx.stamp_stop(THESIS_REFUTED, "later, generic machinery")
        assert ctx.stop_verdict == OUT_OF_BUDGET
        assert ctx.stop_evidence == "hit the cap"

    def test_ctx_stamp_evidence_capped_at_800_with_marker(self):
        # 800 since the 2026-08-13 STORE widening (old 500 cut the
        # stuck_reason-family max of 594 silently); the cut announces
        # itself via the clip marker.
        from loop_types import LoopContext
        ctx = LoopContext(loop_id="lp-1", goal="g")
        ctx.stamp_stop(OUT_OF_BUDGET, "x" * 900)
        assert ctx.stop_evidence.startswith("x" * 800)
        assert "truncated: first 800 of 900" in ctx.stop_evidence
        # A fitting value rides whole, no marker.
        ctx2 = LoopContext(loop_id="lp-2", goal="g")
        ctx2.stamp_stop(OUT_OF_BUDGET, "y" * 700)
        assert ctx2.stop_evidence == "y" * 700

    def test_loop_result_summary_mentions_verdict_only_when_set(self):
        from loop_types import LoopResult
        plain = LoopResult(loop_id="lp", project="p", goal="g", status="done")
        assert "stop_verdict" not in plain.summary()
        stamped = LoopResult(loop_id="lp", project="p", goal="g",
                             status="stuck",
                             stop_verdict=OUT_OF_BUDGET, stop_evidence="cap")
        assert OUT_OF_BUDGET in stamped.summary()

    def test_record_outcome_row_carries_verdict_and_omits_empty(self, workspace):
        from memory_ledger import _outcomes_path, record_outcome
        record_outcome("g1", "stuck", "s", loop_id="lp-a",
                       stop_verdict=OUT_OF_BUDGET)
        record_outcome("g2", "done", "s", loop_id="lp-b")
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        by_loop = {r.get("loop_id"): r for r in rows}
        assert by_loop["lp-a"]["stop_verdict"] == OUT_OF_BUDGET
        # Empty verdict is dropped from the row, matching goal-verdict style:
        # absence means "none recorded", not "".
        assert "stop_verdict" not in by_loop["lp-b"]

    def test_stamp_outcome_stop_verdict_posthoc(self, workspace):
        from memory_ledger import (
            _outcomes_path,
            record_outcome,
            stamp_outcome_stop_verdict,
        )
        record_outcome("g", "stuck", "s", loop_id="lp-close",
                       task_type="general")
        assert stamp_outcome_stop_verdict("lp-close", NOT_WORTH_IT) is True
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        row = next(r for r in rows if r.get("loop_id") == "lp-close")
        assert row["stop_verdict"] == NOT_WORTH_IT
        # Merge-only: the goal-verdict tri-state is untouched.
        assert "goal_achieved" not in row or row["goal_achieved"] is None

    def test_stamp_outcome_stop_verdict_miss_returns_false(self, workspace):
        from memory_ledger import stamp_outcome_stop_verdict
        assert stamp_outcome_stop_verdict("no-such-loop", NOT_WORTH_IT) is False

    # Adversarial-review round (2026-07-27, three-lens consensus): the rail
    # promised verdict + evidence but the ledger row carried only the verdict
    # — loop_blocked's convergence evidence and the director's [refines: …]
    # note died at the row boundary.
    def test_record_outcome_row_carries_evidence_and_omits_empty(self, workspace):
        from memory_ledger import _outcomes_path, record_outcome
        record_outcome("g1", "stuck", "s", loop_id="lp-ev",
                       stop_verdict=THESIS_REFUTED,
                       stop_evidence="exhausted: 3 retries, converging=True")
        record_outcome("g2", "stuck", "s", loop_id="lp-noev",
                       stop_verdict=OUT_OF_BUDGET)
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        by_loop = {r.get("loop_id"): r for r in rows}
        assert by_loop["lp-ev"]["stop_evidence"].startswith("exhausted:")
        assert "stop_evidence" not in by_loop["lp-noev"]

    def test_record_outcome_evidence_capped_at_800_with_marker(self, workspace):
        # Same 2026-08-13 contract as the ctx stamp above.
        from memory_ledger import _outcomes_path, record_outcome
        record_outcome("g", "stuck", "s", loop_id="lp-cap",
                       stop_verdict=OUT_OF_BUDGET, stop_evidence="x" * 900)
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        row = next(r for r in rows if r.get("loop_id") == "lp-cap")
        assert row["stop_evidence"].startswith("x" * 800)
        assert "truncated: first 800 of 900" in row["stop_evidence"]

    def test_posthoc_stamp_carries_evidence(self, workspace):
        from memory_ledger import (
            _outcomes_path,
            record_outcome,
            stamp_outcome_stop_verdict,
        )
        record_outcome("g", "done", "s", loop_id="lp-merge")
        assert stamp_outcome_stop_verdict(
            "lp-merge", EXTERNAL_INTERRUPT,
            "worktree merge failed — work preserved on branch b") is True
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        row = next(r for r in rows if r.get("loop_id") == "lp-merge")
        assert row["stop_verdict"] == EXTERNAL_INTERRUPT
        assert "work preserved" in row["stop_evidence"]

    def test_posthoc_stamp_rejects_off_vocabulary(self, workspace):
        # Fail to unstamped, never a phantom value: a typo'd verdict would
        # silently drift past every string-matching consumer.
        from memory_ledger import (
            _outcomes_path,
            record_outcome,
            stamp_outcome_stop_verdict,
        )
        record_outcome("g", "stuck", "s", loop_id="lp-typo")
        assert stamp_outcome_stop_verdict("lp-typo", "out-of-buget") is False
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        row = next(r for r in rows if r.get("loop_id") == "lp-typo")
        assert "stop_verdict" not in row

    def test_ctx_stamp_drops_off_vocabulary(self):
        from loop_types import LoopContext
        ctx = LoopContext(loop_id="lp-1", goal="g")
        ctx.stamp_stop("out-of-buget", "typo'd literal")
        assert ctx.stop_verdict == ""
        # A later correct stamp still lands (the bad one didn't claim the field).
        ctx.stamp_stop(OUT_OF_BUDGET, "real cap")
        assert ctx.stop_verdict == OUT_OF_BUDGET


# ---------------------------------------------------------------------------
# Post-hoc stamps: handle demotion + director escalation close
# ---------------------------------------------------------------------------


class TestHandleDemotionStamp:
    def test_demotion_stamps_when_unset(self):
        from handle import _stamp_stop_on_demotion
        lr = SimpleNamespace(stop_verdict="", stop_evidence="", loop_id="")
        _stamp_stop_on_demotion(lr, LOST_THE_PLOT, "closure contradicts done")
        assert lr.stop_verdict == LOST_THE_PLOT
        assert lr.stop_evidence == "closure contradicts done"

    def test_demotion_defers_to_break_site_verdict(self):
        # Landing-synthesis out-of-budget must survive a later closure
        # demotion: the site closest to the stop evidence wins.
        from handle import _stamp_stop_on_demotion
        lr = SimpleNamespace(stop_verdict=OUT_OF_BUDGET,
                             stop_evidence="landing", loop_id="")
        _stamp_stop_on_demotion(lr, LOST_THE_PLOT, "closure contradicts done")
        assert lr.stop_verdict == OUT_OF_BUDGET
        assert lr.stop_evidence == "landing"


class TestDirectorCloseStamp:
    def test_close_overwrites_and_keeps_prior_visible(self, workspace):
        # The director's close is a later, better-informed judgment ending
        # the chain: it overwrites the run's mechanical cap verdict but keeps
        # it visible in evidence ([refines: ...]).
        from director import _stamp_close_stop_verdict
        from runs import create_run_dir
        rd = create_run_dir(
            "h00close1", prompt="g", lane="agenda", model="m",
            extra_metadata={"stop_verdict": OUT_OF_BUDGET,
                            "stop_evidence": "cap"})
        _stamp_close_stop_verdict("h00close1", depth=3, confidence=8,
                                  reasoning="partial result suffices")
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["stop_verdict"] == NOT_WORTH_IT
        assert "[refines: out-of-budget]" in meta["stop_evidence"]
        assert "partial result suffices" in meta["stop_evidence"]

    def test_close_with_no_loop_id_is_noop(self, workspace):
        from director import _stamp_close_stop_verdict
        _stamp_close_stop_verdict("", depth=1, confidence=9, reasoning="r")

    def test_close_evidence_reaches_ledger_row(self, workspace):
        # Review round 2026-07-27: the [refines: …] refinement context used to
        # survive only in metadata — a ledger-only consumer saw the typed
        # label without the evidence that made it trustworthy.
        from director import _stamp_close_stop_verdict
        from memory_ledger import _outcomes_path, record_outcome
        from runs import create_run_dir
        create_run_dir(
            "h00close2", prompt="g", lane="agenda", model="m",
            extra_metadata={"stop_verdict": OUT_OF_BUDGET,
                            "stop_evidence": "cap"})
        record_outcome("g", "stuck", "s", loop_id="h00close2")
        _stamp_close_stop_verdict("h00close2", depth=3, confidence=8,
                                  reasoning="partial result suffices")
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        row = next(r for r in rows if r.get("loop_id") == "h00close2")
        assert row["stop_verdict"] == NOT_WORTH_IT
        assert "[refines: out-of-budget]" in row["stop_evidence"]


# ---------------------------------------------------------------------------
# Consumers
# ---------------------------------------------------------------------------


class TestLearnability:
    def test_unverified_done_at_cap_is_not_learnable(self):
        # Landing synthesis flips status to "done" at the budget cap; the
        # verdict keeps the cap-hit visible so the row can't seed learning.
        from outcome_policy import is_learnable_outcome
        assert is_learnable_outcome({
            "success_class": "done-unverified",
            "stop_verdict": OUT_OF_BUDGET,
        }) is False
        assert is_learnable_outcome({
            "status": "done", "stop_verdict": OUT_OF_BUDGET,
        }) is False

    def test_verified_success_at_cap_stays_learnable(self):
        from outcome_policy import is_learnable_outcome
        assert is_learnable_outcome({
            "success_class": "success",
            "goal_achieved": True,
            "stop_verdict": OUT_OF_BUDGET,
        }) is True

    def test_plain_done_unverified_still_learnable(self):
        from outcome_policy import is_learnable_outcome
        assert is_learnable_outcome({"success_class": "done-unverified"}) is True

    def test_interrupted_row_is_not_learnable_unless_achieved(self):
        # Merge-failure re-stamp (review round 2026-07-27): the ledger row may
        # still read status="done" from before the merge blocks ran; the
        # post-hoc external-interrupt stamp must fail it closed as a seed.
        from outcome_policy import is_learnable_outcome
        assert is_learnable_outcome({
            "status": "done", "stop_verdict": EXTERNAL_INTERRUPT,
        }) is False
        assert is_learnable_outcome({
            "success_class": "success", "goal_achieved": True,
            "stop_verdict": EXTERNAL_INTERRUPT,
        }) is True


class TestRepeatGuard:
    def _attempt(self, status, *, stop_verdict="", goal_achieved=None):
        from recall import PriorAttempt
        from datetime import datetime, timezone
        return PriorAttempt(
            goal="g", handle_id="h", status=status,
            when=datetime.now(timezone.utc).isoformat(), match="exact",
            goal_achieved=goal_achieved, stop_verdict=stop_verdict)

    def test_external_interrupt_disarms_all_failing(self):
        from recall import RecallResult
        rr = RecallResult(thread=None, prior_attempts=[
            self._attempt("stuck", goal_achieved=False),
            self._attempt("interrupted", stop_verdict=EXTERNAL_INTERRUPT),
        ])
        signals = rr.dispatch_signals(window_minutes=60.0)
        assert signals["repeat_count"] == 2
        # The interrupted attempt is not goal-failure evidence — the goal
        # wasn't disproven, the process was cut down around it.
        assert signals["all_failing"] is False

    def test_judged_failure_still_arms_even_when_interrupted(self):
        from recall import RecallResult
        rr = RecallResult(thread=None, prior_attempts=[
            self._attempt("interrupted", stop_verdict=EXTERNAL_INTERRUPT,
                          goal_achieved=False),
        ])
        assert rr.dispatch_signals(window_minutes=60.0)["all_failing"] is True

    def test_interrupt_status_disarms_even_with_supported_verdict(self):
        # Decree two-channel shape (adversarial-review round 2026-07-27): a
        # run that stamped out-of-budget at its landing site and was THEN
        # operator-stopped keeps the supported verdict in the field while the
        # interrupt event lives in status — the guard must honor either
        # channel, not just the verdict spelling.
        from recall import RecallResult
        rr = RecallResult(thread=None, prior_attempts=[
            self._attempt("interrupted", stop_verdict=OUT_OF_BUDGET),
        ])
        assert rr.dispatch_signals(window_minutes=60.0)["all_failing"] is False


class TestStrategyWeight:
    def test_external_interrupt_scores_neutral(self):
        from strategy_evaluator import _outcome_weight
        cut_down = SimpleNamespace(status="stuck", goal_achieved=None,
                                   stop_verdict=EXTERNAL_INTERRUPT)
        assert _outcome_weight(cut_down) == 0.5

    def test_judged_verdict_beats_interrupt_neutrality(self):
        from strategy_evaluator import _outcome_weight
        judged = SimpleNamespace(status="stuck", goal_achieved=False,
                                 stop_verdict=EXTERNAL_INTERRUPT)
        assert _outcome_weight(judged) == 0.0

    def test_plain_stuck_still_scores_zero(self):
        from strategy_evaluator import _outcome_weight
        stuck = SimpleNamespace(status="stuck", goal_achieved=None,
                                stop_verdict="")
        assert _outcome_weight(stuck) == 0.0


class TestAttributionFilter:
    def _run_batch(self, monkeypatch, outcomes):
        import attribution
        analyzed = []

        def _capture(outcome, adapter=None):
            analyzed.append(outcome)
            return SimpleNamespace(failure_mode="test", failed_skill=None,
                                   confidence=0.5, outcome_id="", reasoning="")

        monkeypatch.setattr(attribution, "attribute_failure", _capture)
        attribution.attribute_batch(outcomes, adapter=None)
        return analyzed

    def test_interrupt_rows_excluded_from_failure_attribution(self, monkeypatch):
        analyzed = self._run_batch(monkeypatch, [
            {"status": "stuck", "stop_verdict": EXTERNAL_INTERRUPT},
            {"status": "stuck"},
        ])
        # The outage-killed run is not attributed; the real stuck run is.
        assert len(analyzed) == 1
        assert "stop_verdict" not in analyzed[0]

    def test_judged_failure_attributed_despite_interrupt(self, monkeypatch):
        analyzed = self._run_batch(monkeypatch, [
            {"status": "stuck", "stop_verdict": EXTERNAL_INTERRUPT,
             "goal_achieved": False},
        ])
        assert len(analyzed) == 1
