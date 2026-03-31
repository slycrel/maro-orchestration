"""Factory Mode — Thin Loop variant.

Keeps the step execution loop (decompose → execute → compile) but strips
all Mode 2 scaffolding: no Director, no Inspector, no persona routing,
no lesson injection, no skill matching, no multi-plan comparison,
no pre-plan challenger, no user context injection.

Just a goal, a clean system prompt, and the loop mechanics.

The system prompts are written as behavior descriptions ("here is what
good looks like") rather than rule systems ("do X then Y then Z").
This is the Bitter Lesson framing: describe the desired outcome,
let the model figure out the how.

Usage:
    python3 factory_thin.py "your goal here" [--model cheap|mid|power]
    poe-factory-thin "your goal here"
"""

from __future__ import annotations

import json
import logging
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

log = logging.getLogger("poe.factory.thin")


# ---------------------------------------------------------------------------
# System prompts — behavior descriptions, not rule systems
# ---------------------------------------------------------------------------

FACTORY_DECOMPOSE = textwrap.dedent("""\
    You are an autonomous agent. Break the goal into 4-8 concrete steps
    that together produce a complete, high-quality result.

    Good steps: specific, independently executable, produce a clear artifact.
    Bad steps: vague, bundled ("research and analyze"), or procedural fluff.

    Steps that involve research should produce specific findings, not "gather info".
    The final step should synthesize everything into a usable deliverable.

    Respond ONLY with a JSON array of step strings. No prose.
    Each step under 20 words.
""").strip()

FACTORY_STEP = textwrap.dedent("""\
    You are an autonomous execution agent completing one step of a larger goal.

    Do the work. Produce a specific, complete result — not a plan for doing it.
    If the step involves research: cite evidence quality (RCT, meta-analysis,
    observational, animal/in-vitro). Grade each claim: Tier 1/2/3.
    If the step involves synthesis: make concrete recommendations, not hedges.

    Use complete_step() with your result when done.
    Use flag_stuck() only if you're genuinely blocked (missing data, access issue).
    Never flag stuck because the task is hard.
""").strip()

FACTORY_COMPILE = textwrap.dedent("""\
    Compile the step results into a final polished deliverable.
    Lead with the key findings. Be specific and actionable.
    No filler. No restating what was asked. Just the work product.
""").strip()


# ---------------------------------------------------------------------------
# Minimal step data types
# ---------------------------------------------------------------------------

@dataclass
class ThinStep:
    index: int
    text: str
    status: str = "pending"   # pending | done | stuck
    result: str = ""
    tokens: int = 0


@dataclass
class ThinLoopResult:
    loop_id: str
    goal: str
    status: str           # done | stuck | partial
    steps: List[ThinStep]
    final_report: str
    total_tokens: int
    cost_usd: float
    elapsed_ms: int
    model: str


# ---------------------------------------------------------------------------
# Core thin loop
# ---------------------------------------------------------------------------

def run_factory_thin(
    goal: str,
    *,
    model: str = "cheap",
    max_steps: int = 8,
    verbose: bool = False,
) -> ThinLoopResult:
    """Run the thin factory loop: decompose → execute → compile."""
    from llm import build_adapter, LLMMessage, ToolCall

    loop_id = str(uuid.uuid4())[:8]
    started_at = time.monotonic()
    total_tokens_in = 0
    total_tokens_out = 0

    def _log(msg: str):
        if verbose:
            print(f"[factory:thin:{loop_id}] {msg}", file=sys.stderr, flush=True)

    _log(f"model={model} goal={goal[:60]!r}")

    adapter = build_adapter(model=model)

    # --- Step 1: Decompose ---
    _log("decomposing...")
    decompose_resp = adapter.complete(
        [
            LLMMessage("system", FACTORY_DECOMPOSE),
            LLMMessage("user", f"Goal: {goal}\n\nDecompose into {max_steps} or fewer steps."),
        ],
        max_tokens=512,
        temperature=0.3,
    )
    total_tokens_in += decompose_resp.input_tokens
    total_tokens_out += decompose_resp.output_tokens

    step_texts = _parse_steps(decompose_resp.content, max_steps)
    if not step_texts:
        step_texts = [goal]
    _log(f"decomposed into {len(step_texts)} steps")

    steps = [ThinStep(index=i+1, text=t) for i, t in enumerate(step_texts)]

    # --- Step 2: Execute each step ---
    _tools = [
        {
            "name": "complete_step",
            "description": "Record the completed result for this step.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "The complete result of this step."},
                },
                "required": ["result"],
            },
        },
        {
            "name": "flag_stuck",
            "description": "Flag that this step cannot be completed.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why this step is blocked."},
                },
                "required": ["reason"],
            },
        },
    ]

    completed_context = ""
    for step in steps:
        _log(f"executing step {step.index}: {step.text[:50]!r}")

        context_block = f"\n\nCompleted so far:\n{completed_context}" if completed_context else ""
        resp = adapter.complete(
            [
                LLMMessage("system", FACTORY_STEP),
                LLMMessage("user",
                    f"Overall goal: {goal}\n\n"
                    f"Current step ({step.index}/{len(steps)}): {step.text}"
                    + context_block
                ),
            ],
            tools=_tools,
            tool_choice="required",
            max_tokens=4096,
            temperature=0.2,
        )
        total_tokens_in += resp.input_tokens
        total_tokens_out += resp.output_tokens
        step.tokens = resp.input_tokens + resp.output_tokens

        if resp.tool_calls:
            tc = resp.tool_calls[0]
            if tc.name == "complete_step":
                step.status = "done"
                step.result = tc.arguments.get("result", "")
                completed_context += f"\nStep {step.index} ({step.text[:40]}): {step.result[:200]}"
                _log(f"step {step.index} done tokens={step.tokens}")
            elif tc.name == "flag_stuck":
                step.status = "stuck"
                step.result = tc.arguments.get("reason", "")
                _log(f"step {step.index} stuck: {step.result[:60]}")
        else:
            # No tool call — treat content as result
            step.status = "done"
            step.result = resp.content
            completed_context += f"\nStep {step.index} ({step.text[:40]}): {step.result[:200]}"

    # --- Step 3: Compile final report ---
    done_steps = [s for s in steps if s.status == "done"]
    _log(f"compiling report from {len(done_steps)}/{len(steps)} done steps...")

    step_summaries = "\n\n".join(
        f"Step {s.index}: {s.text}\n{s.result[:800]}"
        for s in done_steps
    )
    compile_resp = adapter.complete(
        [
            LLMMessage("system", FACTORY_COMPILE),
            LLMMessage("user",
                f"Goal: {goal}\n\nStep results:\n{step_summaries}\n\n"
                f"Compile into a final structured report."
            ),
        ],
        max_tokens=4096,
        temperature=0.1,
    )
    total_tokens_in += compile_resp.input_tokens
    total_tokens_out += compile_resp.output_tokens
    final_report = compile_resp.content

    # --- Metrics ---
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    total_tokens = total_tokens_in + total_tokens_out

    try:
        from metrics import estimate_cost
        cost = estimate_cost(total_tokens_in, total_tokens_out, model=model)
    except Exception:
        cost = 0.0

    stuck_count = sum(1 for s in steps if s.status == "stuck")
    if len(done_steps) == 0:
        status = "stuck"
    elif stuck_count > 0:
        status = "partial"
    else:
        status = "done"

    _log(f"loop done status={status} tokens={total_tokens:,} cost=${cost:.4f} elapsed={elapsed_ms//1000}s")

    return ThinLoopResult(
        loop_id=loop_id,
        goal=goal,
        status=status,
        steps=steps,
        final_report=final_report,
        total_tokens=total_tokens,
        cost_usd=cost,
        elapsed_ms=elapsed_ms,
        model=model,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_steps(content: str, max_steps: int) -> List[str]:
    start = content.find("[")
    end = content.rfind("]") + 1
    if start >= 0 and end > start:
        try:
            steps = json.loads(content[start:end])
            if isinstance(steps, list) and all(isinstance(s, str) for s in steps):
                return [s.strip() for s in steps if s.strip()][:max_steps]
        except (json.JSONDecodeError, ValueError):
            pass
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    p = argparse.ArgumentParser(description="Factory thin loop: decompose→execute→compile")
    p.add_argument("goal", help="Goal to execute")
    p.add_argument("--model", default="cheap", choices=["cheap", "mid", "power",
                   "claude-haiku-4-5-20251001", "claude-sonnet-4-6", "claude-opus-4-6"],
                   help="Model tier or full model ID")
    p.add_argument("--max-steps", type=int, default=8)
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--out", help="Write final report to file")
    args = p.parse_args()

    result = run_factory_thin(
        args.goal,
        model=args.model,
        max_steps=args.max_steps,
        verbose=args.verbose,
    )

    print(result.final_report)
    if args.out:
        Path(args.out).write_text(result.final_report)

    done = sum(1 for s in result.steps if s.status == "done")
    print(
        f"\n--- factory:thin stats ---\n"
        f"status: {result.status}  steps: {done}/{len(result.steps)} done\n"
        f"tokens: {result.total_tokens:,}  cost: ${result.cost_usd:.4f}  "
        f"time: {result.elapsed_ms//1000}s",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
