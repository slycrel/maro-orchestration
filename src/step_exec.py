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

    ANTI-HALLUCINATION:
    If you cannot verify a claim from code or data you have directly read in
    this step, do NOT state it as fact. Mark unverified claims as [UNVERIFIED]
    or use inject_steps to add a verification sub-step.
    NEVER guess file paths, line numbers, function names, or variable names.
    If the step requires information you don't have, use NEED_INFO (see below).

    NEED_INFO — WHEN YOU LACK REQUIRED INFORMATION:
    If a step requires data, code, or context that you do not have access to
    in this execution context, you have two options:
    1. Use inject_steps in your complete_step call to add 1-3 research/verification
       sub-steps that will gather the missing information.
    2. Call flag_stuck with reason "NEED_INFO: [describe what's missing]".
    Do NOT guess or fabricate information to fill gaps.

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

# ---------------------------------------------------------------------------
# Step type classifier
# ---------------------------------------------------------------------------

# Commands that run subprocesses / shell tools — exec_command steps.
_EXEC_CMD_KEYWORDS = frozenset({
    "pytest", "python ", "run ", "execute ", "bash ", "sh ", "make ",
    "npm ", "yarn ", "docker ", "git ", "cargo ", "go test", "mvn ",
    "grep ", "find ", "curl ", "rg ", "wget ", "cat ", "invoke ",
    "install ", "build ", "compile", "lint ", "mypy ", "ruff ",
})


def _classify_step(step_text: str) -> str:
    """Return a high-level step type for prompt injection and manifest display.

    Types:
      exec_command  — runs a shell command or tool
      read_artifact — reads a file or captured output from a prior step
      analyze       — reasons over data that is already available in context
      synthesize    — produces a final deliverable (report, summary, patch)
      inspect_code  — reads source files to understand structure/behaviour
      general       — everything else
    """
    low = step_text.lower()
    if any(kw in low for kw in _EXEC_CMD_KEYWORDS):
        return "exec_command"
    if any(kw in low for kw in (
        "read the", "read and", "load the", "open the",
        "parse the file", "read artifacts", "read output", "read captured",
    )):
        return "read_artifact"
    if any(kw in low for kw in (
        "inspect", "read the code", "review the code", "look at the source",
        "examine the source", "read src/", "read the module",
    )):
        return "inspect_code"
    if any(kw in low for kw in (
        "analyz", "summariz", "interpret", "evaluate", "assess",
        "identify ", "categoriz", "conclude", "judge", "count ",
    )):
        return "analyze"
    if any(kw in low for kw in (
        "synthesiz", "write a report", "compile a report", "compile the",
        "draft", "produce a", "create a summary", "write up",
    )):
        return "synthesize"
    return "general"


# Injected into exec_command steps to enforce artifact-first output handling.
# Long command output in context burns tokens and loses fidelity — save to file.
_ARTIFACT_MATERIALIZE = textwrap.dedent("""\

    ARTIFACT MATERIALIZATION (exec_command step):
    This step runs a command. You MUST:
    1. Run the command
    2. Save ALL stdout + stderr + exit code to: {artifact_path}
    3. Call complete_step with ONLY: artifact path + exit code + ≤100-word summary
    Do NOT include raw command output in complete_step result — just the path + summary.
    Example result: "Saved to {artifact_path}. Exit 0. 142 passed, 3 failed in 4s."
""").strip()


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
                "inject_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional: list of additional steps to insert immediately after this step. "
                        "Use when this step reveals unexpected work that must happen before "
                        "the planned next step (e.g. a dependency is missing, a file needs "
                        "fetching, a subtask was discovered mid-execution). "
                        "Injected steps run in order before the original remaining plan resumes. "
                        "Maximum 3 injected steps. Keep each under 20 words."
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
    shared_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute one step via the LLM. Returns outcome dict."""
    _step_t0 = time.monotonic()
    log.info("step_start %d/%d: %s", step_num, total_steps, step_text[:100])

    # Events as first-class graph nodes: if step_text starts with "await:<kind>"
    # block this step until a typed event of that kind arrives (or timeout).
    # This makes external signals (Telegram, API, timer) first-class DAG nodes —
    # downstream steps that depend on this step receive the event payload as context.
    _await_match = re.match(r"^await(?:_event)?:([^\s\[]+)\s*(?:\[timeout=(\d+)s\])?", step_text.strip(), re.IGNORECASE)
    if _await_match:
        _event_kind = _await_match.group(1).lower()
        _timeout_s = float(_await_match.group(2) or 300)
        log.info("step %d/%d: awaiting typed event kind=%r timeout=%.0fs", step_num, total_steps, _event_kind, _timeout_s)
        try:
            from interrupt import get_event_router
            _ev = get_event_router().wait_for(_event_kind, timeout=_timeout_s)
        except Exception as _er:
            log.warning("step %d event router error: %s", step_num, _er)
            _ev = None
        if _ev is not None:
            return {
                "status": "done",
                "result": f"[event:{_event_kind}] {_ev.payload or '(no payload)'}",
                "summary": f"Received {_event_kind} event from {_ev.source}",
                "tokens_in": 0,
                "tokens_out": 0,
                "inject_steps": [],
            }
        return {
            "status": "blocked",
            "stuck_reason": f"timeout after {_timeout_s:.0f}s waiting for event kind={_event_kind!r}",
            "result": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }

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

    # Classify step type — drives prompt injection and manifest annotation.
    _step_type = _classify_step(step_text)
    log.debug("step %d type=%s", step_num, _step_type)

    # Pre-check: detect data-heavy steps and inject stronger enforcement
    _data_heavy = _is_data_heavy_step(step_text)
    _pipeline_block = ""
    if _data_heavy:
        _pipeline_block = "\n\n" + _DATA_PIPELINE_EXTRA.format(
            project_dir=project_dir or "/tmp/poe_step"
        )
        log.debug("step %d data_heavy=True — injecting pipeline enforcement", step_num)

    # Artifact materialization: exec_command steps save output to a file
    # rather than stuffing raw stdout into the reasoning context.
    _artifact_block = ""
    if _step_type == "exec_command" and not _data_heavy and project_dir:
        _artifact_dir = f"{project_dir}/artifacts"
        _artifact_path = f"{_artifact_dir}/step-{step_num}-output.txt"
        _artifact_block = "\n\n" + _ARTIFACT_MATERIALIZE.format(artifact_path=_artifact_path)
        log.debug("step %d exec_command — injecting artifact materialization", step_num)

    user_msg = (
        f"Overall goal: {goal}{ancestry_block}\n\n"
        f"Current step ({step_num}/{total_steps}) [{_step_type}]: {step_text}"
        f"{workspace_block}"
        f"{context_block}"
        f"{prefetch_block}"
        f"{_pipeline_block}"
        f"{_artifact_block}\n\n"
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
            _raw_summary = tc.arguments.get("summary", step_text)
            _summary_str = _raw_summary if isinstance(_raw_summary, str) else step_text
            _outcome = {
                "status": "done",
                "result": _result_text,
                "summary": _summary_str,
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
            if _confidence:
                _outcome["confidence"] = _confidence
            # Mutable task graph: pick up any injected steps from the worker
            _raw_inject = tc.arguments.get("inject_steps") or []
            if isinstance(_raw_inject, list):
                _clean_inject = [str(s).strip() for s in _raw_inject if s and str(s).strip()][:3]
                if _clean_inject:
                    _outcome["inject_steps"] = _clean_inject
                    log.info("step %d inject_steps: %d step(s) added to plan",
                             step_num, len(_clean_inject))
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
                    shared_ctx=shared_ctx,
                )
                _tw_result_text = format_team_result_for_injection(_tw_res)
                # Write result into shared_ctx so subsequent workers in this loop don't re-fetch
                if shared_ctx is not None and _tw_res.status == "done" and _tw_res.result:
                    _sm_key = f"{_tw_role}:{_tw_task[:40]}"
                    shared_ctx[_sm_key] = _tw_res.result[:600]
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


# ---------------------------------------------------------------------------
# Phase 62: Cross-ref claim verification for steps with specific claims
# ---------------------------------------------------------------------------

# Heuristics for detecting steps likely to contain hallucinated specifics
_SPECIFIC_CLAIM_PATTERNS = (
    "line ", "L:", "at line", ".py:", ".js:", ".ts:",
    "function ", "def ", "class ", "method ",
    "variable ", "returns ", "calls ",
)


def _has_specific_claims(result: str) -> bool:
    """Quick heuristic: does the step result contain file paths, line numbers,
    or function names that could be hallucinated?"""
    if not result or len(result) < 50:
        return False
    _lower = result.lower()
    return sum(1 for p in _SPECIFIC_CLAIM_PATTERNS if p.lower() in _lower) >= 2


def verify_step_with_cross_ref(
    step_text: str,
    result: str,
    adapter,
    *,
    confidence_threshold: float = 0.75,
) -> dict:
    """Enhanced verification: standard verify + optional cross-ref for specific claims.

    Only triggers cross-ref when the step result contains patterns suggesting
    specific factual claims (file paths, line numbers, function names).
    Returns same dict as verify_step with additional 'cross_ref' key if checked.
    """
    base_verdict = verify_step(step_text, result, adapter, confidence_threshold=confidence_threshold)

    # Only cross-ref if base verification passed AND result has specific claims
    if base_verdict.get("passed") and _has_specific_claims(result):
        try:
            from cross_ref import run_cross_ref
            report = run_cross_ref(result, adapter=adapter, max_claims=3, dry_run=False)
            if report.disputed_claims:
                # Cross-ref found disputed claims — flag as needing verification
                disputed_summary = "; ".join(
                    f"[{c.claim[:60]}] ({c.status})" for c in report.disputed_claims[:3]
                )
                base_verdict["cross_ref_disputes"] = disputed_summary
                base_verdict["reason"] = (
                    f"cross-ref disputed {len(report.disputed_claims)} claim(s): "
                    + disputed_summary[:200]
                )
                # Don't fail the step — just annotate. The claim might be correct
                # and the cross-ref wrong. Log for metacognitive learning.
                log.info("cross-ref flagged %d disputed claims in step result",
                         len(report.disputed_claims))
        except Exception as exc:
            log.debug("cross-ref check failed (non-fatal): %s", exc)

    return base_verdict
