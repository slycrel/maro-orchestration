---
status: record
---

# Adversarial Review — Tire-Runs Fixes (2026-07-27)

Post-land review of the five tire-runs fixes (branch `runs-tangent-fixes`,
base `da64c6d`, diff 443 insertions / 13 files → Large ⇒ 3 Codex lenses:
Skeptic, Architect, Minimalist; read-only `codex exec`, opposite model
family per standing discipline). Intent under review: make the four
approved run-fixes (provenance prose-slash gate, planning token caps,
navigator vantage rule, continuation-aware project routing) plus closure
skip observability land without changing API-backend spend behavior or
non-dispatch prompt bytes, and without opening a path-escape.

## Verdict: CONTESTED → remediated same session

12 raw findings deduped to 6. Master verification of the three
load-bearing mechanism claims before any fix (per verify-before-fix —
historical reviewer hallucination rate 30–78%): 3/3 REAL this round
(live `python3` demo of `is_dir()`/`resolve()` symlink behavior; API
adapters forwarding `max_tokens` directly at `llm.py:2321`/`2451`;
binder accepting non-menu names). Zero hallucinated findings —
eleventh consecutive clean round.

## Findings

1. **[high] F1 — symlink escape of projects_root** (unanimous:
   Skeptic 1, Architect 3, Minimalist 1). `is_dir()` follows symlinks,
   so `projects/linked -> /outside` could be offered by the menu AND
   accepted by the binder; downstream writes are `projects_root()/slug`.
   **Fixed**: menu builder filters entries whose `resolve()` is not
   relative to the resolved root; binder independently re-checks the
   same containment. Pinned in test_handle.py (escape pick ignored) +
   test_navigator_prompt.py (menu filters the link).
2. **[medium] F2 — pick not constrained to the offered menu**
   (Skeptic 2, Architect 2). Prompt says "names not on this list are
   invalid" but the binder accepted any slash-free existing dir.
   **Fixed**: binder recomputes `_recent_projects_menu()` and requires
   membership; the recompute can race a just-deleted project, and
   rejection then equals pre-menu behavior (fresh slug) — safe side.
   Pinned (existing-dir-not-on-menu ignored).
3. **[high→resolved] F3 — cap fix not backend-scoped; API spend
   changes** (unanimous: Skeptic 3, Architect 1, Minimalist 3). The
   first draft raised call-site `max_tokens` 512/1024/1200 → 4000; API
   adapters forward that verbatim as the paid output cap — violating
   the review's stated "API spend behavior must not change" constraint.
   **Fixed by redesign**: new `output_cap_tokens` kwarg read ONLY by
   the subprocess adapter's env-cap block, headroom-only
   (`max(cap, kwarg)`); every call site reverted to its original
   answer-sized `max_tokens`, so API requests are byte-identical to
   pre-fix. All adapters (incl. FailoverAdapter forwarding) verified to
   absorb `**kwargs`. Pinned both directions in test_llm.py (widens
   env cap without touching max_tokens; never tightens).
4. **[medium] F4 — `_path_shaped` residuals, opposite directions**
   (Architect 4: prose whose first word coincidentally names a real dir
   still claims and can re-demote; Minimalist 4: extensionless missing
   relative input with absent first segment is no longer claimed, so
   that fabricated-input demotion is lost). **Accepted as residual**,
   known-gap pins added for both (test_handle.py). Rationale: the two
   residuals pull in opposite directions and any cheap tightening of
   one widens the other; the 4d20b559 false demotion of a delivered
   run was the costlier error class, and absolute/anchored/extension
   fabrications still demote.
5. **[low] F5 — one bad entry drops the whole menu** (Architect 5).
   **Fixed**: per-entry try in the menu builder; pinned with a dangling
   symlink beside a real project.
6. **[medium] F6 — dir-mtime ranking misses real activity**
   (Minimalist 2). Editing NEXT.md doesn't bump the parent dir inode,
   so the project that matters could fall off the top-5. **Fixed**:
   ranking key = max(dir mtime, NEXT.md mtime); pinned (stale-inode
   project with fresh NEXT.md ranks first).

## What went well

Reviewers raised no findings against the vantage-rule prompt change
(fix 3 of the examination record), the closure `skip_detail`
observability change, or the provenance gate's core prose-slash fix —
the 4d20b559 regression pin stood unchallenged.

## Lead judgment

- F1 accept (verified live; unanimous; real write-path escape).
- F2 accept (contract said menu-only; enforcement belonged at the
  executor, which is exactly the boundary the lenses named).
- F3 accept — the constraint violated was my own stated one; the
  redesign is strictly better than the first draft, not a compromise.
- F4 accept-as-residual, not fix: reviewers' two findings jointly show
  the gate is a genuine tradeoff, not a bug — pins document the chosen
  side so a future change is deliberate.
- F5/F6 accept (cheap, real, menu utility depends on both).
- No findings rejected this round.
