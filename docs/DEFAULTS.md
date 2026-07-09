---
status: living
---

# Config Defaults — what they are, why, and what flips them

Every config key the code reads, its hardcoded default, the reasoning behind
that default, and what changes if you flip it. Written for clean-room
discovery: someone standing up Maro for general orchestration use should be
able to read this file and know which switches exist, which are safe to
touch, and which encode a governance decision.

Resolution order everywhere: **env var > `~/.maro/workspace/config.yml` >
`~/.maro/config.yml` > the hardcoded default listed here** (see
`src/config.py`). The hardcoded default is what a brand-new install runs.

**The pattern behind the defaults** (worth internalizing before flipping
anything): *capability defaults ON when it only adds internal evidence or
quality; OFF when it self-modifies, acts outward, spends money, or persists
beyond the run.* Every exception below is dated and traced to a decision.

Census enforced by `tests/test_defaults_doc.py` — a key read in `src/` but
missing here fails the suite, so this table can't silently rot.

---

## Memory & knowledge

| key | default | why / flip effect |
|---|---|---|
| `memory.worker_slice` | `True` | **ON since 2026-07-08 (Jeremy's flip)** on the §7 A/B verdict — 16 runs pooled, every measure favored injection or tied (closure 8/8 vs 7/8, tokens-in −29% median); record: `history/2026-07-08-worker-slice-ab.md`. Workers get top-5 recalled lessons + parent goal-brain, capped 1200 chars (~300 tokens). Flip OFF → worker prompts revert byte-identical to pre-slice; lose measured closure/token gains. |
| `memory.consolidation_enabled` | `True` | Knowledge-web consolidation is internal hygiene (merge/decay of crystallized knowledge); no outward effect. Flip OFF → web grows unbounded, retrieval quality decays over weeks. |
| `memory.consolidation_interval_hours` | `CONSOLIDATION_INTERVAL_HOURS` (knowledge_web.py) | Tuning knob. Lower = fresher web, more LLM spend. |
| `knowledge.rule_staleness_days` | `30` | Lens rules older than this get flagged stale rather than trusted. Shorter = more re-verification churn; longer = stale rules steer decisions. |
| `recall.dispatch_guard` | `True` | Stops a failing goal from being re-dispatched in a tight loop (the Apr "stale mission shortcircuit" bug class). Flip OFF only when deliberately stress-testing retry behavior. |
| `recall.guard_attempts` / `recall.guard_window_minutes` | `3` / `60` | The guard's budget: 3 dispatches per hour per goal. Widen for legitimately retry-heavy workloads. |

## Governance & safety (decision-carrying — don't flip casually)

| key | default | why / flip effect |
|---|---|---|
| `workers.allow_main_push` | `False` | Workers may push work branches, never the default branch (Session-40 governance; enforced by `scripts/hooks/pre-push` + `MARO_WORKER_RUN` set at the spawn seam in llm.py; liveness tripwired in `tests/test_git_guard.py` after the hooksPath incident, 2026-07-08). Flip ON → every worker subprocess gets `MARO_ALLOW_MAIN_PUSH=1`; only sane on a throwaway repo. Per-goal override: export `MARO_ALLOW_MAIN_PUSH=1` under explicit goal authorization instead. |
| `constraints.allow_persistence_install` | `False` | Workers cannot install cron/systemd/launchd persistence ("good system citizen": no self-rearming loops; off switches stay off — the Apr rogue-heartbeat lesson). Flip ON only for a goal whose explicit deliverable is scheduled automation, and review the diff. |
| `heartbeat.autonomy` | `False` | The heartbeat may observe but not self-generate work. This box killed its always-on heartbeat in Apr 2026 after a 48h unattended token burn. Flip ON = the system wakes itself up and spends; requires a working budget gate and someone watching the first week. |
| `heartbeat.backlog_every` | `DEFAULT_BACKLOG_EVERY` (heartbeat.py) | How often an autonomy-enabled heartbeat looks at the backlog. Irrelevant while `heartbeat.autonomy` is off. |
| `evolver.auto_enqueue_signals` | `False` | The evolver proposes self-improvements; it does not enqueue them for execution by itself. Human (or explicit config) stays in the self-modification loop. Flip ON = closed-loop self-modification — the north star, but only after the verify→learn loop is trusted. |
| `validate.input_provenance` / `validate.output_provenance` / `validate.result_provenance` | `True` | Anti-hallucination arc: claims need positive evidence (FS-diff artifact checks, claim probes). Flip OFF → "done" reverts to trust-the-model; the 2026-04 fabrication incidents are why these exist. Cost: a little latency per validation. |
| `validate.write_fence_allow` | `[]` | Extra paths workers may write outside the bounded workspace. Empty = fence as designed (goal-declared paths still widen it per-run via `FENCE_EXTENDED`). Additions are standing holes — prefer goal-declared paths. |
| `budget.transparency_usd` | `2.0` | Runs costing more than this get surfaced explicitly in curation/reporting rather than folded into totals. Lower = noisier reports; higher = spend hides. |

## Navigator / dispatch-class rollout (staged cutover, 2026-06→07)

| key | default | why / flip effect |
|---|---|---|
| `navigator.shadow_dispatch` / `navigator.shadow_blocked_step` | `False` | Shadow mode: navigator decides but decisions are only logged, not executed. Ship-dark pattern — 23 live NAVIGATOR_DECIDED events, 14/14 execute agreement before any act flag moved. |
| `navigator.shadow_tiers` | `['cheap']` | Shadow runs on the cheap model tier; it produces no user-visible output, so paying for a big model buys nothing until decisions act. |
| `navigator.act_dispatch` | `True` | Navigator dispatch decisions *execute* (escalate-only via `navigator.act_moves`). **Default ON since 2026-07-08 (Jeremy's flip)** after live evidence on this box since 2026-06-21: 14/14 execute agreement, zero bad escalates — and escalate is safe-by-construction (defers to a human, cannot assert a wrong resolution). Cost of ON: one cheap-tier model call per autonomous dispatch (the decide call fires even with shadowing off). Fail-open by construction: synthesized idunno-chain escalates (chain exhausted — includes adapter outages) never act, so an unreachable navigator cannot stop the line. Flip to `false` for shadow-only decisions with zero act authority. |
| `navigator.act_blocked_step` | `False` | Same idea at the blocked-step recovery point. Stays OFF by default: evidence base is thinner (24 rows, recoverable-class n=5) and a wrong escalate here aborts a run mid-flight rather than pre-dispatch. This box opted in 2026-07-03 via workspace config; re-verify against organic usage before defaulting on. |
| `navigator.act_moves` | `['escalate']` | Per-move allowlist once act is on: escalate-only first because a wrong escalate wastes tokens, a wrong close buries work. Add `'close'` only after organic close-agreement evidence. |
| `navigator.act_confidence_floor` | `0.9` | Below this confidence the navigator defers to the legacy path even with act on. Lower = more navigator authority, more wrong-move risk. |
| `navigator.tiers` | `DEFAULT_TIERS` (navigator_prompt.py) | Model ladder for live navigator calls. |

## Lanes & execution

| key | default | why / flip effect |
|---|---|---|
| `now_lane.escalate_to_director` | `True` | NOW-lane goals that outgrow a single response escalate to the director instead of silently under-delivering. Flip OFF → NOW stays cheap but complex asks get shallow answers. |
| `closure_restart` | `True` | Status-integrity arc: demoted/failed closures restart rather than lingering "done". Flip OFF → done≠achieved drift returns. |
| `scope_generation` | `False` | Phase 65 constraint-orchestration MVE, **PAUSED by Jeremy 2026-04-23** — shipped dormant behind this flag. Flip ON = resume a paused design decision; read `CONSTRAINT_ORCHESTRATION_DESIGN.md` + review first. |
| `scope_ab_skip` | `False` | Companion A/B bypass for the above; only meaningful with `scope_generation` on. |
| `adaptive_execution` | `False` | Dormant design (`ADAPTIVE_EXECUTION_DESIGN.md`) — not started. The flag exists so the seam is visible. |
| `keep_artifacts` | `False` | Run artifacts are cleaned unless a run opts in. Flip ON for debugging; watch disk (this box: 156G shared with everything). |
| `planner.persona` | `None` | The framework orchestrates as the neutral Conductor; personas (e.g. Poe) are opt-in per the Maro rename decree — persona is presentation, not authority. |
| `environment` | `"dev"` | Environment tag stamped into runs/logs. |

## Platform & I/O

| key | default | why / flip effect |
|---|---|---|
| `model.backend_order` | `None` | `None` = built-in resolution order (on this box that lands MODEL_POWER on subprocess `claude -p`, which shares Jeremy's plan quota — accepted trade per budget posture 2026-07-08; API lane deferred to the budget-models phase). Set an ordered list to re-route. |
| `notify.command` | `""` | Empty = external notifications OFF. Notifications run an arbitrary shell command — outward side effect, so opt-in. Events always land in `events.jsonl` regardless. |
| `notify.events` | `DEFAULT_EVENTS` (notify.py) | Which captain's-log event types trigger the command once one is configured. |
| `notify.timeout_seconds` | `30` | Kill a hung notify command rather than wedge the loop. |
| `telegram.chat_id` / `telegram.chat_ids` | `None` | Unset = Telegram listener refuses to engage; doubles as the allowlist of chats it may answer. Never wildcard this — it's the auth boundary. |
| `captains_log.rotate_mb` / `captains_log.rotate_keep` | `5` / `1000` | Log rotation: size trigger + retained-event floor. |
| `record.enabled` | `True` | Byte-level LLM call capture to `<run-dir>/build/calls/` (secret-scrubbed). ON because recorded calls are replay fixtures and mining input — internal evidence, no outward effect. Off (`false` or `MARO_RECORD=0`) trades future replay/mining ability for disk. |
| `report.enabled` | `True` | Per-run HTML report + cross-run static index (run-visibility, 2026-07-08). ON: pure read-side rendering of data already captured — no model calls, no outward effect. `false` stops report generation only; capture is `record.enabled`. |
| `report.debug_snapshots` | `False` | Extra intermediate report snapshots for debugging the report generator itself. OFF: developer-only output, disk noise in every run dir when on. Env override `MARO_REPORT_DEBUG`. |
| `correspondence` | `None` | dev-recall (developer tooling) config block; `None` = defaults. Not part of runtime self-improvement — keep it that way. |
