# Constraint Orchestration — Codebase Audit

**Status:** Independent audit (not a design or implementation doc)  
**Date:** 2026-04-16  
**Scope:** Mapping existing codebase infrastructure against proposed constraint orchestration design

---

## Executive Summary

The thesis to test: **most of the proposed "new" machinery has some primitive form already present in the codebase.**

**Verdict: PARTIALLY TRUE, with caveats and critical blocking issues.**

- Infrastructure for context injection into planning, mid-loop director evaluation, memory recording, and signal detection **already exists** and is mature. ~70% of the proposed "machinery" can be built as extensions to existing components.
- **Critical blocker: name collision.** The word "constraint" in `src/constraint.py` means "pre-execution safety check" (runtime enforcement). The design's "constraints" mean "design-time scope-narrowing decisions" (planning input). These are fundamentally different concepts using the same word. **This must be renamed before any implementation code lands.**
- The minimal viable experiment (per the REVIEW.md recommendation) requires ~150 lines of new code: one inversion prompt + one context parameter + one memory type + one A/B flag. The rest is wiring into existing extension points.

---

## Component-by-Component Audit

### 1. `src/pre_flight.py` — Plan critic (post-decompose review)

**What it does:** Runs a cheap Haiku call or multi-lens review (3 specialized Haiku calls) AFTER the planner produces steps. Returns a PlanReview with scope estimate (narrow/medium/wide/unknown), milestone candidates, assumption risks, and unknown-unknowns.

**Overlap with design:** ~10%. Pre-flight reviews decomposed plans; the design proposes constraint-setting BEFORE planning. Different phases, orthogonal concerns. Pre-flight validates the outcome of planning; constraints narrow the input space to planning.

**What design needs:** No changes to pre-flight itself. Constraint-setting would insert as a new phase upstream, then pre-flight would continue to review the resulting plan.

**Conflicts:** None. Pre-flight is independent.

**Recommendation:** **(b) Extension**. No changes needed for the minimal experiment.

---

### 2. `src/constraint.py` — Runtime safety harness

**What it does:** Phase 35 pre-execution constraint enforcement. Checks step text against pattern-based rules (destructive ops, secret access, path escapes, unsafe network, unsafe exec). Classifies risk as LOW/MEDIUM/HIGH. Also implements HITL gating taxonomy (ACTION_TIER_READ/WRITE/DESTROY/EXTERNAL with gate policies). Has a circuit breaker for evolver-generated dynamic constraints.

**Overlap with design:** 0% — *complete semantic mismatch.* This is a RUNTIME safety constraint (prevents dangerous actions from executing). The design proposes DESIGN-TIME constraints (narrows the solution space before planning). Same word, opposite concepts.

**What design needs:** **CRITICAL: Rename the design's concept before any code lands.** Review recommends "premise," "scope rail," "boundary," or "commitment." Naming collision will cause confusion in code, tests, and prompts if not resolved upfront.

**Conflicts:** BLOCKING. If the design calls new constructs "constraints" and they get injected as planner context, code that imports `constraint.py` will collide with mental models. The word is taken.

**Recommendation:** **(d) Contradicts design.** The design must rename its concept. Suggest: **"premise"** (short, reads naturally: `goal_premises`, `generate_premises()`, `inject_premises()`).

---

### 3. `src/inspector.py` — Post-hoc quality observer

**What it does:** Reads outcomes.jsonl after execution. Detects 7 friction signals (error_events, repeated_rephrasing, escalation_tone, platform_confusion, abandoned_tool_flow, backtracking, context_churn). Produces suggestions.jsonl for evolver. Never modifies running loops — purely read-only analysis with 7 signal constants.

**Overlap with design:** ~20%. The design proposes "constraint-violation as a friction signal." Inspector already has a signal framework and categorization. New signal type would fit cleanly. But "constraint violation" is not yet implemented — it's a new signal type the design would add.

**What design needs:** (1) Add `SIGNAL_CONSTRAINT_VIOLATION` constant alongside existing signals. (2) Implement detector logic (during/after steps, check if a promised property was violated). (3) Wire detector output to inspector's signal emission.

**Conflicts:** None. Signal framework is extensible by design.

**Recommendation:** **(b) Extension.** The friction-signal framework is mature. Violation detection logic would be new, but the integration points exist.

---

### 4. `src/persona.py` — Modular agent identities

**What it does:** Personas are YAML frontmatter + markdown body. Fields: name, role, model_tier, tool_access, memory_scope, communication_style, hooks, composes. System_prompt is literally the markdown body — it's a system prompt override mechanism. Personas compose by merging spec fields.

**Overlap with design:** ~5%. The design says "personas are distinct perspectives that draw different constraint lines." The code shows: personas ARE currently system-prompt costumes. They differ only in system prompt text, tool access, model tier, and hooks. There is no mechanism for different personas to reliably produce different inversion outputs. The "triad produces different constraints" is aspirational, not implemented.

**What design needs:** To make the design work as written, personas would need: (1) explicit "which failure modes does this persona attend to" metadata, (2) specialized inversion prompts per persona, (3) empirical proof that PM/engineer/architect personas diverge materially in their reasoning. None of this exists.

**Conflicts:** The design's assumption that the PM/engineer/architect triad will produce materially different constraint lists is untested. If the three are just "optimist," "skeptic," "engineer" variants of the same base prompt, they'll produce 95% overlap in outputs. The design might be aspirational about persona capability.

**Recommendation:** **(c) Drop from minimal experiment.** Per the review's recommendation, use a single generalist inversion prompt in v1. Personas can be extended later if single-persona inversion produces useful signal. The current persona system is not sufficiently differentiated to bear the design's weight.

---

### 5. `src/director.py` — Mid-loop evaluation and decisions

**What it does:** `director_evaluate()` is called on three triggers: "verify_failure", "step_threshold", "stuck". Takes an EvaluationContext snapshot (goal, steps done/remaining, verify failures, etc.) and produces a DirectorDecision with action in {continue, adjust, replan, restart, escalate}. Trigger is a string parameter; logic branches on trigger value.

**Overlap with design:** ~60%. The design proposes "director gains a new trigger: constraint_review." Director already has extensible trigger mechanism. Adding trigger="constraint_review" would be one new line in agent_loop + one new branch in director prompt logic.

**What design needs:** (1) Add trigger="constraint_review" to director_evaluate() calls in agent_loop. (2) Add prompt case for "when called with constraint_review, focus on whether constraints are still valid." (3) Wire a call to director_evaluate from wherever constraint violations are detected (inspector or elsewhere).

**Conflicts:** None. Trigger model is extensible by design.

**Recommendation:** **(b) Extension.** The trigger mechanism already exists. One new trigger case + one new prompt branch.

---

### 6. `src/handle.py` + `src/agent_loop.py` — AGENDA lane chain

**What it does:** 
- **BLE rewrite** (`rewrite_imperative_goal`, line 548-554): Strips prescribed execution steps, keeps outcome intent.
- **Clarity check** (`check_goal_clarity`, line 561-591): Returns {clear: bool, question: str}. If unclear, asks via channel or returns clarification_needed.
- **Agent loop entry** (`run_agent_loop`): Calls `_plan_and_decompose()` which calls `planner.decompose()` (agent_loop line 2048).

Chain: `handle.py` (BLE + clarity) → `agent_loop` → `decompose`.

**Overlap with design:** ~70%. The design proposes "insert constraint-setting between clarity-check and decompose." The chain already exists with clear insertion point. Clarity check happens in handle.py (lines 561-591). Decompose call is in agent_loop line 2048. The insertion point is identified and clean.

**What design needs:** (1) After clarity returns clear=True, call inversion function (generate failure modes + premises). (2) Record inversion output. (3) Pass inversion as additional context to `planner.decompose()` (already accepts lessons_context, ancestry_context, skills_context, cost_context). (4) Wire premises_context through the call stack.

**Conflicts:** Acknowledged but not blocking. Review notes that clarity + BLE + constraint-setting becomes a "gauntlet" (time-to-first-step: seconds → minute+). This is a design trade-off, not a code conflict.

**Recommendation:** **(b) Extension.** Insertion point is clean. Inversion logic + context wiring is new, but scaffolding exists.

---

### 7. `src/planner.py` — Decompose with context injection

**What it does:** `decompose()` (line 307) accepts: goal, adapter, max_steps, verbose, **lessons_context, ancestry_context, skills_context, cost_context**. Calls LLM with DECOMPOSE_SYSTEM prompt + these injected contexts. Returns list of step strings. The function signature is already designed for multiple context types.

**Overlap with design:** ~95%. The design proposes passing "constraints/premises as additional context." Decompose already accepts and injects multiple context types. Adding premises_context would be: (1) one line to function signature, (2) one line to user message assembly.

**What design needs:** (1) Add `premises_context: str = ""` to decompose signature. (2) Include it in user message assembly (around line 337). (3) Wire it through from `agent_loop._plan_and_decompose()`.

**Conflicts:** None. Extension points are designed for exactly this.

**Recommendation:** **(b) Trivial extension.** The code is ready for it. Three lines of change.

---

### 8. `src/memory.py` + knowledge layer (`knowledge_web.py`, `knowledge_lens.py`, `memory_ledger.py`)

**What it does:** Memory system with 3 layers: (1) Session bootstrap (load prior outcomes), (2) Outcome recording (after each run, record what happened + lessons), (3) Reflexion (per-task reflection stored as lessons, injected on future similar tasks). Knowledge_web.py has tiered lessons with scoring/decay/reinforce. Knowledge_lens.py has standing rules, hypotheses, verification outcomes. Memory_ledger.py has append-only outcome/lesson files.

**Overlap with design:** ~30%. The design says "constraint-outcome recording feeds memory layer." Memory layer already records outcomes with lessons. The question is whether new "constraint outcome" records would find retrieval paths. Current retrieval (agent_loop line 2643) calls `inject_lessons_for_task("agenda", goal, max_lessons=3)`. A "constraint outcome" would be a new lesson type or subtype. Recording infrastructure exists; retrieval logic would need definition.

**What design needs:** (1) Define new lesson type or add "constraint_outcome" field to existing Lesson. (2) After each run, record constraint sets + outcome. (3) Extend `inject_lessons_for_task()` to also inject constraint outcomes when relevant. (4) Define "relevant" — goal type? goal similarity? something else?

**Conflicts:** None. Infrastructure is designed to be extended with new lesson types.

**Recommendation:** **(b) Extension.** Foundation exists. New retrieval paths would be minimal.

---

### 9. `src/skills.py` — Reusable execution patterns

**What it does:** Skills are reusable execution patterns. Loaded from skills.jsonl. Each skill has: name, description, trigger_patterns (regexes), steps_template. Skills are scored (success_rate via SkillStats) and injected into planner prompts via `format_skills_for_prompt()` when goal matches triggers.

**Overlap with design:** ~5%. The design proposes "inversion skills and constraint-validation skills as first-class types." Skills today are EXECUTION patterns (step sequences that solved a class of problems). An "inversion skill" would be a prompt template, not an execution pattern. The design conflates "skill" (reusable execution) with "prompt module" (inversion template). Skills are not designed to produce judgments — they're designed to produce step sequences.

**What design needs:** Either (1) define a new SkillType enum with "execution" | "inversion" | "validation", OR (2) recognize that inversion/validation prompts are not "skills" and live elsewhere (e.g., prompt_templates.py).

**Conflicts:** The word "skill" might mislead. Inversion is a one-off LLM call, not a reusable step sequence. Mixing these under "skill" could confuse the codebase.

**Recommendation:** **(c) Drop from minimal experiment.** Use a single hardcoded inversion prompt. Skills remain what they are. If later we want to evolve inversion prompts over time, that's Phase B — make it a separate subsystem, not a skill.

---

### 10. Completion standard (mentioned as "subsumed" in design)

**What it does:** NOT FOUND. Searched extensively. No completion_standard module or logic exists. The design mentions "Completion standard | Subsumed" in the integration table, but there's nothing to subsume.

**Overlap with design:** N/A — doesn't exist.

**What design needs:** Clarify what mechanism was being subsumed, or drop the mention.

**Conflicts:** The design references something that doesn't exist.

**Recommendation:** **(d) Drop the mention.** If there's prior art on "completion standards," reference it explicitly. For now, assume it's not a concern.

---

## Minimum Viable Experiment — Implementation Cost

Per REVIEW.md's recommendation: "one LLM call that emits a failure-mode list before planner.decompose(), passed as additional planner context, recorded alongside the plan, no other changes."

**Net-new code required:**

1. **Inversion prompt + execution** (~80 lines): Define a generalist inversion prompt template (no triad, single persona). In agent_loop._plan_and_decompose(), after clarity check, call llm.complete() with that prompt. (NEW)

2. **Context parameter through stack** (~10 lines): Add `premises_context: str = ""` to `planner.decompose()` signature (line 307). Include in user message assembly (line ~337). Wire through from agent_loop._plan_and_decompose() (line ~2048). (EXTENSION)

3. **Recording** (~20 lines): After decompose, record goal + inversion output + plan to memory. Follow existing pattern from memory_ledger.py. (EXTENSION)

4. **A/B mechanism** (~30 lines): Add `--inversion-off` flag (or env var) to skip inversion call, run planner solo, for comparison. (NEW)

5. **Rename** (search-and-replace): Change design's "constraint" → "premise" throughout code/comments to avoid collision with constraint.py. (CRITICAL)

**Total: ~150 lines of new/modified code + rename sweep.**

The infrastructure is ready. The experiment is one inversion prompt template + one context parameter + one memory record type + one flag + one rename.

---

## Top 3 Findings (Summary for Immediate Review)

1. **Critical blocker: Name collision.** The word "constraint" in `src/constraint.py` means "pre-execution safety check" (runtime). The design's "constraints" mean "scope-narrowing decisions" (design-time). These are opposite concepts. The design MUST rename before any implementation code lands. Recommend: **"premise"** (short, reads naturally, no collision).

2. **Most infrastructure already exists.** Context injection into planning (DONE), mid-loop director evaluation (DONE), memory recording/retrieval (extensible), signal detection framework (extensible). ~70% of proposed machinery can be built as extensions. The minimal experiment requires ~150 lines of new code: one inversion prompt + one parameter + one A/B flag.

3. **Persona triad is aspirational.** The design assumes PM/engineer/architect personas will produce materially different constraint lists. Today's persona system is just system-prompt variations. The triad is untested. Per the review, drop it from v1. Use a single generalist inversion prompt. Personas can extend later if signal justifies it.

---

## Thesis Verdict

**"Most of the proposed machinery has some primitive form already present" — TRUE for ~70% of the design.**

- Pre-planning context injection: exists, extensible
- Mid-loop director evaluation: exists, trigger system is extensible  
- Memory recording/retrieval: exists, new lesson types feasible
- Signal detection framework: exists, new signal types feasible

**NOT present: inversion itself (needs prompt + execution), constraint recording in memory (needs new lesson type), constraint/violation injection into steps (needs retrieval), and resolution of the name collision with constraint.py (BLOCKING).**

**Minimum experiment cost: ~150 lines of new code + one strategic rename. The infrastructure to support it already exists.**

