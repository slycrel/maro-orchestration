#!/usr/bin/env python3
"""
factory_full_sim.py — v3. Self-evolving OpenClaw miniature.

Tests the Bitter Lesson thesis: can the entire flywheel (Director → Workers → Inspector →
Evolver → tiered memory → recovery) be expressed as a pure behavioral prompt + thin loop?

v2 changes (from self-audit via factory_audit_v2.md):
  - Per-step Ralph verify (PASS/RETRY) with inline re-exec on RETRY
  - 3-critic Skeptic Council (devil_advocate / domain_skeptic / implementation_critic)
  - Evolver threshold: every 5 cycles → every 3 cycles OR on repeated step failure
  - Memory hard limits enforced at scaffold level (hot=5×150, warm=8×100, cold=10×60)
  - Token target: 1000 → 700/cycle; step budget: 500 → 400 tokens
  - needs_escalation flag in metrics; verify + ESCALATE shown in print_cycle

v3 changes:
  - Full model-tier escalation on needs_escalation=true (cheap→mid→power)
    Mirrors mainline quality_gate.next_model_tier(). All 5 audit gaps now closed.
  - Default --model changed to mid (per nootropic benchmark: sharper council, fewer tokens)

Nootropic benchmark (v2):
  cheap: 27,896 tok / $0.048 / 62s / 5 steps / all PASS
  mid:   15,854 tok / $0.104 / 69s / 3 steps / all PASS — sharper adversarial

See factory_full_sim_v1.py for baseline. See factory_audit_v2.md for gap analysis.

Usage:
    python3 scripts/factory_full_sim.py "your goal" [--model cheap|mid|power] [--cycles N] [-v] [--out FILE]
    python3 scripts/factory_full_sim.py --resume   # continue interrupted run
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

# Model escalation — mirrors mainline quality_gate.next_model_tier()
def escalate_model(current: str) -> str:
    return {"cheap": "mid", "mid": "power", "power": "power"}.get(current, "mid")

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
  observational, animal/in-vitro). Grade each claim Tier 1/2/3. Target <400 tokens per step.
  Use bullet points and structured data. Never quote verbatim — extract key facts.

VERIFY (per step): After each step, rate PASS or RETRY.
  PASS: result directly addresses step goal with specific content.
  RETRY: result is vague, off-topic, or is a plan rather than work done.
  On RETRY: re-execute once with refined approach. Mark partial if second attempt also fails.
  Record verify field in each executed_step.

SKEPTIC COUNCIL (3 critics, replaces single adversarial pass):
  1. devil_advocate: gaps, unjustified assumptions
  2. domain_skeptic: wrong evidence tier, confounded variables, population mismatch
  3. implementation_critic: missing actionable specifics, internal inconsistency
  Grade each: CONFIRMED | DOWNGRADED | CONTESTED | OVERCLAIMED.
  Set needs_escalation=true in metrics if 2+ critics find WEAK/CONTESTED outputs.

INSPECTOR + QUALITY GATE: Two-pass review.
  Pass 1 — stuck steps, missing artifacts, vague outputs.
  Pass 2 — steps together form complete answer to goal.
  Verdict: PROCEED | RETRY (specify step) | ABORT.

EVOLVER (every 3 cycles OR on any repeated failure — same step RETRY 2+ times):
  Scan cycle history for patterns. Propose one concrete improvement.
  Mark AUTO_APPLY (low-risk) or SUGGEST (needs human review).

MEMORY (tiered — hard limits: hot=5×150, warm=8×100, cold=10×60 chars):
  hot: Current cycle working set. Max 5 items, 150 chars each.
  warm: Prior cycle summaries. Max 8 items, 100 chars each. Compress after cycle 1.
  cold: Older entries. Max 10 items, 60 chars each.

RECOVERY PLANNER: If inspector_verdict is RETRY for >2 consecutive cycles on same step,
  trigger recovery: reframe the step, try an alternative approach, split it, or mark partial.

Output ONLY valid JSON each cycle. No prose outside the JSON. Structure:

{
  "cycle": N,
  "director_plan": {"steps": [...], "parallel_groups": [[0,1],[2],[3,4]], "rationale": "..."},
  "executed_steps": [
    {"step": "...", "status": "done|stuck|partial", "verify": "PASS|RETRY",
     "result": "max 400 tokens", "artifacts": [], "evidence_grades": [{"claim":"...","tier":1}]}
  ],
  "inspector_verdict": "PROCEED|RETRY|ABORT",
  "inspector_notes": null,
  "adversarial_findings": [
    {"critic": "devil_advocate|domain_skeptic|implementation_critic",
     "claim": "...", "grade": "CONFIRMED|DOWNGRADED|CONTESTED|OVERCLAIMED", "reason": "..."}
  ],
  "evolver_suggestion": {"type": "AUTO_APPLY|SUGGEST", "description": "..."} | null,
  "memory": {"hot": ["max 5x150"], "warm": ["max 8x100"], "cold": ["max 10x60"]},
  "recovery_action": null,
  "final_output": "deliverable — only on last cycle or COMPLETE",
  "status": "running|partial|complete",
  "metrics": {"steps_done": N, "steps_stuck": N, "adversarial_downgrades": N, "needs_escalation": false}
}

Rules:
- Target <700 tokens total per cycle output. Ruthlessly concise.
- [UNCERTAIN] marks genuinely unknown claims. Never hedge with "consult an expert".
- status=complete requires non-null final_output with the actual deliverable.
- Memory hard limits are enforced by the scaffold — do not exceed them.
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
        # Carry memory forward with hard limits (hot=5×150, warm=8×100, cold=10×60)
        new_mem = cycle.raw.get("memory", {})
        if new_mem:
            state.memory = new_mem
        for key, max_n, max_c in [("hot", 5, 150), ("warm", 8, 100), ("cold", 10, 60)]:
            state.memory[key] = [e[:max_c] for e in state.memory.get(key, [])[:max_n]]

        state.status = cycle.raw.get("status", state.status)
        if cycle.raw.get("final_output"):
            state.final_output = cycle.raw["final_output"]

        # v3: model-tier escalation on needs_escalation (quality_gate parity)
        if cycle.raw.get("metrics", {}).get("needs_escalation"):
            new_tier = escalate_model(state.model)
            if new_tier != state.model:
                print(f"  [ESCALATE] {state.model} → {new_tier} for next cycle")
                state.model = new_tier

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
    esc = " | ESCALATE" if metrics.get("needs_escalation") else ""
    print(f"Status: {status} | Inspector: {verdict} | Steps: {metrics.get('steps_done',0)} done / {metrics.get('steps_stuck',0)} stuck{esc}")

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
                verify = f"[{s.get('verify','?')}] " if s.get("verify") else ""
                print(f"  {status_icon} {verify}{s['step'][:60]}")
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
    parser.add_argument("--model", default="mid", choices=["cheap", "mid", "power"], help="Model tier (default: mid)")
    parser.add_argument("--cycles", type=int, default=3, help="Max cycles before stopping (default: 3)")
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

    adapter = build_adapter(model=model)

    print(f"\nOpenClaw Factory Full Sim")
    print(f"Goal:  {goal}")
    print(f"Model: {args.model} ({model})")
    print(f"Max cycles: {args.cycles}")
    print(f"{'='*60}")

    t_start = time.time()

    for _ in range(args.cycles):
        # Rebuild adapter if model was escalated last cycle
        if state.model != model:
            model = state.model
            adapter = build_adapter(model=MODEL_MAP[model])
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
