---
status: record
---

# Stop-Path Survey — where maro runs actually stop today (2026-07-23)

Substrate for wiring the §9.4 stop-verdict split (compound-thinking chunk
9; Jeremy's partial approval of 2026-07-23). Produced as a **star-skill
exercise** — the first run under star's new recon contract (task flavors,
VOI gate, typed stops; landed f0fd4e0 the same day) — so this record is
also star-adjudication evidence.

## Invocation contract (stated before delegation)

- **Goal**: a verified survey of every seam in maro's runtime (src/) where
  a run stops short of clean success, classified against the four stop
  verdicts, with file:line evidence.
- **Done-means**: reproducible sweep patterns listed; every found seam in
  a table with mechanism + nearest verdict + conflation notes; master
  spot-verifies ≥5 load-bearing claims against source; explicit list of
  verdicts with no distinct representation.
- **Cuts**: src/ runtime only; no code changes; no fix designs beyond
  one-line notes; step-level mechanics only where they decide the run's
  outcome.
- **Budget**: 5 delegations (serial). Used: 2.

## Run ledger

| # | Task (outcome) | Flavor | Criteria stated? | Verdict | Surprise |
|---|----------------|--------|------------------|---------|----------|
| 1 | Inventory every run-stop seam in src/ | recon | yes | accept (6/6 spot-checks confirmed) | ~50 seams across 11 families — far past the handful of headline mechanisms; budget-pressure "landing synthesis" ends the run status **done** with partiality only in manifest text |
| 2 | Classify inventory vs the four stop verdicts | recon | yes | accept (5/5 spot-checks confirmed; one grep-miss resolved — value assigned via variable at handle.py:2739) | **reachable-but-not-worth-it is recorded nowhere**; closure demotions bypass the taxonomy's own done-not-achieved bucket via their status flip |

VOI gates (stated before each delegation): both tasks answer "where does
the stop-verdict split wire in, and which verdicts need new representation
vs relabeling" — the pending build-order decision for tonight's artifacts
conversation.

Master spot-verification (judgement, per the 30-78% unverified-claim
rule): task 1 — loop_init.py:83-108 budget gate, loop_execute.py:364-391
landing synthesis, closure_verify.py:991-1005 unjudged verdict,
handle.py:2091-2107 demotion, run_curation.py:64-68 status sets,
loop_blocked.py:1077-1088 exhausted-options — all confirmed verbatim.
Task 2 — classify_outcome's `else: unknown` fall-through, the
done-not-achieved bucket-bypass logic, `_LEARNABLE_SUCCESS_CLASSES`
including done-unverified, director close/surface writing only the
escalation artifact (grep-level check: no verdict stamp in the escalation
handler), classification_reason="navigator_escalate" via variable — all
confirmed.

## Result block

- **Deliverables**: this record.
- **Done-means verdict**: PASS — sweep patterns listed below; all
  inventory seams classified in the table; 11 spot-verifications run (not
  narrated — outputs in the session transcript); unrepresented-verdicts
  section explicit.
- **Residuals**: the task-1 "Uncertain" list below, unverified by design.
  The load-bearing one: whether closure verification reliably catches
  landing-synthesis partials (bears directly on conflation 2) — verify
  before wiring.
- **Cost**: 2 of 5 delegations.
- **Findings** (star adjudication corpus): no crystallization pressure
  (zero supporting code needed). Keep-signal evidence, use #1 under the
  new contract: the exercise surfaced two insights the normal flow had
  missed — the done-labelled out-of-budget ending, and the demotion
  bucket-bypass. Recon-flavor verification (spot-probe the claimed edges)
  caught nothing false this run: 11/11 claims held, which is itself a
  data point for the claim-verification priors.

---

## Task-1 sweep patterns (reproducibility)

```
grep -rniE 'blocked|abort|give_up|giveup|bail|halt' src/ --include="*.py" -l
grep -rniE 'escalat|budget|exceeded|goal_achieved|exhausted|max_attempts|convergen|unreachable' src/ --include="*.py" -l
```
then targeted per-file greps over the run-lifecycle modules
(loop_init/loop_execute/loop_blocked/loop_post_step/loop_finalize/
loop_parallel/agent_loop/handle/handle_queue/closure_verify/director/
run_curation/memory_ledger/audit_policy/outcome_policy/heartbeat/orch/
build_loop_runner) with reads of every hit region. Full inventory with
per-seam trigger/aftermath detail: subsumed by the classification table
below (the table keeps every seam's file:line, mechanism, and recorded
label).

## Task-1 uncertain list (honesty — not findings)

- director.py:100 `"needs_approval"` — declared in the status type
  comment, no assignment found; apparent dead enum value.
- ~~run_director (director.py:579-581) result feed into run-level outcome
  untraced; may be reachable only from tests/older callers.~~
  *[post-review: WRONG — live callers exist (`maro director` via
  cli.py:494; Telegram `/director|build|ops` via
  telegram_listener.py:347-350). See "Post-review corrections" for the
  director/worker seam rows this family adds.]*
- stamp_outcome_verdict internals (memory_ledger.py:570) — edge behaviors
  unread.
- audit_repair.py un-quarantine flow unread.
- quality_gate.py non-escalate weak-outcome paths unenumerated.
- metrics.spend_today body unread (which spend sources count).
- navigator.act_blocked_step runtime config state on this box unchecked
  (code default OFF; box enabled it 2026-07-03 per memory).
- **Landing-synthesis closure interaction unverified** — whether these
  by-design partials classify as "success" is the open question that
  matters most downstream.

---

# Task-2 deliverable (verbatim, master-judged; bracketed **[post-review]** notes added after the chunk's adversarial review — see "Post-review corrections" at the end)

## Classification table

| Seam (file:line) | Mechanism | Current label recorded | Nearest verdict | Conflation note |
|---|---|---|---|---|
| src/loop_init.py:83-108 | Daily budget pre-start gate | `status="stuck"`, `stuck_reason="daily budget exhausted: ..."` | out-of-budget | "stuck" puts a preset-cap refusal in `_FAIL_STATUSES` alongside genuine dead ends; run never probed the goal at all |
| src/loop_init.py:197-210 | Kill-switch pre-start refusal | `status="interrupted"`, `stuck_reason="kill switch active: {msg}"` | external-interrupt | clean (distinct status), but "interrupted" falls in no run_curation status set → success_class "unknown" |
| src/loop_init.py:276-333 | Busy admission refusal | `status="refused_busy"`, `stuck_reason=str(_busy)` | external-interrupt | Mostly clean — distinct status, heartbeat re-queues to TODO; a scheduling deferral, not a goal verdict; also lands success_class "unknown" |
| src/agent_loop.py:335-408 | Write-fence setup failure | `status="stuck"`, `stuck_reason=_fence_msg` | external-interrupt | Infrastructure failure stamped "stuck" — downstream reads it as goal failure though the goal was never attempted |
| src/loop_execute.py:306-316 | max_iterations ceiling | `loop_status="stuck"`, `stuck_reason="hit max_iterations=..."` | out-of-budget | "stuck" loses "cap hit, possibility unknown"; only stuck_reason prose distinguishes it from retry-exhaustion stops |
| src/loop_execute.py:364-391 | Budget-aware landing synthesis | `status="done"`; partial-ness only in manifest text `[REPLAN — budget pressure]` | out-of-budget | Worst inversion: an out-of-budget stop recorded as "done" — if closure fails open it becomes done-unverified, which is LEARNABLE (outcome_policy.py:14) |
| src/loop_execute.py:860-873 | Token budget breaker | `loop_status="stuck"` (+ token-budget reason) | out-of-budget | Same as max_iterations; the finished-plan carve-out is the only cost/possibility separation, and it lives in control flow, not the label |
| src/loop_execute.py:878-896 | Cost budget breaker (×1.2 slush) | `loop_status="stuck"` | out-of-budget | Same loss; slush and warn-at-80% show cost-awareness that never reaches the recorded label |
| src/loop_execute.py:908-915 | Runaway-cost circuit | `loop_status="stuck"` (`error_class="budget_runaway"` on step) | out-of-budget | Preset multiplier cap, but carries extra signal (pathological single call) that "stuck" discards; error_class survives only on the step outcome |
| src/loop_execute.py:1054-1062→1334-1353 | Stuck-streak terminal (3× same outcome) | `loop_status="stuck"`, `stuck_reason="same outcome '{s}' on '{step}' repeated 3 times"` | thesis-refuted (amb. out-of-budget) | Ambiguous: 3× is a preset count-cap, but the semantic is "avenue not changing outcome" — step-local, never lifted to a goal-level verdict |
| src/loop_execute.py:1631-1646, 1702-1703 | _ae2 restart (verify_failure≥2 / 5-step drift) | `loop_status="restart"` | not-a-stop | The drift trigger is the ONLY in-loop lost-the-plot-shaped detector, and its signal is consumed by restart, never recorded as a verdict |
| src/loop_execute.py:1843-1856 | Injection-triggered director restart | `loop_status="restart"` | not-a-stop | clean (re-run machinery) |
| src/loop_execute.py:1713-1727 | Interrupt check | `loop_status="interrupted"` | external-interrupt | clean status-wise; success_class "unknown" hole applies |
| src/loop_post_step.py:360-371 | Kill switch mid-loop | `loop_status="interrupted"`, `stuck_reason="kill switch: {msg}"` | external-interrupt | clean |
| src/loop_post_step.py:374-381 | Wall-clock timeout | `loop_status="interrupted"`, `stuck_reason="wall-clock timeout ({secs}s)"` | external-interrupt (amb. out-of-budget) | Ambiguous: a preset time cap sharing the human-stop label; downstream can't tell operator kill from cap expiry without parsing reason text |
| src/loop_post_step.py:453-463 | Interrupt-queue stop | `loop_status="interrupted"`, `stuck_reason="stopped by {source}: ..."` | external-interrupt | clean (source captured in prose) |
| src/loop_blocked.py:887-907 | MISSING_INPUT honest-fail | stuck, `"MISSING_INPUT: a required input appears absent — ..."` | thesis-refuted (weak fit) | Really "reachable pending input" — none of the four fit; "stuck" reads as failure though possibility is untested; prefix is greppable but not a field |
| src/loop_blocked.py:929-952 | Timeout split failure | stuck, `"TIMEOUT and split-recovery failed: ..."` | out-of-budget (amb. thesis-refuted) | Root cause is a preset per-step cap; one failed recovery ≠ avenues exhausted, but the label claims terminal failure |
| src/loop_blocked.py:971-976 | Retry-churn exhausted | stuck, `"retry_churn after {n} re-decompositions"` | thesis-refuted (amb. out-of-budget) | Preset count ceilings (≥2/≥2) doing duty as "avenues exhausted"; no distinction recorded |
| src/loop_blocked.py:304-308 | Re-decompose machinery errored | stuck, `"re-decompose failed after {n} retries: ..."` | external-interrupt | Recovery tooling broke, not the goal — recorded identically to genuine exhaustion |
| src/loop_blocked.py:317-332 | Adapter-hung detection (3× max timeout) | stuck, `"Adapter appears hung: ..."` | external-interrupt | Backend-dead verdict recorded as goal failure; then 370-426 attributes skill failure for a dead backend |
| src/loop_blocked.py:1077-1088 | Exhausted-all-options | stuck, `stuck_reason=block_reason`; `metacognitive_reason="exhausted: {r} retries, {n} re-decompositions, converging=..., sibling_rate=..."` | thesis-refuted | Closest real thesis-refuted: convergence + sibling-rate evidence exists but lives only in metacognitive_reason/event; stuck_reason is the raw error and status is plain "stuck" |
| src/loop_blocked.py:602-716 | Navigator escalate at blocked step | stuck, `"NAVIGATOR_ESCALATE: ..."`; NAVIGATOR_ACTED event | thesis-refuted (amb.) | Machine-initiated hand-to-human is orthogonal to the four (who-decides-next, not why-stopped); prefix distinct but status still "stuck" |
| src/loop_blocked.py:370-426 | Terminal blocked common exit | STATE_BLOCKED + `record_skill_outcome(success=False)` + `("normal", ..., "stuck", reason)` | not-a-stop | Cause-blind: stamps skill-failure attribution uniformly for every upstream cause including system failures — where conflation propagates into learning |
| src/loop_post_step.py:37-143 | Continuation vs escalation enqueue | task `source="loop_continuation"` / `"loop_escalation"` (depth ≥ 3) | not-a-stop | Depth cutoff is itself a preset cap; escalation reason text encodes out-of-budget exhaustion as prose only |
| src/handle_queue.py:78-106 | loop_continuation drain | (none — unjudged row possible) | not-a-stop | Continuations can end with no goal verdict at all — any stop verdict for the chain evaporates |
| src/handle_queue.py:28-76 | loop_escalation drain | notify "escalation surfaced" on surface | not-a-stop | clean (routing) |
| src/director.py:1112-1371 | handle_escalation decision | `EscalationDecision(action=continue/narrow/close/surface)` + `escalation-{job}-{action}.md` artifact | not-a-stop (close ≈ reachable-but-not-worth-it) | The ONLY place a not-worth-it-shaped judgment is made ("close: accept the partial result, no further work") — recorded as artifact file only, never stamped on any run/outcome row; the originating run stays "stuck" |
| src/handle.py:1775-1801, 2240-2259 | Director restart re-run / exhaustion | exhausted: channel "stuck" — "Director restart loop exhausted" | out-of-budget | Preset restart-depth cap (3) exhaustion delivered as generic "stuck"; says nothing about possibility |
| src/director.py:1399-1442 | Convergence-budget forced-continue | (no record — "MUST return continue") | not-a-stop | Silently converts an exhausted replan budget into continue; if the run later ends stuck, the budget exhaustion is absent from the label |
| src/closure_verify.py:925-937 | Behavioral-gap downgrade | `complete False`, `downgrade_reason`, "Downgraded to not-achieved — ..." | lost-the-plot (amb. quality-failure) | Nearest recorded lost-the-plot; but can't distinguish "coherently wrong assembly" from "merely buggy/incomplete work" |
| src/closure_verify.py:938-948 | Diagnosis-gap downgrade | same shape | lost-the-plot (amb.) | Same as above |
| src/closure_verify.py:964-982 | Inconclusive-only zero-passed | `complete=False`, confidence 0.6 | not-a-stop | Verifier uncertainty encoded as low-conf not-achieved → DIRECTIONAL trust; reasonable, but reads as a weak lost-the-plot rather than "couldn't tell" |
| src/closure_verify.py:991-1005 | Unjudged verdict | `goal_achieved=None`, source `closure_unverifiable` | not-a-stop | clean — distinctly recorded and EXCLUDED from learning |
| src/closure_verify.py:1047-1062 | Environment-noise confidence cap | confidence capped 0.69 | not-a-stop | clean — deliberate sub-threshold design |
| src/closure_verify.py:883-885, 1137-1139 | Verifier failure fail-open | `_emit_skip("verdict_parse_failed")` / treat-as-complete | not-a-stop | Verifier's own crash leaves run "done" unverified — an external-interrupt of the VERIFIER laundered into done-unverified (learnable) |
| src/handle.py:1863-1872, 1880-1902 | Closure restart gate + superseded stamp | superseded: `goal_achieved=False, source="closure"` | not-a-stop (stamp ≈ lost-the-plot) | clean-ish; superseded attempt gets an honest not-achieved stamp |
| src/handle.py:1903-1948 | Closure-restart stamp failure (fail-closed) | `status="incomplete"`, `goal_verdict_source="closure_stamp_failed"` | external-interrupt | Persistence failure recorded with distinct source (good) but status "incomplete" merges it into the "partial" success_class with real demotions |
| src/handle.py:2012-2078 | Agenda provenance guard | `status="incomplete"`, `stuck_reason "provenance: ..."`, `goal_achieved=False, source="provenance"` | lost-the-plot | Distinct source; fabricated-artifact detection is the sharpest "locally green, doesn't serve the ask" signal, still bucketed "partial" |
| src/handle.py:2091-2107 | Closure-contradicts-done demotion | done → `"incomplete"` (judged, conf ≥0.7, complete=False) | lost-the-plot | Status flip means these land in success_class "partial" (run_curation.py:67), NOT "done-not-achieved" — the taxonomy's lost-the-plot bucket is bypassed by its own demotion |
| src/handle.py:2261-2352 | Quality gate + escalate re-run | quality_gate_action; re-run at next tier | not-a-stop | clean (routing) |
| src/handle.py:2383-2436 | Post-escalate re-verify + demotion | same shape as 2091-2107 | lost-the-plot | Same bucket-bypass as 2091-2107 |
| src/handle.py:2240-2259 | Channel delivery of failure | channel "stuck" / "error" | not-a-stop | Collapses every non-done ending into two words at the human surface |
| src/handle.py:442-502 | _verify_now_outcome | "incomplete" + `goal_achieved=False`, source `provenance_missing` / judge false; fail-open unjudged | lost-the-plot (demotion paths) | Distinct sources exist; same "partial" flattening downstream; fail-open path keeps unjudged |
| src/handle.py:1104-1153 | NOW→AGENDA verdict escalation | OFF: HandleResult "incomplete" | not-a-stop | A not-achieved NOW ending recorded as bare "incomplete" when escalation is off — verdict context lost |
| src/handle.py:1206-1245 | Clarification stop | `HandleResult(status="clarification_needed")` + question | external-interrupt (weak fit) | Mostly clean — distinct status + question preserved; an awaiting-human hold, not a goal verdict; success_class "unknown" hole applies |
| src/handle.py:1280-1292 | Thin-mode passthrough | non-done status + warning note | not-a-stop | clean (passthrough) |
| src/handle.py:2691-2801 | Navigator act at dispatch | escalate: `status="stuck"`, `classification_reason="navigator_escalate"`; close: "done"/"incomplete" | thesis-refuted (amb.) | Has a machine-readable reason code (rare!); still "stuck" at status level; navigator "close" can mint a "done" with no run dir |
| src/handle_queue.py:119-192 | Dispatch recall guard | `status="error"`, `classification_reason="recall_guard"`, "refusing to re-run without a change of approach" | thesis-refuted (amb. out-of-budget) | Its own text admits the thesis ISN'T refuted ("without a change of approach") — approach-exhaustion under a preset 3/60min window, labeled "error" |
| src/handle_queue.py:280-307 | drain_task_store outcome | "error"→task_fail; else complete(result_status=...) | not-a-stop | A stuck continuation is completed-with-status — task_store view diverges from run view |
| src/loop_finalize.py:334-361 | Container clone merge failure | "done"→"partial", "container clone merge failed/errored — work preserved..." | external-interrupt | Landing machinery failed after goal work succeeded; "partial" merges it with mid-goal shortfalls |
| src/loop_finalize.py:366-384 | Worktree merge failure | "done"→"partial", "worktree merge failed — work preserved on {branch}" | external-interrupt | Same |
| src/loop_parallel.py:99-108 | Per-step worktree merge failure | step "blocked", `"worktree merge failed: {detail}"` | external-interrupt | System failure enters the blocked-step path → skill-failure attribution applies |
| src/loop_parallel.py:362-381 | Fan-out any-blocked aggregation | `_fanout_loop_status="stuck"` | not-a-stop | Aggregator collapses heterogeneous per-step causes into one "stuck" |
| src/loop_parallel.py:485, 497, 509, 630, 643, 659 | Parallel/DAG failures | stuck; "parallel execution error" / "fan-out timeout" / "missing from fan-out results" / "dag timeout" / "dag execution error" / "upstream dep did not complete" | external-interrupt (timeouts amb. out-of-budget) | Six distinct system/cap causes, one status; timeouts are preset caps sharing the label with execution errors |
| src/agent_loop.py:602-651 | Stuck auto-recovery one-shot | AUTO_RECOVERY event; one re-run | not-a-stop | clean (recovery machinery) |
| src/loop_finalize.py:554-586 | Unjudged outcome row | outcome row, no verdict; lessons deferred | not-a-stop | clean (recording) |
| src/loop_finalize.py:753-829 | Deferred learning drain | skip crystallization when `goal_achieved is False` | not-a-stop | Binary gate: a lost-the-plot False and a system-failure False skip identically |
| src/run_curation.py:171-213 | success_class taxonomy | "success"/"done-not-achieved"/"done-unverified"/"partial"/"failed"/"unknown" | not-a-stop | THE flattening point: `_FAIL_STATUSES` merges out-of-budget, thesis-refuted, and system failures into "failed"; interrupted/stranded/refused_busy/clarification_needed fall through to "unknown" |
| src/memory_ledger.py:48-69 | Outcome fields | `goal_achieved` tri-state; `goal_verdict_source` enum | not-a-stop | Schema has verdict-provenance but no stop-cause field — stuck_reason prose never reaches the ledger row as structure |
| src/memory_ledger.py:84-146 | verdict_trust | FULL/DIRECTIONAL/NEUTRAL/EXCLUDED | not-a-stop | clean for what it covers — but only rates verdict trust, never stop cause |
| src/audit_policy.py:38-127 | Audit quarantine | `audit_incomplete=True`, learning_allowed=False | not-a-stop | clean |
| src/outcome_policy.py:14-50 | Learnability | `_LEARNABLE_SUCCESS_CLASSES=("success","done-unverified")` | not-a-stop | Inherits every upstream conflation; notably admits landing-synthesis budget-partials via done-unverified |
| src/handle.py:648-727 | Terminal delivery | notify surface | not-a-stop | clean |
| src/agent_loop.py:761, src/handle.py:2872 | Exit codes | `0 if status=="done" else 1` | not-a-stop | Maximal collapse: all six verdict kinds → one bit |
| src/heartbeat.py:384-448 | Stranded-state sweep | DOING→TODO revert; notify "stranded_run" | external-interrupt | clean (crash aftermath, distinct notify) |
| src/heartbeat.py:520-594 | Stranded run-card backfill | `meta["status"]="stranded"` | external-interrupt | Distinct status (good); success_class "unknown" hole applies |
| src/heartbeat.py:1036-1068 | Backlog drain marking | done→DONE; refused_busy→TODO; other→STATE_BLOCKED | not-a-stop | "other→BLOCKED" folds interrupted/stuck/partial into one item state — retry semantics identical for all causes |
| src/orch.py:462-467 | Build-loop done-but-not-passed | "blocked" | lost-the-plot (amb. quality-failure) | Work locally done, validation says it doesn't serve — recorded with the same word as retry caps |
| src/orch.py:468-478 | Build-loop retry-streak stop | "blocked" | out-of-budget (amb. thesis-refuted) | Preset streak cap, same label as validation failure |
| src/orch.py:536-552 | Build-loop max-attempts stop | finalize_run "blocked" | out-of-budget | Same |
| src/build_loop_runner.py:203-368 | Runner statuses | "idle"/"busy"/"interrupted"/"ok" | not-a-stop | Separate vocabulary from LoopResult entirely; quality gate skipped, so nothing downstream re-classifies |

## Conflations

1. **"stuck" is one word for three verdicts.** Out-of-budget (loop_init.py:83, loop_execute.py:306/860/878/908, handle.py:2240-2259 restart-exhaustion), thesis-refuted (loop_blocked.py:971/1077, loop_execute.py:1054→1334), and external system failure (agent_loop.py:335, loop_blocked.py:304/317, loop_parallel.py:485-659) all stamp `status="stuck"`. run_curation.py:68 maps all of them to `success_class="failed"`, so learning, verdict windows, and escalation consumers cannot tell "goal impossible" (update the thesis) from "cap too low" (bump the budget) from "backend died" (learn nothing) — and loop_blocked.py:370-426 records skill-failure attribution for all three alike.
2. **"done" hides out-of-budget stops.** The landing synthesis (loop_execute.py:364-391) converts a max_iterations cap-hit into `status="done"` with the partial-ness recorded only in manifest/step text *[post-review: conditional — the branch replaces remaining steps with one synthesis step; status stays "done" (initialized at loop_execute.py:236) only when that step succeeds; a failed synthesis still lands stuck]*; if closure fails open (closure_verify.py:883-885/1137-1139) it becomes done-unverified — which outcome_policy.py:14 makes LEARNABLE. An out-of-budget stop can seed success lessons.
3. **"incomplete" merges lost-the-plot with infrastructure failure.** Closure demotions (handle.py:2091-2107, 2383-2436 — genuine "green pins, wrong string") share the status with stamp-persistence failure (handle.py:1903-1948) and NOW provenance/judge misses (handle.py:442-502); run_curation.py:67 buckets all as "partial". Worse, the demotion's status-flip means these runs bypass the taxonomy's own "done-not-achieved" class — the one bucket built for this verdict.
4. **"interrupted" merges human stops with preset caps — then falls into "unknown".** Kill switch (loop_init.py:197, loop_post_step.py:360) and wall-clock timeout (loop_post_step.py:374 — a preset cap) share the label; and "interrupted" appears in none of run_curation's status sets, so all of them get `success_class="unknown"` alongside stranded/refused_busy/clarification_needed — external-interrupt exists only as a taxonomy hole.
5. **"blocked" (build-loop + item state) merges validation failure with attempt caps.** orch.py:462 (done-but-not-passed, lost-the-plot-flavored) vs orch.py:468/536 (preset caps); heartbeat.py:1036-1068 folds every non-done non-busy backlog ending into STATE_BLOCKED with identical retry semantics.
6. **The only reachable-but-not-worth-it judgment leaves no verdict anywhere.** director.py:1112-1371 "close" ("accept the partial result, no further work") is a discovered value/cost call, but it's recorded solely as `escalation-{job}-close.md`; the originating run keeps its earlier "stuck" (out-of-budget) label, so run_curation/verdict_trust never learn the chain was deliberately closed rather than dead. *[post-review: "only" is overstated — dispatch navigator close (handle.py:2743-2747, budget-pressure-preferring per navigator_prompt.py:78-80) is a second close-shaped judgment and DOES record machine-readably (`classification_reason="navigator_close"`); but it fires pre-run (run prevented), so the point stands that no verdict ever reaches the outcome row of the run that actually hit the wall.]*

## Unrepresented verdicts

- **Thesis-refuted — no distinct recorded outcome; nearest approximation exists in prose.** loop_blocked.py:1077-1088 actually computes the structural evidence (`converging=`, `sibling_rate=`) and emits a METACOGNITIVE_DECISION event, but the run-level record is plain `stuck` with `stuck_reason=block_reason` (the raw error). No status, source, or class value anywhere means "avenues exhausted, nothing connects"; every candidate is step-local and shares its label with cap-hits.
- **Reachable-but-not-worth-it — does not exist as a recorded outcome at all.** The judgment is made in exactly one place *[post-review: two — dispatch navigator close (handle.py:2743-2747) also makes it, recording `classification_reason="navigator_close"` on a HandleResult before any run starts; no outcome-row representation either way]* — director.handle_escalation "close"/"narrow" (director.py:1112-1371) — and recorded only as an artifact markdown file plus an in-memory EscalationDecision. No LoopResult status, no metadata field, no goal_verdict_source, no success_class ever says "path found, discovered cost exceeds value." The mid-loop budget bump (loop_execute.py:318-359) makes the inverse worth-it call (and continues) without recording that either.
- **Out-of-budget — richly detected, never distinctly recorded.** Six-plus preset caps (daily spend, max_iterations, tokens, cost, runaway multiplier, restart/continuation depth, wall-clock) each name themselves in `stuck_reason`/reason prose, but the machine-readable layer collapses to "stuck" (or "interrupted" for wall-clock, or "done" post-synthesis). Recognizable today only by string-parsing stuck_reason.
- **Lost-the-plot — partially represented, conflated with plain quality failure.** The closure demotions (closure_verify.py:925-948; handle.py:2012-2078, 2091-2107, 2383-2436) and success_class "done-not-achieved" are real recorded outcomes in this territory, with distinguishing `goal_verdict_source`/`downgrade_reason`. But (a) nothing separates "coherently assembled, wrong rabbit" from "buggy/incomplete work"; (b) the demotion status-flip routes runs into "partial" instead of "done-not-achieved"; (c) the only mid-loop coherence signal (drift → _ae2 restart, loop_execute.py:1631-1646) is consumed by restart machinery and never recorded as a verdict.

## Wiring observations

- LoopResult (loop_types.py:174) already pairs `status` + `stuck_reason` at every terminal; a `stop_verdict` field would sit beside `stuck_reason`, written at the same break sites that write it today.
- `_BlockDecision` (loop_blocked.py) already carries `metacognitive_reason` — a structured stop-cause channel exists in-flight and is dropped at the LoopResult boundary.
- METACOGNITIVE_DECISION events (loop_blocked.py:1077-1088) already record the exact evidence a thesis-refuted verdict needs (converging, sibling_rate); the event stream has it, the outcome row does not.
- `Outcome.goal_verdict_source` (memory_ledger.py:48-69) is the existing enum-shaped provenance field; a stop-verdict field would be its natural sibling, flowing through the same stamp path verdict_trust already reads.
- run_curation.classify_outcome (run_curation.py:171-213) is the single choke point deriving success_class from `status`+`goal_achieved`; it is where a stop_verdict in metadata would change downstream classes without touching consumers. *[post-review: single choke point for success_class only — four learning/recall consumers read raw `status` directly and would keep the old conflations unless also reached: outcome_policy.is_learnable_outcome's no-success_class fallback (outcome_policy.py:44-50), recall's unjudged-attempt fallback `a.status != "done"` (recall.py:157-165), strategy_evaluator's `_STATUS_WEIGHTS.get(outcome.status)` (strategy_evaluator.py:62-67), attribution's raw-status failure filter (attribution.py:367-371). Stop-verdict wiring must land on the outcome row itself (memory_ledger sibling field), not only in curation metadata.]*
- HandleResult `classification_reason` ("navigator_escalate", "recall_guard") is already a machine-readable stop-reason slot — dispatch dead-ends are the only stop family with one today.
- Budget seams already emit distinct notify events (`point="budget_gate"`, loop_init.py:83-108) — out-of-budget is distinct at the notify layer and lost at the status layer.
- The run_curation status-set holes (interrupted/stranded/refused_busy/clarification_needed → "unknown") show external-interrupt is currently expressed as a taxonomy gap rather than a label — the statuses exist, the class doesn't.

---

# Post-review corrections & additions (chunk-9a adversarial review, 2026-07-23)

Three Codex lenses (Skeptic/Architect/Minimalist) reviewed this record;
every accepted finding below was re-verified by the master against source
before being applied. Bracketed *[post-review: …]* notes above mark the
in-place amendments; this section carries the additions.

## Taxonomy coverage — the four verdicts don't cover two stop families (all three lenses, independently)

The invocation contract says "classified against the four stop verdicts,"
but the table needed two working categories beyond them: **external-interrupt**
(kill switch, operator stop, backend death, merge-machinery failure —
~14 rows) and **not-a-stop** (routing/recovery/recording machinery —
~20 rows). That is itself a survey finding, stated explicitly now rather
than smuggled: *the four verdicts are goal-directed stop causes; they do
not cover infra/human interruption at all.* Whether external-interrupt
becomes a fifth verdict, or stays a status-level concern (`interrupted`/
`stranded` etc.) orthogonal to the verdict field, is an **open agenda
question for the artifacts conversation** — the wiring must not silently
force those rows into the four, nor silently mint a fifth.

## Missed seam family — director/worker path (Skeptic; verified)

The uncertain-list entry claiming run_director "may be reachable only from
tests/older callers" was wrong — the path is live (`maro director`,
cli.py:494; Telegram `/director|build|ops`, telegram_listener.py:347-350).
Its stop vocabulary, verified:

| Seam (file:line) | Mechanism | Current label | Nearest verdict | Conflation note |
|---|---|---|---|---|
| src/director.py:580-581 | run_director overall status | `"done" if all_done else "stuck"` | not-a-stop (aggregator) | Same collapse shape as the fan-out aggregator: heterogeneous worker causes → one "stuck" |
| src/workers.py:264-270 | Worker LLM call failed | WorkerResult `status="blocked"`, `stuck_reason="LLM call failed: …"` | external-interrupt | Backend failure recorded with the same label as honest worker flag_blocked |
| src/workers.py:287-291 | Worker self-reports blocked (`flag_blocked` tool) | `status="blocked"` + reason | thesis-refuted (amb.) | The one honest self-declared block; shares its label with infra failure above |
| src/workers.py:312 | No useful output | `status="blocked"` | not-a-stop (quality) | Third distinct cause, same label |
| src/director.py:558-566 | Review rounds exhausted | best-effort result **accepted**, run proceeds toward "done" | out-of-budget | A review-cap hit silently converts to acceptance — the DirectorResult never records that the cap, not the review, ended the loop |

DirectorResult/WorkerResult is a **separate status vocabulary** from
LoopResult (like build_loop_runner) — a stop-verdict field on LoopResult
alone would not reach it.

## Spot-verification evidence (Skeptic: outputs belong in the record, not the transcript)

The 11 master spot-checks, with what the cited source actually says:

1. loop_init.py:83-108 — budget gate returns `LoopResult(status="stuck", stuck_reason="daily budget exhausted: …")`. Confirmed.
2. loop_execute.py:364-391 — `_remaining_budget <= 2` branch replaces remaining steps with a synthesis step; `loop_status` initialized `"done"` at :236 and never re-stamped by this branch. Confirmed (conditional wording applied above).
3. closure_verify.py:991-1005 — unjudged verdict: `goal_achieved=None`, `goal_verdict_source="closure_unverifiable"`. Confirmed.
4. handle.py:2091-2107 — judged complete=False conf ≥0.7 flips status done→"incomplete". Confirmed.
5. run_curation.py:60-70 — status sets: done→success-family, `stuck/error/blocked`→"failed", else→"unknown"; "interrupted"/"stranded"/"refused_busy"/"clarification_needed" absent from all sets. Confirmed.
6. loop_blocked.py:1077-1088 — exhausted-options builds `metacognitive_reason="exhausted: …converging=…sibling_rate=…"`, emits METACOGNITIVE_DECISION; run-level stuck_reason stays the raw block_reason. Confirmed.
7. run_curation.py:171-213 classify_outcome — single derivation of success_class from status+goal_achieved with `else: "unknown"` fall-through. Confirmed.
8. Done-not-achieved bucket-bypass — classify_outcome requires status in _SUCCESS_STATUSES for "done-not-achieved"; the demotion flips status first, landing "partial". Confirmed.
9. outcome_policy.py:14 — `_LEARNABLE_SUCCESS_CLASSES = ("success", "done-unverified")`. Confirmed.
10. director.py:1325-1348 — escalation "close" writes `escalation-{job}-{action}.md` artifact + EscalationDecision; no outcome-row stamp (grep across the handler: no stamp call). Confirmed.
11. handle.py:2739/2794 — `classification = "navigator_escalate"` assigned to a variable, passed as classification_reason (the earlier literal-grep miss). Confirmed.

## Review verdict

PASS with fixes (no high-severity findings; 7 findings — 5 medium,
2 low — all verified real, all accepted at least in part). Record:
docs/history/2026-07-23-chunk9a-adversarial-review.md.
