# Feature 2 on both engines: the plan step reads the run landscape

Phase 4, second half (`successor-plan.md`): the same feature on both
engines, per-feature cost ledger, "which is cheaper to change safely".
Feature 1 (`feature-lineage-memory.md`) gave both engines lineage:
a goal can follow a prior run, and memory is scoped to the lineage.
That surface (`--after`) is explicit — the operator decides which
prior run a goal relates to.

**Jeremy, 2026-09-05 (decree):** *"long run I'd think the plan step
would examine the landscape, decide to pull in 'adjacent/related' run
context (or not) along with a fresh run, or potentially choose a
re-run if it's similar enough. I'm fine to start with an explicit
path, but that's pushing that decision that maro should make to the
orchestrator IMO; the orchestrator doesn't have the data to make a
better decision than maro."*

So feature 2 is not a new operator flag. It moves the relation
decision INTO the run: `--after` stays as the override, and the
default is that Maro reads the landscape and decides.

## The feature, defined once

1. **Landscape.** At plan time a goal G with no explicit lineage sees
   the workspace's prior runs: goal text, outcome, delivered payload
   (or its head), lineage root, age. Candidate selection is
   deterministic and recorded (a lexical similarity over goal text,
   top-K with the excluded count on the record); no model call is
   spent when the landscape has no candidate above the floor — G is
   simply fresh, and the record says the landscape was empty.
2. **Decision, by Maro.** When candidates exist, ONE cheap model call
   (the judge tier, same as closure) reads G and the candidates and
   returns one of three relations, with the chosen run and a reason:
   - **fresh** — nothing here bears on G. G is the root of its own
     lineage. Candidates are recorded as considered.
   - **related** — a prior run bears on G (an angle, a tangent, a
     follow-up). G FOLLOWS that run: its lineage is the prior's
     (feature 1's substrate, so scoped memory walks to it), and the
     prior's delivered payload rides into G's plan/execute request as
     "related prior run" context, in the same rendered block position
     as recall.
   - **rerun** — the prior run asked the same thing. G follows it AND
     the prior's plan is offered to the planner as the starting plan
     (reuse or revise, judged like any plan); the prior's delivery is
     context, never served as G's answer — a rerun still runs.
3. **Recorded.** The relation is a record on the goal's lineage (Go: a
   `Landscape` record the fold binds to the goal — candidates,
   relation, chosen run, reason, the call's receipt; Python:
   `origin.related_by = "landscape"` + a `landscape` block on run
   metadata with the same fields). `--after` writes the same record
   with `related_by = "operator"` and no call. A `--fresh` override
   skips the landscape (tests, cost).
4. **Subsumes** Python's Goal Ancestry block (`recall._thread_from_
   project_ancestry`), which pulls prior runs by goal-text slug — a
   landscape decision made by string equality, and the cause of the
   feature-1 side-find (an identically-worded stranger inherited a
   lineage's ancestry). Once the landscape decides, ancestry follows
   the DECIDED lineage; the slug path is retired, not kept beside it.
5. **Fixture (script-checkable, both engines):**
   - A: root goal, delivered. B, worded as a follow-up to A, no
     `--after`: landscape decides *related*, B's lineage root is A,
     B's request carries A's payload, the record names A and the
     reason. B recalls a lesson scoped to A's lineage (feature 1).
   - C: unrelated wording: landscape decides *fresh* (or no candidate
     above the floor → no call); C is its own root; record says so.
   - D: A's wording again: *rerun*; D follows A and its plan request
     carries A's plan; D still executes.
   - E: `--fresh` with A's wording: no landscape call, own root.
   The judge is scripted in tests (the relation comes from the
   backend), so each branch is exercised regardless of a model's
   taste; the live run uses haiku and records what it chose.

## Cuts (v1 of this feature)

- Similarity is lexical (token overlap over goal text). No embeddings,
  no retrieval index; the workspace's run count is small and the
  floor + top-K are recorded, so the day this is the wrong instrument
  the record shows it.
- The landscape reads goal text + delivered payload head; it does not
  read run transcripts or plans of candidates (only the CHOSEN run's
  plan, and only for rerun).
- One decision per goal, at intake/plan. No mid-run re-examination.
- Related context is the chosen run's payload only, not the whole
  lineage's.

## Where each engine is (read 2026-09-05)

| seam | Go | Python |
|---|---|---|
| prior runs with goal + payload | `Ledger.Runs` (goal text on `Goal`, delivered `Payload` ref in the store) | `runs/` dirs: `metadata.json` (prompt, origin), delivered answer in the run dir |
| plan-time call | driver `intent` → `plan` (purposes exist; judge backend at closure) | `loop_planning` / intent resolution in `handle.py` |
| lineage substrate | `Driver.After`, `Lineage`, `scope(goal)` | `origin.parent_handle_id`, `recall.lineage_root` |
| context injection point | rendered recall block in the request | `recall()` result blocks (thread, lessons, …) |
| existing slug ancestry | none | `_thread_from_project_ancestry` — retire |

## Ledger (filled at land)

| column | Go | Python |
|---|---|---|
| wall to land (design→green→pushed) | | |
| files touched / lines +/- | | |
| tests added / mutations killed | | |
| review findings (V/R/OOS) | | |
| fixture | | |
| edge classes met | | |
