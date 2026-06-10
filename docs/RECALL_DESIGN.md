# recall() — Shape Definition (goal-brain sequencing, step 3)

**Status:** design pinned 2026-06-10; dispatch slice is the v0 implementation target.
**Upstream:** `GOAL_BRAIN.md` (step 1, the artifact), pressure-test findings (step 2,
GOAL_BRAIN.md Compiled truth). Sequencing per `docs/conversations/2026-05-18-memory-and-goal-brain.md`:
artifact → **recall()** → navigator schema → navigator prompt.
**Downstream:** navigator decision schema (step 4) consumes the navigator-turn slice;
the correspondence edge vocabulary (Mage memory v1) hangs off the shape defined here.

---

## What recall() is

One read seam over every memory substrate. The navigator's (and today, the
pipeline's) single question: **"what do I already know that's relevant right now?"**

From `THREAD_ARCHITECTURE.md`: the substrates exist (memory.py, knowledge_web.py,
knowledge_lens.py, runs/, captain's log, dev-correspondence) but are read piecemeal
at scattered injection sites. **Correction after reading the code (2026-06-10,
same day):** the loop-start read path is already unified in one function —
`_build_loop_context()` (agent_loop.py:2791) — which composes EIGHT memory
substrates into `lessons_context` (tiered lessons, standing rules, decisions,
graveyard resurrection, failure notes, captain's-log read bridge, playbook,
knowledge nodes) plus non-memory contexts (skills, cost, codebase graph). So:

| Site | What it reads | Where |
|---|---|---|
| loop start | 8 memory substrates, one composer | agent_loop.py `_build_loop_context` |
| **dispatch** | **nothing** | — |

The loop-slice work is therefore a **relocation**, not a unification: move the
memory half of `_build_loop_context` behind recall(slice="loop") so the seam owns
it and instruments it; skills/cost/graph stay in agent_loop (they're selection
and planning context, not memory recall). Until that lands, recall()'s loop slice
is a partial composition (4 of the 8) with no caller — do not wire anything to it
before the relocation makes it complete.

The dispatch row is the step-2 finding: the same goal ran ~25× in 35 minutes on
2026-05-17 (findings 1 and 3) because nothing at the task-queue → `handle_task` →
`handle()` boundary asks "have we seen this before, and how did it go?" Origin
ancestry is now *recorded* at every requeue boundary (commit `4e133eb`) but nothing
*consults* it. recall() is the consumer.

## The shape

```python
# src/recall.py  (new module — read-only consumer of the substrates)

@dataclass
class PriorAttempt:
    goal: str            # the matched goal text
    handle_id: str       # run-dir linkage
    status: str          # done | stuck | error | ...
    when: str            # ISO timestamp
    match: str           # "exact" | "near" (similarity >= 0.9)

@dataclass
class ThreadIdentity:
    parent_goal: str     # from origin ancestry (origin.parent_goal)
    parent_handle_id: str
    chain: list[str]     # handle_id chain walked via run metadata, oldest first
    source: str          # task_store | agent_loop | director | direct

@dataclass
class RecallResult:
    thread: Optional[ThreadIdentity]      # None when no ancestry resolvable
    prior_attempts: list[PriorAttempt]    # recent window, newest first
    lessons: str                          # formatted blocks, reuse existing
    standing_rules: str                   #   inject_* formatting verbatim —
    decisions: str                        #   they are already prompt-shaped
    knowledge: str
    sources: dict                         # instrumentation: per-substrate counts + ms

    def as_context_block(self) -> str: ...      # one injectable string, sized cap
    def dispatch_signals(self) -> dict: ...     # {repeat_count, all_failing, window_minutes}

def recall(goal: str, *,
           slice: str = "loop",            # "dispatch" | "loop" | (future) "navigator"
           origin: Optional[dict] = None,  # the 4e133eb ancestry dict
           project: str = "",
           window_hours: float = 24.0) -> RecallResult
```

**Slices** — same seam, different depth. What each consults:

| Substrate | dispatch | loop | navigator (future) |
|---|---|---|---|
| origin ancestry → ThreadIdentity (runs/ metadata) | ✓ | ✓ (pass-through) | ✓ |
| prior attempts (outcomes.jsonl + runs/ recent window) | ✓ | ✓ | ✓ |
| tiered lessons (`inject_lessons_for_task`) | — | ✓ | ✓ |
| standing rules (`inject_standing_rules`) | — | ✓ | ✓ |
| decisions (`inject_decisions`) | — | ✓ | ✓ |
| knowledge nodes (`inject_knowledge_for_goal`) | — | ✓ | ✓ |
| goal-brain artifact (whole file — it's sized to inject whole) | — | — | ✓ |
| correspondence graph walk (Mage v1) | — | — | ✓ |

Dispatch is deliberately thin — identity + history only, no LLM calls, pure local
file reads, fast enough to run on every task dequeue. The loop slice is the four
existing injection sites unified behind the seam (same output, one call,
instrumented). The navigator slice is **defined here but not implemented** — it has
no consumer until step 4/5 ship.

## Dispatch-time behavior (v0 — the implementable slice)

Call site: `handle()` right where origin is already in hand; guard check in
`handle_task()` before invoking `handle()`.

1. **Inject** — `result.as_context_block()` is appended to the ancestry context the
   planner sees. A goal that ran 24× stuck in the last hour now *arrives* with that
   fact attached: "Prior attempts (last 24h): 24 runs, 24 stuck, newest 3m ago."
   Advisory, not gating — the planner/loop can act on it.
2. **Guard** — in `handle_task()` only (the autonomous requeue path; direct
   `handle()` calls from a human are never blocked): if `repeat_count >= 3` within
   60 minutes and **all** of them non-done, do not run. Mark the task error with a
   readable reason ("recall guard: 3 prior attempts in 47m, all stuck — refusing to
   spin") and emit the event. Config: `recall.dispatch_guard` (default **on**),
   `recall.guard_attempts` (3), `recall.guard_window_minutes` (60). The 2026-05-17
   burn is the basis for default-on; the guard only fires when every recent attempt
   already failed, so the cost of a false positive is one delayed retry, while the
   cost of the old behavior was ~25 wasted runs.
3. **Instrument** — every recall() call (any slice) emits a `RECALL_PERFORMED`
   captain's-log event: slice, sources dict, signals, sizes. Per the 2026-05-18
   decision: static now, instrument every tuple from day one, crystallize later.
   A guard fire additionally emits `RECALL_GUARD_TRIPPED`.

**Matching** for prior attempts: normalized exact match first (case/whitespace),
then near-match via the existing `_text_similarity` at ≥ 0.9 — same machinery the
lesson dedup already trusts. Window default 24h; consult `outcomes.jsonl` (status
history) joined with `runs/` metadata (handle ids, origin) — both already on disk.

## Correspondence edge vocabulary (pinned now, walked later)

From the 2026-05-18 Turn-11 sketch — recall() is eventually a bounded weighted walk
from the active thread outward. The edge types, pinned so writes can start carrying
them before the walk exists:

| Edge | Already derivable from | Walk status |
|---|---|---|
| `ancestor-thread` | origin ancestry (4e133eb) | v0: ThreadIdentity.chain IS this walk, depth-limited |
| `sibling-thread` | shared `parent_handle_id` in run metadata | deferred |
| `similar-shape-prior-attempt` | goal-text similarity over outcomes | v0: PriorAttempt match="near" |
| `persona-used` | run metadata / task ledger | deferred |
| `skill-used` | skill outcome records | deferred |
| `lesson-cited` | lesson injection ↔ run linkage | deferred (needs write-side stamp) |

v0 sympathy function = what exists: TF-IDF rank × recency × tier weight. Tuning by
example is a post-navigator concern.

## What this is not

- **Not a new store.** recall() writes nothing except its own instrumentation
  events. Lifecycle (decay/promotion/consolidation) stays in knowledge_web.
- **Not the navigator.** The navigator slice is a contract here, not code. Building
  it before the decision schema (step 4) would invert the agreed sequencing.
- **Not a daemon.** Pure in-process reads at existing call sites (program-not-OS
  invariant).

## Open ends carried forward

- `lesson-cited` edges need a write-side stamp (which lessons were injected into
  which run) before they're walkable. Cheap to add at the loop-slice call site;
  do it when the loop slice lands.
- The navigator slice's goal-brain injection assumes per-thread goal-brains exist;
  today there is exactly one (`GOAL_BRAIN.md`, the project's own). Per-thread
  goal-brain creation is a step-4/5 question.
- Guard thresholds are a made call (3 attempts / 60 min), not measured. Revisit
  against RECALL_GUARD_TRIPPED data once it accumulates.
