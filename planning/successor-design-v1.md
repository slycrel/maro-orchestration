---
status: living
---

# Successor v1 — design note (Phase 2, the whole system) — v1.1

*2026-09-04. Implementation is mine (Jeremy: "implementation, per usual, is
yours"); this note comes to him as a vision read. Brief = the drift review's
§8 (`docs/history/2026-09-04-holistic-drift-review.md`, main) as amended by
D7–D17 in `successor-plan.md`; contract practice =
`contract-testing-input.md`; boundary spec = `docs/CONTRACTS.md` + the Phase 1
behavior suite. v1.1 = v1.0 rewritten against the r1 design review (codex,
three lenses, ledger at the review dir's `verdict-r1.md`; converged findings
listed in §17). Where a shape is inherited, the justifying sentence is beside
it (anti-lift rule).*

## 0. What v1 is

One process, in Go, that takes a goal and: routes it, plans it, executes it
through backends, judges what came back, records what happened, learns from
it in a way the next run is **measured** against, and tells the user the
outcome where they asked. The learn loop is closed in v1 (D14). Recursion is
designed in and exercised one level deep. No process code truncates, aborts,
or "corrects" a thought (D13, D15, D16). Removal is a deliverable from the
first commit (§8 item 9).

The prototype's never-closed items are design targets: cost envelope as a
measured target; learning that measurably changes behavior; fork with a join
and scoped memory; findings that close; state that is re-read or not written;
always-on inside one process. Worker isolation is deferred (D12, revisit
later).

**Home (D9):** branch `successor`, source under this repo's `go/`, landed via
the shared `scripts/land.sh`, tested with the shared suites, reviewed the
house way. The frozen `go-port` branch is read, never edited (D4). **Python
lift (D8):** every shared-contract change states whether Python needs a
provider-side repair (allowed) or a behavior redesign (forbidden; rejected or
separately authorized). **Work posture (D6):** one build lane at a time on
this box; a second lane only in its own worktree.

## 1. Foundations — records, identity, the two populations

### 1a. Everything durable is an immutable, versioned record

```go
type RecordHeader struct {
    ID        RecordID     // ULID: time-ordered, unique, allocated by the sequencer
    Schema    SchemaVer    // "outcome/2" — contract version of THIS record kind (D3)
    Seq       uint64       // per-workspace monotonic; the happens-before order
    RunID     RunID        // zero for workspace-scope records
    Subject   Ref          // what this record is about (a goal, run, thought, learned item)
    Supersedes RecordID    // zero unless this record overrules an earlier one
    At        time.Time    // observation time; never used for ordering (Seq is)
    Origin    Origin       // production | experiment — see §9
}
```

Status, standing, and "current verdict" are **folds** over records, never
fields something mutates. Compaction can never change semantics because
nothing depends on file position.

### 1b. Thoughts are content-addressed, immutable, and opaque to process code (D16)

```go
type ThoughtRef struct {           // what every other record carries
    Hash    Hash                   // blake3, domain-separated by Kind, versioned ("b3v1:…")
    Kind    ThoughtKind            // goal | prompt | response | step_result | deliverable | lesson_text | chunk
    Bytes   int64                  // derived at store time from the body, never caller-supplied
}
// Construction is private to the thought store: bytes in → ThoughtRef out, hash
// computed and verified at that boundary; on read the hash is re-verified.
```

The engine stores, routes, hashes, renders, and hands thoughts to judges and
backends. It never slices one. Interpretation happens only at **declared
interpretation boundaries**, each a typed function from a ThoughtRef to a
process artifact, validated once: `Intent(goal) → IntentAssessment`,
`Claims(deliverable) → ClaimSet`, `Plan(goal, …) → Plan`, `Judge(…) →
Verdict`. A model call is such a boundary; so is a deterministic parser.

**Overflow is a boundary decision, never a slice.** When a thought exceeds a
backend's declared `MaxInputBytes` the engine does exactly one of: hand it
**by reference** (the backend can read the artifact — subprocess agents can),
produce **derived chunk thoughts** (Kind `chunk`, each with byte-range
provenance and a completeness manifest, so the whole is reconstructable and
the receipt says which chunks the backend saw), or return a typed
`backend_incapable` outcome. Which happened is a recorded routing event.

Thought fields in every contract are declared `unconstrained` (someone
looked; any value of the type is legal) and the suite feeds an oversized,
an empty, and a non-UTF-8 body through every boundary.

### 1c. Process artifacts

Every artifact in §2–§10 is a record with a `RecordHeader` and a registered
contract. v1 hand-writes the declared schema and reference reader for the
edges the shared spec covers; a generator for the derived half arrives when
hand-maintained types demonstrably drift (contract input §1 — deferred, not
dropped).

## 2. The workspace journal — one writer, many lanes

All durable state is **one append-only journal** of records plus the
content-addressed thought store. One goroutine, the **sequencer**, owns the
journal: lanes submit `Command`s (idempotency key + records to append +
preconditions), the sequencer validates preconditions, allocates `Seq`,
appends, fsyncs, and acknowledges. Multi-record invariants (outcome + run
card + delivery receipt; verdict + supersedes; fork + join settlement) are
one command and therefore atomic. Readers see only acknowledged records.

The registered ledgers of the shared spec (`memory/outcomes.jsonl`,
`lessons.jsonl`, `captains_log.jsonl`, `events.jsonl`, `handle_inputs.jsonl`,
`build/calls/*.json`, `run_card.json`) are **materialized views** the
projector writes from the journal, byte-compatible with `docs/CONTRACTS.md`
B-entries, so the pack moves learning both ways (D10). Lanes never write
them. Any index (in-memory, SQLite later) is a rebuildable projection with a
committed source offset.

**Admission:** one process per workspace root, enforced by a lease file with
PID + monotonic process epoch; a second process refuses to start. Every
command carries an idempotency key, so restart replay cannot duplicate a
mutation. Locked read-modify-write exists only for the lease and for view
files the spec requires to be rewritten whole.

## 3. Goals, runs, fork, join

```go
type Goal struct { RecordHeader; Parent GoalID; Root GoalID; Text ThoughtRef; Lane Lane; Origin GoalOrigin }
type RunAttempt struct { RecordHeader; Goal GoalID; Attempt int; Config ConfigSnapshot }
type Fork struct { RecordHeader; Parent RunID; Step StepID; Children []GoalID /* fixed at creation */; Policy JoinPolicy }
type Join struct { RecordHeader; Fork RecordID; Winner GoalID; Terminal map[GoalID]ChildState; Cancelled []GoalID }
```

A fork's membership is fixed when written. A join is one settlement record:
which child satisfied the policy, the terminal state of every child, which
were cancelled and acknowledged. Cancellation executes a **persisted**
decision: the sequencer writes the Join, then the executor cancels losers'
contexts and waits for their drain acknowledgement before the parent step
continues. Late results from a cancelled child are recorded as
`late_result` and ignored by the parent (they remain evidence).

v1 join policies: `all`, and `first_verdict` (first child whose closure
verdict says achieved, others cancelled). Nothing else until a scenario needs
it. Memory scope walks Goal ancestry (own → parents → root → workspace).
v1 runs one level deep in production; the suite pins a two-level scripted
tree for both policies including crash-before-settlement and
crash-after-settlement restarts.

## 4. Backends — one invocation boundary, recording on it

```go
type Invocation struct {            // allocated by the run shell before any backend runs
    RecordHeader
    Purpose   Purpose               // enum, queried on receipts (why this call exists)
    Request   ThoughtRef            // the exact backend-visible request, hashed
    Backend   BackendSnapshot       // name, model, version, capabilities AS SEEN at decision time
    Target    *Budget               // optional {Name, Limit, Why}; only when a target applies (D13)
}
type Receipt struct { RecordHeader; Invocation RecordID; Response ThoughtRef; Attempts []Attempt; Usage Usage; ToolEvents []ToolEvent; Terminal TerminalState }
```

The **run shell** owns the invocation boundary: it writes the Invocation,
calls a *pure* adapter (`Complete(ctx, req) (Response, error)` — no store
access), records every attempt and stream terminal state, and commits the
Receipt. There is no adapter to inject that bypasses this, because adapters
never record; the shell does (FINDINGS #6, restated at the right layer).
Capability and model are snapshotted into the Invocation so a routing
decision and its receipt agree.

v1 backends: `subprocess` (claude / codex CLIs, agentic, `tool_events`
receipts) and `scripted` (tests, replay). One judge path rides subprocess in
v1. Anthropic API, Fireworks and others are later additions through the same
seam; none is in v1.

## 5. The spine — pure decisions, one shell

```
Intake → Intent → Plan → Execute → Judge → Record → Deliver ⟶ (tail) Learn
```

Each stage is a **pure decision function** over validated, immutable inputs
returning an output plus a list of declarative effects (records to commit,
invocations to make). The **run driver** is the shell: it runs stages, makes
invocations through §4, submits effects to the sequencer as commands, and
emits one lifecycle event per stage boundary carrying `handle_id`, `run_id`,
`goal_id` (FINDINGS #9). Control flow — early exits, deferrals, retries — is
written in the driver in the open. Tests exercise decisions without a store.

Lanes are **configurations** of the driver: NOW = single-step plan, one
invocation, self-verdict or provenance judge, slim outcome; AGENDA = full
decomposition, per-step judge, closure with restart. The driver is one
function with plan-cardinality and judge configuration as parameters; there
is no second driver.

**Interrupts** are a registered record (`Interrupt{Target RunID, Action,
IssuedSeq}`) consumed only by the run driver at stage boundaries, acknowledged
by a record, idempotent by ID, expired when their target reaches a terminal
state. Completion-vs-pause races resolve by `Seq` (FINDINGS #1).

### 5a. Run state machine (the owner of "finished")

```
created → executing → judged → recorded → delivered(attempted|accepted|acknowledged) → post_run_done
                                   ↘ undeliverable (retryable, never re-executes the goal)
```

Each transition is one sequencer command with a named owner: executor
produces facts; judges produce verdicts; the driver commits Record; the
delivery lane commits delivery states; the tail commits `post_run_done`.
Learning becomes eligible at `recorded`, never earlier. A run's product
status is settled at `recorded`; delivery and post-run states do not change
it. Shutdown drains under a deadline; anything past `recorded` resumes from
its last committed transition; anything before it is marked `recoverable`
and restarts from the last committed idempotent stage (no mid-call resume).

## 6. Judges and verdicts

```go
type Verdict struct {
    RecordHeader                    // Subject = the run/step/claim judged; Supersedes when overruling
    Kind       VerdictKind          // step | closure | provenance | fabrication | stuck | delivery
    Outcome    Outcome              // per-Kind registered vocabulary
    Confidence float64
    Source     Source               // deterministic(check) | judge(invocation) | self | operator
    Basis      []Ref                // thoughts, receipts, events looked at
    Falsifiers []ThoughtRef         // closure only
    Direction  Direction            // may_demote | may_promote | both — replaces a bare Recoverable bool
}
```

Verdicts are append-only assertions. The **effective verdict** for a subject
is a pure fold `Effective([]Verdict)` over a registered resolver table
(source standing × kind applicability × direction × Seq); every resolution
writes a `Resolution` record naming the candidates. Deterministic checks
(provenance, existence, fabrication diff, receipt completeness) **contribute
evidence records with confidence and observation time**; they short-circuit
only a final-success verdict, never the judge's view of the claim — an
observation failure (a check that could not run) is a distinct outcome from a
refutation.

**Sheriff** is a lane with a *separate evaluator* reading only committed
records (its own state machine, no shared interpretation code with the
executor). It emits `stuck` verdicts that name the evidence (repeated
closure fingerprint, no new artifacts past a watermark while the backend
reports idle, backend refusing). Thresholds are config carrying a Why and are
reported, not enforced as stops; the caller's recovery state machine acts on
the reason (contract input §11). Tests: stalled executor, stalled store,
stalled Sheriff, slow-but-live backend, reordered events, Sheriff restart.

## 7. Memory — recording and recall

Learned data:

```go
type Learned struct {
    RecordHeader                    // one record per REVISION; Supersedes links revisions
    Kind       LearnedKind          // lesson | mechanism   (v1: exactly these two — §8)
    Stage      Stage                // candidate | observed | provisional | effective | canon | contested | tombstone
    Scope      ScopePath            // goal-ancestry path or workspace
    Family     FamilyKey            // registered goal family (§9)
    Text       ThoughtRef
    Provenance Provenance           // minted_from run/receipt/model, quarantine flags
}
type LifecycleTransition struct { RecordHeader; Learned RecordID; From, To Stage; Evidence RecordID /* an EffectMeasurement, or a tenure observation */ }
type Application struct { RecordHeader; Learned RecordID; Invocation RecordID; Representation ThoughtRef /* exactly what the backend saw */ }
```

**Recall is one query**, `Recall(purpose, scope, standing) → RecallSelection`,
where `RecallSelection` records every candidate considered, the deterministic
ordering, each inclusion or exclusion with its reason, and the projected
size. The three prototype "slices" are three purposes passed to this one
query; no separate subsystems (anti-lift: the contract does not force three,
and Go's typed query does not want them). A learned item counts as
**applied** only when an `Application` record links it to an Invocation whose
request hash contains its representation. A budget on recall is a target
with a Why; exclusion is recorded, never silent; excluded items remain
discoverable by reference.

## 8. Self-improvement — closed and measured, with a valid experimental unit

```
Observe   committed receipts, verdicts, usage, friction signals (tail lane, after `recorded`)
Diagnose  deterministic classifiers first, a model lens second → FailureClass records
Propose   a Learned revision at stage `candidate` (v1 kinds: lesson, mechanism)
Apply     through ONE v1 apply surface: recall injection (an Application record per use)
Measure   an Experiment → EffectMeasurement
Lifecycle a transition whose Evidence is an EffectMeasurement (or a tenure observation that
          can reach `observed` only — never an efficacy-bearing stage)
```

### 8a. The experimental unit

```go
type Experiment struct {
    RecordHeader
    Hypothesis  Learned RecordID            // exactly one item under test (one-item ablation)
    Population  FamilyKey                   // registered BEFORE assignment
    Assignment  Assignment                  // paired_replay | randomized_live | shadow_arm
    Treatment, Control Snapshot             // backend/model/version, config, applied-item SET, seed where available
    Outcome     OutcomeSpec                 // predeclared: which verdict/usage dimensions, direction, margin
    MinSamples  int; Denominator int
    Evaluator   EvaluatorRef                // identity + version of whatever scores outcomes
    Exclusions  []Rule                      // fixed before observation
}
type EffectMeasurement struct { RecordHeader; Experiment RecordID; Delta Delta; N int; Uncertainty Unc; Verdict EffectVerdict /* helpful | harmful | null | insufficient */ }
```

Two instruments in v1:

1. **Paired replay (plumbing + first evidence).** The same decision-bearing
   invocation is re-issued twice under an identical captured environment,
   treatment with the item applied and control without, both scored by the
   predeclared outcome spec against an **outcome oracle** (the run's closure
   verdict, a deterministic check, or a scripted oracle) — never against the
   historical receipt. Action-match against the old receipt is retained only
   as a diagnostic ("did the injection change the request at all").
2. **Randomized live.** Eligible production runs in the item's family are
   assigned treatment/control at intake; outcomes accrue under the
   experiment's denominator. This is where D11's "measured behavior change"
   is actually established.

The loop is **closed** when a `candidate` from run A has an Application in a
later run, an Experiment with a valid control, an EffectMeasurement, and a
LifecycleTransition citing it. **v1 exit for the loop:** a blinded scripted
scenario in which a planted helpful item is promoted and a planted harmful
item is demoted by the same machinery; then one live experiment on this box
with a predeclared outcome spec, real receipts, and n ≥ MinSamples. Tenure
(exposure without regression) may move `candidate → observed` and may trigger
re-measurement, expiry, or tombstoning; it never promotes to `effective`.

**Apply surfaces:** v1 has one, recall injection, because the causal proof
needs one and the second surface (backend selection) has no learned item yet
that cannot use the first. Adding a surface is a Finding with a concrete
item, not an enum edit. Proposals that would act outward or change authority
config are held on the escalation surface (autonomy boundary unchanged).

## 9. Experiments are a separate population (D17 without contamination)

Records carry `Origin: production | experiment`. **Experimental records live
in their own journal segment** with their own reader capability; the
production readers (recall, diagnose, propose, pack export, cost reports)
are typed so they cannot return experimental records. The only artifact
that crosses from experiment to production is an `EffectMeasurement`, written
by the evaluator lane. This replaces the impossible "stamped and never
ingested" with a rule a type checker and a test can hold: every production
reader is contract-tested with poisonous experimental rows.

**Champion–challenger (D17).** The `mechanism` learned kind represents a
harness mechanism (recall injection, decomposition, a judge). A shadow arm
(`plain`: bare goal; `star`: orchestration-as-prompt; `ablate(m)`: harness
without mechanism m) is an Experiment with `Assignment = shadow_arm`, run
post-hoc, black-box, in a scratch workspace, eligible only for goals whose
registered family is read-only. "Matches" = the predeclared outcome vector
within the family's margin at n ≥ MinSamples. A matching challenger yields an
EffectMeasurement of `null` for the mechanism, and the mechanism's standing
decays through the same lifecycle as any learned item. No up-front sort of
what ages well; the engine measures it.

## 10. One process — lanes, timers, lifecycle (D12)

One binary, one root context, one workspace lease. Lanes: **intake** (CLI
only in v1; the typed intake seam is the one place other front ends will
attach), **executor** (bounded concurrency, child runs under parent
contexts), **sequencer** (§2), **projector** (views), **tail** (post-run:
observe/diagnose/propose/measure, enqueued after `recorded`), **sheriff**,
**evaluator** (experiments), **timers** (in-process periodic sweeps —
consolidation, decay, experiment scheduling — with intervals in config
carrying a Why; no cron, no systemd timers), **delivery** (outbox with
idempotency keys). Shutdown and restart per §5a.

## 11. Budget and metering — targets, never constraints (D13, D15)

Every Invocation and Receipt carries usage; budgets are optional targets
with a Why; an overage is an event and a line in the delivery, and the run
continues. Subscription ceilings are **external**: the engine does not
estimate remaining allowance and never stops on cost. The authority gate
covers exactly two things: outward acts in the registered authority classes,
and a provider's hard refusal (surfaced as a backend outcome, not a cost
decision). Malformed authority config fails closed for outward acts only.

## 12. Delivery — proven at the user's edge (§8 item 1)

Delivery states per origin: `attempted` (rendered, handed to transport),
`accepted` (transport confirmed — for CLI, written to the terminal and the
process still alive), `acknowledged` (the origin's own ack where one exists:
a message id, a read receipt). Only the highest state an origin supports
counts as delivered. Pending or failed delivery is retried from the outbox
without re-running the goal. Rendering is plain words first: outcome, the
effective verdict with confidence, cost against targets, what is uncertain,
detail by reference.

## 13. Workspace and contracts (D10, D3)

Own root: `~/.maro-go/workspace`, `MARO_GO_WORKSPACE` override, resolved
path printed before any write. The shared spec's families appear as
projector-written views (§2). Every record kind carries its `Schema`
version; a contract change ships a migration function, a supported-reader
range, and golden fixtures in both directions (newer row reads by an older
reader with declared absence semantics; older row reads by the newer). The
pack has a versioned envelope, per-record schema versions, an import
transaction ID, duplicate detection by record ID, a quarantine stage for
imported learned items (they enter at `candidate`, never higher), and
vocabulary mapping. Renames are delete + add.

Contract registry per shared edge: a hand-written declared file (absence
semantics, unknown-value handling, used-for, retry guidance, fail-soft
collapse sets, `unconstrained` on every thought field), a lifecycle
(`stable | transitional | internal-loose | hardened-legacy+design-flag |
design-pending`), and an answer key. Every reader that degrades a failure to
a legitimate value is declared fail-soft with its collapse set or does not
exist.

**Personas as lenses (§8 item 8):** a lens is a judge/render configuration
over immutable artifact references — a `LensRef` on an Invocation of Purpose
`judge` or `render`. It never changes what was recorded and is never an
execution path. v1 ships the neutral lens and the seam.

## 14. Subtraction and findings (§8 item 9)

`Finding{RecordHeader; Kind: add|remove|change; Subject; Evidence; Owner;
Closure criterion}` is a workspace record. Removal acceptance = an absence
test (the behavior no longer reachable), a census of references/config/store
rows, migration disposition, and the same review as an addition. **Step 0 of
every build step below is subtraction**: enumerate what the step proposes and
delete what no decree, edge contract, or named scenario requires.

## 15. Verification — falsifiable system outcomes

The Phase 1 behavior suite is the shared spec; a Go harness maps its driver.
v1 adds, red first: fork/join with both crash points; the closed-loop
discrimination scenario (§8); experimental-row poisoning of every production
reader; oversized/empty/non-UTF-8 thoughts through every boundary; two
processes on one root (admission); kill/restart at every state-machine
transition; concurrent verdict arrival in all pairwise orders; delivery
outbox under dropped transport and restart; pack import idempotency with
unknown fields and malformed rows; removal of one mechanism with absence
proof.

A named **must-detect battery** replaces "mutation kill-proof": drop a
receipt, ingest an experimental row, reorder a lifecycle transition, bypass
Application, falsely mark delivered, slice a thought — each must fail a
test. DONE for a vertical slice = its named edge behaviors green plus the
must-detects that touch it red-when-mutated; codex review is evidence
discovery on risky slices, not a completion ritual. The registry's declared
half is checked once against an independently written pair before v1 exit.

## 16. Build order (by fan-in; each step: subtract → build → edge tests → land)

1. Records, identity, schema versions; thought store with hashing at the
   boundary; the workspace lease.
2. Journal + sequencer + projector (views byte-compatible with the shared
   spec); idempotent commands; restart from committed offsets.
3. Invocation boundary: Invocation/Receipt records, `scripted` backend, the
   run shell's recording path.
4. Run state machine + driver with pure stages; NOW configuration end to
   end; lifecycle events; delivery outbox with CLI acceptance. First real
   delivered answer.
5. Verdict records, resolver, deterministic checks as evidence; Sheriff lane
   with its own evaluator.
6. Recall query + RecallSelection + Application; re-run identity.
7. AGENDA configuration: plan, per-step judge, closure with restart;
   `subprocess` backend live.
8. Fork/join durable records, two-level scenario, cancellation barrier.
9. Tail lane and timers; observe → diagnose → propose at `candidate`.
10. Experiment + EffectMeasurement + paired replay; lifecycle transitions;
    the blinded discrimination scenario.
11. Experimental journal segment + poisoning tests; randomized live
    assignment; shadow arms and mechanism standing.
12. Pack envelope + import quarantine; import the Python workspace's learned
    data at `candidate`.
13. Live acceptance on this box: one goal family, one experiment to
    MinSamples, the Manti target measured and reported, one mechanism
    removed with absence proof.

## 17. Conformance appendix (binding; each row names its mechanism and its evidence)

| Decree / commitment | Mechanism | Evidence (test or artifact) |
|---|---|---|
| D1 contract-not-port | pure stages, records, journal; no Python structure without a sentence | anti-lift sentences in §5, §7 |
| D2/D10 formats shared, roots separate | projector views byte-compatible; own root | view fixtures vs CONTRACTS.md; root printed |
| D3 versioned contracts | `Schema` on every record; migrations; golden fixtures both directions | §13 fixtures |
| D4 go-port frozen | read-only mining; §0 | no commits on go-port |
| D5 clean-room to intent | §8 loop designed from VISION intent; §7 one recall query | this note |
| D6 serialized work | §0 work posture | worktree-per-lane |
| D8 Python lift rule | §0 change rule | each shared-contract change states its class |
| D9 home | §0 | branch/path |
| D11 measured behavior change | Experiment + EffectMeasurement; tenure ≤ `observed` | §8 discrimination scenario; live n ≥ MinSamples |
| D12 one process, no cron | §10 lanes + lease | two-process admission test; no cron in tree |
| D13 targets not constraints | §11; thresholds reported not enforced | overage-continues test |
| D14 full loop in v1 | §8, steps 9–11 | closed-loop record chain exists |
| D15 no cost aborts; ceilings external | §11 | no cost-stop path in code |
| D16 thoughts unconstrained | §1b; overflow is reference/chunk/incapable | boundary tests; must-detect "slice a thought" |
| D17 bitter lesson inside the process | §9 mechanism kind + shadow arms; no up-front sort | mechanism decay via the same lifecycle |
| §8.1 delivery is the product | §12 states; run not finished before delivery | outbox + restart tests |
| §8.2 done is a claim | §6 verdicts, resolver, direction | pairwise-order tests |
| §8.3 learn measurably | §8 | as D11 |
| §8.4 validator not counter | §6 Sheriff evidence; thresholds are reports | Sheriff test set |
| §8.5 artifacts truth, path kept | journal append-only; late results kept; excluded recall discoverable | must-detect "drop a receipt" |
| §8.6 recursion | §3 durable fork/join, scoped recall | two-level scenario |
| §8.7 program not OS, always-on | §10 | as D12 |
| §8.8 swappable substrate/model/persona | §4 seam; §13 lens seam | scripted ↔ subprocess; neutral lens |
| §8.9 edges + removal | §14 Finding; step-0 subtraction | one mechanism removed with absence proof |

## 18. What v1.1 changed from v1.0 (r1 review)

Experiment as the unit of measurement; tenure capped at `observed`;
experimental journal segment instead of "stamped, never ingested"; ThoughtRef
with boundary hashing and explicit overflow behaviors; RecordHeader identity/
version/order on everything; one sequencer and materialized views instead of
per-file RMW; durable Fork/Join; pure stages + one driver instead of `*Run`
mutation; run state machine with delivery states; subscription gate removed;
recall as one query; one apply surface; backends cut to subprocess +
scripted; join policies cut to two; generator deferred; per-module review
gate replaced by slice gates and a must-detect battery; Finding record and
step-0 subtraction; D6/D8/D9 stated; lens seam; interrupts in the lifecycle;
Sheriff independence defined; deterministic checks as evidence not
suppression; build order re-sequenced by fan-in; conformance appendix.
