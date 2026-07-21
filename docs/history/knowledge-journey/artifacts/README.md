---
status: record
---

# Knowledge-journey archaeology — raw artifacts

Provenance for the 2026-07-20/21 history excavation (Jeremy: "save the
artifacts of the history archaeology"). The curated outputs are the era files
one directory up; these are the materials they were built from.

| File | What |
|---|---|
| `knowledge-journey-workflow-journal.json` | The workflow run journal: every agent() call's actual return value (12 era excavations, 12 adversarial verifications, 12 writes, completeness critic). The distilled record — read this before the tarball. |
| `knowledge-journey-workflow-script.js` | The workflow script that ran the excavation (serial for-loop form, post-OOM rewrite — see `feedback_workflow_parallelism_box` memory for why). |
| `checkpoint-review-grounding-checker.md` | Verbatim final report: history-grounding check of the amended plan (zero anchor rot, 7 unabsorbed edges, spot-checks). |
| `checkpoint-review-adversary.md` | Verbatim final report: adversarial review of the checkpoint decisions (1 BLOCKER, 6 SIGNIFICANT, 5 MINOR — including catching this session's own citation inversions). |
| `raw-agent-transcripts.tar.gz` | Full tool-call-level jsonl transcripts of all session subagents (48 workflow agents + 5 article reviewers + completeness critic + 2 checkpoint reviewers; ~18M unpacked). |

The conversational record of the same arc: `docs/conversations/2026-07-20-swarm-review-arc.md`.
Session: `438e91bd` on the maro box, 2026-07-20 → 07-21.
