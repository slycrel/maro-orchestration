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
`claim` row and declines if a live claim stands. Liveness is
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
repeats nothing.

**Failure.** A job that raises is marked done WITH its error, not left
pending. It has already had its effect on whatever it touched before it
raised, and a sweep re-running it would repeat that half.

**Append-only.** The store is written by two processes that can overlap. The
ten-round destructive-rewrite arc (r1–r10, this same month) is the record of
what read→transform→rewrite does to a store under exactly those conditions.
Nothing here rewrites a line, so nothing here can lose one; a torn row costs
one record, announced, via `read_jsonl_announced`.

## Off by default

`tail.spawn` defaults False. The spawn does not change what the tail does,
but it changes WHERE its LLM spend and store writes happen, and that wants
burn-in on a real workload before a fresh install inherits it. Off — or when
the spawn cannot be made — the jobs run inline, which is phase-1 behaviour
exactly.

## What was probed

`tests/test_tail_jobs.py` (30 tests) and `tests/mutation/tail_jobs.json`
(28 must-detect mutants, **28/28 accounted for**; 27 on the first sweep).

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
