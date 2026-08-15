---
status: record
---

# Adversarial review — closure claimed-but-unwired probe (chunk 3), 2026-08-16

Target: commit 3874675 (pattern-5 probe in closure_verify). Reviewers:
sonnet-medium ×3 lenses (Skeptic / Architect / Minimalist) — codex
usage-capped until ~08-19, so the proven fallback lane ran; claims-audit
protocol framing (audit the claims record, attack the labeled residue,
cover what the author can't see). Fix commit: the one carrying this file.

## Verdict: CONTESTED → fixed same session

16 findings across three lenses, zero hallucinated (every load-bearing
premise verified against the tree before acting — the Signal-1 regex
really does match the original block's wording; safe_list really does
drop non-dict items silently). The round is a textbook claims-audit win:
the HIGHs were not in the deterministic core the author probed — they
were in the seams the author's own tests structurally could not see
(prompt-induced judge phrasing, LLM shape drift, gate-metric validity).

## Accepted and fixed (with pinning tests)

1. **Signal-1 echo side channel (Minimalist HIGH).** The verdict-prompt
   paragraph invited the judge to name unwired guarantees in gaps; the
   original block wording ("no executed check exercised them") matches
   `_RUNTIME_GAP_ADMISSION` verbatim — a judge echo would convert the
   advisory block into a deterministic True→False flip on all-static
   runs, falsifying C4 via a channel `test_advisory_never_flips` (mocked
   judge) cannot see. Fixed three ways: block/doctrine wording chosen
   outside the regex's match surface; the prompt now asks for the marked
   form "Unverified claim: …"; `_strip_coverage_echo_gaps` keeps marked
   lines out of the admission scan (both call sites — initial + retry),
   organic admissions untouched. Pinned by
   test_marked_coverage_gap_does_not_trip_signal1 +
   test_organic_admission_still_trips_signal1. Labeled residual: an
   induced-but-UNMARKED echo (judge ignores the phrasing instruction)
   still fires Signal 1 — fail-open to pre-probe behavior, bounded by
   the ≥0.6 floor and (on opted-in boxes) the audit cancel lane.
2. **Exercised/contradicted links invisible to the judge (all three
   lenses, HIGH — the round's convergence).** Surfacing only unwired
   entries meant a mismapped or plan-fabricated "exercised" link — the
   laundering direction, worse than an honest unknown — got LESS
   scrutiny than unwired, and "contradicted" (the strongest disproof)
   was never explicitly linked. The block now renders every recorded
   entry with status markers and link-scrutiny doctrine ("a passing
   check proves its guarantee only if it actually exercises that
   behavior"). Pinned by test_coverage_block_reaches_verdict_call.
3. **Audit lanes blind to coverage (Architect HIGH-adjacent).** The
   pass-audit fires on exactly the all-static achieved shape pattern-5
   targets, yet audited blind; the negative-audit's "holds strictly more
   evidence" premise was false for coverage-informed verdicts. Both
   lanes now receive the shared block renderer (must-not-fork
   convention). Pinned by test_audit_lanes_receive_coverage_block.
4. **Shape drift reads as silence (Architect HIGH).** safe_list dropped
   a bare-string guarantees list silently — indistinguishable from "no
   guarantees stated", biasing the keep/kill firing data toward kill.
   `_claim_coverage` now takes the raw payload: strings become unwired
   entries; junk-typed entries and non-list payloads count in
   `malformed`. Pinned by test_string_shaped_entries_are_unwired_not_silence.
5. **Overflow undercounted the gate metric (Skeptic).** Entries beyond
   the 8-cap skipped `counts` entirely — the many-guarantees runs that
   argue loudest for keep were the ones undercounted. Counts now cover
   every guarantee; only the recorded list (and block) is capped, with
   `overflow` naming the excess. Pinned in
   test_cap_overflow_and_empty_text_dropped.
6. **Token-cap regression risk (Skeptic + Architect).** 768 was a guess
   sharing one JSON object between checks and an unbounded-at-generation
   guarantees list; overflow loses the CHECKS (a regression vs the
   512/guarantees-free world). Prompt now says "at most 8"; max_tokens
   1024 with shape-based sizing in the comment; both pinned by
   test_plan_call_caps_pinned.
7. **C2 overclaim: plan_index leaked into the judge payload (Minimalist)
   + event context unprobed (Architect).** results_text now scrubs
   plan_index (judge payload byte-identical on guarantee-free plans);
   the persisted row deliberately KEEPS it as the join key — now pinned
   as intentional, alongside the event-context absence assertion, in
   test_absent_key_is_todays_behavior.
8. **Keep/kill gate validity (Minimalist HIGH, docs fix).** "unwired
   fires" is nearly guaranteed by the prompt's own conservative bias
   (null-when-unsure), so firing alone can't justify keep. The BACKLOG
   gate now requires discrimination evidence — exercised AND unwired
   both firing across the corpus — plus spot-checks in BOTH directions
   (fabricated guarantees; semantic correctness of exercised mappings),
   and watches the malformed/inconclusive buckets.

## Accepted as labeled limitations (no code change)

- **Evasion by omission (Skeptic + Minimalist).** A run that narrates
  vaguely extracts zero guarantees and skips the probe entirely; the
  adjudication corpus is structurally biased toward over-narrating runs,
  and fleet-wide silence is indistinguishable from fleet-wide honesty.
  Real, unlabeled in the original commit, not fixable at this seam
  (guarantee extraction from deliverable CONTENT is future work) — now
  named on the BACKLOG gate entry so the adjudicator reads the corpus
  with the bias in mind.
- **Semantic mapping honesty** stays the labeled residue: no code-level
  guard can verify a check MEANS its guarantee; the fix is the judge
  seeing the link (fix 2) and the gate spot-checking it (fix 8).

## Rejected / no action

- Injection-scanning the plan/verdict construction path (Architect,
  rider on finding 2): work_summary was already unscanned in these
  prompts pre-chunk — a real but PRE-EXISTING seam, wider than this
  chunk; noted here, not silently patched as if it were new.
- Commit-message test-count nit (Minimalist LOW): "4 tests" vs 5 in the
  class — miscount in an immutable message; recorded here.

## Meta

Sonnet-medium fallback lane: 0 hallucinated findings again. The round
validates the claims-audit protocol's prediction about where defects
hide when step 3 is done honestly: not in the probed deterministic core
(C1/C5 survived contact untouched) but in prompt-side seams and the
eval hook's decision validity — both places the author's unit tests
structurally cannot reach.
