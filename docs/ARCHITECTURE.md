# Architecture

How the openclaw-orchestration system works, from a Telegram message to a completed goal.

---

## Request lifecycle

```
1. User sends Telegram message (or /command)
2. telegram_listener.py receives it via long-poll
3. Immediate ack sent if message is non-trivial ("⏳ Working on it...")
4. handle.py classifies intent → NOW or AGENDA
5a. NOW: single LLM call → response → ack edited with result
5b. AGENDA: agent_loop.py decomposes goal → executes steps → done|stuck
6. Director/Worker optionally invoked for complex directives (/director)
7. Response sent (ack edited or new message)
8. reflect_and_record() persists outcome + lessons
```

---

## Module dependency graph

```
telegram_listener
    ├── handle (intent → routing)
    │   ├── intent (NOW/AGENDA classifier)
    │   └── agent_loop (AGENDA execution)
    │       ├── llm (adapter layer)
    │       ├── memory (lessons injection)
    │       ├── ancestry (ancestry prompt)
    │       └── director (complex directives)
    │           └── workers (research/build/ops/general)
    ├── sheriff (check_system_health, check_all_projects)
    └── ancestry (goal chain for /ancestry command)

heartbeat (runs independently, 60s loop)
    ├── sheriff (system health + project checks)
    ├── llm (tier-2 LLM diagnosis)
    ├── telegram_listener (tier-3 Telegram escalation)
    └── evolver (every 10 ticks)
        └── memory (load_outcomes)

cli (entry point for all commands)
    └── all modules above
```

---

## LLM adapter layer (`llm.py`)

All LLM calls go through `LLMAdapter.complete()`:

```python
@dataclass
class LLMMessage:
    role: str    # "system" | "user" | "assistant"
    content: str

@dataclass
class LLMResponse:
    content: str
    tool_calls: List[ToolCall]
    input_tokens: int
    output_tokens: int

class LLMAdapter:
    def complete(self, messages, *, tools=None, tool_choice="auto",
                 max_tokens=4096, temperature=0.3) -> LLMResponse: ...
```

### Backend selection (`build_adapter("auto")`)

Priority order:
1. `ANTHROPIC_API_KEY` → `AnthropicSDKAdapter` (native tool API)
2. `claude` binary → `ClaudeSubprocessAdapter` (`claude -p --output-format json`, no separate API key)
3. `OPENROUTER_API_KEY` → `OpenRouterAdapter`
4. `OPENAI_API_KEY` → `OpenAIAdapter`

### Tool calls via subprocess

`claude -p` doesn't expose a tool API. `ClaudeSubprocessAdapter` simulates it by embedding tool schemas as JSON instructions in the system prompt, then parsing `{"tool": "name", ...args}` from the response.

### Model tiers

```python
MODEL_CHEAP = "cheap"   # haiku / gpt-4o-mini / etc.
MODEL_MID   = "mid"     # sonnet / gpt-4o / etc.
MODEL_POWER = "power"   # opus / gpt-4.5 / etc.
```

Resolved per backend via `_MODEL_MAP`.

---

## Autonomous loop (`agent_loop.py`)

```
run_agent_loop(goal)
    → load ancestry context (ancestry.py)
    → load lessons context (memory.py)
    → _decompose(goal) → List[str] steps
        [LLM: system=_DECOMPOSE_SYSTEM + ancestry + lessons, user=goal]
        [fallback: heuristic word-split]
    → for each step:
        _execute_step(goal, step)
            [LLM: system=_EXECUTE_SYSTEM, user=goal+ancestry+step+context]
            [tools: complete_step | flag_stuck]
        → stuck detection: same action 3x → break loop
    → reflect_and_record(outcome)
    → LoopResult
```

Steps are appended to the project's `NEXT.md` and tracked via `RunRecord`.

---

## Director/Worker hierarchy (`director.py`, `workers.py`)

```
run_director(directive)
    → _produce_spec(): LLM produces JSON plan with tickets
    → requires_explicit_acceptance(): check for public/irreversible actions
    → for each ticket:
        dispatch_worker(worker_type, ticket)
            → load persona from personas/<type>.md (or inline)
            → LLM executes: tools=[deliver_result, flag_blocked]
    → up to MAX_REVIEW_ROUNDS=2:
        Director reviews worker outputs → accept | request revision
    → compile report
    → DirectorResult
```

Worker types: `research`, `build`, `ops`, `general`
Each has a distinct system prompt persona loaded from `personas/` directory.

---

## Goal ancestry (`ancestry.py`)

Every project can carry its full goal chain:

```
ancestry.json (per project):
{
  "parent_id": "parent-project-slug",
  "ancestry": [
    {"id": "top-mission", "title": "Build self-leveling AI assistant"},
    {"id": "parent-project", "title": "Add autonomous research capability"}
  ]
}
```

When `_decompose()` and `_execute_step()` run, they prepend:

```
Goal Ancestry (stay aligned with this chain):
  1. Build self-leveling AI assistant
  2. Add autonomous research capability
  → Current Task: <the current step>
```

This prevents agents from drifting away from the top-level mission even in deeply nested sub-goals.

---

## Memory and learning (`memory.py`)

### Flat memory (Phase 5, legacy)
```python
reflect_and_record(loop_result)
    → record_outcome()  → memory/outcomes.jsonl
    → extract_lessons_via_llm()  → 1-3 lessons
    → store_lesson()  → memory/lessons.jsonl
    → _append_daily_log()  → memory/YYYY-MM-DD.md
```

### Tiered memory (Phase 16)

Three tiers with decay, promotion, and canon path. See `docs/MEMORY_ARCHITECTURE.md`.

```
short   — in-process only (short_set/get/clear/all). Evicts at session end.
medium  — memory/medium/lessons.jsonl. Grok decay: score *= 0.85/day, +0.3 on reinforce.
long    — memory/long/lessons.jsonl. Promoted from medium at score ≥ 0.9 + 3 sessions.
```

Graduation path:
```
medium lesson → long (score+sessions gate) → AGENTS.md identity (human-gated via canon-candidates)
```

Canon tracking:
```python
inject_tiered_lessons(task_type, track_applied=True)
    → increments times_applied on each injected lesson
    → _record_canon_hit() → memory/canon_stats.jsonl (lesson_id, tier, task_type, date)

get_canon_candidates(min_hits=10, min_task_types=3)
    → filters long-tier lessons that cross both thresholds
    → surfaces for human review via poe-memory canon-candidates
    → NEVER auto-writes to AGENTS.md
```

Skill tiers (Phase 16):
```
Skill.tier: "provisional" (default) → "established" (promote_skill_tier, requires pass^3 ≥ 0.7)
```

### Bootstrap
```python
bootstrap_context()
    → loads outcomes + lessons + today's log
    → formats as session context for `memory context` command
```

---

## Loop Sheriff (`sheriff.py`)

`check_project(slug)` examines:
1. **Repetition**: same TODO selected 3+ times with no state change
2. **Artifact freshness**: have output files changed recently?
3. **Decision log freshness**: are new decisions being added?

Returns `SheriffReport(status="healthy"|"warning"|"stuck", diagnosis=..., evidence=[...])`

`check_system_health()` checks:
- `workspace_writable`: can write to orch root
- `pkg_anthropic`: `import anthropic` works
- `pkg_requests`: `import requests` works
- `disk_space`: >15% free
- `api_key`: at least one API key or claude binary present
- `openclaw_gateway`: ws://127.0.0.1:18789 reachable

---

## Heartbeat (`heartbeat.py`)

Runs every 60 seconds (via systemd):

```
run_heartbeat()
    → check_system_health() → SystemHealth
    → check_all_projects() → List[SheriffReport]
    → Tier 1: _tier1_scripted(checks) → scripted recovery actions
    → Tier 2: _tier2_llm_diagnosis(stuck_projects) → LLM suggestions
    → Tier 3: _tier3_escalate(report) → Telegram alert if critical
    → write_heartbeat_state() → memory/heartbeat-state.json
    → _log_heartbeat() → memory/heartbeat-log.jsonl

Every 10 ticks:
    → run_evolver() → analyze 50 outcomes → suggest improvements
```

---

## Meta-Evolver (`evolver.py`)

```
run_evolver()
    → load_outcomes(limit=50)
    → _build_outcomes_summary(outcomes)
    → LLM (MODEL_MID): identify failure_patterns + suggestions
    → Suggestion objects: {category, target, suggestion, failure_pattern, confidence}
    → _save_suggestions() → memory/suggestions.jsonl
    → optional: Telegram summary
```

Suggestions are reviewed via `poe-evolver --list` and approved via `poe-evolver --apply <id>`.

Categories: `prompt_tweak`, `new_guardrail`, `skill_pattern`, `observation`

---

## Quality Metrics (`metrics.py`)

```
get_metrics()
    → load_outcomes()
    → compute_metrics(outcomes)
        → GoalMetrics per task_type: success_rate, avg_elapsed_ms, cost_usd
        → SystemMetrics: overall_success_rate, most_expensive_goals, slowest_goals
    → identify_expensive_patterns(outcomes) → suggestions
```

Cost estimation: `$0.25/1M input tokens + $1.25/1M output tokens` (approximate mid-tier pricing).

---

## Evaluation suite (`eval.py`)

```
run_eval()
    → for each BUILTIN_BENCHMARK:
        run_benchmark(benchmark)
            → handle(goal)    ← uses real LLM (or dry_run stub)
            → score_result(response, expected_keywords)
            → BenchmarkResult(status=pass|fail|error, score=0.0-1.0)
    → EvalReport(overall_score, results)
    → save to memory/eval-results.jsonl
```

Built-in benchmarks test NOW lane (simple factual) and AGENDA lane (multi-step research).

---

## Interrupt handling (`interrupt.py`)

Added Phase 9. Source-agnostic — any interface writes, the agent loop consumes.

```
InterruptQueue (file-backed, thread-safe)
    post(message, source)
        → _classify_intent()
            → LLM (MODEL_CHEAP) or heuristic fallback
            → intent: additive | corrective | priority | stop
            → new_steps: List[str]
        → append to memory/interrupts.jsonl

run_agent_loop() [per-step]:
    → interrupt_queue.poll()
    → for each interrupt:
        stop      → break, status="interrupted"
        priority  → prepend new_steps to pending_steps
        additive  → append new_steps to pending_steps
        corrective → replace remaining steps (or goal)

Loop lock:
    set_loop_running(loop_id) → memory/loop.lock (PID-verified)
    clear_loop_running()      → removes lock on completion/error

telegram_listener:
    is_loop_running() → True: post to interrupt queue
                     → False: handle() normally
    /stop slash command → posts stop interrupt
```

---

## Storage layout

```
workspace/prototypes/poe-orchestration/
├── projects/
│   └── <slug>/
│       ├── NEXT.md          # task checklist (todo/doing/done/blocked)
│       ├── DECISIONS.md     # decision log
│       ├── config.json      # project config (slug, mission, priority)
│       ├── ancestry.json    # goal ancestry chain (optional)
│       └── output/
│           └── runs/        # RunRecord artifacts
└── memory/
    ├── outcomes.jsonl       # per-run outcomes
    ├── lessons.jsonl        # extracted lessons
    ├── suggestions.jsonl    # evolver suggestions
    ├── interrupts.jsonl     # interrupt queue (polled by agent loop)
    ├── loop.lock            # PID-verified lock while a loop is active
    ├── heartbeat-state.json # last heartbeat result
    ├── heartbeat-log.jsonl  # heartbeat history
    ├── eval-results.jsonl   # benchmark results
    └── YYYY-MM-DD.md        # daily narrative log
```

---

## Telegram UX contract

| Message type | Response timing |
|-------------|----------------|
| Short (<= 20 chars) or `/status` | Typing indicator → send response |
| Long natural language | "⏳ Working on it..." → edit with result |
| `/director`, `/research`, `/build`, `/ops` | "⏳ Working on it..." → edit with result |
| Any message while loop is active | Routed to interrupt queue; ack sent |
| `/stop` | Posts stop interrupt; loop halts at next step boundary |

Heartbeat alerts are sent only when health is `critical` or `degraded`, or stuck projects are detected. Non-actionable status is logged silently.

---

## Mission Layer (`mission.py`, `background.py`, `skills.py`)

Phase 10. Multi-day goal hierarchy with fresh context per unit of work.

```
run_mission(goal)
    → decompose_mission(goal) [MODEL_POWER]
        → Mission: id, goal, milestones[]
        → Milestone: id, title, features[], validation_criteria[]
        → Feature: id, title (each gets its own run_agent_loop call)
    → for each milestone (sequential):
        → for each feature (parallel, max_workers=2):
            run_agent_loop(feature.title, project=project)   ← fresh context
        → _validate_milestone(milestone) [MODEL_MID]
            → PASS: advance to next milestone
            → FAIL: mission.status="stuck", break
    → persist: projects/<slug>/mission.json
    → log: memory/mission-log.jsonl
```

Background execution (`background.py`):
```
start_background(command) → BackgroundTask (non-blocking, returns immediately)
poll_background(task_id)  → checks PID liveness, reads exit code
wait_background(task_id)  → polls every 2s until done or timeout
```

Skill library (`skills.py`):
```
extract_skills(outcomes) → analyzes successes → Skill objects → memory/skills.jsonl
find_matching_skills(goal) → keyword match against trigger_patterns
format_skills_for_prompt(skills) → injected into _decompose() system prompt
```

---

## Sandbox Hardening (`sandbox.py`) — Phase 18

Production-grade skill isolation. Every sandboxed execution is audited.

```python
@dataclass
class SandboxConfig:
    timeout_seconds: int = 30
    max_cpu_seconds: int = 20      # RLIMIT_CPU (child process)
    max_file_size_mb: int = 10     # RLIMIT_FSIZE
    max_open_files: int = 64       # RLIMIT_NOFILE
    block_network: bool = True     # soft socket monkey-patch (no root required)
    use_venv: bool = False         # uv venv → python3 venv fallback; disabled by default (500ms overhead)
    audit: bool = True             # appends to memory/sandbox-audit.jsonl
```

Static analysis (`is_skill_safe(skill)`):
```
Dangerous patterns blocked: import os, subprocess, eval/exec, open(, shutil,
socket.connect, requests.get/post, httpx, aiohttp, pickle.loads, marshal.loads,
import ctypes, ctypes., cffi., urllib.request
```

Network isolation (soft):
```
_NETWORK_BLOCKER_CODE injected into runner preamble when block_network=True
→ monkey-patches socket.socket.connect to raise ConnectionRefusedError
→ no root required; meaningful protection without network namespaces
```

Audit log (`memory/sandbox-audit.jsonl`):
```
fields: audit_id (12-char UUID), timestamp, skill_id, skill_name, static_safe,
        exit_code, elapsed_ms, timed_out, success, network_blocked,
        venv_isolated, resource_limited, output_preview, error
read: load_audit_log(limit=50) — newest-first
CLI: poe-sandbox audit [--limit N] [--format json]
     poe-sandbox config
     poe-sandbox test <skill_id> [--no-network-block] [--venv]
```

`SandboxResult` Phase 18 fields: `audit_id`, `network_blocked`, `venv_isolated`, `resource_limited`.

Note: `RLIMIT_AS` (virtual memory) intentionally omitted — breaks Python mmap on Linux with overcommit.

---

## Config and Bootstrap (`config.py`, `bootstrap.py`) — Phase 21

**No OpenClaw dependency.** All path resolution centralized in `src/config.py`.

Workspace root resolution (priority order):
```
1. POE_WORKSPACE env var          (canonical)
2. OPENCLAW_WORKSPACE env var     (backward compat)
3. WORKSPACE_ROOT env var         (backward compat)
4. ~/.poe/workspace               (default — no OpenClaw required)
```

Credential discovery:
```
1. POE_ENV_FILE env var           → explicit override
2. <workspace>/secrets/.env       → workspace-local
3. ~/.openclaw/workspace/secrets/ → legacy fallback (if exists)
```

`config.py` public API:
```python
workspace_root() → Path
memory_dir()     → Path  (creates if needed)
secrets_dir()    → Path
credentials_env_file() → Path
load_credentials_env() → dict[str, str]
openclaw_cfg_path()    → Path  (OPENCLAW_CFG env var override)
load_openclaw_cfg()    → dict
deploy_dir()           → Path  (deploy/ sibling of src/)
```

Bootstrap (`poe-bootstrap`):
```
install    → create dirs + write service files + smoke test
dirs       → create workspace/memory/skills/projects/output/secrets/logs/
services   → write systemd (Linux) or launchd (macOS) service files
status     → show workspace path, dir existence, service states
smoke      → dry-run NOW-lane task via handle.py
```

Service file generation:
```
Linux (platform.system() == "Linux"):
    deploy/systemd/poe-heartbeat.service
    deploy/systemd/poe-telegram.service
    deploy/systemd/poe-inspector.service

macOS (Darwin):
    deploy/launchd/com.poe.heartbeat.plist
    deploy/launchd/com.poe.telegram.plist
    deploy/launchd/com.poe.inspector.plist
```

---

## Persona System (`persona.py`, `personas/`) — Phase 20

Personas are **composable data primitives**: YAML frontmatter + markdown body. Compose > inherit — no subclassing, pure data composition.

```
PersonaSpec fields:
    name                 slug (must match filename stem)
    role                 human-readable role
    model_tier           "power" | "mid" | "cheap"
    tool_access          list of allowed tool names (empty = all)
    memory_scope         "session" | "project" | "global"
    communication_style  one-liner baked into system prompt header
    hooks                hook names to register when active
    composes             other persona names (informational; use compose_persona() to merge)
    system_prompt        markdown body (everything after frontmatter)
```

Composition (`compose_persona(*names)`):
```
system_prompt  → concatenated with "---" separator
tool_access    → union, deduped (left-to-right)
hooks          → union, deduped
model_tier     → highest wins (power > mid > cheap)
memory_scope   → broadest wins (global > project > session)
name           → joined ("researcher+critic")
```

Spawn flow:
```
spawn_persona(name, goal, dry_run=False, compose_with=[...])
    → PersonaRegistry.load(name)  (+ compose if compose_with given)
    → short_clear()               (memory isolation: evict previous session)
    → short_set(persona_name, persona_goal)
    → build_persona_system_prompt(spec, goal=goal)
    → run_agent_loop(goal, system_prompt_extra=...)
    → short_clear()               (evict on exit)
    → SpawnResult(status, summary, steps_taken, model_tier, memory_scope)
```

Built-in personas:
```
researcher    power   session   analytical, source-grounded, multi-angle
builder       mid     project   direct, implementation-focused, ship-oriented
critic        mid     session   skeptical, evidence-demanding, direct about weaknesses
ops           mid     project   reliability-first, precise, incident-aware
summarizer    cheap   session   concise, executive-level, signal over noise
strategist    power   global    goal-aligned, long-horizon, trade-off explicit
```

Telegram integration:
```
/research <goal>  → spawn_persona("researcher", goal)
```

---

## Hook System (`hooks.py`)

Phase 11. Pluggable callbacks at every level of the execution hierarchy.

```
HookRegistry (backed by .hooks/hooks.json)
    register(hook) / unregister(id) / enable(id) / disable(id)

Hook types:
    reviewer     → LLM critique; BLOCK/FAIL in output sets should_block=True
    reporter     → emit summary to log or Telegram (never blocks)
    coordinator  → LLM routing decision, injected as context
    script       → shell command, output captured (non-blocking)
    notification → System Notification (Factory pattern): injects guidance
                   into next LLM call at the right moment, not front-loaded

run_hooks(scope, context, fire_on) → List[HookResult]
    → fired at: SCOPE_MISSION, SCOPE_MILESTONE, SCOPE_FEATURE, SCOPE_STEP
    → never raises; errors returned as status="skipped"
    → any_blocking(results) → gates advancement in mission/agent_loop

Built-in hooks (disabled by default, opt-in via poe-hooks enable <id>):
    builtin-step-reviewer        — cheap model reviews each step result
    builtin-milestone-validator  — mid model validates milestone criteria
    builtin-progress-reporter    — logs milestone completion
    builtin-plan-alignment       — notification: reminds worker of mission goal
```

---

## Inspector (`inspector.py`)

Phase 12. Independent quality oversight — separate from heartbeat (health).

```
Heartbeat = is the system running?
Inspector = is the system producing the right outcomes?

run_full_inspector()
    → load_outcomes(limit=50)
    → detect_friction(outcomes) → List[FrictionSignal]
        signals: error_events | repeated_rephrasing | escalation_tone |
                 platform_confusion | abandoned_tool_flow | backtracking | context_churn
    → check_alignment(session) per recent outcome [heuristic or LLM]
        → AlignmentResult(aligned, score, gaps)
    → cluster_patterns(signals) → List[str] named patterns
    → generate_tickets(patterns) → structured improvement tickets
    → forward auto_evolver tickets → evolver.receive_inspector_tickets()
    → persist: memory/inspector-log.jsonl, memory/friction-signals.jsonl
    → return InspectorReport(executive_summary, ...)

heartbeat_loop(): every 20 ticks → run_full_inspector()
```

---

## Poe CEO Layer (`poe.py`, `autonomy.py`, `goal_map.py`)

Phase 13. Role separation enforced at the code level.

```
poe_handle(message)
    → routes to Mission / Director / Inspector
    → does NOT execute steps directly
    → compiles executive summary for Jeremy

Jeremy (Telegram — mission/goal level only)
  └── poe_handle() [CEO/Communicator — MODEL_POWER]
        ├── run_mission()     [Planner + Workers]
        ├── run_director()    [Planner/Reviewer]
        └── run_full_inspector() [Oversight]

goal_map.py:
    GoalMap — directed graph of active missions and their relationships
    detect_conflicts(map) → overlapping goals, resource contention
    /map Telegram command → visual summary of active mission graph

autonomy.py:
    AutonomyTier: MANUAL | SAFE | FULL
    get_autonomy(project, action) → tier
    set_autonomy(project, tier)   → persists to memory/autonomy.json
    MANUAL: human approves each action
    SAFE:   auto-execute low-risk, escalate high-risk
    FULL:   autonomous within defined scope

assign_model_by_role(role):
    orchestrator → MODEL_POWER
    worker       → MODEL_MID
    classifier   → MODEL_CHEAP
```

---

## Complete module dependency graph

```
telegram_listener
    ├── poe (CEO layer — Phase 13 entry point)
    │   ├── mission → agent_loop (fresh context per feature)
    │   ├── director → workers
    │   └── inspector
    ├── handle (legacy NOW/AGENDA routing — backward compat)
    │   ├── intent
    │   └── agent_loop
    │       ├── llm
    │       ├── memory
    │       ├── ancestry
    │       ├── skills      (Phase 10 — injected into decompose)
    │       ├── attribution (Phase 14 — failure attribution)
    │       ├── hooks       (Phase 11 — step-level)
    │       └── interrupt   (Phase 9 — polled between steps)
    ├── sheriff
    └── ancestry

heartbeat (60s loop)
    ├── sheriff
    ├── llm
    ├── telegram_listener
    ├── evolver     (every 10 ticks)
    │   └── skills  (test gate on skill_pattern mutations — Phase 14)
    └── inspector   (every 20 ticks — Phase 12)
        └── attribution  (per-session failure attribution — Phase 14)

cli (all commands)
    └── all modules above
```

---

## Storage layout (complete)

```
<workspace>/                         # POE_WORKSPACE or ~/.poe/workspace (Phase 21)
├── .hooks/
│   └── hooks.json               # HookRegistry — registered hooks
├── projects/
│   └── <slug>/
│       ├── NEXT.md              # task checklist (todo/doing/done/blocked)
│       ├── DECISIONS.md         # decision log
│       ├── config.json          # project config (slug, mission, priority)
│       ├── ancestry.json        # goal ancestry chain (optional)
│       ├── mission.json         # active Mission object (Phase 10)
│       └── output/
│           └── runs/            # RunRecord artifacts
├── secrets/
│   └── .env                     # credentials (POE_ENV_FILE override, Phase 21)
└── memory/
    ├── outcomes.jsonl           # per-run outcomes
    ├── lessons.jsonl            # extracted lessons (flat, legacy)
    ├── suggestions.jsonl        # evolver suggestions
    ├── skills.jsonl             # skill library (Phase 10)
    ├── background-tasks.jsonl   # background subprocess tracking (Phase 10)
    ├── mission-log.jsonl        # mission run history (Phase 10)
    ├── interrupts.jsonl         # interrupt queue (Phase 9)
    ├── loop.lock                # PID-verified lock while loop is active
    ├── inspector-log.jsonl      # inspector run history (Phase 12)
    ├── friction-signals.jsonl   # running friction signal log (Phase 12)
    ├── attributions.jsonl       # per-session failure attributions (Phase 14)
    ├── skill-stats.jsonl        # per-skill success rate tracking (Phase 14)
    ├── skill-tests.jsonl        # auto-generated skill test cases (Phase 14)
    ├── autonomy.json            # autonomy tier config (Phase 13)
    ├── heartbeat-state.json     # last heartbeat result
    ├── heartbeat-log.jsonl      # heartbeat history
    ├── eval-results.jsonl       # benchmark results
    ├── YYYY-MM-DD.md            # daily narrative log
    ├── canon_stats.jsonl        # lesson application hits (Phase 16)
    ├── sandbox-audit.jsonl      # per-execution sandbox audit log (Phase 18)
    ├── medium/
    │   └── lessons.jsonl        # medium-tier lessons with decay scores (Phase 16)
    └── long/
        └── lessons.jsonl        # long-tier validated lessons (Phase 16)
```

**In-process observability note:** There is no real-time step stream today. To observe a live loop:
- `memory/loop.lock` — PID + goal of active loop
- `heartbeat-state.json` — last health check (stale by up to 60s)
- `memory/YYYY-MM-DD.md` — narrative log, flushed at reflect_and_record()
- `memory/sandbox-audit.jsonl` — live during sandboxed skill execution
- Hook with `fire_on=step` + `type=reporter` — most direct path to per-step observability

See `docs/FUTURE_CONSIDERATIONS.md` for the planned observability dashboard.

---

## Telegram UX contract

| Message type | Response timing |
|-------------|----------------|
| Short (<= 20 chars) or `/status` | Typing indicator → send response via poe_handle |
| Long natural language | "⏳ Working on it..." → edit with result |
| `/director`, `/research`, `/build`, `/ops` | "⏳ Working on it..." → edit with result |
| `/map` | Goal relationship graph summary |
| Any message while loop is active | Routed to interrupt queue; ack sent |
| `/stop` | Posts stop interrupt; loop halts at next step boundary |

Heartbeat alerts: `critical` or `degraded` health, or stuck projects.
Inspector alerts: friction patterns crossing threshold (batched, not per-run).
