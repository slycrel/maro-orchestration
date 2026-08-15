---
status: record
---

# Stale-Test Sweep — 2026-08-15

Jeremy's stretch decree named a fallback lane: *"pick 4 or more tests
that haven't been run, or are at least a few weeks stale, and see what
you can learn."* With the treasure-map solo frontier exhausted, this
sweep ran five verification surfaces that nothing exercises routinely.
Method per the eval decree: run the real thing, judge by evidence,
verify every flag before believing it.

## Surfaces and findings

| # | Surface | Staleness | Result |
|---|---------|-----------|--------|
| 1 | `scripts/smoke.sh` | last touched Aug 6 | PASS (exit 0). Still exercises the deprecated `maro tick` path — harmless, noted. |
| 2 | `scripts/audit-phases.sh` | untouched since **Jul 4** — pre-dates container mode ON | **Broken AND lying.** See below. |
| 3 | dev-recall (`correspondence`) | last ingest **Aug 12** | 3 days stale — missing the entire treasure-map arc. Re-ingested (199 new chunks); retrieval probe now surfaces the arc. |
| 4 | `navigator_shadow --agreement` | rows accrue live; 3 divergences unadjudicated | Adjudicated all 3 → `navigator_right` (cumulative 91 navigator / 12 pipeline). Lesson-inject A/B: 56% agreement with lessons vs 41% baseline. |
| 5 | Coverage floor (`test-cov.sh`) | last full measure **Jul 14** (78.04%) | **80.18%** — floor ratcheted 70 → 75 (72752a1). |

## The audit-phases.sh find (the sweep's centerpiece)

Two compounding failures, both invisible until someone actually ran it:

1. **Container blindness.** The script dispatches a maro run whose goal
   names the live repo, but `executor.container` (ON since 08-13)
   refuses goal-declared rw roots outside the write scope. The worker
   honestly burned 1.5M tokens across 4 steps proving the repo "does
   not exist," then went stuck. The script rotted the day container
   mode flipped on — five weeks unnoticed.
2. **Instrument blindness (taxonomy pattern 7, live).** The script
   exited **0** on the stuck run. Judge-by-exit-code — this box's
   standing rule — would have called it green forever.

Fixes, both proven by re-run: honest exit code (d9ec4e4), and the repo
added to `executor.container_extra_mounts` as a **read-only** mount in
this box's workspace config (operator-trusted ro lane; self-dev writes
still ride the scratch clone; introspection provisioning doesn't cover
this — it deliberately mounts only `src/` + `runs/`). Re-run: 3/3
steps done, full report produced, honest exit.

## The audit's own product

50/55 phases VERIFIED. The 5 flags were all *archive* problems, not
code problems — verified against git history before acting:

- Phase 18 sandbox.py → deliberately retired (C4 containers, 69265f6)
- Phase 51 passes.py → Tier 1 dead-code deletion (a278575)
- Phase 53 poe_self.py → de-default-persona refactor (f2a8f62)
- Phase 26 root Dockerfile → superseded by Dockerfile.executor
- Phase 55 `lat check` → **no trace in git history; aspirational when
  written** (the only claim that was never true)

ROADMAP_ARCHIVE.md now carries inline supersession notes (70677e1) so
the next audit doesn't re-flag settled history.

## Lessons

- **Scripts rot at mode boundaries.** audit-phases.sh worked until the
  environment changed under it (container ON). Anything that dispatches
  runs should be re-run after executor-lane changes; nothing does that
  today. If a second script rots this way, consider a smoke lane for
  `scripts/`.
- **Exit-code honesty is a checkable property.** One script lying about
  failure cost a 1.5M-token blind run. test-cov.sh's `-n auto` footgun
  got the same treatment (throttle knobs, ff48e9f) while in the area.
- **The archive is a claims ledger, and claims age.** The audit's
  BROKEN verdicts were all true-when-written; the missing artifact was
  supersession notes. Same currency rule as CLAUDE.md prose: fix the
  stale claim where it lives, in the same session that proves it stale.
