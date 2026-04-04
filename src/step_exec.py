"""Step execution — LLM call, constraint check, response parsing.

Extracted from agent_loop.py for readability and targeted file reads.
The execute prompt, tool definitions, and per-step logic live here.

Usage:
    from step_exec import execute_step, generate_refinement_hint, EXECUTE_SYSTEM, EXECUTE_TOOLS
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.loop")

from llm_parse import extract_json, safe_float, safe_str, content_or_empty  # noqa: E402


# ---------------------------------------------------------------------------
# Execute system prompt
# ---------------------------------------------------------------------------

EXECUTE_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous execution agent.
    Complete the given step and call exactly one tool:
      - complete_step: successfully completed
      - flag_stuck: genuinely blocked (explain precisely)
    Do NOT flag_stuck for solvable problems — work through them first.

    URL FETCHING:
    Use ONLY the pre-fetched content in PRE-FETCHED URL CONTENT below.
    If a URL is missing from that block, note it as unavailable and proceed.
    EXCEPTION: Goal-level CLI/SDK/tool instructions override this — use those tools.

    PRIOR STEP DATA:
    "Completed steps so far" is real execution data. Reference ONLY the exact file
    names, function names, and values shown there — never invent or guess.

    TOKEN EFFICIENCY:
    1. Extract 2-3 key facts from sources; never quote long passages verbatim.
    2. Output: bullet points or structured JSON. No preamble, no sign-offs.
    3. Work with partial information rather than flagging stuck.
    4. Target under 500 tokens for complete_step. More = you're quoting, not summarizing.

    DATA PIPELINE (steps fetching external data):
    NEVER dump raw output into context. Instead:
    1. Write a filter script: fetch → filter to ≤20 items → save to {project_dir}/step_data.json
    2. Read the filtered file and summarize in ≤200 words.
    Call complete_step with that summary, not raw data.
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
    {
        "name": "register_tool",
        "description": (
            "Register a new reusable bash-backed tool that becomes available in "
            "subsequent steps. Use this when you need a custom capability that "
            "doesn't exist yet (e.g. a project-specific data filter, a CLI wrapper, "
            "a recurring transformation). The tool persists across sessions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Tool name — snake_case, lowercase alphanumeric + underscores only. "
                        "Example: 'filter_markets', 'fetch_price', 'parse_logs'."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "What the tool does (shown in system prompt for future steps).",
                },
                "bash_template": {
                    "type": "string",
                    "description": (
                        "Shell command with {placeholder} substitution. "
                        "Placeholders must match parameter names. "
                        "Example: 'jq {filter} {file}' — call with filter='.[]' file='data.json'."
                        "For a single freeform argument, use {args}: 'python3 script.py {args}'."
                    ),
                },
                "parameters_json": {
                    "type": "string",
                    "description": (
                        "Optional JSON Schema string for tool parameters. "
                        "If omitted, defaults to a single freeform 'args' string parameter. "
                        "Example: '{\"type\":\"object\",\"properties\":{\"filter\":{\"type\":\"string\"},\"file\":{\"type\":\"string\"}},\"required\":[\"filter\",\"file\"]}'"
                    ),
                },
            },
            "required": ["name", "description", "bash_template"],
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

# ---------------------------------------------------------------------------
# Registry-based tool access (Phase 41)
# ---------------------------------------------------------------------------
# Use get_tools_for_role() in new code instead of the imperative lists above.
# The imperative lists remain for backward compatibility.

try:
    from tool_registry import (  # noqa: E402
        registry as _tool_registry,
        PermissionContext,
        ROLE_WORKER, ROLE_SHORT, ROLE_INSPECTOR, ROLE_DIRECTOR, ROLE_VERIFIER,
        worker_context, short_context, inspector_context, director_context,
    )

    def get_tools_for_role(role: str, deny_patterns=None) -> list:
        """Return tool schema list for the given role, filtered by deny patterns.

        Preferred over the imperative EXECUTE_TOOLS_* lists for new code.
        Falls back to EXECUTE_TOOLS on any error.
        """
        ctx = PermissionContext(role=role, deny_patterns=deny_patterns or [])
        return _tool_registry.get_tool_schemas(ctx)

except ImportError:
    # Graceful fallback if tool_registry is unavailable
    def get_tools_for_role(role: str, deny_patterns=None) -> list:  # type: ignore[misc]
        if role == ROLE_INSPECTOR if "ROLE_INSPECTOR" in dir() else "inspector":
            return EXECUTE_TOOLS_INSPECTOR
        if role == ROLE_SHORT if "ROLE_SHORT" in dir() else "short":
            return EXECUTE_TOOLS_SHORT
        return EXECUTE_TOOLS

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

    # Phase 41 step 5: PreStepExecution event — extensible blocking gate
    try:
        from step_events import step_event_bus as _bus
        _pre_veto = _bus.fire_pre(
            step_text=step_text,
            goal=goal,
            step_index=step_num - 1,  # 0-based index, step_num is 1-based
        )
        if _pre_veto is not None:
            log.warning("step %d vetoed by event handler %r: %s",
                        step_num, _pre_veto.handler_name, _pre_veto.reason)
            return {
                "status": "blocked",
                "stuck_reason": f"pre-step veto ({_pre_veto.handler_name}): {_pre_veto.reason}",
                "result": "",
                "tokens_in": 0,
                "tokens_out": 0,
            }
    except Exception:
        pass  # step_events module optional

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

    # Detect steps that run external commands — give them a longer timeout.
    # 900s covers: pytest on large suites (90s) + LLM analysis time.
    # These timeout values are passed to the adapter; CodexAdapter and
    # ClaudeSubprocessAdapter both respect the timeout kwarg.
    _long_running_keywords = {"pytest", "test suite", "npm run", "make ", "docker ", "pip install",
                              "git clone", "build ", "compile", "deploy", "cargo ", "mvn "}
    _step_lower = step_text.lower()
    _is_long_running = any(kw in _step_lower for kw in _long_running_keywords)
    _step_timeout = 900 if _is_long_running else None

    # Phase 41 step 6: inject tool_search if any deferred tools are in the list
    _active_tools = list(tools)
    try:
        from tool_search import inject_tool_search_if_needed
        _active_tools = inject_tool_search_if_needed(_active_tools)
    except Exception:
        pass

    log.debug("step %d adapter_call start adapter=%s long_running=%s", step_num, type(adapter).__name__, _is_long_running)
    _llm_t0 = time.monotonic()
    try:
        _call_kwargs: Dict[str, Any] = dict(
            tools=_active_tools,
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

    # Parse tool call — collect outcome in _outcome, fire PostStepExecution before return
    _outcome: Dict[str, Any]
    _tool_name_used: Optional[str] = None

    if resp.tool_calls:
        tc = resp.tool_calls[0]
        _tool_name_used = tc.name

        # Phase 41 step 6: tool_search — deferred tool resolution
        # Model called tool_search(query=...) to get full schema for a deferred tool.
        # Resolve the schema and re-call the LLM with the expanded tool list.
        if tc.name == "tool_search":
            _ts_query = tc.arguments.get("query", "")
            log.debug("step %d tool_search query=%r", step_num, _ts_query)
            _resolved_schemas: List[dict] = []
            try:
                from tool_search import resolve_deferred_tools, format_tool_search_result
                _resolved_schemas = resolve_deferred_tools(_ts_query)
            except Exception as _ts_exc:
                log.warning("step %d tool_search failed: %s", step_num, _ts_exc)
            if _resolved_schemas:
                # Re-call the LLM with expanded tool list
                _expanded_tools = _active_tools + _resolved_schemas
                _ts_result_block = format_tool_search_result(_resolved_schemas)
                try:
                    resp = adapter.complete(
                        [
                            LLMMessage("system", EXECUTE_SYSTEM),
                            LLMMessage("user", user_msg),
                            LLMMessage("assistant", f"[tool_search result for '{_ts_query}']"),
                            LLMMessage("user", _ts_result_block),
                        ],
                        tools=_expanded_tools,
                        tool_choice="required",
                        max_tokens=4096,
                        temperature=0.3,
                    )
                    _tok = resp.input_tokens + resp.output_tokens
                    _tool_name_used = resp.tool_calls[0].name if resp.tool_calls else None
                    tc = resp.tool_calls[0] if resp.tool_calls else tc
                    log.debug("step %d tool_search re-call done: tool=%r", step_num, _tool_name_used)
                except Exception as _rerun_exc:
                    log.warning("step %d tool_search re-call failed: %s", step_num, _rerun_exc)
                    # Fall through to original response handling
            else:
                log.debug("step %d tool_search: no matches for %r", step_num, _ts_query)

        if tc.name == "complete_step":
            _confidence = tc.arguments.get("confidence", "") or ""
            _result_text = tc.arguments.get("result", resp.content)
            if not isinstance(_result_text, str):
                _result_text = json.dumps(_result_text)
            # Post-check: flag raw dumps so caller can act on it
            if _result_looks_like_raw_dump(_result_text):
                log.warning("step %d RAW_DUMP_DETECTED result_len=%d — agent ignored pipeline enforcement",
                            step_num, len(_result_text))
                _result_text = "[RAW_OUTPUT_DETECTED] " + _result_text
            log.info("step %d DONE (complete_step) tokens=%d elapsed=%.1fs confidence=%s",
                     step_num, _tok, time.monotonic() - _step_t0, _confidence or "unset")
            _outcome = {
                "status": "done",
                "result": _result_text,
                "summary": tc.arguments.get("summary", step_text),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
            if _confidence:
                _outcome["confidence"] = _confidence
        elif tc.name == "flag_stuck":
            _reason = tc.arguments.get("reason", "unknown")
            log.info("step %d BLOCKED (flag_stuck) reason=%r tokens=%d elapsed=%.1fs",
                     step_num, _reason[:80], _tok, time.monotonic() - _step_t0)
            _outcome = {
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
            _tw_result_text = "[team-worker error: no output]"
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
            _outcome = {
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
            _sched_result = "[no action — scheduler unavailable]"
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
            _outcome = {
                "status": "done",
                "result": _sched_result,
                "summary": f"Scheduled follow-up: {_sched_goal[:60]}",
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
        elif tc.name == "register_tool":
            _rt_name = tc.arguments.get("name", "").strip()
            _rt_desc = tc.arguments.get("description", "")
            _rt_bash = tc.arguments.get("bash_template", "")
            _rt_params_json = tc.arguments.get("parameters_json", "") or ""
            _rt_params = None
            if _rt_params_json:
                try:
                    import json as _json
                    _rt_params = _json.loads(_rt_params_json)
                except Exception as _pje:
                    log.warning("step %d register_tool: invalid parameters_json: %s", step_num, _pje)
            _rt_result = "[register_tool failed]"
            try:
                from runtime_tools import register_runtime_tool
                _rt_schema = register_runtime_tool(_rt_name, _rt_desc, _rt_bash, _rt_params)
                # Inject into _active_tools so the new tool is available if re-called this step
                _active_tools.append(_rt_schema)
                _rt_result = (
                    f"Tool '{_rt_name}' registered. "
                    f"Available in all subsequent steps. "
                    f"bash_template: {_rt_bash[:100]}"
                )
            except Exception as _rt_exc:
                _rt_result = f"[register_tool failed: {_rt_exc}]"
                log.warning("step %d register_tool %r failed: %s", step_num, _rt_name, _rt_exc)
            log.info("step %d DONE (register_tool) name=%r tokens=%d elapsed=%.1fs",
                     step_num, _rt_name, _tok, time.monotonic() - _step_t0)
            _outcome = {
                "status": "done",
                "result": _rt_result,
                "summary": f"Registered tool: {_rt_name}",
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
        else:
            # Check runtime tools before giving up — agent may have registered one earlier
            _rt_output: Optional[str] = None
            try:
                from runtime_tools import dispatch_runtime_tool
                _rt_output = dispatch_runtime_tool(tc.name, tc.arguments)
            except Exception:
                pass
            if _rt_output is not None:
                log.info("step %d DONE (runtime_tool:%r) tokens=%d elapsed=%.1fs",
                         step_num, tc.name, _tok, time.monotonic() - _step_t0)
                _outcome = {
                    "status": "done",
                    "result": _rt_output,
                    "summary": f"Tool {tc.name}: {_rt_output[:60]}",
                    "tokens_in": resp.input_tokens,
                    "tokens_out": resp.output_tokens,
                }
            else:
                # Unknown tool name — treat as blocked
                _outcome = {
                    "status": "blocked",
                    "stuck_reason": f"unrecognised tool: {tc.name}",
                    "result": "",
                    "tokens_in": resp.input_tokens,
                    "tokens_out": resp.output_tokens,
                }
    elif resp.content and len(resp.content) > 20:
        # No tool call — treat content as result (some models don't always call tools)
        log.info("step %d DONE (content fallback, %d chars) tokens=%d elapsed=%.1fs",
                 step_num, _content_len, _tok, time.monotonic() - _step_t0)
        _outcome = {
            "status": "done",
            "result": resp.content,
            "summary": step_text,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
        }
    else:
        log.warning("step %d BLOCKED (no tool call, content=%d chars) tokens=%d elapsed=%.1fs content=%r",
                    step_num, _content_len, _tok, time.monotonic() - _step_t0,
                    (resp.content or "")[:120])
        _outcome = {
            "status": "blocked",
            "stuck_reason": "LLM did not call a tool and produced no useful content",
            "result": resp.content,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
        }

    # Phase 41 step 5: PostStepExecution event (non-blocking)
    try:
        from step_events import step_event_bus as _bus
        _step_elapsed_ms = int((time.monotonic() - _step_t0) * 1000)
        _bus.fire_post(
            step_text=step_text,
            goal=goal,
            step_index=step_num - 1,
            result=_outcome.get("result"),
            tool_name=_tool_name_used,
            elapsed_ms=_step_elapsed_ms,
        )
    except Exception:
        pass

    return _outcome


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

        data = extract_json(content_or_empty(resp), dict, log_tag="step_exec.verify_step")
        if data:
            verdict = safe_str(data.get("verdict", "PASS")).upper()
            reason = safe_str(data.get("reason"))
            confidence = safe_float(data.get("confidence"), default=0.5, min_val=0.0, max_val=1.0)
            passed = verdict == "PASS" or confidence < confidence_threshold
            log.debug("verify_step verdict=%s confidence=%.2f passed=%s reason=%r",
                      verdict, confidence, passed, reason[:80])
            return {"passed": passed, "reason": reason, "confidence": confidence}

    except Exception as exc:
        log.debug("verify_step failed (non-fatal): %s", exc)

    return {"passed": True, "reason": "verify skipped (error)", "confidence": 0.0}
