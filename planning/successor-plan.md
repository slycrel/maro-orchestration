# The Go successor — plan (DRAFT v0.1, for iteration)

Status: **PHASE 0 — the plan itself is the deliverable.** Nothing in
phases 1+ starts until Jeremy calls it. This document is the living
plan; it gets re-read and revised at every zoom-out checkpoint (see
Standing rules). Inputs: `python-arch-map.md` and `go-tree-triage.md`
(both done, both in this directory).

## Decisions already made (Jeremy, 2026-08-28)

- **D1 — Contract, not port.** The Go engine is a spiritual successor
  engineered to maro's *workspace/data contracts*, not a translation of
  its Python. The reasoning, the patterns, the mesh — "partly the
  material, partly the weave, mostly the interaction" — are what carry
  over. Line-by-line CPython fidelity is explicitly renounced.
- **D2 — Same workspace formats.** *(AMENDED by D10, 2026-09-04: separate
  workspace ROOTS; the shared thing is the contract spec, not a live
  store.)* Both engines read/write the same workspace formats. The
  contract is the compatibility boundary; internals diverge freely.
- **D3 — Contracts may be upgraded, both sides.** Sharpening a contract
  or adding persistence metadata is encouraged — improve the Python
  side too, then leverage the better contract. Contracts are versioned
  and written down (see Phase 1), never sharpened silently.
- **D4 — New branch.** The `go-port` branch is frozen as a stale
  reference to be *mined*, never continued. Mining happens only into an
  existing design note (see anti-lift rules).
- **D5 — Reference-allowed clean-room.** Looking at the Python is
  allowed; being bound by it is not. The most over-engineered
  subsystems (self-improvement loop, director hierarchy) lean
  pure-clean-room: redesign to the *intent*, not the implementation.
- **D6 — Subagents are allowed** (the "no subagents" constraint was a
  compaction artifact). Box limit stands: serialize heavy work, ~1
  background agent alongside the orchestrator. Codex ($100/mo seat) is
  the design-review and adversarial-review lane, and a candidate
  second implementer.

### Decisions 2026-09-04 (Jeremy — the three phase-2 gates)

- **D7 — v1 scope.** *"Backbone + memory recording, along with the
  proper hooks for the self-improvement (that we add later). Design
  should be modular and anti-fragile, with the expectation that we are
  updating the processing over time (forwards compatible for our
  internal systems, allowing for rewriting processes and keeping
  contracts stable)."* Read: v1 = the Phase 2 backbone plus the
  memory/knowledge recording strand; self-improvement ships as v2 BUT
  its seams (where the inspector/evolver/graduation loop plugs in, what
  it reads, what it may write) are designed into v1 — hooks first,
  processing later. Internal processes are expected to be rewritten;
  the contracts they sit behind are not. This is the design brief for
  the Phase 2 backbone note.
- **D8 — Python lift while the successor builds.** Contract-sharpening
  PLUS real defects the behavior suite or reviews surface (the
  unmetered pre-flight `build_adapter` bypass is the model case). No
  behavior redesign on the Python side. Python stays production.
- **D9 — Home.** Branch `successor`, Go code in this repo's `go/`,
  rebuilt fresh on the new branch. `go-port` stays frozen and reachable
  for mining (D4). Shared land.sh / test-safe / review ledger / CI.
- **D10 — Separate workspaces; the SPEC is what's shared (AMENDS D2).**
  Jeremy: *"Let's keep the workspaces separate. From here on out we're
  diverged in implementation... we have a target spec, we can
  forwards-compat move that spec when we need to (where we missed in the
  past or need change for reasons in the future), and we know the right
  high level contracts. input -> black box process -> output should be
  consistent and the edges are where our success/failures lie, with
  processing simply implementation... the woven rope is the complexity,
  not the pattern. I suspect proper higher level contract testing will
  serve us well."* Read: the Go engine gets its OWN workspace root; the
  two engines never share a live store. What they share is the contract
  registry (`docs/CONTRACTS.md`) + the behavior suite — the spec — which
  moves forwards-compatibly when it must. Success/failure is judged at
  the edges (workspace artifacts in, workspace artifacts out); everything
  between is implementation and may diverge freely. Phase 4's comparison
  therefore runs both engines on their own workspaces against the same
  goals and compares at the artifact boundary — not on one shared store.
  Learned outputs still travel as DATA between engines (the pack), never
  as a shared file tree. Jeremy is sourcing further nuance on
  contract-testing practice from his work side; slot left open for it in
  the Phase 2 design note.
- **D11 — Learning bar (answers drift-review §9 Q1).** *"learning changes
  behavior is before 'cheaper', though learning to be cheaper is probably a
  kind of learning, one of many."* Read: the v1 self-improvement hooks are
  judged on MEASURED behavior change; cost is one axis of learning among
  several, not the gate.
- **D12 — Always-on shape (answers §9 Q2).** *"not an OS wins; I'm cool with
  a daemon or something, maybe... but no crons for timers and such; this is
  designed as a process on a machine, even though we have given maro a
  machine of it's own. (dockerized or other people's machines are not the
  same context as what we have)."* Read: the successor is ONE process; its
  background lanes (timers, watchers, tails) live inside that process's
  lifecycle and die with it. A long-lived daemon-shaped process is
  acceptable; cron/systemd timers as the scheduler are not. Design for the
  process-on-a-machine context first; containers and other hosts are a
  different context, not the default.
- **D13 — Cost envelope is a target, never a constraint (answers §9 Q3),
  and the deeper principle.** *"That is one of many possible goals for
  testing... I'm fine with that being a target, not fine with it being a
  hard constraint. Similar to our magic numbers -- we measure overages, we
  don't chop and break due to them. LLMs want math complete problems, not
  probabilities and fuzzy logic, and that's where we need to live to be
  different/do different (and thus be better than a 1-shot LLM call)."*
  Read: the Manti envelope is one measured target among many; overages are
  measured and surfaced, never enforced by breaking. The vision-level half:
  the harness earns its existence by living in probability and fuzzy
  judgement — the space a single LLM call avoids — so the successor's
  verdicts, budgets and gates are graded and recoverable by construction
  (rejoins recovery-over-correctness, 08-02, and caps-as-breakers).
- **D14 — v1 carries the FULL self-improvement system, not hooks (amends
  D7).** Jeremy, on the drift review's pushback A: *"I'm fine if we want to
  implement these instead of do hooks; your recommendation was to wait, that
  was my compromise. Now that you've read over everything, seems like that
  may have changed -- I'm fine proceeding with the full system rather than
  leaving that hanging."* Read: v1 = backbone + memory + self-improvement,
  clean-room to intent (D5), with the measurement half (record, replay,
  Δ-judge) so the loop is CLOSED in v1 — the thing the prototype never did.
  Phase 3's "quality/self-improvement strand" folds into Phase 2's exit.
- **D15 — Budget posture for the build: figure it out first, optimise
  after.** *"until we 'know what we're doing / confident' all the things are
  on the table; tokens, models, 'spend' is limited by what I'm throwing at
  this; currently it's $200/mo anthropic, $100/mo openAI, and whatever's
  left of our fireworks.ai grok tokens. We want to figure this out, then
  make it optimal; I started out trying to do both, which is harder, and it
  seems problematic; we're still fighting that a bit with chopped input
  lengths and aborted runs due to 'cost'. We don't have infinite spend, but
  we should have plenty of headroom at this point to work."* Read: the
  successor does NOT chop inputs or abort runs on cost during the
  figure-it-out phase; spend is metered and reported against targets
  (D13). The subscription ceilings are the only hard limit and they are
  external to the engine. Optimisation is a later, separate pass.
- **D16 — Thought process vs thoughts (resolves the D10/D13 question by
  dissolving it).** *"maro's goals are not math, and are never going to be
  'correct'. it's processes and patterns can be; so while we might say we
  don't want a 4MB document as output, sometimes the goal will demand that,
  or derive that, and we don't want to mix our processing edges (which can
  be deterministic) with our _output_. Maro is the thought process, the data
  that flows through it the thoughts; connected, related, and inevitably
  intertwined, but discrete and meaningfully separate."* Read: TWO kinds of
  thing cross the workspace. **Process artifacts** (events, verdict records,
  receipts, run cards, config, ledgers, the pack envelope) are the engine's
  own — deterministic, contract-tested, where success/failure lives.
  **Thoughts** (goal text, step results, deliverables, lesson content, LLM
  payloads) flow THROUGH the engine — never "correct", never shape- or
  size-constrained by the process, opaque to the contract beyond presence
  and provenance. The caps fragility of the prototype was exactly process
  constraints leaking onto thought content (600-char recall, `[:N]` on step
  results). The contract registry pins process artifacts hard and declares
  thought payloads UNCONSTRAINED on purpose (someone looked and decided any
  value is legal). Verdicts are process facts ABOUT thoughts: exact in shape
  (exists, carries confidence + source), graded in value.
- **D17 — The bitter lesson lives inside the process; redundancy is a
  learning, not a pre-sort.** *"the bitter lesson is within our process, not
  part of it; There is definitely an overlap aspect, where a 1-shot can get
  better and possibly even replace our learned subsystems; that is itself a
  learning that should be allowed and organically improved, rather than
  controlled up front. The irony of all this is that all this really is is
  a multi-stage harness; the bitter lesson says that eventually an LLM will
  be able to reason through this itself and not need maro. That's sort of
  the star skill in action -- to see how close we can get to that directly
  with prompt engineering."* Read: do NOT design an up-front "ages well /
  at risk" sort into the engine (retracts the drift review's §7 sort as a
  DESIGN input; it stands as a reading of history). Instead the engine
  carries a standing champion–challenger against the 1-shot (the star
  skill / shadow lane shape): when a single call matches a learned
  subsystem, that is recorded as a learning and the subsystem's standing
  decays organically (competence-redundancy decay already exists in the
  prototype — carry the idea, re-derive the mechanism). D11 "learning
  changes behavior" includes learning that a mechanism is no longer needed.
- **D12 stands; worker isolation revisited later** (Jeremy: "we can revisit
  later, yes").

## The framing that generates the structure

Maro's spine is one structured LLM call per step, with agency pushed
into subprocess backends that return `tool_events` receipts — a
map-reduce whose reduce step (reflect/record/curate) rewrites the
mapper's future behavior. The rope metaphor gives the build order:
**the weave is the contracts between phases and the workspace artifacts
they exchange; the strands are subsystem implementations.** Keep the
weave, respin the strands. So: contracts first, backbone second,
strands third — and a strand can be re-laid at any time without the
rope failing, which is the property that makes half-abandoned code
cheap instead of fatal.

Why Go is worth it here (name the wins so we actually spend them):
one static binary for the always-on host; goroutines + channels where
Python grew fold/parallel machinery; typed contract structs at the
workspace boundary; `context.Context` cancellation instead of
killswitch polling; cheap fearless concurrency for the heartbeat/
watcher lanes. If a Go module's shape isn't buying one of these, ask
why it has that shape.

## Phases

### Phase 1 — Contracts + behavior suite (the spec, extracted)
1a. **Contract registry.** Structured by Jeremy's forwards-compatibility
    standard (machine-local at `~/claude/fromWork/` — distill the rules,
    never commit those files: work-internal). The four rules adapted to
    workspace files: contracts owned by the writing repo; writers only
    add; readers tolerate growth; removal only via deprecate→measure→
    migrate→remove. One doc (or `contracts/` dir) naming each
    workspace artifact the backbone touches: run dir layout,
    `build/calls/*.json`, outcomes/verdicts, memory + lesson writes,
    playbook, config resolution. Each contract: shape, writer, readers,
    version. Extracted from the arch map §3 and the code, then
    *deliberately* upgraded where 3b applies. Lives where both engines
    can cite it (mainline repo, eventually).
1b. **Black-box behavior suite against the Python engine.** ~15
    scenario tests at the workspace boundary: goal in → what artifacts
    exist, what decisions were recorded. Built on maro's existing
    seams (`_DryRunAdapter`, `ScriptedAdapter` harness, recorded call
    corpora — all already in production). This suite is simultaneously
    the spec, the flaw-finder, and the successor's acceptance tests.
    Writing it is also the honest test of "are we defined enough to
    port" — if a behavior can't be pinned, that's a finding, not a
    blocker.
    Exit: suite green against Python; contract registry reviewed
    (codex design pass) and agreed.

    Triage input: the five packages where the triage found the "Python
    shape" IS the shared-workspace contract (skills, orch, playbook,
    record, pack) are exactly where the registry earns its keep — each
    needs a contract-scoping decision (keep the shape, or upgrade it
    under D3) rather than a translation. Also: the two tools that
    already compare engines at the workspace boundary
    (`tools/engine-compare.py`, `write-compare.py`) and `testenv`
    survive into this regime; the pyprobe differential harness and the
    ~30 mutation batteries do not — they stay behind with the branch.

### Phase 2 — Backbone slice (the weave, in Go)
**Scope amended by D14 (2026-09-04): v1 is the full system — backbone +
memory + self-improvement with the loop closed and measured — not backbone
plus hooks.** The Phase 3 quality/self-improvement strand moves into this
phase's exit criteria. Contract-testing practice for the process edges:
`planning/contract-testing-input.md` (distilled from Jeremy's work-side
practice, 2026-09-04).

**Design note starts from the vision, not the contracts** (Jeremy
2026-09-04: "that's still zoomed in too closely. We have the vision, laid out
in a number of iterations within our repo, goal brain, and related
documents"). The holistic drift review at
`docs/history/2026-09-04-holistic-drift-review.md` (main) is the input: its §8
lists the nine vision-level commitments the successor carries and what it
sheds; §5 lists the seven things the prototype never closed, which the backbone
designs FOR (self-improvement seam with a measurable effect contract; fork with
a join; scoped memory seam; in-app background lanes; cost/latency as an
acceptance number; removal as a first-class deliverable). `docs/CONTRACTS.md`
+ the behavior suite are how §8 is checked, not what it is.

New branch. Goal → intent/route → plan → execute steps (one-call-per-
step spine, subprocess agents as backends) → record outcome → closure/
verdict → report. Kernel-first per fan-in: config, runs, the adapter
seam, file_lock. Built idiomatic-Go against the Phase 1 suite; mined
from the stale tree only through design notes. NOW lane and AGENDA
lane both, minimal breadth: enough features that a real goal runs end
to end, nothing more.

Triage input, and it is good news: `_handle_impl` and the body of
`run_agent_loop` — the god-orchestrator — were never ported, so the
spine gets designed fresh with no translation gravity at all. Around
it, the mining shortlist (already-ported logic worth pulling through
design notes): `internal/llm` (adapter seam, subprocess backend, Fake),
`config`, `record` (store-as-constructor-argument + the locked/unlocked
write lanes), `runs`, `looptypes`, the `loop*` phase packages,
`closure`, `intent`+`now`, `loopfinalize`+`recall`, `budget`+`jsonx`.
Emulation coupling in the mined code is three habits, not fifty —
`pyval.Obj` ordered records, Python string semantics, Python
truthiness/timestamp shapes — and mechanical to strip for ~2/3 of
sites.

Port-made improvements the successor KEEPS (the old branch got these
right and the Python doesn't have them): `Budget{Name, Limit, Why}`
with a test failing on an empty Why; the resolved store as a
constructor argument with the workspace printed before any write (the
structural fix for the 2026-08-16 live-ledger incident);
`llm.Options.Purpose` mandatory at every call site with the recorder
on the same seam; cycles broken by tiny composition packages instead
of lazy imports.

Exit: the behavior suite passes against the Go engine on a scratch
workspace; one real goal runs on this box.

### Phase 3 — Strands (subsystems, in value order)
Per subsystem: short design note → codex adversarial *design* review →
build → behavior tests → codex code review. Order decided at the
Phase 2 exit checkpoint with the triage in hand; current expectation:
memory/knowledge (clean leaf, portable), then quality/self-improvement
(pure-clean-room redesign to intent — the ~90%-infrastructure loop and
the bypassed director hierarchy are the flaw-shedding targets), then
platform breadth (heartbeat, notify, viz).

### Phase 4 — Two-engine comparison (the original north star)
Same workspace, same goals, both engines; compare at the artifact
boundary. Then decide what the successor becomes.

**Amended 2026-09-05 (Jeremy).** Order after the v1 acceptance run:
(1) per-workspace working directory + operator tool policy for
executes (the Go NOW execute ran in this repo's cwd with no web, so no
world-needing goal compares); (2) the oracle class becomes part of the
experiment hypothesis — Jeremy: "you can have a tool and not know how
to use it and call it junk; that doesn't mean it's useless, just that
you're holding it wrong" — the blinded evaluator is kept and MATCHED to
what it can judge, never retired; (3) the comparison on a small goal
set. Then, once both engines run at roughly the same level: add a
couple of new features on BOTH sides and judge whether they are merely
different or one is better suited to own — the measured question is
"which is cheaper to change safely", so the features should touch the
seams the prototype never closed (fork-with-join, scoped memory,
removal-as-deliverable), with a per-feature cost ledger. Jeremy has a
feel for the Python edge classes, much less for Go; that calibration is
part of the judgment.

## Standing rules (the guardrails this plan exists to enforce)

- **Anti-lift-and-translate.** A Go module is written from the arch
  map + behavior suite + its design note. Reading Python is fine;
  *copying structure* needs one sentence of justification in the
  design note ("this shape exists because Go wants it / because the
  contract forces it" — never "because Python had it"). Reviews ask
  that question explicitly.
- **Anti-zoom-stuck.** Zoom-out checkpoint at every phase exit and at
  any tripwire: re-read this plan, revise it, then continue. Tripwires:
  a module's verification code exceeds ~1.5× its production code; a
  third session lands on the same module; any work that reproduces a
  Python-stdlib behavior for its own sake.
- **Verification bar.** Behavior tests + one adversarial review pass
  is the default DONE. Batteries, derivation guards, and differential
  sweeps are decree-only upgrades, never the default.
- **Work queue lives here.** A `## Queue

- **[NEXT] Phase 2 build, step 1** of `successor-design-v1.md` §16 — records,
  identity, envelopes, thought store, lease, contracts foundation. Design note
  review CLOSED 2026-09-04 at r7 by Jeremy's call ("Do we really need 7
  review rounds on our guidance doc...?"); r7 residuals are §19 of the note,
  picked up at the steps that touch them (2, 3, 8, 10).
 section below carries
  ordered work with status; each session reads it first, takes the top
  item, updates it. The queue is this doc — no machinery unless the
  doc demonstrably fails at it.
- **Half-complete code is disposable by design.** Abandoning a strand
  mid-build to change direction is a sanctioned move, not a failure —
  the contracts are what accumulate.

## Open questions — RESOLVED 2026-09-04 (Phase 2 commit UNBLOCKED)

All three answered by Jeremy in one sitting; recorded as D7–D9 above.
The section is kept so the questions stay readable next to their answers.

- **Q-scope** → D7. v1 = backbone + memory recording, with the
  self-improvement HOOKS designed in now and the processing added later.
- **Q-python-lift** → D8. Contract-sharpening plus real defects the
  suite or reviews surface; no Python behavior redesign.
- **Q-name/branch** → D9. Branch `successor`, code in this repo's `go/`,
  rebuilt fresh; `go-port` stays frozen for mining.

## Queue` section below carries
  ordered work with status; each session reads it first, takes the top
  item, updates it. The queue is this doc — no machinery unless the
  doc demonstrably fails at it.
- **Half-complete code is disposable by design.** Abandoning a strand
  mid-build to change direction is a sanctioned move, not a failure —
  the contracts are what accumulate.

## Open questions (blocking phase-2 commit, not phase 1)

- **Q-scope: what is successor v1?** Backbone + which strands before
  we call it viable? (My lean: backbone + memory recording is enough
  to live on; self-improvement is v2.)
- **Q-python-lift: how much Phase 1 improvement of the Python side is
  in scope?** 3b allows it; the risk is rabbit-holing in Python while
  the successor waits. Proposed rule: contract-sharpening only, no
  behavior redesign on the Python side during Phase 1.
- **Q-name/branch:** branch name for the successor, and whether the
  Go code stays in this repo's `go/` or gets its own module root.

## Queue

1. [done] python-arch-map.md
2. [done] go-tree-triage.md
3. [ ] Iterate this plan with Jeremy → v1 (open questions above)
4. [DONE 2026-08-28 — LANDED fb67b77c..90f9f601] Phase 1a shipped and
   landed to main. docs/CONTRACTS.md (11 backbone entries + B12 census of
   ~44 unregistered stores + §A rule 9 hard/soft taxonomy) taken through
   FIVE adversarial rounds to convergence: r1 REJECT (doc promised what
   the engine doesn't do) → r2 REJECT (6 verified HIGHs, fixes-of-
   instances) → r3 REJECT (6 more, the class one layer up each time) →
   r4 (2 HIGHs: census-instrument evasion + durable-card gap) → r5 NO
   HIGHs, converged. ~40 code fixes with red-verified must-detect tests:
   lesson/run-record extras round-tripping, locked RMWs everywhere
   (finalize_run, run-records via mutate_run_record, card republish),
   call-record temp+os.link publication, events.jsonl byte authority +
   single os.write + short-write honesty, one shared corrupt-metadata
   park (unique fsynced sidecars), verdict-trust hard-value validation at
   both stamp and read, strict-bool config class closed (~50 gates incl.
   workers.allow_main_push; alias-resolving census tripwire). Suite:
   10416 green. Side lesson for the Go build: every round's HIGHs were
   the COMPOSITION layer above the previous round's fixes — the successor
   should design those seams (commit points, locked RMW primitives,
   single-writer stores) rather than inherit them.
   Original sanction text follows for the record:
   Phase 1a EARLY-STARTED by Jeremy's sanction
   ("python prep work... upgrade our on-disk contracts"). Progress:
   registry (docs/CONTRACTS.md, 11 backbone entries) extracted and
   adversarially reviewed — round-1 verdict REJECT with an 8/8-VERIFIED
   ledger (headline: the doc guaranteed things the Python engine doesn't
   do). That flipped the doc's job: the five code defects it exposed are
   now the top of the prep queue. Registry REVISED per verdict +
   Jeremy's enforcement taxonomy (hard/soft element axis, readers-only-
   loosen, seam rule, least-privileged unknowns) — commit b93ba84e in
   maro-wt-contracts: honest scope (B12 census of ~44 unregistered
   stores), DEFECT-tagged reality in B2/B3/B4/B8/B9, new C0 tier of 8
   verified coexistence prerequisites. C0 implementation subagent in
   flight (lesson-store RMW round-tripping, finalize_run locking,
   events.jsonl byte-caps, call-record O_EXCL, atomic log rotation,
   file_lock strict parse, least-privileged verdict trust, tolerant
   RunRecord). Next: codex round 2 on the whole chunk, verify-before-
   fix, land via land.sh. Phase 2 remains gated on plan v1.
5. [DONE 2026-08-28 — LANDED 90f9f601..c7e673ba] Behavior suite (1b)
   shipped: tests/behavior/ (harness + data-first scenario table + 6
   subsystem modules, 29 tests, seconds-fast, zero LLM/network under
   conftest isolation). Taken through SIX review rounds to fixpoint
   (r1 skeptic+qa: vacuous B9/B8, stuck-fabrication hole, _curation
   private-key pins, misaligned agenda tables — the clarity assessor
   consumes the first agenda call; r2: log-slice-as-evidence, lifecycle
   splicing, duplicate-key tolerance; r3: loop-agnostic evidence globs;
   r4: counter-swing — unregistered artifact names, answered by
   registering B3 "Loop evidence artifacts"; r5: the new registry block
   itself misdescribed the code (L28 — wrong at birth); r6: one prose
   scope-word, rest confirmed clean). Suite carries kill proof: 4
   engine mutations red (M1 events 8, M2 stuck→success 2, M3 intake 7,
   M4 step-artifacts 1). Flaw-finder role paid immediately: explicit-
   backend build_adapter calls (pre-flight plan review) ran OUTSIDE the
   FailoverAdapter record/meter/cap seam — fixed in llm.py, every
   branch wraps, 7-way parameterized pin. Nine unpinnable-behavior
   FINDINGS recorded in tests/behavior/README.md as PHASE-2 DESIGN
   INPUT — headline items for the Go engine: registered interrupt
   intake (F1), recording on the construction-guaranteed call path
   (F6), events carry handle_id + per-lane lifecycle bus events (F9),
   port the concurrency/durability unit batteries or grow subprocess
   fault injection (F8). Suite: 10453 green. Convergence lesson
   repeated from 1a: rounds alternated between "pin too loose" and
   "pin too tight" — the fixpoint is where the registry says exactly
   what the code does, no more.
