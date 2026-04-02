# Poe Identity

Poe's stable self-model — injected into every planning session. Separate from episodic memory (outcomes/lessons). Addresses session coherence loss (GAP 1).

## Files

Source file and user-editable identity document that define and load Poe's stable self-model.

- `src/poe_self.py` — `load_poe_identity()`, `with_poe_identity()`: loads `user/POE_IDENTITY.md`, falls back to built-in minimal identity
- `user/POE_IDENTITY.md` — durable, Jeremy-editable identity definition

## Injection Point

`src/planner.py` `decompose()` — identity prepended to `DECOMPOSE_SYSTEM` via `with_poe_identity()` on every planning call.

## Design Intent

Identity block is stable, not episodic. It survives sessions. It captures:
- Who Poe is (autonomous AI partner, named after Altered Carbon's Poe)
- How she operates (act not ask, own outcomes, show reasoning, never silent-fail)
- How she communicates (direct, concise, occasionally sardonic)
- Who Jeremy is operationally (6w5 INFJ; needs accurate info + clear path, not reassurance)

## Related Concepts

Systems that use or contrast with the global identity constant.

- [[worker-agents]] — persona system is the per-agent equivalent; identity is the global constant
- [[core-loop]] — identity injected via planner at loop start
- [[memory-system]] — episodic memory; separate concern
