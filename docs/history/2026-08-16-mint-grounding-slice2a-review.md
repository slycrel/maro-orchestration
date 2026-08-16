---
status: record
---

# Adversarial review — mint-grounding slice 2a (face30b), 2026-08-16

Four reviewers, sonnet-medium fallback via `claude -p` (codex capped
until Aug 19): Skeptic, Architect, Minimalist, **plus the 4th-persona
try-once** (expert QA — error paths, kill paths, sad-path record loss;
GOAL_BRAIN 08-01 decree, first outing).

## Intent

Complete lesson-lane grounding coverage (step-lessons, prereq,
thinkback), fix R1-4 laundering (KnowledgeNode.grounding at create,
promotion-judge visibility, advisory-only), doctrine fixed: fail-open,
no new judge.

## Verdict: CONTESTED → fixed to green same session

15 raw findings, deduped to 9 distinct. **0 hallucinated** (streak
intact). 3 HIGHs, all verify-before-fix CONFIRMED, all fixed:

1. **[HIGH, Skeptic] Title-truncation false-support** — grounding
   `f"{title}. {description}"` let `_extract_heuristic`'s 8-word title
   prefix drop a claim's identifying specifics; the truncated duplicate
   sentence then had no identifier-shaped tokens and rode the
   family-level fallback to `supported` off an unrelated event — the
   R1-1 false-support class reopened by composition in a new caller.
   Reproduced live (probe red pre-fix). **Fix:** ground DESCRIPTION
   only; title-only claims are the accepted residual. Also killed
   Skeptic's medium (title/description double-count).
2. **[HIGH, QA persona] Unprobed rendered as refutation** — an
   all-unprobed node reached the promotion judge as "0/3 method
   claim(s) supported by event-log receipts", dressing the design's own
   documented honest-default (>30% unprobed is the EXPECTED v1 regime)
   as failure. **Fix:** three-way render; unprobed states "not checked —
   uncertainty, not refutation"; supported-count line only when
   something was supported.
3. **[HIGH, Minimalist] Missed CREATE sibling: `world_facts.land_facts`**
   — minted candidate nodes with `loop_id` already in hand and no
   stamps; declared world facts are exactly the claim shape R1-4 names.
   Watch-list probe 2 (sibling census) doing its job. **Fix:** the
   anecdotal-landing loop grounds each fact's description; pinned.

Mediums fixed: forged/non-list `grounding` no longer `len()`s into a
fabricated ratio (treated as absent; pinned); the two silent excepts in
`outcome_to_knowledge` replaced by the module's own batch entry point
(`ground_lessons_for_run`) per Architect's boundary finding, and all
writer-site excepts now `log.debug`; the NODE injection surface
(`inject_knowledge_for_goal`) now renders `grounding_marker` — QA's
point that the laundering had otherwise just moved one hop downstream
to the one consumer not marking (pinned).

Accepted, not fixed (recorded): Skeptic's precompute-waste on the
update path (bounded, low); Architect's empirical question — LLM-
extracted node prose is generalized and may rarely carry the past-tense
claim shape, so R1-4's hit rate on the dominant path is unmeasured →
folded into the existing >30%-unprobed falsifier census (evidence-
gated); helper extraction for the now-3-site fail-open block waits for
slice 2b's 4th site per the repo's own three-is-fine rule; Minimalist's
grounding_marker-reuse for the judge block declined with rationale
in-code (judge is not prompt-space-constrained; wording carries the
weigh instruction).

## 4th persona (expert QA) — try-once verdict: EARNED ITS SEAT

Unique yield no other lens produced: the unprobed-as-refutation HIGH,
the consumption-surface laundering gap, the zero-logging finding with
its named missing tests, and the forged-shape probe. Zero overlap-only
findings. Recommendation: institutionalize as the 4th lens for LARGE
reviews (Jeremy to ratify — the 08-01 decree reserved the roster call).

## Probes

6 new red-first tests (verified via stash-rerun), suite 8890 → 8896 / 0
skipped. Fix commit follows this record.
