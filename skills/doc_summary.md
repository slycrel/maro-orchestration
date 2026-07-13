---
name: doc_summary
description: "Compress one long document into a faithful one-page operator summary — every claim traceable to the source, nothing invented, length actually capped"
roles_allowed: [worker]
triggers: [summarize this doc, one-page summary, operator summary, faithful summary, what changed what's left, read this and summarize]
---

## Overview

Use this skill when the goal is a single long document (design doc, report,
transcript, spec) and the ask is a short, faithful compression of it — not
combining multiple artifacts (`report_synthesize` is for that) and not
format conversion or table extraction (`document_process` is for that).
The failure mode this exists to prevent: a summary that reads well but
states things the source doc never actually said, or a "one-pager" that
quietly runs three pages.

## Steps

1. **Read the entire document before writing anything.** No summarizing
   from headings/skimming — a claim in the output must trace to a passage
   you actually read, not one you inferred from a section title.
2. **Identify the doc's own shape** — for a design/decision doc: what
   changed, what was decided, what's left open. For a narrative/report: the
   thesis and its main supporting points. Use the doc's own structure,
   don't impose a template it doesn't fit.
3. **Extract only claims the source actually states.** Paraphrase is fine;
   invention is not. If the doc is ambiguous or ends without a resolution,
   report that ambiguity — don't resolve it on the source's behalf.
4. **Compress to one page** (roughly 400-600 words, or the length the goal
   specifies) preserving the load-bearing facts and dropping the rest
   explicitly rather than silently.
5. **Spot-check 3-5 sentences of the summary** against the exact source
   passage they claim to represent. Anything that can't be traced back
   gets cut or softened to "the doc is unclear on X."
6. **Note what was cut for length** — a one-line "not covered in this
   summary" pointer so the reader knows the one-pager isn't the whole
   document, and can go back to the source for what's missing.
7. **Flag internal contradictions or stale/superseded sections** rather
   than silently picking one version to report as the doc's position.
8. **Save the summary as an artifact** if the run produces file output,
   noting the source document path/name in it.

## Quality gates

- Every claim in the summary traces to an actual passage in the source —
  no claim survives that the spot-check (step 5) couldn't confirm.
- The output actually respects the length cap — "one-page" that runs long
  fails this skill's job regardless of quality.
- Contradictory or superseded content in the source is reported as such,
  never quietly resolved into a single confident answer.
- If the document is genuinely too long/dense to fully read in one pass,
  say what was and wasn't read rather than presenting a partial read as
  complete coverage.

<!-- crystallizes CAPABILITIES.md Tier 2 ("doc-summary"): "Read this design
     doc and write a one-page operator summary: what changed, what's left."
     Built 2026-07-13, BACKLOG #22 blank-slate curation. Live-verified
     2026-07-13 (handle_id b5d35f89, `direct:`-prefixed handle.py run in this
     worktree) against skills/arch-quality-selfimprove.md: produced a
     58-line/459-word summary, within the length cap, and largely faithful
     on manual spot-check — with one real miss the skill's own step-5 gate
     should have caught: the closing line claims "8 core modules" and lists
     8 filenames, but the source's own File Map table has 9 rows
     (skill_types.py silently dropped/undercounted). See docs/CAPABILITIES.md
     Tier 2 row for the full note. Distinct from report_synthesize (N
     artifacts → one synthesis with a Conflicts section) and document_process
     (format/table extraction, not narrative compression). -->
