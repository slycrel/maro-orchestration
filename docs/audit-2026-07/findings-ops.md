# Purgatorio Eye 1 — Ops census: "what actually runs?"

Probed 2026-07-09 ~15:30–15:45 MDT, read-only. Every claim below was probed in
this session (command output / file mtime / journal line cited). Nothing was
fixed, restarted, enabled, or disabled.

## What matters most for 1.0

1. **Nothing schedules the Maro heartbeat — it has never beaten in production
   under the Maro name** (ops-01). Last completed state write: `checked_at
   2026-04-04` (96 days). `memory/heartbeat-log.jsonl` (the beat ledger
   heartbeat.py writes) **does not exist at all**. The systemd units written
   for it (`deploy/systemd/maro-heartbeat.service`, Jun 25) were never
   installed. The platform subsystem's liveness layer is built, tested, and
   not operating — the exact archetype this audit exists for.
2. **The evolver has never run in production — confirmed with three
   independent probes** (ops-02): workspace `skills/` and `personas/` are
   empty dirs (untouched since Apr 11), `change_log.jsonl`'s last entry is
   2026-04-12 with `suggestion_id: "test-03"` (a test), and playbook.md is
   frozen at Apr 11. The fresh `evolver-baselines.jsonl` (659 rows, latest
   today) is *baseline recording by the loop*, not evolver activity — easy to
   mistake for a live evolver. Self-learning is 1.0 scope (item f); its
   engine has zero production hours.
3. **The only liveness verifier (host-check cron) has never fired, and when
   it does it will red-alert daily forever** (ops-03). Installed today at
   14:39 (after its 08:05 slot); its log doesn't exist. Its heartbeat check
   fails at age > 900s vs a 96-day-old state file, so from tomorrow it
   escalates to Telegram every morning until a heartbeat scheduler exists —
   correct behavior, but the steady state is a daily alarm, and the
   cron→notify_telegram path itself is end-to-end unverified.
4. **Six built-but-dead runtime lanes litter the workspace with stale state**
   (ops-04..07): slack-bridge (source code missing entirely, yet live-looking
   tokens sit in `.env`), telegram_listener (offset frozen Apr 4),
   build-loop (lock names dead PID, May 6), btc_monitor (Apr 22),
   maro-observe (service never installed, no listener), plus a stale
   `run/heartbeat.pid` from a dead PID this morning. None are monitored;
   host-check's stale-lock check watches a file (`memory/loop.lock`) that
   doesn't exist while missing `output/build-loop.lock` which does.
5. **Token economics are healthy — no silent burner found** (ops-10):
   $229.38 all-time in step-costs.jsonl, ≤ $2.79/day since Jul 2, $0.43
   today, OpenClaw cron list is empty (the old 5-minute Codex burner is
   really gone). But 2113/3337 (63%) of cost records have an empty `model`
   field, so per-model attribution — the thing that catches the *next*
   burner — is blind for most of history (ops-09).

## Findings

| id | claim | evidence | subsystem | severity | status | disposition-suggestion |
|---|---|---|---|---|---|---|
| ops-01 | Maro heartbeat has no scheduler anywhere (no cron, no installed systemd unit, no running process) and has never completed a production beat: heartbeat-log.jsonl doesn't exist, heartbeat-state.json is 96 days stale | `crontab -l` = 2 entries, neither heartbeat; `systemctl` system+user: no maro units; `deploy/systemd/maro-heartbeat.service` mtime Jun 25 but absent from /etc/systemd/system and ~/.config/systemd/user; `ls ~/.maro/workspace/memory/heartbeat-log.jsonl` = No such file; heartbeat-state.json `checked_at 2026-04-04T04:34:45Z` (mtime Apr 11); `scripts/heartbeat-ctl.sh status` = "heartbeat: stopped"; `ps` shows no heartbeat.py | platform | blocker-for-1.0 | confirmed | backlog-item: pick ONE supervision story (systemd timer per deploy/ units, or cron) and install it; rides 1.0 item (h) backend-resilience |
| ops-02 | Evolver has never run in production: workspace skills/ and personas/ empty, change_log.jsonl last entry is a test from 2026-04-12, playbook.md frozen Apr 11; fresh evolver-baselines.jsonl is loop-side baseline recording, not evolver runs | `ls -lat ~/.maro/workspace/skills/ personas/` = both empty, dir mtimes Apr 11 17:46-47; change_log.jsonl mtime Apr 11, 406 lines, tail = `{"ts": "2026-04-12T01:14:13Z", "module": "evolver", ..., "suggestion_id": "test-03"}`; playbook.md mtime Apr 11 19:14; evolver-baselines.jsonl 659 rows latest 2026-07-09T19:20Z written by loop | quality/self-improve | blocker-for-1.0 | confirmed | goal-brain-correction + backlog-item: evolver needs production hours before 1.0 claims self-learning; triangulates with data eye |
| ops-03 | host-check-notify cron (the only liveness verifier) has never fired; first fire tomorrow 08:05 will FAIL heartbeat (96d vs 900s threshold) and escalate to Telegram daily until ops-01 is fixed; cron→notify_telegram path unverified end-to-end | crontab `5 8 * * *` entry; /var/spool/cron/crontabs/clawd mtime Jul 9 14:39 (after 08:05); `/home/clawd/claude/logs/host-check.log` = No such file; host-check.sh:32 `MARO_HEARTBEAT_MAX_SEC default 900`, :151 fails on missing/stale state | ops | real-but-deferrable | confirmed | verify tomorrow 08:05 that the Telegram alert lands (that IS the end-to-end test); expect daily red until heartbeat scheduled |
| ops-04 | slack-bridge is dead in every dimension yet holds credentials: no systemd unit, no process, and its source (index.js per package.json main) does not exist — only package.json + lockfile + .env (600 perms, Mar 23) remain; auto-memory still describes it as a running systemd service | `systemctl status slack-bridge` (system+user) = "could not be found"; `ps aux | grep slack` = empty; `ls /home/clawd/claude/slack-bridge/` = .env, .env.example, .gitignore, package.json, package-lock.json only — no .js, no node_modules | ops | real-but-deferrable | confirmed | backlog-item: decide revive-or-remove; if remove, revoke/delete the Slack tokens in .env; correct the memory file |
| ops-05 | maro-observe dashboard is not running and its service was never installed | `deploy/systemd/maro-observe.service` mtime Jun 25, absent from unit dirs; `ss -tlnp` shows no observe listener; `ps` no observe process | platform | real-but-deferrable | confirmed | fold into ops-01's supervision decision (same deploy/ dir, same never-installed state) |
| ops-06 | Maro inbound-Telegram lane (telegram_listener.py) is dead: offset file frozen 2026-04-04, no listener process; only outbound notify_telegram is exercised | `~/.maro/workspace/telegram_offset.txt` mtime Apr 4; src/telegram_listener.py:67 names that offset file; `ps` shows no listener; live Telegram is OpenClaw gateway's (@edgar_allen_bot, journal Jul 9 13:09:12) | interface | real-but-deferrable | confirmed | backlog-item or explicit deprecation note — currently looks alive on disk |
| ops-07 | Stale dead-PID state litter, some invisible to host-check: run/heartbeat.pid names dead PID 3937627 (started 2026-07-09T09:33Z, gone by 15:35), output/build-loop.lock names dead PID 1948002 (May 6), btc_monitor.heartbeat frozen Apr 22; host-check's stale-lock probe watches memory/loop.lock (absent) and never sees build-loop.lock | cat run/heartbeat.pid = pid 3937627; `ps -p 3937627` = empty; cat output/build-loop.lock = `pid=1948002 started_at=2026-05-06T17:38:41Z`; btc_monitor.heartbeat mtime Apr 22; host-check.sh:55 `LOCK_FILE="$MEMDIR/loop.lock"`; `ls memory/loop.lock` = No such file | ops | cosmetic | confirmed | fixed-inline candidate for a later chunk: rm stale files; widen host-check lock glob if build-loop is kept |
| ops-08 | Two competing heartbeat pidfile conventions: heartbeat-ctl.sh tracks /tmp/maro-heartbeat.pid while heartbeat.py/proc_lock.py uses workspace run/heartbeat.pid — ctl `status` reports "stopped" regardless of a heartbeat started via `python -m heartbeat` | scripts/heartbeat-ctl.sh:15 `HEARTBEAT_PID_FILE="/tmp/maro-heartbeat.pid"` (header still says "poe heartbeat"); src/proc_lock.py:40-41 `<workspace>/run/<name>.pid`; observed: ctl said "stopped" while run/heartbeat.pid existed (albeit dead) | platform | real-but-deferrable | confirmed | backlog-item: port heartbeat-ctl to proc_lock's pidfile (or delete ctl in favor of the systemd story from ops-01) |
| ops-09 | 63% of step-costs.jsonl records (2113/3337) have empty `model` field, blinding per-model cost attribution across most of history | python census of step-costs.jsonl: total 3337, model empty 2113; sample first line (2026-03-28) and last line (2026-07-09) both `"model": ""` | platform/metrics | real-but-deferrable | confirmed | backlog-item: populate model at record time; matters for catching the next silent burner |
| ops-10 | No silent token burner exists today: $229.38 all-time, recent daily spend ≤ $2.79 (Jul 2-9: 2.45/2.78/1.97/0.69/0.43), OpenClaw cron list empty, no LLM-touching loop runs unattended | step-costs.jsonl daily aggregation (this session); `openclaw cron list` = "No cron jobs."; `ps` census: only interactive claude TUI (1d4h), openclaw gateway, hermes shim | ops | cosmetic (positive) | confirmed | record as verified-good; host-check spend cap ($25/day, host-check.sh:48) is the standing tripwire |
| ops-11 | Git hooks are currently healthy (post-fix): core.hooksPath unset, pre-push worker guard installed and executable (Jul 8), tripwire tests present | `git config --get core.hooksPath` exit 1 (unset); `.git/hooks/pre-push` mode -rwxr-xr-x mtime Jul 8 17:38; tests/test_git_guard.py:48-70 asserts installed+no-stale-hooksPath | ops | cosmetic (positive) | confirmed | none — already tripwired |
| ops-12 | Hermes trial containers auto-resurrect on reboot: hermes-maro and hermes-maro-shim run with restart=unless-stopped (started today), so today's experiment persists across reboots indefinitely — an off-switch-stays-off concern | `docker inspect`: /hermes-maro restart=unless-stopped started=2026-07-09T19:40Z; /hermes-maro-shim same, 18:11Z; hermes-maro-shim listens 127.0.0.1:11435 (claude_shim.py) | ops | real-but-deferrable | confirmed | when the trial chunk ends, `docker rm` (not just stop) per good-system-citizen rule |
| ops-13 | Three services listen on all interfaces of a box running an agent with money attached: novnc 0.0.0.0:6080, openclaw-webhost nginx 0.0.0.0:8088 (container up 2 weeks, restart=unless-stopped), vnc [::]:5900 | `ss -tlnp`: LISTEN 0.0.0.0:6080, 0.0.0.0:8088, [::]:5900, [::]:8088; systemctl is-enabled novnc/x11vnc = enabled; docker inspect openclaw-webhost started 2026-06-20 | ops/security | real-but-deferrable | confirmed | hand to Eye 5 threat model (in scope there); note whether 8088 serves anything sensitive |
| ops-14 | tmux-claude.service is decorative: oneshot "active (exited)" since Jun 20 boot, but its tmux session is gone — the current session 0 was created manually Jul 8; nothing re-creates the session or notices it died | systemctl status tmux-claude = active (exited) since 2026-06-20; `tmux ls` = "0: 1 windows (created Wed Jul 8 11:33:31 2026)"; sole pane runs interactive claude (pid 3768997) | ops | cosmetic | confirmed | backlog-item (tiny): RemainAfterExit oneshot can't supervise; either make it a real service or delete it |
| ops-15 | OpenClaw side (read-only context): gateway is the one genuinely supervised process on the box — user unit enabled, Restart=always/RestartSec=5, linger=yes, internal heartbeat + Telegram provider started on boot; restarted today 12:52→13:09 (manual, v-bump window) | systemctl --user status openclaw-gateway = enabled, active 2h24m, Restart=always in unit file; `loginctl show-user clawd -p Linger` = yes; journal Jul 9 13:09:12 `[heartbeat] started`, `[telegram] starting provider`; journalctl restart history Jul 1 + Jul 9 | ops (openclaw) | cosmetic (positive) | confirmed | none — this is the supervision pattern ops-01 should copy |

## Census (coverage, not findings)

Everything enumerated, whether or not it produced a finding:

- **Cron:** user clawd crontab = 2 entries (claude-update 04:00 daily — FIRES, log mtime Jul 9 04:00, last run updated 2.1.204→2.1.205; host-check-notify 08:05 — never fired). root crontab = none. /etc/cron.d = 5 stock (anacron, e2scrub_all, sysstat, zfsutils-linux, placeholder). cron.daily/weekly/monthly = stock distro + google-chrome. No `at` daemon.
- **systemd system:** 6 custom/notable units — tmux-claude, novnc, x11vnc, xvfb, xfce-minimal, postgresql (active-exited, stock) — plus 12 stock timers (apt, logrotate, fstrim, ...). No maro/slack/poe units.
- **systemd user:** 1 enabled unit — openclaw-gateway (running, supervised) — plus stock launchpadlib timer; litter: openclaw-gateway.service.bak.
- **Docker:** 3 containers — hermes-maro, hermes-maro-shim (today's trial, unless-stopped), openclaw-webhost nginx :8088 (up 2 wks).
- **Git hooks:** 1 hook (pre-push worker guard) in the maro repo; hooksPath unset; tripwire tests live.
- **Designed-but-unscheduled Maro lanes:** heartbeat, evolver, maro-observe, telegram_listener, build-loop, btc_monitor — 6, all dead, none supervised.
- **Listeners:** sshd :22, novnc :6080, nginx :8088, gateway :18789 (lo), hermes shim :11435 (lo), vnc :5900, postgres :5432 (lo), cups :631 (lo), unknown lo:43131 (likely claude TUI internal).
- **Long-running processes:** interactive `claude --dangerously-skip-permissions` (1d4h, in tmux), openclaw gateway node (2h24m), hermes shim python (3h22m). No orphaned Maro python procs (matches host-check's orphan probe: zero).
- **OpenClaw scheduled work:** `openclaw cron list` = none. Gateway-internal heartbeat only; openclaw.json heartbeat config section is empty.
- **Spend:** $229.38 all-time across 3337 step-cost records; Apr 26→Jun 11 gap = project hiatus, not data loss (matches repo history); post-Jul-2 era ≤ $2.79/day.
