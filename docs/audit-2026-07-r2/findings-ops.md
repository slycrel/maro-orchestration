# Purgatorio r2 — Eye 1 — Ops census: "what actually runs?"

Probed 2026-07-10 ~01:35–01:55 MDT, read-only. Every claim below was probed in
this session (command output / file mtime / gh run listed). Nothing was fixed,
restarted, enabled, or disabled. Prior file:
`docs/audit-2026-07/findings-ops.md` (2026-07-09).

## Headline

The fix wave genuinely moved the two big ops blockers, but neither is closed:

1. **The heartbeat has now beaten in production** — 2 beats in
   `memory/heartbeat-log.jsonl` (02:29Z degraded → 03:21Z healthy, Jul 10 UTC,
   Jeremy-authorized burn-in), tier-2 diagnosis fired, `telegram_sent: true`
   on the degraded beat. But **still nothing schedules it**, now by decree
   (BACKLOG #21: "one-shot ticks only... installation is Jeremy's call").
   ops-01's "never beat" half is resolved; the "no scheduler" half is now a
   deliberate posture, not a missing fix.
2. **The evolver got a trigger but still has zero production meta-cycles** —
   `evolver.run_cadence: 10` enabled in workspace config (Jul 9 20:28),
   counter live at `runs_since_evolve: 1` (Jul 10 02:35Z). But
   change_log.jsonl is still frozen at the 2026-04-12 test entry, workspace
   skills/ and personas/ still empty (Apr 11), playbook.md frozen Apr 11.
   Nine more organic finalizations before the first real cycle.
3. **New structural contradiction (ops-r2-01):** host-check cron (daily
   08:05, still never fired — first slot since install arrives ~6h after this
   probe) fails heartbeat age at >900s, but the decree forbids a recurring
   heartbeat hook. Steady state = red Telegram alert every morning, forever,
   by design collision. The heartbeat→Telegram leg is now proven
   (`telegram_sent: true`); the cron→host-check→Telegram leg is still
   end-to-end unverified.
4. **Cleanups verified on disk:** hermes containers gone (`docker ps -a`:
   only openclaw-webhost + one 3-months-dead sandbox), shim listener :11435
   gone, slack-bridge `.env` deleted + MOTHBALLED.md present, build-loop.lock
   gone, CI workflow exists AND runs (green at HEAD; one mid-wave red fixed
   by the next commit).
5. **Spend healthy:** $232.97 all-time; Jul 9 = $3.93 (burn-in day incl.
   7 tier-2 zombie diagnoses — the exact class the sheriff fix then killed),
   Jul 10 = $0.09. `openclaw cron list` = none. But model attribution is
   still blind on NEW rows (Jul 9: 10/70 empty; Jul 10: 1/2) — ops-09 not
   touched by the wave.

## A) Prior findings re-verified

| id | r1 claim (short) | current status | evidence probed 2026-07-10 | note |
|---|---|---|---|---|
| ops-01 | Heartbeat never beat, nothing schedules it | **partially-resolved** | `wc -l heartbeat-log.jsonl` = 2 beats (02:29Z degraded / 03:21Z healthy Jul 10 UTC); heartbeat-state.json `checked_at 2026-07-10T03:21Z` healthy; `crontab -l` = 2 entries (neither heartbeat); `systemctl` system+user list-units/timers: zero maro units; `openclaw cron list` = none; no heartbeat hooks in openclaw.json | Beat-half resolved (burn-in real, verdict/diagnosis machinery live-proven). Scheduler-half now decreed open (BACKLOG #21: hook install is Jeremy-gated) — no longer a missing-fix blocker, but the liveness layer still doesn't run unattended |
| ops-02 | Evolver zero production hours | **partially-resolved** | workspace config.yml `evolver.run_cadence: 10` (file mtime Jul 9 20:28); `memory/evolver_cadence.json` = `{"runs_since_evolve": 1, "updated_at": "2026-07-10T02:35Z"}`; change_log.jsonl still mtime Apr 11, tail = `suggestion_id: "test-03"` 2026-04-12; skills/ + personas/ still empty (Apr 11); playbook.md Apr 11 | Trigger shipped + enabled + counter proven incrementing on organic finalization (d6c143b). Meta-cycle itself still has zero production runs — fires at the 10th finalization |
| ops-03 | host-check cron never fired; will red-alert daily | **still-open** | `cat ~/claude/logs/host-check.log` = No such file (still never fired — installed Jul 9 14:39, only 08:05 slot since then is today, ~6h after this probe); host-check.sh:49 `HB_MAX_SEC 900` unchanged (git log: last touched b8758c9, pre-audit); heartbeat-state mtime Jul 9 21:21 → will be ~11h stale at 08:05 | Escalation-path split: heartbeat→notify_telegram now PROVEN (beat 02:29Z `telegram_sent: true`); cron→host-check→Telegram still unverified. Daily-red prediction upgraded to structural — see ops-r2-01 |
| ops-04 | slack-bridge dead but holds credentials | **resolved** | `ls ~/claude/slack-bridge/` = .env.example, .gitignore, MOTHBALLED.md (Jul 9 21:00), package.json, package-lock.json — **.env gone**; MOTHBALLED.md records tokens revoked by Jeremy 2026-07-09 + revive-or-delete deferred | Disk state fully matches the mothball memory. Token revocation itself is Jeremy's recorded claim (not probeable from here); the on-disk credential exposure is gone, which was the finding |
| ops-05 | maro-observe never installed, not running | **still-open** | `deploy/systemd/maro-observe.service` still on disk (Jun 25); no unit installed (`systemctl` greps empty); `ss -tlnp` shows no observe listener | Unchanged. Under the new supervision posture (d6c143b: bootstrap prints instructions, no units) the deploy/ unit is now a leftover of a rejected story — see ops-r2-04 |
| ops-06 | telegram_listener lane dead but alive-looking | **still-open** | `~/.maro/workspace/telegram_offset.txt` mtime still Apr 4; no listener process in `ps`; `git log -- src/telegram_listener.py` = nothing since e0e33c0/cf9a475 (no deprecation note added) | Untouched by the wave |
| ops-07 | Stale dead-PID litter; host-check watches wrong lock | **partially-resolved** | `output/build-loop.lock` = **gone**; `btc_monitor.heartbeat` still mtime Apr 22; host-check.sh:55 still `LOCK_FILE="$MEMDIR/loop.lock"` only; NEW stale pidfile: `run/heartbeat.pid` names PID 4165512 started 2026-07-10T06:12Z, `ps -p` = dead (see ops-r2-03) | build-loop litter cleared; btc_monitor + lock-glob unchanged; one new dead-PID pidfile appeared (harmless by flock design — proc_lock: stale pidfile can never block) |
| ops-08 | Two competing heartbeat pidfile conventions | **still-open** | scripts/heartbeat-ctl.sh:15 still `HEARTBEAT_PID_FILE="/tmp/maro-heartbeat.pid"`, header still "poe heartbeat", still launches `--loop --interval 60`; proc_lock still uses `run/heartbeat.pid` | Untouched; now the third supervision story next to deploy/ units and the shim's printed instructions (ops-r2-04) |
| ops-09 | 63% of cost rows have empty model | **still-open** | Re-census: 2121/3394 empty all-time; **new rows still empty**: Jul 9 = 10/70 empty, Jul 10 = 1/2 empty | Fix wave did not touch record-time model attribution; the next-burner-attribution blindness persists |
| ops-10 | No silent token burner (positive) | **resolved** | step-costs.jsonl daily: Jul 8 $0.69 / Jul 9 $3.93 / Jul 10 $0.09; total $232.97; `openclaw cron list` = "No cron jobs."; ps census: only interactive claude TUI (since Jul 8), openclaw gateway | Re-verified good. Jul 9 uptick = burn-in tier-2 diagnoses on 7 zombie projects — the exact class a9824ce then eliminated (03:21Z beat: 0 stuck, 0 recovery actions) |
| ops-11 | Git hooks healthy (positive) | **resolved** | `git config --get core.hooksPath` exit 1 (unset); `.git/hooks/pre-push` -rwxr-xr-x Jul 8 17:38; CI job now also runs "Install git hooks" step (gh run view 29067595461 shows it green) | Re-verified good; CI now exercises hook install on every push |
| ops-12 | Hermes containers auto-resurrect (unless-stopped) | **resolved** | `docker ps -a` = only openclaw-webhost (up 2 wks) + openclaw-sbx worker Exited 3 months ago; no hermes-maro / hermes-maro-shim; `ss` shows no :11435 listener; commit 0281403 "SF-7 hermes trial containers removed" matches disk | Containers removed, not just stopped — off-switch rule satisfied |
| ops-13 | novnc/nginx/vnc listen on all interfaces | **still-open** | `ss -tlnp`: 0.0.0.0:6080 (novnc), 0.0.0.0:8088 + [::]:8088 (nginx webhost, container still up 2 wks), [::]:5900 (vnc; v4 side is 127.0.0.1) | Unchanged; Eye 5's domain, tracked here for census continuity |
| ops-14 | tmux-claude.service decorative | **still-open** | `systemctl status tmux-claude` = active (exited) since 2026-06-20; `tmux ls` = session 0 created Jul 8 11:33 (manual) | Untouched (cosmetic) |
| ops-15 | OpenClaw gateway is the one supervised process (positive) | **resolved** | `systemctl --user is-active openclaw-gateway` = active; node gateway pid 4008084 up since Jul 9, listening [::1]/127.0.0.1:18789 | Re-verified good |

## B) New findings (r2)

| id | claim | evidence | subsystem | severity |
|---|---|---|---|---|
| ops-r2-01 | Daily-red is now structural, not transitional: host-check cron (08:05 daily) fails heartbeat at age >900s, while BACKLOG #21's decree forbids the recurring heartbeat hook ("one-shot ticks only, no persistent timer") — so once the cron finally fires (first-ever slot is TODAY 08:05; log still absent at probe time) it escalates to Telegram every morning indefinitely; heartbeat-state was already ~4.5h old at probe. The heartbeat→Telegram leg is proven (beat 02:29Z `telegram_sent: true`) but the cron→host-check→notify leg remains end-to-end unverified | crontab `5 8 * * *`; `/home/clawd/claude/logs/host-check.log` = No such file; host-check.sh:49 `HB_MAX_SEC=${MARO_HEARTBEAT_MAX_SEC:-900}` (unchanged since b8758c9); heartbeat-state.json mtime Jul 9 21:21; BACKLOG.md #21 decree text; heartbeat-log.jsonl beat 2026-07-10T02:29Z `"telegram_sent": true` | ops | real-but-deferrable |
| ops-r2-02 | `heartbeat.autonomy: true` left set in `~/.maro/workspace/config.yml` after the Jul 9 burn-in: any future `heartbeat --loop` start (via deploy unit, heartbeat-ctl, or a copy-pasted hook instruction) runs in autonomy mode — backlog/task drain, evolver, LLM spend — not health-only; the deploy unit's comment ("intentionally runs heartbeat in health-only mode by default") is now false on this box. A left-on switch with no scheduler pointing at it yet | workspace config.yml lines 1-2 `heartbeat:\n  autonomy: true` (mtime Jul 9 20:28); heartbeat.py resolves `_cfg_get("heartbeat.autonomy", False)` when `--autonomy` not passed; deploy/systemd/maro-heartbeat.service comment block; burn-in beats show tier-2 LLM diagnosis + Telegram actually fired | platform/ops | real-but-deferrable |
| ops-r2-03 | Unattributed heartbeat loop start died invisibly: `run/heartbeat.pid` records PID 4165512 started 2026-07-10T06:12:27Z (00:12 local, after the last commit-push window), PID dead at probe, and NO beat row after 03:21Z — a `heartbeat --loop` start that dies before its first completed beat leaves no record anywhere (no log row, no crash artifact, nothing notices). Harmless per flock design (stale pidfile can't block restart), but demonstrates pre-beat deaths are unobservable | `cat run/heartbeat.pid` = `{"pid": 4165512, "started_at": "2026-07-10T06:12:27Z"}`; `ps -p 4165512` = empty; heartbeat-log.jsonl last row 03:21Z (2 rows total); only run_loop writes the pidfile (heartbeat.py:944-945, sole try_hold_pidfile caller) | platform | cosmetic |
| ops-r2-04 | Three contradictory supervision stories now ship simultaneously: d6c143b's decided posture (bootstrap prints host-scheduler instructions, no units) coexists with `deploy/systemd/maro-heartbeat.service` + `maro-observe.service` (Jun 25, never installed, "health-only" comment stale per ops-r2-02) and `scripts/heartbeat-ctl.sh` (/tmp pidfile, "poe" header, hardcoded `--loop --interval 60`). An operator following deploy/ or ctl gets a 60s-interval loop that the decree says shouldn't exist | ls deploy/systemd/ (both units, Jun 25); heartbeat-ctl.sh:1-24 probed; d6c143b commit body ("bootstrap no longer generates systemd/launchd units... replaced with printed host-scheduler hook instructions") | ops | cosmetic |

## Clean checks (probed, no finding)

- **CI is real and green at HEAD** (Purgatorio blocker 8 / land-02): `.github/workflows/ci.yml`
  (Jul 9 18:48) + `gh run list` shows 8 runs on Jul 10, all push-triggered on main.
  One red mid-wave: 6c03068 failed `tests/test_stranded_sweep.py::test_resume_runs_agent_loop`
  (`SimpleNamespace has no attribute 'steps'`), fixed by the very next commit
  772bb20 ("closure pass tolerates results without steps") — subsequent runs green.
  Two "cancelled" runs are superseded pushes, not failures.
- **Sheriff dormancy/lane-health fix (a9824ce) live-proven in the beat ledger**:
  beat 02:29Z (pre-fix) = degraded, 7 stuck test projects, 7 tier-2 LLM
  diagnoses, `pkg_anthropic: warn`; beat 03:21Z (post-fix) = healthy, 0 stuck,
  0 recovery actions, `llm_backend: ok: subprocess, openrouter, openai`.
  `projects/_archive/` exists and populated; projects/ root ~120 dirs
  (BACKLOG #21 claims 238→125 live — matches the ls count);
  `sheriff.dormant_days` registered in docs/DEFAULTS.md:60.
- **Hermes trial fully gone**: no containers (`docker ps -a`), no :11435
  listener, no shim process.
- **Slack-bridge mothball matches disk**: .env deleted, MOTHBALLED.md dated
  2026-07-09 records revocation + deferral.
- **No new cron entries, no new systemd units (system or user), no new
  listeners, no new long-running processes** vs. the r1 census. Interactive
  claude TUI (Jul 8) + openclaw gateway remain the only long-runners.
- **Spend**: $232.97 all-time (+$3.59 since r1's $229.38 snapshot across
  ~19h); no burner; `openclaw cron list` empty; budget caps unchanged
  (per_run $2 / daily $10 in user config; host-check $25/day tripwire).
- **evolver_cadence counter mechanics**: locked file `memory/evolver_cadence.json`
  + `.lock` sibling, counter=1 after one organic finalization — matches
  d6c143b/fb667b0 claims exactly.
