---
status: living
---

# Migration — moving a Maro workspace to a new machine

Covers both directions: the box died and you're restoring onto a blank
machine, and consolidating a second install's learning into an existing one.
Design rationale: `docs/PORTABLE_LEARNING_DESIGN.md` §5.

## Case A — empty new machine (the HDD-died case)

All learning (runs, memory, skills, config) lives under `~/.maro/` — no
external database, nothing else to bring over.

```bash
# old machine (or from your last backup)
old$ tar czf maro-backup.tgz -C ~ .maro

# new machine
new$ pip install maro-orchestration
new$ tar xzf maro-backup.tgz -C ~
new$ maro-doctor
```

What just worked, and why:
- `index.db` needs nothing: on first open the store detects the copied event
  log and catches up or rebuilds from `memory_events.jsonl`
  (`memory_sqlite.py`) — this happens automatically the moment anything
  touches memory, including `maro-doctor` itself. If paranoid, delete
  `memory/module/index.db` before first run; rebuild is the designed path,
  not a fallback.
- Shipped skills/personas come from the *new* install's packaged defaults;
  workspace overrides in the tarball keep winning by the normal resolution
  order (workspace → repo). Upgrading Maro during migration therefore
  refreshes defaults without touching anything you evolved.
- Config moves verbatim — both `~/.maro/config.yml` and
  `~/.maro/workspace/config.yml` restore as-is.

**Before you start using the new box, run `maro-doctor` and read the three
rows it reports for exactly this situation:**

1. **Config paths on this box** — absolute paths that were valid on the old
   machine (extra mount points, a hand-set `notify.command` binary, etc.)
   may not exist here. Doctor lists any that resolve to nothing; fix
   `config.yml` before relying on them.
2. **Stale machine state** — `jobs.json`, `heartbeat-state.json`, any
   `*.lock` files, and `telegram_offset.txt` all describe *this machine's*
   in-flight state, not learning. If they traveled in the tarball, **delete
   them** — schedules and heartbeats must be re-armed intentionally on the
   new box, never auto-revived by a restore. A backup that silently
   resurrects a heartbeat is a self-rearming loop with extra steps, which
   Maro deliberately never does (see the "good system citizen" invariant —
   off switches stay off).
   ```bash
   rm -f ~/.maro/workspace/memory/jobs.json \
         ~/.maro/workspace/memory/heartbeat-state.json \
         ~/.maro/workspace/telegram_offset.txt
   find ~/.maro/workspace -name '*.lock' -delete
   ```
3. **Memory index sync** — reports whether the sqlite index caught up to the
   restored event log cleanly, or had to fully rebuild (both are fine;
   rebuild just means the copy's index metadata didn't match — the log is
   always the source of truth).

None of these three rows fail the doctor run — they're informational, since
a normal *running* box legitimately has jobs queued and a live heartbeat.
Read them once, right after a restore, before you re-arm anything.

## Case B — merging into an existing workspace

This is `maro-import` (already shipped, proven on the Hermes-in-Docker
trial 2026-07-09):

```bash
maro-import --source /path/to/other/workspace --label docker-trial
```

- `runs/<id>/` copy-if-absent, each gets an `imported_from.json` marker.
- `memory/**/*.jsonl` ledgers merge with exact-line dedup under file locks
  — idempotent, re-running is a no-op.
- `memory/<date>.md` daily logs append once under a provenance heading.
- Curated state (`MEMORY.md`, `playbook.md`, `skills/`, `personas/`) never
  merges into live files automatically — pass `--include-curated` to copy
  it into `imports/<label>/` for manual (or evolver) review. Curated
  artifacts stop at quarantine until reviewed and adopted; `maro-pack`
  (§3, §7 of the design doc) is the eventual one-command path for both
  import styles, not shipped yet.
- Machine state (`config.yml`, `jobs.json`, task store, heartbeat, secrets,
  locks, `correspondence.db`) is never touched by `maro-import` — it only
  moves learning, not process state.

Same-owner merges (this is you, consolidating your own boxes) carry no
trust demotion — `maro-import` is trust-neutral by design. A future
`maro-pack import` (not yet shipped) is the trust-demoting path for
someone else's pack.

## Not yet built

- `maro-pack export/seal/import/adopt` — the curated-learning sharing
  lifecycle (trust demotion, provenance, scrub, quarantine → adopt). See
  `docs/PORTABLE_LEARNING_DESIGN.md` §2b–§4, §7 chunks 2–4.
- Signing/identity, richer identifier scrub, imported-skill A/B before
  adoption — explicitly deferred post-1.0 (§7.5 of the design doc).
