---
status: record
---

# Async tail phase 3 — the tail becomes a process, 2026-08-20

Jeremy, opening the chunk: *"That's not really async if we're blocking the
CLI call right? is this an exec level spawn of another process to make it
truly async in cases like that? Seems like something that's solveable with a
little effort."*

Both halves were right, and the second half is the interesting one: the fix
is small, but only after the thing being moved stops being a closure.

## What phase 1 actually did

`_defer_learning_post_notify` (handle.py) appended a callable to a
module-level dict. `_drain_deferred_learning` popped and called them
**synchronously, same process, same thread**, from `handle()`'s finalize
block, after the `run_completed` notify. The maintenance twin
(`defer_maintenance_post_notify` / `drain_deferred_maintenance`,
loop_finalize.py) had the same shape.

So the tail was *reordered* past the notify, not *backgrounded*. A
notify/Telegram consumer saw the answer first and genuinely benefited. A
caller waiting on process exit — `python3 -m handle`, a script, a CI step —
waited for the whole tail and got nothing from phase 1.

Measured on the 2026-08-19 sol-advisor run
(`research/2026-08-19-sol-advisor-efficiency-claim.md`): deliverable written
at 14m37s, process alive until 31m08s. **16m31s, 53% of wall clock, after the
answer existed** — closure audit, adversarial claim review, lesson extraction,
~10 skill-promotion validations, ~8 skill rewrites, ~10 knowledge-node
validations, evolver, business signal scan.

## The one design decision

A closure cannot cross a process boundary, and a module-level dict cannot
survive one. Those are the same constraint, and satisfying it is the whole
chunk: **the registration becomes a serializable record, and the drain
becomes a function over the store of records.**

```
<run_dir>/build/tail_jobs.jsonl          # append-only
  {"event": "job",   "seq": 1, "kind": "learning",    "spec": {...}}
  {"event": "job",   "seq": 2, "kind": "maintenance", "spec": {...}}
  {"event": "spawn", "pid": 4242, "spawned_at": ...}
  {"event": "claim", "pid": 4242, "claimed_at": ...}
  {"event": "done",  "seq": 1, "ok": true, "finished_at": ...}
  {"event": "claim", "pid": 4242, "released_at": ...}
```

Everything else follows from it:

- **`maro finalize-tail --handle-id X`** reconstructs the tail from that
  store and runs it. It is the standalone entry point the design named as
  missing; `_finalize_cli_deferred_learning` could not be it, because it takes
  an in-memory result object.
- **`handle()`'s finalize** calls `drain_or_spawn`, which either starts a
  detached child and returns, or runs the same jobs in the same place phase 1
  ran them. **Both lanes run the same executor over the same records** — the
  point being that a fallback which re-implements the work is a sibling that
  drifts, and this arc has paid for that shape more than once.
- **The module-identity hazard is retired, not worked around.** handle.py is a
  documented `python -m handle` entry point: run that way it executes as
  `__main__`, so `loop_finalize`'s `import handle` loads a SECOND copy whose
  registry the finalize block never drains. That was found in the 3-lens
  review of `707a541` and fixed by *placing* the maintenance twin in another
  module — a rule every future author has to re-derive. A store keyed by
  handle_id has no module identity to get wrong.

## What the record has to carry, and why it is not read back from the run dir

The design note said "run records are already durable on disk — so a child
process can re-open the run by id". That is true of most of it and **false of
the one field that matters**:

- `build/loop-*-log.json` persists `result_length`, **not** `result`.
- `build/loop-*-step-NN.md` is a rendered artifact
  (`f"# Step {n}: {text}\n\n{result}\n"`), not the field — recovering
  `result` means parsing a synthesized header back off, for every step that
  wrote one.
- The tail's step-lesson extraction and skill crystallization read `result`.

So the step outcomes ride the handoff whole, serialized from the objects the
parent already holds. That is the "explicit state handoff" the design asked
for: the parent writes what it has instead of the child guessing it back.

The adapter travels as an identity (`backend`, `model_key`) rather than an
object, and the child rebuilds: exact backend first, then the same model on
auto-detect, then the ordinary default. A tail that runs on a neighbouring
backend is worth more than a tail that does not run.

## The contracts

**Overlapping tails.** One tail process per handle_id — a drainer appends a
`claim` row and declines if a live claim stands. *(Round 1: as first written
this was check-then-act, so it was a comment rather than a mechanism. The
check and the append are one locked transaction now — see below.)* Liveness is
`os.kill(pid, 0)` (portable: the `/proc` checks written for the Linux box
were silently always-false on the dev Mac, 2026-07-08), EPERM counts as
alive, and the claim is host-scoped because a pid from another machine says
nothing about this one.

Tails for *different* runs may overlap. That is not a new hazard: heartbeat
already runs skill maintenance on its own tick concurrently with any run's
tail, and every store these phases touch is lock-protected. Serializing
across runs would be a guarantee this codebase has never had, invented at the
moment nobody asked for it.

**Stranding.** Phase 1's open watch-item was that `handle()`'s `_hid=None`
exception path could strand registered callables with no trace at all. The
record is what changes that: `find_stranded()` reports runs with pending jobs
and no live claim, `maro finalize-tail --sweep` drains them, and heartbeat
runs that sweep on its health tick (grace window 1800s, so it can never race
a child that is still starting). Every job kind is idempotent — lesson
extraction skips rows that already carry lessons, crystallization re-checks
the verdict gate, maintenance is threshold/cadence-based — so a late drain
repeats nothing. *(Round 1 corrected this: threshold-based is NOT idempotent.
`run_post_run_maintenance` advances durable cadence counters, so the sweep
asks per kind and surfaces maintenance it cannot prove safe to repeat.)*

**Failure.** A job that raises is marked done WITH its error, not left
pending. It has already had its effect on whatever it touched before it
raised, and a sweep re-running it would repeat that half.

**Append-only.** The store is written by two processes that can overlap. The
ten-round destructive-rewrite arc (r1–r10, this same month) is the record of
what read→transform→rewrite does to a store under exactly those conditions.
Nothing here rewrites a line, so nothing here can lose one; a torn row costs
one record, announced, via `read_jsonl_announced`. *(Round 1: true of LINES
and, as first written, false of JOBS — two registrars could allocate the same
`seq` outside the lock, and the executor is keyed by seq.)*

## Off by default

`tail.spawn` defaults False. The spawn does not change what the tail does,
but it changes WHERE its LLM spend and store writes happen, and that wants
burn-in on a real workload before a fresh install inherits it. Off — or when
the spawn cannot be made — the jobs run inline, which is phase-1 behaviour
exactly. *(Round 1: not exactly — the inline lane was rebuilding the adapter
instead of using the run's live one. Fixed; the claim holds now.)*

## What was probed

`tests/test_tail_jobs.py` (30 tests) and `tests/mutation/tail_jobs.json`
(28 must-detect mutants, **28/28 accounted for**; 27 on the first sweep).
*(Superseded by round 1: 47 tests, 50 mutants. The numbers below describe the
pre-review state and are left as written — what a sweep accounted for before
four adversarial seats read the same file is the point of keeping them.)*

The claim the chunk exists for is asserted directly:
`test_spawned_tail_outlives_the_parent_call` requires `drain_or_spawn` to
return **with the job still pending** — the parent is free to exit right
there — and the job to be gone later, done by a process whose pid is not
ours. A reordered-but-synchronous tail cannot pass it: it would return
`ran=1` with nothing pending.

The three detachment properties are asserted on the arguments of a real
spawn (the spy calls through), because their effect is invisible from inside
the test: a new session so a group kill aimed at the run does not take the
tail, `/dev/null` on stdin, and output to `build/tail.log` rather than the
parent's stdout. The last one is the subtle one — an inherited pipe keeps
`out=$(maro handle ...)` blocked until the last writer closes it, so the
caller would wait for the entire tail while believing it had been handed an
answer.

The surface-refresh half was verified against a REAL run dir rather than the
fixture, because a `log.debug`-swallowed exception would have made it a
no-op that nothing failed on: mtimes advance on
`captains_log_slice.jsonl`, `run_card.json` and the report HTML.

The mutation sweep's one survivor was real and is now pinned:
`find_stranded`'s live-claim filter could be deleted with a green suite,
which would report a working child as stranded once its tail outran the
grace window. Age is not abandonment.

## What was NOT probed, and the residuals

- **The end-to-end saving is unmeasured.** The 53% figure is phase 1's cost,
  measured on one run; what a real workload's wall clock looks like with
  `tail.spawn` on is the burn-in, and it belongs on the runtime box with a
  real goal, not here.
- **`knowledge_web.maybe_consolidate()` is still in-process**, in the same
  `finally` block, after the tail dispatch. It is marker-gated to once per
  ~24h, but when it fires it is the dream cycle (decay + a size-gated LLM
  compress) and the caller waits for it. It is post-answer work that the
  chunk's own logic says should be a job; it was left alone because the ask
  named the tail phases, and moving a non-run-scoped phase into a per-run
  store is its own decision. **This is the remaining "the process is still
  busy after the answer" surface** and should be the first thing burn-in
  looks at.
- **Cost attribution moved slightly.** The parent used to scope both drains
  to `_tail_lid` (the last loop id in metadata); each job now scopes to its
  own recorded `loop_id`. For the normal path these are the same id; for a
  job registered by the escalation lane the new one is more precise. Nothing
  reads it differently — but nothing has re-derived a cost table under the
  spawn lane either.
- **The in-process registries still exist** as the fallback for a handle
  that owns no run-dir to record into. That lane is rare (nearly every path
  through `handle()` opens a run), and its own refresh blocks are largely
  inert there anyway, since there is no run dir to re-render. Worth a census
  before deleting them; not worth deleting on a guess.

---

# Adversarial round 1 — 2026-08-20, four codex seats

Skeptic, Architect, Minimalist, Expert QA, via `/adversarial-review` against
`f4d3b26~1..HEAD`. **Verdict: REJECT.** Six distinct HIGHs, all reproduced,
zero hallucinations. Every one of them lives in what this chunk ADDED — the
round's own stated prior, holding again.

Two of the four seats independently reached the same top finding by the same
route, and all four reached it eventually. It is worth stating plainly because
the chunk's own commit message got it wrong:

> **Append-only is not atomic.**

Byte-level safety says no line is overwritten. It says nothing about a
decision made from a read that a later write depends on — and every
state-dependent write here was one of those:

- `_next_seq` read the rows, then appended outside the lock. Two registrars
  could allocate `seq: 1` twice; both lines are physically on disk and one job
  is invisible, because the executor is keyed by seq. Probed literally: two
  `seq: 1` job rows in, `pending == ['maintenance']` out.
- `run_jobs` checked the standing claim, then appended its own. Two drainers
  could both read "unclaimed" and both run. The "one tail process per
  handle_id" contract was a comment, not a mechanism.

Both now go through `_transact`: read, decide, append, one lock
(`file_lock.locked_write` is reentrant per thread, so the inner append does
not deadlock).

**The default lane had lost the run's adapter.** `handle()` called
`drain_or_spawn(adapter=None)`, so even with `tail.spawn` OFF the tail rebuilt
from the recorded identity — dropping a `FailoverAdapter`'s live fallback
state and any caller-injected adapter, in the lane that ships on by default.
"Phase-1 behaviour exactly" was therefore false in the one place it was load
bearing. `_handle_impl` builds its own adapter when the caller passes none
(`handle.py:1270`), so the object is not recoverable from `handle()`'s scope:
it is remembered at record time and used where it still exists. The identity
stays the fallback for the child, which cannot have the object.

**"Every job kind is idempotent" was too broad.** `run_post_run_maintenance`
advances DURABLE cadence counters (`evolver_cadence_tick` and the inspector
twin), so a child that died after a tick and before its `done` row would have
that tick counted twice by the sweep — firing a meta-cycle early. Threshold-
based is not idempotent. The sweep now asks per kind: learning re-drains,
maintenance whose drain already started is surfaced under `needs_operator`.
Note the direction — this hazard did not exist in phase 1, because phase 1 had
no sweep to re-run anything. The recovery mechanism introduced it, and a
correct-looking blanket claim hid it.

**A failed job was visible nowhere.** It is marked done, so it is not pending,
and pending was all `find_stranded` reported — while the comment two lines
above claimed the sweep reported the error. `state()` carries `failed` now,
and the CLI, the sweep result and heartbeat all show it.

Also fixed: claim/done/release append failures were computed and discarded (a
claim we could not publish is a claim we do not hold — the drain declines
now); the sweep truncated candidates to `limit * 4` newest-first BEFORE
filtering, and heartbeat passes `limit=3`, so twelve healthy recent runs could
hide an old abandoned tail from every tick forever (it walks until it has
`limit` stranded runs, oldest first); an unreadable store read as an empty one,
which would allocate `seq: 1` over whatever it already held; a failed surface
refresh was swallowed at debug level, leaving a store that says the tail
finished beside a card that is stale; and `tail.spawn` used plain truthiness,
so a quoted `"false"` in YAML turned the spawn ON — the one direction the
OFF-by-default rollout exists to prevent.

## The one the seats did not find

`runs.current_run_dir()` is a **ContextVar** — process-local. The spawned
child pinned nothing, and `runs.record_llm_call` **no-ops when no run-dir is
active**, with record-mode ON by default. So `tail.spawn=on` would have
silently stopped capturing the tail's LLM calls into `<run_dir>/build/calls/`,
and the run card's `n_calls` — counted from exactly those files — would have
under-reported calls the run actually paid for. The codebase already documents
this hazard one lane over (`llm.py:1368`, where the fetch tool's capture dir
is resolved in the parent and handed down explicitly *because* a fresh process
sees None).

Found by reading during the tree freeze, not by a seat. Four adversarial
reviewers looked at a process-spawn diff and none of them asked what ambient
process state the child does not inherit — worth remembering as a lens gap
next time the subject is a new process rather than new logic.

## Receipts

Mutation spec 28 → 50, **50/50 accounted for** (1 standing equivalent). The
first sweep after the fixes returned six SKIPs and two survivors: the SKIPs
were anchors bound to lines my own fixes had rewritten — re-anchored before
the sweep was called green, because a skipped mutation is not a passed one —
and both survivors were single-kind fixtures that could not tell a whole-run
drain from a filtered one, or a silent skip from an announced one. Tests
30 → 47. Full suite green.

## What is still not proven

Everything named in "What was NOT probed" above still stands, plus one
correction: the DEFAULTS.md line claiming the process "exits at the answer"
was an overclaim and has been narrowed — `maybe_consolidate` still runs inline
in the same `finally` block. The seats found that too, independently.

Per the skill's own coverage note: one round surfaces roughly 75–80% of what
two find, and across ~50 recorded rounds the prior round's fix layer is the
single likeliest home of the next round's worst finding. **This fix layer is
unreviewed.**
