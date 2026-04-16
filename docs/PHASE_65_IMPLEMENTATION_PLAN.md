# Phase 65 Implementation Plan — Scope (minimum viable experiment)

**Date:** 2026-04-16
**Status:** Implementation (minimum viable, A/B-ready)
**Decision trail:** `CONSTRAINT_ORCHESTRATION_DESIGN.md` (design) → `CONSTRAINT_ORCHESTRATION_REVIEW.md` (review) → `CONSTRAINT_ORCHESTRATION_AUDIT.md` (existing infra audit)

---

## Rename

Concept is **"scope"** (not "constraint", "premise", or "boundary"):

- Captures both sides of the coin — what IS and what IS NOT being pursued
- Complements specs naturally (spec = what to build, scope = within which boundaries)
- Reads well in code: `ScopeSet`, `generate_scope()`, `inject_scope_into_plan()`
- Avoids collision with `src/constraint.py` (pre-execution HITL/risk harness, unrelated concept)

---

## Hypothesis Being Tested

**One LLM call producing a failure-mode-informed scope, injected into planner context, produces measurably better plans than the current unbounded decompose.**

If true: expand to lifecycle, personas, violation detection in subsequent phases.
If false: the constraint orchestration direction is wrong and the design becomes informational only.

---

## What's In (v1, ship this)

1. **`src/scope.py`** — new module
   - `ScopeSet` dataclass (in_scope list, out_of_scope list, failure_modes list, raw_text)
   - `generate_scope(goal, adapter)` → `ScopeSet`
   - One inversion prompt: "what are 3-7 ways this goal fails? → from failures, what's in scope, what's out?"
   - Returns a `ScopeSet` with the three lists + the raw LLM output
   - Non-fatal: returns `None` on failure, logged clearly
   - `inject_scope_into_context(scope, ancestry_context_extra)` helper — appends scope markdown to ancestry, returns new string

2. **`src/handle.py` AGENDA lane integration**
   - After clarity check, before `run_agent_loop` call
   - Check `config.get("scope_generation", False)`
   - Check `config.get("scope_ab_skip", False)` — if true, record "would have generated scope" but skip
   - Call `generate_scope()`, attach to `ancestry_context_extra`
   - Record generated scope to `~/.poe/workspace/projects/<slug>/artifacts/scope.md`
   - If no channel (autonomous path): no gate, just log `log.info("[scope-deferred] human-gate: no channel, proceeding with generated scope")`

3. **`src/planner.py` — no changes**
   - Planner already accepts `ancestry_context` (`planner.decompose(goal, adapter, max_steps, ancestry_context=...)`). Scope just rides on ancestry.
   - Audit confirmed this extension point exists.

4. **Config keys** (user-level, both default `False`)
   - `scope_generation: bool` — master enable
   - `scope_ab_skip: bool` — force-disable even if generation=true (for A/B runs)

5. **Tests (`tests/test_scope.py`)**
   - `test_scope_generation_parses_llm_output` — LLM returns structured bullets, ScopeSet fields populate
   - `test_scope_generation_handles_bad_llm_output` — garbage response → returns `None`, logs warning
   - `test_scope_injection_appends_to_ancestry` — scope.md text appears in ancestry_context_extra
   - `test_scope_disabled_by_default` — `config.get("scope_generation")` default False, nothing happens
   - `test_scope_ab_skip_records_but_does_not_inject` — records "would have generated" but doesn't add to ancestry
   - `test_handle_no_channel_skips_gate` — autonomous path logs the deferred decision

---

## What's Out (explicit deferrals, log each one)

At every deferred point, `log.info("[scope-deferred] <what>")` so we can find them. Known deferrals:

- **Triad:** Using single generalist inversion, not PM/engineer/architect triad. Log: `[scope-deferred] triad: using single generalist prompt, multi-persona rotation deferred`
- **Human gate:** No approval UI for autonomous or interactive use. Log: `[scope-deferred] human-gate: scope used without human review, gate UX deferred`
- **Violation detection:** Scope is aspirational — nothing checks violations mid-execution. Log: `[scope-deferred] enforcement: scope injected but not checked, violation detection deferred`
- **Lifecycle:** Scope is immutable after generation. No revise/except/break. Log (one-time at generation): `[scope-deferred] lifecycle: scope immutable, director revise/except/break deferred`
- **Retrieval-based injection:** Scope is fully injected as a block, not retrieved per-step. Log: `[scope-deferred] retrieval: scope fully injected, per-step relevance deferred`
- **Memory / learning:** Scope is recorded but nothing retrieves it across goals. Log: `[scope-deferred] memory: scope recorded but no cross-goal retrieval, Phase D deferred`

These logs are the explicit "think harder here later" markers Jeremy asked for. They make the deferrals searchable (`grep "scope-deferred"`) when expanding later.

---

## A/B Mechanism

Two config flags control the experiment:

```yaml
# scenario 1: feature off entirely (baseline)
scope_generation: false

# scenario 2: feature on, normal behavior
scope_generation: true
scope_ab_skip: false

# scenario 3: feature "on" but skipped mid-flight (for paired comparison)
scope_generation: true
scope_ab_skip: true
```

Scenarios 1 and 3 both produce unbounded plans; the difference is scenario 3 records what *would* have been generated, so we can compare "with scope" runs to "without scope but same generation counterfactual."

For a real A/B corpus: run 20 goals with `scope_ab_skip=false` and 20 with `scope_ab_skip=true`. Compare outcomes on whatever metric matters (step count, token cost, goal satisfaction).

---

## Out of Scope for v1 (do not build)

- No changes to `director.py` — scope does not integrate with `director_evaluate` yet
- No changes to `inspector.py` — no new friction signal
- No changes to `persona.py` — no new persona bundles
- No changes to `skills.py` — no new skill types
- No constraint lifecycle (set/inject/detect/revise/except/break — only set + inject)
- No retrieval for per-step injection — scope goes into ancestry as a block
- No mid-execution constraint review trigger
- No human gate UI (neither Telegram/Slack flow nor CLI block-on-approval)
- No cost ceiling (caught by existing token metering; add if this ships to prod)

---

## Cost Model

- +1 LLM call per AGENDA goal (when `scope_generation=true`). Mid-tier (planning tier).
- +~2-4KB of context on planner prompt when scope is injected.
- Zero per-step cost added.
- When `scope_generation=false` (default): zero cost impact.

If inversion generation itself fails (timeout, bad JSON, etc.): log warning, skip scope injection, loop proceeds as if feature off. Never blocks.

---

## Implementation Order

1. `src/scope.py` — `ScopeSet` dataclass + `generate_scope()` function + prompt template
2. `tests/test_scope.py` — tests with a mock adapter
3. `src/handle.py` — thread scope generation into AGENDA lane
4. Run full test suite — nothing should break
5. Commit + push as a logical unit
6. Note next steps in MILESTONES (A/B run, measurement approach)

Total target: ~150-200 lines net-new code. Fits the audit's estimate.

---

## Success Criteria for v1

Not "outcomes improve" — that's the hypothesis being tested. Success for v1 shipping is:

- [ ] All tests pass (including existing 4,300+)
- [ ] Scope generation is off by default; no existing goal behavior changes
- [ ] When enabled, a real goal runs end-to-end with a real LLM scope generation step, and the generated scope appears in the planner prompt
- [ ] Scope artifact saved to project dir
- [ ] All deferred decisions are logged explicitly
- [ ] A/B skip mechanism works (scenario 3 above)

The *experiment* (does it improve outcomes?) comes after v1 ships, via a run across a corpus of real goals.

---

## Post-Implementation

After v1 ships, queue:
1. Run the A/B corpus (probably 20 goals, spread across goal types — research, code, analysis)
2. Measure: plan quality (LLM-judged? human?), token cost per goal, step count to completion, verification outcome
3. Write up results with explicit signal claim
4. If positive: extend toward triad, lifecycle, retrieval. If negative: design doc becomes informational.

The verification sibling (the "nobody ran a browser" gap) is a separate design. Do not bolt it onto scope.
