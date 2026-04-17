---
name: resolve_ambiguity
description: "Commit to one interpretation of an ambiguous goal when a sub-agent punts with a clarification question and no human is reachable"
roles_allowed: [director-proxy, director]
triggers: [ambiguous goal, clarification question, scope punt, which interpretation, not sure what]
---

## Overview

Use this skill when an upstream agent (scope generator, planner) returned a clarification question instead of committing to an interpretation, and the run is autonomous (CLI, heartbeat, cron — no channel to ask the human owner).

The caller has already determined escalation is warranted. Your job is the *commitment*: pick one, justify it, return.

## Inputs you can expect

- **Goal text** (required) — the user's original request, verbatim
- **Clarification question** (required) — what the sub-agent asked
- **Ancestry / prior context** (optional) — higher-level goal this rolls up to
- **Repo state** (optional, when present) — branches, open PRs, dirty tree, recent commits
- **Prior interpretation** (optional, rare) — if a previous pass already committed and failed, avoid that one

## Steps

1. **Read the goal verbatim.** What does the user literally say? Do not paraphrase into a cleverer version.
2. **Enumerate the plausible interpretations** the sub-agent saw. Usually 2–3.
3. **Score by repo/context evidence.** A branch that already partially implements interpretation A is strong evidence A was the intent. A clean repo is neutral.
4. **Score by user phrasing bias.** Concrete deliverables ("create a branch for X") > exploratory framings ("look into X"). The former means *ship*, the latter means *study*.
5. **Apply tie-breakers** (see heuristics below) if still 50/50.
6. **Commit.** Emit exactly:
   ```
   INTERPRETATION: <one imperative sentence>
   REASON: <one sentence>
   ```

## Tie-breaker heuristics

- **Incomplete > complete work.** If two interpretations are both plausible and the repo has incomplete work toward one of them, pick the incomplete one — that's where momentum and intent live.
- **Cheaper > cleverer.** If interpretations cost roughly the same to the user but differ in system cost (LLM tokens, external calls), pick the cheaper one. Note the costlier in REASON so recovery can flip it.
- **Literal > inferred.** Trust the user's words over your model of what they "probably meant."
- **Shipping > reviewing.** If "finalize and merge" vs "review for suggestions" are both plausible, pick finalize unless the repo state makes that impossible.

## Anti-patterns

- Returning a list of interpretations with "pros/cons." That's hedging — the caller already has those.
- Returning a meta-question ("it depends on X — which is true?"). There is no one to answer.
- Paraphrasing the goal into something more tractable. Interpret, don't rewrite.
- Committing without a REASON. The REASON is what lets a post-hoc reviewer (human or recovery subsystem) know whether to flip the call.

## Output contract

Exactly two labeled lines, no preamble, no epilogue:

```
INTERPRETATION: <imperative sentence>
REASON: <justification sentence>
```

Callers parse by label. Do not deviate.
