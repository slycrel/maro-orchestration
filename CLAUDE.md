# Claude Code — openclaw-orchestration

**This is the mainline repo.** All orchestration work happens here unless explicitly directed elsewhere.

- GitHub: https://github.com/slycrel/openclaw-orchestration
- Machine: Ubuntu headless, user `clawd`, `/home/clawd/claude/openclaw-orchestration/`
- Owner: Jeremy Stone (`slycrel`) — 25+ years engineering, AI orchestration

---

## What this is

**Poe** — an autonomous AI concierge (named after the AI from *Altered Carbon*). Takes a high-level mission, breaks it into milestones, executes over days/weeks, learns from what works, reports progress without hand-holding. User's job: mission definition + exception handling.

North star: self-improving, autonomous agent. Visible → Reliable → Replayable.

---

## Repo layout

```
src/                 All production Python (50+ modules)
  agent_loop.py      Core autonomous execution loop (~74KB)
  handle.py          Entry point — routes to NOW or AGENDA lane
  intent.py          Goal classifier (NOW vs AGENDA)
  director.py        Director: plans, delegates, reviews
  workers.py         Workers: research / build / ops / general
  inspector.py       Quality gates — friction detection
  evolver.py         Meta-improvement every ~10 heartbeats
  memory.py          Outcome recording, lesson extraction, Reflexion
  skills.py          Skill library: auto-promote, score, test
  introspect.py      Phase 44: failure classifier + lenses (IN PROGRESS)
  llm.py             LLM adapter suite (Anthropic, OpenRouter, OpenAI, subprocess)
  web_fetch.py       Jina Reader + X/tweet fetching (Phase 30 — token saver)
  metrics.py         Cost + token tracking per model
  persona.py         Persona system — modular agent identities
  constraint.py      Pre-execution constraint enforcement
  ...

tests/               50+ test files, ~1290 tests
scripts/             smoke.sh, audit-phases.sh, enqueue.sh
personas/            YAML persona specs
docs/                Architecture, memory systems, self-reflection design
memory/              Runtime: outcomes.jsonl, lessons.jsonl, diagnoses.jsonl
output/              Runtime: phase audits, self-review reports
deploy/              systemd service files
```

---

## Current state

**As of 2026-03-29:**

| Phase | Name | Status |
|-------|------|--------|
| 0–22 | Foundation through Knowledge Crystallization | DONE |
| 23 | Observability Dashboard | PARTIAL |
| 24 | Messaging Integrations (Slack, Signal) | PARTIAL |
| 25 | Ops Hardening | PARTIAL |
| 27 | Prerequisite Knowledge Sub-Goals | PARTIAL |
| 28 | Poe Personality | PARTIAL |
| 29 | Human Psychology Research Track | PARTIAL |
| 30–39, 43 | Token visibility, skills auto-promotion, token self-improvement, overnight missions, dashboards, skill synthesis, OSS hygiene, structured logging | DONE |
| 38 | Subpackage Structure | PARTIAL |
| 40 | Pluggable Memory Backend | TODO |
| 41 | Tool Registry + Function Calling | TODO |
| 42 | Nightly Eval Wired to Evolver | TODO |
| **44** | **Self-Reflection — Run Observer + Failure Classifier** | **IN PROGRESS** |
| 45 | Self-Reflection — Recovery Planner | TODO |
| 46 | Self-Reflection — Intervention Graduation | TODO |

**Active work:** Phase 44. `src/introspect.py` is 1138 lines — core diagnosis engine complete, lenses wired, CLI wired (`poe-introspect`), auto-diagnose wired into `_finalize_loop()`. What's left: mark ROADMAP.md Phase 44 DONE, then start Phase 45 (recovery planner — lookup table, no LLM).

See `ROADMAP.md` for full phase specs. See `CHANGELOG.md` for what shipped.

---

## Queued steal-list items (from prototype research)

These were identified from studying ClawTeam, MetaClaw, superpowers, oh-my-claudecode, Agent-Reach. Not all apply — these are the candidates worth considering:

| Item | What | Priority |
|------|------|----------|
| `task_store.py` port | FileTaskStore: file-per-task JSON, fcntl locking, DAG dep resolution, stale claim recovery. Replaces bash task-queue.sh pattern. | NOW |
| Ralph verify loop | `verify_step_id` on AGENDA steps — run verifier after step, retry if fails. Explicit quality gate complementing Inspector. | NEXT |
| Magic keyword triggers | `ralph:`, `pipeline:`, `verify:`, `strict:` prefixes in goal text mutate execution behavior. Could live in `intent.py`. | NEXT |
| SlowUpdateScheduler | IDLE_WAIT → WINDOW_OPEN → UPDATING → PAUSING. Gates heavy background work to idle windows. Good for heartbeat consolidation. | NEXT |
| `doctor` command | Check which tools/channels work in current environment. Useful for validate-environment before runs. | NEXT |

Reference implementation: `~/.openclaw/workspace/prototypes/poe-orchestrator/` — that's the prototype where these were prototyped. Use it for reference only; do not develop there.

---

## Where things live on this machine

| Path | What |
|------|------|
| `/home/clawd/claude/openclaw-orchestration/` | **This repo — mainline** |
| `~/.openclaw/workspace/` | OpenClaw system (GPT/Codex-based). Has SOUL.md, TASKS.md, AGENTS.md, GOALS.md |
| `~/.openclaw/workspace/prototypes/poe-orchestrator/` | Old prototype — reference only, do not continue work here |
| `~/.openclaw/workspace/scripts/` | ~80 shell scripts: heartbeat, task queue, X/Telegram/email |
| `~/.claude/projects/.../memory/` | Claude Code persistent memory across sessions |

---

## Running things

```bash
# Tests
cd /home/clawd/claude/openclaw-orchestration
python3 -m pytest tests/ -q

# Smoke
bash scripts/smoke.sh

# Phase audit
bash scripts/audit-phases.sh

# Introspection (Phase 44)
poe-introspect --latest
poe-introspect --latest --lenses

# Observe dashboard (Phase 36)
poe-observe serve
```

---

## Jeremy's communication style

- Says what he means once. If permission is granted, it's granted.
- "Sounds good" = execute now. "Keep going" = stop pausing.
- Frustrated by: re-asking for permission, plans presented as work, option tables when action suffices.
- Values: honest "tried X, failed, learned Y, trying Z" updates. Progress over perfection.

Act, don't ask. Forgiveness over permission. Ask first only for: spending real money, posting publicly as Jeremy, destructive irreversible actions, exposing private data.
