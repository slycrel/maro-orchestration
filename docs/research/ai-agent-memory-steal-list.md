# Research Synthesis: 6 Posts — AI Agent Memory, Knowledge, Self-Improvement
**Date:** 2026-04-14  
**Persona:** psyche-researcher  
**Confidence:** strong (artifacts directly read; post3 guide text truncated — quality criteria inferred from preview + post author credentials)

---

## Per-Post Extraction

### Post 1: Engramme / Large Memory Models (Santiago/@svpino)
**Core pattern:** Proactive/associative recall — memories surface automatically ("come to you") vs. RAG's query-on-demand. Neuroscience-grounded. Founded by Harvard lab researchers (160+ Nature/ICLR papers).  
**Poe gap:** memory.py and JSONL ledger are retrieval-on-demand (called explicitly). No proactive surfacing. No associative linking (node X triggers recall of related node Y without being queried).

### Post 2: Better Harness / Evals-as-Training-Data (Sigrid Jin/@realsigridjin)
**Core pattern:** Evals = trainable layer for frozen-weight agents. Loop: prod failures → curated evals → harness tweaks → holdout validation. Reward-hacking is real; holdout sets are the fix. Quality > quantity.  
**Poe gap:** No eval harness for Poe's own execution. inspector.py catches failures but doesn't turn them into structured evals. evolver.py improves prompts but without train/holdout discipline — reward-hacking risk exists.

### Post 3: Claude Skills Quality (Avid/@av1dlive + @eng_khairallah1)
**Core pattern:** 80K+ skills, majority bad. Failure modes: wrong-request firing, inconsistent output, edge case breakage. Anthropic engineers have canonical quality bar (16-min talk).  
**Poe gap:** synthesize_skill() does not enforce trigger precision, output schema, or edge case testing before promotion. Skills can graduate on score alone.

### Post 4: Team OS (Aakash Gupta/@aakashg0)
**Core pattern:** Shared GitHub repo = collective brain. CLAUDE.md + skills + automations + learning flywheel. Solves zero-shared-context problem for teams.  
**Poe gap:** All Team OS components exist in Poe for single-user. Only gap: no horizontal lesson pooling across multiple Poe instances.

### Post 5: Anthropic Managed Agents / Lock-in (carsonfarmer + Sarah Wooders)
**Core pattern:** Anthropic managed agents API = Letta API (1yr lag), closed source. Memory-as-API-blocks limits agent action space and increases switching costs by design. Letta verdict: memory must live outside providers.  
**Poe gap:** None. Poe's file-based memory is architecturally correct. Risk only if Poe integrates Claude memory APIs for persistence — it currently does not.

### Post 6: Harness Hill-Climbing + Specialist Local Models (mr-r0b0t)
**Core pattern:** Combine eval harness discipline (post2) with specialist local models per domain. Each domain gets tuned model + its own eval harness. Frontier model is not the end-state.  
**Poe gap:** llm.py routes to frontier models only. No per-task-type routing to specialist models. No per-domain eval harness.

---

## Key Questions

### Q1: Engramme vs. RAG — what's portable to Poe's file-based memory?

| Dimension | RAG | Engramme | Poe today |
|-----------|-----|----------|-----------|
| Recall trigger | Explicit query | Automatic/associative | Explicit call |
| Architecture | Vector similarity | Neuroscience-grounded (proprietary) | JSONL decay/reinforce |
| Surfacing | On-demand | Proactive | On-demand |

**Portable aspects:**
1. **Associative linking** — when recording a memory node, cross-link to related nodes. When any node is accessed, surface its linked neighbors. Implementable in Poe's JSONL with a `related_ids` field + lightweight scorer in reflect_and_record().
2. **Proactive injection** — at step start, run a background pass over recent memory nodes and inject the highest-relevance ones into context without explicit query. knowledge_lens.py already ranks nodes; the missing piece is calling it proactively at loop entry rather than on request.
3. **Decay-as-filter** — Engramme's neuroscience grounding implies Ebbinghaus decay is correct (Poe already does this). No change needed here.

**Not portable (without Engramme access):** The proprietary LMM architecture that enables true associative recall at scale. We can approximate the behavior with file-based tricks, not replicate the mechanism.

---

### Q2: synthesize_skill() quality criteria (what separates good from bad Claude Skills)

Three failure modes from post3 + their corresponding quality gates:

| Failure mode | Quality gate to add to synthesize_skill() |
|-------------|-------------------------------------------|
| Wrong-request firing | Trigger precision test: run skill on 10 off-target inputs, assert 0 activations |
| Inconsistent output | Output schema enforcement: define expected output structure, validate against it before promotion |
| Edge case breakage | Minimum edge case coverage: must pass at least 3 adversarial edge cases before score is computed |

**Implementation sketch for synthesize_skill():**
```python
def synthesize_skill(candidate):
    # 1. Trigger precision gate (new)
    off_target_hits = test_trigger_precision(candidate, n=10)
    if off_target_hits > 0:
        return reject("trigger fires on wrong requests")
    
    # 2. Output schema gate (new)
    if not validate_output_schema(candidate):
        return reject("output schema undefined or inconsistent")
    
    # 3. Edge case coverage gate (new)
    edge_pass_rate = run_edge_cases(candidate, min_n=3)
    if edge_pass_rate < 1.0:
        return reject("fails edge cases")
    
    # 4. Existing score gate (keep)
    if candidate.score < PROMOTION_THRESHOLD:
        return defer()
    
    return promote(candidate)
```

Current synthesize_skill() likely only gates on score. All three new gates must precede the score check — a skill that scores well on happy paths but fires on wrong inputs is the core failure mode.

---

### Q3: Does Team OS add anything beyond Poe's existing user/CONFIG/GOALS/SIGNALS?

**Answer: No, for single-user Poe.** Every Team OS component maps 1:1 to Poe's existing architecture:
- CLAUDE.md → user/CONFIG/GOALS structure
- Shared skills → skills.py + ~/.poe/workspace/skills/
- Automations → heartbeat.py + task_store.py
- Learning flywheel → evolver.py + memory.py lessons pipeline

**One genuinely novel angle:** horizontal lesson sharing across multiple users/instances. Team OS allows 10 people's learnings to compound into a shared brain. Poe has no multi-instance sync. This is only relevant if Jeremy ever runs multiple Poe instances or opens Poe to other users — not a current priority.

**Verdict:** No action needed. Team OS validates current architecture rather than revealing gaps.

---

### Q4: Practical risk of Anthropic managed agents API vs. Poe's file-based approach?

**Lock-in mechanism:** Provider captures memory/state → switching costs rise → customers locked.  
**Anthropic's feature:** Read-only memory blocks + memory block sharing (Letta had this 1yr prior).  
**Letta CEO verdict:** API memory blocks limit agent action space and learning ability.

**Poe's position:** No risk. File-based memory (~/.poe/workspace/memory/) is fully portable, inspectable, version-controllable. Switching LLM providers loses zero state.

**One watch item:** If llm.py or agent_loop.py ever starts persisting state TO Claude's API (e.g., using Claude's new memory APIs for convenience), that creates lock-in. Current code does not do this — keep it that way.

---

## Prioritized Steal List

| Priority | Item | What to steal | Sketch |
|----------|------|--------------|--------|
| **P1** | Eval harness discipline (posts 2+6) | prod failures → structured evals → holdout validation | Add `inspector.log_failure_trace()` → batch to `~/.poe/workspace/evals/`; separate train/holdout split; evolver validates on holdout only |
| **P2** | synthesize_skill() quality gates (post 3) | trigger precision + output schema + edge case gates | 3-gate pre-check before score promotion — see sketch above |
| **P3** | Proactive memory injection (post 1/Engramme) | inject top-N memory nodes at loop entry without explicit query | Call knowledge_lens.rank() at agent_loop entry; inject top 3 nodes into system context; no query required |
| **P4** | Associative memory links (post 1/Engramme) | `related_ids` field on JSONL nodes, surface neighbors on access | Add to reflect_and_record(): cosine-similarity pass over recent nodes, write top-3 related_ids; read in knowledge_lens |
| **P5** | Task-type routing (post 6) | route different task types to different model configs | Extend llm.py adapter selection with task_type tag; stub local model routing even if all routes still go to frontier for now |

**Not stealing:**
- Team OS components (already have them all)
- Anthropic managed agents API (actively avoid)
- Multi-instance lesson sync (not current priority)

---

## Implementation Order Recommendation

1. **Eval harness (P1)** — highest leverage. Evolver currently has no holdout discipline; reward-hacking risk is real and grows as the system matures. This is foundational before any further self-improvement work.
2. **synthesize_skill() gates (P2)** — 80K+ bad skills is a data point about what happens without quality gates. Poe's skill library is small now; add gates before it grows.
3. **Proactive memory injection (P3)** — quick win. knowledge_lens.rank() exists; calling it at loop entry is 5-10 lines. High signal-to-effort ratio.
4. **Associative links (P4)** — medium effort, high value for long-running memory. Implement after P3 validates the injection mechanism.
5. **Task-type routing (P5)** — future-proofing. No local models available now; stub routing only.
