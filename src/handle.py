#!/usr/bin/env python3
"""Poe's Handle — unified entry point for all incoming requests.

Routes to NOW lane (1-shot) or AGENDA lane (multi-step loop) based on
intent classification. This is the interface Jeremy sends messages through.

Response timing contract:
    - Immediate ack printed within the call (before execution starts)
    - Status updates printed as execution progresses (--verbose)
    - Substantive result in HandleResult.result

Usage:
    from handle import handle
    result = handle("research winning polymarket strategies")
    print(result.format())

CLI:
    python -m handle "your request here" [--project SLUG] [--dry-run]
    orch poe-handle "your request here"
"""

from __future__ import annotations

import json
import logging

import sys
import time
import uuid

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from conversation import ConversationChannel

log = logging.getLogger("poe.handle")
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Magic prefix registry
# ---------------------------------------------------------------------------
# Each prefix mutates execution without changing the goal text.
# The registry defines all prefixes centrally — adding a new prefix requires
# one entry here, not scattered startswith() chains scattered through handle().

@dataclass
class _PrefixRule:
    prefix: str           # exact lowercase prefix string (including trailing : or space)
    flag: str             # attribute name on _PrefixResult to set True
    model_tier: str = ""  # if non-empty, override model to this tier (cheap/mid/power)
    max_steps: int = 0    # if > 0, override max_steps to this value
    persona: str = ""     # if non-empty, force this persona name (overrides persona_for_goal)


@dataclass
class _PrefixResult:
    """Result of applying the prefix registry to a message."""
    message: str          # cleaned message with all prefixes stripped
    model_tier: str = ""  # model tier override (empty = no override)
    max_steps: int = 0    # max_steps override (0 = no override)
    thin_mode: bool = False
    btw_mode: bool = False
    ultraplan_mode: bool = False
    direct_mode: bool = False
    ralph_mode: bool = False
    pipeline_mode: bool = False
    strict_mode: bool = False
    team_mode: bool = False
    forced_persona: str = ""   # if non-empty, override persona_for_goal selection


_PREFIX_REGISTRY: List[_PrefixRule] = [
    # effort: overrides model tier; exclusive per level (first match wins)
    _PrefixRule("effort:low",   flag="",           model_tier="cheap"),
    _PrefixRule("effort:mid",   flag="",           model_tier="mid"),
    _PrefixRule("effort:high",  flag="",           model_tier="power"),
    # execution mode modifiers
    _PrefixRule("mode:thin",    flag="thin_mode"),
    _PrefixRule("btw:",         flag="btw_mode"),
    _PrefixRule("ultraplan:",   flag="ultraplan_mode", model_tier="power", max_steps=12),
    _PrefixRule("direct:",      flag="direct_mode"),
    # quality / behavior modifiers (non-exclusive — can stack)
    _PrefixRule("ralph:",       flag="ralph_mode"),
    _PrefixRule("verify:",      flag="ralph_mode"),   # alias for ralph:
    _PrefixRule("pipeline:",    flag="pipeline_mode"),
    _PrefixRule("strict:",      flag="strict_mode"),
    _PrefixRule("team:",        flag="team_mode",  model_tier="mid"),
    # forced persona shortcuts
    _PrefixRule("garrytan:",    flag="",           model_tier="power", persona="garrytan"),
]


def _apply_prefixes(message: str) -> _PrefixResult:
    """Strip all recognized magic prefixes from `message` and return a _PrefixResult.

    Prefixes are matched case-insensitively and stripped in registry order.
    Multiple prefixes can stack (e.g. "strict: pipeline: do the thing").
    The effort: group is exclusive (first level wins); all others accumulate.

    This replaces nine separate startswith() blocks scattered through handle().
    """
    result = _PrefixResult(message=message)
    changed = True
    while changed:
        changed = False
        lower = result.message.lower()
        for rule in _PREFIX_REGISTRY:
            if lower.startswith(rule.prefix):
                result.message = result.message[len(rule.prefix):].lstrip()
                if rule.flag:
                    setattr(result, rule.flag, True)
                if rule.model_tier:
                    if result.model_tier and result.model_tier != rule.model_tier:
                        import logging as _logging
                        _logging.getLogger("poe.handle").warning(
                            "conflicting model tiers: %r already set, ignoring %r (from prefix %r)",
                            result.model_tier, rule.model_tier, rule.prefix,
                        )
                    elif not result.model_tier:
                        result.model_tier = rule.model_tier
                if rule.max_steps:
                    result.max_steps = rule.max_steps
                if rule.persona and not result.forced_persona:
                    result.forced_persona = rule.persona
                changed = True
                lower = result.message.lower()  # re-check after strip
                break  # restart registry scan after each match
    return result


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class HandleResult:
    handle_id: str
    lane: str                   # "now" | "agenda"
    lane_confidence: float
    classification_reason: str
    message: str
    status: str                 # "done" | "stuck" | "error"
    result: str                 # The substantive response / work product
    project: Optional[str] = None
    loop_result: Any = None     # LoopResult if AGENDA
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    artifact_path: Optional[str] = None

    def format(self, mode: str = "text") -> str:
        if mode == "json":
            return json.dumps({
                "handle_id": self.handle_id,
                "lane": self.lane,
                "classification_reason": self.classification_reason,
                "status": self.status,
                "result": self.result,
                "project": self.project,
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                "elapsed_ms": self.elapsed_ms,
                "artifact_path": self.artifact_path,
            }, indent=2)
        lines = [
            f"handle_id={self.handle_id}",
            f"lane={self.lane} (confidence={self.lane_confidence:.2f})",
            f"status={self.status}",
            f"tokens={self.tokens_in}in+{self.tokens_out}out elapsed={self.elapsed_ms}ms",
        ]
        if self.project:
            lines.append(f"project={self.project}")
        if self.artifact_path:
            lines.append(f"artifact={self.artifact_path}")
        lines.append("")
        lines.append(self.result)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# NOW lane executor
# ---------------------------------------------------------------------------

_NOW_SYSTEM = """You are Poe, an autonomous AI assistant.
Answer the user's request directly and completely. Be thorough but concise.
If the request is a question, answer it. If it's a task, complete it.
Do not hedge or defer — just do the work.
"""

_BTW_SYSTEM = """You are Poe, surfacing a non-blocking observation.
Note what you observe, briefly and specifically. Do not attempt to fix or solve anything.
Keep it to 1–3 sentences max. Format: one sentence per observation, plain text.
This is a side-note, not a task result.
"""


def _is_complex_directive(message: str) -> bool:
    """Heuristic: does a NOW-classified message actually require Director-level planning?

    Returns True when the message shows signs of multi-step complexity that the
    single-shot NOW lane would handle poorly. Used to gate optional escalation to
    AGENDA when now_lane.escalate_to_director is enabled.

    Signals:
      - More than 25 words (classifier uses ≤15 as simple)
      - Multi-step sequencing language
      - Action verbs that imply building/researching/designing
      - Multiple sentences (compound task)
    """
    import re
    msg_lower = message.lower().strip()
    words = msg_lower.split()

    if len(words) > 25:
        return True

    # Multi-step indicators
    _SEQUENCE_PATTERNS = [
        r'\bthen\b', r'\bfirst\b.{0,60}\bthen\b', r'\bafter(ward)?\b',
        r'\bstep\s+\d', r'\b\d+\.\s', r'\band\s+also\b', r'\badditionally\b',
    ]
    if any(re.search(p, msg_lower) for p in _SEQUENCE_PATTERNS):
        return True

    # Action verbs implying multi-step work (require 8+ words to avoid false positives
    # on short creative requests like "write a haiku" or "create a joke")
    _COMPLEX_VERBS = {
        "build", "implement", "design", "research", "analyze",
        "investigate", "develop", "plan", "architect", "refactor",
        "migrate", "integrate", "deploy", "configure",
    }
    first_words = set(words[:8])
    if len(words) >= 8 and first_words & _COMPLEX_VERBS:
        return True

    # Multiple sentences (compound task)
    sentences = [s.strip() for s in re.split(r'[.!?]', message) if s.strip() and len(s.strip()) > 10]
    if len(sentences) >= 2:
        return True

    return False


def _run_now(
    message: str,
    handle_id: str,
    adapter,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Execute a NOW-lane task: single LLM call, returns result dict."""
    from llm import LLMMessage

    if verbose:
        print(f"[poe:{handle_id}] NOW lane — executing...", file=sys.stderr, flush=True)

    t0 = time.monotonic()
    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _NOW_SYSTEM),
                LLMMessage("user", message),
            ],
            max_tokens=2048,
            temperature=0.4,
        )
        elapsed = int((time.monotonic() - t0) * 1000)
        content = resp.content.strip()
        if not content:
            content = "[no response]"
        return {
            "status": "done",
            "result": content,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
            "elapsed_ms": elapsed,
        }
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return {
            "status": "error",
            "result": f"NOW lane error: {exc}",
            "tokens_in": 0,
            "tokens_out": 0,
            "elapsed_ms": elapsed,
        }


# ---------------------------------------------------------------------------
# User config loader
# ---------------------------------------------------------------------------

def _load_user_config() -> dict:
    """Parse user/CONFIG.md into a key→value dict. Non-fatal — returns {} on any error."""
    try:
        cfg_path = Path(__file__).resolve().parent.parent / "user" / "CONFIG.md"
        if not cfg_path.exists():
            return {}
        result = {}
        for line in cfg_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.split("#")[0].strip()  # strip inline comments
            if key and val:
                result[key] = val
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Core handle function
# ---------------------------------------------------------------------------

def handle(
    message: str,
    *,
    project: Optional[str] = None,
    repo_path: str = "",
    model: Optional[str] = None,
    adapter=None,
    force_lane: Optional[str] = None,   # "now" | "agenda" | None (auto)
    dry_run: bool = False,
    verbose: bool = False,
    channel: Optional["ConversationChannel"] = None,
    prior_context: Optional[str] = None,
) -> HandleResult:
    """Process an incoming request through Poe's handle.

    Args:
        message: The natural language request.
        project: Project slug to attach AGENDA work to.
        repo_path: Optional path to target source repo (auto-injects stack context).
        model: LLM model override.
        adapter: Pre-built LLMAdapter (skips build_adapter).
        force_lane: Override classification ("now" or "agenda").
        dry_run: Simulate without API calls.
        verbose: Print progress.
        channel: Optional ConversationChannel for bidirectional comms (e.g. dashboard).
            When provided, the clarity check uses channel.ask() to gather missing info
            (rather than returning clarification_needed), and step events are emitted.

    Returns:
        HandleResult with routing info and substantive result.
    """
    from intent import classify
    from llm import build_adapter, MODEL_CHEAP
    from agent_loop import run_agent_loop, _DryRunAdapter

    handle_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()

    if verbose:
        print(f"[poe:{handle_id}] handle: {message!r}", file=sys.stderr, flush=True)

    # Persist raw input before any prefix stripping — visibility hole fix.
    # Writes to memory/handle_inputs.jsonl so every goal + its prefixes are recoverable.
    _raw_input = message
    try:
        _inputs_path = Path(__file__).resolve().parent.parent / "memory" / "handle_inputs.jsonl"
        _inputs_path.parent.mkdir(parents=True, exist_ok=True)
        with _inputs_path.open("a", encoding="utf-8") as _fh:
            _fh.write(
                json.dumps({
                    "handle_id": handle_id,
                    "raw_input": _raw_input,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }) + "\n"
            )
    except Exception:
        pass  # never block on logging

    # Apply user/CONFIG.md defaults (non-fatal — bad config never blocks a run)
    _cfg = _load_user_config()
    if model is None:
        _tier = _cfg.get("default_model_tier", "").strip().lower()
        if _tier in ("cheap", "mid", "power"):
            model = _tier

    # Apply magic prefix registry — strips all recognized prefixes in one pass.
    _pfx = _apply_prefixes(message)
    message = _pfx.message
    if _pfx.model_tier and model is None:
        model = _pfx.model_tier

    # Unpack prefix flags into local names for backward compatibility
    # with the rest of this function (no other code changes needed below).
    _use_thin_mode = _pfx.thin_mode
    _btw_mode = _pfx.btw_mode
    _ultraplan_max_steps = _pfx.max_steps if _pfx.max_steps else None
    _direct_mode = _pfx.direct_mode
    _ralph_prefix = _pfx.ralph_mode
    _pipeline_prefix = _pfx.pipeline_mode
    _strict_prefix = _pfx.strict_mode
    _team_prefix = _pfx.team_mode

    # Scope-based model floor: wide/deep goals shouldn't start on cheap.
    # The pre-flight scope estimate is free (<1ms, zero LLM) and already exists.
    # If no explicit model was requested (no prefix, no config), lift to mid.
    if model is None or model == MODEL_CHEAP:
        try:
            from planner import estimate_goal_scope
            _scope = estimate_goal_scope(message)
            if _scope in ("wide", "deep"):
                model = "mid"
                log.info("handle: scope=%s → lifting model floor to mid (was %s)",
                         _scope, model or "cheap")
        except Exception:
            pass

    # Build adapter
    if adapter is None and not dry_run:
        adapter = build_adapter(model=model or MODEL_CHEAP)
    elif dry_run:
        adapter = _DryRunAdapter()

    # Classify intent
    if force_lane:
        lane = force_lane
        confidence = 1.0
        reason = f"forced to {force_lane}"
    else:
        lane, confidence, reason = classify(message, adapter=adapter if not dry_run else None, dry_run=dry_run)

    if verbose:
        print(f"[poe:{handle_id}] classified lane={lane} confidence={confidence:.2f}: {reason}", file=sys.stderr, flush=True)

    # direct: forces AGENDA lane regardless of classifier — the whole point is to bypass
    # Director overhead (which only applies to AGENDA) and go straight to run_agent_loop.
    if _direct_mode:
        lane = "agenda"

    # btw mode: quick observation, always routes to NOW regardless of classification.
    # The result is prefixed with "[Observation]" to distinguish from work products.
    if _btw_mode:
        from llm import LLMMessage
        try:
            _btw_resp = adapter.complete(
                [LLMMessage("system", _BTW_SYSTEM), LLMMessage("user", message)],
                max_tokens=256,
                temperature=0.3,
            )
            _btw_content = _btw_resp.content.strip() or "[no observation]"
        except Exception as _btw_exc:
            _btw_content = f"[observation error: {_btw_exc}]"
            _btw_resp = type("R", (), {"input_tokens": 0, "output_tokens": 0})()
        elapsed = int((time.monotonic() - started_at) * 1000)
        return HandleResult(
            handle_id=handle_id,
            lane="now",
            lane_confidence=1.0,
            classification_reason="btw: non-blocking observation",
            message=message,
            status="done",
            result=f"[Observation] {_btw_content}",
            tokens_in=getattr(_btw_resp, "input_tokens", 0),
            tokens_out=getattr(_btw_resp, "output_tokens", 0),
            elapsed_ms=elapsed,
        )

    # Route to lane
    if lane == "now":
        # Optional escalation: if now_lane.escalate_to_director is enabled and the
        # message looks complex, reclassify to agenda so the Director can plan it.
        _now_escalate_enabled = False
        try:
            from config import get as _cfg_get
            _now_escalate_enabled = bool(_cfg_get("now_lane.escalate_to_director", False))
        except Exception:
            pass
        if _now_escalate_enabled and _is_complex_directive(message):
            lane = "agenda"
            reason = reason + " [now→agenda: complex directive escalated to Director]"
            log.info("handle: now→agenda escalation for: %s", message[:80])
            # Fall through to the agenda branch below

    if lane == "now":
        outcome = _run_now(message, handle_id, adapter, verbose=verbose)
        elapsed = int((time.monotonic() - started_at) * 1000)

        # Write artifact
        artifact_path = _write_now_artifact(handle_id, message, outcome.get("result", ""), elapsed)

        return HandleResult(
            handle_id=handle_id,
            lane="now",
            lane_confidence=confidence,
            classification_reason=reason,
            message=message,
            status=outcome["status"],
            result=outcome["result"],
            tokens_in=outcome["tokens_in"],
            tokens_out=outcome["tokens_out"],
            elapsed_ms=elapsed,
            artifact_path=artifact_path,
        )

    else:  # agenda
        # Only route through poe CEO layer for meta-commands (status, inspect, goal-map).
        # For actual mission goals, always go direct to run_agent_loop to avoid stale
        # mission data being returned instead of a fresh run.
        _is_meta_command = False
        try:
            from poe import _looks_like_status, _looks_like_inspect, _looks_like_goal_map
            _is_meta_command = (
                _looks_like_status(message)
                or _looks_like_inspect(message)
                or _looks_like_goal_map(message)
            )
        except ImportError:
            pass

        if not dry_run and not project and _is_meta_command:
            try:
                from poe import poe_handle
                from agent_loop import _goal_to_slug
                poe_response = poe_handle(
                    message,
                    adapter=adapter,
                    model=model,
                    dry_run=False,
                )
                elapsed = int((time.monotonic() - started_at) * 1000)
                _poe_project = _goal_to_slug(message)
                return HandleResult(
                    handle_id=handle_id,
                    lane="agenda",
                    lane_confidence=confidence,
                    classification_reason=reason + " [routed via poe CEO layer]",
                    message=message,
                    status="done",
                    result=poe_response.message,
                    project=_poe_project,
                    elapsed_ms=elapsed,
                    artifact_path=None,
                )
            except (ImportError, Exception):
                pass  # fall through to direct agenda handling
        # BLE rewriter — strip prescribed execution steps, keep outcome intent (non-blocking)
        # Bitter Lesson Engineering: embed the "what", let the AI own the "how".
        if not dry_run:
            try:
                from intent import rewrite_imperative_goal
                _rewritten = rewrite_imperative_goal(message, adapter=adapter)
                if _rewritten != message:
                    if verbose:
                        print(f"[poe:{handle_id}] BLE rewrite: imperative goal → outcome goal", file=sys.stderr, flush=True)
                    message = _rewritten
            except Exception:
                pass  # rewrite failures must never block a run

        # Clarification milestone — check goal clarity before starting (skipped if yolo=true)
        _yolo = _cfg.get("yolo", "false").strip().lower() == "true"
        if not dry_run and not _yolo:
            try:
                from intent import check_goal_clarity
                _clarity = check_goal_clarity(message, adapter=adapter)
                if not _clarity.get("clear"):
                    _q = _clarity.get("question", "Could you clarify the goal?")
                    if verbose:
                        print(f"[poe:{handle_id}] clarity check: UNCLEAR — {_q}", file=sys.stderr, flush=True)
                    if channel is not None:
                        # Ask via channel and wait for reply — then continue with enriched goal
                        _reply = channel.ask(_q)
                        if _reply:
                            message = f"{message}\n\nAdditional context: {_reply}"
                        # Fall through to continue execution
                    else:
                        # No channel — return clarification_needed (CLI path)
                        elapsed = int((time.monotonic() - started_at) * 1000)
                        return HandleResult(
                            handle_id=handle_id,
                            lane="agenda",
                            lane_confidence=confidence,
                            classification_reason=reason + " [clarity check: ambiguous]",
                            message=message,
                            status="clarification_needed",
                            result=(
                                f"Before starting, I need to clarify one thing:\n\n"
                                f"{_q}\n\n"
                                f"*(Add `yolo: true` to user/CONFIG.md to skip this check.)*"
                            ),
                            elapsed_ms=elapsed,
                        )
            except Exception:
                pass  # clarity check must never block execution

        if verbose:
            print(f"[poe:{handle_id}] AGENDA lane — starting loop...", file=sys.stderr, flush=True)

        # mode:thin — use factory_thin loop (faster, lower cost) instead of full Mode 2
        if _use_thin_mode and not dry_run:
            try:
                from factory_thin import run_factory_thin
                _thin_result = run_factory_thin(
                    message,
                    model=model or "cheap",
                    verbose=verbose,
                )
                elapsed = int((time.monotonic() - started_at) * 1000)
                _thin_text = _thin_result.final_report or "[no output produced]"
                if _thin_result.status != "done":
                    _thin_text += f"\n\n⚠️ Thin loop status: {_thin_result.status}"
                return HandleResult(
                    handle_id=handle_id,
                    lane="agenda",
                    lane_confidence=confidence,
                    classification_reason=reason + " [mode:thin]",
                    message=message,
                    status=_thin_result.status,
                    result=_thin_text,
                    project=project or "",
                    tokens_in=_thin_result.total_tokens // 2,
                    tokens_out=_thin_result.total_tokens // 2,
                    elapsed_ms=elapsed,
                )
            except Exception as _thin_exc:
                log.warning("mode:thin failed, falling back to Mode 2: %s", _thin_exc)
                # Fall through to run_agent_loop below

        # pipeline: prefix — user specifies explicit steps as "step1 | step2 | step3".
        # Bypasses LLM decomposition entirely; runs the given steps in order.
        if _pipeline_prefix:
            _pipe_raw = _pfx.message
            _pipe_steps = [s.strip() for s in _pipe_raw.split("|") if s.strip()]
            if not _pipe_steps:
                _pipe_steps = [s.strip() for s in _pipe_raw.splitlines() if s.strip()]
            if _pipe_steps:
                if verbose:
                    print(f"[poe] pipeline: {len(_pipe_steps)} steps: {_pipe_steps}", file=sys.stderr, flush=True)
                _pipe_result = run_agent_loop(
                    _pipe_raw,
                    project=project,
                    model=model,
                    adapter=adapter,
                    dry_run=dry_run,
                    verbose=verbose,
                    preset_steps=_pipe_steps,
                )
                return _loop_result_to_handle(
                    _pipe_result, handle_id=handle_id, message=message,
                    confidence=confidence, reason=reason, started_at=started_at,
                    project=project, reason_suffix=" [pipeline]",
                )

        # team: prefix — decompose into DAG and execute with dep-aware parallel pool.
        # Uses parallel_fan_out=4 so _run_steps_dag fires when [after:N] parallelism is found.
        if _team_prefix:
            if verbose:
                print("[poe] team: dag execution mode (parallel_fan_out=4)", file=sys.stderr, flush=True)
            _team_result = run_agent_loop(
                _pfx.message,
                project=project,
                model=model,
                adapter=adapter,
                dry_run=dry_run,
                verbose=verbose,
                parallel_fan_out=4,
            )
            return _loop_result_to_handle(
                _team_result, handle_id=handle_id, message=message,
                confidence=confidence, reason=reason, started_at=started_at,
                project=project, reason_suffix=" [team]",
            )

        # direct: prefix — skip quality gate and escalation, route straight to run_agent_loop.
        # Bitter Lesson experiment: for simple goals, scaffolding overhead doesn't improve output.
        if _direct_mode:
            _direct_result = run_agent_loop(
                message,
                project=project,
                model=model,
                adapter=adapter,
                dry_run=dry_run,
                verbose=verbose,
            )
            return _loop_result_to_handle(
                _direct_result, handle_id=handle_id, message=message,
                confidence=confidence, reason=reason, started_at=started_at,
                project=project, reason_suffix=" [direct]",
            )

        _ralph_from_cfg = _cfg.get("ralph_verify", "").strip().lower() == "true"
        _loop_kwargs: dict = dict(
            project=project,
            repo_path=repo_path,
            model=model,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            ralph_verify=_ralph_from_cfg or _ralph_prefix,
        )
        if _ultraplan_max_steps is not None:
            _loop_kwargs["max_steps"] = _ultraplan_max_steps

        # Wire step_callback for channel live updates (main AGENDA path only)
        if channel is not None:
            def _step_cb(step_num: int, step_text: str, summary: Optional[str], status: str) -> None:
                channel.emit(
                    "step",
                    text=f"Step {step_num}: {(summary or step_text)[:600]}",
                    step_num=step_num,
                    status=status,
                )
            _loop_kwargs["step_callback"] = _step_cb

        # Persona injection: select best persona for goal and inject as ancestry_context_extra.
        # forced_persona (from garrytan:, etc.) overrides auto-selection.
        _persona_ctx = ""
        try:
            from persona import persona_for_goal, PersonaRegistry, build_persona_system_prompt, record_persona_dispatch, _DEFAULT_PERSONA
            _preg = PersonaRegistry()
            _pconf = 1.0
            if _pfx.forced_persona:
                _pname = _pfx.forced_persona
            else:
                _pname, _pconf = persona_for_goal(message, registry=_preg, confidence_threshold=0.75)
            # Track dispatch for persona gap detection (evolver uses this)
            try:
                _is_fallback = not _pfx.forced_persona and (
                    _pconf < 0.75 or _pname == _DEFAULT_PERSONA
                )
                record_persona_dispatch(message, _pname, _pconf, is_fallback=_is_fallback)
            except Exception:
                pass
            _pspec = _preg.load(_pname)
            if _pspec:
                _persona_ctx = build_persona_system_prompt(_pspec, goal=message)
                log.info("handle: persona=%s conf=%.2f forced=%s", _pname, _pconf, bool(_pfx.forced_persona))
        except Exception:
            pass
        _extra_ctx_parts = []
        if prior_context:
            _extra_ctx_parts.append(
                f"== Prior run context (for continuation) ==\n{prior_context}\n"
                f"== End prior context — continue from here =="
            )
        if _persona_ctx:
            _extra_ctx_parts.append(_persona_ctx)
        # Completion standard — injected for every AGENDA run
        try:
            _std_path = Path(__file__).parent.parent / "user" / "COMPLETION_STANDARD.md"
            if _std_path.exists():
                _extra_ctx_parts.append(_std_path.read_text(encoding="utf-8").strip())
        except Exception:
            pass

        # Phase 65 minimum viable experiment: scope generation via inversion.
        # Gated by `scope_generation` config flag (default off). `scope_ab_skip`
        # is the paired A/B flag — when true, we'd-have-generated is recorded
        # but not injected, so the same goal can be run with/without scope for
        # comparison. Uses the same config system as adaptive_execution (reads
        # from ~/.poe/config.yml, not the repo-local user/CONFIG.md).
        # See docs/PHASE_65_IMPLEMENTATION_PLAN.md.
        _scope = None
        try:
            from config import get as _config_get
            _scope_on = bool(_config_get("scope_generation", False))
            _scope_ab_skip = bool(_config_get("scope_ab_skip", False))
        except Exception:
            _scope_on = False
            _scope_ab_skip = False
        if _scope_on and not dry_run:
            try:
                from scope import generate_scope
                _scope = generate_scope(message, adapter)
                # Resolve the project artifacts dir once; used for both
                # successful scope.md persistence and raw-dump on parse failure.
                try:
                    from agent_loop import _goal_to_slug
                    _scope_project = project or _goal_to_slug(message)
                    _proj_dir = Path.home() / ".poe" / "workspace" / "projects" / _scope_project / "artifacts"
                    _proj_dir.mkdir(parents=True, exist_ok=True)
                except Exception:
                    _proj_dir = None

                if _scope is not None and _scope.is_empty():
                    # Parse failed. Persist the raw LLM response so the next
                    # debug pass has evidence, and record a captain's log event
                    # so closure/scope observability runs can count parse failures.
                    _raw = (_scope.raw_text or "").strip()
                    if _proj_dir is not None and _raw:
                        try:
                            (_proj_dir / "scope-raw-FAILED.txt").write_text(
                                _raw + "\n", encoding="utf-8"
                            )
                            log.info("scope: parse failed, raw response at %s/scope-raw-FAILED.txt", _proj_dir)
                        except Exception as _raw_exc:
                            log.debug("scope: could not record raw response: %s", _raw_exc)
                    try:
                        from captains_log import log_event, SCOPE_PARSE_FAILED
                        log_event(
                            SCOPE_PARSE_FAILED,
                            subject="scope_parse_failed",
                            summary=f"Scope LLM response did not parse into failure_modes/in_scope/out_of_scope sections.",
                            context={
                                "goal_preview": message[:200],
                                "raw_length": len(_raw),
                                "raw_preview": _raw[:400],
                            },
                        )
                    except Exception:
                        pass
                    _scope = None  # treat as "no scope" for the rest of the pipeline
                elif _scope is not None and not _scope.is_empty():
                    # Successful parse. Persist scope.md + emit captain's log event.
                    if _proj_dir is not None:
                        try:
                            (_proj_dir / "scope.md").write_text(
                                _scope.to_markdown(), encoding="utf-8"
                            )
                            log.info("scope: recorded artifact at %s/scope.md", _proj_dir)
                        except Exception as _scope_rec_exc:
                            log.debug("scope: could not record artifact: %s", _scope_rec_exc)
                    try:
                        from captains_log import log_event, SCOPE_GENERATED
                        log_event(
                            SCOPE_GENERATED,
                            subject="scope_generated",
                            summary=(
                                f"Scope: {len(_scope.failure_modes)} failure modes, "
                                f"{len(_scope.in_scope)} in-scope, "
                                f"{len(_scope.out_of_scope)} out-of-scope."
                            ),
                            context={
                                "goal_preview": message[:200],
                                "failure_modes_count": len(_scope.failure_modes),
                                "in_scope_count": len(_scope.in_scope),
                                "out_of_scope_count": len(_scope.out_of_scope),
                                "ab_skip": bool(_scope_ab_skip),
                            },
                        )
                    except Exception:
                        pass
                    # A/B skip: record but don't inject
                    if _scope_ab_skip:
                        log.info("[scope-deferred] ab-skip: scope generated "
                                 "but not injected (ab-test control arm)")
                    else:
                        _extra_ctx_parts.append(_scope.to_markdown())
                        if channel is None:
                            log.info("[scope-deferred] human-gate: no channel, "
                                     "proceeding with generated scope without review")
                        else:
                            log.info("[scope-deferred] human-gate: scope used "
                                     "without review (gate UX deferred)")
                        log.info("[scope-deferred] enforcement: scope injected "
                                 "but not checked mid-execution, violation "
                                 "detection deferred")
            except Exception as _scope_exc:
                log.warning("scope: generation failed, continuing without scope: %s", _scope_exc)

        if _extra_ctx_parts:
            _loop_kwargs["ancestry_context_extra"] = "\n\n".join(_extra_ctx_parts)

        if channel is not None:
            _loop_kwargs["channel"] = channel

        loop_result = run_agent_loop(message, **_loop_kwargs)
        elapsed = int((time.monotonic() - started_at) * 1000)

        # Director restart: loop broke with restart status — re-run with restart context.
        # continuation_depth increment prevents infinite restart loops.
        if (loop_result.status == "restart"
                and not dry_run
                and _loop_kwargs.get("continuation_depth", 0) < 3):
            try:
                _restart_ctx = loop_result.stuck_reason or "Director requested restart."
                _restart_ancestry = (
                    _loop_kwargs.get("ancestry_context_extra", "")
                    + f"\n\n== Director restart context ==\n{_restart_ctx}\n== End restart context =="
                ).strip()
                _restart_kwargs = dict(_loop_kwargs)
                _restart_kwargs["ancestry_context_extra"] = _restart_ancestry
                _restart_kwargs["continuation_depth"] = (
                    _loop_kwargs.get("continuation_depth", 0) + 1
                )
                log.info("handle: director restart (depth %d) — %s",
                         _restart_kwargs["continuation_depth"], _restart_ctx[:80])
                if channel is not None:
                    channel.emit("restart", text=f"Director restart: {_restart_ctx[:200]}")
                loop_result = run_agent_loop(message, **_restart_kwargs)
                elapsed = int((time.monotonic() - started_at) * 1000)
            except Exception as _rst_exc:
                log.warning("handle: restart re-run failed: %s", _rst_exc)

        # Director closure check — verify the goal was actually achieved
        # Runs after the loop declares "done"; emits verification/needs_work events
        # to channel. Non-fatal: never raises.
        # If closure_restart is enabled and the verdict says the goal is NOT
        # complete with material confidence, feed the gaps back as restart context
        # and re-run the loop (capped by continuation_depth).
        if not dry_run and loop_result.status == "done":
            try:
                from director import verify_goal_completion
                _closure = verify_goal_completion(
                    message,
                    loop_result.steps,
                    adapter,
                    workspace_path=repo_path or "",
                    channel=channel,
                    scope=_scope,
                )
            except Exception:
                _closure = None

            try:
                from config import get as _config_get
                _closure_restart = bool(_config_get("closure_restart", True))
            except Exception:
                _closure_restart = True

            _depth = _loop_kwargs.get("continuation_depth", 0)
            if (
                _closure_restart
                and _closure is not None
                and not _closure.complete
                and _closure.confidence >= 0.6
                and _closure.checks_run > 0
                and _depth < 3
            ):
                _gap_lines = "\n".join(f"- {g}" for g in _closure.gaps) or "(none specified)"
                _closure_ctx = (
                    f"The previous run declared done, but closure verification found gaps.\n"
                    f"Summary: {_closure.summary}\n"
                    f"Gaps:\n{_gap_lines}\n"
                    f"Verification: {_closure.checks_passed}/{_closure.checks_run} checks passed.\n"
                    f"Address the gaps before declaring done again."
                )
                _closure_ancestry = (
                    _loop_kwargs.get("ancestry_context_extra", "")
                    + f"\n\n== Closure gap context ==\n{_closure_ctx}\n== End closure gap context =="
                ).strip()
                _closure_kwargs = dict(_loop_kwargs)
                _closure_kwargs["ancestry_context_extra"] = _closure_ancestry
                _closure_kwargs["continuation_depth"] = _depth + 1
                log.info(
                    "handle: closure restart (depth %d) — gaps=%d confidence=%.2f",
                    _closure_kwargs["continuation_depth"],
                    len(_closure.gaps),
                    _closure.confidence,
                )
                if channel is not None:
                    try:
                        channel.emit(
                            "closure_restart",
                            text=f"Closure verification found gaps — restarting.\n{_closure.summary}",
                        )
                    except Exception:
                        pass
                try:
                    loop_result = run_agent_loop(message, **_closure_kwargs)
                    elapsed = int((time.monotonic() - started_at) * 1000)
                except Exception as _cr_exc:
                    log.warning("handle: closure restart re-run failed: %s", _cr_exc)

        # Notify channel that the main loop completed
        if channel is not None:
            try:
                _result_parts = [
                    s.result for s in loop_result.steps
                    if s.status == "done" and s.result
                ]
                _result_summary = "\n\n".join(_result_parts) if _result_parts else "[no output]"
                if loop_result.status == "stuck":
                    _stuck_reason = getattr(loop_result, "stuck_reason", None) or "no further progress possible"
                    channel.emit("stuck", text=f"Loop got stuck after {len(loop_result.steps)} steps: {_stuck_reason}")
                elif loop_result.status == "restart":
                    # restart re-run failed or depth exceeded — treat as stuck
                    _rst_reason = getattr(loop_result, "stuck_reason", None) or "restart limit reached"
                    channel.emit("stuck", text=f"Director restart loop exhausted: {_rst_reason}")
                elif loop_result.status not in ("done", "complete"):
                    channel.emit("error", text=f"Loop ended with status: {loop_result.status}")
                channel.complete(_result_summary)
            except Exception:
                pass  # channel notifications must never block

        # Quality gate — skeptic review; escalate model tier if output is below bar
        _gate_note = ""
        _contested_claims: list = []
        if not dry_run and loop_result.status == "done" and _cfg.get("quality_gate", "true") == "true":
            try:
                from quality_gate import run_quality_gate, next_model_tier
                _gate_verdict = run_quality_gate(
                    message, loop_result.steps, adapter,
                    run_council=_strict_prefix,
                    run_cross_ref=_strict_prefix,
                )
                _contested_claims = _gate_verdict.contested_claims or []
                if _gate_verdict.escalate:
                    _next_tier = next_model_tier(model or "cheap")
                    _action = _cfg.get("quality_gate_action", "escalate").strip().lower()
                    _gate_note = f"\n\n⚠️ Quality gate: ESCALATE — {_gate_verdict.reason}"
                    if verbose:
                        print(f"[poe:{handle_id}] quality gate: ESCALATE → {_next_tier} ({_gate_verdict.reason})",
                              file=sys.stderr, flush=True)
                    if _action == "escalate" and _next_tier:
                        if verbose:
                            print(f"[poe:{handle_id}] re-running with model={_next_tier}",
                                  file=sys.stderr, flush=True)
                        _escalated_adapter = build_adapter(model=_next_tier)
                        loop_result = run_agent_loop(
                            message,
                            project=(project or loop_result.project or "") + "-escalated",
                            model=_next_tier,
                            adapter=_escalated_adapter,
                            dry_run=False,
                            verbose=verbose,
                        )
                        elapsed = int((time.monotonic() - started_at) * 1000)
                        _gate_note = f"\n\n✅ Quality gate escalated to {_next_tier} — re-run complete."
                        _contested_claims = []  # fresh run — don't append stale claims
            except Exception:
                pass  # gate never blocks delivery of results

        # Build extra annotations from quality gate / pre-flight
        _extra = ""
        _pf = getattr(loop_result, "pre_flight_review", None)
        if _pf and getattr(_pf, "scope", None) == "wide":
            _extra += f"\n\n⚠️ Pre-flight: scope=wide — {_pf.scope_note}"
        if _contested_claims:
            _claims_text = "\n".join(
                f"- [{c.get('verdict', '?')}] {c.get('claim', '')} — {c.get('reason', '')}"
                for c in _contested_claims
            )
            _extra += f"\n\n---\n\n**⚠️ Adversarial review — contested claims:**\n{_claims_text}"
        if _gate_note:
            _extra += _gate_note

        return _loop_result_to_handle(
            loop_result, handle_id=handle_id, message=message,
            confidence=confidence, reason=reason, started_at=started_at,
            project=project, extra_text=_extra,
        )


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def _loop_result_to_handle(
    loop_result,
    *,
    handle_id: str,
    message: str,
    confidence: float,
    reason: str,
    started_at: float,
    project: Optional[str] = None,
    reason_suffix: str = "",
    extra_text: str = "",
) -> "HandleResult":
    """Convert a LoopResult into a HandleResult with formatted step text.

    Deduplicates the pipeline/team/direct/default AGENDA paths that all
    format steps identically.
    """
    elapsed = int((time.monotonic() - started_at) * 1000)
    result_parts = []
    for s in loop_result.steps:
        if s.status == "done" and s.result:
            result_parts.append(f"**Step {s.index}: {s.text}**\n{s.result}")
    result_text = "\n\n---\n\n".join(result_parts) if result_parts else "[no output]"
    if loop_result.status == "stuck":
        result_text += f"\n\n⚠️ Stuck: {loop_result.stuck_reason}"
    if extra_text:
        result_text += extra_text
    _class_reason = reason + reason_suffix if reason_suffix else reason
    return HandleResult(
        handle_id=handle_id,
        lane="agenda",
        lane_confidence=confidence,
        classification_reason=_class_reason,
        message=message,
        status=loop_result.status,
        result=result_text,
        project=loop_result.project or project or "",
        loop_result=loop_result,
        tokens_in=loop_result.total_tokens_in,
        tokens_out=loop_result.total_tokens_out,
        elapsed_ms=elapsed,
        artifact_path=getattr(loop_result, "log_path", None),
    )


def _write_now_artifact(
    handle_id: str,
    message: str,
    result: str,
    elapsed_ms: int,
) -> Optional[str]:
    """Write NOW-lane result to a shared artifacts directory."""
    try:
        # Use orch_root if available, else cwd
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).parent))
            from orch import orch_root
            base = orch_root()
        except Exception:
            base = Path.cwd()

        artifacts_dir = base / "prototypes" / "poe-orchestration" / "artifacts" / "now"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"now-{handle_id}.json"
        path = artifacts_dir / fname
        payload = {
            "handle_id": handle_id,
            "lane": "now",
            "message": message,
            "result": result,
            "elapsed_ms": elapsed_ms,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Task store routing — escalation and continuation consumers
# ---------------------------------------------------------------------------

def _context_firewall(reason: str, depth: int, cap: int = 600) -> str:
    """Filter a continuation/escalation reason blob for passing to a sub-loop.

    At depth ≤ 1: pass the full reason (capped) — the first continuation should
    have full context of what came before.

    At depth ≥ 2: strip accomplished steps (they're done and irrelevant to the
    sub-loop's planner). Extract only:
      - Original goal (one line)
      - Remaining steps (the work that actually needs to happen)
    This prevents context contamination and token bloat at depth 3, 4, etc.

    Always caps at `cap` characters.
    """
    if depth <= 1:
        return reason[:cap]

    # Deep continuation: extract only what matters to the next executor
    lines = reason.split("\n")
    goal_line = ""
    remaining_lines: list = []
    in_remaining = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Original goal:"):
            goal_line = stripped
        elif stripped == "Remaining:" or stripped.startswith("Remaining:"):
            in_remaining = True
            remaining_lines.append(line)
        elif in_remaining:
            if stripped.startswith("Accomplished:") or stripped.startswith("ESCALATION"):
                in_remaining = False
            else:
                remaining_lines.append(line)

    if goal_line or remaining_lines:
        filtered = "\n".join(filter(None, [goal_line] + remaining_lines)).strip()
        return filtered[:cap]

    # Fallback: just cap it
    return reason[:cap]


def _parse_continuation_reason(reason: str):
    """Extract (goal, context) from a loop_continuation or loop_escalation reason string.

    Recognized prefixes and their formats:

    "CONTINUATION of: <goal>\\n\\nPass N..."
        → goal=<goal>, context=remainder

    "NARROWED from escalation <id>:\\n\\n<revised goal>\\n\\n..."
        → goal=<revised goal> (second line block), context=full reason

    "ESCALATION — task has been through..."
        → goal extracted from "Original goal: <goal>" line, context=full reason

    Falls back to (reason, "") for unrecognized formats.
    """
    if reason.startswith("CONTINUATION of:"):
        parts = reason.split("\n", 1)
        goal = parts[0].replace("CONTINUATION of:", "").strip()
        context = parts[1].strip() if len(parts) > 1 else ""
        return goal, context

    if reason.startswith("NARROWED from escalation"):
        # Format: "NARROWED from escalation <id>:\n\n<revised goal>\n\n..."
        # The revised goal is the first non-empty line after the prefix line.
        lines = reason.split("\n")
        for line in lines[1:]:
            stripped = line.strip()
            if stripped:
                return stripped, reason
        return reason, ""

    if reason.startswith("ESCALATION —"):
        # Format includes "Original goal: <goal>" line
        for line in reason.split("\n"):
            if line.startswith("Original goal:"):
                goal = line.replace("Original goal:", "").strip()
                return goal, reason
        return reason, ""

    # Fallback: treat the whole reason as the goal
    return reason, ""


def handle_task(
    task: dict,
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
):
    """Route a task_store task to the appropriate handler based on its source.

    - loop_escalation → director.handle_escalation() (judgment call: continue/narrow/close/surface)
    - loop_continuation → run_agent_loop() directly with continuation_depth (already classified AGENDA)
    - all others → handle(reason) (standard text-based routing)

    This is the closure mechanism: escalation tasks don't sit silently in the queue,
    they route to the director for a reasoned decision.
    """
    source = task.get("source", "")
    reason = task.get("reason", "")
    try:
        depth = int(task.get("continuation_depth", 0))
    except (TypeError, ValueError):
        depth = 0
    job_id = task.get("job_id", "unknown")

    if source == "loop_escalation":
        from director import handle_escalation
        log.info("handle_task routing escalation job_id=%s depth=%d", job_id, depth)
        return handle_escalation(task, adapter=adapter, dry_run=dry_run, verbose=verbose)

    elif source == "loop_continuation":
        # Continuations are already classified AGENDA — skip intent classification overhead.
        # Extract the original goal cleanly; pass accomplished/remaining context as ancestry
        # so the planner gets focused decomposition ("this is pass N, remaining work is X")
        # rather than treating the full blob as a new goal to plan from scratch.
        log.info("handle_task routing continuation job_id=%s depth=%d", job_id, depth)
        _cont_goal, _cont_ctx = _parse_continuation_reason(reason)
        from agent_loop import run_agent_loop
        if adapter is None and not dry_run:
            from llm import build_adapter, MODEL_CHEAP
            adapter = build_adapter(model=MODEL_CHEAP)
        _filtered_ctx = _context_firewall(_cont_ctx, depth=depth) if _cont_ctx else ""
        return run_agent_loop(
            _cont_goal,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            continuation_depth=depth,
            ancestry_context_extra=_filtered_ctx,
        )

    else:
        log.info("handle_task routing %s job_id=%s via handle()", source or "unknown", job_id)
        return handle(reason, adapter=adapter, dry_run=dry_run, verbose=verbose)


def drain_task_store(
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
    max_tasks: int = 3,
    sources: tuple = ("loop_continuation", "loop_escalation", "user_goal"),
) -> int:
    """Claim and process queued task_store tasks with known sources.

    Called from the heartbeat or scheduler to consume continuation,
    escalation, and user-enqueued goals. Returns the number processed.

    Args:
        max_tasks: Max tasks to process per call (avoids monopolizing the heartbeat).
        sources: Which task sources to drain. Includes user_goal for
                 ad-hoc goals enqueued via ``poe-enqueue``.
    """
    try:
        from task_store import list_tasks, claim, complete, fail as task_fail
    except ImportError:
        log.warning("drain_task_store: task_store not available")
        return 0

    queued = [
        t for t in list_tasks(status_filter="queued")
        if t.get("source") in sources
    ]
    if not queued:
        return 0

    log.info("drain_task_store: %d queued task(s) to process", len(queued))
    processed = 0

    for task in queued[:max_tasks]:
        job_id = task.get("job_id", "unknown")
        try:
            claim(job_id)
        except Exception as exc:
            log.warning("drain_task_store: failed to claim %s: %s", job_id, exc)
            continue

        try:
            handle_task(task, adapter=adapter, dry_run=dry_run, verbose=verbose)
            try:
                complete(job_id)
            except Exception as _ce:
                log.warning("drain_task_store: failed to mark %s complete: %s", job_id, _ce)
            processed += 1
            log.info("drain_task_store: completed %s", job_id)
            # Emit observable event so the dashboard shows continuation/escalation activity
            try:
                from observe import write_event as _write_event
                _write_event(
                    "task_drained",
                    goal=task.get("reason", "")[:80],
                    project=task.get("parent_job_id", ""),
                    loop_id=job_id,
                    status=task.get("source", ""),
                    detail=f"depth={task.get('continuation_depth', 0)}",
                )
            except Exception:
                pass
        except Exception as exc:
            log.warning("drain_task_store: task %s failed: %s", job_id, exc)
            try:
                task_fail(job_id, str(exc))
            except Exception:
                pass

    return processed


# ---------------------------------------------------------------------------
# Goal queue — user-facing mission enqueue
# ---------------------------------------------------------------------------

def enqueue_goal(
    goal: str,
    *,
    reason: str = "",
    blocked_by: Optional[List[str]] = None,
) -> str:
    """Enqueue a user goal for the director to process sequentially.

    Returns the job_id. The goal will be picked up by ``drain_task_store``
    on the next heartbeat tick (or can be drained manually).

    This is the user-facing "drop goals here" API. Each goal runs through
    ``handle()`` in order — the director gets full discretion over how to
    decompose and execute each one.
    """
    from task_store import enqueue
    task = enqueue(
        lane="agenda",
        source="user_goal",
        reason=reason or goal,
        blocked_by=blocked_by,
    )
    job_id = task["job_id"]
    log.info("enqueue_goal: queued %s — %s", job_id, goal[:80])
    return job_id


def enqueue_goals(goals: List[str], *, sequential: bool = True) -> List[str]:
    """Enqueue multiple goals. If sequential=True, each goal is blocked_by the previous."""
    job_ids = []
    for goal in goals:
        blocked = [job_ids[-1]] if sequential and job_ids else None
        jid = enqueue_goal(goal, blocked_by=blocked)
        job_ids.append(jid)
    return job_ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="poe-handle", description="Poe's unified request handler")
    parser.add_argument("message", nargs="+", help="The request to handle")
    parser.add_argument("--project", "-p", help="Project slug for AGENDA work")
    parser.add_argument("--repo", help="Path to target repo (auto-injects stack context into decompose)")
    parser.add_argument("--model", "-m", help="LLM model string")
    parser.add_argument("--lane", choices=["now", "agenda"], help="Force a specific lane")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    parser.add_argument("--format", choices=["text", "json"], default="text")

    args = parser.parse_args(argv)
    msg = " ".join(args.message)

    result = handle(
        msg,
        project=args.project,
        repo_path=args.repo or "",
        model=args.model,
        force_lane=args.lane,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    print(result.format(mode=args.format))
    return 0 if result.status == "done" else 1


def enqueue_main(argv=None):
    """CLI entry point for ``poe-enqueue``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="poe-enqueue",
        description="Enqueue goals for the director to process sequentially.",
    )
    parser.add_argument("goals", nargs="+", help="Goal(s) to enqueue. Each arg is one goal.")
    parser.add_argument(
        "--parallel", action="store_true",
        help="Allow goals to run in parallel (default: sequential, each waits for previous)",
    )
    parser.add_argument(
        "--drain", action="store_true",
        help="After enqueueing, immediately drain the queue (run goals now).",
    )
    parser.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)
    job_ids = enqueue_goals(args.goals, sequential=not args.parallel)

    for i, (goal, jid) in enumerate(zip(args.goals, job_ids)):
        print(f"  [{i+1}] {jid} — {goal[:80]}")
    print(f"\n{len(job_ids)} goal(s) queued ({'sequential' if not args.parallel else 'parallel'})")

    if args.drain:
        print("\nDraining queue...")
        n = drain_task_store(verbose=args.verbose, max_tasks=len(job_ids))
        print(f"Processed {n} task(s)")

    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
