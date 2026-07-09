---
name: report_synthesize
description: "Combine N artifact files from sub-agents into one synthesis with per-claim source attribution and an explicit conflicts section"
roles_allowed: [worker]
triggers: [combine artifact files, merge sub-agent outputs, synthesize findings from multiple artifacts, cross-reference sources, reconcile source files, build a synthesis report from artifacts, per-claim source attribution]
---

## Overview

Use this skill when you have two or more artifact files (sub-agent outputs, notes, extracts) covering the same topic and need one coherent document out of them. Produces a structured synthesis where every claim is traceable to its source file and disagreements between sources are surfaced explicitly — never averaged, blended, or silently dropped.

## Steps

1. **Inventory the artifacts** — list every input file by path/name and read each one fully before writing anything.
2. **Extract claims per artifact** — for each file, pull out its discrete factual claims as a flat list, tagging each with its source file.
3. **Group claims by subject** — cluster claims across artifacts that address the same sub-topic or question, so overlapping and competing claims sit next to each other.
4. **Detect conflicts** — within each cluster, compare claims pairwise; if two sources state incompatible facts (different numbers, opposite conclusions, mutually exclusive statuses), mark it as a conflict rather than picking a winner or blending the values.
5. **Write the synthesis body** — produce prose/sections organized by subject. Every claim sentence carries an inline source citation (e.g. `[artifact_a.md]`) pointing to the exact file it came from. Do not state a claim without attribution.
6. **Write the Conflicts section** — add an explicit `## Conflicts` section listing each detected disagreement: the subject, the competing claims verbatim (with sources), and — only if evidence supports it — which is likely correct and why. If no signal indicates one side is more credible, say so instead of guessing.
7. **Check for silent smoothing** — reread the synthesis body and confirm no conflicting claim was merged into a single averaged/hedged statement outside the Conflicts section; if found, split it out.
8. **Save the output** — write the finished synthesis to a file (e.g. under `output/`) and note in it which artifact files were consumed.

## Quality gates

- Every factual claim in the synthesis body has an inline source attribution.
- Any pair of sources that state incompatible facts on the same subject appears in the Conflicts section — it must not be resolved by averaging, hedging, or omission elsewhere in the document.
- The Conflicts section names the specific sources and quotes or closely paraphrases each side of the disagreement.
- If sources fully agree on a subject, no conflict entry is fabricated for it.

<!-- built by Maro itself (dogfood run 59a9fdd7-cobalt-saffron, 2026-07-09) as 1.0 item (f);
     closure verdict goal_achieved=true @0.95; verified against a planted-contradiction fixture
     set (surfaced in Conflicts, not averaged away); reviewed and graduated by hand. -->
