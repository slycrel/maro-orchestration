#!/usr/bin/env bash
#
# host-check.sh — Maro host monitoring checklist (cron-friendly).
#
# Runs four independent checks against this host's Maro runtime state and prints
# ONE line per check: "PASS <name>  <observed value>" or "FAIL <name>  <offending
# value>". The script exits NONZERO if ANY check fails, so cron/systemd can treat
# a non-zero exit as the alert.
#
# Dependency-light by design: bash + coreutils (awk, sed, grep, df, date, stat,
# tr) + procps (ps, pgrep). No python, no jq — safe on a bare cron box with no
# venv or cwd. JSON fields are extracted with grep/sed; the spend sum is an awk
# pass over the ledger (verified equal to metrics.spend_today() = $0.689412 on
# 2026-07-08). See docs/HOST_MONITORING.md for rationale + standalone commands.
#
# Usage:
#   scripts/host-check.sh            # full report, all four lines + summary
#   scripts/host-check.sh --quiet    # cron mode: print only FAIL lines; silent
#                                     # (no output) when everything passes
#   scripts/host-check.sh -q         # same as --quiet
#
# Exit: 0 = all checks passed, 1 = one or more checks failed.
#
# All state-artifact paths verified on this host (2026-07-08):
#   token spend : ~/.maro/workspace/memory/step-costs.jsonl  (metrics.record_step_cost)
#   loop lock   : ~/.maro/workspace/memory/loop.lock         (interrupt.set_loop_running)
#   heartbeat   : ~/.maro/workspace/memory/heartbeat-state.json  (sheriff.write_heartbeat_state)
#
# Configurable via environment (with sane defaults):
#   MARO_DISK_WARN_PCT         disk/inode use% that fails a check  (default 85)
#   MARO_DAILY_USD_CAP         daily token-spend cap in USD        (default 25)
#   MARO_HEARTBEAT_MAX_SEC     max heartbeat age before stale      (default 900 = 15m)
#   MARO_PROC_MAX_ETIMES       max age (s) for a matched proc      (default 7200 = 2h)
#   MARO_WORKSPACE             workspace dir holding memory/       (default ~/.maro/workspace)

set -o pipefail

# --- args --------------------------------------------------------------------
QUIET=0
case "${1:-}" in
    -q|--quiet) QUIET=1 ;;
    "" ) ;;
    * ) echo "usage: $0 [--quiet|-q]" >&2; exit 2 ;;
esac

# --- config ------------------------------------------------------------------
DISK_WARN_PCT="${MARO_DISK_WARN_PCT:-85}"
DAILY_USD_CAP="${MARO_DAILY_USD_CAP:-25}"
HB_MAX_SEC="${MARO_HEARTBEAT_MAX_SEC:-900}"
PROC_MAX_ETIMES="${MARO_PROC_MAX_ETIMES:-7200}"
WORKSPACE="${MARO_WORKSPACE:-$HOME/.maro/workspace}"
MEMDIR="$WORKSPACE/memory"

COSTS_FILE="$MEMDIR/step-costs.jsonl"
LOCK_FILE="$MEMDIR/loop.lock"
HB_FILE="$MEMDIR/heartbeat-state.json"

TRIPPED=0

# pass/fail emit one line each. In --quiet mode PASS lines are suppressed so a
# healthy cron run produces no output at all; FAIL lines always print.
pass() { TRIPPED=$TRIPPED; [ "$QUIET" -eq 1 ] || printf 'PASS  %-10s %s\n' "$1" "$2"; }
fail() { TRIPPED=1;        printf 'FAIL  %-10s %s\n' "$1" "$2"; }
skip() { TRIPPED=$TRIPPED; [ "$QUIET" -eq 1 ] || printf 'SKIP  %-10s %s\n' "$1" "$2"; }

# --- (1) disk + inodes -------------------------------------------------------
# df the workspace fs itself (follows whichever fs the workspace lives on; on
# this host that resolves to / = /dev/sda3, no dedicated mount). Fail if block
# OR inode Use% exceeds the threshold — a full inode table wedges writes too.
check_disk() {
    local blk ino
    blk=$(df -P  "$WORKSPACE" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
    ino=$(df -Pi "$WORKSPACE" 2>/dev/null | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
    if [ -z "$blk" ] || [ -z "$ino" ]; then
        fail disk "cannot parse df for $WORKSPACE"
        return
    fi
    if [ "$blk" -ge "$DISK_WARN_PCT" ] || [ "$ino" -ge "$DISK_WARN_PCT" ]; then
        fail disk "block ${blk}% / inode ${ino}% (>= ${DISK_WARN_PCT}%) on $WORKSPACE"
    else
        pass disk "block ${blk}% / inode ${ino}% (< ${DISK_WARN_PCT}%)"
    fi
}

# --- (2) token spend ---------------------------------------------------------
# Sum cost_usd over ledger rows whose recorded_at is today (UTC). Pure awk —
# equals metrics.spend_today() but needs no python/venv/cwd. Fail over the cap.
check_spend() {
    if [ ! -f "$COSTS_FILE" ]; then
        fail spend "ledger missing at $COSTS_FILE (loop not recording costs?)"
        return
    fi
    local spend
    spend=$(awk -v day="$(date -u +%Y-%m-%d)" '
        index($0, "\"recorded_at\": \"" day)==0 { next }
        match($0, /"cost_usd":[ ]*[0-9.]+/) {
            s = substr($0, RSTART, RLENGTH); sub(/.*:[ ]*/, "", s); total += s
        }
        END { printf "%.4f", total+0 }' "$COSTS_FILE")
    # Float compare via awk — bash integer test can't handle decimals.
    if awk -v s="$spend" -v cap="$DAILY_USD_CAP" 'BEGIN{exit !(s+0 >= cap+0)}'; then
        fail spend "\$${spend} today (>= \$${DAILY_USD_CAP} cap)"
    else
        pass spend "\$${spend} today (< \$${DAILY_USD_CAP} cap)"
    fi
}

# --- (3) orphaned processes + stale lock ------------------------------------
# A Maro python proc reparented to init (PPID 1), gone zombie (STAT has Z), or
# running longer than the age ceiling = a loop whose supervisor died but the
# worker leaked. Plus a stale loop.lock (present, but its recorded PID is dead).
check_orphans() {
    local bad
    # Match Maro's real invocation forms only (`-m handle`, handle.py, agent_loop,
    # heartbeat.py) — not a bare "heartbeat" substring (would catch websockify etc).
    bad=$(ps -eo pid=,ppid=,etimes=,stat=,cmd= \
        | awk -v maxage="$PROC_MAX_ETIMES" '
            /python3?.*(-m +handle|handle\.py|agent_loop|heartbeat\.py)/ &&
            ($2==1 || $4 ~ /Z/ || $3+0 > maxage) {
                printf "%s(ppid=%s,age=%ss,stat=%s) ", $1, $2, $3, $4
            }')
    if [ -n "$bad" ]; then
        fail orphans "reparented/zombie/aged Maro procs: ${bad}"
    else
        pass orphans "0 reparented/zombie/aged Maro procs"
    fi

    # Stale-lock check: loop.lock names a PID that is no longer alive.
    if [ -f "$LOCK_FILE" ]; then
        local lpid
        lpid=$(grep -o '"pid"[ ]*:[ ]*[0-9]\+' "$LOCK_FILE" | grep -o '[0-9]\+' | head -1)
        if [ -n "$lpid" ] && [ "$lpid" != "0" ]; then
            if kill -0 "$lpid" 2>/dev/null; then
                pass loop-lock "held by live PID $lpid"
            else
                fail loop-lock "$LOCK_FILE names dead PID $lpid (rm to clear)"
            fi
        else
            pass loop-lock "present, no PID recorded"
        fi
    else
        pass loop-lock "absent (no loop claims to run)"
    fi
}

# --- (4) stale heartbeat -----------------------------------------------------
# heartbeat-state.json's checked_at (UTC ISO) vs now. Missing/unparseable =
# breach. Fail if older than MARO_HEARTBEAT_MAX_SEC.
check_heartbeat() {
    if [ ! -f "$HB_FILE" ]; then
        fail heartbeat "state file missing at $HB_FILE (heartbeat loop down?)"
        return
    fi
    local checked_at ts now age
    # Extract checked_at with sed — no jq/python. Field is: "checked_at": "<iso>",
    checked_at=$(grep -o '"checked_at"[ ]*:[ ]*"[^"]*"' "$HB_FILE" \
        | head -1 | sed 's/.*:[ ]*"//; s/"$//')
    ts=$(date -d "$checked_at" +%s 2>/dev/null)
    [ -z "$ts" ] && ts=$(stat -c %Y "$HB_FILE" 2>/dev/null)   # fall back to mtime
    if [ -z "$ts" ]; then
        fail heartbeat "cannot read checked_at or mtime from $HB_FILE"
        return
    fi
    now=$(date +%s)
    age=$(( now - ts ))
    # A beat >30 days old is a design state, not an incident — the heartbeat
    # loop was deliberately removed on this host (2026-04, "off switches stay
    # off"). A live loop that dies is caught in the 15min–30day window. Set
    # MARO_HEARTBEAT_EXPECTED=1 to enforce staleness regardless.
    if [ "$age" -gt 2592000 ] && [ "${MARO_HEARTBEAT_EXPECTED:-0}" != "1" ]; then
        skip heartbeat "no active loop (last beat $((age / 86400))d ago; intentionally off — MARO_HEARTBEAT_EXPECTED=1 to enforce)"
    elif [ "$age" -gt "$HB_MAX_SEC" ]; then
        fail heartbeat "last beat ${age}s ago (> ${HB_MAX_SEC}s); checked_at=${checked_at:-<none>}"
    else
        pass heartbeat "last beat ${age}s ago (< ${HB_MAX_SEC}s)"
    fi
}

# --- run all -----------------------------------------------------------------
[ "$QUIET" -eq 1 ] || echo "== Maro host-check @ $(date -u +%Y-%m-%dT%H:%M:%SZ) =="
check_disk
check_spend
check_orphans
check_heartbeat
if [ "$QUIET" -eq 0 ]; then
    echo "== $( [ "$TRIPPED" -eq 0 ] && echo 'ALL OK' || echo 'CHECK(S) FAILED' ) =="
fi

exit "$TRIPPED"

# =============================================================================
# INSTALL STANZA — commented out. Jeremy: uncomment ONE of these to enable.
# Not wired automatically (per ticket). host-check.sh exits non-zero on breach,
# so any wrapper that alerts on non-zero exit works.
#
# --- Option A: cron (every 10 min, mail on failure only via --quiet) ---------
#   crontab -e   # then add:
#   */10 * * * * /home/clawd/claude/maro-orchestration/scripts/host-check.sh --quiet \
#       || echo "maro host-check FAILED on $(hostname) at $(date)" \
#          | mail -s "[maro] host-check alert" slycrel@gmail.com
#
# --- Option B: systemd timer (service + timer units) -------------------------
#   # /etc/systemd/system/maro-host-check.service
#   [Unit]
#   Description=Maro host-check
#   [Service]
#   Type=oneshot
#   User=clawd
#   ExecStart=/home/clawd/claude/maro-orchestration/scripts/host-check.sh --quiet
#
#   # /etc/systemd/system/maro-host-check.timer
#   [Unit]
#   Description=Run Maro host-check every 10 minutes
#   [Timer]
#   OnBootSec=5min
#   OnUnitActiveSec=10min
#   [Install]
#   WantedBy=timers.target
#
#   # then:  sudo systemctl daemon-reload && sudo systemctl enable --now maro-host-check.timer
#   # a FAIL exit is recorded by systemd; surface it with:
#   #   OnFailure=  or  journalctl -u maro-host-check.service
# =============================================================================
