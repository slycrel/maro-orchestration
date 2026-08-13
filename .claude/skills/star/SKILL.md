---
name: star
version: 7
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

**Prior-attempt check (re-run identity, 2026-08-10):** before the first
delegation, ask whether this goal — or its dead ends — has run before
(prior ledgers, stop verdicts, existing deliverables). A re-run points
taste at the prior result's residuals instead of re-treading; an
IDENTICAL goal re-asked will mostly re-find the old answer, so either
aim at what the last run did NOT settle or name the different axis.

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
   - the **failure contract**: every delegated task states that if the
     sub-agent cannot complete, it must return a typed `blocked_on`
     block instead of a shrug (see Diagnosis at the failure boundary);
   - the minimum context it needs — partition, don't forward the whole
     history. Partitioned context must keep facts and assumptions
     separable: judged-accepted findings pass as established; anything
     unjudged (a hypothesis, a prior task's unverified claim) passes
     labeled as an assumption, never restated as fact — an unproven
     fact is just an assumption, and honest labeling is what licenses
     passing it at all (world-facts §7.1 as amended, 2026-08-11).
   - if success depends on the sub-agent using a specific tool or
     method, state it in the criteria as a decision rule ("if X, do Y —
     do not Z"), not a mention: advertisement-only teaching was
     measured insufficient (A/B-4, 2026-08-11 — the advertised verb
     rode every prompt and was invoked zero times).

   When a prior task came back `blocked_on`, routing its recommendation
   is itself a taste act with its own ledger row: run the proposed
   experiment as a `recon` task / spawn a capability side-quest / reroute
   away (the goal may no longer need that step at all) / honest typed
   stop. The step recommended; the master decides — never auto-adopt the
   step's proposal. **Dedup at the map**: two tasks blocked on the same
   *named missing thing* (`what_would_be_different`, not the cause enum —
   "missing GitHub access" and "missing DB credentials" are both
   missing-capability but need different side-quests) become ONE routing
   decision, not two experiments.
2. **DELEGATE.** One Agent-tool subagent, serial (one live at a time —
   box rule). The subagent's final text is data for step 3, not truth.
3. **JUDGE.** Validate the answer against the criteria stated in step 1
   *before* integrating it. Sub-agent reports are claims — spot-verify
   the load-bearing ones against the tree/artifacts yourself (reads and
   probes only; 0–78% of unverified reviewer claims are wrong across
   many measurements — later review rounds measured 0%, so the rate is
   unpredictable and verification stays unconditional). Verdict is
   tri-state: **accept** / **reject-with-evidence** / **inconclusive**.
   Inconclusive is never silently promoted to accept — it either
   becomes a new probing task or is reported as inconclusive.
   Verdict trust rides evidence modality (MH #1 pass-audit,
   2026-08-10): an accept built solely on static reads — nothing
   executed, nothing probed — gets ONE adversarial refutation question
   ("what executed check would catch this being wrong?") before it
   stands; if the answer names a runnable check, run it.
   For `recon` tasks judgement asks a different question: did the map
   actually change, and are the new landmarks/edges REAL? Spot-probe
   claimed edges (a claim should name what settles it — the
   `settled_by_command` discipline); a claimed edge that can't be probed
   is inconclusive, never accepted.
4. **RECORD.** Append one row to the run ledger (kept in your reply, see
   below), including the **surprise** field: what differed from what you
   expected when you wrote the task? A task whose `blocked_on` SURVIVES
   judgement records `blocked:<cause>` in the Verdict column — an
   accepted observation, not a reject: it does not count toward the
   two-consecutive-rejects escalation (the routing row owns what happens
   next). A malformed or excuse-shaped `blocked_on` is a
   reject-with-evidence like any other unsupported claim, and DOES
   count. The master's routing decision gets its own row either way.
5. Loop. Done only when the done-means checks from the invocation
   contract actually pass a final judgement step (run them; do not
   narrate them).

## Diagnosis at the failure boundary (§14, 2026-07-27)

A failed pass must produce a causal hypothesis before it becomes a
verdict. The diagnosis question every blocked sub-agent answers: **what
would have to be different for this pass to succeed?** The answer is a
typed `blocked_on` block:

```
blocked_on:
  cause: missing-capability | missing-information | wrong-approach
         | terrain | transient
  what_would_be_different: <the specific missing thing, named>
  proposed_experiment: <the check that would prove it is really missing
                        — the settled_by_command discipline applied to
                        the blockage itself>
```

The cause types route differently: missing-capability/tool/access is
*acquirable* (side-quest), missing-information is *recon-able*,
wrong-approach is *re-plannable*, terrain is *genuinely refuted*
(feeds thesis-refuted), transient is *retriable*.

Two boundary cases the cause set deliberately does NOT absorb:

- **Cost-shaped inability is not a blockage.** "Feasible, but it
  exceeds my granted budget / violates the cuts" returns as a
  *reachability-and-cost estimate* in a normal result block — a map
  edit feeding the master's worth-it judgment
  (reachable-but-not-worth-it), not a `blocked_on`.
- **The diagnosis question applies at every failure, not only
  self-declared blockage.** When the master REJECTS or marks
  INCONCLUSIVE a completion attempt, the routing row for what happens
  next types the master's own cause hypothesis from the rejection
  evidence — a failed pass never becomes a bare retry or a silent
  abandon without a typed cause (that's the §14 smell this section
  exists to kill).

**Falsifiability is the excuse-killer.** "I can't" must name the
specific missing thing AND the experiment that would prove it's really
missing. An excuse names nothing specific, so it fails diagnosis and is
rejected like any other unsupported claim; a boil-the-ocean ask is the
same failure inverted — wanting everything because the one thing was
never named.

**Ownership split (Jeremy's tweak — the load-bearing part): the step
DIAGNOSES and RECOMMENDS; the master DECIDES and ROUTES.** A blocked
sub-agent may substantiate its hypothesis with evidence gatherable
inside its already-granted scope and budget (the `which <tool>` class —
that makes the recommendation falsifiable, not routing). The line IS
the already-granted scope/budget: inside it, running the probe is
substantiation — and a probe that RESOLVES the blockage means the step
isn't blocked, keep working; beyond it, the probe is named as
`proposed_experiment` and returned unrun. What the step never does with
a probe result, either way, is route on it (choose the side-quest,
expand its own scope, or reroute itself). The
discriminating experiment as a plan move, the side-quest spawn, and the
revisit are map operations the step does not own — dedup only exists at
the map (steps can't see each other's blockages), and the master may
route away regardless (the goal may not need that step; a step
self-serving its experiment pre-empts a reroute that was never its call
— delegation-boundary razor: routing is parent-taste). A blocked task
is a landmark with a reopen condition, not a dead end: revisit when the
routing decision lands its new capability or data.

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
  fact (Jeremy 2026-07-23: dead ends don't stay dead). A reopen
  condition should also name the TOOLSET the stop was declared under:
  a dead end that predates a new capability is re-examinable with one
  cheap probe — "sometimes you have to revisit previous dead ends with
  new tools... for a path to open up" (Jeremy, §14h 2026-08-11; depth
  is bridge-building / puzzle-solving / pathfinding, and revisits are
  the puzzle-solving mode). *When* to declare
  one is no longer left to taste alone — see the structural-stall
  trigger below:
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

## Structural stall — the declare-blocked trigger (§9.3, 2026-07-29)

The ledger's **Map Δ** column records what each judged result actually
changed: deliverable progress, or a **named** map edit (new landmark or
edge, a resolved or newly-surfaced unknown, a reachability-and-cost
estimate). The FIRST failure of an approach is a map edit — an edge
marked impassable; the *same* failure again is not, and scores Δ 0. A
second task blocked on the same named missing thing is likewise Δ 0
(the dedup-at-the-map rule, restated as measurement). This is Phase
62's `_is_converging` one level up: map-edit rate, per compound-thinking
§12 nudge 1.

**Trigger: two consecutive judged rows with Δ 0 = structural stall.**
The master must then do one of two things, on the record:

- declare the appropriate typed stop (Honest exit) citing the zero-Δ
  streak as its evidence, or
- write a routing row naming the NEW avenue the next task opens — a
  different frontier, vantage, or approach, not the same probe run
  louder. That row is what separates justified persistence from
  ralphing.

Silently delegating past a fired trigger is a contract violation. The
trigger is deliberately decoupled from budget — it can fire on
delegation 3 of 8. Which verdict to declare stays judgement: a stall
while probing usually feeds thesis-refuted; a stall that surfaced a
cost feeds reachable-but-not-worth-it.

## Run ledger (required, in the final reply)

| # | Task (outcome) | Flavor | Criteria stated? | Verdict | Map Δ | Surprise |
|---|----------------|--------|------------------|---------|-------|----------|

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

**ADJUDICATED 2026-07-28 (Jeremy): KEEP.** At 2 uses, both met the keep
signal (use 1: stop-path survey seam map — ~50 seams/11 families the
normal flow hadn't surfaced; use 2: recon/diagnosis contract + cap-stuck
numbers). No kill signal fired. Standing intent: keep growing the skill
as the workflow evolves. Anti-prompt-soup rules now in force: additions
must be contract changes or distilled principles (war stories go to
history docs, this file gets the rule + a pointer); consolidation pass
when the file doubles or ~3 arcs pass, whichever first, archiving the
prior version (playbook-curation discipline); the DEV_PATTERNS
graduation valve applies — anything that gains a deterministic home
(census/test) leaves this file. A runtime port of this contract is the
NOW retry rung — shallow half SHIPPED (artifact-seeded retry; the
star-shaped arm (ii) stays a pre-registered open experiment, see
BACKLOG "NOW retry rung").

**Versioning (Jeremy, 2026-08-12):** the frontmatter `version` bumps on
every contract change, and a bump asserts the changed contract has been
exercised at least once — version is a tested-revision counter, not a
edit counter. Set to 7 at introduction (matches the seven distinct
change-days in git history: 07-21, 22, 23, 27, 28, 29, 08-12). Usage
note, same date: exercised 8–10+ times by Jeremy's count (adjudication
was at 2 uses) — keep signal standing.

**Consolidation pass 2026-08-12** (the ~3-arcs condition fired:
verdict-integrity, REPL A/B, world-facts arcs all closed since the KEEP
verdict; prior version archived at
`docs/history/2026-08-12-star-skill-pre-consolidation.md`). Five
contract deltas folded in, each from a landed arc: prior-attempt check
(re-run identity), assumption-labeled context partitioning (world-facts
§7.1 amendment), teach-as-decision-rule (A/B-4 falsifier a),
evidence-modality refutation at judge (MH #1 pass-audit),
toolset-stamped reopen conditions (§14h revisit mechanic). Claim-error
range updated 30–78% → 0–78% (later rounds measured 0%; verification
stays unconditional).
