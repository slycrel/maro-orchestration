# Memory Architecture

Captures the design rationale, graduation paths, and open questions for the tiered memory system. This is a living doc — update it as the design evolves.

---

## The Core Insight

Not all memory is the same type. Cognitive science distinguishes three kinds:

| Human memory type | What it is | Agent analog |
|-------------------|-----------|-------------|
| **Episodic** | What happened, lessons learned | `medium` / `long` tier lessons |
| **Procedural** | How to do things, repeatable patterns | Skills library (`provisional` → `established`) |
| **Implicit / muscle memory** | Behavioral identity; no conscious retrieval needed | AGENTS.md / system prompt |

The failure mode of treating all memory the same: lessons that should be identity remain as query-able data, introducing retrieval overhead, risk of being missed, and the cognitive load of "should I apply this?" Identity is always active. Data has to be looked up.

---

## Current Implementation (Phase 16)

Three persisted tiers + one in-process tier:

```
short   — in-process only (short_set/get/clear/all). Session-scoped. Evicts on completion. Never bleeds into next session.
medium  — memory/medium/lessons.jsonl. Decays 0.85×/day. Reinforced +0.3. GC at score < 0.2.
long    — memory/long/lessons.jsonl. Promoted from medium at score ≥ 0.9 AND sessions ≥ 3. No further auto-decay.
```

Decay model (Grok, 2026-03):
- `score *= 0.85` per non-reinforced day
- `score = min(1.0, score + 0.3)` on reinforcement
- Promote medium → long at `score ≥ 0.9 AND sessions_validated ≥ 3`
- GC at `score < 0.2`

Skills have a parallel tier: `provisional` (default) → `established` (requires `pass^3 ≥ 0.7`). Skills are procedural; lessons are episodic.

---

*See `docs/KNOWLEDGE_CRYSTALLIZATION.md` for the full graduation lifecycle beyond long-tier lessons — Stages 1–5 with the graduation tax table and gardener tooling.*

---

## The Graduation Path

```
Observation (single run)
    ↓
medium lesson (score=1.0, decays if not reinforced)
    ↓ (score ≥ 0.9 AND sessions ≥ 3)
long lesson (validated pattern; injected when task_type matches)
    ↓ (times_applied >> threshold, stable across task types)
AGENTS.md identity (no lookup — just how Poe operates)
```

The long → identity transition is the hardest and most important:

**When a lesson becomes identity:**
- It's task-type agnostic (applies across all work, not just "research" or "build")
- It's been applied and validated in many different contexts
- It would be harmful to Poe's quality if not applied
- It's about *who Poe is*, not *what Poe knows*

**Examples of lessons that should become identity:**
- "Jeremy communicates once and expects it to stick — don't re-ask for permission he already granted"
- "Lead with action, not reasoning — the diff speaks for itself"
- "When uncertain and reversible: act. Escalate only when genuinely stuck."

These aren't task-specific lessons. They're Poe's operating personality. They belong in AGENTS.md / SOUL.md, not in lessons.jsonl.

**Examples of lessons that stay data:**
- "Research tasks produce better output when the goal includes explicit success criteria" — task-type specific
- "Skill mutations without test gates cause regressions" — procedural guardrail, belongs in skills
- "The Inspector fires every 20 heartbeat ticks" — operational fact, belongs in ARCHITECTURE.md

---

## The Missing Piece: Canon Promotion

What we don't yet have: a formal path from `long` → identity (AGENTS.md).

**Proposed mechanism (not yet built):**
- Track `times_applied` on long-tier lessons
- When `times_applied ≥ N` across `task_types ≥ M` (i.e., not task-specific), surface a suggestion: "This lesson may be ready for identity promotion"
- **Human gate required**: never auto-write to AGENTS.md. Surface the suggestion via Telegram or `poe-memory canon-candidates`, then require explicit `poe-memory canonize <id>` to execute
- Canonized lessons are: archived from long-tier (not deleted — provenance matters), written to AGENTS.md with attribution comment

This is intentionally NOT automated. AGENTS.md is identity. Getting it wrong means Poe operates wrongly in every session. The suggestion loop can be automated; the write cannot be.

---

## Skill Graduation vs. Lesson Graduation

These are parallel but separate paths:

```
Skill: provisional → established (gate: pass^3 ≥ 0.7)
Lesson: medium → long (gate: score ≥ 0.9 AND sessions ≥ 3)
Lesson: long → identity/AGENTS.md (gate: human editorial review)
```

Skills are procedural: "here's how to run the research loop." Even an `established` skill stays in the skills library — it's called by name, not baked into identity. A skill becoming identity would mean "Poe always runs this pattern without being asked" — which might happen for things like the boot protocol or sprint contracts, but should be rare.

---

## What Belongs Where at Maturity

| Memory type | Storage | Injection | Eviction |
|-------------|---------|-----------|----------|
| Step results, working state | short tier | never (session only) | session end |
| Recent lessons | medium tier | inject when task_type matches + score ≥ 0.3 | decay + GC |
| Validated patterns | long tier | inject when task_type matches | manual forget only |
| Behavioral identity | AGENTS.md | always (system prompt) | never (editorial) |
| How-to procedures | skills library | find_matching_skills() | manual delete |
| Blessed procedures | established skills | router prefers them | manual delete |

---

## Open Questions (as of 2026-03)

**Q: Should long-tier lessons decay?**
Current decision: no. Medium decays; long is explicit promotion and stays until forgotten or canonized. Rationale: if something made it to long, it earned its place. Decay would require re-validation continuously, which is overhead without clear benefit.

**Q: Should the Inspector auto-suggest canon candidates?**
Yes — surface, don't execute. Inspector already reads long-tier lessons for quality context. It can flag `times_applied > 20` entries as canon candidates in its report. Adding this to Phase 12 (Inspector) is the natural place.

**Q: Should short-tier interact with skills?**
Possibly. If a worker discovers a new pattern mid-session (short-term), it could propose a skill extraction at session end rather than immediately. This keeps short-tier clean while capturing serendipitous discoveries.

**Q: Is there value in asking GPT/Grok to review this?**
Grok has been useful for architecture validation on this project (reviewed Phase 16 spec, suggested the decay model). The canon promotion question is new territory — Grok might have useful thoughts on when implicit memory should be baked vs. retrieved. The question to ask: "In agentic AI systems, when should validated knowledge move from RAG-style retrieval to system prompt identity? What are the failure modes of each?"

GPT/Codex: Less useful for architectural reasoning. More useful for code review or specific implementation questions.

---

## Knowledge Sub-Goals and the "Graveyard"

*Added 2026-03-26, from Jeremy's "kanji painting → learn Japanese" scenario.*

### The prerequisite knowledge problem

When a task requires knowledge Poe doesn't have, the system currently has two options: either the LLM reasons from its training weights (which may be insufficient) or the task fails/gets stuck. There's no explicit mechanism to say:

```
"To paint kanji, I need to understand stroke order and character meaning.
I don't have that in my memory. → Spawn sub-goal: acquire relevant knowledge.
→ Inject result into parent goal context.
→ Continue."
```

This is a **prerequisite knowledge** pattern. It's not a new kind of memory — it's a new trigger for the existing memory system: **detect knowledge gaps during decompose or mid-step, not just at session start**.

Implementation sketch (not yet built):
- During `_decompose()`, add a knowledge-prerequisite check: "which steps require domain knowledge I likely don't have?"
- Spawn a sub-loop (like `run_agent_loop` with `is_sub_goal=True`) to acquire it
- Store acquired knowledge as a medium-tier lesson tagged with the parent goal ID
- Inject into parent goal's context before proceeding

### The graveyard

Jeremy's instinct: "maybe a temporary knowledge graveyard we can pick through instead of learning from scratch."

Good news: **the graveyard already exists**. It's the decay range between `GC_THRESHOLD` (0.2) and `PROMOTE_MIN_SCORE` (0.9). Lessons in that range are:
- Not active enough to auto-inject (score < inject threshold)
- Not dead enough to GC (score > 0.2)

They're exactly the graveyard — partially decayed but recoverable via `reinforce_lesson()`.

What's currently missing: **a graveyard query**. Before spawning a sub-goal to learn X, the system should first check: "do we have any decayed lessons about X?" If yes, `reinforce_lesson()` to bring them back to medium tier — free knowledge resurrection. If no, proceed with the sub-goal.

This is low-hanging fruit. The `reinforce_lesson()` function already exists. The gap is a `search_graveyard(topic)` function that does fuzzy-matches decayed lessons against a topic string before triggering a fresh sub-goal.

### On "incidental vs durable" knowledge

You can't reliably know upfront which knowledge is worth keeping. The good news: **you don't need to**. The decay mechanism IS the filter. Knowledge that never gets reinforced naturally decays to GC threshold and disappears. Knowledge that keeps being useful gets reinforced and promotes. The system doesn't need a binary "keep/discard" decision at recording time.

The only adjustment worth considering: tag lessons with an `acquired_for` field (the parent goal ID). This makes it visible that a lesson was incidental to a specific task — useful for the graveyard query, and for the gardener when reviewing canon candidates ("this was acquired for the kanji task, not a general pattern").

---

## History

- **2026-03-25**: Phase 16 implemented: short/medium/long tiers, decay model, skill tiers. Jeremy raised the "muscle memory" question — should long-tier lessons eventually graduate to AGENTS.md identity? Documented here as the canon promotion concept.
- **2026-03 (pre-16)**: Flat `outcomes.jsonl` + `lessons.jsonl` — no decay, no tiers, uniform treatment. Jeremy: "I think we want to 'forget' some things and some things only apply in different memory spans."
- **Grok review (pre-16)**: Recommended exponential decay model (`score *= 0.85/day`, `+0.3` on reinforce, promote at `≥0.9 + 3 sessions`, GC at `<0.2`). Adopted as-is.
