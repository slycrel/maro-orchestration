---
name: reference_links
description: "Consult Jeremy's curated link farm (shared memory of ideas/tech/concepts) — sync first, check vintage before any absence claim, respect the questionable tag"
roles_allowed: [worker, researcher]
triggers: [link farm, reference links, prior art, "have we seen", curated links, known technique, existing implementation, research a technique]
---

# Reference Links — consulting the link farm

**What this is.** The link farm (`github.com/slycrel/link-farm`, cloned at
`~/claude/link-farm/`) is Jeremy's curated reference memory: ideas, tech,
and concepts he has taken time to identify as reference points for later.
It is not a scrape — every entry was deliberately kept. Treat it as a
shared memory surface, the same way lessons and knowledge nodes are
consulted, whenever a question touches techniques, tools, papers, agent
patterns, or "have we seen something about X."

**When to use.** Any research or design task that would benefit from "has
Jeremy already flagged something relevant" — new capability directions,
evaluating a technique, looking for prior art, sanity-checking whether an
idea has a known implementation. If you are about to search the web for a
concept, check here first; the curation signal (what Jeremy kept, and his
topic tags — including `questionable`) is information the web won't give
you.

## Discipline (non-negotiable, learned the hard way)

1. **Sync before searching.** The repo receives near-daily updates:
   `bash scripts/sync-link-farm.sh` (pulls ff-only, prints the vintage).
2. **Check the vintage before claiming absence.** On 2026-08-02 a search
   against a stale clone (frozen 2026-04-11) reported six on-point RLM
   articles as absent — all postdated the snapshot. An absence claim is
   only valid if the reported vintage covers the period where the thing
   would exist.
3. **Search generously, then precisely.** Substring traps are real
   (`repl` matches "replaces"). Start wide, tighten with word boundaries,
   and try synonyms — the summaries are prose, not keywords.

## How to query

The canonical store is the SQLite db; the JSON is a synced export.

```bash
# vintage (always first)
sqlite3 ~/claude/link-farm/db/ai_links.db \
  "select count(*), min(date), max(date) from posts"

# full-text-ish scan over summaries
sqlite3 ~/claude/link-farm/db/ai_links.db \
  "select date, url, substr(summary,1,120) from posts
   where summary like '%recursive%' order by date desc limit 20"

# topic-tagged slices (tags include: agent-design, research, dev-practices,
# claude-code, management, industry, questionable, skills-mcp, general)
sqlite3 ~/claude/link-farm/db/ai_links.db \
  "select date, url, substr(summary,1,120) from posts
   where topics like '%agent-design%' order by date desc limit 20"
```

Python fallback when sqlite3 is unavailable: load `posts_final_v3.json`
and filter — but prefer the db; the JSON has historically lagged.

## Interpreting results

- **`questionable` tag is Jeremy's skepticism marker** — the entry was
  worth keeping but its claims weren't vetted. Report it with that
  caveat, never as an endorsement.
- Entries are pointers, not content: the summary is a curation note. For
  claims that matter, fetch the linked source — the same
  claimed-vs-probed discipline as everywhere else.
- If a search surfaces something maro's knowledge layer should have had,
  note it: the `lf-` knowledge-node import lags this repo by design
  until the lf- edge-contamination question is settled (BACKLOG).

## Maintenance

- The clone is a read-only mirror; never commit to it from here. Local
  edits mean something is wrong — `git status` should always be clean.
- Sync rides consultation (this skill), not a scheduler — the
  no-daemons invariant applies. If the vintage is ever months stale
  despite this skill existing, the skill isn't being loaded when it
  should be; fix the trigger description, not the invariant.
