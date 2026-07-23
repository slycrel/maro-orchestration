---
name: star
description: ALPHA star-pattern mini-orchestrator — a master loop that owns taste (choose the next task) and judgement (validate the answer) and delegates everything else, 0..n dynamically chosen steps, no pre-planned pathway. Dev-side gut check for maro's orchestration patterns; also the modern melt-test of the 2026-03-31 factory branch.
---

# star — alpha star-pattern orchestrator

**Status: ALPHA PROTOTYPE** (Jeremy, 2026-07-21). This is an experiment
instrument, not production tooling. It exists to gut-check the patterns we
are building into maro by running them as pure prompting on the Claude Code
harness. If following this skill ever requires writing supporting *code*,
stop — that is a crystallization-pressure finding to report, not a feature
to build.

## Shape

Star pattern (Jeremy's term): a master process that delegates a task,
receives an answer, judges it, then chooses the next task — repeat until
done. The process is **0..n steps discovered as you go**, not a planned
pathway. Explicitly NOT map-reduce recursion: the decomposition is not
known up front; each choice is informed by the last answer.

The master owns exactly two things (the delegation-boundary razor,
2026-07-21): **taste** — determining what the sub-agent attempts — and
**judgement** — validating that we accomplished what we set out to do.
Everything else is the sub-agent's.

## The node contract (bounded, function-shaped)

A star run is a function call, not an open-ended wander (Jeremy
2026-07-21, the email-pipeline bound: fixed inputs, bounded outputs):

- **In**: the invocation contract — goal, done-means, cuts, budget.
- **Out**: the result block (see ledger close) — named deliverables,
  done-means verdict, residuals. Nothing else escapes the run; side
  effects beyond the named deliverables are contract violations to
  report.

This uniform contract is what makes the pattern recursive *in
principle*: goal → taste+judgement → result at every scale ("our steps
in a nutshell"). Alpha keeps recursion OFF. The enabling conditions,
if/when it turns on, are structural, not vibes:

1. a child is invoked with the exact same contract shape;
2. the child's budget is a strict fraction of the parent's **remaining**
   budget — well-founded recursion, termination by decreasing measure
   (the off-the-rails guard);
3. the child's cuts at invocation are a superset of the parent's cuts
   *as they stand at that moment* (no sideways scope drift down the
   tree — but the parent's cuts are living; see the re-cutting
   guardrail);
4. the parent judges the child's result block against criteria the
   PARENT set — a child's self-reported verdict is a claim, never a
   verdict (fork-fabrication lesson, era 08).

## Invocation contract (taste, up front — do this BEFORE any delegation)

Write these four lines in your reply before the first delegation. If you
cannot fill one in, ask the user — that gap is itself a finding.

1. **Goal**: one sentence, outcome language.
2. **Done-means**: the executed check(s) that will verify completion —
   named now, not after.
3. **Cuts**: what is explicitly out of scope / what would make this the
   wrong thing to build (inversion). Cuts are a living term, not a
   frozen contract — see the re-cutting guardrail.
4. **Budget**: max delegations this run (default 8). Hitting the cap =
   stop and report honestly, never push past it silently.

## The loop (repeat 0..n times)

1. **TASTE — choose the next task.** From the goal + everything judged so
   far, pick ONE next task, or declare done / a typed stop (see Honest
   exit). A task states:
   - its **flavor** — `commit` (buys deliverable progress) or `recon`
     (buys map edits: a resolved or newly-surfaced unknown, a new
     landmark/edge, a reachability-and-cost estimate — not progress).
     A recon task must pass the **VOI gate**: name the pending decision
     its answer would change. No decision named, no recon run — "would
     clearing more fog change the next choice?" is the guard against
     ritual exploration (codex correction, 2026-07-22);
   - the outcome wanted (never the method — the sub-agent owns the how);
   - **explicit output criteria** (form, bounds, and what evidence must
     accompany claims). A task without output criteria does not get
     delegated — criteria-free prompts drift, invent, and call
     hand-waving a completed product (factory finding #3, 2026-03-31);
   - the minimum context it needs — partition, don't forward the whole
     history.
2. **DELEGATE.** One Agent-tool subagent, serial (one live at a time —
   box rule). The subagent's final text is data for step 3, not truth.
3. **JUDGE.** Validate the answer against the criteria stated in step 1
   *before* integrating it. Sub-agent reports are claims — spot-verify
   the load-bearing ones against the tree/artifacts yourself (reads and
   probes only; ~30-78% of unverified reviewer claims are wrong, five
   independent measurements). Verdict is tri-state: **accept** /
   **reject-with-evidence** / **inconclusive**. Inconclusive is never
   silently promoted to accept — it either becomes a new probing task or
   is reported as inconclusive.
   For `recon` tasks judgement asks a different question: did the map
   actually change, and are the new landmarks/edges REAL? Spot-probe
   claimed edges (a claim should name what settles it — the
   `settled_by_command` discipline); a claimed edge that can't be probed
   is inconclusive, never accepted.
4. **RECORD.** Append one row to the run ledger (kept in your reply, see
   below), including the **surprise** field: what differed from what you
   expected when you wrote the task?
5. Loop. Done only when the done-means checks from the invocation
   contract actually pass a final judgement step (run them; do not
   narrate them).

## Guardrails (structural, not vibes)

- **Master does no production work.** The master's own tool use is limited
  to what taste and judgement require: reads, searches, probes, running
  verification commands. All edits/builds/writes of deliverables are
  delegated. If a task feels too small to delegate, note that as a
  granularity finding and delegate it anyway or fold it into a larger task
  — do not quietly do it inline.
- **Re-cutting is a master taste act, on the record** (compound-thinking
  correction, 2026-07-22: cuts-first is cuts-*continuously* — possible-now
  bias protects the *thesis*; the cuts stay liquid). The master may redraw
  its own cuts mid-run when a judged result licenses it; the redraw gets
  its own ledger row whose Surprise column names the observation that
  licensed it — that audit trail is what separates re-cutting from drift.
  Sub-agents never edit cuts: a child that believes the cuts are wrong
  surfaces the evidence in its result block (the fork-contract
  escalation-trigger lane) and the master decides. Silent drift — by
  master or child — remains the violation.
- **Two consecutive rejects on the same task → escalate to the user** with
  both rejection evidences. Vary approach once; never ralph the same
  prompt.
- **Serial only.** No parallel delegations in alpha (host-OOM rule on this
  box, and star is definitionally answer-informed).
- **Recursion is not foreclosed** (standing decree) but alpha adds no
  machinery for it: a sub-agent may structure its own work internally as
  it likes; the master never spawns a star inside a star. The turn-on
  conditions live in "The node contract" above.
- **Honest exit, typed.** "Blocked" is not one thing (compound-thinking
  §3a/§13b). A run that doesn't reach done ends with one of four stop
  verdicts, each recorded with its **evidence** and its **reopen
  condition** — a stop verdict is a cached observation, not a permanent
  fact (Jeremy 2026-07-23: dead ends don't stay dead):
  - **thesis-refuted** — avenues exhausted, nothing connects; evidence =
    what was tried. Reopens on a new landmark or vantage.
  - **reachable-but-not-worth-it** — a path was found but its discovered
    cost exceeds the goal's value; evidence = the cost. Reopens when the
    cost or value estimate moves.
  - **out-of-budget** — the delegation cap hit; says nothing about
    possibility. Reopens trivially with budget.
  - **lost-the-plot** — locally green, but the assembled work no longer
    serves the original ask; evidence = the divergence. Reopens on
    re-anchor against the invocation contract.
  All four are legitimate endings — report them plainly. A green summary
  over unrun checks is the failure mode this whole instrument exists to
  catch.

## Run ledger (required, in the final reply)

| # | Task (outcome) | Flavor | Criteria stated? | Verdict | Surprise |
|---|----------------|--------|------------------|---------|----------|

Close the ledger with the **result block** (the node's bounded output):
- **Deliverables**: each named artifact — path + one-line description.
- **Done-means verdict**: pass/fail/inconclusive, with the actual check
  output (run it, don't narrate it).
- **Stop verdict** (only when not done): one of the four typed stops,
  with evidence and reopen condition (see Honest exit, typed).
- **Residuals**: what remains undone or uncertain, honestly.
- **Cost**: delegations used vs budget.
- **Findings**: crystallization-pressure, granularity, or strategy notes
  (which local move was chosen where — one-shot / delegate / would-have
  -recursed — and whether it was right in hindsight). These strategy
  rows are the corpus that strategy selection can later be learned from.

## Alpha adjudication (pre-registered — the test that can fail)

The lineage this instrument descends from (playbook, Stage-5 rules,
factory branch) half-died for lack of a decision gate. This one carries
its own:

- **Usage expectation**: exercised on real scoped tasks opportunistically
  during the swarm-review chunks (not gating them).
- **Keep signal**: it surfaces a pattern violation or design insight that
  the normal flow missed, at least once per ~3 uses, OR its ledgers
  measurably sharpen DEV_PATTERNS.
- **Kill signal**: two consecutive uses produce nothing the normal flow
  didn't already have, or its overhead exceeds its findings.
- **Adjudicate at swarm-review arc end** (or after 5 uses, whichever
  first) — verdict written to GOAL_BRAIN Decisions either way. No silent
  half-death.
