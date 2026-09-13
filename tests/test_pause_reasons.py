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
        # + budget-decision (2026-08-02): extension ladder exhausted — two
        # one-run-budget extensions granted, third breach waits on the user.
        from stop_verdicts import PAUSE_OP_BUDGET_DECISION
        assert PAUSE_REASONS_OPERATOR == frozenset(
            (PAUSE_OP_MANUAL, PAUSE_OP_CLARIFICATION,
             PAUSE_OP_BUDGET_DECISION))
        # + container-auth-expired (2026-09-13): executor.container=require
        # refused because the auth volume's session is dead — a human
        # re-seeds, the breaker self-clears, the run resumes.
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        assert PAUSE_REASONS_ERROR == frozenset(
            (PAUSE_ERR_BUSY, PAUSE_ERR_WRITER_DIED, PAUSE_ERR_LLM_UNREACHABLE,
             PAUSE_ERR_NO_TOKENS, PAUSE_ERR_DISK_FULL, PAUSE_ERR_CONTAINER_AUTH))
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
        from stop_verdicts import PAUSE_OP_BUDGET_DECISION
        assert pause_family(PAUSE_OP_MANUAL) == "operator"
        assert pause_family(PAUSE_OP_BUDGET_DECISION) == "operator"
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


class TestContainerAuthPause:
    """require-lane refusal for a dead session maps to its own typed pause;
    the backend-auth gap (dead API key) stays deliberately unmapped."""

    def test_container_auth_error_class_maps_to_typed_pause(self):
        from stop_verdicts import (pause_reason_for_error_class,
                                   PAUSE_ERR_CONTAINER_AUTH, pause_family)
        assert pause_reason_for_error_class("container_auth") == PAUSE_ERR_CONTAINER_AUTH
        assert PAUSE_ERR_CONTAINER_AUTH == "container-auth-expired"
        assert pause_family(PAUSE_ERR_CONTAINER_AUTH) == "error"

    def test_backend_auth_stays_unmapped(self):
        from stop_verdicts import pause_reason_for_error_class
        assert pause_reason_for_error_class("auth_actionable") == ""


class TestContainerAuthPauseEndToEnd:
    """The literal path (2026-09-13): resolve_container_run raises
    ContainerAuthExpired inside the adapter → step_exec carries the
    container_auth error class → loop_execute stamps the typed pause and
    ends the run `interrupted` — no blocked-step churn against a session
    no retry can revive."""

    def test_step_exec_carries_the_container_auth_class(self, tmp_path):
        import container_exec as ce
        from step_exec import execute_step

        class _Raising:
            model_key = "test"
            backend = "subprocess"
            def complete(self, messages, **kwargs):
                raise ce.ContainerAuthExpired(
                    "executor.container=require but the container lane is "
                    "unavailable: container auth breaker tripped (OAuth session expired)")

        outcome = execute_step(
            goal="read the inbox", step_text="list the newest five", step_num=1,
            total_steps=1, completed_context=[], adapter=_Raising(), tools=[],
            project_dir=str(tmp_path))
        assert outcome["status"] == "blocked"
        assert outcome["error_class"] == "container_auth"
        assert "maro-claude-auth" in outcome["user_action"]

    def test_loop_pauses_typed_on_the_container_auth_class(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        import runs
        import loop_planning
        import loop_execute
        from agent_loop import run_agent_loop
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        monkeypatch.setattr(loop_planning, "_decompose",
                            lambda *a, **k: ["list the newest five", "count the inbox"])
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, **k: list(steps))
        executed = []

        def _worker(**kwargs):
            executed.append(kwargs["step_text"])
            return {"status": "blocked", "error_class": "container_auth",
                    "stuck_reason": "LLM call failed (container_auth): re-seed",
                    "user_action": "re-seed the maro-claude-auth volume",
                    "result": "[partial output before kill]\nlisted two", "tokens_in": 7, "tokens_out": 0}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)
        rd = runs.create_run_dir("cauth0001", prompt="read the inbox")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the inbox", dry_run=False, max_steps=3,
                                    handle_id="cauth0001")
        assert executed == ["list the newest five"], "no retry churn: one refusal, then pause"
        assert result.status == "interrupted"
        assert result.pause_reason == PAUSE_ERR_CONTAINER_AUTH
        meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
        assert meta.get("pause_reason") == PAUSE_ERR_CONTAINER_AUTH
        # Round 8: the refused step is a step this run paid for — recorded,
        # not dropped by the pause's early exit (steps=0 before).
        assert len(result.steps) == 1 and result.steps[0].status == "blocked"
        assert result.steps[0].tokens_in == 7 and "before kill" in result.steps[0].result

    def test_sequential_pause_persists_with_a_closed_stderr(self, monkeypatch, tmp_path):
        # Round 5: the sequential pause branch printed unguarded after the
        # in-memory stamp; a closed stderr escaped the loop before
        # finalization wrote the pause to metadata.
        import io, sys
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        fake = tmp_path / "claude"
        fake.write_text("#!/bin/sh\nexit 0\n")
        fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        import runs
        import loop_planning
        import loop_execute
        from agent_loop import run_agent_loop
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        monkeypatch.setattr(loop_planning, "_decompose", lambda *a, **k: ["list the newest five"])
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, **k: list(steps))
        class _Broken(io.TextIOBase):
            def write(self, s):
                raise BrokenPipeError("stderr closed")
            def flush(self):
                raise BrokenPipeError("stderr closed")
            def close(self):
                pass  # no flush at GC — the failure under test is the write
        def _worker(**kwargs):
            monkeypatch.setattr(sys, "stderr", _Broken())
            return {"status": "blocked", "error_class": "container_auth",
                    "stuck_reason": "LLM call failed (container_auth): re-seed",
                    "user_action": "re-seed the maro-claude-auth volume",
                    "result": "", "tokens_in": 0, "tokens_out": 0}
        monkeypatch.setattr(loop_execute, "_execute_step", _worker)
        rd = runs.create_run_dir("cauth0002", prompt="read the inbox")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("read the inbox", dry_run=False, max_steps=3,
                                    handle_id="cauth0002", verbose=True)
        assert result.status == "interrupted" and result.pause_reason == PAUSE_ERR_CONTAINER_AUTH
        meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
        assert meta.get("pause_reason") == PAUSE_ERR_CONTAINER_AUTH


class TestContainerAuthPauseThroughTheRealWrapper:
    """The LITERAL production composition (review 2026-09-13 HIGH): worker
    step → FailoverAdapter → ClaudeSubprocessAdapter → resolve_container_run
    raises ContainerAuthExpired → the wrapper wraps it (actionable) →
    step_exec classifies the WRAPPER → the class must survive → the loop's
    seam maps it to the typed pause. No fallback backend runs, no circuit
    trips, no host `/login` alert is emitted."""

    def _arm(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import container_exec as ce
        import llm
        monkeypatch.setattr(ce, "get", lambda k, d=None: "require" if k == "executor.container" else d)
        monkeypatch.setattr(ce, "docker_probe", lambda: (True, "docker 24"))
        monkeypatch.setattr(ce, "auth_breaker_blocks", lambda: "Failed to authenticate: OAuth token expired")
        monkeypatch.setattr(ce, "container_suppressed", lambda: False)
        monkeypatch.setattr(llm, "_BACKEND_CIRCUIT", {})
        emitted = []
        import notify
        monkeypatch.setattr(notify, "emit", lambda et, payload, **kw: emitted.append((et, payload)) or True)
        return emitted

    def test_wrapped_refusal_keeps_the_container_auth_class(self, monkeypatch, tmp_path):
        emitted = self._arm(monkeypatch, tmp_path)
        import llm
        from llm import FailoverAdapter, ClaudeSubprocessAdapter
        from step_exec import execute_step
        from stop_verdicts import environmental_pause_for, PAUSE_ERR_CONTAINER_AUTH
        launched = []
        monkeypatch.setattr(llm.subprocess, "Popen",
                            lambda *a, **k: launched.append(a) or (_ for _ in ()).throw(AssertionError("no subprocess must launch")))
        adapter = FailoverAdapter([ClaudeSubprocessAdapter(model="claude-x", claude_bin="/bin/true")])
        outcome = execute_step(
            goal="read the inbox", step_text="list the newest five", step_num=1,
            total_steps=1, completed_context=[], adapter=adapter, tools=[],
            project_dir=str(tmp_path))
        assert outcome["status"] == "blocked"
        assert outcome["error_class"] == "container_auth", outcome
        assert environmental_pause_for(outcome) == PAUSE_ERR_CONTAINER_AUTH
        assert launched == [], "the refusal happens before any subprocess"
        assert llm._BACKEND_CIRCUIT == {}, "container-owned: the backend circuit must not trip"
        assert emitted == [], "the breaker owns the alert; no generic host /login alert"

    def test_a_backend_auth_failure_still_stays_terminal(self, monkeypatch, tmp_path):
        # Negative control: the deliberate vocabulary gap is untouched.
        from llm_errors import BackendError, ErrorInfo, AUTH_ACTIONABLE
        from stop_verdicts import environmental_pause_for
        from step_exec import execute_step

        class _Raising:
            model_key = "test"; backend = "anthropic"
            def complete(self, messages, **kwargs):
                raise BackendError(ErrorInfo(error_class=AUTH_ACTIONABLE, backend="anthropic",
                                             retryable=False, failover=True,
                                             user_action="fix the key", detail="401"))
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=_Raising(), tools=[],
                               project_dir=str(tmp_path))
        assert outcome["error_class"] == AUTH_ACTIONABLE and environmental_pause_for(outcome) == ""


class TestContainerAuthPauseOnParallelPaths:
    """Review 2026-09-13 HIGH: fan-out/DAG turned a blocked container_auth
    outcome into `stuck`; the batch path only logged it. Every path
    consults the one seam now."""

    _AUTH = {"status": "blocked", "error_class": "container_auth",
             "stuck_reason": "LLM call failed (container_auth): re-seed", "result": "",
             "tokens_in": 0, "tokens_out": 0}
    _PLAIN = {"status": "blocked", "stuck_reason": "tool refused", "result": "",
              "tokens_in": 0, "tokens_out": 0}
    _DONE = {"status": "done", "result": "ok", "summary": "ok", "tokens_in": 1, "tokens_out": 1}

    def _ctx(self):
        import time
        from loop_types import LoopContext
        class _Ctx:
            goal = "g"; adapter = None; ancestry_context = ""; verbose = False
            project = ""; loop_id = "loop-t"; step_callback = None
            started_at = time.monotonic(); pause_reason = ""
            def stamp_pause(self, reason):
                LoopContext.stamp_pause(self, reason)
        return _Ctx()

    def _fanout(self, monkeypatch, outcomes, use_dag=False):
        import loop_parallel
        from loop_parallel import _run_parallel_path
        monkeypatch.setattr(loop_parallel, "_run_steps_parallel", lambda **kw: outcomes)
        monkeypatch.setattr(loop_parallel, "_run_steps_dag", lambda **kw: outcomes)
        monkeypatch.setattr(loop_parallel, "_drain_pending_context", lambda ctx: ("", ""))
        ctx = self._ctx()
        steps = [f"step {i}" for i in range(len(outcomes))]
        res = _run_parallel_path(ctx, steps, clean_steps=steps, deps={}, levels=[steps] if use_dag else None,
                                 parallel_levels=[], parallel_fan_out=2, proj_fanout_dir="",
                                 loop_shared_ctx={}, use_dag=use_dag, resolve_tools_fn=lambda: [])
        return ctx, res

    @pytest.mark.parametrize("use_dag", [False, True])
    def test_fanout_and_dag_pause_typed(self, monkeypatch, use_dag):
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        ctx, res = self._fanout(monkeypatch, [self._DONE, self._AUTH], use_dag=use_dag)
        assert res.status == "interrupted"
        assert res.pause_reason == PAUSE_ERR_CONTAINER_AUTH == ctx.pause_reason
        assert len(res.steps) == 2 and res.steps[0].status == "done"

    def test_a_plain_blocked_fanout_is_still_stuck(self, monkeypatch):
        ctx, res = self._fanout(monkeypatch, [self._DONE, self._PLAIN])
        assert res.status == "stuck" and res.pause_reason == "" and ctx.pause_reason == ""

    def test_a_pause_outranks_a_later_plain_block(self, monkeypatch):
        ctx, res = self._fanout(monkeypatch, [self._AUTH, self._PLAIN])
        assert res.status == "interrupted"

    def test_batch_member_stamps_the_pause(self, monkeypatch, tmp_path):
        import loop_parallel
        from loop_parallel import _run_parallel_batch
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        monkeypatch.setattr(loop_parallel, "_run_steps_parallel", lambda **kw: [self._DONE, self._AUTH])
        ctx = self._ctx()
        _run_parallel_batch(ctx, "lead", ["peer"], step_outcomes=[], completed_context=[],
                            remaining_steps=[], remaining_indices=[], loop_shared_ctx={},
                            resolve_tools_fn=lambda: [], parallel_fan_out=2, proj_artifact_dir="",
                            iteration=0, step_idx=0, batch_item_indices=None)
        assert ctx.pause_reason == PAUSE_ERR_CONTAINER_AUTH

    def test_the_loop_ends_interrupted_after_a_paused_batch(self, monkeypatch, tmp_path):
        # The batch stamps; the sequential driver must stop scheduling the
        # next step and end the run resumable.
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        fake = tmp_path / "claude"; fake.write_text("#!/bin/sh\nexit 0\n"); fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        import runs, loop_planning, loop_execute, loop_parallel
        from agent_loop import run_agent_loop
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        steps = ["a", "b", "c"]
        monkeypatch.setattr(loop_planning, "_decompose", lambda *a, **k: list(steps))
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda s, **k: list(s))
        # a and b are independent peers at one level; c depends on both
        monkeypatch.setattr(loop_planning, "_infer_step_dependencies",
                            lambda *a, **k: {1: [], 2: [], 3: [1, 2]}, raising=False)
        monkeypatch.setattr(loop_parallel, "_run_steps_parallel", lambda **kw: [self._DONE, self._AUTH])
        seq = []
        monkeypatch.setattr(loop_execute, "_execute_step", lambda **kw: seq.append(kw["step_text"]) or dict(self._DONE))
        rd = runs.create_run_dir("cauth0002", prompt="g")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("g", dry_run=False, max_steps=5, handle_id="cauth0002",
                                    parallel_fan_out=2)
        assert result.pause_reason == PAUSE_ERR_CONTAINER_AUTH, result
        assert result.status == "interrupted" and "c" not in seq


class TestToolSearchRecallRefusal:
    """Review round 2: the tool_search re-call's handler sits OUTSIDE the
    initial call's `except`, so re-raising the environmental error escaped
    execute_step (uncaught on the sequential driver, stringified by the
    fan-out pool). It must become the same typed blocked outcome, with the
    first call's spend kept on the step's books."""

    def test_refused_recall_is_a_typed_blocked_outcome(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import tool_search
        from llm import LLMResponse, ToolCall
        from container_exec import ContainerAuthExpired
        from step_exec import execute_step
        from stop_verdicts import environmental_pause_for, PAUSE_ERR_CONTAINER_AUTH

        class _Adapter:
            model_key = "t"; backend = "subprocess"; calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content="", input_tokens=7, output_tokens=3, cost_usd=0.01,
                                       tool_calls=[ToolCall(name="tool_search", arguments={"query": "mail"})])
                raise ContainerAuthExpired("executor.container=require but the container lane is unavailable")

        monkeypatch.setattr(tool_search, "resolve_deferred_tools",
                            lambda query, *a, **k: [{"name": "imap_read", "description": "d",
                                                     "input_schema": {"type": "object", "properties": {}}}])
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter, tools=[],
                               project_dir=str(tmp_path))
        assert adapter.calls == 2
        assert outcome["status"] == "blocked" and outcome["error_class"] == "container_auth", outcome
        assert environmental_pause_for(outcome) == PAUSE_ERR_CONTAINER_AUTH
        assert (outcome["tokens_in"], outcome["tokens_out"]) == (7, 3)
        assert outcome.get("provider_cost_usd") == pytest.approx(0.01)

    @pytest.mark.parametrize("make_exc, expect_class, extra_in, extra_cost", [
        (lambda: __import__("llm_errors").TokenRunawayError(100000, 50000, estimated_cost_usd=1.25),
         "token_runaway", 100000, 1.25),
        (lambda: __import__("llm_errors").BudgetRunawayError(9.0, 6.0), "budget_runaway", 0, 0.0),
    ])
    def test_a_runaway_recall_is_the_same_typed_outcome(self, monkeypatch, tmp_path,
                                                        make_exc, expect_class, extra_in, extra_cost):
        # Round 3: the token-brake re-raise sat in the same outside-the-
        # handler position and escaped; the cost circuit fell through and
        # lost its class. Both are terminal: same builder, spend summed.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import tool_search
        from llm import LLMResponse, ToolCall
        from step_exec import execute_step

        class _Adapter:
            model_key = "t"; backend = "subprocess"; calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content="", input_tokens=7, output_tokens=3, cost_usd=0.01,
                                       tool_calls=[ToolCall(name="tool_search", arguments={"query": "mail"})])
                raise make_exc()

        monkeypatch.setattr(tool_search, "resolve_deferred_tools",
                            lambda query, *a, **k: [{"name": "imap_read", "description": "d",
                                                     "input_schema": {"type": "object", "properties": {}}}])
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter, tools=[],
                               project_dir=str(tmp_path))
        assert adapter.calls == 2 and outcome["status"] == "blocked"
        assert outcome["error_class"] == expect_class, outcome
        assert outcome["tokens_in"] == 7 + extra_in and outcome["tokens_out"] == 3
        assert outcome.get("provider_cost_usd") == pytest.approx(0.01 + extra_cost)

    def test_a_successful_recall_keeps_the_first_calls_usage(self, monkeypatch, tmp_path):
        # Round 4: the re-call replaced `resp`; cost summed, tokens dropped.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import tool_search
        from llm import LLMResponse, ToolCall
        from step_exec import execute_step

        class _Adapter:
            model_key = "t"; backend = "subprocess"; calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content="", input_tokens=7, output_tokens=3, cost_usd=0.01,
                                       tool_calls=[ToolCall(name="tool_search", arguments={"query": "mail"})])
                return LLMResponse(content="", input_tokens=11, output_tokens=5, cost_usd=0.02,
                                   tool_calls=[ToolCall(name="complete_step",
                                                        arguments={"result": "five newest listed", "summary": "ok"})])

        monkeypatch.setattr(tool_search, "resolve_deferred_tools",
                            lambda query, *a, **k: [{"name": "imap_read", "description": "d",
                                                     "input_schema": {"type": "object", "properties": {}}}])
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter, tools=[],
                               project_dir=str(tmp_path))
        assert adapter.calls == 2 and outcome["status"] == "done", outcome
        assert (outcome["tokens_in"], outcome["tokens_out"]) == (18, 8)
        assert outcome.get("provider_cost_usd") == pytest.approx(0.03)

    def test_a_plain_recall_failure_is_typed_not_blamed_on_the_tool(self, monkeypatch, tmp_path):
        # Round 7: "fall through to the first response" could only ever
        # produce "unrecognised tool: tool_search"; the invoked call's own
        # failure is the diagnosis, with the first call's spend kept.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import tool_search
        from llm import LLMResponse, ToolCall
        from step_exec import execute_step

        class _Adapter:
            model_key = "t"; backend = "subprocess"; calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content="", input_tokens=7, output_tokens=3,
                                       tool_calls=[ToolCall(name="tool_search", arguments={"query": "mail"})])
                raise RuntimeError("flaky")

        monkeypatch.setattr(tool_search, "resolve_deferred_tools",
                            lambda query, *a, **k: [{"name": "imap_read", "description": "d",
                                                     "input_schema": {"type": "object", "properties": {}}}])
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter, tools=[],
                               project_dir=str(tmp_path))
        assert adapter.calls == 2 and outcome["status"] == "blocked"
        assert outcome.get("error_class") != "container_auth"
        assert "flaky" in outcome["stuck_reason"] and "unrecognised tool" not in outcome["stuck_reason"]
        assert (outcome["tokens_in"], outcome["tokens_out"]) == (7, 3)

    def test_the_recall_hands_the_adapter_real_tool_objects(self, monkeypatch, tmp_path):
        # Round 7: raw schema dicts were concatenated onto the LLMTool list;
        # every real adapter builds its prompt from `t.name`/`t.parameters`,
        # so EVERY production re-call died of AttributeError.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import tool_search
        from llm import LLMResponse, LLMTool, ToolCall
        from step_exec import execute_step
        seen = {}

        class _Adapter:
            model_key = "t"; backend = "subprocess"; calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content="", tool_calls=[ToolCall(name="tool_search", arguments={"query": "mail"})])
                # what the real subprocess/anthropic/openai prompt builders do
                seen["tools"] = [(t.name, t.description, t.parameters.get("properties", {})) for t in kwargs["tools"]]
                return LLMResponse(content="", tool_calls=[ToolCall(name="complete_step",
                                                                    arguments={"result": "r", "summary": "ok"})])

        monkeypatch.setattr(tool_search, "resolve_deferred_tools",
                            lambda query, *a, **k: [
                                {"name": "imap_read", "description": "d",
                                 "parameters": {"type": "object", "properties": {"folder": {"type": "string"}}}},
                                {"name": "old_shape", "description": "", "input_schema": {"type": "object", "properties": {}}}])
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter,
                               tools=[LLMTool(name="complete_step", description="c", parameters={"type": "object", "properties": {}})],
                               project_dir=str(tmp_path))
        assert adapter.calls == 2 and outcome["status"] == "done", outcome
        assert ("imap_read", "d", {"folder": {"type": "string"}}) in seen["tools"]
        assert ("old_shape", "", {}) in seen["tools"]
        # a schema without a name is a RESOLUTION failure: no second call
        monkeypatch.setattr(tool_search, "resolve_deferred_tools", lambda query, *a, **k: [{"description": "nameless"}])
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter, tools=[], project_dir=str(tmp_path))
        assert adapter.calls == 1 and outcome["status"] == "blocked"

    def test_the_first_call_advertises_tool_search_for_a_deferred_llmtool(self, monkeypatch, tmp_path):
        # Round 8: the injector read dict keys off LLMTool objects —
        # AttributeError, swallowed — so tool_search was never advertised
        # on the first call and the repaired re-call was unreachable
        # through the intended contract. Whole pipeline: stub → tool_search
        # advertised as an LLMTool → model calls it → re-call → done.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import tool_search
        from llm import LLMResponse, LLMTool, ToolCall
        from step_exec import execute_step
        seen = {}

        class _Adapter:
            model_key = "t"; backend = "subprocess"; calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                seen[self.calls] = [(t.name, t.parameters.get("properties", {})) for t in kwargs["tools"]]
                if self.calls == 1:
                    return LLMResponse(content="", tool_calls=[ToolCall(name="tool_search", arguments={"query": "imap"})])
                return LLMResponse(content="", tool_calls=[ToolCall(name="complete_step",
                                                                    arguments={"result": "r", "summary": "ok"})])

        monkeypatch.setattr(tool_search, "resolve_deferred_tools",
                            lambda query, *a, **k: [{"name": "imap_read", "description": "d",
                                                     "parameters": {"type": "object", "properties": {"folder": {"type": "string"}}}}])
        stub = LLMTool(name="imap_read", description="[deferred] read mail", parameters={"type": "object", "properties": {}})
        done = LLMTool(name="complete_step", description="c", parameters={"type": "object", "properties": {}})
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter, tools=[stub, done], project_dir=str(tmp_path))
        assert adapter.calls == 2 and outcome["status"] == "done", outcome
        names1 = [n for n, _ in seen[1]]
        assert "tool_search" in names1 and names1.count("tool_search") == 1
        assert ("imap_read", {"folder": {"type": "string"}}) in seen[2]
        # dict callers keep the dict shape; a list with no stub gets nothing
        out = tool_search.inject_tool_search_if_needed([{"name": "x", "description": "[deferred] y", "parameters": {"type": "object", "properties": {}}}])
        assert isinstance(out[-1], dict) and out[-1]["name"] == "tool_search"
        assert tool_search.inject_tool_search_if_needed([done]) == [done]
        assert tool_search.inject_tool_search_if_needed([stub, tool_search.inject_tool_search_if_needed([stub])[-1]])[-1].name == "tool_search"

    def test_a_killed_recall_keeps_its_partial_output(self, monkeypatch, tmp_path):
        # Round 7: the timeout class was outside the round-3 allow-list, so a
        # killed re-call fell through to "unrecognised tool" and the only
        # record of what it did before the kill was gone.
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        import subprocess
        import tool_search
        from llm import LLMResponse, ToolCall, _subprocess_timeout_error
        from step_exec import execute_step

        class _Adapter:
            model_key = "t"; backend = "subprocess"; calls = 0
            def complete(self, messages, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    return LLMResponse(content="", input_tokens=7, output_tokens=3,
                                       tool_calls=[ToolCall(name="tool_search", arguments={"query": "mail"})])
                raise _subprocess_timeout_error("claude", subprocess.TimeoutExpired(
                    cmd=["claude"], timeout=600, output="partial work already performed"), 600)

        monkeypatch.setattr(tool_search, "resolve_deferred_tools",
                            lambda query, *a, **k: [{"name": "imap_read", "description": "d",
                                                     "parameters": {"type": "object", "properties": {}}}])
        adapter = _Adapter()
        outcome = execute_step(goal="g", step_text="s", step_num=1, total_steps=1,
                               completed_context=[], adapter=adapter, tools=[], project_dir=str(tmp_path))
        assert adapter.calls == 2 and outcome["status"] == "blocked"
        assert "timed out" in outcome["stuck_reason"] and "unrecognised tool" not in outcome["stuck_reason"]
        assert "partial work already performed" in outcome["result"]
        assert (outcome["tokens_in"], outcome["tokens_out"]) == (7, 3)


class TestSchedulersStopOnEnvironmentalPause:
    """Review round 2: the REAL schedulers kept submitting after a refusal
    (the DAG released dependents regardless of outcome; fan-out queued
    everything upfront). One refusal now stops further submission; peers
    already running finish; unstarted steps are marked, not executed."""

    _AUTH = {"status": "blocked", "error_class": "container_auth",
             "stuck_reason": "LLM call failed (container_auth): re-seed", "result": "",
             "tokens_in": 0, "tokens_out": 0}
    _PLAIN = {"status": "blocked", "stuck_reason": "tool refused", "result": "",
              "tokens_in": 0, "tokens_out": 0}

    def _arm(self, monkeypatch, outcome):
        import loop_parallel
        executed = []
        def fake(**kw):
            executed.append(kw["step_num"])
            return dict(outcome)
        monkeypatch.setattr(loop_parallel, "_execute_step", fake)
        monkeypatch.setattr(loop_parallel, "_run_in_step_worktree", lambda label, fn: fn())
        return executed

    def test_dag_does_not_release_dependents_after_a_refusal(self, monkeypatch):
        import loop_parallel
        executed = self._arm(monkeypatch, self._AUTH)
        out = loop_parallel._run_steps_dag(goal="g", steps=["a", "b", "c"],
                                           deps={1: set(), 2: {1}, 3: {2}}, adapter=None,
                                           ancestry_context="", tools=[], verbose=False, max_workers=2)
        assert executed == [1]
        assert out[0]["error_class"] == "container_auth"
        assert all(o["status"] == "blocked" and o["stuck_reason"].startswith("not started") for o in out[1:])

    def test_dag_queued_roots_stop_before_the_coordinator_wakes(self, monkeypatch):
        # Round 3: three independent roots, one worker — the pool thread
        # picks root 2 before the coordinator consumes root 1's future, so
        # the halt must be set IN the worker.
        import loop_parallel
        executed = self._arm(monkeypatch, self._AUTH)
        out = loop_parallel._run_steps_dag(goal="g", steps=["a", "b", "c", "d"],
                                           deps={1: set(), 2: set(), 3: set(), 4: {1, 2, 3}}, adapter=None,
                                           ancestry_context="", tools=[], verbose=False, max_workers=1)
        assert executed == [1]
        assert len(out) == 4 and out[0]["error_class"] == "container_auth"
        assert all(o["stuck_reason"].startswith("not started") for o in out[1:])

    def test_dag_queued_roots_control_all_run_on_a_plain_block(self, monkeypatch):
        import loop_parallel
        executed = self._arm(monkeypatch, self._PLAIN)
        loop_parallel._run_steps_dag(goal="g", steps=["a", "b", "c", "d"],
                                     deps={1: set(), 2: set(), 3: set(), 4: {1, 2, 3}}, adapter=None,
                                     ancestry_context="", tools=[], verbose=False, max_workers=1)
        assert sorted(executed) == [1, 2, 3, 4]

    def test_dag_control_a_plain_block_still_releases(self, monkeypatch):
        import loop_parallel
        executed = self._arm(monkeypatch, self._PLAIN)
        loop_parallel._run_steps_dag(goal="g", steps=["a", "b", "c"], deps={1: set(), 2: {1}, 3: {2}},
                                     adapter=None, ancestry_context="", tools=[], verbose=False, max_workers=2)
        assert executed == [1, 2, 3]

    def test_fanout_cancels_queued_steps_after_a_refusal(self, monkeypatch):
        import loop_parallel
        executed = self._arm(monkeypatch, self._AUTH)
        out = loop_parallel._run_steps_parallel(goal="g", steps=["a", "b", "c"], adapter=None,
                                                ancestry_context="", tools=[], verbose=False, max_workers=1)
        assert executed == [1]
        assert len(out) == 3 and out[0]["error_class"] == "container_auth"
        assert all(o["stuck_reason"].startswith("not started") for o in out[1:])

    def test_fanout_refusal_after_the_deadline_keeps_its_outcome(self, monkeypatch):
        # Round 4: the timeout handler wrote synthetic rows and the real
        # outcomes that landed afterwards (with the halt) were discarded —
        # the operator was told "timeout" instead of the actual cause.
        import time as _t
        import loop_parallel
        monkeypatch.setenv("MARO_STEP_TIMEOUT", "1")
        executed = []
        def slow(**kw):
            executed.append(kw["step_num"])
            _t.sleep(1.3)
            return dict(self._AUTH, tokens_in=7)
        monkeypatch.setattr(loop_parallel, "_execute_step", slow)
        monkeypatch.setattr(loop_parallel, "_run_in_step_worktree", lambda label, fn: fn())
        out = loop_parallel._run_steps_parallel(goal="g", steps=["a", "b"], adapter=None,
                                                ancestry_context="", tools=[], verbose=False, max_workers=1)
        assert executed == [1]
        assert out[0]["error_class"] == "container_auth" and out[0]["tokens_in"] == 7
        assert out[1]["stuck_reason"].startswith("not started"), out[1]

    def test_dag_refusal_after_the_deadline_marks_the_queued_root_not_started(self, monkeypatch):
        # Round 5: the fan-out reconcile (round 4) had no DAG twin — the
        # queued root's early "not started" return was never committed, so
        # the coordinator's synthetic "dag timeout" row stood for a step
        # that never ran.
        import time as _t
        import loop_parallel
        monkeypatch.setenv("MARO_STEP_TIMEOUT", "1")
        executed = []
        def slow(**kw):
            executed.append(kw["step_num"])
            _t.sleep(1.3)
            return dict(self._AUTH, tokens_in=7)
        monkeypatch.setattr(loop_parallel, "_execute_step", slow)
        monkeypatch.setattr(loop_parallel, "_run_in_step_worktree", lambda label, fn: fn())
        out = loop_parallel._run_steps_dag(goal="g", steps=["a", "b"], deps={1: set(), 2: set()},
                                           adapter=None, ancestry_context="", tools=[],
                                           verbose=False, max_workers=1)
        assert executed == [1]
        assert out[0]["error_class"] == "container_auth" and out[0]["tokens_in"] == 7
        assert out[1]["stuck_reason"].startswith("not started"), out[1]

    @pytest.mark.parametrize("scheduler", ["fanout", "dag"])
    def test_environmental_pause_survives_a_broken_progress_stream(self, monkeypatch, scheduler):
        # Round 4: verbose printing ran BEFORE the halt/commit; a closed
        # stderr (BrokenPipeError) replaced the refusal with an execution
        # error and the DAG carried on.
        import io, sys
        import loop_parallel
        executed = self._arm(monkeypatch, self._AUTH)
        class _Broken(io.TextIOBase):
            def write(self, s):
                raise BrokenPipeError("stderr closed")
            def flush(self):
                raise BrokenPipeError("stderr closed")
        monkeypatch.setattr(sys, "stderr", _Broken())
        if scheduler == "dag":
            out = loop_parallel._run_steps_dag(goal="g", steps=["a", "b", "c"],
                                               deps={1: set(), 2: set(), 3: set()}, adapter=None,
                                               ancestry_context="", tools=[], verbose=True, max_workers=1)
        else:
            out = loop_parallel._run_steps_parallel(goal="g", steps=["a", "b", "c"], adapter=None,
                                                    ancestry_context="", tools=[], verbose=True, max_workers=1)
        assert executed == [1]
        assert out[0]["error_class"] == "container_auth"
        assert all(o["stuck_reason"].startswith("not started") for o in out[1:])

    def test_batch_stamps_the_pause_even_when_the_print_fails(self, monkeypatch):
        import io, sys
        import loop_parallel
        from loop_parallel import _run_parallel_batch
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        monkeypatch.setattr(loop_parallel, "_run_steps_parallel", lambda **kw: [self._AUTH])
        ctx = TestContainerAuthPauseOnParallelPaths()._ctx()
        ctx.verbose = True
        class _Broken(io.TextIOBase):
            def write(self, s):
                raise BrokenPipeError("stderr closed")
        monkeypatch.setattr(sys, "stderr", _Broken())
        _run_parallel_batch(ctx, "lead", [], step_outcomes=[], completed_context=[],
                            remaining_steps=[], remaining_indices=[], loop_shared_ctx={},
                            resolve_tools_fn=lambda: [], parallel_fan_out=2, proj_artifact_dir="",
                            iteration=0, step_idx=0, batch_item_indices=None)
        assert ctx.pause_reason == PAUSE_ERR_CONTAINER_AUTH

    def test_fanout_control_a_plain_block_runs_everything(self, monkeypatch):
        import loop_parallel
        executed = self._arm(monkeypatch, self._PLAIN)
        loop_parallel._run_steps_parallel(goal="g", steps=["a", "b", "c"], adapter=None,
                                          ancestry_context="", tools=[], verbose=False, max_workers=1)
        assert sorted(executed) == [1, 2, 3]


class TestParallelPausePersists:
    """Review round 2: the fan-out/DAG early return in agent_loop bypassed
    loop_finalize's stop-verdict stamp, and the continuation lane picks
    RESUME by reading metadata.pause_reason — so a paused parallel run
    restarted under a new identity."""

    def test_fanout_pause_reaches_metadata_and_passes_the_resume_test(self, monkeypatch, tmp_path):
        import json as _json
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        fake = tmp_path / "claude"; fake.write_text("#!/bin/sh\nexit 0\n"); fake.chmod(0o755)
        monkeypatch.setenv("CLAUDE_BIN", str(fake))
        import runs, loop_planning, agent_loop
        from agent_loop import run_agent_loop
        from loop_types import LoopResult
        from stop_verdicts import PAUSE_ERR_CONTAINER_AUTH
        steps = ["a", "b", "c"]
        monkeypatch.setattr(loop_planning, "_decompose", lambda *a, **k: list(steps))
        monkeypatch.setattr(loop_planning, "_shape_steps", lambda s, **k: list(s))
        monkeypatch.setattr(loop_planning, "_steps_are_independent", lambda s: True)
        seen = {}
        def fake_parallel(ctx, *a, **k):
            seen["called"] = True
            ctx.stamp_pause(PAUSE_ERR_CONTAINER_AUTH)
            return LoopResult(loop_id=ctx.loop_id, project=ctx.project, goal=ctx.goal,
                              status="interrupted", steps=[], total_tokens_in=0, total_tokens_out=0,
                              elapsed_ms=1, stuck_reason="environmental pause",
                              pause_reason=PAUSE_ERR_CONTAINER_AUTH)
        monkeypatch.setattr(agent_loop, "_run_parallel_path", fake_parallel)
        rd = runs.create_run_dir("cauth0003", prompt="g")
        with runs.scoped_run_dir(rd):
            result = run_agent_loop("g", dry_run=False, max_steps=5, handle_id="cauth0003",
                                    parallel_fan_out=2)
        assert seen.get("called"), "harness did not take the fan-out path"
        assert result.status == "interrupted" and result.pause_reason == PAUSE_ERR_CONTAINER_AUTH
        meta = _json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
        assert meta.get("pause_reason") == PAUSE_ERR_CONTAINER_AUTH
        # the continuation lane's strict-affirmative resume test (handle_queue)
        assert meta.get("pause_reason") and not meta.get("goal_verdict_source")
