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
- **D2 — Same workspace.** Both engines read/write the same
  `~/.maro/workspace/` formats. The workspace is the compatibility
  boundary; internals diverge freely.
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
- **Work queue lives here.** A `## Queue` section below carries
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
4. [in flight 2026-08-28] Phase 1a EARLY-STARTED by Jeremy's sanction
   ("python prep work... upgrade our on-disk contracts"): contract
   registry extraction (subagent, in maro-wt-contracts worktree) →
   review → codex design pass → land; then additive contract
   sharpenings + tolerant-reader fixes from its findings, tested +
   codex-reviewed + landed. Phase 2 remains gated on plan v1.
5. [ ] Behavior suite (1b) — after the registry lands
