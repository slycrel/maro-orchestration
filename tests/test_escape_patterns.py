"""Escape-pattern guards (BACKLOG #23a / #23g).

Corpus Family 3 (claims-without-execution) inside our own pipeline:

- async-escape (run 89cb097a): worker started a background Monitor and
  returned "it will notify me when the subprocess completes"; ralph RETRY'd
  it, but the generic fallback hint didn't say *synchronous*, so the retry
  repeated the async pattern and rode the 600s timeout twice.
- env-limitation (run 8a20665f step 3): worker burned $0.93 on a step
  premised on "the execution environment does not provide Read, Bash, or
  local file access" — false, never probed.

Guards under test: deterministic detectors + done→blocked demotion at the
complete_step seam (step_exec), and the targeted retry hints in
loop_blocked._handle_blocked_step.
"""

import pytest

from llm import LLMAdapter, LLMResponse, ToolCall
from step_exec import (
    ASYNC_ESCAPE_TAG,
    ENV_CLAIM_TAG,
    EXECUTE_SYSTEM,
    execute_step,
    result_claims_env_limitation,
    result_signals_async_escape,
)
from loop_blocked import _handle_blocked_step, _escape_pattern_hint


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

class TestAsyncEscapeDetector:
    def test_live_specimen_phrasing(self):
        # run 89cb097a, X-search step
        result = ("I've started a Monitor that will notify me when the "
                  "subprocess completes. The search is running.")
        assert result_signals_async_escape(result)

    def test_background_job_promise(self):
        result = ("Launched the sweep in the background; once it finishes "
                  "the results will land in artifacts/.")
        assert result_signals_async_escape(result)

    def test_will_be_notified(self):
        assert result_signals_async_escape(
            "The fetch is queued and I will be notified when it completes.")

    def test_waiting_as_end_state(self):
        assert result_signals_async_escape(
            "Now waiting for the subprocess to complete.")

    def test_results_available_once(self):
        assert result_signals_async_escape(
            "Results will be available once the job completes.")

    def test_past_tense_narration_passes(self):
        # Finished work narrated with "after waiting" is not a promise.
        result = ("After waiting for the tests to complete, all 142 passed. "
                  "Saved output to artifacts/test_run.txt.")
        assert not result_signals_async_escape(result)

    def test_legit_long_lived_server_result_passes(self):
        # The long-lived-process contract: spawn + probe + observed result.
        result = ("Spawned the server in the background (PID 4312). Probed "
                  "readiness: curl http://localhost:8080/health returned 200. "
                  "Server is listening; killed it after the check.")
        assert not result_signals_async_escape(result)

    def test_ordinary_result_passes(self):
        assert not result_signals_async_escape(
            "Read planner.py; decompose() routes narrow goals to single-shot.")


class TestEnvLimitationDetector:
    def test_live_specimen_phrasing(self):
        # run 8a20665f step 3
        result = ("Since the execution environment does not provide Read, "
                  "Bash, or local file access, I based this analysis on the "
                  "step description alone.")
        assert result_claims_env_limitation(result)

    def test_no_filesystem_access(self):
        assert result_claims_env_limitation(
            "I don't have access to the filesystem in this context.")

    def test_cannot_run_commands(self):
        assert result_claims_env_limitation(
            "I cannot execute commands here, so the test run is skipped.")

    def test_tools_not_available(self):
        assert result_claims_env_limitation(
            "The Bash tool is not available in this environment.")

    def test_ordinary_result_passes(self):
        assert not result_claims_env_limitation(
            "Fetched 13 posts; 4 mention the API change. Saved to artifacts/.")

    def test_gpu_style_claims_pass(self):
        # Only file/shell/tool-access claims — other limitations are legit.
        assert not result_claims_env_limitation(
            "The environment has no GPU, so inference ran on CPU.")


# ---------------------------------------------------------------------------
# Demotion at the complete_step seam
# ---------------------------------------------------------------------------

class _CompleteStepAdapter(LLMAdapter):
    """Returns complete_step with a fixed result text."""
    model_key = "x"

    def __init__(self, result_text, backend="subprocess"):
        self.backend = backend
        self._result_text = result_text

    def complete(self, messages, **kwargs):
        return LLMResponse(
            content="",
            model=self.model_key,
            input_tokens=10,
            output_tokens=10,
            tool_calls=[ToolCall(name="complete_step", arguments={
                "result": self._result_text,
                "summary": "did the thing",
            })],
        )


def _run(adapter):
    return execute_step(
        goal="g",
        step_text="run the X sweep and save results",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=[],
    )


class TestDemotion:
    def test_async_escape_demotes_done_to_blocked(self):
        outcome = _run(_CompleteStepAdapter(
            "Started a Monitor that will notify me when the subprocess "
            "completes."))
        assert outcome["status"] == "blocked"
        assert outcome["stuck_reason"].startswith(ASYNC_ESCAPE_TAG)

    def test_env_claim_demotes_on_agentic_lane(self):
        outcome = _run(_CompleteStepAdapter(
            "The execution environment does not provide Bash or file access, "
            "so I summarized from context.", backend="subprocess"))
        assert outcome["status"] == "blocked"
        assert outcome["stuck_reason"].startswith(ENV_CLAIM_TAG)

    def test_env_claim_passes_on_api_lane(self):
        # On a non-agentic lane the claim can be true — no demotion.
        outcome = _run(_CompleteStepAdapter(
            "The execution environment does not provide Bash or file access, "
            "so I summarized from context.", backend="anthropic"))
        assert outcome["status"] == "done"

    def test_env_claim_with_probe_evidence_passes(self):
        outcome = _run(_CompleteStepAdapter(
            "I ran `ls` to probe: command failed with 'not permitted', so "
            "this environment does not provide Bash or file access.",
            backend="subprocess"))
        assert outcome["status"] == "done"

    def test_clean_result_stays_done(self):
        outcome = _run(_CompleteStepAdapter(
            "Sweep finished: 160 tweets fetched, saved to artifacts/x.json."))
        assert outcome["status"] == "done"


# ---------------------------------------------------------------------------
# Targeted retry hints (loop_blocked)
# ---------------------------------------------------------------------------

class TestTargetedHints:
    def test_tagged_async_escape_gets_synchronous_hint(self):
        decision = _handle_blocked_step(
            step_text="run the X sweep",
            outcome={
                "status": "blocked",
                "stuck_reason": f"{ASYNC_ESCAPE_TAG} step returned a promise",
                "result": "Started a Monitor that will notify me when done.",
            },
            prior_retries=0,
            adapter=None,
        )
        assert decision.retry
        assert "SYNCHRONOUSLY" in decision.hint
        assert "background" in decision.hint

    def test_tagged_env_claim_gets_probe_hint(self):
        decision = _handle_blocked_step(
            step_text="read the report and extract findings",
            outcome={
                "status": "blocked",
                "stuck_reason": f"{ENV_CLAIM_TAG} unprobed claim",
                "result": "No file access here.",
            },
            prior_retries=0,
            adapter=None,
        )
        assert decision.retry
        assert "PROBE" in decision.hint
        assert "flag_stuck" in decision.hint

    def test_ralph_verify_fallback_detects_async_result(self):
        # Untagged ralph reason + async-shaped result → same targeted hint.
        hint = _escape_pattern_hint(
            "[ralph verify] result is a plan, not the work",
            "I started a background job and will be notified when it completes.")
        assert "SYNCHRONOUSLY" in hint

    def test_unrelated_block_gets_no_escape_hint(self):
        assert _escape_pattern_hint(
            "LLM call failed: connection reset",
            "partial work") == ""

    def test_exhausted_retries_fall_through(self):
        # At the retry threshold the targeted branch must not loop forever.
        decision = _handle_blocked_step(
            step_text="run the X sweep",
            outcome={
                "status": "blocked",
                "stuck_reason": f"{ASYNC_ESCAPE_TAG} step returned a promise",
                "result": "Started a Monitor.",
            },
            prior_retries=3,
            adapter=None,
        )
        assert decision.hint != _escape_pattern_hint(
            f"{ASYNC_ESCAPE_TAG} x", "") or not decision.retry


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------

class TestPromptContract:
    def test_execute_system_names_synchronous_rule(self):
        assert "SYNCHRONOUS EXECUTION" in EXECUTE_SYSTEM
        assert "background" in EXECUTE_SYSTEM

    def test_verify_prompts_name_promise_retry(self):
        from step_exec import _VERIFY_SYSTEM
        from verification_agent import _VERIFY_STEP_SYSTEM
        assert "PROMISES" in _VERIFY_SYSTEM
        assert "PROMISES" in _VERIFY_STEP_SYSTEM
