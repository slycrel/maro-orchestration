---
status: record
---

# LeAct × maro — Opus-5 blind contrast, verified (2026-07-31)

**Status: record.** Jeremy ran an Opus-5 cowork session blind over the LeAct
paper (arXiv 2607.21856) and the public maro repo, and brought the output here
(`~/claude/opus-feedback.txt`) for verification and comparison against our
existing LeAct BACKLOG entry. Every checkable code claim was verified against
the tree at badee73 before this record was written. This file is the input for
any revisited discussion; the BACKLOG entry points here.

## The paper, in one paragraph

LeAct's contribution is a retention gate, not "learn from experts": for each
state x with known-good action y*, generate N candidate rationales, keep only
those where Δ(z) = log p(y*|x,z) − log p(y*|x) > 0 — keep an explanation only
if having it in context measurably raises the probability of reproducing the
good action. §6 ablation: random ranking at matched budget kills the whole
advantage — the gain is entirely from WHICH rationales are kept. Prior filters
(annotation, outcome correctness, model-internal likelihood) are named
insufficient. App D.2.1 gives the no-logprobs workaround: action-match reward
over M samples.

## Accuracy scorecard on the Opus contrast

~12 checkable claims: 8 verified exactly, 1 flat wrong, 2 overstated, 1
arithmetic slip. Far better than the historical 30–50%-wrong rate for blind
reviewer claims, but the errors were load-bearing enough to matter.

**Verified exactly:**
- Promotion medium→long = score ≥ 0.9 AND sessions_validated ≥ 3
  (knowledge_web.py:15), incremented only via reinforce on a similarity > 0.8
  same-task-type restatement (knowledge_web.py:339-340, 719). The only tenure
  route is being independently restated 3×. The comment "Promotion is
  unaffected — sessions_validated still gates." is verbatim (line 376).
- times_applied counts injection exposure, never effect (knowledge_web.py:1422
  via the injection path).
- Skills/lessons asymmetry: skills have UTILITY_EMA_ALPHA=0.3,
  AUTO_PROMOTE_MIN_USES=5, utility EMA + circuit breakers
  (skills.py:56-57, 1007-1036); lessons have nothing comparable.
- Fail-open LLM judge: skills.py:1203 "validation unavailable (fail-open)".
  (Wider than Opus noticed — artifact_check's exec check also fails open.)
- lens_ablation costume-vs-evidence epistemics = the paper's epistemics,
  independently derived, aimed at review instead of learning. Fair hit.
- S2 seed-reader prepends a top clean LONG lesson as a style exemplar
  (memory.py `_seed_lesson_block`; quarantine-skip applies).
- compact_ab.py is a with/without harness for one skill;
  tests/fixtures/orchestration_corpus/step_outcomes.thinned.jsonl is ~2.2MB.

**Wrong / overstated (do not inherit into future discussion):**
- "CANON_APPLY_THRESHOLD = 10 promotes on that number" — wrong. It surfaces
  candidates only, and battery V3 established there is NO promote path
  (NODE_CANDIDATE forever; readers filter ACTIVE-only). The feared
  exposure-count promotion is a dead end, not a live mechanism.
- "Lesson extraction is unfiltered rationalization" — overstated. Extraction
  is deferred until the closure/provenance verdict is stamped and receives
  goal_achieved (extract_deferred_lessons, data-r2-01: "lessons must not be
  extracted verdict-blind"). We sit at the outcome-filtered baseline the paper
  calls insufficient — not the naive baseline. Direction right, severity wrong.
- "You don't mine any of your silent oracles for lessons" — misses shipped
  machinery. The chunk-4 contradiction path is oracle-anchored negative
  selection (FULL-trust goal_achieved=False + citations → CONTRADICTION_CANDIDATE
  → adjudication → contradict_pattern → contested tier → refight), and as of
  Chunk B the test suite stamps deterministic_tests verdicts feeding
  verdict-aware extraction. The narrower claim that survives: our oracle
  anchoring is negative-only or population-level (navigator A/B: with_lessons
  58% (15/26) vs baseline 41% (49/120)); no mechanism mints or tenures an
  individual lesson from a positive oracle signal. That gap is real.
- Arithmetic: novelty boost extends audition ~10 → ~11.5 days (1.3 start,
  0.85/day, GC at 0.2), not 7 → 11. The structural point stands: a runway
  extension, not a second route to tenure.
- Steal #2 assumed live `build/calls/` replay capture; record-mode capture is
  structurally dead on single-backend boxes (seam only in FailoverAdapter —
  chunk-2 side-find, BACKLOG'd). Replay fixtures need another source here.

## What Opus adds beyond the existing BACKLOG entry

1. **Blind-spot/tenure diagnosis (the keeper).** The existing entry worries
   about false positives surviving (plausible-but-wrong narratives shipping as
   lessons). Opus identifies the opposite failure: true novel positives dying —
   a lesson that fills a hole is by definition one the extractor won't
   independently re-derive 3×, so blind-spot-filling lessons are structurally
   guaranteed to die at GC. Same gate, opposite selection error. Δ-gating fixes
   both at once by flipping selection pressure from "agrees with the corpus" to
   "changes behavior" — and "marginal contribution" is exactly what "fills a
   hole" means. Directly engages Jeremy's "maro is starting to think like me"
   concern: the mechanics don't merely permit convergence-on-the-teacher, the
   tenure rule actively selects for it.
2. **Rule-vs-reason stratification requirement.** Many maro lessons are their
   own action ("run tests before claiming done" is the decision, not an
   explanation of one). Δ ≈ 0 on those definitionally (§6 self-referential
   collapse). Any experiment must stratify rule-shaped vs reason-shaped lessons
   or the aggregate is uninterpretable; discovering the corpus split is itself
   part of the experiment.
3. **Competence-redundancy decay (follow-on steal).** Retire a lesson when the
   model now follows it unprompted (Δ vs current competence ≈ 0 — it's been
   internalized), instead of calendar 0.85/day. Strictly better decay signal;
   requires the same replay harness as the gate, so it sequences after it.
4. **Surprise-count diagnostic (free, decisive, first).** Jeremy reads the
   long-tier corpus and counts entries that SURPRISE him — not entries that are
   wrong. Zero after this many runs = collapse confirmed empirically; nonzero =
   a seed set of exactly the lessons the current gate fails to protect, and the
   Δ harness has concrete targets. Pair with the chunk-6 stored novelty field
   (novelty-at-mint per row) for a mechanical companion count.

Ranked last: the S2 style-exemplar A/B (Fig 3b: no-verbatim stratum had higher
Δ; the paper's backward prompt deliberately redacts the expert policy to stop
the generator copying). Testable on existing rails, but there's a disanalogy —
S2's exemplar shapes lesson WRITING style, Fig 3b is about rationale
generation, and taste transfer is wanted (codeLike* family). Taste transfer vs
blind-spot transfer is the distinction to hold.

## What our side already has that Opus didn't know

- Hosted-free rung: App D.2.1's action-match workaround needs M replays per
  candidate; hosted-free makes that ~$0. Opus called n "brutal" without this.
- Verdicted ground truth just got its plumbing: Chunk B (5f74d46 + badee73)
  closed the verdict-blind lanes; honest count on 2026-07-31 is 4 known-arrival
  verdict events ever, accumulating from now. A Δ-gate scores candidates
  against verdicted outcomes — the denominator starves today and grows weekly.
- The chunk-4 adjudication template (candidate → capped verdict at evolver
  cadence → append-only event) is the shipped shape a Δ-gate rides — new
  signal, no new subsystem.
- Consumer-first pin already in the entry: the filter's verdict must gate
  minting or tiering, not just annotate.

## Sequencing verdict (why not build now)

The minimum experiment stays the existing entry's (navigator A/B rails), now
upgraded: stratify rule-vs-reason; M replays via hosted-free; action-match
scored against verdicted outcomes. It starves on today's 4-event denominator.
Revisit trigger: `verdict_flow` shows a real weekly verdict flow (verdict
events arriving week over week, not stock restatements). The surprise-count
diagnostic has no such dependency and can run any time Jeremy has twenty
minutes and the long-tier corpus in front of him.
