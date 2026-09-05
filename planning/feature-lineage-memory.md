# Feature 1 on both engines: lineage-scoped memory

Phase 4, second half (`successor-plan.md`): the same small feature on
both engines, with a per-feature cost ledger, to measure "which is
cheaper to change safely". Jeremy's steer 2026-09-05: memory first
(supplemental, additive), then "related goals / expanding run
horizons" as the growth target. This feature is the memory half,
shaped so the horizon feature lands ON it: the first scope a goal can
name is its own lineage.

## The feature, defined once

1. **A goal can follow a prior run.** Operator surface `--after
   <handle>` on the goal-entry verb. The new goal's lineage is the
   prior goal's: its root is the prior's root (or the prior itself
   when the prior was a root). The lineage is on the record.
2. **Lessons minted from a run are scoped to the run's lineage root**,
   not to the workspace. Workspace-wide lessons still exist (seeds,
   operator-added, promoted later); the run's own learning is local
   until evidence says otherwise. (Promotion to workspace scope is
   NOT in this slice.)
3. **Recall walks the lineage**: own goal → parent → root → workspace.
   A lesson scoped to lineage L is recalled by any goal in L and by no
   goal outside it; the recall record says what was excluded by scope.
4. **Fixture (script-checkable, both engines):** run A mints or is
   given a lesson at its lineage scope; run B `--after A` recalls it
   (the lesson text reaches B's request); run C, unrelated, does not,
   and C's recall record counts the exclusion as scope.

## Where each engine already is (read 2026-09-05)

| piece | Python | Go |
|---|---|---|
| lineage on the goal | `Origin.parent_handle_id` chain in run metadata; `recall.ThreadIdentity` walks it; no CLI flag sets it | `Goal.Parent/Root`; door allows a parent ONLY for replay/fork origins; `scope()` already walks own→parent→root→workspace |
| lesson scope | `TieredLesson.scope` = method/world portability stamp (a different meaning; census-only, never a ranking input by decree e2b83703) — no lineage field | `LearnedRevision.Scope` = `workspace` or `goal:<id>`; the tail mints every lesson at `workspace` |
| recall filter | `query_lessons_scored(goal, task_type)`; no lineage filter | `learn.Recall(Query{Scope: chain})` — exists, exercised only by `learn add --scope` |

So on Go the feature is: open the door for a `follows` parent on a
cli/service goal (fold: parent exists, is a production goal, same
root), a driver field + `--after` flag, mint at root scope, readout.
On Python it is: a `--after` flag → `origin.parent_handle_id`, a
`lineage` field on `TieredLesson` (root handle id at mint, distinct
from the existing `scope` stamp), and a lineage filter in the lesson
query that the recall seam passes from `ThreadIdentity`.

## Ledger (filled at land, 2026-09-05)

| column | Go | Python |
|---|---|---|
| wall to land (design→green→pushed) | feature 17 min (18:58→19:15Z, `30393f57`); review-fix round 19 min (`f4397e9b`) | feature ~19 min to green (19:15→~19:34Z); review-fix round ~45 min incl. fast suite; landed 19:59Z (`73f4da36`) |
| files touched / lines +/- | feature 7 code/test files +282/−12 (+2 regenerated contracts); fixes 5 files +170/−4 | feature 6 src files +122/−3 + 1 test file (9 tests); fixes 7 src files + 1 test stub, cumulative src +256/−10, test file 362 lines (19 tests) |
| tests added / mutations killed | 4 tests + 2 assertions / 12 killed (5 feature + 7 fix) | 19 tests / 20 killed (5 feature + 15 fix) |
| review findings (V/R/OOS) | 2 / 0 / 0 — both HIGH, both fixed same round | 8 / 0 / 0 — 5 HIGH + 3 MEDIUM, all fixed same round |
| fixture | `TestLineageScopesRecall` (a → lesson@goal:a → b after a: Included 1, chain [b,a,ws]; stranger c: ExcludedCounts.scope 1; d after b: root a, 4-scope chain) + live `--after` on `$SP/c3` | `tests/test_lineage_memory.py` (same shape: a → lesson@a → b after a recalls; stranger c: `lineage_excluded` 2) + live `--after` on `$SP/pyws` (run 4fade67b) |
| edge classes met | **Parent overload** — `Goal.Parent` meant fork/replay everywhere it was read; the resume sweeps skipped followed goals (review HIGH). **Fold binds by re-derivation, not by truth** — the learn fold re-derived a recall from the scope it recorded; only the run fold holds the goal (review HIGH, pattern 83 again). | **Origin conflates provenance and lineage** — `--after` stamps an origin, and origin PRESENCE was the autonomous-run proxy (NOW gained the self-verdict call). **Identity is the goal slug** — an identically-worded stranger inherits the lineage's Goal Ancestry (live find, BACKLOG). **Surface count** — ranked recall was one of five injection surfaces (graveyard, legacy inject, worker mirror, public reader, pack import) and each needed the rule. **Fail-open default** — unknown scope became "" = widest. |

## Review round (one pass, codex Skeptic + Expert QA, 2026-09-05)

Every finding VERIFIED in tree before fixing; none refuted, none
out of scope. Go: (1) run fold never compared a selection's recorded
scope to `scope(rs.Goal)` and the continuation branch compared only the
derived arrays → `scopeMatches` on policy + recall at the attempt
boundary, `sameQuery` on continuations; (2) resume sweeps skipped on
`Parent != ""` → skip by origin (fork/replay). Python: (1) graveyard,
`inject_tiered_lessons`, `memory_bridge` ingest and `query_lessons`
read lineage rows unfiltered → one rule `_visible_in_lineage` on every
surface, worker mirror skips lineage rows (its scope taxonomy is
thread/run); (2) pack import rebuilt rows without lineage → "" (widest)
→ `foreign:<root>` namespace, never a local match; (3) dedup/reinforce
ignored lineage → B's re-learning reinforced A's row → lineage is part
of dedup identity; (4) `_lineage_root_for` failed OPEN → fails closed
to `unresolved:<ref>` (injected nowhere; "" only with no run context);
(5) `--after` origin flipped NOW into the autonomous verdict path →
`_autonomous_origin` reads the source, cli = interactive; (6) cycle
root was start-dependent → deterministic `unresolved:cycle:<min>`
sentinel; (7) exclusion unrecorded → `sources.lineage_excluded`
(distinct rows); (8) `--after` accepted a run with unreadable metadata
→ refused rc 2. Residual, both engines: promotion to workspace scope
(next slice); Python's persona `recent_lessons` and strategy-evaluator
inject stay workspace-only (no run context at those call sites).

## Reading

Same feature, same fixture, same reviewers. Go took two findings, both
in one class the ledger already knew (fold rules that bind by equality
are safe only when the driver satisfies them by construction — and a
rule that re-derives from a recorded input is not that). Python took
eight, and the eight are a map of the codebase, not of the change: the
lesson store has five model-facing readers, transport and dedup each
re-derive a row's identity, and the origin dict carries two meanings.
"Which engine is cheaper to change safely" on this feature: Go, by
wall (36 min vs ~65) and by review yield (2 vs 8) — but the honest
denominator is surface area, and the successor has fewer surfaces
because it is younger, not because it is better designed. The second
feature (related goals / horizon) will lean on this one's substrate on
both sides; the comparison gets sharper when both engines have to
grow, not just gate.
