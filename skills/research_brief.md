---
name: research_brief
description: "Research a topic and commit to a decision brief: options, tradeoffs, and an actual recommendation — not an option table that hands the decision back to the reader"
roles_allowed: [worker]
triggers: [decision brief, give me a recommendation, which one should i, research and recommend, options and tradeoffs, help me decide]
---

## Overview

Use this skill when the goal is "research X and tell me what to do," not
just "research X." The failure mode this exists to prevent: a well-
researched pile of options presented as a neutral table with no verdict —
technically informative, but it just relocates the decision onto the reader
instead of doing the requested work. Use `web_research`/`deep_research` for
the gathering; this skill is the opinionated-synthesis layer on top that
turns findings into a committed recommendation.

## Steps

1. **Name the actual decision and its constraints** — what is being chosen
   between, and what does the person actually care about (budget, timeline,
   must-haves, deal-breakers)? If the goal doesn't state constraints, infer
   the most likely ones from context and say what you assumed — don't stall
   on a clarifying question when a reasonable default exists.
2. **Gather real candidate options** — via `web_research` or `deep_research`
   depending on depth needed; 2-5 genuine candidates, each with sourced,
   dated facts (price, specs, availability) rather than reputation-only
   claims.
3. **Build the tradeoff comparison as an input, not the deliverable** — a
   table of facts is a work-product on the way to a recommendation, not the
   answer itself.
4. **Weigh the tradeoffs against the stated decision criteria** and commit
   to one recommendation. State it as a plain sentence up front: "Pick X,
   because Y" — not buried after a wall of caveats.
5. **State the confidence and the flip condition** — what would have to be
   true for the recommendation to change (e.g. "if timeline mattered more
   than cost, pick B instead"). This is what makes the recommendation
   falsifiable and honest rather than a guess dressed as certainty.
6. **If it's a genuine tie**, say so explicitly and name the one factor
   that would break it — don't silently pick one to avoid looking
   indecisive, and don't dodge into "it depends" without naming on what.
7. **Note what's out of scope** — what more research would change the
   picture, so the reader knows the brief's edges.

## Quality gates

- The brief ends with an explicit recommendation sentence — a bare options
  table with no verdict fails this skill's job even if the research is good.
- Every factual claim behind the recommendation is sourced and dated (reuse
  `web_research`/`deep_research` quality gates for the gathering phase).
- Confidence is stated, and it is falsifiable — a named condition that
  would flip the call, not a hedge that survives any outcome.
- A genuine tie is reported as a tie with a named tie-breaker, never
  smoothed into a false-confident pick.

<!-- crystallizes CAPABILITIES.md Tier 2 ("research-brief"): "Research
     [topic] and give me a decision brief: options, tradeoffs, your
     recommendation." Built 2026-07-13, BACKLOG #22 blank-slate curation.
     Status: target — not yet run end-to-end through handle.py in this
     session (a real decision brief needs live multi-source web research,
     the same cost/time class as the Manti canonical case: ~$1.50-2.50 and
     15-25 minutes per docs/CAPABILITIES.md's own measurements — too
     expensive to spend against this box's shared run budget for a
     curation pass with no live decision behind it). The opinionated-
     synthesis discipline in this file is what's new; the gathering
     mechanics it reuses (web_research/deep_research) are already
     independently verified. -->
