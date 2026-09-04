---
status: living
---

# Successor v1 — design note (Phase 2, the whole system)

*2026-09-04. Implementation is mine (Jeremy: "implementation, per usual, is
yours"). This note comes to him as a vision read. Brief = the drift review's
§8 (`docs/history/2026-09-04-holistic-drift-review.md`, main) as amended by
D7–D17 in `successor-plan.md`; contract practice =
`contract-testing-input.md`; boundary spec = `docs/CONTRACTS.md` + the Phase 1
behavior suite. Where a shape is copied from the Python or the frozen port, the
sentence that justifies it is next to it (anti-lift rule).*

## 0. What v1 is

One process, in Go, that takes a goal and: routes it, plans it, executes it
through backends, judges what came back, records what happened, learns from
it in a way the next run can be measured against, and tells the user the
outcome where they asked. **The learn loop is closed in v1** (D14). Recursion
is designed in and exercised one level deep. Nothing in the engine truncates,
aborts, or "corrects" a thought (D13, D15, D16).

The prototype's seven never-closed items are the design targets, not later
work: cost envelope as a measured target; learning that measurably changes
behavior; fork with a join and scoped memory; findings that close (removal as
a deliverable); state that is re-read or not written; always-on inside one
process; worker isolation deferred (D12, revisit later).

## 1. Two populations — the type system says which is which (D16)

**Thoughts** flow through. **Process artifacts** are the engine's own.

```go
// A Thought is opaque to process code. It can be stored, hashed, routed,
// handed to a judge or a backend, and rendered. It is never parsed,
// truncated, or "fixed" by the engine. Judges produce Verdicts ABOUT it.
type Thought struct {
    Kind       ThoughtKind   // goal | prompt | response | step_result | deliverable | lesson_text | ...
    Body       []byte        // any size; the contract declares it UNCONSTRAINED
    Hash       Hash          // content address; what receipts and edges point at
    Provenance Provenance    // who produced it: user | model(call receipt) | tool(event) | engine(stage)
    Bytes      int           // reported, never enforced
}
```

Every process artifact (Event, Verdict, Receipt, RunCard, Outcome, Lesson
envelope, Proposal, Budget report, Delivery receipt) is a typed struct with a
registered contract: derived from the Go type by a generator, declared by a
human where a generator cannot know, given a lifecycle, and explained in an
answer key (contract input §1, §5, §15). A lesson is a process envelope
around a thought: the envelope (stage, provenance, effect, standing) is
contract-hard; the text is a Thought.

The one place a thought's size matters is a backend's context window. That
is the backend's constraint, declared on the adapter (`MaxInputBytes`,
`enforced-at: backend`), and the engine's response to it is a *routing*
decision (chunk through the read verb, pick a bigger window, hand the
artifact by reference), recorded as an event — never a silent slice.

## 2. The spine — stages with typed seams

```
Intake → Intent → Plan → Execute → Judge → Record → Learn → Deliver
```

Each stage is a function `func(ctx, *Run, in) (out, error)` over typed
artifacts. A `Run` driver composes them and owns the control flow; early
exits and deferrals are written in the driver, in the open (L48: the
original's shape is a rule, so the successor's shape must be readable as one).
Every stage boundary emits a lifecycle event carrying `handle_id`, `run_id`,
`goal_id`, and `loop_id` when one exists (FINDINGS #9: NOW runs get real
lifecycle events, not incidental ones).

Stage contracts, minimum:

| Stage | Reads | Writes | Verdict it can emit |
|---|---|---|---|
| Intake | goal Thought, origin, interrupts (registered store — FINDINGS #1) | handle_inputs row, `goal_received` event | rejected (authority), duplicate (re-run identity) |
| Intent | goal, prior attempts (recall/dispatch slice) | `lane_chosen` event with reasons | clarity: ask-user vs proceed (yolo config) |
| Plan | goal, recall/loop slice, completion standard | Plan artifact (steps as Thoughts + step metadata) | plan_reviewed (pre-flight, recorded call) |
| Execute | Plan, backend | per-step Receipts, step_result Thoughts, step artifacts | step: done / blocked / unclear + confidence |
| Judge | step results, deliverables, claims | Verdict artifacts (closure, provenance, fabrication) | achieved / not / unknown + confidence + basis + falsifiers |
| Record | everything above | outcomes row, run card, captain's log, call records | — |
| Learn | receipts, verdicts, outcomes | Proposals (data), lifecycle transitions, Δ measurements | — |
| Deliver | run card, verdict | delivery receipt event, escalation row | delivered / undeliverable |

Lanes are **configurations of the same spine**, not code forks. NOW = Plan
yields one step, Execute is one call with URL pre-fetch, Judge is
self-verdict or provenance, Learn records a slim outcome. AGENDA = full
decomposition, per-step judge, closure with restart, full learn. The suite
already drives both through one driver; the engine keeps that shape.

## 3. Goals are a tree — recursion and fork/join (§8 item 6)

```go
type Goal struct {
    ID     GoalID
    Parent GoalID   // zero for a root
    Root   GoalID
    Text   Thought
    Lane   Lane     // chosen at Intent; a child may differ from its parent
    Origin Origin   // user | queue | child_of(step) | shadow | rerun_of
}
```

A run executes one goal. A step may `Fork` child goals; `Join` awaits them
under a declared policy (`all | any | quorum(n) | first_verdict`) with the
parent's context cancellation propagating down. A join produces a Verdict
about the children, not a merged thought — merging is a plan step the parent
chooses. Memory is scoped by ancestry: recall at any level walks up the tree
(own scope, then parents to root, then global), and a child's learned
proposals carry their ancestry so promotion can weigh where they came from.
v1 runs one level deep in production; the suite pins a two-level scripted
tree with `all` and `first_verdict` joins, so the primitive is exercised
before anything depends on it.

Goroutines and `context.Context` are the win being spent here: a child run is
a goroutine under the parent's context; cancellation is structural, not a
killswitch file.

## 4. Backends — one seam, recording on the construction path

```go
type Request struct {
    Purpose  Purpose        // mandatory; every call says why it exists
    Messages []Thought
    Tools    []Tool
    Budget   Budget         // {Name, Limit, Why} — a test fails on an empty Why
}
type Adapter interface {
    Name() string
    Capabilities() Caps     // agent tools? context window? cost table?
    Complete(ctx, Request) (Response, error)
}
```

`Purpose` mandatory and `Budget.Why` required are kept from the frozen port
because Go's type system makes them free and the prototype's unrecorded
pre-flight calls were exactly the omission they prevent. **Recording,
metering, and receipts happen in the one constructor that hands out
adapters** (`backends.New(cfg, store)`); there is no wrapper to forget and
no bare adapter to inject (FINDINGS #6). Tests use a `Scripted` backend
built by the same constructor, so scripted calls are recorded too.

Backends in v1: `subprocess` (claude / codex CLIs with `tool_events`
receipts — the agentic executors), `anthropic` (API, for judges and small
calls), `scripted` (tests, replay). Fireworks/grok as opt-in. The 1-shot
challenger (§8) uses the same seam, so its receipts are comparable.

## 5. Judges — verdicts are process facts about thoughts

```go
type Verdict struct {
    Kind        VerdictKind  // step | closure | provenance | fabrication | stuck | delivery
    Outcome     Outcome      // the vocabulary is per Kind and registered
    Confidence  float64      // always present
    Source      Source       // deterministic | judge(model, receipt) | self | operator
    Basis       []Ref        // what it looked at (thought hashes, receipts, events)
    Falsifiers  []Thought    // what would overturn it (closure only)
    Recoverable bool         // may a later verdict overrule this one
}
```

Design rules, from D13/D16 and 08-02 recovery-over-correctness:

- **Distinguishable terminal cases first, thresholds second.** A stuck
  verdict says *why* (repeated fingerprint, no new artifacts in N steps,
  backend refusing) so the caller's recovery state machine can act on the
  reason. The threshold that fires it is config with a written Why.
- **The Sheriff is evidence-based and independent.** It runs as its own
  lane (§9) over events and receipts; it never counts iterations.
- **Verdicts overrule by standing.** A closure verdict can demote a run's
  status; a self-verdict cannot promote one; an operator restamp outranks
  both and is recorded as such. The overrule order is a registered table.
- **Deterministic checks run before any model judge**: provenance of claims,
  file existence, fabrication diff, receipt completeness. A model judge
  never sees a claim a deterministic check already refuted.

## 6. Memory — recording and recall

Stores are JSONL ledgers where `docs/CONTRACTS.md` registers them (outcomes,
lessons, captain's log, events, handle inputs, call records, run cards), so
the pack moves learning between engines (D10, D3). Each ledger is a **table
transport** with a declared writer set, reader set, and vocabulary
consistency block (contract input §14). Derived indexes (SQLite, in-memory)
are caches rebuilt from the ledgers, never sources.

Writes go through locked read-modify-write primitives with explicit commit
points (L51 from Phase 1a: design the seams in, don't fix them post-hoc).
Every writer either succeeds, fails loudly, or is declared fail-soft with its
collapse set (contract input §10). "Best-effort" is a declaration, not an
adjective.

Recall has three slices as in the prototype's design, because the shape is
right: **dispatch** (re-run identity, prior attempts, guard), **loop**
(per-step injection: lessons, standing rules, skills, playbook — each with a
budget that has a Why and is reported, never silently truncated), and
**navigator** (decision context). Scope walks the goal tree (§3).

Learned data carries, always:

```go
type Learned struct {   // the envelope around a lesson/skill/rule/proposal
    ID         LearnedID
    Kind       LearnedKind   // lesson | skill | standing_rule | playbook_entry | config_suggestion
    Stage      Stage         // candidate | provisional | medium | long | canon | contested | tombstone
    Text       Thought
    Provenance Provenance    // minted_from (run, receipt, model), quarantine flags, ancestry
    Effect     []Effect      // Δ measurements with their control and denominator
    Standing   Standing      // score, confirmations, last_verified, decay basis
}
```

## 7. Self-improvement — the loop, closed and measured (D14, D11, D17)

Clean-room to the intent in VISION and the arch skills, not to
inspector/evolver/graduation as built. The loop:

```
Observe   receipts, verdicts, costs, friction signals (per run, in the tail lane)
Diagnose  deterministic classifiers first, a model lens second → FailureClass events
Propose   a Proposal = Learned data: lesson | skill | rule | config_suggestion
Apply     only through the registered apply surfaces (below); never code
Measure   Δ vs a control, on replay and live; champion–challenger vs the 1-shot
Lifecycle promote (effect OR tenure) / demote / decay / tombstone, recorded
```

**Apply surfaces are enumerated and registered** — recall injection, step
shaping, backend/tier selection, playbook, a config suggestion held for the
operator. If a proposal cannot be expressed as data on one of these surfaces
it is not a proposal, it is an escalation.

**Measurement is the point.** Three instruments, all in v1:

1. **Replay Δ** — decision-bearing calls replayed with and without a learned
   item against the recorded receipt (the prototype's Δ-gate shape:
   action-match, no logprobs). Cheap, deterministic, the unit of the effect
   route.
2. **Live shadow** — a post-run, isolated challenger re-run of eligible
   goals, arm randomized among `star` (orchestration as a prompt), `plain`
   (bare goal), and `harness-without-X` (ablation of one learned item or
   mechanism). Shadow output is stamped and never ingested by any learning
   path; enforced by test.
3. **Champion–challenger standing (D17).** When a `plain` or `star` arm
   matches the harness on a goal family, that is recorded as a learning
   about the *mechanism*, and the mechanism's standing decays like any other
   learned item (the prototype's competence-redundancy decay, re-derived).
   No up-front sort of what ages well; the engine finds out.

**The loop is closed** when: a proposal minted from run A is applied in run
B, run B's verdict and receipts are recorded, the Δ against a control is
computed and written on the proposal, and a lifecycle transition follows
from it. The behavior suite pins this end to end with scripted backends and a
deterministic Δ before any model is involved. The v1 exit criterion is one
closed loop per proposal kind, plus one live closed loop on this box.

**Autonomy boundary (unchanged since Feb):** proposals that would act
outward, spend beyond the subscriptions, or change authority config are held
on the escalation surface; everything else applies as data. Guardrails stay a
human boundary.

## 8. One process — lanes, timers, lifecycle (D12)

One binary. One root context. Lanes are goroutines:

- **Intake** — CLI now; a socket/file drop for other front ends; Telegram
  via Hermes later. Each accepted goal becomes a run under the executor.
- **Executor** — bounded concurrency; child runs (§3) under parent contexts.
- **Tail** — deferred post-run work (learn, curate, render) as a lane, not a
  spawned process; drained on shutdown.
- **Sheriff** — ticks over events/receipts; emits stuck verdicts.
- **Timers** — in-process periodic sweeps (consolidation, shadow sweep,
  decay) with intervals in config carrying a Why; no cron, no systemd
  timers. A daemon-shaped long life is fine; the OS is not the scheduler.
- **Delivery** — renders outcomes to where the goal came from.

Shutdown drains lanes under a deadline; anything unfinished is checkpointed
and **resumed on next start** (never-closed: checkpoint resume is automatic).

## 9. Budget and metering — targets, never constraints (D13, D15)

Every call is metered (tokens in/out/cache, wall time, estimated cost) into
its receipt. Budgets are `{Name, Limit, Why}` **targets**; an overage emits
an event and a line in the delivery, and the run continues. The Manti
envelope ("~1–3 min for cents") is one registered target among many. The
only stops are the authority gate (spend beyond the subscriptions, outward
acts) which pauses and escalates, and fail-closed on malformed authority
config. Optimisation is a later pass, on data these receipts produce.

## 10. Delivery — the user hears the outcome (§8 item 1)

A run is not finished until a delivery receipt event exists saying where the
outcome went and whether it arrived. The rendering is plain words first
(outcome, verdict with confidence, what it cost, what's uncertain), detail by
reference. Escalations are rows on the escalation surface plus a push where
configured. A NOW answer and an AGENDA report share the same delivery
contract.

## 11. Workspace and contracts (D10)

Own root: `~/.maro-go/workspace` by default, `MARO_GO_WORKSPACE` override,
resolved path printed before any write (the 2026-08-16 live-ledger scar,
structurally). Layout mirrors the shared spec's families (`runs/`,
`memory/*.jsonl`, `output/`, `config.yml`) so the pack can move learning both
ways; the two engines never share a root.

The contract registry is per artifact: a **generated** half derived from the
Go types, a **declared** half (absence semantics, unknown-value handling,
used-for, retry guidance, fail-soft collapse sets, `unconstrained` on every
Thought field), a **lifecycle** (stable / transitional / internal-loose /
hardened-legacy with a design flag / design-pending), and an **answer key**
with wire examples and evidence crumbs. Regeneration diff is the review. The
Go engine is the provider and ships its own reference reader (forward: a
newer row reads; backward: an older row reads). The Python suite becomes a
consumer of the shared spec.

## 12. Verification

- The Phase 1 behavior suite is the shared spec; a Go harness maps its
  driver. New v1 behaviors (fork/join, closed learn loop, delivery receipt,
  lifecycle events per lane) are added to the suite first, red, then built.
- Durability and concurrency get a fault-injection battery below the
  workspace boundary (FINDINGS #8), ported in shape from the Python unit
  batteries, not in code.
- Every module: behavior tests + one codex review = DONE (standing rule).
  Mutation kill-proof on the suite itself at the v1 exit.
- Circularity guard (contract input §17): the registry's declared half is
  checked once against an independently written pair before v1 exit.

## 13. Build order (kernel first; each step lands green with a review)

1. Workspace root, config (two-tier, strict parsing, precedence declared),
   contracts scaffold (generator + declared + answer key), Thought/Verdict
   types.
2. Backend seam with recording, metering, receipts, `Scripted`.
3. Stores: locked RMW primitive, the registered ledgers, run dir.
4. Spine, NOW lane end to end, lifecycle events, delivery receipt.
5. AGENDA: plan, execute, step judge, closure, Sheriff lane.
6. Record + recall (dispatch and loop slices), re-run identity.
7. Learn loop: propose → apply surfaces → replay Δ → lifecycle.
8. Fork/join, scoped recall, two-level suite scenario.
9. Lanes: tail, timers, shutdown/resume.
10. Shadow lane + champion–challenger standing.
11. One real goal on this box; Manti target measured and reported;
    pack import of the Python workspace's learned data.

## 14. Deliberate divergences from the prototype

Events carry `handle_id`; interrupt intake is a registered store; recording
lives on the constructor; no process code slices a thought; verdict is a
struct with confidence and source, never a status overload; lanes are
configs; learned things are data with lifecycle and provenance; one process
with in-process timers; fork has a join; delivery is a receipt. Each is a
never-closed item or a FINDINGS entry, not taste.

## 15. Open design questions (mine to settle, listed for visibility)

- Binary and workspace naming (`maro-go`, `~/.maro-go`) — placeholder until
  the first real goal runs.
- JSONL everywhere the spec shares; whether a SQLite index is worth having
  in v1 or waits for the first slow recall.
- The first real goal to run on this box (candidate: the Manti case, because
  it carries a target).
- Interfaces beyond CLI in v1: a file/socket drop is enough for Hermes to
  dispatch; Telegram direct waits.
