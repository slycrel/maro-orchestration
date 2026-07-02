# Milestones — Prioritized Work Queue

What to do next, in what order. Updated each session. Strategic phases live in ROADMAP.md; deferred ideas live in BACKLOG.md. This file is the bridge — the executable queue.

Last updated: 2026-06-24 (full reorg — collapsed ~628 lines of Done-log into docs/ROADMAP_ARCHIVE.md; file is now header + Active Queue + Dormant pointer. Queue content current as of 2026-06-21/23.)

Truth anchor: GOAL_BRAIN.md Threads. History: docs/ROADMAP_ARCHIVE.md.

---

## Active Queue

0. **Substrate trial (OpenClaw → Maro → Telegram)** — *the current arc (Jeremy, 2026-07-01: "get the project where we can trial it for real with hermes or openclaw").* Substrate contract SHIPPED + live-verified 2026-07-01: notify hook (`notify.command`, run_completed/escalation, payload = run_card), uniform result retrieval (`run_result` + `maro-runs status|result`), escalation delivery (navigator dispatch-escalate + director surface both ping Telegram), `maro-notify-telegram` target, `deploy/openclaw/maro-dispatch.sh` symlinked into OpenClaw's scripts. E2E proven: dispatch script → enqueue → drain → run → run_card → Jeremy's Telegram DM (verified success-class goal). Docs: `docs/SUBSTRATE_INTEGRATION.md`. **Unattended hardening SHIPPED 2026-07-01:** budget gates (`budget.per_run_usd` default cost_budget + `budget.daily_usd` cross-run gate via `metrics.spend_today()`, refusal emits escalation notify; box config set 2.0/10.0), phantom `Step -1` root-caused + fixed (parallel batch discarded NEXT.md indices — threaded through, done items marked, display numbers by position; BACKLOG #2), drain-once contract (`enqueue --drain` → `drain_task_store(job_ids=...)`: a dispatch runs exactly what it enqueued, stale queued tasks can't piggyback). Also: user-config test-isolation hole closed (`MARO_USER_DIR`). **Remaining:** (b) OpenClaw-side delegation instruction (tell Poe when to hand a goal to `scripts/maro-dispatch.sh`); (c) burn-in: ~15 realistic goals through the full path, adjudicate via run_cards (done≠achieved + cost per goal); (d) Hermes adapter — deferred until after the OpenClaw trial, contract makes it cheap.
1. **Per-decision-class cutover** — *code shipped default-off, refined to per-MOVE granularity 2026-06-12.* `navigator.act_dispatch` + `act_confidence_floor` (0.9) + `act_moves` (default `["escalate"]`): escalate earned cutover (defers to human, 6/6 divergences right), close is opt-in (asserts resolution without running; probe-only evidence). Guard keeps first word; `NAVIGATOR_ACTED` audit event; `python3 -m navigator_shadow --agreement` is the evidence table. **Enable decision (23 live rows, 14/14 execute incl. 5/5 organic, all acting-move divergences synthetic probes): escalate ENABLED LIVE 2026-06-21** (Jeremy's call) — `navigator.act_dispatch: true`, `act_moves: [escalate]` in `~/.maro/workspace/config.yml`. Reversible: flip `act_dispatch` off. **MECHANISM PROVEN end-to-end 2026-06-21:** first `NAVIGATOR_ACTED` row written via the real enqueue→drain→`handle_task` path — a "$50k wire transfer" goal drew escalate 0.98, status=stuck/`navigator_escalate`, **no run dir spawned** (the run was prevented, deferred to human). Wiring is live and correct. Remaining is *passive organic accrual* — escalate firing on Poe's own self-generated goals during normal operation (the validation run was a deliberate trigger, not organic). Then → revisit close cutover once it has non-synthetic evidence. Closure decision class stays shadow-only (no live closure callsite yet).
2. **Dumb-loop audit** — *static half done 2026-06-11; data half round 1 done 2026-06-21* (`docs/DUMB_LOOP_AUDIT.md`). Static: full decision-point inventory, navigator-move mapping, high-consequence priority order. Data round 1: dispatch boundary agreement table from 28 live `NAVIGATOR_DECIDED` rows — execute 14/14 agree, all 13 escalate/close divergences are correct navigator catches on synthetic/probe/impossible/dangerous goals (zero false-escalates on healthy work). **Bounded by coverage:** dispatch is the only live shadow point with data. **Round 2 instrumentation wired 2026-06-23:** `_handle_blocked_step` tree (agent_loop.py:3137–3366, the priority-1 point — step-2 pressure test quantified ~40 wasted runs there) now has a live navigator shadow tap (`navigator_shadow.shadow_blocked_step_live`, config-gated off via `navigator.shadow_blocked_step`); `--agreement` breaks down `by_point`. Heuristic→move map: retry=extend, redecompose/split=fork, stuck=close. **Next:** enable the gate for a batch of real runs that hit blocked steps → round-2 agreement table → adjudicate divergences by outcome.
3. **Thread-brain per-turn maintenance** — *decision-half shipped 2026-06-21.* `agent_loop._record_loop_decision()` appends the director's live mid-loop course-corrections (replan/adjust/escalate/restart on stuck / verify_failure / step_threshold) to the active thread's goal-brain Decisions section via `current_run_dir()`. The director is the live supervisor (Phase 64) and the single clean seam the navigator takes over when it goes per-turn — not the dumb pipeline, so no duplication. Bounded volume (fires on director triggers, not per-iteration). Never-raise; 5 tests (TestLoopDecisionSeam). **Still open:** (a) Compiled-truth half — append verified claims (e.g. ralph-verify passes) to Compiled truth, needs a volume-conscious source filter; (b) dispatch-navigator rationale — record the live dispatch decision (move/conf/why) into the spawned run's brain, needs the decision threaded into `handle()` run-dir creation (run dir doesn't exist at dispatch time).
4. **Async fork join + `wait`** — `fork` exists in the navigator schema; the runner has no join semantics. *Reconciled 2026-06-11 with NAVIGATOR_SCHEMA.md's recorded deferral ("until a real thread needs it; sync join in v1"): the navigator is shadow-only, so no thread can issue a fork yet — this is gated behind per-class cutover (#1), not ahead of it. Don't build join semantics for a move that can't fire.*
5. **Skill/playbook freshness layers** — only if staleness shows up there in practice (rules have it; skills have score + circuit breaker).

### Live observation tasks (from GOAL_BRAIN)

- **End-to-end standing-rule observation** — does the medium → long → standing-rule path actually fire in real runs post-M2? Needs production runtime, then check `standing_rules.jsonl`.
- **Recall guard thresholds** — guard thresholds are unmeasured; watch `RECALL_GUARD_TRIPPED` and revisit the made-call defaults.
- **Fan-out revisit policy** — when does the navigator go back to an abandoned/failed child? Judgment call; lands in the step-5 prompt and gets measured via `NAVIGATOR_DECIDED`.
- **When to pull full work-LLM output** — criteria for the "sometimes" in the 2026-06-10 visibility decision; deliberately unpinned until examples accumulate.

6. **Closure check unification** (Phase C leftover) — `director_evaluate(trigger="closure")` wraps `verify_goal_completion`; `ClosureVerdict` retired. Low-priority code hygiene. (hygiene, low priority)

---

## Dormant

See GOAL_BRAIN.md Threads → Dormant (Thread Architecture impl, Phase 65 constraint orchestration, Mage correspondence memory, backlogged repairs).

---

## Changelog pointer

Full session-by-session Done log archived in docs/ROADMAP_ARCHIVE.md (still ingested by dev-recall).
