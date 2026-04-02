# Intent Classification

Routes incoming messages to the right execution lane before any LLM planning happens.

## Lanes

Two execution lanes determine how a goal is handled: immediate single-shot or multi-step mission.

- **NOW** — immediate, single-shot execution. Direct answer or quick action.
- **AGENDA** — multi-step mission. Decompose → execute → memory → report.

## Key Source Files

Modules responsible for classifying intent and routing to the correct lane.

- `src/intent.py` — `classify_intent()`: keyword heuristics + LLM fallback; `check_goal_clarity()`: clarification gate for ambiguous AGENDA goals
- `src/handle.py` — entry point; applies lane routing, prefix handling, model tier selection

## Prefixes Handled in handle.py

Magic keyword prefixes that modify routing, model tier, or execution behavior before planning begins.

| Prefix | Effect |
|--------|--------|
| `effort:low/mid/high` | Override model tier |
| `ultraplan:` | model=power, max_steps=12 |
| `direct:` | Skip director, go straight to agent_loop |
| `yolo:` | Skip clarification gate |
| `btw:` | Non-blocking observation mode |
| `mode:thin` | Lightweight loop variant |

## Related Concepts

Systems downstream of intent classification that receive routed goals.

- [[core-loop]] — AGENDA lane feeds into run_agent_loop
- [[worker-agents]] — NOW lane can invoke director or agent_loop directly
- [[constraint-system]] — applied per step after routing
