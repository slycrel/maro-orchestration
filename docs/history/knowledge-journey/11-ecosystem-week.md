---
status: history
---

# Ecosystem week: audits, free validation, Hermes, going multi-box

*2026-07-15 – 2026-07-20*

57 commits from 3e47750 (07-16 09:06, "Two-box PoC doc + morning decisions" — container on, alerts to ops channel) to 6659bf5 (07-20 15:23, GOAL_BRAIN decision: Hermes propose-only lane). Representative commit 33e3af8 (07-18 01:07, introspection container provisioning). The week Maro stopped being one box: Hermes on a second $100 Mac Mini became the interface brain over a five-verb SSH gate, the validation ladder flipped to hosted-free, the delivery loop was decreed to BE the product, and the trust boundary for agent contributions moved from push to merge-to-main.

## Architecture as it was

At 33e3af8: single-repo Python, flat src/ of 153 modules, five documented subsystems (docs/ARCHITECTURE_OVERVIEW.md): Interface/Routing (handle.py — 2881 actual lines; the doc's "~2526" was stale — → intent.py → director.py → workers.py, NOW-vs-AGENDA with a fresh deterministic link-triage shortcut ahead of the LLM classifier, f9c9228), Core Loop (agent_loop.py — 807 lines post-decomposition; the doc's "~5400" was a pre-decomposition relic — seven-phase INIT→DECOMPOSE→PRE_FLIGHT→PARALLEL→PREPARE→EXECUTE→FINALIZE over a LoopContext), Memory/Knowledge, Quality/Self-improvement, Platform (llm.py, config.py, task_store.py). Governance in-repo: append-only GOAL_BRAIN.md Decisions (whole file 3565 lines @6659bf5; ~3.3k was the repo's own 07-17 self-description), docs/DEFAULTS.md census-tripwired by tests/test_defaults_doc.py under the standing pattern "ON when it only adds internal evidence; OFF when it self-modifies, acts outward, spends money, or persists," SF-13 session-close rule at CLAUDE.md:233 (standing since 07-09).

**Validation** was a five-rung ladder (docs/LOCAL_VALIDATOR.md): Tier 0 free deterministic → Tier 1 hosted-free (src/hosted_free.py; gemini-flash-lite-latest first — 14/14, 0.66s avg — 429-breaker spill to groq llama-3.1-8b-instant) → Tier 1b local qwen2.5-coder:3b as availability backup only → Tier 2 paid → Tier 3 paid 3-persona council. Decreed 07-16: hosted-free first ("slow + local seems better than a network API call fail"); a genuine hosted UNDECIDED escalates straight to paid — a weaker local model doesn't overrule a stronger model's uncertainty. Fresh installs keep hosted-free consent-gated OFF. Backend order on the box: Claude-subprocess first, openrouter/openai removed entirely (both credit/quota-dead 07-17), billing-dead backends circuit-break 15 min process-wide, cache-aware budget breaker (the $2.41-phantom vs $0.406-real fix).

**Two-box:** Hermes (Nous Research harness) on mini2 (2014 Mac Mini, Monterey) as interface brain — Telegram, own memory, codex-OAuth brain — connected over exactly one edge: dedicated ed25519 key into deploy/hermes/maro-ssh-gate.sh, forced-command verb allowlist ping/dispatch/status/result/list ("the Mini's brain is an LLM with shell access, so no login shell"). deploy/hermes/dispatch.py splits enqueue from drain (Hermes caps tool calls at 300s): job_id receipt in seconds, detached per-job worker drains, job_id→handle_id join persisted in output/hermes-dispatch/. Container executor ON box-wide (fresh default off); introspection-SHAPED goals get per-run read-only provisioning — intent.classify's introspects_self field → run-scoped ContextVar → container_exec.introspection_provision() mounting runs/ + maro source ro, all-or-nothing fail-closed, symlinked runs/ refused.

**Product surface** redefined as the delivery loop: one honest run-level completion message (verdict, findings, cost, viewer link), answer-first, two-tone (humans get prose; LLM consumers get goal+answer+deliverable data via notify-hermes.sh to mini2's inbox), deferred learning drained AFTER the run_completed notify (6126d38). Planner calls became pure-text contracts: ~55 adapter.complete sites pinned no_tools=True with purpose tags, 6 deliberate agentic sites marked, enforced by tests/test_no_tools_contract.py (BACKLOG_DONE #27). Public viewer at maro.feifdom.com behind Caddy + GitHub OAuth. By 6659bf5 the gate had a sixth verb, `land`: Hermes proposes on hermes/<topic> branches from a fetch-only https clone; docs-only auto-fast-forwards to main, code holds as a PR (deploy/hermes/PROPOSE_LANE.md).

## Discoveries & aha moments

### Free hosted beats local at any hardware level — the ladder flips (07-16)
Weeks of local small-LLM investment died in one bakeoff: VibeThinker Q4 hit the 60s timeout on 13/14 verdicts (vs the 15s breaker) while gemini-flash-lite-latest scored 14/14, all decisive, 0 unsafe false-passes, 0.66s avg — "M1 reference quality at 13x speed," free. Jeremy: "Kinda lame that the free tier of cloud stuff is better nearly at any local hardware level (even the M1 likely)." Local demoted to offline floor; don't invest further unprompted. Subtle epistemics rule same day: hosted UNDECIDED escalates to paid, never down to local.
Evidence: 8944e5d, fb0f6fd, 01bf218, 5c3a886; GOAL_BRAIN 2026-07-16 late-evening entries; docs/LOCAL_VALIDATOR.md @33e3af8; research/validator-bakeoff-linux-2026-07-16.json.

### The delivery loop IS the product (07-16/17)
After the first real Hermes-dispatched run, Jeremy: "we're still making the standard LLM mistake... I'm sitting here 'waiting' as an end user... It's like we missed the forest for the trees." Honest verdicts, provenance gates, run cards — and no way for the asker to hear the outcome in plain words where they asked. Standing test born: "does the end user hear the outcome, in plain words, where they asked for the work?" Completion is two-tone; the verifier's self-grade answers "did the machinery work?" — the wrong question, earning space only on failure ("user doesn't care that it worked (presumption is that it did...!)"). Deferred learning moved after the notify so a finished answer never waits ~90-120s behind lesson crystallization.
Evidence: 61ccac0, 5ef0d9c, 6126d38; GOAL_BRAIN 2026-07-17 delivery-loop + two-tone entries; deploy/hermes/notify-hermes.sh @33e3af8.

### Conversational compute, not research-paper compute — 23 min to 60 s (07-17)
Jeremy: "essentially I'd like to drop a link somewhere and ask 'is this worth my time?'... I'm looking for conversational compute, not research paper level compute I think." Maro answered a 2-minute human question with a 23-minute claims-matrix ritual. Three same-day escalating clarifications: (1) fast lane inside Maro; (2) Hermes one-shots NOW-shaped asks at the interface; (3) that posture is Hermes-side ONLY — Maro stays fully capable, never assumes upstream vetting. Built: deterministic link-triage ahead of the classifier + reply-aware URL pre-fetch + one opinionated no_tools read. Live before/after on the same ask: 23 min → 60 s.
Evidence: f9c9228 (commit body carries the measurement, run 08f214c3-zesty-ibis); GOAL_BRAIN 2026-07-17 three-entry decree chain; docs/CAPABILITIES.md @33e3af8.

### Planning calls are pure-text contracts — the planner must never hold tools (07-17/18)
The calm-echo run's wall clock was mostly self-inflicted overhead, worst case: a decompose call ran the subprocess adapter WITH tools, so it EXECUTED the goal instead of planning it — a ~4-minute rogue side-quest wrote a wrong FINAL_REPORT the size-ranked deliverable locator then preferred, making the Telegram message contradict the run's own verdict. Generalized as BACKLOG #27: every adapter.complete site classified, ~55 pinned no_tools=True + purpose, 6 intentionally-agentic sites marked, lint test makes it permanent. Tool access is per-callsite contract, not ambient default.
Evidence: efe0aa0, cae19a0, 33e3af8; GOAL_BRAIN 2026-07-17 calm-echo entry; BACKLOG_DONE #27; tests/test_no_tools_contract.py.

### Verify evidence, not narration (07-17)
zesty-ash got stuck on one wrong premise propagating three times: ralph verify saw only truncated narration and FAILed a step whose deliverable sat correctly in artifacts/; the blocked-step guard pattern-matched "not found" inside research narration and converted a retryable FAIL into terminal stuck; the goal verdict trusted the poisoned DEAD_ENDS entry over the artifact on disk. Fix: artifact-evidence note (fresh artifacts/ listing with size + excerpt) threaded through the entire validator ladder. The shadow-eval batch independently converged: 89 rows, 92.1% gemini-vs-paid agreement, all 4 false-passes were narration-vs-evidence — "provenance is the lever, not thresholds."
Evidence: 8457590; GOAL_BRAIN 2026-07-17 zesty-ash + calm-echo entries.

### Provision the container, don't route around it (07-18)
Container-on day one: brisk-saffron spent 2.8M tokens / 28 minutes exhaustively proving it couldn't see the host run records it was asked to diagnose. Tempting fixes were host-side routing (escape hatch) or blanket read-only mounts. Jeremy's decree took neither: "Install in the container only for the runs that need access." Containment stays default; introspection-SHAPED runs get per-run, all-or-nothing, fail-closed ro provisioning. Security posture and capability stopped being binary; the container became parametric per-run. Shipped same day WITH a 3-reviewer adversarial pass (1 High + 4 Medium caught pre-commit).
Evidence: a14f45a, 33e3af8; GOAL_BRAIN 2026-07-18; src/container_exec.py introspection_provision @33e3af8.

### The trust boundary is merge-to-main, not push (07-20)
A Hermes backlog commit stranded in an ephemeral /tmp shallow clone on mini2 — survived only because /tmp hadn't been reaped (recovered as 1d89191, authored clawd@Hermes-Mac-mini.local). Instead of giving Hermes credentials, the fix relocated the boundary: mini2 keeps ZERO GitHub credentials (https clone = fetch-only by construction), proposes via the new `land` verb; docs-only auto-fast-forwards, code holds as PR — "an autonomous agent must not modify the orchestration that governs it without a human in the loop." Pushing became harmless by construction; merging became the guarded act. "No new keys, no new listeners."
Evidence: 1d89191, 1da7781, c1cfc52, 6659bf5; deploy/hermes/PROPOSE_LANE.md ("Born from a real failure"); GOAL_BRAIN 2026-07-20.

### Topology is a runtime fact, not an architecture commitment (07-15/16)
Jeremy's 07-15 stance ("It shouldn't matter if the hermes box and this box eventually converge... it's just the working/active orchestrator") promoted portable learning from post-1.0 nice-to-have to THE enabling mechanism for multi-box. Within a day cross-box dispatch was live on two 2014 Mac Minis (~$100 for the second), trust edge = five SSH verbs; OpenClaw was shut down the same day, freeing its Telegram bot for Hermes. Unexpected: Hermes unprompted ENRICHED a terse ask into a cleaner goal before dispatching — the interface brain adds value, not just transport.
Evidence: 3e47750; docs/SESSION_PROTOCOL_DESIGN.md §1 @33e3af8; deploy/hermes/TWO_BOX_POC.md; auto-memory project_poe_openclaw.md.

### The bitter-lesson lens and records-weight — the system audits its own harness (07-17 – 07-20)
Jeremy: "the bitter lesson trumps about half of what we're trying to do already... harness engineering is hard" — a lens for the commissioned drift review: sort every mechanism by what survives model improvement vs what compensates for evaporating weaknesses. Same wrap, the records became a tracked concern ("heavier and heavier"), with deliberate sequencing: compress AFTER the drift review, because compressing first erases the drift evidence. The 07-20 compound-thinking review filed the operational twin: does the system choose the smallest useful shape of work, or turn a simple ask into an orchestration ritual?
Evidence: cbc9e82, 38e93ac, 1d89191; GOAL_BRAIN 2026-07-17 bitter-lesson + records-weight entries; BACKLOG.md Part 2 (added 2026-07-20).

## Pros vs today's architecture

- **Trust boundaries as tiny auditable verb sets, not policy docs** — the entire cross-box attack surface was five (later six) forced-command SSH verbs in one shell script, no-pty, no forwarding, dedicated key. The whole security model reads in one file. (deploy/hermes/maro-ssh-gate.sh @33e3af8)
- **Boring transport on purpose** — SSH over LAN, no new daemons/bus/webhooks; the propose lane reused the same rail. Every multi-box feature composed onto one existing edge. (docs/SESSION_PROTOCOL_DESIGN.md §3; PROPOSE_LANE.md)
- **A genuinely free validation floor with honest epistemics** — Tier-0 + hosted-free at 0.66s + local offline backup + latency breaker, with weaker-model-defers-to-stronger-uncertainty. Most validation calls cost zero without pretending free judges are authoritative. (docs/LOCAL_VALIDATOR.md; 01bf218, 5c3a886)
- **Decision-record discipline at full stride** — verbatim quotes, reversal chains, same-day amendments (conversational-compute amended twice in one day, all three readings preserved), staleness corrections logged as corrections. 542 lines of Decisions for 5 days; this archaeology exists because of it. (GOAL_BRAIN 07-16..20; CLAUDE.md:233 SF-13)
- **Defaults governance as code** — every key in docs/DEFAULTS.md with rationale, census-tripwired by test, one stated pattern. Box-level flips never leaked into fresh-install defaults all week. (docs/DEFAULTS.md; tests/test_defaults_doc.py)
- **Fast decree-to-shipped with review still in the loop** — introspection decree decided and shipped same-day WITH a 3-reviewer adversarial pass; the probe-modality fix was gated on a 58-verdict replay measurement before Jeremy blessed it. (GOAL_BRAIN 07-18; d8be7a3)

## Cons vs today's architecture

- **resolved-since** — X reply-thread capture missing across both live research runs, degrading the canonical link-triage use case. Fixed same day by the reply-aware direct-CLI X rung (ce8171a, BACKLOG #26), consumed by the NOW-lane pre-fetch (f9c9228).
- **resolved-since** — Hermes had no safe way to contribute work back; its first attempt stranded a commit in mini2's /tmp. Recovered as 1d89191; propose lane shipped 07-20 (1da7781); maro-box direct-land script followed (scope decree "PRs for Poe; maro box continues as before").
- **still-present** — GOAL_BRAIN at 3565 lines, append-only; Jeremy flagged the weight, and how an append-only Decisions section compacts without losing the reversal-chain property was unanswered. Distillation deliberately queued AFTER the drift review.
- **still-present** — The commissioned holistic drift review (cold-read the repo, verdict on drift vs north star, bitter-lesson sort, honest including "wrong continent") was specced but never executed in-era.
- **still-present** — Forced --lane dispatch skips the intent classifier, so forced runs can never receive the introspection grant — accepted known gap (33e3af8 commit body).
- **still-present** — Personal-data blobs (medication-era user/ files) remain reachable in PUBLIC repo history (99f5a67..358ad5d + two stale branches); the 07-12 rewrite was employer-token-scoped only. Resolved by informed ACCEPT 07-16, not by rewrite.
- **still-present** — Answer-first/two-tone delivery shapes derived from a single research run; Jeremy flagged the pattern-vs-example risk ("Hopefully this is identifying the right pattern, as opposed to dialing in this specific example"). Build/ops/failure-shaped runs unvalidated.
- **still-present** — Split-brain memory: Hermes conversational memory and Maro execution memory don't share learned context; parked as "maybe that's ok for now and like the network layer a phantom sidequest" (SESSION_PROTOCOL_DESIGN §1).
- **still-present** — ~120-160s of closure + curation still sits between a finished answer and the notify; closure-parallel quality gate and closure-through-hosted-free-ladder deliberately unbuilt.
- **still-present** — The hosted-free quality lane depends on free-tier catalogs that measurably churn (gemini 2.x went limit:0 for new users, 2.5 went 404 — within the model's own lifetime); only a slower local backup behind it.

## What we believed then

- **"Local small models are the free validation lane"** — dead by 07-16: gemini's free tier delivered reference quality at 13x speed while the best local option couldn't beat the 15s breaker. (8944e5d, 5c3a886)
- **"The 07-12 git-history rewrite covered the personal-data exposure"** — the 07-16 scan showed it was employer-token-scoped ONLY; medication-era blobs still public. Resolved by informed ACCEPT, not rewrite.
- **"Monterey is too old for the iMessage 2FA device path"** — declared DEAD: the Mini itself worked as a 2FA device; the iPhone 7 failed identically. Replacement theory: iMessage activation requires a real phone number.
- **"Adapter calls can safely default to tool-bearing"** — implicit until calm-echo, where a decompose with tools executed the goal, wrote a wrong report, and delivered it. Now: pure-text contract, lint-enforced. (BACKLOG_DONE #27)
- **"The run window can be reconstructed as now minus elapsed"** — provenance freshness did exactly this and falsely demoted a clean run when slow closure pushed "now" 8 minutes past loop end. Fixed with a recorded wall-clock anchor. (8393883)
- **"The verifier's self-grade is what the user wants to hear"** — the completion message led with machinery status. Self-grade now earns space only when the goal was NOT achieved.
- **"Internal correctness improvements are product progress"** — the week's central correction: a run with honest verdicts that leaves the user waiting with no answer in their channel is a product failure regardless of internal quality.
- **"The stuck advisor is off / needs building"** — the morning session had already shipped it (3d35ba0); the record-keeping caught the duplication instead of double-building.

## Lost good ideas

- **Full-Tailscale private topology** (instead of public DNS + Caddy SSO). Lost: "I really like the full tailscale stack, I just balk at needing custom setup at each point along the way"; tailnet retreat "documented, not planned." Worth reviving: yes, conditionally — if public surfaces multiply or SSO friction grows, the tailnet collapses the auth problem to membership.
- **Local-model validation investment** (VibeThinker reference, qwen sweeps). Lost: decreed parked after hosted-free won. Worth reviving: on a trigger — free-tier churn was MEASURED in-era; if hosted rug-pulls, local is the only zero-cost floor, and the bakeoff methodology (corpus + latency gate + unsafe-false-pass count) is built and reusable.
- **Shadow-eval agreement batches as a validation-quality sensor.** Lost: doubled validation spend, closed early after 89 rows / 92.1%. Worth reviving: yes — periodic (quarterly or post-model-swap) re-armed batches; its one run produced the era's sharpest validation insight, and the mechanism (validate.shadow_eval + validation_shadow --agreement) is still in the tree.
- **iMessage as the native phone channel for Hermes.** Lost: blocked by Apple activation (real-phone-number theory), not by choice. Worth reviving: maybe — a cheap SIM would test the final theory; "Telegram remains the fallback if it's awful" implies the ambition was real.
- **hist-05: "run this prompt with this persona" as a first-class reusable pattern owner.** Lost: fell through BOTH disposition channels; the r2 audit caught itself re-improvising the same multi-eye pattern by hand a 4th time while documenting the loss. Worth reviving: yes — it is the exact SF-13 failure class the record discipline exists to prevent. (docs/audit-2026-07-r2/findings-historian.md hist-r2-02)
- **Effort-estimate + consent as a session-protocol message type** ("give me some time to figure that out," spend UX in effort language). Lost: designed 07-15 (SESSION_PROTOCOL_DESIGN §4/§5, message type 1, marked "new") but the week's energy went to dispatch/delivery/propose lane. Worth reviving: yes — the delivery-loop decree fixed the OUTPUT half; the consent half is the unbuilt mirror image, and "dollar figures rot; effort-language doesn't" still holds.

## Sources

- git log 2026-07-16..2026-07-21 in /home/clawd/claude/maro-orchestration (57 commits); commit bodies read in full: 3e47750, 33e3af8, f9c9228, 1d89191, 1da7781
- GOAL_BRAIN.md @6659bf5 Decisions 2026-07-16..20 (read in full) + @33e3af8 for 07-15 boundary entries
- docs/ARCHITECTURE_OVERVIEW.md, docs/LOCAL_VALIDATOR.md, docs/DEFAULTS.md, docs/SESSION_PROTOCOL_DESIGN.md, docs/CAPABILITIES.md @33e3af8 (note: the overview's line-count figures for handle.py/agent_loop.py were stale; actuals verified via git show)
- deploy/hermes/TWO_BOX_POC.md @33e3af8; deploy/hermes/PROPOSE_LANE.md @6659bf5
- docs/audit-2026-07-r2/findings-historian.md @33e3af8; CLAUDE.md:233 @33e3af8
- BACKLOG_DONE.md, BACKLOG.md, docs/history/CHANGELOG.md, MILESTONES.md @6659bf5/@33e3af8
- auto-memory: project_poe_openclaw.md, project_hermes_git_egress.md
- Verification pass: all 24+ cited hashes, quotes, and numbers checked against primary sources; two stale doc-transcribed line counts corrected above.
