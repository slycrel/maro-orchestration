"""Runaway cost circuit (BACKLOG #23e): mid-step spend gate at the adapter seam.

The between-step cost breaker can't see a single step burning multiples of
the run ceiling (run 8a20665f: step 9 alone = $2.04 past a $2.40 budget).
The circuit meters every FailoverAdapter call and refuses the NEXT call
pre-call once spend crosses budget.runaway_multiplier x cost_budget.
Runaway-only by decree (Jeremy 2026-07-11): it must never churn-kill
legitimate long work under budget, never retry, never failover.
"""

import pytest

from llm import (
    FailoverAdapter,
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    arm_cost_meter,
    cost_meter_state,
)
from llm_errors import (
    BUDGET_RUNAWAY,
    BudgetRunawayError,
    classify_error,
    is_actionable,
)


class _FakeAdapter(LLMAdapter):
    backend = "fake"
    model_key = "claude-sonnet-4-6"

    def __init__(self, tokens_in=100_000, tokens_out=100_000):
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        return LLMResponse(
            content="ok",
            model=self.model_key,
            input_tokens=self.tokens_in,
            output_tokens=self.tokens_out,
        )


MSGS = [LLMMessage("user", "hi")]


class TestClassification:
    def test_budget_runaway_class(self):
        info = classify_error(BudgetRunawayError(4.26, 3.60))
        assert info.error_class == BUDGET_RUNAWAY
        assert info.retryable is False
        assert info.failover is False

    def test_not_actionable_channelwise(self):
        # The exception message already says what to do; it must not join the
        # auth/billing escalation surfaces.
        info = classify_error(BudgetRunawayError(1.0, 0.5))
        assert not is_actionable(info)

    def test_message_names_the_knobs(self):
        exc = BudgetRunawayError(4.26, 3.60)
        assert "runaway_multiplier" in str(exc)
        assert "$4.26" in str(exc)
        assert "$3.60" in str(exc)

    def test_text_matching_cannot_misfile_it(self):
        # The message contains "budget"/"cost" words; the isinstance check
        # must outrank every substring pattern (billing says "budget"? no —
        # but guard the invariant anyway).
        info = classify_error(BudgetRunawayError(2.0, 1.0))
        assert info.error_class == BUDGET_RUNAWAY


class TestMeterLifecycle:
    def test_disarmed_by_default(self):
        assert cost_meter_state() is None

    def test_arm_snapshot_disarm(self):
        disarm = arm_cost_meter(3.0)
        try:
            state = cost_meter_state()
            assert state == {"spent_usd": 0.0, "ceiling_usd": 3.0}
        finally:
            disarm()
        assert cost_meter_state() is None

    def test_accrual_on_successful_call(self):
        fake = _FakeAdapter()
        adapter = FailoverAdapter([fake])
        disarm = arm_cost_meter(1000.0)  # ceiling far away — accrue only
        try:
            adapter.complete(MSGS)
            state = cost_meter_state()
            assert state["spent_usd"] > 0
        finally:
            disarm()

    def test_no_accrual_when_disarmed(self):
        fake = _FakeAdapter()
        adapter = FailoverAdapter([fake])
        adapter.complete(MSGS)  # must not raise, must not create a meter
        assert cost_meter_state() is None


class TestCircuit:
    def test_refuses_next_call_after_ceiling_crossed(self):
        fake = _FakeAdapter()  # 200k tokens/call — far past a $0.000001 ceiling
        adapter = FailoverAdapter([fake])
        disarm = arm_cost_meter(0.000001)
        try:
            adapter.complete(MSGS)  # first call goes through (pre-call spend = 0)
            assert fake.calls == 1
            with pytest.raises(BudgetRunawayError):
                adapter.complete(MSGS)
            # Refusal is pre-call: no backend was tried.
            assert fake.calls == 1
        finally:
            disarm()

    def test_under_ceiling_never_fires(self):
        fake = _FakeAdapter(tokens_in=10, tokens_out=10)
        adapter = FailoverAdapter([fake])
        disarm = arm_cost_meter(100.0)
        try:
            for _ in range(5):
                adapter.complete(MSGS)
            assert fake.calls == 5
        finally:
            disarm()

    def test_zero_ceiling_means_disabled(self):
        # arm_cost_meter(0) can result from cost_budget * multiplier edge
        # cases; a non-positive ceiling must never refuse calls.
        fake = _FakeAdapter()
        adapter = FailoverAdapter([fake])
        disarm = arm_cost_meter(0.0)
        try:
            adapter.complete(MSGS)
            adapter.complete(MSGS)
            assert fake.calls == 2
        finally:
            disarm()

    def test_step_exec_converts_to_blocked_outcome(self):
        # step_exec's adapter-error catch must carry error_class through so
        # the loop can break instead of churning through remaining steps.
        from step_exec import execute_step

        class _RunawayAdapter(LLMAdapter):
            backend = "fake"
            model_key = "x"

            def complete(self, messages, **kwargs):
                raise BudgetRunawayError(4.26, 3.60)

        outcome = execute_step(
            goal="g",
            step_text="do the thing",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=_RunawayAdapter(),
            tools=[],
        )
        assert outcome["status"] == "blocked"
        assert outcome.get("error_class") == BUDGET_RUNAWAY
        assert "runaway cost circuit" in outcome["stuck_reason"]
