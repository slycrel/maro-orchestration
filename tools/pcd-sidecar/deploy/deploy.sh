#!/usr/bin/env bash
# rsync tools/pcd-sidecar/ to a remote host and (re)install the systemd
# --user service. Assumes the remote already has a venv at ~/pcd/.venv with
# torch/transformers/accelerate/numpy installed (see README.md "Deploy").
#
# Usage: ./deploy/deploy.sh <ssh-host>
set -euo pipefail

HOST="${1:?usage: deploy.sh <ssh-host>}"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== rsyncing sidecar to $HOST:~/pcd/sidecar =="
rsync -az --delete \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.pytest_cache' \
  "$LOCAL_DIR/" "$HOST:~/pcd/sidecar/"

echo "== installing systemd --user unit =="
ssh "$HOST" 'mkdir -p ~/.config/systemd/user'
scp "$LOCAL_DIR/deploy/pcd-sidecar.service" "$HOST:~/.config/systemd/user/pcd-sidecar.service"
ssh "$HOST" '
  loginctl enable-linger "$(whoami)" 2>/dev/null || true
  systemctl --user daemon-reload
  systemctl --user enable --now pcd-sidecar.service
  sleep 1
  systemctl --user status --no-pager pcd-sidecar.service || true
'

echo "== done. try: curl http://$HOST:8765/healthz =="
