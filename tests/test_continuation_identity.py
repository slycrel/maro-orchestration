"""Continuation identity (decree, Jeremy 2026-08-02 — BACKLOG LT arc).

A queued loop_continuation is one of two animals, discriminated by the
parent run's stamps:
- RESUME  (typed pause, no closure verdict): same run identity — parent run
  dir re-pinned, loop row appended, older same-handle outcome rows gain
  superseded_by markers (addendum, never overwrite).
- RESTART (judged parent, or ambiguity): new run identity through the full
  handle() front door, so the recall guard sees the retry and seeded context
  rides operator_context.

Before this seam existed, both shapes ran dirless (scoped_run_dir(None)) —
the EDGE-2 "continuation lane records nothing by construction" hole.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))


def _mk_parent(handle_id, *, pause_reason="", verdict_source="", status="stuck"):
    """Create a parent run dir with the stamps the discriminator reads."""
    import runs
    rd = runs.create_run_dir(handle_id, prompt="the original mission")
    meta_path = rd / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = status
    if pause_reason:
        meta["pause_reason"] = pause_reason
    if verdict_source:
        meta["goal_verdict_source"] = verdict_source
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    runs.index_run_dir(rd, meta)
    return rd


def _task(parent_handle, *, reason="CONTINUATION of: finish the mission"):
    return {
        "job_id": "task-test-cont",
        "lane": "agenda",
        "source": "loop_continuation",
        "reason": reason,
        "continuation_depth": 1,
        "origin": {"parent_handle_id": parent_handle, "source": "task_store"},
    }


class _FakeLoopResult:
    loop_id = "resumeloop1"
    status = "done"


class TestDiscriminator:
    def test_typed_pause_resumes_in_parent_run_dir(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        import runs
        from handle_queue import handle_task

        rd = _mk_parent("parenthd01", pause_reason="budget_exhausted",
                        status="interrupted")
        seen = {}

        def _fake_loop(goal, **kwargs):
            # The resume must run WITH the parent run dir pinned — this is
            # the EDGE-2 fix: artifact/record writers land somewhere.
            seen["pinned"] = runs.current_run_dir()
            seen["handle_id"] = kwargs.get("handle_id")
            return _FakeLoopResult()

        with patch("agent_loop.run_agent_loop", side_effect=_fake_loop):
            result = handle_task(_task("parenthd01"), dry_run=True)

        assert seen["pinned"] == rd, "resume must re-pin the parent run dir"
        assert seen["handle_id"] == "parenthd01", "resume keeps the identity"
        assert getattr(result, "loop_id", "") == "resumeloop1"
        # Identity bookkeeping: the new loop is on the ledger, run indexed.
        meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
        assert "resumeloop1" in (meta.get("loop_ids") or [])
        assert meta.get("status") == "done"
        # Drain-batch hygiene preserved: nothing leaks to the next task.
        assert runs.current_run_dir() is None

    def test_judged_parent_restarts_through_handle(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle_queue import handle_task
        import handle as handle_mod

        _mk_parent("parenthd02", verdict_source="closure", status="stuck")
        called = {}

        def _fake_handle(message, **kwargs):
            called["message"] = message
            called["kwargs"] = kwargs
            return "restarted"

        with patch.object(handle_mod, "handle", side_effect=_fake_handle):
            result = handle_task(_task("parenthd02"), dry_run=True)

        assert result == "restarted"
        assert called["kwargs"]["force_lane"] == "agenda"
        # The archaeology tie rides origin; seeded context rides
        # operator_context (provenance-labeled), never the goal text.
        assert called["kwargs"]["origin"].get("parent_handle_id") == "parenthd02"
        assert "finish the mission" in called["message"]

    def test_ambiguity_fails_toward_restart(self, monkeypatch, tmp_path):
        """No pause_reason, no verdict — could be either; restart is the
        safe default (spurious parent = archaeology noise; spurious resume
        corrupts a closed run's record)."""
        _setup(monkeypatch, tmp_path)
        from handle_queue import handle_task
        import handle as handle_mod

        _mk_parent("parenthd03", status="incomplete")  # no stamps either way
        called = {}
        with patch.object(handle_mod, "handle",
                          side_effect=lambda m, **k: called.setdefault("hit", True)):
            handle_task(_task("parenthd03"), dry_run=True)
        assert called.get("hit"), "ambiguous stamps must route to restart"

    def test_missing_parent_restarts(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from handle_queue import handle_task
        import handle as handle_mod

        called = {}
        with patch.object(handle_mod, "handle",
                          side_effect=lambda m, **k: called.setdefault("hit", True)):
            handle_task(_task("neverexisted"), dry_run=True)
        assert called.get("hit"), "unresolvable parent must route to restart"

    def test_paused_but_judged_parent_restarts(self, monkeypatch, tmp_path):
        """A pause_reason WITH a closure verdict means the run was judged
        after all — resuming would corrupt a closed record."""
        _setup(monkeypatch, tmp_path)
        from handle_queue import handle_task
        import handle as handle_mod

        _mk_parent("parenthd04", pause_reason="operator_pause",
                   verdict_source="closure")
        called = {}
        with patch.object(handle_mod, "handle",
                          side_effect=lambda m, **k: called.setdefault("hit", True)):
            handle_task(_task("parenthd04"), dry_run=True)
        assert called.get("hit")


class TestSupersededRows:
    def test_resume_supersedes_interrupted_segment_rows(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from memory_ledger import record_outcome, mark_outcomes_superseded

        # Segment 1 (interrupted) writes a row; the resume's final row lands
        # later; older same-handle rows get the addendum marker.
        record_outcome(goal="g", status="interrupted", summary="s1",
                       task_type="agenda", handle_id="hdX")
        record_outcome(goal="g", status="interrupted", summary="s2",
                       task_type="agenda", handle_id="hdX")
        record_outcome(goal="g", status="done", summary="final",
                       task_type="agenda", handle_id="hdX")
        record_outcome(goal="other", status="done", summary="foreign",
                       task_type="agenda", handle_id="hdY")

        n = mark_outcomes_superseded("hdX")
        assert n == 2

        rows = [json.loads(l) for l in
                (tmp_path / "memory" / "outcomes.jsonl").read_text().splitlines()
                if l.strip()]
        mine = [r for r in rows if r.get("handle_id") == "hdX"]
        final = mine[-1]
        assert not final.get("superseded_by"), "the authority row stays unmarked"
        for r in mine[:-1]:
            # Addendum, never overwrite: marker present, data intact.
            assert r["superseded_by"] == final["outcome_id"]
            assert r["status"] == "interrupted"
            assert r["summary"] in ("s1", "s2")
        foreign = [r for r in rows if r.get("handle_id") == "hdY"][0]
        assert not foreign.get("superseded_by"), "other identities untouched"

    def test_single_row_is_never_marked(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from memory_ledger import record_outcome, mark_outcomes_superseded
        record_outcome(goal="g", status="done", summary="only",
                       task_type="agenda", handle_id="hdZ")
        assert mark_outcomes_superseded("hdZ") == 0

    def test_idempotent_re_mark(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from memory_ledger import record_outcome, mark_outcomes_superseded
        record_outcome(goal="g", status="interrupted", summary="s1",
                       task_type="agenda", handle_id="hdW")
        record_outcome(goal="g", status="done", summary="final",
                       task_type="agenda", handle_id="hdW")
        assert mark_outcomes_superseded("hdW") == 1
        assert mark_outcomes_superseded("hdW") == 0, "already-marked rows stay"


class TestEnvironmentalPause:
    """§13e decree (Jeremy 2026-08-02): out-of-tokens/provider-down is a
    PAUSE; the chosen budget ceiling is a CONCLUSION. The mapping helper is
    the tested seam; the break site consumes it."""

    def test_mapping_covers_the_decree(self):
        from stop_verdicts import (
            pause_reason_for_error_class,
            PAUSE_ERR_NO_TOKENS, PAUSE_ERR_LLM_UNREACHABLE,
        )
        assert pause_reason_for_error_class("billing_actionable") == PAUSE_ERR_NO_TOKENS
        assert pause_reason_for_error_class("retry_at") == PAUSE_ERR_NO_TOKENS
        assert pause_reason_for_error_class("failover") == PAUSE_ERR_LLM_UNREACHABLE

    def test_non_environmental_classes_do_not_pause(self):
        from stop_verdicts import pause_reason_for_error_class
        # budget_runaway has its own STOP-verdict break; auth needs a human
        # (deliberate vocabulary gap); ordinary failures ride blocked/recovery.
        for cls in ("budget_runaway", "auth_actionable", "fatal",
                    "token_runaway", "retry_backoff", "", None):
            assert pause_reason_for_error_class(cls) == ""

    def test_mapped_reasons_are_valid_vocabulary(self):
        """The stamp site drops off-vocabulary reasons silently — every value
        this helper emits must be in VALID_PAUSE_REASONS or the decree's
        stamps evaporate."""
        from stop_verdicts import (
            pause_reason_for_error_class, VALID_PAUSE_REASONS)
        for cls in ("billing_actionable", "retry_at", "failover"):
            assert pause_reason_for_error_class(cls) in VALID_PAUSE_REASONS

    def test_paused_env_run_is_resumable_by_the_lane(self, monkeypatch, tmp_path):
        """End-to-end compose check: a run parked by the environmental pause
        is exactly what the continuation lane's resume test accepts."""
        _setup(monkeypatch, tmp_path)
        import runs
        from handle_queue import handle_task
        from stop_verdicts import PAUSE_ERR_NO_TOKENS

        rd = _mk_parent("parenthd05", pause_reason=PAUSE_ERR_NO_TOKENS,
                        status="interrupted")
        seen = {}

        def _fake_loop(goal, **kwargs):
            seen["pinned"] = runs.current_run_dir()
            return _FakeLoopResult()

        with patch("agent_loop.run_agent_loop", side_effect=_fake_loop):
            handle_task(_task("parenthd05"), dry_run=True)
        assert seen["pinned"] == rd, (
            "no-tokens pause must resume as the same run identity")
