# Constraint Orchestration — Perspective-Driven Bounded Execution

> **IMPORTANT — READ FIRST:** This document represents a decision point. It has been superseded in part by [`CONSTRAINT_ORCHESTRATION_REVIEW.md`](./CONSTRAINT_ORCHESTRATION_REVIEW.md), which identified a scope-narrowing issue (this design focuses on *planning*, but the originating conversation was about judgment + validation at *all levels* — the real defect is in verification, not planning), plus a name collision with `src/constraint.py`, an unreachable human gate in the autonomous path, and a substantially smaller minimum experiment. Both documents should be read together. This doc is preserved for decision-trail integrity.

**Status:** Design (pre-implementation, superseded-in-part — see review)
**Phase:** 65 (proposed)
**Context:** Emerged from slycrel-go regression testing + Phase 64 post-mortem discussion (2026-04-16). The orchestrator produces plausible output reliably but has no structural mechanism for evaluating whether output is *correct* at the behavioral/architectural level. Phase 64 added mid-loop director supervision, but the loop still operates against an unbounded solution space rather than a deliberately-narrowed one.

---

## Problem

The orchestrator has two structural gaps that existing verification does not address:

**1. Plausibility is not correctness.** Current verification catches syntactic errors (compile failures, hallucinated file paths) and friction signals (stuck streaks, verify failures) but does not catch behavioral incorrectness. The slycrel-go headless server ran to `status=done` with `go build ./...` returning exit 0 — but nobody ran a browser against it. "Compiles" is not "works," and the system treats them as equivalent.

**2. Planning operates in an unbounded space.** `planner.decompose()` receives a goal and immediately generates steps across the full solution space. There is no prior phase that narrows the space by committing to structural decisions. The planner must simultaneously decide "what are we building?" and "how do we build it?" in a single inference pass. Expert engineers do these sequentially: constraint-setting first, then bounded planning. We conflate them.

Combined effect: the loop reliably produces plausible things inside an unbounded space, with no way to verify those things are correct within a space that was never explicitly narrowed.

---

## Core Frame

The conceptual model comes from how expert practitioners — software engineers, but not exclusively — solve complex problems.

### Constraint propagation as scope reduction

Each well-chosen decision eliminates a large portion of the solution space, not by solving problems but by making them irrelevant. "We're using REST" doesn't solve the data format problem — it eliminates every non-REST problem from the conversation entirely. Four or five such decisions collapse infinite possibility into a bounded, navigable space where the remaining work is implementation, not discovery.

Jeremy's mental model for this: draw a rectangle representing the full possibility space. Draw a diagonal line that slices off ~40%. Draw another line from a point on the first, slicing off another 30-40% of what remains. Repeat counter-clockwise. After four or five lines, the remaining area is small — that is the bounded space in which execution happens. Each line is a constraint decision. The lines don't point toward the goal; they eliminate everything that isn't the goal.

### Inversion as constraint-discovery

The most reliable method for generating constraints is Charlie Munger's inversion: enumerate the ways this goal definitively fails, then draw lines that eliminate those failure modes. Success isn't designed; it's what remains after failure modes are eliminated.

Failure modes are structurally easier to name than success conditions. "What would make this break?" is a question humans (and LLMs) answer more reliably than "what would make this succeed?" because failure is concrete and local while success is diffuse and global.

Reference: [inversion thinking as an LLM prompting technique](https://x.com/MillieMarconnni/status/2044358003714097601) — "They invert the goal. They don't invert the system."

### Zoom + rotation as perspective orchestration

Granularity (zoom) alone is insufficient — you can vary depth but stay trapped in a single perspective. Rotation (personas) alone is insufficient — you can have multiple views but at a fixed level. Both together give a genuine 360° view of the problem.

Architect, engineer, and product-manager each zoom in and out independently, and their combined constraint lines define the bounded space. A constraint drawn by the architect at a structural level composes with a constraint drawn by the PM at a behavioral level, even though they're operating at different zoom levels.

### Refinement decisions vs. implementation decisions

These are different activities that the current infrastructure conflates:

- **Refinement** ("what are we NOT doing?") requires perspective and judgment. Personas are the right frame — the decision depends on what angle you're looking from.
- **Implementation** ("how do we do this specific thing within the committed constraints?") requires competence. Skills are the right frame — the decision depends on knowing the domain.

The planner currently does both at once. This is why its decomposition is sometimes unbounded (refinement failure) and sometimes over-detailed (implementation leaking into planning).

---

## The Constraint Lifecycle

A constraint is a first-class artifact with a lifecycle, not a static rule set once at planning time.

### Set

Generated by an inversion pass before decomposition. Multiple personas run inversion from different angles and their outputs combine into a constraint set.

Human gate (unless yolo) for review — not approval theater, but a quality check on the inversion pass itself. The gate presents failure modes, proposed constraints, and system confidence in a form that lets the human see where inversion went shallow or missed something. This is the right abstraction level for human involvement — humans evaluate 4-5 strategic decisions better than they evaluate 40 implementation steps.

### Inject

Constraints persist throughout execution and get retrieved into step prompts where relevant. Not all constraints in every step — that would crowd out useful context. Retrieval, not blanket injection.

### Detect

A declared constraint is only valuable if violations are caught. Detection spans a spectrum:

- **Mechanical**: "must compile clean" → `go build ./...` exit code
- **Structural**: "must be mockable" → code-shape check (interface present? dependencies injectable?)
- **Semantic**: "must degrade gracefully under partition" → LLM judgment against the constraint

Different constraints fall at different points on this spectrum. The representation needs to carry the check method alongside the statement.

### Revise

New information during execution may invalidate a constraint or reveal the need for a new one. The director's `director_evaluate` (Phase 64) gains a new trigger: `constraint_review`. Distinct from goal-drift (adjust/replan) — the loop may be on track but executing inside a boundary that's no longer correct.

### Except

Sometimes the elegant move is violating a constraint *for this one case* while keeping the constraint intact for others. Different from constraint revision — the constraint was right, this instance is a justified exception. Phase 64C's `escalate` action provides the infrastructure: execution flags the intended violation and reasoning, director decides (or defers to human for structural exceptions), exception gets recorded.

Exception vs. revision is a meaningful distinction. Exceptions say "this is a special case, the constraint still holds generally." Revisions say "the constraint was wrong, update it globally." They produce different learning signal.

### Break

In extreme cases, the constraint set itself is wrong and should be scrapped. This is Phase 64C's `restart` at the constraint layer — the director invalidates the constraint set, re-runs inversion with what was learned, and re-plans. The extreme case of revise.

---

## Perspective Orchestration

Personas in this frame are not system-prompt costumes. They are distinct **perspectives that draw different constraint lines** because they attend to different failure modes.

### Default triad

The minimal perspective rotation that covers the core failure modes:

| Persona | Lens | Catches |
|---------|------|---------|
| Product Manager | "what does the user need?" | Behavioral / experience failure modes |
| Engineer | "what breaks in implementation?" | Code-level failure modes |
| Architect | "what breaks the system?" | Structural / integration failure modes |

Three is the minimum meaningful rotation. More are sometimes warranted (security, operations, data integrity) when goal stakes or ambiguity are high, but not hardcoded. The selection is itself a judgment call — which is the recursive instance of the problem we're solving.

### Persona = perspective, skill = competence

A persona with no skills has opinions but can't check anything. A skill with no persona is competent but unattended — nothing decides when to invoke it. The bundle `persona + skill` is the execution unit.

For inversion specifically: `PM persona + user-impact-inversion skill`, `engineer persona + implementation-inversion skill`, `architect persona + systems-inversion skill`. The persona defines the angle; the skill defines what to actually do from that angle.

### Selection

The hardest open problem. Knowing which personas to invoke for which moment requires judgment that currently only humans have. Pragmatic approach:

1. Start with the triad as default for AGENDA goals that pass the constraint-setting gate
2. Allow goals to explicitly request additional personas (prefix-driven, e.g., `security:`)
3. Over time, learn from constraint-outcomes which perspectives were load-bearing for which goal types, and bias future selection

Selection is self-referential: constraint-setting informs persona selection which produces constraints. This is bounded by the initial triad default — we don't recurse infinitely, we just improve the default over time.

---

## Architecture Integration

This is not a parallel track. It extends existing infrastructure:

| Existing | Relationship |
|----------|--------------|
| `handle.py` AGENDA lane | Gains constraint-setting phase between BLE-rewrite and planner-decompose |
| Clarity check | Precedes constraint-setting (clarity is about the goal; constraints assume goal is clear) |
| BLE rewrite | Unchanged — produces outcome-focused goal that constraint-setting operates on |
| Completion standard | Subsumed — constraints are a richer version of "here's what done looks like" |
| `planner.decompose()` | Receives constraints alongside goal; decomposes within bounded space |
| `director.py` | Gains constraint-review as a responsibility, alongside plan/review/dispatch |
| Phase 64 `director_evaluate` | Gains `trigger="constraint_review"` for mid-execution constraint updates |
| Phase 64 `escalate` action | Path for constraint-exception requests |
| Phase 64 `restart` action | Path for constraint-set invalidation (break) |
| Inspector | Gains constraint-violation as a friction signal type |
| Ralph verify | Unchanged — step-level verification independent of constraint layer |
| Personas | Gain explicit inversion skills; default triad formalized |
| Skills | Gain inversion and constraint-validation as first-class skill types |
| Memory | Records constraint sets + outcomes per goal type (enables Phase D's approach-history) |

Constraint-setting is a new *responsibility* distributed across existing components, not a new component.

---

## Design Tensions (Open Questions)

These are deliberate trade-offs. The minimal implementation picks one side; the other side remains for later.

**When to invoke constraint-setting.** Fast path vs. full pipeline. NOW/AGENDA is one cut; constraint-setting needs its own cut within AGENDA. Simple AGENDA goals ("rename this function across the codebase") shouldn't pay the inversion tax. The gate decision is itself judgment — systematizing *it* is the outer instance of the problem.

**Constraint representation.** Natural language is expressive but not mechanically checkable. Structured constraints are checkable but stiff and the LLM won't use them naturally. Hybrid (NL statement + optional check method) is probably right but adds surface area.

**Constraint conflicts between personas.** Architect draws "must be testable in isolation"; PM draws "must work end-to-end as a unit." The lines cross. Resolution paths: meta-negotiation (expensive), human arbitration (good for structural conflicts), or "both apply and implementation must satisfy both" (works when constraints compose; fails when they genuinely contradict).

**Inversion quality.** If the system enumerates the wrong failure modes, constraints misdirect work — worse than no constraints. The human gate is the quality check, but that requires presenting inversion output in a reviewable form, not just approve/deny. Over time, the memory layer could bias future inversions toward historically-useful failure modes for similar goal types.

**Constraint decay signal.** When is a constraint stale? The system needs a trigger distinct from goal-drift. One option: any step result that contains new structural information (discovered file, inferred interface, external dependency) implicitly triggers constraint-review.

**Token economics.** Constraint injection into every step crowds out useful context. Retrieval is better than blanket injection, but retrieval requires constraint-step relevance judgments — itself a cost.

**Exception vs. revision distinction.** Breaking a constraint for one case vs. updating the constraint globally. Similar in the moment, different in consequence. The mechanism must distinguish them explicitly or the learning signal collapses.

---

## Implementation Profiles

The idea has two implementation shapes. We should build the first and leave the second as the intended extension path — don't over-build before we have signal.

### Minimal (ship-in-a-week)

- New phase in `handle.py` AGENDA lane, between BLE-rewrite and `planner.decompose()`
- Single inversion prompt template using the PM/engineer/architect triad (one LLM call per persona, three total)
- Constraint list as plain markdown, injected into planner as additional goal context
- Human gate reuses clarity-check infrastructure (present constraints, proceed/edit/cancel)
- Director gains one new trigger condition that consults the constraint list for relevance
- No violation detection beyond existing ralph verify + compile checks
- No persona/skill bundles — inversion prompts are static for v1
- Gate decision: AGENDA goals above N words, or explicitly flagged with a complexity marker

Validates whether constraint-setting improves outcomes at all before investing in full machinery.

### Ambitious (intended extension)

- Constraints as first-class typed artifacts with lifecycle, check method, and provenance
- Retrieval-based injection (not blanket)
- Persona+skill bundles for inversion, with learned selection mechanism
- Violation detection spectrum (mechanical → structural → semantic)
- Constraint-outcome recording feeds memory layer for future inversion quality
- Exception vs. revision vs. break as distinct paths with distinct learning signals
- Constraint-aware step shaping (steps know which constraints they operate under)

North star. Should only exist once the minimal version shows that bounded planning produces measurably better outcomes than unbounded planning.

---

## Relationship to Phases

### Past / current

- **Phase 64 (Adaptive Execution)** — Foundational. Provides the `director_evaluate` infrastructure that constraint-review hooks into. Provides `escalate` and `restart` actions that exception and break reuse.
- **BLE rewrite** — Focuses the *goal* before planning. Constraint-setting focuses the *solution space* before planning. Complementary.
- **Clarity check** — Resolves ambiguity in the goal. Precedes constraint-setting, which assumes goal is clear.
- **Ralph verify** — Per-step verification. Independent of constraint layer but informs violation signals.
- **Persona system** — Existing infrastructure. This phase gives it a concrete load-bearing use case beyond style overrides.

### Future

- **Phase D (memory for approach history)** — Becomes more valuable when constraint-outcomes are recorded. "Which constraint sets worked for this goal type" is a better learning signal than step-level outcomes.
- **Violation detection subsystem** — Eventually warrants its own phase. Starts as a spectrum of ad-hoc checks; matures into a typed validator system.
- **Persona/skill auto-packaging** — The selection mechanism is the recursive instance of the problem. Future work extends constraint-informed selection to personas themselves.

### Not a replacement for

- Planner (remains; operates on bounded space)
- Director (remains; gains new responsibility)
- Inspector (remains; gains new signal type)
- Skills (remain; gain new types)

---

## What This Is Not

**Not a replacement for LLM capability.** A constraint-aware system still needs the LLM to reason well within the constraints. Good constraints make execution easier but don't substitute for it.

**Not self-improvement by itself.** Recording constraint-outcomes is a training signal, but the system is still doing in-context learning over frozen weights. The mechanism here produces *structured* signal; conversion into durable improvement still requires the memory layer to retrieve and apply prior constraint lessons.

**Not a verification system.** Constraints declare what "within bounds" means; verification (ralph verify, inspector, tests) checks whether specific outputs satisfy specific checks. Constraint-setting improves verification's job by narrowing what needs to be checked, but doesn't replace it.

**Not good judgment itself.** The best we can do is *systematize* good judgment — externalize it into inversion passes, persona rotations, constraint records. The judgment itself still lives partly in the LLM and partly in the human at the gate. The hope is that systematization makes judgment improvable over time, not that it makes the system independently wise.

---

## Why This Matters

The orchestrator's current failure mode is that it produces plausible-looking output for complex goals and has no structural mechanism for recognizing when plausible is not correct. Phase 64 addresses mid-loop drift but operates against the same unbounded solution space.

Hypothesis: most of what makes expert engineers reliable is not that they execute well — it's that they execute inside a deliberately-narrowed solution space, and they know how to narrow it. Systematizing that narrowing — making it explicit, inspectable, and eventually learnable — is the load-bearing move we haven't made yet.

If the hypothesis is right: the orchestration we already have will produce dramatically better outcomes, because it will be operating on the right problem rather than a superset of the right problem.

If the hypothesis is wrong: we will have built expensive scaffolding around the same frozen weights, and the actual path forward is somewhere else.

Either outcome is worth knowing.

---

## Origin

This design emerged from a multi-turn conversation on 2026-04-16 following Phase 64 testing. Key frames that shaped it:

- The rectangle / constraint-line mental model (Jeremy's whiteboard description)
- Munger-style inversion as the constraint-discovery technique
- The refinement-vs-implementation decision separation
- The zoom + rotation perspective framework
- The explicit distinction between exception, revision, and break
- The observation that what we've built is a "reliable task executor with a weak verifier," and that this work is aimed at the verifier half

Future work in this area should re-read this document before proposing changes — the conceptual scaffolding is load-bearing and the tensions listed above have already been considered.
