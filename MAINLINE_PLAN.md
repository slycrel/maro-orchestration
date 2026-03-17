# Mainline Plan

## Current baseline (v0.5.0)

Mainline includes:
- Deterministic orchestration core (`src/orch.py`)
- Stable CLI (`src/cli.py`)
- Tests, smoke harness, CI
- Migration/compat/security docs
- Persona specs and persona directory
- Shell scripts for task queue, heartbeat, orchestration tick/pump
- **NEW**: `VISION.md` — full intent guide for Agentic Poe
- **NEW**: `ROADMAP.md` — 8-phase build plan (Phase 0-7)
- **NEW**: Source documents in `docs/` (intent, spec, anti-patterns)

## What v0.5.0 represents

An honest audit of the codebase. The original M0-M4 milestones built real infrastructure scaffolding. v0.5.0 acknowledges that and resets the roadmap around the actual goal: making Poe autonomous.

The old N1-N4 roadmap (execution adapters, multi-project scheduling, collaboration workflows, distribution hardening) has been superseded by the phased plan in `ROADMAP.md`. Infrastructure items will be absorbed into the relevant phases as needed.

## What's next

Phase 0 from the roadmap — see `ROADMAP.md`. Then Phase 1: the autonomous loop, which is the critical unlock.

## Release steps (`v0.5.0`)

```bash
cd openclaw-orchestration
python3 -m pytest tests/
bash scripts/smoke.sh
git add VISION.md ROADMAP.md MAINLINE_PLAN.md docs/poe_intent.md docs/poe_orchestration_spec.md docs/poe_miscommunication_patterns.md
git commit -m "feat: add vision guide, reset roadmap for autonomy-first build"
git tag -a v0.5.0 -m "v0.5.0: honest foundation audit + autonomy roadmap"
git push origin HEAD --follow-tags
```
