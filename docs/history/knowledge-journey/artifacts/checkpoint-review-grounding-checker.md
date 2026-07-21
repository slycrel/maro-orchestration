---
status: record
---

# History-grounding check of the amended plan

*Clean two-agent review, 2026-07-21 (Fable), agent a3cb204d7ed1338b5. Verbatim final report; its verified corrections are folded into the plan's checkpoint-amendments header and KNOWLEDGE_JOURNEY.md. Load-bearing claims were independently re-verified before adoption (verify-before-fix).*

---

All reads and verifications complete. Final report.

---

# Check 1 — Unabsorbed edges (era-file items chunks 1–8 neither absorb nor disposition)

The plan dispositioned exactly the digest's candidate-revival list (KNOWLEDGE_JOURNEY.md final section). The era files contain more edges than the digest surfaced; those extras got **no** disposition. Tiered by how hard the history itself argues:

**A. In-scope for this arc; the era files argue loudly (deserve a plan line or explicit rejection):**
1. **Stage-5 provenance pointers** (09-decisions-threads.md:97): "worth reviving *urgently by its own argument*: every Stage-4/5 promotion without a provenance field is evidence permanently lost." Chunk 4's refight loop is the exact consumer of that provenance ("re-fight from the evidence that produced it") — silent omission in the very chunk that needs it.
2. **Side-channel C-findings → failure-pattern corpus** (12-swarm-review.md:62): era 12 names its own two loss candidates — the census (became chunk 8) and "folding the side-channel C-findings into `docs/CAPABILITIES.md`", which is nowhere in the plan. Phase 0.5's skill is the through-lines-as-checks, not corpus entries; era 12 explicitly warns "if a future reader finds these unshipped, this is the circling ledger's next row."
3. **Phase 49 decision gate / mode:thin adjudication** (02-research-steal.md:99,118): still-present con — `factory_minimal.py`/`factory_thin.py`/`mode:thin` ride at HEAD unadjudicated; "carrying an unadjudicated variant contradicts the era's own best insight." Chunk 1 is the cull chunk and doesn't touch it.
4. **SF-13 decrees as a `record_decision` writer** (03-phase-sprint.md:108): "piping SF-13 decree-class statements through record_decision() resurrects the searchable journal for ~zero new design." Chunk 3 wires executor + closure writers only; the decree-capture writer is undispositioned.
5. **Signal-source rotation, runtime half** (05-adaptive-optional.md:94): the digest claims chunk 5 "independently reinvented" it, but chunk 5 rotates *review* evidence channels; the era's version is a *navigator move at stuck steps* (screenshots → logs → git-diff). That half is unabsorbed.
6. **Exception-vs-break distinction** (04-session-sprint.md:92): chunk 4's adjudication is binary (contradicted or not); the justified-exception branch — "today's system accumulates rules with no principled path for distinguishing the two" — is undispositioned.
7. **Hosted-free churn hedge** (11-ecosystem-week.md:79,95): era 11's still-present con — free-tier catalogs measurably churn, local was the only zero-cost floor behind them. Chunk 1 removes that floor. Covered by decree 2, but the plan silently drops the era's named revival trigger (hosted rug-pull; bakeoff methodology reusable). One sentence in chunk 1 would make the drop deliberate.

**B. Adjacent, partially absorbed — the dropped remainder is silent:**
- REASSESS_DRIFT_GUARD: chunk 8 takes one question; era 06 (:87) says revive the full 7-question overlay "as-is." 
- Recurring md-claims grounding census (06:84): chunk 8 borrows it as *spec* for wiring checks but schedules no recurring doc-grounding % census.
- `productive_persistence.md` untagged action queue (06:70) — still-present con, untouched.
- Promotion-cycle yield ~zero (03:88) — chunk 4 fixes the demote half only; observe/promote-side starvation undispositioned.
- Periodic hand-adjudicated burn-in ritual (08:87) — Phase 0.5 borrows the method once, doesn't institutionalize it.
- Consumer-first applied to chunk 6: the novelty-term scoring change names no readout/consumer (chunk 7's readout doesn't tabulate it) — the rule the checkpoint added for chunk 3 isn't applied to chunk 6.

**C. Out-of-arc silent drops** (fine to batch-BACKLOG or reject in chunk 1 alongside the four existing BACKLOG adds): Loop-Sheriff one-pager, degrade-don't-idle, metric-thresholded promotion gates (era 00); UX timing SLA numbers, per-phase cost models (01); SOURCES.md landed-where log, runtime-awareness/tool-latency injection, research-doc-with-parallels format (02); bi-temporal fields, queryable git, @lat backlinks, PASS/FAIL-gate template, `await:<kind>` (03); constraint-level outcomes, human-gate-at-constraint-altitude, eval train/test holdout (04); parallel watcher, elephant invariant, agenda-reframing/propose-better-goal, cross-run convergence cap, blocking ask() (05); per-claim ratings format (06); GOAL_BRAIN compaction (07 — queued post-drift-review elsewhere, OK); semver CHANGELOG, rung-ladder DoD form (08); fastembed/sqlite-vec gate re-check (09); session-reuse cost A/B, verifier-synthesis resumption, enrichment consumption, Fable-handoff discipline (10); drift review specced-never-executed, closure-latency-before-notify (11).

# Check 2 — Factual anchor rot

Zero drift. HEAD (690e34b) has no code commits since the plan's 07-21 amendment; every cited target verified by direct read:

| Cited target | Status |
|---|---|
| handle.py:905 `build_adapter(model=model or MODEL_CHEAP)` | still-accurate (exact line) |
| handle.py:889-901 scope-lift block | still-accurate (comment at 889 → `pass` at 901) |
| loop_init.py:236-241 `assign_model_by_role("worker")` | still-accurate |
| playbook.py:109-135 `inject_playbook` head-window | still-accurate (def 109, return 135) |
| knowledge_web.py:790+ consolidation seam | still-accurate (dream-cycle block ~789; `maybe_consolidate` at 827) |
| knowledge_lens.py:373 `contradict_pattern` | still-accurate (def exactly 373; zero src callers confirmed — memory.py:95 re-export only) |
| skill_lifecycle.py:684-694 refight wiring | still-accurate (import 684, `refight_rule` call 694) |
| quality_gate.py:301-329 local rung | still-accurate (`if _ladder:` 301 → fallback log 329) |
| quality_gate.py:117-182 council | still-accurate (`run_llm_council` def at 117) |
| quality_gate.py:388-398 WEAK_ESCALATE | still-accurate |
| conductor.py:131-191 `classify_step_model` | still-accurate (`_CHEAP_STEP_KEYWORDS` 131; def 159; ends ~191) |
| loop_execute.py:94-106 | still-accurate |
| loop_parallel.py:403-407, 570-574 | still-accurate (both sites) |
| loop_post_step.py:757-778 verify-fail ladder | still-accurate |
| memory.py:193-209 `_REFLECT_SYSTEM` | still-accurate |
| cross_ref.py:6-8 fresh-context design | still-accurate (statement at 7-8; 6 blank) |
| step_exec.py:1260-1266 inject_steps parse | still-accurate |
| closure_verify.py:648-650 proxy-interpretation | still-accurate |

Supporting premises also verified: loop_post_step.py:910-924 (100-char compression), knowledge_lens.py:882 (`inject_decisions`; loop-slice caller recall.py:643-644), local_models.py:543-549 (`auto_verify_enabled`), agent_loop.py = 807 lines, `record_decision` zero production callers, dead `model.default_tier/planning_tier/advisor_tier` keys at ~/.maro/config.yml:9-11 (src only uses autonomy's unrelated `default_tier`), workspace `validate.runtime/local_models/ollama_keep_alive` at config.yml:41-46, `RECALL_PERFORMED.lessons_cited` (recall.py:700-701), session-17 dedup guard (playbook.py:182), `bootstrap_context` CLI-only (cli.py:427 sole caller), `_tabulate_agreement` (navigator_shadow.py:484), hosted_free.py / test_defaults_doc.py / PORTABLE_LEARNING_DESIGN §"How contestation resolves" (:234) all present.

Two minor imprecisions, not rot: (a) chunk 1.4 cites ARCHITECTURE_OVERVIEW.md:123,132 for the agent_loop "~5400" claim — :132 is that claim; :123 is a *different* stale count (handle.py "~2526", actual 2881) — fix both when executing. (b) chunk 2.1 "~28 duplicate lines (21-60)": range right, count is 40 "Be more concise" lines. UNVERIFIED (not re-checked, low risk): "council/cross_ref zero organic uses" premises.

# Check 3 — Doc-currency collisions

1. **Landing policy**: plan line 31 "document → commit → **push**" predates the 2026-07-20 decree (GOAL_BRAIN.md:3566-3580; CLAUDE.md end-of-chunk now reads Document → Commit → **Land via `bash scripts/land.sh`**). Not a behavioral conflict (land.sh pushes over SSH) but the plan should say "land," matching the blessed path and the dead-gh-token context.
2. **Chunk 1.5 is already satisfied / the wrong target**: "GOAL_BRAIN Decisions lines for the five decisions above" — the five decrees were recorded 2026-07-20 (GOAL_BRAIN.md:3581-3599). What is **missing** from GOAL_BRAIN is the 2026-07-21 checkpoint layer: the confirmed chunk deltas, the revival dispositions (IN/BACKLOG), and Phase 0.5's adoption. Per SF-13 those are decree-class and have no Decisions line (the file ends at :3609 with the 07-20 commission entry). Chunk 1.5 should be repointed at the 07-21 decisions.
3. Minor wording conflation, chunk 2.3: "riding the existing consolidation 'dream cycle' seam (`maybe_consolidate`) **at evolver cadence**" — the dream cycle is the in-process 24h-marker lifecycle rider (knowledge_web.py:789-800); evolver cadence is a different hook (the verify_applied_suggestions rail). Pick one seam when implementing.
4. No conflicts found with: garrytan decree (chunk 1.3 explicitly honors), personas-stay, retention decree (chunk 2 archives the original), effort-language decree (chunk 7 framing), recursion decree (chunk 3.3 keeps doors open, BACKLOG), consumer-first, no-daemons, DEFAULTS census.

# Check 4 — Sequencing sanity

Order is largely history-consistent: Phase 0.5 first is decide-by-benchmark applied to itself; chunk 1 → 5 dependency is explicit (local rung removed in 1, hosted-free replaces in 5, interim byte-identical-to-paid is safe); chunks 3–4 before chunk 8 matches the instances-before-census lesson; probe-env hardening (B3) already shipped 2026-07-12, so chunk 4 rides a hardened verdict stream. Two flags:

1. **Chunk 4's candidate emitter must consume verdicts through `verdict_trust()`** — the era-10 hard lesson ("no learning consumer may read a verdict except through a single trust gate"; closure_unverifiable/env-capped excluded). The amendment covers tri-state semantics at *adjudication* but the *emitter* trigger ("closure verdict is complete=False") is stated raw. Unstated, chunk 4 is positioned to become the next "unverified verifier teaches its own bugs as facts" instance — the exact through-line the arc exists to close.
2. **Chunk 6 ships a scoring change with no named consumer/readout** (chunk 7's readout doesn't tabulate the novelty term; decay is disposal, not measurement). The checkpoint's consumer-first rule, applied consistently, wants at least a liveness/effect check in-chunk — otherwise it's a half-closed loop shipped by the plan that names half-closed loops as the disease.
