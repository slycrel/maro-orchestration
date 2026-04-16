# Constraint Orchestration — Critical Review & Scope Correction

**Status:** Review + addendum to `CONSTRAINT_ORCHESTRATION_DESIGN.md`
**Date:** 2026-04-16 (same day as the design)
**Context:** Fresh-eyes review by an independent agent with no conversation context. Findings identified a load-bearing scope narrowing during synthesis (planning-only, when the originating conversation was about judgment + validation at all levels) and several concrete implementation gaps.

---

## How to read this document

`CONSTRAINT_ORCHESTRATION_DESIGN.md` is preserved unchanged — it represents the decision point as it stood when synthesis completed. This document captures:

- The critical review findings (verbatim, for signal preservation)
- The scope correction (what the conversation was actually about vs. what the design captured)
- The revised minimal experiment (substantially smaller than the design's "minimal profile")
- Known issues to fix before any code lands

Future work should read both. The design doc is the architectural frame; this doc is the reality check.

---

## Scope Correction

The originating conversation covered **judgment and validation across all levels of execution**, not just the pre-planning phase. Specifically it surfaced the frame "stages done ≠ completed goal" — the observation that the system declares success when the loop exits, which is a different thing than whether the goal was actually achieved.

The design doc narrowed this to **constraint-setting before decomposition**. The narrowing is legitimate as a scoping move (you can't do everything in one phase), but the design did not name it explicitly as a narrowing, which made the subsequent "this addresses the problem" framing misleading.

The review's sharpest observation:

> "A perfect constraint set on 'build a headless server' would not have caught slycrel-go's actual failure. The server 'must work against a real browser' is a sensible constraint, but nothing in the proposed lifecycle actually runs a browser. Detection tops out at 'LLM judgment against the constraint' for semantic items, and the LLM is exactly the thing that already judged it done."

Restated: the design improves the **planning** phase of a system whose biggest defect is in the **verification** phase. Those are different phases. Constraint-setting is useful but does not close the loop that the originating conversation was actually about.

**Implication for the design:** Constraint orchestration and verification-with-real-feedback are siblings, not the same thing. The real north star is both together — bounded planning *and* ground-truth verification. Either alone is partial. The minimal experiment should acknowledge this.

---

## Critical Review (verbatim)

### 1. Blind spots and unstated assumptions

**The biggest one, sitting in plain sight: `src/constraint.py` already exists and means something different.** It's the pre-execution HITL/risk harness (HIGH/MEDIUM/LOW flags, DESTROY/EXTERNAL/WRITE/READ tiers, circuit breaker for dynamic patterns). The design doc uses "constraint" to mean a *scope-narrowing architectural commitment*, which is a completely different concept. The integration table casually lists "Inspector gains constraint-violation as a friction signal type" without noticing that `constraint.check_step_constraints()` already produces a thing called a "violation" that feeds the inspector. When implementation starts, someone will either namespace-collide, shadow the existing module, or worse, conflate the two concepts in prompts. Rename early ("scope rail," "boundary," "premise," "commitment" — pick anything) or this lands as technical debt the moment the first PR opens.

**The human gate is treated as if it's always reachable.** `handle.py` already distinguishes `channel is not None` (ask via Telegram/Slack) from "CLI path" (return `clarification_needed`). The design says "Human gate (unless yolo)" as if that's binary. For the autonomous/heartbeat/cron path — the path Jeremy actually cares about — there is no channel; the goal is running unattended. So in the dominant use case this gate will either (a) silently skip, reducing the feature to "LLM inversion prompt + no oversight," (b) block and escalate, which breaks the autonomous loop, or (c) degrade to "send it to Telegram and wait N minutes then proceed." The doc never names which. This is not a minor tension — it's load-bearing for whether the mechanism improves anything in the regime Poe actually operates in.

**"AGENDA goals above N words" is a worse gate than the doc admits.** Word count doesn't correlate with needing inversion. "rewrite the Polymarket edge ledger to include a staleness decay function" is short and benefits enormously from constraints. "also please update the docs and tests for anything you change" is longer and benefits from none. The doc shrugs at this ("gate decision is itself judgment, systematizing it is the outer instance of the problem") but ships the word-count heuristic anyway. That heuristic will misfire often enough to prejudice the signal from Phase A's whole experiment.

**The inversion pass assumes the LLM can enumerate failure modes a user would recognize as complete.** Inversion is a known technique; it also has a known failure mode: LLMs produce generic failures ("insufficient error handling," "missing tests") that aren't actually the ones that'd bite the specific goal. The slycrel-go example list in the conversation log is beautifully specific — but it's specific in hindsight, written by Claude *after the run failed*. There's no evidence yet the system generates that quality of inversion upfront. The entire design rests on "inversion works," and there's no small test of that claim before investment.

**Prerequisite never stated: personas need content that embodies an actual perspective.** Today `persona.py` is largely a system-prompt mechanism with the skeptic modifier and persona-for-goal selection. The design says "personas in this frame are not system-prompt costumes. They are distinct perspectives." That's aspirational — the codebase doesn't have that yet. The design depends on having engineer/PM/architect personas that *reliably* draw different inversion lines. If the three personas all inherit the same base prompt and diverge only by a system-level one-liner, the triad will produce nearly identical constraint lists with a 3x token cost. No test is proposed to catch this.

**"Completion standard is subsumed" is hand-waved.** The current completion-standard injection is a concrete mechanism; "subsumed" is a word, not a migration plan. What happens during the rollout window? Does completion-standard still run? If both run, do they contradict? This is the kind of integration gap that shows up as confusion in goal logs, not as a bug.

### 2. Missing angles

- **Cost observability.** Phase 64 already documents the director adds up to 8 calls per run. Add three persona inversions + a constraint-review trigger + retrieval-time relevance judgments, and the per-goal cost floor roughly doubles *before* any replanning happens. The doc mentions "token economics" as an open question but does not propose any metric, budget, or kill switch. Given the April 7–9 token-burn incident, this deserves explicit accounting: measure baseline cost/goal now, set a ceiling for constraint-enabled runs, trip a circuit breaker on exceeding it.

- **Debuggability when it misfires.** If the bounded planner produces nothing useful, the failure will look identical to "planner produced nothing useful" without constraints — same status, same logs, harder to diagnose because now there's an extra layer producing invisibly-wrong inputs. No proposal for a "why is the constraint set this way?" inspector view, or a way to re-run a goal *without* constraints for A/B comparison. You will want that within the first three real failures.

- **Backwards compatibility with existing goals in flight.** What about an in-progress AGENDA run when the feature flag flips? What about `continuation_depth > 0` cases where the original goal got bounded but the continuation hits the re-entry without the constraint set? Phase 64's restart already has to carry forward ancestry context; constraints add another thing that must be preserved across restart boundaries. The doc names "break = invalidate constraints and re-run inversion" but doesn't specify whether ordinary restarts preserve, discard, or refresh constraints.

- **Interaction with concurrent loops.** `team:` prefix and the DAG executor run steps in parallel. Do all parallel workers share the same constraint set? If two workers draw conflicting implementation decisions that each satisfy the constraints independently but together violate one, who catches that? Not specified. The design reads as if single-lane AGENDA is the only lane.

- **What happens when constraint-setting itself fails.** Three LLM calls, one per persona. If one returns garbage, or one rate-limits, or one diverges wildly from the others — what's the fallback? Skip? Retry? Fall back to the other two? Abort? This is exactly the kind of failure the Polymarket rate-limit notes say the system currently handles poorly. It's going to recur here.

- **Retrieval for injection is hand-waved harder than the doc acknowledges.** "Retrieval, not blanket injection" — retrieved by what? If it's LLM-judged relevance, that's another per-step call. If it's keyword/embedding match, that's another subsystem. The design silently punts this to "Phase D" while relying on it to solve the token-crowding objection.

- **The memory of constraint outcomes has no retrieval story.** "Constraint-outcome recording feeds memory layer" — the memory layer already exists and has its own retrieval semantics (knowledge_web, knowledge_lens). Dropping constraint records into it without a defined retrieval path means they sit inert. Phase D handwaves this; the minimal profile ignores it entirely.

### 3. Inference vs. grounded reasoning

The load-bearing analogies, and what they'd cost if wrong:

- **"Senior engineers do this naturally" / "expert teams work this way."** Asserted, not shown. The rectangle mental model is Jeremy's — a genuine description of how *he* works. Generalizing from that to "this is how good engineers work, and replicating the mechanism replicates the outcome" is a big leap. It might be that good engineers *feel* like they're drawing constraint lines but are actually doing something subtler (pattern-matching against thousands of failure memories; sensing when their intuition objects). Systematizing the shadow may not capture the substance. If this analogy is wrong, the mechanism is ceremony.

- **"Inversion produces success by elimination."** Cited as Munger. Mental-model-level true, but Munger's inversion is used by a human holding decades of domain knowledge about *which* failure modes are material. An LLM doing inversion in a cold context window is enumerating failures from its training distribution, which isn't the same thing. The design treats the technique as transferable without defending the transfer.

- **"Four or five constraints collapse infinite possibility into a bounded space."** Visually compelling. Not obvious it's the right number, or even the right metaphor — real engineering constraints often *add* work (compliance, compatibility) rather than eliminate it. The rectangle model is a motivating story, not a testable claim.

- **"Constraint-level outcomes are better training signal than step-level outcomes."** Asserted in both docs. Plausible but untested. It might also be that constraint sets are too coarse to be differentiated by outcome — when a constraint set "works," was it because of constraint 2 or constraint 4, or in spite of constraint 3?

- **"Personas are the right frame for refinement, skills for implementation."** The doc leans on this to justify `persona + skill` bundles. It's clean on paper; in practice the current persona system is "system prompt override + selection" and the skill system is "text files injected into context." Whether those two give rise to genuinely different behaviors when bundled is an empirical question the design treats as settled.

Load-bearing ones that would bring the design down if disproved: the expert-analogy claim and the inversion-works claim. Neither has a proposed validation step in the minimal profile beyond "see if outcomes improve," which conflates mechanism effectiveness with LLM capability.

### 4. Second-order effects

If the minimal profile ships:

- **Planners (both humans writing goals and `planner.decompose`) will start writing to the constraint gate.** Goal authors will learn that short goals skip inversion and longer ones don't. You will see goal-length manipulation: padding to trigger the gate, compression to avoid it. The gate heuristic becomes the tail wagging the dog.

- **The clarity-check + BLE-rewrite + constraint-setting chain becomes a gauntlet.** Right now AGENDA entry is: classify → BLE-rewrite → clarity-check → enter loop. Adding constraint-setting between clarity and decompose pushes time-to-first-step from seconds to maybe a minute-plus (three inversion calls + potential human gate). Interactive users will notice. Telegram/Slack users will notice more. Autonomous-cron runs will either skip the gate (losing the value) or block on an unreachable channel (breaking the pipe).

- **First-week surprises:** (a) constraint sets will occasionally contain mutually-incompatible items because the three personas each sounded confident, and the planner will faithfully try to satisfy both, producing weird contortions in the first few steps; (b) the director's new `constraint_review` trigger will fire on normal step-result surprises, producing revise/except churn in the middle of running loops; (c) goals that used to work fine will now fail or route to escalate because the inversion pass introduced a constraint the goal didn't need; (d) token costs for simple goals that tripped the word-count heuristic will spike 2-3x with no visible benefit; (e) someone will notice the name collision with `constraint.py` during a grep and there will be confusion in PRs.

- **The human gate, in interactive use, shifts Jeremy's role** from "exception handler" to "constraint reviewer." On paper this is the right abstraction level — but it's *more* touchpoints per goal, not fewer, until the system learns to set good defaults.

### 5. The honest gut check

The conversation log already asked "have we built a fancy model trainer?" and answered "yes-ish, but orchestration has value independent of learning." The design doc's answer to that doubt is essentially "but the constraint-setting move is load-bearing — it narrows the space the frozen weights operate in, which is the biggest leverage point in-context learning has." That's plausible. It is also exactly the kind of argument that sounds right in the room where it's made, and then doesn't reproduce in the field.

Honest read: **this is sophisticated scaffolding around the same underlying problem** — in the specific sense that what actually went wrong with slycrel-go wasn't that the solution space was too wide. It was that nobody ran a browser. The failure was *ground truth feedback absence*, not scope unboundedness. The rectangle story is genuinely useful, and the inversion frame is genuinely interesting, but they don't address the load-bearing gap the same conversation identified ("verification against reality vs. verification against LLM judgment"). Constraint-setting improves the *planning* phase of a system whose biggest defect is in the *verification* phase.

Put differently: a perfect constraint set on "build a headless server" would not have caught slycrel-go's actual failure. The server "must work against a real browser" is a sensible constraint, but nothing in the proposed lifecycle actually runs a browser. Detection tops out at "LLM judgment against the constraint" for semantic items, and the LLM is exactly the thing that already judged it done.

The design will probably produce *something* — better-factored plans, fewer conceptual contradictions across steps, occasional useful rejections at the gate. It is unlikely to produce the jump to reliability that motivated the conversation.

### 6. What to cut or sharpen

**Keep (the actually-cheap experiment that would produce signal):**
- The inversion pass itself, as a *diagnostic* before planning. One LLM call, one persona (engineer), produce a bullet list of ways this goal fails. Stash it next to the plan. No gate, no human review, no detection, no revision, no triad. Just: does having this list, retrieved into planning context, produce measurably better plans on a corpus of goals?
- Record the inversion + the plan + the outcome in memory, even if nothing retrieves it yet. That's the substrate for ever learning whether this helps.

**Cut (from Phase A, bring back later if signal justifies):**
- The triad. Three personas before evidence that one persona produces anything useful is premature. Run one, establish baseline, then ablate to show the second and third add value.
- The human gate in minimal. It's the feature most likely to degrade poorly in the autonomous path, and it confounds the signal: did outcomes improve because inversion works, or because Jeremy hand-edited the constraints? Hold human gating for later.
- The constraint lifecycle (set/inject/detect/revise/except/break). Beautiful taxonomy, but most of it is Phase 64 machinery re-labeled. Until you have a single real constraint whose violation was actually detected in the wild, the lifecycle is speculative.
- Persona+skill bundles. Premature formalization.

**Sharpen (things the doc fudges):**
- Rename the concept. "Constraint" is taken.
- Name the autonomous-path behavior. What happens when there's no channel?
- Spell out the cost ceiling.
- Define the A/B condition. You cannot evaluate whether bounded planning produces measurably better outcomes without running the same goal both ways.

**The smallest thing that produces signal:** one LLM call that emits a failure-mode list before `planner.decompose()`, passed as additional planner context, recorded alongside the plan, no other changes. Run 20 goals with it on and 20 with it off. That tells you whether the *core hypothesis* has legs. Everything else in the design is scaffolding that only pays off if the core hypothesis is already true.

---

## Scope Correction — What the Original Conversation Was About

The review's observation that the design narrows unacknowledged is correct. For future reference:

**What the conversation surfaced:**
- Judgment and validation at **all levels** of execution
- The "stages done ≠ completed goal" problem — the loop declares success when it exits, not when the goal is actually satisfied
- Verification against reality vs. verification against LLM judgment
- Zoom + rotation as perspective infrastructure throughout execution, not just at a single phase
- Constraints as one *technique* for systematizing judgment — among others

**What the design captured:**
- Pre-planning constraint generation via inversion
- Constraint lifecycle centered on that upfront phase
- Perspective rotation applied specifically to constraint-setting

**What was lost in synthesis:**
- Verification with real ground-truth feedback (running code, hitting endpoints, exercising UIs)
- Judgment application during execution beyond director triggers
- The "nobody ran a browser" class of failure — which is the actual defect that motivated the conversation

The corrected framing: constraint orchestration is one *candidate technique* for one *slice* of the broader problem. The broader problem remains unsolved by this design. A full approach would pair bounded planning with ground-truth verification — probably in a separate design that treats verification as a first-class concern.

---

## Minimum Viable Experiment (revised from the design's "minimal profile")

Per the review's recommendation, the scoped-down experiment:

1. **One inversion call** before `planner.decompose()`, using a single generalist prompt (no triad, no persona specialization for v1)
2. **Inversion output** is a bullet list of 3–7 ways this goal definitively fails
3. **Injected** as additional context in the planner call (not as a separate constraint artifact yet)
4. **Recorded** alongside the plan in the existing artifact directory
5. **A/B mechanism** — a flag (or prefix) that runs the same goal with and without the inversion, so we can actually measure

That's it. No gate, no lifecycle, no violation detection, no persona rotation, no memory retrieval, no director integration beyond passing the text to the planner.

**What this tells us:** Whether having a failure-mode list in planning context produces measurably better plans. That is the core hypothesis. If yes: invest in the fuller design. If no: the rest of the design is moot.

**Cost bound:** +1 LLM call per AGENDA goal, no additional per-step calls. Easy to budget, easy to kill switch.

**Rename for any implementation work:** "scope rail," "premise," or "commitment" — to avoid the collision with `constraint.py`. Strawman: **premise** (short, memorable, reads naturally in code: `goal_premises`, `generate_premises()`, `inject_premises()`).

---

## Known Issues Before Any Code Lands

- [ ] **Rename.** `constraint.py` is taken; the current design's use of "constraint" must be renamed before any imports are written.
- [ ] **Autonomous path.** Document what happens when there's no channel. Default should probably be "no gate, log the inversion output for post-hoc review, continue."
- [ ] **Cost ceiling.** Define a per-goal token ceiling for inversion-enabled runs. Trip a soft-disable when exceeded.
- [ ] **A/B mechanism.** Before enabling anywhere, add the ability to run a goal with inversion on *and* off for comparison.
- [ ] **Existing-systems review.** Before layering new infrastructure, audit what already exists along these lines. `constraint.py` was a surprise; there are likely others.

---

## What Comes Next

- The design doc stays as the architectural frame. This doc is the corrective.
- Before implementing, run an audit of existing systems for overlap/inefficiency (separate task).
- Before expanding from the minimum experiment, validate the core hypothesis with an A/B on a small corpus of real goals.
- Verification with real feedback is a sibling concern that needs its own design. Constraint work without it closes half the loop.

---

## Origin

Review conducted by independent agent with no conversation context on 2026-04-16. Findings delivered in response to the question "what's being inferred or missed?" — findings preserved verbatim above for signal. Scope correction and minimum experiment added by the conversation participants in response to the review.
