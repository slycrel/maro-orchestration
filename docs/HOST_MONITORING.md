---
status: living
---

# Host Monitoring Checklist — Maro

Four surfaces that silently kill an always-on Maro box: disk, token/$ spend,
orphaned worker processes, and a stale heartbeat. Each section below carries a
**rationale, a threshold, and ONE self-contained detection command** that is
**quiet on success and exits non-zero on breach** — drop any into
cron/systemd-timer/heartbeat and treat a non-zero exit as the alert. Thresholds
are tunable via the leading shell var.

`scripts/host-check.sh` bundles all four into one run — one `PASS`/`FAIL` line
per item (with the offending value), a non-zero exit if any check fails, and a
`--quiet` mode that prints only failures for cron. It is dependency-light (bash +
coreutils/procps; no python/jq). The commands below are the standalone
equivalents you can lift individually.

Paths grounded on the live host (2026-07-08). All of `~/.maro/workspace/` (incl.
`output/`, `memory/`) resolves to `/` (`/dev/sda3`, ext4, 156G) — no dedicated
mount.

---

## 1. Disk

**Rationale.** The workspace, run output, and the append-only memory ledgers all
sit on `/`. A full root filesystem silently corrupts JSONL writes, stalls the
loop, and can wedge the box. The in-code gate (`sheriff.check_system_health`)
only fails at **< 100 MB free** — effectively already-dead for this workload —
so we alert far earlier.

**Threshold.** `/` usage **≥ 85%** (currently 56%). Override via the literal in
the `awk` test.

```bash
df --output=pcent / | tail -1 | tr -dc 0-9 | awk '{exit ($1>=85)?1:0}'
```

*Remediation:* prune `~/.maro/workspace/runs/` and old `output/`;
`du -sh ~/.maro/workspace/* | sort -h`.

---

## 2. Token spend

**Rationale.** Every step's cost is appended to
`~/.maro/workspace/memory/step-costs.jsonl` by `metrics.record_step_cost()`. An
autonomous loop that goes off the rails burns real API dollars; a daily ceiling
is the cheapest circuit breaker. `metrics.spend_today()` sums `cost_usd` for all
entries since UTC midnight (`metrics.py:168`).

**Threshold.** Today's spend **≥ $20.00** (a normal day observed at ~$0.69).
Adjust `CAP`.

```bash
CAP=20.00; cd /home/clawd/claude/maro-orchestration && \
  PYTHONPATH=src python3 -c "import sys,metrics; sys.exit(1 if metrics.spend_today()>=${CAP} else 0)"
```

*Remediation:* check for a runaway loop; raise the cap or gate on
`budget.daily_usd`.

---

## 3. Orphaned processes

**Rationale.** Legitimate agent/worker processes match
`python.*(handle|agent_loop)` and run under a live parent. When a supervising
session dies, its children get reparented to init (PPID 1), crash into a
`<defunct>` zombie (`stat` contains `Z`), or hang for hours — each leaks memory
and can hold locks on the memory ledgers. This flags any matching process that
is reparented, zombie, or older than the age ceiling. Zero matches = healthy.

**Threshold.** Any matched process with **PPID 1**, **defunct (`Z`) status**, or
**age > 7200s (2h)**. Age is advisory — a genuinely long legitimate run can trip
it; PPID 1 and `Z` are the hard signals.

```bash
pgrep -f 'python.*(handle|agent_loop)' \
  | xargs -r -I{} ps -o pid=,ppid=,etimes=,stat= -p {} \
  | awk '($2==1)||($4 ~ /Z/)||($3>7200){bad++} END{exit (bad>0)?1:0}'
```

*Remediation:* inspect then `kill <pid>`; if `memory/loop.lock` names a dead
PID, `rm memory/loop.lock` (cleared on next `interrupt.get_running_loop()` read,
`interrupt.py:553`).

---

## 4. Stale heartbeats

**Rationale.** The heartbeat writes liveness to
`~/.maro/workspace/memory/heartbeat-state.json` (field `checked_at`, UTC ISO)
each time it ticks (`sheriff.write_heartbeat_state()`, `sheriff.py:429`).
Maro ships no recurring heartbeat of its own — `maro heartbeat` is a
one-shot tick (README "Optional Services"; `heartbeat.autonomy` off by
default) you hook to your own scheduler if you want recurrence. Unless
you've installed such a hook, there's no promise of any particular tick
cadence, so this check is a "has this box gone dark for a while?" signal,
not a "did the last cycle fire on time?" one. We compare the recorded
`checked_at` against now rather than file mtime, since `checked_at` is the
authoritative liveness marker. A missing/unparseable file counts as a breach.

**Threshold.** `checked_at` **older than 7 days**, or file absent/unreadable.
Adjust `N` (days). **Re-aligned 2026-07-12 (ops-r2-01/02):** the old
15-minute threshold assumed a recurring 30-min loop that was never actually
installed on this host — it fired FAIL every day via
`host-check-notify.sh`'s cron, paging Jeremy's Telegram for a condition
that wasn't an incident. `scripts/host-check.sh` additionally treats a beat
older than 30 days as an intentionally-off design state (`skip`, not
`fail`) rather than a breach — set `MARO_HEARTBEAT_EXPECTED=1` to enforce
staleness past 30 days too, once a recurring hook is actually wired up.

```bash
N=7; HB=/home/clawd/.maro/workspace/memory/heartbeat-state.json; python3 -c "
import json,sys,datetime
try:
    ts=json.load(open('$HB'))['checked_at']
    age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(ts)).total_seconds()
    sys.exit(1 if age > $N*86400 else 0)
except Exception:
    sys.exit(1)
"
```

*Remediation:* if you want recurring ticks, hook your scheduler to
`maro heartbeat` (see README "Optional Services"); a fresh `maro heartbeat`
run clears the staleness immediately.

---

Keep this checklist in sync with `scripts/host-check.sh` if either changes
(currency rule).
