# Purgatorio eye 7 — forward historian (findings)

**Date:** 2026-07-09. **Method:** per the charter, NOT re-deriving history — diffed the
compiled record (GOAL_BRAIN.md Decisions/Threads, BACKLOG.md, BACKLOG_DONE.md,
MILESTONES.md, docs/history/) against (a) raw commits back to the repo's first commit
(97473ac, 2026-03-05), (b) Jeremy's verbatim messages extracted from all 12 Claude Code
session transcripts on disk (Jul 1–9; older sessions rotated out), (c) dev-recall over
the re-ingested chat/docs/telegram lanes, (d) Claude Code auto-memory
(`~/.claude/projects/-home-clawd-claude/memory/`). Every quote below was read in its
surrounding session context, not just a query snippet. Cross-checked against
findings-{ops,data,archaeology,docs,code-security,landscape}.md and RECONCILIATION.md
to avoid re-reporting (arch-10 factory branch, arch-11 GOAL_BRAIN staleness, ops-07
build-loop litter, SF-1..SF-10 all left to their owners).

## Findings

| id | claim | evidence | subsystem | severity | status | disposition |
|---|---|---|---|---|---|---|
| hist-01 | **Jeremy's 2026-07-09 Hermes-swap decision + iMessage interface preference never entered the repo record.** Session 382a0d38 (18:34–18:41): "I've been thinking for a few months I should swap over" → "I do think I want to make the swap, but it might be when I get a new, more modern machine... **I'd love to be able to tie into iMessage instead of telegram.** And sadly, even though I've got poe access to yahoo email and an x account, it's never successfully used those outside of direct commands from me". The record still says the opposite/less: GOAL_BRAIN:212 "Hermes stance unchanged: steal-from-don't-migrate", MILESTONES #0 "Next: (d) Hermes adapter... SHELVED 2026-07-04 (Jeremy): revisit ~next week" — the revisit HAPPENED (hermes-maro-trial, same day, verdict positive; BACKLOG #18 + maro-import are its only repo traces) and produced a substrate-direction decision that lives only in Claude Code auto-memory (project_hermes_swap_plan.md). Downstream: the 1.0 item (a) escalation-channel design conversation (awaiting Jeremy) lists candidates (desktop/terminal notify, `maro inbox`, doctor-refusal) with no iMessage row, and every notify lane in the codebase is Telegram-hardwired. | ~/.claude/projects/-home-clawd-claude/382a0d38-...jsonl turns 4–5 (2026-07-09T18:34/18:41); GOAL_BRAIN.md:212, 1322-1326; MILESTONES.md:17; grep iMessage across GOAL_BRAIN/BACKLOG/MILESTONES = 0 hits | Platform / docs | real-but-deferrable (but shapes 1.0 item (a) + the substrate thread) | confirmed | goal-brain-correction (new Decisions entry + update Threads #0) + feed the (a) design conversation |
| hist-02 | **Jeremy's standing ToS/licensing worry about the `claude -p` lane is recorded nowhere, while 1.0 tells strangers to use that lane.** Session 382a0d38 (18:49): "there might be the possibility of a claude plugin or something to be the orchestrator that I'm interfacing with, but **I think that's against the license; I'm probably pushing it a little with -p usage and ssh access sadly**." Same worry is in the pre-repo workspace CLAUDE.md ("without Anthropic OAuth gray-area risk" — a founding question). The repo record has zero mention (grep license/ToS/terms/gray-area over GOAL_BRAIN, BACKLOG, MILESTONES, README, SECURITY_MODEL — only an unrelated skill-swipe "license review"), while README.md:16/77 recommends "the `claude` CLI (Claude Code OAuth — no key needed)" as a first-class backend for fresh installs. A compliance question the owner keeps voicing is invisible to the 1.0 gate. | 382a0d38 turn 6 (2026-07-09T18:49); ~/claude/CLAUDE.md "What's Next" §; README.md:16,77; grep results above | docs / Platform | real-but-deferrable (1.0-adjacent: recommending the lane to strangers is a different posture than using it yourself) | confirmed | backlog-item (ToS review of recommended backend lanes; Jeremy decision on 1.0 framing) |
| hist-03 | **The repo already shipped v0.1.0 and v0.2.0 and has a release go/no-go checklist — the 1.0 arc record references none of it.** Git tags v0.1.0 + v0.2.0 exist (539897c "v0.1.0 release notes" 2026-03; 7999e72 "release: v0.2.0 — **portable installs**, autonomous heartbeat, step-shape invariant" 2026-04-04); `docs/PUBLISH_CHECKLIST.md` (c30b709, 2026-03-10; still present, status dormant-design) gates release on exactly the classes the audit later found broken: "No secrets... no personal account data" (= SF-5 user/ shipping), "`maro-bootstrap install` runs on a clean workspace" (= the pip-never-worked finding), "CHANGELOG updated and version tag applied". MILESTONES -3, GOAL_BRAIN's 1.0 decisions, and BACKLOG's 1.0 sections cite neither the tags nor the checklist (grep = 0); the arc re-derived its gap list from scratch, and versioning is incoherent (git tags v0.2.0 vs docs/history/CHANGELOG.md at 1.19.0). No other eye covered this (grep publish/checklist/v0. over findings-* = 0). | `git tag`; 7999e72; 539897c; c30b709; docs/PUBLISH_CHECKLIST.md; grep PUBLISH_CHECKLIST GOAL_BRAIN/BACKLOG/MILESTONES/docs/INDEX.md = 0 | docs / ops | real-but-deferrable | confirmed | backlog-item (adopt-or-retire PUBLISH_CHECKLIST as part of the 1.0 gate; pick a version scheme before tagging 1.0) |
| hist-04 | **Raw commits supply the mechanism SF-9 (May data hole) asked for.** The data eye flagged May as unexplained (476 run dirs + 965 captains-log events, zero outcomes/step-costs/daily rows). The commit timeline: May has only 25 commits, dominated by the 2026-05-05/06 build-loop cluster (92bd659 dedicated runner, c0a50e7 CLI, 7c22df7 **cron wrapper**, fa445de "mark build-loop cron routing complete"), and 9496f11 explicitly exports `POE_ORCH_ROOT="$REPO_ROOT"` for every build-loop invocation — May's cron-driven activity wrote its state under **repo-local roots, not the canonical workspace**. This is the same split-brain class GOAL_BRAIN 2026-07-03 (BACKLOG #-1) later unified, one era earlier. Note the era also pre-dates the no-cron invariant (2026-06-10) — the invariant was a reaction to this, and the wiring is recorded in BACKLOG_DONE:1705-1720 but never connected to the recording hole. | `git log --since=2026-05-01 --until=2026-06-01` (25 commits); 9496f11 diff (POE_ORCH_ROOT pin in scripts/build-loop.sh + build_loop_runner.py); RECONCILIATION.md SF-9; BACKLOG_DONE.md:1705-1720 | data / ops | real-but-deferrable | mechanism confirmed (commits); causal link to SF-9's zero-rows plausible, not reproduced | goal-brain/SF-9 annotation — closes SF-9's "investigate the wiring break" with a named suspect; verify by checking repo-local memory/ for May-era rows |
| hist-05 | **The "run this prompt with this persona" reusable-pattern ask was captured as prose and dropped as work.** Jeremy 2026-07-08 01:07 (session 006a52c3, memory-direction conversation): "ideally we'd have a **pattern on 'run this prompt with this persona'** that could be applied from many different angles." GOAL_BRAIN:1041-1043 records the wish ("He also wants the bake-off run as a reusable... pattern (the docs brain-trust shape, generalized)") but no BACKLOG item, MILESTONES entry, or skill exists (grep brain-trust/persona-pattern over BACKLOG/MILESTONES/skills/ = 0; skills/ + maro_assets ship set contain nothing of this shape). Meanwhile the pattern keeps being re-improvised by hand (docs brain trust 07-04, bake-off dossiers 07-07, Purgatorio eyes 07-09) — three worked examples, zero crystallization, in a project whose thesis is crystallizing repeated patterns. | 006a52c3 turn (2026-07-08T01:07); GOAL_BRAIN.md:1041-1043; grep results | Quality/Self-improvement | real-but-deferrable | confirmed | backlog-item |
| hist-06 | **Jeremy's decree that adversarial-review ship as a 1.0 skill is not satisfied by the shipped set, and the (e) checkbox is already closed.** Jeremy 2026-07-09 21:09 (session e58c39de, Purgatorio design): "I really like that pattern... **That, or a flavor of it, should probably be one of our skills we ship with.** :)" PURGATORIO_AUDIT.md records it as "a named candidate for the 1.0 default skill set". But BACKLOG (e) is marked `[x] SHIPPED` with 10 skills and none is adversarial-review (`ls src/maro_assets/skills/`); the only vehicle is the code_review dogfood goal ((f) run 4, still running), and nothing marks adversarial-review as an (e) remainder — if that run's skill doesn't graduate, the decree falls through a closed checkbox. | e58c39de turn (2026-07-09T21:09); docs/PURGATORIO_AUDIT.md:115-117; BACKLOG.md "(e) Default personas + skills — SHIPPED" (~line 671); `ls src/maro_assets/skills/` (10 files, none adversarial) | Quality/Self-improvement / docs | real-but-deferrable (1.0 ship-set completeness) | confirmed | backlog-item (reopen an explicit (e) remainder: adversarial-review skill, gated on the code_review dogfood graduation or hand-built) |
| hist-07 | **The 2026-06-21/22 heartbeat-gate design conversation is absent from the repo record, and SF-1's supervision decision is being teed up without it.** Jeremy (session ebc90d15, 20:22–21:07): "worth using that qwen model locally for the heartbeat, and escalate if there's work to be done?" ... "Sounds like **we don't want heartbeat to run for openclaw and want to make our own**." The design direction (free local model answers "is there work?", escalate to paid only on yes; the real work is the context assembler) lives only in auto-memory (project_heartbeat_gate_design.md). Repo grep (heartbeat gate / local heartbeat) = 0 in BACKLOG/GOAL_BRAIN. SF-1's resolution shape ("pick ONE supervision story") should have this on the table as the owner's stated preference for what a heartbeat should even be. | ebc90d15 turns 68-76 (2026-06-21T20:22–21:07); ~/.claude/.../memory/project_heartbeat_gate_design.md; grep BACKLOG.md/GOAL_BRAIN.md = 0 | Platform / Quality-Self-improvement | real-but-deferrable | confirmed | feed SF-1 resolution + backlog-item |
| hist-08 | **The 2026-07-08 budget-posture decree has no GOAL_BRAIN Decisions entry** — it lives in a DEFAULTS.md table-cell parenthetical and auto-memory only. Jeremy 2026-07-08 20:02 (006a52c3): "I've got a $20/mo codex plan... and just went from $100/mo to $200/mo plan for anthropic. **That's more spend honestly than I want at the moment, so not looking to add to that path**... probably when we start looking at budget models and such for orchestration." This supersedes/extends the 2026-07-02 accept-contention decision that IS in GOAL_BRAIN; a session honoring the currency rule ("GOAL_BRAIN wins") could legitimately re-pitch an API key next month. | 006a52c3 turn (2026-07-08T20:02); docs/DEFAULTS.md:88 (only repo trace); GOAL_BRAIN Decisions 2026-07-02 model-lane entry (no 07-08 successor); auto-memory project_budget_posture.md | docs | cosmetic (but decree-class content in the wrong tier) | confirmed | goal-brain-correction (append dated budget decision) |
| hist-09 | **The failed-run-retry-with-prior-context ask is unlinked to any tracked item.** Jeremy 2026-07-04 15:35 (006a52c3, fence talk-through): "opus and I have talked about **task failures being retried, with the old task context available**, to get a better result the second or possibly even third time before ultimately failing. That may have been streamlined out at some point or just never implemented. I'm a little surprised the failure is so brittle though -- might be something to consider." Within-run retry exists (blocked-step hint+tier-up — the in-session answer), but the cross-RUN half maps only to the "re-attempt hinter" miner TODO (BACKLOG #0 / GOAL_BRAIN:167), which predates the ask and cites nothing; nothing in the record marks it as a Jeremy ask vs an idea. The done-vs-achieved corpus (~68 judged runs) and BACKLOG #18 both show failed runs currently get no second chance. | 006a52c3 turn (2026-07-04T15:35); BACKLOG.md:45 (re-attempt indexer mention inside #0); GOAL_BRAIN.md:167 | Core Loop / Memory | cosmetic (mechanism tracked; provenance lost) | confirmed | backlog-item annotation (stamp the ask + date onto the re-attempt hinter TODO so it's prioritized as an owner ask) |
| hist-10 | **CLAUDE.md's "repo not renamed on GitHub" reads as if the 2026-06-26 rename never happened.** Jeremy (dbbb5f5c, 2026-06-26T03:29): "**I've renamed openclaw-orchestration to maro-orchestration**" — and `git remote -v` = `slycrel/maro-orchestration`. The parenthetical presumably means "not renamed to bare `maro`", but as written it contradicts the URL beside it and the raw history. | CLAUDE.md:53; dbbb5f5c turn (2026-06-26T03:29); `git remote -v` | docs | cosmetic | confirmed | fixed-inline candidate (reword) |

## Promises ledger — open commitments found in raw history

Status: **OPEN-unrecorded** = promise exists only in chat/auto-memory; **OPEN-recorded** = tracked in the repo record; **CLOSED** = verified done.

| promise | source | status |
|---|---|---|
| Escalation-channel design conversation with Jeremy (1.0 item (a)) — now owes consideration of iMessage preference + "converse from my phone better... my own jarvis type system" (382a0d38 18:41/18:49) | MILESTONES -3 (a) | OPEN-recorded, but missing the hist-01/02 inputs |
| Hermes swap "when I get a new, more modern machine"; reusable setup preserved at `~/claude/hermes-maro-trial/` | 382a0d38 18:41 | OPEN-unrecorded (auto-memory only) → hist-01 |
| Hermes hardening on "a fully clean box... we might get into that later, too" | 382a0d38 /goal text 09:47 | OPEN-unrecorded |
| (g) portable-learning: 8 provisional decisions await Jeremy; (h) resilience: 9 provisional decisions await Jeremy | BACKLOG (g)/(h); docs/history/2026-07-09-decisions-for-jeremy.md | OPEN-recorded |
| Blocked-step escalate cutover: "re-verify in the future based on actual usage" (Jeremy 2026-07-03) | GOAL_BRAIN 2026-07-03; MILESTONES #2 | OPEN-recorded (standing re-verify) |
| Quality-gate local ladder: watch for rubber-stamping (all-PASS conf 1.0 rows) | GOAL_BRAIN 2026-07-03 (7); BACKLOG #9/#10 | OPEN-recorded |
| Local reasoning-model revisit "in 3-6 months we might have a chance at a meaningful local model even on this hardware" (2026-06-21, → ~Sep–Dec 2026) | ebc90d15 21:06; auto-memory project_local_validator_box_test | OPEN-unrecorded in repo (BACKLOG model-bake-off item exists but carries no revisit date) |
| API-key/OpenRouter lane deferred "when we start looking at budget models"; don't re-pitch | 006a52c3 2026-07-08T20:02 | OPEN-unrecorded in GOAL_BRAIN → hist-08 |
| Adversarial-review as a shipped 1.0 skill | e58c39de 2026-07-09T21:09 | OPEN, at risk (closed (e) checkbox) → hist-06 |
| "run this prompt with this persona" reusable pattern | 006a52c3 2026-07-08T01:07 | OPEN-unrecorded as work → hist-05 |
| Codex-side rc=1 payload check "deferred-pending-repro" (M5) | GOAL_BRAIN Threads M5 | OPEN-recorded |
| README Virgil-name header ask (2026-06-26) | dbbb5f5c 03:29 → README.md:3 | CLOSED (verified) |
| Status-bar memory counter ask (2026-07-08 17:51) | 006a52c3 → ~/.claude/statusline-command.sh (RSS + MemAvailable, cites the 14.3G OOM) | CLOSED (verified) |
| Observe-dashboard "needs revisited... more discussion at a later time" (2026-07-02 #7) | BACKLOG_DONE:1170/1181/1320 status updates + BACKLOG:575 forward item | CLOSED as recording (the discussion itself still pending — rides BACKLOG:575) |

## Clean checks (probed, no finding)

1. The 2026-07-02 nine-item disposition list (sessions 72b533f8/0c570234) was fully
   honored: #1 loop-split verified-then-shipped, #2/#3 backlogged, #4 fetch skill
   unified (MILESTONES 07-04), #5 corpus adjudicated, #6 ancestry documented+wired,
   #7 dashboard flipped done→needs-revisited, #8 standard-skills → 1.0 (e), #9
   polymarket kept out of Maro.
2. VibeThinker ask-chain fully recorded: BACKLOG:89 (think-strip overrun), :405, :411
   (model bake-off) — nothing dropped.
3. Cache-blind token alarms ask (2026-06-21 22:19) → cache-aware meter shipped, recorded.
4. Recursion decree, 1.0 scope expansion (e)–(h), Purgatorio decree, memory-direction
   decree: session verbatims match GOAL_BRAIN's quoted entries — the record's quotes
   are accurate, not paraphrase-drifted (checked word-for-word on four decrees).
5. "Capture step data for offline mocks" ask (2026-06-26 20:35) → harvest_corpus +
   record-mode, recorded with intent quote.
6. Extensibility/MTG-stack vision → BACKLOG:388 composable decision-point hooks, recorded.
7. Director-clarification ask → shipped (`check_goal_clarity`, BACKLOG_DONE), recorded.
8. Telegram-era (Feb–Apr) sampled asks (Hermes-vs-OpenClaw 2026-03-21, overnight v0
   pipeline runs, M-stages green light) all adjudicated in BACKLOG_DONE/ROADMAP_ARCHIVE.
9. garrytan persona concern (Opus brute-force cost) → resolved via (e) ship-set removal,
   recorded with rationale.
10. Jeremy's 2026-06-10 founding invariants (no-cron, installable harness, docs-are-
    best-guess, make-a-call) — verbatim in session ebc90d15 and quoted intact in
    GOAL_BRAIN Invariants.

## Notes for reconciliation

- hist-01 + hist-02 + the promises-ledger phone/jarvis rows should be delivered
  together as inputs to the 1.0 item (a) escalation-channel conversation — that
  conversation is the one place Jeremy is already expected.
- hist-03 pairs with SF-5/SF-6: the March checklist is a ready-made scaffold for the
  1.0 gate the audit is about to re-derive.
- hist-04 belongs to SF-9's owner (data eye) — it converts "investigate the wiring
  break" into "confirm/refute the POE_ORCH_ROOT pin routed May state repo-local".
- The auto-memory <-> repo-record boundary is itself the systemic gap this eye keeps
  hitting (hist-01, -07, -08, promises rows): decree-class statements made in Claude
  Code sessions reliably reach auto-memory and only sometimes reach GOAL_BRAIN. The
  end-of-chunk discipline updates GOAL_BRAIN for *work*, but conversations that end
  without a work chunk (the Hermes wrap-up, the budget aside) leave no repo trace.
  Worth one structural fix: a session-close rule that any Jeremy statement worth an
  auto-memory write is also worth a GOAL_BRAIN Decisions line.
