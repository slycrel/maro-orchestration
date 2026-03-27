# Productive Persistence in Agents

**Question**: Productive persistence in agents — exploration-exploitation tradeoff, grit vs. stubbornness, when to quit. What does ML + psych research say about the optimal persistence model? (incl. OpenAI autocurricula emergent strategies, Duckworth grit, Kapur productive failure)

**Informs**: Roadblock resilience in `agent_loop.py`

---

## Key Findings

- **Grit is goal-stable, not strategy-stable.** Duckworth (2007, 2016) defines grit as sustained passion and perseverance toward *long-term goals*, not toward any particular method. High-grit subjects abandon strategies readily while holding goals constant. The failure mode — stubbornness — is strategy-stable goal-unstable: clinging to one approach until the goal itself collapses from exhaustion. An agent that retries the same tool call 20 times is exhibiting stubbornness, not grit.

- **Productive failure requires struggle before instruction.** Kapur (2010, 2016) showed that students who attempt a novel problem without guidance — generating multiple wrong solutions — subsequently retain and transfer correct solutions far better than students who received instruction first. Key mechanism: exploratory failure activates broader feature space, making the final solution anchor to richer semantic context. For agents, this means early-phase errors should *not* short-circuit learning; a naive first-pass attempt followed by a corrective second pass outperforms a hyper-guarded first pass that tries to avoid all errors.

- **Autocurricula produce emergent persistence strategies through environmental pressure.** The OpenAI multi-agent hide-and-seek study (Baker et al., 2019) showed that agents trained in sufficiently rich environments developed tool use, cooperative blocking, and counter-strategies purely from self-play — without explicit reward shaping for those behaviors. Persistence behaviors (e.g., systematic box-stacking) emerged not from intrinsic motivation but from *curriculum pressure*: the opposing team's competence forced creative problem-solving. The implication is that persistence quality is environment-coupled, not a fixed agent trait.

- **Exploration-exploitation is a budget allocation problem, not a binary switch.** The ε-greedy and UCB literature (Auer et al., 2002; Sutton & Barto, 2018) frames the tradeoff as: how much of the remaining budget should be spent confirming known-good options vs. probing unknowns? Optimal exploration decreases as budget decreases (deadline pressure should reduce exploration). In practice, agents should increase exploration early (high uncertainty, high budget) and tighten exploitation as remaining budget narrows.

- **Stuck detection requires error-signature hashing, not raw retry counts.** A retry on the same error state is stubbornness. A retry after a meaningful state change (new context, different tool, reformulated subgoal) is grit. The operationally useful distinction: track a hash of (error_type × last_action × context_signature). If the hash repeats, the agent is looping. If it changes, it's exploring.

---

## Implications

### Persistence is a three-layer system

Map Duckworth's goal-stability insight onto a three-level hierarchy:

| Layer | Stability | Quit trigger |
|-------|-----------|--------------|
| **Goal** (e.g., "write tests for auth module") | High — change only on explicit user revision or fundamental infeasibility | Repeated failure across multiple strategies with no progress signal |
| **Strategy** (e.g., "use pytest fixtures") | Medium — swap when error signature repeats or a better path is found | Error-signature hash collision + >N attempts without state change |
| **Tactic** (e.g., "run pytest -v") | Low — change on first failure if a variant exists | Single failure is sufficient to try an alternative |

### `agent_loop.py` design implications

1. **Tiered retry budgets.** Define separate counters for tactic retries (small budget, ~2–3), strategy retries (medium budget, ~3–5), and goal-level escalations (small budget, ~1–2 before `flag_stuck`). Exhausting a tactic budget should trigger strategy rotation, not loop termination.

2. **Error-signature deduplication.** Before each retry, compute a hash of `(error_type, last_N_actions, relevant_context_digest)`. If the hash matches a prior attempt, force a strategy-level change. Raw retry counts without this check will silently allow stubbornness.

3. **Productive failure window.** Do not prematurely abort on first-pass errors in exploratory phases. A two-pass design — naive attempt → correction — will outperform a heavily-guarded single pass on novel tasks (Kapur). Apply this most aggressively when the task is underspecified or the agent is operating in new territory.

4. **Exploration budget decay.** Tie exploration width to remaining step budget: early in a plan, allow broad tool/strategy diversity; as remaining steps narrow, tighten to highest-confidence paths. A simple formulation: `exploration_factor = remaining_budget / initial_budget`.

5. **Autocurricula pressure via adversarial subgoals.** For tasks with evaluable outputs, consider generating a "critic subgoal" that challenges the current plan before execution. Environmental resistance (even simulated) forces the agent to develop more robust strategies — the hide-and-seek analog. This is optional complexity; only worth the overhead when task quality matters more than speed.

6. **Stuck escalation path.** The canonical escalation: tactic failure → alternate tactic → strategy rotation → goal reformulation → `flag_stuck`. Each level should produce a logged rationale. The `flag_stuck` call is not a failure state — it is correct behavior when all levels are exhausted. Suppressing it in favor of infinite retries is the stubbornness failure mode.

---

## Sources

- Duckworth, A. L., Peterson, C., Matthews, M. D., & Kelly, D. R. (2007). Grit: Perseverance and passion for long-term goals. *Journal of Personality and Social Psychology*, 92(6), 1087–1101.
- Duckworth, A. L. (2016). *Grit: The Power of Passion and Perseverance*. Scribner.
- Kapur, M. (2010). Productive failure in mathematical problem solving. *Instructional Science*, 38(6), 523–550.
- Kapur, M. (2016). Examining productive failure, productive success, unproductive failure, and unproductive success in learning. *Educational Psychologist*, 51(2), 289–299.
- Baker, B., Kanitscheider, I., Markov, T., Wu, Y., Powell, G., McGrew, B., & Mordatch, I. (2019). Emergent tool use from multi-agent autocurricula. *arXiv:1909.07528*.
- Auer, P., Cesa-Bianchi, N., & Fischer, P. (2002). Finite-time analysis of the multiarmed bandit problem. *Machine Learning*, 47(2–3), 235–256.
- Sutton, R. S., & Barto, A. G. (2018). *Reinforcement Learning: An Introduction* (2nd ed.). MIT Press.

---

**Date**: 2026-03-26
