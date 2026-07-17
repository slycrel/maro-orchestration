# Caddy reverse proxy — public entry for the maro box

`https://mc.feifdom.com/maro/...` → the viz server on `127.0.0.1:8787`,
behind an authentication portal. Set up 2026-07-17.

**Auth posture (Jeremy's decree, 2026-07-17): SSO as the floor,
implementation swappable.** The caddy-security plugin provides an auth
portal that issues JWTs and an authorization policy that gates `/maro`.
The identity provider *behind* the portal is a config block: it starts as
a local user store (zero external dependencies) and upgrades to GitHub
OAuth without touching the `/maro` route or its policy. Flat-open "we'll
add auth later" was explicitly rejected.

## Pieces

| What | Where | In git? |
|---|---|---|
| Caddy binary (custom build, caddy-security baked in) | `/usr/local/bin/caddy` | no — see rebuild below |
| Caddyfile | `/etc/caddy/Caddyfile` (source of truth: `deploy/caddy/Caddyfile`) | yes |
| systemd unit | `/etc/systemd/system/caddy.service` (source: `deploy/caddy/caddy.service`) | yes |
| JWT signing key | `/etc/caddy/caddy.env` (mode 600) | **never** |
| Local user db | `/etc/caddy/auth/users.json` (mode 600, plugin-managed) | **never** |
| ACME certs/state | `~clawd/.local/share/caddy/` | never |

Binary rebuild/upgrade (the plugin is baked in at build time, apt caddy
won't have it):

```bash
curl -sSL -o /tmp/caddy "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com%2Fgreenpau%2Fcaddy-security"
chmod +x /tmp/caddy && /tmp/caddy list-modules | grep -q '^security$' && sudo mv /tmp/caddy /usr/local/bin/caddy
sudo systemctl restart caddy
```

## Install / update config

```bash
sudo cp deploy/caddy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy        # validate first: caddy validate --config /etc/caddy/Caddyfile
sudo cp deploy/caddy/caddy.service /etc/systemd/system/caddy.service
sudo systemctl daemon-reload && sudo systemctl enable --now caddy
```

## First login (bootstrap credentials)

The plugin auto-creates a `webadmin` user (role `authp/admin`) on first
start — and in the installed version its generated password is logged
NOWHERE (the journal line has username/email/roles only). The working
bootstrap/reset procedure is to set the password hash directly
(2026-07-17, verified by bcrypt round-trip):

```bash
umask 077
openssl rand -base64 18 > /etc/caddy/auth/bootstrap-password.txt
hash=$(caddy hash-password --plaintext "$(cat /etc/caddy/auth/bootstrap-password.txt)")
python3 -c "
import json, sys
path = '/etc/caddy/auth/users.json'
d = json.load(open(path))
d['users'][0]['passwords'][0]['hash'] = sys.argv[1]
json.dump(d, open(path, 'w'), indent=2)" "$hash"
sudo systemctl restart caddy
```

Read the credential with `sudo cat /etc/caddy/auth/bootstrap-password.txt`,
log in at `https://mc.feifdom.com/auth/` as `webadmin`, store the
password in a password manager, then delete the file.

**There is no in-portal password change**: this plugin build hard-404s
`/auth/settings` (probed 2026-07-17 — the authcrunch portal refactor
dropped the self-service pages; `/auth/whoami` and `/auth/portal` exist,
`/auth/settings*` does not). To change the password, re-run the
hash-reset above with a plaintext of your choosing.

Note: the auth flow cannot be exercised against the loopback test
listener — the portal's `cookie domain mc.feifdom.com` makes session
cookies invalid on `127.0.0.1`, so sandbox state never sticks there.
Test logins on the real domain.

## Go-live checklist (router side — Jeremy)

DNS is already right (`mc.feifdom.com` → home ISP IP). Remaining:

1. Router port-forwards to this box (`192.168.0.45`):
   - external **80** → `192.168.0.45:80` (ACME HTTP-01 challenge)
   - external **443** → `192.168.0.45:443`
2. Nothing else — Caddy is already running and retrying cert issuance;
   within a minute of the forwards existing, `https://mc.feifdom.com/auth`
   serves the portal with a real certificate. Check with:
   `sudo journalctl -u caddy -f | grep -iE "certificate|acme"`.
3. **After confirming the portal loads from off-LAN**, flip the message
   links: in `~/.maro/config.yml` set
   `notify.viewer_url: "https://mc.feifdom.com/maro"`. Don't flip before —
   dead links in completion messages are worse than LAN-only links.

## Upgrade path: GitHub OAuth (the SSO this was sized for)

1. GitHub → Settings → Developer settings → OAuth Apps → New. Homepage
   `https://mc.feifdom.com/`, callback
   `https://mc.feifdom.com/auth/oauth2/github/authorization-code-callback`
   (verify the exact callback path against authcrunch.com docs for the
   installed plugin version when doing this).
2. Append the client id/secret to `/etc/caddy/caddy.env` as
   `GITHUB_CLIENT_ID=` / `GITHUB_CLIENT_SECRET=`.
3. In the Caddyfile `security` block add the provider and enable it in
   the portal:

   ```
   oauth identity provider github {
       realm github
       driver github
       client_id {env.GITHUB_CLIENT_ID}
       client_secret {env.GITHUB_CLIENT_SECRET}
       scopes user
   }
   ```

   ```
   authentication portal maro_portal {
       ...
       enable identity provider github
       transform user {
           match realm github
           match sub github.com/slycrel
           action add role authp/user
       }
   }
   ```

   The `transform user` match is the allow-list — without it any GitHub
   account could log in and get no roles (denied by policy), but pinning
   `slycrel` to `authp/user` is the explicit intent. `/maro`'s
   authorization policy is untouched.
4. `sudo systemctl reload caddy`. Optionally remove the local store once
   GitHub login is proven (keep it as break-glass until then).

## Shape notes

- The `/maro` prefix is stripped before proxying (`uri strip_prefix`), so
  the viz server is prefix-blind. Loop reports use relative links
  (verified 2026-07-17) so they survive subpath serving; if a future
  index page uses absolute paths it will break behind `/maro` — fix it
  there, not here.
- The viz server's own allowlist (build/** + artifact/ prose files,
  default-deny) still applies behind the proxy — auth is a second layer,
  not a replacement.
- A dedicated subdomain (`maro.feifdom.com`) instead of the path is one
  extra site block + a Namecheap DNS record — nothing here assumes the
  path shape except `viewer_url`.
- Local wiring check without touching the domain:
  `curl -s http://127.0.0.1:8880/auth` (same routes, loopback-only site).
