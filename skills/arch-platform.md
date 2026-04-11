---
name: arch-platform
description: Architecture context for platform layer (LLM adapters, config, heartbeat, projects, tasks, metrics)
roles_allowed: [worker, director, researcher]
triggers: [llm, adapter, config, heartbeat, orch_items, task_store, metrics, cost, model, subprocess, openrouter]
always_inject: false
---

# Platform Architecture

Operational substrate everything runs on. Model-agnostic, cost-aware, resilient.

## LLM Adapter Hierarchy (llm.py, ~1128 lines)

Unified `.complete(messages, tools, ...)` interface across backends:

```
build_adapter(backend="auto", model=MODEL_MID)
  → Try in order: AnthropicSDK → OpenRouter → OpenAI → ClaudeSubprocess
  → First one with valid API key wins
  → ClaudeSubprocess always available (spawns `claude -p`)
```

Model tiers (callers use constants, backends map to native IDs):
- **MODEL_CHEAP** (Haiku): Volume work, pre-flight review, cheap verification
- **MODEL_MID** (Sonnet): Default execution, decomposition, inspection
- **MODEL_POWER** (Opus): Advisor calls, garrytan: prefix, tier escalation

**Advisor Pattern** (`advisor_call()`): Sonnet executes, Opus advises at decision points. Wired into stuck detection. Next: milestone boundaries, evolver triggers.

**Important:** `ClaudeSubprocessAdapter` hangs during interactive sessions (`claude --continue` blocks `claude -p`). Pre-flight and heartbeat skip subprocess backend.

Retry: Automatic exponential backoff (5s, 15s, 45s) on rate limits, 5xx, connection failures.

## Config System (config.py)

Two-tier YAML mirroring git's ~/.gitconfig:

| File | Scope | Examples |
|------|-------|---------|
| `~/.poe/config.yml` | User-level | API keys, model prefs, yolo mode |
| `~/.poe/workspace/config.yml` | Workspace-level | Evolver, inspector thresholds, constraint settings |

Workspace inherits from user; workspace keys override. Nested dicts merge one level deep.

Access: `from config import get; get("inspector.breach_threshold", 0.30)`
Priority: env var > config.yml > hardcoded default.

## Heartbeat (heartbeat.py, ~998 lines)

Periodic health check + tiered self-healing (runs every 60s in loop mode):

- **Tier 1** (Scripted): Disk cleanup, config validation, API key checks
- **Tier 2** (LLM-Assisted): Cheap model diagnoses stuck projects
  - Diagnosis cooldown: 30 min per project (prevents runaway token burn)
  - Session guard: detects `claude --continue` → skips ALL autonomous LLM work
- **Tier 3** (Escalation): Telegram notification

**Lifecycle management:** Use `scripts/heartbeat-ctl.sh start|stop|status|restart`. Auto-stops after 4 hours. Never start as bare `nohup python3 heartbeat.py &`.

**Backlog drain:** Heartbeat picks up NEXT.md TODO items when idle. Interval: every 30 ticks (~30 min). Skips failed/paused projects (lifecycle markers `.poe-failed`/`.poe-paused`).

## Project & Item Management (orch_items.py)

Workspace structure:
```
~/.poe/workspace/
  projects/SLUG/
    NEXT.md          — Markdown todo list ([ ] / [x] / [~] / [!])
    DECISIONS.md     — What was chosen and why
    RISKS.md         — Known risks
    PROVENANCE.md    — Where data came from
    .poe-failed      — Lifecycle marker: skip in all automation
    .poe-paused      — Lifecycle marker: monitor but don't execute
  memory/            — All JSONL data stores
  output/            — Run artifacts, reports
```

**NEXT.md parsing:** Regex-based (`ITEM_RE`). States: TODO (` `), DOING (`~`), DONE (`x`/`X`), BLOCKED (`!`).

**Global backlog:** `select_global_next()` picks highest-priority project with available TODO items. Sort by (priority, mtime). Failed/paused projects excluded.

## Task Queue (task_store.py)

File-per-task JSON with fcntl advisory locking:
- **DAG dependencies:** `blocked_by` list with cycle detection on enqueue
- **Stale claim recovery:** If claiming PID dies, claim resets to queued
- **Continuation depth:** Prevents infinite rework loops
- **Lanes:** now, agenda, user_goal

## Metrics & Cost (metrics.py)

Per-model, per-step-type cost tracking to `memory/step-costs.jsonl`:
- Step types classified via regex (research, summarize, analyze, write, verify, implement, plan, general)
- Token counts, elapsed ms, goal preview, model used
- `tool_cost_report.py` for operator-facing summaries

**Gap:** Cost is recorded after-the-fact. No real-time budget enforcement ("stop, you've spent $5 on this goal") — only loop-level `cost_budget` parameter with coarse checking.

## Workspace Routing (known issue)

`~/.poe/workspace/` is the stable runtime workspace for memory and captain's log. But `output_root()` and `projects_root()` still resolve to the repo directory. This split needs consolidation.

## File Map

| File | Lines | Role |
|------|-------|------|
| src/llm.py | ~1128 | Adapter hierarchy, model abstraction, advisor |
| src/config.py | ~200 | Two-tier YAML config |
| src/heartbeat.py | ~998 | Health checks, session guard, backlog drain |
| src/orch_items.py | ~600 | Project/item management, NEXT.md |
| src/task_store.py | ~400 | File-per-task queue, DAG deps |
| src/metrics.py | ~300 | Cost tracking, step classification |
| scripts/heartbeat-ctl.sh | | Lifecycle management (start/stop/status) |
