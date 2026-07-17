#!/usr/bin/env bash
# Flip the mc.feifdom.com portal from local-login-only to local + GitHub
# OAuth. Run on the maro box AFTER adding the OAuth app credentials:
#
#   1. Create the OAuth app (browser, ~2 min, only Jeremy can):
#      https://github.com/settings/applications/new
#        Application name: maro viewer
#        Homepage URL:     https://mc.feifdom.com/
#        Callback URL:     https://mc.feifdom.com/auth/oauth2/github/authorization-code-callback
#      Then "Generate a new client secret".
#   2. Append to /etc/caddy/caddy.env (mode 600):
#        GITHUB_CLIENT_ID=<client id>
#        GITHUB_CLIENT_SECRET=<client secret>
#   3. bash deploy/caddy/enable-github-oauth.sh
#
# The script refuses to proceed without credentials, validates before
# touching the live config, and restarts (not reloads) caddy because
# systemd only reads EnvironmentFile at service start.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VARIANT="$REPO/deploy/caddy/Caddyfile.github-oauth"
ENV_FILE="/etc/caddy/caddy.env"
LIVE="/etc/caddy/Caddyfile"

fail() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || fail "$ENV_FILE missing"
set -a; . "$ENV_FILE"; set +a
[ -n "${GITHUB_CLIENT_ID:-}" ] && [ -n "${GITHUB_CLIENT_SECRET:-}" ] \
  || fail "GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET not set in $ENV_FILE — create the OAuth app first (see header of this script)"

/usr/local/bin/caddy validate --config "$VARIANT" --adapter caddyfile >/dev/null 2>&1 \
  || fail "variant failed validation: caddy validate --config $VARIANT"

cp "$LIVE" "${LIVE}.pre-oauth.bak"
cp "$VARIANT" "$LIVE"
sudo systemctl restart caddy
sleep 2
systemctl is-active --quiet caddy \
  || { cp "${LIVE}.pre-oauth.bak" "$LIVE"; sudo systemctl restart caddy; \
       fail "caddy failed to come back — restored previous config"; }

echo "GitHub OAuth enabled. Try 'Login with GitHub' at https://mc.feifdom.com/auth/"
echo "(local webadmin login remains as break-glass; previous config at ${LIVE}.pre-oauth.bak)"
