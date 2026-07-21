# The `user/` lane

Operator-specific context and defaults. The copies in this directory are
**neutral shipped templates** — they contain no real operator data and are
safe to install anywhere. Your real files live in the **workspace overlay**:

```
~/.maro/workspace/user/        # more precisely: <workspace_root>/user/
├── GOALS.md
├── CONTEXT.md
├── SIGNALS.md
└── CONFIG.md
```

## Resolution order

Same overlay convention as `skills/` and `personas/`:

1. `<workspace_root>/user/<file>` — your real file (workspace root resolves
   `MARO_WORKSPACE` → `OPENCLAW_WORKSPACE` → `WORKSPACE_ROOT` →
   `~/.maro/workspace`)
2. `<repo or install dir>/user/<file>` — the shipped neutral template
3. Missing entirely → the feature quietly no-ops

Implemented in `src/config.py:user_file()`. All five files resolve through
it — the former caveat (CONFIG.md / COMPLETION_STANDARD.md read from the
repo copy only) was closed 2026-07-10; put your real files in
`<workspace>/user/` and they win everywhere.

## The files

| File | Read by | Injected where |
|------|---------|----------------|
| `GOALS.md` | `src/planner.py` | First ~500 chars into every goal-decomposition prompt |
| `CONTEXT.md` | `src/planner.py` | First ~500 chars into every goal-decomposition prompt |
| `SIGNALS.md` | `src/planner.py`, `src/evolver_scans.py` | ~500 chars into decompose prompts; ~600 chars into evolver signal scanning (weights proposed sub-missions toward your declared threads) |
| `CONFIG.md` | `src/handle.py`, `src/heartbeat.py` | Not injected into prompts — a flat `key: value` config lane (see below) |
| `COMPLETION_STANDARD.md` | `src/handle.py` | Completion-quality standard appended to run prompts (neutral; usually fine as shipped) |

Because GOALS/CONTEXT/SIGNALS go into LLM prompts on **every** run, whatever
you write there is sent to your model provider. Keep secrets out.

## CONFIG.md keys

Flat `key: value` lines, `#` comments (hand-parsed by
`src/handle.py:_load_user_config` — not YAML). Distinct from the two-tier
YAML config (`~/.maro/config.yml` + `<workspace>/config.yml`, see
`docs/DEFAULTS.md`).

| Key | Default | Effect |
|-----|---------|--------|
| `yolo` | `false` | `true` = skip clarification prompts and just run (also: `MARO_YOLO` env var) |
| `default_model_tier` | unset (code role default: `mid`) | Model tier for all runs: `cheap` (Haiku) / `mid` (Sonnet) / `power` (Opus). Unset since 2026-07-21 — execution floor is MID |
| `max_steps` | `8` | Maximum steps per run |
| `always_skeptic` | `false` | `true` = skeptic framing on every run (else only with `skeptic:` prefix) |
| `ralph_verify` | `false` | `true` = per-step verify loop with retry on every run (~30% wall time; else `ralph:` prefix) |
| `quality_gate` | `true` | Post-loop skeptic quality check |
| `quality_gate_action` | `escalate` | On gate rejection: `escalate` (re-run, next tier) or `warn` (keep result) |
| `notify_on_complete` | `true` | Telegram notification when a mission finishes (no-op if not configured) |
| `mcp_servers` | *(unset)* | Comma-separated MCP servers loaded at heartbeat startup (shell command = stdio, URL = HTTP) |

## Privacy note

These templates replaced real operator files on 2026-07-09 (audit finding
SF-5/docs-02): the previous contents are still present in **git history**.
Fixing the tip does not scrub history.
