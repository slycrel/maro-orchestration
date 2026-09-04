---
status: living
---

# Successor v1 — design note (Phase 2, the whole system) — v1.3

*2026-09-04. Implementation is mine (Jeremy: "implementation, per usual, is
yours"); this note comes to him as a vision read. Brief = the drift review's
§8 (`docs/history/2026-09-04-holistic-drift-review.md`, main) as amended by
D7–D17 in `successor-plan.md`; contract practice =
`contract-testing-input.md`; boundary spec = `docs/CONTRACTS.md` + the Phase 1
behavior suite. v1.3 = v1.2 amended against the r3 design review (codex, Architect +
Skeptic, whole document; ledgers `verdict-r1..3.md` in the review dir — r3
confirmed every r2 root cause resolved and left 18 precise items). §18 lists
what changed. Inherited shapes
carry their justifying sentence beside them (anti-lift).*

## 0. What v1 is

One process, in Go, that takes a goal and: routes it, plans it, executes it
through backends, judges what came back, records what happened, learns from
it in a way the next run is **measured** against, and tells the user the
outcome where they asked — and treats "the user did not hear it" as a failed
mission, whatever the artifacts say. The learn loop is closed in v1 (D14).
Recursion is designed in and exercised one level deep. No process code
truncates, aborts, or "corrects" a thought (D13, D15, D16). Removal is a
deliverable from the first commit (§8 item 9).

The prototype's never-closed items are design targets: cost envelope as a
measured target; learning that measurably changes behavior; fork with a join
and scoped memory; findings that close; state that is re-read or not written;
always-on inside one process. Worker isolation is deferred (D12).

**Home (D9):** branch `successor`, source under this repo's `go/`, landed via
the shared `scripts/land.sh`, reviewed the house way; `go-port` is read, never
edited (D4). **Python lift (D8):** every shared-contract change is classified
*provider repair* (allowed; lands on main through the normal Python chunk
discipline as its own work item) or *behavior redesign* (forbidden unless
separately authorized). **Work posture (D6, as written):** "serialize heavy work, ~1 background
agent alongside the orchestrator" — one CPU-heavy job (build, battery,
reviewer) at a time; the orchestrating session may run beside it doing light
coordination; a second worktree is for isolation, never a second heavy job.

## 1. Foundations

### 1a. Records: immutable, versioned, ordered, and framed

```go
type RecordHeader struct {
    ID         RecordID    // ULID, allocated by the sequencer
    Schema     SchemaVer   // "outcome/2": the contract version of this record kind (D3)
    Seq        uint64      // per-workspace monotonic; happens-before
    RunID      RunID       // zero for workspace scope
    Attempt    uint32      // run attempt generation (a retried run is a new attempt)
    Subject    Ref         // what this record is about
    Supersedes RecordID    // set ONLY when this record replaces an earlier assertion of the same subject+kind
    At         time.Time   // observation time; never an ordering key
}
```

Records come in two **disjoint envelope types**, `ProductionRecord` and
`ExperimentalRecord` (§9); a reader API is typed to one or the other, so a
production query cannot return experimental rows by construction.

Status, standing, "current verdict", and lifecycle stage are **folds** over
records. No record carries a mutable status field.

### 1b. Thoughts (D16)

```go
type ThoughtRef struct {
    Hash  Hash        // blake3, domain-separated by Kind, versioned ("b3v1:…"); computed and verified by the thought store
    Kind  ThoughtKind // goal | prompt | response | step_result | deliverable | lesson_text
    Bytes int64       // derived at store time
    Text  Encoding    // utf8 | bytes — what a text backend may be handed vs what only a file transport may carry
}
```

Construction is private to the thought store (bytes in → ThoughtRef out). The
engine stores, hashes, routes, renders, and hands thoughts to judges and
backends. **It never slices one.** Interpretation happens only at declared
interpretation boundaries — typed functions from ThoughtRef to a process
artifact, validated once: `Intent`, `Claims`, `Plan`, `Judge`. A model call
is such a boundary; so is a deterministic parser; each owns validation and
error translation for what it produces.

**Overflow is a routing problem, never a run outcome by itself.** Backends
declare capabilities (`MaxInputBytes`, `ReadsByReference`). Planning selects a
backend/transport that can consume the thought **whole**; a backend that
cannot returns a typed `backend_incapable` for *that attempt*, which triggers
rerouting to any registered lossless route (by-reference handoff — subprocess
agents read artifacts) and, only when no lossless route exists, an
escalation naming the size and the routes tried. The thought is never
sliced and never the reason a run silently ends. Stated honestly: a thought
no available substrate can carry whole cannot be processed by anyone; the
engine says so instead of pretending. There is no engine-generated chunking
in v1. Semantic decomposition of a large artifact,
if ever needed, is an explicit interpretation producing a new artifact with
its own completeness verdict — a later Finding, not an overflow path.

Thought fields in every contract are declared `unconstrained`; the suite
feeds oversized, empty, and `bytes`-encoded bodies through every boundary
and asserts that what reached the backend was the whole thought or a typed
refusal.

### 1c. Contracts are built first, not later (D3; contract input §1–§2, §16)

**Contract before implementation, per edge.** For every record kind and
shared edge, the declared semantics, answer key entry, and red reference-
reader cases land *before* the provider implementation of that edge; the
generated half is produced the moment the provider type exists and must
agree before the slice lands. Step 1 builds the machinery and applies it to
the step-1 types; every later step applies it to its own edges first. Each
edge therefore has: a
**generated** file derived from the Go type at a pinned ref, a **declared**
file (absence semantics, unknown-value handling, used-for, retry guidance,
fail-soft collapse sets, `unconstrained` on thought fields, `measured-by:` on
any measured claim — `not-re-runnable-here` is a legal value), a
**lifecycle**, an **answer key**, and a **reference reader**. Regeneration
diff is the review. Warnings for undefined dimensions are never silenced.
The independent-pair check (§15) runs on representative edges in CI, not
once at exit.

## 2. The workspace journal

**Transactions are framed.** A lane submits a `Command{IdempotencyKey,
Preconditions, Records}`. The sequencer (one goroutine) validates
preconditions, allocates `Seq` to every record, and writes **one framed,
checksummed envelope** containing all of the command's records; the envelope
is durable (fsync) before acknowledgement. A torn tail on recovery is a
partial envelope with a bad checksum and is discarded; nothing inside an
unacknowledged envelope is ever visible. Command IDs are deduplicated on
replay. Multi-record invariants that are genuinely simultaneous (verdict +
supersedes; outcome + run card) are one envelope; a join decision and its
cancellations are deliberately NOT (§3 — the crash between them is a tested
state). Things that are *not* simultaneous (delivery) are their own
transitions (§5a).

**Two durable states at the edge.** *Committed* = in the journal. *Published*
= the projector has written the shared-spec views for it and advanced a
durable watermark. External readers of the workspace read views and the
watermark; the contract guarantee at the workspace edge is stated per view
in terms of *published*, never *committed*. Multi-file view generations are
replaced atomically (write to a generation dir, rename). The projector
recovers from its watermark.

**Backpressure and shutdown.** Every lane has a bounded, durable work cursor
over the journal (no in-memory queues of unbounded work); overload means the
cursor lags, never memory growth. Shutdown is a quiesce DAG: (1) intake and
timers stop accepting; (2) derived-work producers (tail, evaluator, sheriff)
quiesce — finish the command in flight, submit nothing new; (3) executor
drains to its next committed transition; (4) delivery flushes its outbox;
(5) the sequencer freezes the **final commit watermark** and refuses further
commands; (6) the projector publishes through that watermark; (7) the
sequencer closes. A lane whose cursor is behind the final watermark resumes
from its cursor on next start; nothing is lost and nothing replays forever.

**Admission:** one process per root, enforced by a lease file with PID and a
monotonic process epoch; every command carries the epoch, and the sequencer
refuses commands from a stale epoch.

Locked read-modify-write exists only for the lease and for view files the
spec requires to be rewritten whole.

## 3. Goals, runs, fork, join

```go
type Goal struct { RecordHeader; Parent GoalID; Root GoalID; Text ThoughtRef; Origin GoalOrigin; Delivery DeliveryPolicy }
type RunAttempt struct { RecordHeader; Goal GoalID; Config ConfigSnapshot; Family *FamilyAssessment }
type AttemptRef struct { Run RunID; Attempt uint32 }
type Fork struct { RecordHeader; Parent AttemptRef; Step StepID; Members []AttemptRef /* fixed at creation; the barrier is over exactly this set */; Policy JoinPolicy }
type JoinDecision          struct { RecordHeader; Fork RecordID; Selected []AttemptRef; Cancel []AttemptRef }  // all: Selected=every member, Cancel=∅; first_verdict: one selected, rest cancelled
type CancellationIssued    struct { RecordHeader; Fork RecordID; Child AttemptRef; Reason Ref }   // idempotent by Fork+Child
type ChildTerminal         struct { RecordHeader; Fork RecordID; Child AttemptRef; State ChildState }  // written by that attempt's own driver; a terminal from any other generation is rejected at the journal
type JoinSettled           struct { RecordHeader; Fork RecordID }                   // legal only when every member has a ChildTerminal for this Fork
```

The order is causal and each step survives a crash between it and the next:
the parent commits `JoinDecision` alone (selection fixed; for `all` it is
written when every member has a ChildTerminal and cancels nothing); the
executor issues cancellations idempotently *from that record*, as separate
commands; each loser's driver commits its
own `ChildTerminal` (cancelled | completed_late | failed); `JoinSettled` is
computed by a fold and committed only when the drain barrier is provably met;
the parent step continues only after `JoinSettled`. A child that completes
after `JoinDecision` records `completed_late`; its records remain evidence
and it may not enter the tail (no learning from a cancelled arm). Kill tests
sit between every pair of these transitions.

v1 join policies: `all`, and `first_verdict` (first child whose effective
closure verdict is *achieved*; others cancelled) — kept because the
two-level scenario needs one early-return policy to exercise cancellation;
`any`/quorum have no scenario and are not in the enum. Memory scope walks
Goal ancestry (own → parents → root → workspace). Child runs are goroutines
under the parent's context; cancellation *executes* a committed decision.

## 4. Backends — effectful boundary components, one invocation state machine

```go
type Invocation struct { RecordHeader; Purpose Purpose; Request ThoughtRef; Backend BackendSnapshot; Target *Budget; Lens *LensRef; EffectToken Token /* committed BEFORE dispatch; outward-capable adapters pass it to every tool that accepts an idempotency key */ }
// states, each a record:  prepared → dispatched → terminal_observed → receipt_committed
type Attempt   struct { RecordHeader; Invocation RecordID; N int; Terminal TerminalState }
type ToolEffect struct { RecordHeader; Invocation RecordID; Attempt RecordID; Seq int; Class EffectClass /* read | write_local | outward */; EffectID string; Evidence ThoughtRef }
type Receipt   struct { RecordHeader; Invocation RecordID; Response ThoughtRef; Usage Usage }
```

Adapters are **effectful boundary components**, not pure: a subprocess agent
does I/O, tool actions, and retries. The run shell owns the invocation state
machine: it commits `prepared`, dispatches, commits every `Attempt` and every
`ToolEffect` as the stream reports them, commits `terminal_observed`, then
the `Receipt`. Partial-stream semantics are declared once per backend (a
malformed tool-event frame after a valid response = `terminal_observed` with
`Terminal=partial`, receipt committed with the response, the frame recorded
as evidence — never a silent retry, never a rejected receipt).

**Restart.** Every dispatched invocation is presumed effectful until the
adapter's declared capability proves it cannot act outward (a `judge`-purpose
call on a tool-less adapter). An Invocation found `dispatched` without
`terminal_observed` on an outward-capable adapter yields
`indeterminate_external_effect` — absence of committed ToolEffects is not
evidence of purity, because the frame may have died with the process; reconciliation is evidence-based per effect class (a `write_local`
is re-checked against the filesystem; an `outward` effect is queried by the
Invocation's `EffectToken` where the tool supports idempotency keys or lookup,
otherwise escalated). Blind
replay never happens. Kill tests: after dispatch, after each tool effect,
after response, before receipt.

`Purpose` is a receipt enum, queried; `Target` is optional and present only
when a registered target applies (D13). v1 backends: `subprocess` (claude /
codex CLIs — kept because the agentic executor with `tool_events` receipts is
the only backend class that can do real work on this box today, and its
receipts are the evidence the contracts need) and `scripted` (tests, replay).
Others attach through the same seam later.

## 5. The spine — pure decisions, one shell

```
Intake → Intent → Plan → Execute → Judge → Record → Deliver ⟶ (tail) Learn
```

Stages are pure decision functions over validated immutable inputs returning
an output plus a typed **effect list** (`Commit{records}`, `Invoke{intent}`,
`Fork{…}`); the **run driver** is the shell that makes invocations (§4),
submits commits, and emits one lifecycle event per boundary carrying
`handle_id`, `run_id`, `goal_id`, `attempt` (FINDINGS #9). Control flow is
written in the driver in the open. Validation ownership is one table
(§13): CLI args, config, subprocess frames, journal replay, imported records,
model-produced `Plan`/`ClaimSet`/`IntentAssessment` — each validated once at
its boundary.

**NOW / AGENDA** are configurations of the one driver (plan cardinality and
judge selection as parameters; no second driver) — the names are kept because
the shared spec registers `lane` as a vocabulary on the outcomes ledger and
the behavior suite drives both by name; the engine keeps the vocabulary, not
the prototype's code split.

**Intake commits, before any outcome-bearing work:** the Goal, its
`DeliveryPolicy` (what counts as delivered for this origin), and a
`FamilyAssessment` produced by a *treatment-blind, deterministic* classifier
(registered rule version; ambiguous or unmatched goals get `family=none` and
are ineligible for experiments). `Intent` (the model interpretation) runs
after and may not revise the assessment for the current run. Assignment (§8)
is then a **sequencer-enforced command** at intake: keyed deterministic
randomization (`hash(experiment_seed, GoalID)`), the randomization **unit is
the GoalID**, so every attempt of the goal inherits the same arm and the
unit enters the denominator once; at most one active experiment may claim a
unit (mutual exclusion enforced by the same command); admit/stop is atomic
with assignment, so a stopping rule and an intake cannot race.

**Interrupts:** `Interrupt{RecordHeader; Target RunID; Attempt; Action;
Expires}` consumed only by the driver at stage boundaries, acknowledged by a
record, expired when the target attempt reaches a terminal state
(FINDINGS #1).

### 5a. Run state machine — execution outcome vs mission outcome

```
created → executing → judged → recorded ──→ delivered{transport_accepted | user_acknowledged}
                                        └─→ delivery_failed (after bounded retry/escalation)
post-run:  tail_done   (learning, curation; enqueued after `recorded`)
```

Two outcomes, both folds: **execution outcome** settles at `recorded` (what
the run produced and how it was judged). **Mission outcome** = execution
outcome ⊗ delivery state under the goal's `DeliveryPolicy`: a run whose
required delivery state was not reached is `mission_failed(delivery)` even
when execution succeeded (§8 item 1). Learning eligibility is `recorded`.
Restart: past `recorded`, resume from the last committed transition; before
it, the attempt is marked `recoverable` and a new attempt starts from the
last committed idempotent stage, subject to §4's external-effect
reconciliation.

## 6. Judges, verdicts, resolution

```go
type Observation struct { RecordHeader; Check CheckKind; Result ObsResult /* refuted | supported | could_not_observe */; Confidence float64; Evidence []Ref }
type Verdict     struct { RecordHeader; Kind VerdictKind; Outcome Outcome; Confidence float64; Source Source; Basis []Ref; Falsifiers []ThoughtRef; Direction Direction }
type Resolution  struct { RecordHeader; Subject Ref; Effective RecordID; Candidates []RecordID; ResolverVer string }
```

Deterministic checks produce **Observations**, not verdicts; a judge sees
every claim together with its observations. An observation that could not run
is distinct from a refutation. The **resolver** is versioned data defining a
*partial order* over (source standing, kind applicability, direction) with
explicit incomparable cases and tie results; `Seq` orders only genuine
supersession (same subject, same kind, `Supersedes` set), never independent
evidence, so arrival order cannot change the effective verdict — the
pairwise-order tests assert exactly that. A sufficient set of refuting
observations may settle *failure* without a judge (so a dead judge backend
cannot block forever); success always needs a judge or an operator.

**Sheriff** runs under the process **supervisor** (§10): a separate lane with
its own evaluator over committed records, a heartbeat record with a progress
watermark, and a bounded restart policy. It emits `stuck` verdicts naming the
evidence; thresholds are config with a Why and are reported, not enforced.
Whole-process death is not detectable from inside and is stated as such: the
launching edge checks the lease.

## 7. Memory — learned data, recall, application

```go
type LearnedID  string   // stable identity of an item across revisions; minted once, never reused
type LearnedRevision struct { RecordHeader; Item LearnedID; Predecessor RecordID; Kind LearnedKind /* lesson | policy */; Scope ScopePath; Family FamilyKey; Text ThoughtRef; Provenance Provenance }
type LifecycleTransition struct { RecordHeader; Item LearnedID; Revision RecordID; From, To Stage; Evidence RecordID }
type Application struct { RecordHeader; Item LearnedID; Revision RecordID; Invocation RecordID; Representation ThoughtRef }   // recall injection
type PolicyApplication struct { RecordHeader; Item LearnedID; Revision RecordID; Run AttemptRef; Snapshot PolicySnapshot }    // orchestration policy
type PolicySelection struct { RecordHeader; Run AttemptRef; Considered []LearnedID; Enabled []LearnedID; Basis []RecordID /* the transitions that decided it */ }
```

Stage is a fold over `LifecycleTransition`s for the `LearnedID` (a revision
never carries a stage); attribution names both the item and the exact
revision applied. Stages: `candidate | observed | provisional |
effective | canon | contested | tombstone`.

**Two apply surfaces in v1**, because D17 needs the second: (1) **recall
injection** — a `lesson` reaches a backend request, proven by an
`Application` whose representation is in the request hash; (2) **orchestration
policy** — a `policy` item is versioned data (which mechanisms are enabled,
decomposition depth, judge configuration) consumed at one policy boundary in
the driver, proven by a `PolicyApplication` snapshot on the run. Mechanisms are
therefore data the engine reads (D17). **The policy-selection fold is
versioned data**: `EffectMeasurement.Verdict → LifecycleTransition →
Enabled`. `harmful` → `contested` (still enabled, flagged); `equivalent` at
the stopping rule → `tombstone` for that family → **disabled** in the next
`PolicySelection` for that family; `helpful` → `effective`; conflicting later
evidence reopens `contested`; rollback is a new transition citing the
contrary evidence. The end-to-end test: an `ablate(m)` equivalence changes
the next production run's `PolicySelection` and its observable driver path.

**Recall is one query**, `Recall(purpose, scope, standing) → RecallSelection`
(candidates, deterministic order, each inclusion/exclusion with reason,
projected size). One query, not three subsystems: the contract does not force
three and a typed query serves all three purposes. Exclusions are recorded as
a bounded projection (counts by reason + the top-k excluded refs), not one row
per excluded item (§14 census).

## 8. Self-improvement — the closed, measured loop

```
Observe   committed receipts, verdicts, usage, friction (tail lane, after `recorded`)
Diagnose  deterministic classifiers → Observations; a model lens second → FailureClass
Propose   a Learned revision at `candidate` (v1 kinds: lesson, policy)
Apply     recall injection (Application) | orchestration policy (PolicyApplication)
Measure   Experiment → Assignment → ArmObservation → OutcomeAssessment → EffectMeasurement
Lifecycle a transition whose Evidence is an EffectMeasurement; tenure reaches `observed` only
```

### 8a. The experimental unit — immutable protocol, derived counts

```go
type Experiment struct {           // immutable protocol; nothing in it accrues
    RecordHeader
    Hypothesis   RecordID          // exactly one learned item (one-item ablation)
    Population   FamilyKey         // from FamilyAssessment (intake, treatment-blind)
    Assignment   AssignmentKind    // paired_replay | randomized_live | shadow_arm
    Arms         []ArmSpec         // treatment/control snapshots: backend, model, version, config, applied-item SET, seed
    Outcome      OutcomeSpec       // predeclared dimensions, direction, equivalence margin
    Analysis     AnalysisSpec      // intention_to_treat AND per_protocol; missing-outcome policy; stopping rule; estimator; uncertainty threshold
    Oracle       OracleClass       // deterministic_fixture | external_observed | blinded_evaluator
    Exclusions   []Rule            // fixed before observation
}
type Assignment        struct { RecordHeader; Experiment RecordID; Unit GoalID; Arm ArmID; Seed Hash; Rule RuleVer }   // committed at intake by the sequencer command in §5; one per unit; inherited by every attempt
type ArmObservation    struct { RecordHeader; Assignment RecordID; Exposed bool /* Application/PolicyApplication present */; Artifacts []Ref /* complete, raw */; Missing MissingReason }   // raw terminal evidence ONLY — no scores
type OutcomeAssessment struct { RecordHeader; ArmObservation RecordID; Evaluator EvaluatorRef; Inputs []Hash /* provably exclude the hypothesis revision */; Scores OutcomeVec; OracleResult OracleVerdict; Missingness Decision }
type EffectMeasurement struct { RecordHeader; Experiment RecordID; Assessments []RecordID; AssignedN, ExposedN int; Delta Delta; Uncertainty Unc; Verdict EffectVerdict /* helpful | harmful | equivalent | insufficient */ }  // a DETERMINISTIC fold over its Assessments; a test recomputes it from the journal
```

Counts are **derived from Assignment records**, never stored on the
experiment. Both intention-to-treat (all assigned) and per-protocol (exposed
only) are computed and reported; a promotion requires the predeclared one.

**Oracles are typed, and the historical closure verdict is not one.** A
historical verdict was produced under the historical treatment path and may
share its judge; it is inadmissible for efficacy. Admissible: a deterministic
task fixture, an externally observed outcome, or a blinded evaluator over the
complete arm artifacts whose `Inputs` provably exclude the hypothesis item.
Paired replay therefore proves **exposure and decision difference** (did the
request change; did the decision change) and may carry efficacy only when the
call's outcome has a deterministic fixture. Randomized live is where D11 is
established.

**Loop closed** = `candidate` (run A) → Assignment → Application or
PolicyApplication (run B) → OutcomeAssessment with an independent evaluator →
EffectMeasurement → LifecycleTransition citing it → **a later production run
whose Application/PolicySelection reflects the transition**. **v1 exit:** a
blinded scripted scenario where a planted helpful item is promoted and a
planted harmful item is demoted by the same machinery; then one live
randomized experiment on this box, through the real subprocess path,
reaching its stopping rule with a `helpful` or `harmful` verdict (or
`equivalent` followed by the D17 policy change), the predeclared
LifecycleTransition committed, and its consequence observed in a subsequent
production run. Tenure may move `candidate → observed` and may trigger
re-measurement, expiry, or tombstoning; never `effective`.

Proposals that would act outward or change authority config are held on the
escalation surface (autonomy boundary unchanged).

## 9. Experiments are a separate population (D17 without contamination)

Three envelope types share the sequencer and the physical log (one `Seq`
space, one crash story) and are distinct Go types with distinct reader
capabilities — a **capability matrix** (writer × reader × envelope) is part
of the registry and is checked at compile time by the typed APIs:

- `ProductionRecord` — everything a production run writes and reads,
  including its own `Application`/`PolicyApplication` (exposure is a
  production fact about a production run).
- `ControlRecord` — `Experiment`, `Assignment`, stopping state: the
  experiment control plane, readable through a *narrow assignment
  capability* used only by the intake command and the evaluator, never by
  recall, diagnose, or propose.
- `ExperimentalRecord` — shadow-arm runs and their artifacts, readable only
  by the evaluator.

Every production reader is contract-tested with poisonous control and
experimental rows before the first replay runs. The evaluator writes into
production exactly one thing: an **attestation** — `OutcomeAssessment` and
`EffectMeasurement` carrying the protocol hash, the assigned/exposed unit
IDs, per-assessment scores, estimator inputs, and evaluator identity — so a
production fold can verify a transition's evidence without reading any
experimental artifact, and an auditor with the evaluator capability can
recompute it.

**Attribution (D17).** `plain` and `star` shadow arms compare the *whole
harness* to a 1-shot; a match updates a `HarnessChallenger` record only. An
individual mechanism's standing changes solely from a predeclared
`ablate(m)` experiment (harness with everything else fixed), and
"redundant" requires an **equivalence test** at the family's margin with the
stopping rule met — failure to reject a difference is `insufficient`, not
`equivalent`. Shadow arms run post-hoc, black-box. For families the
`FamilyAssessment` classifies read-only they run in a scratch workspace. For
effectful families (local writes) they run in an **isolated snapshot** of the
workspace/repo (copy-on-write) with outward effects intercepted by the
authority gate and recorded, never committed; the comparison is over
proposed actions and judged products. Families with unavoidable outward
effects are **ineligible**, and D17 is declared *partial by scope* for the
mechanisms that only those families exercise (§17).

## 10. One process — supervisor, lanes, timers

One binary, one root context, one lease. A **supervisor** owns lane
registration, heartbeat/progress watermarks per lane, panic capture, bounded
restart, and a degraded-health line in every delivery while any lane is
down. Lanes: intake (CLI in v1 — the always-on submission path is the CLI
writing a Goal into the journal of the running process via the lease's
socket; a second front end attaches at the same seam), executor, sequencer,
projector, tail, sheriff, evaluator, timers (in-process periodic sweeps with
intervals carrying a Why; no cron, no systemd timers), delivery (outbox).
Shutdown and restart per §2 and §5a.

## 11. Budget and metering (D13, D15)

Every Invocation and Receipt carries usage; budgets are optional targets with
a Why; overage is an event and a delivery line; the run continues.
Subscription ceilings are external; the engine never estimates remaining
allowance and never stops on cost. The authority gate covers registered
outward-act classes and provider hard refusals only; malformed authority
config fails closed for outward acts only.

## 12. Delivery — honest at the user's edge (§8 item 1)

`DeliveryPolicy` is captured at intake per origin and names the required
state. States, named by what they prove: `transport_accepted` (the transport
took the payload), `endpoint_accepted` (the receiving program stored or
forwarded it — a file-drop consumer, a bot's message id), and
`user_acknowledged` (an acknowledgement **causally generated by the
user-facing client after presentation**, carrying an ack token bound to the
delivery ID and the payload hash — an explicit CLI ack command, a
read-receipt the origin itself produces). Nothing below the last is ever
labelled user delivery. CLI in v1 supports `transport_accepted` and an
explicit ack command; a mission whose policy requires `user_acknowledged`
carries `accepted_unacknowledged` until the ack arrives. The v1 acceptance
exercises the real ack protocol: crash-before-display, stale ack, duplicate
ack, wrong-payload ack. Bounded retry from
the outbox, then `delivery_failed` with an escalation row.

## 13. Workspace, contracts, compatibility (D10, D3)

Own root (`~/.maro-go/workspace`, `MARO_GO_WORKSPACE` override, path printed
before any write). **Two promises, kept apart (D2, D3).** Every edge the
shared spec designates shared is written by the projector **exactly to its
versioned wire contract** — required fields, vocabulary, absence behaviour —
and the view fixtures fail on any deviation; "readable" is not a promise
level for a shared edge. Where the Go-native record is richer, the shared
projection is by construction a *lossy view of a richer source*, and the
mapping table records what the view omits so nobody mistakes the view for
the source; changing what a shared edge carries is a D3 versioned contract
change, never a silent downgrade. Go-only causal history (revisions,
transitions, applications, effects, attestations) travels in a **native pack
envelope**. `Go → Python reader` and `Python → Go quarantine`
(imports enter at `candidate`) are tested separately; no round-trip is
claimed where only readability is proven.

Validation ownership table (one row per boundary: CLI, config, subprocess
frames, journal replay, imported pack, model-produced artifacts, thought
store integrity) is part of the registry.

**Personas as lenses (§8 item 8):** a `LensRef` on `judge`/`render`
invocations — a configuration over immutable references, never an execution
path. v1 ships the neutral lens plus one non-trivial lens and a test that
swaps them on the same facts. **Action-bias, structurally:** the falsifiable
criterion is that no prompt text can widen authority — outward acts pass the
registered authority classes (§11) regardless of what the model was told; the
must-detect battery includes "prompt instructs an outward act; gate still
holds".

## 14. Records that are re-read, and removal (§8 items 5, 9)

A **record census** is part of the registry: for every record kind — writer,
authoritative reader/query, the decision it affects, retention/compaction
rule, and `audit-only` where that is the honest answer with its operator
read surface named. A must-detect mutation disables each required reader and
must fail an edge scenario. Records with no consumer are not written;
diagnostic traces are bounded projections.

`Finding{RecordHeader; Kind add|remove|change; Subject; Evidence; Owner;
Closure}` is a record. Removal acceptance = absence test + reference/config/
store census + migration disposition + the same review as an addition.
**Step 0 of every build step is subtraction, and it produces a reviewable
artifact**: the list of proposed items with the decree/edge/scenario that
requires each, and the deletions.

## 15. Verification — falsifiable system outcomes

Shared spec = the Phase 1 behavior suite; the Go harness maps its driver.
Added red-first: fork/join kills between every transition; the blinded
discrimination scenario; experimental-row poisoning of every production
reader; oversized/empty/bytes thoughts through every boundary; two processes
on one root; kill/restart at every state transition and at every invocation
state; concurrent verdict arrival in all pairwise orders (order-independence);
delivery outbox under dropped transport and restart with a real ack origin;
pack import idempotency; removal with absence proof; lens swap on the same
facts; authority gate under an instructing prompt.

**Must-detect battery** (named, finite): drop a receipt; ingest an
experimental row; reorder a lifecycle transition; bypass Application; falsely
mark delivered; slice a thought; disable a required reader; widen authority by
prompt. DONE for a vertical slice = its edge behaviors green and its
must-detects red-when-mutated; codex review is evidence discovery on risky
slices. The independent-pair contract check runs in CI on representative
edges.

## 16. Build order (by fan-in; each step: subtraction artifact → build → edge tests → land)

1. Records, identity, schema versions, envelope types; thought store; lease;
   **contracts foundation** (generator, declared, answer key, reference
   reader, record census) for the step-1 types.
2. Journal with framed transactions, sequencer, projector with watermark and
   atomic view generations, lane cursors, restart; production/experimental
   separation and poisoning tests; the B3/B4/B6 projection mappings for the
   first slice.
3. Invocation state machine with Attempt/ToolEffect/Receipt records;
   `scripted` and `subprocess` backends; restart reconciliation.
4. Observations, verdicts, resolver (order-independence tests).
5. Run state machine + driver with pure stages; Intake with DeliveryPolicy
   and FamilyAssessment; NOW configuration; lifecycle events; delivery outbox
   with an acking origin. **First real delivered answer** (subprocess live).
6. Recall query, RecallSelection, Application; re-run identity.
7. **Supervisor** (lane registration, heartbeats, panic capture, bounded
   restart) and the generic lane lifecycle; then AGENDA configuration: plan,
   per-step judge, closure with restart; Sheriff as the first supervised
   lane.
8. Fork/join transitions over attempt refs; two-level scenario with kills
   between every transition, both policies.
9. Tail lane, timers; observe → diagnose → propose.
10. Experiment protocol records; paired replay (exposure/decision proof);
    lifecycle transitions; policy apply surface; the blinded discrimination
    scenario.
11. Randomized live assignment; evaluator lane; shadow arms; `ablate(m)`
    equivalence; HarnessChallenger.
12. Native pack envelope, projection mapping table complete, import
    quarantine; import the Python workspace's learned data at `candidate`.
13. Live acceptance: one goal family, one live experiment to its stopping
    rule with a non-`insufficient` verdict, the Manti target measured and
    reported, one mechanism removed with absence proof, one lens swap.

## 17. Conformance appendix (status is honest; each row names the observable edge, the test whose failure condition is stated, and the build step)

| Item | Status now | Observable edge / failing condition | Step |
|---|---|---|---|
| D1 contract-not-port | honored | inherited shapes each carry a sentence (§3–§5, §7) | — |
| D2/D10 formats shared, roots separate | honored in design | shared edges exact to the versioned wire contract (fixtures); native envelope for richer history | 2, 12 |
| D3 versioned contracts | honored in design | generated/declared pair from step 1; regeneration diff fails CI unreviewed | 1 |
| D4 go-port frozen | honored | no commits on go-port | — |
| D5 clean-room to intent | honored | §8 from VISION intent; one recall query; one driver | — |
| D6 serialized heavy work | honored | one heavy job at a time, orchestrator beside it (§0, quoted) | — |
| D8 Python lift rule | honored, plan-only | each shared-contract change classified; provider repairs are their own main-branch items | 2, 12 |
| D9 home | honored | branch/path | — |
| D11 measured behavior change | partial until step 13 | live experiment: helpful/harmful (or equivalent + policy change), transition committed, consequence observed in a later production run | 10–13 |
| D12 one process, no cron | honored in design | two-process admission test; no cron in tree; supervisor heartbeats | 2, 9 |
| D13 targets not constraints | honored | overage-continues test; thresholds reported | 3, 11 |
| D14 full loop in v1 | partial until step 11 | discrimination scenario + live loop | 10–11 |
| D15 no cost aborts | honored | no cost-stop path; grep + test | 3 |
| D16 thoughts unconstrained | partial (routing) | whole-thought-or-typed-refusal at every boundary; reroute on `backend_incapable`; escalation only when no lossless route; must-detect "slice" | 1, 3, 5 |
| D17 bitter lesson inside | partial until step 11, and partial BY SCOPE after (families with unavoidable outward effects) | ablate(m) equivalence → transition → next PolicySelection changes the driver path; snapshot challenger for effectful families | 10–11 |
| §8.1 delivery is the product | partial until the ack protocol is exercised | mission outcome folds delivery; `user_acknowledged` only from a client-generated, payload-bound ack; CLI ack command tested incl. crash-before-display | 5 |
| §8.2 done is a claim | honored in design | order-independence tests on the resolver | 4 |
| §8.3 learn measurably | as D11 | | |
| §8.4 validator not counter | honored in design | Sheriff evidence tests; supervisor detects lane stall | 7, 9 |
| §8.5 artifacts truth, re-read | partial | record census with required readers; must-detect "disable a reader" | 1, every step |
| §8.6 recursion | honored in design | join transitions survive kills between each | 8 |
| §8.7 always-on, not OS | honored in design | as D12; whole-process death is external, stated | 10 |
| §8.8 swappable + lenses + action-bias | partial | lens swap test; authority-under-prompt must-detect | 5, 13 |
| §8.9 edges + removal | honored in design | subtraction artifact per step; one removal with absence proof | every step, 13 |

## 18. What changed by round

**v1.3 (r3):** EffectToken committed before dispatch and presumed-effectful
restart; overflow as capability-aware routing with escalation only when no
lossless route (D16 marked partial); shared edges exact to their versioned
wire contract with the native envelope for richer history; `LearnedID`
distinct from revisions everywhere attribution matters; fork membership as
attempt refs with `JoinDecision{Selected, Cancel}` covering `all`; three
envelope types with a capability matrix and a verifiable attestation as the
only evaluator→production write; snapshot challenger for effectful families
and D17 partial-by-scope; supervisor before Sheriff; contract-before-
implementation per edge; `user_acknowledged` only from a client-generated,
payload-bound ack (§8.1 partial until exercised); join decision and
cancellation as separate envelopes; D6 quoted; raw ArmObservation vs scored
OutcomeAssessment vs recomputable EffectMeasurement; GoalID as the
randomization unit with keyed seed, mutual exclusion, and atomic admit/stop;
the policy-selection fold that actually disables a mechanism; shutdown as a
quiesce DAG with a frozen final watermark; live exit requires the transition
and its consequence in a later run; appendix statuses corrected.

**v1.2 (r2):**

Experiment split into immutable protocol + Assignment/ArmObservation/
OutcomeAssessment with derived counts, ITT and per-protocol, typed oracles
(historical verdict inadmissible), non-vacuous live exit; production/
experimental as disjoint envelope types built before the first replay;
engine chunking removed (reference or `backend_incapable`); join as four
transitions with kills between each; execution vs mission outcome with
DeliveryPolicy and honest CLI `accepted_unacknowledged`; `Stage` removed from
Learned (fold over transitions on a stable ID); `policy` learned kind and the
orchestration-policy apply surface so D17 has a mechanism; FamilyAssessment
at intake, treatment-blind, before Intent; framed transactions with checksum
and torn-tail recovery, committed vs published, bounded lane cursors,
shutdown order, epoch on every command; invocation state machine with
Attempt/ToolEffect records and evidence-based restart reconciliation;
contracts foundation moved to step 1 with `measured-by`; Observation vs
Verdict and a partial-order resolver; supervisor with heartbeats; record
census and bounded exclusion projection; per-B-entry mapping table with loss
declaration and a native pack envelope; whole-harness vs `ablate(m)`
attribution with an equivalence test; anti-lift sentences for subprocess,
NOW/AGENDA, first_verdict; conformance appendix with honest statuses and
steps; build order re-sequenced.
