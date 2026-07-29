---
status: record
---

# Adversarial review — Architect — `git diff b2dc34d..HEAD`

Scope: recursive-goal check-in (`director.py`/`notify.py`), planning-depth
shadow (`navigator.py`/`navigator_prompt.py`/`navigator_shadow.py`),
`director_evaluate` bug fix, R1 architectural cleanup (`prefixes.py`,
`decision_prior.py`, `run_curation.py` topo-sort, `evolver.py` skill-candidate
sweep). Lens: coupling, layering, two-sources-of-truth, seams that get
expensive later. Not flagging style.

---

1. **High — the `director_evaluate` masked-failure fix only covers one of
   two code paths that produce the exact symptom it was created to fix.**
   `src/director.py:1440-1441` splits the old shared `_continue` object into
   `_continue` (`"evaluation skipped"`, deliberate no-op) and
   `_continue_on_error` (`"evaluation failed — treated as continue"`,
   exception fallback) — but only the `except Exception:` branch at
   `:1532` was rewired to the new object. The sibling early-return at
   `:1483` (`if not data: return _continue`) — reached when the adapter call
   *succeeds* but `extract_json` can't parse the response — still returns
   the original `_continue`, i.e. still reports `"evaluation skipped"`.
   This is the identical failure class the fix targets: the GOAL_BRAIN
   entry for this exact change (2026-07-13, thread-arch #9) traces a real
   production incident where a rate-limited LLM call's failure was
   misreported as a deliberate skip, confusing debugging of a live `/loop`
   session — but a rate-limited or malformed-JSON response is exactly what
   lands in the `if not data:` branch, not necessarily the `except`
   branch (a successful HTTP call that returns unparseable content never
   raises). The test suite mirrors the gap:
   `tests/test_director.py:2525` (`test_bad_json_returns_continue`) asserts
   only `result.action == "continue"` — no `reasoning` assertion — while its
   siblings added in this same diff (`:2377`, `:2382`, `:2549-2551`) all
   pin the `reasoning` string precisely, including a comment explicitly
   warning against reasoning collapsing into `"evaluation skipped"`. Next
   time a run traces a masked failure through this seam, it will land in
   the untouched branch and reproduce the same debugging dead-end the fix
   was written to close.
   **Fix direction:** return `_continue_on_error` (or a third, distinctly-worded
   decision, e.g. `"evaluation response unusable"`) from the `if not data:`
   branch too, and add the same reasoning assertion `test_bad_json_returns_continue`
   is missing.

2. **Medium — the `origin` ancestry dict is an unowned, schema-less,
   shared-mutable structure now written by 4+ independent producers with
   inconsistent key sets, and this diff adds a consumer that silently
   degrades when the schema it assumes isn't there.** `origin` is built
   from scratch in at least four places with four different shapes:
   `src/cli.py:641` (`{"source": "cli-run"}` — no goal-bearing key at all),
   `src/cli.py:2111` (`{"source": "cli-resume", "resumed_from": ...}`),
   `src/loop_post_step.py:62-66` (`{"parent_loop_id", "parent_handle_id",
   "parent_goal"}`), and now `src/director.py:1079-1085`
   (`_advance_origin_with_checkin`, adding `next_checkin_depth` and
   `checkins_sent` on top of whatever was already there). Nothing defines
   "the origin dict" as a type — no dataclass, no `TypedDict`, no doc
   listing which keys a consumer may rely on. This diff's own consumer,
   `_fire_checkin` (`src/director.py:1033-1037`), reads a 3-deep fallback
   chain — `origin.get("parent_goal") or origin.get("root_goal") or
   origin.get("goal") or task.get("reason", "")` — of which `root_goal` and
   `goal` are never written anywhere in `src/`, `tests/`, or `docs/` (only
   `parent_goal` is real, populated solely by `loop_post_step.py`'s
   budget-ceiling path). Because `handle_escalation` is only ever invoked
   for `source == "loop_escalation"` tasks (`src/handle_queue.py:52-55`),
   which only `loop_post_step.py` creates, the dead fallback happens to
   never fire *today* — but that's an accident of there being exactly one
   producer for this call path right now, not a structural guarantee. The
   design doc this ships from (`docs/RECURSIVE_CHECKIN_DESIGN.md`, "walk
   `origin` back to the root goal text if carried") describes a proper
   ancestry walk; what shipped is a flat 3-key `.get()` chain with two keys
   that don't exist. Every time a module adds a new field to `origin`
   (parent lineage, now check-in cadence, potentially planning-depth or
   cost-accounting state next), the coupling surface grows without a
   contract anyone can check against — a future producer that forgets to
   set `parent_goal` (e.g. a new entry point mirroring `cli.py`'s minimal
   `{"source": "cli-run"}` shape) will silently produce a check-in
   notification whose "original ask" is internal escalation boilerplate
   text instead of the user's goal, and nothing will error.
   **Fix direction:** give `origin` a real shape — a small dataclass or
   `TypedDict` in `ancestry.py` (which already owns goal-lineage
   modeling) with one constructor every entry point calls, so "does this
   chain carry a real goal string" is a property of the type, not a
   grep-and-hope convention. At minimum, drop the dead `root_goal`/`goal`
   fallback keys so the chain doesn't imply a richer contract than exists.

3. **Medium — `CuratorSpec.provides`/`requires` is a declared contract with
   nothing checking it against what the curator functions actually read or
   write on `card`.** `src/run_curation.py:908-926` derives execution order
   from hand-written `provides=(...)`/`requires=(...)` tuples per curator,
   which is a real improvement over a bare list + comment — but the
   validation is purely structural (does every `requires` key have *some*
   declared provider, is the graph acyclic). Nothing verifies that
   `classify_outcome`'s actual body writes exactly `{"success_class",
   "status", "goal_achieved", "goal_verdict_summary", "total_cost_usd"}` to
   `card`, or that `index_decision_prior` actually reads only the keys it
   declared as `requires`. `curate_run()` still swallows every curator's
   exceptions (unchanged by this diff), so if a future curator's code drifts
   from its own declared spec — reads a key it forgot to declare, or a
   maintainer edits the function body without touching the `CuratorSpec`
   three lines above it — the topo sort still produces a *plausible*,
   confidently-computed order that's wrong relative to actual data flow,
   and the only symptom is a silently missing/stale card field. The new
   machinery replaces "trust the list order" with "trust the declared
   spec matches the code" — a different, not smaller, trust requirement,
   and one with no test enforcing it (`tests/test_run_curation.py::TestCuratorTopoSort`
   exercises the sort algorithm against synthetic specs, not the real
   specs against the real function bodies).
   **Fix direction:** a lightweight runtime assertion — e.g. `curate_run`
   snapshots `card.keys()` before/after each curator call in a debug/test
   mode and asserts the new keys are a subset of that curator's declared
   `provides` — would catch spec/code drift the topo sort itself cannot.

4. **Medium — `evolver.promote_skill_candidates` bridges two independent,
   hand-synced "is this outcome good enough to learn from" vocabularies
   with a hardcoded translation, instead of sharing one.**
   `src/run_curation.py:164-175` (`classify_outcome`) computes `success_class`
   from `{status, goal_achieved}` into one of `success` /
   `done-not-achieved` / `done-unverified` / `partial` / ...; separately,
   `skills.extract_skills` (`src/skills.py:261`) filters candidates by its
   own, differently-shaped rule: `status == "done" and goal_achieved is not
   False`. These two classification schemes currently agree by
   construction (`done-unverified` is defined as "achieved is not False"),
   but nothing ties them together — they're two hand-maintained
   encodings of the same underlying judgment, reconciled only by the new
   glue code at `src/evolver.py:396` (`if card.get("success_class") not in
   ("success", "done-unverified"): continue`) which then *re-synthesizes*
   a fake `status: "done"` (`:401-408`) to satisfy `extract_skills`'
   filter, with an inline comment acknowledging the mismatch ("hardcoded to
   what it actually checks rather than passed through from the card's own
   (possibly differently spelled) status field"). If `run_curation` ever
   adds a new `success_class` value that should also count as
   learnable (e.g. a future "partial-achieved"), or if `extract_skills`'
   own filter changes, this translation drifts silently — there's no shared
   enum, no single function both sides call to answer "is this outcome
   usable for skill extraction."
   **Fix direction:** promote one shared predicate (e.g.
   `decision_prior.is_learnable_outcome(card) -> bool`, alongside the
   already-extracted neutral `decision_prior.py`) that both `evolver.py`'s
   filter and `skills.extract_skills`' internal filter call, so the two
   taxonomies can't independently drift.

5. **Low-Medium — the planning-depth vocabulary is hand-duplicated across a
   Python `frozenset` and a hand-written prompt string, with no shared
   source and a silently-forgiving parser on either side of a future
   mismatch.** `src/navigator.py:52` declares
   `PLANNING_DEPTHS = frozenset({"plan", "one-shot", "thin-plan",
   "spawn-sub-goal"})`; `src/navigator_prompt.py:114-142`
   (`PLANNING_DEPTH_ADDENDUM`) independently spells out the same four
   values as English prose the model reads. Nothing generates one from the
   other. `parse_decision()` (`src/navigator.py:220-222`) coerces any value
   outside `PLANNING_DEPTHS` — including a value the prompt just told the
   model to use, if someone edits the addendum without touching the
   frozenset, or vice versa — to `"plan"`, silently, no error, no log. A
   drift here wouldn't break anything observably; it would just quietly
   make the shadow-only agreement data (the entire point of this feature)
   wrong, and nobody would know because the failure mode is indistinguishable
   from "the model chose plan."
   **Fix direction:** generate `PLANNING_DEPTH_ADDENDUM`'s enumerated list
   from `PLANNING_DEPTHS` (or at minimum add a test that greps the addendum
   string for every member of the frozenset) so the two can't silently
   diverge.

6. **Low — `evolver.promote_skill_candidates`'s "reuse this cycle's
   adapter" comment is false for the more common invocation path.**
   `src/evolver.py:737-742` calls `promote_skill_candidates(adapter=adapter,
   ...)` with the comment "Reuses this cycle's adapter (already built for
   the LLM analysis above) instead of constructing a second one." That's
   true only when `run_evolver`'s own `adapter` parameter was passed in
   explicitly — true for `loop_finalize.py:647`
   (`run_evolver(adapter=adapter, ...)`), but `heartbeat.py:823`, the other
   real caller (and the one driving the autonomous, unattended cycle this
   feature exists to serve — `run_evolver(dry_run=dry_run, verbose=verbose,
   notify=escalate, min_outcomes=5)`), never passes `adapter`, so it stays
   `None` throughout `run_evolver`. `_llm_analyze` (`src/evolver.py:181-192`)
   builds its own local adapter internally when its `adapter` param is
   `None` — that build is scoped to `_llm_analyze`'s own local variable and
   never propagates back to `run_evolver`'s `adapter`. So for the
   heartbeat-triggered path, `promote_skill_candidates` builds a *second*,
   independent `MODEL_MID` adapter (`src/evolver.py:412`) in the same
   cycle — not a correctness bug (both adapters are equivalent), but the
   comment documents an invariant ("at most one adapter per cycle") that
   doesn't hold for the primary trigger, which will mislead whoever next
   needs that guarantee (e.g. for a rate-limit/connection-pool argument).
   **Fix direction:** either thread the adapter `_llm_analyze` builds back
   out to `run_evolver`'s local variable so later stages genuinely reuse
   it, or fix the comment to say "reuses the adapter only when the caller
   supplied one explicitly."

7. **Low — the `prefixes.py` extraction leaves two live names bound to one
   registry object, and only one of them is load-bearing.**
   `src/handle.py:57-60` re-exports `prefixes.PREFIX_REGISTRY` as
   `handle._PREFIX_REGISTRY` (among others) for backward compatibility.
   Because `apply_prefixes()` (`src/prefixes.py:118`) closes over the
   `PREFIX_REGISTRY` name in its own module, mutating the shared list
   in place (`handle._PREFIX_REGISTRY.append(...)`) works, but *reassigning*
   `handle._PREFIX_REGISTRY = [...]` (an easy mistake for someone editing
   `handle.py` who doesn't know the real owner moved) would silently no-op
   — `apply_prefixes` would keep using `prefixes.py`'s original list, and
   the new prefix would parse as if nothing changed, with no error anywhere.
   Not exercised today (current tests only read `_PREFIX_REGISTRY`, never
   reassign it), but it's a classic re-export footgun sitting on the exact
   file (`handle.py`) most people will still reach for when adding a new
   magic prefix, since `prefixes.py` is a name few will think to check
   first.
   **Fix direction:** either keep the re-export read-only in spirit (a
   comment at the binding site saying "append via `prefixes.PREFIX_REGISTRY`
   directly, never reassign this name") or drop the backcompat alias for
   `_PREFIX_REGISTRY` specifically (the rule list, unlike the function/dataclass
   names, has no obvious reason external code would import it by the old
   name) and make `handle.py` reference `prefixes.PREFIX_REGISTRY` directly
   wherever it's used.

---

**Considered, not flagged as a finding:** the recursion check-in's only
safety valve for unbounded goal-pass recursion is a best-effort `notify.emit`
wrapped in a blanket `try/except Exception: pass` (`src/director.py:1064-1065`)
— if the notify channel is silently broken, a runaway `continue` chain gets
no visibility and no stop mechanism beyond `InterruptQueue`, which the design
doc itself already flags as having a drain-window gap. This is explicitly
decreed behavior ("doesn't stop the goal until the user wants it to" — ralph-
style optimistic default is the point), not an accidental gap, so I'm not
scoring it as an architectural defect — but it's worth naming for whoever
next asks "what actually bounds runaway spend on a recursive chain," since
today the answer is "nothing, by design, other than a message that might not
arrive."
