#!/usr/bin/env bash
# local-validator.sh — optional, framework-managed local validator runtime.
#
# Stands up a zero-cost local model that Poe uses as the first-pass step/quality
# validator (see src/local_models.py). Installing this is OPTIONAL: if it's not
# running, validation falls back to the paid path automatically.
#
# Apple Silicon -> MLX (mlx_lm.server, in a uv-managed venv decoupled from the
# system Python). On Linux, use Ollama instead: `ollama pull <model>` and point
# `validate.runtime: ollama` at http://127.0.0.1:11434/v1. With `validate.autostart`
# on, the orchestration spins ollama up itself under a CPU cap and reaps it — no
# script needed. If you instead start it by hand to keep it warm across many runs,
# cap it the same way so it can't starve the box (the orchestration only manages
# what it launches):
#   OLLAMA_KEEP_ALIVE=30s OLLAMA_NUM_PARALLEL=1 nice -n 12 taskset -c 2,3 ollama serve
#
# Usage:
#   scripts/local-validator.sh setup            # create venv + install mlx-lm
#   scripts/local-validator.sh pull  [MODEL]     # download + warm the model
#   scripts/local-validator.sh start [MODEL] [PORT]
#   scripts/local-validator.sh status
#   scripts/local-validator.sh stop
#
# Defaults: MODEL=mlx-community/VibeThinker-3B-8bit  PORT=8088
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv-mlx"
PY="$VENV/bin/python"
PIDFILE="${TMPDIR:-/tmp}/poe-local-validator.pid"
LOGFILE="${TMPDIR:-/tmp}/poe-local-validator.log"

MODEL="${2:-${LOCAL_VALIDATOR_MODEL:-mlx-community/VibeThinker-3B-8bit}}"
PORT="${3:-${LOCAL_VALIDATOR_PORT:-8088}}"

need_uv() { command -v uv >/dev/null 2>&1 || { echo "error: 'uv' not found — install from https://docs.astral.sh/uv/"; exit 1; }; }

cmd_setup() {
  need_uv
  if [ ! -x "$PY" ]; then
    echo "[setup] creating venv ($VENV) with python 3.12"
    uv venv --python 3.12 "$VENV"
  fi
  echo "[setup] installing mlx-lm"
  uv pip install --python "$PY" mlx-lm
  echo "[setup] done. Next: scripts/local-validator.sh start"
}

cmd_pull() {
  [ -x "$PY" ] || { echo "run 'setup' first"; exit 1; }
  echo "[pull] warming $MODEL (downloads on first run)"
  "$PY" -m mlx_lm generate --model "$MODEL" --prompt "OK" --max-tokens 1 >/dev/null
  echo "[pull] cached."
}

cmd_start() {
  [ -x "$PY" ] || { echo "run 'setup' first"; exit 1; }
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "[start] already running (pid $(cat "$PIDFILE")) on :$PORT"; exit 0
  fi
  echo "[start] mlx_lm.server model=$MODEL port=$PORT -> $LOGFILE"
  nohup "$PY" -m mlx_lm server --model "$MODEL" --port "$PORT" >"$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 2
  echo "[start] pid $(cat "$PIDFILE"). Probe: curl -s http://127.0.0.1:$PORT/v1/models"
  echo "[start] set in config.yml:  validate.local_models: [\"$MODEL\"]  validate.endpoint: http://127.0.0.1:$PORT/v1"
}

cmd_status() {
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "running (pid $(cat "$PIDFILE"))"
  else
    echo "not running (via this script)"
  fi
  curl -s -m 3 "http://127.0.0.1:$PORT/v1/models" 2>/dev/null \
    | "$PY" -c "import sys,json;print('loaded:',[m['id'] for m in json.load(sys.stdin).get('data',[])])" 2>/dev/null \
    || echo "endpoint :$PORT unreachable"
}

cmd_stop() {
  if [ -f "$PIDFILE" ]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null && echo "[stop] stopped pid $(cat "$PIDFILE")" || echo "[stop] not running"
    rm -f "$PIDFILE"
  else
    echo "[stop] no pidfile"
  fi
}

# NOTE: there is intentionally no install-as-OS-service command. The orchestration
# owns the validator's lifecycle — it spins the model up on demand and reaps it
# after idle (see local_models.ensure_validator_running / the idle reaper). These
# manual commands are for dev use: warming the model, or keeping it up across many
# back-to-back runs so you don't pay the load each time.

case "${1:-}" in
  setup)  cmd_setup ;;
  pull)   cmd_pull ;;
  start)  cmd_start ;;
  status) cmd_status ;;
  stop)   cmd_stop ;;
  *) echo "usage: $0 {setup|pull|start|status|stop} [MODEL] [PORT]"; exit 1 ;;
esac
