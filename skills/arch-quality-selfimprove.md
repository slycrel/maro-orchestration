---
name: arch-quality-selfimprove
description: Architecture context for quality verification AND self-improvement (they're zoom levels of the same thing)
roles_allowed: [worker, director, researcher]
triggers: [inspector, evolver, graduation, introspect, quality gate, skills, constraint, self-improvement, friction]
always_inject: false
---

# Quality & Self-Improvement Architecture

Two zoom levels of the same question: "did this work?" (per-run) and "how do we get better?" (over time). These were built as separate systems but share the same domain.

## The Intended Loop (from VISION)

```
Run completes
  → Inspector detects friction signals
  → Introspect classifies failure type
  → Evolver proposes improvement
  → Low-risk: auto-apply (lessons, observations)
  → High-risk: hold for review (guardrails)
  → Graduation: repeated patterns → permanent fixes
  → Verify fix actually worked
  → Learn from verification result
  → Loop closes
```

**Current reality:** The verify→learn *plumbing* is closed — session 17's `_verify_post_apply()` in evolver.py runs pytest after auto-applying suggestions, records outcome to memory_ledger and captain's log, and fires EVOLVER_VERIFY. But the learning *input* — lesson extraction from runs — was silently dead until 2026-06-11: a `safe_list` bug dropped the typed lesson dicts the prompt produces, so extraction returned `[]` on every real run (fixed, commit `d088ca7`). It is now live-verified but only lightly exercised (~2 typed lessons from one real call). The open question is whether the full medium→long→standing-rule accretion actually fires on organic runtime, not just in tests. Graduation *behavioral* verification closed 2026-07-14 (VERIFY_LEARN_ARC V3 — per-class verdict + demote); proactive lesson testing remains open.

## Per-Run Quality (zoom in)

### Inspector (inspector.py, ~2065 lines)
Post-hoc analyzer of outcomes.jsonl. Detects 7 friction signals:
- error_events, repeated_rephrasing, escalation_tone, platform_confusion, abandoned_tool_flow, backtracking, context_churn

Configurable thresholds via config.yml. Produces InspectorReport with severity classification (low/medium/high).

### Quality Gate (quality_gate.py)
Multi-pass review system. 5 optional passes:
1. PASS/ESCALATE verdict (mandatory)
2. Adversarial claim review (CONFIRMED/DOWNGRADED/CONTESTED)
3. Cross-reference fact check
4. LLM Council (3 critics)
5. Multi-agent debate (Bull/Bear/Risk Manager)

All passes use cheap model. Defaults to PASS on any error. In practice, most runs only get pass 1 — the expensive passes are rarely triggered.

### Introspect (introspect.py, ~1590 lines)
Failure classification (11 types: setup_failure, adapter_timeout, token_explosion, etc.). Each diagnosis has severity, evidence, recommendation. Written to diagnoses.jsonl.

Lenses: infrastructure exists but not fully wired. Heuristic lenses (free) run always; LLM lenses run selectively.

### Constraint (constraint.py)
Pre-execution enforcement. Tiered gates: READ (observe), WRITE (warn), DESTROY (block), EXTERNAL (confirm). Dynamic constraints from evolver (JSONL + TTL + circuit breaker).

**Audit trail (session 17):** `_log_constraint_event()` writes to `constraint_log.jsonl` when flags are found. Records: timestamp, allowed, risk_level, step_text, goal, flags detail.

## Over-Time Improvement (zoom out)

### Evolver (evolver.py, ~3265 lines)
Proposes improvements from outcome patterns. Triggered by heartbeat (~every 10 ticks) or manually.

Suggestion types:
- `prompt_tweak` → auto-applied as TieredLesson (low risk)
- `new_guardrail` → held for human review by default
- `skill_pattern` → unit-test gate before apply
- `observation` → auto-applied (informational)
- `sub_mission` → proposed follow-up goal (not auto-enqueued)

Applied changes logged to change_log.jsonl with rollback snapshots.

### Graduation (graduation.py)
Scans diagnoses.jsonl for repeated failure classes (≥3 occurrences). Promotes to permanent fixes using templates (8 failure classes covered). Each template has a verify_pattern (shell command) AND (V1) an `expected_signal` declaring `failure_class_rate ↓`.

**Verify→demote (VERIFY_LEARN_ARC V3, shipped 2026-07-14):** applied graduation rows get a *behavioral* verdict at evolver cadence via `verify_applied_suggestions` — verdicted on their own `failure_class_rate` over timestamped-diagnosis windows (diagnoses gained a `recorded_at` stamp in V3), demoted under symmetric authority. Rules stay advisor-gated (human-applied → a degraded row surfaces for review, never auto-reverted). The `verify_graduation_rules()` structural grep IS called automatically (`run_graduation_verification` at cadence) but stays **pure observability** — a grep miss ≠ the applied lesson failed, so it never gates state.

### Skills (skills.py, ~2055 lines + skill_types.py)
Discovery, scoring, promotion/demotion with circuit breaker. Shared types (`Skill`, `SkillStats`, `SkillTestCase`, `SkillMutationResult`, `compute_skill_hash`, `verify_skill_hash`, `skill_to_dict`, `dict_to_skill`) extracted to `src/skill_types.py` (session 17) to break circular import with evolver. `skills.py` re-exports for backward compat.
- **Score:** use_count, success_rate, utility_score (EMA), consecutive streaks
- **Circuit states:** closed (normal) → half_open (recovering) → open (rewrite eligible)
- **Auto-promote:** ≥5 uses + ≥70% success → provisional→established
- **Auto-demote:** ≥3 consecutive failures opens circuit, triggers rewrite
- **Test gate:** Skill mutations blocked if unit tests fail
- **Retirement archives, never deletes (retention decree, 2026-07-10):**
  island culls (`cull_island_bottom_half`) and A/B variant retirement
  (`retire_losing_variants`) move skills to `memory/skills_archive.jsonl`
  with `archived_reason` + a `retire` provenance record. The live pool
  shrinks; the record survives. Guarded by tests/test_no_silent_deletion.py
  (AST tripwire over all file-deletion call sites in src/).
- **Skills-lite (Rider A, 2026-07-10):** skill-shaped .md artifacts from
  successful runs auto-promote into the workspace skills overlay
  (`tier: skills-lite`) + a companion provisional Skill in skills.jsonl, so
  normal decay/circuit-breaker degradation applies; a tripped companion
  quarantines the .md to `skills/_quarantine/`. Human review gates only
  ship-set/repo graduation. Promotion runs in run curation's explicit
  maintenance phase, after the pure card has been persisted.
  `run_curation.promote_skills_lite` /
  `degrade_skills_lite`, config `skills.lite_promotion` (default ON by
  decree — see docs/DEFAULTS.md).

**Gap:** New skill discovery from *outcomes* (extract_skills) is rare; skills-lite covers only runs that deliberately author a skill .md.

### Post-loop self-reflection was dead for six weeks (session 40)

The entire Phase 44-45 block in `agent_loop._finalize_loop` (diagnosis save, lenses, recovery plan, diagnosis lesson) referenced `ctx.project` after the monolith extraction removed `ctx` from that scope — a NameError on every run, silently swallowed by the block's own broad `except`. Fixed 2026-06-10. Same sweep found `evolver.rewrite_skill` missing its `verbose` param (both callers passed it → TypeError → skill rewriting/circuit-breaker recovery dead) and two more latent NameErrors (llm.py `thinking_budget` fallback, agent_loop terminal-handler `block_reason`). The bug class is now locked out by `tests/test_static_undefined_names.py` (pyflakes undefined-name sweep over src/).

**Recovery lessons (session 40 M3):** `_finalize_loop` now records typed `lesson_type="recovery"` lessons mechanically (no LLM calls): a stuck run with a table recovery plan records `[recovery-plan] <failure_class>: <action>` (confidence 0.5, suggestion); a *completed* run with `recovery_steps > 0` records `[recovery-verified] <kinds> unblocked a run: <first failure>` (confidence 0.7 — the run finishing is the verification). Stable text means recurring recoveries reinforce via dedup and can accrete toward standing rules (M2 pipeline).

## The Self-Improvement Gap

What's autonomous today:
- ✅ Prompt tweaks auto-applied as lessons
- ✅ Skills auto-promoted/demoted based on success rate
- ✅ Low-risk recovery auto-applied (Phase 45)
- ✅ Verify→learn *plumbing* closed (session 17): evolver runs pytest after auto-apply, records to memory_ledger + captain's log, fires EVOLVER_VERIFY. But lesson extraction (the learning input) was silently dead until 2026-06-11 — a `safe_list` bug dropped typed lesson dicts (fixed, commit `d088ca7`); now live-verified but only lightly exercised (~2 typed lessons from one real call). Open question: does medium→long→standing-rule accretion fire on organic runtime, not just in tests?
- ✅ Constraint audit trail: flag events logged to constraint_log.jsonl
- ✅ Playbook entry validation: empty entries rejected, truncation at 500 chars

What requires humans:
- ❌ Guardrails held for review (correct safety boundary)
- ❌ No auto-enqueue of follow-up missions
- ✅ Graduated rules auto-verified at cadence (VERIFY_LEARN_ARC V3, 2026-07-14): applied graduation rows get a per-class behavioral verdict + demote (advisor-gated: degraded surfaces for review, never auto-reverted)
- ❌ Inspector and evolver don't share data structures

The infrastructure is ~90% built. Applied-change verify→learn is now closed for both the evolver-suggestion (V2) and graduation (V3) lanes; remaining gaps are the navigator half (V4/V5) and inspector↔evolver data sharing.

## File Map

| File | Lines | Role |
|------|-------|------|
| src/inspector.py | ~2065 | Friction detection, alignment check |
| src/evolver.py | ~3265 | Improvement proposals, auto-apply, advisor wiring |
| src/graduation.py | ~495 | Repeated-pattern promotion |
| src/introspect.py | ~1590 | Failure classification, lenses |
| src/quality_gate.py | ~840 | Multi-pass review |
| src/skill_types.py | ~225 | Shared types (Skill, SkillStats, hash fns) — breaks circular import |
| src/skills.py | ~2055 | Discovery, scoring, circuit breaker (re-exports skill_types) |
| src/constraint.py | ~685 | Pre-execution enforcement, audit trail (constraint_log.jsonl) |
| src/eval.py | ~1140 | Evals-as-training-data flywheel |
