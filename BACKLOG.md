# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Last reviewed: 2026-07-27 (archiving pass, Jeremy's ask — "consider
archiving on the backlog, I think it's getting pretty big"). The
accumulated triage-pass history (eighteenth through twenty-fifth passes,
incl. the VERIFY_LEARN V2–V5 narrative) and all fully-closed entries
moved to BACKLOG_DONE.md "Archived from BACKLOG 2026-07-27" with context
intact: R2/R3/R4/R5 reviews (all residuals closed), R6 (E watch-item
retained below), C4-BOX burn-in (flip stub retained), -1 Purgatorio r2
graduates, #22 shipped trail (residuals retained), DONE pointer stubs,
install-trial residuals (watch-item retained), launch-content arc (open
remainders retained), and the 2026-07-04 stale-drop record. Previous
full triage: 2026-07-04.

---

## Actionable Stack

Ordered open work that matters. Top of the list is next.

### SP. Session-protocol arc — two-box Hermes dispatch, interactive goals, effort UX (OPENED 2026-07-15, Jeremy)

The umbrella for the next big lane; full skeleton + stance decrees in
**`docs/SESSION_PROTOCOL_DESIGN.md`** (iterate there; decree record in
GOAL_BRAIN Decisions 2026-07-15). New box arrives 2026-07-16 → Hermes on it as
a real end-user interface dispatching to Maro here. Staged: prove ssh/tailscale
dispatch slice first (enqueue from box B, run_card back), then effort+consent →
live progress query → next-pending-step injection (seam refactor first) →
clarification loop. Pre-box actionable now: **§6 seam inventory** (where
step-context is assembled; what a typed, provenance-stamped injection input
needs). **DONE 2026-07-15 → design doc §6a** (verdict: qualified one-seam;
work list = typed contributions, parallel fan-out gap, context-only interrupt
intent). Side find, fixed same day: verify/threshold director-escalate replies
were clobbered by the carry-forward consume before reaching the next step
(`loop_execute.py`, pinned by `test_adaptive_escalate_reply_reaches_next_step`).
**Seam refactor v1 SHIPPED 2026-07-15** (see BACKLOG_DONE for the full
adversarial-review record): typed `ContextContribution` ledger, `maro
interrupt --intent note`, parallel-batch threading. Remaining §6a gaps
(run-scoped `ancestry_context_extra` shapes, resume step-text mutation,
worker lane) stay open in the design doc; `director_evaluate
(trigger="injection")` decided + ENABLED on this box 2026-07-16
(DEFAULTS.md row; the "evening decision session" framing here had gone
stale — corrected 2026-07-18).
Parallel, box-independent: tier-up test goals (flagship: the 5–6yr
Telegram trading-channel corpus → backtested strategy, research-only —
CAPABILITIES.md Tier 5). Related standing items it touches: escalation channel
(the substrate go-between decree IS msg-4's foundation), portable
learning (promoted: the data layer that makes "active orchestrator" a runtime
fact), container-on posture for network-sourced goals (revisit at Hermes
go-live).

Preflight/observability findings (2026-07-16 evening, hermes X-thread dispatch
task-…b414ccab / run cobalt-pine — five-link failure chain, four fixed live):
- [x] **BLE imperative heuristic false positive** — bare `next\s` matched the
  noun phrase "exact safe next action" and dragged an outcome-shaped goal
  through the rewriter. FIXED: `next` now only counts clause-initially;
  pins `test_next_action_noun_not_imperative` / `test_clause_initial_next_is_imperative`.
- [x] **Rewriter dropped the goal's URL** — cheap-model rewriter went
  off-script (role confusion: "I cannot browse the URL"), its embedded JSON
  replaced the URL with "the referenced thread"; rule 3 (preserve constraints)
  is advisory-only. FIXED: enforced URL-preservation guard
  (`_rewrite_loses_referent`) rejects any rewrite that loses a URL; pins
  `test_rewrite_dropping_url_rejected` / `_preserving_url_accepted`.
- [x] **Clarity check ran on the rewritten goal** — system-introduced
  ambiguity became a clarification question back at the user for info they
  had provided. FIXED: clarity now runs on the goal as submitted, before the
  rewrite; pin `test_clarity_judges_goal_as_submitted_and_question_is_persisted`.
- [x] **Clarification question never persisted** — it lived only in the
  ephemeral HandleResult; run metadata/card and the hermes dispatch record
  all showed a bare `clarification_needed`. FIXED: question stamped into run
  metadata → carried on the run card (`clarification_question`), and
  dispatch.py worker now records `result_excerpt` from any preflight-
  terminated result; `status` verb surfaces both plus `goal_verdict_gaps`
  (new: closure gaps ride the card so the not-achieved "why" survives the
  300-char summary truncation — merry-nettle showed goal_achieved=false
  beside a summary whose visible prefix read "Goal achieved.").
  Tests: `tests/test_hermes_dispatch.py` (new).
- [x] **Task-store status vs dispatch status disagree** — SHIPPED 2026-07-16
  (same evening, parallel-fork pass): queue "done" = drained stays, annotated
  with `result_status` carrying the handle-result outcome; handle-level
  `error` results route to the existing `fail()` path with the result text.
  Both call sites (dispatch worker + `drain_task_store`) share the semantics;
  enqueue's 500-char display goal now marks truncation with `…`. Pins in
  `test_task_store` / `test_hermes_dispatch`.
- [x] **subprocess adapter ignores max_tokens** — SHIPPED 2026-07-16 (same
  evening, parallel-fork pass). Root cause: accepted-but-never-passed (the
  `-p` flag set has no cap flag). Utility (`no_tools`) calls now set
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (live-probed: overrun = hard CLI error, not
  truncation — callers all fall back safely, which is the right direction for
  JSON-contract calls). **Correction 2026-07-17 (live, dapper-heron answer
  synthesis):** on this CLI version the env cap acts PER API MESSAGE
  (thinking tokens included) and the CLI auto-continues past it — the `-p`
  result is then only the LAST chunk (1005 tokens out vs a 350 cap; content
  began mid-sentence). Not a hard error, worse than truncation: it
  decapitates. Treat the env cap as a warning-seam companion, not an
  enforcer — callers needing shape safety must check
  `output_tokens > requested` and fall back (run_curation._llm_answer does;
  pin in test_run_curation). Agentic calls deliberately uncapped. Every call
  record now carries `max_tokens_requested`; the FailoverAdapter seam warns
  on utility overrun for backends with no enforcement (codex CLI
  accepts-and-ignores, no cap flag exists). Anthropic SDK and
  OpenAI-compat adapters verified honoring. Pins in `test_llm` /
  `test_record_mode`.
- [x] **Closure prose can contradict its own flag** — SHIPPED 2026-07-16
  (same evening, parallel-fork pass): `_verdict_first_summary()` in
  closure_verify makes the FLAG write the summary opener deterministically
  ("Achieved: " / "Not achieved: " / "Not judged (…): "), stripping a
  leading LLM verdict restatement so nothing doubles; downgrade-branch
  summaries (already code-written verdict-first) pass through; fail-open
  skip sentinels untouched. Prompt also asks for verdict-first prose
  (secondary). Pins in `test_director` incl. the merry-nettle
  contradiction case.
- [x] **Container executor walls off introspection goals — SHIPPED
  2026-07-18 (same day as the decree)** — brisk-saffron (task-…80466244)
  spent 2.8M tokens / 28min proving only its own isolation. **Jeremy's call
  (GOAL_BRAIN Decisions 2026-07-18): "Install in the container only for the
  runs that need access."** Shipped shape: `intent.classify()` gained an
  `introspects_self` schema field (4-tuple return; fails open False on the
  heuristic path — isolation is the safe default) → handle threads it as
  `introspection_access` into `run_agent_loop` → run-scoped ContextVar in
  container_exec (finding-D reset pattern) → `introspection_provision()`
  adds ro mounts (workspace `runs/` + maro source dir + PYTHONPATH +
  MARO_INTROSPECTION markers) to the container branch in llm.py. Gated by
  `executor.introspection_access` (docs/DEFAULTS.md row; default on, inert
  unless containers are on); provision output still passes build_mount_map's
  forbidden filter (workspace root/memory/config/secrets stay out); fails
  CLOSED to blind isolation. Design doc §4 amended. In-container maro CLI is
  best-effort (stdlib-only modules — image has no maro deps); the records
  mount is the real guarantee. Tests: TestIntrospectionProvision
  (test_container_exec.py), TestIntrospectsSelf (test_intent.py),
  agent_loop threading pins. Adversarial review (Codex ×3) same day:
  confirmed-and-fixed = conductor dropped the grant before run_agent_loop;
  pipeline/team/direct handle paths didn't thread it; provisioning failed
  partially OPEN (source-only mount + MARO_INTROSPECTION=1 with no records
  — now all-or-nothing None); symlinked runs/ could ride the grant past the
  descendant allowance (now refused). Accepted known gap: `--lane agenda`
  forces past the classifier → no grant (comment at the force_lane branch).
- [x] **Per-loop restart alert reads as terminal failure** — SHIPPED
  2026-07-17 (same night): loop_finalize's per-loop "Mission complete"
  telegram block (terminal language + per-loop totals for a NON-terminal
  event) replaced with a restart-only "🔄 Replanning … the run continues"
  ping. Completion is announced once, at run level, by
  notify.emit(run_completed) → notify_telegram with the curated card.
- [x] **Hermes never notified on run completion** — SHIPPED 2026-07-17
  (same night): SESSION_PROTOCOL §3 push leg wired for real.
  notify.command → `deploy/hermes/notify-hermes.sh` (two legs: rich
  ops-channel message via notify_telegram + ssh push of the job_id-enriched
  card to mini2 `~/bin/maro-inbox.sh` → `~/.hermes/inbox/maro/`); for
  dispatched-run completions + escalation-class events the inbox script
  also DMs Jeremy deterministically (fields quoted, no brain turn).
  notify_telegram rewritten user-grade: plain-language outcome header,
  verdict, gaps, findings excerpt (goal-echo stripped), cost+duration,
  per-run viewer link (notify.viewer_url). Verified live end-to-end with
  dapper-heron's real card. SKILL.md tells Hermes to read the inbox before
  ssh-polling.
- [x] **Viewer links: base URL posture (Jeremy 2026-07-17)** — "the detail
  link is a local IP. probably should be a tailscale link or a
  (configurable?) DNS base URL." The knob already existed
  (`notify.viewer_url`); this box now points at the tailscale IP
  (`http://100.82.68.63:8787`) and DEFAULTS.md documents the posture
  (localhost for same-box installs — the common case — tailscale/DNS over
  LAN IP for split setups). Found + fixed on the way: viz_server's
  allowlist 403'd `artifact/**`, so every "Full report" deliverable link
  sent before 2026-07-17 was dead on arrival regardless of base — now
  serves `<run>/artifact/*.{md,txt,html,json,csv}` only (repo.bundle et al
  stay denied; pins in test_viz_server). Remaining, both Jeremy-side:
  (a) MagicDNS doesn't resolve on the tailnet (both boxes fail on
  `maro.taile7bacd.ts.net`) — enable in the tailscale admin console for a
  stable pretty name; (b) the phone isn't on the tailnet (only maro +
  jers-16-mbp), so tailscale links open on the MacBook but not the phone
  until it joins — if that's a problem in practice, swap viewer_url back
  to the LAN IP in ~/.maro/config.yml (one line).
  **Update (same night):** superseded-in-flight by the public route —
  Caddy + caddy-security now runs on this box (SSO-as-floor decree;
  deploy/caddy/) serving https://mc.feifdom.com/maro/* behind an auth
  portal. **LIVE 2026-07-17 (same night):** Jeremy forwarded 80+443; the
  first ACME timeouts predated the forwards (external TCP probes then
  confirmed both ports reachable; a caddy restart got "certificate
  obtained successfully"); portal verified from off-LAN; viewer_url
  flipped to https://mc.feifdom.com/maro. Still Jeremy-side: first
  portal login (bootstrap credential retrieval + password change —
  README) and, when wanted, the GitHub OAuth upgrade. LAN caveat: the
  public name from inside the house needs router NAT-loopback
  (hairpin); if his router lacks it, in-house devices fall back to the
  tailscale/LAN base while messages carry the public link.
  **CLOSED 2026-07-17 (morning):** final shape is a dedicated subdomain —
  Jeremy added maro.feifdom.com at Namecheap and created the GitHub OAuth
  app; viz now serves at the subdomain ROOT (no /maro prefix), cert
  obtained, GitHub OAuth ENABLED live (pinned to slycrel; webadmin local
  login = break-glass, password in ~/claude/credentials-backup/caddy/).
  mc.feifdom.com was reclaimed for the kids' Minecraft server — no
  redirect kept, pre-swap message links are dead by choice. viewer_url →
  https://maro.feifdom.com. **VERIFIED by Jeremy 2026-07-17: "github
  SSO is working as intended."** Arc closed end-to-end. He expects
  "another UX pass at some point for the goal runs" — unscheduled, no
  open work.
- [x] **Completion excerpt should be the deliverable, not the step log** —
  SHIPPED 2026-07-17 (same night, answer-first pass). Two new curators:
  `locate_deliverables` (FS-diff of the run's project dir via
  `files_modified_since`, ranked by name-hint/prose/size; top copy lands in
  `<run>/artifact/` and `deliverable_link_path` on the card) and
  `synthesize_answer` (`answer_summary` = the ANSWER to the goal — gated
  cheap LLM call under `curation.answer_synthesis`, deterministic
  deliverable excerpt otherwise/on failure). Both message surfaces lead
  with the answer; verifier self-grade only shown when NOT achieved.
  Two-tone contract (Jeremy 2026-07-17): raw-orchestrator surfaces render
  human-readable; the Hermes push carries DATA (goal + answer_summary +
  full `deliverable_content` ≤16KB) and a detached Hermes brain turn
  composes the user DM from it (deterministic DM kept as spawn-failure
  fallback). Verified live: Hermes composed, sent, and filed the event to
  processed/ unprompted. Pins in test_run_curation / test_notify_telegram.

Container-on day-one findings (2026-07-16, two dispatched verification runs):
- [x] **Provenance freshness-window false demotion** — run 123bf935 demoted
  `incomplete` because `_run_window_start` reconstructed the window as
  now − elapsed − buffer and a slow post-loop closure slid it past artifacts
  the run's own early steps wrote. FIXED same morning (8393883): wall-clock
  start recorded in `_handle_impl`, preferred at both provenance call sites;
  pin `test_run_window_start_prefers_wall_anchor`. Re-verified live on run
  d2f4e2f4 (no mtime flag).
- [x] **Closure downgrade reason never reaches the run card** — SHIPPED
  2026-07-16 (code swept into `5c3a886`, review fixes follow-up commit; full
  adversarial-review record in BACKLOG_DONE). Summary now leads with
  "Downgraded to not-achieved — {reason}"; `goal_verdict_downgrade_reason`
  rides metadata → card → report → CLI, replace-semantics on re-stamp
  (resume stale-key bug found by review, fixed). The "detector too
  shape-strict" side-question became the compound-command item below.
- [x] **Step-count constraint ignored in decompose** — SHIPPED 2026-07-16
  (code swept into `5c3a886`, review fixes follow-up commit; full record in
  BACKLOG_DONE). `goal_step_ceiling()` detector + binding directive + prompt
  clamp + re-ask-then-truncate enforcement on all six decompose lanes,
  carries across boundary AND milestone expansion; byte-identical prompts
  when no ceiling stated.
- [x] **Probe-modality classifier counts run-then-grep compound commands as
  static** — SHIPPED 2026-07-16 (Jeremy's call, same day, after the
  verdict-shift measurement; full record incl. the measurement and the
  original DECISION-FLAGGED analysis in BACKLOG_DONE). Per-segment
  classification with a quote-aware top-level splitter; exactly one
  historical verdict flips (d2f4e2f4's own false downgrade).
- [x] **Precondition pre-flight strings leak into the closure check list** —
  SHIPPED 2026-07-16 (full record in BACKLOG_DONE, incl. a mechanism
  correction: the strings were never run as shell — the real chain was
  scope.py's naive comma split shredding prose preconditions +
  `_classify_precondition`'s absence-of-spaces command gate letting the
  fragments (`wc)`, `PYTHONPATH=src`) through to shutil.which as synthetic
  inconclusive rows). Fixed both layers: paren-aware top-level comma split
  + positive binary-name command match; pinned with d2f4e2f4's exact
  deliverable lines producing zero synthetic rows.

- [x] **Stuck advisor block is dead code** — RESOLVED 2026-07-16, decided
  by Jeremy twice in two parallel sessions with the same outcome ("fix +
  config-gate... turn it on by default for ourselves" in the morning
  session; "let's fix + enable that stuck lane's advisor. I thought that
  was on" in the afternoon one — he thought it was on because he'd already
  turned it on). Shipped in `3d35ba0`: attribute access fixed, context
  build outside the try (shape bugs loud), except narrowed to the LLM call
  at warning level, (b)-retry records the stuck-flagged execution, the
  folded restart-break residual fixed (`_append_stuck_step_outcome()`
  before the break), `advisor.stuck_step` gate default OFF
  (docs/DEFAULTS.md) with this box opted in, 3 gate tests. Afternoon
  session added the missing restart-break pin
  (`test_stuck_restart_records_stuck_step_outcome`, mutant-verified —
  the 3d35ba0 tests covered the gate and (b)-retry but not the break
  exit's record guarantee).


### LT. Live-learning test arc — target burndown, cold/warm deltas, capability ladder (OPENED 2026-07-31, Jeremy)

Run live learning tests against goals we have **claimed but not probed**,
measure whether learning actually levels up, and write down the capability
progression those tests are climbing. The corpus problem is already solved
— `docs/CAPABILITIES.md` holds 5 tiers of real asks under the claimed≠probed
rule, and `research/ai-failure-task-patterns.md` holds 24 external failures
in 6 families (7 already folded into the tiers). What's missing is
**evidence**: the majority of catalog rows read `target` ("we believe
current machinery covers it, unproven").

Six shape decisions at open (Jeremy, 2026-07-31 — 5 and 6 same day, on
review of the first draft):

1. **Weighted to target-burndown**, not net-new. Every run should also
   settle a standing claim. Net-new entries only where a corpus family is
   thin in the tiers — Family 3 (tool-use/execution verification) and
   Family 6 (agency/trust violations).
2. **Instrumentation is a prerequisite, and wider than verdicts.** Jeremy:
   "let's fix this, and audit anything we might want written down by the
   runs that isn't already; we've already got lots of words on paper
   trails, but I think we may be missing some of that still; the more we
   can examine after (or even during) the runs, the better; both at the
   edges of steps and in the different processing layers within the
   overall harness."
3. **"Leveled up" = cold-vs-warm re-run delta.** Every test goal runs
   twice — once with the lesson/skill store cold, once warm. The delta
   (cost, steps, verdict, which lesson/skill was cited) is the evidence.
   Doubles spend; needs no new machinery and can't be self-graded.
4. **The ladder gets its own short doc** — `docs/CAPABILITY_LADDER.md`.
   CAPABILITIES.md stays the goal well; the ladder is the progression map.
5. **A failure is never a success.** No goal may be graded pass-by-refusing;
   goals shaped that way get reframed with a positive deliverable, and a
   genuine miss is an unbuilt bridge, recorded as not-achieved. Full decree
   in LT-1.
6. **Trace the work and write it all down, before the batch runs.** Each
   decision, LLM prompt and output, step plan, and artifact — durable and
   meaningfully reachable by all three consumers (report, tests, mining).
   The review pass comes first because "we keep stumbling on data that we
   thought we had but didn't." Full decree + first-pass findings in LT-0(d).

Vocabulary is already decreed — do not coin a parallel one.
`COMPOUND_THINKING_DESIGN.md` §8: capability edges are the only edges that
persist off the map, and "the tech tree IS the skill library / evolver." A
level-up paid once amortizes across the goal family. The bridges in this
arc are tech-tree nodes; the top rung of every ladder is a **skill
capture**, which is what makes the rung amortize instead of evaporate.

- [ ] **LT-0 — verdictability + paper-trail audit (PREREQ, blocks LT-1).**
  A batch that can't be examined afterward teaches nothing; the
  2026-07-29 packaging census already found real holes.
  - [x] **(a) Verdict-blind lanes — CLOSED 2026-07-31 by chunk B**
    (concurrent session, not this arc; GOAL_BRAIN "Chunk B shipped:
    verdict-blind lanes closed + verdict-flow readout"). `_verify_post_apply`
    now stamps `goal_achieved` from the test suite (the suite IS the judge);
    interactive NOW is judged via the hosted-free family only;
    `stamp_outcome_verdict` stamps `goal_verdict_at` so framing→verdict
    delay is measurable. **Its live census independently confirms this
    arc's pooling correction from the other side** — "agenda flow is
    healthy since W28 (18/34 → 6/12 judged); the 3% all-time stock is
    April-era pre-verdict rows (no backfill, by decree)." So the census's
    96.3%-unverdictable headline was stock, not a live leak.
    **`python3 -m verdict_flow` is the authority on verdictability from
    here — not this census**, which now says so in its own output.
  - [ ] **(b) done-without-closure tripwire.** 5 of 51 loop_id-era agenda
    rows are status=done with no verdict in either store (closure never
    ran, not a stamp miss); 2 were same-day, so it's live and low-rate.
    Captain's-log honesty event at finalize so the gap is visible.
  - [x] **(c) Run-dir record census — TOOL BUILT 2026-07-31; BOX RUN DONE
    same day.** Baseline committed (deliberate gitignore exception, per the
    re-run+diff protocol below): `output/provenance_census/census.{json,txt}`
    — 734 runs, commit 861ab28, generated 2026-08-01T05:22Z. Predictions
    scored against the pre-registered table:
    - `build/calls/` **came back otherwise — and the "otherwise" reading
      was WRONG, my instrument's fault (corrected 2026-08-01).** The
      scored line said "record mode is effectively off in production."
      It is not. The 12.6%-all-history / 50%-post-era pair pooled 619 runs
      that PREDATE record mode (shipped 2026-06-26) with runs that could
      have used it, and the post-era bucket was **n=8**. The monthly
      series says: 2026-04 0%, 2026-05 0% (n=476), 2026-06 0% (n=141,
      feature shipped on the 26th), **2026-07 80.7% (92/114)**. Record
      mode works. The real, much narrower finding: **~19% of July runs
      still capture zero calls** — the ContextVar/lane hole of EDGE 2, at
      a fifth of runs rather than seven-eighths. Separately true and worth
      recording: the **pre-2026-06-26 corpus (~619 runs) is permanently
      blind on LLM I/O** — not a bug, the feature did not exist — so
      retrospective prompt/response mining of that history is impossible.
    - **The real alarm the pooled table buried: `recall_citations.json` at
      10% of July agenda runs** (2.1% all-history). This is the artifact
      naming *which lessons a run cited* — the exact rail a cold/warm
      delta needs to attribute an improvement to a specific lesson. The
      pre-registered reading said "all-absent = the citation join is dead
      and cold/warm attribution has no rail"; 10% is not all-absent but is
      close enough to no rail that LT-1 cannot measure what it exists to
      measure until this is fixed. Its partner
      `skills_manifest.jsonl` sits at 55% of July runs — no manifest, no
      attribution, so the skill gets neither credit nor blame.
      **These two, not record mode, are the batch blockers.**
    - outcomes `no_loop_id` **came back otherwise** — 96.3% (1397/1450),
      far beyond NOW-lane-sized; unverdictable by task_type is dominated
      by `agenda` (1256). Agenda runs are losing the join too. **Caveat
      pending re-run:** this number has the same pooling hazard as
      `build/calls/` above and the first census could not rule it out —
      `census_outcomes` had no date bucketing, so pre-loop_id-stamping
      rows are pooled in. The tool now emits a per-month verdictability
      table; **re-run before acting on 96.3%.** The finding is directionally
      real either way (only 41 rows in all of history carry a verdict), but
      the fix differs: a live join failure gets wired now, a historical
      one only means the denominator has to be rebuilt going forward.
    - scope/resolved_intent 87.5% post-era, recall_citations 100% post-era,
      skill_attribution low-by-design — all roughly as predicted.
    - Environment.json/skills_manifest/checkpoint/run_card post-era at
      100% but tiny all-history denominators; per-stage table in census.txt.
      July-only rates from the monthly series: environment 60%,
      skills_manifest 55%, checkpoint 48%, run_card 93%, step-*.md 50%,
      scope/resolved_intent 80%, plan.md 96%, report.html 90%,
      captains_log_slice 92%. `skill_attribution.json` 29% stays
      correct-by-design (FULL-trust verdicts only).
    - [x] **Instrument fixed 2026-08-01 — the era split was the defect.**
      A single late boundary (2026-07-29) left n=8 post and pooled
      pre-feature history into the whole-history column, which is how a
      working feature got scored as a production outage. The census now
      emits a **per-month coverage series** (artifacts) and a **per-month
      verdictability series** (outcomes), marks thin denominators (<20
      runs) with `~`, and says in both the code comment and the rendered
      header that the monthly view is load-bearing and the era columns must
      not be read alone. **Re-run on the box** to get a corrected baseline;
      diff against the committed one.
    `scripts/provenance_census.py` + `scripts/provenance-census.sh`.
    Covers 16 artifacts across 10 harness stages (intake, scoping, recall,
    skill-injection, llm-io, events, planning, step-exec, reporting,
    curation, attribution), plus the verdictability join from
    `outcomes.jsonl` that (a) and (b) need denominators for.

    **BOX HANDOFF — run this, hand back `census.json`:**
    ```bash
    cd /home/clawd/claude/maro-orchestration
    git pull
    setsid nohup bash scripts/provenance-census.sh &   # detached
    # artifacts → output/provenance_census/{census.json,census.txt}
    ```
    **Same trip — the SF-13 runtime pipe for this arc's decrees.** These
    six are in GOAL_BRAIN.md already; `decisions.jsonl` lives in the
    *workspace* memory dir, so the pipe only reaches recall when it runs
    **on the box** — running it on the dev Mac would write to that
    machine's fossil workspace and never surface to a run. Paste-ready:
    ```bash
    cd /home/clawd/claude/maro-orchestration
    D() { PYTHONPATH=src python3 -m knowledge_lens decision "$1" --rationale "$2" --domain learning-tests; }
    D "Live-learning test batch weights toward burning down CAPABILITIES.md target rows, not net-new goals" \
      "The corpus problem is solved; the evidence problem is not. Every run should also settle a standing claim."
    D "Instrumentation is a prerequisite to the learning batch, and wider than verdicts" \
      "Audit what the runs should be writing down and aren't — at step edges and across harness layers — because we keep stumbling on data we thought we had but didn't."
    D "Learning is measured as a cold-vs-warm re-run delta" \
      "Every test goal runs twice, store cold then warm. Needs no new machinery and cannot be self-graded."
    D "The capability ladder gets its own doc, separate from the goal catalog" \
      "CAPABILITIES.md stays the goal well; docs/CAPABILITY_LADDER.md is the C0-C5 progression map and the blank-slate acceptance tests."
    D "A failure is never a success: no goal may be graded pass-by-refusing" \
      "An expected failure is a goal we haven't engineered or learned to solve yet. Reframe such goals with a positive deliverable; record a genuine miss as not-achieved and feed it to the ladder, so the store cannot learn that declining is what wins."
    D "Trace the full run paper trail before spending on the batch" \
      "Each decision, LLM prompt and output, step plan and artifact must be durable and reachable by the report, the tests, and post-hoc mining."
    D "A coverage percentage is uninterpretable without knowing what its denominator contains" \
      "The first census pooled 619 pre-record-mode runs with post-feature runs and scored a working feature as a production outage; a pre-registered prediction table does not protect against a denominator defect."
    ```
    Safe to run against a working box **by construction**: read-only,
    stdlib-only, and it deliberately does **not** import from `src` (the
    sibling stats scripts do) — importing `config`/`ancestry` would load
    workspace config and let the probe perturb the state it is measuring.
    Wrapper runs it at `nice -n 15`.

    Three design points that keep the numbers honest, worth knowing before
    reading the output:
    - **Per-lane applicability.** A NOW one-shot has no decompose phase, so
      a missing loop plan there is correct, not a gap. Only artifacts
      expected for that lane count against it.
    - **In-flight runs are excluded** from coverage. A run mid-flight
      legitimately lacks finalize artifacts; counting it would manufacture
      findings.
    - **Era bucketing at 2026-07-29** (the attribution seam's ship date).
      A single whole-history rate would understate today's coverage and
      hide a live regression, so pre/post columns are reported separately.

    **Predictions, written down BEFORE the run** (claimed≠probed applied to
    our own audit — so a surprising result can't be rationalized after the
    fact):
    | Artifact | Expected | If it comes back otherwise |
    |---|---|---|
    | `build/calls/` | near-100% post-era (record mode is documented default-ON) | EDGE 2 is live and large — record mode is effectively off in production and we have been reasoning about prompts we never kept |
    | `source/scope.md`, `resolved_intent.md` | high on agenda runs (scope_generation is OFF for fresh installs but ON on this box since 2026-07-09) | the box's scope lane is not writing what we think it is |
    | `source/recall_citations.json` | present on agenda runs that recalled anything | all-absent = the chunk-4 citation join is dead, and cold/warm attribution has no rail |
    | `source/skill_attribution.json` | **LOW, and that is correct** — it only stamps on FULL-trust verdicts | do not read this row as a hole; it is gated by design |
    | outcomes `no_loop_id` | roughly NOW-lane-sized | a larger share means agenda runs are losing the join too |

    **The dev-Mac numbers are not a baseline.** The local workspace holds 3
    runs, all from 2026-07-12 — a fossil predating several of these seams.
    Recorded here so nobody later cites them as a "before" picture. (Local
    output for shape-check only: 0/3 `build/calls/`, 0/1 scope +
    resolved_intent + recall_citations on the single agenda run, 2/3
    outcomes rows unverdictable, both NOW.)

    **Re-run + diff is the protocol.** The first box `census.json` is the
    committed baseline; after fixes, re-run and diff rather than re-eyeball.
    Read the whole census before fixing anything — fixing the loudest row
    first is how you miss the pattern underneath it.

    **CORRECTED BASELINE (box re-run 2026-08-01, commit 312b6a6, same 734
    runs on the fixed instrument). Settled reading:**
    | artifact | all | since | first seen | verdict |
    |---|---|---|---|---|
    | `build/calls/` | 13% | **81%** | 2026-07 | works; 19% live drop |
    | `run_card.json` | 14% | **93%** | 2026-07 | fine |
    | `captains_log_slice` | 89% | 89% | 2026-04 | fine |
    | `plan.md` | 94% | 94% | 2026-04 | fine |
    | `scope.md` / `resolved_intent` | 42% | 42% (**80% in July**) | 2026-04 | improving: May 17 → Jun 74 → Jul 80 |
    | `skills_manifest.jsonl` | 11% | **55%** | 2026-07 | **BLOCKER** |
    | `recall_citations.json` | 2% | **10%** | 2026-07 | **BLOCKER** |
    | `skill_attribution.json` | 6% | 29% | 2026-07 | correct-by-design |

    - [x] ~~**BLOCKERS for LT-1, confirmed not artifacts:** `recall_citations`
      10% and `skills_manifest` 55% … the batch cannot measure what it
      exists to measure until these are fixed.~~ **RETRACTED 2026-08-01 —
      this was wrong, and it was the SAME denominator defect a third and
      fourth time, at finer granularity each time.** I asserted it twice
      (here and in a commit message); the correction:
      - `recall_citations.json`'s writer shipped **2026-07-21** (afe5c5a,
        chunk-4 contradiction wiring). The July bucket pooled **90 runs
        where the file was IMPOSSIBLE** with 15 where it was possible.
        True post-ship rate: **11/15 = 73%**, not 10%.
      - `skills_manifest.jsonl`'s writer shipped **2026-07-09** (f49f318).
        Post-ship: **58/62 = 94%**, not 55%.
      - `scope.md`'s 42% was the *other* defect: 18% across May's 321
        step-shaped pseudo-runs vs **80% on July goal-runs**.
      **Nothing in the paper trail is a blocker.** The rail is in far better
      shape than I reported. What survives as a genuine gap is EDGE 2's
      ~19% silent call-record drop (independently confirmed by the
      plan-produced/reached-done discriminator, so not a ship-date artifact),
      `step-*.md` at 50% of July runs, and `skill_attribution` at 57%
      (by-design, FULL-trust only).
      **The empty-case fix (landed 54ac079) is still correct and worth
      keeping** — it converts an ambiguous absence into an explicit empty
      record, which the cold arm of a cold/warm pair needs. But it is a
      refinement for the residual 4/15 and 4/62 runs, **not an unblocking**.
      Do not let the fix's existence imply the batch was blocked.
      **Instrument fixed again:** first-seen now infers at **day**
      granularity from the earliest run that actually produced the artifact,
      not month. Documented caveat, because it bounds the claim: first-
      OBSERVED is a proxy for the ship date and anchors late when early
      post-ship runs legitimately had nothing to record — `recall_citations`
      reads 100%/n=11 from 07-27 where the true 07-21 figure is 73%. Better
      than months; still not a substitute for knowing the commit.
    - [x] **LIVE VERIFICATION 2026-08-01 — run `fd00c7be-humble-ferret`
      (agenda, local ledger census, 6m47s, $2.02).** The whole rail works
      end to end on a real run: `recall_citations.json` present and
      populated (4 rule ids + 3 lesson ids), `skills_manifest.jsonl` present
      with BOTH stages (decompose 2 skills, curated_summaries 1),
      `build/calls/` capturing 10 calls with **zero unlabeled purposes**,
      `loop_id` on the outcome row, and a closure verdict
      (`goal_achieved=True`, source `closure`, confidence 0.9). Deliverable
      was faithful and self-critical (it flagged a 26.7% stuck rate in its
      own sample — see the spin-off item below).
      **EDGE 6 paid for itself immediately.** Per-layer token attribution,
      previously unanswerable without prompt-sniffing:
      `step-execute` 381,197 in (**87%**) vs the ENTIRE orchestration stack
      (routing + clarity + scope + cuts + 3× decompose-candidate +
      decompose-compose) at ~56,000 (13%). One step burned **273,738 input
      tokens to run `wc -l` + `tail -3`**. That is the errand-envelope
      problem measured and attributed rather than inferred — and it says
      the lever is worker re-read churn inside the executor, not harness
      overhead. Backend is subprocess `claude -p` by deliberate choice
      (Jeremy 2026-08-01, "don't sweat the overruns"), so the repeated
      max_tokens cap overruns are expected and not a defect to chase.
      **NOT yet verified: the empty-case recording**, which is what the
      fixes were actually about. This run cited lessons and matched skills,
      so it exercised the populated path. The 10%/55% coverage came from
      runs where nothing was injected; that half proves out either on a run
      that matches nothing or by re-censusing once several August runs
      exist and watching coverage move toward 100%.
    - [ ] **EDGE 2 sharpened — it is a real silent drop, not early deaths.**
      22 of 114 July runs captured zero LLM calls. Of those 22, **16
      produced a plan** (decompose is an LLM call, so calls provably
      happened) and **11 reached `status=done`** with a `run_card`. A
      completed, planned, curated run with zero captured I/O cannot be
      explained by dying early — recording silently dropped. The census now
      computes this discriminator itself. NOW-lane drop rate (4/8) runs
      higher than agenda (17/105) but n is small.
    - **Verdictability resolved by the per-month table**: 2026-04 1272 rows
      100% blind / 0 verdicted, 2026-06 66 rows 100% blind, **2026-07 112
      rows 52.7% blind, 41 verdicted.** The 96.3% headline was 1272 April
      rows. July's 52.7% still predates most of chunk B (shipped 07-31), so
      the live rate should be read from `verdict_flow`, not here.
    - [ ] **Store-history mismatch worth a look before LT-1 joins anything:**
      outcomes.jsonl has 1272 rows stamped 2026-04 but the workspace holds
      only 2 April run dirs, and **zero outcome rows for May** against 476
      May runs. The two stores' histories do not line up, so the
      runs↔outcomes joinable universe is roughly the July era (~112 rows /
      114 runs), not 1450/734. Cause unverified — likely the outcomes ledger
      predating run dirs, possibly a backfill stamp. Worth 10 minutes before
      any cold/warm join is built on it.
  - [ ] **(d) Full provenance trace — the decree, and the review pass that
    precedes the batch.** Jeremy 2026-07-31: "we should be capturing each
    decision, LLM prompt and output, step plan, and other artifacts along
    the way; they should be meaningfully available to the end user via the
    report we have as well as usable for testing and post-processing
    examination of the data… We keep stumbling on data that we thought we
    had but didn't for various reasons." **Three consumers, all binding:**
    the run report (human), the test suite, and post-hoc mining. A stage
    that writes nothing durable is a hole in all three at once.

    **First-pass trace (2026-07-31, dev-Mac read of the code — findings,
    not yet box-verified):**
    - Confirmed sound: `runs.open_run` pins the run-dir *before*
      `intent.classify` (handle.py:862 vs :961), so the routing call is
      inside record-mode's window; `record_llm_call` is a single seam over
      every backend in `FailoverAdapter.complete` (llm.py:713), scrubbed
      via `secret_scrub`; `scope.md` / `resolved_intent.md` /
      `recall_citations.json` / `skills_manifest.jsonl` are real run-dir
      writes; the report already renders timeline, steps, LLM calls,
      verdict, environment, run activity, decision points, and operator
      injections.
    - **EDGE 1 — the first decision of every run is unrecorded as data.**
      `intent.classify` returns lane + confidence + reason +
      introspects_self; only `lane` reaches `metadata.json`
      (`write_metadata`'s field set is fixed: handle_id, nickname, prompt,
      lane, model, started_at, ended_at, status, pid, + extra). The
      confidence and the reason go to stderr under `--verbose` and
      nowhere else. There is **no captain's-log event for classification**
      (no INTENT_* type exists). The rationale is reconstructable only by
      hand-parsing the `purpose="classify"` call record. This is the exact
      decision that produced the Manti Run-1 misroute — and a cold/warm
      batch measuring routing behavior has no queryable field for it.
    - **EDGE 2 — record-mode is silently conditional.** `record_llm_call`
      returns None (no error, no event) when recording is off OR no
      run-dir is pinned. `_current_run_dir` is a **ContextVar**: threads
      that don't inherit it write nothing. `loop_parallel` handles this
      correctly (`contextvars.copy_context().run`, loop_parallel.py:469)
      and `agent_loop`'s multi-goal pool opts out deliberately
      (agent_loop.py:803, each goal owns its own run-dir) — but the
      failure mode is *silence*, so any future call path off the pinned
      context loses its prompt/response with nothing to detect it by.
      Wants a tripwire: a run that finalizes with zero call records when
      recording is enabled should say so out loud.
    - **EDGE 3 — coverage is unmeasured.** No census exists for "did this
      run write the artifacts it should have." Sub-item (c) is that
      census; it needs to run on the box (the 3-run Mac sample is
      unrepresentative).
    - **The census measures presence, not sufficiency** — it counts files,
      it cannot tell us a file carries the field we need. 100% coverage is
      not the finish line. EDGE 1 is the proof: `metadata.json` is present
      on every run and still doesn't carry the classification rationale.
    **Second-pass trace — the four the census can't answer (2026-07-31,
    dev-Mac code read, done while the box census was queued):**

    - [ ] **EDGE 4 — plan evolution is overwritten, not versioned.**
      `loop_artifacts._write_plan_manifest` writes `loop-<id>-plan.md`
      immediately after decompose and **overwrites it after every step**
      (its own docstring: "Write (or overwrite)"). It carries the *what* —
      steps, type tags, per-step status/elapsed/tokens/cost, an execution
      log — and none of the *why*. `replan_count` is a bare integer: you
      learn it replanned 3× and nothing about what changed or what
      triggered it. On a replan the earlier plan is simply gone. For a
      cold/warm pair where the warm run is expected to plan *differently*
      (fewer, fatter steps is the stated errand-envelope lever), the
      artifact that would show it is the one being overwritten.
      Mitigation that already exists: the decompose calls ARE labeled
      (`purpose="decompose-staged"` / `"decompose-compose"`, planner.py:927
      and :990), so raw plan reasoning is recoverable from `build/calls` —
      *if* record mode was on. `CUTS_DRAWN` separately carries
      constraints/probes.
    - [ ] **EDGE 5 — the step-edge context ledger is never persisted.**
      `ContributionLedger` is typed and provenance-stamped in memory
      (`ContextContribution(source, kind, text)`), and `drain()` at
      loop_execute.py:698 is the single merge point. The drained batch's
      only downstream use is **blocked-step re-arm** (loop_blocked.py:219,
      :275, :348). Nothing writes it. So "which contributions did *this
      step* actually see" survives only as `[source]`-prefixed text
      embedded in the step prompt inside `build/calls` — unstructured, and
      requires parsing the prompt to recover. That is precisely the field
      a cold/warm delta needs: if a warm run improves because a `prereq`
      or `reorientation` contribution fired that the cold run never got,
      the mechanism is currently unqueryable. (Operator injections are the
      exception — they ride `ctx.injections` → `LoopResult` → the report
      and are durable.)
    - [ ] **EDGE 6 — per-layer cost attribution breaks exactly at the work
      layer.** `purpose=` on call records is the per-layer key, and
      coverage is good overall: **73 of 82 real `adapter.complete()` call
      sites are labeled** (86 raw regex hits minus 3 docstring examples and
      the deliberate inner forward in `FailoverAdapter`, which pops
      `purpose` at llm.py:646 by design). But **4 of the 9 unlabeled sites
      are the agentic executor seams** — `step_exec.py:1202` (worker
      executor step), `step_exec.py:1313` (its tool-search retry),
      `workers.py:253` (worker-ticket executor), `team.py:215` (team worker)
      — each marked `# agentic:` in-source as where the real work happens.
      These are the biggest token consumers in any run. Net effect:
      "what did the harness spend vs what did the work spend" is
      answerable for the cheap orchestration layers and falls back to
      `loop_report._sniff_call_head`'s prompt-opener heuristic for the
      expensive one. The remaining 5 (factory_minimal:70, factory_thin:218,
      harness_optimizer:419/575, hosted_free:374) are experiment/dev-tool
      lanes — lower stakes. Fix is one kwarg per site.
    - [ ] **EDGE 7 — the live report drops operator injections.** Good
      news first: `plan.md` IS a genuine during-the-run surface (written
      post-decompose, refreshed each step; its docstring calls it "the
      primary debugging artifact for in-flight runs"), so "during" is not
      unanswered — it was under-credited in the first pass. But of the four
      `_write_run_report` call sites, only the three in `loop_finalize`
      pass `injections=`; the mid-run writers (agent_loop.py:516 parallel
      batch, loop_planning.py:288 post-plan, loop_post_step.py:254
      per-step) omit it. An operator who injects a note and then opens the
      live report to confirm it landed sees an empty panel until the run
      finalizes. Fix is one argument at three call sites.

    Deliverable: an integrity-gaps table in the shape of
    `CAPTAINS_LOG_EVENTS.md`'s, but for run-dir artifacts and harness
    layers — every stage listed with what it writes, where, and which of
    the three consumers can actually see it — plus fixes for whatever is
    cheap, and a census tripwire so the table can't rot the way the
    event-contract doc did before its own tripwire landed.

- [ ] **SPIN-OFF from the LT-0 verification run: stuck runs are not being
  auto-recovered.** Found by the verification run's own deliverable
  (`fd00c7be`, 2026-08-01) reading the live ledger, then corroborated by
  the census: **last-30 rows = 21 done / 8 stuck / 1 restart**, and
  whole-workspace = 168 stuck + 132 error + 57 stranded of 733 settled
  (~43% done). The run's own words: *"the near-absence of `restart` (1/30)
  alongside a substantial `stuck` count suggests stuck runs are largely
  being left stuck rather than auto-recovered, which is worth checking
  against whatever restart/recovery policy is supposed to be in place."*
  Two readings and they need separating before anything is built: either
  the restart predicate is too conservative and stuck runs that could
  recover don't, or stuck is a correct terminal state here and the number
  is just honest. Check the restart/auto-recovery policy against a sample
  of the 8 stuck rows first — this is a diagnosis, not yet a defect.
  (Filed here rather than as its own stack item because it came out of LT
  work; promote it if the sample confirms a live gap.)

- [ ] **LT-1 — the batch (8 goals, cold+warm pairs).** Mostly `target`
  rows from the catalog; two net-new from the thin families.

  | # | Ask | What it forces | Source |
  |---|---|---|---|
  | 1 | Quote what *[specific doc URL]* says about X | fetch-then-diff quote vs source; reject non-match | corpus 1.4/1.9 |
  | 2 | ✅✅ **COLD+WARM PAIR COMPLETE 2026-08-01 — the arc's first full cold/warm measurement.** Cold `4bf7f761`: $24.88/7.68M-in/42min, "cannot determine" + 13-step trail, achieved=True @ 0.85. Warm `e2f4578b` (byte-identical prompt): **$17.39/5.42M-in/21.8min — −30% cost, −48% wall — landing dead-center in the pre-registered $15–20 artifact-reuse tier, exactly what UU-2 predicted** (no terrain lesson existed to reach $3–6). Mechanism, from camera frame + tool events + citations: (a) **lesson transfer DID happen** — 2 of the 3 cold-minted lessons were the top-2 recall candidates by score and were cited (initially misread as no-transfer: the tiered store re-IDs lessons vs the mint ledger — see UU-4); (b) **artifact reuse confirmed** — 35 tool-events touched prior artifacts, source_trail.md UPDATED in place, plan collapsed 7→3 planned steps; (c) **residual waste the terrain teaching would zero**: still probed the known-blocked archives 5× (vs dozens cold). And the warm deliverable is BETTER, not just cheaper: verdict upgraded to SPLIT (the claim bundles "first" + "illegally", resolved separately) on newly-fetched primary evidence — the actual 1898 BMJ Maidstone paper (PMC2434294) the cold run only name-dropped. Achieved=True @ 0.9. | cold/warm delta (decree #3), lesson transfer, artifact reuse | corpus 1.3, Tier 1 `verified`, pair measured |
  | 3 | Iterate on this parser until it produces *[output]* on *[input]* | execution grounding — every claimed output executed, not written | corpus 3.2, Tier 4 `target` |
  | 4 | Extract *[fields]* from this doc into JSON matching this schema | the two-check split: schema-valid **and** field-content-verified | corpus 1.11, Tier 4 `target` |
  | 5 | Tell me about *[obscure book/author]* | retrieval-before-describe, or "insufficient information found" | corpus 1.6/1.10, Tier 1 `target` |
  | 6 | Correct a fact mid-run, then ask the same thing in a **later run** | does the correction persist across runs? the purest learning test here | corpus 4.5 (net-new) |
  | 7 | Take this failing suite and fix what's actually broken — don't paper over it | integrity under pressure (the standing no-skip/no-xfail bar) | Tier 4 `target` |
  | 8 | Diagnose why dispatch task *[id]* ended *[status]* with no detail | self-inspection across the dispatch boundary — unblocked 2026-07-18, never proven live | Tier 2 `target` |

  **Round-3 experiments (2026-08-01, Jeremy's variant folded in;
  predictions registered BEFORE dispatch):**
  - [ ] **(3a) Phrase-varied control** — same claim, different wording →
    different project slug → artifact cache defeated; measures lessons-only
    transfer on today's system, and is the control arm RUN_TEACHINGS
    chunk-1 needs. **Prediction: $20–25 (near-cold)** — the 3 strategy
    lessons transfer but are worth a few dollars; nothing else survives the
    slug change. Materially under $20 = today's learning does more than
    credited and the design's projected delta shrinks (want to know before
    building).
  - [ ] **(3b) Explicit-pointer corpus reuse (Jeremy's variant)** — hand
    the run the OLD project by name with a related-but-separate question
    over the same evidence (Maidstone 1897 vs Jersey City 1908: who has
    the stronger "first continuous municipal chlorination" claim).
    Not a control — the pointer re-enables reuse by design; it measures
    the Ladder-D user-facing shape ("you researched X, now answer Y from
    it") and probes the exemplar-run "surfacing" direction. **Prediction:
    $4–8 if pointer-following works** (read corpus + synthesize + maybe
    one fresh fetch); **$15–25 = it ignored the pointer and re-researched,
    which is itself the finding** (pointer-following failure, its own
    ladder rung). Run AFTER 3a so 3a's mints don't ride on 3b's slug.

  **Repeat-run economics (the warm arm, mechanism written down before
  running it — 2026-08-01, Jeremy's question).** On the subprocess backend
  there is NO cross-run prompt cache — every `claude -p` call is a fresh
  process — so **zero savings come from token re-reads. Every dollar the
  warm arm saves is behavioral change, which makes the delta an unusually
  clean learning measurement on this box.** Where savings can actually come
  from, in expected order of size for the chlorination goal:
  1. **Blocked-source knowledge** — the cold run burned ~800–900k tokens/
     step re-confirming HathiTrust/Google Books/Archive.org are blocked,
     multiple attempts each. A lesson as simple as "these 3 archives are
     infrastructure-blocked from this box; don't retry" cuts the most
     expensive steps entirely. Biggest single lever.
  2. **Artifact reuse** — the project slug derives from goal text, so an
     identically-phrased repeat lands in the SAME project dir with the
     full-text opinions + verdict_report already on disk. If the plan reads
     them instead of re-fetching, research-class collapses toward
     errand-class. (Also the m3 confound from the worker-slice A/B — here
     it is the measurement, not the confound, but phrase-vary the goal if
     you want to measure LESSON transfer rather than artifact reuse.)
  3. **Plan shape** — cold arm took 13 steps incl. a replan and two blocked
     branches; a warm plan that goes straight at CourtListener + the known
     gap could be ~4 steps.
  Level-off floor: the harness layers (~56k tokens, 13%) and one
  read-and-confirm executor pass. **Prediction, registered before the warm
  run: if lessons carry the blocked-source fact, warm ≈ $3–6; if they
  don't, ≈ $15–20 (artifact reuse only); if neither, $25 flat and the
  learning loop failed the test.** The warm arm is dispatch-ready any time.

  **Exemplar-run capture (Jeremy, 2026-08-01, direction — "not fully
  baked"):** this run may become one of the first "save this run's data"
  exemplars, serving three consumers at once — **learning** (its lessons/
  trail), **sharing between maro orchestrations** (the portable-learning
  `pack.py` lane + shared-skill-directory vision already in BACKLOG), and
  **surfacing** (the verdict_report/source_trail as a user-facing product
  shape). Nothing to build yet; when the shape firms up, this run
  (`4bf7f761`) is the designated first specimen — run dir, project
  artifacts, and call records are all intact for it.

  **Link farm as goal seeds (Jeremy, 2026-08-01):** the curated link-farm
  posts (already imported as `lf-` knowledge nodes) are a natural seed bank
  for both capability goals ("is this worth my time" / "research this" per
  post) and run suggestions. Cheap to pilot: pick 3 posts, phrase each as a
  catalog-style ask, run them as warm-lane probes. Note the standing
  `lf-` edge-contamination item elsewhere in this file before wiring
  anything automatic.

  **The paywall wall is a live §8 balloon-ask specimen.** The run's "What
  would change this verdict" section is EXACTLY the capability-frontier
  escalation payload from COMPOUND_THINKING §8 ("I can see landmark L — the
  Magie report — worth W, across a soft dead end; routes require capability
  C — paid archive access / a library card / interlibrary loan; here's the
  current-tech composition I tried; authorize the level-up?") — **written
  in prose but never routed as an escalation.** The run went `done` instead
  of surfacing the ask. That is a real gap between the escalation design
  and closure behavior, and this run is its first live specimen. (Jeremy's
  half-joke — the orchestration earning a bank account to get past
  paywalls — is this exact edge at full scale: capability acquisition as
  an authorized level-up, not a workaround.)

  **Look-back forensics on the cold arm (Jeremy's ask, 2026-08-01 — "it's
  the unknown unknowns that get ya"). What the archive holds well:** 32
  call records w/ full prompts+responses, 146 tool_events with
  name/input/output (so "which URLs did the worker try" IS recoverable),
  closure_verdicts.jsonl, per-event captains-log slice, full-text artifacts.
  **Three unknown-unknowns found:**
  *(UU-1-class direction, Jeremy 2026-08-01: "my gut knows [this] is there
  in way more edges than we think… we should fix those as they come up;
  might be worth some error handling assumption checks as well. Happy path
  coding isn't just a human problem, LLMs are as likely to do that as
  anyone." Two captures from that: (1) fix silent-loss edges as found, no
  dedicated sweep yet; (2) **idea, explicitly maybe-scope-creep: a 4th
  adversarial-review persona — expert QA** — whose lens is error paths,
  kill paths, and sad-path record loss specifically. The existing 3 Codex
  lenses keep finding logic/security issues; none owns "what happens to
  the paper trail when this dies mid-flight." Jeremy: "I bet [it] would
  find edges." Try it on ONE review before institutionalizing.)*
  - [ ] **(UU-1) Timeout-killed LLM calls leave NO call record.** Step 1
    ran 10 minutes, was killed at the 601s cap, and has ZERO bytes in
    `build/calls/` — `record_llm_call` rides the completed-call path only,
    so the run's single most expensive wall-clock event is unrecoverable
    (prompt gone, partial output gone; the 08:31→08:42 hole in the call
    sequence is the only trace). Fix direction: a stub record on the
    kill/timeout path (purpose, elapsed, killed=true, prompt, partial
    output if the transport can salvage it). This is EDGE-2-adjacent but
    distinct: not a ContextVar drop, a code-path hole.
  - [ ] **(UU-2) Lesson extraction favors strategy over operational
    facts.** The cold run minted 3 lessons — all strategy-shaped
    ("inconclusive verdict acceptable when the goal says so", "plans are
    step budgets", "pre-digital-record claims…"). **None captured the
    blocked-archives fact** (HathiTrust/Google Books/Archive.org
    infrastructure-blocked) — the single highest-value operational fact
    for any repeat, worth ~$15 of the $25. The trail HAS it
    (source_trail.md, per-step HTTP codes); the lesson funnel didn't
    distill it. The warm arm is now also a test of whether generic
    lessons + artifact reuse compensate for the missing operational
    lesson. **ARCHITECTED 2026-08-01 per Jeremy's direction ("1-trick
    pony… what the run overall teaches… lighthouse through the fog") →
    `docs/RUN_TEACHINGS_DESIGN.md`** (in the reading queue): root cause
    is structural three ways — extraction input is `summary[:500]` so
    the extractor never sees the trail; all 5 lesson_types are
    strategy-shaped; no scope concept (box / project / goal-class /
    global). Design: goal-level "what did this run teach" pass reading
    the EVIDENCE layer, typed teachings (terrain / landmark-class /
    self), probe-verifiable terrain facts, cuts-time injection, existing
    store extended not replaced. Build waits on the doc's 4 provisional
    DECISIONs; chunk 1's acceptance test is a phrase-varied warm re-run.
  - [ ] **(UU-4, from the warm-arm forensics) Lesson identity does not
    survive the mint→store seam.** The 3 cold-minted lessons carry ledger
    IDs (`lessons.jsonl`: 54d37f31/7de933d1/cd790134) but recall's tiered
    store — and therefore `recall_citations.json` and the camera frame —
    knows the SAME lessons (byte-identical text prefixes) under different
    IDs (56a3f80e/2b0290ed/…). Cross-store provenance joins are
    broken-by-design: "was the lesson minted by run A applied in run B?"
    cannot be answered by ID join, only by text matching. This misled the
    warm-arm analysis for a full round ("cold lessons never transferred" —
    wrong). Fix direction: carry the mint-ledger id into the tiered store
    row (or mint both from one id), so the citation rail joins end-to-end.
    Cold/warm attribution at scale needs this — text-matching does not
    survive paraphrase-on-reinforcement.
  - [ ] **(UU-3) This run planned WITHOUT any decompose-labeled call.**
    Zero decompose-candidate/compose purposes in the record — the 7-step
    plan was born from the `timeout-split` call (the killed step 1 got
    split into the numbered steps). So EDGE 4's mitigation ("plan
    reasoning is recoverable from the labeled decompose calls") does NOT
    hold for the timeout-split planning path; plan provenance for this
    shape lives only in that one call's unstructured response. plan.md's
    "Replans: 1" gives no hint the entire plan came from a split.
  Minor wishes, noted not filed: call records carry start `ts` only (no
  elapsed — per-call latency is adjacency-inferred, conflating pool time);
  late step-execute calls balloon to 1.2M–1.9M tokens_in (the re-read
  churn, now visible per-call — more errand-envelope evidence, not new
  instrumentation).

  **Grading decree (Jeremy, 2026-07-31): a failure is never a success.**
  "An expected failure is just a goal we haven't engineered (or learned) to
  solve yet, not a success." Two consequences, both binding on the batch:
  - **No goal may be graded pass-by-refusing.** Any goal whose only
    success condition is a decline gets **reframed with a positive
    deliverable** before it runs. #2 as first drafted ("find a source; if
    you can't, say so") was exactly this trap — an honest "unable to
    verify" scored as a win. Reframed, the deliverable is the *evidence
    trail*: the sources searched, what each returned, and the verdict that
    trail supports. A bare refusal with no trail is a fail; a correct
    "unsupported" **backed by an auditable search** is a pass. Same
    treatment for any Family-6 scope-breach probe — the deliverable is the
    in-scope work completed plus the record of what it declined to touch
    and why, not the decline alone.
  - **A genuine miss is recorded as not-achieved and becomes a ladder
    target.** When a run can't do it, that is an unbuilt bridge (soft dead
    end, `COMPOUND_THINKING_DESIGN.md` §8) — it feeds LT-2/LT-3 as the next
    capability edge. It never launders into the learning store as a
    success. This is what keeps the cold/warm delta honest: the store must
    not be able to learn "declining is what wins here."
  - **Container posture** for the network-sourced and scope-breach goals:
    `executor.container` is still opt-in pending the C4 flip.

- [x] **LT-2 — `docs/CAPABILITY_LADDER.md` SHIPPED 2026-08-01.** C0–C5
  checkpoints + four ladders (A web-reading, B here-and-now grounding, C
  self-inspection, D remember-across-runs) with per-rung status, indexed in
  `docs/INDEX.md`. Two findings worth carrying out of the writing: **(1) C4
  (persist & reuse) is the first rung orchestration earns that a good
  single-shot model plus tools cannot approximate** — everything below it is
  table stakes, so C4 is both the prize and where our evidence is weakest.
  **(2) Ladder D rung D1 (record what was injected) is not a capability any
  user would ask for** — it exists solely to make D2/D3 measurable, which is
  precisely why it was the thing quietly broken at 10% coverage. Original
  spec follows.
- [ ] ~~LT-2 — `docs/CAPABILITY_LADDER.md`.~~ Broad checkpoints, each a
  small named goal set that must read `verified`, not `target`: **C0**
  know what you don't know → **C1** fetch & ground → **C2** triangulate &
  resist fabrication → **C3** execute & check → **C4** persist & reuse →
  **C5** self-direct across ticks. Doubles as the acceptance tests for the
  blank-slate pre-installed skill set (same selection principle already in
  CAPABILITIES.md: the shipped set and the test corpus verify each other).

- [ ] **LT-3 — bridge asks (the rungs themselves).** Worked example, the
  web-reading ladder Jeremy named — mostly built, never assembled as a
  ladder: fetch one URL reliably (paywall/JS/PDF/403 are the real
  failures) → answer *one specific question* from a page without
  summarizing it → "is this worth my time" triage (SHIPPED, `verified`
  2026-07-17) → triangulate multi-source + HTTP-validate every URL →
  **capture the working access path as a reusable skill** (Tier 4
  `target`; the tech-tree node). Three more chasms worth laddering:
  local/geographic grounding, own-run introspection,
  persistence-across-runs. New asks land in CAPABILITIES.md as-phrased per
  the capability-capture rule.

**Open, Jeremy's call:** placement in this stack (the box pulls from the
top, and LT-0 is dev-side work); the spend envelope (errand class is
~7m12s/$1.50 post-warm-pool, so 8 goals × cold+warm ≈ 2h and ~$25); and
whether the batch runs concurrently with whatever the box is already
chewing.

### Typed dispatch envelope — channel separation at the dispatch boundary (OPENED 2026-07-29, Jeremy-decreed direction; box-side intake SHIPPED 2026-07-29)

Spec is written: **`docs/DISPATCH_ENVELOPE.md`**. Follow-on to the
db37d525 contamination repair (provenance gate SHIPPED 2026-07-29 —
the load-bearing mint-side fix; this is the channel-separation half).
A dispatch payload may be typed JSON — `user_ask` (verbatim, the goal
closure judges), `operator_context` (labeled advisory, never learnable),
`attached_artifacts` (stored with provenance sidecars),
`operator_constraints` (scoped-to-run, structurally barred from lesson
extraction). Prose dispatches keep working conservatively. UX decree
(Jeremy 2026-07-28): machine-to-machine ONLY — never force a human into
the envelope; "we're going to want the direct ask and the prompt
separately."

**Box-side intake SHIPPED 2026-07-29** (spec steps 1+2):
`src/dispatch_envelope.py` (parse fail-loud on declared-but-malformed,
labeled `operator_block` renderer, attachment store under
`output/dispatch-artifacts/<job_id>/` with sha256+source sidecars,
basename-only names); `handle_queue.handle_task` re-parses the task
reason so recall guard / navigator / handle() / closure / lessons all
key on `user_ask` (origin stamped `dispatch_envelope`); new
`operator_context` param on `handle()` rides `_extra_ctx_parts` →
`ancestry_context_extra` (context channel, never the goal — extraction
exclusion falls out because `reflect_and_record` receives goal only,
pinned by interface test); `dispatch.py cmd_enqueue` validates at the
boundary (malformed → exit 2, nothing queued; record displays user_ask
+ envelope meta, queue carries raw payload). 34 tests
(test_dispatch_envelope.py + hermes/handle pins). **Delivery rendering
box-side SHIPPED 2026-07-29** (trio-triage DO_NOW 3/3): `cmd_result`
emits a `delivery` block (`you_asked` verbatim + `dispatched_with`
envelope meta) for envelope dispatches only; dispatch record keeps
`user_ask` untruncated past the 500-char display copy; prose keeps the
pre-envelope contract. Note: like
prior_context, the operator channel reaches AGENDA-lane runs only —
the NOW lane has no ancestry context by existing contract.

**~~Poe-side skill + delivery rendering~~ — SHIPPED 2026-08-01**:
mini2-maro-dispatch-SKILL.md v0.3.0 teaches envelope construction
(shape + field authority, the write-file `dispatch $(cat ...)`
invocation that survives SSH quoting, refuse-don't-fallback on
malformed JSON) and delivery-block rendering (lead with `you_asked`
answered; `verbatim: false` renders "as recorded", never quoted as
exact). Repo copy = source of truth; installed to mini2 via the
header's scp path.

**~~Artifacts-travel-with-dispatch rider~~ — SHIPPED 2026-08-01**
(smaller than sketched — no gate changes needed; attachments already
crossed the SSH lane inside the payload): `runs.create_run_dir` copies
the dispatch's stored attachments + provenance sidecars into
`<run_dir>/fetch-raw/dispatch/` when origin carries the envelope
marker (the linkage rides origin because no run dir exists at dispatch
time — the navigator-rationale pattern). Fail-soft: the operator
block's dispatch-side paths still serve the subprocess lane if the
copy fails. Named residual edge: a CONTAINERIZED worker can't read
either copy — build_mount_map hard-excludes the workspace and the run
dir isn't mounted; if the container lane flips on for dispatched runs,
attachments need a landing under the mounted cwd (or prompt inlining
with a size cap). Evidence-gated on the C4-BOX flip, don't pre-build.

### NOW retry rung — failure-class-routed ladder: NOW → artifact retry / star → AGENDA (OPENED 2026-07-28, Jeremy)

Jeremy's ask: a middle rung between a failed NOW one-shot and a full
AGENDA run — "run it alongside a failed NOW result; I wonder what the
success rate would be over a strictly prompted NOW... or if the 'here's
the failure artifacts, try again' is just as good." Agreed hypothesis:
route by failure shape — shallow failures (missing detail, format,
unfetched link) → artifact-seeded NOW retry; structural failures
(multi-verb pipeline, needs tools/verification, "a single completion
can't do the work") → star-shaped mini-orchestration (runtime port of
the `.claude/skills/star` contract: master owns taste+judgement,
serial, 0..n discovered steps, typed stops); star-stuck → full AGENDA.
The §9.6 family-ROI line is the router's evidence base.

What exists today (verified in handle.py 2026-07-28): pre-execution
`_is_complex_directive` now→agenda escalation (default ON — but zero
live firing records found in metadata/memory/logs; verify reachability
before building on it); post-execution `_verify_now_outcome`
self-verdict demotion (task-path only; interactive keeps raw speed);
`_now_escalation_context` stash carries the failed quick answer into an
escalated agenda run. The rung slots between demotion and full
escalation.

**2026-07-28 corpus scan** (726 runs/ metadata files):
- 195 NOW runs: 59 done / 7 incomplete / 129 error — errors are ALL
  2026-05 broken-era noise ("Define success criteria" repeated), not
  signal.
- **Organic failure corpus ≈ 1.** Six of the 7 incompletes are one
  synthetic demotion-fix test (2026-06-11 nonexistent-binary); the one
  real case is the 2026-07-02 'comm' ask ("actually run them" —
  tool-requiring, exactly the structural class the rung targets).
- Real-world NOW asks on record (~6, mostly succeeded because easy):
  Manti gas, HTTP 429, worth-my-time link triage, what-time-is-it,
  system status, BST one-liner. `docs/CAPABILITIES.md` Tier 1–2 rows
  are the richer seed corpus — Manti Run 1 is the archetype (NOW
  answered from stale model knowledge, FAILED the contract; later fixed
  by pre-routing; the rung is the post-execution answer for what
  routing doesn't catch).
- Side-find: NOW runs carry NO provenance stamps
  (organic/smoke/control stamping shipped for loop runs only) — the
  organic corpus is smoke-contaminated ("say the word ok"). Stamp NOW
  runs before any organic A/B.

- [x] Verify `_is_complex_directive` escalation actually fires live —
  **DONE 2026-07-29 (autonomous batch), with a twist.** The gate IS
  live-reachable: config default ON, and `force_lane` (which bypasses
  it) is only ever passed by eval.py and the CLI `--lane` flag —
  dispatch/telegram traffic flows through it. But the zero-firings
  recon was structurally inconclusive: NOTHING durable was written at
  flip time (`classification_reason` is a HandleResult field, not
  metadata), so never-fired and fired-invisibly were indistinguishable.
  Fixed: the flip-time metadata write now stamps `now_escalated: true`
  (mirroring the verdict-escalation's existing `now_verdict_escalated`),
  pinned by `test_escalation_stamps_now_escalated_metadata`. Best
  current read: zero firings because the upstream classifier already
  routes complex directives to agenda (the escalation is a backstop for
  classifier misses, and those are rare post-BLE-fix) — now measurable
  instead of argued.
- [x] Provenance-stamp NOW-lane runs — **ALREADY LIVE, checkbox was
  stale (verified 2026-07-29).** Both halves shipped with the
  `open_run` unification after the 2026-07-28 scan: `open_run` stamps
  `measurement_class` into run metadata before lane routing (verified
  on live runs dba0386d/08f214c3, both `mc=organic`; older runs
  predate it), and the NOW-lane `record_outcome` call (handle.py)
  passes `measurement_class` onto the durable outcome row. Historical
  corpus stays unstamped — any organic A/B should filter to
  prospective rows, as verdict-gap-stats already does.
- [x] Build the rung — **shallow half SHIPPED 2026-07-29** (trio-triage
  PARTIAL verdict: artifact-seeded retry only; star port explicitly
  OUT). On NOW self-verdict demotion: one seeded NOW retry (ask +
  failed answer[:1500] + demotion reason), re-judged against the
  ORIGINAL ask; complex directives skip (structural → escalation);
  errored retry keeps attempt 1's answer; both attempts fully recorded
  (`-retry` artifact + outcome row + `NOW_ARTIFACT_RETRY` event).
  Gated `now_lane.artifact_retry` (default OFF, ON this box). A
  still-failed retry falls through to `escalate_on_not_achieved`
  unchanged, escalation context names both attempts. The star-executor
  arm (ii) stays open below with the experiment.
- [ ] Pre-registered experiment, 3 arms on the same seeds: (a) plain
  re-prompt, (b) artifact-seeded retry, (c) artifact-seeded star.
  Prediction on record (2026-07-28): (b) beats (a) clearly; (b) ≈ (c)
  on shallow failures, (c) wins on structural. Seeds: CAPABILITIES
  Tier 1–2 asks + authored structural-failure NOW asks (organic corpus
  too thin). Score on the done-vs-achieved ledger.
- [ ] **Both-lane requirement (Jeremy decree 2026-07-28):** run the
  matrix in the mature workspace (with accrued learning) AND a freshly
  minted workspace (without) — learning-delta is a first-class
  measurement axis. Reuse the benchmark-cell isolation machinery
  (twentieth pass).

### Next-leap: auto persona+skill packaging (ARC OPENED 2026-07-29, first-claim pull; slice 1 SHIPPED 2026-07-29)

The capacity-parked next-leap item, pulled into the free ACTIVE slot
2026-07-29 (claim condition met by the closure-unification ship; Jeremy's
"your coined terminology, not mine" cleared the over-cautious defer).
Trio-triage verdict governs the shape: **readout-first** — evolver
outcome data isn't causal evidence yet, so make the would-be packaging
inspectable before any behavior changes.

- [x] **Slice 1 — packaging readout (report-only) SHIPPED 2026-07-29**:
  `src/packaging_readout.py` + CLI (`PYTHONPATH=src python3 -m
  packaging_readout [--persona N] [--json]`). Persona × goal-type cells
  with would_include / would_exclude / insufficient_evidence buckets;
  evidence = the LIVE selector's own picks (find_matching_skills) joined
  to skill-stats.jsonl (the real usage store — skills.jsonl use_count is
  unmaintained, 2/314 nonzero), plus a supplementary open-circuit scan
  the live selector can't show. Coverage block reports every join's hit
  rate: dispatch→outcomes 133/140 goals; persona-outcomes loop_id join
  DEAD (0/40); verdicted outcomes thin (22 achieved / 18 not / 156
  unverdicted). Liveness-tested (real writers, no read-side mocks) +
  live-verified non-empty on this box.
- [x] **Slice 1 catch — degenerate router FIXED 2026-07-29:** root cause
  was two structural defects, both verified live: (1) training corpus
  99.4% positive (166/167 rows — labels bucket success_rate>0.6→1.0
  over a store where nearly everything succeeds), so the classifier
  predicts ~0.992 for everything; (2) `route_skills` vectorizes
  `skill.description` only — the goal NEVER enters scoring, so scores
  are a goal-independent per-skill prior (a haiku goal and a pytest
  goal got identical top-3). Fix: discrimination guard in
  `route_skills` (score spread < DISCRIMINATION_EPSILON across ≥2
  candidates → results degrade to method="keyword", so
  find_matching_skills' existing router check falls through to
  goal-sensitive keyword/tf-idf matching). Deterministic, reversible,
  no retraining. Live delta: research goal → research skills, pytest
  goal → Test-Driven Code Fix, haiku → haiku_to_file; readout buckets
  went from one constant trio to 46 distinct would_include skills in
  builder×agenda alone. 4 pin tests incl. end-to-end consumer pin.
- [ ] **Goal-aware router needs new training data**: skill-stats.jsonl
  rows carry no goal text, so the model structurally cannot learn
  (goal, skill) → outcome. If the router is ever to out-rank keyword
  matching, record the goal (or its 120-char prefix) on skill-stats
  rows at outcome time first; until then the guard keeps the model
  honest by benching it. UPDATE 2026-07-29: the run-verdict attribution
  markers (`<run-dir>/source/skill_attribution.json`, shipped with the
  measurement-honesty fix below) join loop_id → outcomes.jsonl goal
  text — (goal, skill, verdict) triples now exist on disk; a retrain
  can be built from them without touching the stats schema.
- [x] **Skill-stats measurement honesty SHIPPED 2026-07-29** (the
  disease under the router symptom): legacy counters credit every
  keyword-matched skill with a step completion (bystander attribution,
  per-step × per-run, failures only on hard-block) — 99.4%-positive
  store, top "skills" at 852 uses @ ~1.0. Shipped: (1) double-count
  fix — `update_skill_utility` no longer writes stats rows internally
  (both live callers already call `record_skill_outcome` directly);
  (2) honest counter pair on SkillStats
  (injected_runs/injected_successes/injected_success_rate) recorded at
  the closure-verdict seam (`stamp_outcome_verdict` →
  `_maybe_record_skill_injection_outcomes`) for skills ACTUALLY in the
  run's `source/skills_manifest.jsonl`, label = the run's FULL-trust
  goal verdict (era-10 verdict_trust gate), idempotent via
  `source/skill_attribution.json` marker (verdicts are re-stampable);
  (3) packaging readout prefers the honest regime whenever
  injected_runs > 0 and labels which regime each number came from.
  Legacy counters keep accruing (single-count now) so existing
  consumers stay fed. Pinned: counter math, no-stats-from-utility,
  seam end-to-end (FULL/directional/unjudged/idempotent re-stamp/
  manifest dedup/no-manifest/no-run-dir), readout regime preference.
- [ ] **Migrate legacy-rate consumers onto injected counters**
  (consumer-first, one at a time, each with its own liveness check):
  every consumer below still reads the inflated legacy success_rate —
  (a) `get_skills_needing_escalation` + needs_escalation flag
  (skills.py — escalation threshold 0.4 is unreachable when the store
  is 99.4% positive, so redesign never triggers); (b) router
  `build_training_data` labels (router.py:142-206 — blocked on the
  goal-capture item above, the guard benches it meanwhile);
  (c) pack.py portable-learning export `claimed_success_rate` (ships
  inflated claims to other boxes); (d) skill_loader.py Stats section +
  cli.py skills-list display (operator-facing numbers); (e) evolver
  promotion/variant scoring reads Skill.success_rate/utility (same
  provenance). Rule: a consumer migrates when injected_runs gives it a
  real denominator — don't starve breakers on day-one sparse data.
  FIRST CONSUMER MIGRATED 2026-07-29: the frontier gate
  (`frontier_skills`) now reads injected_runs/injected_success_rate —
  see the A/B-variant entry below. (e) partially done (variant
  candidacy); promotion scoring still legacy.
- [ ] **Slice 2 — wire packaging behind a flag** (default OFF,
  no-silent-spend posture): persona spec gains a packaged-skills field
  fed from would_include rows; router fix landed 2026-07-29 — remaining
  gate is a fatter verdicted denominator. UPDATE 2026-07-29 (census +
  backfill): the denominator is now real — 30 FULL-trust runs
  backfilled through the attribution seam, 21 skills carry injected
  counters, top skills at 18–19 verdicted runs. Honest data moved the
  picture: the former would_include trio sits at 0.67–0.68, BELOW the
  0.70 include bar — packaging on legacy counters would have shipped
  skills the run-verdict evidence doesn't support. Denominator grows
  organically now that the seam fires live (index v2 fix).
- [ ] **NOW + evolver_verify lanes are verdict-blind (census
  2026-07-29)**: both record outcomes without loop_id (NOW: 20 rows;
  evolver_verify: 5, still accruing), so stamp_outcome_verdict can
  never land on them — they are permanent "unverdicted" rows. Decide
  per lane whether a verdict is even meaningful (NOW one-shots are
  conversational; evolver_verify arguably self-verdicts), THEN wire
  loop_id stamping or an explicit exempt marker — an honest
  denominator needs rows to be verdictable-or-exempt, not silently
  neither.
- [ ] **"done" runs occasionally skip closure silently (census
  2026-07-29)**: 5 of 51 loop_id-era agenda rows are status=done with
  no verdict in EITHER store (outcomes row and run metadata both
  unjudged — closure never ran, not a stamp miss); 2 are from
  2026-07-29 morning, so it's live, low-rate. Candidate tripwire: at
  run finalization, a done run with no goal_verdict_source gets a
  captain's-log honesty event so the gap is visible instead of
  accreting quietly.
- [x] Durable dispatch→outcome join SHIPPED 2026-07-29:
  `record_persona_dispatch` stamps handle_id (handle.py passes the run's
  id), readout joins handle_id-first with goal-prefix fallback for
  legacy rows, coverage line reports the durable-join hit rate (0 at
  ship — accrues as new dispatches land). Pinned both sides + an
  end-to-end divergent-goal-text case where only the durable key joins.
- [x] **A/B variant subsystem un-starved SHIPPED 2026-07-29** (arena
  sweep headline): the whole Agent0 variant lifecycle (frontier rewrite
  → create_skill_variant → 50/50 routing → record_variant_outcome →
  retire_losing_variants) was wired end-to-end on live paths with green
  tests and had NEVER executed — `frontier_skills` gated on
  `use_count >= 3`, and `increment_use` (the only use_count writer) had
  zero callers since birth (2/314 nonzero live, both leaked test
  fixtures). Fix: frontier gate reads
  SkillStats.injected_runs/injected_success_rate (honest verdicted
  evidence; first legacy-rate consumer migrated), `increment_use`
  removed (use_count legacy-frozen on the dataclass), pin test:
  legacy use_count confers no candidacy. Live effect: the router's
  former constant trio (0.67–0.68 over 18–19 verdicted runs) lands in
  the 0.4–0.7 frontier band — the first organic A/B variants will
  target exactly the most-used below-bar skills.
- [x] **persona-outcomes.jsonl retired 2026-07-29**: writer removed
  (`record_persona_outcome` + conductor call site — sole caller sat on
  the unexercised conductor path; store dead since 2026-04-04, 0/40
  loop_id join). Superseded by the durable handle_id dispatch join.
  File kept on disk as historical data (data-retention rule). If
  persona-outcome tracking is ever rebuilt, do it at the verdict seam
  like the skill-injection counters.

### Subsystem liveness / self-health monitoring (Jeremy decree 2026-07-29 — "probably load bearing in the future, nice to have for now") — **v1 SHIPPED 2026-07-30**

**v1 shipped 2026-07-30** — `src/system_health.py`: DECLARED_PROCESSES
liveness registry (6 declared processes = the sweep's four finds plus
the run-ref join and closure-verdict stamping), cheap deterministic
probes riding loop_finalize beside run_skill_maintenance (Jeremy's
correction: "not a cron, let's hook into our startup/closure of the
goals… that we decided with skills months ago"), snapshot at
`memory/system_health.json` (the seeded maro-level systemic-metadata
home — state in the store, transitions in the log), SUBSYSTEM_SILENT /
SUBSYSTEM_RECOVERED user-surfaced transition events (edge-triggered on
narration state, never repeats while held), report-only, killswitch
`health.probes_enabled`, CLI `python3 -m system_health [--probe]`.
Rode in with the captain's-log audience adornment (dual-contract
decree) and the HOUSE_STYLE declared-liveness rule (new dynamic
process ⇒ declare it with a probe). Still open below: the fuller
declared-writer/consumer/join-key registry shape, and census-grade
enforcement (a probe per new process is review-enforced prose until
then).

Jeremy, on the arena-sweep finds: "this is why grafana and ops
monitoring is a thing… we need a way to ensure the system itself is
active and working, especially if we're going to allow it to modify
itself (and eventual code/mod/organic support)." Explicitly NOT
wall-of-monitors ops dashboards — system self-health. He notes he
wanted this at a basic IT level early on and it didn't land, "most
likely because it was an answer without any questions."

The questions now exist. One week of live-fire probing found four
dynamic processes that were wired, green-tested, and silently inert:
the contradiction emitter (dead loop_id→run-dir join, 0 events ever),
the A/B variant subsystem (use_count write-orphan starved the frontier
gate, 0 variants ever), persona-outcomes (writer on a dead path since
April), and times_applied receipts (bumped in-memory copies, never
persisted). Common shape: **writers that fire, consumers that don't,
joins that silently miss** — the failure class tests structurally
cannot catch (suites patch the joins; production data never crosses
them).

Shape sketch (design-first, not committed): a liveness registry where
each dynamic process declares its writer, consumer, join key, and
freshness expectation ("variants: expect create events within N
maintenance cycles of a non-empty frontier"), plus a periodic health
readout comparing declared vs observed (store row growth, event
counts, join hit rates, last-fired timestamps) that surfaces
"wired-but-silent" as a first-class finding. Prior art in-repo: the
chunk-8 stores/guards census (BACKLOG'd with the same
registration-convention prerequisite), packaging_readout's live
join-health lines (measured the persona-outcomes join dead — right
idea, report-only), heartbeat, and the DEFAULTS reverse census
(declared-vs-observed enforced by pytest). Load-bearing once
self-modification lands: a system that changes itself must be able to
notice a subsystem it just killed.

### Dev-approach "house style" doc + intentionality loop (OPENED 2026-07-28, Jeremy; v1 SHIPPED 2026-07-29)

Jeremy: "We should write down our dev approach somewhere in a guidance
doc... our learned-over-time 'house style' is meaningfully impactful,
and maybe deserves its own intentionality loop... would be hard to
replicate in a vacuum on a new machine."

**v1 SHIPPED 2026-07-29** — `docs/HOUSE_STYLE.md` from repo-visible
material only (part (a) of the approved shape): the workflow loop
(shape → build → verify → document → land → cross-model review →
verify-before-fix → fix → record), standing invariants each labeled
with its tripwire or honestly prose-only (out-of-the-box decree +
clean-checkout tripwire, DEFAULTS census, consumer-first, SF-13, data
retention, compiled-truth), pointer table to DEV_PATTERNS/CODING_NOTES/
CLAUDE.md, maintenance rule (tripwire-or-labeled; prose shrinks to a
pointer when the deterministic home ships). CLAUDE.md entry pointer
added. **Still open (needs Jeremy):** (b) importing the feedback-class
auto-memories from ~/.claude, interview-elicited gaps, codeLikeJeremy
pattern steals + the regression-harness idea (see item below).

Gap analysis (2026-07-28): much exists but is scattered — CLAUDE.md
(discipline rules), docs/DEV_PATTERNS.md (taste/judgement),
docs/CODING_NOTES.md (coding posture), the adversarial-review + star
skills — and the genuinely un-replicable part is the ~25
feedback_*/project_* auto-memories in `~/.claude` (machine-local, NOT
in the repo). Proposed shape (shape approved 2026-07-28 — "glad this is in the
backlog and think this will be good to have written down; and assume
it will continue to evolve"): one `docs/HOUSE_STYLE.md` that (a) writes
down the workflow loop itself (chunk → land → cross-model adversarial
review → verify-before-fix → fix → record; SF-13; census tripwires;
consumer-first; end-of-chunk discipline), (b) imports the durable
feedback-class memories into the repo where they're portable, (c)
carries its own maintenance cadence + adjudication gate (same
discipline as the star skill), with CLAUDE.md reduced to the entry
pointer.

- [~] **mini2 executes-locally detection — PARKED as accepted risk (Jeremy
  2026-07-29, same day it was raised).** The gap is real and stated: the
  2026-07-20 zero-creds decree is enforced **by construction for push only**
  (https fetch-only clone, no credentials → cannot land code); nothing
  prevents or surfaces mini2 *running* maro off its reference copy.
  Jeremy's disposition, unprompted and explicit: *"that was me essentially
  saying 'yep, it's a gap, that's a problem for future me'; writing that off
  as something I'll deal with if it happens and I don't think it's likely to
  happen... and difficult to guard against. I'm aware I'm playing with fire
  ... I've been running `--dangerously-skip-permissions` on the orchestrator
  box for about 5 months now — I'm living on a few edges, this is one I'm
  not worried about."* Consistent with the documented trusted-operator model
  (`docs/SECURITY_MODEL.md`), not a divergence from it.
  **Falsifiable park reason** (census: test these, don't re-argue the
  premise) — reopen if any becomes true: (a) mini2 grows a
  `~/.maro/workspace`, i.e. it actually ran something; (b) mini2 gains any
  credential that could land code; (c) anyone other than Jeremy gets access
  to mini2; (d) the poe lane starts carrying work whose failure isn't
  cheaply reversible. Until then the surfacing check (report a
  `~/.maro/workspace` on mini2, never gate) stays unbuilt **by decision, not
  by omission**. Context:
  `docs/history/2026-07-28-m1-vs-box-workflow-contrast.md` §8.2.
- [ ] **Declare the out-of-the-box invariant + tripwire it (Jeremy
  2026-07-28, DECREE):** *"Functionality that we add should presume that
  it's 'out of the box' functionality for the project day 1 — unless it's
  specifically functionality gated on prior learning data for whatever
  reason"*; corollary, *"the work we're doing should be able to be verified
  on a clean clone of the maro repository."* He flagged it as an assumption
  he'd never stated — *"maybe it needs to be declared?"* Declare it in
  HOUSE_STYLE.md (entry pointer in CLAUDE.md) **with a tripwire, not just a
  sentence**: this invariant was silently violated for months and caught
  only by the 2026-07-09 docker clean-machine trial (flat `src/` → pip
  installed zero modules; masked locally by `PYTHONPATH=src`). Shape to
  steal: DEFAULTS.md + its census tripwire, but for *runtime data* instead
  of config — a fresh-workspace check that entry points behave against an
  empty `~/.maro/workspace`, and an explicit marker on the exemption
  (learning-gated functionality must declare itself and degrade gracefully
  when the data isn't there). Ties to the M1's inability to review runs
  (M1-contrast pass above): seeded test-run data is the same missing piece.
  **FIRST TRIPWIRE SHIPPED 2026-07-29** (Jeremy: *"agree, sure, let's do
  that"*) — the **clean-checkout tripwire**: `tests/_checkout_tripwire.py`
  (mechanism) + `pytest_sessionstart`/`sessionfinish` in `tests/conftest.py`
  (wiring) + `tests/test_clean_checkout_tripwire.py` (7 tests). Snapshots the
  working tree at session start, compares at finish, and **fails the run** if
  the suite added files; only additions are flagged, since modifications to
  tracked files are already `git status`-visible while untracked drops into a
  gitignored dir are not — which is exactly where both known violations
  landed. Prunes only regenerable noise (`.git`, `__pycache__`,
  `.pytest_cache`, `.venv*`, `*.pyc`, `.coverage`) and **deliberately does
  not prune `output/` or `memory/`**, the two dirs the real leaks went to.
  Escape hatch `MARO_ALLOW_CHECKOUT_WRITES=1`. Verified three ways rather
  than assumed: unit tests on the pruning rules, end-to-end scoped pytest
  runs proving a littering test fails and a clean one doesn't, and — the
  decisive one — **the historical bug was temporarily reintroduced and the
  tripwire caught it**, naming all 7 files across both leak paths while the
  tests themselves still reported pass, exit code 1. Full suite green with it
  armed, no false positives across 6800+ tests. The mechanism lives in its
  own module rather than inline in conftest specifically so it could be
  tested — the 2026-06-25 lesson that a guard nobody exercises (every git
  hook, dead for a month behind a stale `core.hooksPath`) is
  indistinguishable from no guard. **Still open on this item:** the
  fresh-workspace half (entry points against an empty `~/.maro/workspace`),
  the learning-gated exemption marker, and writing the invariant down in
  HOUSE_STYLE.md — this tripwire covers the suite-hygiene half only.
  **Happy accident worth keeping** (Jeremy 2026-07-29: *"thankful for happy
  accidents"*): the M1 has **no maro config at all** — no `~/.maro/config.yml`
  and no workspace `config.yml` — so anything run here uses pure fresh-install
  defaults. That makes this dev host, unplanned, the closest thing to a
  clean-install test bed the project has, and it cheapens the §8 "give the M1
  the ability to run a run" item: no new infrastructure, just seeded data and
  a `maro-bootstrap install`.
- [ ] **Threshold provenance instead of observe-only (Jeremy 2026-07-28):**
  replaces the rejected "M1 never ships a live threshold" rule — *"we can
  hypothesize and make a best guess, then create follow-up work to confirm
  or pivot."* A magic number lands marked `reasoned` or `measured`, plus a
  paired backlog row naming the measurement that would confirm or pivot it
  (the drift-batch falsifiable-reparking discipline, applied to numbers).
  Shape accepted in principle, mechanism unbuilt; first candidates are this
  arc's three (fresh ceiling, weighted ceiling, Bash cap).
- [ ] **M1 workspace re-setup (Jeremy 2026-07-28, DECREE):** *"we should
  generally have a workspace on any given box rather than litter copies of
  the repo all over without purpose... we'd do well to re-set up our
  workspace for a reusable location on the M1, we have a sort of randomly
  set up repo right now."* Confirmed state: **two live workspaces on the
  M1** — `~/.maro/workspace/` (real; `runs/`, `memory/`,
  `correspondence.db`, touched 2026-07-27) and `.run-workspace/` inside the
  checkout (gitignored, mostly frozen 2026-06-21 plus two 2026-07-14 session
  dirs) — plus the checkout is still named `openclaw-orchestration` long
  after the rename. Decide one canonical location, migrate or delete the
  other (nothing is deleted without a look — retention decree), rename the
  checkout.
  **DONE 2026-07-29 (the consolidation half; Jeremy: "let's clean up the
  workspace and run with the best practices as far as the maro conventions
  on this box").** `~/.maro/workspace/` is now the M1's ONLY workspace.
  Actions, nothing deleted that wasn't provably regenerable: (a) the second
  workspace `.run-workspace/` moved **intact** to
  `~/.maro/evidence/m1-repo-run-workspace/` — deliberately *not* merged into
  the live workspace, since it carries its own `captains_log.jsonl` and
  merging foreign learning data during the Poe-contamination arc is the
  exact mistake that arc is about; (b) repo-local `memory/` (2 files frozen
  2026-06-21) archived to `~/.maro/evidence/m1-repo-memory-2026-06-21/`;
  (c) stale `output/build-loop.lock` removed (dead PID, its run had
  completed); (d) `.pytest_cache`, `.coverage`, `__pycache__` purged
  (regenerable). 466M → 434M, working tree clean, **suite green on the M1
  after the move: 6805 passed / 0 failed / 0 errors / 22 skipped, 119s**
  (removing repo-local `memory/` broke nothing — tests do write there via
  `OPENCLAW_WORKSPACE`, but they recreate it, confirming the isolation
  CLAUDE.md claims).
  **New convention this established:** `~/.maro/evidence/` — machine-local
  archived evidence that landed docs cite, kept *beside* the workspace so
  `~/.maro/workspace/` keeps matching the documented layout exactly.
  **Finding that outranks the cleanup** (see the out-of-the-box item above —
  this is that decree failing in the wild, found by doing this work): **three
  separate landed docs cite evidence a clean clone cannot see.**
  `BACKLOG_DONE.md` ×3 and `MILESTONES.md` ×1 cite
  `docs/history/2026-07-13-adversarial-review-*.md`; two 2026-07-14 history docs
  cited `.run-workspace/` (now repointed and explicitly marked
  machine-local); `docs/LOCAL_VALIDATOR.md:138` names repo `.venv-mlx` as a
  default that only exists here. The citations aren't wrong, they're
  *unreachable* — which is precisely what "verifiable on a clean clone"
  forbids. **Still open, deliberately left for Jeremy** (each is a judgment
  call, not a cleanup step): (1) do cited adversarial-review reports get
  committed so a clean clone can check the claim, or do citations get marked
  machine-local like the two above? — committing publishes to a public repo,
  which is his call; (2) `.venv-mlx` is 307M (70% of the checkout) and is
  referenced by LOCAL_VALIDATOR.md as a default — keep or drop; (3) the
  checkout rename `openclaw-orchestration` → `maro-orchestration` **has a
  real cost**: Claude Code keys session history by path, so renaming orphans
  `~/.claude/projects/-Users-jeremy-claude-openclaw-orchestration/` and
  loses `--resume` history for this project; (4) `output/runs/` holds 208
  repo-local dev runs — prune or keep as the M1's only local run corpus
  (note: that corpus is the M1's *sole* run-shaped evidence, per the
  contrast doc's §8 — deleting it is not obviously free).
  **ALL FOUR RESOLVED 2026-07-29 (Jeremy adjudicated; 466M → 131M).** Each
  answer changed once the data was actually looked at:
  (1) **Reviews — kept, archived out of the repo** (his rule: *"if that's
      review that's for completed code I'm not sure we need it; if we may
      reference it later for meaningful data, let's keep it"*). 29 report
      files existed and **not one of them was cited by anything**; the four
      citations in BACKLOG_DONE/MILESTONES name *different* files that are
      **not on the M1 at all and were never in git** (two of the four
      already self-describe as "box-local", so check the orchestrator box
      before calling them lost — the other two now carry the same marker).
      The surviving 29 are the sort of thing HOUSE_STYLE's regression
      harness would want as fixtures, so they live in
      `~/.maro/evidence/adversarial-reviews-2026-07/` rather than being
      deleted or published.
  (2) **`.venv-mlx` — was exactly the hole he suspected, removed.** The
      local rung was REMOVED 2026-07-21 by decree ("local LLMs are in the
      way for now"), `LOCAL_VALIDATOR.md` is `status: record`, and **zero
      live code under `src/` or `scripts/` references mlx** — the 307MB venv
      was pure residue. Deleted only after pinning revival cheap:
      `docs/data/venv-mlx-requirements-2026-07-29.txt` (mlx 0.31.2 / mlx_lm
      0.31.3 / transformers 5.12.1), noted in LOCAL_VALIDATOR.md's header.
      **Rider:** the bake-off *results* were sitting in gitignored `output/`
      too — 4 measured JSON files (accuracy/latency/per-case rows for
      qwen2.5-coder-3b and three vibethinker variants). Those are the data
      the doc's revival trigger would be judged against, so they were
      **promoted into the repo** at `docs/data/validator-bakeoff-*.json` —
      the one place committing was clearly right.
  (3) **Rename — symlink does NOT work, but the fix is trivial anyway.**
      Verified: `os.getcwd()` resolves symlinks (`PWD` keeps the link path,
      `getcwd()` returns the real one), so a symlink at the old location
      would still key sessions to the new path. The actual fix is to rename
      `~/.claude/projects/-Users-jeremy-claude-openclaw-orchestration/`
      (93 sessions, 36M) to match the new path key. Caveat found: each
      `.jsonl` embeds `"cwd": "/Users/jeremy/claude/openclaw-orchestration"`
      internally, so the rename is best-effort — and his own fallback
      ("worst case we go spelunking in jsonl files") is correct, they are
      plain JSONL. **DECIDED 2026-07-29 — rename SKIPPED** (Jeremy: *"let's
      skip the rename, agree it's not worth the effort"*). The checkout stays
      `openclaw-orchestration`; the GitHub repo is `maro-orchestration` and
      the local directory name is cosmetic. Do not re-raise: the cost is 93
      sessions of `--resume` history for a directory name, and that trade was
      weighed and rejected, not deferred.
  (4) **Run corpus — not evidence, and the real find was a leak.** The
      audit killed the premise: all 107 runs were **byte-identically
      trivial** — every `item.txt` == `"repo local"`, every status `done`,
      every one the synthetic `repo-local-check` smoke item. Not "early runs
      with bad data" and not "legit failed runs"; the same nothing, 107
      times. **Cause: `tests/test_build_loop_script.py` cleaned up its
      inputs but not its outputs**, leaking one run dir per suite execution
      since 2026-06-21 (plus 110 matching `output/heartbeat/runs/` records
      on a second path). Purging without fixing that would have regrown it.
      Fixed by snapshotting both output dirs before the subprocess and
      removing only what the test itself created — verified delta=0 on both
      paths, and the loose `build-loop-status.json`/`.lock` it also dropped
      are now cleaned when the test created them. One specimen of each kept
      in `~/.maro/evidence/m1-repo-local-smoke-specimen/`; the rest purged.
  **Correction to this item's own earlier text:** it claimed the corpus was
  "the M1's sole run-shaped evidence, deleting it is not obviously free."
  That was wrong — it was test litter, and deleting it cost nothing. The
  M1's lack of run-shaped evidence (contrast doc §8) is unchanged by this,
  which if anything sharpens the point: the M1 had 107 run dirs and zero
  runs.
- [ ] **Iteration protocol (Jeremy 2026-07-28):** the workflow spec in
  the open-thread entry below is a *partial dump* by his own flag —
  ">30 years... I've got reflexes and intuitions I likely can't
  directly name." Protocol: after the first pass or two, he shares real
  work-side changes and we **quiz him on the thought process behind
  them** — "I'm a much better question answerer than an essay writer."
  Interview-shaped elicitation for actionable unlocks; open-ended
  questions stay welcome for context-gathering — his same-day
  correction: "'never' is pretty strong... I like to ramble and answer
  more open-ended questions with talking about context." (Runtime
  journal amended accordingly: 83f06acf supersedes e2124d71 — style
  matched to purpose, not quiz-always.)
- [ ] **CodeLikeJeremy PoC as pattern input (Jeremy 2026-07-28):** his
  work-side `/codeLikeJeremy` skill (machine-local zip at
  `~/claude/strands-review-poc-master-skills-codeLikeJeremy.zip`) — a
  measured behavioral profile from ~1,600 commits + 63 review comments,
  with a regression harness. Work artifacts stay out of this repo
  (boundary decree); the *patterns* are directly stealable for
  HOUSE_STYLE.md: (a) descriptive-vs-prescriptive split — measured
  behavior beside the written standard, divergences labeled
  "Jeremy-specific, not incorrect"; (b) per-context profiles (style
  varies by repo — the M1-vs-maro contrast, independently reinvented);
  (c) author/reviewer asymmetry + **negative space as signal** (what he
  does NOT push back on is data); (d) hedge-language as a
  preference-vs-principle type marker; (e) a dated normative-override
  layer that beats measured densities (our GOAL_BRAIN quoting
  convention, rediscovered); (f) **the headline steal: a style doc
  with a regression harness** — real-issue fixtures, roll the repo
  back, fresh agent + skill makes the change, score against his actual
  diff. Decree-with-tripwire applied to style; HOUSE_STYLE.md can be
  tested the same way (fixtures = real maro sessions).

### Open-thread structure — beyond the backlog (DISCUSSION OPEN, updated 2026-07-28)

Jeremy: work fans out consequences faster than the linear chat walks
them; partially-opened paths never get fully revisited and it has
bitten us; "I'm not sure just adding to 'the backlog' is quite enough."
Framing agreed in-session: retention is solved (BACKLOG_DONE +
dev-recall + history docs) — **attention** is the gap; BACKLOG is a
flat list whose triage requires manual whole-file reads. Also his
verdict on the obvious alternative: "I had hoped that a simple sort of
epic/issue style flat backlog with sub-items was enough, but fighting
those branches eventually overwhelms me and I lose the plot."

**Design inputs — Jeremy's own dev workflow IS the requirements spec
(2026-07-28, near-verbatim):** he stubs modules/classes/methods first —
the skeleton is the plan, living in the medium of the work itself;
`//todo` comments are attachment points for "thoughts along the way
that need referenced later but no concrete attachment point in the
organization itself"; he "sees a path through", architects the module
shapes surfacing that path, then digs into details; he spirals over
modules to refine path thinking where unknowns remain, trusting the
skeleton to hold everything he's not currently touching. Standing
constraint (his recurring gut-feel, restated this session): keep
Claude's persistence-shaped thinking and his **timeline/story-shaped
memory** aligned — the structure needs BOTH views.

**Proposal v2 (2026-07-28, awaiting Jeremy's reaction):**
1. **Thread census first** (unchanged) — sweep BACKLOG / GOAL_BRAIN
   open threads / history-doc residuals / wiring-inventory surprises
   into a deduped inventory with parent provenance, state (untouched /
   partially-walked / blocked-on-Jeremy), last-touched. Now also
   produces the initial ACTIVE/PARKED split and validates the marker
   convention against the real thread shapes found. Census before
   structure — prior passes at this problem (graph-memory direction,
   recursive-orchestration memory, thread architecture) committed to
   shape before knowing the territory.
2. **Narrative spine** — a dev-lane captain's log (append-only, one
   paragraph per session close: what happened, what it opened, what it
   parked). Timeline-shaped for Jeremy's memory; entries name thread
   slugs so the two views link. Near-zero cost; end-of-chunk
   discipline gains one line.
3. **Attachment-point markers** — the `//todo` translation:
   `THREAD[slug]` markers in docs/code at the point of relevance; a
   deterministic collector generates the map + staleness ranking
   (markers are the durable copy; the view is regenerable). BACKLOG
   then holds arcs/epics only; leaf threads live where they attach —
   position carries the context that flat entries must rebuild in
   prose.
4. **Working-set cap** — explicit ACTIVE set (3–7 threads, the
   spiral), everything else PARKED (trusted skeleton); the surfacer
   proposes promotions only when ACTIVE has room. Attention budget as
   a first-class constraint, not a virtue.

Runtime mirror (deliberate, not coincidence): see Vision entry
"Step-skeleton parallelization" — the dev-side structure is the cheap
prototype of the runtime step-DAG. Jeremy: "we're organizing all of
this to a degree on the way I think and I'll take that as a sign we're
starting to really find some of the foundations."

**Census DONE 2026-07-28** → `docs/history/2026-07-28-thread-census.md`
(star use #3). 56 threads / 7 states; headline: **7 premise-drift
finds** — 6 queued for re-adjudication as one proposed ACTIVE batch,
1 (the C4 flip) resolved on the spot against live-box evidence — plus
7 stale doc lines currency-fixed in the census commit. Marker
convention validated with three rules the sweep forced: history docs
never carry live markers; doc-archival explicitly sweeps forward
obligations to BACKLOG/GOAL_BRAIN or declares them dead; every
PARKED/BLOCKED entry states its reason as a **falsifiable claim** so a
future pass can test it (the six drifts are exactly reasons falsified
silently). **Proposals 2–4 ADJUDICATED 2026-07-28 (Jeremy, same evening):**
(2) Narrative spine RATIFIED after reading the first real entry
("that's pretty good, I like that it's terse but cites references") —
with same-day amendments shipped: date headers for multi-day
run-on sessions, plain-language rule (gloss codenames on first use),
and a viz-server Dev log tab next to Runs/Reading ("gives me a 1-stop
shop to go find references"). (3) `THREAD[slug]` markers + collector
DECLINED for now — "let's keep the existing setup... let's not
overengineer our dev workflow that's already becoming a bit intense";
the census + falsifiable-park-reasons discipline stands alone.
**Revisit rider (falsifiable):** the next thread census re-asks this
question with fresh drift data; any lost-thread/premise-drift
incident before then reopens it immediately. (4) Cap mechanics:
self-serve promotion with notification BLESSED ("happy to have your
help here"); Jeremy explicitly reserves tossing prioritization to the
wind for his own agendas — that's legitimate, never a process
violation; Claude surfaces genuine priority questions when they'd
change the dev cycle. His standing concern to design against: "being
able to keep tabs on everything."

**Split RATIFIED 2026-07-28** ("Sure, sounds good"): ACTIVE =
threads 1–5 + drift-cleanup batch + 1 free slot, cap 7. Same day
(loose-ends pass): drift find #5 closed both halves (suite exit 0 +
fragile seam deleted in swarm chunk 1); free-slot trio examined — census
#31 (depth-cap) and #32 (backend-resilience pair) were ALREADY SHIPPED
(2026-07-12 / 2026-07-09; stale doc annotations inherited by the sweep,
BACKEND_RESILIENCE_DESIGN.md currency-fixed), so **the free slot =
closure-check unification (census #30)** — SHIPPED 2026-07-28: unified
at the decision layer as `director.evaluate_closure()` (deterministic
verdict→DirectorDecision mapping, zero new LLM calls); the census's
literal fold ("retire `ClosureVerdict`", 3 call sites) turned out stale
on both counts — four call sites (post-escalate re-verify was
uncounted), and the verdict carries burn-in verdict-integrity machinery
every consumer stamps from, so it survives as the evidence record on
`DirectorDecision.closure_verdict` (spec correction recorded in
ADAPTIVE_EXECUTION_DESIGN.md Trigger Point 3). Adversarial review DONE
2026-07-29 (3 Codex lenses vs d9f9968, PASS with one fix — first-try
PASS; 3/3 findings cited real code, 0 hallucinated; 1 accepted →
AST census tripwire pinning that no production caller bypasses
evaluate_closure, mutation-proven; 2 rejected as documented design.
Record: docs/history/2026-07-29-closure-unification-adversarial-review.md).
**Drift batch
ADJUDICATED same day (Jeremy)** — all four re-parked on falsifiable
reasons (GOAL_BRAIN Dormant threads + Decisions 2026-07-28): heartbeat
gate re-based on hosted-free; next-leap packaging capacity-parked with
**first claim on the next free ACTIVE slot**; thread-arch flows
as-we-go via the census-tested Remaining-pieces ledger
(THREAD_ARCHITECTURE.md L1–L5); Mage parks under the graph-memory
direction. Census follow-ups all closed — nothing from the census
awaits Jeremy anymore except the ACTIVE work itself.

### R6-E. lesson_text embeds truncated goal previews (anchoring risk) — watch-item

The one open residual of the R6 VERIFY_LEARN_ARC V4/R5 V4/V5 review
(everything else archived to BACKLOG_DONE 2026-07-27). `lesson_inject`
is ON (since 2026-07-14) so the A/B is running; first live numbers
(chunk-7 readout, 2026-07-22): with_lessons 58% (15/26) vs baseline 41%
(49/120) — directional positive, small n. Re-evaluate the goal-preview
anchoring risk once the comparison has real n; pre-optimizing the prompt
before then is guessing.

### 22. Capabilities catalog — open residuals (shipped trail archived)

Catalog + canonical Manti case + blank-slate skill set + cuts-first v0 +
session-reuse spike/prototype all SHIPPED (full trail archived to
BACKLOG_DONE 2026-07-27; canonical measurements in
`docs/CAPABILITIES.md`). Still open:

- [ ] **Errand-envelope target (~1–3 min / cents): re-measure DONE
  2026-07-29, target still open but the lever changed.** Clean live run
  (0c833432-hardy-haven): 6 steps, **7m12s** wall vs the 16m43s July-11
  baseline — the warm-pool fix landed as predicted (tight-loop
  between-call gaps 15–23s ≈ 12–15s pool + model time; 24 calls captured
  in build/calls, first live proof of the always-wrap record seam).
  Verdict on the next lever: closure plan→verdict→quality gate ran ~8s
  serial — the closure ∥ quality-gate safe pair is NOT worth building.
  The actual tail is the adversarial claim review (~57s, and it fired
  live, contesting unverified version/format/line-count claims — working
  as designed). Remaining distance to 1–3 min is step count × model
  time, i.e. plan-shape (fewer, fatter steps for errand-class goals),
  not orchestration overhead. Boot-tax prompt half + answer-first
  delivery already shipped.
- [ ] **Standing habit:** capture real asks as-phrased into
  `docs/CAPABILITIES.md` (also the CLAUDE.md capability-capture rule
  since 2026-07-11).
- [ ] **(Vision)** shared trusted skill directory + cross-instance
  learning share — see Vision section entry.

### 0. Test corpus — capture the missing layers (forward record-mode + full archive)

**Shipped 2026-06-26 (the "now" half):** `scripts/harvest_corpus.py` distills the
live workspace history (`runs/` 569 captains-log slices + `projects/`) into
deduped fixture slices under `tests/fixtures/orchestration_corpus/` (thinned
slices committed, full git-ignored + reproducible). 24 slices, 5,646 raw records;
`tests/test_orchestration_corpus.py` proves consumability + regression-guards the
quality-gate escalate formula against 122 real verdicts (0 mismatches). Workspace
data is preserved, not deleted.

**Shipped 2026-06-26 (forward record-mode + curation):**
- [x] **Forward byte-level record mode.** `FailoverAdapter.complete` now captures
  `{prompt, response, tool_events, tokens}` per call to
  `<run-dir>/build/calls/call-NNNNN.json` via `runs.record_llm_call`. One seam
  over every backend; secret-scrubbed through the new single-source
  `src/secret_scrub.py` (harvester now imports the same module — no divergence).
  **Default ON**; off via `MARO_RECORD=0` or config `record.enabled: false`
  (`runs.recording_enabled`). Future runs now yield true byte-level replay
  fixtures. Tests: `tests/test_record_mode.py`.
- [x] **Post-goal curation pass.** `src/run_curation.py` runs at goal-end (hooked
  in handle.py finalize), writes `run_card.json` (outcome class + mineable
  inventory) so the paid-for capture is parked for later mining, not discarded.
  Miner registry (v0: classify + inventory). User-visible/prunable CLI
  (`python3 -m run_curation list|show|curate|prune`). Tests:
  `tests/test_run_curation.py`.

**Remaining ("later"):**
- [x] **Mining passes on the parked data — ALL SHIPPED, stale bullet
  corrected 2026-07-13.** All four miners named here are live in
  `run_curation._CURATOR_SPECS` (9 curators total, topo-sorted since
  the R1 batch-1 fix this session): `flag_skill_candidate` (skill
  scraper, now with a real consumer — `evolver.promote_skill_candidates`,
  shipped this session), `scrape_scripts` (script scraper),
  `index_decision_prior` (decision-prior indexer — the owner-ask half,
  Jeremy 2026-07-04 "task failures being retried, with the old task
  context available"), `rescue_partial` (partial-run rescue).
- [x] **Unify rung-4 step I/O** — DONE 2026-07-04. `FailoverAdapter.complete`
  stamps the record path onto the response (`resp.call_record`) when
  `record_llm_call` captures; `execute_step` carries it on the outcome dict;
  `StepOutcome.call_record` threads it through all construction sites (main
  loop, stuck-repeat, parallel batch + fan-out); `_write_loop_log` emits it
  per step. The loop view's truncated excerpt now links straight to
  `<run-dir>/build/calls/call-NNNNN.json`. 4 tests in test_record_mode.py.
- [ ] **Full raw archive (optional).** If/when `runs/`+`projects/` (~79M) get
  pruned, snapshot the full (non-thinned) slices somewhere durable first — they're
  only reproducible while the workspace exists.
- [x] **Wire more slices into real tests** — DONE 2026-07-03. Five replay
  tests added to tests/test_orchestration_corpus.py: too-broad breach
  conjunction (113 recs, floor-division boundary documented), metacognitive
  convergence-heuristic tail replayed against 281 recorded decisions (0
  mismatches; diagnosis-path out of scope — events don't carry loop state),
  claim-verifier outcome/action pairing, diagnosis subjects pinned to the
  current FAILURE_CLASSES taxonomy, closure-verdict internal consistency +
  proof the BACKLOG #5 restart predicate discriminates on real history.

### 1. Bound worker writes — residual: Bash write shapes the fence can't see

**Shipped arc archived 2026-07-04** — the full write-fence history (cwd
root-cause + soft fence, projectless-run fence hole, scavenge detection,
cwd-drift tracking, tier-a demotion, Jeremy's enable + same-day narrowing
with `/tmp` + goal-declared roots) lives in BACKLOG_DONE.md ("BACKLOG #1:
write fence — shipped arc") and `docs/BOUNDED_WORKSPACE.md`.

- [ ] **Residual: Bash write shapes the regex can't see** — `cp`/`mv`/`sed -i`
  targets, subshell/pushd cds stay invisible to `detect_out_of_fence_access`
  (documented in `docs/BOUNDED_WORKSPACE.md` known holes). Extend from real
  `SCAVENGE_DETECTED` evidence, not speculation. Current state: detection
  always-on (`validate.scavenge_detect`), enforcement **code-default ON
  since 2026-07-09** (1.0 posture flip; this box had run it enabled since
  2026-07-04 with no false positives — opt out via
  `validate.write_fence: false`, see docs/DEFAULTS.md). Reads stay
  unrestricted by design (logged, not blocked); a read-restricting mode
  remains possible if scavenge read rows ever show real contamination.
  **Evidence refresh 2026-07-14:** searched the unified workspace, run slices,
  repo experiment output, committed corpus, and pre-unification workspace
  locations. No `SCAVENGE_DETECTED` / `FENCE_WRITE_BLOCKED` row or recorded
  `cp`/`mv`/`sed -i` miss survives. The only concrete historical evasion is
  run `668e46d1`'s `cd` + relative redirect, already fixed and regression-pinned
  in `TestScavengeCwdDrift`. Leave this residual evidence-gated; do not add
  speculative shell parsing until a real missed transcript lands.

### 17. Run-visibility residuals (2026-07-09 real-data review)

All four original sub-items shipped 2026-07-09 (two concurrent sessions —
see BACKLOG_DONE for both): contextvar loop_id threading + purpose stamping
(this session), live-report post-curation refresh + NOW-lane mini-reports
(concurrent session, superseding this session's own narrower post-curation
refresh attempt — see BACKLOG_DONE for the reconciliation note).

- [x] **Goal search in the run visualization — SHIPPED 2026-07-13** (Jeremy,
  2026-07-10, rider on the retention decree): `maro viz search` filters run
  summaries by goal text / lane / status / date, plus a client-side filter
  bar in the HTML index. See BACKLOG_DONE for full detail.
- [ ] **Index rebuild is O(all runs) at every finalize** (~277ms at 668
  dirs, via the post-curation hook). Fine now; revisit around ~10k run dirs
  (incremental index, or rebuild only on viz/backfill).

---

## Vision / Deferred

### Verbal UX for orchestration (2026-07-28, Jeremy — revisit trigger named)

Jeremy, during the compound-thinking spitfire: "we sort of started
hand-waving at a verbal UX for all of this. If we don't have a backlog
item to revisit this (later, after we start seeing larger successes
more often), maybe as part of optimization when real time makes more
sense." No such item existed — this is it. Not a build item: revisit
when (a) larger successes are landing often enough that interaction
cadence, not capability, is the bottleneck, or (b) a real-time
optimization pass makes latency work worthwhile anyway. Prior art
touchpoint: GOAL_BRAIN:2174's note that voice UX masks planning
latency. Ties to the escalation-surface decree (the substrate
go-between IS the surface) — a verbal surface would be another face of
the same go-between, not a new channel architecture.

### Two small refactors from the 2026-07-18 adversarial review (low, grab when nearby)

- **NOW-lane prompt assembly is duplicated** between `handle._run_now` and
  `conductor._handle_now_lane` (both: enrich_step_with_urls + link-read
  system suffix + degrade-on-failure). Two copies is at the repo's
  3-wants-extraction edge; if a third NOW surface appears or the enrichment
  pattern changes again, extract a shared pure builder (structured state in,
  (system, user) out) into web_fetch or a small now_lane module.
- ~~**`intent.classify()`'s widening tuple wants a dataclass.**~~ SHIPPED
  2026-07-29 (autonomous batch): frozen `ClassifyResult` dataclass with the
  five named routing facts; `classify()` returns it on all four paths,
  handle/conductor read attributes, all test unpacks + mock patches
  migrated. Deliberately NOT iterable (stale tuple-unpack fails loudly —
  pinned in test_classify_returns_named_result). Bonus: `needs_live_data`
  is now exposed to callers instead of being swallowed inside classify(),
  and the file-output override no longer drops it. Side-find while
  migrating: `persona.py:412` imports nonexistent `intent.classify_intent`
  → always excepts → `task_type` template var is permanently "general" —
  this CONFIRMS the wiring-inventory "persona template seam dormant" claim
  (item in the 8-claim verify list).

### Time blindness — LLMs don't experience ideas over time (2026-07-11, Jeremy)

Jeremy (closing theme, verbatim): "humans perceive stories and ideas
over time (as we experience them) and LLMs... don't. That's a
communication blind spot. We might need to fight some kind of time
blindness between prompts, even in the same session, I think it's
getting worse rather than better here and there, and sometimes it
matters a lot."

No concrete goal yet — recorded well per his ask. Candidate starting
hooks when this gets a session: (a) age-stamp injected evidence and
recalled context (a lesson from February reads identically to one from
yesterday today — staleness is invisible to the model); (b) elapsed-
time awareness between steps and between sessions (the run knows wall
clock; the model is never told "your last step was 40 minutes ago" or
"this thread went quiet for 3 days"); (c) ordering/decay in recall —
dev-recall and memory injection currently rank by relevance, with time
as a hidden variable; (d) the captain's-log slice already carries
timestamps — surfacing them *into prompts* is cheap and measurable.
Related evidence: the godot retrospective (agenda-state divergence over
a long session = time blindness inside one session), stale-source
dissent handling in the Manti runs (the system already fights data
staleness — this extends the fight to its own conversational state).

**First slice — SHIPPED 2026-07-15** (vehicle added 2026-07-12 handoff
audit; hooks (d)+(a) only). What shipped: `src/age_stamp.py` +
`memory.age_stamps` flag (hardcoded default OFF; byte-identical
off-path test-enforced, worker_slice pattern); age suffixes at the
three seams — worker slice (`memory_bridge.stamp_items_with_age` +
director wiring), recall() loop slice, `inject_lessons_for_task` (the
decompose seam turned out to BE recall()'s loop slice — seams b/c
converge); `[time]` elapsed-gap line (≥10 min module constant) via the
contribution ledger; `age_stamped: true` on WORKER_SLICE_INJECTED /
RECALL_PERFORMED only-when-stamped. Adversarial review (FIX_FIRST →
fixed same pass): legacy rows missing `recorded_at` no longer fabricate
a load-time "learned today" stamp (absence preserved as `""` at BOTH
raw-row loaders — dedup previously froze fabricated dates to disk);
time contributions are recomputed-never-replayed
(`ContributionLedger.drop_source` at both drain points — the re-arm
paths replay every other source correctly); batch-boundary monotonic
capture pinned (mutant killed); future timestamps render date-only, no
"ago" claim. Eyeballed on real box data (273 lessons, all dated) →
flag ON this box per the vehicle. Natural second slice: the graveyard
block in recall() ("resurrected from decay") injects with no age.
Hooks (b) full elapsed-time model — incl. cross-process resume gaps
("this run was interrupted 3 days ago"; checkpoint knows
`in_flight`/`started_at`, the monotonic capture is in-process only) —
and (c) time-aware recall *ranking* stay in this vision entry — (c)
especially must not ship as a silent relevance-formula change; it's a
verify→learn-adjacent measurement question.

### Perspective / camera rotation — bringing the human lens functionally (2026-07-11, Jeremy)

Jeremy (closing theme, verbatim): "I've talked about rotation, and
zooming in and out for seasoned developers. That's really just
re-framing and adjustment of perspective (from a game engine camera
type perspective), and I think the same holds true for ideas. LLMs have
ridiculous access to data, language and information. But the
perspective isn't the same at all. We need to help bring the 'human'
perspective, both innate and skilled usage of, into things at least in
a more functional light... Watching you react to seeing the
orchestration finding some of the perspective that is much more easily
discoverable from an end-user perspective makes me happy -- we're
getting there to a degree, but I'd like to refine that."

Standing direction, not a task. Constraints already on record: fixes
belong in inference moves (scope, memory, inversion, rotation), NOT
prompt taxonomies (feedback_inference_not_prompting); cuts-first
planning is the first shipped rotation-like move (narrowing = zoom).
What "refine" plausibly means next: (a) named lens/rotation moves the
planner or navigator can *choose* (invert, zoom-out-to-goal,
zoom-in-to-specimen, end-user-seat) the way draw_cuts chooses probes;
(b) the corpus arc as evidence — the end-user perspective (what a
person actually asked, what they actually got) surfaced failure
patterns that code-side inspection never would; institutionalize that
seat in review/verify stages; (c) ties to the "are we re-inventing
reasoning-model behavior?" open question — same kill-test posture
applies to any lens machinery.

**First slice — VEHICLE (added 2026-07-12 handoff audit; deliberately
the smallest honest step, per the kill-test posture):** the end-user
seat (b) only, because it's the one lens with live evidence behind it.
Concretely: closure verification and adversarial review each gain an
explicit end-user-seat pass — "answer as the person who asked: did I
get what I asked for, in the form I could actually use?" — as a named
section of the existing prompts (no new LLM call, no lens registry).
The Manti NOW failure is the canonical specimen (a *where* question
answered with no *where*); the shipped `_NOW_VERIFY_SYSTEM` non-answer
judging (a1f472f) is the pattern to generalize. Acceptance: on the
existing dogfood/closure corpus, the seat catches ≥1 real miss that
current checks pass (candidate: run 315ebffb's on-topic-but-wrong-
subject haiku) without new false demotions. Named lens *moves* for the
planner/navigator (a) stay vision — they must survive the kill-test
("would the same-tier model with identical context do this unprompted?")
before earning machinery.

### Learning-trust maintenance — "usage only" vs "learning" sessions (vision, 2026-07-12, Jeremy)

Jeremy (closing the container-executor design conversation, verbatim —
full quote in GOAL_BRAIN Decisions 2026-07-12): skill poisoning and
self-learning edges, "same sort of thing in both directions... ultimately
we will likely need 'usage only' vs 'learning' sessions, the ideal being
scanning and auto-upgrades by the system itself, which is a neverending
quest, same as a virus scanner solving protecting an individual
workstation; one way to solve it but a constant maintenance headache.
We'll get into all that more later."

Direction, not design. Doors already built when this gets a session:

- **Usage-only mode is a flag, not an architecture:** the learning
  ingestion side is already gateable per-run — `defer_learning`
  (loop_types/handle), the crystallization gates in finalize, the
  skills-lite promotion switch (`skills.lite_promotion`). A
  `learning: off` session = those seams held closed for the run, artifacts
  still produced, nothing ingested. Cheap when wanted.
- **The scanning/auto-upgrade half** is the verify→learn arc's
  expectation→verdict→demote lifecycle pointed at learned artifacts —
  VERIFY_LEARN_ARC.md V2 (cadence verdicts + auto-revert) and V3
  (graduation class-specific auto-verify), both SHIPPED 2026-07-14, are the seed
  machinery; the virus-scanner
  analogy's "constant maintenance" is exactly why it must ride existing
  cadence hooks, never a daemon.
- **Trust boundaries already on record:** imports arrive contested
  (PORTABLE_LEARNING_DESIGN §8, ratified), skills-lite injection_guard +
  quarantine (cs-r2-01 family), never-auto-adopt. The both-directions
  concern (poisoned learning leaking OUT too) is the export half —
  `secret_scrub` + pack sealing cover the mechanical side; content-level
  export scanning is unaddressed and belongs to this item.

### Shared trusted skill directory + cross-instance learning (vision, 2026-07-10, Jeremy)

"Maybe a shared and trusted directory to pull from at a later time,
crowd-sourced or not" — the blank-slate pre-installed set (item 22) is the
seed; sharing is the scale-out. Ties directly to "sharing learning across
instances" as a later feature: skills are the portable unit today
(`maro-import` already merges with quarantine + provenance), lessons ride
the same rails later. Trust boundary notes recorded in CAPABILITIES.md so
we don't relearn them: a shared directory is a supply chain (cs-r2-01's
threat model at internet scale) — provenance required, same
injection/dangerous-pattern gates as skills-lite, imports arrive as
reviewable candidates never auto-trusted. Direction, not design; wants its
own pass when it ships publicly.

**Refined 2026-07-11 (Jeremy, after the social_search arc):** "an opt-in
brain for the users of this orchestration, to share knowledge and skills;
sourced, with pedigree, maro-graduated and proven skills only, and only
opt-in overall from the user's standpoint, the sharing and details are
maro-as-clients talking to a coordination server. But that's for later."
Architecture sketch this adds to the 07-10 direction: client-server (a
coordination server, not peer-to-peer), pedigree/provenance as a
first-class field, the graduation machinery as the quality gate (only
maro-graduated skills are shareable), and opt-in as a hard product
stance — default is fully local. Trigger insight was the Reddit/X access
recipes: platform-access knowledge is exactly the kind of
expensive-to-discover, cheap-to-share, decays-over-time artifact a
coordination brain is for. Still later; still direction, not design.

### Post-Purgatorio decision batch (2026-07-09, Jeremy — quotes in GOAL_BRAIN Decisions)

- [x] **Skills-lite two-tier promotion — SHIPPED 2026-07-10.** Jeremy rider
  on the graduation precedent: "we want things promoted to skills that the
  local orchestration can pick up and use while waiting for user review...
  looked at as skills-lite, and degraded the same as regular skills that
  get broken or stop working." Implementation:
  `run_curation.promote_skills_lite` (new curator, also the first BACKLOG
  #0 miner — the skill scraper): skill-shaped .md artifacts (frontmatter
  name+description+triggers/roles) from successful runs (success /
  done-unverified only; done-not-achieved excluded) copy into the
  workspace skills overlay stamped `tier: skills-lite` +
  `promoted_from: <handle_id>` — skill_loader injects them immediately.
  Each promotion registers a companion provisional Skill in skills.jsonl
  so the normal stats/decay/circuit-breaker machinery tracks it;
  `degrade_skills_lite()` quarantines the .md to `skills/_quarantine/`
  when the companion trips (circuit open) or vanishes (gc/culled) — the
  "degraded the same as regular skills" half, riding the exact demote
  signals. Fail-closed: sandbox `_DANGEROUS_PATTERNS` scan (first real
  consumer of that lane), never overwrites an existing skill name, unsafe/
  colliding candidates recorded as skipped on the run card
  (`card["skills_lite"]`) so they surface for human review. Provenance
  sidecars (create/demote) via `write_skill_provenance`. Config
  `skills.lite_promotion` default ON by decree (docs/DEFAULTS.md row;
  census green). 10 tests in tests/test_run_curation.py; live no-op smoke
  on the real workspace (no false-positive promotions on re-curation).
  SF-10 demand side: companion Skills enter the funnel with real
  trigger_patterns, so promotion pressure now has a source.
- [ ] **Official scheduler/timer layer (later; auto-resume rides it — session-protocol arc raises its priority, see SP entry).**
  Jeremy: "maybe we need a more general official scheduler/timer that the
  user can hook into/see/manage if they wish." A visible, user-managed
  timer surface (list/inspect/disable) — coexists with the no-cron
  invariant, which bans *hidden self-rearming* schedules, not an official
  transparent one. Auto-resume of interrupted runs ((h) deferred half)
  becomes this layer's first consumer; heartbeat scheduling may too,
  pending the SF-1 supervision decision.
- [x] **Migrate the two remaining repo-copy user/ readers to
  `config.user_file()` — DONE 2026-07-10.** All three call sites
  (`handle._load_user_config`, handle's COMPLETION_STANDARD injection,
  heartbeat's mcp_servers read) now resolve workspace-overlay-first via
  `config.user_file()`; a workspace `user/CONFIG.md` /
  `COMPLETION_STANDARD.md` is honored everywhere. user/README.md caveat
  removed, DEFAULTS.md lane note updated. Test:
  `test_load_user_config_reads_workspace_overlay` (tests/test_config.py).
- [x] **Orphan scope A/B datasets — WRITTEN OFF 2026-07-12 (Jeremy,
  handoff decision batch; closes arch-03).** `~/.maro/experiments/
  scope-ab-2026-04-25-v0/` and `scope-ab-2026-04-26-v1/` hold full PAID
  treat/control run dirs with no ANALYSIS.md. Explicitly written off: the
  inject decision was already made on the 2026-04-22 evidence and shipped
  (SF-4 flip). Spend acknowledged, not silently forgotten; data stays on
  disk per the retention decree if a future analysis wants it.
- [ ] **Knowledge-web read side: wire it properly (later, KEEP) — TRACED
  2026-07-13, premise was wrong, real prerequisite work identified before
  any read-side code can pay off.** Descoped from 1.0 docs (node store +
  BM25 is the honest claim) but explicitly kept: "I'd like to keep it on
  the list. I think it could be really powerful if done well (and right
  now sounds like it isn't)." That instinct was correct — traced the real
  `~/.maro/workspace/memory/knowledge_{nodes,edges}.jsonl` data before
  writing any code, and the backlog's own framing ("write side + 2124
  edges exist; read side has zero callers") undersold the actual gap:

  - **All 2124 edges connect only `lf-` (link-farm import) nodes to other
    `lf-` nodes — zero edges touch the 252 real, system-authored
    orchestration nodes** (insight/pattern/principle/technique/tool). Not
    a sampling artifact — checked exhaustively: 2124 both-lf, 0 both-real,
    0 mixed. `build_wiki_link_edges` (the only code that could produce a
    `related` edge with two node-id endpoints) has **zero production
    callers** — these edges came from whatever process bulk-imported the
    link-farm content, not from anything in `src/`.
  - **Zero of the 252 real nodes' descriptions contain `[[wiki-link]]`
    markup** — checked every one. `build_wiki_link_edges` would produce
    zero edges even if run against them today; nobody authors nodes with
    that convention, so the mechanism the read side was meant to traverse
    is dead on the write side that actually matters.
  - Net effect: wiring `load_knowledge_edges` into
    `inject_knowledge_for_goal` as originally conceived (walk a matched
    node's edges, inject adjacent nodes) would do **nothing** for a real
    goal (the orchestration knowledge that matters has no edges to walk),
    or — if scoped to all nodes including `lf-` — would inject arbitrary
    link-farm co-occurrence pairs (e.g. a financial candlestick model
    linked to a genome-sequencing tweet linked to an open-source research
    agent, weight uniformly 0.5, "related" for everything) into goal
    context as if they were meaningfully connected. That's noise, not the
    "Correspondence" payoff — exactly what Jeremy's instinct flagged.
  - **Separate, smaller pre-existing note (not this item's blocker, just
    surfaced by the same trace):** `inject_knowledge_for_goal`'s existing
    TF-IDF node query (`domain=None` from `recall.py`) already ranks
    `lf-` link-farm nodes in the same domains as real orchestration
    insights (`orchestration`, `tooling`, etc.) — a goal could already be
    injected raw curated-tweet content today if it scores well, independent
    of edges. Not investigated further; flagging so it isn't
    re-discovered as a surprise later.

  **Fix direction, in order:** (1) decide whether `lf-` link-farm nodes
  should ever inform live goal execution at all, or should be a
  read-only reference corpus queried separately (a domain/tag exclusion
  in `query_knowledge` is a 2-line fix once decided); (2) if adjacent-
  knowledge retrieval over the *real* knowledge base is still wanted, build
  an actual edge-generation mechanism for it — since manual `[[wiki-link]]`
  authoring isn't a convention anyone follows, the realistic option is an
  LLM-assisted "does this new node relate to an existing one" pass at
  node-creation/crystallization time (same shape as the skill_candidate
  catch-up sweep shipped this session), not the existing regex-only
  `build_wiki_link_edges`; (3) only then does wiring `load_knowledge_edges`
  into injection have real signal to traverse. Left as `[ ]` — this is a
  design decision (what should the graph even encode) before it's an
  engineering task, not something to improvise past Jeremy's own stated
  uncertainty about doing it well.

### Graph memory + recursive-orchestration scoped memory (2026-06-21, vision)

**RESOLVED 2026-07-07/08 — this entry was stale until 2026-07-09.** Direction
decided 2026-07-07 (memory becomes a module; see GOAL_BRAIN.md Decisions),
bake-off same day picked a self-built sqlite3+FTS5 adapter over TencentDB
Agent Memory / Mem0 / Zep-Graphiti (`docs/history/2026-07-07-memory-bakeoff.md`),
shipped same day (`src/memory_sqlite.py`). Worker-recall-slice §7 A/B completed
2026-07-08 (16 clean runs, every measure favors the slice or ties) and Jeremy
flipped it on as the hardcoded default (`memory.worker_slice`, see
`docs/DEFAULTS.md` and `docs/history/2026-07-08-worker-slice-ab.md`). Original
brief: `docs/history/2026-07-04-memory-decision-brief.md`. **One residual not
yet decided:** the fastembed+sqlite-vec semantic lane is still gated behind
"only if BM25 measures insufficient" — full-corpus verdict (1,652 items, see
GOAL_BRAIN.md 2026-07-07/08 entries) showed sqlite-fts5 wins hit@1 + 5×
latency but loses hit@5/MRR to token-overlap; whether that's "insufficient"
enough to build the semantic lane is unmeasured/undecided. (2026-07-09
review: confirmed stays-gated, nothing blocked on it — revisit only when
organic worker-slice retrieval misses surface, with the paraphrase-lane
numbers as the evidence file.)

Durable replacement for the fixed-size inter-step truncation caps (the 800/500/200 band-aids
above — lossy fixed-array-vs-string, the kind of thing that's bitten us). Jeremy's framing:
orchestration is likely "recursive — orchestration all the way down," so a memory layer must
support **scoped/hierarchical** access — a sub-agent reads its own scope PLUS the higher
orchestration scope, built generically enough to serve both. Pairs with CAG-style caching so
sub-agents lever cached static context instead of re-ingesting. See memory
`project_retrieval_graph_memory_direction` + `project_recursive_orchestration_memory`.
NOTE: this replaces the *caps*, not the token-explosion *leak* — justify it on its own merits
(truncation is a band-aid), not on the 485K number. Ties to hybrid-retrieval priority
(start BM25+embedding, SQLite adjacency, not Neo4j until thousands of nodes).
Input from docs refactor (2026-07-04): dev-recall (`correspondence.py`) turned out to be
pure FTS5/BM25 — no embeddings ever existed despite the old "sqlite-vec" docstring — and it
had silently indexed a pre-rename ghost clone for 7 weeks (fixed: pruned + full re-ingest).
Two lessons for this design: (a) the "hybrid" in hybrid retrieval is still 100% unbuilt,
BM25 alone is what we run on today; (b) any index needs a staleness/provenance check
(sources-on-disk assertion) or it rots invisibly. `lat.md/` + `lat_inject.py` fate also
folds into this decision (see docs/INDEX.md note).

Fresh evidence + decree (2026-07-29, token-cap decree — see GOAL_BRAIN): run
ba58f96c (third self-diagnosis dispatch) step 3 asked one agentic call to
digest a 292KB session transcript and got liveness-killed at 469s; steps 1–2
had already burned 1.7M tokens re-reading prior-run jsonls. Jeremy: "stop
trying to manage those [token caps] up front and start trying to be more
clever about what we pull in from a large doc." That is THIS item: a
retrieval handle over large artifacts (read the slice you need, not the file)
is the fix; the cap-side half (uniform runaway ceiling, no per-call magic
numbers) already shipped in the same-day cap-and-kill-reason chunk.
Accepted residual (Jeremy, same day): the 16000 ceiling is itself a magic
number — "fixing magic numbers with magic numbers … we will likely still
have to revisit that later." Revisit lands here: when the retrieval handle
exists, the right ceiling question becomes measurable (observed no_tools
output distribution) instead of guessed.

### Design constraint: decay trust, never data

- [x] **Retention-decree audit — 3 violations found and FIXED 2026-07-10**
  (Jeremy: "let's fix, no time like the present"). Sweep of every deletion
  site in src/ against the retention decree found the same auto-deletion
  family the step-artifact bug belonged to:
  1. **Lesson decay-GC deleted memory** (the path that once ate the whole
     38-lesson MEDIUM store): `run_decay_cycle` + `gc_memory` now archive
     to `memory/lessons_archive.jsonl` before dropping from the live store;
     `search_graveyard` reaches the archive and resurrects via
     `resurrect_archived_lesson()`; `forget_lesson` archives as
     `user_forget` (excluded from auto-resurrection — forgetting is the
     user's call); `maro-knowledge` stats surface the archived count.
  2. **Skill island culls + A/B variant retirement hard-deleted skills**:
     both now archive to `memory/skills_archive.jsonl` + write a `retire`
     provenance record.
  3. **Finalize deleted the checkpoint on done**, but closure verification
     runs after finalize — a run demoted done→incomplete had already lost
     its resume state. Checkpoints now kept on completion (stranded-sweep
     already skips finalized runs via metadata status; `checkpoint delete`
     CLI remains the user-level removal path).
  Enforcement so the class can't recur silently:
  **tests/test_no_silent_deletion.py** — AST census of every file-deletion
  call in src/ (unlink/rmtree/os.remove/rmdir incl. aliased/bare-import
  forms) against a justified allowlist, plus a pin that nothing outside
  checkpoint.py references `delete_checkpoint`. Same pattern as the
  DEFAULTS.md census tripwire. Known limit: record-level rewrites aren't
  generically detectable — the two known record-level deleters are the
  ones fixed above, pinned by unit tests.

- [x] **Graduation-specific behavioral verification (VERIFY_LEARN_ARC V3) —
  SHIPPED 2026-07-14.** V1 (expectation stamping), V2 (cadence verdicts +
  auto-revert), and V3 all landed the same day. `verify_applied_suggestions()`
  now verdicts an applied graduation row on its *own* `failure_class_rate`
  (per-class rate over timestamped-diagnosis windows — diagnoses gained a
  `recorded_at` stamp), instead of the class-neutral global stuck-rate on which
  a single class is noise (why graduation rows only ever parked `unverifiable`).
  Falls back to the stuck-rate when class windows are thin. Lifecycle +
  symmetric authority reused from V2; structural `verify_pattern` stays pure
  observability. Knob `evolver.verify_use_class_signal` (default ON).
  **Deliberately NOT built (owner call, Jeremy 2026-07-14): autonomous *apply*
  of graduation rows.** Graduation stays advisor-gated — a human applies via
  `maro evolver apply`; nothing auto-applies a standing rule, so a degraded
  graduation row surfaces for review (never auto-reverted). This is a safety
  posture, not a gap; revisit only if the auto-apply-threshold owner call
  changes. (The live workspace still has no *applied* `graduation:` suggestions
  to verdict yet, but the class-rate time axis is live: `_load_dated_diagnoses`
  now recovers 1274/1277 real diagnoses via the events-log join, so the metric
  is exercisable on real data the moment a graduation row is applied.)

- [ ] **Design constraint, not a task: decay trust, never data.** Append-only
  evidence layer stays perfect (the computerization edge over human forgetting);
  only compiled-truth confidence decays. Crystallization Stages 4–5 must be
  demotable back to language form — world-change is the frequent trigger,
  model upgrades the rare one. Partially embodied: "Decay-by-invalidation v0"
  (`knowledge_lens.py`, 2026-06-11) decays Stage-5 rule *trust* on recorded
  contradictions without touching data — but `knowledge.py` demotion only goes
  Stage 5→Stage 4 (rules→skills), NOT back to language form (Stage 2/3). The
  language-form demotion path is the part still open. Input to the memory
  architecture decision.

### File-claim fabrication — residuals (v1 guards SHIPPED 2026-06-26, archived to BACKLOG_DONE)

The three shipped layers (FS-diff missing-artifact, inert-output AST,
execution-contradiction) moved to BACKLOG_DONE 2026-07-04; the guard lives in
`loop_execute.py` post-split (originally wired in agent_loop.py). Kept here:
the rejected design (a documented trap) and the deliberately-deferred shapes.

- **REJECTED: no-path-write layer.** Prototyped (write-ish words + empty diff +
  no path named) and reverted same day: it is **absence-based, not
  evidence-based** — an empty workspace diff does not prove fabrication
  (analysis/planning steps and out-of-workspace writes legitimately leave it
  empty). It false-positived on 4 real test completions in the full suite. A
  verifier that hallucinates is its own failure mode; the guard now only fires on
  positive evidence (a named-but-absent file, or an inert file vs a concrete
  output claim).

- [ ] **Remaining exec-fabrication shapes (deliberately deferred — false-positive
  risk).** Two cases the v1 contradiction check intentionally does NOT flag,
  because each can fire on legitimate runs (same lesson that killed the
  no-path-write layer): (a) **"claims execution but ran nothing"** — the per-step
  transcript can't see a prior step's legitimate run, so absence ≠ proof; (b)
  **partial** — some commands succeeded and a later/key one failed; telling the
  test command from setup needs intent modeling, and fix-then-succeed is
  legitimate. Revisit only with a sharper signal (e.g. matching the claimed test
  count against the real `tool_result`), not a looser gate.

### Step-skeleton parallelization — refinable contracts, reopen-as-normal (vision, 2026-07-28, Jeremy)

Jeremy, verbatim: "For maro I envision a point where we parallelize
chunks of work that are all step-shaped (rather than what I think is a
super linear path we are currently taking through everything). Router
maybe needs to be smarter in order to do that; there's a front loading
component there and a 'redo but with re-shaping' context there as well
when we find step dependencies and need to revisit/refine 'finished'
step work. Which feels like a different way to organize/approach a
sidequest really, so maybe there's something there."

Reading (agreed in-session): decompose emits a **contract skeleton** —
step stubs with broad-brush contracts that harden as work proceeds
(mirrors Jeremy's stub-then-spiral dev workflow) — and reopening a
"finished" step on a discovered dependency is a **normal move, not a
failure**. Hooks already shipped: §13b revisitable milestones (stop
verdicts carry evidence + reopen conditions), §13d side-quest DAG
proposal, goal ancestry, recursion decree (sub-goal spawning never
foreclosed). Belongs to the thread-architecture implementation arc —
design input, not a build item yet. Pairs with the Actionable Stack
"Open-thread structure" entry: same shape, built twice deliberately
(dev-lane prototype first, runtime second).

### DESIGN SPACE — Thread Architecture (2026-04-26 sketch; narrow navigator SHIPPED, full reframe unbuilt)

**Doc:** `docs/THREAD_ARCHITECTURE.md` (the sketch + decisions + open list)
**Conversation log:** `docs/conversations/2026-04-26-thread-architecture.md` (literal transcript)
(The `arch/thread-navigator` branch was merged to main via 131d629 and deleted — no separate branch anymore.)

The 1-shot-first DISCUSS item (formerly here) expanded into a full architectural sketch over a 7-turn planning conversation. Rather than just inverting the planning default, the conversation reframed the unit of orchestration to **thread**, with a per-turn `navigator → work → navigator` loop, navigator-selected personas, sub-thread fork/collate, build-folder-as-thread-residence, and crystallization (Stages 1–5) as the navigator's improvement path.

**Status 2026-07-04 — distinguish shipped from unbuilt.** The *narrow* navigator
is real and live: dispatch + blocked-step judge (`navigator_shadow.py`), per-thread
goal brain (`thread_brain.py`), escalate cutovers enacted on this box (MILESTONES
#1/#2), thread-brain maintenance closed (MILESTONES #3). The *full reframe* —
per-turn navigator→work→navigator loop, sub-thread fork/collate (gated on
MILESTONES #4 async fork join), navigator-selected personas per turn, thread as
the unit of orchestration — is NOT built. The 9 open decisions in the doc need
re-scoping against what shipped before any further implementation.

**1-shot-first** is preserved as one move-shape the navigator picks per turn (not the default; navigator decides whether to plan or execute). Existing planning scaffolding (`decomposition_too_broad`, mid-loop redecompose, scope-as-armor) probably shrinks but does not delete — Jeremy pushed back on aggressive deletion (Tesla-vs-driver: confident-sounding LLM ideas without critical-thinking-edges drift, because people's context ≠ LLM context).

**Adjacent items that should be re-evaluated under this frame** (2026-07-04: two struck as shipped):
- Intent resolution (next entry) — folds into "fork+collate" sub-thread mechanism
- Captain's log infrastructure-vs-visibility (new) — should be demoted to data, not infrastructure
- ~~Persona auto-selection~~ — SHIPPED (`persona_for_goal`, c964d3b; wired in conductor + handle)
- ~~Recall() interface~~ — SHIPPED (`src/recall.py`, 9f1a43a)
- Crystallization Stage 5 (existing gap in `KNOWLEDGE_CRYSTALLIZATION.md`) — the navigator's cheaper-over-time mechanism
- Shared-learning portability (new) — self-learned artifacts should survive HDD loss / orchestrator switch

**Part 2 — Compound-thinking review (added 2026-07-20, Jeremy).** Re-review the
thread/navigator design from the practical question: does the system choose the
smallest useful shape of work, or does it turn a simple ask into a large
orchestration ritual? The target is not brute-force decomposition or a new
"reasoning model." It is an external cognitive architecture that can: (a) keep a
true one-shot as a one-shot; (b) split genuinely independent questions into
small, concurrent, independently verifiable children; (c) sequence only real
dependencies; and (d) preserve/merge the resulting evidence without losing the
user's context.

- **Review the current move set** (`execute`, `fork`, `collate`, `extend`,
  `close`, `escalate`) and identify the minimum decision inputs and output
  contracts needed to distinguish one-shot / fan-out / sequence / monitor.
  Do not add an always-plan phase or a generic task taxonomy.
- **Use organic acceptance cases**, including the Manti non-ethanol-gas ask,
  link-farm triage ("is this worth my time?"), a bounded code change
  (inspect → change → test → verify), and a genuinely parallel comparison.
  Compare the chosen shape against a direct one-shot baseline on usefulness,
  wall time, cost, and whether the final answer contains the requested result.
- **Concurrency is conditional, not aspirational:** fan out only when children
  have independent inputs/side effects and a defined collation artifact.
  Each child must leave inspectable evidence; a failed child returns its
  partial result plus its exact blocker, not a generic planner failure.
- **Keep the human context as a first-class input.** The work-shape decision
  must preserve stated outcome, constraints, prior thread artifacts, and user
  preferences; decomposition is a tool for reducing work, not a license to
  replace the user's intent with a planner's abstraction.
- **Bitter Lesson posture:** cite `docs/history/2026-03-30-bitter-lesson-analysis.md`
  as a historical design lens, but do not treat it as a deletion mandate. Its
  earlier suggestion that parallel fan-out/locks are merely "how" needs
  reconciliation with observed cases: lightweight harnessing, durable context,
  boundaries, and verifiable collation may increase usable model capability.
  The review should retain only mechanisms that beat the one-shot baseline on
  real tasks, and explicitly remove or demote mechanisms that do not.

**Deliverable:** a short decision memo that updates
`docs/THREAD_ARCHITECTURE.md` only after the organic-case review: retained work
shapes, rejected/removed ceremony, required evidence contracts, and the first
small implementation or measurement slice. No broad rewrite before that memo.

### Intent resolution — naming the "side-quests before decompose" shape (discovered 2026-04-18)

Run 7 of slycrel-go surfaced (again) that "done" means "the plan we guessed
up front got executed," not "the goal's artifact exists." The server was
built. The browser client wasn't — and the prompt explicitly said "browser
as a client." Closure missed it because closure checks against the plan's
deliverable list, and the plan's deliverable list was itself a 1-shot guess.

We keep writing pieces that nibble at this (`scope.py`, closure,
inversion, ralph, director-restart) and stopping there. The structural
phase missing is: **delay decomposition until intent-resolution
side-quests have settled the unknowns.** See
`docs/INTENT_RESOLUTION_DESIGN.md` for the full sketch + the minimum
experiment proposal.

**Partially shipped (2026-04-23, ResolvedIntent v0):** the deliverable-map
prompt + resolved-intent artifact schema subs moved to BACKLOG_DONE — shipped
as `scope.py` ResolvedIntent/Deliverable + `generate_resolved_intent()`,
persisting `resolved_intent.md`. Side-quest orchestration remains open.

- [x] **Minimum experiment — RESOLVED 2026-07-09 (Jeremy): accept v0 on
  organic evidence, retroactive A/B dropped.** The done-vs-achieved corpus
  analysis (1.0 arc) is the cheaper honest check on the closure ceiling.
  Full context in BACKLOG_DONE.
- [ ] **Pivot reuse across goal-family reruns.** (Narrowed 2026-07-04: the
  infrastructure half exists — per-project persistent dirs under
  `~/.maro/workspace/projects/<slug>/` are live and goal-slug-bound. What's
  missing is the *reuse* logic: a rerun/rephrase of the same goal family
  neither detects nor feeds prior side-quest artifacts back as context. The
  `polymarket-edges` ledger pattern (project_polymarket_edges.md memory) is
  the proof of value; generalize that.)
  **Deterministic project-family reuse shipped 2026-07-14:** every full AGENDA
  run now persists its resolved project in run metadata; dispatch/loop recall
  treats the same project as a family match even when the rephrase has low word
  overlap, and injects a bounded inventory of durable project/artifact paths so
  the planner can inspect and reuse prior side-quest products. Literal project
  names (for example `polymarket-edges`) already bind project-less dispatches
  through `_match_existing_project`. No LLM or embedding call was added.
  **Residual:** a rephrase that neither supplies nor names the old project still
  mints a new goal slug and cannot be safely joined semantically. Keep this item
  open for an evidence-backed family resolver; do not lower the 0.9 Jaccard
  threshold and risk unrelated-project context contamination.

### Modular refactoring (AFK-friendly chunks, queued 2026-04-18) — deferred chunks

Jeremy's framing: LLMs don't feel rework cost the way humans do, so our
codebase has accumulated seams that are hidden (not broken, just hostile
to the next edit). These chunks are sized so one session can ship one of
them cleanly without needing real-time direction. Pick any of them when
looking for an AFK-friendly chore. Principles in `docs/CODING_NOTES.md`.

- [ ] **Test clutter trim.** Jeremy's outside-in-testing posture
  applied to the suite: tests that poke private functions with mocked
  collaborators and assert call-shape are performative. Sweep tests
  touched during recent refactors and mark ones that would break on
  a rename-without-behavior-change — delete the clearest offenders,
  keep anything covering a module boundary or regression. Don't do
  a mass pass; trim opportunistically when editing neighboring code.
  (Tracked as a posture, not a standalone chunk.)

### Storage decision — sqlite indexer (deferred)

- [ ] **Storage decision (deferred).** JSONL captain's log is fine for within-run analysis. Sqlite *indexer* on top (not replacement) is the right pattern when cross-run queries become routine — "median treat-vs-control delta across N runs," "all CLOSURE_VERDICT < 0.5 in last 30 days." Defer until we have a concrete query we keep wanting.

### Step-to-goal elevation

- [ ] **Step-to-goal elevation.** When a step's elapsed time or token
  spend crosses a threshold, pause it, capture its state, respawn as a
  child goal with its own decompose/execute/verify loop, merge result
  back. Invasive (state handoff + result merge + parent-loop resumption);
  wait for heartbeat signal to tell us *which* steps actually need this
  before building.

### Phase 65 — Constraint/Premise Orchestration (MVE live here; deeper expansion deferred)

See `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`. **Current truth 2026-07-14:** the MVE shipped behind `scope_generation` (fresh-install default OFF because it adds an LLM call). The runtime box audited in July explicitly opted in and injected ResolvedIntent from 2026-07-09; this M1 dev host currently has no Maro config and therefore remains OFF. The 2026-04-22 six-run A/B's reliable signal was plan compression, and the old runtime control-only configuration was corrected 2026-07-09. `scope_ab_skip` remains only as an experiment control and should be unset in real configs. Items below are the still-open design questions for expansion beyond the single-persona MVE.

- [x] ~~BLOCKER: Autonomous-path behavior.~~ Resolved as shipped: no gate — scope output is logged and used as planner context, exactly the "log for post-hoc review, continue, no gate" default this blocker recommended.
- [x] ~~BLOCKER: A/B mechanism.~~ Resolved as a mechanism and actually run:
  the 2026-04-22 A/B used 3 inject + 3 control goals. Its reliable primary
  signal was plan compression (8 steps versus 15–40). The apparent 3/3 versus
  1/3 clean-run gap was confounded by recovery bugs in two long control plans,
  so closure-quality improvement remains under-tested and is not claimed here.
  `scope_ab_skip` survives only to reproduce a control arm.
- [x] ~~BLOCKER: Cost ceiling.~~ Resolved for the MVE by the per-run + daily budget gates shipped 2026-07-01 (`budget.per_run_usd` / `budget.daily_usd`). Reconsider a scope-specific sub-budget only if Phase 65 expands into multiple calls/personas; it is not a live blocker today.
- [ ] **Gate heuristic.** Design's "AGENDA goals above N words" is wrong (short goals often benefit most, long ones often don't). Needs an actual judgment signal — possibly complexity classifier, or "use for goals with ≥3 deliverables."
- [x] ~~Triad vs. single persona.~~ Resolved as shipped: single persona, per the review's recommendation. Triad remains unvalidated and should stay out unless ablation shows different constraint lines (next bullet).
- [ ] **Persona content vs. costumes.** Design assumes personas produce genuinely different perspectives. Current `persona.py` is largely system-prompt overrides + skeptic modifier. Validate that PM/engineer/architect personas *actually* draw different inversion lines (not just prompt flavor) before investing in triad.
- [ ] **Scope: verification sibling.** Design addresses the *planning* phase. Biggest defect in the system is in the *verification* phase — slycrel-go "passed" because nobody ran a browser. Constraint-setting alone won't close this gap. Needs sibling design for ground-truth verification (real browsers, real endpoints, real test execution — not LLM judgment).
- [ ] **Completion-standard coexistence.** Design says "completion standard is subsumed." Migration plan needed: does completion-standard still run during rollout? If both, do they contradict?
- [ ] **continuation_depth interaction.** Phase 64 restart carries ancestry context across boundaries. Constraints/premises must also be preserved (or explicitly refreshed) across restart. Design is silent.
- [ ] **Concurrent-loop interaction.** `team:` and DAG executor run parallel workers. Do they share the constraint set? Who catches cross-worker conflicts that individually-satisfy-but-together-violate? Unspecified. *2026-07-09 note: the concurrency-hardening arc (fail-closed file_lock, admission gate, worktree isolation) made parallel workers **file/git-safe** — this item is the remaining **semantic** layer (shared constraint set, cross-worker conflict detection) and is explicitly a follow-up, not covered by that arc.*

### Verifier synthesis as a deliverable (scope's other half)

**First real slice SHIPPED 2026-07-12 →
`docs/history/2026-07-12-routing-and-probe-synthesis-design.md` Part B** (Deliverable.shape,
shape-conditional behavioral-probe MUST with logged waiver, probe-env
hardening incl. the cwd=None residual; chunks B1–B3, see MILESTONES -5). The
full BDD red-green loop below stays deferred until the honest-measurement
prerequisites ship (now satisfied) — this entry remains the long-arc record.

Two residual gaps surfaced by adversarial-review pass 3 (2026-07-12, scoped
skeptic pass on the pass-2 fix commit `0621417`) — both judged real,
in-scope for the full BDD loop below, not for B1-B3, and documented in-code
rather than fixed with a fragile heuristic:
- [ ] **Waiver content isn't judged, only presence.** `behavioral_probe_waived`
  suppresses the B2 MUST (`closure_verify._detect_behavioral_gap` Signal 3)
  on ANY non-empty string — a pretextual waiver ("static compile proves it")
  bypasses it exactly as well as a genuine one. Needs an LLM judge (new
  verifier-LLM scope) or would otherwise require the external-taxonomy
  approach this function's own docstring says to avoid. See
  `closure_verify.py` Signal 3 comment. Pinned so this is testable-against,
  not just prose: `tests/test_director.py::TestDetectBehavioralGap::
  test_known_gap_pretextual_waiver_still_suppresses_signal3` — flip the
  assertion once waiver-content judging ships.
- [ ] **A "fail" outcome isn't checked for relevance, only cleanliness.**
  B3(b)'s confidence cap (narrowed in pass 2 to exempt any clean
  `outcome=="fail"` from capping) can't distinguish a real, meaningful
  failure from a brittle/irrelevant check the plan LLM wrote badly — both
  now uncap the same way. Mechanically irreducible with only
  pass/fail/inconclusive counts; needs a check-to-deliverable relevance
  signal that doesn't exist today. Accepted per an explicit asymmetric-cost
  argument (over-eager demotion costs one bounded `closure_restart`; a
  wrongly-suppressed real failure silently poisons `goal_achieved`) — see
  `closure_verify.py` B3(b) comment. Pinned: `tests/test_director.py::
  TestProbeEnvHardening::test_known_gap_irrelevant_fail_still_exempts_
  confidence_cap` — flip once a check-to-deliverable relevance signal
  exists.
- [ ] **Heuristic live-data regex misses named-place phrasing.**
  `_LIVE_DATA_RE` (no-LLM fallback path, `intent.py`) only catches
  current/latest/today wording; asks like "where can I get non-ethanol gas
  near Manti, Utah" still route NOW even though the LLM path correctly
  routes the same question AGENDA via `needs_live_data`
  (`test_llm_needs_live_data_forces_agenda`). Confirmed still-open by 3
  independent adversarial reviewers 2026-07-12; accepted as a deliberately
  narrow lexical approximation (design doc DECISION at
  `docs/history/2026-07-12-routing-and-probe-synthesis-design.md:70`), not
  a bug to chase. Pinned: `tests/test_intent.py::TestLiveDataOverride::
  test_known_gap_named_place_live_data_not_caught_by_heuristic`.

- [ ] **Verifier synthesis phase.** Dream-level: orchestrator builds its own verifier when none exists, rather than degrading to LLM judgment or failing as "hard." Framing: BDD + TDD. Scope declares Given/When/Then (what must be true for "done"). Execution includes a mandatory red-green pair: synthesize an executable probe, break the code on purpose to confirm it catches the failure, fix the code, probe goes green. The probe is a first-class checked-in artifact.

  **Needs additional scoping (Jeremy, 2026-07-12, agreed).** Three concrete
  residual-risk pin tests now point at this item (waiver content unjudged,
  fail-relevance unjudged, heuristic regex gap — see above) on top of the
  original slycrel-go motivating anecdote below. No longer just an
  open-ended "dream-level" aspiration; worth a real scoping pass (MVE
  sizing, which open question (a)-(d) below gates first) before treating
  it as a queueable chunk. Not started — noted, not yet scheduled.

  Motivation: slycrel-go "done" run (loop `bd9b581c`, 2026-04-16, 1.55M tokens, status=done) passed `go build` while nothing exercised the binary. Three real bugs (`atomicWrite` race, silent `os.Executable` error, ignored write errors) survived untouched — caught only by the follow-up `identify-and-fix-the-3` review run. Scope alone would have named the gap; a synthesized probe would have closed it.

  Replay result after Phase 65 + closure wiring: materially better, but still half-real. The replay refused to mark the branch done, yet the decisive catch was static: closure found hallucinated `xterm.js` claims in the work summary via repo inspection, not via booting the server or exercising the client. This is progress, but it exposes the remaining defect precisely.

  **Concrete defect: runtime-probe bias.** Closure-plan synthesis defaults to static/code-inspection probes (`grep`, `test -f`, source reads) even when the prompt explicitly permits live checks. In the slycrel replay all generated checks stayed static; none started the server, hit `/health`, opened a websocket, or drove browser/client behavior. The verifier is real enough to catch hallucinated code content, but still weak on unexercised runtime behavior.

  **Likely cause:** the current prompt rewards checks that are fast, safe, read-only, and self-cleaning, but does not provide cheap lifecycle scaffolding for runtime probes (boot ephemeral server, wait for readiness, hit endpoint, clean up). The LLM is taking the path of least resistance, not refusing in principle.

  **MVE:** one goal class ("build X that does Y") requires scope to declare ≥1 executable probe (shell script, curl+WS, Playwright spec). Step graph adds a mandatory "probe-fails-on-broken-code → probe-passes-on-fixed-code" pair. Compare outcome quality + regression rate vs checklist-complete path.

  **Implementation direction for the first real slice:**
  - add lightweight runtime-probe scaffolding examples to the closure plan prompt (boot in background, readiness wait, cleanup trap)
  - require at least one behavioral probe for runtime-delivering goals unless the planner explicitly explains why it is impossible in this environment
  - log probe modality for evals (`static`, `process`, `http`, `ws`, `browser`) so closure quality can be measured instead of guessed

  **Secondary issue:** probe brittleness/calibration. One replay check false-positive'd because the grep pattern for `RemoteAddr.*username` was stricter than the real log line. After runtime-probe bias, harden probe robustness so static checks do not become noisy theater.

  **Open questions:**
  (a) recursion — who verifies the verifier? Bounded version: the "break it on purpose" step IS the verifier-of-verifier.
  (b) which goal class first — probably build/implement missions, since research/report missions have softer success criteria.
  (c) interaction with completion-standard — does the probe subsume it, or both run?
  (d) cost ceiling — synthesizing + running a probe adds LLM calls and execution time; need per-goal budget.

  Related: BDD (Given/When/Then framing), TDD (red-green cycle), property-based testing (∀ operation, property holds), mutation testing (probe-of-probe bounded version). Sibling of Phase 65 "Scope: verification sibling" blocker above — this IS that sibling. **Cross-link:** also the sibling of the Actionable "Closure treats failed-to-run commands as checks-passed" item — runtime-probe bias is closure *choosing* static over behavioral probes; the closure-failed-to-run item is closure *mis-reading* the behavioral probes it does choose. Same root: the verdict is decoupled from whether the thing was verified.

  **Replay raw numbers** (evidence for the bias finding above): `~/.maro/workspace/projects/slycrel-replay/artifacts/summary.json` — `complete=False, confidence=0.35, 3/5 checks passed`. The two failing probes: (i) overly-strict grep for `!RemoteAddr.*username` false-positived on a legit log line `log.Printf(... username, r.RemoteAddr)`; (ii) `grep -qi xterm web/*` correctly caught that the work summary hallucinated xterm.js integration. The `_CLOSURE_PLAN_SYSTEM` prompt (now at `closure_verify.py:29`, imported by director.py post-split) says "Commands must be fast (<15s), safe (read-only or self-cleaning), exit 0 on success. Wrap background processes with `timeout` and always clean up PIDs" — permits live probes but nudges toward grep via path-of-least-resistance. (2026-07-04 status: the "implementation direction" modality-logging bullet above is SHIPPED — closure logs probe modality, and `_detect_behavioral_gap` / `_detect_diagnosis_gap` exist in closure_verify.py. The scaffolding-examples + required-behavioral-probe bullets and open questions (a)–(d) remain open.)

  **Second full run (2026-04-17, after observability fixes) — modality chart is stark.** CLOSURE_VERDICT event recorded `modality_distribution={"static": 4, "process": 1}`, zero http/ws/browser — on a goal explicitly about "headless server with browser as a client." Closure's own summary admits: *"Gap: runtime validation (server startup + browser connection) was not performed."* Yet it still returned `complete=True confidence=0.92`. Manual post-hoc runtime probe (3 curl calls, ~5 seconds): `/health → 200`, `/ → 200`, `/ws → 101 upgrade`, server logs `player "test" connected/disconnected`. The thing works; closure lucked into being right via static checks. The cheap, mechanical proof would have been three curls — and the system *had time*: the loop ran 810s / 3M tokens / 39 steps. Budget was not the constraint; scaffolding was.

  **Cross-cutting: adversarial review was the hallucinator on this run.** The loop's own adversarial review contested "Go not installed on this machine" and "headless-browser-client branch does not exist" — both false (Go 1.24.2 at `~/go/bin/go`, branch at `origin/headless-browser-client@4fdf0202`). Step output was substantially accurate; the review fabricated contradictions. Suggests the review path needs the same inversion-at-verification discipline: dispute a claim → run the probe that settles it. Currently reviews reason from priors without grounding.

### Composable decision-point hooks (design exploration)

- [ ] **Composable decision-point hooks** — (2026-07-04 correction: `step_events.py` was built, accumulated zero real handlers, and was PRUNED in the repo-wide refactor — see REFACTOR_PLAN. The live interception surfaces are inspector observation, quality gate, and prompt injection of standing rules/lessons/skills into decompose.) These aren't composable: you can't say "after decompose, before execution, run extra verification on steps 3 and 5." MTG-style stack where effects can be intercepted at targeted points. For now, prompt-stage injection is sufficient. Revisit when operational experience shows which decision points actually need interception. Key constraint: any self-extensibility must be human-gated (see evolver guardrail auto-apply fix).

### Phase Transition Contracts (architecture — revisit after operational data)

- [ ] **Formal stage contracts between pipeline phases** — Currently phase transitions are implicit: decompose outputs strings, execute takes strings, finalize takes outcomes. No typed contracts, no hard validation gates between phases. Pre-flight is advisory-only (loop proceeds regardless). Trajectory check is the first real mid-pipeline gate. Need: (1) typed output contracts per phase (not just "a list of strings" but "atomic steps that cover the goal scope"); (2) hard gates that re-plan or abort instead of proceeding with garbage input; (3) audit which existing checks are load-bearing vs noise. The Starship optimization: delete the advisory checks that never change behavior and replace with fewer, harder gates. Defer until operational data shows which gates actually matter.

### Phase 38 subpackage move

- [ ] **Phase 38 subpackage move** — src/ is flat, now at ~130 modules (was 49 when this was written). Successor plan: `docs/REFACTOR_PLAN.md` Tier 4 is this same move, sized against current reality. Deferred (33+ imports per group), revisit when it causes real problems.
  The `orch.py` legacy-trio removal rides this move (trio deprecated
  2026-07-09, stderr warnings + tripwire live; stub archived to
  BACKLOG_DONE 2026-07-27): remove `maro tick`/`loop`/`plan` and
  promote the path/NEXT.md layer as the real orchq/paths subsystem.

### Agentic verifier for large artifacts

- [ ] **Agentic verifier for large artifacts.** Today the validator sees a bounded
  in-context slice of the result (`validate.max_input_chars`; hosted-free uses
  its own `input_char_budget`). For multi-KB artifacts, stuffing the whole thing
  into context is wasteful — a tool-using verifier that reads the artifact
  selectively (grep/read a temp file) is the better pattern. Scope it as an
  opt-in verifier tier, not the default. (2026-07-21: local-model caveats
  removed with the local rung; the pattern itself stands.)

### Store/guard enforcement censuses — gated on a registration convention (swarm-review chunk 8, 2026-07-22)

Chunk 8 shipped the mechanical half: the DEFAULTS.md **reverse census**
(`test_every_documented_key_has_a_reader` — a documented flag nothing in
src/ reads fails the suite; wrapper reads and f-string-constructed keys
resolved by AST shape, zero hand-maintained exemptions). The other two
wiring-inventory checks CANNOT ship the same way yet, per the checkpoint:

- [ ] **(a) Stores census** — every store file (jsonl/md under the
  workspace) must name a live writer AND reader. Prerequisite: store
  paths declared through one helper/registry the census can walk
  (today they're ad-hoc `_x_path()` functions across modules — a census
  without the registry is a hand-maintained rot list).
- [ ] **(c) Guards census** — every installed guard must be probed as
  *firing*, test_git_guard-style (installed/runtime state + a
  production-data-shape pin). Prerequisite: a guard manifest consumed
  by BOTH the installer and the census. Lesson pinned in the plan: the
  impact scanner passed its tests and never fired — "a test exists"
  proves nothing about a guard.

When either registry ships, the census follows in the same chunk
(consumer-first: convention and enforcer land together).

### Coherence signal upgrade (chunk-9 item 2, later half — decided 2026-07-27)

Now-lane (decided, ships with chunk-9 wiring): the coherence question
("does the assembled path still tell the original story?") rides the
existing closure/navigator judgment seams — a question added to seams
that already run, not a new subsystem. This item is the later half:
revisit with live data, confirm the seam-riding version actually fires
(or visibly misses), and only then upgrade lost-the-plot to its own
tracked signal with a readout. Consumer-first both times: the upgrade
needs evidence of misses, not vibes. Decision 704420c7; design
§6/§10 "Coherence vs done-means" (COMPOUND_THINKING_DESIGN.md).

### §9.3 declare-blocked — named v1 cuts (2026-07-29)

The §9.3 structural declare-blocked v1 shipped at the closure-restart
boundary (evaluate_closure `prior_verdict` fingerprint comparison; see
COMPOUND_THINKING_DESIGN 2026-07-29 addendum). Post-land adversarial
review (docs/history/2026-07-29-declare-blocked-adversarial-review.md):
CONTESTED → remediated same session, 2/2 findings verified real —
status honesty now rides the declaration (a declared-blocked "done"
demotes to incomplete regardless of the 0.7 confidence bar), and
fingerprint material is now command+exit+output-slice signatures, so
broad commands failing differently no longer false-stall. Extensions
cut by name, consumer-first when their evidence arrives:

1. **Main-gate prior-verdict join.** The main closure gate at
   continuation_depth>0 has no baseline in hand — declining an
   evidence-free restart BEFORE it fires (rather than declaring blocked
   after) needs a persistence join. **Join material now exists
   (2026-07-29 persist-the-artifacts chunk, Jeremy's "fix the lineage"
   call):** metadata.json `loops[]` carries loop_reason/parent_loop_id/
   continuation_depth per loop, and build/closure_verdicts.jsonl
   carries every verdict's failed-check signatures + fingerprint in
   order — the join is now a local read of the SAME run dir. Zero
   depth≥2 restarts exist live today — build when the restart-boundary
   declaration's log line (or offline analysis of the persisted
   fingerprints) shows the case occurring.
2. **Redecompose plan-fingerprint convergence** (loop_blocked). The
   `_REDECOMPOSE_THRESHOLD = 2` cap is budget-shaped; the structural
   twin is: fingerprint successive decompositions and stop
   re-decomposing when the PLANS stop changing, not when the counter
   hits 2. Same shape as §9.3, one seam over.
3. **Fingerprint coarsening — DECLINED (Jeremy 2026-07-29: "fix the
   lineage, not the wording").** Live recon: in both fully-recoverable
   historical restart pairs, closure checks were LLM-regenerated with
   different wording — identity matching missed them. The
   false-POSITIVE half was fixed in the 2026-07-29 review remediation
   (signatures now carry failure output, so broad commands failing
   differently no longer collide). The loosening direction (fuzzy
   wording/artifact matching) is declined in favor of persistence:
   build/closure_verdicts.jsonl now keeps every attempt's fingerprint
   and per-check evidence, so miss-rate is measurable offline instead
   of argued about. Reopen only with that measurement in hand.

### Tire-runs tangent — deferred findings (2026-07-27, Opus independent review)

From the three Poe-dispatched tire-research runs (record:
`docs/history/2026-07-27-tire-runs-examination.md`). The four
runtime fixes + closure-skip observability shipped same day; these are
the bigger design items that came out of the same evidence, deliberately
deferred rather than silently dropped:

- **Token-lean fetch for subprocess workers + mid-step token brake.**
  Run 3 step 4 burned 2.14M input tokens ($1.21) curling raw retailer
  HTML; the capped Jina path (`web_fetch.py`) is architecturally
  unreachable from the `claude -p` backend (`fetch_tool.py` docstring
  says subprocess workers "have their own fetch tools") — the live
  backend on this box bypasses the one seam that would contain the
  blowup. The 200K/step figure is diagnostic, not a brake. Cost control
  has to live at the substrate boundary.

  **Fetch half SHIPPED 2026-07-27** (branch `token-lean-fetch`): the seam
  is now reachable from a shell — `python3 src/fetch_tool.py <url>` (and
  the `web_fetch.py` delegate) returns the capped markdown chain, and the
  EXECUTE_SYSTEM "URL FETCHING" block names that command with an
  absolute path resolved at import, replacing the old text that told
  workers a non-pre-fetched URL was simply "unavailable" (which left
  curl as the only move). Chain hardened to Jina → **Cloudflare
  markdown.new** → raw HTTP+BS4: Jina was a single point of failure that
  403s/rate-limits, and a worker with no working markdown tier falls
  back to curl. Measured on en.wikipedia.org/wiki/Tire: 754,447 chars
  (~188K tokens) raw → 20,279 (~5K) through the chain, 97.3% reduction.
  Raw HTML is captured full-fidelity to `<rundir>/fetch-raw/` (content-
  addressed, `index.jsonl` manifest) with only a one-line path pointer in
  context, so a later step can re-extract JSON-LD/tables the markdown
  drops without re-fetching — verified by pulling a `datePublished` field
  out of a capture the markdown had dropped. Deliberately NOT under
  `<rundir>/build/`: captures are unscrubbed third-party HTML and
  viz_server serves that tree.

  **Adversarial review 2026-07-27 (3 Codex lenses) returned REJECT by
  consensus; remediated in `d3bda62`.** Findings that were real and are
  now fixed: capture-store symlink handling (a planted `<digest>.html`
  was reused and its path handed to the model as something to parse — a
  file-disclosure primitive; a fixed `.tmp` name was both a planting
  target and a two-lane race), a symlinked capture dir redirecting
  captures into the viz-served `build/` tree, no egress policy (the
  capture path made a second unproxied request, so a page could return
  clean markdown via Jina and redirect the capture at cloud metadata),
  credentials/query stored plaintext in the manifest, no disk budget,
  "full fidelity" that was a 500KB decoded prefix, and a Cloudflare
  string-valued `result` envelope parsed as failure. The review earns
  its cost again — the 6612-green suite caught none of these.

  **Mid-step token brake SHIPPED 2026-07-27** (same branch, follow-up
  chunk). Two enforcement points, both at the substrate boundary:
  (1) per-tool-call ingest cap — agentic subprocess calls now carry
  `BASH_MAX_OUTPUT_LENGTH` (config `llm.subprocess.bash_max_output_chars`,
  default 24000 chars; `MARO_BASH_MAX_OUTPUT_CHARS` overrides, 0 disables),
  which makes the CLI ITSELF persist oversized Bash output to a file and
  put only a capped slice in the model's context — verified live on CLI
  2.1.220 (100K-char output → exactly-cap-sized slice in the transcript,
  including through `ClaudeSubprocessAdapter.complete` end-to-end); covers
  curl/cat/multi-MB JSON alike, and the keys are forwarded through the
  container lane's explicit env passthrough. (2) per-call accumulation
  brake — a stream-side probe (`llm._build_step_token_brake`) sums uncached
  ingest (input + cache_creation, deduped per API message id) and kills the
  subprocess past `llm.subprocess.fresh_input_ceiling_tokens` (default
  300K; `MARO_SUBPROCESS_FRESH_INPUT_CEILING`, 0 disables) →
  `TokenRunawayError` → error_class `token_runaway` → the STEP blocks and
  the run continues (deliberately unlike budget_runaway, which stops the
  run). The one thing maro canNOT do was verified and documented in
  `llm.py`: tool results cannot be rewritten before the model sees them —
  the NDJSON is the CLI's after-the-fact echo of its own loop, so
  truncating in `_parse_stream_json` would only falsify maro's records.
  Same-seam fixes riding along, both from live NDJSON evidence: the
  stream cost probe double-counted usage ~2x (one assistant event per
  content block, same message id + usage — now deduped), and the
  rate-limit retry dropped `env_extra` (a capped call retried uncapped).
  The EXECUTE_SYSTEM "curl is fine for JSON APIs" carve-out is closed with
  size-aware guidance (`head -c 20000` / curl -o + jq) and discloses the
  harness truncation so workers plan around the persisted file. Tests:
  tests/test_step_token_brake.py (28).

  **Adversarial review 2026-07-27 (3 Codex lenses vs 344973f): REJECT →
  remediated (acc5eda) → merge-gate round found the remediation's own gap
  (empty loop_status coerced to "stuck" — no-retry delivered, run-continues
  NOT) → fixed at merge. Full record:
  docs/history/2026-07-27-token-brake-adversarial-review.md.**

  **Resolved at merge (2026-07-27):**
  - **Brake ceiling calibration — MEASURED.** 123 cost-metered steps on
    this box: healthy fresh ingest cost-bounded at p95 ≈ 259K, pathology
    band 337-478K → 300K default confirmed. (Raw run-log `tokens_in` is a
    trap metric for this: includes cache reads + pre-fix double-count.)
  - **Cache-read amplification — ceiling #2 shipped.** Weighted ingest
    (fresh + cache_read/10) vs `llm.subprocess.weighted_input_ceiling_tokens`
    (default 600K ≈ 2.3× healthy p95; `trigger="weighted"` on the error).
  - **Read bypass — ACCEPTED as Bash-only, guarantee restated** (DEFAULTS
    row): fetch-extract reads are the sanctioned lane; a wholesale Read of
    a large capture lands as fresh ingest and the accumulation ceilings
    are the backstop. The Read tool has no CLI knob (recorded, below).

  **Still open:**
  - **Read tool has no per-call cap knob.** Bash-cap equivalent doesn't
    exist CLI-side; if the CLI ever grows one, wire it into
    `_bash_output_cap_env`'s sibling.
  - **Codex lane has no brake.** `CodexCLIAdapter` streams a different
    event shape; the token brake and Bash cap are claude-lane only.
  - **Container-mode CLI resolution — UNVERIFIED.** `maro-fetch` is now
    a console entry point and `_fetch_cli_path` prefers it, but nothing
    proves it resolves inside the executor image; the host-path fallback
    certainly does not. Needs a real-docker E2E that runs a research goal
    with `executor.container=on` and asserts the fetch command ran.
    Until then, container workers may still fall back to curl.
  - **`skill_loader` progressive disclosure is not wired.** `load_full()`
    has exactly one production caller (`scope.py`, hardcoded to
    `resolve_ambiguity`); the step executor never calls it, so a matched
    skill's BODY never reaches the worker — only the summary reaches the
    planner. **Docstring corrected 2026-07-28** to mark step 2 PARTIALLY
    WIRED and state the consequence (skill-body prose is documentation,
    not instruction; anything that must bind worker behavior belongs in
    EXECUTE_SYSTEM). Wiring `load_full()` at the executor is still open —
    the "stop implying it" half is done, the "wire it" half is not.
  - ~~**SSRF depth.**~~ **FIXED 2026-07-29** (autonomous batch; codex
    trio unanimous DO_NOW). Resolve-then-pin shipped at the HTTP layer:
    `_vet_resolved_ips` (all-or-nothing — a [public, private] split
    answer is treated as rebinding-shaped and refused outright) +
    `_PinnedHTTP(S)Connection` (connects to the vetted IP, never a
    second DNS answer; TLS still validates against the NAME) +
    `_SafeRedirectHandler` (re-gates every hop). Writing it surfaced a
    WORSE pre-existing hole: `fetch_url_content`'s Tier-3 raw fetch
    called `_http_get_bytes` with no gate at all, so a private-literal
    URL the proxy tiers refused fell through to a DIRECT fetch —
    `_http_get_bytes` now owns its own gate. `_resolve_redirect`'s
    manual hop-follower pinned + gated too. 17 regression tests; live
    check: example.com 200 via pin, 169.254.169.254 refused.
  - ~~**`viz_server` symlink containment.**~~ **FIXED 2026-07-28.**
    Containment was checked against the runs root only, and every denied
    sibling (`source/`, `fetch-raw/`, `metadata.json`) also lives under
    that root — so a `build/report.html` symlinked to
    `../fetch-raw/<digest>.html` was served, handing unscrubbed
    third-party HTML to the operator's browser as same-origin content.
    Now checked against the specific allowed subtree, AND the subtree
    itself must sit at its literal path: writing the test surfaced a
    second hole where symlinking `<run>/build` -> `<run>/source` put every
    denied sibling back in reach. 5 regression tests.
- **Success accounting vs answer quality.** `stuck → failed`
  (`run_curation.py`) even when partial_rescue holds contract-meeting
  deliverables — run 3 recorded "failed" with 2 of 3 tiers purchase-ready,
  and the rescue summary surfaced only one. The Opus review's headline:
  execution got monotonically better across the three runs while the
  recorded verdict got monotonically worse. Also gates learning:
  goal_achieved=False skips lesson crystallization, so the system's best
  run taught it nothing. Belongs to the stop-verdict/compound-thinking
  agenda (chunk 9), not a quick fix.
  *Chunk-9 #4 update (2026-07-27):* the typed stop verdict now rides
  beside status — a cap-stuck run carries `out-of-budget` with evidence,
  so consumers can tell it from a goal-failure stuck (and the four
  raw-status consumers were rewired to honor it). REMAINING: the
  success_class itself still maps cap-stuck → "failed"; rebucketing
  cap-stuck-with-deliverables (e.g. to partial, or closure-on-stop
  judging the deliverable) is a separate judged decision — the tire
  run 4 needle doc (2026-07-27) is the live specimen.
  *Star recon numbers (2026-07-27, chunk-9 #2 exercise —
  docs/history/2026-07-27-chunk9-2-recon-diagnosis-star.md):* the
  cap-stuck family is 9 of 726 runs in the live store (all
  `cost_budget=$2.00 + slush` endings; 2 more cap-shaped land as
  stranded/done); zero stamped stop_verdicts pre-date the fc93dfa rail,
  so any legacy rebucket needs a stuck_reason-derived fallback (the cap
  evidence lives in `build/loop-*-log.json`, NOT metadata — heavier
  read). Forward runs carry the verdict; the open call is vocabulary
  (new class vs verdict-aware readers).
- **Trust-aware success classification (design question, per-step-learning
  review 2026-07-27, Architect finding — rejected as a chunk defect,
  real as architecture).** `classify_outcome` and `is_learnable_outcome`
  consume raw `goal_achieved` without consulting `verdict_trust()` — ALL
  verdict branches, not just the new achieved-not-done ones (done+True →
  "success" is equally trust-blind). A DIRECTIONAL (confidence < 0.7)
  judged verdict can therefore set a gating success_class, while the
  seams with teeth (V2 cadence windows, contradiction emitter's era-10
  single-gate law, evolver scans) correctly filter on trust. Question:
  should curation-time classification apply the same law (e.g. a
  directional verdict classifies as if unjudged), or is label-layer
  trust-blindness fine because every learning consumer re-checks? If
  changed, it's a consumer census across all verdict branches at once —
  rows carry `goal_verdict_confidence`, so the data is already there.
- **Closure fail-open posture.** With skip_detail now recorded, the
  remaining question is design: should closure return complete=True when
  the goal carries an explicit output contract and verification never
  ran? Ties into the stop-path survey's fail-open conflations.
- **Stop-verdict deliberate cuts (chunk-9 #4, 2026-07-27).** Seams left
  unstamped, each a scoped follow-up, none load-bearing for the
  choke-point accounting that shipped
  (docs/history/2026-07-27-stop-verdict-split.md):
  - *Parallel/fan-out lane* — `loop_parallel` builds its LoopResult
    outside `_build_result_and_finalize`; a branch-level stop verdict
    needs an aggregation rule (which branch's ending IS the run's?).
  - *Navigator escalate* — who-decides-next, not a stop; stamp only if
    a navigator decision ever terminates a run directly.
  - *DirectorResult/WorkerResult + build_loop_runner vocabularies* —
    separate status vocabularies with their own consumers
    (handle_queue.py drain, director review loop); unify or bridge
    when those consumers need typed endings.
  - *NOW-lane lost-the-plot stamp* — `_verify_now_outcome` demotions
    reach the right success_class via the choke-point rebucket
    (incomplete + judged False); the metadata verdict stamp itself is
    a 5-line add to `_run_now`'s metadata write when wanted.
  - *Outcome-row ordering gap* — CLOSED by the adversarial-review round
    (2026-07-27, Skeptic finding 2): finalize now re-stamps the
    already-written row post-hoc when a merge-back block adds a verdict,
    and `is_learnable_outcome` fails closed on external-interrupt
    without a positive goal verdict. The row-write ordering itself is
    unchanged (load-bearing for lesson dedup).
- **Paused-state upgrade edges (§13e slice 1 shipped 2026-07-31, decree
  7afe8b3a).** Typed pause reasons landed (stop_verdicts.py PAUSE_*
  vocabulary, stamp_pause rail, writer sites, run-card forwarding). The
  honest-good-enough boundary, each edge independently upgradeable:
  - *Reserved-reason stamp sites* — `llm-unreachable`, `no-tokens`,
    `disk-full` exist in the vocabulary with NO writer yet. Natural seams:
    the adapter failover ladder's terminal failure (llm-unreachable), the
    budget breaker (no-tokens), an ENOSPC catch at artifact-write sites
    (disk-full). Consumer-first is satisfied (run cards already forward);
    each stamp is a small scoped add.
  - *Live pause/resume lifecycle* — today "paused" is observed provenance
    on runs that already stopped; there is no commanded `paused` status a
    running loop enters and resumes from. If/when built, the resume seam
    is `_find_resumable_runs` (stranded already resumes manually).
  - *Sheriff naming collision* — project-level `.maro-paused` marker
    (sheriff.py → orch_items status "paused") is the same word with a
    different lifecycle (project intake gate vs run state). Unify the
    vocabulary or rename one when the run-level state goes commanded.
  - *Untyped interrupt edges* — loop_finalize merge-failure paths
    (~:358-401) and the agent_loop fence path set external-interrupt
    without a pause reason; type them if their frequency ever matters
    (census: they're rare).
  - *Residual raw-status reads* — strategy_evaluator/attribution are
    interrupt-aware but still consume raw status on non-interrupt rows;
    pause_family gives them a cleaner join when next touched.
  - *Stranded outcome-ledger gap* (slice-1 review #5) — the sweep stamps
    metadata only; a writer that died mid-finalize leaves an untyped (or
    absent) outcome row. A ledger back-stamp wants the loop_id join the
    LT arc is repairing; card provenance is already correct via metadata.
  - *Legacy cards untyped* (slice-1 review #6) — run cards curated before
    slice 1 (census: 57 stranded, 24 clarification_needed) never get the
    fallback typing; `list_runs` returns stored cards verbatim.
    Recuration under a drifted card schema is riskier than the value —
    revisit only if a consumer needs typed history, denominators live in
    `output/provenance_census/`.
  - *Lifecycle predicates* (slice-1 review #8, architect) — when a
    commanded pause ships, expose `is_paused_status()`/reason-derivation
    from the pause domain instead of leaning on the INTERRUPT_STATUSES
    alias in consumers.
- **Fail-open judge-error edges (§13e slice 2 shipped 2026-07-31).**
  `judged: bool` markers landed on StepVerdict/ArtifactVerdict/
  QualityVerdict + honest thread-brain `verify-error:` lines + GATE_ERROR
  events + inspector unjudged-alignment caps. The deliberate cuts, each
  independently upgradeable:
  - ~~[JEREMY DECISION] Skill auto-promote validation is dead code~~ —
    **RESOLVED 2026-08-01, Jeremy: "let's fix the promote validation."**
    Adapter wired at the evolver call site (production passes it via
    loop_finalize → run_skill_maintenance), so the Voyager harness +
    repair loop are live for the first time; promotions can newly fail
    and be held provisional. Fail-open kept (validation error degrades
    to the old numeric-gates-only behavior) but stamped: SKILL_PROMOTED
    events carry `validation: passed|unjudged|skipped`. Pins in
    test_skills.py::TestSkillValidationHarness.
  - *Learning writers are still judged-blind* — loop_post_step feeds
    `update_skill_utility(success=True)` / `record_variant_outcome` /
    `record_skill_outcome` off `passed` alone; an unjudged fail-open pass
    still earns skill credit (matches the verify-off baseline, so it's
    defensible — but now the `judged` bit is there to consume when we
    decide credit should require a judgment).
  - *Judged-denominator readout* — discretion_readout gate-family tables
    now have GATE_ERROR rows + `judged` in event context; a
    judged-vs-unjudged column would show how often the gate actually
    judges in production (census said: parse errors are real).
  - *`alignment_score_avg` mixes judged and display-default scores* —
    inspector report aggregates still average the 0.7 unjudged display
    value alongside real LLM scores; split or annotate when the report
    is next touched.
- **Recon-flavor upgrade edges (chunk-9 #2 runtime slice shipped
  2026-08-01, docs/history/2026-08-01-recon-flavor-runtime.md).** The
  `[recon: <decision>]` tag + map-edit execution contract + map-change
  verification question landed; the honest-good-enough boundary, each
  edge independently upgradeable:
  - *Structured map_edits return* — recon results are shaped by prompt
    only; a `map_edits` field on complete_step (the chunk-3 `decisions`
    pattern: validated, journaled, carried uncompressed) is the upgrade
    when a consumer that reads structured map edits exists (reassess
    seam, or the minimal map schema §12 nudge 4 says must emerge by
    subtraction — never build the store first).
  - *VOI hard-gate* — bare `[recon]` keeps its flavor with voi ""
    (demoting it would hand the step the WRONG verification question);
    outcome rows carry the denominator (`flavor` without
    `recon_decision`). Gate at parse time only if live data shows
    ritual-exploration recon surviving verification.
  - *Probe-armed recon verification* — the recon verify question demands
    claims name what settles them, but the verifier doesn't RUN probes;
    wiring claim_probe (chunk-5b read-only allowlist machinery) into
    recon verification is its own slice.
  - ~~*Recon-aware readout*~~ — CLOSED 2026-08-01 (same day the cuts
    fix made the corpus real): `discretion_readout.recon_summary` reads
    `runs/*/build/loop-*-log.json` step rows (the durable carrier is the
    step TEXT — the tag is the schema); reports recon share, VOI-missing
    rate, and done/blocked split by flavor with SINCE-FIRST-SEEN
    denominators (the LT retraction lesson — pre-emission loops are
    counted, shown, never pooled in). Per-step VERIFY verdicts still
    aren't durably joined to step identity — step status is the proxy,
    named in "Not computable today". A typed StepOutcome field remains
    deliberately NOT added (dual source of truth; 2026-08-01 adversarial
    review F2 rejected).
  - *Parallel/fan-out lanes skip step verification entirely* —
    pre-existing lane property for ALL steps ("Ralph verify is not run
    in parallel mode — session-level state"), which recon inherits: a
    tagged step in a fan-out lane gets the map-edit execution contract
    but no map-change verification. Upgrade rides whatever fixes
    parallel-lane verification generally, not a recon-specific patch.
  - ~~*Cuts-first probes untagged*~~ — CLOSED 2026-08-01: live smokes
    showed the cuts lane intercepts exactly the survey-shaped goals and
    shipped textbook recon probes untagged (the lane returns before the
    taught decompose). Fixed deterministically in `_cuts_plan` (VOI =
    the boundary plan), gated on the same `planner.recon_flavor`
    emission killswitch; boundary-expansion carry verified (probe text
    rides `- Probe:` evidence lines verbatim, nothing exact-matches);
    milestone expansion exempts recon steps (text rebuilds strand the
    tag — same class as the `_shape_steps` skip).
  - ~~*`strip_recon_tag` has no runtime consumer*~~ — CLOSED 2026-08-01:
    the recon readout's sample lines are the consumer (step text rendered
    stripped, the decision as its own column). Viz step lists /
    thread-brain lines remain tag-inclusive — fine, the tag is
    informative there.
  - *Recon flavor-carry through milestone expansion* — the expansion
    exemption applies to EVERY recon-tagged step, not just cuts probes
    (2026-08-01 adversarial review, Architect+Minimalist): a broad
    prompt-emitted recon step flagged as a milestone now runs as one
    oversized execution. Deliberate for now — expanding a recon step
    sub-decomposes it into a commit-shaped sub-plan, destroying the VOI
    question; oversized recon is an emitter-contract violation the recon
    JUDGE + readout blocked/other buckets surface. Upgrade path if the
    corpus shows broad recon in practice: carry flavor+VOI through
    expansion instead of exempting. Related deferral: loop-log writes
    are plain `write_text` (loop_artifacts.py:233) — racing readers see
    partial JSON as a visible `files_failed` count; make atomic only if
    live readouts show it persistently nonzero.
- **Persona auto-selection misroute.** Run 3 routed to creative-director
  (conf 0.8) for a spec/pricing research task. Harmless here; worth a
  look if it recurs on research-shaped goals.

### Swarm-review chunk-1 batch adds (2026-07-21)

Recorded in one pass per the checkpoint decree ("add all BACKLOG entries
in chunk 1") so every deferred item from the knowledge journey, the
plan-revision review, the Phase 0.5 battery, and the wiring inventory is
a deliberate drop with a paper trail — not a silent one.

**Checkpoint revival dispositions (Jeremy 2026-07-21, review-confirmed
BACKLOG; era refs in `docs/KNOWLEDGE_JOURNEY.md` + era files):**

- [ ] Also-After hooks — structural attachment point for post-goal review (era 01)
- [ ] RISKS.md as reviewer input — swarms shouldn't re-flag accepted risks (era 00)
- [ ] Decision-gated-ping escalation shape (era 00)
- [ ] Blind persona-panel tiebreaker for contested verdicts (era 09; designed, never productized)
- [ ] Signal-source rotation, runtime half — navigator stuck-step move (era 05; distinct from chunk-5 review lenses)
- [ ] Exception-vs-break lifecycle — justified-exception events (era 04; waits on a consumer per consumer-first)
- [ ] REASSESS full 7-question overlay (era 06)
- [ ] Recurring doc-grounding census cadence (era 06)
- [ ] Promotion-side yield starvation (era 03)
- [ ] Periodic hand-adjudicated burn-in ritual — impossible-control goals, verdicts vs artifacts on disk (era 08; the only method that caught verdict-layer bugs automated metrics blessed)

**Battery side-finds V3/V4 (verified against the tree 2026-07-21;
evidence in `docs/history/2026-07-21-phase05-battery.md`):**

- [ ] **V3 — NODE_CANDIDATE promote path missing.** Bridged knowledge nodes are born `NODE_CANDIDATE` (conf 0.3) and nothing ever promotes them; live readers filter ACTIVE-only, so every live write is invisible to every live read. **Liveness pin SHIPPED 2026-07-29** (`test_knowledge_bridge.py::TestCandidateInvisibilityPin`): proves write→candidate→invisible end-to-end at the recall surface, plus a structural tripwire that fails when any node-promotion symbol appears — whoever builds promotion must revisit the pin. **Promotion criteria remain Jeremy's call** (what earns candidate→active: times_applied? validation events? hand adjudication?) — deliberately NOT built.
- [x] **V4 — knowledge.py canon-candidate dict-vs-attr + phantom `canonize` command.** SHIPPED 2026-07-29 (autonomous batch). Degradation verified worse than noted: `get_canon_candidates` returns dict rows, so `_stage3_data`'s attr access errored exactly and only when candidates existed (empty list → clean zeros; non-empty → `{"error": ...}`) — the report broke precisely when there was something to report. Fixed to dict access (rows arrive pre-sorted; redundant re-sort dropped) + non-empty pin test (old tests only exercised the empty path, and one asserted `or "error" in result`). Phantom `canonize` hint replaced with the honest human gate ("no auto-writer by design; promote by editing AGENTS.md by hand" — matches `get_canon_candidates`' never-auto-written docstring). Building a real `canonize` AGENTS.md-writer would be a design decision (entry format, placement) — deliberately not taken.

**Era-file C-tier drops (grounding-checker review §C — out-of-arc,
batched here so the drop is deliberate; full context in each era file's
"lost good ideas" section):** Loop-Sheriff one-pager; degrade-don't-idle;
metric-thresholded promotion gates (era 00). UX timing SLA numbers;
per-phase cost models (01). SOURCES.md landed-where log;
runtime-awareness/tool-latency injection; research-doc-with-parallels
format (02). Bi-temporal fields; queryable git; @lat backlinks;
PASS/FAIL-gate template; `await:<kind>` (03). Constraint-level outcomes;
human-gate-at-constraint-altitude; eval train/test holdout (04). Parallel
watcher; elephant invariant; agenda-reframing/propose-better-goal;
cross-run convergence cap; blocking ask() (05). Per-claim ratings format
(06). GOAL_BRAIN compaction (07 — separately queued post-drift-review).
Semver CHANGELOG; rung-ladder DoD form (08). Fastembed/sqlite-vec gate
re-check (09). Session-reuse cost A/B; verifier-synthesis resumption;
enrichment consumption; Fable-handoff discipline (10). Drift review
specced-never-executed; closure-latency-before-notify (11). None is
load-bearing for chunks 3-5 (review-confirmed); pull individually on a
real trigger, don't sweep.

**Wiring-inventory surprises (2026-07-21 agent-reported; **VERIFIED
2026-07-29** — verify-before-fix pass adjudicated all 8 against the
tree: 7 CONFIRMED, 1 mischaracterized. Full docket with current
line numbers + smallest consumer-first next move per row:
`docs/history/2026-07-29-wiring-claims-verification.md`. Items stay
open — verification ≠ repair; each needs a wire-or-retire decision):**

- [ ] `task_ledger.jsonl` WRITE-ORPHAN — **VERIFIED**: writer live (loop_execute.py:1377), `load_task_ledger` has zero runtime callers (def + re-export + tests only)
- [ ] Outcome-compression pipeline DEAD at runtime — **VERIFIED**, with nuance: fully unit-tested (test_memory.py:527-705), zero runtime callers — the "a test exists proves nothing" case; outcomes.jsonl grows unbounded
- [ ] `knowledge_edges.jsonl` dead both ends — **VERIFIED**: writer gated on `skills_used` (knowledge_bridge.py:397-400) which both live call sites (memory.py:717/:881) omit; `build_wiki_link_edges` + `load_knowledge_edges` zero callers
- [x] `times_applied += 1` in-memory only — **VERIFIED** at knowledge_web.py:1787 (`inject_knowledge_for_goal`); never written back — **SHIPPED 2026-07-29** (receipt write-back thread): `_bump_node_times_applied` locked raw-dict rewrite (unknown keys survive — data retention) called for rendered nodes only; pinned incl. unrendered-node-no-receipt + future-key survival; live-verified accruing. Bonus: the same thread found and fixed the BIGGER twin — the recall LOOP slice (the live main-loop lesson surface) bypassed `_increment_times_applied` entirely, so all 338 lessons.jsonl rows and every tiered row sat at times_applied=0 while the render promised "applied Nx" receipts; now rendered-only lessons accrue (same law as citations), live-verified in medium+long tiers, canon-candidate pathway (CANON_APPLY_THRESHOLD=10) reachable for the first time.
- [ ] Persona template memory seam dormant — **VERIFIED + worse**: persona.py:412 imports nonexistent `intent.classify_intent` → always excepts → task_type permanently "general"; AND no persona file uses the template vars
- [ ] `hypotheses.jsonl` no runtime injection reader — **VERIFIED** (pack.py CLI only; other hits are prose/docstrings); internal confirm→promote loop intact — arguably working as designed
- [ ] Verification calibration cluster dead — **VERIFIED + extended**: one copy (knowledge_lens.py:1264/1349/1383), zero runtime callers of writer OR consumers; only tests touch it → Phase 60 "DONE" shipped symbols+tests without the runtime wiring
- [x] `RULE_GRADUATED` "second phantom event" — **MISCHARACTERIZED, closed as record-fix**: emitter exists and works (`maro-knowledge graduate` → rules.py:271-273), just zero live firings ever; right in practice, wrong in mechanism — nothing to build (docket row 8)
- [ ] Director/dispatch context omits the playbook (wiring row 17) — CONFIRMED structurally 2026-07-21 (chunk 2): `RecallResult.as_context_block` renders only lessons/standing_rules/decisions/knowledge; playbook (+ graveyard, failure_notes, learning_activity) reach only the loop slice via `as_loop_block`. The playbook module docstring claimed director injection for months. Decide whether the director prompt should carry playbook wisdom (and which other substrates), then wire with a liveness test — consumer-first.
- [x] Record-mode never fires on single-backend boxes — CONFIRMED 2026-07-21, **SHIPPED 2026-07-29** (autonomous batch): `build_adapter(auto)` now always wraps in `FailoverAdapter`, len==1 included — the wrapper is the one seam carrying record-mode capture, the runaway-cost meter, and the utility-call cap warning, and the bare-adapter fast path left all three dark on subprocess-only boxes (`n_calls: 0` on every run despite record-mode default-ON; evidence: run c772366a-wily-badger). The `MARO_BACKEND` env override (still the auto path) wraps too. Pins inverted in test_llm.py (single-backend → wrapped, backend property forwards). Side-find fixed in the same commit: both `cross_ref.py` adapter fallbacks passed a model tier as the positional *backend* arg (`build_adapter("cheap")` → AssertionError → caught → silent empty-report/dry-run degrade every time the fallback path ran); now `model=` keyword, call-shape pinned in test_cross_ref.py.
- [ ] Ancestry write-side unification (recursion prerequisite) — thread_brain and ancestry.json are separate write paths; at fork time they must be one record or children inherit divergent truth. Named a fork-implementation prerequisite in the THREAD_ARCHITECTURE fork-contract note (2026-07-21, chunk 3); GOAL_BRAIN open thread. Deliberately NOT part of the swarm-review arc — queue for the thread-architecture implementation arc.

### LeAct acceptance filter for minted reasoning traces (2026-07-31, from dispatched run 4125f34e-azure-haven)

- [ ] Steal from LeAct (arXiv 2607.21856), surfaced by the Hermes minimal-prompt
  research run: keep a sampled reasoning trace only if it **measurably raises
  the probability of reproducing a known-correct action** — an outcome-gated
  acceptance filter on explanations, not just on changes. Mapped onto us:
  `thinkback.py` (single-run LLM narration, no quantitative filter),
  `evolver.py` (LLM pattern-spotting, no trained selection objective), and
  the near-miss `VERIFY_LEARN_ARC` — which gates learning on MEASURED outcome
  but verifies applied CHANGES, not REASONING TRACES. The gap: nothing applies
  the measurable-improvement filter to the explanations thinkback/evolver
  mint, so a plausible-but-wrong narrative ships as a lesson exactly like a
  validated one (same failure family as the ablation's confabulation finding —
  confident wrong theories defeat retrospective review). Minimum experiment:
  score a minted trace by whether injecting it improves replay outcome on the
  run family it was minted from (the navigator A/B rails already exist).
  Consumer-first: the filter's verdict must gate minting or tiering, not just
  annotate. No successor-arc decree stands (verify-learn closed V1–V5) — this
  queues as a discrete item, not an arc reopen.

  **Amended 2026-07-31** (Opus-5 blind contrast, every code claim verified vs
  tree at badee73 — full record + accuracy scorecard:
  `docs/history/2026-07-31-leact-opus-contrast.md`). Deltas folded in:
  - **Blind-spot/tenure diagnosis**: the original entry's worry is false
    positives surviving; the opposite failure is also live — true novel
    positives dying. Tenure requires sessions_validated ≥ 3, incremented only
    on similarity>0.8 restatement (knowledge_web.py:339/719), and a lesson
    that fills a blind spot is by definition one the extractor won't
    independently re-derive 3×. Δ-gating fixes both directions at once: it
    flips selection pressure from "agrees with the corpus" to "changes
    behavior". (Engages Jeremy's "maro is starting to think like me" concern
    directly — the tenure rule actively selects for teacher-copying.)
  - **Stratify rule-vs-reason before reading results**: lessons that ARE their
    own action ("run tests before claiming done") have Δ≈0 definitionally
    (LeAct §6 self-referential collapse). Unstratified aggregate Δ is
    uninterpretable; discovering the corpus split is part of the experiment.
  - **No-logprobs workaround**: LeAct App D.2.1 action-match reward over M
    samples; the hosted-free rung makes M replays ~$0.
  - **Follow-on steal (sequences after the gate)**: competence-redundancy
    decay — retire a lesson when the model follows it unprompted (internalized,
    Δ≈0 vs current competence), replacing calendar 0.85/day. Needs the same
    replay harness. **Jeremy concurs 2026-07-31 (decree 55c877da)**: calendar
    decay was "mostly placeholder"; and the tenure side — leverage successful
    one-offs rather than requiring 3x independent re-derivation; repeat
    patterns are better-than-luck signals to EXAMINE, not a tenure gate.
  - **Free diagnostic, runnable any time**: surprise-count — Jeremy reads the
    long-tier corpus and counts entries that SURPRISE him (not wrong ones).
    Zero = collapse empirically confirmed; nonzero = the seed set the gate is
    evaluated against. Pair with the chunk-6 novelty-at-mint field.
    **Chunk-1 read landed 2026-08-01** (L1–L4 + M1–M15; full record + verbatim
    reads in `docs/history/2026-07-31-lesson-corpus-surprise-read.md`):
    mirroring collapse REFUTED (11/19 flagged) — but only M4 + M12 are
    positive surprise (the Δ-gate seed set so far); six are
    operator-certified contradictions (L4, M6, M8, M9, M13, M14 — the test
    corpus a retirement-by-contradiction mechanism has been missing); two
    route to existing threads (M7 → side-quest identification, M11 → fetch
    verb). Recurring critique in 4 reads: lessons minted at the wrong
    altitude — *how* (named procedure) where they should record *what*
    ("2 trusted sources") — candidate mint-time rule, pending Jeremy.
    Raw-corpus reading judged too tedious as an instrument; the remainder is
    re-issued in the same doc as **8 family judgments + optional 5-row tail
    sample** instead of 116 rows.
  - **Corrections (don't inherit Opus's errors)**: CANON_APPLY_THRESHOLD
    surfaces candidates that never promote (battery V3 — no promote path);
    extraction IS verdict-aware (extract_deferred_lessons, data-r2-01) — we're
    at the outcome-filtered baseline the paper calls insufficient, not naive;
    build/calls record-mode capture is dead on single-backend boxes, so replay
    fixtures need another source; oracle anchoring exists but only negative
    (chunk-4 contradiction path) or population-level (navigator A/B 58% vs
    41%) — per-lesson positive Δ is the genuinely missing piece.
  - **Revisit trigger**: don't build while the verdict denominator starves
    (4 known-arrival verdict events as of 2026-07-31). Revisit when
    `verdict_flow` shows real week-over-week verdict arrivals (Chunk B closed
    the plumbing; flow measurable from 2026-07-31 forward).

### Standing test-goal menu (future ideas)

- [ ] **Polymarket behavioral test** — "Analyze 400M+ Polymarket trades to find behavioral patterns among top wallets — what do winners do differently?" (from hrundel75 link)
- [ ] **"Get Jeremy rich" prompt** — long-term, after trading patterns are validated and backtested. Baby steps.

### Conservative — verify before dropping

These four are kept (not deleted) this triage pending verification against current code/data.

- [ ] **done != achieved, confirmed on organic runs — and the gap is large.** (verify before dropping)
  First organic batch through the new goal-verdict metadata (2026-06-12, 5
  real goals): 4 came back `done` but only **1** had `goal_achieved=True`. The
  three done-but-not-achieved (health-report refresh, roadmap audit, weekly
  digest) all wrote a structurally-correct artifact the closure verdict judged
  as falling short — "file created and non-empty" / "5/6 checks" — at low
  confidence (0.2–0.35). Two implications: (1) the done≠successful split is
  doing exactly its job — without it this batch reads as 80% success; with it,
  20% genuinely achieved, the rest flagged for review. Validates Jeremy's
  "done as 'I did it' not 'it worked'" concern with live data. (2) The verdict
  confidences are *low* — these are doubt flags, not definitive failures, and
  they correctly stay `done` (below the 0.7 demotion threshold) rather than
  flipping to incomplete. Open question worth watching: is the closure verifier
  systematically harsh on build-artifact goals (false-negative achievement), or
  are these outputs genuinely thin? Needs a few more organic batches + spot
  audits before trusting the rate. Don't tune the threshold on n=5.
  **Update 2026-07-04: the data now exists** — ~68 judged runs with verdict
  metadata on disk (~26 achieved). The gate is analysis, not data: re-run the
  done-vs-achieved rate check on the full corpus before touching thresholds.
  **ANALYSIS RUN 2026-07-09** (`docs/history/2026-07-09-done-vs-achieved.md`,
  1.0 item (b)): 72 verdict runs, era-segmented at 90b4d1b (55 poisoned
  excluded). Clean era n=17: done 65%, achieved 53%; organic slice n=10:
  raw achieved 40%, corrected ~60-70% after spot-audit (2 of 4
  done-but-not-achieved were verifier false negatives: verbatim-grep on
  paraphrase tasks, wrong-section grep on append-only ledgers; +1
  false-on-its-evidence at conf 0.95 via probe-env mismatch). Closure IS
  systematically harsh on build-artifact goals, but errs safe: zero false
  blessings post-fix, all false negatives below the 0.7 demotion threshold.
  **Verdict: keep 0.7; 1.0's gap is packaging, not closure quality.** Fix
  lever = probe-env hardening (cd to goal-named repo, cap confidence on
  environment-error signatures), not threshold tuning. Standing caveat: raw
  goal_achieved understates organic success ~20-30 points — don't feed it
  unadjusted into verify→learn; re-run at organic n≈30.
  **Prospective gate shipped 2026-07-14:** normal `maro handle` / `maro run`
  work now stamps `measurement_class=organic` into both run metadata and the
  durable outcome row; synthetic callers can explicitly select `smoke`,
  `control`, or `benchmark`, and dry runs are excluded. `handle_id` on the
  outcome collapses restarted loops to one run. Run
  `scripts/verdict-gap-stats.sh` (or `--json`) to see the n≈30 gate. It counts
  only judged, explicitly-organic rows; missing/legacy labels remain unknown
  and raw `goal_achieved` is named as an uncorrected verdict rate. Current
  unified ledger: 3 unknown legacy rows, 0 prospective organic rows, gate not
  due. The old n=10 hand-classified slice is historical evidence, not silently
  carried into the new counter. Keep this item open only until the report says
  the manual artifact re-audit is due.
  **Tangential architecture finding (2026-07-14 adversarial review):**
  `handle_task`'s budget-ceiling `loop_continuation` lane still calls
  `run_agent_loop` directly, outside the normal run ownership + closure-verdict
  lifecycle. This change now carries the parent handle/class explicitly, clears
  stale ambient run context, and conservatively lets the newer continuation row
  make the top-level request organic-but-unjudged. That prevents metric
  contamination, but it also exposes the larger pre-existing gap: a successful
  multi-pass continuation never receives the terminal closure verdict that
  would let that request enter the judged cohort. Fix by giving continuation
  consumption the shared run/closure lifecycle (not by teaching this report to
  bless the earlier partial pass). This belongs with the dedicated
  Verify→Learn/closure design arc; it is too large to smuggle into a stats
  report. **2026-07-29 rider (persist-the-artifacts review, minimalist):**
  the same `scoped_run_dir(None)` detachment also means queued continuations
  persist NO `loops[]` lineage and no closure_verdicts.jsonl — both new
  writers key off `current_run_dir()` and will start working here the moment
  this lane gets its run-dir lifecycle; nothing extra to build then.

- [~] **`decomposition_too_broad` residual.** (verify before dropping) The cache-aware conversion (2026-06-22) removed the observed noise source; remaining open question is whether a step doing genuinely >200K *fresh* tokens on an otherwise-successful run should warn at all, or only when the loop also shows stress (blocked steps / budget exhaustion). Revisit only if a real fresh-heavy run flags spuriously. (Full block archived to BACKLOG_DONE; this is the residual watch-item.)

- [ ] **Per-class routing (gathering shadow-eval data).** (verify before dropping — open children retained) Expect high agreement on
  verifiable code/math steps, low on fuzzy research-quality steps. Once the
  `--agreement` table has enough rows, route only the classes where the local judge
  earns it (per-class `min_certainty`); keep the rest on the paid path. Don't trust
  benchmark parity globally.
  **First data (2026-06-23, n=29, qwen2.5-coder:3b vs paid):** overall agreement
  96.6%, **0 false_pass across every class** (the dangerous direction — local PASS /
  paid FAIL — never happened). Per class: analyze 4/4, exec_command 4/4, synthesize
  3/3, read_artifact 1/1 all 100%; `general` 16/17 (94.1%) with the lone miss a
  **false_fail** (local FAIL@0.90 vs paid PASS on a routine file-save — local was
  *too strict*, costs a wasted escalation, not a missed defect). Surprise: the fuzzy
  synthesize/analyze essay-critique steps held at 100% — divergence showed up on a
  mundane `general` step, not the subjective work we expected to break it.
  Calibration: 0.9–1.0 bucket = 96.6% (slightly overconfident, erring strict).
  **Caveat: 29 rows is a smoke sample, not enough to set thresholds.** Next: a larger
  deliberate batch (more runs with diverse step mixes) before committing per-class
  `min_certainty` — and watch specifically for any `false_pass`, since that's the
  only error direction that can let a real defect through.
  **Larger batch (2026-06-24, n=42):** 92.9% overall, and the **first `false_pass`
  appeared** — `general` class, local PASS@**1.00** vs paid FAIL. The step was
  "list skills/ and save the listing to `artifacts/skills-listing.txt`"; the worker
  saved to a *different* path and narrated success. Local can't see the artifact
  never landed where asked — a requirement/side-effect miss, not a confidence
  problem (it fired at max confidence). Concrete classes held: exec_command 5/5,
  analyze 5/5, synthesize 3/3 — 100%, 0 false_pass; read_artifact 4 (75%, all misses
  false_fail/safe). **Decision: do NOT set per-class `min_certainty`.** (a) The
  safe-class n (3–5) is too small to justify lowering thresholds; (b) the danger
  class `general` can't be made safe by a threshold — the false_pass was at conf
  1.00. The lever the data actually points at is **provenance verification** (did
  the side effect land / was the requirement met?), which is the same root as the
  fabricated-input bug and is exactly the closure-verdict-provenance-net item above.
  So #3 feeds #2. Keep global `min_certainty: 0.6`; revisit per-class only after the
  safe-class corpus is much larger. Full write-up: `docs/LOCAL_VALIDATOR.md`.

### Run visibility residual — general-purpose server question (main entry → BACKLOG_DONE 2026-07-09)

- [ ] **Deferred: does a live server surface belong at all?** The static
  per-run report + cross-run index (`src/loop_report.py`) shipped and merged
  2026-07-09 — full history in BACKLOG_DONE.md "Run visibility: static
  per-run report + cross-run index". What that build deliberately excludes,
  and what the 2026-07-02 dashboard archive left open: a live (auth'd,
  read-only-by-default) server view, and whether goal-submission/replay
  controls ever belong in the same surface. Needs product discussion first;
  static files are the answer until cross-run browsing becomes a real habit.

### Install trial residual — $HOME haiku.txt watch-item

The 2026-07-09 docker clean-machine trial's blocker + residuals are all
shipped (full record archived to BACKLOG_DONE 2026-07-27). One
watch-item remains:

- [~] **E2E run left a second haiku.txt at `$HOME`** — investigated
  2026-07-09, unreproduced post-#16 (utility-call tool strip likely
  mooted it); detection hole by design (`detect_out_of_fence_access`
  scans absolute paths only). Watch: next docker/clean-machine trial
  must persist the workspace and grep FENCE/SCAVENGE rows before
  teardown.

### Initial-public-release — open remainders (shipped arc archived)

The (e)/(f)/(g)/(i) launch-content items are SHIPPED (full record
archived to BACKLOG_DONE 2026-07-27; decree quotes in GOAL_BRAIN
Decisions 2026-07-09). Sequencing constraint that survives: (g) needs
design sign-off before any public release. Still open:

- [ ] **(g) known-gap: filename scrubbing.** Artifact filenames /
  manifest `path` strings / REVIEW.md headings are not
  identifier-scrubbed — only artifact *content* passes
  `scrub_identifiers`; the human review gate is the only backstop. The
  correct fix is a filename-rewrite decision that also changes how
  `adopt()` derives live filenames from quarantined names — revisit on
  a real case, not speculatively.
- [~] **(h) auto-resume graduated shape — discussion wanted.** Manual
  resume + classify/message/checkpoint/sweep slices shipped; billing
  failover RATIFIED OFF 2026-07-16; the 1-auto-resume cap NOT ratified
  ("likely right decision is not binary") — graduated shape per
  error-class / cost-budget / per-goal consent, see
  `docs/BACKEND_RESILIENCE_DESIGN.md` annotations. The session-protocol
  arc raises its priority (a dead run behind a network edge is
  invisible — see SP entry).

---

Full history in [BACKLOG_DONE.md](BACKLOG_DONE.md).
