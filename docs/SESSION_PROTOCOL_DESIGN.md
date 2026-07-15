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

- **Internal:** an effort estimate at goal-admission time — pre-flight is the
  natural home; the cuts-first planner + the ~68-run verdict corpus + Manti
  envelope data ($2.47 → $1.52 post-fix) are the calibration inputs. Buckets,
  not dollars: `quick / normal / involved / heavy` (names TBD).
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

## 7. Data layer / split brain

- Portable learning is the mechanism-to-be: JSONL-truth stores, maro-import,
  Stage-5 regenerables, secret_scrub — the doors exist (chunks 1–4 shipped
  2026-07-13). What's missing is a **sync/replicate flow** between live
  workspaces (export→import is manual today). That's this arc's data chore,
  and it is what cashes the "topology is a runtime fact" stance.
- Boundary for now: **Hermes owns conversational memory; Maro owns execution
  learning.** No cross-pollination mechanism yet; phantom-sidequest status by
  decree. First real bite likely: Hermes knows user preferences that should
  shape goal scoping.

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
   (where step-context is assembled today; what a typed injection input needs).
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

1. Effort estimator placement: pre-flight vs director vs intent-classifier —
   who owns the estimate, and is it a cheap-tier LLM call or corpus stats?
2. Consent-grant plumbing: where does a per-goal budget override live so the
   budget gate honors it without a standing config write?
3. Progress query for a LIVE run: poll the captain's log/thread brain (cheap,
   staleness-tolerant) vs a run-side status summarizer (costs a call)?
4. Injection semantics under parallel fan-out steps: next-pending is
   well-defined for sequential loops; for a parallel batch, inject into all,
   next boundary, or director-only?
5. Learning-data sync: one-way (dev box → others) first? Conflict story when
   two workspaces both learned? (Probably: append-only JSONL merge + dedup by
   deterministic id — the memory-module design anticipated this.)
6. Does anything in msg 1–4 force a persistent channel, or does ssh+poll
   carry the whole protocol? (Suspicion: ssh+poll carries it further than it
   has any right to.)
7. When Hermes dispatch goes live, does box `container: on` become the
   standing posture for network-sourced goals? (Likely yes — decide then.)

## Non-goals (for now, by decree or common sense)

Public HTTP API / open ports; hive-mind learning-share (opt-in someday,
per portable-learning decrees); trade execution; building forking/alternate-
timeline runs (§6 horizon — recorded, not scheduled); solving split-brain
memory; moving off Telegram before iMessage proves out.
