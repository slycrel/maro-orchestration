# Productive Persistence in Agents: When to Keep Trying vs. Quit

*Research synthesis — ML + psychology perspectives*
*Date: 2026-03-27*

---

## 1. ML Perspective

### Core Problem: Exploration-Exploitation Tradeoff
Persistence in RL agents is fundamentally the exploration-exploitation problem: when do you keep trying a strategy (exploit) vs. switch to something new (explore)?

**Key algorithms and what they imply about persistence:**

- **UCB (Upper Confidence Bound):** Persist with options that have high uncertainty-adjusted value. Quit when confidence intervals tighten around a low mean.
- **Thompson Sampling:** Probabilistic persistence — sample from belief distributions; naturally phase out low-value options as evidence accumulates.
- **Gittins Index:** Optimal stopping rule for bandit problems. Each option has a computable "index" — quit when another option's index exceeds the current one. Persistence cost is opportunity cost.
- **ε-greedy:** Simplest heuristic — persist on best-known option most of the time, with random exploration. Blunt but robust.

### Failure as Signal
- **Productive failure (Kapur):** Errors that expose constraint boundaries are high-value even without immediate success. Agents should distinguish *informative failure* (update beliefs, keep going) from *confirming failure* (confirms dead end, quit).
- **Model-based RL:** Agents with internal world models can simulate failure cheaply — persist in simulation, quit in execution when simulated P(success) falls below threshold.

### Key Thresholds from RL
| Condition | Persist | Quit |
|-----------|---------|------|
| High uncertainty + moderate reward | Yes | No |
| Low uncertainty + low reward | No | Yes |
| Informative failure (new info gained) | Yes | No |
| Repeating same failure (no new info) | No | Yes |
| Opportunity cost of alternatives is low | Yes | No |
| Opportunity cost is high | No | Yes |

### Meta-RL / Learning to Persist
Meta-RL framing (Wang et al., Duan et al.): the agent learns *when* to persist as part of its policy, not just *what* to do. Persistence becomes a learned behavior shaped by reward structure. Implication: agents trained on problems with high variability in difficulty learn richer persistence heuristics than those trained on uniform problems.

---

## 2. Psychology Perspective

### Duckworth's Grit — Passion + Perseverance
Angela Duckworth's grit research identifies two components:
- **Passion:** Consistency of interest over time (prevents premature quitting from boredom)
- **Perseverance:** Sustained effort despite failure

Key nuance often overlooked: **strategic quitting is not ungritty**. Duckworth explicitly distinguishes quitting low-level tactics (fine) from quitting high-level goals (the costly kind). "Gritty" people quit bad strategies readily — they just don't quit the goal.

### Seligman's Learned Helplessness
Uncontrollable failure produces learned helplessness — agents (human or otherwise) stop trying even in new, solvable situations. The critical variable is **perceived control**, not actual success rate.

Implication for AI agents: an agent that receives too many uncontrollable failures (no feedback loop, opaque environment) will develop policy collapse analogous to learned helplessness.

### Growth Mindset (Dweck)
Fixed mindset → failure = identity threat → quit to protect ego.
Growth mindset → failure = information → persist to learn.

The mechanism is **attribution**: growth-mindset persistence is sustained because failure is attributed to *effort and strategy* (controllable) rather than *ability* (fixed).

### Optimal Challenge Point (Bjork, Kapur)
Learning and persistence are maximized at a specific difficulty level — not too easy (no signal), not too hard (helplessness). The "desirable difficulty" zone is roughly:
- Success rate ~60–85% (too high = no growth; too low = demoralizing)
- Failures are interpretable and correctable

### Human Stopping Rules
Psychology research identifies common heuristics people use to quit:
1. **Aspiration threshold:** Quit when results fall below a minimum acceptable level.
2. **Resource depletion:** Quit when effort cost exceeds perceived expected value.
3. **Comparative opportunity cost:** Quit when alternatives become visibly better.
4. **Social proof:** Quit when others in same situation have quit (norm signal).

---

## 3. Synthesis: Heuristics for Agents

The cross-domain synthesis produces a coherent framework. Persistence is productive when:

### Productive Persistence Conditions
1. **Failure is informative** — each attempt narrows the hypothesis space or updates a belief. (RL: exploration value; Psychology: growth mindset attribution)
2. **The goal is stable but the strategy is flexible** — don't quit the goal, quit the tactic. (Duckworth's grit distinction; RL: policy update vs. goal reset)
3. **Uncertainty is high relative to evidence** — confidence intervals are wide; more data is worth collecting. (UCB/Thompson Sampling)
4. **Opportunity cost is low** — no clearly better alternative is available. (Gittins Index)
5. **The agent has interpretable feedback** — failure is attributed to controllable factors. (Seligman: learned helplessness prevention; Dweck: growth attribution)
6. **Difficulty is in the optimal challenge zone** — ~60–85% success rate across attempts. (Bjork/Kapur)

### Quit Conditions
1. **Failure is repetitive with no new information** — same error, same cause, no update.
2. **Uncertainty has collapsed to a confident low estimate** — model is sure this doesn't work.
3. **A clearly better alternative exists** — opportunity cost is high.
4. **The feedback loop is broken** — agent cannot tell why it's failing (environment opacity → helplessness risk).
5. **Resource/time budget is exhausted** — expected value of continuing is negative given remaining budget.

### Practical Agent Heuristics

```
persist_if:
  - failure_is_new_information
  - uncertainty > threshold
  - no_better_alternative_visible
  - strategy_can_be_varied (goal unchanged)
  - success_rate in [0.15, 0.85]  # informative zone

quit_if:
  - same_failure_repeated N times with no variation
  - P(success | remaining_budget) < epsilon
  - alternative_expected_value > current_expected_value
  - feedback_loop_broken (can't interpret failure)
  - goal_itself_is_invalid (not just tactic)
```

**N for "repeated failure" heuristic:** RL literature suggests N ≈ 3–5 for most task structures before switching strategy. Psychology: after ~3 identical failures, humans typically switch or quit — agents should match this rhythm.

### The Meta-Skill: Strategy Rotation vs. Goal Abandonment
The most important distinction: **quitting a strategy** is healthy and necessary; **quitting a goal** requires much higher evidence. Agents should maintain a stack:
- Goal level: high persistence threshold (quit only if goal is proven invalid)
- Strategy level: moderate persistence (quit after N informative failures)
- Tactic level: low persistence (quit quickly if better option visible)

---

## 4. Open Questions

### Empirical Gaps
1. **What is the right N for "informative failure" before switching?** RL theory gives asymptotic answers; practical agent designers need finite-horizon values. Empirical benchmarks across task types are sparse.

2. **How do agents detect feedback loop breakage?** Learned helplessness requires the agent to recognize "I cannot influence outcomes." This is a meta-cognition problem — current RL agents lack reliable mechanisms for this.

3. **Does the 60–85% success rate zone generalize beyond human learning to ML agents?** Bjork/Kapur findings are from cognitive science. Transfer to agent architectures (especially LLM-based agents) is assumed but not empirically validated.

4. **How does persistence interact with multi-agent coordination?** When multiple agents pursue the same goal, optimal individual persistence thresholds may not equal optimal collective thresholds. The "social proof" quitting heuristic suggests coordination effects, but this is underexplored.

5. **Can agents learn persistence calibration from experience?** Meta-RL suggests yes — but most production agent systems don't implement meta-learning. What's the minimum viable implementation for adaptive persistence?

6. **Passion analogue for agents:** Duckworth's "consistency of interest" (passion) is a key persistence predictor in humans. Do agents benefit from an analogous mechanism — e.g., goal salience weighting that resists distraction? How would this be implemented?

### Design Questions for Autonomous Agents (Poe-relevant)
- How should a long-running autonomous agent like Poe track "informative failure" vs. "confirming failure" across multi-day task spans?
- What signals should trigger a goal-level quit vs. a strategy-level pivot?
- How do we prevent learned helplessness in agents operating in low-feedback environments (e.g., tasks where success is only measurable days later)?

---

*Sources: Duckworth (2016) Grit; Seligman (1972) Learned Helplessness; Dweck (2006) Mindset; Bjork (1994) Desirable Difficulties; Kapur (2016) Productive Failure; Sutton & Barto (2018) RL: An Introduction; Auer et al. (2002) UCB; Thompson (1933); Gittins (1979); Wang et al. (2016) Meta-RL; Duan et al. (2016) RL²*
