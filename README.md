# openclaw-orchestration

Autonomous agent orchestration for Poe — a self-improving AI concierge running on a headless Linux box, reachable via Telegram.

---

## What it is

A Python orchestration system that gives Poe (the AI assistant) the ability to:

- **Receive goals via Telegram** and route them autonomously
- **Decompose, plan, and execute** multi-step work without human hand-holding
- **Delegate** to specialized workers (research, build, ops) via a Director/Worker hierarchy
- **Learn from failures** — lessons and outcomes persist across sessions
- **Self-heal** — heartbeat monitors health, detects stuck loops, escalates to Telegram
- **Self-improve** — meta-evolver reviews patterns and proposes prompt/guardrail improvements
- **Trace goals to mission** — every task carries its full ancestry chain to prevent drift

---

## Architecture

```
Telegram message
      ↓
telegram_listener.py    ← polls Telegram Bot API, slash commands, ack+edit UX
      ↓
handle.py               ← NOW/AGENDA intent classification + routing
      ↓
  ┌───┴────┐
  │        │
now lane  agenda lane
(1-shot)  agent_loop.py  ← decompose → execute steps → done|stuck
              ↓
          director.py    ← Director plans → Worker executes → Director reviews
          workers.py     ← research | build | ops | general personas
              ↓
          memory.py      ← record outcome, extract lessons, inject in future runs
          ancestry.py    ← goal ancestry chain injected in prompts
              ↓
          evolver.py     ← periodic: analyze patterns → suggest improvements
          heartbeat.py   ← periodic: health check → tiered recovery → Telegram alert
```

### LLM Backends (`llm.py`)

All backends share one interface: `LLMAdapter.complete(messages, *, tools, ...) → LLMResponse`

| Backend | Trigger |
|---------|---------|
| `AnthropicSDKAdapter` | `ANTHROPIC_API_KEY` set |
| `ClaudeSubprocessAdapter` | `claude` binary available (OAuth via Claude Code) |
| `OpenRouterAdapter` | `OPENROUTER_API_KEY` set |
| `OpenAIAdapter` | `OPENAI_API_KEY` set |

`build_adapter("auto")` selects the best available backend. `MODEL_CHEAP/MID/POWER` abstract model names across backends.

---

## Quickstart

```bash
cd openclaw-orchestration

# Give Poe a goal — autonomous loop (Phase 1)
python3 src/cli.py poe-run "research the three main benefits of prediction markets"

# Route a message (auto-classifies NOW vs AGENDA) (Phase 2)
python3 src/cli.py poe-handle "what time is it in Tokyo?"
python3 src/cli.py poe-handle "build me a research summary on polymarket strategies"

# Director/Worker pipeline (Phase 3)
python3 src/cli.py poe-director "write a comprehensive report on LLM orchestration frameworks"

# System health (Phase 4)
python3 src/cli.py sheriff health
python3 src/cli.py poe-heartbeat --dry-run

# Memory + learning (Phase 5)
python3 src/cli.py memory context
python3 src/cli.py memory outcomes
python3 src/cli.py memory lessons

# Start Telegram listener (Phase 6)
python3 src/cli.py poe-telegram --once       # process pending
python3 src/cli.py poe-telegram              # run forever

# Goal ancestry (Phase 6 / §18)
python3 src/cli.py poe-run "research sub-goal" --parent "top-mission-project"
python3 src/cli.py ancestry my-project
python3 src/cli.py impact top-mission

# Meta-evolver (Phase 7 / §19)
python3 src/cli.py poe-evolver --dry-run
python3 src/cli.py poe-evolver --list        # pending suggestions
python3 src/cli.py poe-evolver --apply <id>  # mark applied

# Quality metrics (Phase 8)
python3 src/cli.py poe-metrics
python3 src/cli.py poe-eval --dry-run
```

---

## Telegram interface

Deploy `deploy/poe-telegram.service` to get Poe listening 24/7:

```bash
sudo cp deploy/poe-telegram.service /etc/systemd/system/
sudo systemctl enable --now poe-telegram
```

Slash commands in Telegram:

| Command | What it does |
|---------|-------------|
| `/status` | System health, heartbeat, stuck projects |
| `/director <directive>` | Full Director/Worker pipeline |
| `/research <goal>` | Research worker |
| `/build <goal>` | Build worker |
| `/ops <command>` | Ops worker |
| `/ancestry <project>` | Show goal ancestry chain |
| `/help` | Command list |

Natural language messages are auto-routed (NOW = fast, AGENDA = multi-step loop).

---

## Always-on services

```bash
# Heartbeat + meta-evolver (60s interval, evolver every 10 ticks)
sudo cp deploy/poe-heartbeat.service /etc/systemd/system/
sudo systemctl enable --now poe-heartbeat
```

Heartbeat recovery tiers:
1. **Scripted**: disk warn, API key missing, gateway down → log suggestion
2. **LLM diagnosis**: stuck projects → ask cheap LLM for recovery action
3. **Telegram escalation**: critical health or stuck projects → alert Jeremy

---

## Source modules

| Module | Phase | What it does |
|--------|-------|-------------|
| `orch.py` | 0 | Core file-first state: NEXT.md tasks, run records, project lifecycle |
| `llm.py` | 1/6 | Platform-agnostic LLM adapters (Anthropic, OpenRouter, OpenAI, subprocess) |
| `agent_loop.py` | 1 | Autonomous loop: decompose goal → execute steps → done\|stuck |
| `intent.py` | 2 | NOW/AGENDA classifier (LLM + heuristic fallback) |
| `handle.py` | 2 | Entry point: classify → route → execute → respond |
| `director.py` | 3 | Director agent: plan → delegate → review |
| `workers.py` | 3 | Worker agents: research, build, ops, general |
| `sheriff.py` | 4 | Loop Sheriff: detect stuck loops + system health checks |
| `heartbeat.py` | 4 | Periodic health check + tiered recovery + Telegram escalation |
| `memory.py` | 5 | Outcome recording, lesson extraction, Reflexion injection |
| `telegram_listener.py` | 6 | Telegram polling, slash commands, ack+edit UX |
| `ancestry.py` | 6 | Goal ancestry chain: parent_id, ancestry.json, prompt injection |
| `evolver.py` | 7 | Meta-evolver: analyze outcomes → propose improvements |
| `metrics.py` | 8 | Quality tracking: success rate, cost, token usage per task type |
| `eval.py` | 8 | Evaluation suite: benchmark goals with known-good outcomes |
| `cli.py` | all | Unified CLI entry point |

---

## Goal ancestry

Every project can carry a reverse-linked chain back to the top-level mission:

```bash
# Link a sub-goal to a parent
python3 src/cli.py poe-run "implement the WebSocket handler" \
    --parent top-level-mission \
    --parent-title "Build self-leveling autonomous assistant"

# Or set ancestry manually
python3 src/cli.py ancestry my-project \
    --set-parent top-mission \
    --parent-title "Top level goal"

# Inspect
python3 src/cli.py ancestry my-project
python3 src/cli.py impact top-mission   # all descendants (BFS)
```

Ancestry is injected into every decomposition and execution prompt, keeping agents aligned with the big picture.

---

## Memory and self-improvement

Poe accumulates knowledge across sessions:

```
Run completes
    → reflect_and_record() in memory.py
    → outcome saved to memory/outcomes.jsonl
    → LLM extracts 1-3 lessons to memory/lessons.jsonl
    → daily log in memory/YYYY-MM-DD.md

Every 10 heartbeat ticks (~10 minutes):
    → evolver analyzes last 50 outcomes
    → identifies failure patterns
    → generates suggestions (prompt_tweak | new_guardrail | skill_pattern)
    → saves to memory/suggestions.jsonl

Next run with similar task type:
    → inject_lessons_for_task() loads relevant lessons
    → ancestry context loaded
    → both injected into decompose + execute prompts
```

---

## Development

```bash
# Run tests
python3 -m pytest tests/ -q

# Run with dry-run (no LLM calls)
python3 src/cli.py poe-run "test goal" --dry-run --verbose
python3 src/cli.py poe-heartbeat --dry-run
python3 src/cli.py poe-eval --dry-run
```

Test count: 346+ passing. All LLM calls are mocked in tests.

---

## Configuration

Credentials are read from (in priority order):
1. Environment variables: `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
2. `~/.openclaw/openclaw.json` (existing OpenClaw config)

No separate config file needed if you're already running OpenClaw.
