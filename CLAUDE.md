# Claude Code — Maro

**This is the mainline repo** (`maro`, formerly `openclaw-orchestration`). All orchestration work happens here unless explicitly directed elsewhere.

**Currency rule:** narrative prose anywhere (including this file) loses to GOAL_BRAIN.md and MILESTONES.md. If a doc or skill states a fact you've just proven stale, fix it in the same commit — don't leave it for "later".

**Start-of-session checklist:**
1. Read this file (CLAUDE.md)
2. Read GOAL_BRAIN.md — compiled truth: Jeremy's invariants (quoted), verified state, decisions, open threads. **When it disagrees with any other doc, GOAL_BRAIN.md wins** — all other docs are best-guess by decree. Update its system-maintained sections at end-of-chunk.
3. Read MILESTONES.md — prioritized work queue. This is what to do next.
4. Read BACKLOG.md — active deferred items, bugs, ideas. Update as you work. When an item ships, move it to BACKLOG_DONE.md with its context intact (the archive is ingested by `dev-recall` for historical "why/how/rejected" context).
5. Looking for a specific doc? `docs/INDEX.md` maps questions → docs and carries the status legend (living / dormant-design / history).
6. Check `~/claude/grok-response-*.txt` for unprocessed feedback

**When you need to recall something from prior correspondence (design docs, conversation logs, rationale for a past decision), use `dev-recall` instead of blind grep.** It's full-text (FTS5/BM25) retrieval over docs/, lat.md/, GOAL_BRAIN/VISION/MILESTONES/BACKLOG/BACKLOG_DONE/ROADMAP/CLAUDE, and auto-memory:

```bash
PYTHONPATH=src python3 -m correspondence query "why did we rename constraint to scope"
PYTHONPATH=src python3 -m correspondence ingest --since 1d   # re-embed recent changes
PYTHONPATH=src python3 -m correspondence status
```

This is **dev-facing tooling only** — not part of Maro's runtime self-improvement. See `src/correspondence.py` module docstring. Don't blur these.

**Before modifying a subsystem, load its architecture skill.** The `skills/arch-*.md` files describe intent, interfaces, gaps, and file maps for each subsystem. Read the relevant one before making design decisions:

| Working on... | Load this skill |
|--------------|----------------|
| Goal entry, routing, intent, director, workers, personas | `skills/arch-interface-routing.md` |
| Core loop, decompose, step execution, pre-flight | `skills/arch-core-loop.md` |
| Memory, knowledge, lessons, captain's log, crystallization | `skills/arch-memory-knowledge.md` |
| Inspector, evolver, graduation, introspect, skills, constraints | `skills/arch-quality-selfimprove.md` |
| LLM adapters, config, heartbeat, projects, tasks, metrics | `skills/arch-platform.md` |

These skills document **intent vs implementation gaps** — what the system is supposed to do vs what's actually coded. They prevent accidental regressions and surface the real design constraints.

**Session patterns:** read `docs/DEV_PATTERNS.md` before shaping or
reviewing work — the taste half (cuts-first, consumer-first, done-means,
possible-now bias…) while planning, the judgement half (live writer?
executed check? claim verified?…) while reviewing. Non-gated pre-read,
honestly labeled: the 2026-07-21 with-doc/control battery showed no
measurable delta (both arms at ceiling; see
`docs/history/2026-07-21-phase05-battery.md`) — it ships on cost≈zero
grounds, not benchmark evidence.

**Coding posture:** read `docs/CODING_NOTES.md` before shipping. This repo
is heavily iterating — principles for keeping seams visible and rework
cheap live there (registry vs dispatch, 3-is-fine/4-wants-extraction,
don't-refactor-mid-feature, test seams not internals, etc.). Not a style
guide; the minimum overhead that keeps the codebase honest during
exploration.

**Open design spaces** — if your work touches these, read the doc first:

| Space | Doc | Status |
|---|---|---|
| Intent resolution / side-quests / "what does done mean" | `docs/INTENT_RESOLUTION_DESIGN.md` | Partially shipped (ResolvedIntent/Deliverable live); side-quest handling open |
| Scope + constraint orchestration (Phase 65) | `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + review | Scope+ResolvedIntent injection LIVE on this box since 2026-07-09 (SF-4 resolution; 2026-04-22 A/B: inject wins). Fresh installs: `scope_generation` OFF by default (no silent LLM spend). Deeper constraint-orchestration discussion deferred |
| Adaptive execution | `docs/ADAPTIVE_EXECUTION_DESIGN.md` | Dormant design — not started |
| Memory / graph / filesystem-vs-real-memory | `docs/history/2026-07-04-memory-decision-brief.md` (inputs: `docs/MEMORY_ARCHITECTURE.md`, `docs/KNOWLEDGE_CRYSTALLIZATION.md`) | Direction decided 2026-07-07: memory-as-module, 3rd-party bake-off behind `src/memory_port.py`; MILESTONES arc -1 |

- GitHub: https://github.com/slycrel/maro-orchestration (renamed from openclaw-orchestration 2026-06-26; kept the `-orchestration` suffix rather than bare `maro`)
- Machine: Ubuntu headless, user `clawd`, `/home/clawd/claude/maro-orchestration/`
- Owner: Jeremy Stone (`slycrel`) — 25+ years engineering, AI orchestration

---

## What this is

**Maro** — an autonomous agent framework. Takes a high-level mission, breaks it into milestones, executes over days/weeks, learns from what works, reports progress without hand-holding. The framework orchestrates as a neutral role (the Conductor) and can optionally wear a persona (e.g. `personas/poe.md`). User's job: mission definition + exception handling.

North star: self-improving, autonomous agent. Visible → Reliable → Replayable.

---

## Architecture (5 subsystems)

See `docs/ARCHITECTURE_OVERVIEW.md` for the full map with intent-vs-implementation gaps. (The older, longer `ARCHITECTURE.md` is a point-in-time record in `docs/history/` — pre-rename era, don't treat it as current.)

| Subsystem | What | Key files | Skill |
|-----------|------|-----------|-------|
| **Interface** | Goal entry, classification, routing | handle.py, intent.py, director.py, workers.py, persona.py | `skills/arch-interface-routing.md` |
| **Core Loop** | Decompose → execute → introspect | agent_loop.py, planner.py, step_exec.py, pre_flight.py | `skills/arch-core-loop.md` |
| **Memory/Knowledge** | Recording, retrieval, crystallization | memory.py, knowledge_web.py, knowledge_lens.py, memory_ledger.py | `skills/arch-memory-knowledge.md` |
| **Quality + Self-Improvement** | Validation AND getting better over time | inspector.py, evolver.py, graduation.py, introspect.py, skills.py | `skills/arch-quality-selfimprove.md` |
| **Platform** | LLM adapters, config, heartbeat, projects, tasks, metrics | llm.py, config.py, heartbeat.py, orch_items.py, task_store.py | `skills/arch-platform.md` |

**Two things, often conflated:**
- **Maro-as-tool**: Execute tasks autonomously. *Works today.*
- **Maro-as-self-improving-system**: Detect friction → change behavior → verify it worked → learn. *Infrastructure 80% built; verify→learn loop not closed.*

---

## Repo layout

```
src/                 All production Python (~130 flat modules; REFACTOR_PLAN Tier 4 = subpackage plan)
  agent_loop.py      Core loop entry (physical phases split into loop_*.py modules)
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

tests/               pytest suite (run via bash scripts/test-safe.sh; counts change weekly)
scripts/             smoke.sh, audit-phases.sh, enqueue.sh
personas/            YAML persona specs
docs/                Architecture, memory systems, self-reflection design
lat.md/              Knowledge graph: 9 cross-linked concept nodes + index
memory/              Repo-local: stale copies (tests write here via OPENCLAW_WORKSPACE). Real data is in ~/.maro/workspace/memory/
output/              Repo-local output (real output in ~/.maro/workspace/output/)
research/            Research outputs: X link synthesis, Polymarket validation, Phase 41 design
user/                Neutral operator-doc templates (GOALS, CONFIG, CONTEXT, SIGNALS, COMPLETION_STANDARD);
                     real files live in ~/.maro/workspace/user/ (overlay wins) — see user/README.md
personas/poe.md      Optional Poe persona (the framework defaults to a neutral role)
deploy/              systemd service files
```

---

## Current state

**This file does not track current state — by design.** Current truth lives in GOAL_BRAIN.md (compiled truth + decisions), MILESTONES.md (queue), and BACKLOG.md. Phase history: `docs/history/` (ROADMAP_ARCHIVE for completed phases). A snapshot here rots — the 2026-04-14 snapshot that used to live in this section sat stale for months claiming to be current.

Prototype-era steal-list research (all items long since shipped) is recorded in `docs/history/` (steal-list + sources). The old prototype at `~/.openclaw/workspace/prototypes/poe-orchestrator/` is reference only; do not develop there.

---

## Where things live on this machine

| Path | What |
|------|------|
| `/home/clawd/claude/maro-orchestration/` | **This repo — mainline** |
| `~/.openclaw/workspace/` | OpenClaw system (GPT/Codex-based). Has SOUL.md, TASKS.md, AGENTS.md, GOALS.md |
| `~/.openclaw/workspace/prototypes/poe-orchestrator/` | Old prototype — reference only, do not continue work here |
| `~/.openclaw/workspace/scripts/` | ~80 shell scripts: heartbeat, task queue, X/Telegram/email |
| `~/.claude/projects/.../memory/` | Claude Code persistent memory across sessions |
| `/home/clawd/.maro/workspace/` | **Stable runtime workspace** — all learning data, self-evolved artifacts, and runtime state. Not in git. |

**Workspace layout (`~/.maro/workspace/`):**

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
| `~/.maro/config.yml` | User-level | Model prefs, notifications (API keys stay in env or `secrets/.env`; `yolo` lives in `user/CONFIG.md` / `MARO_YOLO`) |
| `~/.maro/workspace/config.yml` | Workspace-level | Evolver, inspector thresholds, constraint settings, quality gate |

Workspace inherits from user; workspace keys override. Nested dicts merge one level deep.

Access in code: `from config import get; get("inspector.breach_threshold", 0.30)`

Priority: env var > config.yml > hardcoded default. Tests are isolated (config reads from tmp paths).

---

## Running things

```bash
# Tests — targeted (safe to run alongside TUI)
cd /home/clawd/claude/maro-orchestration
python3 -m pytest tests/test_agent_loop.py -q

# Tests — full suite (use this one — caps CPU to 2 cores + nice 15)
# Runs in 40-file chunks so progress is visible; won't tip over the box.
bash scripts/test-safe.sh

# Fast feedback lane (explicitly skips @pytest.mark.slow)
bash scripts/test-safe.sh --fast

# Tests — full suite, raw (only when the box is idle / no TUI running)
python3 -m pytest tests/ -q

# Tests — with coverage (enforces 70% floor per .coveragerc)
bash scripts/test-cov.sh
bash scripts/test-cov.sh --html     # also produce output/coverage_html/

# Smoke
bash scripts/smoke.sh

# Phase audit
bash scripts/audit-phases.sh

# Run a goal (defaults to ~/.maro/workspace/ — no env vars needed)
cd /home/clawd/claude/maro-orchestration
PYTHONPATH=src python3 -m handle "your goal here"

# Introspection (Phase 44)
maro-introspect --latest
maro-introspect --latest --lenses
```

---

## Jeremy's communication style

- Says what he means once. If permission is granted, it's granted.
- "Sounds good" = execute now. "Keep going" = stop pausing.
- Frustrated by: re-asking for permission, plans presented as work, option tables when action suffices.
- Values: honest "tried X, failed, learned Y, trying Z" updates. Progress over perfection.

Act, don't ask. Forgiveness over permission. Ask first only for: spending real money, posting publicly as Jeremy, destructive irreversible actions, exposing private data.

---

## End-of-chunk discipline

When a chunk of work is done (milestone delivered, bug fixed, feature shipped — not every tiny edit), always:

1. **Document** — update MILESTONES.md / BACKLOG.md / relevant docs so the next session knows what changed and what's next.
2. **Commit** — clean, scoped commit with a useful message. No "WIP" or dangling work.
3. **Land** — get it onto `main`. On the maro box, once tests are green, land your
   directed work directly with **`bash scripts/land.sh`** (fast-forwards `main`
   over SSH — no PR, no GitHub API token). Don't leave a finished chunk sitting
   on a branch waiting for a human to merge it; a box crash loses unlanded work.

**Landing policy (Jeremy, 2026-07-20 — "PRs for Poe; maro box continues as before"):**
The PR-and-human-review gate is the **Poe/Hermes** lane only (mini2 dispatched-
autonomous work — `deploy/hermes/`, `PROPOSE_LANE.md`), where an agent that could
modify its own orchestration must have a human in the loop. The **maro box lands
its own directed work directly to main** — you're already the human in the loop
here. `scripts/land.sh` is ff-only and never force-pushes main, so it's safe
alongside concurrent sessions. (`gh` PR creation is dead on this box — invalid
token — and stays moot for this path; SSH push is the credential that works.)

Don't wait to be asked. Landing is cheap, forgetting is expensive.

**Session-close rule (SF-13, standing since 2026-07-09):** any Jeremy
statement worth an auto-memory write also gets a GOAL_BRAIN.md Decisions
line before the session ends — even when the conversation produced no work
chunk. Decree-class statements must reach the compiled record, not just
Claude's memory; a session that ends conversationally is the exact case
this rule exists for. Since 2026-07-21 (swarm-review chunk 3) the same
decree also gets piped into the RUNTIME decision journal so recall can
surface it to runs:
`PYTHONPATH=src python3 -m knowledge_lens decision "<decree>" --rationale "<why>"`.

**Capability-capture rule (2026-07-11, Jeremy):** when work surfaces a
real ask or a missing capability mid-session — a user-shaped request, a
run failure that names a skill we don't have, a "we should be able to
just ask it X" moment — capture it in `docs/CAPABILITIES.md` as-phrased
(with tier + verified/target/aspirational mark) in the same session.
This is the middle ground between testing and backlog Jeremy asked for:
more concrete than an idea, not yet a test goal. Don't wait for a
dedicated capabilities pass; the phrasing is the value and it evaporates.
