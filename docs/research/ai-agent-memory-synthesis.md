# AI Agent Memory, Knowledge & Self-Improvement — Research Synthesis

**Date:** 2026-04-14  
**Informs:** memory.py, skills.py, evolver.py, inspector.py, llm.py  
**Confidence:** strong (posts 1–6 directly read; post3 guide preview only — quality criteria inferred from preview + author credentials)  
**Sources:** 6 X posts (svpino/Engramme, realsigridjin/Better Harness, av1dlive/Claude Skills, aakashg0/Team OS, carsonfarmer/Managed Agents, mr_r0b0t/Hill-climbing)

---

## Key Questions & Answers

### Q1: What makes Engramme different from RAG — what's portable to Poe's file-based memory?

| Dimension | RAG | Engramme | Poe today |
|-----------|-----|----------|-----------|
| Recall trigger | Explicit query | Automatic / associative | Explicit call |
| Architecture | Vector similarity search | Neuroscience-grounded (proprietary LMM) | JSONL decay/reinforce |
| Surfacing | On-demand | Proactive — "memories come to you" | On-demand |

**The core distinction:** Engramme memories surface *without being asked*. No search, no prompt. The architecture proactively decides what's relevant.

**Portable to Poe (without Engramme access):**
1. **Proactive injection** — call `knowledge_lens.rank()` at `agent_loop` entry, inject top-3 nodes into system context before step execution. Zero new infrastructure; 5-10 lines.
2. **Associative linking** — add `related_ids` field to JSONL nodes via cosine-similarity pass in `reflect_and_record()`. When any node is accessed, surface its linked neighbors. Approximate associative recall with file-based indirection.
3. **Decay as filter** (already correct) — Engramme's neuroscience grounding confirms Ebbinghaus decay is correct. Poe's existing decay implementation needs no change.

**Not portable:** The proprietary LMM mechanism for true associative recall at scale. We can approximate the *behavior*; we cannot replicate the *mechanism*.

---

### Q2: What separates well-built Claude Skills from the 80K+ poorly-built ones?

Three canonical failure modes from Anthropic engineers (post3):

| Failure mode | Frequency | Root cause |
|-------------|-----------|-----------|
| Wrong-request firing | Most common | Trigger too broad or poorly specified |
| Inconsistent output | Second | No output schema enforced |
| Edge case breakage | Third | Tested on happy paths only |

**Quality gates `synthesize_skill()` must enforce (in order):**

```python
def synthesize_skill(candidate):
    # Gate 1: Trigger precision — must fire 0 times on 10 off-target inputs
    off_target_hits = test_trigger_precision(candidate, n=10)
    if off_target_hits > 0:
        return reject("trigger fires on wrong requests")

    # Gate 2: Output schema — defined and validated
    if not validate_output_schema(candidate):
        return reject("output schema undefined or inconsistent")

    # Gate 3: Edge case coverage — must pass >=3 adversarial edge cases
    edge_pass_rate = run_edge_cases(candidate, min_n=3)
    if edge_pass_rate < 1.0:
        return reject("fails edge cases")

    # Gate 4: Score gate (existing — keep, runs last)
    if candidate.score < PROMOTION_THRESHOLD:
        return defer()

    return promote(candidate)
```

All three new gates precede the score check. A skill scoring well on happy paths but firing on wrong inputs is the primary failure mode.

---

### Q3: Does Team OS add anything beyond Poe's existing user/CONFIG/GOALS/SIGNALS?

**Answer: No — for single-user Poe.**

Every Team OS component maps 1:1 to Poe's existing architecture:

| Team OS component | Poe equivalent |
|-------------------|---------------|
| CLAUDE.md shared context | user/CONFIG/GOALS structure |
| Shared skills | `skills.py` + `~/.poe/workspace/skills/` |
| Shared automations | `heartbeat.py` + `task_store.py` |
| Learning flywheel | `evolver.py` + `memory.py` lessons pipeline |

**One genuinely novel angle not in Poe:** horizontal lesson sharing across multiple users/instances. Team OS allows 10 people's learnings to compound. Poe has no multi-instance sync — only relevant if multiple Poe instances run concurrently or Poe is opened to other users. Not a current priority.

**Verdict:** Team OS validates Poe's architecture. No action needed.

---

### Q4: Practical risk of Anthropic managed agents API vs. Poe's file-based approach?

**Lock-in mechanism:**  
Provider captures memory/state → switching costs rise → customers locked in. Anthropic's features (read-only memory blocks, memory block sharing) were in Letta ~1 year prior, now closed-source.

**Letta CEO (Sarah Wooders) verdict:**  
API-based memory blocks limit agent action space and learning ability. Memory must live outside model providers.

**Poe's position:**  
No current risk. `~/.poe/workspace/memory/` is fully portable, inspectable, version-controllable. Switching LLM providers loses zero state.

**One watch item:**  
If `llm.py` or `agent_loop.py` ever persists state *to* Claude's API (e.g., using Claude memory APIs for convenience), that creates lock-in. Current code does not. Keep it that way.

---

## Prioritized Steal List (ranked by Poe gap size)

| Priority | Item | Gap | Source | Implementation sketch |
|----------|------|-----|--------|-----------------------|
| **P1** | Eval harness + holdout discipline | Large — evolver has no holdout; reward-hacking risk grows with system maturity | Posts 2, 6 | `inspector.log_failure_trace()` → batch to `~/.poe/workspace/evals/train/`; evolver validates against `evals/holdout/` only; separate curator step for quality over quantity |
| **P2** | `synthesize_skill()` 3-gate pre-check | Medium — skills can graduate on score alone; trigger precision and edge case gates absent | Post 3 | Add gates in order: trigger precision → output schema → edge case coverage → score. Reject before score computation. |
| **P3** | Proactive memory injection at loop entry | Medium — knowledge_lens exists but is never called proactively | Post 1 (Engramme) | In `agent_loop.py` step entry: call `knowledge_lens.rank(context, top_n=3)`, prepend results to system prompt. ~10 lines. |
| **P4** | Associative JSONL links (`related_ids`) | Medium — nodes are isolated; no neighbor surfacing on access | Post 1 (Engramme) | In `reflect_and_record()`: cosine-similarity pass over last 50 nodes, write top-3 as `related_ids`; in `knowledge_lens`: surface neighbors when parent node activated. |
| **P5** | Task-type routing stub in `llm.py` | Small now, grows with scale | Post 6 | Add `task_type` tag to LLM call metadata; adapter selection checks tag first; all routes still go to frontier — stub for future specialist model routing. |

**Not stealing:**
- Team OS components (already have all of them)
- Anthropic managed agents API (actively avoid — lock-in by design)
- Multi-instance lesson sync (not current priority)

---

## Implementation Order

1. **P1 — Eval harness** (foundational): evolver reward-hacking risk is real and compounds. Do this before further self-improvement work.
2. **P2 — synthesize_skill() gates** (protective): Poe's skill library is small now; add quality gates before it grows.
3. **P3 — Proactive memory injection** (quick win): `knowledge_lens.rank()` exists; calling it at loop entry is ~10 lines. Validates the injection path for P4.
4. **P4 — Associative links** (medium effort): implement after P3 confirms proactive injection works.
5. **P5 — Task-type routing stub** (future-proofing): low urgency; no local models available yet.

---

## Sources

- Post 1: Santiago (@svpino) on Engramme / Large Memory Models — https://x.com/svpino/status/2042275938390639069
- Post 2: Sigrid Jin (@realsigridjin) on Better Harness paper — https://x.com/realsigridjin/status/2042440330503733343
- Post 3: Avid (@av1dlive) + @eng_khairallah1 on Claude Skills quality — https://x.com/av1dlive/status/2042172428127002906
- Post 4: Aakash Gupta (@aakashg0) on Team OS — https://x.com/aakashg0/status/2041984945380585785
- Post 5: carsonfarmer + Sarah Wooders on Anthropic managed agents — https://x.com/carsonfarmer/status/2042038527639068763
- Post 6: mr-r0b0t on harness hill-climbing + specialist local models — https://x.com/mr_r0b0t/status/2041930298238087464
