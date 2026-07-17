# Caddy reverse proxy — public entry for the maro box

`https://maro.feifdom.com/...` → the viz server on `127.0.0.1:8787`,
behind an authentication portal at `/auth`. Set up 2026-07-17.

**Auth posture (Jeremy's decree, 2026-07-17): SSO as the floor,
implementation swappable.** The caddy-security plugin provides an auth
portal that issues JWTs and an authorization policy that gates the site.
The identity provider *behind* the portal is a config block: local user
store (break-glass) + GitHub OAuth (primary, pinned to `slycrel`). Flat-
open "we'll add auth later" was explicitly rejected — but so was
high-friction auth: if this stack generates friction, the sanctioned
retreat is TLS-only read-only (see Fallback posture).

**Serving shape (2026-07-17):** dedicated subdomain, viz at the root —
no path prefix, no `uri strip_prefix`. (The original path shape on
another hostname was dropped same-day — that host belongs to the kids'
Minecraft server; links sent before the swap are dead, accepted.)

## Pieces

| What | Where | In git? |
|---|---|---|
| Caddy binary (custom build, caddy-security baked in) | `/usr/local/bin/caddy` | no — see rebuild below |
| Caddyfile (live = OAuth variant) | `/etc/caddy/Caddyfile` (source: `deploy/caddy/Caddyfile.github-oauth`) | yes |
| Local-only fallback Caddyfile | `deploy/caddy/Caddyfile` | yes |
| systemd unit | `/etc/systemd/system/caddy.service` (source: `deploy/caddy/caddy.service`) | yes |
| JWT signing key + GitHub OAuth creds | `/etc/caddy/caddy.env` (mode 600) | **never** |
| Local user db | `/etc/caddy/auth/users.json` (mode 600, plugin-managed) | **never** |
| ACME certs/state | `~clawd/.local/share/caddy/` | never |

Secrets backup: `~/claude/credentials-backup/caddy/` holds copies of
`caddy.env`, `users.json`, and the bootstrap password (manual snapshot —
re-copy on rotation; never commit).

Binary rebuild/upgrade (the plugin is baked in at build time, apt caddy
won't have it):

```bash
curl -sSL -o /tmp/caddy "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com%2Fgreenpau%2Fcaddy-security"
chmod +x /tmp/caddy && /tmp/caddy list-modules | grep -q '^security$' && sudo mv /tmp/caddy /usr/local/bin/caddy
sudo systemctl restart caddy
```

## Install / update config

```bash
sudo cp deploy/caddy/Caddyfile.github-oauth /etc/caddy/Caddyfile
sudo systemctl restart caddy   # restart, not reload — EnvironmentFile is start-time-only
sudo cp deploy/caddy/caddy.service /etc/systemd/system/caddy.service
sudo systemctl daemon-reload && sudo systemctl enable --now caddy
```

Validate first: `set -a; . /etc/caddy/caddy.env; set +a; caddy validate
--config deploy/caddy/Caddyfile.github-oauth --adapter caddyfile`.

## GitHub OAuth (primary login)

Enabled 2026-07-17 via `enable-github-oauth.sh`. The OAuth app lives
under the `slycrel` GitHub account ("maro viewer", client ID
`Ov23li63QjwAWcrpZI0s`); the client secret is in `/etc/caddy/caddy.env`.
Only `github.com/slycrel` is granted a role — any other GitHub login
authenticates but holds no role and is denied (default-deny holds).

The OAuth app's **Authorization callback URL** must be
`https://maro.feifdom.com/auth/oauth2/github/authorization-code-callback`.
If GitHub shows a `redirect_uri` mismatch on login, the app was created
with a different callback — edit it at github.com → Settings →
Developer settings → OAuth Apps.

Secret rotation: generate a new client secret on the OAuth app page,
replace `GITHUB_CLIENT_SECRET` in `/etc/caddy/caddy.env`, then
`sudo systemctl restart caddy`.

### Adding another person

Access is per-GitHub-account, in config — no database, no GitHub-side
change. Each allowed account is one `transform user` block in
`Caddyfile.github-oauth`; copy the existing one and change the username:

```
transform user {
	match realm github
	match sub github.com/<their-github-username>
	action add role authp/user
}
```

Then install: `sudo cp deploy/caddy/Caddyfile.github-oauth
/etc/caddy/Caddyfile && sudo systemctl restart caddy`. They log in with
their normal GitHub account. Any GitHub account can *authenticate*
against the OAuth app; only accounts with a matching block get a role,
and no role = denied (default-deny). Removal = delete the block,
reinstall. (Passworded local accounts via `users.json` are also
possible, but GitHub is the intended path.)

## Local login (break-glass)

`webadmin` in the local identity store remains as break-glass if GitHub
is unreachable. Its password is in the credentials backup
(`~/claude/credentials-backup/caddy/bootstrap-password.txt`).

**There is no in-portal password change**: this plugin build hard-404s
`/auth/settings` (probed 2026-07-17 — the authcrunch portal refactor
dropped the self-service pages). To reset the password, set the hash
directly (verified by bcrypt round-trip):

```bash
umask 077
openssl rand -base64 18 > /tmp/new-pw.txt    # or write a chosen password
hash=$(caddy hash-password --plaintext "$(cat /tmp/new-pw.txt)")
python3 -c "
import json, sys
path = '/etc/caddy/auth/users.json'
d = json.load(open(path))
d['users'][0]['passwords'][0]['hash'] = sys.argv[1]
json.dump(d, open(path, 'w'), indent=2)" "$hash"
sudo systemctl restart caddy
# then move /tmp/new-pw.txt into the credentials backup and delete it
```

The plugin auto-creates `webadmin` (role `authp/admin`) on first start,
and in the installed version its generated password is logged NOWHERE
(the journal line has username/email/roles only) — the hash-reset above
is the only way in after a fresh `users.json`.

Note: the auth flow cannot be exercised against the loopback test
listener — the portal's `cookie domain maro.feifdom.com` makes session
cookies invalid on `127.0.0.1`, so sandbox state never sticks there.
Test logins on the real domain.

## Fallback posture (Jeremy 2026-07-17)

"Still a good floor if we can get it going well. If not, we can
probably be read-only pages with no auth." If the auth stack keeps
generating friction, the sanctioned retreat is: viewer stays read-only
(it already is — GET-only allowlist, no mutations), drop the portal,
keep TLS. That retreat is a config deletion, not a rebuild: remove the
`security` block + `authorize`/`authenticate` directives from the
Caddyfile.

## Shape notes

- The viz server serves at the subdomain root — no prefix stripping.
  Loop reports use relative links (verified 2026-07-17) so they'd also
  survive subpath serving, but nothing depends on that anymore.
- The viz server's own allowlist (index.html at root, build/** +
  artifact/ prose files, default-deny) still applies behind the proxy —
  auth is a second layer, not a replacement.
- DNS: Namecheap A-record → home ISP IP; router forwards 80
  (ACME HTTP-01) + 443 → this box (`192.168.0.45`).
- Message links come from `notify.viewer_url` in `~/.maro/config.yml`
  (`https://maro.feifdom.com`).
- Local wiring check without touching the domain:
  `curl -s http://127.0.0.1:8880/auth` (same routes, loopback-only site).
