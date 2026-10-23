# Followups — dated, tripwired

Deferred items with a due date. `scripts/followups-check.sh` (daily cron,
Telegram on red) fails from the due date until the box is ticked, so "later"
cannot quietly become "never" (Jeremy, 2026-10-23: "my concern is we won't
revisit in a week"). Tick with `[x]` when done and say where it landed.

Line shape the check parses: `- [ ] YYYY-MM-DD  text` (open) / `- [x] …` (done).

- [x] 2026-10-30  Hermes update on mini2 — DONE EARLY 2026-10-23 (Jeremy: "let's update hermes"): v0.18.2 (2ea39dae) → v0.22.x (f3b5b980, 2026-10-23). The 5 vendored patches re-ported to the new layout (BlueBubbles is now `plugins/platforms/bluebubbles/adapter.py`; adapter registry one-line) as ONE idempotent patcher `~/.hermes/patches/apply_all.py`; gateway + both proxies restarted on new code, Telegram + iMessage reconnected, codex/fireworks proxies healthy. codex binary 0.144.5 → 0.164.0. Side-finds: new `hermes pm` wants node 26 which won't load on Monterey (libc++) — pinned to 22.23.4 in pm/lock.json; `session_reset.mode: both` now needs plugin `hermes-session-reset-policy` (installed+enabled). Rollback snapshot: mini2 `~/security-2026-10-23-backups/hermes-pre-update/` + `~/.hermes/backups/pre-update-2026-10-23-140957.zip`.
- [ ] 2026-11-06  Python 3.15 migration on the maro box: install the 53 user-site packages (`~/claude/security-2026-10-23-backups/pip-user-freeze.txt`, names only — the pins `youtube-transcript-api==1.2.4` / `sqlite-vec==0.1.9` declare `<3.15`) into python@3.15's user-site, relink `python3` → 3.15, drop the `~/.local/bin/python3` shim, `scripts/test-safe.sh`, `pipx reinstall-all`, then `brew uninstall python@3.14`. Deferred 2026-10-23: 3.15.0 was three weeks old and pinned deps did not resolve.
- [ ] 2026-11-06  Apple trust store on m6: `/usr/bin/curl` still accepts the forged eMudhra chains until Apple ships; re-probe `https://dv-g3b-emudhra.we-have-a-problem.com` after each macOS update (expect refusal). mini2 (Monterey) never gets this fix — residual accepted 2026-10-23 for the Sectigo-AAA path; emSign-root path closed by `apply-root-deny.sh`.
