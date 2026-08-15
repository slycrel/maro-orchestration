---
status: living
---

# House Style — the dev approach itself

**What this is.** Jeremy (2026-07-28): "our learned-over-time 'house
style' is meaningfully impactful, and maybe deserves its own
intentionality loop... would be hard to replicate in a vacuum on a new
machine." This doc writes the approach down where it's portable. Shape
approved 2026-07-28 ("glad this is in the backlog... assume it will
continue to evolve").

**Status: v1, repo-visible material only** (2026-07-29). Deferred halves,
named so they don't silently evaporate: (b) importing the durable
feedback-class auto-memories from `~/.claude` (machine-local today),
interview-elicited gaps, and the codeLikeJeremy pattern steals
(descriptive-vs-prescriptive split, negative-space-as-signal, style doc
with a regression harness — BACKLOG holds the full list). Those need
Jeremy in the loop; this half doesn't.

---

## The workflow loop

One chunk of work, start to finish. This loop IS the house style — the
individual rules below exist to keep it honest.

1. **Shape** — read `docs/DEV_PATTERNS.md` (taste half): cuts-first,
   consumer-first, done-means named before building, scope inversion,
   possible-now bias. Load the relevant `skills/arch-*.md` before
   touching a subsystem. **State the chunk's contract as falsifiable
   claims** (Jeremy decree 2026-08-16, the anti-"naive development
   optimism" reframe: work is proving a theory, not making assertions
   review will test for you): what must be true after this change, and
   — census FIRST, by field/class, not after a reviewer asks — which
   class members the change must reach (every writer, reader, branch
   twin, sibling lane).
2. **Build** — `docs/CODING_NOTES.md` posture: seams visible, rework
   cheap, test seams not internals, don't refactor mid-feature.
3. **Verify = run the experiment yourself** — the author probes every
   claim from step 1 before landing; review's job is auditing the
   evidence, not discovering that the assertion was never tested. Per
   claim: the executing probe (degenerate inputs, the literal
   production path end-to-end, and must-detect for every new test —
   stash the fix, watch it fail). A claim you could not probe ships
   HONESTLY LABELED "reasoned, not probed" in the commit message so
   review can target exactly the unproven residue. The claim → probe →
   result record rides the commit. Then `bash scripts/test-safe.sh`
   (judge by exit code). "Done" without an executed check is a claim,
   not a fact. **Read the totals line too, not just the exit code** — a
   green exit says nothing about how many tests *ran*. On 2026-08-02
   this box had been silently skipping ten router tests for want of
   `scikit-learn` (declared in pyproject's `dev` extra, never
   installed), and the runner couldn't have told you: its own `-q`
   stacked with pyproject's into `-qq`, which suppresses the "N passed,
   M skipped" line entirely. Both fixed; the habit is the durable part.
   A skip count above zero is a finding, not a footnote — the one
   legitimate standing skip is the Darwin ABI probe, inert on Linux.
4. **Document** — currency rule: a doc your chunk proved stale gets
   fixed in the same commit. BACKLOG/MILESTONES updated so the next
   session knows what changed.
5. **Commit + land** — end-of-chunk discipline: document → commit →
   `bash scripts/land.sh`. Don't pile up unpushed commits; a box crash
   loses unlanded work.
6. **Adversarial review** — with step 3 done honestly, review's role
   SHIFTS (2026-08-16): audit the claims-and-probes record, attack the
   labeled unproven residue, and cover what the author structurally
   cannot (cross-chunk seams, an adversary's perspective, the
   DEV_PATTERNS reviewer watch-list) — not re-derive untested
   assertions. Convergence in 1-2 rounds is the health signal; a round
   that finds an unprobed claim the author didn't label is a step-3
   failure, not a review win. Reviewers run on the opposite model
   family when available (`codex exec` from Claude, `claude -p` from
   Codex); sonnet-medium same-model is the proven fallback (2026-08-14
   decree window) — never internal subagents.
7. **Verify-before-fix** — every reviewer finding gets checked against
   the tree before acting: measured hallucination rate across ten
   review rounds ran 0–78%, historically ~30–50%. A finding is a lead,
   not a fact.
8. **Fix + record** — accepted findings fixed and landed; the review
   record (verdict, accepted/rejected, why) goes to `docs/history/`.

## Reviewer roster — which model sits in which seat

Cross-model (step 6) decides the *family*; this table decides the
*tier*. They answer different questions and both apply: an
adversarial-review reviewer must be on the opposite family (a
same-model subagent is not a reviewer), and any same-family delegate —
the read-only review agents, a fan-out verifier — still needs a tier
chosen deliberately.

The design rule (2026-07-27, work box): **model choice is a property of
the role, not the moment.** Encoding `model:`/`effort:` in agent
frontmatter decides the tier once, deliberately, instead of
re-reasoning it under time pressure per invocation. `effort` is the
second knob and often the better lever — raising effort on a cheaper
model frequently beats jumping a tier.

| Seat | Model | Effort | What it's for |
|---|---|---|---|
| `review-skeptic` | opus | xhigh | Correctness — what inputs break this |
| `review-architect` | opus | xhigh | Structure — coupling, boundaries, what gets harder next |
| `review-minimalist` | sonnet | high | Necessity — what can be deleted |
| `build-verifier` | haiku | low | Builds/tests → pass/fail + failing lines only |
| `deep-debugger` | fable | max | Root causes that survived the obvious |

Tier fit, from the `claude-api` skill: fable (10/50 per 1M in→out) —
hardest, longest-horizon only; opus (5/25) — review, verification,
architecture; sonnet (3/15) — default workhorse; haiku (1/5) —
mechanical fan-out.

Three calls worth keeping when this gets copied:

- **The three lenses replace a generic "code reviewer."** Distinct
  parallel perspectives strictly dominate one general pass — same
  reason step 6 scales 1→3 lenses by diff size.
- **`review-minimalist` runs sonnet, not opus** — `/simplify` applies
  cleanups, so this seat only has to *find* excess.
- **`deep-debugger` is gated hard in its own description.** Fable is 2×
  opus; reaching for it should feel expensive.

Adjacent measured finding (`docs/history/2026-07-30-taste-lens-panel.md`,
round 2): **frontier-at-xhigh lenses ship rejection conditions;
mid-tier lenses mostly don't** — four arms, twenty lens outputs, two
families, three capability tiers. Round 2 overturned a settled decision
and produced the arc's first kill criteria; round 1 at mid-tier didn't.
That's the argument for spending the expensive tier on *design* review
specifically, not on review generally.

Provenance and honest limits: the roster was built and is installed on
Jeremy's **work box** (`~/.claude/agents/`, 2026-07-27 session record at
`~/claude/2026-07-27-subagent-setup-handoff.md`); it is **not installed
on the maro box** — here it's guidance, not configuration. The tiers are
reasoned, not benchmarked; the panel finding above is the only measured
tier-vs-review-depth evidence we have. *Tripwire:* prose-only.

## Standing invariants, each with its tripwire

The house rule about house rules: a principle ships with the test that
could fail it, or it's labeled prose-only. Prose-only rules rot;
tripwired rules fire (decree-with-tripwire, DEV_PATTERNS).

- **Out-of-the-box invariant (Jeremy DECREE 2026-07-28):** "Functionality
  that we add should presume that it's 'out of the box' functionality
  for the project day 1 — unless it's specifically functionality gated
  on prior learning data"; corollary: "the work we're doing should be
  able to be verified on a clean clone of the maro repository."
  Violated silently for months (flat `src/` masked by `PYTHONPATH=src`,
  caught only by the 2026-07-09 docker clean-machine trial).
  *Tripwires, both halves now armed:* the clean-checkout tripwire
  (`tests/_checkout_tripwire.py` + conftest session hooks, 2026-07-29 —
  the suite must not litter the working tree), and the fresh-workspace
  tripwire (`tests/test_fresh_workspace.py`, 2026-08-08 — eleven
  read-only entry points must answer against an empty
  `~/.maro/workspace`, and against one whose directory does not exist
  at all). The second scrubs all three workspace env aliases before
  invoking, because an ambient pin on a developer box is precisely how
  the original violation survived for months. Still missing (BACKLOG):
  an explicit self-declared marker on learning-gated exemptions.
- **Config honesty:** every documented config key has a live reader.
  *Tripwire:* `tests/test_defaults_doc.py` census over
  `docs/DEFAULTS.md` (AST-shape resolution, zero hand exemptions;
  reverse census added swarm-review chunk 8).
- **Consumer-first:** a store, emitter, or flag ships in the same chunk
  as its proven-live consumer and a liveness test. The repo's own
  history is the case law — decisions.jsonl sat readerless for months;
  contradict_pattern had no writer; the playbook injected a dead
  window. *Tripwire:* per-feature liveness tests (pattern, not one
  census — each chunk pins its own).
- **Decree capture (SF-13):** any decree-class statement from Jeremy
  gets a GOAL_BRAIN.md Decisions line before session end, and since
  2026-07-21 is also piped to the runtime decision journal
  (`python3 -m knowledge_lens decision`). *Tripwire:* none —
  prose-only, honestly labeled; it's a session-close habit.
- **Data retention:** never auto-delete run/user data; archive-before-
  rewrite (playbook curation keeps `playbook_history/`); the path is
  part of the result. *Tripwire:* prose-only; enforced by review.
- **Declared liveness (2026-07-29 monitoring decree):** shipping a new
  dynamic process — anything that's supposed to keep firing on live
  runs after the shipping session ends (a writer at a cadence, a
  lifecycle stage, an A/B loop) — includes declaring it in
  `DECLARED_PROCESSES` (`src/system_health.py`) with a probe that can
  observe it actually executing. The case law: the A/B variant
  subsystem sat wired-but-never-executed for four months behind green
  tests because nothing watched whether it *ran*. *Tripwire:* the
  probes themselves (`SUBSYSTEM_SILENT` fires when a declared process
  stops observably executing); coverage of *new* processes is
  review-enforced prose until a census exists.
- **Compiled truth beats narrative:** GOAL_BRAIN.md wins over any other
  doc when they disagree; narrative snapshots rot (the CLAUDE.md
  "current state" section sat stale for months and was removed rather
  than refreshed). *Tripwire:* prose-only.

## Where the rest lives

This doc is the loop + invariants. The neighbors it deliberately does
NOT duplicate:

| Concern | Doc |
|---|---|
| Taste + judgement checklists | `docs/DEV_PATTERNS.md` |
| Coding posture during iteration | `docs/CODING_NOTES.md` |
| Session discipline, shared-tree git rules, landing policy | `CLAUDE.md` |
| Config key census | `docs/DEFAULTS.md` |
| Review mechanics | `.claude` adversarial-review skill; records in `docs/history/*-adversarial-review.md` |
| Runtime (not dev) review seats | `src/quality_gate.py` evidence-path lenses; `docs/LENSES.md` proposed registry |
| Subsystem intent vs implementation | `skills/arch-*.md` |

## Maintenance

Same discipline as the star skill: this doc evolves, and each addition
either names its tripwire or is labeled prose-only. When a rule's
deterministic home ships (a census, a conftest hook), the prose entry
here shrinks to a pointer — the test is the rule. Review this doc when
a new invariant gets decreed or a tripwire ships; don't let it become a
second BACKLOG.
