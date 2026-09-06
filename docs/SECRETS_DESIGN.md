---
status: living
---

# Secrets — one managed path for every way Maro runs

*Shipped 2026-09-06 (Python + Go). Decree: Jeremy, 2026-09-06 — decision
journal 5870f189 — "secrets management is the fix, not flipping the
container off".*

## 1. Why this exists

The mailbox-access arc (BACKLOG "Mailbox-access arc findings (2026-09-06)")
ended with three runs each reporting, in good faith and with in-container
probes to back it, that *no credential exists anywhere on this box*. Every
claim was true inside the executor container and false on the host: the
container is designed not to mount `secrets/`, config, memory or `~/claude`.
Jeremy's read:

> "I do like that we're running 'secure' dockerized... we probably need a
> way to manage secrets in a meaningful way; I prefer ENV injection in a
> container... a more maro-specific management path for all of the
> different ways it's run, rather than relying on you knowing the secrets
> or having hermes injecting those directly. So that's ultimately the fix
> on this one... rather than flipping that functionality on or off."

and, an hour later:

> "secrets should be both user-injected and maro-derived and we might need
> meta-data on both."

So: the container stays on; Maro gets a secrets store every runtime reads
the same way; secrets flow into executor environments by an operator
policy, never by a worker finding a file; a run can hand back a credential
it obtained; and a worker is always told what exists, so it can never
again mistake its own view for the machine.

## 2. The tool: sops + age

[getsops/sops](https://github.com/getsops/sops) with the
[age](https://github.com/FiloSottile/age) backend. Two single static
binaries, prebuilt for Linux and macOS (this box: `brew install sops age`;
mini2 and the M6 mini: release binaries — the prebuilt-binaries rule),
no server, no account, no cloud dependency.

Why not the alternatives:

- **OpenBao / Infisical / Vault** — a server process with its own unseal,
  auth and uptime story on an always-on box that already has too many
  processes to watch. Nothing in Maro needs dynamic secrets or leases.
- **bare age** — encrypts the whole file: no per-key `--set`, no names
  readable without the key, no in-place edit. sops adds exactly those and
  keeps age as the primitive.
- **pass (gpg)** — one file per secret, gpg agent semantics, no clean
  `dotenv → env` story.

Both engines shell out to the `sops` binary. No library dependency in
Python or Go (`feedback_deps_and_exceptions`): the sops Go module pulls in
every cloud KMS SDK; the CLI contract is small and stable
(exit 128 = no key could open it, exit 100 = no such file).

## 3. Layout — machine-level, engine-neutral

```
~/.maro/secrets/                (MARO_SECRETS_DIR overrides; MARO_USER_DIR moves ~/.maro)
  maro.sops.env                 the store — dotenv, NAMES cleartext, VALUES encrypted
  age-identity.txt              this box's age private key, 0600
  inject                        injection policy: one name or glob per line
  meta.json                     cleartext metadata per name (§5)
```

It sits next to `~/.maro/config.yml`, not inside the Python workspace:
secrets are a property of the machine, and the Go engine (which never
reads the Python engine's workspace, design note §13) reads the same dir.

**Names-cleartext is the load-bearing property.** A sops dotenv file
looks like:

```
YAHOO_APP_PASSWORD=ENC[AES256_GCM,data:…,iv:…,tag:…,type:str]
NVIDIA_API_KEY=ENC[AES256_GCM,data:…,type:str]
sops_age__list_0__map_recipient=age1…
sops_mac=ENC[…]
```

`secrets_store.names()` / Go `secrets.Names()` read that file without
the key. That is what lets a container that must not hold the key still
render the presence index (§6), and what lets the encrypted file travel
through `~/claude/credentials-backup/` or a private repo at rest.

**Multi-box.** One store, many recipients: `maro secrets recipients add
age1…` re-wraps the data key for another box's public key (the identity
never leaves its box). The M6 mini gets its own identity and is added as
a recipient; the file is then the same on every box.

## 4. Lookup chain

`config.load_credentials_env()` — the one seam every backend and
hosted-free provider already used — now returns:

```
process env  >  the store  >  legacy plaintext <workspace>/secrets/.env  >  OpenClaw recovery .env
```

Process env stays the caller's explicit override (`llm._get_key`). The
legacy plaintext keeps serving names the store lacks, so the day the store
appears nothing breaks; `maro secrets check` and `maro doctor` list it as
*plaintext residue* until the operator retires it by hand (data-retention
decree: `migrate` never deletes the source).

## 5. Two origins, one store — metadata

A secret is **operator-entered** (`maro secrets set`, `migrate`) or
**maro-derived** (a run minted an app password by driving a UI, was issued
a token, captured a session cookie). Same store, different record.
`meta.json` is cleartext (names are cleartext anyway; who/when/what-for is
not secret and every runtime must read it without the key):

```json
{"YAHOO_APP_PASSWORD": {"origin": "maro", "created": "2026-09-06T21:02:11Z",
  "updated": "2026-09-06T21:02:11Z", "run": "0bd44fef", "service": "yahoo",
  "source": "drop", "note": "app password minted via account security page"}}
```

`origin` flips to whoever last set the value; `created` survives updates;
`source` is `cli`, `migrate:<path>`, `drop` (§7) or `init`. The presence
index renders it as `YAHOO_APP_PASSWORD (maro-derived by run 0bd44fef,
2026-09-06, yahoo)` so a worker knows a credential's provenance before
using it.

## 6. Injection and the presence index

**Nothing is injected by default.** The `inject` file lists names or
globs (`YAHOO_*`, `NVIDIA_API_KEY`); matching names are placed in an
executor's environment:

| runtime | how the value travels | where the worker reads it |
|---|---|---|
| Python, containerized executor | docker client's env + bare `-e NAME` (never the argv) — the mechanism the hosted-free keys already used | `$NAME` inside the container |
| Python, host lane | a per-step `secrets.env` (0600) in the run scratch, written before the step and shredded after it; the child env carries only `$MARO_SECRETS_FILE` (no run scratch ⇒ merged into the child env, as the container lane does) | `grep '^NAME=' $MARO_SECRETS_FILE` |
| Go engine (`maro-go now/agenda`) | `invoke.HandOff`: the same `secrets.env` (0600) under `<ws>/drop/`, written per tool-bearing call and shredded when it returns; `Subprocess.Env` carries only the two paths | `grep '^NAME=' $MARO_SECRETS_FILE` |
| Hermes dispatch lane | **never** — a dispatch carries a goal, not credentials; the box resolves its own store | — |

**Why a file on the host and env in the container** (Jeremy 2026-09-06):
in docker the container's env *is* the silo — it starts empty, gets what
`-e` names, dies with the container. On a general OS a process env is
inherited by every descendant of the worker (each tool shell, MCP server,
script), is readable from `/proc/*/environ` by the same user, and lands
in whatever a child logs. A 0600 file the child is merely told about is
read on purpose and gone when the step ends — the mirror of the drop
file (§7). The presence block's wording follows the mechanism: "Injected
for this step as NAME=value lines in <path> ($MARO_SECRETS_FILE; mode
0600, shredded when the step ends)" on the host, "Injected into your
environment as variables" in the container.

Every injected value is scrubbed from captured output
(`llm._scrub_secret_values`, already in place) so a goal-driven `env`
never persists one into a transcript. The hosted-free provider keys keep
their own consent gate (`container_exec.hosted_free_container_env`); the
policy adds names, it never removes that gate's.

**The presence index.** Every execute frame — Python
`step_exec.execute_system_for_lane` (both lanes) and the Go `DefaultFrame`
— carries a `## Secrets` paragraph rendered by `presence_block` (Python)
/ `secrets.Presence` (Go) with identical wording:

- the names in the store, each with its metadata summary;
- which are injected into *this* environment;
- which are held on the host and not injected — and, in the container,
  the instruction: *never conclude that no credential exists for these;
  report "exists in the Maro secrets store but is not injected into this
  environment" and name the variable; the operator enables it by adding
  the name to `~/.maro/secrets/inject`*. On the host the same list comes
  with the one-line `sops -d --extract` read recipe (the worker is the
  operator's user; withholding would be theatre);
- where to drop a credential the worker obtains (§7).

This is the direct fix for the three false "no credential anywhere"
claims: the frame tells the worker what it cannot see.

## 7. The drop file — maro-derived secrets

A worker that obtains a credential must not print it. The frame names
`$MARO_SECRETS_DROP`, a path inside the run's scratch dir (host lane:
`<run>/scratch/secrets-derived.env`; container: `/tmp/secrets-derived.env`,
which is the same directory — the run scratch is bound at the container's
`/tmp`). The worker appends `NAME=value` lines there and says in its
result *which name* it dropped and for which service.

After the step (`llm._ingest_secret_drop`, on normal completion and on
the kill path), `secrets_store.ingest_drop` stores each value with
`origin=maro`, `source=drop` and the run handle, then zero-fills and
removes the file. A store that cannot be opened, or a name that fails,
leaves the file in place with a warning — a derived credential is never
lost to a torn hand-off. Without a store, ingest creates one.

## 8. Verbs

```
maro secrets init                 identity + empty store + policy template
maro secrets migrate [--source F] fold a plaintext dotenv in (source kept)
maro secrets list                 names + metadata; * = injectable
maro secrets check [--json]       tools, store, identity, policy, residue
maro secrets get NAME             the one verb that prints a value
maro secrets set NAME --stdin --service S [--note N] [--maro --run R]
maro secrets unset NAME
maro secrets edit                 sops in $EDITOR
maro secrets recipients [age1…]   list / add a box

maro-go secrets list|check|get    the same store, the Go engine's view
```

`maro doctor` carries a "Secrets store" row.

## 9. Threat posture, stated honestly

- At rest: values encrypted to the box's age identity. The identity file
  is the trust root; it is 0600 and stays out of every backup that
  crosses a boundary (the encrypted store may travel; the identity does
  not).
- In a Maro host process: values are decrypted into process memory for a
  run's lookups (cached per store mtime), the same exposure the plaintext
  `.env` had. Owner/root can read `/proc`; same trust domain.
- In a worker: an injected value is in the worker's env by design (the
  decree's accepted exposure), scrubbed from every captured output.
- Not covered: a worker exfiltrating an injected value on purpose. The
  policy file is the operator's lever — inject only what the goal class
  needs.

**Backup convention.** The backup unit is the four files in
`~/.maro/secrets/` (`maro.sops.env`, `meta.json`, `inject`,
`age-identity.txt`). The first three may be copied anywhere — a private
repo, another box, a cloud folder — because nothing in them opens without
the identity. The identity goes wherever the operator keeps keys (a
password manager, an offline copy), and a backup that holds both is
plaintext-equivalent and must stay inside the box's own trust domain. A
second box is better served as a *recipient* (`maro secrets recipients
add`) than as a holder of this box's identity. The store is the source of
truth for account logins as well as API keys; the operator should not keep
a parallel plaintext list once a name is in the store (`maro secrets get
NAME` is the way to read one). On this box the local backup at
`~/claude/credentials-backup/maro/secrets/` holds all four by Jeremy's
choice (same trust domain, never leaves the box); `refresh.sh` there
re-copies them.

## 10. Residuals / next

- **Rotation** is manual (`set` again). A `rotated_after` field in the
  metadata and a `check` warning are the obvious next slice.
- ~~File hand-off on the host lane~~ — SHIPPED 2026-09-06 (§6). Residue:
  the no-scratch fallback still merges values into the env; every real
  run has a scratch dir, so that path is the bare-`_run_subprocess_safe`
  probe's, not a run's.
- **Scoped injection per run** (a goal declaring which names it needs,
  the operator approving once) would replace the box-wide policy file
  when there is evidence a box-wide list is too coarse.
- **The Telegram question loop** (decision 1d1ad8b0: rare exception, not
  the norm) is the other half of the mailbox arc — a 2FA code asked for
  and waited on. Separate design.
