---
status: record
---

# Adversarial review (Skeptic) — `git diff b2dc34d..HEAD`

Scope: recursive-goal check-in (director.py/notify.py), planning-depth shadow
(navigator.py/navigator_prompt.py/navigator_shadow.py), director_evaluate
skip/error split, R1 architectural cleanup (prefixes.py, decision_prior.py,
run_curation.py curator topo-sort, evolver.py skill_candidate consumer).

Verification performed: read the full diff (3417 lines), read the current
source (not just the hunks) for every touched function, hand-traced the new
topological sort against the real `_CURATOR_SPECS` registry to confirm it
reproduces the old hand-written `CURATORS` order, cross-checked every
declared `provides`/`requires` against what each curator function actually
reads, and ran the full affected test files plus
`test_recall.py`/`integration/test_integration.py`/`regression/test_regression.py`
— all green, so the "pure refactor, byte-identical order/prompt" claims in
the diff's own commentary are not hallucinated.

---

## 1. [Medium] Recursive check-in fires an optimistic "still running" notification *before* the continuation is actually enqueued — a false-positive on exactly the failure path it exists to catch

**Evidence:** `src/director.py:1222-1241` (continue) and `:1248-1268` (narrow):

```python
if action == "continue":
    try:
        from task_store import enqueue as _ts_enqueue
        new_depth = depth + 1
        _origin = _advance_origin_with_checkin(          # <- fires notify.emit("recursion_checkin", ...) HERE
            task, new_depth, "continue", reasoning, summary_for_user)
        _cont_task = _ts_enqueue(                         # <- can raise; caught by the SAME except below
            lane="agenda", source="loop_continuation", reason=reason,
            parent_job_id=job_id, continuation_depth=new_depth, origin=_origin,
        )
        followup_task_id = _cont_task["job_id"]
    except Exception as exc:
        log.warning("escalation continue: failed to enqueue continuation: %s", exc)
```

`_advance_origin_with_checkin` (`director.py:1070`) unconditionally calls
`_fire_checkin` (`director.py:1018`) — which emits the `recursion_checkin`
notify event, telling the user "This goal is still running in the
background... no reply means keep going" — *before* `_ts_enqueue` runs. Both
calls sit inside the same `try` block.

**Failure scenario:** `task_store.enqueue` (`src/task_store.py:154-177`)
takes a file lock and does an atomic write (`_lock_task` / `_atomic_write`)
— a real, reachable failure mode (lock contention, disk full, a stale lock
file, `_check_cycle` raising on a corrupt `blocked_by` graph). If it raises
on a `new_depth` that has already crossed the check-in threshold: the user
has already been told "the goal is still running, no need to do anything,"
`followup_task_id` stays `None`, `EscalationDecision.action` is still
`"continue"`, and **no continuation task was created** — the goal chain is
now silently dead. `handle_queue.py`'s only escalation-decision
notification path fires for `action == "surface"`
(`src/handle_queue.py:55-68`); `continue`/`narrow` with a `None`
`followup_task_id` triggers no operator alert anywhere. The one mechanism
whose entire purpose is "tell the user the truth about a long-running goal"
gives a false "keep going" precisely when the goal has stopped.

**Not tested:** `tests/test_escalation.py::test_checkin_notify_failure_does_not_block_enqueue`
covers the reverse case (notify raises, enqueue still succeeds) but there is
no test where `task_store.enqueue` raises after the check-in threshold is
crossed — grepped the whole file, it isn't there.

**Fix direction:** call `_ts_enqueue` first; only advance/fire the check-in
after the continuation task exists (or fire the check-in with the truth —
"enqueue failed, escalating" — on the exception path instead of silence).

---

## 2. [Medium] `promote_skill_candidates`' unconsumed-candidate scan is an unbounded full-runs-directory walk, wired on by default into the periodic (~every 10 heartbeats) evolver cycle

**Evidence:** `src/run_curation.py:1085-1105`:

```python
def find_unconsumed_skill_candidates(limit: int = 20) -> List[dict]:
    root = _runs_root()
    ...
    for d in root.iterdir():
        ...
        cp = d / "run_card.json"
        ...
        card = json.loads(cp.read_text(encoding="utf-8"))
        sc = card.get("skill_candidate")
        if isinstance(sc, dict) and sc.get("flagged") and not sc.get("consumed_at"):
            out.append(card)
    out.sort(key=lambda c: c.get("started_at") or "", reverse=True)
    return out[:limit]
```

Every run directory ever created gets its `run_card.json` opened and
JSON-parsed on every call — the `limit` only trims the *return*, it doesn't
bound the scan. `evolver.py:1246` wires this in with
`scan_skill_candidates: bool = True` as the default (no config gate, per the
diff's own note "no new config key... existing scan_* kwarg pattern"), and
`run_evolver()` is invoked automatically both from `heartbeat.py:823`
(`_run_evolver_bg`, roughly every ~10 heartbeats per this repo's own
architecture doc) and from `loop_finalize.py:647` (run-cadence). This repo's
own retention decree (`feedback_data_retention`: never auto-delete run
data) means the runs directory only grows — and this box's own GOAL_BRAIN
entry from the same session already found "~700 run dirs" during an
unrelated trace. `recall.py`'s equivalent scan
(`find_prior_attempts`) is explicitly "mtime-ordered, capped" for exactly
this reason; this new scan has no such bound.

**Failure scenario:** not a crash — a silently-growing per-tick tax on every
autonomous background cycle, disk I/O + JSON parse × (every run this box
has ever curated), forever, with no cap besides eventual human intervention.
Exactly the "silent shared-resource spend that grows without the user
asking for it" class this repo's own citizenship posture (`feedback_good_system_citizen`)
flags as unacceptable elsewhere.

**Fix direction:** either cap the scan window (mtime-ordered like
`find_prior_attempts`, stop once `limit` unconsumed candidates found or a
time/count ceiling is hit) or maintain a small persistent index of
flagged-unconsumed handle_ids instead of re-deriving it from a directory
walk every cycle.

---

## 3. [Low] The new curator topo-sort doesn't detect duplicate `provides` keys — the exact "silent, no error" failure mode the fix claims to close is still reachable via a different route

**Evidence:** `src/run_curation.py:931-963` (`_topo_sort_curators`):

```python
provider_of: Dict[str, str] = {}
for spec in specs:
    for key in spec.provides:
        provider_of[key] = spec.name          # <- last declaration silently wins
```

If a future curator is added that (by mistake) declares a `provides` key
another curator already provides, there's no check for it — `provider_of`
just takes whichever spec appears last in `_CURATOR_SPECS`, and every
consumer's dependency edge silently points at that curator instead of the
"real" one, with zero error, zero warning. The whole point of this R1 fix,
per its own comment, was "a future miner inserted out of order... raises
loudly... not buried" — but a duplicate-provides bug produces exactly the
silent-wrong-behavior class this was built to prevent, just via a collision
instead of an ordering gap. Not live today (verified: the 9 real specs have
no overlapping `provides`), but it's a gap in the guarantee the diff's own
commentary asserts ("a broken graph must fail loudly... never silently
produce a plausible-looking-but-wrong order" — `tests/test_run_curation.py::TestCuratorTopoSort`
docstring), and no test exercises the duplicate-provides case (only
missing-provider and cycle are tested).

**Fix direction:** raise in `_topo_sort_curators` when a key appears in more
than one spec's `provides`.

---

## 4. [Low] Check-in cadence/threshold config coercion has no floor after the min/max swap, and no floor at all on `checkin_first_depth`

**Evidence:** `src/director.py:996-1016`:

```python
def _checkin_first_depth() -> int:
    try:
        return int(config_get("recursion.checkin_first_depth", 2))
    except (TypeError, ValueError):
        return 2
    # no clamp: a configured negative/zero value is returned as-is

def _checkin_jitter() -> int:
    try:
        lo = int(config_get("recursion.checkin_jitter_min", 4))
        hi = int(config_get("recursion.checkin_jitter_max", 7))
    except (TypeError, ValueError):
        lo, hi = 4, 7
    if lo < 1:
        lo = 1
    if hi < lo:
        lo, hi = hi, lo          # <- lo can become negative again here, un-re-floored
    return random.randint(lo, hi)
```

**Failure scenario:** a config typo (`recursion.checkin_jitter_max: -5`,
`recursion.checkin_jitter_min: 4`) triggers the swap branch: `lo, hi =
-5, 4`. The floor-to-1 check already ran *before* the swap, so `lo=-5`
survives; `random.randint(-5, 4)` can return a negative jitter, which can
push `origin["next_checkin_depth"]` *below* the current `new_depth` —
meaning the very next continuation, and every one after it until the
config is fixed, re-fires a check-in (spamming the user) instead of
respecting the 4-7 cadence. Separately, `recursion.checkin_first_depth: -1`
(or `0`) makes the very first check-in fire on the *first* continuation
(`new_depth=1 >= -1`), contradicting the decree's "3rd goal pass" contract
with no error or warning anywhere. Both require a misconfigured value to
trigger — genuine but narrow.

**Fix direction:** re-floor `lo` (and clamp `hi >= lo`) after the swap in
`_checkin_jitter`; clamp `_checkin_first_depth()`'s return to `>= 0` (or
`>= 1`) the same way the jitter function already tries to.

---

## 5. [Low] Skill-candidate sweep has a TOCTOU race across concurrent `run_evolver` invocations — no lock around read-then-process, only around the final consumed-stamp write

**Evidence:** `evolver.promote_skill_candidates` (`src/evolver.py:1140-1223`)
reads unconsumed candidates, calls `extract_skills` (an LLM round-trip), and
only *afterward* calls `mark_skill_candidate_consumed` (which does use
`file_lock.locked_rmw` for the write itself). There's no lock spanning the
read→LLM-call→mark sequence. `run_evolver` is reachable from at least three
independent entry points with no cross-process mutual exclusion between
them: `heartbeat.py`'s background thread (guarded only by an in-process
`_evolver_active` flag, `heartbeat.py:775`), `loop_finalize.py`'s run-cadence
trigger, and a manual CLI invocation (`cli.py:747`).

**Failure scenario:** an operator runs the evolver CLI manually while the
heartbeat daemon's background evolver thread is also mid-cycle (or two
heartbeat processes briefly overlap around a restart) — both see the same
unconsumed candidate batch, both pay for an `extract_skills` LLM call on
the identical outcomes, and both then race to stamp `consumed_at` (safe
individually, no corruption, but the duplicate LLM spend already happened).
Narrow window, not the most severe finding here, but a real gap the
`locked_rmw` choice on the write side implies the author was aware of the
general concurrency risk without closing the read-side half of it.

**Fix direction:** either gate the sweep behind the same `proc_lock`-style
singleton the heartbeat daemon itself uses, or make
`find_unconsumed_skill_candidates` + processing atomic per-candidate (claim
before processing, e.g. write a provisional `consumed_at` stamp first, roll
back on failure) instead of claim-after.

---

## On test shallowness (explicitly asked to scrutinize)

The new tests are, on the whole, **not** shallow — they exercise real code
paths rather than re-asserting mocks:
- `test_evolver.py`'s `_sc_flagged_run` helper drives the real
  `create_run_dir` → `finalize_run` → `curate_run` pipeline (real
  `classify_outcome`/`inventory_assets`/`scrape_scripts`/`flag_skill_candidate`),
  not a hand-built card fixture — a genuine end-to-end wiring test.
- `TestCuratorTopoSort` tests the validator directly against deliberately
  broken specs (missing provider, cycle) rather than only re-checking the
  derived order.
- `test_escalation.py`'s new check-in tests correctly distinguish "fires
  exactly at threshold" / "suppressed until next threshold" / "notify
  failure doesn't block enqueue" as separate, non-redundant cases.

The one place tests are *incomplete* rather than shallow: the check-in
suite has no test for enqueue failure *after* a successful check-in fire
(finding #1) and no test for the duplicate-`provides` case in the topo sort
(finding #3) — both are the natural "adversarial" cases a reviewer would
add next to the existing suites, not different tests that were faked.

---

## Claims verified true (not hallucinated)

- `_topo_sort_curators(_CURATOR_SPECS)` really does reproduce the exact old
  hand-written `CURATORS` order (hand-traced Kahn's-algorithm execution
  against all 9 specs).
- Every curator's declared `requires` matches what its function body
  actually reads from `card`/`meta` (checked `classify_outcome`,
  `inventory_assets`, `excerpt_result`, `spend_transparency`,
  `promote_skills_lite`, `scrape_scripts`, `flag_skill_candidate`,
  `rescue_partial`, `index_decision_prior` against their `CuratorSpec`
  declarations line by line).
- `prefixes.py`'s single scan-loop is behaviorally identical to the old
  two-mechanism `_apply_prefixes` (literal rules always get first crack per
  while-iteration; the persona pattern rule, appended last, is only tried
  after every literal rule fails to match that iteration — matches the old
  `if not changed:` fallback exactly).
- All touched test files plus `test_recall.py`, `integration/test_integration.py`,
  and `regression/test_regression.py` pass on the current tree — the "pure
  refactor, nothing else changed" and "byte-identical order" claims hold up
  under actual execution, not just reading the diff.

No `success_class` inconsistency was found between `flag_skill_candidate`'s
gate and `evolver.promote_skill_candidates`' redundant re-check (both use
the identical `("success", "done-unverified")` tuple, and `classify_outcome`
guarantees `success_class in that set` implies `goal_achieved` is never
`False` — so the done≠achieved invariant is preserved through the new
consumer path, not silently violated).
