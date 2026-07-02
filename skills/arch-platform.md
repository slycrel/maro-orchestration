---
name: arch-platform
description: Architecture context for platform layer (LLM adapters, config, heartbeat, projects, tasks, metrics)
roles_allowed: [worker, director, researcher]
triggers: [llm, adapter, config, heartbeat, orch_items, task_store, metrics, cost, model, subprocess, openrouter]
always_inject: false
---

# Platform Architecture

Operational substrate everything runs on. Model-agnostic, cost-aware, resilient.

## LLM Adapter Hierarchy (llm.py, ~1700 lines)

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

**Agentic subprocess cwd contract.** Subprocess adapters (`claude -p`, `codex`) spawn an agent that does *real* file tool work, so where it writes matters. The cwd is resolved as `kwargs["cwd"] or get_default_subprocess_cwd()` in both subprocess adapters' `complete()`. `_DEFAULT_SUBPROCESS_CWD` is a run-scoped `ContextVar`:
- `run_agent_loop` sets it to the project dir; `handle.py` scopes it around `run_quality_gate`; `claim_probe` reads it for its `settled_by_command` runner.
- **Deliberately NOT reset on loop exit** — quality_gate runs *after* the loop returns and must inherit the same project dir; recursive/fan-out sub-loops re-set it on their own entry. Tests reset it via an autouse conftest fixture (`_clear_default_subprocess_cwd`). Do not "fix" the no-reset — it's load-bearing.
- NOW lane leaves it unset (None) → inherits Maro's launch cwd, which is correct for an interactive ask.
- *Why it exists:* the executor always bound cwd, but the non-executor agentic paths (verify/quality_gate/pre_flight/refinement/claim_probe) used to inherit the launch cwd — a verifier that couldn't find a cited artifact would re-create it there, leaking files AND fabricating ground truth. See BACKLOG #1.

**Record-mode (forward LLM capture).** `FailoverAdapter.complete()` is the single capture seam: on every successful call it records `{prompt, response, tool_events, tokens}` to `<run-dir>/build/calls/call-NNNNN.json` via `runs.record_llm_call`. This is the keystone for visibility ladder rungs 5–6 (ROADMAP) — the replay tier.
- **Default ON.** Off via `MARO_RECORD=0`/`false`/`off` or config `record.enabled: false` (`runs.recording_enabled`; env wins over config). No-op when off or when there's no current run-dir; never raises (swallows all errors — capture must not affect the request outcome).
- Seq counter is per-run-dir and process-global (`runs._CALL_COUNTERS`, lock-guarded). Tests clear it.
- Secret-scrubbed through `src/secret_scrub.py` — the **single source** for what counts as a secret, shared with `scripts/harvest_corpus.py` so the runtime recorder and the committed-fixture path can never diverge.
- *Import direction:* `runs` does not import `llm`; `llm` lazily imports `runs.record_llm_call` inside `complete()`. Keep it that way.

**Substrate notify hook (notify.py).** How an external substrate (OpenClaw, Hermes, shell) learns a run finished or a human is needed — `docs/SUBSTRATE_INTEGRATION.md` is the contract. `notify.emit(event_type, payload)`: always appends to `memory/events.jsonl` (via observe.write_event) so polling works; additionally runs config `notify.command` with the payload JSON on stdin + `MARO_EVENT_TYPE`/`MARO_HANDLE_ID`/`MARO_STATUS`/`MARO_RUN_DIR` env when the event is in `notify.events` (default `[run_completed, escalation]`). Off by default; bounded by `notify.timeout_seconds` (30); never raises. Emit sites: handle.py finalize (`run_completed`, payload = the run_card — curation feeds notification), navigator dispatch-escalate (`escalation`, point=dispatch — run prevented, no run-dir, this is the only signal out), director surface adjudication (`escalation`, point=director_escalation). `notify_telegram.py` (`maro-notify-telegram`) is the shipped Telegram target: formats card/escalation → plain-text send via telegram_listener resolvers (env → **maro config `telegram.chat_id`/`chat_ids`** → legacy openclaw.json). Do NOT add a server/daemon variant — pull-based drain + in-lifecycle hooks are the invariant.

**Post-goal curation (run_curation.py).** Hooked in handle.py's finalize `finally` (after `_finalize_run`, so it reads the just-written status). `curate_run(handle_id, status)` writes `<run-dir>/run_card.json`: outcome class (`success`/`done-not-achieved`/`done-unverified`/`partial`/`failed` — done≠achieved aware via `goal_achieved` metadata) + mineable inventory (calls, scripts, artifacts, steps). It's a **miner registry** (`CURATORS` — ordered pure `(rd, meta, card)->None` fns); v0 ships classify + inventory, future miners (skill/script scrapers, decision-prior indexer, re-attempt hinter, partial rescue) append without touching the hook. User-visible/prunable: `python3 -m run_curation list|show|curate|prune`. Best-effort, never affects request outcome. *Intent:* don't discard paid-for runs — park them for later mining and keep them visible/prunable to the user.

## Config System (config.py)

Two-tier YAML mirroring git's ~/.gitconfig:

| File | Scope | Examples |
|------|-------|---------|
| `~/.poe/config.yml` | User-level | API keys, model prefs, yolo mode |
| `~/.poe/workspace/config.yml` | Workspace-level | Evolver, inspector thresholds, constraint settings |

Workspace inherits from user; workspace keys override. Nested dicts merge one level deep.

Access: `from config import get; get("inspector.breach_threshold", 0.30)`
Priority: env var > config.yml > hardcoded default.

## Heartbeat (heartbeat.py, ~1100 lines)

Periodic health check + tiered self-healing (runs every 60s in loop mode):

- **Tier 1** (Scripted): Disk cleanup, config validation, API key checks
- **Tier 2** (LLM-Assisted): Cheap model diagnoses stuck projects
  - Diagnosis cooldown: 30 min per project (prevents runaway token burn)
  - Session guard: detects `claude --continue` → skips ALL autonomous LLM work
- **Tier 3** (Escalation): Telegram notification

**Lifecycle management:** Use `scripts/heartbeat-ctl.sh start|stop|status|restart`. Auto-stops after 4 hours. Never start as bare `nohup python3 heartbeat.py &`.

**Autonomy switch:** `heartbeat_loop(..., autonomy=False)` is health-only by default. Scheduler drain, task-store drain, mission drain, backlog drain, evolver, inspector, and eval work only run when autonomy is explicitly enabled via CLI/config.

**Backlog drain:** When autonomy is enabled, heartbeat picks up NEXT.md TODO items when idle. Interval: every 30 ticks (~30 min). Skips failed/paused projects (lifecycle markers `.poe-failed`/`.poe-paused`).

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

## Test Isolation (session 17)

`tests/conftest.py` provides an autouse fixture that isolates all tests from the real workspace and credentials:
- `POE_WORKSPACE` → tmp directory
- API keys stripped from environment
- Credential file paths redirected to non-existent paths
- 62 previously un-isolated test files now safe. No test can accidentally read/write `~/.poe/workspace/` or use live API keys.

## Workspace Routing (known issue)

`~/.poe/workspace/` is the stable runtime workspace for memory and captain's log. But `output_root()` and `projects_root()` still resolve to the repo directory. This split needs consolidation.

## File Map

| File | Lines | Role |
|------|-------|------|
| src/llm.py | ~1700 | Adapter hierarchy, model abstraction, thinking budget, advisor |
| src/config.py | ~250 | Two-tier YAML config |
| src/heartbeat.py | ~1100 | Health checks, session guard, backlog drain |
| src/orch_items.py | ~655 | Project/item management, NEXT.md |
| src/task_store.py | ~425 | File-per-task queue, DAG deps |
| src/metrics.py | ~615 | Cost tracking, step classification |
| src/observe.py | ~1690 | Observe dashboard (runtime visibility) |
| src/runs.py | ~700 | Run-dir lifecycle, metadata, record-mode capture (record_llm_call) |
| src/run_curation.py | ~290 | Post-goal curation: classify + inventory + result excerpt → run_card.json; run_result normalizer; status/result/list/prune CLI |
| src/notify.py | ~120 | Substrate notify hook: events.jsonl + config notify.command |
| src/notify_telegram.py | ~120 | Telegram notify target (maro-notify-telegram) |
| deploy/openclaw/ | | OpenClaw adapter: maro-dispatch.sh + setup README |
| src/secret_scrub.py | ~40 | Single-source secret scrubber (recorder + harvester share it) |
| scripts/heartbeat-ctl.sh | | Lifecycle management (start/stop/status) |
