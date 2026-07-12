---
status: history
---

# Git-history privacy scan — 2026-07-12

First execution of the long-deferred "git-history personal-data review"
(PUBLISH_CHECKLIST, Purgatorio blocker; Jeremy-gated since 2026-07-09).
Read-only forensic pass over all 1,114 commits (history from 2026-03-05,
.git = 20M). Triggered while wiring the PyPI 0.8.0 name-reserve publish.

## Headline: the shipped package is clean

The sdist/wheel contain **zero** files from `research/` or `docs/` — only
`src/`, `tests/`, README, LICENSE, pyproject. So **PyPI 0.8.0 publish is
safe independent of everything below.** `twine check --strict` green.
No real secret/key anywhere in history (all `sk-ant-*`/`xoxb-*` matches are
test placeholders in `tests/`). No `.env`/keys/credential files ever committed.

## The real exposure: two "knowledge-layer" dirs in the public tree

All personal/employer leaks are localized to internal working notes, touched
by only a handful of commits (`redacted`=3 commits, `jstone`=1, `/Users/*`=1-2).

| Item | Where | Severity |
|---|---|---|
| Work email `jstone@redacted.com` in content | `research/orchestration-knowledge-layer/archive/00_CONVERSATION_TRANSCRIPT-RAW.md` | high (employer + work email) |
| Personal Mac username/path `/Users/jstone/Desktop/…` | same raw transcript | med (real username) |
| Internal hostname `git.redacted.com` + redacted/dev-team/leadership refs | `04_GAPS_AND_BLIND_SPOTS.md` (in BOTH dirs) | high (internal infra) |
| First-name path `/Users/jeremy/claude/…` | `docs/history/2026-07-09-thread-architecture-decisions-brief.md:8` | low |
| Location "Manti / Utah" (208 refs) | README example + test corpus | low (town-level, mostly intentional) |

### Disposition differs by dir
- **`research/orchestration-knowledge-layer/`** (8 files) — a KNOWN trailing
  duplicate (BACKLOG.md:1538; content merged into `docs/knowledge-layer/`
  per BACKLOG_DONE:1450). Holds the raw transcript = worst offender.
  → **remove candidate.**
- **`docs/knowledge-layer/`** (10 files) — INTENTIONAL canonical research,
  linked from `docs/INDEX.md:52`. → **scrub in place** (`git.redacted.com`
  line + redacted refs in `04_GAPS_AND_BLIND_SPOTS.md`), don't delete.

## Author-metadata (separate from content)

Three committer identities across history:
`slycrel@users.noreply.github.com` (973, clean), `agentic.poe@yahoo.com`
(86), `jstone@redacted.com` (45). A `.mailmap` only fixes display; real
removal needs a history rewrite.

## Recommended sequence (pending Jeremy)

1. Publish 0.8.0 now — decoupled, package clean.
2. Current-tree cleanup (reversible commit): remove `research/orchestration-
   knowledge-layer/`; scrub leaks in `docs/knowledge-layer/04_GAPS_AND_BLIND_
   SPOTS.md`; genericize the one `/Users/jeremy` path.
3. One bundled history rewrite (destructive; force-push + Ubuntu box re-clone
   + coordinate concurrent sessions): `git filter-repo` to purge the removed
   content AND normalize author emails (redacted/yahoo → noreply). Do (2)
   before (3) so a single rewrite covers content + metadata.

## DECISION — Jeremy, 2026-07-12

Scope narrowed after review:

- **KEEP all research/knowledge-layer docs** — the nuanced history has value
  even though it's not the daily working location. No deletions (reverses
  recommendation #2's removal of `research/orchestration-knowledge-layer/`).
- **The concern is commit attribution under the work email**
  `jstone@redacted.com` (46 commits) — "for privacy reasons on both sides."
  Fix = mailmap history rewrite → `slycrel@users.noreply.github.com`.
- **Not bothered by:** his name / content in chat logs & history; personal
  Mac username; Manti/Utah location ("a few hours away, not where I live").

**Rewrite proven on a throwaway clone (2026-07-12):** `git filter-repo
--mailmap` folds redacted (+ optionally the personal yahoo address) into the
noreply identity in <1s; 0 redacted emails remain; all content incl. research
docs preserved; commit count intact. Earliest work-email commit is
2026-03-17, so ~1101/1114 SHAs are rewritten = effectively a full-history
rewrite → force-push + box/session re-clone required.

**Open (pending Jeremy):** (a) also fold the personal `agentic.poe@yahoo.com`
(86 commits) into one identity, or keep it? (b) also `--replace-text` scrub
the work-email STRING + `git.redacted.com` where they appear in transcript
CONTENT, or leave content per "chat logs are fine"? (c) execution timing —
needs box quiet + concurrent sessions paused. 0.8.0 PyPI publish is
independent and can go before or after.
