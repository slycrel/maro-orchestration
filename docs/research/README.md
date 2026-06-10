# Research Track — Human Psychology, Neurology, Philosophy

This directory stores targeted research outputs from Phase 29.

The research is not a bottoms-up literature survey — it's directed by specific
questions that come up when building Poe. Each artifact is tied to the phase or
design question it informs.

## Active question queue

| Question | Informs | Priority |
|----------|---------|----------|
| How does spaced repetition interact with confidence/score signals? | Memory decay model (Phase 16/27) | High |
| What distinguishes tacit vs. explicit knowledge in expertise research? | Crystallization Stage 4→5 (Phase 22) | High |
| Kahneman System 1/2: when should an autonomous agent deliberate vs. act? | Loop decompose/execute split | Medium |
| What does Enneagram research say about 6w5 + INFJ communication failures? | Phase 28 companion persona | Medium |
| Sleep consolidation analogies for memory tier promotion? | Memory decay model | Low |
| What do complexity scientists say about "rule emergence" vs. "rule design"? | Stage 5 (Skill→Rule) | Low |

## Artifact format

Each research output is a Markdown file with:
- **Question**: the specific question that triggered the research
- **Key findings**: 3–5 bullet points, source-grounded
- **Implications**: how this changes (or doesn't change) the relevant design
- **Sources**: links or citations
- **Date**: when this was researched

Use the `psyche-researcher` persona to generate these.

## Completed research

| Artifact | Question answered | Date |
|----------|------------------|------|
| `productive_persistence.md` | When should an agent persist vs. pivot vs. quit? Full implementation guide: definition, 6 core dimensions (hypothesis-narrowing, tiered retry budget, failure taxonomy, recovery ladder, zoom-out signals, durable artifacts), decision tree, current codebase state, signal implementation status, dissent (Credé grit critique), recommendation (3-wave implementation roadmap), and 9 concrete next actions. Cross-linked to lat.md/ nodes. | 2026-05-12 |
| `productive-persistence.md` | Earlier synthesis: ML/psychology research on productive persistence (Duckworth, Kapur, RL). Theoretical depth; used as source for `productive_persistence.md`. | 2026-03-27 |
| `zoom-metacognition-adaptive-expertise.md` | How do experts toggle between object-level execution and meta-level goal review? Adaptive expertise, CFT, crystallization failure mechanisms, 15 Poe implementation recommendations. | 2026-05-12 |
| `zoom-metacognition.md` | Earlier zoom-out synthesis: Argyris double-loop learning, OODA, agent metacognition analogues. | ~2026-04 |
| `system1-system2-agents.md` | Kahneman System 1/2: when should an autonomous agent deliberate vs. act? | ~2026-04 |
| `spaced-repetition-confidence.md` | How does spaced repetition interact with confidence/score signals? | ~2026-04 |
| `tacit-vs-explicit.md` | What distinguishes tacit vs. explicit knowledge in expertise research? | ~2026-04 |
| `agent0-synthesis.md` | Agent0 / Foundation Agent synthesis — steal-list from related agent architectures. | ~2026-04 |
