---
status: living
---

# Path portability — export-side placeholders instead of import-side rewriting

**Status: SHIPPED 2026-08-18** (`src/path_tokens.py`, export/import wiring in
`scripts/maro_export.py`). The build order at the bottom is done; what follows
describes live behaviour.

## The problem, measured

An exported workspace carries absolute paths from the source machine. Import
(`src/path_rewrite.py`) makes them true locally by substituting the three
recorded roots — 62,116 substitutions across 4,787 files on the 2026-08-16
import. Two things are wrong with that shape:

1. **It rewrites historical records in place.** This is the only place in the
   codebase that deliberately does so. The whole 2026-08 byte-safety arc went
   the other way: preserve torn lines, refuse to launder tainted rows, announce
   losses rather than smooth them. The import boundary survived that arc
   because nobody was looking at it.
2. **It is incomplete and silent.** Measured on the current box copy: **2,029
   files still embed `/home/clawd`, 25,278 occurrences.** The import announces
   which *roles* it could not map; nothing counts the *paths* it left alone.

Residual, by prefix:

| occurrences | prefix | verdict |
|---|---|---|
| 6,150 | `/home/clawd/claude/openclaw-orchestration` | **mappable** — the pre-rename repo root; stale on both machines |
| 5,202 | `/home/clawd/.openclaw/workspace` | foreign — no local counterpart |
| 3,296 | `/home/clawd/.claude/projects` | foreign |
| 2,376 | `/home/clawd/claude/telegram-export` | foreign |
| 2,246 | `/home/clawd/.poe/workspace` | foreign |
| 801 | `/home/clawd/claude/maro-wt-round4` | **a scratch clone** — see roots below |

## The decision

Jeremy, 2026-08-18: *"on export we do some sort of replaceable like
`$MARO_ROOT/` instead, and export the root path that was there prior. Then it's
'correct' going out and not 'painful' going back in."*

**Substitute at export, expand at import.** A path stays a path — a readable,
greppable string with a variable prefix — rather than becoming a structured
payload at every one of the ~24 sites that persist one. The archive becomes
machine-neutral: every importer does the same trivial expansion, and no
importer has to reason about a foreign machine's roots.

Rejected alternatives, and why:

- **Rooted URIs / `{root, rel}` objects.** Most rigorous, but it payload-ifies
  every path and changes every writer and reader. The gain over a placeholder
  prefix is small once the roots are named.
- **A residual census on import.** Reports the problem rather than fixing it.
  Buys a number, not portability.

## Roots

The docker question — *do container paths need their own namespace?* — is
answered by the mount contract, not by policy. `container_exec.build_run_command`
binds each mount **at the same absolute path inside the container** so `-w` and
the worker's relative writes resolve to the host dir. Container and host paths
are therefore the *same string* by construction, and a partly-containerized run
needs no special handling.

Two exceptions, both bounded:

| root | placeholder | note |
|---|---|---|
| workspace | `$MARO_WORKSPACE/` | `~/.maro/workspace` |
| maro user dir | `$MARO_HOME/` | `~/.maro` |
| repo | `$MARO_REPO/` | must also match the **pre-rename** name — 6,150 occurrences point at `openclaw-orchestration`, a directory that no longer exists on either machine |
| run scratch | `$MARO_SCRATCH/` | the one mount that is **not** identity-mapped: host `scratch_dir` binds to container `/tmp` |
| self-dev clone | `$MARO_CLONE/` | a distinct host path (`maro-wt-*`); the live repo is hard-excluded from the mount set, so a self-dev run's `$MARO_REPO` is *not* the live repo |

Anything outside these stays absolute. That is correct — `.openclaw`, `.poe`
and `go/` have no local counterpart, and inventing one would be a lie. The
benefit is that "paths with no local meaning" becomes a small explicit set
instead of something found by grep.

## The design line: owned vs observed

This distinction does not exist in the code today and is the thing most likely
to be got wrong in both directions.

**Owned** — a path *we* wrote, referring to *our* data. Substitute these.
Candidate sites from a first census:

- `run_curation.py` — `result_path`, deliverable and omitted-artifact entries
- `dispatch_envelope.py` — stored artifact paths
- `memory_jsonl.py` / `memory_sqlite.py` — store self-paths
- `delta_replay.py` — `call_path`
- `loop_artifacts` / `runs` — `call_record`, artifact and manifest paths
- `orch_bridges.py` — source directory, worker command path

**Observed** — a path the system *saw*, where the absolute string is the
finding. **Never substitute.** Doing so would destroy the evidence:

- `artifact_check.py` scavenge reports (reads/writes outside the fence)
- write-fence violations (`loop_execute` — `wrote N path(s) outside the fence`)
- the fence-probe canary (`/home/clawd/fence-probe-stray.txt`, 373 occurrences
  in the corpus — a deliberate out-of-fence marker)
- `container_exec` forbidden-root and mount-map realpaths

Rule of thumb: if the record answers *"where is our thing"*, substitute. If it
answers *"what did this run touch"*, leave it verbatim.

## Invertibility is the hard requirement

Archives are currently **byte-faithful**, and that has already paid off: the
flat lesson ledger was restored 466/466 rows from the 2026-08-16 archive
precisely because the archive holds true source bytes while the imported copy
is path-rewritten. Substituting at export puts that property at risk.

It survives **iff the transform is exactly invertible** — the original root
ships with the archive (provenance already records all three, both sides), and
no substitution is ambiguous on the way back.

The ambiguous case to guard: a root path that appears inside *prose* rather
than as a reference. On the way back you cannot tell whether the source said
`$MARO_REPO` literally or the expanded path. Any site where a root string can
appear in free text is a site where substitution must be skipped, or the
inversion is a guess.

**Jeremy's suggestion — ship both forms.** *"storing both the relative path and
an absolute path might be useful for the translation."* This is not paranoia;
it converts a proof obligation into a data cost, which is usually the right
trade for evidence. With the original present, invertibility is guaranteed by
construction rather than argued.

One qualification: put both at the **manifest/archive level, not inline in
every record**. Two copies of the same fact inline is exactly the failure this
codebase keeps hitting — the run card and the loop log disagree about cost on
92% of comparable runs, and nobody can say which is right. Inline the
placeholder form only; keep the substitution table (root → original absolute,
with occurrence counts per file) in the archive manifest, where there is one
authoritative copy that cannot drift per-record.

## Scope and honesty

- **This DOES convert existing run data — that is the point, not a side
  effect.** Export substitutes whatever is in the workspace at export time,
  which is all the historical records in it; that is how the 6,150 stale
  pre-rename repo paths get retired. An earlier draft of this doc called the
  change "forward-only", which was wrong. Precisely:
  - the **live workspace on disk is untouched** — it keeps its absolutes;
  - **every new archive is converted**, historical rows included;
  - **already-made archives are unchanged**, so `path_rewrite` stays for them
    indefinitely.
  The consequence that follows, and the reason Q3 below is load-bearing: any
  consumer reading an archive **directly** must expand. The flat lesson ledger
  was restored 466/466 rows straight out of the 2026-08-16 archive — that
  reader would have written placeholder text into a live store.
- **Provenance stays absolute.** Recording what the source roots *were* is its
  entire job; substituting there would destroy the custody chain.
- **`path_rewrite` is byte-level and stays that way.** It reads `rb`,
  substitutes on raw bytes, writes `wb`, and never decodes — so it cannot
  launder a byte-tainted row. Any new substitution layer must keep that
  property.

## Decisions (2026-08-18, each probed)

**The token cannot be `$MARO_ROOT/` as first proposed.** Probed against the
live corpus: `$MARO_WORKSPACE` already appears **30 times** as a literal, and
`$MARO_MOUNT_VIEW` **428 times** — the latter is a real env var the container
sets. A `$MARO_*` placeholder therefore collides with existing content and the
substitution is *not* invertible: on the way back you cannot tell a substituted
prefix from source text that always said that.

Candidates probed for zero collisions in the same corpus:

| token | collisions |
|---|---|
| `%%MARO_WORKSPACE%%` | 0 |
| `maro-workspace://` | 0 |
| `{{maro.workspace}}` | 0 |
| `$MARO_WORKSPACE` | **30** |

**Recommended: `%%MARO_WORKSPACE%%`** — zero collisions, unmistakably a
placeholder on sight, and it keeps the value a plain string rather than a
structured payload. Note the honest cost: *no* placeholder preserves
"looks like an absolute path", so any consumer testing `startswith("/")` sees a
different shape. Q4 below bounds who that is.

**Q1 — all members, or text-shaped only?** *Decided: reuse the existing
screen.* `path_rewrite.skip_reason` already applies a skip-suffix list plus a
whole-file NUL sniff, and that sniff was hardened specifically because a
NUL-free-header binary let a path get spliced into its tail. Same code, same
doctrine, no new policy.

**Q2 — alias the pre-rename repo name?** *Decided: yes, substitute it, and
record the alias in the manifest.* This retires 6,150 occurrences pointing at a
directory that exists on neither machine. The objection to aliasing — that it
silently equates two names that were distinct when the record was written — is
answered by moving that fact to the manifest rather than destroying it: the
root table records that two source roots mapped to one placeholder, and which
name each record originally used remains recoverable.

**Q3 — where does expansion happen for a reader that never imports?**
*Decided: expansion is the DEFAULT on read, plus a tripwire.* This is the
question the lesson-ledger restore makes load-bearing. Two obligations:
  1. a shared resolver used by every archive-reading path, and by
     `maro-export inspect`;
  2. a test asserting a placeholder literal **never appears in a live
     workspace store** — if one does, expansion was skipped somewhere, and the
     failure is silent otherwise.

**Q4 — does anything downstream pattern-match on absolute paths?** *Measured;
the set is small and named.* The consumers that resolve a stored path back to a
file, and therefore must expand:
  - `loop_report.py:832` and `:948` — `call_record` → `Path(...).read_text()`
  - `run_curation.py:604` — a stored deliverable path
  - `loop_report.py:1026` uses only `Path(...).name`, so it is unaffected
The `startswith("/")` sites found elsewhere (`listener_core`, `conductor`,
`web_fetch`, `orch_bridges`) test user input or HTTP redirects, not stored
record paths, and are out of scope.

## Build order

1. Root table + token, with a collision assertion at export time (fail closed
   if the chosen token appears in the corpus being exported).
2. Substitution at export, reusing `path_rewrite`'s binary screen.
3. Manifest: root table + alias record + per-root occurrence counts.
4. Shared resolver + expansion at the three consumer sites above.
5. Tripwire test: no placeholder literal in a live workspace store.
6. Owned/observed enforcement — the scavenge and fence sites must be provably
   excluded from substitution.


---

## What shipped, and two silent failures found on the way

Landed with the suite at 9,580 green. Export substitutes root prefixes into the
archive; import expands them. `path_rewrite` is untouched and still handles
every archive made before this.

**How `--no-rewrite-paths` got better.** It used to mean "leave the source's
absolutes alone", which was only approximately the source's view. With the
token table, expansion targets the archive's own recorded roots and reproduces
the source bytes **exactly**. The escape hatch is now byte-exact by
construction rather than by omission.

**Owned vs observed is enforced by construction, not by a field list.** Only
paths under our own roots are substituted. An observed path — a scavenge hit, a
write-fence violation — is flagged precisely *because* it lies outside the
fence, so it carries no root prefix and is left verbatim. The evidence
survives; only our own references become portable. A violation that *is* under
a root stays fully identified, just root-relative, and expands back exactly.
Pinned by `test_observed_out_of_fence_path_survives_verbatim`.

### Two failures that were silent, and what caught them

Neither was found by reasoning. Recording them because both are shapes that
recur:

1. **A symlinked root never matches itself.** `config.workspace_root()`
   resolves symlinks; records hold whatever string produced them. On macOS
   `/var` → `/private/var`, so a workspace under `/var/folders/...` was
   compared against its resolved twin and *nothing substituted* — export
   reported success having done nothing. `realpath` only closes one direction;
   the unresolved spelling cannot be derived from the resolved one, so the
   caller passes it in (`extra_roots`, fed from `MARO_WORKSPACE`).
2. **A whitelisted reader drops what it does not name.** Export wrote
   `path_tokens` into provenance; `_normalize_provenance` rebuilds the object
   from a fixed key list and silently discarded it, so import saw no token
   table and left placeholders sitting in the live workspace.

Both were caught by the **tripwire test** (build step 5) — the one that asserts
no placeholder literal ever survives into a live workspace. It failed on its
first run for two independent reasons. Neither would have produced an error
anywhere else: the first looks like a clean export, the second like a clean
import.

### Still true, and still forward-only

This retires none of the 25,278 absolutes already sitting in existing
archives; `path_rewrite` remains the lane for those and is pinned by
`test_legacy_untokenized_archive_still_goes_through_path_rewrite`. The box must
pull this code before any export produces a tokenized archive.

---

## Adversarial round, 2026-08-20 — five HIGHs, all verified, all fixed

Four Codex lenses (Skeptic, Architect, Minimalist, Expert QA) reviewed the
implementation. Every HIGH reproduced under an independent probe. Recorded
here because three of them were *claims this document made* that the code did
not keep.

| # | Defect | Fix |
|---|---|---|
| H1 | A tokenized archive still advertised `format: 2`, so an importer predating tokens accepted it, dropped the unknown key, and wrote placeholders into a live workspace | tokenized archives advertise **v3**; untokenized stay v2, so an old importer fails closed on the format gate |
| H2 | Export screened binaries / `.db` / oversized members out, but import expanded tokens in **every** regular member — corrupting bytes export had promised to preserve | provenance records the **exact member list**; import expands only those. One recorded list instead of two independently drifting screens |
| H3 | Substitution was a raw `bytes.replace`, so root `/owned` rewrote `/owned-other/violation.txt` — falsifying owned-vs-observed, the guarantee that protects evidence | boundary-aware matching: a root must end at a real path boundary, never mid-component |
| H4 | "Exactly invertible" and "`--no-rewrite-paths` reproduces source bytes" were false for aliases and symlink twins, which are many-to-one | claims scoped to canonical spellings; alias hits counted per root and **announced on import** |
| H5 | `path_tokens` was whitelisted but never validated — `"applied": "false"` is truthy, so a corrupt marker activated a destructive transform | full schema validation, failing closed before the workspace is touched |

Mediums fixed alongside: the consumer census was incomplete — **three** raw
readers, not the two the review found (`loop_report._call_meta`,
`run_curation`'s `result_path` fallback, and `camera_readout._result_text`, the
third located by censusing the class instead of fixing the named instances); a
collision left a partial archive at the operator's requested path
(`tarfile.open` truncates up front — the archive is now built under `.partial`
and renamed only on success, so a pre-existing archive survives a failed run);
token expansion swallowed read errors while printing a success count (now fails
loudly, and stages every write until all members have been read); and a relaxed
test assertion had become a tautology (`… or ev["transformed"]` after asserting
it True).

**The round's own prediction held.** Both mid-development fixes flagged in the
prompt as prime suspects were the papered-over ones: `extra_roots` — the
symlink fix — added *more* many-to-one spellings rather than preserving
invertibility, and adding `path_tokens` to the provenance whitelist repaired
the drop but not the trust boundary that had caused it.

**What the fixes are not.** They are themselves unreviewed. Across ~50 recorded
rounds the prior round's fix layer is the likeliest home of the next round's
worst finding, and this round's changes touch the same seams the HIGHs did.
