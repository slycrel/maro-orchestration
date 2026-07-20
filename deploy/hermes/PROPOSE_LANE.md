# Hermes Propose Lane

**Policy (Jeremy, 2026-07-20):** Hermes proposes, this box disposes. The
trust boundary is **merge to main**, not push. Hermes never holds GitHub
credentials — mini2's maro clone uses the https remote, which can fetch
but has no way to push. Pushing a `hermes/*` branch is harmless by
construction (nothing runs off it); merging to main is the act with blast
radius, and that gate lives on this box.

- **Docs-only proposals** (every touched file `*.md`) fast-forward to
  main automatically — a backlog note shouldn't need babysitting.
- **Anything touching code** stops as a pushed `hermes/*` branch with a
  PR-create URL, awaiting human review. This is deliberate: an autonomous
  agent must not modify the orchestration that governs it without a human
  in the loop.

Born from a real failure: Hermes committed backlog work into an ephemeral
`/tmp` shallow clone it couldn't push from, and the commit survived only
because `/tmp` hadn't been reaped (recovered as `1d89191`, 2026-07-20).

## Mechanics

```
mini2 (Hermes)                              maro box (this repo)
~/.hermes/repos/maro-orchestration          deploy/hermes/land.sh
  commit on hermes/<topic>          land →    fetch branch from mini2 clone
~/.hermes/bin/maro-propose        ─────→     push to GitHub as hermes/<topic>
  start / send / status / sync    ssh gate    docs-only + ff-able → push main
                                             else → PR URL in JSON receipt
```

- **mini2:** persistent clone at `~/.hermes/repos/maro-orchestration`
  (https remote — fetch-only by construction). Helper
  `~/.hermes/bin/maro-propose`; contract documented for Hermes in
  `~/.hermes/skills/orchestration/maro-propose/SKILL.md`. Never `/tmp`
  clones, never commits on main.
- **Transport:** the existing dispatch rail — mini2's forced-command SSH
  key (`maro-ssh-gate.sh`), extended with a `land hermes/<topic>` verb.
  No new keys, no new listeners.
- **This box:** `land.sh` fetches the branch straight from mini2's clone
  over SSH (refs only — it never touches this checkout's working tree, so
  it is safe alongside concurrent sessions) and pushes to GitHub. Plain
  push to main means GitHub itself rejects anything that isn't a
  fast-forward; main is never force-pushed. `hermes/*` refs may be
  force-pushed on re-proposal — that namespace is proposals, not history.
- **Receipt:** `land` returns one JSON object (status, sha, files,
  pr_url, human-readable note). Hermes relays the outcome to Jeremy in
  plain words — the delivery loop ends at Jeremy, not at a log file.

## Ops notes

- Landed-main pushes move `origin/main` ahead of this checkout's local
  `main`; the next `git pull` here catches up — same as any push from
  another machine.
- Proposal branches on GitHub are not auto-deleted; clean up merged
  `hermes/*` branches manually when they accumulate.
- If mini2's clone falls behind, `maro-propose start <topic>` always
  branches from freshly-fetched `origin/main`, so stale-base proposals
  are self-healing on the next attempt; `land.sh` reports (not merges)
  stale-base docs proposals.

**Verified end-to-end 2026-07-20:** this line was committed on mini2 by the lane itself and landed on main via `maro-propose send` → `land`.
