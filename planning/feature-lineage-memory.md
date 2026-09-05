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

## Ledger (filled at land)

| column | Go | Python |
|---|---|---|
| wall to land (design→green→pushed) | | |
| files touched / lines +/- | | |
| tests added / mutations killed | | |
| review findings (V/R/OOS) | | |
| fixture | | |
| edge classes met | | |
