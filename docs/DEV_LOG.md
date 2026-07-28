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
same session. **Surprised by:** the recon-delegation contract
(verbatim-quote + file:line + disposition) producing the repo's first
10/10-clean spot-verification sample — the historical 30–78%
hallucination rate apparently wasn't a model property but a
contract-shape property.
