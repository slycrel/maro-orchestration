---
name: deep_research
description: "Heavy-tier research: fan-out query planning, parallel source gathering, adversarial claim-checking, and a citation-verified report. For quick lookups use web_research instead."
roles_allowed: [worker]
triggers: [deep research, deep dive, comprehensive report, literature review, due diligence, multi-source report, cited report, fact-check]
---

## Overview

Use this skill when a goal needs a defensible, cited research deliverable — not a quick answer. Pipeline: plan sub-questions → gather per-question in parallel → adversarially check claims → write a report where every claim traces to a fetched source. If the goal is a single-fact lookup or a light survey, use `web_research` (the lite tier) instead.

## Steps

1. **Scope the brief** — restate the question, name the deliverable (report sections, expected length), and decompose into 3-6 sub-questions that together give an objective view of the topic (include at least one skeptical/criticism angle).
2. **Plan the query fan-out** — for each sub-question, write 2-3 distinct search queries (definition, evidence, counter-evidence, recency). Record the plan before executing it.
3. **Gather per sub-question, in parallel where the harness allows** — each lane searches, fetches 3-5 pages, and produces a per-source summary with URL, title, date, and the specific claims it supports. Prefer primary sources over aggregators.
4. **Curate** — dedupe sources across lanes, drop low-quality/undated/aggregator pages, and keep a source table (URL, credibility note, what it's used for).
5. **Extract the claim ledger** — bullet every load-bearing claim with its supporting source(s). A claim without a fetched source does not enter the ledger.
6. **Adversarial pass** — for each load-bearing claim, run at least one search specifically for disconfirming evidence. Mark each claim verified (2+ independent sources), contested (credible disagreement), or unverified.
7. **Write the report** — sections per the brief; inline citations on every factual claim; a dedicated "Contested / open questions" section; lead with the answer.
8. **Verify citations** — re-check that every cited URL was actually fetched this run and actually supports the sentence it's attached to. Remove or downgrade anything that fails.
9. **State gaps and confidence** — what remains unknown, overall confidence, and what further work would change the picture.

## Quality gates

- Every factual claim in the report cites a source fetched during this run — no from-memory citations.
- Load-bearing claims need 2+ independent sources; single-source claims are labeled as such.
- Contested claims are reported as contested — never silently averaged into consensus.
- The adversarial pass (step 6) is mandatory, not optional-on-time-pressure.

<!-- pipeline shape swiped from github.com/assafelovic/gpt-researcher (Apache-2.0, verified 2026-07-09): planner agent → parallel execution agents → per-source summarization with attribution → filter/aggregate → cited report. Shape only; no code or deps taken. -->
