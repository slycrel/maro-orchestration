# Quality Gates

Multi-layer verification: constraint enforcement, inspector friction detection, adversarial pass, council review, cross-reference fact checking.

## Layer Stack (earliest → latest)

Five verification layers applied in sequence from pre-execution constraint checks through post-loop council review.

1. **Constraint enforcement** (`src/constraint.py`) — pre-execution tier/risk check; blocks or confirms before tool calls. See [[constraint-system]]
2. **Inspector** (`src/inspector.py`) — friction detection after each step; detects stuck loops, cost overruns, drift
3. **Quality gate** (`src/quality_gate.py`) — post-loop review: 5 passes (initial assessment, cross-ref, adversarial, council, summary)
4. **Cross-reference** (`src/cross_ref.py`) — two-stage fact verification; extract claims → verify each in fresh LLM context (no source answer visible; prevents confirmation bias)
5. **Passes pipeline** (`src/passes.py`) — unified chaining: quality_gate → adversarial → council → debate → thinkback

## Passes Pipeline

Presets: `quick` / `standard` / `thorough` / `full` / `all`

```bash
poe-passes --goal "..." --passes council,debate
poe-passes --goal "..." --preset thorough
```

## Self-Tightening Gates (Phase 56 — DONE)

Quality criteria are now dynamic via the promotion cycle in [[memory-system]]. Gates that trigger frequently → promoted to standing rules injected at decompose. Gates that never fire → candidates for pruning via `contradict_pattern()`. Inspector trigger history feeds `observe_pattern()` in `memory.py`.

## Related Concepts

Systems that feed into or are fed by the quality gate pipeline.

- [[self-improvement]] — thinkback replays step decisions; evolver applies recovery suggestions
- [[core-loop]] — quality gate fires after loop completion
- [[memory-system]] — lessons extracted from gate findings
- [[constraint-system]] — the earliest gate; fires pre-execution
