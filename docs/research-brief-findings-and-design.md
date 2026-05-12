# Research Brief: Constraint Orchestration, Intent Resolution & Adaptive Execution

**Date:** 2026-05-12  
**Author:** Research Assistant (Deep Synth)  
**Status:** Final — synthesized from Steps 4–6 source docs  

---

## §0 — Question

**Primary question:** How should Poe's constraint system, intent resolution, and adaptive execution compose into a coherent execution architecture?

**Why we care:** Three design docs exist independently. Each describes one layer of the same causal chain — but no document describes the chain itself. Phase 65 (constraint orchestration) is the open active design space; the other two are prerequisites and runtime continuations. Shipping Phase 65 without understanding how it slots into intent resolution (upstream) and adaptive execution (downstream) risks building the wrong seams.

**Success criteria:** A single source-grounded brief that shows the full flow (intent → constraints → execution), surfaces the open questions that block progress, and recommends the next concrete action.

---

## §1 — Constraints

- **Sources:** Three design docs read directly from repo. No web sources; this is internal architecture.
- **Stance:** Descriptive + prescriptive. What's built, what's missing, what to do next.
- **Scope:** Architectural seams only — not implementation line-by-line.

---

## §2 — Research Plan

Angles pursued:

1. What does each subsystem do in isolation?
2. Where does each subsystem's input come from / output go?
3. What are the current implementation gaps in each?
4. What are the seams between the three — where does one hand off to the next?
5. What shared data structures or protocols are needed for the handoffs?
6. What open questions in each doc block the others?
7. What is the minimum viable integration order?

---

## §3 — Sources & Provenance

| Doc | Path | Status |
|-----|------|--------|
| Constraint Orchestration Design | `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` | Read in full (Step 4) |
| Intent Resolution Design | `docs/INTENT_RESOLUTION_DESIGN.md` | Read in full (Step 5) |
| Adaptive Execution Design | `docs/ADAPTIVE_EXECUTION_DESIGN.md` | Read in full (Step 6) |
| CLAUDE.md | Root | Identifies Phase 65 as open design space |

All sources are primary internal docs. No web sources used. Claim confidence is source-grounded unless marked [INFERRED].

---

## §4 — Executive Summary

Poe has three partially-independent subsystems that together answer one question: *given a user goal, how does Poe decide what's in scope, refuse what isn't, and correct course when reality diverges from plan?*

- **Intent Resolution** answers: "What does 'done' mean? What scope boundaries apply?" It is a **pre-execution clarification phase** — side quests, scope probes, and intent disambiguation happen here before constraints are set.
- **Constraint Orchestration** answers: "What are the hard and soft rules that govern this run?" It is a **first-class runtime object** (`ConstraintSet`) visible to the Director, not a pre-flight checklist. Currently fragmented across ~12 independent checks with no runtime update path.
- **Adaptive Execution** answers: "Given what we've discovered, should we continue, adjust, replan, restart, or escalate?" It is the **runtime correction layer** — `director_evaluate()` fires on triggers and uses constraint state to make mid-run decisions.

The causal chain is: **resolve intent → crystallize constraints → execute adaptively**. None of the three docs describes this chain. Phases A/B/C of adaptive execution are shipped; Phase D (memory layer, learning from history) and the constraint orchestration redesign (Phase 65) are the active gaps. Intent resolution is partially designed but not phase-assigned.

**The central risk:** Phase 65 (constraint orchestration) is being designed without a contract for what intent resolution writes into the `ConstraintSet`, and without knowing what fields `director_evaluate()` needs to read back out. This is a seam problem, not an implementation problem.

---

## §5 — Key Findings

### F1 — Intent Resolution is a constraint-discovery protocol (not just goal clarification)

> Source: `INTENT_RESOLUTION_DESIGN.md`

Side quests are framed as **scope probes** — lightweight runs whose purpose is to surface implicit constraints (budget, time, tool availability, user taste). The output of a successful intent resolution phase is not a cleaner goal string; it is a populated `ConstraintSet`. This reframes intent resolution as Phase 65's upstream feeder, not a standalone UX improvement.

**So what:** `ConstraintSet` must have an API for progressive population — constraints discovered during intent probes should be addable before the main run starts, and the constraint orchestrator needs to handle partial vs complete constraint sets.

---

### F2 — Constraint enforcement is currently fragmented and static

> Source: `CONSTRAINT_ORCHESTRATION_DESIGN.md`

~12 constraint checks exist across `constraint.py`, `pre_flight.py`, and individual worker modules. Each checks independently. There is no runtime update path — constraints set at pre-flight cannot change during execution. This means:

- A side quest that discovers "user prefers no web searches" has no way to propagate that constraint into a live run.
- A budget overrun discovered mid-run can only be handled by hard-stop, not graceful adjustment.
- The Director has no visibility into which constraints are active, soft vs hard, or close to breach.

**Design target (Phase 65 v1):** `src/scope.py` — `generate_scope()` function + `ScopeSet` dataclass (`in_scope`, `out_of_scope`, `failure_modes`, `raw_text`). Minimal path: plain markdown injected into planner via existing `ancestry_context` extension point. No singleton, no hot-reload, no Director changes in v1. Full lifecycle (`ConstraintOrchestrator` with revise/except/break, Director visibility, retrieval injection) is the **Ambitious** path — deferred until the minimal experiment shows constraint-setting improves outcomes.

> **Note:** The concept was renamed from "constraint" to **"scope"** in `PHASE_65_IMPLEMENTATION_PLAN.md` to avoid collision with `src/constraint.py` (the pre-execution HITL/risk harness, an unrelated concept). All Phase 65 artifacts use `ScopeSet`, `generate_scope()`, `scope_generation` config key.

---

### F3 — Adaptive Execution (Phases A–C) is operational but blind to constraint state

> Source: `ADAPTIVE_EXECUTION_DESIGN.md`

`director_evaluate(goal, eval_ctx, trigger)` fires on budget/step/loop triggers and returns a `DirectorDecision` (continue / adjust / replan / restart / escalate). The `EvaluationContext` snapshot exists, but:

- `current_approach` field is always empty string (Phase D deferred) — Director cannot learn from history.
- Director reads step *strings*, not objects — no structured access to constraint state from within evaluation.
- `verify_goal_completion()` path is separate from `director_evaluate()` — closure and mid-run share no logic.

**So what:** `EvaluationContext` will eventually need a `scope_state: ScopeSet` field. Phase 65 v1 **explicitly defers** this — `director.py` and `inspector.py` have zero changes in the minimal implementation. Phase D and `EvaluationContext` extension are the co-design concern for the phase after the A/B experiment validates that scope-setting improves outcomes at all. Building the extension before validation is premature.

---

### F4 — The three-phase flow has no explicit protocol

> Source: All three docs [INFERRED from absence]

None of the three docs defines the handoff data structure between phases. What constraint orchestration writes, adaptive execution must be able to read. What intent resolution discovers, constraint orchestration must be able to ingest. The current approach is implicit: each subsystem reads from shared state via `config.get()` or `LoopContext`. This works for static constraints but breaks for dynamic ones.

**Minimum viable protocol (proposed):**

```
IntentResolutionResult  [INFERRED — no implementation yet]
  ├── goal_refined: str
  ├── scope_boundary: str  (what's explicitly out)
  ├── discovered_constraints: List[ConstraintDraft]
  └── confidence: float  (0=guess, 1=explicit user confirmation)

ScopeSet  (Phase 65 v1 — src/scope.py, PHASE_65_IMPLEMENTATION_PLAN.md)
  ├── in_scope: List[str]
  ├── out_of_scope: List[str]
  ├── failure_modes: List[str]
  └── raw_text: str
  # NOTE: hard/soft, conflict_policy, hot_reload are Ambitious path (deferred)

EvaluationContext  (Phase D extension — NOT in Phase 65 v1 scope)
  └── scope_state: Optional[ScopeSet] = None  ← deferred until A/B validates
```

---

### F5 — Phase ordering matters and is currently inverted in practice

> Source: All three docs

Adaptive execution is partially shipped (Phases A–C). Constraint orchestration (Phase 65) has not started. Intent resolution has a design doc but no phase number. The risk: adaptive execution was built against a static constraint model it will need to be retrofitted when Phase 65 ships. The `ExecutionPlan` struct deferral (Phase D) creates a second retrofit risk.

**So what:** Phase 65 should be designed **with** `EvaluationContext` extension as part of its scope, not as a separate downstream concern.

---

## §6 — Counterpoints / Dissent

**"Intent resolution is UX, not architecture."**  
Counter: The design doc explicitly calls side quests constraint-discovery probes. Whether that's UX or architecture depends on whether the output is a better goal string (UX) or a populated `ConstraintSet` (architecture). The doc implies both. If intent resolution's output is only a string, the constraint seam doesn't exist and Phase 65 doesn't need an intake API. This is the key design fork.

**"Phase D memory layer is separate from constraint orchestration."**  
Counter: `current_approach: always empty string` means Director has no history of which constraints were active during prior replans. If Phase 65 ships a `ConstraintSet` without threading it into `EvaluationContext`, Phase D will need to retrofit it. Co-designing the seam costs ~1 day; retrofitting later costs N days plus regression risk.

**"12 independent constraint checks work fine for current scale."**  
Counter: True for static, pre-flight, single-run constraints. False as soon as side quests dynamically update scope or mid-run discoveries change what's allowed. The fragmented model has no path to dynamic updates.

---

## §7 — Risks, Unknowns, Disconfirming Evidence

| Risk | Impact | Status |
|------|--------|--------|
| Phase 65 ships without `EvaluationContext` extension | Director remains blind to constraint state | High — Phase D depends on it |
| Intent resolution's output is a string not a `ConstraintSet` | Seam between intent and constraints doesn't exist | Unresolved design fork |
| `ConstraintSet` conflict policy not designed | Two constraints conflict, no resolution rule | No doc exists yet |
| Closure path (`verify_goal_completion`) diverges from `director_evaluate` | Two separate "is this done" logics drift | Phase C leftover, deferred |
| `ExecutionPlan` struct deferred | Phase D must retrofit without breaking callers | Explicit deferral in doc |
| Director reads step strings not objects | No structured constraint access inside evaluation | Known gap, no fix scoped |

**Open questions (unresolved):**

1. Does intent resolution write a `ConstraintSet` or a cleaner goal string? (Architecture fork)
2. When does the system stop clarifying and start executing? (Intent resolution: no decision rule)
3. What is the conflict resolution policy for two active constraints that disagree?
4. What user override protocol exists for soft constraints discovered mid-run?
5. How does the Director express confidence in its current approach to inform memory (Phase D)?

---

## §8 — Recommendation

**Design Phase 65 (constraint orchestration) as the integration layer, not an isolated subsystem.**

Specifically:

1. **Implement Phase 65 v1 per `PHASE_65_IMPLEMENTATION_PLAN.md`.** The design is complete. Ship: `src/scope.py` (`ScopeSet` + `generate_scope()`), `tests/test_scope.py` (6 tests specified), `src/handle.py` AGENDA lane integration. ~150–200 lines net-new. This is the prerequisite for everything else — validates the hypothesis before investing in the Ambitious path.
2. **Resolve the intent resolution output fork.** Decide: does a successful intent resolution phase produce a `ScopeSet` (or `List[str]` of discovered constraints) or only a refined goal string? This decision gates the upstream API design. Add one sentence to `INTENT_RESOLUTION_DESIGN.md` stating the decision.
3. **Run the A/B experiment.** 20 goals with `scope_ab_skip=false` vs. 20 with `scope_ab_skip=true`. Compare step count, token cost, goal satisfaction. This gates the Ambitious path investment.
4. **Extend `EvaluationContext` (post-A/B only).** If A/B shows constraint-setting improves outcomes, add `scope_state: Optional[ScopeSet] = None` to `EvaluationContext`. Make it optional to preserve Phase A–C compatibility. This is Phase D co-design — do not build before A/B validates.

**Confidence: strong** — §1–3 are grounded in `PHASE_65_IMPLEMENTATION_PLAN.md` (existing detailed spec). §4 is grounded in the explicit deferral in that same doc ("Out of Scope for v1: No changes to director.py, no constraint lifecycle"). The prior recommendation to define `ConstraintSet` first was incorrect — `ScopeSet` is already designed; the next step is implementation.

---

## §9 — Next Actions

- [ ] **1. Implement `src/scope.py`** — `ScopeSet` dataclass (`in_scope`, `out_of_scope`, `failure_modes`, `raw_text`) + `generate_scope(goal, adapter)` + `inject_scope_into_context()`. Non-fatal: returns `None` on failure. ~1 hour. Full spec in `PHASE_65_IMPLEMENTATION_PLAN.md`.
- [ ] **2. Write `tests/test_scope.py`** — 6 tests specified in the implementation plan: parse good output, handle bad output, injection appends to ancestry, disabled by default, ab_skip records-but-doesn't-inject, no-channel skips gate. ~45 minutes.
- [ ] **3. Thread scope into `src/handle.py` AGENDA lane** — after clarity check, before `run_agent_loop`. Config gate: `scope_generation: False` default. Records generated scope to `~/.poe/workspace/projects/<slug>/artifacts/scope.md`. ~30 minutes.
- [ ] **4. Resolve intent resolution output type** — decide: `ScopeSet` / list-of-strings or refined goal string. Add one sentence to `INTENT_RESOLUTION_DESIGN.md`. ~30 minutes.
- [ ] **5. Run A/B experiment** — 20 goals `scope_ab_skip=false` vs. 20 `scope_ab_skip=true`. Compare step count, token cost, goal satisfaction. Gates investment in Ambitious path (lifecycle, Director integration, EvaluationContext extension, persona triad). Timeline: after v1 ships.

> **Correction note:** Prior next actions incorrectly recommended defining `ConstraintSet` (already designed as `ScopeSet`), extending `EvaluationContext` (explicitly deferred in v1), and writing a Phase 65 design spec (already exists: `PHASE_65_IMPLEMENTATION_PLAN.md`). These have been corrected above to reflect the actual implementation plan.

---

## §10 — Appendix

### Causal chain (summary)

```
User goal (ambiguous)
    │
    ▼ [Intent Resolution — pre-execution]
    Scope boundary defined
    Discovered constraints (budget, tool, style, time)
    └─→ writes: IntentResolutionResult → feeds: ConstraintSet population
    │
    ▼ [Scope Orchestration — Phase 65 v1]
    ScopeSet generated: in_scope, out_of_scope, failure_modes
    Injected into planner via ancestry_context (existing extension point)
    Director/Inspector changes deferred to post-A/B Ambitious path
    └─→ writes: ScopeSet (ancestry markdown) → feeds: planner.decompose()
    │
    ▼ [Adaptive Execution — Phases A–C shipped, Phase D pending]
    director_evaluate(goal, eval_ctx, trigger)
    → continue / adjust / replan / restart / escalate
    └─→ Phase D (post-A/B): EvaluationContext gains scope_state: Optional[ScopeSet]
        records scope_state in history for learning
```

### Implementation gap matrix

| Subsystem | Designed | Shipped | Gap |
|-----------|----------|---------|-----|
| Intent Resolution | Partial (design doc) | Not started | No phase number, no output type decision |
| Scope Orchestration (Phase 65 v1) | Complete (PHASE_65_IMPLEMENTATION_PLAN.md) | Not started | `src/scope.py` + `ScopeSet` + handle.py integration; ~150–200 LOC, A/B config flags ready to define |
| Scope Orchestration (Ambitious) | Partial (CONSTRAINT_ORCHESTRATION_DESIGN.md) | Deferred | Full lifecycle (revise/except/break), Director visibility, retrieval injection — gated on A/B validation |
| Adaptive Execution (A–C) | Complete | Complete | Operational |
| Adaptive Execution (Phase D) | Partial | Not started | Memory layer, ExecutionPlan struct, EvaluationContext `scope_state` — gated on Phase 65 A/B |
