---
status: history
---

# Swarm review & the knowledge-journey commission

*2026-07-20 → 2026-07-21*

Written first-hand by the session that lived it, not excavated — the only era file with a primary source.

## Architecture as it was

Today's architecture — the baseline every other era file compares against. Five subsystems (Interface, Core Loop, Memory/Knowledge, Quality/Self-Improvement, Platform; `docs/ARCHITECTURE_OVERVIEW.md`). NOW/AGENDA lanes in `handle.py`; POWER-tier decompose with stateless per-step execution; 8-substrate recall; always-on verdict+adversarial gate passes with `strict:`-gated council/cross_ref; claim_probe grounding; Tier-0 → hosted-free → local → paid validation ladder; GOAL_BRAIN compiled truth under SF-13; worktree-per-parallel-step. Verify→learn V1–V5 closed; navigator lesson-inject in A/B watch.

## Discoveries & aha moments

### Maro already implements the article's economics
Cursor's agent-swarm-model-economics piece (read 2026-07-20 with five code-grounded review agents) argues for expensive-planner/cheap-worker splits, context partitioning over parallelism, and a curated Field Guide. Maro had independently converged on all three — planner at POWER, stateless workers, playbook injection. The transferable findings were not economics but **knowledge flow**: where learning is captured, carried, and re-enters prompts.

### The half-closed-loop pattern (the review's headline)
Three mechanisms shipped a live read side whose write side never landed: `decisions.jsonl` (recall reads it; zero writers ever), `contradict_pattern` (`knowledge_lens.py:373`, zero callers — making `refight_rule` unreachable), and ancestry (dual-source, write-side unification open). The part of an idea that fit in one session shipped; the rest stayed conversation. Named as a *pattern* — instance-fixes are chunks 3–4 of the swarm-review plan; the systemic countermeasure is the proposed wiring-census tripwire (every store with a reader must have a live writer; every A/B a readout), generalizing `test_defaults_doc.py`.

### The playbook injection-horizon bug
`inject_playbook` (`playbook.py:109-135`) takes an 800-char head window; the live file's head is ~28 duplicate test-era "Be more concise" entries, so learned content has never reached a prompt. The Field-Guide analog existed and was silently dead cargo.

### Poe's independent review adds, and misses
Hermes/Poe read the same article cold. Genuine additions: coordination-waste **anti-metrics**; a three-way decision-ownership taxonomy (leaf-local / parent-owned / escalation-trigger — explicitly not parent-always-wins, matching Jeremy's own doubt); end-to-end model-route evaluation. Misses: called council live (it has zero organic uses) and claim-probing novel (shipped, 78% of contestations DISMISSED_BY_PROBE). Cross-model review remains decorrelated in *perspective* but not in *facts* — it needs code grounding.

### Jeremy's five decrees (2026-07-20)
1. **Give up the cheap split** — under flat-rate it's a non-decision; unify execution at MID.
2. **Local LLMs are "in the way"** — a nice OSS dream; remove the wiring, revisit in a year or three, stay LLM-agnostic at adapter seams.
3. **Personas stay** — multi-angle examination of the same facts is key to taste/judgement; disuse is not a cull reason. (Corrected the session's initial misread.)
4. **Skip the 3-arm spend experiment** — don't get bogged down in spend-implementation; the target is the general *pattern of discretion*, spend being one simple lever that roughly proxies capability.
5. **Fork contract is not parent-always-wins** — children need an evidence-based escalation path.

### The knowledge-journey commission
Jeremy rejected going straight to implementation: first assemble this history ("I trust your judgement but sadly, not yet your context"), including chat history and other artifacts, with pros/cons of each era vs today. His stated worry — circling ideas, partial implementation, "literal in some places, rather than systemic" — was then *confirmed with evidence* by the side-channel mining ([side-channels.md](side-channels.md)): the generative loop (principle named conversationally → partially built → orphaned by substrate failure → rebuilt later), with two accelerants (conversational grants don't persist as machine state; interleaved side-asks clear the standing agenda). Meta-finding: the code-layer half-closed loops and the channel-layer unpersisted decrees are the same disease.

## Pros of today's architecture (what the review confirmed strong)

- The economics the industry is now writing up were already in place — empirically, via the verify-fail ladder, not by decree.
- Verification depth (claim_probe, deterministic provenance, adversarial pass) exceeds the article's proposals; 4/4 measured gate false-passes were narration-vs-evidence, which provenance already catches.
- The lesson lifecycle (tiering, decay, graduation, refight *design*) out-engineers the article's append-only Field Guide — where it's actually wired.

## Cons of today's architecture (what the review found broken)

- Half-closed loops (above) — still present; chunks 3–4 target the instances, census tripwire the class.
- Playbook horizon bug — still present; chunk 2.
- Review lenses share evidence paths (prompt costume ≠ decorrelation) — still present; chunk 5.
- No discretion/coordination-waste readout — still present; chunk 7.
- Dead weight: local-model wiring, dead config tiers, stale docs — chunk 1.

## What we believed then (open bets, judged by future eras)

- That evidence-path diversity (transcript / artifact-only / probe-armed) beats persona-prompt diversity for review decorrelation.
- That a wiring census can hold the half-closed-loop class shut mechanically.
- That spend-as-discretion-proxy is a placeholder until a better judgement metric emerges from the chunk-7 readout.
- That history-first before implementation ("context before judgement") pays for its detour cost. This document is that bet.

## Lost good ideas

None lost yet — this era's candidates for loss are the plan's unbuilt edges: the wiring-census tripwire and folding the side-channel C-findings into the failure-pattern corpus (`docs/CAPABILITIES.md`). If a future reader finds these unshipped, this is the circling ledger's next row.

## Sources

cursor.com/blog/agent-swarm-model-economics; Poe/Hermes commentary (2026-07-20, in-session); five-agent code-grounded review (this session); [side-channels.md](side-channels.md); swarm-review plan (`~/.claude/plans/abundant-gathering-lagoon.md`); `playbook.py:109-135`, `knowledge_lens.py:373`, `skill_lifecycle.py:684-694`.
