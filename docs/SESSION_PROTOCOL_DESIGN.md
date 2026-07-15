---
status: dormant-design
---

# Session Protocol — the substrate contract grows up

*(aka: two-box / Hermes arc, networked dispatch, interactive goals, effort-based
spend. One umbrella doc — Jeremy 2026-07-15: "we should probably write this all
down and iterate over the skeleton." This is the skeleton. Iterate here.)*

**Spark:** a second 2014 Mac Mini arrives 2026-07-16. Hermes goes on it as a
"real" end-user interface, dispatching to the Maro orchestration on this box.
Part of the point is Jeremy kicking the tires *as an end user* — the semi-
contrived-today tests in that direction are the product research.

**The reframe that makes this one arc, not five:** the network layer, the
in-car conversational goal (Manti++), mid-flight data injection, and
spend-above-the-cap all want the same missing thing — the substrate↔Maro
contract grown from fire-and-forget dispatch into an **interactive session
protocol**, carried over a network edge. Everything below is a section of that
one thing.

---

## 1. Stance decisions (Jeremy, 2026-07-15 — the load-bearing quotes)

These are decided postures, not open questions. Reversals go through
GOAL_BRAIN Decisions.

- **Topology is not a decision to make.** "It shouldn't matter if the hermes
  box and this box eventually converge; our goals are already to share learned
  data between machines… persisting that data or using it in an alternate
  location isn't making a hard decision, it's just the working/active
  orchestrator." → The active orchestrator is a **runtime fact, not an
  architecture commitment**. Design consequence: nothing in this protocol may
  assume "box A is forever the brain." The data layer (portable learning) is
  what makes orchestrator location cheap to change — which quietly promotes
  `PORTABLE_LEARNING_DESIGN.md` from post-1.0 nice-to-have to the enabling
  mechanism for multi-box.
- **Spend UX is effort-language, not dollars.** "'That's going to take a lot
  of work' or 'give me some time to figure that out' — that's an indication of
  elevated spend; the user can override with natural language." Ranges/bounds
  stay available but **opt-in for people who want to dip into such things**.
  Rationale: dollar figures rot (model economics shift — direction unclear);
  effort-language doesn't.
- **1.0 = "initial public release", later, not a gate.** 0.8.0 is on PyPI (the
  spur-of-the-moment intent behind shipping 0.8 instead of 1.0). Nothing in
  this arc is "for 1.0"; the bar may keep moving and that's accepted for now.
- **Modular and maintainable, improvable along contract edges.** "I suspect
  we're headed down the road of a larger multi-layer system, even if it's all
  'an app' overall." Layers get named contracts; we improve within a layer or
  along an edge, never by blurring one into another.
- **iMessage is worth attempting** (Hermes-side) for native phone chat;
  Telegram remains the fallback if it's awful.
- **Interactive is in the arc but not first**; a seam refactor comes before
  the conversational layer (§6).
- **Split-brain memory (Hermes conversational memory vs Maro execution
  memory) is acknowledged and parked** — "maybe that's ok for now and like
  the network layer a phantom sidequest." Revisit when it bites.

---

## 2. Layer map (name the edges)

```
[ phone / iMessage / Telegram / STT ]        ← channel (substrate-owned)
        │
[ Hermes on box B ]                          ← interface brain: conversation,
        │                                       persona, channel handling,
        │                                       its own conversational memory
   ── transport edge (§3) ──
        │
[ session protocol ]                         ← THIS DOC: the message contract
        │                                       (dispatch, status, effort,
        │                                       consent, inject, clarify)
[ Maro orchestration on box A ]              ← execution brain: routing, loop,
        │                                       honesty machinery, containment
[ learning data (~/.maro/workspace) ]        ← portable in principle (maro-
                                                import, JSONL-truth); the layer
                                                that makes "active orchestrator"
                                                a runtime fact
```

Existing contract surface (all shipped, local-only today):
`notify.command` push, `maro enqueue --drain`, `maro-runs status|result`,
`run_card.json`, `output/escalations.jsonl`, `deploy/openclaw/maro-dispatch.sh`.
The protocol below **extends** this surface; it does not replace it.

## 3. Transport (v0: boring on purpose)

- **v0 is SSH one-shots**: Hermes runs `ssh boxA maro enqueue …` / `ssh boxA
  maro-runs result <id>` and parses stdout (run_card JSON). Dispatch and result
  pulls are short-lived RPCs — tmux-style durability isn't needed for them
  (tmux matters for *interactive login sessions*, which this isn't; a dropped
  ssh mid-enqueue just retries — enqueue is already idempotent-safe via the
  drain-once contract).
- **Tailscale, concretely:** a mesh VPN — every box gets a stable private
  address (100.x.y.z) and hostname reachable from anywhere, NAT or not, with
  WireGuard encryption and its own key management. What it buys us: no open
  ports on either box, boxes reachable when not on the same LAN (phone → home
  boxes included), and SSH rides *over* it unchanged. It composes with — not
  replaces — the SSH answer. Recommended: install it as network fabric, keep
  SSH as the protocol. (Free tier covers a personal tailnet comfortably.)
- **Push direction** (Maro → Hermes: run_completed, escalations) reuses
  `notify.command` — the configured command becomes an ssh-wrapped call to a
  Hermes-side inbox script. Same contract, new legs.
- **NOT building:** an HTTP daemon, an open port, a message broker. A public
  API is one auth bug away from remote code execution (a goal IS code
  execution). Graduate to a persistent channel only if a message type in §4
  proves it needs one (candidate: live progress streaming; even that can poll).

## 4. Session protocol — message types, staged

Each stage is independently useful; ship in order; stop anywhere and the
system still works.

| # | Message | Direction | Status |
|---|---------|-----------|--------|
| 0 | dispatch goal → run_card back | H→M→H | **shipped** (local); v0 = same over ssh |
| 1 | effort estimate + consent (§5) | M→H→M | new |
| 2 | in-flight progress query ("how's it going?") | H→M | bones exist (run-visibility arc: captain's log, NOW mini-reports, run_card); needs a query surface for a *live* run |
| 3 | mid-flight injection (§6) | H→M | new — the seam refactor |
| 4 | clarification round-trip (director asks, user answers, goal proceeds) | M→H→M | design exists (`project_director_clarification`: ask-on-ambiguity + YOLO opt-out); needs the transport loop |

The in-car Manti++ scenario = 0+1+2+4 composed, with STT on the channel layer.
Note for that scenario: voice-dictated goals arrive noisier than typed ones
(transcription errors, filler, half-formed scope) — which makes stage 4
load-bearing rather than polish for the voice channel specifically.

## 5. Effort-based spend UX

Sketch (iterate here):

- **Internal:** the estimate comes **after the initial plan breakdown**
  (Jeremy 2026-07-15 — answers open question #1): decompose/cuts-first
  produces the plan, and the plan is what you estimate from (step count,
  worker classes, probe evidence), calibrated against the run corpus + Manti
  envelope data ($2.47 → $1.52 post-fix). Buckets, not dollars:
  `quick / normal / involved / heavy` (names TBD).
- **Latency masking (voice channel especially):** the plan-then-estimate
  ordering costs seconds, and conversational UX already has the answer —
  filler-with-content. "Let me think about this for a moment" buys the time
  the initial plan breakdown needs; better still, a **clarifying question is
  productive filler** — it buys the same seconds AND narrows scope while the
  plan assembles (Jeremy: not new UX ideas, and that's the point — reuse the
  known pattern). Protocol consequence: msg 4 (clarification) can fire
  *during* plan assembly, not only after it.
- **User-facing:** effort language only. "That'll take some real work — want
  me to go ahead?" Heavy goals get a consent round-trip (protocol msg #1);
  quick/normal proceed silently under the standing cap.
- **Override is natural language**, parsed at the interface layer: "take your
  time / go deep" raises the run's budget ceiling; "keep it cheap / just a
  quick look" lowers effort AND scope. The consent grant is **per-goal**, not
  a standing cap change — the existing budget gate machinery already refuses-
  and-escalates; the grant is the missing resume path.
- **Config for the curious:** bounds/ranges exposed opt-in (a
  `budget.effort_bands` style map, dollars visible only if you look). Default
  UX never says "$".
- **Time-robustness:** buckets are defined by *relative* cost within the
  system's current economics, recalibrated from run corpus stats — so model
  price shifts move the mapping, not the UX.
- **Two spend domains now exist** (Hermes conversational spend on B, Maro
  execution spend on A). Combined visibility is eventually needed — cf. the
  cron token-burn and rogue-heartbeat history; all current daily-cap machinery
  is single-box. Parked, but named.

## 6. The injection seam (the enabling refactor)

Jeremy's simple option, adopted as the design center: **inject additional
information into the next pending step, alongside prior step results** — a
"live-ish pivot," *different but the same* as undetermined-run continuation or
failure retry.

- The loop already has re-entry shapes: blocked-step recovery, director replan,
  NOW→AGENDA escalation-attach, resume-from-checkpoint. Injection should be
  the **same shape**: a typed input in the step-context assembly, not a new
  side channel. Refactor goal: one seam where "context for the next step" is
  assembled (step results + brain slice + lessons + …), so an injected note is
  just one more, provenance-stamped, contributor.
- **Refinements (Jeremy 2026-07-15, shape not implementation-dictate):**
  - It's a **new status/injection type**, delivered at the *next available
    processing step* — the LLM-TUI queue pattern (typed input isn't injected
    into the in-flight turn; it waits for the next boundary). Same here: no
    mid-step interruption semantics, ever.
  - **Don't co-opt the existing plan.** The injection is metadata ON the run,
    not a rewrite OF it: an **adjacent payload handled in tandem with the
    regular step data**, so the step sees (prior results) + (injected note)
    as distinct inputs with distinct provenance.
  - It's a **decision point**, not just context: receiving an injection may
    legitimately mean continue-unchanged / adjust-next-step / replan — the
    same decision seam the director/navigator already owns at blocked steps.
    Adjacency is what preserves the choice; folding it into the plan would
    decide "adjust" implicitly, every time.
- Provenance matters: an injected instruction is user-authority (it may
  legitimately redirect), but it must be *visible* in the transcript/thread
  brain as injected-mid-flight — the honesty machinery should never wonder
  where a pivot came from.
- **Horizon (recorded, not built):** two distinct future shapes fell out of
  this conversation —
  1. **Alternate timeline** — fork a goal run (or parallelize multiples
     against the same goal), like forking an LLM conversation. Same goal,
     divergent paths, compare/merge. (Worktree isolation + run forking
     machinery are the seeds.)
  2. **Maze-exit change** — mid-run goal *mutation*: "we're suddenly working
     toward a different exit." Not tree traversal into sub-goals; the
     destination itself moves.
  Both honor the recursion decree (sub-goal spawning never foreclosed).
  Neither is scheduled. Injection (§6) is deliberately the minimal cousin.

### 6a. Seam inventory (2026-07-15 — verified against source; line numbers drift)

**SHIPPED 2026-07-15 (v1): gaps 1–2 + the `note` intent.** Typed
contributions live in `src/loop_types.py` (`ContextContribution`,
`ContributionLedger`, `render_contributions`): contributors append to
`ctx.pending_context`, the merge point in `loop_execute.py` drains exactly
once per step; parallel boundaries drain via
`loop_parallel._drain_pending_context` (once per batch/fan-out); `maro
interrupt --intent note` appends a context-only `user_note` contribution.
Empty ledger ⇒ byte-identical prompts (pinned in `tests/test_agent_loop.py`).
Adversarial review (same day, FIX_FIRST → fixed): every path that consumes
without executing a prompt must re-arm — the compound-step invariant guard
(`loop_execute.py`) and ALL THREE blocked branches (retry/redecompose/split
in `loop_blocked.py`) re-append the drained batch; `note` is explicit-only
(the LLM classifier coerces a "note" label to additive); per-record 32K cap
in `ContributionLedger.append`.

**Verdict: qualified yes — the sequential core loop already has one
consumption seam.** Everything an in-loop injector wants to say to the next
step funnels through one accumulator and one merge point:

- **Accumulator:** `ctx.pending_context` (`ContributionLedger` of typed
  `{source, kind, text}` records; was flat-string
  `_next_step_injected_context` pre-2026-07-15). Contributors today:
  budget-pressure reminder (~399), goal reorientation every 5 steps (~601),
  per-step prereq/graveyard knowledge (~642), post-step hook output
  (`hooks.get_injected_context`, appended at ~1529), blocked-retry hints
  (via `loop_blocked`, whose retry/redecompose/split branches all re-arm the
  failed step's delivered records), director-escalate user replies (stuck trigger ~1140;
  verify/threshold trigger ~1506), user notes (`interrupt --intent note`).
- **Merge point:** `loop_execute.py:~650` — the ledger is drained once,
  rendered (`render_contributions`), and becomes both `incremental_context`
  and an appended tail on `ancestry_context`, passed to `execute_step`.
- **Prompt build:** `step_exec.py` `execute_step` — single builder for all
  loop-executed steps (`user_msg` ~975-986). The live-session variant
  (`session_delta_msg` ~1005-1016) already renders incremental context as a
  distinct labeled block ("NEW CONTEXT SINCE THE PRIOR STEP") — **that block
  is the natural slot for an adjacent, provenance-stamped injection payload.**
- **Delivery channel already exists:** the file-backed, process-safe
  `InterruptQueue` (`src/interrupt.py`) is polled exactly once per step at the
  loop boundary (`loop_execute.py:~1526` → `_check_loop_interrupts`). This IS
  the LLM-TUI queue pattern (§6). `additive`/`priority`/`corrective`
  **mutate the plan or the goal**; `note` (added 2026-07-15) is the
  context-only intent — appends a `user_note` contribution, touches nothing
  else, never auto-classified. (A separate typed-event path exists —
  `post_typed_event` + `await:<kind>` steps — but it's pull-based and needs a
  planned await step.)

**What breaks "one seam" today (the refactor's actual work list):**

1. ~~The accumulator is an untyped flat string — no provenance, everything
   concatenated.~~ **SHIPPED 2026-07-15**: typed
   `ContextContribution` records, rendered provenance-labeled
   (`[source] text`) at the merge point.
2. ~~**Parallel fan-out bypasses it entirely**~~ **SHIPPED 2026-07-15**:
   batch and fan-out paths drain the ledger once per boundary and pass the
   rendering as `incremental_context` + merged ancestry to every step in
   the batch.
3. Four re-entry shapes ride the *run-scoped* `ancestry_context_extra`
   instead (set once at loop start, `loop_init.py:~367`): NOW→AGENDA
   escalation-attach, undetermined-run continuation, director restart,
   closure-gap restart. Cross-process, but same conceptual input.
4. Checkpoint resume mutates the step *text* itself (`loop_planning.py:~185`
   prepends a "[resume note: …]" to `steps[0]`) — a third pattern.
5. The director/worker ticket lane assembles context separately
   (`director.py:~449-490` → `workers.py:~248`), with the `memory.worker_slice`
   off-⇒-byte-identical A/B contract living there.

**Refactor shape (recommendation — shipped as described 2026-07-15, two
deltas):** replace the flat string with a typed
list of contribution records `{source, kind, text}` on the loop context;
render as a provenance-labeled block in `execute_step` (the delta-block slot
above), **empty ⇒ byte-identical prompts** (tests assert exact prompt shapes,
e.g. `tests/test_agent_loop.py:~103`, and the worker-slice A/B contract
depends on byte-identity). Delivery = a new context-only interrupt intent
(`note`) that appends a contribution instead of touching steps/goal — it
arrives at exactly the next-boundary point. Optionally fire
`director_evaluate(trigger="injection")` at the same boundary so
continue/adjust/replan stays an explicit decision (§6's decision-point
requirement). Thread the list into the parallel batch paths to close gap 2.
Keep the worker lane untouched in v1 (gap 5 is a lane, not a bug).
*Deltas as shipped:* rendering happens loop-side (`render_contributions` at
the drain points, feeding both the delta-block slot and the fresh-prompt
ancestry tail — fresh prompts never see `incremental_context`, so
`execute_step` is unchanged); `director_evaluate(trigger="injection")` was
NOT built (spend-gated, pending Jeremy).

**Semantics trap (found + fixed during this inventory):** the carry-forward
assignment at `loop_execute.py:~1522` doubles as the *consume/clear* of the
previous step's context. The verify/threshold director-escalate path wrote
the user's reply into the accumulator and then fell through to that
assignment — the reply was silently clobbered (the stuck-trigger path only
survived because it `continue`s). Fixed 2026-07-15 (merge, not overwrite;
pinned by `test_adaptive_escalate_reply_reaches_next_step`). The typed-list
refactor must make append-vs-consume explicit or it will reintroduce this
class of bug. **Resolved by the v1 ship:** contributors only ever
`append()`/`extend()`; `drain()` at the merge point is the sole consumer —
the assignment that doubled as consume/clear no longer exists.

**Also true:** continuation crosses the process boundary as a parsed reason
*string* (`handle.py:~2422`) — typed payloads won't survive it without
extending the task_store record. In-scope only if msg-3 injection needs to
outlive a continuation.

## 7. Data layer / split brain

- Portable learning is the mechanism-to-be: JSONL-truth stores, maro-import,
  Stage-5 regenerables, secret_scrub — the doors exist (chunks 1–4 shipped
  2026-07-13). What's missing is a **sync/replicate flow** between live
  workspaces (export→import is manual today). That's this arc's data chore,
  and it is what cashes the "topology is a runtime fact" stance.
- Boundary for now: **Hermes owns conversational memory; Maro owns execution
  learning.** No store-level cross-pollination mechanism yet; phantom-
  sidequest status by decree. But the boundary has a defined *flow* across it:
- **Goal enrichment — the interface brain is the user's agent, not a
  pass-through** (Jeremy 2026-07-15). There's already a translation layer
  between what the user says and what the orchestrator is asked; "it could be
  literal, but there's no requirement there." Canonical example: *"where can I
  get fluffy's favorite food"* — Hermes knows fluffy is the user's cat and
  what the food is; a strict pass-through goal likely fails, the enriched one
  ("find local suppliers of [brand] cat food near [town]") succeeds.
  **DECIDED (Jeremy 2026-07-15) — MVP is "we don't care":** the dispatch
  payload requires the **goal, which is assumed enriched**; when the
  interface also has a distinct raw user ask, it passes that too as an
  **optional second field**. Nothing downstream is enrichment-aware in the
  MVP — the goal is the goal for planning, execution, learning, and
  verification. The optional raw ask is *captured data, not consumed
  machinery*: it accumulates exactly the corpus later work needs (memory /
  shared-memory shaping, and untangling "achieved the enriched goal" from
  "got what the user wanted") without building any of that now. Two-author
  provenance, mis-enrichment detection, user-intent-tracing verification:
  all real, all far-reaching, all **deliberately deferred** — "keep it
  simple and we will have to refine later."
- **Inner-processing visibility is a first-class interest here** (Jeremy —
  "still more interested in" this than the memory question): capture the
  right metadata for **both audiences and both timeframes** — the system
  consuming it along-the-way (navigator/director decisions, injection
  handling) and the end user viewing it after-the-fact (what happened and
  why), with both audiences occasionally crossing over (user peeks mid-run
  via msg 2; the system re-reads history at resume). The run-visibility arc
  (captain's log, thread brain, run cards, mini-reports) is the substrate;
  what this arc adds is emitting it in a form the *interface brain* can
  consume and narrate — msg 2 is a consumer view over existing recording,
  not new recording.

## 8. Security posture

- Goal dispatch = code execution. The transport edge is therefore an
  execution-authority edge: **trusted link only** (ssh keys, tailnet), no
  public listener, no shared-secret HTTP.
- The C4 containment work (mount whitelist, structural probe) compounds here:
  a networked dispatcher is exactly the "hostile goal author" the whitelist
  now contains. Container-on is the right default posture for network-sourced
  goals — worth revisiting the box flip when Hermes dispatch goes live.
- Injected mid-flight context is untrusted-ish input entering a live run:
  same scrutiny as goal text (fence, containment, provenance stamp).
- Cross-box secrets: Hermes box gets its OWN credentials; nothing copies
  ~/.claude or workspace secrets across boxes (the auth-volume rule
  generalizes).

## 9. Sequencing

1. **Now (pre-box):** this doc; iterate the skeleton. Seam inventory for §6
   (where step-context is assembled today; what a typed injection input
   needs). **DONE 2026-07-15 → §6a.**
2. **Box lands (2026-07-16):** Hermes up on B (recipe: `~/claude/hermes-maro-trial/`
   + memory notes). Thinnest cross-box slice: enqueue from B over
   ssh/tailscale, run_card back on B. Prove the contract crosses the wire
   before ANY richness.
3. **Then, by leverage:** effort+consent (msg 1) → progress query (msg 2) →
   injection (msg 3, after the seam refactor) → clarification loop (msg 4).
4. **Parallel, box-independent:** deeper/larger test goals (§10) — they
   pressure-test decompose, spend, and compounding, and generate the SF-1
   self-improvement evidence.
5. **Later, channel layer:** iMessage on the Hermes side (Mac hardware now
   exists for it); STT front-end; Telegram stays the fallback.

## 10. Deeper test goals (scoping the asks up)

Current corpus skews small (single-session, <$2, one deliverable). The next
tier should stress multi-session compounding, elevated spend with consent, and
long-horizon decompose:

- **Flagship: the Telegram trading-channel corpus goal.** Scrape 5–6 years of
  one channel's content → analyze → construct + backtest a strategy around it.
  Long-horizon, multi-run, persistent project state (polymarket-edges pattern:
  ledger-driven, deepen-1-add-1), real elevated spend (effort UX's first real
  customer). **Scope guard:** research deliverable — corpus, analysis,
  strategy, backtest. No trade execution (ask-first-for-money line holds).
  Cares: Telegram scraping ToS/rate limits; survivorship + lookahead bias when
  backtesting a tout's public calls (they're public *after* they're right).
- Other tier-up shapes (candidates, pick as they get real): multi-day research
  with interim check-ins (exercises msg 2/4); a goal that legitimately needs
  a consent round-trip (exercises msg 1); a cross-run compounding workspace in
  a new domain.
- Each of these goes in `docs/CAPABILITIES.md` as-phrased when run (standing
  capture rule).

## 11. Open questions (the iteration surface)

1. ~~Effort estimator placement~~ **ANSWERED (Jeremy 2026-07-15): post
   initial-plan-breakdown** — estimate from the plan, not the raw goal (§5).
   Residual: cheap-tier LLM call over the plan vs pure corpus stats — likely
   stats first, LLM only if the buckets misfire.
2. Consent-grant plumbing: where does a per-goal budget override live so the
   budget gate honors it without a standing config write?
3. Progress query for a LIVE run: poll the captain's log/thread brain (cheap,
   staleness-tolerant) vs a run-side status summarizer (costs a call)?
4. Injection semantics under parallel fan-out steps: next-pending is
   well-defined for sequential loops; for a parallel batch, inject into all,
   next boundary, or director-only? (The §6 decision-point framing suggests:
   deliver to the director's next decision boundary, let IT fan out.)
5. Learning-data sync: one-way (dev box → others) first? Conflict story when
   two workspaces both learned? (Probably: append-only JSONL merge + dedup by
   deterministic id — the memory-module design anticipated this.)
6. ~~Persistent channel?~~ **ANSWERED (Jeremy 2026-07-15): no — don't start
   with one.** ssh+poll until a message type proves it can't carry it.
7. When Hermes dispatch goes live, does box `container: on` become the
   standing posture for network-sourced goals? (Likely yes — decide then.)
8. ~~Enrichment metadata format~~ **ANSWERED (Jeremy 2026-07-15): MVP = "we
   don't care."** Goal required + assumed enriched; optional raw user-ask
   field rides along when the interface has one; nothing downstream is
   enrichment-aware yet (§7). Captured-not-consumed — refine later.

## 12. Related standing work (backlog cross-links)

Items elsewhere in BACKLOG/MILESTONES this arc touches — check before
building each stage:

- **Escalation channel** (formerly "1.0 item (a)") — the substrate go-between
  decree (2026-07-12) + escalation file surface ARE msg 4's foundation.
- **Run-visibility residuals** (BACKLOG #17) — msg 2 is a consumer over this
  recording; residuals graduate from polish to protocol prerequisites.
- **Backend-error resilience / auto-resume** (item (h); auto-resume
  deliberately deferred) — cross-box dispatch raises the stakes: a dead run
  behind a network edge is invisible unless resilience surfaces it.
- **Official scheduler/timer layer** (deferred; auto-resume rides it) — same
  reason.
- **Director clarification design** (`project_director_clarification`:
  ask-on-ambiguity, YOLO opt-out, user-level defaults) — msg 4 is its
  transport; §5's clarify-during-planning is its new timing.
- **Portable learning** (chunks 1–4 shipped; 8 provisional decisions await
  Jeremy) — §7's sync flow is the next chunk when the arc needs it.
- **Hosted-free small-LLM tier** (BACKLOG #25, awaiting API keys) — candidate
  lane for cheap protocol chatter (effort phrasing, progress summaries) that
  shouldn't burn the main quota.
- **Time blindness** (Vision) — interactive sessions make "the system's sense
  of elapsed time" user-visible; the filler-UX in §5 is a tiny first contact.

## Non-goals (for now, by decree or common sense)

Public HTTP API / open ports; hive-mind learning-share (opt-in someday,
per portable-learning decrees); trade execution; building forking/alternate-
timeline runs (§6 horizon — recorded, not scheduled); solving split-brain
memory; moving off Telegram before iMessage proves out.
