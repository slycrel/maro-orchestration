# Claude Code — openclaw-orchestration

**This is the mainline repo.** All orchestration work happens here unless explicitly directed elsewhere.

**Start-of-session checklist:**
1. Read this file (CLAUDE.md)
2. Read BACKLOG.md — deferred items, bugs, ideas. Update as you work.
3. Check ROADMAP.md for phase status
4. Check `~/claude/grok-response-*.txt` for unprocessed feedback

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
memory/              Runtime: outcomes.jsonl, lessons.jsonl, standing_rules.jsonl, decisions.jsonl
output/              Runtime: phase audits, self-review reports
research/            Research outputs: X link synthesis, Polymarket validation, Phase 41 design
user/                POE_IDENTITY.md — durable Poe identity (editable)
deploy/              systemd service files
```

---

## Current state

**As of 2026-04-09.** Phases 0–61 complete. Tests: 3,235 passing (92s sequential).

| Status | Phases |
|--------|--------|
| DONE | 0–23, 26–27, 29–37, 39–48, 50–61 |
| PARTIAL | 24 (Slack skeleton, Signal deferred), 25 (ops hardening — heartbeat-ctl.sh shipped), 28 (persona — blocked on personas/jeremy.md), 38 (subpackage) |

**Recent (Apr 7-9):** Pre-flight subprocess hang fixed (skip ClaudeSubprocessAdapter). Heartbeat ops hardening: diagnosis cooldown, session guard, `heartbeat-ctl.sh` with 4h auto-stop. Project lifecycle state (`.poe-failed`/`.poe-paused`). Compact notation skill. Full test suite sealed against HTTP leaks + hanging subprocess calls. xdist disabled (sequential is <2min).

**Next:** Auto persona+skill packaging (Phase 62 candidate). Compact notation A/B testing. Doc cleanup in progress.

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
