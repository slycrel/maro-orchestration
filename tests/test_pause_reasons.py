"""§13e (2026-07-31, decree 7afe8b3a): typed pause reasons — paused is a
run-lifecycle state with a WHY, orthogonal to the stop verdict. Error-class
(box-busy, writer-died, llm-unreachable, no-tokens, disk-full) or
operator-class (manual-intervention, awaiting-clarification). A paused run
"may or may not ever be finished" — so the reason is provenance about the
pause, never goal evidence.

Pins: the vocabulary (families disjoint, fallback map refuses to guess on
ambiguous statuses), the stamp rail (LoopContext.stamp_pause first-write-wins
and off-vocabulary-dropped, mirroring stamp_stop), the outcome-row carry
(empty omitted), the run-card forwarding (explicit stamp wins over
status-derived fallback), and the stranded sweep's post-hoc writer-died stamp.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stop_verdicts import (
    INTERRUPT_STATUSES,
    PAUSE_ERR_BUSY,
    PAUSE_ERR_DISK_FULL,
    PAUSE_ERR_LLM_UNREACHABLE,
    PAUSE_ERR_NO_TOKENS,
    PAUSE_ERR_WRITER_DIED,
    PAUSE_OP_CLARIFICATION,
    PAUSE_OP_MANUAL,
    PAUSE_REASON_BY_STATUS,
    PAUSE_REASONS_ERROR,
    PAUSE_REASONS_OPERATOR,
    PAUSED_STATUSES,
    VALID_PAUSE_REASONS,
    pause_family,
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
    def test_two_families_disjoint_and_complete(self):
        assert PAUSE_REASONS_OPERATOR == frozenset(
            (PAUSE_OP_MANUAL, PAUSE_OP_CLARIFICATION))
        assert PAUSE_REASONS_ERROR == frozenset(
            (PAUSE_ERR_BUSY, PAUSE_ERR_WRITER_DIED, PAUSE_ERR_LLM_UNREACHABLE,
             PAUSE_ERR_NO_TOKENS, PAUSE_ERR_DISK_FULL))
        assert not (PAUSE_REASONS_OPERATOR & PAUSE_REASONS_ERROR)
        assert VALID_PAUSE_REASONS == PAUSE_REASONS_OPERATOR | PAUSE_REASONS_ERROR

    def test_paused_statuses_is_the_interrupt_family(self):
        # §13e layered on the 2026-07-27 interrupt decree: same status family,
        # decree-named alias — not a rename (both decrees stay quotable).
        assert PAUSED_STATUSES is INTERRUPT_STATUSES

    def test_fallback_map_refuses_to_guess(self):
        # "interrupted" has many causes (kill switch, external stop, …);
        # deriving a reason from it would fabricate provenance. Only statuses
        # with one unambiguous cause appear in the map.
        assert "interrupted" not in PAUSE_REASON_BY_STATUS
        assert PAUSE_REASON_BY_STATUS == {
            "clarification_needed": PAUSE_OP_CLARIFICATION,
            "refused_busy": PAUSE_ERR_BUSY,
            "stranded": PAUSE_ERR_WRITER_DIED,
        }
        assert set(PAUSE_REASON_BY_STATUS) <= set(INTERRUPT_STATUSES)
        assert set(PAUSE_REASON_BY_STATUS.values()) <= VALID_PAUSE_REASONS

    def test_pause_family(self):
        assert pause_family(PAUSE_OP_MANUAL) == "operator"
        assert pause_family(PAUSE_ERR_DISK_FULL) == "error"
        assert pause_family("not-a-reason") == ""
        assert pause_family("") == ""


# ---------------------------------------------------------------------------
# Stamp rail: LoopContext → LoopResult → outcome row
# ---------------------------------------------------------------------------


class TestStampRail:
    def test_ctx_stamp_first_write_wins(self):
        from loop_types import LoopContext
        ctx = LoopContext(loop_id="lp-1", goal="g")
        ctx.stamp_pause(PAUSE_OP_MANUAL)
        ctx.stamp_pause(PAUSE_ERR_BUSY)
        assert ctx.pause_reason == PAUSE_OP_MANUAL

    def test_ctx_stamp_drops_off_vocabulary(self):
        from loop_types import LoopContext
        ctx = LoopContext(loop_id="lp-1", goal="g")
        ctx.stamp_pause("hdd-full")  # near-miss typo for disk-full
        assert ctx.pause_reason == ""
        ctx.stamp_pause(PAUSE_ERR_DISK_FULL)
        assert ctx.pause_reason == PAUSE_ERR_DISK_FULL

    def test_loop_result_defaults_empty(self):
        from loop_types import LoopResult
        lr = LoopResult(loop_id="lp", project="p", goal="g", status="done")
        assert lr.pause_reason == ""

    def test_record_outcome_row_carries_reason_and_omits_empty(self, workspace):
        from memory_ledger import _outcomes_path, record_outcome
        record_outcome("g1", "clarification_needed", "s", loop_id="lp-a",
                       pause_reason=PAUSE_OP_CLARIFICATION)
        record_outcome("g2", "done", "s", loop_id="lp-b")
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        by_loop = {r.get("loop_id"): r for r in rows}
        assert by_loop["lp-a"]["pause_reason"] == PAUSE_OP_CLARIFICATION
        assert "pause_reason" not in by_loop["lp-b"]

    def test_reflect_and_record_accepts_pause_reason(self, workspace):
        # loop_finalize passes pause_reason= on EVERY run; if the kwarg ever
        # regresses, the TypeError is swallowed by finalize's catch-all and
        # every run silently loses its learning data (found live 2026-07-31).
        from memory import reflect_and_record
        out = reflect_and_record(
            goal="g", status="refused_busy", result_summary="s",
            dry_run=True, pause_reason=PAUSE_ERR_BUSY)
        assert out.pause_reason == PAUSE_ERR_BUSY


# ---------------------------------------------------------------------------
# Run-card forwarding (run_curation.classify_outcome)
# ---------------------------------------------------------------------------


class TestRunCardForwarding:
    def _classify(self, meta):
        from run_curation import classify_outcome
        card = {}
        classify_outcome(Path("/nonexistent"), meta, card)
        return card

    def test_explicit_stamp_wins_over_fallback(self):
        card = self._classify({"status": "stranded",
                               "pause_reason": PAUSE_ERR_NO_TOKENS})
        assert card["pause_reason"] == PAUSE_ERR_NO_TOKENS
        assert card["pause_family"] == "error"

    def test_invalid_stamp_falls_back_to_status_map(self):
        card = self._classify({"status": "stranded",
                               "pause_reason": "power-loss??"})
        assert card["pause_reason"] == PAUSE_ERR_WRITER_DIED
        assert card["pause_family"] == "error"

    def test_prestamping_statuses_get_derived_reason(self):
        card = self._classify({"status": "clarification_needed"})
        assert card["pause_reason"] == PAUSE_OP_CLARIFICATION
        assert card["pause_family"] == "operator"
        card = self._classify({"status": "refused_busy"})
        assert card["pause_reason"] == PAUSE_ERR_BUSY

    def test_ambiguous_interrupted_stays_untyped(self):
        card = self._classify({"status": "interrupted"})
        assert card["success_class"] == "interrupted"
        assert "pause_reason" not in card
        assert "pause_family" not in card

    def test_paused_then_finished_keeps_the_record(self):
        # A run that paused for clarification, resumed, and finished keeps
        # its pause provenance — history, not a contradiction of "done".
        card = self._classify({"status": "done", "goal_achieved": True,
                               "pause_reason": PAUSE_OP_CLARIFICATION})
        assert card["success_class"] == "success"
        assert card["pause_reason"] == PAUSE_OP_CLARIFICATION
        assert card["pause_family"] == "operator"


# ---------------------------------------------------------------------------
# Stranded sweep post-hoc stamp (heartbeat)
# ---------------------------------------------------------------------------


@pytest.fixture
def runs_env(tmp_path, monkeypatch):
    import runs as runs_module
    monkeypatch.setattr(runs_module, "runs_root", lambda: tmp_path / "runs")
    (tmp_path / "runs").mkdir(exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Slice-1 adversarial-review fixes (2026-07-31)
# ---------------------------------------------------------------------------


class TestReviewFixes:
    def test_refusal_stamp_carries_pause_reason(self, runs_env, monkeypatch):
        # Review #1: the pre-start kill-switch refusal returns before normal
        # finalization and "interrupted" deliberately has no curation
        # fallback — metadata is the ONLY durable home for its typed reason.
        import runs as runs_module
        rd = runs_env / "runs" / "hkz-a"
        rd.mkdir(parents=True)
        monkeypatch.setattr(runs_module, "current_run_dir", lambda: rd)
        from loop_init import _stamp_refusal_verdict
        _stamp_refusal_verdict("external-interrupt", "kill switch active: x",
                               pause_reason=PAUSE_OP_MANUAL)
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["pause_reason"] == PAUSE_OP_MANUAL
        assert meta["stop_verdict"] == "external-interrupt"

    def test_finalize_empty_pause_preserves_stamped_history(
            self, runs_env, monkeypatch):
        # Review #2: a resumed run reuses the run dir the stranded sweep
        # stamped writer-died into; its fresh context has no pause_reason.
        # loop_finalize passes `result.pause_reason or None` — None must
        # preserve, not erase, the stamped history.
        import runs as runs_module
        rd = runs_env / "runs" / "hrz-a"
        rd.mkdir(parents=True)
        (rd / "metadata.json").write_text(json.dumps(
            {"handle_id": "hrz", "status": "stranded",
             "pause_reason": PAUSE_ERR_WRITER_DIED}))
        monkeypatch.setattr(runs_module, "current_run_dir", lambda: rd)
        runs_module.stamp_run_metadata({
            "stop_verdict": "goal-achieved",
            "stop_evidence": "resumed and finished",
            "pause_reason": "" or None,  # loop_finalize's exact expression
        })
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["pause_reason"] == PAUSE_ERR_WRITER_DIED
        assert meta["stop_verdict"] == "goal-achieved"

    def test_record_outcome_drops_off_vocabulary_reason(self, workspace):
        # Review #6: vocabulary holds at the ledger boundary too — an
        # off-vocab string must not persist while curation silently falls
        # back (stores disagreeing instead of rejecting at ingress).
        from memory_ledger import _outcomes_path, record_outcome
        record_outcome("g3", "stranded", "s", loop_id="lp-c",
                       pause_reason="hdd-full")
        rows = [json.loads(l) for l in
                _outcomes_path().read_text().splitlines() if l.strip()]
        row = next(r for r in rows if r.get("loop_id") == "lp-c")
        assert "pause_reason" not in row


def test_stranded_sweep_stamps_writer_died(runs_env):
    from heartbeat import _backfill_stranded_run_cards
    rd = runs_env / "runs" / "hpz-a"
    (rd / "build").mkdir(parents=True)
    started = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    (rd / "metadata.json").write_text(json.dumps(
        {"handle_id": "hpz", "status": None, "started_at": started,
         "ended_at": None, "pid": 999999999}))
    assert "hpz" in _backfill_stranded_run_cards()
    meta = json.loads((rd / "metadata.json").read_text())
    assert meta["status"] == "stranded"
    assert meta["pause_reason"] == PAUSE_ERR_WRITER_DIED
