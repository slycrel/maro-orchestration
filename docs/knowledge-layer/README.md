# Knowledge Layer — Canonical Documentation

This is the authoritative location for Poe's knowledge layer design and implementation docs.

## Architecture

The knowledge layer has three views (Ledger / Web / Lens) implemented across three modules:

| Module | View | What it answers |
|--------|------|----------------|
| `src/memory_ledger.py` | Ledger (temporal) | What happened? When? What did we learn? |
| `src/knowledge_web.py` | Web (associative) | How are things related? What patterns recur? |
| `src/knowledge_lens.py` | Lens (contextual) | What matters right now, for this task? |
| `src/memory.py` | Public API | Coordinates all three + re-exports |

## K Stages (Implementation Roadmap)

| Stage | Status | Description |
|-------|--------|-------------|
| K0 | DONE | Baseline audit (K0_BASELINE.md) |
| K1 | DONE | Module split (memory_ledger, knowledge_web, knowledge_lens) |
| K2 | TODO | Migrate links — transform knowledge into graph nodes |
| K3 | TODO | Read path — wire into decompose context, standing rules |
| K4 | TODO | Write path — outcomes update knowledge layer |
| K5 | TODO | Lens/persona — different personas query differently |
| K6 | TODO | Temporal intelligence — bi-temporal fields, decay |
| K7 | TODO | Correspondence interface — cross-view queries |
| K8 | TODO | Self-improvement — auto-prune, strengthen, detect gaps |

## Files

- `01_ARCHITECTURE.md` — Core design (Ledger/Web/Lens tri-coordinate)
- `02_K_STAGES.md` — K0-K8 implementation phases
- `03_RESEARCH_LANDSCAPE.md` — Related systems (Zep, Graphiti, etc.)
- `04_GAPS_AND_BLIND_SPOTS.md` — Design gaps
- `06_LEARNING_LOOP_AUDIT.md` — How the learning pipeline works
- `07_CAPTAINS_LOG_SPEC.md` — Captain's Log feature spec
- `08_SESSION_NOTES_APR9.md` — Session notes
- `K0_BASELINE.md` — Empirical snapshot of current data stores
- `memory_landscape_research.json` — Memory system inventory data

Raw conversation transcripts archived at `research/orchestration-knowledge-layer/archive/`.
