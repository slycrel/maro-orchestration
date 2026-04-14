# Claude Code — openclaw-orchestration

**This is the mainline repo.** All orchestration work happens here unless explicitly directed elsewhere.

**Start-of-session checklist:**
1. Read this file (CLAUDE.md)
2. Read MILESTONES.md — prioritized work queue. This is what to do next.
3. Read BACKLOG.md — deferred items, bugs, ideas. Update as you work.
4. Check ROADMAP.md for phase status
5. Check `~/claude/grok-response-*.txt` for unprocessed feedback

**Before modifying a subsystem, load its architecture skill.** The `skills/arch-*.md` files describe intent, interfaces, gaps, and file maps for each subsystem. Read the relevant one before making design decisions:

| Working on... | Load this skill |
|--------------|----------------|
| Goal entry, routing, intent, director, workers, personas | `skills/arch-interface-routing.md` |
| Core loop, decompose, step execution, pre-flight | `skills/arch-core-loop.md` |
| Memory, knowledge, lessons, captain's log, crystallization | `skills/arch-memory-knowledge.md` |
| Inspector, evolver, graduation, introspect, skills, constraints | `skills/arch-quality-selfimprove.md` |
| LLM adapters, config, heartbeat, projects, tasks, metrics | `skills/arch-platform.md` |

These skills document **intent vs implementation gaps** — what the system is supposed to do vs what's actually coded. They prevent accidental regressions and surface the real design constraints.

- GitHub: https://github.com/slycrel/openclaw-orchestration
- Machine: Ubuntu headless, user `clawd`, `/home/clawd/claude/openclaw-orchestration/`
- Owner: Jeremy Stone (`slycrel`) — 25+ years engineering, AI orchestration

---

## What this is

**Poe** — an autonomous AI concierge (named after the AI from *Altered Carbon*). Takes a high-level mission, breaks it into milestones, executes over days/weeks, learns from what works, reports progress without hand-holding. User's job: mission definition + exception handling.

North star: self-improving, autonomous agent. Visible → Reliable → Replayable.

---

## Architecture (5 subsystems)

See `docs/ARCHITECTURE_OVERVIEW.md` for the full map with intent-vs-implementation gaps.

| Subsystem | What | Key files | Skill |
|-----------|------|-----------|-------|
| **Interface** | Goal entry, classification, routing | handle.py, intent.py, director.py, workers.py, persona.py | `skills/arch-interface-routing.md` |
| **Core Loop** | Decompose → execute → introspect | agent_loop.py, planner.py, step_exec.py, pre_flight.py | `skills/arch-core-loop.md` |
| **Memory/Knowledge** | Recording, retrieval, crystallization | memory.py, knowledge_web.py, knowledge_lens.py, memory_ledger.py | `skills/arch-memory-knowledge.md` |
| **Quality + Self-Improvement** | Validation AND getting better over time | inspector.py, evolver.py, graduation.py, introspect.py, skills.py | `skills/arch-quality-selfimprove.md` |
| **Platform** | LLM adapters, config, heartbeat, projects, tasks, metrics | llm.py, config.py, heartbeat.py, orch_items.py, task_store.py | `skills/arch-platform.md` |

**Two things, often conflated:**
- **Poe-as-tool**: Execute tasks autonomously. *Works today.*
- **Poe-as-self-improving-system**: Detect friction → change behavior → verify it worked → learn. *Infrastructure 80% built; verify→learn loop not closed.*

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
  introspect.py      Phases 44–46: failure classifier, lenses, recovery planner, intervention graduation (DONE)
  llm.py             LLM adapter suite (Anthropic, OpenRouter, OpenAI, subprocess)
  web_fetch.py       Jina Reader + X/tweet fetching (Phase 30 — token saver)
  metrics.py         Cost + token tracking per model
  persona.py         Persona system — modular agent identities
  constraint.py      Pre-execution constraint enforcement
  ...

tests/               60+ test files, ~3,195 tests
scripts/             smoke.sh, audit-phases.sh, enqueue.sh
personas/            YAML persona specs
docs/                Architecture, memory systems, self-reflection design
lat.md/              Knowledge graph: 9 cross-linked concept nodes + index
memory/              Repo-local: stale copies (tests write here via OPENCLAW_WORKSPACE). Real data is in ~/.poe/workspace/memory/
output/              Repo-local output (real output in ~/.poe/workspace/output/)
research/            Research outputs: X link synthesis, Polymarket validation, Phase 41 design
user/                POE_IDENTITY.md — durable Poe identity (editable)
deploy/              systemd service files
```

---

## Current state

**As of 2026-04-12.** Phases 0–61 complete. Tests: 3,789 passing (~100s sequential).

| Status | Phases |
|--------|--------|
| DONE | 0–23, 26–27, 29–37, 39–48, 50–61 |
| PARTIAL | 24 (Slack skeleton, Signal deferred), 25 (ops hardening — heartbeat-ctl.sh shipped), 28 (persona — blocked on personas/jeremy.md), 38 (subpackage) |

**Recent (Apr 12, session 17):** Test isolation overhaul (conftest.py). Circular import skills↔evolver broken via skill_types.py. Verify→learn loop closed (_verify_post_apply). Constraint audit trail. Playbook validation. Evolver rollback API (revert_suggestion). Eval regression detection. knowledge_web + orch_bridges + workers tests. 3 adversarial review rounds. 3789 tests (+320).

**Next:** See MILESTONES.md for prioritized queue. Top items: real-world regression tests, K2 links import, LoopStateMachine conversion, evolver confidence calibration.

See `ROADMAP.md` for active phases (57+). See `docs/ROADMAP_ARCHIVE.md` for completed phases (0–56).

---

## Queued steal-list items (from prototype research)

These were identified from studying ClawTeam, MetaClaw, superpowers, oh-my-claudecode, Agent-Reach. Not all apply — these are the candidates worth considering:

| Item | What | Priority |
|------|------|----------|
| `task_store.py` port | FileTaskStore: file-per-task JSON, fcntl locking, DAG dep resolution, stale claim recovery. Replaces bash task-queue.sh pattern. | DONE |
| Ralph verify loop | `verify_step_id` on AGENDA steps — run verifier after step, retry if fails. Explicit quality gate complementing Inspector. | DONE |
| Magic keyword triggers | `ralph:`, `pipeline:`, `verify:`, `strict:` prefixes in goal text mutate execution behavior. | DONE (2026-04-02) |
| SlowUpdateScheduler | IDLE_WAIT → WINDOW_OPEN → UPDATING → PAUSING. Gates heavy background work to idle windows. Good for heartbeat consolidation. | DONE (2026-04-04) |
| `doctor` command | Check which tools/channels work in current environment. Useful for validate-environment before runs. | DONE (extended with Phase 41 checks, 2026-04-02) |

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
| `/home/clawd/.poe/workspace/` | **Stable runtime workspace** — all learning data, self-evolved artifacts, and runtime state. Not in git. |

**Workspace layout (`~/.poe/workspace/`):**

| Path | What | Written by |
|------|------|-----------|
| `memory/` | Outcomes, lessons, knowledge nodes, captain's log, diagnoses | reflect_and_record, learning pipeline |
| `skills/` | Self-created/evolved skill .md files (override repo skills) | evolver |
| `personas/` | Self-created/evolved persona specs (override repo personas) | evolver |
| `playbook.md` | Director's operational wisdom (auto-maintained) | evolver, append_to_playbook() |
| `output/` | Run artifacts, operator status, research outputs | agent_loop, orch |
| `projects/` | Per-project NEXT.md, decisions, risks | orch_items |
| `config.yml` | Workspace-level config | manual |

**Resolution order** for skills and personas: workspace → repo. When the system evolves a better version of a shipped skill/persona, the workspace version wins. Repo versions are the shipped defaults.

---

## Configuration

Two-tier YAML config (like git's `~/.gitconfig` vs `.git/config`):

| File | Scope | What goes here |
|------|-------|---------------|
| `~/.poe/config.yml` | User-level | API keys, model prefs, yolo mode, notifications |
| `~/.poe/workspace/config.yml` | Workspace-level | Evolver, inspector thresholds, constraint settings, quality gate |

Workspace inherits from user; workspace keys override. Nested dicts merge one level deep.

Access in code: `from config import get; get("inspector.breach_threshold", 0.30)`

Priority: env var > config.yml > hardcoded default. Tests are isolated (config reads from tmp paths).

---

## Running things

```bash
# Tests — targeted (safe to run alongside TUI)
cd /home/clawd/claude/openclaw-orchestration
python3 -m pytest tests/test_agent_loop.py -q

# Tests — full suite (use this one — caps CPU to 2 cores + nice 15)
# Runs in chunks of 1000 so progress is visible; won't tip over the box.
bash scripts/test-safe.sh

# Tests — full suite, raw (only when the box is idle / no TUI running)
python3 -m pytest tests/ -q

# Smoke
bash scripts/smoke.sh

# Phase audit
bash scripts/audit-phases.sh

# Run a goal (defaults to ~/.poe/workspace/ — no env vars needed)
cd /home/clawd/claude/openclaw-orchestration
PYTHONPATH=src python3 -m handle "your goal here"

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
