---
status: living
---

# Mutation specs

Must-detect mutation lists, one JSON file per surface, run by
`scripts/mutate.py`. The runner's docstring is the format reference; this
file is the convention and the coverage ledger.

```bash
python3 scripts/mutate.py tests/mutation/scope_14a.json
python3 scripts/mutate.py tests/mutation/scope_14a.json --only quarantine
```

These are **not** collected by pytest and do not run in CI — a sweep is
minutes of targeted test runs and the value is in the reading behind the
list, not in re-running it on every push. Run one when you change the
surface it covers, and when you add coverage to a new surface.

## Why the spec file is the artifact

Derive the mutation list from **reading the file, not from your own
diff** (HOUSE_STYLE step 3; Jeremy 2026-08-16). A diff-derived list only
tests whether your fixes are pinned. A file-derived list tests whether
the behavior is — and in the arc that produced this directory, five
adversarial review rounds and two diff-derived harnesses walked past
twelve real gaps, the worst being a decree guarded by two tests that
could not fail.

Committing the spec answers "what did we actually probe?" months later.
Before this, each round wrote a throwaway harness in a scratchpad and
the answer was unrecoverable by the next session.

## Conventions

- **One anchor, one match.** The runner refuses an anchor matching 0 or
  2+ times, because a mis-applied mutation silently reads as a pass.
- **The baseline must be green.** Every distinct `tests` target is run
  once unmutated before anything is applied, and a red baseline aborts
  the sweep. DETECTED is only "pytest exited non-zero", so without this
  a broken environment reports a perfect run — the instrument built to
  find false confidence manufacturing it. Added 2026-08-16 after the
  Experimentalist lens found the hole; the three green specs on record
  were re-run under the gate and all held.
- **A survivor is a hole in the suite**, not a mutation to delete.
  Strengthen the test.
- **Mark genuinely equivalent mutants `equivalent`** with the reason
  they cannot fail. They are still applied and run; a *detected*
  equivalent fails the run, because it means the stated reason is wrong.
  Contorting a test to kill an equivalent mutant is how a suite starts
  testing its own mocks.
- **The sweep runs against HEAD, not your tree.** `mutate.py` mutates a
  clean copy of the committed state, so uncommitted work is invisible to
  it: anchors on lines you have only edited locally come back `SKIP —
  anchor matched 0x`, and mutations of code you have only locally fixed
  come back SURVIVED. Commit first, then sweep. (The skip message is
  loud for exactly this reason — a skipped mutation is not a passed one.)
- **Name the surface, not the file.** Coverage below is per *surface* —
  a spec covering the scope paths in `knowledge_web.py` says nothing
  about the other 85% of that module. Do not read "swept" off a
  filename.

## A SURVIVED verdict is a lead, not a fact

Same discipline as verify-before-fix on a reviewer finding, and it bites
for the same reason. Two ways this runner reports a survivor that isn't
a hole, both hit on the first sweep after the tool landed:

1. **Too narrow a `tests` target.** A mutation aimed at
   `knowledge_web.py` but run only against `test_lesson_provenance.py`
   "survives" work that `test_knowledge_web.py` does pin. Scope the
   field to every file that plausibly covers the site — the run is
   slower and the verdict means something.
2. **A redundant downstream guard.** Removing `promote_lesson`'s own
   `_is_quarantined` check still refuses the promotion, because another
   check later in the flow catches it. Neutering the *predicate*
   promotes the row, so the invariant IS tested; the individual guard is
   defense in depth. Probe before calling it a hole: monkeypatch the one
   thing and see whether the behavior actually changes.

A third shape showed up on the dispatch-envelope sweep, and it is the one
worth arguing with: **two guards, one test.** `_safe_name` prevents
traversal twice — a basename call and a character whitelist — and
`test_traversal_names_are_flattened_to_basenames` passes with either one
removed. So the test pins *neither*, and both mutations survive. The
lazy resolution is to mark both equivalent and move on; the right one is
to ask what job only ONE of them does. The whitelist also scrubs spaces,
`;`, newlines and `$(...)` out of a dispatcher-chosen filename, nothing
tested that, and pinning it turned one survivor into a real test while
leaving the other honestly equivalent. Redundancy in the *code* is fine;
redundancy that makes a test unable to distinguish its subject is not.

Both are recorded in the spec rather than in someone's memory —
`equivalent` for a settled one, `unverified` for a survivor that hasn't
had the probe yet. An unprobed survivor left unmarked reads as a
confirmed hole to the next reader, which is the same false-confidence
failure the sweep exists to find.

## Coverage ledger

| Spec | Surface | Mutations | Swept |
|---|---|---|---|
| `scope_14a.json` | §14a scope/stamp/portability: `knowledge_web` scope screens, tiered-store load/rewrite/mutate, quarantine, reinforce heal, `_tfidf_rank_scored`; `camera_readout` census, `_lesson_origins`, `_stamp_coverage`, `_scope_rollup`, `_print_portability`; `pack` lesson transport border | 34 + 1 equivalent | 2026-08-16, all accounted for |
| `provenance_gate.json` | The db37d525 contamination gate: `lesson_provenance` classifier sub-patterns + killswitch string/exception handling, the `_is_quarantined` predicate and its six enforcement sites in `knowledge_web` (both graveyard legs, both promote scans, effect-promotion, canon), the `memory_ledger` mint choke point | 16 + 3 equivalent | 2026-08-16, all accounted for — **CLOSED** (landed red at 6/18, reopened and closed same day) |
| `path_rewrite.json` | Embedded-path rewriting on transfer: `path_rewrite` root validation + ordering + both match boundaries + skip screens + atomic swap and its post-commit half, the `maro-export import` wiring (extracted-files-only list, provenance gate, custody `transformed`), the `maro-import --source` wiring (rewrite-before-dedup, marker verbatim, per-file quarantine, dry-run honesty, unresolved-source mapping) | 39 + 2 equivalent | 2026-08-16, all accounted for (10 added after the review round, 1 after the live import) |
| `dispatch_envelope.json` | The typed dispatch boundary: `parse_dispatch_payload` shape/version/type screens, `_safe_name`, `store_attachments` dedup + provenance sidecar, `land_in_run_dir` idempotence, `operator_block` authority label, and the extraction-exclusion decree at the `handle_queue` intake | 19 + 1 equivalent | 2026-08-16, all accounted for (12/20 on first pass, 7 gaps closed) |
| `defaults_census.json` | The DEFAULTS.md registry tripwire itself (`tests/test_defaults_doc.py`): forward census getter set + alias resolution + rglob + dotless leg + `config.py` exemption, reverse census table-cell parse + read-evidence shapes + per-file keying, and the living-frontmatter check | 15 | 2026-08-16, all accounted for (3/14 on first pass, seam + 16 fixtures added) |
| `dev_status.json` | The project-state readout: CAPABILITIES row-mark parsing (the shape that must never re-admit prose), the backlog three-way census and stop-rule detection, trend degradation to unknown, staleness rendering, and the DEV_LOG managed-block splice | 11 | 2026-08-16, all accounted for (2 gaps closed — an evasion fixture and one badly-built mutation of my own) |
| `operator_attachments.json` | The `handle --attach` lane: local-file store (binary fidelity, refusal on missing/oversize, collision handling, basenaming), run-tree landing including the open_run wiring, the read-only mount seam that makes an attachment reachable at all, and the advisory block's mounted-path + operator-supplied labeling | 8 + 1 equivalent | 2026-08-16, all accounted for (the mount seam was EXTRACTED because the sweep proved the inline version untestable) |
| `retention_decree.json` | The 2026-07-10 retention decree's tripwire (`tests/test_no_silent_deletion.py`): the AST deletion scanner's API legs + function attribution + nested-package scan, the (module, function) allowlist key, the stale-entry census, and the `delete_checkpoint` no-auto-caller pin | 15 | 2026-08-16, all accounted for (4/13 on first pass, seam + 21 fixtures added) |
| `delta_gate_floors.json` | The Δ-gate's acting floors: `EFFECT_PROMOTE_MIN_DELTA/MIN_CALLS`, `EFFECT_DEMOTE_MAX_DELTA`, `EFFECT_INERT_MAX_ABS_DELTA/MAX_SPREAD`, and every guard in the four routes (`promote_lesson_by_effect`, `confirm_lesson_by_delta`, `demote_lesson_by_effect`, `inert_lesson_by_effect`) plus `resolve_remint_watch` — killswitches, finite-only, call floor, spread, stratum, replay-errors, boundary flags, text binding | 34 + 2 equivalent | 2026-08-16, all accounted for (33/35 on first pass — best of the arc) |
| `jsonl_utils.json` | The shared JSONL reader (9 callers): `_iter_lines_reverse` chunk stitching + order + boundary hold-back, the one `_classify` ladder (blank / undecodable / malformed / non-dict) and which bucket each lands in, `_read`'s missing-vs-unreadable split and tail/full-scan branch, `SkipReport.dropped/__bool__/summary`, and the WARNING that names the store | 32 | 2026-08-16, all accounted for (32/32 first pass — see the note below on what that does and does not mean) |

Note on `jsonl_utils.json`: 32/32 on the first pass is the weakest kind of
green in this directory, and the row says so on purpose. The tests and the
mutation list were written in the same sitting against the same reading of
the file, so the sweep mostly proves the guard matches the list its author
wrote — not that the list is complete. Compare `defaults_census.json`
(3/14) or `retention_decree.json` (4/13), where the sweep audited guards
written months earlier by someone not thinking about mutations. **A sweep
co-written with its surface is a design tool; a sweep run against an older
surface is a measurement.** The one genuine find here came from the gap
between the two: `mutate.py` sweeps a copy of HEAD, so the first run
scored the *committed* reader, and a survivor there
(`if position > 0` hold-back removed) turned out to be rescued by a
trailing `if buffer: yield buffer` block that is unreachable in unmutated
code — dead since it was written, and now removed.

Note on `path_rewrite.json`: the first sweep of it ran green at 30/30 and
a six-lens review still found four real defects afterward, two of them
HIGH with independent consensus. **A green sweep bounds what the tests
pin, not what the code does** — every mutation in that first pass was
derived from behavior the author had already thought of, which is
exactly the blind spot a reviewer is for. The ten mutations added after
the review are the durable half of that round: each one now fails the
suite if its fix is ever undone.

And then the first LIVE run over the real corpus found a defect neither
had: the review's new left boundary blocked a genuine path start whenever
the path followed an escape inside a JSON transcript (`…notes.md\n\n/home/
clawd/…`), leaving 5,543 occurrences unrewritten across 24,553 imported
files. Every synthetic fixture in the sweep and in six review prompts
wrote paths preceded by a space, a quote, or start-of-line. **Sweep,
review, and one run against real data are three different instruments** —
a transform over a corpus is not verified until it has run over the
corpus.

`provenance_gate.json` was deliberately landed red at 6/18 and closed
the same day, and the interval is the point. Its four `unverified`
enforcement survivors split two and two under the probe: two were
redundant guards whose removal changes nothing observable, two were real.
Had they been reported as holes on the strength of the SURVIVED verdict,
half the follow-up work would have been writing tests for behavior
already enforced elsewhere — tests that pass for the wrong reason, which
is the failure this directory exists to find.

The run_decay_cycle survivor is the one worth remembering, because
reading the code would have gotten it wrong in the *other* direction. The
tier move IS refused downstream, so "redundant guard, mark equivalent"
looks right — but `promoted_ids` is built from the screen and feeds the
returned count and the change_log audit entry. Remove it and the cycle
reports promoting lessons it did not promote: a clean store with a lying
audit trail. **Probe behaviourally, not by reading; a guard can be
redundant for state and load-bearing for the record of that state.**

Everything else in `src/` (178 modules) has never had a file-derived
sweep. The ordered plan for covering it is the top item in the BACKLOG
Actionable Stack: tests that claim to enforce a decree first (silent by
construction), then data-integrity boundaries, then operator-facing
output, then churn.

Four tier-1 decree surfaces are now swept, and the spread is the point.
The e2b83703 scope decree was guarded by two tests that could not fail.
The dispatch envelope's extraction exclusion held under both mutations
aimed at it (leak operator prose into the goal; drop the operator
channel) — a surface that passes clean is the result, not a failed hunt.
The DEFAULTS.md census scored 3/14 and the retention tripwire 4/13. The
sweep is not a formality that always finds something, and it is not a
formality that never does.

**Three of the five were tripwires, and two of those could not fail;
the two production surfaces scored 12/20 and 33/35.** That is the
standing lesson of this arc: the code a decree protects tends to be
fine, and the guard claiming to protect it tends not to be. Enforcement
written as a test attracts less scrutiny than enforcement written as a
feature, because a passing test looks like evidence.

The Δ-gate floors are the control that makes the claim mean something.
Five numeric floors, five killswitches and every screen across four
parallel routes — all already pinned, on code that had five adversarial
review rounds. Review does work on features. It is enforcement-as-test
that slips through it.

## Sweeping a tripwire: mutate the guard, not the guarded

Two of the three tier-1 surfaces were production code; the defaults
census is a *test*, and that changes the question. You are not asking
"do the tests catch a change in the code" — you are asking **"can this
guard fail at all?"**

The census scored 3/14 because it had no seam: every helper reached for
`REPO_ROOT` itself, so there was no way to hand it a known violation. It
was untestable by construction, which is exactly why months of review
walked past it — nothing looks wrong, and the test does pass. The three
mutations that WERE caught fired by accident, having broken the census
hard enough against real repo data to raise a false positive. Accidental
detection is not coverage, and the sweep is what tells them apart.

The fix generalizes to any tripwire: give the checker its inputs as
parameters (defaulting to the live ones, so the deployed guard is
unchanged), then add must-detect fixtures that inject one violation each
and assert it is named. Pin the *exemptions* the same way — a quiet
census is only trustworthy if you can show what it stays quiet about.
**A detection shape with no fixture is a claim, not a guard**, so add
the fixture in the same commit as the shape.
