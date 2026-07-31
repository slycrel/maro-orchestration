---
status: record
---

# Chunk B Adversarial Review — verdict-blind lanes + flow readout (2026-07-31)

Post-land review of 5f74d46 (evolver_verify + interactive-NOW verdict
closures, `goal_verdict_at` stamp, `verdict_flow` readout), per the
per-chunk discipline. Three Codex lenses (Large change: Skeptic /
Architect / Minimalist) via `codex exec` against the full
`git show 5f74d46 -- src/ tests/ pyproject.toml docs/DEFAULTS.md
docs/COMPOUND_THINKING_DESIGN.md` diff (928 lines).

## Intent

Close the two verdict-blind outcome lanes (evolver_verify, interactive
NOW) and instrument verdict flow per the taste-lens panel's round-3
prescription — without breaking interactive NOW's deliberate
keep-raw-speed property, without silent LLM spend on unkeyed installs,
and with honest counters over silent drops.

## Verdict: REJECT-as-reviewed → remediated same session

Seven distinct findings after dedup; **7/7 verified real against the
tree, 0 hallucinated** (twelfth clean round). Three highs carried
cross-lens consensus (F1 unanimous, F2/F4 by all three) — REJECT under
the verdict logic; all seven accepted and fixed in this record's
companion commit.

## Findings (verified, ordered by severity)

1. **[high — all three lenses] Interactive NOW no longer kept raw
   speed.** The hosted-free judge ran synchronously before the response
   returned; the ladder tries providers serially with a 20s default
   latency cap each (120s uncapped), so a keyed provider outage could
   add 40–240s to an interactive answer — the exact property the chunk
   declared inviolable. **Fix:** the judge runs on a daemon thread under
   a hard `_INTERACTIVE_JUDGE_BUDGET_S = 5.0` wall-clock budget (thread
   gets a snapshot copy — no shared-dict race if abandoned; daemon never
   blocks CLI exit); on breach the answer returns un-judged at raw
   speed, provenance guard still applied. Pinned with a real
   slow-adapter timing test.

2. **[high — all three] "Verdicts/week" was rows-by-record-week, not
   verdict events.** Flow bucketed by `recorded_at` only: a January row
   judged this week was invisible (outside the window), a verdict
   stamped next week counted retroactively into this week, and the
   readout could not answer its own headline question ("is the pipe
   moving this week"). The Skeptic exercised this live. **Fix:** flow
   now counts verdict EVENTS by arrival week (`goal_verdict_at`, or
   record time for genuinely at-record verdicts) alongside
   rows-recorded, explicitly labeled different populations. Live
   effect: the old readout showed 18/34-style weekly judged counts that
   were actually arrival-time-unknown historic stamps; the honest
   number is 4 known-arrival verdict events, with flow measurable from
   today forward.

3. **[high — Minimalist; folded into F2 by Skeptic] Stamped-unverifiable
   rows were excluded from the flow numerator** despite the code
   classifying them as verdict events in stock — a week of
   `closure_unverifiable` stamps read as a dead pipe. **Fix:** verdict
   events = judged + unverifiable, split shown per week.

4. **[high — Skeptic/Architect; medium — Minimalist] evolver_verify
   delays fabricated as `<1m`.** `_verify_post_apply` recorded no
   `elapsed_ms` (default 0) while the suite runs up to 900s; the
   at-record delay path bucketed the zero. **Fix:** wall-clock measured
   around the suite run and passed as `elapsed_ms`; pinned.

5. **[high — Architect] Agenda provenance verdicts misclassified as
   at-record.** The agenda lane stamps `goal_verdict_source="provenance"`
   post-record via `persist_delivered_outcome_verdict` after loop
   finalization — the source name alone doesn't say when the verdict
   landed. **Fix:** `goal_verdict_at` always wins when present; the
   at-record assumption applies only to stamp-less rows. Pinned
   (provenance + stamp → stamped_later, 1-6h bucket, not <1m).

6. **[medium — Skeptic + Architect] "Inert without keys" was false.**
   `build_hosted_free_adapter()` also requires the
   `validate.hosted_free.enabled` egress opt-in (default false — a
   credential proves authentication, not consent), so a keyed fresh
   install stays verdict-blind; the docs claimed keys were the gate.
   **Fix:** docs corrected (DEFAULTS row, design doc, killswitch
   docstring) to name the double gate. The coupling itself is KEPT —
   it IS the consent posture, not a bug.

7. **[medium — Skeptic] Judge failures were indistinguishable from
   "unjudged by design".** Adapter build errors and judge
   timeout/parse/provider failures all fell open to a verdict-less row
   identical to an unkeyed install — a broken verdict pipe would look
   merely quiet. **Fix:** attempted-but-failed verdicts stamp
   `goal_verdict_source="now_self_verdict_error"` (a stamp with no
   `goal_achieved` — schema-consistent with stamped-unverifiable);
   `verdict_flow` surfaces them as a broken-pipe counter and excludes
   them from verdict events and delays. Applies to task-path judge
   errors too, same record seam.

## What went well

- The lane closures themselves survived all three lenses: nobody found
  a path where the paid adapter judges interactively, where the
  killswitch fails, or where task-path verdict behavior changed.
- The `goal_verdict_at` stamp (including on unverifiable stamps) was
  attacked and held — F5 was about the READER's classification, not
  the stamp.
- The honest-counter posture did its job: every high was a case where a
  number would have quietly lied, and the fix was always "measure or
  say you can't", never new machinery.

## Lead judgment

All seven accepted. F1 was the chunk's own stated constraint violated
by its own implementation — the unanimous high is deserved, and the
budget-thread fix is the same cheap-insurance pattern as Chunk A's 2s
lock override. F2+F3 are the panel's actual ask ("is the pipe moving")
implemented as its record-week shadow; the live ledger made the
difference concrete — the fix shrinks this week's flow number from a
comfortable illusion to an honest 4. F6 is accepted as a documentation
defect only: the double gate is deliberate consent architecture and
stays. No false positives, no style-for-substance findings this round.
