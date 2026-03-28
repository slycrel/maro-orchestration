# Adversarial Self-Review — Regression Test Specification

## Purpose

Test whether the orchestration system can autonomously review its own codebase
given only a repo URL and a task description. This is the minimum competence bar:
if it can't do this, it can't be trusted with more ambitious autonomous work.

## Test Passes

### Pass A — Blind (realistic prompt)

**Prompt:** "Check out this repo at https://github.com/slycrel/openclaw-orchestration
and do a deep, critical review of what it gets right, what it's missing, and what
should improve. Be adversarial, concrete, and evidence-based."

**Tests:**
- Can it decide how to acquire the repo (clone, read local, etc.)?
- Can it structure a review without hand-holding?
- Can it criticize, not just summarize?
- Does it produce actionable findings?

**Success criteria:** Setup accounting + strengths + weaknesses + actionable improvements.
**Failure:** "Here's a nice summary of the README."

### Pass B — Seeded fallback

**Same prompt** but with a clean local checkout provided. Separates "can it set up
the work?" from "can it think clearly about the work?"

Run only if Pass A faceplants on repo acquisition.

### Pass C — Independent human/AI review

Compare orchestration output against an independent review (e.g., Grok's feedback,
or a manual review) to assess:
- Overlap (did it find the same issues?)
- Misses (what did it fail to notice?)
- False positives (did it flag non-issues?)
- Shallowness vs depth
- Self-forgiveness (is it too kind to itself?)

## Expected Output Structure

1. **Setup/accounting** — what source, whether cloned, why that path
2. **What the repo gets right** — architecture wins, evidence from code/tests
3. **What's weak/missing/risky** — gaps, monolith hotspots, test theater
4. **Actionable improvements** — near-term, medium-term, v1 boundary
5. **Notable unknowns** — anything it couldn't verify

## Minimum Bar

A competent high-schooler with coaching could produce a rough version of this.
The orchestration should do significantly better — reasonable setup choices,
real issues found, useful next steps, evidence-based.

## How to Run

```bash
# Pass A (blind — no pre-seeded repo)
POE_LOG_LEVEL=INFO bash scripts/adversarial-review.sh

# Pass A dry-run (plan only)
bash scripts/adversarial-review.sh --dry-run

# Compare against Grok's review (in conversation history, March 27 2026)
# and against independent manual review
```

## Historical Results

- **2026-03-27 (Codex/openclaw-poe):** Got through 8/13 code review steps before
  timing out on "run full pytest and analyze" (step 9). Setup/acquisition was the
  first bottleneck (fixed), then step granularity (fixed), then subprocess timeout
  on test execution (fixed with adaptive timeout + decompose prompt).
- **2026-03-28 (Codex/openclaw-poe):** Got through 18/20 steps, stuck on
  budget_exhaustion (max_iterations=20, since bumped to 40). Partial review was
  substantive — successfully read and analyzed all major module groups.
