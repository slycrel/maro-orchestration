#!/usr/bin/env bash
# maro-ssh-gate.sh — forced-command target for the Hermes dispatch SSH key.
#
# The transport edge is an execution-authority edge (SESSION_PROTOCOL_DESIGN
# §8): the Mini's Hermes brain is an LLM with shell access, so its key gets
# this allowlist instead of a login shell. authorized_keys entry shape:
#
#   command="/home/clawd/claude/maro-orchestration/deploy/hermes/maro-ssh-gate.sh",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... hermes-dispatch@mini2
#
# Allowed (everything else is rejected):
#   ping                      liveness/auth check
#   dispatch <goal text>      enqueue + detached drain → {job_id}
#   status <job_id>           dispatch record / run progress
#   result <job_id>           final run_card JSON
#   list                      recent dispatches
#
# Goal text passes through raw — a goal is code execution by design; Maro's
# containment machinery is the control for goal CONTENT, this gate only
# pins the SURFACE. IDs are strictly validated.
set -euo pipefail

REPO="/home/clawd/claude/maro-orchestration"
DRIVER="$REPO/deploy/hermes/dispatch.py"

cmd="${SSH_ORIGINAL_COMMAND:-}"
if [ -z "$cmd" ]; then
    echo '{"error": "no command — this key is dispatch-only (ping|dispatch|status|result|list)"}' >&2
    exit 2
fi

verb="${cmd%% *}"
rest=""
if [ "$cmd" != "$verb" ]; then
    rest="${cmd#* }"
fi

_check_id() {
    if ! [[ "$1" =~ ^[A-Za-z0-9_-]{4,64}$ ]]; then
        echo '{"error": "bad id"}' >&2
        exit 2
    fi
}

case "$verb" in
    ping)
        exec python3 "$DRIVER" ping ;;
    list)
        exec python3 "$DRIVER" list ;;
    dispatch)
        if [ -z "$rest" ]; then
            echo '{"error": "dispatch needs goal text"}' >&2
            exit 2
        fi
        exec python3 "$DRIVER" enqueue "$rest" ;;
    status|result)
        _check_id "$rest"
        exec python3 "$DRIVER" "$verb" "$rest" ;;
    *)
        echo "{\"error\": \"verb not allowed: $verb\"}" >&2
        exit 2 ;;
esac
