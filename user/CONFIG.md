# User Configuration Defaults
#
# WHAT THIS FILE IS
# A flat `key: value` config lane, separate from the YAML config
# (~/.maro/config.yml + <workspace>/config.yml). Settings here apply to
# every run unless overridden by CLI flags. Parsed by a hand parser
# (src/handle.py:_load_user_config): one `key: value` per line, `#`
# comments, inline comments stripped. Readers today:
#   - src/handle.py    — yolo, default_model_tier, research_step_model
#   - src/heartbeat.py — mcp_servers
# NOTE: unlike GOALS/CONTEXT/SIGNALS (workspace overlay wins), CONFIG.md is
# currently read from the repo/install copy only. Overlay migration for this
# file is queued — see user/README.md for the lane's full documentation.
#
# Edit this file to change system-wide behavior without touching code.
# All values below are the shipped defaults.

# ---------------------------------------------------------------------------
# Autonomy
# ---------------------------------------------------------------------------

# YOLO mode: skip clarification prompts and just run.
# "true" = always proceed without asking. "false" = ask when ambiguous.
# (Also settable per-invocation with the MARO_YOLO env var.)
yolo: false

# ---------------------------------------------------------------------------
# Model Defaults
# ---------------------------------------------------------------------------

# Default model tier for all runs.
# Options: cheap (Haiku), mid (Sonnet), power (Opus)
# Override per-run with: --model claude-haiku-4-5-20251001
default_model_tier: cheap

# Override for research/analysis steps specifically (two-tier routing).
# "auto" = let classify_step_model decide. "mid" or "cheap" to force.
research_step_model: auto

# ---------------------------------------------------------------------------
# Run Behavior
# ---------------------------------------------------------------------------

# Maximum steps per run (default: 8).
max_steps: 8

# Default lane when intent is ambiguous (future — not yet wired).
# Options: now, agenda
# default_lane: agenda

# Inject skeptic modifier for all runs.
# "true" = always add skeptic framing. "false" = only when "skeptic:" prefix used.
always_skeptic: false

# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

# Enable Ralph verify loop: per-step quality check with retry when the result
# doesn't address the step goal. Adds ~30% wall time. Best for high-stakes runs.
# Can also trigger per-run with the "ralph:" prefix in the goal.
# "true" = enable for all runs. "false" = only when ralph: prefix is used.
ralph_verify: false

# ---------------------------------------------------------------------------
# Quality Gate
# ---------------------------------------------------------------------------

# Run a skeptic quality check after every loop. If output is below par,
# escalate to a better model and re-run automatically.
# "true" = enable. "false" = skip.
quality_gate: true

# What to do when the gate rejects the output.
# "escalate" = auto re-run with next model tier.
# "warn"     = log a warning but keep the result.
quality_gate_action: escalate

# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

# Send Telegram notification when a mission finishes.
# Requires a Telegram bot token + chat ID (no-op when not configured).
notify_on_complete: true

# ---------------------------------------------------------------------------
# MCP Servers
# ---------------------------------------------------------------------------

# Comma-separated list of MCP servers to load at heartbeat startup.
# Each entry is either:
#   - A shell command (stdio transport):  npx -y @modelcontextprotocol/server-memory
#   - An HTTP URL (HTTP transport):       http://localhost:3001
# Leave empty (or comment out) to disable MCP tool loading.
# Example:
#   mcp_servers: npx -y @modelcontextprotocol/server-memory, http://localhost:3001
# mcp_servers:
