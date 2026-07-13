---
name: changelog_digest
description: Summarize recent git commits into a short operator digest for stakeholder reporting
roles_allowed: [worker]
triggers: ["changelog digest", "summarize commits", "commit digest", "generate changelog", "recent commits summary", "what changed since last release"]
---
# Changelog Digest

## Overview

This skill summarizes a git repository's recent commits into a concise operator digest for quick stakeholder updates. It extracts meaningful changes from commit history, groups them by impact category, and formats them as a brief narrative suitable for status reports or incident postmortems.

Use this skill when you need to communicate what has changed in a repository to non-developer stakeholders, generate automated release notes, or audit recent deployments.

## When to use

- **Before deployments:** summarize commits in this release to catch unintended changes
- **Status reporting:** include a digest of recent activity in weekly/daily briefs
- **Incident review:** understand what code changes preceded a production issue
- **Audit trail:** document what was deployed and when for compliance/traceability

Do NOT use this skill for:
- Deep code review or impact analysis (use code_review skill)
- Generating formal release notes with versioning (requires semver integration)
- Analyzing cross-repository changes (single repo, single branch)

## Prerequisites

- Git CLI available (`git` command in PATH)
- Read access to the target repository
- At least one commit in the repository

## Steps

1. **Determine commit scope**
   - Default to commits on `main` or `master` branch from the last 7 days
   - If a specific ref or tag is provided, use commits since that ref
   - If a date range is specified, filter to commits within that range
   - Exclude merge commits (parent count > 1) by default using `--no-merges` unless they contain meaningful messages

2. **Fetch raw commits**
   - Run: `git log --oneline --decorate --date=short --no-merges <branch> --since="7 days ago"`
   - Capture: commit hash, author, date, and full commit message
   - If no commits found, return: "No changes in the specified period"

3. **Parse and categorize**
   - Group commits by conventional commit type if present:
     - `feat:` → Features
     - `fix:` → Bug Fixes
     - `docs:` → Documentation
     - `chore:`, `ci:`, `refactor:` → Maintenance
     - Other (no prefix) → Miscellaneous
   - If conventional commits aren't used, group by author or date buckets (day-by-day for digests < 30 days)

4. **Truncate and filter**
   - Show max 10 commits per category (or 20 total if ungrouped)
   - If truncating, add: "… and N more"
   - Omit commits with subject length < 5 characters (likely noise)
   - Omit automated commits (authored by bots: dependabot, renovate, github-actions) unless they contain keywords: deploy, release, version

5. **Format output**
   - Use markdown bullet-list format
   - Each bullet: `<category> · <date> · <author-initials>: <commit-subject-60-chars-max>`
   - If grouped by type, use second-level heading per category: `### Features`, `### Bug Fixes`
   - Prepend summary: `Recent changes (main, last 7 days): N commits`

6. **Generate digest**
   - Combine all formatted commits into a single markdown block
   - Append footer: `Query run at [ISO-datetime]; branch: [branch_name]`
   - Total output must not exceed 30 lines (truncate categories if needed)

7. **Return result**
   - Emit the formatted markdown digest
   - If errors occur (e.g., git command fails), return error message with troubleshooting step

## Output Format

```
Recent changes (main, last 7 days): 5 commits

### Features
- feat · 2026-07-10 · ab: Add user authentication flow
- feat · 2026-07-09 · cd: Extend API rate limits to 1000 req/min

### Bug Fixes
- fix · 2026-07-08 · ef: Prevent crash on null session token
- fix · 2026-07-07 · ab: Handle malformed JSON in request body

### Maintenance
- chore · 2026-07-06 · gh: Update dependencies
- ci · 2026-07-05 · ci-bot: Run integration tests on PR

Query run at 2026-07-10T14:32:00Z; branch: main
```

## Worked Example

**Input:** Repository `/repo`, branch `main`, window `last 7 days`

**Raw git log output:**
```
abc1234 (HEAD -> main) feat: Add user authentication flow
def5678 fix: Prevent crash on null session token
ghi9012 chore: Update dependencies
jkl3456 ci: Run integration tests on PR
mno7890 fix: Handle malformed JSON in request body
```

**Digest output (after processing):**
```
Recent changes (main, last 7 days): 5 commits

### Features
- feat · 2026-07-10 · ab: Add user authentication flow

### Bug Fixes
- fix · 2026-07-08 · ef: Prevent crash on null session token
- fix · 2026-07-07 · ab: Handle malformed JSON in request body

### Maintenance
- chore · 2026-07-06 · gh: Update dependencies
- ci · 2026-07-05 · ci-bot: Run integration tests on PR

Query run at 2026-07-10T14:32:00Z; branch: main
```

## Git CLI Dependency

This skill requires the `git` command-line tool. It uses:
- `git log --oneline --decorate --date=short --no-merges <branch> --since="7 days ago"` — fetch commits
- `git rev-parse --abbrev-ref HEAD` — get current branch name
- `git show --format=%an <commit>` — get author for a commit

If git is unavailable or the repository inaccessible, the skill exits with an error message.

## Common Traps & Edge Cases

### Empty history
If no commits exist in the period: `No changes in the specified period (main, last 7 days).`

### Merge commits
Merge commits (e.g., "Merge branch 'feature' into main") often obscure real changes. Exclude via `--no-merges`. If a merge has a meaningful message (not just "Merge ..."), include it.

### Revert chains
A reverted commit appears as two entries (the original + the revert). Both are valid — they document the change and its reversal. Do not de-duplicate.

### Very active repositories
If a repo has >20 commits/day, truncate to max 10 per category (or 20 total) and append "… and N more [view full log]". Never exceed 30 lines.

### Non-conventional messages
If commits don't follow `type:` conventions, group by author or date. Example: "Recent changes by alice (7 commits), bob (3 commits)".

### Detached HEAD
Report the commit hash instead of branch name: `branch: abc1234 (detached)`.

### Bot-authored commits
Exclude CI/CD bot commits (dependabot, renovate, github-actions) by default unless they contain `deploy`, `release`, or `version`. Re-include if explicitly requested.

### Access restrictions
If certain commits are hidden by permissions, list only those readable and note at the footer: "[N commits hidden due to permissions]".

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `fatal: not a git repository` | Working dir is not in a git repo | Verify repo path and permissions |
| `fatal: your current branch 'main' does not have any commits yet` | Empty/newly initialized repo | Seed with at least one commit |
| `permission denied` | No read access | Check file permissions and git credentials |
| `fatal: ambiguous argument 'main'` | Branch does not exist | Verify branch name (`git branch -a`) or use `HEAD` |

## Notes

- The digest summarizes a **single branch** in **one repository**. Multi-repo digests require separate invocations.
- Output is ephemeral; for persistent records, export to markdown file or log service.
- All git commands are read-only (no modifications to the repository).
- Date format: ISO 8601 short form (YYYY-MM-DD).
- Author initials are the first two characters of the git author name (or full name if < 2 chars).

<!-- promoted 2026-07-13 (BACKLOG #22 blank-slate curation) from the workspace
     skills-lite artifact (~/.maro/workspace/skills/changelog_digest.md,
     promoted_from 21f5b815, tier: skills-lite) into the repo default set.
     Content unchanged from the version live-verified 2026-07-10 (changelog
     digest live batch, CAPABILITIES.md Tier 2 "repo-digest" row); only the
     workspace-bookkeeping frontmatter (tier/promoted_from) was stripped —
     those track runtime promotion provenance, not a shipped default's
     identity. Gap this closes: the catalog said "exists: changelog_digest"
     but the skill only lived in ~/.maro/workspace/skills/, which is
     git-ignored runtime state (see CLAUDE.md workspace table) — a fresh
     `maro bootstrap` never got this skill until this commit. -->
