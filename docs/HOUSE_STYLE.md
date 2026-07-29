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
   touching a subsystem.
2. **Build** — `docs/CODING_NOTES.md` posture: seams visible, rework
   cheap, test seams not internals, don't refactor mid-feature.
3. **Verify** — the executed check named in step 1, plus
   `bash scripts/test-safe.sh` (judge by exit code). "Done" without an
   executed check is a claim, not a fact.
4. **Document** — currency rule: a doc your chunk proved stale gets
   fixed in the same commit. BACKLOG/MILESTONES updated so the next
   session knows what changed.
5. **Commit + land** — end-of-chunk discipline: document → commit →
   `bash scripts/land.sh`. Don't pile up unpushed commits; a box crash
   loses unlanded work.
6. **Adversarial review, cross-model** — reviewers run on the opposite
   model family (`codex exec` from Claude, `claude -p` from Codex),
   never internal subagents (same-model review defeats the purpose).
7. **Verify-before-fix** — every reviewer finding gets checked against
   the tree before acting: measured hallucination rate across ten
   review rounds ran 0–78%, historically ~30–50%. A finding is a lead,
   not a fact.
8. **Fix + record** — accepted findings fixed and landed; the review
   record (verdict, accepted/rejected, why) goes to `docs/history/`.

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
  *Tripwire:* the clean-checkout tripwire
  (`tests/_checkout_tripwire.py` + conftest session hooks) — first
  shipped 2026-07-29. Still missing (BACKLOG): a fresh-workspace check
  that entry points behave against an empty `~/.maro/workspace`, and an
  explicit self-declared marker on learning-gated exemptions.
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
| Subsystem intent vs implementation | `skills/arch-*.md` |

## Maintenance

Same discipline as the star skill: this doc evolves, and each addition
either names its tripwire or is labeled prose-only. When a rule's
deterministic home ships (a census, a conftest hook), the prose entry
here shrinks to a pointer — the test is the rule. Review this doc when
a new invariant gets decreed or a tripwire ships; don't let it become a
second BACKLOG.
