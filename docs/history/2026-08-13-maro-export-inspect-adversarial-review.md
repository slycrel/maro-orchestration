---
status: record
---

# Adversarial review — maro-export `inspect` subcommand (977f58f)

*2026-08-13, 2-lens Codex review (Skeptic + Architect), defensive framing per
the skill's 2026-08-13 update — no cyber-filter trips (the deferred
cross-model pass the BACKLOG entry promised). Reviewer CLI: `codex exec`,
read-only sandbox. Both output files present.*

## Intent

`maro-export inspect ARCHIVE` — read-only look-before-you-import: prints
provenance + custody, verifies the workspace-shape digest against the
archive's own bytes, previews what import WOULD skip (unsafe member
types/links, secret-shaped meta, traversal) — nothing extracted or mutated.
Exit 0 clean / 2 digest MISMATCH / 3 unsupported-newer-format. Must uphold
the v2 import-hardening posture (archive = UNTRUSTED input) while being safe
to point at arbitrary archives.

## Verdict: REJECT

Both lenses independently converged on the same three ship-blockers: the
exit-code trust gate fails open (unsafe content still exits 0 "clean"),
inspect re-implements import's screening policy and the two already disagree
on concrete probes, and the resource caps apply only after the whole archive
has been scanned into memory. For a tool whose entire purpose is "trust
decision before import", the first two defeat the purpose.

## Findings (deduped, severity-ordered)

1. **[high] Exit 0 does not mean clean — the trust gate fails open.** (both)
   `rc` is set solely from digest/format (maro_export.py:1053); the
   unsafe-member and secret-meta assessment never affects it. An archive with
   an absolute symlink prints "import would SKIP 1 unsafe member" and exits
   0. Unreadable/oversized provenance collapses to `None`, relabeled
   "v1 (no meta/provenance)", exit 0. Non-int `format` versions ("99", 3.0)
   bypass the exit-3 gate. The shipped test captures `rc` without asserting
   it. → Fix: typed assessment verdict {clean / absent-meta / unsafe /
   malformed / mismatch / unsupported}; only clean exits 0; new documented
   exit code for unsafe/malformed; tests assert rc.

2. **[high] Inspect and import are separate policy implementations that
   already contradict each other.** (both) `_member_import_risk` omits
   destination containment and `_should_exclude`; meta inspection counts only
   secret-shaped regular files, missing meta traversal, links, specials, and
   the size cap `_stage_meta` enforces; provenance selection differs
   (oversized-first-then-duplicate vs stop-at-first). Reviewer probes:
   `meta/../escaped.txt` + absolute meta symlink + FIFO → inspect reported
   zero unsafe; `workspace/.env` omitted from both counts.
   → Fix: one shared classifier consumed by both inspect and import;
   destination-dependent checks labeled explicitly in inspect output.

3. **[high] Resource caps apply after the expensive work.** (both)
   `tar.getmembers()` fully materializes the member list before
   `_MAX_MEMBERS` is checked; the `m not in meta` partition is quadratic
   (identity-list search). 100k empty members in a 713KB archive → ~66MB RSS
   before any rejection. → Fix: one-pass streaming scan; enforce member-count
   and name-byte budgets as members stream; partition by name predicate in
   the same pass; bound retained report samples.

4. **[medium] Archive-controlled terminal injection.** (both) `format`, meta
   counters, and symlink-target reasons print unsanitized; reviewer
   reproduced ANSI clearing + forged output lines through three fields.
   → Fix: route every archive-authored value through one terminal-safe
   renderer; risks become {code, detail} with detail sanitized + capped.

5. **[medium] No binding between inspected bytes and imported bytes.**
   (Skeptic) inspect and import reopen the pathname independently; a
   concurrent writer swaps the file between commands. → Fix: inspect prints
   the archive's sha256; import gains `--expect-sha256` to pin it.

## What went well

- Strictly read-only held: no extraction, no writes, no tempfiles — both
  lenses probed for side effects and found none.
- The digest verification itself (recomputing the workspace-shape digest from
  archive bytes rather than trusting the recorded value) is the right trust
  direction and survived review.
- Scriptable exit codes were the right instinct; the fix widens the
  vocabulary rather than reworking the interface.

## Lead judgment

Accept all five. 1-3 are the ship-blockers and land together (the shared
classifier is what makes the exit-code gate honest); 4 rides the same
rendering pass; 5 is two small additions. The Architect's full
`ArchiveAssessment` dataclass is adopted in spirit — one classifier, typed
result — but kept as plain dict/tuple shapes consistent with the script's
existing style rather than a new type hierarchy.
