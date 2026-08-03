---
status: record
---

# Playbook surprise read (2026-08-02)

**Status: reading artifact** — verbatim snapshot of
`~/.maro/workspace/playbook.md` (the evolver-maintained "Director's
Playbook", last self-updated 2026-07-29), taken 2026-08-02 so it renders in
the Reading tab. The live file is untouched.

This is the second self-maintained corpus getting an operator read (first:
the 2026-07-31 lesson-corpus read). Stakes are higher here: unlike lessons,
which inject selectively, **this document is injected into every director
and decompose call** — a wrong entry taxes every run.

## How to do the read

Same instrument as the lesson read, both signals welcome:

- **Surprise** — entries you would not have written yourself (taught the
  system something you didn't carry).
- **Argue** — entries you'd push back on. Chunk 1 of the lesson read showed
  the reaction data is at least as valuable as the surprise count.

React with entry numbers (`P1–P24`). 24 entries, ~10 minutes.

## Mechanical companion notes (before your read)

- **Three evolver entries are truncated mid-sentence** (P9 ends "adjust p",
  P10 ends "Do all of", P17 ends "goal no") — the evolver writes clipped
  lines into the director's context on every run.
- **The drift warnings are frozen alarms** (P19, P20, P23): three
  cost-rise-vs-baseline warnings from different eras all still "live",
  telling every run to consider rollbacks that may long since have
  happened. The playbook has no retirement path — nothing has ever exited.
- **P18 is a July run-debugging TODO** (two stuck haiku tasks) sitting in
  permanent every-run context.
- The calibration entries (P21, P22) are near-duplicates of each other.

---

## The playbook, numbered

### Decomposition

**P1** — Research goals benefit from a gather → synthesize → verify structure.

**P2** — Narrow goals (≤15 words) should get 1-4 steps, not more.

**P3** — Wide/deep goals should use staged-pass decomposition.

**P4** — More atomic steps > fewer broad steps. One file or one command per step.

### Execution

**P5** — If a step fails 3 times, the problem is usually the decomposition, not the execution.

**P6** — Token budgets for build tasks should be ~2x research tasks.

**P7** — Always verify outputs before recording as done.

**P8** — Before Write/Bash calls targeting file creation, validate: (1) target path is within /home/clawd/.maro/workspace/ unless explicitly authorized elsewhere, (2) parent directory exists or can be created, *(from evolver:8f7419c8-00)*

**P9** — When a step is marked 'blocked', add an investigative prompt: 'Why is this blocked? Is it a permission error, missing dependency, malformed task spec, or environment issue?' Attempt recovery (adjust p *(from evolver:8f7419c8-03)*

**P10** — Before execution, require all agenda goals to include explicit success criteria: 'Task succeeds when: [measurable condition]'. Reject vague goals like 'System file-relocation diagnostic' or 'Do all of *(from evolver:fdf888c3-00)*

### Cost

**P11** — Execution floor is MID (2026-07-21 unification decree); POWER at orchestrator/planner/reviewer decision points; CHEAP only for non-agentic calls (classify, triage, curation).

**P12** — Enable extended thinking for decompose (high) and advisory calls (mid).

**P13** — Narrow goals should skip multi-plan (saves 3 LLM calls).

### Quality

**P14** — The verification loop is the highest-leverage investment.

**P15** — Inspector friction signals should be acted on, not just logged.

**P16** — Standing rules are zero-cost — promote aggressively when validated.

### Learned

**P17** — For agenda tasks with explicit 'goal' state (achieved/NOT-achieved), define acceptance criteria upfront and validate against them before marking task complete. If completion steps are done but goal no *(from evolver:8f7419c8-01)*

**P18** — Investigate tasks bef5ea6e and b22b3ece (identical 'write a 3-line haiku about a fresh hermes install to file hermes_haiku.t' goals)—both stuck on identical step. This is a signal of either (1) inhere *(from evolver:8f7419c8-02)*

**P19** — avg_cost_usd has risen 21.6% from baseline (1.0350 vs 0.8515) for 7 consecutive cycles. Recent evolver changes may be degrading quality — consider rolling back recent auto-applied suggestions. *(from evolver:drift-3c1405d9)*

**P20** — avg_cost_usd has risen 42.1% from baseline (1.6163 vs 1.1372) for 4 consecutive cycles. Recent evolver changes may be degrading quality — consider rolling back recent auto-applied suggestions. *(from evolver:drift-571f333f)*

**P21** — CONFIDENCE MISCALIBRATION in category 'observation': self-reported confidence 0.92 but empirical pass rate 0.50 (3/6 verified). Reduce LLM confidence prompts for this category or tighten auto-apply th *(from evolver:calibration-12738e5f)*

**P22** — CONFIDENCE MISCALIBRATION in category 'observation': self-reported confidence 0.90 but empirical pass rate 0.43 (3/7 verified). Reduce LLM confidence prompts for this category or tighten auto-apply th *(from evolver:calibration-b9922c90)*

**P23** — avg_cost_usd has risen 53.2% from baseline (3.5405 vs 2.3106) for 7 consecutive cycles. Recent evolver changes may be degrading quality — consider rolling back recent auto-applied suggestions. *(from evolver:drift-92fc56aa)*

### Signals

**P24** — [Signal] Complete the AI-failure-task-patterns catalog v2, then analyze failure families to extract patterns that could inform Mode 1/2/3 taxonomy refinement and self-improving system robustness. *(from evolver:sig-94a153df)*

---

# Jeremy's reactions (2026-08-02, verbatim)

> playbook surprise things:
>
> p2 - this is the same as a 200 char limit on output; we simply don't
> know what a run might take, even if it's just a few words; inappropriate
> IMO
>
> p3 - I'd rephrase this as "often" rather than "should" — again, judging
> via guessing rather than allowing it to organically form; this is
> advice/direction, not requirements right?
>
> p4 - agree, though I'd probably say it differently. Again, sounds like a
> command instead of guidance, in the direction of guessing again.
>
> For all of these we get into prompt semantics... I think we want to say
> "usually, do this" instead of "it has to look like this". Otherwise we
> run the risk of an LLM doing what we ask, rather than what it might be
> capable of.
>
> p6 - surprised; I'd consider build paths as sidequests that lead back to
> either a chained sidequest series or a result that allows for a step to
> be completed.
>
> p8 - slight surprise, paths are fragile, we should reference a maro
> configuration instead of the path directly.
>
> p9 - surprised; seems to be working around systemic limitations (this
> data should already be available to examine in step meta-data?)
>
> p10 - slight surprise... are we categorically denying multi-step
> sidequests with this prompting (run a collection of steps and return the
> results)?
>
> p11 - positive surprise, expect that those categories may change over
> time, slight concern there
>
> p13 - "should" feels strong here, "could" might be better? Seems like
> the wrong confidence going in blind to me.
>
> p14 - slight surprise. I might say a good skill or foundational data
> would trump the verification as the highest leverage, though if this is
> only quality scoped maybe that's true
>
> p16 - slight surprise; I agree generally, concerned that if we fast
> track without a fast track out for problematic results, we might be in
> for some pain later.
>
> p17 - yeah, this is cut off and needs completed, my gut says it's not
> quite right, but fine on it's face
>
> p18 - seems like it doesn't belong here
>
> p19, p20, p23 - slight surprise, not sure that cost should win over
> correctness; there will always be tension here, but we shouldn't only
> consider cost. Probably downstream symptom of degradation work?
>
> p21, p22 - also cut off, definitely a half-formed thought, needs
> corrected
>
> p24 - is this post-run work that happens? Seems fine, assuming that's
> the context.

Unflagged: P1, P5, P7, P12, P15.

## Disposition (applied same day — live playbook + repo seed rewritten)

The pre-rewrite live file had accreted PAST this snapshot: a 4th drift
alarm (drift-a2af29bb), a 3rd calibration near-dup (calibration-781771a6,
pass rate down to 0.33), and three more parked Signals — the frozen-alarm
problem was still compounding while the read sat in the queue. Archived
verbatim to `playbook_history/playbook-20260803T001831Z-operator-surprise-read-rewrite-2026-08-02.md`
before the rewrite; untruncated originals for every clipped entry were
recovered from `memory/suggestions.jsonl`.

| Entry | Action |
|---|---|
| P2 | **Removed** (live + seed). Certified inappropriate — step-count caps guess what a run needs. |
| P3, P4, P13, P14 | **Rephrased to usually-form** (live + seed), per the prompt-semantics decree. P14 carries the caveat "a good skill or foundational data can beat it". |
| P6 | Rephrased to a prior ("~2x tokens is a fair prior, not a cap"). The build-paths-as-sidequests reframe is recorded here as design direction for the open side-quest thread (INTENT_RESOLUTION_DESIGN), not minted as playbook doctrine. |
| P8 | Literal `/home/clawd/.maro/workspace/` replaced with `config.workspace_root()` reference. |
| P9 | **Retired — the systemic answer is yes**: blocked steps now get deterministic diagnosis (`loop_blocked` failure classifier + recovery planner) and terrain memory records blocks across runs (§5b, live-confirmed 2026-08-02). The entry was a 07-10 workaround for machinery that has since shipped. Also truncated. |
| P10 | **Replaced**: success-criteria guidance kept in usually-form; "Reject vague goals" dropped for clarification-first ("prefer a clarifying question or deriving criteria over rejecting"), matching the standing director-clarification decree. Answer to the question: nothing structural denies multi-step side-quests — the risk was exactly this injected prose biasing decompose, now removed. |
| P11, P12 | Kept. P11's category-drift concern noted: the tier map cites the 2026-07-21 decree, so it has provenance to re-check against when tiers change. |
| P16 | Kept + the fast-track-out added: "contest/retire is the exit when one turns out wrong" — the contest verb (rules grey-flip + lesson contest, both live) is that exit. |
| P17 | **Completed** from the recovered original (8f7419c8-01) in guidance form. The "not quite right" gut: its enforcement half is now systemic (done≠successful split at closure); the upfront-criteria half is still useful at decompose time, which is what the completed entry keeps. |
| P18 | **Removed** — July run-debugging TODO (two stuck haiku tasks) in permanent every-run context. |
| P19, P20, P23 (+4th) | **All four drift alarms removed.** They are frozen-in-time cost observations phrased as quality verdicts ("may be degrading quality — consider rolling back") with no expiry — and yes, downstream of degradation work: emitted by the evolver drift checker, never retired. Cost-vs-correctness read distilled into one Cost entry ("cost signals are inputs to weigh, not verdicts"). Mechanism fix (alarms need an expiry/resolution path) → BACKLOG. |
| P21, P22 (+3rd) | **Collapsed to one completed entry** carrying the full trend (0.50 → 0.43 → 0.33) from the recovered originals. The finding is real and worth keeping; three clipped near-dups were not. |
| P24 (+3 more Signals) | **All four Signals retired from the playbook.** Answer to the question: no — these are NOT post-run work that happens. They're `sub_mission` suggestions parked "for human review" (`evolver.auto_enqueue_signals` defaults false) in permanent injection context, where nobody reviews them. sig-94a153df's ask (failure-pattern catalog) was completed separately; sig-9c78fc89's ask (`complete_step` audit) was done by the 2026-08-02 residue counter. The two never-reviewed remainder (sig-4b23f9d6 self-diagnosis audit, sig-1266881a fact-check-pipeline packaging) are preserved in the archive + `suggestions.jsonl`; a real review surface for held signals → BACKLOG. |

**Root-cause fixes shipped with the rewrite:** the truncation was a bare
`suggestion_text[:200]` slice at both evolver append sites — removed
(`append_to_playbook`'s 500-char cap with an honest ellipsis is the only
truncation now). The prompt-semantics decree is stamped into the playbook
header and `_SEED_CONTENT` so future readers and fresh installs inherit
the form.
