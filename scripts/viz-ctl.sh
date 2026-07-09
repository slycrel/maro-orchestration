#!/usr/bin/env bash
# viz-ctl.sh — manage the read-only run-visibility HTTP server safely
#
# Usage:
#   viz-ctl.sh start [--host H] [--port P]   Start server in the background
#   viz-ctl.sh stop                          Kill any running viz server
#   viz-ctl.sh status                        Show if it's running
#   viz-ctl.sh restart [--host H] [--port P] Stop + start
#
# Serves runs_root() (default ~/.maro/workspace/runs/) read-only over
# http://, loopback-only by default (src/viz_server.py). No max-runtime
# timeout, unlike heartbeat-ctl.sh — a viz server is meant to stay up.

set -euo pipefail

VIZ_PID_FILE="/tmp/maro-viz-server.pid"
VIZ_LOG_FILE="/tmp/maro-viz-server.log"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

_is_running() {
    if [[ -f "$VIZ_PID_FILE" ]]; then
        local pid
        pid=$(cat "$VIZ_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
            return 0
        fi
        rm -f "$VIZ_PID_FILE"
    fi
    local found
    found=$(pgrep -u "$(id -u)" -f "cli.py viz serve" 2>/dev/null || true)
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
        echo "viz server: running (pid=$pid, uptime=${elapsed:-?}s, log=$VIZ_LOG_FILE)"
    else
        echo "viz server: stopped"
    fi
}

cmd_stop() {
    local pid
    if pid=$(_is_running); then
        echo "stopping viz server (pid=$pid)..."
        kill "$pid" 2>/dev/null || true
        sleep 1
        kill -9 "$pid" 2>/dev/null || true
        rm -f "$VIZ_PID_FILE"
        echo "stopped."
    else
        echo "viz server is not running."
    fi
}

cmd_start() {
    local pid
    if pid=$(_is_running); then
        echo "viz server already running (pid=$pid). Use 'restart' to replace."
        exit 1
    fi
    echo "starting viz server..."
    cd "$REPO_ROOT"
    export PYTHONPATH="${REPO_ROOT}/src"
    nohup python3 "${REPO_ROOT}/src/cli.py" viz serve "$@" > "$VIZ_LOG_FILE" 2>&1 &
    local new_pid=$!
    echo "$new_pid" > "$VIZ_PID_FILE"
    sleep 0.5
    if ! kill -0 "$new_pid" 2>/dev/null; then
        echo "failed to start — see $VIZ_LOG_FILE"
        rm -f "$VIZ_PID_FILE"
        exit 1
    fi
    echo "started (pid=$new_pid). log=$VIZ_LOG_FILE"
}

cmd_restart() {
    cmd_stop
    cmd_start "$@"
}

action="${1:-status}"
[[ $# -gt 0 ]] && shift

case "$action" in
    start)   cmd_start "$@" ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    restart) cmd_restart "$@" ;;
    *)
        echo "Usage: $0 {start|stop|status|restart} [--host H] [--port P]"
        exit 1
        ;;
esac
