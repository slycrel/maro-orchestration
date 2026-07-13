---
status: record
---

# Git-history privacy scan — 2026-07-12

First execution of the long-deferred "git-history personal-data review"
(PUBLISH_CHECKLIST, Purgatorio blocker; Jeremy-gated since 2026-07-09).
Read-only forensic pass over all 1,114 commits (history from 2026-03-05,
.git = 20M). Triggered while wiring the PyPI 0.8.0 name-reserve publish.

> **STATUS: EXECUTED 2026-07-12.** The rewrite below was run and force-pushed
> to all branches/tags (`main` 16cd656→77db43c, `factory` 27acb82→1476f97).
> Verified 0 employer-token occurrences on every surface (author/committer,
> commit messages, blob content, tag tagger) across all refs; yahoo identity
> kept; research docs intact. Local pre-rewrite backup: scratch
> `maro-mirror-backup-prewrite` (never pushed). **Note:** this rewrite
> self-scrubbed THIS doc — the employer token was obfuscated to `redacted`
> wherever it appeared below, so lines like the mailmap/`replace-text`
> examples now read circularly (`redacted==>redacted`). That is expected and
> harmless; the config is historical and the literal match-token is
> deliberately not restated. Meaning survives.

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

**Choices FINALIZED (Jeremy, 2026-07-12):**
- (a) **KEEP `agentic.poe@yahoo.com`** — it marks OpenClaw/Codex-initiated
  changes (him, indirectly); a useful historical/future nuance. Mailmap
  remaps ONLY the work email.
- (b) **Obfuscate the employer strings** in content AND commit messages
  (case-insensitive `redacted` → `redacted`) — "out of a sense of security
  for my employer... obfuscated is as good as deleting there." Keeps the
  surrounding context. Covers the work-email string (`jstone@redacted.com`),
  the hostname (`git.redacted.com`), and prose "redacted" mentions.
- (c) **Execute soon** — after Jeremy wraps the concurrent Sonnet session;
  "sooner and work through it than later and harder." 0.8.0 publish is
  independent, before or after.

**Final config PROVEN on a throwaway clone (2026-07-12, 2.8s):** all three
surfaces clean — author/committer metadata `redacted`=0, blob content=0,
commit messages=0; yahoo identity kept (86); research docs preserved (8);
commit count intact. NOTE the `--replace-message` pass is REQUIRED — two
commit *messages* (this session's own doc commits) name the work email and
`--replace-text` alone (blobs only) misses them.

## RUNBOOK — one-shot execution (gated on Jeremy's go + quiet box)

Config files (regenerate if scratch is gone):

`mailmap.txt` (work email only; yahoo untouched):
```
Jeremy Stone <slycrel@users.noreply.github.com> <jstone@redacted.com>
```
`replace-text.txt` (used for BOTH --replace-text and --replace-message):
```
regex:(?i)redacted==>redacted
```

Pre-flight (Jeremy): pause other session(s); confirm NOTHING unpushed
anywhere incl. the Ubuntu box; note current `origin/main` HEAD.

Execute (from any full clone with push rights — e.g. the Mac):
```
git clone --mirror git@github.com:slycrel/maro-orchestration.git maro-mirror
cd maro-mirror
git filter-repo --mailmap mailmap.txt \
  --replace-text replace-text.txt \
  --replace-message replace-text.txt --force
git push --force --mirror   # or: git push --force origin 'refs/heads/*'
```
(NB: filter-repo strips the `origin` remote by design; `--mirror` clone +
`push --mirror` sidesteps re-adding it. Verify `git log --all | grep -ci
redacted` == 0 in the mirror BEFORE pushing.)

Post (Jeremy/box/sessions): on every other clone,
`git fetch origin && git reset --hard origin/main` (old local SHAs are now
orphaned — ~1101/1114 rewritten). No tags exist yet; trusted publisher keys
off repo/workflow/env, not SHAs — unaffected.

The rewrite will also self-scrub `redacted` from THIS doc + GOAL_BRAIN's
decision entries (they quote the email) — expected and harmless; the meaning
survives as `redacted`.
