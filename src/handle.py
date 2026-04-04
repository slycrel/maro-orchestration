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
import os
import sys
import time
import uuid

log = logging.getLogger("poe.handle")
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    model: Optional[str] = None,
    adapter=None,
    force_lane: Optional[str] = None,   # "now" | "agenda" | None (auto)
    dry_run: bool = False,
    verbose: bool = False,
) -> HandleResult:
    """Process an incoming request through Poe's handle.

    Args:
        message: The natural language request.
        project: Project slug to attach AGENDA work to.
        model: LLM model override.
        adapter: Pre-built LLMAdapter (skips build_adapter).
        force_lane: Override classification ("now" or "agenda").
        dry_run: Simulate without API calls.
        verbose: Print progress.

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

    # Apply user/CONFIG.md defaults (non-fatal — bad config never blocks a run)
    _cfg = _load_user_config()
    if model is None:
        _tier = _cfg.get("default_model_tier", "").strip().lower()
        if _tier in ("cheap", "mid", "power"):
            model = _tier

    # effort: prefix modifier — "effort:low/mid/high <goal>" overrides model tier
    # Stripped before classification so the effort keyword doesn't affect routing.
    _EFFORT_MAP = {"low": "cheap", "mid": "mid", "high": "power"}
    _msg_lower = message.lower()
    for _effort_level, _effort_tier in _EFFORT_MAP.items():
        _effort_prefix = f"effort:{_effort_level}"
        if _msg_lower.startswith(_effort_prefix):
            message = message[len(_effort_prefix):].lstrip()
            if model is None:
                model = _effort_tier
            break

    # mode:thin prefix — route to factory_thin loop (faster, lower cost, Haiku by default)
    # Use when wall-time matters more than depth. Strips prefix before routing.
    _use_thin_mode = False
    if message.lower().startswith("mode:thin"):
        message = message[len("mode:thin"):].lstrip()
        _use_thin_mode = True

    # btw: prefix — non-blocking observation mode. Surfaces a quick note without
    # spawning a full loop. The result is tagged as an observation, not a work product.
    # Good for "btw: I noticed the API is rate-limiting us" style side-notes.
    _btw_mode = False
    if message.lower().startswith("btw:"):
        message = message[len("btw:"):].lstrip()
        _btw_mode = True

    # ultraplan: prefix — deep planning mode. Uses power model + max_steps=12.
    # For complex multi-part goals that need thorough decomposition.
    _ultraplan_max_steps = None
    if message.lower().startswith("ultraplan:"):
        message = message[len("ultraplan:"):].lstrip()
        if model is None:
            model = "power"
        _ultraplan_max_steps = 12

    # direct: prefix — skip Director, route immediately to run_agent_loop.
    # Experiment: for simple goals, Director SPEC+challenge+dispatch overhead
    # adds cost without improving output quality (Bitter Lesson).
    _direct_mode = False
    if message.lower().startswith("direct:"):
        message = message[len("direct:"):].lstrip()
        _direct_mode = True

    # Magic keyword prefixes — mutate execution behaviour without changing the goal.
    # ralph:/verify: — enable Ralph per-step verify loop (retries if verifier says RETRY).
    # pipeline: — inject strong data pipeline enforcement for data-heavy goals.
    # strict: — enable thorough quality passes (council + debate + cross-ref).
    _ralph_prefix = False
    _pipeline_prefix = False
    _strict_prefix = False
    _msg_lower_current = message.lower()
    if _msg_lower_current.startswith("ralph:"):
        message = message[len("ralph:"):].lstrip()
        _ralph_prefix = True
    elif _msg_lower_current.startswith("verify:"):
        message = message[len("verify:"):].lstrip()
        _ralph_prefix = True
    if message.lower().startswith("pipeline:"):
        message = message[len("pipeline:"):].lstrip()
        _pipeline_prefix = True
    if message.lower().startswith("strict:"):
        message = message[len("strict:"):].lstrip()
        _strict_prefix = True

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
        _btw_t0 = time.monotonic()
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
        # Clarification milestone — check goal clarity before starting (skipped if yolo=true)
        _yolo = _cfg.get("yolo", "false").strip().lower() == "true"
        if not dry_run and not _yolo:
            try:
                from intent import check_goal_clarity
                _clarity = check_goal_clarity(message, adapter=adapter)
                if not _clarity.get("clear"):
                    _q = _clarity.get("question", "Could you clarify the goal?")
                    elapsed = int((time.monotonic() - started_at) * 1000)
                    if verbose:
                        print(f"[poe:{handle_id}] clarity check: UNCLEAR — {_q}", file=sys.stderr, flush=True)
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
            elapsed = int((time.monotonic() - started_at) * 1000)
            _direct_parts = []
            for s in _direct_result.steps:
                if s.status == "done" and s.result:
                    _direct_parts.append(f"**Step {s.index}: {s.text}**\n{s.result}")
            _direct_text = "\n\n---\n\n".join(_direct_parts) if _direct_parts else "[no output]"
            if _direct_result.status == "stuck":
                _direct_text += f"\n\n⚠️ Stuck: {_direct_result.stuck_reason}"
            return HandleResult(
                handle_id=handle_id,
                lane="agenda",
                lane_confidence=confidence,
                classification_reason=reason + " [direct]",
                message=message,
                status=_direct_result.status,
                result=_direct_text,
                project=_direct_result.project or project or "",
                tokens_in=_direct_result.total_tokens_in,
                tokens_out=_direct_result.total_tokens_out,
                elapsed_ms=elapsed,
            )

        _ralph_from_cfg = _cfg.get("ralph_verify", "").strip().lower() == "true"
        _loop_kwargs: dict = dict(
            project=project,
            model=model,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            ralph_verify=_ralph_from_cfg or _ralph_prefix,
        )
        if _ultraplan_max_steps is not None:
            _loop_kwargs["max_steps"] = _ultraplan_max_steps

        loop_result = run_agent_loop(message, **_loop_kwargs)
        elapsed = int((time.monotonic() - started_at) * 1000)

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

        # Build result text from completed steps
        result_parts = []
        for s in loop_result.steps:
            if s.status == "done" and s.result:
                result_parts.append(f"**Step {s.index}: {s.text}**\n{s.result}")
        result_text = "\n\n---\n\n".join(result_parts) if result_parts else "[no output produced]"
        if loop_result.status == "stuck":
            result_text += f"\n\n⚠️ Stuck: {loop_result.stuck_reason}"
        if _contested_claims:
            _claims_text = "\n".join(
                f"- [{c.get('verdict', '?')}] {c.get('claim', '')} — {c.get('reason', '')}"
                for c in _contested_claims
            )
            result_text += f"\n\n---\n\n**⚠️ Adversarial review — contested claims:**\n{_claims_text}"
        if _gate_note:
            result_text += _gate_note

        return HandleResult(
            handle_id=handle_id,
            lane="agenda",
            lane_confidence=confidence,
            classification_reason=reason,
            message=message,
            status=loop_result.status,
            result=result_text,
            project=loop_result.project,
            loop_result=loop_result,
            tokens_in=loop_result.total_tokens_in,
            tokens_out=loop_result.total_tokens_out,
            elapsed_ms=elapsed,
            artifact_path=loop_result.log_path,
        )


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

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
    depth = int(task.get("continuation_depth", 0))
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
        return run_agent_loop(
            _cont_goal,
            adapter=adapter,
            dry_run=dry_run,
            verbose=verbose,
            continuation_depth=depth,
            ancestry_context_extra=_cont_ctx[:600] if _cont_ctx else "",
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
    sources: tuple = ("loop_continuation", "loop_escalation"),
) -> int:
    """Claim and process queued task_store tasks with known sources.

    Called from the heartbeat or scheduler to consume continuation and
    escalation tasks. Returns the number of tasks processed.

    Args:
        max_tasks: Max tasks to process per call (avoids monopolizing the heartbeat).
        sources: Which task sources to drain. Default covers continuation + escalation.
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
            except Exception:
                pass
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
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="poe-handle", description="Poe's unified request handler")
    parser.add_argument("message", nargs="+", help="The request to handle")
    parser.add_argument("--project", "-p", help="Project slug for AGENDA work")
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
        model=args.model,
        force_lane=args.lane,
        dry_run=args.dry_run,
        verbose=args.verbose or True,
    )

    print(result.format(mode=args.format))
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
