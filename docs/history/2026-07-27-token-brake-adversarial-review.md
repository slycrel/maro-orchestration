---
status: record
---

# Mid-step token brake — adversarial review, remediation, and merge-gate round (2026-07-27)

Subject: commit 344973f (`token-lean-fetch` branch) — the mid-step token
brake, touch-up #3 of the token-lean fetch arc. Reviewed by 3 Codex lenses
run from the Opus session (opposite-model rule); remediated there
(acc5eda); merged to main by the maro-box session (merge gate), which
contributed the box-only measurements and caught one further ship-blocker
in the remediation itself.

## Review verdict: REJECT (consensus), headline verified

**Headline (3/3 lenses, verified directly by the lead AND independently
re-confirmed at the merge gate):** `token_runaway` was retried.
`budget_runaway` is special-cased in `loop_execute` (run stops, by
design); `token_runaway` appeared ZERO times in the loop layer, so the
typed error fell into generic blocked-step recovery, which returns
`retry=True` on first occurrence — a step killed at 300K could burn
another 300K, then be redecomposed, then mark the whole run stuck. The
exact inverse of both stated guarantees. The no-retry logic existed only
at the adapter seam (`classify_error` → `retryable=False,
failover=False`), which the merge-gate's own earlier verification had
checked — and wrongly generalized from. Lesson recorded: verify an
error-policy claim at EVERY layer that consumes the error, not just
where it is classified.

**Highs (2/3 each, both real, both design calls):**
- **Read bypass**: the Bash cap doesn't cover the Read tool (`curl -o
  page.html` + Read). The enforcement claim was stated too broadly.
- **Cache-read amplification**: excluding cache reads from the fresh
  ceiling leaves re-send amplification unbounded (250K fresh + twenty
  cached transcript re-reads ≈ 5M input while `total_fresh` sits flat).

**Mediums (reproduced):** drain-before-exit race (fast-exiting call
bypassed the probe entirely; the committed test hid it behind a 30s
sleep — reproduced 10/10 and 5/5 by two lenses); `MARO_BASH_MAX_OUTPUT_CHARS=0`
failed to override an operator-exported `BASH_MAX_OUTPUT_LENGTH`;
malformed usage fields failed open and discarded the rest of the event
batch; killed calls persisted as zero-token/zero-cost; the typed error
was swallowed by broad handlers in `step_exec.py` and `team.py`.

**Reviewer self-correction (recorded because it's the discipline working):**
the lead nearly filed "BASH_MAX_OUTPUT_LENGTH is a no-op" — at 100K both
arms look identical because both persist. At 25K chars the default admits
all 25K while the var clamps the retained preview (~2K) and lowers the
persist threshold. The mechanism works; the honest minimalist framing is
that 24000 buys a ~20% tightening over the CLI's own default while making
the invariant maro-owned config.

## Remediation (acc5eda, Opus session)

No-retry special case at the top of `_handle_blocked_step`; drain-and-
probe-once-more after subprocess exit; explicit `{key: None}` = "unset in
child" for the `=0` override (plain `update()` would set the literal
string "None"); defensive usage parsing with ingest recorded before the
optional cost estimate; `TokenRunawayError.estimated_cost_usd` +
`fresh_input_tokens` carried into the blocked outcome; re-raise through
both broad handlers. +8 tests, suite 6658 green on the worktree.

## Merge-gate round (this box): the remediation's own gap

The remediation's decision object said `loop_status=""` ("flow is normal,
the loop advances") — but the terminal handler coerces
`_decision.loop_status or "stuck"`, and `loop_execute` breaks on
`"stuck"`. Net effect: no-retry delivered, run-continues NOT — a
brake-killed step silently stopped the whole run. The codex-round pin
asserted the decision object, not the flow (the same shape as the 30s-
sleep test hiding the drain race, one layer up).

Fix at merge: explicit `advance: bool` on `_BlockDecision` (set only by
the token_runaway branch), honored by the terminal handler; the
`loop_execute` caller adopts only a non-empty status so `""` neither
breaks the loop nor clobbers the final `"done"`. Flow-level pins added
(`TestRunawayAdvancesAtTheFlowSeam`): token_runaway → flow "normal",
empty status, no re-queue, no retry accounting; ordinary terminals still
coerce to "stuck".

## Calibration: measured, not reasoned (box-only work, done during the review)

Mined 499 loop logs / 1,529 steps / 727 run dirs on this box.

**Trap metric named:** raw run-log `tokens_in` (p90 = 390K, 239 steps
> 300K, top tail 3.94M — all status=done) is NOT the brake's metric: main's
own `ResponseUsage.input_tokens` documents "INCLUDING cache reads", and
the historical accumulator carried the per-content-block double-count
(live probe on CLI 2.1.220: one assistant event per content block, same
message id, byte-identical usage echo — dedupe-by-id-with-max counts
exactly once; under-counting would require incremental same-id usage,
which the CLI demonstrably does not emit).

**Honest metric:** fresh tokens cost ≥ $3/M on the MID floor, so
`provider_cost_usd / 3` hard-bounds brake-counted fresh ingest. Over the
123 cost-metered steps: p50 = 69K, p95 = 259K, p99 = 403K, max = 478K.
Only 4 steps could possibly have exceeded 300K — exactly the pathology
band (golden-birch $1.43, the run-3 tire step $1.21, dapper-heron $1.01,
rapid-heron $0.93). Caveat: loop logs record no per-step model; if any
metered step ran haiku pricing the bound triples (paranoid envelope: 36
steps), but the loop lane was MID-default throughout the metered era.

**Decisions (Jeremy, 2026-07-27 — "your recommendations are probably as
good as mine this late, let's run with them"):**
1. **Fresh ceiling stays 300K** — above healthy p95 (259K bound), at the
   bottom of the pathology band. Raising it to clear the raw tokens_in
   tail would calibrate against cache reads + a fixed bug.
2. **Ceiling #2: weighted ingest** (fresh + cache_read/10 = input-side
   cost in fresh-token equivalents), default 600K ≈ 2.3× healthy p95,
   above every observed step — fires only on the amplification shape.
   Unweighted total-ingest rejected: healthy long steps legitimately
   accumulate multi-100K reads, putting any attack-catching total too
   close to the false-positive band.
3. **Read bypass accepted as Bash-only, guarantee restated** (DEFAULTS
   row): fetch-extract reads are the sanctioned artifacts-over-streams
   lane; a wholesale Read of a large capture lands as fresh ingest and
   the accumulation ceilings are the backstop.

Also closed: the CLI-version item (box = 2.1.220, same version the spill
behavior was verified against; the live spill test ran on this binary).

## Designed trade, stated plainly

The run-3 tire step (status=done, $1.21) would likely have been killed by
this brake. That is intended: the fetch lane now does that work for
pennies, and the brake interdicts regression to raw-curl grinding. A
false kill is visible and cheap — error_class `token_runaway`, step dies,
run continues, never silently retried.
