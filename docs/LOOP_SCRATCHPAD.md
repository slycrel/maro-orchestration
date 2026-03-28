# Loop Scratchpad — Step-Level Data Sharing Architecture

## Problem

Steps in the agent loop can't effectively share findings. Today:
- `completed_context` passes 800-char excerpts inline (band-aid)
- Step artifacts go to disk but the subprocess executor can't reference them
- No structured way to say "step 2 found these files, step 5 should cite them"

Result: the executor hallucmates plausible file names instead of citing real data
from prior steps. Accuracy audit showed ~60% fabricated evidence.

## Design

A **loop scratchpad** — a persistent structured store scoped to one loop execution:

```
Loop scratchpad (per loop_id)
├── step_1_result: { summary, findings: [...], files_found: [...], ... }
├── step_2_result: { summary, findings: [...], code_refs: [...], ... }
├── ...
└── shared: { repo_root, module_list: [...], test_count, ... }
```

### Write path
After each step completes, the step result is parsed for structured data
(file names, key findings, open questions) and written to the scratchpad.

### Read path
Before each step executes, the scratchpad is serialized into the context:
- **Inline:** summary of each prior step (one line each)
- **Reference:** "Full findings from step N available in scratchpad. Cite step N
  data by referencing specific items from its findings list."

### Token budget
The inline summaries are capped (~100 chars each). The scratchpad detail
is available but not sent unless the step text references a prior step.
This prevents context explosion while keeping data accessible.

## Implementation Options

### Option A: In-memory dict (simplest)
```python
scratchpad: Dict[str, Any] = {}
scratchpad[f"step_{idx}"] = {
    "summary": step_summary,
    "result_excerpt": step_result[:2000],
    "files_cited": [...],  # extracted via regex
    "findings": [...],     # extracted from result
}
```
Pro: Zero I/O, fast. Con: Lost if process crashes mid-loop.

### Option B: JSON file per loop
```python
scratchpad_path = artifacts_dir / f"loop-{loop_id}-scratchpad.json"
```
Pro: Survives crashes, readable for debugging. Con: File I/O per step.

### Option C: SQLite per loop (overkill for now)
Pro: Queryable. Con: Complexity for no current benefit.

**Recommended: Option A (in-memory dict) with Option B as persistence for post-mortem.**
Write to dict during loop, dump to JSON at end or on crash.

## Relationship to Ancestry

`ancestry.py` tracks **project-level** lineage (which project spawned which).
The loop scratchpad tracks **step-level** data flow within a single loop.

If we later add multi-loop missions where loop N's output feeds loop N+1,
the scratchpad would be the natural carrier — it becomes the "working memory"
that persists across loop boundaries. That's when it might graduate to SQLite
or merge with the ancestry system.

## Phase Mapping

This work lives at the **reliability→replayability** boundary:
- **Reliability:** steps cite real data instead of hallucinating → more trustworthy output
- **Replayability:** the scratchpad is a structured record of what each step actually found,
  enabling replay ("what would step 5 have done with different step 2 findings?")
