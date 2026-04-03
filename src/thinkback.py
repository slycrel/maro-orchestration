"""Phase 47: Thinkback Replay — session-level decision replay for self-improvement.

After a mission completes, replay its decisions with hindsight: given what we know
about the final outcome, would we have made different choices at each step?

Unlike the Evolver (which works at the pattern level across many runs), Thinkback
works at the run level — analyzing a *single* completed session to extract
step-level insights that the post-hoc observer couldn't see in real time.

The loop:
  1. Load a completed LoopResult (from outcomes.jsonl or passed directly)
  2. For each step, ask: given the final outcome, was this the right decision?
  3. Produce a ThinkbackReport: per-step analysis + overall lessons
  4. Optionally write lessons back to memory for future runs

Usage:
    from thinkback import run_thinkback, load_latest_outcome
    report = run_thinkback(loop_result)
    print(report.summary())

    # Or load from outcomes.jsonl and replay the latest
    outcome = load_latest_outcome()
    report = run_thinkback_from_outcome(outcome)

CLI:
    poe-thinkback [--latest] [--outcome-id ID] [--dry-run] [--save]
"""

from __future__ import annotations

import json
import logging
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from llm_parse import safe_float

log = logging.getLogger("poe.thinkback")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_THINKBACK_SYSTEM = textwrap.dedent("""\
    You are a hindsight analyst reviewing an AI agent's completed mission.
    Your job: given what we now know about the final outcome, evaluate each step
    decision and identify where the agent could have done better.

    Be specific and actionable. "The agent should have done X at step N because Y."
    Not "the agent could have been more thorough."

    Focus on:
    - Steps where the agent chose the wrong tool or approach
    - Steps that wasted effort on things that didn't contribute to the final outcome
    - Steps where the agent missed something that later caused a problem
    - Decision points where a different choice would have changed the trajectory
    - Signs of stuck loops, overclaiming, or premature completion

    Respond with JSON matching this exact schema:
    {
      "step_reviews": [
        {
          "step_index": <int>,
          "step_summary": "<30-word summary of what the step did>",
          "decision_quality": "good" | "acceptable" | "poor",
          "hindsight_note": "<what would have been better, or why this was right>",
          "counterfactual": "<what we'd do differently now — null if no change>"
        }
      ],
      "overall_assessment": "strong" | "acceptable" | "weak",
      "mission_efficiency": <float 0.0-1.0>,
      "key_lessons": ["<lesson 1>", "<lesson 2>", "<lesson 3>"],
      "would_retry": <bool>,
      "retry_strategy": "<how to approach this goal differently next time — null if would_retry=false>"
    }
""").strip()


_THINKBACK_USER_TEMPLATE = textwrap.dedent("""\
    MISSION REPLAY
    ==============
    Goal: {goal}
    Final Status: {status}
    Steps completed: {done_count}/{total_count}
    Total tokens: {tokens_in}in + {tokens_out}out

    STEP-BY-STEP DECISIONS:
    {steps_block}

    FINAL OUTCOME:
    {outcome_summary}

    Given this complete picture, review each step decision with hindsight.
    What would you do differently? What worked well?
""").strip()


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class StepReview:
    step_index: int
    step_summary: str
    decision_quality: str      # "good" | "acceptable" | "poor"
    hindsight_note: str
    counterfactual: Optional[str] = None


@dataclass
class ThinkbackReport:
    run_id: str
    goal: str
    status: str
    step_reviews: List[StepReview]
    overall_assessment: str    # "strong" | "acceptable" | "weak"
    mission_efficiency: float  # 0.0-1.0
    key_lessons: List[str]
    would_retry: bool
    retry_strategy: Optional[str]
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def summary(self) -> str:
        poor = [r for r in self.step_reviews if r.decision_quality == "poor"]
        good = [r for r in self.step_reviews if r.decision_quality == "good"]
        lines = [
            f"ThinkbackReport run_id={self.run_id}",
            f"  goal={self.goal!r}",
            f"  status={self.status} assessment={self.overall_assessment}",
            f"  efficiency={self.mission_efficiency:.0%}",
            f"  steps: {len(good)} good / {len(self.step_reviews) - len(good) - len(poor)} acceptable / {len(poor)} poor",
            f"  would_retry={self.would_retry}",
        ]
        if self.key_lessons:
            lines.append("  lessons:")
            for l in self.key_lessons:
                lines.append(f"    - {l}")
        if self.retry_strategy:
            lines.append(f"  retry_strategy: {self.retry_strategy}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Outcome loading helpers
# ---------------------------------------------------------------------------

def _outcomes_path() -> Path:
    """Resolve memory/outcomes.jsonl path."""
    try:
        from orch_items import memory_dir
        return memory_dir() / "outcomes.jsonl"
    except Exception:
        return Path("memory/outcomes.jsonl")


def load_latest_outcome(task_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Load the most recent outcome record, optionally filtered by task_type."""
    path = _outcomes_path()
    if not path.exists():
        return None
    last = None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if task_type is None or rec.get("task_type") == task_type:
                    last = rec
            except json.JSONDecodeError:
                continue
    except OSError:
        return None
    return last


def load_outcome_by_id(outcome_id: str) -> Optional[Dict[str, Any]]:
    """Load a specific outcome by outcome_id."""
    path = _outcomes_path()
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("outcome_id") == outcome_id:
                    return rec
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Core thinkback logic
# ---------------------------------------------------------------------------

def _build_steps_block(steps: List[Dict[str, Any]]) -> str:
    """Format step list for the thinkback prompt."""
    if not steps:
        return "(no step data available)"
    lines = []
    for s in steps:
        idx = s.get("index", "?")
        text = s.get("text", "")[:80]
        status = s.get("status", "?")
        result_preview = (s.get("result", "") or "")[:200].replace("\n", " ")
        conf = s.get("confidence", "")
        conf_tag = f" [{conf}]" if conf else ""
        lines.append(
            f"Step {idx} [{status}]{conf_tag}: {text}\n"
            f"  → {result_preview}"
        )
    return "\n\n".join(lines)


def _parse_thinkback_response(text: str, total_steps: int) -> Dict[str, Any]:
    """Extract JSON from LLM thinkback response."""
    # Find JSON block
    start = text.find("{")
    if start == -1:
        return {}
    # Find matching close
    depth = 0
    end = start
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def run_thinkback(
    loop_result,  # LoopResult from agent_loop
    *,
    adapter=None,
    dry_run: bool = False,
    save_lessons: bool = False,
) -> ThinkbackReport:
    """Replay a completed LoopResult through hindsight analysis.

    Args:
        loop_result: A LoopResult from run_agent_loop()
        adapter: LLM adapter for analysis (builds cheap adapter if None)
        dry_run: If True, return a stub report without calling LLM
        save_lessons: If True, write extracted lessons back to memory

    Returns:
        ThinkbackReport with per-step analysis and lessons
    """
    run_id = getattr(loop_result, "loop_id", str(uuid.uuid4())[:8])
    goal = getattr(loop_result, "goal", "unknown goal")
    status = getattr(loop_result, "status", "unknown")
    steps = getattr(loop_result, "steps", [])
    tokens_in = getattr(loop_result, "total_tokens_in", 0)
    tokens_out = getattr(loop_result, "total_tokens_out", 0)

    # Build outcome summary
    done = [s for s in steps if getattr(s, "status", "") == "done"]
    outcome_parts = []
    for s in done[-3:]:  # last 3 done steps = final state
        result = (getattr(s, "result", "") or "")[:200]
        outcome_parts.append(f"Step {s.index}: {result}")
    outcome_summary = "\n".join(outcome_parts) or f"Mission ended with status={status}"

    # Convert steps to dicts for prompt building
    steps_dicts = [
        {
            "index": getattr(s, "index", i),
            "text": getattr(s, "text", ""),
            "status": getattr(s, "status", ""),
            "result": getattr(s, "result", ""),
            "confidence": getattr(s, "confidence", ""),
        }
        for i, s in enumerate(steps)
    ]

    if dry_run:
        return ThinkbackReport(
            run_id=run_id,
            goal=goal,
            status=status,
            step_reviews=[
                StepReview(
                    step_index=s["index"],
                    step_summary=s["text"][:40],
                    decision_quality="acceptable",
                    hindsight_note="[dry-run: no analysis]",
                )
                for s in steps_dicts
            ],
            overall_assessment="acceptable",
            mission_efficiency=0.5,
            key_lessons=["[dry-run: no lessons]"],
            would_retry=False,
            retry_strategy=None,
        )

    # Build adapter if not provided
    if adapter is None:
        try:
            from llm import build_adapter
            adapter = build_adapter("cheap")
        except Exception as exc:
            log.warning("thinkback: could not build adapter: %s", exc)
            return run_thinkback(loop_result, dry_run=True)

    # Build prompt
    user_msg = _THINKBACK_USER_TEMPLATE.format(
        goal=goal,
        status=status,
        done_count=len(done),
        total_count=len(steps),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        steps_block=_build_steps_block(steps_dicts),
        outcome_summary=outcome_summary,
    )

    # Call LLM
    parsed: Dict[str, Any] = {}
    try:
        from llm import LLMMessage
        messages = [
            LLMMessage(role="system", content=_THINKBACK_SYSTEM),
            LLMMessage(role="user", content=user_msg),
        ]
        resp = adapter.complete(messages, tools=[])
        raw = resp.content or ""
        parsed = _parse_thinkback_response(raw, len(steps))
    except Exception as exc:
        log.warning("thinkback: LLM call failed: %s", exc)
        parsed = {}

    # Parse step reviews
    step_reviews = []
    for sr_data in parsed.get("step_reviews", []):
        step_reviews.append(StepReview(
            step_index=sr_data.get("step_index", 0),
            step_summary=sr_data.get("step_summary", ""),
            decision_quality=sr_data.get("decision_quality", "acceptable"),
            hindsight_note=sr_data.get("hindsight_note", ""),
            counterfactual=sr_data.get("counterfactual"),
        ))

    # Fallback: empty reviews for steps we didn't get back
    reviewed_indices = {r.step_index for r in step_reviews}
    for s in steps_dicts:
        if s["index"] not in reviewed_indices:
            step_reviews.append(StepReview(
                step_index=s["index"],
                step_summary=s["text"][:40],
                decision_quality="acceptable",
                hindsight_note="[no analysis returned]",
            ))

    step_reviews.sort(key=lambda r: r.step_index)

    key_lessons = parsed.get("key_lessons", [])
    report = ThinkbackReport(
        run_id=run_id,
        goal=goal,
        status=status,
        step_reviews=step_reviews,
        overall_assessment=parsed.get("overall_assessment", "acceptable"),
        mission_efficiency=safe_float(parsed.get("mission_efficiency"), default=0.5, min_val=0.0, max_val=1.0),
        key_lessons=key_lessons,
        would_retry=bool(parsed.get("would_retry", False)),
        retry_strategy=parsed.get("retry_strategy"),
    )

    # Optionally save lessons back to memory
    if save_lessons and key_lessons:
        try:
            from memory import record_outcome
            # We don't re-record the outcome, but we do use the lesson mechanism
            # Write directly to lessons.jsonl
            _save_thinkback_lessons(goal, key_lessons, run_id)
        except Exception as exc:
            log.warning("thinkback: could not save lessons: %s", exc)

    return report


def run_thinkback_from_outcome(
    outcome: Dict[str, Any],
    *,
    adapter=None,
    dry_run: bool = False,
    save_lessons: bool = False,
) -> ThinkbackReport:
    """Run thinkback from a raw outcome dict (from outcomes.jsonl).

    Outcome dicts don't have step-level data, so we synthesize a minimal
    LoopResult-like object from the summary + lessons fields.
    """

    class _FakeStep:
        def __init__(self, index: int, text: str, result: str):
            self.index = index
            self.text = text
            self.status = "done"
            self.result = result
            self.confidence = ""

    class _FakeLoopResult:
        pass

    summary = outcome.get("summary", "")
    lessons = outcome.get("lessons", [])
    goal = outcome.get("goal", "")
    status = outcome.get("status", "unknown")

    # Synthesize steps from lessons + summary
    fake_steps = [_FakeStep(0, "Mission execution", summary)]
    for i, lesson in enumerate(lessons, 1):
        fake_steps.append(_FakeStep(i, f"Lesson {i}", lesson))

    obj = _FakeLoopResult()
    obj.loop_id = outcome.get("outcome_id", str(uuid.uuid4())[:8])
    obj.goal = goal
    obj.status = status
    obj.steps = fake_steps
    obj.total_tokens_in = outcome.get("tokens_in", 0)
    obj.total_tokens_out = outcome.get("tokens_out", 0)

    return run_thinkback(obj, adapter=adapter, dry_run=dry_run, save_lessons=save_lessons)


def _save_thinkback_lessons(goal: str, lessons: List[str], run_id: str) -> None:
    """Write thinkback-derived lessons to lessons.jsonl."""
    try:
        from orch_items import memory_dir
        lessons_path = memory_dir() / "lessons.jsonl"
    except Exception:
        lessons_path = Path("memory/lessons.jsonl")

    now = datetime.now(timezone.utc).isoformat()
    task_type = "general"  # thinkback lessons are cross-cutting

    try:
        with open(lessons_path, "a", encoding="utf-8") as f:
            for lesson_text in lessons:
                if not lesson_text.strip():
                    continue
                rec = {
                    "lesson_id": str(uuid.uuid4())[:8],
                    "task_type": task_type,
                    "outcome": "done",
                    "lesson": f"[thinkback:{run_id}] {lesson_text}",
                    "source_goal": goal,
                    "confidence": 0.65,
                    "times_applied": 0,
                    "times_reinforced": 0,
                    "recorded_at": now,
                }
                f.write(json.dumps(rec) + "\n")
    except OSError as exc:
        log.warning("thinkback: could not write lessons: %s", exc)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli_main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="poe-thinkback",
        description="Replay a completed mission with hindsight analysis.",
    )
    p.add_argument("--latest", action="store_true", help="Replay the latest outcome")
    p.add_argument("--outcome-id", metavar="ID", help="Replay a specific outcome by ID")
    p.add_argument("--task-type", metavar="TYPE", help="Filter by task_type when using --latest")
    p.add_argument("--dry-run", action="store_true", help="Stub analysis, no LLM calls")
    p.add_argument("--save", action="store_true", help="Save extracted lessons to memory")
    p.add_argument("--model", default="cheap", help="Model tier for analysis (default: cheap)")
    args = p.parse_args(argv)

    if not args.latest and not args.outcome_id:
        p.error("Specify --latest or --outcome-id ID")

    if args.outcome_id:
        outcome = load_outcome_by_id(args.outcome_id)
        if outcome is None:
            print(f"ERROR: outcome {args.outcome_id!r} not found", flush=True)
            return 1
    else:
        outcome = load_latest_outcome(task_type=args.task_type)
        if outcome is None:
            print("ERROR: no outcomes found in memory/outcomes.jsonl", flush=True)
            return 1

    print(f"Replaying outcome: {outcome.get('outcome_id')} / {outcome.get('goal', '')[:60]!r}")

    adapter = None
    if not args.dry_run:
        try:
            from llm import build_adapter
            adapter = build_adapter(args.model)
        except Exception as exc:
            print(f"WARNING: could not build adapter ({exc}), using dry-run mode", flush=True)

    report = run_thinkback_from_outcome(
        outcome,
        adapter=adapter,
        dry_run=args.dry_run,
        save_lessons=args.save,
    )

    print()
    print(report.summary())
    print()

    poor = [r for r in report.step_reviews if r.decision_quality == "poor"]
    if poor:
        print("POOR DECISIONS:")
        for r in poor:
            print(f"  Step {r.step_index}: {r.step_summary}")
            print(f"    → {r.hindsight_note}")
            if r.counterfactual:
                print(f"    → Alternative: {r.counterfactual}")
        print()

    if args.save:
        print(f"Lessons saved to memory ({len(report.key_lessons)} entries)")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli_main())
