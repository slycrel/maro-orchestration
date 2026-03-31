# Bitter Lesson Analysis — Applied to openclaw-orchestration

Source: Grok feedback (grok-response-3.txt), Miessler's BLE blog, Zakin's Mode 1/2/3 taxonomy.
Date: 2026-03-30

## The Core Challenge

We're Mode 2 (agent orchestrator: human gives goal → agents execute). The north star is Mode 3 (factory: agents interpret signals and specify their own work). The risk: our Mode 2 scaffolding becomes legacy cruft as models improve.

## What vs How Audit

**Things that are "what" (keep, strengthen):**
- VISION.md / goals / user context
- Outcome recording + lessons extraction
- The persona registry (what role, not how to act)
- Token budget / cost tracking (constraint, not logic)
- `poe-observe` dashboard (visibility)

**Things that are "how" (candidates for thinning):**
- CEO → Director → Worker → Inspector hierarchy (4 layers of human logic)
- `persona_for_goal()` auto-select (we engineered the routing, model could do it)
- Sheriff / heartbeat recovery (we built the stuck detection, Phase 44 could replace it)
- Structured logging pipeline (necessary now, but shouldn't grow more)
- Parallel fan-out with locks (execution detail the model shouldn't need to know about)

**Things already on the Mode 3 path (validate and accelerate):**
- Evolver (proposes improvements from outcomes — needs to become more autonomous)
- Diagnosis → lesson → planner injection loop (self-improving decomposition)
- Skills auto-promotion (learned behaviors replacing engineered ones)
- Recovery planner (auto-applying fixes without human intervention)

## Concrete Actions

### NOW (this sprint)

1. **"What vs How" guardrail in director decompose**
   When the goal contains imperative steps ("do X then Y then Z"), auto-strip them and re-prompt as pure outcome + context. Let the planner decompose from the outcome, not the instructions.
   File: `src/director.py` or `src/planner.py`
   Effort: S

2. **Data pipeline strategy** (already added to step_exec.py)
   Steps should build scripts to fetch/filter data rather than dumping raw output into context. This is "what" (I need filtered data) not "how" (here's 50KB of JSON to parse in-context).
   Status: DONE

3. **USER/ context folder**
   Formalize `USER/GOALS.md`, `USER/PREFERENCES.md`, `USER/SIGNALS.md` that get auto-injected into every mission. Keeps human context rich without baking execution logic.
   File: New `user/` dir + injection in `src/planner.py`
   Effort: S

### NEXT (after stability sprint)

4. **Evolver signal scanning**
   Extend meta-evolver to scan recent outcomes for "business signals" and propose new sub-missions autonomously. E.g., "Polymarket liquidity dropped → propose arbitrage investigation."
   This is the Mode 2 → Mode 3 bridge.
   File: `src/evolver.py`
   Effort: M

5. **Skip-Director experiment**
   For simple NOW-lane goals, try skipping the Director entirely and letting the planner decompose directly. The Bitter Lesson predicts this works for simple goals. Use replay data to compare.
   Effort: S (test), M (if we ship it)

6. **Thin the sheriff**
   Phase 44 auto-diagnose + recovery planner can replace most of what the sheriff does. Run both in parallel for a sprint, compare, then remove the heavier one.
   Effort: M

### LATER (Mode 3 territory)

7. **Signal interpretation layer**
   New module that reads external signals (Polymarket API, GitHub activity, email/Telegram, cost trends) and proposes missions without human prompting. This IS Mode 3.
   Effort: L

8. **Self-specification guardrail**
   For every new feature proposal (from evolver or human), ask: "does this help the agent interpret signals and self-specify work (Mode 3), or does it just make human specs easier (Mode 2)?" Filter accordingly.
   Effort: S (prompt change in evolver)
