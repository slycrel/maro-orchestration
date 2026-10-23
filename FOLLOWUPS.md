# Followups — dated, tripwired

Deferred items with a due date. `scripts/followups-check.sh` (daily cron,
Telegram on red) fails from the due date until the box is ticked, so "later"
cannot quietly become "never" (Jeremy, 2026-10-23: "my concern is we won't
revisit in a week"). Tick with `[x]` when done and say where it landed.

Line shape the check parses: `- [ ] YYYY-MM-DD  text` (open) / `- [x] …` (done).

- [ ] 2026-10-30  Hermes update on mini2: v0.18.2 (2026-07-16 git install) → upstream v0.22.x (gateway/auth fixes, catalog security pins). Rollback = `git checkout 2ea39dae` + venv rebuild. Re-apply the 7 vendored patches in `~/.hermes/patches/` (3 bluebubbles + codex/fireworks proxy adapters), then `hermes gateway restart` + kickstart both proxies; verify Telegram + iMessage round-trips. Also swap the prebuilt codex binary (0.144.5 → current) — prebuilt-binaries-not-brew rule on Monterey.
- [ ] 2026-11-06  Python 3.15 migration on the maro box: install the 53 user-site packages (`~/claude/security-2026-10-23-backups/pip-user-freeze.txt`, names only — the pins `youtube-transcript-api==1.2.4` / `sqlite-vec==0.1.9` declare `<3.15`) into python@3.15's user-site, relink `python3` → 3.15, drop the `~/.local/bin/python3` shim, `scripts/test-safe.sh`, `pipx reinstall-all`, then `brew uninstall python@3.14`. Deferred 2026-10-23: 3.15.0 was three weeks old and pinned deps did not resolve.
- [ ] 2026-11-06  Apple trust store on m6: `/usr/bin/curl` still accepts the forged eMudhra chains until Apple ships; re-probe `https://dv-g3b-emudhra.we-have-a-problem.com` after each macOS update (expect refusal). mini2 (Monterey) never gets this fix — residual accepted 2026-10-23 for the Sectigo-AAA path; emSign-root path closed by `apply-root-deny.sh`.
