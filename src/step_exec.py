"""Step execution — LLM call, constraint check, response parsing.

Extracted from agent_loop.py for readability and targeted file reads.
The execute prompt, tool definitions, and per-step logic live here.

Usage:
    from step_exec import execute_step, generate_refinement_hint, EXECUTE_SYSTEM, EXECUTE_TOOLS
"""

from __future__ import annotations

import logging
import os
import re
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.loop")


# ---------------------------------------------------------------------------
# Execute system prompt
# ---------------------------------------------------------------------------

EXECUTE_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous execution agent.
    You are given a goal and a specific step to complete.
    Complete the step to the best of your ability, producing a concrete result.
    Then call exactly one tool:
      - complete_step: if you have successfully completed this step
      - flag_stuck: if you genuinely cannot complete this step (explain why precisely)
    Do NOT call flag_stuck for solvable problems — work through them first.
    Be thorough but concise. Output quality matters.

    URL FETCHING POLICY — IMPORTANT:
    All URL content has been pre-fetched and is provided in the PRE-FETCHED URL CONTENT
    block below. Use ONLY that pre-fetched content for any URLs mentioned in the step.
    Do NOT use Bash to curl/wget URLs. Do NOT use any tool to fetch URLs.
    If a URL's content is missing from the pre-fetch block, note it as unavailable and
    work with what you have — do not attempt to fetch it yourself.
    EXCEPTION: If the overall goal specifies a CLI tool or command for data access
    (e.g. a specific CLI, SDK, or installed tool), USE THAT TOOL as instructed.
    Goal-level tool instructions take priority over this URL policy.

    PRIOR STEP DATA — IMPORTANT:
    The "Completed steps so far" section contains summaries AND excerpts from prior
    step results. This is real data from actual execution — use it. When referencing
    files, modules, or findings from prior steps, cite the ACTUAL names and content
    shown in those excerpts. Do NOT invent or guess file names, function names, or
    line numbers. If a prior step found specific files, reference those exact names.
    If you need information not in the prior step data, say so explicitly rather
    than fabricating plausible-sounding references.

    TOKEN EFFICIENCY — CRITICAL:
    Your output is consumed by downstream agents and the orchestrator, not humans.
    Every extra token costs real money and slows the pipeline.
    1. Use only pre-fetched content and prior step data already in context.
    2. NEVER quote long passages verbatim. Extract the 2-3 key facts, discard the rest.
    3. Work with partial information rather than declaring stuck due to missing detail.
    4. Output format: short bullet points, structured data (JSON where appropriate),
       no preamble, no summaries of what you're about to do, no sign-offs.
    5. Never use a tool call, Bash command, or file read if the answer is already in context.
    6. Target under 500 tokens for your complete_step result. If you need more, something
       is wrong — you're probably quoting instead of summarizing.
    Pick the interpretation that requires the fewest tokens to produce a useful result.

    DATA PIPELINE STRATEGY — for steps that fetch external data:
    When a step requires fetching data from an API or CLI tool, NEVER dump raw output
    into context. Instead:
    1. Write a small script that fetches, filters, and summarizes the data to a file.
    2. Run the script. Read only the summary file (not the raw output).
    3. Pass the filtered summary to complete_step, not the raw data.
    Example: instead of running `polymarket-cli list` and processing 50KB of JSON
    in-context, write a script: fetch → filter top 10 by volume → extract key fields
    → save to project_dir/filtered_data.json → read and summarize that file.
    This turns a 100K-token step into a 5K-token step.
""").strip()

# ---------------------------------------------------------------------------
# Data pipeline enforcement helpers
# ---------------------------------------------------------------------------

# Keywords in step text that suggest the agent will touch external data sources
# and is at risk of dumping raw output into context.
_DATA_HEAVY_KEYWORDS = frozenset({
    "fetch all", "get all", "list all", "query all", "retrieve all",
    "polymarket-cli", "fetch data", "get data", "download data",
    "curl ", "wget ", "requests.get", "httpx.get", "enumerate all",
    "dump ", "raw output", "full response", "entire response",
    "all markets", "all events", "all trades", "all positions",
})

# Extra enforcement block injected into user_msg when a data-heavy step is detected.
_DATA_PIPELINE_EXTRA = textwrap.dedent("""\

    DATA PIPELINE ENFORCEMENT — this step touches external data:
    You MUST NOT return raw output. Write a filter script instead:
      1. Fetch → save raw output to a temp file
      2. Filter → extract only the needed fields (≤20 items max)
      3. Save the filtered result to {project_dir}/step_data.json
      4. Read the filtered file, summarize in ≤200 words
    Call complete_step with that summary. Raw JSON dumps will be flagged.
""").strip()

# Result length above which we check for raw-dump patterns.
_RAW_DUMP_CHAR_THRESHOLD = 2000


def _is_data_heavy_step(step_text: str) -> bool:
    """Return True if the step text suggests risk of raw-output dumping."""
    lower = step_text.lower()
    return any(kw in lower for kw in _DATA_HEAVY_KEYWORDS)


def _result_looks_like_raw_dump(result: str) -> bool:
    """Heuristic: detect if a complete_step result is raw API output.

    Checks two signals:
    - High brace density (JSON dump)
    - Many long lines (raw text output)
    """
    if len(result) < _RAW_DUMP_CHAR_THRESHOLD:
        return False
    brace_count = result.count("{") + result.count("}")
    if brace_count > 30:
        return True
    long_lines = sum(1 for line in result.splitlines() if len(line) > 300)
    return long_lines > 5


EXECUTE_TOOLS = [
    {
        "name": "complete_step",
        "description": "Mark this step as complete and record the result.",
        "parameters": {
            "type": "object",
            "properties": {
                "result": {
                    "type": "string",
                    "description": "The work product, findings, or output of this step.",
                },
                "summary": {
                    "type": "string",
                    "description": "One-sentence summary of what was accomplished.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["strong", "weak", "inferred", "unverified"],
                    "description": (
                        "How confident you are in this result. "
                        "strong = verified/cited; weak = partial/indirect; "
                        "inferred = reasoned from context; unverified = not independently confirmed."
                    ),
                },
            },
            "required": ["result", "summary"],
        },
    },
    {
        "name": "flag_stuck",
        "description": "Signal that this step cannot be completed, with a precise reason.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why this step cannot be completed.",
                },
                "attempted": {
                    "type": "string",
                    "description": "What was tried before giving up.",
                },
            },
            "required": ["reason"],
        },
    },
    {
        "name": "create_team_worker",
        "description": (
            "Dynamically create a specialist worker to handle a focused subtask. "
            "The worker runs immediately with its own LLM call and returns its result "
            "back to you, so you can use it when calling complete_step. "
            "Use this when you need a specific angle of analysis that warrants a "
            "dedicated specialist (e.g. risk-auditor, market-analyst, fact-checker, "
            "data-extractor, devil-advocate, synthesizer). "
            "Maximum 3 team workers per step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": (
                        "The specialist role (e.g. 'market-analyst', 'risk-auditor', "
                        "'fact-checker', 'data-extractor', 'devil-advocate', 'synthesizer'). "
                        "Free-form — unknown roles get a generic specialist persona."
                    ),
                },
                "task": {
                    "type": "string",
                    "description": "The specific focused task for this specialist.",
                },
                "persona": {
                    "type": "string",
                    "description": "Optional: custom system prompt override for this worker.",
                },
            },
            "required": ["role", "task"],
        },
    },
    {
        "name": "schedule_run",
        "description": (
            "Schedule a future autonomous run with a new goal. "
            "Use this to set up follow-up work: e.g. 'check this market again tomorrow', "
            "'re-analyze after data updates in 2 hours', or 'run daily digest at 08:00'. "
            "The scheduled run fires even if this process restarts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The goal or task to run at the scheduled time.",
                },
                "when": {
                    "type": "string",
                    "description": (
                        "When to run. Accepts: ISO datetime (e.g. '2026-04-02T09:00:00Z'), "
                        "'daily at HH:MM' (recurring daily), "
                        "'in N minutes' / 'in N hours' / 'in N days' (one-shot offset)."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "Optional context note for the scheduled run.",
                },
            },
            "required": ["goal", "when"],
        },
    },
]

# Role-specific subsets — reduces hallucinated calls and prompt noise.
# Worker (default): all tools including schedule_run and create_team_worker.
# Short-run: complete_step + flag_stuck only; no schedule_run or team workers.
# Inspector: flag_stuck only; inspector roles produce critiques, not completions.
EXECUTE_TOOLS_WORKER = EXECUTE_TOOLS
EXECUTE_TOOLS_SHORT = [t for t in EXECUTE_TOOLS if t["name"] not in {"schedule_run", "create_team_worker"}]
EXECUTE_TOOLS_INSPECTOR = [t for t in EXECUTE_TOOLS if t["name"] == "flag_stuck"]

_MAX_TEAM_WORKERS_PER_STEP = 3  # guard against runaway spawning


# ---------------------------------------------------------------------------
# Refinement hint (round 2 retry)
# ---------------------------------------------------------------------------

def generate_refinement_hint(
    step_text: str,
    block_reason: str,
    partial_result: str = "",
    *,
    adapter=None,
) -> str:
    """Generate a targeted refinement hint using a cheap LLM call.

    Called on the second retry of a blocked step. Uses the cheap model
    to analyze the specific failure and suggest a concrete patch.

    Falls back to a generic hint if the adapter is unavailable or errors.
    """
    _fallback = (
        f"[Refinement attempt 2 — blocked: {block_reason[:100]}] "
        "Analyze the failure carefully. Try a completely different approach: "
        "decompose this step further, use only information already available, "
        "or produce a partial result and mark the step complete."
    )

    if adapter is None:
        return _fallback

    try:
        from llm import LLMMessage, MODEL_CHEAP
        _refine_prompt = (
            f"A step in an autonomous agent loop failed twice.\n\n"
            f"Step: {step_text}\n"
            f"Failure reason: {block_reason[:200]}\n"
        )
        if partial_result:
            _refine_prompt += f"Partial result so far: {partial_result[:300]}\n"
        _refine_prompt += (
            "\nIn ONE sentence, suggest the most likely fix or alternative approach. "
            "Be specific and actionable. Do not suggest giving up."
        )

        # Use cheap model for refinement analysis
        try:
            from llm import build_adapter
            _cheap_adapter = build_adapter(model=MODEL_CHEAP)
        except Exception:
            _cheap_adapter = adapter

        resp = _cheap_adapter.complete(
            [LLMMessage("user", _refine_prompt)],
            max_tokens=150,
            temperature=0.3,
        )
        hint = resp.content.strip()
        if hint:
            return f"[Refinement suggestion: {hint}] Previous failure: {block_reason[:80]}"
    except Exception:
        pass

    return _fallback


# ---------------------------------------------------------------------------
# Schedule-run: when parsing
# ---------------------------------------------------------------------------

def _parse_when(when: str) -> Dict[str, Any]:
    """Parse a natural-language 'when' string into a scheduler schedule dict.

    Supports:
      - ISO datetime: '2026-04-02T09:00:00Z'  → once at that time
      - 'daily at HH:MM'                       → daily recurring
      - 'in N minutes' / 'in N hours' / 'in N days'  → once, offset from now
    """
    w = when.strip().lower()

    # "daily at HH:MM"
    m = re.match(r"daily\s+at\s+(\d{1,2}:\d{2})", w)
    if m:
        return {"type": "daily", "time": m.group(1)}

    # "in N minutes / hours / days"
    m = re.match(r"in\s+(\d+)\s+(minute|minutes|hour|hours|day|days)", w)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        now = datetime.now(timezone.utc)
        if "day" in unit:
            dt = now + timedelta(days=n)
        elif "hour" in unit:
            dt = now + timedelta(hours=n)
        else:
            dt = now + timedelta(minutes=n)
        return {"type": "once", "at": dt.isoformat()}

    # ISO datetime or other datetime-like string — try once
    # Normalize: support both 'Z' and '+00:00'
    _iso = when.strip().replace("Z", "+00:00")
    try:
        datetime.fromisoformat(_iso)
        return {"type": "once", "at": _iso}
    except ValueError:
        pass

    # Fallback: run once 1 hour from now
    log.warning("schedule_run: could not parse 'when' %r — defaulting to 1 hour from now", when)
    _dt = datetime.now(timezone.utc) + timedelta(hours=1)
    return {"type": "once", "at": _dt.isoformat()}


# ---------------------------------------------------------------------------
# Step execution
# ---------------------------------------------------------------------------

def execute_step(
    goal: str,
    step_text: str,
    step_num: int,
    total_steps: int,
    completed_context: List[str],
    adapter,
    tools: List[Any],
    verbose: bool = False,
    ancestry_context: str = "",
    project_dir: str = "",
) -> Dict[str, Any]:
    """Execute one step via the LLM. Returns outcome dict."""
    _step_t0 = time.monotonic()
    log.info("step_start %d/%d: %s", step_num, total_steps, step_text[:100])
    from llm import LLMMessage

    context_block = ""
    if completed_context:
        context_block = "\n\nCompleted steps so far:\n" + "\n".join(
            f"  - {c}" for c in completed_context
        )

    ancestry_block = f"\n\n{ancestry_context}" if ancestry_context else ""

    # Phase 35 P1/P2: HITL constraint check — block/warn before any LLM call
    try:
        from constraint import hitl_policy, ACTION_TIER_DESTROY, ACTION_TIER_EXTERNAL, ACTION_TIER_WRITE
        _hp = hitl_policy(step_text, goal=goal)
        log.debug("step %d constraint: tier=%s risk=%s allowed=%s",
                  step_num, _hp["tier"], _hp["risk_level"], _hp["allowed"])
        if not _hp["allowed"]:
            _block_detail = _hp["reason"] or f"tier={_hp['tier']}"
            log.warning("step %d BLOCKED by constraint: %s (tier=%s risk=%s) elapsed=%.1fs",
                        step_num, _block_detail, _hp["tier"], _hp["risk_level"],
                        time.monotonic() - _step_t0)
            return {
                "status": "blocked",
                "stuck_reason": f"constraint violation ({_hp['risk_level']}, tier={_hp['tier']}): {_block_detail}",
                "result": "",
                "tokens_in": 0,
                "tokens_out": 0,
            }
        _tier = _hp["tier"]
        if _tier == ACTION_TIER_EXTERNAL:
            print(
                f"[poe] HITL confirm: step {step_num} is EXTERNAL (gate=confirm) — proceeding autonomously",
                file=sys.stderr, flush=True,
            )
        elif _tier == ACTION_TIER_WRITE and verbose:
            print(f"[poe] HITL warn: step {step_num} is WRITE tier", file=sys.stderr, flush=True)
        elif _hp["risk_level"] == "MEDIUM" and verbose:
            print(f"[poe] constraint MEDIUM on step {step_num}: {_hp['reason']}", file=sys.stderr, flush=True)
    except Exception:
        pass  # constraint module optional

    # Pre-fetch URLs
    prefetch_block = ""
    try:
        from web_fetch import enrich_step_with_urls
        _prior_ctx = "\n".join(completed_context) if completed_context else ""
        prefetch_block = enrich_step_with_urls(step_text, extra_context=_prior_ctx)
        if prefetch_block:
            prefetch_block = "\n\n" + prefetch_block
    except Exception:
        pass

    workspace_block = ""
    if project_dir:
        workspace_block = (
            f"\n\nWORKSPACE: Save all output files to {project_dir}/ (not /tmp)."
            f" This directory exists and is where artifacts persist across steps."
        )

    # Pre-check: detect data-heavy steps and inject stronger enforcement
    _data_heavy = _is_data_heavy_step(step_text)
    _pipeline_block = ""
    if _data_heavy:
        _pipeline_block = "\n\n" + _DATA_PIPELINE_EXTRA.format(
            project_dir=project_dir or "/tmp/poe_step"
        )
        log.debug("step %d data_heavy=True — injecting pipeline enforcement", step_num)

    user_msg = (
        f"Overall goal: {goal}{ancestry_block}\n\n"
        f"Current step ({step_num}/{total_steps}): {step_text}"
        f"{workspace_block}"
        f"{context_block}"
        f"{prefetch_block}"
        f"{_pipeline_block}\n\n"
        f"Complete this step now. Call complete_step when done or flag_stuck if blocked."
    )

    # Detect steps that run external commands — give them a longer timeout
    _long_running_keywords = {"pytest", "test suite", "npm run", "make ", "docker ", "pip install",
                              "git clone", "build ", "compile", "deploy"}
    _step_lower = step_text.lower()
    _is_long_running = any(kw in _step_lower for kw in _long_running_keywords)
    _step_timeout = 600 if _is_long_running else None

    log.debug("step %d adapter_call start adapter=%s long_running=%s", step_num, type(adapter).__name__, _is_long_running)
    _llm_t0 = time.monotonic()
    try:
        _call_kwargs: Dict[str, Any] = dict(
            tools=tools,
            tool_choice="required",
            max_tokens=4096,
            temperature=0.3,
        )
        if _step_timeout is not None:
            _call_kwargs["timeout"] = _step_timeout
        resp = adapter.complete(
            [
                LLMMessage("system", EXECUTE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            **_call_kwargs,
        )
    except Exception as exc:
        _elapsed = time.monotonic() - _step_t0
        log.warning("step %d adapter_error: %s elapsed=%.1fs", step_num, exc, _elapsed)
        return {
            "status": "blocked",
            "stuck_reason": f"LLM call failed: {exc}",
            "result": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }

    _llm_elapsed = time.monotonic() - _llm_t0
    _tok = resp.input_tokens + resp.output_tokens
    _has_tool = bool(resp.tool_calls)
    _content_len = len(resp.content) if resp.content else 0
    log.debug("step %d adapter_done: llm=%.1fs tokens=%d tool_calls=%s content_len=%d",
              step_num, _llm_elapsed, _tok, _has_tool, _content_len)

    # Parse tool call
    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc.name == "complete_step":
            _confidence = tc.arguments.get("confidence", "") or ""
            _result_text = tc.arguments.get("result", resp.content)
            # Post-check: flag raw dumps so caller can act on it
            if _result_looks_like_raw_dump(_result_text):
                log.warning("step %d RAW_DUMP_DETECTED result_len=%d — agent ignored pipeline enforcement",
                            step_num, len(_result_text))
                _result_text = "[RAW_OUTPUT_DETECTED] " + _result_text
            log.info("step %d DONE (complete_step) tokens=%d elapsed=%.1fs confidence=%s",
                     step_num, _tok, time.monotonic() - _step_t0, _confidence or "unset")
            _out: dict = {
                "status": "done",
                "result": _result_text,
                "summary": tc.arguments.get("summary", step_text),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
            if _confidence:
                _out["confidence"] = _confidence
            return _out
        elif tc.name == "flag_stuck":
            _reason = tc.arguments.get("reason", "unknown")
            log.info("step %d BLOCKED (flag_stuck) reason=%r tokens=%d elapsed=%.1fs",
                     step_num, _reason[:80], _tok, time.monotonic() - _step_t0)
            return {
                "status": "blocked",
                "stuck_reason": _reason,
                "result": tc.arguments.get("attempted", ""),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
        elif tc.name == "create_team_worker":
            _tw_role = tc.arguments.get("role", "general")
            _tw_task = tc.arguments.get("task", "")
            _tw_persona = tc.arguments.get("persona") or None
            _tw_result_text = f"[team-worker error: no output]"
            try:
                from team import create_team_worker as _create_worker, format_team_result_for_injection
                _tw_res = _create_worker(
                    _tw_role,
                    _tw_task,
                    persona=_tw_persona,
                    adapter=adapter,
                )
                _tw_result_text = format_team_result_for_injection(_tw_res)
            except Exception as _tw_exc:
                _tw_result_text = f"[team-worker failed: {_tw_exc}]"
                log.warning("step %d create_team_worker failed role=%r: %s", step_num, _tw_role, _tw_exc)
            log.info("step %d DONE (create_team_worker) role=%r tokens=%d elapsed=%.1fs",
                     step_num, _tw_role, _tok, time.monotonic() - _step_t0)
            return {
                "status": "done",
                "result": _tw_result_text,
                "summary": f"Team worker [{_tw_role}]: {_tw_task[:60]}",
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
        elif tc.name == "schedule_run":
            _sched_goal = tc.arguments.get("goal", "")
            _sched_when = tc.arguments.get("when", "in 1 hour")
            _sched_note = tc.arguments.get("note", "")
            _sched_result = f"[no action — scheduler unavailable]"
            try:
                from scheduler import add_job
                _schedule = _parse_when(_sched_when)
                _job = add_job(_sched_goal, _schedule)
                _sched_result = (
                    f"Scheduled: '{_sched_goal[:80]}' "
                    f"(job_id={_job['job_id']}, next_run={_job['next_run'][:16]})"
                )
                if _sched_note:
                    _sched_result += f" — {_sched_note}"
            except Exception as _exc:
                _sched_result = f"[schedule_run failed: {_exc}]"
            log.info("step %d DONE (schedule_run) job=%r tokens=%d elapsed=%.1fs",
                     step_num, _sched_goal[:60], _tok, time.monotonic() - _step_t0)
            return {
                "status": "done",
                "result": _sched_result,
                "summary": f"Scheduled follow-up: {_sched_goal[:60]}",
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }

    # No tool call — treat content as result (some models don't always call tools)
    if resp.content and len(resp.content) > 20:
        log.info("step %d DONE (content fallback, %d chars) tokens=%d elapsed=%.1fs",
                 step_num, _content_len, _tok, time.monotonic() - _step_t0)
        return {
            "status": "done",
            "result": resp.content,
            "summary": step_text,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
        }

    log.warning("step %d BLOCKED (no tool call, content=%d chars) tokens=%d elapsed=%.1fs content=%r",
                step_num, _content_len, _tok, time.monotonic() - _step_t0,
                (resp.content or "")[:120])
    return {
        "status": "blocked",
        "stuck_reason": "LLM did not call a tool and produced no useful content",
        "result": resp.content,
        "tokens_in": resp.input_tokens,
        "tokens_out": resp.output_tokens,
    }


# ---------------------------------------------------------------------------
# Ralph verify loop — per-step verification
# ---------------------------------------------------------------------------

_VERIFY_SYSTEM = textwrap.dedent("""\
    You are a step verifier. A step in a larger task just completed.
    Your job: did the result actually accomplish what the step asked for?

    PASS: the result directly addresses the step goal with specific content.
    RETRY: the result is vague, off-topic, incomplete, or mostly a plan for doing
           the work rather than the work itself.

    Respond with JSON only:
    {"verdict": "PASS" or "RETRY", "reason": "one sentence", "confidence": 0.0-1.0}

    Be strict but fair. RETRY only if the result genuinely failed the step goal.
    Do not retry steps that are complete but imperfect.
""").strip()


def verify_step(
    step_text: str,
    result: str,
    adapter,
    *,
    confidence_threshold: float = 0.75,
) -> dict:
    """Verify that a completed step result actually addressed the step goal.

    Delegates to VerificationAgent for a named, composable implementation.
    Returns a dict with: passed, reason, confidence.
    Non-fatal — returns passed=True on any error so verify never blocks execution.
    """
    try:
        from verification_agent import VerificationAgent
        va = VerificationAgent(adapter, confidence_threshold=confidence_threshold)
        verdict = va.verify_step(step_text, result)
        return {"passed": verdict.passed, "reason": verdict.reason, "confidence": verdict.confidence}
    except ImportError:
        pass

    # Fallback: inline logic if verification_agent unavailable
    if not isinstance(result, str):
        result = str(result) if result else ""
    if not result.strip():
        return {"passed": False, "reason": "empty result", "confidence": 1.0}

    try:
        from llm import LLMMessage
        import json

        resp = adapter.complete(
            [
                LLMMessage("system", _VERIFY_SYSTEM),
                LLMMessage("user",
                    f"Step goal: {step_text}\n\n"
                    f"Step result (first 1200 chars):\n{result[:1200]}"
                ),
            ],
            max_tokens=128,
            temperature=0.1,
        )

        content = resp.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            verdict = data.get("verdict", "PASS").upper()
            reason = data.get("reason", "")
            confidence = float(data.get("confidence", 0.5))
            passed = verdict == "PASS" or confidence < confidence_threshold
            log.debug("verify_step verdict=%s confidence=%.2f passed=%s reason=%r",
                      verdict, confidence, passed, reason[:80])
            return {"passed": passed, "reason": reason, "confidence": confidence}

    except Exception as exc:
        log.debug("verify_step failed (non-fatal): %s", exc)

    return {"passed": True, "reason": "verify skipped (error)", "confidence": 0.0}
