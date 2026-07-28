---
status: living
---

# Dev Captain's Log

One paragraph per session close, newest first, append-only — the dev-side
analog of the runtime captain's log. Entries name their thread slugs so
`dev-recall` and the next census can join against them, and each carries a
**Surprised by:** line (Jeremy 2026-07-28: "your data omniscience aside,
there are always angles that surprise"). This is a narrative record, not a
work queue — obligations still live in BACKLOG/GOAL_BRAIN; a session that
ends conversationally still gets a line (same spirit as SF-13).

---

**2026-07-28** — threads: `thread-census`, `house-style`,
`docs-surfacing`, `dev-log`. Landed the thread census (star use #3):
56 threads / 7 states, 7 premise-drift finds — the headline being that
park reasons rot silently (C4's "box runs container off" gate was 12 days
stale; closed on the spot against live config). Jeremy ratified the
ACTIVE/PARKED split (cap 7), corrected the quiz-decree as too strong
(style matches purpose — runtime journal amended, 83f06acf), and offered
his work-side CodeLikeJeremy PoC, which yielded six house-style patterns
including the headline steal: a style doc with a regression harness. Then
a detour on his "reading .md over ssh isn't a perfect experience" — the
viz server gained a Reading tab (docs/READING_QUEUE.md → reading.html,
GitHub-rendered links, nothing self-hosted), live at maro.feifdom.com
same session. AFK loose-ends pass closed drift find #5 (full suite exit
0; the fragile parallel-runner seam turned out deleted by swarm chunk 1)
and examined the free-slot trio: two of three (depth-cap #31,
backend-resilience #32) were already shipped weeks ago behind stale doc
annotations, so the free slot goes to closure-check unification (#30) by
elimination. Jeremy adjudicated the drift batch
on return: all four re-parked on falsifiable reasons — his summary "all
claims and doc-guidance as much as anything" — with thread-arch getting
the Remaining-pieces ledger (THREAD_ARCHITECTURE.md L1–L5, census-tested)
as the answer to "I'm a little concerned it will get lost," and next-leap
packaging holding first claim on the next free slot. The reading queue's
first row completed its full lifecycle (queued → read → decided → Done)
within one day of the feature existing. **Surprised by:** the
recon-delegation contract (verbatim-quote + file:line + disposition)
producing the repo's first 10/10-clean spot-verification sample — the
historical 30–78% hallucination rate apparently wasn't a model property
but a contract-shape property. And its immediate qualifier: perfect
quote-accuracy still inherited two stale "still open" doc annotations
into the census as live threads — currency is a separate check from
accuracy.
