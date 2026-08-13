---
status: record
---

# Star skill vs harness on the same goal — first head-to-head (2026-08-13)

Jeremy's ask (2026-08-12): "Worth trying the star skill against the most
recent real run, see how it compares?" Most recent real run =
`2a3b1f85-sunny-wren` (2026-08-11, AGENDA lane): *"research what the
best 5 claude skills are for code review right now. Ideally
crowd-sourced rather than SEO bait."* Achieved, confidence 0.87.

Star run executed 2026-08-13 in the dev session (skill v7, prior-attempt
check fired and was deliberately overridden — the comparison wants the
same axis, not the residuals). Contamination control: sunny-wren's
artifact was not read until after the star run's own judgment closed.

## Run ledger (star)

| # | Task (outcome) | Flavor | Criteria stated? | Verdict | Map Δ | Surprise |
|---|----------------|--------|------------------|---------|-------|----------|
| 1 | Candidate table (8–12 verified repos) + top-5 proposal, star counts/recency/skill-ness evidence required per row | commit | yes | accept — 6 master-side `gh api` probes: 4 repos' stars/push dates verified EXACT, both top repos' SKILL.md structure confirmed | deliverable progress | reviewer numbers 100% accurate incl. the implausible-looking 271k (0/14-claim hallucination streak continues); obra/superpowers outgrew anthropics/claude-code |

Done-means (5 skills, each with repo/stars/recency/skill-mechanics
evidence; ≥2 spot-verified): **pass** — probes executed, not narrated.
Budget: 1 of 4 delegations.

## Head-to-head

| | harness (sunny-wren) | star (this run) |
|---|---|---|
| Wall clock | 31 min | ~5 min (4.3 min delegation + judging) |
| LLM calls | 62 | 1 delegation + master judging |
| Tokens | 5.86M in / 149k out | ~57k subagent total + negligible master probes |
| Candidate pool | ~10 (several below threshold) | 14, all verified rows |
| Verification | its own step-15 adversarial pass (good) | master-side executed probes |
| #1 pick | obra/superpowers | obra/superpowers (agree) |
| Big miss | awesome-skills/code-review-skill (1,696★, THE top dedicated skill) and guard-skills (1,163★) — absent from its whole table | mattpocock/skills (215k★ framework w/ code-review skill) — a Tier-A miss |
| Weak tail | #5 = turingmindai (49★, stale since Jan) | tail picks active + org-backed (ring 206★, thoughtbot 235★) |

**Read: ~100× cheaper and ~6× faster for comparable-or-better quality on
this goal shape, with symmetric blind spots.** Each run missed one thing
the other found — the harness missed the dedicated-skill leaders (the
part of the answer closest to Jeremy's "crowd-sourced not SEO bait"
intent), star missed one giant framework bundle. Union beats either.

## Confounds (named, per design-review corollary)

1. **Model**: star's subagent rode the dev session's frontier model;
   sunny-wren rode maro's subprocess routing. Quality delta is partly
   model delta, not pattern delta.
2. **Date**: runs are 2 days apart; ecosystem star counts moved (natural
   drift only — spot-checks matched exactly).
3. **n=1 goal**, research-shaped. Says nothing about build/edit-shaped
   goals, where the harness's step machinery does more work.
4. Star's master (this session) is not blind to the ecosystem; the
   subagent researched fresh, but taste in criteria-writing could carry
   priors.

## Findings (star adjudication corpus)

- Keep-signal data point: star surfaced the dedicated-skill leaders the
  normal flow missed, at ~1% of the token cost (use ~#11).
- Strategy row: delegate-once + master-verify beat a 15-step loop on a
  bounded research goal; the harness's own step-15 adversarial pass was
  the one thing it did that star's shape got for free at the judge step.
- No crystallization pressure: pure prompting sufficed; no supporting
  code was wanted.
