---
status: record
---

# Chunk 9a Adversarial Review (2026-07-23)

Scope: commits a527911 + f0fd4e0 + 65478b2 (diff 5751393..65478b2 — 761
insertions, 6 files: design-doc §12/§13, codex-take record, star SKILL.md
contract, stop-path survey record, GOAL_BRAIN/MILESTONES). Reviewers:
3 Codex lenses (`codex exec`, read-only, opposite model family) —
Skeptic, Architect, Minimalist. Every finding master-verified against the
tree before acceptance (house rule; historical hallucination rate 30-78%
— this round: 7/7 real, 0 hallucinated, tenth substantially-clean round).

## Intent

Under Jeremy's partial approval ("dig in where it appears that we all
agree, and make the right things concrete"), ship ONLY the
three-way-agreed compound-thinking points — §13 corrections (one
map/vantage, revisitable milestones with reopen conditions, river
terminology, side-quest proposal), the star recon/stop-verdict contract —
and exercise the new contract once on a real recon task (the stop-path
survey), producing an accurate record to steer the future src/ wiring.
Deliberate non-goals: src/ changes, resolving open §9/§10 questions.

## Verdict: PASS with fixes

No high-severity findings. 7 findings (5 medium, 2 low), all verified
real, all accepted at least in part, all fixed in the follow-up commit.
The survey's core content held: both Skeptic and Architect independently
spot-verified 10+ classification-table rows and the main mechanical
claims (budget gates → stuck, landing synthesis, run_curation
flattening, closure demotion flip, no structured stop-cause field,
done-unverified learnable) — all confirmed.

## Findings

1. **[medium] The survey silently uses categories beyond the approved
   four verdicts** — `external-interrupt` (~14 rows) and `not-a-stop`
   (~20 rows) appear as "Nearest verdict" while the invocation contract
   claims classification "against the four stop verdicts"
   (survey:15 vs star SKILL.md:138).
   Lens: ALL THREE, independently (the round's only consensus finding).
   Principle: prove-it-works / boundary-discipline.
   Fix applied: explicit "Taxonomy coverage" section in the survey — the
   four verdicts are goal-directed stop causes and do not cover
   infra/human interruption; fifth-verdict vs status-level-concern named
   as an open agenda question. Added to the MILESTONES agenda.

2. **[medium] run_director declared possibly-dead is live, and its stop
   vocabulary was missed** — survey's uncertain list said "may be
   reachable only from tests/older callers"; actual callers: `maro
   director` (cli.py:494) and Telegram `/director|build|ops`
   (telegram_listener.py:347-350). director.py:580-581 collapses worker
   outcomes to done/stuck; workers.py stamps `blocked` for three distinct
   causes (LLM failure :264-270, honest flag_blocked :287-291, no useful
   output :312); director.py:558-566 review-cap exhaustion silently
   accepts best-effort. Lens: Skeptic. Principle: prove-it-works.
   Fix applied: uncertain-list entry struck with correction; 5 verified
   seam rows added in the survey's corrections section; noted
   DirectorResult/WorkerResult is a separate status vocabulary a
   LoopResult-only stop_verdict would not reach.

3. **[medium] "Single choke point" wiring note too narrow** — four
   learning/recall consumers read raw `status` and bypass
   classify_outcome: outcome_policy.py:44-50 no-success_class fallback,
   recall.py:157-165 unjudged-attempt fallback, strategy_evaluator.py:
   62-67 status weights, attribution.py:367-371 status filter.
   Lens: Architect. Principle: boundary-discipline.
   Fix applied: post-review note on the wiring observation — stop-verdict
   wiring must land on the outcome row (memory_ledger sibling field), not
   only in curation metadata.

4. **[medium] "Reachable-but-not-worth-it recorded nowhere / judged in
   exactly one place" overstated** — dispatch navigator close
   (handle.py:2743-2747; budget-pressure-preferring per
   navigator_prompt.py:78-80) is a second close-shaped judgment and
   records `classification_reason="navigator_close"` machine-readably.
   Nuance kept: it fires pre-run, so no verdict ever reaches the outcome
   row of the run that actually hit the wall — the survey's structural
   point stands. Lens: Minimalist. Principle: prove-it-works.
   Fix applied: post-review notes on conflation 6 + unrepresented-verdicts
   prose; MILESTONES headline reworded.

5. **[medium] Survey record lacked the verification outputs the star
   contract it exercises requires** ("run it, don't narrate it" —
   SKILL.md result-block rule; the record pointed at the ephemeral
   session transcript). Lens: Skeptic. Principle: prove-it-works.
   Fix applied: all 11 spot-verifications written into the record with
   what each cited source line actually says.

6. **[low] Landing-synthesis headline unconditional** — the budget branch
   replaces remaining steps with a synthesis step; status stays "done"
   (initialized loop_execute.py:236) only when that step succeeds.
   Lens: Minimalist. Principle: prove-it-works.
   Fix applied: conditional wording in the survey conflation 2 and
   MILESTONES.

7. **[low] §13a "one map" ambiguous against §6/§10 cross-goal language**
   ("every future map", "across maps"). Reviewer framed it as
   superseded-text rot; master re-verified and REFRAMED: no
   contradiction — §13a's decree is against per-recursion-level maps
   within a goal; cross-goal map identity was never decided.
   Lens: Architect. Principle: foundational-thinking.
   Fix applied: scope note in §13a naming the per-goal reading and
   flagging cross-goal map persistence as an open agenda question.

## What went well

- Both verifying lenses independently confirmed 10+ survey rows and every
  headline mechanism — the two-recon-delegation + master-spot-check
  pipeline produced zero false claims that survived to the record.
- The star contract itself came through clean: ledger schema, loop text,
  and the four typed stops are internally consistent (Skeptic checked
  explicitly); no responsibility leaks in the taste/judgement boundary
  (Architect); no crystallization pressure — the prompt-only constraint
  held (Minimalist raised no overhead finding).
- The partial-approval boundary held: no src/ changes, open questions
  left open (verified by Minimalist).

## Lead judgment

Accept 1-6 as stated. Accept 7 reframed (the "superseded" reading was
wrong — verified against §13a's actual scope — but the ambiguity is real
and cheap to kill). Note for the star adjudication corpus: finding 1 is
the interesting one — the exercise *discovered* a coverage gap in the
taxonomy it was applying, and the honest move was to surface it as an
agenda question rather than force-fit or silently extend; that is
exactly the "cuts are living" behavior the contract intends, it just
happened without being narrated as such. Findings 2 and 5 are the
survey's own quality bar applied to itself — fair and fixed.
