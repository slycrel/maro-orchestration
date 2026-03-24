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

### After each run
```python
reflect_and_record(loop_result)
    → record_outcome()  → memory/outcomes.jsonl
    → extract_lessons_via_llm()  → 1-3 lessons
    → store_lesson()  → memory/lessons.jsonl
    → _append_daily_log()  → memory/YYYY-MM-DD.md
```

### On next run
```python
inject_lessons_for_task(task_type, goal)
    → load_lessons(task_type=task_type, limit=3)
    → format as "Prior lessons — apply these:" block
    → prepended to _DECOMPOSE_SYSTEM
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

## Planned architecture: Phases 10–13

Not yet built. Documented here so design intent is clear before implementation.

### Phase 10: Mission Layer

```
Mission (top-level goal, multi-day)
  └─ Milestone (validation checkpoint — must pass before advancing)
      └─ Feature (unit of work, one Worker Session)
          └─ Worker Session (fresh context window per feature)
```

Key constraints:
- No single session holds the full project in context
- Milestones execute sequentially; features within a milestone can parallelize
- Git is the coordination primitive for session handoffs
- Background execution: agent starts a subprocess, continues other work, polls result
- Skill library: completed goal chains → extracted reusable patterns → surfaced to future orchestration

### Phase 11: Hook System

```
HookRegistry
  ├── mission-level hooks   (fire on mission start/end)
  ├── milestone-level hooks (fire before milestone validation)
  ├── feature-level hooks   (fire before/after each feature)
  └── step-level hooks      (fire between steps in agent_loop)

Hook types:
  reviewer   → LLM critique of work product before advancing
  reporter   → emit summary to Telegram/log (non-blocking)
  coordinator → LLM decides next step routing
  script     → shell command (non-blocking, result injected as context)

System Notifications (Factory pattern):
  Hooks inject contextual guidance at the right moment —
  not front-loaded in system prompt, injected when relevant.
```

### Phase 12: Inspector (Oversight + Quality)

```
Inspector (runs independently, NOT inside the agent loop)
  ├── Friction detection: 7 signals per session
  │   error_events | repeated_rephrasing | escalation_tone |
  │   platform_confusion | abandoned_tool_flow | backtracking | context_churn
  ├── LLM-as-judge batch analysis (periodic, not per-run)
  │   → abstracted signals (no raw content exposed)
  │   → semantic clustering → friction patterns
  ├── Threshold crossing → structured ticket → evolver → suggestion PR
  │   human approves before apply
  ├── Goal alignment check: did completed work match mission intent?
  └── Output: executive summary → Poe (CEO layer), not raw detail
```

Role distinction: Heartbeat = health (is the system running?). Inspector = quality (is the system producing the right outcomes?).

### Phase 13: Role Separation (Poe as CEO)

```
Jeremy (Telegram — mission/goal level only)
  └── Poe [CEO/Communicator]
        - sets direction, surfaces executive summaries
        - advisor on pivots/conflicts, not executor
        - maintains map of active missions vs. north stars
        ├── Director [Planner/Reviewer — POWER model]
        │     - produces SPEC, reviews output, iterates
        │     - does NOT execute steps directly
        │     ├── Worker Sessions [Executors — MID model]
        │     │     - fresh context per feature
        │     │     - research/build/ops/general personas
        │     └── Validator [Quality check — MID or specialized]
        └── Inspector [Independent Oversight]
              - separate from execution chain
              - reports up to Poe, not to Director

Autonomy tiers (per project/action):
  manual  → human approves each action
  safe    → auto-execute low-risk, escalate high-risk
  full    → autonomous within defined scope

Model assignment:
  orchestrator  → MODEL_POWER (Opus)
  worker/plan   → MODEL_MID (Sonnet)
  classification/heartbeat → MODEL_CHEAP (Haiku)
```

Delegator-as-non-coder principle: if Poe or Director is executing steps directly, the architecture has failed. The role contract must be enforced at the code level, not just by convention.
