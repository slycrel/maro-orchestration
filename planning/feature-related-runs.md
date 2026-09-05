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

## Ledger (filled at land, 2026-09-05)

| column | Go | Python |
|---|---|---|
| wall to land (design→green→pushed) | 78 min (20:24→21:42Z; incl. two live-check fixes and the history gates) | 23 min (21:42→22:05Z; the design was settled during the Go build) |
| files touched / lines +/- | 21 non-generated files, +1226/−51 (non-test +723/−40); +54 regenerated contract files | 6 files, +907/−91 (non-test +402/−36) |
| tests added / mutations killed | landscape_test.go (fixture A–H, resume, forgeries incl. one-field mutations + a mutated judge call + history-before-first-landscape, parse) + door/tail/lineage/contracts cases; 26 mutants killed | tests/test_landscape.py 30 tests (units, fixture A–E, NOW e2e, AGENDA loop context, fresh/after/dry/unreadable, CLI contradiction); 35 mutants killed, 1 substrate-equivalent survivor |
| review findings (V/R/OOS) | 4 raised on Go: 2 VERIFIED+fixed (tail gate, parser), 1 OOS-lead (plan request binding, pre-existing), 1 LOW→backlog (fresh_override unbound) | 6 raised on Python: 5 VERIFIED+fixed, 1 VERIFIED+partial (project ancestry is a different fact) |
| review-fix cost | +7 mutants killed; `internal/run`, `internal/tail`, 2 tests, contracts regen | +7 tests, +10 mutants killed; 1 dead guard removed |
| fixture | A fresh (no call) · B follow-up → related, follows A · C unrelated → fresh (no call) · D rerun → plan offered · E `--fresh` · F unreadable judge → fresh, recorded · G judge answers by handle · H chained lineage | same A–E + unreadable + chosen-outside-candidates; live in `$SP/pylive` on haiku (60184e36 → cc3f5450 related; 752d2640 fresh) |
| edge classes met | history gates (no engine-version record: rule gated on the first Landscape record's Seq; tail's first lineage-scoped proposal + the run's own landscape record); prompt template versioning (`prompt_ver`, v1/v2 text kept, v3 = strict parse); judge answers with the handle not the index; every two-similar-goals test now pays a landscape call (scripted fresh answer or `Fresh: true`) | hosted-free judge built lazily (a no-candidate goal builds nothing — a killswitch test caught the eager build); metadata is the record (stamp is best-effort → checked); terminal-status vocabulary is run_curation's, not "truthy"; the scan cap counts eligible runs; the goal-slug ancestry.json is project nesting, not run lineage |

**Which engine was cheaper to change safely, feature 2:** Python by wall
(23 vs 78 min) — but the Go build carried the design work (candidate
rule, prompt shape, parser leniency, the fixture) and the two
history-gate side-finds; the Python port had the design in hand. By
lines the two are close in non-test code (+402 vs +723) and Go's extra
is the fold's re-derivation (`checkLandscape`, ~120 lines) that Python
has no equivalent of. By review: Go's two fixes were both in the
re-derivation layer (gate composition, parser strictness under a new
version); Python's five were the substrate's honesty (best-effort
writes, truthy status, mtime cap) — the class of defect the Go fold
makes structurally impossible and Python has to test for one by one.
Same verdict as feature 1: Python is faster to write, Go is cheaper to
trust.

## Implementation notes (both engines)

**Go (`0133a8d0` + review fixes).** `run.Landscape` record (attempt 0,
AsOf watermark, rule, floor/top-k, scanned/below-floor, candidates with
similarity, relation/chosen/reason, judge invocation, prompt_ver);
`checkLandscape` re-derives the candidates as of the watermark, binds
the judge invocation (run, attempt 0, purpose landscape, no tools,
request == the versioned prompt), re-parses the receipt under the
record's prompt_ver and requires rule/relation/chosen/reason equality;
`RunState.Landscape/Parent/Root/Related/TerminalAt`; `Driver.Fresh`;
`--fresh`. Two **history gates** because the journal has no
engine-version record: attempts after the first Landscape record's Seq
must carry a landscape; tail proposals are lineage-scoped after the
first lineage-scoped proposal OR when the run itself carries a
landscape record (review fix — the global gate alone stayed open across
post-upgrade runs whose tails proposed nothing). **Prompt template
versioning:** the fold binds the request byte-for-byte, so the template
is versioned on the record and old text kept; the parser contract is
versioned with it (v2 accepts the handle a live haiku answered with; v3
= v2's text, read strictly: whole candidate numbers, fresh names no
candidate). Same exposure exists for the intent/plan/step/closure
templates — backlog.

**Python (`c19d619e` + `bd43ad31`).** `src/landscape.py` (similarity,
candidates over `runs_root()` metadata, prompt, parse, decide → record
dict, apply → metadata `landscape` + origin
`parent_handle_id/parent_goal/related_by=landscape/relation`,
related_context incl. the rerun's plan-manifest steps). Hook in
`_handle_impl` after the adapter is built, skipped when the origin
already names a parent; judge = hosted-free when buildable else the
run's adapter, built lazily; NOW prepends the block to the user
message, AGENDA appends it to `ancestry_context_extra`. `fresh` on
`handle()`; `--fresh`; `--after`+`--fresh` → rc 2. Retired
`recall._thread_from_project_ancestry` (the goal-slug fallback) and
suppressed `record_fork_ancestry` for landscape origins. Kept:
`find_prior_attempts` / `rerun_identity` (the 24 h near-duplicate
brief) — overlapping instrument, narrower question ("was this exact
goal just run?"); fold into the landscape later. Kept: the loop's
project-ancestry block (`build_ancestry_prompt`) — project nesting for
dispatch forks, a different fact from the run's lineage; the reviewers
read the two as one and they can disagree by construction; rename or
fold is a follow-up, not a fix.

**Review ledger (one pass, Skeptic + Expert QA on codex, 2026-09-05).**
HIGH S1/Q2 Python unrecorded lineage drives the run — VERIFIED
(`stamp_run_metadata_for` returns None on failure), fixed: apply checks
the write and raises; the stage's except path binds nothing; warning
logs; NOT adopted: refusing to execute when even the fresh record cannot
be written (a metadata-unwritable run is a pre-landscape run, said out
loud). HIGH S2/Q5 failed runs as candidates — VERIFIED (any truthy
status), fixed with the lifecycle's terminal vocabulary; failed priors
KEPT as candidates on purpose, shown with their outcome (Go parity).
HIGH S3/Q4 Go plan request bound by substring — VERIFIED as
PRE-EXISTING: the plan request was never byte-bound (only the intent
request is; the recall block binds by suffix), feature 2 added a
containment check on top → OOS-lead, backlog "bind the plan request to
`planPrompt(goal, interpretation, related, block)`" alongside the
template-versioning item. HIGH Q1/MED S7 Go tail gate — VERIFIED, fixed
(per-run evidence composes with the global gate; test
`TestLandscapedRunTailIsNeverReadAsHistory`). HIGH Q3 Python apply with
chosen ∉ candidates — VERIFIED, fixed (ValueError, no lineage). MED
S4/Q6 scan cap — VERIFIED, fixed (cap on eligible runs, `truncated` on
the record). MED S5/Q7 parser leniency — VERIFIED both, fixed as
contract v3 (v2 records read as they did). MED S6/Q8 two lineage
strings — VERIFIED partially (see kept/suppressed above). LOW S8 Go
`fresh_override` not bound to an operator record — UNSETTLED-by-design:
`Driver.Fresh` is transient; binding it needs a `Fresh` field on the
goal record (door + fold + contracts); backlog. No finding REFUTED this
round; every fix landed with a must-detect test.
