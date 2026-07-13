---
name: errand_research
description: "One real-world question, minutes not a research project: multi-source lookup collapsed into one direct, sourced answer — the passenger-in-a-car contract"
roles_allowed: [worker]
triggers: [where can i, how much does, is compatible with, compare the cheapest, top five, near me, what's wrong with my, find a source for, tell me about, errand, quick answer]
---

## Overview

Use this skill for a real-world errand question a person asks in passing —
gas station locations, library hours, shipping-price comparisons, "is X
compatible with Y," a symptom lookup for one specific appliance model. The
information exists publicly but takes a person several search-and-cross-
reference steps to collect; the job here is to do those steps and hand back
**one answer**, not a research project. If the goal wants a defensible,
cited multi-page deliverable, use `deep_research` instead; if it's a light
survey with no urgency around directness, `web_research` covers it. This
skill is specifically the tight, stop-when-good-enough, no-narration
contract for a question someone wants answered in a minute or two.

## Steps

1. **Restate the actual question in one sentence** — what specific decision
   or fact does the person need? Don't broaden it into a survey.
2. **Search 2-3 angles in parallel where possible** — direct fact lookup,
   an official/authoritative source, and a cross-check source. Prefer the
   source closest to the ground truth (official site/store page over an
   aggregator, the exact appliance model's manual over the product
   category's common answer).
3. **Fetch and triangulate** — pull the 2-4 pages that actually answer the
   question. If sources disagree (stale hours, conflicting phone numbers),
   verify against the more authoritative one and note the correction rather
   than averaging.
4. **Validate anything you're about to hand back as a concrete claim** —
   an address, a link, a phone number, a specific business name. A dead
   link or a fabricated-sounding specific is worse than saying less.
5. **Check freshness for anything time-sensitive** — hours, availability,
   prices, "is it open today" — prefer the most recently updated source and
   flag a stale-looking one rather than repeating it as current.
6. **Stop when the question is answered** — do not keep searching once you
   have enough to give a direct, confident answer with caveats. More
   sources past that point cost the person time, not accuracy.
7. **Answer directly** — lead with the answer (name/place/number/verdict),
   1-3 ranked options if the question calls for options, then the caveats
   and sources. No "here's how you could find out," no plan narration, no
   options dump when the question wanted one thing.
8. **If genuinely unable to verify, say so plainly** — "couldn't confirm X"
   beats a confident guess or an invented source.

## Quality gates

- The answer is one direct response, not a how-to-search list and not an
  unprompted options table — match the shape the question actually asked
  for (a verdict, a name, a short ranked list).
- Every concrete claim (name, address, link, price, phone number) was
  actually fetched this run — no from-memory specifics.
- Long-tail/obscure asks get "insufficient information found" rather than
  an invented plausible-sounding answer.
- An instance-specific question (this exact model, this exact town) is
  answered for that instance, not the category-majority case.
- Only ask a clarifying follow-up if the question is genuinely ambiguous
  (e.g. two towns share the name) — not as a way to avoid committing.
- Time/cost envelope stays errand-scale: a handful of fetches and one
  synthesis pass, not a multi-round research pipeline.

<!-- crystallizes CAPABILITIES.md Tier 1 ("errand research") — the canonical
     case is "Where can I get non-ethanol gas in or around Manti, Utah?"
     (docs/CAPABILITIES.md, canonical simple case). Built 2026-07-13 as part
     of BACKLOG #22 blank-slate curation; the content-quality half of that
     case (Run 2, 2026-07-10) is already live-verified — this skill encodes
     the contract that run satisfied plus the failure-corpus lessons (link
     validation, instance-specific lookup, source triangulation, honest
     "unable to verify") folded into the Tier 1 catalog rows. Distinct from
     web_research (generic lite survey, no stop-when-good-enough or
     one-answer UX contract) and deep_research (heavy cited-report tier).
     Status: target — this skill file itself was not separately re-run
     through handle.py this session (a fresh live-data errand run is the
     same ~$1.50-2.50/15-25min cost class as the Manti canonical case per
     docs/CAPABILITIES.md's own measurements); the underlying content
     discipline it crystallizes is already live-verified via that case. -->
