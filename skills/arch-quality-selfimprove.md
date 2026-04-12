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

**Current reality:** The loop runs from "detect" through "apply" but breaks at "verify." Nobody checks that applied changes fixed the diagnosed problem. This is the critical gap.

## Per-Run Quality (zoom in)

### Inspector (inspector.py, ~1964 lines)
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

### Introspect (introspect.py, ~1448 lines)
Failure classification (11 types: setup_failure, adapter_timeout, token_explosion, etc.). Each diagnosis has severity, evidence, recommendation. Written to diagnoses.jsonl.

Lenses: infrastructure exists but not fully wired. Heuristic lenses (free) run always; LLM lenses run selectively.

### Constraint (constraint.py)
Pre-execution enforcement. Tiered gates: READ (observe), WRITE (warn), DESTROY (block), EXTERNAL (confirm). Dynamic constraints from evolver (JSONL + TTL + circuit breaker).

## Over-Time Improvement (zoom out)

### Evolver (evolver.py, ~2126 lines)
Proposes improvements from outcome patterns. Triggered by heartbeat (~every 10 ticks) or manually.

Suggestion types:
- `prompt_tweak` → auto-applied as TieredLesson (low risk)
- `new_guardrail` → held for human review by default
- `skill_pattern` → unit-test gate before apply
- `observation` → auto-applied (informational)
- `sub_mission` → proposed follow-up goal (not auto-enqueued)

Applied changes logged to change_log.jsonl with rollback snapshots.

### Graduation (graduation.py)
Scans diagnoses.jsonl for repeated failure classes (≥3 occurrences). Promotes to permanent fixes using templates (8 failure classes covered). Each template has a verify_pattern (shell command).

**Gap:** verify_graduation_rules() exists but isn't called automatically.

### Skills (skills.py, ~2164 lines)
Discovery, scoring, promotion/demotion with circuit breaker:
- **Score:** use_count, success_rate, utility_score (EMA), consecutive streaks
- **Circuit states:** closed (normal) → half_open (recovering) → open (rewrite eligible)
- **Auto-promote:** ≥5 uses + ≥70% success → provisional→established
- **Auto-demote:** ≥3 consecutive failures opens circuit, triggers rewrite
- **Test gate:** Skill mutations blocked if unit tests fail

**Gap:** Auto-promote/demote works for existing skills. New skill discovery from outcomes is rare.

## The Self-Improvement Gap

What's autonomous today:
- ✅ Prompt tweaks auto-applied as lessons
- ✅ Skills auto-promoted/demoted based on success rate
- ✅ Low-risk recovery auto-applied (Phase 45)

What requires humans:
- ❌ Guardrails held for review (correct safety boundary)
- ❌ No auto-verification of applied changes
- ❌ No auto-enqueue of follow-up missions
- ❌ Graduated rules not auto-verified in heartbeat
- ❌ Inspector and evolver don't share data structures

The infrastructure is 80% built. Closing the verify→learn loop is the 20% that makes it autonomous.

## File Map

| File | Lines | Role |
|------|-------|------|
| src/inspector.py | ~1964 | Friction detection, alignment check |
| src/evolver.py | ~2126 | Improvement proposals, auto-apply, advisor wiring |
| src/graduation.py | ~482 | Repeated-pattern promotion |
| src/introspect.py | ~1448 | Failure classification, lenses |
| src/quality_gate.py | ~655 | Multi-pass review |
| src/skills.py | ~2164 | Discovery, scoring, circuit breaker |
| src/constraint.py | ~623 | Pre-execution enforcement |
| src/eval.py | ~979 | Evals-as-training-data flywheel |
