---
status: record
---

# Adversarial review — Minimalist — `git diff b2dc34d..HEAD`

Scope: recursive-goal check-in (director.py/notify.py), planning-depth shadow
(navigator.py/navigator_prompt.py/navigator_shadow.py), director_evaluate bug
fix, R1 architectural cleanup (prefixes.py, decision_prior.py, run_curation.py
topo-sort, evolver.py skill_candidate sweep).

---

1. **Medium — `run_curation._topo_sort_curators` is a general Kahn's-algorithm
   graph engine built to order 9 functions whose dependency graph is, by the
   diff's own comment, already a total order.** `src/run_curation.py:970-1042`
   (`CuratorSpec` dataclass + `_topo_sort_curators`) implements provider-map
   construction, in-degree tracking, a worklist loop, and cycle detection —
   general machinery — to replace a 9-item hand-maintained list. The
   docstring for `_topo_sort_curators` says outright: "the resulting order
   matches the old hand-maintained list exactly, because that list already
   respected the same dependencies" (`src/run_curation.py:1005-1009`). So the
   sort computes nothing new; its only job is to *validate* that a
   9-node, mostly-linear chain (`classify_outcome → inventory_assets →
   excerpt_result → {spend_transparency, promote_skills_lite} →
   scrape_scripts → flag_skill_candidate → rescue_partial →
   index_decision_prior`) is internally consistent, and to fail loudly if a
   future edit breaks that. That property doesn't need a topological-sort
   *derivation* — it needs a topological-sort *check* against the existing
   list, which is a much smaller function (walk `CURATORS` in order, track
   which keys have been provided so far, assert every `requires` key is
   already satisfied — no provider map, no in-degree bookkeeping, no
   worklist, no separate "derive the order" step). The diff also ships a
   dedicated test class for the generic algorithm's edge cases —
   `tests/test_run_curation.py::TestCuratorTopoSort` (`test_missing_provider_raises`,
   `test_cycle_raises`, `test_independent_curators_keep_declaration_order`,
   lines 806-847) — which test graph-algorithm mechanics that would not
   exist if the fix were a same-order *validator* instead of a *sorter*.
   Nothing real is lost by simplifying: the same "loud failure on drift"
   guarantee is achievable by asserting the declared order is consistent
   with declared deps, without generalizing to arbitrary reorderings that
   will never be exercised (ties are broken by declaration order anyway, so
   the "derive a new order" path is dead weight even in principle — see next
   finding).
   Simplification: replace `_topo_sort_curators` with a `_validate_curator_order(specs) -> None`
   that walks `specs` in the given (hand-maintained) order, accumulates
   `provided` keys, and raises if any `requires` isn't already in `provided`
   by the time its curator is reached — same fail-at-import-time contract,
   ~15 lines instead of ~50, and the three algorithm-internals tests
   (missing-provider / cycle / tie-break) collapse into one or two
   order-violation tests. `CURATORS` stays the literal list (still the
   source of truth for order), the specs just gate it.

2. **Medium — `navigator_shadow.analyze_planning_depth_agreement` is a
   near-verbatim copy of `analyze_live_agreement`, differing only in field
   names.** Compare `src/navigator_shadow.py:480-529` (`analyze_live_agreement`)
   to `:532-586` (`analyze_planning_depth_agreement`, added this diff): same
   row-building loop over `NAVIGATOR_DECIDED` events, same `by_<field>`
   dict-of-counters accumulation, same divergence-list collection, same
   return shape (`live_rows`/`by_X`/`agreements`/`divergences`). The only
   substantive differences are which context keys get pulled (`move`/
   `pipeline` vs `planning_depth`/`pipeline_depth`) and the absence of the
   `in_kind` special-case and `by_point` breakdown in the new function. This
   is exactly the kind of duplication the codebase's own "3-is-fine/4-wants-
   extraction" posture (`docs/CODING_NOTES.md`) argues against once a second
   near-identical instance appears — and a third such shadow field (there are
   two now: move-class, planning-depth) is a named possibility given the
   repo's history of adding shadow fields one at a time (dispatch-class →
   now planning-depth). Nothing is lost by sharing the tabulation logic —
   the two callers only ever wanted different field names and an optional
   extra breakdown axis.
   Simplification: extract a shared `_tabulate_agreement(events, *, judged_field,
   pipeline_field, marker_field, extra_breakdown=None, in_kind=None)` helper
   that both `analyze_live_agreement` and `analyze_planning_depth_agreement`
   call, parameterized on field names and the one `in_kind`/`by_point`
   difference `analyze_live_agreement` needs.

3. **Low — dead fallback: `origin.get("root_goal")` is read but never
   written anywhere in the codebase.** `src/director.py:1034-1035`
   (`_fire_checkin`) walks `origin.get("parent_goal") or origin.get("root_goal")
   or origin.get("goal") or task.get("reason", "")` to reconstruct the
   original ask for the check-in payload. `parent_goal` is a real, wired key
   (set in `loop_post_step.py:65`, read in `thread_brain.py`, `recall.py`,
   `handle.py`, `navigator_shadow.py`, and now here) — but `grep -rn
   "root_goal" src/ tests/ docs/` turns up exactly one hit: this line. No
   code anywhere ever sets `origin["root_goal"]`, so that branch of the `or`
   chain is permanently dead; it will never fire and costs nothing to
   delete. Low severity because it's inert (dict `.get()` on a key that's
   never present is a no-op, not a bug), but it's exactly the kind of
   speculative "maybe we'll thread this through later" fallback this repo's
   subtract-before-you-add posture argues against.
   Simplification: drop the `origin.get("root_goal")` clause from the
   fallback chain; if a genuine root-goal-vs-parent-goal distinction becomes
   real later (multi-hop ancestry beyond one parent), add the key and the
   read together, in the same commit that populates it.

4. **Low — three near-duplicate single-value parse tests in
   `TestPlanningDepthParsing` that a single parametrized test would
   cover.** `tests/test_navigator.py:96-160`: `test_present_value_kept`
   (asserts `"one-shot"` round-trips), `test_spawn_sub_goal_is_legal_not_dropped`
   (asserts `"spawn-sub-goal"` round-trips + membership), and
   `test_thin_plan_is_legal` (asserts `"thin-plan"` round-trips) are the same
   assertion — `parse_decision(...).planning_depth == <literal>` — run three
   times against three different legal values from the same
   `PLANNING_DEPTHS` set. Nothing is being tested in test 2/3 that isn't
   already exercised by test 1's pattern; the "not dropped as an
   afterthought" narrative for `spawn-sub-goal` is a docstring concern, not
   a behavioral difference the test proves that the others don't.
   Simplification: `@pytest.mark.parametrize("depth", sorted(PLANNING_DEPTHS
   - {"plan"}))` over one test body; keep `test_case_normalized` and the
   fail-closed tests separate since those exercise genuinely different code
   paths (normalization, invalid input).

5. **Low (informational, not actionable) — the planning-depth shadow and the
   recursion check-in's config knobs are real but worth naming as the
   min-cost end of the spectrum, not the max.** Both were explicitly
   decreed/decided before this chunk (planning-depth shadow: GOAL_BRAIN
   Decisions 2026-07-09 "#5 planning-vs-Tesla-mode"; check-in cadence:
   GOAL_BRAIN Decisions 2026-07-13 verbatim decree quoted in
   `docs/RECURSIVE_CHECKIN_DESIGN.md:70-80`), so neither is unrequested
   speculative work — and the planning-depth shadow specifically reuses an
   already-proven playbook in this codebase (the dispatch-class shadow that
   already graduated to a live per-move cutover), costs zero extra LLM calls
   when off (single boolean gate, `src/navigator_shadow.py:305-307`), and is
   genuinely byte-identical-prompt when disabled (`src/navigator_prompt.py:311`:
   `system_prompt = SYSTEM_PROMPT + (PLANNING_DEPTH_ADDENDUM if
   judge_planning_depth else "")`). That part is fine as shipped. The one
   soft-target inside it: `docs/RECURSIVE_CHECKIN_DESIGN.md:227-232` says
   hardcoded constants for the `2`/`4-7` thresholds would have been
   "also fine" — the config path (`recursion.checkin_first_depth`,
   `.checkin_jitter_min`, `.checkin_jitter_max`, three new rows in
   `docs/DEFAULTS.md` plus two helper functions with their own
   try/except-TypeError/ValueError coercion, `src/director.py:993-1013`) was
   chosen anyway for values Jeremy phrased as a fixed decree ("2", "4-7"),
   not as something an operator is expected to tune per-install. This is
   consistent with the repo's dense existing config-knob convention (dozens
   of similar rows in `DEFAULTS.md` already), so I'm not flagging it as
   waste — just noting it's three config keys plus two coercion helpers in
   service of numbers nobody has asked to change yet, versus two named
   constants that would have been equally legal per the design doc itself.
   No action recommended unless the config surface starts to feel noisy.

---

**Not flagged, considered and cleared:**
- `evolver.promote_skill_candidates` / `find_unconsumed_skill_candidates` /
  `mark_skill_candidate_consumed`: verified `skills.extract_skills` has
  exactly three callers after this diff (`loop_finalize.py:710`, `cli.py:1646`,
  and the new `evolver.py:413`) — this wires a previously dead field
  (`card["skill_candidate"]`, flagged but unread outside tests before this
  diff) into the *same* crystallization call other paths already use, on a
  different trigger (periodic catch-up vs. same-run). Genuinely additive,
  not a second promotion pathway — `promote_skills_lite` (a distinct,
  pre-existing skills-lite tier mechanism) is unaffected and uses a
  different code path entirely.
- `src/prefixes.py` / `src/decision_prior.py`: both are extractions that
  remove existing cross-module private-name reach-ins
  (`recall.py` importing `handle._apply_prefixes`, `run_curation.py`
  importing `recall._find_prior_attempts`) flagged by a prior review round
  — net code reduction in `handle.py` (139 lines changed, mostly deletions)
  and no new duplication introduced.
- `director_evaluate`'s `_continue_on_error` split (`src/director.py:1440-1441,
  1531-1532`): a one-line, single-purpose bug fix (two call sites were
  sharing one `DirectorDecision("evaluation skipped")` object, collapsing a
  live LLM failure into the same text as a deliberate no-op) — correctly
  minimal, test additions (`tests/test_director.py`) are proportionate (3
  assertions, no new test classes).
