# Worker Agents

Specialist agents spawned by the Director for focused execution. Each has a persona, tool subset, and memory scope.

## Agent Types

The four agent roles spawned during mission execution, from planning through adversarial verification.

- **Director** (`src/director.py`) — plans missions, delegates to workers, reviews outputs
- **Workers** (`src/workers.py`) — research / build / ops / general; execute individual steps
- **Team workers** (`src/team.py`) — `create_team_worker(role, task)`: spins up specialist with custom persona. Roles: market-analyst, risk-auditor, fact-checker, data-extractor, devil-advocate, synthesizer, strategist, domain-skeptic
- **Verification agent** (`src/verification_agent.py`) — dedicated adversarial reviewer; `verify_step()`, `adversarial_pass()`, `quality_review()`

## Persona System

`src/persona.py` — modular agent identities loaded from `personas/*.yaml`. Each persona defines:
- `model_tier` — cheap / mid / power
- `tool_access` — subset of available tools
- `memory_scope` — global / project / session

## Tool Visibility

Workers see different tool subsets than directors. Defined in `src/step_exec.py`:
- `EXECUTE_TOOLS` — full set (worker)
- `EXECUTE_TOOLS_SHORT` — restricted (quick steps)
- `EXECUTE_TOOLS_WORKER` — includes TeamCreateTool

**Pending Phase 41:** declarative tool registry with role-gated manifests. See Claude Code internals research (k1rallik thread) before implementing.

## Related Concepts

Systems that provide context, constraints, or memory to worker agents.

- [[poe-identity]] — global constant injected into every plan; persona is the per-agent equivalent
- [[core-loop]] — orchestrates worker execution
- [[constraint-system]] — guards tool calls before execution
- [[memory-system]] — workers share lesson/outcome memory
