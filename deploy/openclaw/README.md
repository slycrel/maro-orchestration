# OpenClaw → Maro adapter

Wires OpenClaw (or any Telegram-fronted substrate) to Maro using the substrate
contract in `docs/SUBSTRATE_INTEGRATION.md`. Two halves:

**Dispatch (OpenClaw → Maro):** `maro-dispatch.sh` — enqueue a goal and run it
now (`--queue` to enqueue only). Install by symlinking into OpenClaw's script
dir so agents/scripts there can call it:

```bash
ln -s ~/claude/maro-orchestration/deploy/openclaw/maro-dispatch.sh \
      ~/.openclaw/workspace/scripts/maro-dispatch.sh
```

Then tell the OpenClaw agent (AGENTS.md or a skill) something like:
*"For heavy multi-step goals, delegate to Maro: run
`scripts/maro-dispatch.sh '<goal>'`. Maro reports back on Telegram when done."*

**Notify (Maro → Telegram):** `maro-notify-telegram` (a Maro CLI, not a script
here) formats run_completed / escalation events and sends them to the allowed
Telegram chats. Token + chat IDs resolve from `TELEGRAM_BOT_TOKEN` /
`TELEGRAM_CHAT_ID` env or the legacy `~/.openclaw/openclaw.json` channel
config — the same bot OpenClaw already runs, so replies land in the same
conversation. Enable it in Maro's config:

```yaml
# ~/.maro/config.yml
notify:
  command: "maro-notify-telegram"
  # not pip-installed? use:
  # command: "cd ~/claude/maro-orchestration && PYTHONPATH=src python3 -m notify_telegram"
```

Smoke it without sending:

```bash
maro-runs show <handle_id> | maro-notify-telegram --dry-run
```

Turning it all off: remove `notify.command` from config (stops outbound
messages) and delete the symlink (stops dispatch). No daemons are involved on
either side — dispatch runs inside OpenClaw's lifecycle, notify runs inside
Maro's.

## Teaching the agent to delegate

The dispatch script does nothing until the OpenClaw agent knows when to use
it. Installed 2026-07-01 into `~/.openclaw/workspace/AGENTS.md` (Delegation
Architecture section) — the canonical instruction, reproduced here for fresh
installs:

- Hand Maro **multi-step build/research goals** that benefit from a
  plan → execute → verify loop and a durable run record; keep seconds-scale
  asks in the agent's own quick lane.
- `scripts/maro-dispatch.sh "goal"` blocks until the run finishes;
  `--queue` only enqueues (nothing drains it later — no background drain).
- Completion/escalation reaches the human via the notify hook automatically;
  don't duplicate the alert. Full result: `maro-runs result <handle-id>`.
- Budget-capped (`budget.per_run_usd` / `budget.daily_usd`); a refused
  dispatch says "daily budget exhausted" — note it, don't retry until the
  next UTC day, and never wrap dispatch in a cron loop.
