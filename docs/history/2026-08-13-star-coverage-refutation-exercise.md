---
status: record
---

# Star skill v8 coverage-refutation — exercised on the code-review-skills re-run (2026-08-13)

Jeremy's ask: "update the star skill and run it against the same target."
The update: a **coverage refutation at JUDGE** for enumeration/ranking goals
(top-N, "best X") — symmetric to the existing evidence-modality refutation.
Grounds: the 2026-08-13 star-vs-harness head-to-head
(`2026-08-13-star-vs-harness-comparison.md`) found star's one weakness was a
coverage blind spot — its single sharp-criteria delegation internally
verified every row yet missed a category leader (`mattpocock/skills`, a 200k+★
framework bundle) the harness's wider net caught. "Union beats either."

The "same target" = the head-to-head's goal, from `2a3b1f85-sunny-wren`:
*"research what the best 5 claude skills are for code review right now.
Ideally crowd-sourced rather than SEO bait."*

## Prior-attempt handling

Identical-goal re-run → per the re-run-identity rule, aimed at the RESIDUAL
(the coverage blind spot), not a re-tread. The new axis: the delegation
criteria carried an explicit coverage decision-rule (enumerate BOTH dedicated
skills AND framework bundles that ship a review skill, across multiple
discovery surfaces), plus a warning that "a prior run missed a 200k+★ bundle."
Contamination control: the prior run's specific answer (repo names/numbers)
was NOT passed — only the method.

## Run ledger (star, v8)

| # | Task (outcome) | Flavor | Criteria? | Verdict | Map Δ | Surprise |
|---|---|---|---|---|---|---|
| 1 | 14-row candidate table + top-5, coverage criteria (dedicated + bundle × multi-surface), gh-verified stars | commit | yes | **reject-with-evidence** — coverage refutation fired: master `gh api` probes found TWO verified leaders absent from the table | deliverable progress, but coverage-incomplete | the coverage-aware delegation STILL missed the same 200k+ bundle; it stopped at the first big bundle it found and treated the warning as satisfied |

## What the JUDGE-step coverage probe caught (all `gh api`-verified)

- **mattpocock/skills — 216,678★**, bundle, ships `skills/engineering/code-review/`
  (confirmed via `gh search code`). The exact repo the prior star run missed.
  Absent from this run's table.
- **awesome-skills/code-review-skill — 1,703★**, dedicated, `SKILL.md` present,
  desc "comprehensive code review skill for Claude Code." THE dedicated-skill
  leader (the harness caught it 2026-08-11; both star runs missed it). Absent.
- (context) `amElnagdy/guard-skills` 1,166★ — the harness's other flagged
  leader; security/guard bundle.
- Verified-correct exclusions the delegation got right: `anthropics/skills`
  (169,178★ but ships NO code-review skill); obra/superpowers 271,853★ (real,
  the delegation's #1, accurate number).

## Corrected top-5 (master, post-probe)

1. obra/superpowers (271,853★) — bundle, `requesting-code-review` skill
2. **mattpocock/skills (216,678★)** — bundle, `skills/engineering/code-review/` *(recovered miss)*
3. davila7/claude-code-templates (30,236★) — bundle, `code-review-checklist` SKILL.md + adversarial/builder-reviewer loops
4. trailofbits/skills (6,572★) — security-org, `agentic-actions-auditor` review skill
5. **awesome-skills/code-review-skill (1,703★)** — the dedicated code-review leader *(recovered miss)*

Spans both categories and the full crowd-signal range; the delegation's own
#4/#5 (turingmind 49★, anthroos 38★) were displaced by verified leaders two
orders of magnitude larger.

## Findings

- **The update works as a backstop, and that is the right place for it.**
  Without the coverage refutation (v7), the master would have accepted the
  14-row table and shipped the identical blind spot as the prior run. With it
  (v8), the JUDGE-step probe turned two silent misses into caught misses.
- **Criteria-level coverage teaching is insufficient** — even explicit
  bundle-enumeration criteria + a named warning did not make the single
  delegation self-sufficient; it anchored on the first 200k bundle and stopped.
  This is the A/B-4 "advertisement-only teaching insufficient" lesson recurring:
  the load-bearing mechanism is the master PROBE, not the sub-agent instruction.
  The v8 rule already encodes this (coverage is a probe to RUN, not a caveat to
  write) — this exercise is the evidence.
- **Keep-signal data point** (alpha adjudication): the coverage refutation
  surfaced two category leaders the normal delegate-once flow missed, at the
  cost of ~4 master probes. Use toward the standing keep verdict.
