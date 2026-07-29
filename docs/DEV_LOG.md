---
status: living
---

# Dev Captain's Log

One entry per session close, newest first, append-only — the dev-side
analog of the runtime captain's log. Entries sit under `## <date>`
headers (sessions span days; the date is when the entry was written),
carry a short bold title, name their thread slugs so `dev-recall` and
the next census can join against them, and each ends with a
**Surprised by:** line (Jeremy 2026-07-28: "your data omniscience
aside, there are always angles that surprise"). Plain-language rule
(Jeremy 2026-07-28): first use of a codename or internal term in an
entry gets a short gloss — the reader has less context than the
writer. This is a narrative record, not a work queue — obligations
still live in BACKLOG/GOAL_BRAIN; a session that ends conversationally
still gets a line (same spirit as SF-13). Rendered to the viz server's
Dev log tab at maro.feifdom.com alongside Runs and Reading.

## 2026-07-28

**Evening — telegram-runs review, proposals 2–4 adjudicated, chunk-9
spitfire** — threads: `telegram-runs-review`, `open-thread-structure`,
`compound-thinking`, `dev-log`. Reviewed the two self-diagnosis runs
Jeremy dispatched via Telegram (tawny-ferret: Karpathy LLM-wiki
pattern; dapper-oak: the Erdős-problems X-thread workflow) — full
record in `docs/history/2026-07-28-telegram-runs-review.md`. Headline:
both memos are decision-grade and mostly verify clean, but Poe's
prompt scaffolding claimed a gist was "already recovered" when no gist
text exists anywhere in the run, and its anti-escalation boilerplate
has compiled itself into maro's lesson store (lesson `db37d525`) — the
dispatch-escalate pathway the runs were meant to diagnose is no longer
being exercised at all. Review discussion split to a forked session.
Jeremy then adjudicated the remaining thread-structure proposals: the
narrative spine (this doc) ratified with formatting fixes, date
headers, and its own viz tab; the `THREAD[slug]` marker/collector
machinery declined for now ("let's not overengineer our dev workflow")
with a revisit rider on the next census; working-set promotions stay
self-serve with notification. His spitfire pass on the compound-thinking
soft items landed five dispositions (lens-not-schema for plan maps;
greenlight to try structural declare-blocked; learned-behavior global
to start; capability-investment default-yes; verbal-UX to BACKLOG) —
recorded in COMPOUND_THINKING_DESIGN.md §15 and GOAL_BRAIN.
**Surprised by:** the scaffolding-contamination find — a concierge's
per-prompt babysitting quietly becoming persistent learned behavior is
a failure mode none of our reviews had a category for; the lesson
store faithfully learned the wrong lesson from a false premise.

**Daytime — thread census, reading tab, drift batch** — threads:
`thread-census`, `house-style`, `docs-surfacing`, `dev-log`. Landed
the thread census (third real use of the star mini-orchestrator
skill): 56 threads / 7 states, 7 premise-drift finds — the headline
being that park reasons rot silently (C4's "box runs container off"
gate was 12 days stale; closed on the spot against live config).
Jeremy ratified the ACTIVE/PARKED split (cap 7), corrected the
quiz-decree as too strong (style matches purpose — runtime journal
amended, decision 83f06acf), and offered his work-side CodeLikeJeremy
proof-of-concept, which yielded six house-style patterns including the
headline steal: a style doc with a regression harness. Then a detour
on his "reading .md over ssh isn't a perfect experience" — the viz
server gained a Reading tab (docs/READING_QUEUE.md → reading.html,
GitHub-rendered links, nothing self-hosted), live at maro.feifdom.com
same session. AFK loose-ends pass closed drift find #5 (full suite
exit 0; the fragile parallel-runner seam turned out deleted by swarm
chunk 1) and examined the free-slot trio: two of three (depth-cap #31,
backend-resilience #32) were already shipped weeks ago behind stale
doc annotations, so the free slot goes to closure-check unification
(#30) by elimination. Jeremy adjudicated the drift batch on return:
all four re-parked on falsifiable reasons — his summary "all claims
and doc-guidance as much as anything" — with the thread-architecture
work getting a Remaining-pieces ledger (THREAD_ARCHITECTURE.md L1–L5,
census-tested) as the answer to "I'm a little concerned it will get
lost," and next-leap packaging holding first claim on the next free
slot. The reading queue's first row completed its full lifecycle
(queued → read → decided → Done) within one day of the feature
existing. **Surprised by:** the recon-delegation contract
(verbatim-quote + file:line + disposition) producing the repo's first
10/10-clean spot-verification sample — the historical 30–78%
hallucination rate apparently wasn't a model property but a
contract-shape property. And its immediate qualifier: perfect
quote-accuracy still inherited two stale "still open" doc annotations
into the census as live threads — currency is a separate check from
accuracy.
