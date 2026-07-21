---
status: history
---

# Side Channels: what the git history can't show

*Cross-era companion to the knowledge-journey era files. Mined 2026-07-20 from the Telegram export, the Grok review rounds, and the Codex feedback file — the conversational record around the repo, Feb–Apr 2026.*

## Coverage & verification

- **Telegram** (`~/claude/telegram-export/ChatExport_2026-04-16/result.json`, 3,225 messages, 2026-02-05 → 2026-04-15): 100% of Jeremy's 1,163 messages read; ~3% of Poe's replies, targeted. **The export ends Apr 15** — no channel coverage after that; the repo record and auto-memory carry the later story.
- **Grok reviews**: `~/claude/grok-response{,-2,-3}.txt` (Mar 26/29/30) — fully read. Rounds 4–5 (incl. the Captain's Log round) are not on disk in `~/claude/`.
- **Codex**: `~/claude/codex-feedback-poe.txt` (Mar 26) — fully read; despite the name it is the Poe/Codex conversation containing Codex's clean-checkout review.
- Quotes marked ✓ were re-verified verbatim against the raw files by a second pass; unmarked quotes are the miner's transcription (single-pass).

## The generative loop (the headline finding)

The channels show a cycle the repo's git history cannot: **Jeremy names a principle conversationally (often as an aside or joke) → it gets partially built under whatever substrate is current → substrate/session/token failure wipes or orphans it → the principle resurfaces months later and is rebuilt, better, under the next substrate.** NOW-lane, heartbeat, memory, dashboard, escalation, and concurrency limits all follow this exact arc.

Two accelerants of partial implementation are visible in the channel record:

1. **Conversational grants/directives don't persist as machine state.** "I'm missing something. You're asking me for approval, but I just gave it" (Feb 6); "you're a co-pilot, not a passenger... we keep ending back up here" (Mar 3). The loop only ever broke via config, never via language. Session losses compounded it: every grant died with its session. GOAL_BRAIN + SF-13 are the eventual cure — invented four months after the disease was first named.
2. **Interleaved side-asks silently clear the standing agenda.** Jeremy diagnosed it himself in real time: "I think I derailed things by asking to pause and use the new models; the implication was to keep iterating automatically after we updated, but I think that got lost" (Mar 8). The July Godot replay finding (agenda-state divergence, not capability) is the same mechanism, independently rediscovered.

Jeremy named the overall pattern on **Apr 11**: "We're getting implementation drift vs our stated goals." ✓ — three months before the 2026-07-20 session that commissioned this history for the same reason.

## Aha moments born in the channels

- **Visibility → Reliability → Replayability came from Telegram, not the repo** (Mar 28): "when architecting you need basically 3 things for data streams, and they build on each other. Visibility -> Reliability -> replayability" ✓ — a conference anecdote, adopted into the architecture docs within the hour, along with Poe's counterpart coinage "debugging by séance." The maturity doctrine that now sequences the roadmap started as an aside.
- **The introspection ride-along** (Mar 28): "What we are doing manually here and with claude feels like what our orchestration layer should be doing for itself... maybe a subconscious style sub-system to the conscious mind of the orchestrator." Poe's refinement — lenses must emit *actions*, not commentary, else the system is "very eloquent about its failures, still failing the same way tomorrow" — is the direct ancestor of introspect/observer/verify→learn.
- **The first adversarial cross-model review** (Mar 26): Codex's clean-checkout verdict on the 58-commit Claude expansion — genuine work, but `cli.py`/`orch.py` are "gravitational singularities" ✓ and the project risks becoming a "system about systems" ✓. The opposite-model-review dynamic later institutionalized as the adversarial-review skill started here.
- **The trust rupture** (Mar 19 → Apr 4): "I just looked at the token usage. I'm having a hard time believing you have been working all day" → "I'd hate to set you up to lie to me without the infra to support your claims... I suspect some of that might be happening" ✓ (Apr 4). The channel-side origin of the positive-evidence principle and claim probing.
- **Claude Code arrived as overflow labor during a token drought, not by design** (Mar 21–26): Codex Spark expired, GPT tokens ran dry, five days of channel silence, then "Since it was so long. I got claude involved ($20/mo plan, mostly sonnet use)." idea.md is dated mid-drought (Mar 23). Within a week: Max plan; within a month, Poe's role had shrunk to external reviewer.
- **Session-loss trauma is the origin of the memory obsession** (Feb 5–9): four session losses in five days; Jeremy hand-reconstructed chat history into text files twice; Poe forgot its own name. Every later memory investment traces here.
- **Mar 1 was the constitutional convention**: a single ~2h exchange containing the NOW-pipeline ("simpler than a full orchestration suite that has a bunch of beauromancy going on" — miner transcription), queue limits, two failure classes, escalation-as-stakeholder-clarification, audit-log-vs-actionable-memory, personas-as-avatars, and "I think we should move our failures downstream more." Almost every later era re-implements a clause of this message.
- **Bitter-Lesson gut punch + Mode-3 factories** (Mar 30, Grok): the engineered hierarchy "is classic over-engineering — you're embedding your discoveries about orchestration instead of letting the AI discover it"; and the Mode 2→3 framing (agents self-specifying work from signals) that prefigures the goal-brain direction.
- **The Grok steal lists (Mar 29) predicted the July research agenda**: ralph verify loops, three-layer memory compression, hybrid retrieval (vector+BM25+RRF), graph lesson edges, error nodes, task locking — still substantially the top of the steal list four months later.

## Circling ledger (idea → dropped → re-proposed)

| Idea | Rounds | Arc |
|---|---|---|
| NOW-lane | 3 | Mar 1 proposal → built in openclaw-orchestration (Mar 26 expansion) → rebuilt in Maro Jul 17. Strongest single confirmation of the circling pattern. |
| Memory | ≥6 | Feb 5/6/7-8, Mar 1, Mar 10, Mar 26+, Mar 29, July — each cycle shipped a layer; the graph/hybrid-retrieval level named Mar 29 is still the open item. |
| Heartbeat / self-healing | ~7 | Feb 5 → Jul heartbeat-gate *design*. Longest circle; every failover that mattered in the channel era was actually recovered manually by Jeremy. |
| "Iterate until done" (ralph) | ~7 | Feb 24 → "Why wait?" (Mar 5 ✓) → institutionalized only once *bounded* (July: ralph loops inside a structured phase). |
| Dashboard / visibility | 4+ | Feb 16 → Phase 36 "done" → Jeremy, Mar 29: "it's in theory done but I've never even used it... more of a prop in a play than a meaningful one" ✓ → real visibility only in the July run-visibility arc + public viewer. Best verbatim illustration of partial implementation. |
| Polymarket | 3 | Product (Feb) → archived (Mar 19) → regression-test goal (Mar 28) → persistent research workspace (Jul). Each revival in a different role. |
| X/Twitter access | ≥8 | Feb 8 → Mar 19; enormous effort, never durably solved in-channel. |
| Sandbox / Docker | ≥4 | Feb 5 on → off → blocked → "we've tried that a few times, it doesn't end well" (Mar 1) → returns via container executor era. |
| Concurrency limits | 3 | Mar 1 design → Mar 6 blowout ("let's go for 2") → July concurrency-hardening arc with its own limit footgun. |
| Escalation / clarify-first | 3+ | Mar 1-2 → still landing as of July (director-clarification design). |
| Steal-don't-adopt | many | The control case: a *value* rather than a feature — circled repeatedly and **converged completely** (Feb 6 ClawRouter → Mar 27 "swipe the code" → July feedback rule). Values converge; features orphan. |

## Language-precision cases

- **Quantifier decay**: "green light to implement all of the M stages" (Mar 11) → only M1 shipped → "I think I asked for all the roadmap M items" (Mar 12).
- **"Iterate" heard as a bounded batch**: "still seems arbitrarily gated to 'x tasks per run'. Not what I asked... Why wait?" ✓ (Mar 5; recurs Mar 3/19/20). The most repeated misread in the corpus.
- **Time expressions don't survive contact**: "5-10 5 min cycles" → 2 hours (Mar 20); "last time I said work all night and it was 20 minutes" (Mar 27-28).
- **Informational vs directive**: named by Jeremy as early as Feb 13 — "we have informational/communication oriented messaging and directive oriented messaging. I think we are both still learning where the lines are" — the ancestor of the July "need/worth-looking-at = evaluation ask" rule.
- **Project-boundary ambiguity**: orchestration-vs-first-use-case intermingling (Mar 4-5) took days to unwind.

**Meta-finding**: a large share of apparent partial implementation was *conversational decrees not backed by machine state* — words Jeremy reasonably treated as binding that no system persisted. The half-closed-loop pattern found in the 2026-07-20 code review (read-side live, write-side never shipped: decisions.jsonl, contradict_pattern, ancestry) is the same disease at the code layer: the part of the idea that fit in one session shipped; the rest existed only as conversation.

## Sources

Telegram export `ChatExport_2026-04-16/result.json`; `grok-response.txt`/`-2`/`-3`; `codex-feedback-poe.txt`; `orchestrator-test-recipes` commit log (PM/dev experiment, ~30 commits Apr 12-14, dormant after "dev round 6"). Mined by a single-pass agent 2026-07-20; pivotal quotes second-pass verified (✓) where marked.
