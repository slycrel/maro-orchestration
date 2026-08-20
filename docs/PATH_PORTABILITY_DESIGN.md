---
status: dormant-design
---

# Path portability — export-side placeholders instead of import-side rewriting

**Status: design sketch, nothing built.** Read for intent; verify against code
before acting on specifics.

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

- **Forward-only.** This does not retire the 25,278 existing absolutes.
  `path_rewrite` stays for legacy archives indefinitely; this is a deprecation
  path, not a replacement.
- **Provenance stays absolute.** Recording what the source roots *were* is its
  entire job; substituting there would destroy the custody chain.
- **`path_rewrite` is byte-level and stays that way.** It reads `rb`,
  substitutes on raw bytes, writes `wb`, and never decodes — so it cannot
  launder a byte-tainted row. Any new substitution layer must keep that
  property.

## Open questions

1. Does the substitution run over *all* members or only text-shaped ones? The
   existing rewrite already screens NUL-bearing files as binary; reuse it.
2. Should `$MARO_REPO` resolve the pre-rename name as an alias, or should
   stale-name occurrences stay absolute and be reported? An alias retires 6,150
   occurrences; it also silently equates two directory names that were distinct
   at the time the record was written.
3. Where does the expansion happen for a *reader* that never imports — someone
   reading an archive directly? A resolver helper, or documented placeholders?
4. Does anything downstream pattern-match on absolute paths in a way a
   placeholder would break? Needs a consumer census before building.
