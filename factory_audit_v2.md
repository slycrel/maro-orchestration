=== BITTER LESSON AUDIT: factory_full_sim.py ===

## 5 CRITICAL GAPS (ranked)

1. [CRITICAL] Ralph verify loop absent
   Sim: recovery only after 2 consecutive cycle failures
   Mainline: verification_agent.verify_step() called after EVERY step; RETRY triggers inline re-exec
   Impact: stuck steps silently degrade quality with no feedback loop

2. [CRITICAL] Single adversarial pass vs 3-critic LLM Council
   Sim: one adversarial framing — sycophancy-vulnerable
   Mainline: devil_advocate + domain_skeptic + implementation_critic; escalate if 2+ WEAK
   Impact: overclaimed outputs pass review; single-framing bias

3. [HIGH] No model-tier escalation signal
   Sim: no needs_escalation field, no path to re-run at higher tier
   Mainline: quality_gate.next_model_tier() + escalate flag wire to --model upgrade
   Impact: weak outputs on cheap model have no recovery path

4. [HIGH] Evolver threshold dead in practice
   Sim: fires every 5 cycles — goals typically complete in 1-2
   Mainline: fires on any inspector friction ticket
   Impact: Evolver self-improvement loop is effectively disabled

5. [MEDIUM] Memory compression unenforced at scaffold level
   Sim: run_cycle copies memory dict verbatim with no size limits
   Mainline: tiered memory with explicit decay + hybrid BM25+RRF retrieval
   Impact: hot layer bloats across cycles; LLM compression is behavioral not structural

---

## v2 SIM_SYSTEM (ready to copy — replaces SIM_SYSTEM in factory_full_sim.py)

SIM_SYSTEM_V2 = textwrap.dedent("""
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
  Output verify field in each executed_step.

SKEPTIC COUNCIL (replaces single adversarial pass):
  Run 3 critics — output one adversarial_findings array:
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

MEMORY (tiered — hard limits enforced by caller):
  hot: Current cycle. Max 5 items, 150 chars each.
  warm: Prior cycle summaries. Max 8 items, 100 chars each. Compress after cycle 1.
  cold: Older entries. Max 10 items, 60 chars each.

RECOVERY PLANNER: If inspector_verdict RETRY for >2 consecutive cycles on same step,
  trigger recovery: reframe, try alternative, split, or mark partial.

Output ONLY valid JSON each cycle. Structure:

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
  "memory": {"hot": ["max 5 x 150"], "warm": ["max 8 x 100"], "cold": ["max 10 x 60"]},
  "recovery_action": null,
  "final_output": "deliverable — only on last cycle or COMPLETE",
  "status": "running|partial|complete",
  "metrics": {"steps_done": N, "steps_stuck": N, "adversarial_downgrades": N, "needs_escalation": false}
}

Rules:
- Target <700 tokens total per cycle output. Ruthlessly concise.
- [UNCERTAIN] marks genuinely unknown claims. Never hedge.
- status=complete requires non-null final_output.
- Memory hard limits are enforced by the scaffold — do not exceed them.
""").strip()

---

## Script Diff (factory_full_sim.py)

### 1. run_cycle — enforce memory hard limits after state.memory = new_mem

```python
# After: state.memory = new_mem
# Add:
for key, max_n, max_c in [("hot", 5, 150), ("warm", 8, 100), ("cold", 10, 60)]:
    state.memory[key] = [e[:max_c] for e in state.memory.get(key, [])[:max_n]]
```

### 2. main() — change --cycles default

```python
# Before: parser.add_argument("--cycles", type=int, default=5, ...)
# After:
parser.add_argument("--cycles", type=int, default=3, help="Max cycles (default: 3)")
```

### 3. print_cycle — show verify field and needs_escalation

```python
# In verbose step display, change:
print(f"  {status_icon} {s['step'][:60]}")
# To:
verify = f"[{s.get('verify','?')}] " if s.get('verify') else ""
print(f"  {status_icon} {verify}{s['step'][:60]}")

# In header line, change:
print(f"Status: {status} | Inspector: {verdict} | Steps: {metrics.get('steps_done',0)} done / {metrics.get('steps_stuck',0)} stuck")
# To:
esc = " | NEEDS_ESCALATION" if metrics.get('needs_escalation') else ""
print(f"Status: {status} | Inspector: {verdict} | Steps: {metrics.get('steps_done',0)} done / {metrics.get('steps_stuck',0)} stuck{esc}")
```

### 4. Replace SIM_SYSTEM with SIM_SYSTEM_V2 (rename constant)

```python
# Before: SIM_SYSTEM = textwrap.dedent(...)
# After: SIM_SYSTEM = SIM_SYSTEM_V2 (paste v2 content above)
# In run_cycle: LLMMessage(role="system", content=SIM_SYSTEM)  # unchanged
```

### Summary: 4 diff sites, +~15 LOC, net token cost +90/cycle, projected savings from tighter step budgets: -150/cycle = net -60 tokens/cycle vs current.