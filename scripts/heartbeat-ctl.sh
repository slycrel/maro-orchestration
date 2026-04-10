#!/usr/bin/env bash
# heartbeat-ctl.sh — manage the poe heartbeat process safely
#
# Usage:
#   heartbeat-ctl.sh start    Start heartbeat (foreground, max 4h)
#   heartbeat-ctl.sh stop     Kill any running heartbeat
#   heartbeat-ctl.sh status   Show if heartbeat is running
#   heartbeat-ctl.sh restart  Stop + start
#
# Safety: RuntimeMaxSec equivalent via timeout(1). After 4 hours, the
# heartbeat auto-stops to prevent unattended token burn.

set -euo pipefail

HEARTBEAT_PID_FILE="/tmp/poe-heartbeat.pid"
MAX_RUNTIME_SECS=14400  # 4 hours

# Resolve repo root from this script's location (scripts/ is one level down)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HEARTBEAT_CMD="python3 ${REPO_ROOT}/src/heartbeat.py --loop --interval 60"

_is_running() {
    if [[ -f "$HEARTBEAT_PID_FILE" ]]; then
        local pid
        pid=$(cat "$HEARTBEAT_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
        rm -f "$HEARTBEAT_PID_FILE"
    fi
    # Also check for any heartbeat.py --loop process
    local found
    found=$(pgrep -u "$(id -u)" -f "heartbeat.py --loop" 2>/dev/null || true)
    if [[ -n "$found" ]]; then
        echo "$found" | head -1
        return 0
    fi
    return 1
}

cmd_status() {
    local pid
    if pid=$(_is_running); then
        local elapsed
        elapsed=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
        echo "heartbeat: running (pid=$pid, uptime=${elapsed:-?}s)"
    else
        echo "heartbeat: stopped"
    fi
}

cmd_stop() {
    local pid
    if pid=$(_is_running); then
        echo "stopping heartbeat (pid=$pid)..."
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
        rm -f "$HEARTBEAT_PID_FILE"
        echo "stopped."
    else
        echo "heartbeat is not running."
    fi
}

cmd_start() {
    local pid
    if pid=$(_is_running); then
        echo "heartbeat already running (pid=$pid). Use 'restart' to replace."
        exit 1
    fi
    echo "starting heartbeat (max ${MAX_RUNTIME_SECS}s / $((MAX_RUNTIME_SECS / 3600))h)..."
    cd "$REPO_ROOT"
    export PYTHONPATH="${REPO_ROOT}/src"
    nohup timeout "${MAX_RUNTIME_SECS}" $HEARTBEAT_CMD > /dev/null 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$HEARTBEAT_PID_FILE"
    echo "started (pid=$new_pid). Will auto-stop after ${MAX_RUNTIME_SECS}s."
}

cmd_restart() {
    cmd_stop
    cmd_start
}

case "${1:-status}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_restart ;;
    *)
        echo "Usage: $0 {start|stop|status|restart}"
        exit 1
        ;;
esac
