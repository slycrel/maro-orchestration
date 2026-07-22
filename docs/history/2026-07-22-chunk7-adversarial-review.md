---
status: record
---

# Chunk-7 Adversarial Review — 2026-07-22

Per-chunk review discipline (Jeremy's /goal). Reviewed: commit `b4c8c13`
(chunk 7 — discretion readout: `discretion_readout.py` judgement-report
CLI + the navigator_shadow by-lesson-inject A/B table). Reviewers: 3 Codex
lenses (`codex exec`, opposite model), parallel, read-only against the
live tree.

reviewer_cli=codex; all three output files non-empty (skeptic.md,
architect.md, minimalist.md).

## Intent

A judgement report, not a spend report: read-only tabulations over the
captain's log and step-costs.jsonl — EFFORT headline (dollars one trailing
column), retry/replan discretion, reinjection volume, gate-family A/B
tables, novelty distribution, duty cycle, and an explicit
"Not computable today" honesty block. Plus: `navigator_shadow --agreement`
grows a by-lesson-inject table closing the V5 watch-with-no-readout gap.
No new emitters — only reads of what exists.

## Verdict: PASS (with fixes)

No high-severity findings. Five distinct findings after dedup (the top one
unanimous across all three lenses), **5/5 verified real against the tree,
0 hallucinated — eighth consecutive clean round.** Four accepted (fixed
same session), one accepted-in-part, one sub-recommendation rejected.

## Findings

1. **[medium, UNANIMOUS] The EFFORT headline was a silent tail sample**
   (Skeptic + Architect + Minimalist; prove-it-works /
   foundational-thinking / subtract-before-you-add). `effort_summary()`
   called `metrics.load_step_costs(limit=5000)` — a newest-first tail
   reader — then labeled the result "last N active day(s)" with no
   coverage caveat. Once the file exceeds 5000 rows, older days in the
   displayed window silently go partial or missing — the exact
   silent-cap sin the module's own docstring preaches against, in its
   headline section. Live file is 3700 rows, so the bug was armed, not
   yet firing. **Fixed**: full-file read via
   `read_jsonl_tail(path, limit=None)` — this is an offline CLI; there
   is no reason to sample. Pinned
   (`test_effort_reads_full_file_not_a_tail_sample`).

2. **[medium] `--log-dir` produced a mixed-corpus report** (Architect +
   Minimalist; boundary-discipline). The flag redirected the events glob
   but EFFORT still read the process-default workspace step-costs — an
   operator pointing at an archive got archive events mixed with live
   cost telemetry. **Fixed**: `build_payload(base=...)` sources BOTH
   inputs from the one directory (`base/step-costs.jsonl`); the two
   files co-locate in the memory dir by construction. Pinned
   (`test_log_dir_sources_step_costs_from_same_dir`).

3. **[medium, accepted in part] The advertised CLI command fails from a
   fresh checkout** (Skeptic; prove-it-works). `python3 -m
   discretion_readout` needs `PYTHONPATH=src` in a dev checkout (pytest
   masks this via `pythonpath = ["src"]`; installed envs get the module
   via py-modules). **Fixed in part**: the two operator-facing doc
   mentions this chunk added (DEFAULTS row, arch skill) now carry the
   `PYTHONPATH=src` prefix, matching CLAUDE.md's convention. Rejected:
   a console-script entry point — subtract-before-you-add; the repo has
   many `-m` CLIs and none get scripts until an operator actually needs
   one. Pre-existing bare `python3 -m` references elsewhere are house
   precedent, out of this chunk's scope.

4. **[low] Input-read failures shrank denominators silently** (Architect;
   boundary-discipline / prove-it-works). `load_events()` swallowed
   unreadable files and malformed lines — a corrupt rotated archive
   would quietly shrink every count while the report presented them as
   complete, contradicting the module's own honesty rule. **Fixed**:
   coverage counters (files_read / files_failed / lines_skipped)
   surfaced in the payload and as a report line; unreadable files get an
   explicit "denominators above are incomplete" warning. Pinned (loader
   test asserts the counters).

5. **[low, REJECTED in part] CLI flags contradict the "no flags" posture**
   (Minimalist; subtract-before-you-add). Rejected: the docstring's
   "no flags" meant no *config* flags (killswitches) — `--json` is the
   machine-readable seam and `--log-dir` has a real use (archive
   analysis) now that finding 2 made it coherent. Accepted: the
   docstring now says "no config flags" and distinguishes CLI surfaces
   explicitly.

## Lead Judgment

- Accept 1: unanimous, and it hit the module exactly where its own
  stated principle lives. A readout that preaches "no silent caps" must
  not sample its headline. Root fix (read everything), not a caveat.
- Accept 2: corpus coherence is a boundary invariant; the fix also made
  the CLI test hermetically meaningful.
- Accept 3 in part: real papercut, cheapest correct fix is doc-side;
  a console script is machinery no one asked for.
- Accept 4: the honesty rule the module was built on applies to its own
  inputs — the reviewers used the module's stated intent against it,
  which is the review working as designed.
- Reject 5's deletion ask: flexibility-without-second-use-case is the
  right test, and --log-dir now passes it (archives exist on this box);
  --json is the standard machine surface every sibling readout has.

## What Went Well

- No reviewer faulted the metric definitions themselves: evidence-free
  retry (same-fingerprint), the stamped-only-when-positive lessons_injected
  grouping, the SECOND_FAMILY_ prefix normalization, zero-claim
  denominators, or the pre-chunk-6 novelty split.
- The "Not computable today" block drew zero challenges — the honesty
  framing held up under three adversarial lenses (they instead demanded
  MORE of it, which is the right direction of pressure).
- Eighth consecutive review round with zero hallucinated findings.

## Collateral

- First live A/B numbers stand un-contested: with_lessons 58% (15/26)
  vs baseline 41% (49/120) navigator agreement — small n, directional.
- The readout's first live run itself surfaced two system facts:
  evidence-free retries 0/1253 (Phase 62 convergence detection works)
  and PLAYBOOK_CURATED's emit-on-change-only contract (quiet passes
  invisible — now a stated caveat).
