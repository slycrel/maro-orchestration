# User Configuration Defaults

Settings here apply to every run unless overridden by CLI flags.
Edit this file to change system-wide behavior without touching code.

---

## Autonomy

# YOLO mode: skip clarification prompts and just run.
# "true" = always proceed without asking. "false" = ask when ambiguous.
yolo: false

---

## Model Defaults

# Default model tier for all runs.
# Options: cheap (Haiku), mid (Sonnet), power (Opus)
# Override per-run with: --model claude-haiku-4-5-20251001
default_model_tier: cheap

# Override for research/analysis steps specifically (two-tier routing).
# "auto" = let classify_step_model decide. "mid" or "cheap" to force.
research_step_model: auto

---

## Run Behavior

# Maximum steps per run (default: 8).
max_steps: 8

# Default lane when intent is ambiguous (future — not yet wired).
# Options: now, agenda
# default_lane: agenda

# Inject skeptic modifier for all runs.
# "true" = always add skeptic framing. "false" = only when "skeptic:" prefix used.
always_skeptic: false

---

## Quality Gate

# Run a skeptic quality check after every loop. If output is below par,
# escalate to a better model and re-run automatically.
# "true" = enable. "false" = skip.
quality_gate: true

# What to do when the gate rejects the output.
# "escalate" = auto re-run with next model tier.
# "warn"     = log a warning but keep the result.
quality_gate_action: escalate

## Notifications

# Send Telegram notification when a mission finishes.
# Requires Telegram bot token + chat ID in openclaw.json.
notify_on_complete: true
