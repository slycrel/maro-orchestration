#!/usr/bin/env python3
"""Phase 1: Autonomous loop runner for Poe orchestration.

The critical unlock: give Poe a goal, watch it work until done or stuck.

Loop model:
    goal → decompose → for each step: [act → observe → decide] → done | stuck

Usage:
    from agent_loop import run_agent_loop
    result = run_agent_loop("research winning polymarket strategies", project="polymarket-research")
    print(result.summary())

CLI:
    python -m agent_loop "your goal here" [--project SLUG] [--model MODEL] [--dry-run]
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Imports (lazy to avoid circular with orch)
# ---------------------------------------------------------------------------

def _orch():
    """Lazy import of orch module — resolves sys.path issues."""
    import orch
    return orch


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class StepOutcome:
    index: int
    text: str
    status: str          # "done" | "blocked" | "skipped"
    result: str          # LLM's text output for this step
    iteration: int       # which loop iteration produced this
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0


@dataclass
class LoopResult:
    loop_id: str
    project: str
    goal: str
    status: str          # "done" | "stuck" | "error"
    steps: List[StepOutcome] = field(default_factory=list)
    stuck_reason: Optional[str] = None
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    elapsed_ms: int = 0
    log_path: Optional[str] = None

    def summary(self) -> str:
        done = sum(1 for s in self.steps if s.status == "done")
        blocked = sum(1 for s in self.steps if s.status == "blocked")
        lines = [
            f"loop_id={self.loop_id}",
            f"project={self.project}",
            f"goal={self.goal!r}",
            f"status={self.status}",
            f"steps_done={done}/{len(self.steps)} blocked={blocked}",
            f"tokens={self.total_tokens_in}in+{self.total_tokens_out}out",
            f"elapsed_ms={self.elapsed_ms}",
        ]
        if self.stuck_reason:
            lines.append(f"stuck_reason={self.stuck_reason!r}")
        if self.log_path:
            lines.append(f"log={self.log_path}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_DECOMPOSE_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous planning agent.
    When given a goal, decompose it into 3-6 concrete, independently-executable steps.
    Each step should be a clear action or deliverable, not vague meta-steps.
    Respond ONLY with a JSON array of step strings. No prose. Example:
    ["step one description", "step two description", "step three description"]
""").strip()

_EXECUTE_SYSTEM = textwrap.dedent("""\
    You are Poe, an autonomous execution agent.
    You are given a goal and a specific step to complete.
    Complete the step to the best of your ability, producing a concrete result.
    Then call exactly one tool:
      - complete_step: if you have successfully completed this step
      - flag_stuck: if you genuinely cannot complete this step (explain why precisely)
    Do NOT call flag_stuck for solvable problems — work through them first.
    Be thorough but concise. Output quality matters.
""").strip()

_EXECUTE_TOOLS = [
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
]


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def run_agent_loop(
    goal: str,
    *,
    project: Optional[str] = None,
    model: Optional[str] = None,
    adapter=None,
    max_steps: int = 8,
    max_iterations: int = 20,
    dry_run: bool = False,
    verbose: bool = False,
) -> LoopResult:
    """Run the autonomous loop for a goal.

    Args:
        goal: Natural language goal description.
        project: Existing project slug to attach to, or None to auto-create.
        model: LLM model string (defaults to MODEL_CHEAP).
        adapter: Pre-built LLMAdapter instance (skips build_adapter()).
        max_steps: Maximum steps to decompose the goal into.
        max_iterations: Hard cap on total LLM calls.
        dry_run: Simulate without LLM calls (uses stub responses).
        verbose: Print progress to stdout.

    Returns:
        LoopResult with full outcome.
    """
    from llm import LLMMessage, LLMTool, build_adapter, MODEL_CHEAP

    loop_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()
    start_ts = datetime.now(timezone.utc).isoformat()

    if verbose:
        print(f"[poe] loop_id={loop_id} goal={goal!r}", file=sys.stderr, flush=True)

    # Build adapter
    if adapter is None and not dry_run:
        adapter = build_adapter(model=model or MODEL_CHEAP)
    elif dry_run:
        adapter = _DryRunAdapter()

    # Resolve or create project
    o = _orch()
    if project and not o.project_dir(project).exists():
        o.ensure_project(project, goal[:80])
        if verbose:
            print(f"[poe] created project={project}", file=sys.stderr, flush=True)
    elif not project:
        slug = _goal_to_slug(goal)
        project = slug
        if not o.project_dir(project).exists():
            o.ensure_project(project, goal[:80])
            if verbose:
                print(f"[poe] created project={project}", file=sys.stderr, flush=True)

    # Load goal ancestry for prompt injection
    try:
        from ancestry import get_project_ancestry, build_ancestry_prompt
        _proj_dir = o.project_dir(project)
        _ancestry = get_project_ancestry(_proj_dir)
        _ancestry_context = build_ancestry_prompt(_ancestry, current_task=goal)
    except Exception:
        _ancestry_context = ""

    # Step 1: Decompose goal into steps (inject memory + ancestry context)
    if verbose:
        print(f"[poe] decomposing goal...", file=sys.stderr, flush=True)
    try:
        from memory import inject_lessons_for_task
        _lessons_context = inject_lessons_for_task("agenda", goal, max_lessons=3)
    except Exception:
        _lessons_context = ""
    steps = _decompose(
        goal, adapter, max_steps=max_steps, verbose=verbose,
        lessons_context=_lessons_context, ancestry_context=_ancestry_context,
    )
    if verbose:
        print(f"[poe] decomposed into {len(steps)} steps", file=sys.stderr, flush=True)

    # Add steps to project NEXT.md
    step_indices = o.append_next_items(project, steps)
    o.append_decision(project, [
        f"[loop:{loop_id}] Goal: {goal}",
        *[f"- step {i}: {s}" for i, s in enumerate(steps, 1)],
    ])

    # Step 2: Execute each step in order
    step_outcomes: List[StepOutcome] = []
    total_tokens_in = 0
    total_tokens_out = 0
    stuck_streak = 0
    last_action: Optional[str] = None
    iteration = 0
    loop_status = "done"
    stuck_reason = None
    completed_context: List[str] = []

    for step_idx, (step_text, item_index) in enumerate(zip(steps, step_indices)):
        if iteration >= max_iterations:
            loop_status = "stuck"
            stuck_reason = f"hit max_iterations={max_iterations} before completing all steps"
            break

        iteration += 1
        if verbose:
            print(f"[poe] step {step_idx+1}/{len(steps)}: {step_text!r}", file=sys.stderr, flush=True)

        step_start = time.monotonic()
        outcome = _execute_step(
            goal=goal,
            step_text=step_text,
            step_num=step_idx + 1,
            total_steps=len(steps),
            completed_context=completed_context,
            adapter=adapter,
            tools=[LLMTool(**t) for t in _EXECUTE_TOOLS],
            verbose=verbose,
            ancestry_context=_ancestry_context,
        )
        step_elapsed = int((time.monotonic() - step_start) * 1000)

        total_tokens_in += outcome.get("tokens_in", 0)
        total_tokens_out += outcome.get("tokens_out", 0)

        step_status = outcome["status"]
        step_result = outcome.get("result", "")
        step_summary = outcome.get("summary", step_text)

        # Stuck detection: same action repeated 3x
        action_key = f"{step_text}:{step_status}"
        if action_key == last_action:
            stuck_streak += 1
        else:
            stuck_streak = 0
            last_action = action_key

        if stuck_streak >= 2:  # 3rd repeat
            loop_status = "stuck"
            stuck_reason = f"same outcome '{step_status}' on '{step_text}' repeated 3 times"
            step_outcomes.append(StepOutcome(
                index=item_index,
                text=step_text,
                status="blocked",
                result=step_result,
                iteration=iteration,
                tokens_in=outcome.get("tokens_in", 0),
                tokens_out=outcome.get("tokens_out", 0),
                elapsed_ms=step_elapsed,
            ))
            o.mark_item(project, item_index, o.STATE_BLOCKED)
            o.append_decision(project, [f"[loop:{loop_id}] stuck on step {step_idx+1}: {stuck_reason}"])
            break

        # Write artifact
        artifact_path = _write_step_artifact(project, loop_id, step_idx + 1, step_text, step_result)

        if step_status == "done":
            o.mark_item(project, item_index, o.STATE_DONE)
            completed_context.append(f"Step {step_idx+1} ({step_text}): {step_summary}")
            if verbose:
                print(f"[poe] step {step_idx+1} done: {step_summary}", file=sys.stderr, flush=True)
        else:
            o.mark_item(project, item_index, o.STATE_BLOCKED)
            loop_status = "stuck"
            stuck_reason = outcome.get("stuck_reason", f"step {step_idx+1} blocked")
            if verbose:
                print(f"[poe] step {step_idx+1} stuck: {stuck_reason}", file=sys.stderr, flush=True)

        step_outcomes.append(StepOutcome(
            index=item_index,
            text=step_text,
            status=step_status,
            result=step_result,
            iteration=iteration,
            tokens_in=outcome.get("tokens_in", 0),
            tokens_out=outcome.get("tokens_out", 0),
            elapsed_ms=step_elapsed,
        ))

        if loop_status == "stuck":
            break

    # Final summary artifact
    elapsed_total = int((time.monotonic() - started_at) * 1000)
    log_path = _write_loop_log(
        project=project,
        loop_id=loop_id,
        goal=goal,
        status=loop_status,
        steps=step_outcomes,
        start_ts=start_ts,
        elapsed_ms=elapsed_total,
        stuck_reason=stuck_reason,
    )

    o.append_decision(project, [
        f"[loop:{loop_id}] finished status={loop_status} steps={len(step_outcomes)} tokens={total_tokens_in}+{total_tokens_out}",
    ])
    o.write_operator_status()

    result = LoopResult(
        loop_id=loop_id,
        project=project,
        goal=goal,
        status=loop_status,
        steps=step_outcomes,
        stuck_reason=stuck_reason,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        elapsed_ms=elapsed_total,
        log_path=log_path,
    )

    if verbose:
        print(f"[poe] {result.summary()}", file=sys.stderr, flush=True)

    # Phase 5: Reflexion — record outcome + extract lessons
    try:
        from memory import reflect_and_record
        done_steps = [s for s in step_outcomes if s.status == "done"]
        summary = (
            f"Completed {len(done_steps)}/{len(step_outcomes)} steps. "
            + (stuck_reason or "All steps done.")
        )
        reflect_and_record(
            goal=goal,
            status=loop_status,
            result_summary=summary,
            task_type="agenda",
            project=project,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            elapsed_ms=elapsed_total,
            adapter=adapter if not dry_run else None,
            dry_run=dry_run,
        )
    except Exception:
        pass  # Memory failures must never break the loop

    return result


# ---------------------------------------------------------------------------
# Decompose
# ---------------------------------------------------------------------------

def _decompose(
    goal: str,
    adapter,
    max_steps: int,
    verbose: bool = False,
    lessons_context: str = "",
    ancestry_context: str = "",
) -> List[str]:
    """Ask the LLM to decompose a goal into steps. Falls back to heuristic."""
    from llm import LLMMessage

    system = _DECOMPOSE_SYSTEM
    extras = [x for x in [ancestry_context, lessons_context] if x]
    if extras:
        system = _DECOMPOSE_SYSTEM + "\n\n" + "\n\n".join(extras)

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", system),
                LLMMessage("user", f"Goal: {goal}\n\nDecompose into {max_steps} or fewer concrete steps."),
            ],
            max_tokens=1024,
            temperature=0.2,
        )
        content = resp.content.strip()
        # Extract JSON array from response (LLM may wrap in markdown)
        start = content.find("[")
        end = content.rfind("]") + 1
        if start >= 0 and end > start:
            steps = json.loads(content[start:end])
            if isinstance(steps, list) and all(isinstance(s, str) for s in steps):
                return [s.strip() for s in steps if s.strip()][:max_steps]
    except Exception as exc:
        if verbose:
            print(f"[poe] decompose LLM call failed, using heuristic: {exc}", file=sys.stderr, flush=True)

    # Fallback: heuristic decomposition (reuse orch logic)
    o = _orch()
    return o.decompose_goal(goal, max_steps=max_steps)


# ---------------------------------------------------------------------------
# Execute step
# ---------------------------------------------------------------------------

def _execute_step(
    goal: str,
    step_text: str,
    step_num: int,
    total_steps: int,
    completed_context: List[str],
    adapter,
    tools: List[Any],
    verbose: bool = False,
    ancestry_context: str = "",
) -> Dict[str, Any]:
    """Execute one step via the LLM. Returns outcome dict."""
    from llm import LLMMessage

    context_block = ""
    if completed_context:
        context_block = "\n\nCompleted steps so far:\n" + "\n".join(
            f"  - {c}" for c in completed_context
        )

    ancestry_block = f"\n\n{ancestry_context}" if ancestry_context else ""

    user_msg = (
        f"Overall goal: {goal}{ancestry_block}\n\n"
        f"Current step ({step_num}/{total_steps}): {step_text}"
        f"{context_block}\n\n"
        f"Complete this step now. Call complete_step when done or flag_stuck if blocked."
    )

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _EXECUTE_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            tools=tools,
            tool_choice="required",
            max_tokens=4096,
            temperature=0.3,
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "stuck_reason": f"LLM call failed: {exc}",
            "result": "",
            "tokens_in": 0,
            "tokens_out": 0,
        }

    # Parse tool call
    if resp.tool_calls:
        tc = resp.tool_calls[0]
        if tc.name == "complete_step":
            return {
                "status": "done",
                "result": tc.arguments.get("result", resp.content),
                "summary": tc.arguments.get("summary", step_text),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }
        elif tc.name == "flag_stuck":
            return {
                "status": "blocked",
                "stuck_reason": tc.arguments.get("reason", "unknown"),
                "result": tc.arguments.get("attempted", ""),
                "tokens_in": resp.input_tokens,
                "tokens_out": resp.output_tokens,
            }

    # No tool call — treat content as result (some models don't always call tools)
    if resp.content and len(resp.content) > 20:
        return {
            "status": "done",
            "result": resp.content,
            "summary": step_text,
            "tokens_in": resp.input_tokens,
            "tokens_out": resp.output_tokens,
        }

    return {
        "status": "blocked",
        "stuck_reason": "LLM did not call a tool and produced no useful content",
        "result": resp.content,
        "tokens_in": resp.input_tokens,
        "tokens_out": resp.output_tokens,
    }


# ---------------------------------------------------------------------------
# Artifact writing
# ---------------------------------------------------------------------------

def _write_step_artifact(
    project: str,
    loop_id: str,
    step_num: int,
    step_text: str,
    result: str,
) -> Optional[str]:
    """Write a step's result to the project artifacts directory."""
    try:
        o = _orch()
        artifacts_dir = o.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"loop-{loop_id}-step-{step_num:02d}.md"
        path = artifacts_dir / fname
        content = f"# Step {step_num}: {step_text}\n\n{result}\n"
        path.write_text(content, encoding="utf-8")
        return str(path.relative_to(o.orch_root()))
    except Exception:
        return None


def _write_loop_log(
    project: str,
    loop_id: str,
    goal: str,
    status: str,
    steps: List[StepOutcome],
    start_ts: str,
    elapsed_ms: int,
    stuck_reason: Optional[str],
) -> Optional[str]:
    """Write the full loop log JSON to the project artifacts directory."""
    try:
        o = _orch()
        artifacts_dir = o.orch_root() / "prototypes" / "poe-orchestration" / "projects" / project / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        fname = f"loop-{loop_id}-log.json"
        path = artifacts_dir / fname
        payload = {
            "loop_id": loop_id,
            "project": project,
            "goal": goal,
            "status": status,
            "started_at": start_ts,
            "elapsed_ms": elapsed_ms,
            "stuck_reason": stuck_reason,
            "steps": [
                {
                    "index": s.index,
                    "text": s.text,
                    "status": s.status,
                    "result_length": len(s.result),
                    "iteration": s.iteration,
                    "tokens_in": s.tokens_in,
                    "tokens_out": s.tokens_out,
                    "elapsed_ms": s.elapsed_ms,
                }
                for s in steps
            ],
            "totals": {
                "steps_done": sum(1 for s in steps if s.status == "done"),
                "steps_blocked": sum(1 for s in steps if s.status == "blocked"),
                "tokens_in": sum(s.tokens_in for s in steps),
                "tokens_out": sum(s.tokens_out for s in steps),
            },
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path.relative_to(o.orch_root()))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def _goal_to_slug(goal: str) -> str:
    """Convert a goal string to a filesystem-safe project slug."""
    import re
    words = re.sub(r"[^a-z0-9 ]", "", goal.lower()).split()
    slug = "-".join(words[:5])
    return slug or "unnamed-goal"


# ---------------------------------------------------------------------------
# Dry-run adapter (for testing without API credits)
# ---------------------------------------------------------------------------

class _DryRunAdapter:
    """Simulates LLM responses for testing."""

    def complete(self, messages, *, tools=None, tool_choice="auto", max_tokens=4096, temperature=0.3):
        from llm import LLMResponse, ToolCall

        # Extract user message content for context
        user_content = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )

        # Decompose request → return fake steps
        if "decompose" in user_content.lower() or "concrete steps" in user_content.lower():
            goal_line = next((l for l in user_content.split("\n") if l.startswith("Goal:")), "Goal: test")
            goal = goal_line.replace("Goal:", "").strip()
            words = goal.split()[:6]
            steps = [
                f"Research {' '.join(words[:3])}",
                f"Analyze findings from {' '.join(words[:3])}",
                f"Produce summary of {goal[:40]}",
            ]
            return LLMResponse(
                content=json.dumps(steps),
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=30,
            )

        # Execute step → call complete_step
        if tools and tool_choice == "required":
            step_line = next(
                (l for l in user_content.split("\n") if "Current step" in l), "Current step: do work"
            )
            step_text = step_line.split(":", 1)[-1].strip() if ":" in step_line else step_line
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(
                    name="complete_step",
                    arguments={
                        "result": f"[dry-run] Completed: {step_text}",
                        "summary": f"[dry-run] {step_text[:60]}",
                    },
                )],
                stop_reason="tool_use",
                input_tokens=80,
                output_tokens=40,
            )

        return LLMResponse(
            content="[dry-run] OK",
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=5,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="poe-run", description="Run Poe's autonomous loop on a goal")
    parser.add_argument("goal", nargs="+", help="Goal description")
    parser.add_argument("--project", "-p", help="Project slug (auto-created if not exists)")
    parser.add_argument("--model", "-m", help="LLM model string (e.g. anthropic/claude-haiku-4-5)")
    parser.add_argument("--max-steps", type=int, default=6, help="Max decomposition steps (default: 6)")
    parser.add_argument("--max-iterations", type=int, default=20, help="Hard cap on LLM calls (default: 20)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without LLM API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress")

    args = parser.parse_args(argv)
    goal = " ".join(args.goal)

    result = run_agent_loop(
        goal,
        project=args.project,
        model=args.model,
        max_steps=args.max_steps,
        max_iterations=args.max_iterations,
        dry_run=args.dry_run,
        verbose=args.verbose or True,
    )

    print(result.summary())
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
