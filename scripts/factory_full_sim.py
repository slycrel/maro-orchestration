#!/usr/bin/env python3
"""
factory_full_sim.py — Simulate the full OpenClaw orchestration architecture in one prompt loop.

Tests the Bitter Lesson thesis: can the entire flywheel (Director → Workers → Inspector →
Evolver → tiered memory → recovery) be expressed as a pure behavioral prompt + thin loop,
without the mainline scaffolding?

Usage:
    python3 scripts/factory_full_sim.py "your goal here" [--model cheap|mid|power] [--cycles N] [--verbose] [--out FILE]
    python3 scripts/factory_full_sim.py "nootropic stack for focus and memory" --model cheap --verbose
"""

import argparse
import json
import sys
import time
import textwrap
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm import build_adapter, MODEL_CHEAP, MODEL_MID, MODEL_POWER, LLMMessage
from metrics import estimate_cost

# ─── Constants ───────────────────────────────────────────────────────────────

MODEL_MAP = {"cheap": MODEL_CHEAP, "mid": MODEL_MID, "power": MODEL_POWER}
DEFAULT_STATE_FILE = Path("factory_sim_state.json")

# The mega-prompt. Describes the full architecture as desired behavior.
# Bitter Lesson style: outcome-first, no micromanagement.
SIM_SYSTEM = textwrap.dedent("""
You are OpenClaw — an autonomous orchestration system simulating its own full architecture in one prompt.
Each cycle you run the complete flywheel internally:

DIRECTOR: Decompose the goal into concrete parallel-friendly steps. Be outcome-first.
  Good steps: specific, independently executable, produce a clear artifact.
  Bad steps: vague, bundled, or procedural fluff.
  Identify which steps can run in parallel (group them).

WORKERS: Execute each step. For research steps: cite evidence quality (RCT, meta-analysis,
  observational, animal/in-vitro). Grade each claim Tier 1/2/3. Target <500 tokens per step.
  Use bullet points and structured data. Never quote verbatim — extract key facts.

INSPECTOR + QUALITY GATE: Two-pass review.
  Pass 1 — check for stuck steps, missing artifacts, vague outputs.
  Pass 2 — verify the steps together form a complete answer to the goal.
  Verdict: PROCEED | RETRY (specify which step and why) | ABORT.

ADVERSARIAL VERIFIER: Challenge every significant claim.
  Grade each: CONFIRMED | DOWNGRADED | CONTESTED | OVERCLAIMED.
  Cite competing evidence, mechanism flaws, or context mismatches.
  Skip claims that are clearly solid. Focus on what would change a decision.

EVOLVER (every 5 cycles or on repeated failure): Scan the cycle history for patterns.
  Propose one concrete improvement: a prompt tweak, guardrail, or step strategy change.
  Mark it as AUTO_APPLY (low-risk) or SUGGEST (needs human review).

MEMORY (tiered):
  hot: Current cycle working set — step results, artifacts, running hypotheses (full detail).
  warm: Prior cycle summaries — compress to ~100 chars each after cycle 2.
  cold: Older entries — single-sentence summaries only.

RECOVERY PLANNER: If inspector_verdict is RETRY for >2 consecutive cycles on same step,
  trigger recovery: reframe the step, try an alternative approach, split it, or mark partial.

Output ONLY valid JSON each cycle. No prose outside the JSON. Structure:

{
  "cycle": N,
  "director_plan": {
    "steps": ["step text", ...],
    "parallel_groups": [[0, 1], [2], [3, 4]],
    "rationale": "why this decomposition"
  },
  "executed_steps": [
    {
      "step": "step text",
      "status": "done | stuck | partial",
      "result": "findings, max 500 tokens",
      "artifacts": ["file.py", "data.json"],
      "evidence_grades": [{"claim": "...", "tier": 1}]
    }
  ],
  "inspector_verdict": "PROCEED | RETRY | ABORT",
  "inspector_notes": "what needs fixing, or null",
  "adversarial_findings": [
    {"claim": "...", "grade": "CONFIRMED|DOWNGRADED|CONTESTED|OVERCLAIMED", "reason": "..."}
  ],
  "evolver_suggestion": {"type": "AUTO_APPLY|SUGGEST", "description": "..."} | null,
  "memory": {
    "hot": ["current working items..."],
    "warm": ["prior summaries compressed to ~100 chars..."],
    "cold": ["old one-liners..."]
  },
  "recovery_action": "what recovery was triggered, or null",
  "final_output": "polished deliverable for user — only on last cycle or when COMPLETE",
  "status": "running | partial | complete",
  "metrics": {
    "steps_done": N,
    "steps_stuck": N,
    "adversarial_downgrades": N
  }
}

Rules:
- Target <1000 tokens total per cycle output. Be ruthlessly concise.
- [UNCERTAIN] marks genuinely unknown claims. Never hedge with "consult an expert".
- If status=complete, final_output must be non-null and be the actual deliverable.
- Only mark complete when goal is fully answered. Use partial if meaningful progress was made.
- Memory.hot entries carry forward across cycles. Compress ruthlessly.
""").strip()


# ─── Data ────────────────────────────────────────────────────────────────────

@dataclass
class SimCycle:
    cycle: int
    raw: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    elapsed_ms: int = 0
    parse_error: Optional[str] = None


@dataclass
class SimState:
    goal: str
    model: str
    cycles: list = field(default_factory=list)
    memory: dict = field(default_factory=lambda: {"hot": [], "warm": [], "cold": []})
    cycle_count: int = 0
    total_tokens: int = 0
    total_cost: float = 0.0
    status: str = "running"
    final_output: Optional[str] = None


# ─── State Persistence ───────────────────────────────────────────────────────

def save_state(state: "SimState", path: Path) -> None:
    """Persist resumable state. Excludes full cycle history (too large)."""
    data = {
        "goal": state.goal,
        "model": state.model,
        "memory": state.memory,
        "cycle_count": state.cycle_count,
        "total_tokens": state.total_tokens,
        "total_cost": state.total_cost,
        "status": state.status,
        "final_output": state.final_output,
        "saved_at": datetime.utcnow().isoformat(),
    }
    path.write_text(json.dumps(data, indent=2))


def load_state(path: Path) -> Optional[dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
    return None


# ─── Core Loop ───────────────────────────────────────────────────────────────

def build_user_message(goal: str, state: SimState) -> str:
    ctx = {
        "goal": goal,
        "cycle_number": state.cycle_count + 1,
        "prior_memory": state.memory,
        "cycle_history_summary": [
            {
                "cycle": c.raw.get("cycle"),
                "status": c.raw.get("status"),
                "inspector_verdict": c.raw.get("inspector_verdict"),
                "steps_done": c.raw.get("metrics", {}).get("steps_done", 0),
                "steps_stuck": c.raw.get("metrics", {}).get("steps_stuck", 0),
            }
            for c in state.cycles[-5:]  # last 5 cycles only
        ],
    }
    return (
        f"GOAL: {goal}\n\n"
        f"CONTEXT:\n{json.dumps(ctx, indent=2)}\n\n"
        f"Run cycle {state.cycle_count + 1}. Output ONLY valid JSON."
    )


def run_cycle(goal: str, adapter, state: SimState, verbose: bool = False, state_file: Optional[Path] = None) -> SimCycle:
    state.cycle_count += 1
    user_msg = build_user_message(goal, state)

    messages = [
        LLMMessage(role="system", content=SIM_SYSTEM),
        LLMMessage(role="user", content=user_msg),
    ]

    t0 = time.time()
    response = adapter.complete(
        messages=messages,
        temperature=0.2,
        max_tokens=4096,
    )
    elapsed_ms = int((time.time() - t0) * 1000)

    cycle = SimCycle(
        cycle=state.cycle_count,
        tokens_in=response.input_tokens,
        tokens_out=response.output_tokens,
        elapsed_ms=elapsed_ms,
    )
    cycle.cost_usd = estimate_cost(response.input_tokens, response.output_tokens, response.model)

    raw_text = response.content.strip()

    # Strip markdown fences if present
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        raw_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        cycle.raw = json.loads(raw_text)
    except json.JSONDecodeError as e:
        cycle.parse_error = str(e)
        cycle.raw = {"error": "JSON parse failed", "raw_excerpt": raw_text[:300]}
        if verbose:
            print(f"  [PARSE ERROR] {e}")
            print(f"  Raw excerpt: {raw_text[:500]}")

    # Update state from cycle output
    if not cycle.parse_error:
        # Carry memory forward
        new_mem = cycle.raw.get("memory", {})
        if new_mem:
            state.memory = new_mem

        state.status = cycle.raw.get("status", state.status)
        if cycle.raw.get("final_output"):
            state.final_output = cycle.raw["final_output"]

    state.total_tokens += cycle.tokens_in + cycle.tokens_out
    state.total_cost += cycle.cost_usd
    state.cycles.append(cycle)

    if state_file:
        save_state(state, state_file)

    return cycle


def print_cycle(cycle: SimCycle, verbose: bool = False):
    r = cycle.raw
    verdict = r.get("inspector_verdict", "?")
    status = r.get("status", "?")
    metrics = r.get("metrics", {})
    adv = r.get("adversarial_findings", [])
    downgrades = [f for f in adv if f.get("grade") in ("DOWNGRADED", "CONTESTED", "OVERCLAIMED")]

    print(f"\n{'='*60}")
    print(f"CYCLE {cycle.cycle} | {cycle.elapsed_ms}ms | ${cycle.cost_usd:.4f} | {cycle.tokens_in+cycle.tokens_out:,}tok")
    print(f"Status: {status} | Inspector: {verdict} | Steps: {metrics.get('steps_done',0)} done / {metrics.get('steps_stuck',0)} stuck")

    if cycle.parse_error:
        print(f"  [!] Parse error: {cycle.parse_error}")
        return

    if verbose:
        # Director plan
        plan = r.get("director_plan", {})
        if plan.get("steps"):
            print(f"\nDirector ({len(plan['steps'])} steps):")
            for i, s in enumerate(plan["steps"]):
                print(f"  {i+1}. {s}")

        # Step results
        steps = r.get("executed_steps", [])
        if steps:
            print(f"\nSteps:")
            for s in steps:
                status_icon = "✓" if s.get("status") == "done" else "~" if s.get("status") == "partial" else "✗"
                result_preview = (s.get("result", "")[:120] + "...") if len(s.get("result", "")) > 120 else s.get("result", "")
                print(f"  {status_icon} {s['step'][:60]}")
                if result_preview:
                    print(f"    → {result_preview}")

    # Adversarial
    if downgrades:
        print(f"\nAdversarial downgrades ({len(downgrades)}):")
        for f in downgrades[:3]:
            print(f"  [{f['grade']}] {f['claim'][:80]} — {f.get('reason', '')[:80]}")

    # Inspector notes
    notes = r.get("inspector_notes")
    if notes and verdict != "PROCEED":
        print(f"\nInspector: {notes[:200]}")

    # Evolver
    evo = r.get("evolver_suggestion")
    if evo:
        print(f"\nEvolver [{evo.get('type','?')}]: {evo.get('description','')[:150]}")

    # Recovery
    recovery = r.get("recovery_action")
    if recovery:
        print(f"\nRecovery: {recovery[:150]}")

    # Final output (abbreviated)
    final = r.get("final_output")
    if final:
        print(f"\n{'─'*60}")
        print("FINAL OUTPUT:")
        final_str = final if isinstance(final, str) else json.dumps(final, indent=2)
        print(final_str[:800] + ("..." if len(final_str) > 800 else ""))


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OpenClaw full architecture simulation loop")
    parser.add_argument("goal", nargs="?", default="", help="Goal to achieve")
    parser.add_argument("--model", default="cheap", choices=["cheap", "mid", "power"], help="Model tier")
    parser.add_argument("--cycles", type=int, default=5, help="Max cycles before stopping (default: 5)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show step-level detail")
    parser.add_argument("--out", help="Save final output to file")
    parser.add_argument("--interactive", "-i", action="store_true", help="Prompt to continue after each cycle")
    parser.add_argument("--resume", action="store_true", help="Resume from saved state file")
    parser.add_argument("--state-file", default=str(DEFAULT_STATE_FILE), help="State file path for resume (default: factory_sim_state.json)")
    args = parser.parse_args()

    state_file = Path(args.state_file)

    if args.resume:
        saved = load_state(state_file)
        if not saved:
            print(f"No saved state at {state_file}")
            sys.exit(1)
        goal = args.goal.strip() or saved["goal"]
        model = MODEL_MAP.get(args.model, saved.get("model", MODEL_MAP["cheap"]))
        state = SimState(goal=goal, model=model)
        state.memory = saved.get("memory", state.memory)
        state.cycle_count = saved.get("cycle_count", 0)
        state.total_tokens = saved.get("total_tokens", 0)
        state.total_cost = saved.get("total_cost", 0.0)
        state.status = saved.get("status", "running")
        state.final_output = saved.get("final_output")
        print(f"Resuming from cycle {state.cycle_count} (saved {saved.get('saved_at', '?')})")
    else:
        goal = args.goal.strip()
        if not goal:
            goal = input("Goal: ").strip()
        if not goal:
            print("No goal provided.")
            sys.exit(1)
        model = MODEL_MAP[args.model]
        state = SimState(goal=goal, model=model)

    print(f"\nOpenClaw Factory Full Sim")
    print(f"Goal:  {goal}")
    print(f"Model: {args.model} ({model})")
    print(f"Max cycles: {args.cycles}")
    print(f"{'='*60}")

    t_start = time.time()

    for _ in range(args.cycles):
        cycle = run_cycle(goal, adapter, state, verbose=args.verbose, state_file=state_file)
        print_cycle(cycle, verbose=args.verbose)

        if state.status == "complete":
            break

        if args.interactive:
            cont = input("\nContinue? (y/n): ").strip().lower()
            if cont != "y":
                break

    # Summary
    elapsed = time.time() - t_start
    print(f"\n{'='*60}")
    print(f"DONE — {state.cycle_count} cycles | {elapsed:.0f}s | {state.total_tokens:,} tokens | ${state.total_cost:.4f}")
    print(f"Final status: {state.status}")

    if state.final_output:
        final_str = state.final_output if isinstance(state.final_output, str) else json.dumps(state.final_output, indent=2)
        if not args.verbose:
            print(f"\nFINAL OUTPUT:")
            print(final_str)
        if args.out:
            Path(args.out).write_text(final_str)
            print(f"\nSaved to {args.out}")
    else:
        print("\n[No final_output produced — increase --cycles or try --model mid]")


if __name__ == "__main__":
    main()
