# Purgatorio r2 reconciliation — FINAL (re-run vs 2026-07-09 baseline)

**Status:** FINAL, 2026-07-10. Decreed by Jeremy: "run the purgatorio suite
again and compare the results to the previous run" before 1.0. Same seven
eyes, delta-scoped: HEAD 97aa5ef, auditing 4e6dc1b..HEAD (21 commits, 71
files — the post-Purgatorio fix wave) plus re-verification of every one of
r1's 82 findings by live probe.

**Method:** 7 eye agents (each re-verified its prior findings and swept the
delta) + 42 independent adversarial verifiers (every new finding, plus every
claimed resolution of a blocker-member finding, verified by an agent
prompted to refute it). **41/42 verifications confirmed, 1 refuted** —
against the historical 30-50% hallucination base rate, this run's finding
quality was unusually high, and the one refutation produced the run's best
catch (see ops-r2-05 below). External web sweep deliberately NOT redone
(1 day old); eye 6 ran repo-side only.

---

## Headline: run-over-run scorecard

| | r1 (2026-07-09) | r2 (2026-07-10) |
|---|---|---|
| Findings | 82 new | 82 re-verified + **23 new** (22 confirmed, 1 refuted→replaced) |
| Clean checks | 52 | 63 |
| 1.0 blockers | 9 | **6** (4 of r1's 9 fully cleared, 3 narrowed, 2 new) |

**Of r1's 82 findings: 34 resolved, 17 partially-resolved, 30 still-open,
1 obsolete.** The 30 still-open are dominated by items already adjudicated
as deferred/Jeremy-gated in the 07-09 decision batches (knowledge-web
descope, satellites of SF-6, dead lanes) — the fix wave hit what it aimed
at. **No regressions found in any prior finding.**

The record held up: every decision-bearing commit in the fix wave has a
matching GOAL_BRAIN entry, and the SF-13 session-close rule demonstrably
worked on its first live test (retention decree). GOAL_BRAIN's four quoted
decrees again verified word-for-word.

---

## The 9 r1 blockers, re-verified

| # | r1 blocker | r2 verdict (all independently adversarially verified) |
|---|---|---|
| 1 | Self-improvement zero production hours + broken supervision (SF-1) | **OPEN, narrowed.** First-ever real heartbeat beats exist (2 burn-in beats 07-10, tier-2 diagnosis + Telegram send proven; a9824ce then took zombie targets 183→0). Evolver run-cadence trigger shipped + counter proven incrementing (1/10) — but the evolver meta-cycle still has **zero production runs** (fires at 10th finalization). Supervision *decided* (no-daemons shim, bootstrap prints instructions) but the repo now tells **three contradictory supervision stories** (ops-r2-04, docs-r2-01/04). |
| 2 | Verdict-blind learning stores (SF-2) | **OPEN, narrowed.** d6c143b plumbed tri-state `goal_achieved` into outcomes.jsonl; exactly one production run finalized since, and it worked. Residual is real: agenda-lane lesson extraction and skill crystallization run at finalize **before** closure judges, and the retro-stamp reaches only outcomes — lessons/skills are still extracted verdict-blind (data-r2-01). |
| 3 | `user/` personal data shipped + injected (SF-5) | **RESOLVED at tip** (verified: 6 neutral templates, zero personal-data hits at tip, overlay-first resolution proven in code at all 6 call sites, lane documented in DEFAULTS/README). Residual is the **git-history review — Jeremy's explicitly deferred personal call (07-09), still required before any public tag.** |
| 4 | Security misrepresentation + no-sandbox live path (SF-6) | **DOC HALF RESOLVED** (SECURITY_MODEL.md verified honest: "no sandbox on the live executor path", sandbox.py labelled unwired, POE_ENV_FILE ghost gone). Code half unchanged by design — container direction *decided* for 1.0, but the design pass has **no vehicle anywhere in the queue** (arch-r2-01, new blocker). Satellites cs-02/03/05/06/07 unchanged, as adjudicated. |
| 5 | Test junk in learning paths (SF-3) | **RESOLVED, holding** (live probe: 4 organic lessons, 0 junk, backup intact per retention decree). But r2 found a **new leak of the same class** — see ops-r2-05. |
| 6 | Bootstrap heartbeat unit crash-loops (in SF-1) | **RESOLVED as filed** — d6c143b deleted the unit generator entirely; `sheriff.py --heartbeat` exec is gone. **Superseded by docs-r2-01:** README still tells strangers to `sudo cp` unit files from a directory nothing creates; 2 of the 3 named units exist nowhere in the repo. |
| 7 | Not installable by name (land-01) | **NARROWED to the publish act.** `maro-orchestration` verified FREE on PyPI (checklist ticked 2026-07-10, live 404 re-probed); version scheme decided (1.0.0 at tag, batch #2). pyproject correctly still 0.5.0. Publish remains Jeremy's act at tag time. |
| 8 | CI empty directory (land-02) | **MOSTLY RESOLVED.** ci.yml is a real enforced gate (push-to-main + PR, installs hooks, full pytest) and the latest run is green (29067595461, verified via gh). Residuals: no README badge, single py-version matrix. Fork-PR surface probed safe (no pull_request_target, no secrets). |
| 9 | README self-improvement headline (land-10) | **RESOLVED** (verified at HEAD: "every 10 minutes"/"meta-evolver" claims gone; self-improvement staged as "designed, not production-proven... has never fired in production"; accountability layer now the lead). |

---

## New findings (r2) — 22 confirmed + 1 replacement

**New blockers (2):**

- **arch-r2-01 — the containerized-executor decision has no vehicle.**
  Batch #3's biggest 1.0 decision ("dockerized executor... gets its own
  pass") is recorded in GOAL_BRAIN and SECURITY_MODEL.md §2 as DESIGN
  PENDING — and appears in no MILESTONES arc, no BACKLOG item, no thread.
  The exact decision-without-vehicle failure mode Purgatorio r1 catalogued
  (arch-04/05 class), minted the same night those were fixed.
- **docs-r2-01 — README "Optional Services" is broken end-to-end** for a
  stranger: instructs copying `maro-{heartbeat,telegram,inspector}.service`
  from `~/.maro/workspace/deploy/systemd/`, a directory nothing creates
  anymore (`config.deploy_dir()` has zero callers post-d6c143b); two of the
  three units exist nowhere. Cheap fix: rewrite the section to the printed
  hook-instructions posture.

**Real-but-deferrable (7):** ops-r2-01 (daily-red now structural:
host-check reds at >900s heartbeat age while the decree forbids a recurring
heartbeat; cron→host-check→Telegram leg still never fired), ops-r2-02
(`heartbeat.autonomy: true` left set in workspace config after burn-in — a
left-on switch), data-r2-01 (pre-verdict lesson/skill extraction — SF-2's
store-side residual), data-r2-02 (promotion funnel still empirically zero
on every channel; skills-lite has zero live firings — needs a live batch),
cs-r2-01 (**skills-lite promoter skips injection_guard** — the new
self-modification lane injects worker-authored .md into all future planning
prompts gated only by a Python-code substring blocklist, contradicting
SECURITY_MODEL.md:47; its two sibling self-mod lanes both scan-and-discard),
docs-r2-02 (user/CONFIG.md lane documentation over-claims 4 dead keys),
hist-r2-02 (hist-05, the "run this prompt with this persona" owner ask,
fell through the crack between disposition channels — in neither the brief
nor the backlog; third drop of the same ask).

**Cosmetic (13):** ops-r2-04 (three contradictory supervision stories
shipped simultaneously), docs-r2-03/04/05/06 (CONFIG.md template
contradicts its own fix; stale deploy/ units; "alert Jeremy" in
stranger-facing README vs the checklist's own gate; PUBLISH_CHECKLIST
absent from INDEX.md — also hist-r2-04), land-r2-01 (safety-first
positioning never states the trusted-operator boundary in README),
land-r2-02 (pymaro/Microsoft-MARO disambiguation line the checklist itself
asks for), hist-r2-01 (GOAL_BRAIN compiled-truth Hermes stance contradicts
its own 07-09 Decisions entry), hist-r2-03 (the SF-13 standing rule lives
only in a disposable audit doc + auto-memory — not in any living doc),
hist-r2-05 (brief bucket D4 lessons.jsonl name-collision confirmed live,
recorded nowhere outside the brief), data-r2-03 (atomic rewrites silently
narrow ledger perms to 0600).

**The refutation — ops-r2-03 replaced by ops-r2-05.** The eye read a stale
`run/heartbeat.pid` (dead PID, started 06:12Z) as an invisible pre-beat
heartbeat death. The adversarial verifier disproved the story by transcript
bracketing and then **live-reproduced the real mechanism**:
`tests/test_heartbeat.py::test_heartbeat_loop_none_autonomy_uses_config`
stubs `sys.modules["config"]`, which breaks `proc_lock._run_dir()`'s
import and falls back to `Path.home()/.maro/workspace/run` — bypassing
conftest's `MARO_WORKSPACE` isolation. **Every full-suite run stamps the
REAL workspace's heartbeat.pid from tests.** Recorded as:

- **ops-r2-05 (real-but-deferrable, verified by live repro):** test-isolation
  leak writes to the production workspace — the ghost-index/SF-3 class,
  post-isolation. Fix candidates named: monkeypatch the real config module's
  `get` instead of replacing `sys.modules["config"]`; make
  `proc_lock._run_dir` read `MARO_WORKSPACE` from os.environ before the
  home fallback. (tests/test_heartbeat.py ~:352-367, src/proc_lock.py:32-37.)

---

## r2 merged themes (what the new findings triangulate)

1. **The supervision story is decided but not converged** (docs-r2-01 +
   docs-r2-04 + ops-r2-04 + ops-r2-01/02 + docs-03 residue). One
   cleanup chunk: rewrite README Optional Services to the hook posture,
   delete/rewrite deploy/systemd/ + heartbeat-ctl.sh, reset
   `heartbeat.autonomy` on this box, align host-check's threshold with the
   one-shot-ticks decree. All cheap; all the same item.
2. **The learning engine is wired but still has ~zero verified production
   behavior** (ops-02 + data-r2-01 + data-r2-02 + cs-r2-01). The cadence
   counter works, skills-lite is default-ON but has never fired, extraction
   still runs pre-verdict, and the new self-mod lane skipped the
   injection_guard its siblings use. A deliberate live batch (~10
   finalizations) would exercise evolver-at-cadence, skills-lite promotion,
   and verdict-aware extraction in one shot — with cs-r2-01 fixed first.
3. **The record is healthy; its edges fray at non-work sessions and
   disposable docs** (hist-r2-01/02/03, arch-r2-02). SF-13's rule worked
   live but isn't itself recorded in a living doc; one owner-ask (hist-05)
   has now been dropped three times.

---

## FINAL r2 1.0-blocker list

| # | blocker | carried from | state |
|---|---|---|---|
| 1 | Evolver/self-improvement production hours still zero | r1 #1 (SF-1) | open — burn-in underway; fires at 10th finalization; pair with theme-2 live batch |
| 2 | Pre-verdict lesson/skill extraction (verdict-aware learning incomplete store-side) | r1 #2 (SF-2) | open — data-r2-01; extraction must move post-closure or re-stamp |
| 3 | `user/` git-history personal-data review | r1 #3 (SF-5) | **Jeremy-gated** — his explicit 07-09 deferral; required before public tag |
| 4 | Containerized-executor design pass has no vehicle | r1 #4 (SF-6) + arch-r2-01 | open — needs a MILESTONES/BACKLOG entry at minimum |
| 5 | README Optional Services broken end-to-end (+ supervision-story convergence) | new (docs-r2-01, theme 1) | open — cheap, one chunk |
| 6 | PyPI publish act + tag (name verified free, scheme decided) | r1 #7/#8 (SF-12) | **Jeremy's act** at tag time; CI green, badge cosmetic |

Cleared outright since r1: test-junk purge (holding), bootstrap crash-loop
unit (generator deleted), README headline (repositioned), security-doc
misrepresentation (honest rewrite verified), user/ tip privacy (overlay
complete). **r1's gut-check finding stands inverted: yesterday the audit
found work "littered behind us"; today's re-run confirms the fix wave
cleared what it aimed at without regressions, and the remaining list is
short, named, and mostly Jeremy-gated.**

---

## Disposition

Same machinery as r1: this directory is disposable scaffolding; findings
graduate through BACKLOG/GOAL_BRAIN. Applied this session: BACKLOG entries
for the two new blockers + cs-r2-01/ops-r2-05/data-r2-01 cluster;
GOAL_BRAIN record of the r2 run + verdict. Cosmetic items ride the
supervision-convergence chunk (theme 1) or the next docs pass.
