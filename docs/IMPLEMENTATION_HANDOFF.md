---
status: living
---

# Implementation handoff — working this repo on a smaller model

Written 2026-07-12 at the Fable→Sonnet/Opus transition (top-tier model
access ended; implementation continues on Max-plan models). This doc is
deliberately thin: it says what CHANGES when the implementing model is
weaker. Everything else — session checklist, arch skills, coding posture,
end-of-chunk discipline — is in CLAUDE.md and unchanged; read that first.

## The deal that was struck

The 2026-07-12 handoff session front-loaded the *design judgment*: the
open meta-edges each got either a full design pass, a sized queue vehicle,
or a recorded Jeremy decision. What remains is execution-shaped. **If a
task in the queue seems to require inventing architecture, stop — that's a
sign you've drifted off the design doc, not a gap to fill creatively.**
Re-read the design; escalate to Jeremy if it's genuinely undesigned.

## Chunk discipline

- **One sized chunk per session.** The design docs name their chunks (C1–C4,
  A/B1–B3, V0–V5, portable-learning chunks 1–4) with acceptance criteria.
  Don't merge chunks to "make progress"; don't start a second chunk after
  finishing one unless it's trivially small.
- **The design doc is the spec; the code is the truth.** Design docs carry
  file:line references from commit ffff3f6 — they WILL drift. Verify each
  seam against the current tree before editing (docs-are-best-guess
  invariant). If the design's assumption no longer holds, update the design
  doc in the same commit — don't silently adapt.
- **Verify before fix, always** (standing rule; the Purgatorio audits found
  30–50% hallucination base rates in unverified findings). Reproduce the
  problem / probe the claim before writing the fix. Two of the four
  self-speedup proposals had false premises — that's the caution rate on
  plausible-sounding analysis.

## Model-tier guidance for queued work

**Sonnet-safe** (mechanical, spec'd, tripwired): container C1/C3; routing
Part A; probe-synthesis B1/B2; verify→learn V1/V3; portable-learning chunks
1–4; escalation file surface; depth-cap unification; supervision-convergence
docs chunk; time-blindness first slice.

**Wants Opus** (judgment-adjacent, touches verdict/spend/seam semantics):
container C2 (the `_run_subprocess_safe` wrap — it runs everything);
probe-synthesis B3 (closure confidence semantics); verify→learn V2
(auto-revert authority) and V5; anything touching closure verdicts, budget
gates, or the learning stores' trust semantics.

**Stays Jeremy's** (recorded, do not start): git-history privacy review;
PyPI publish + tag; model-route exploration (#24, his session); container
flip decision (C4 adjudication); escalation-design revisions; any cutover
enablement (navigator close stays organic-blocked, reconfirmed 2026-07-12).

## Standing constraints that bite harder on smaller models

All decreed; GOAL_BRAIN Decisions has the full text:

1. **Structural boundaries over prose scope** (2026-07-03) — if a worker/
   agent keeps escaping a prose instruction, the fix is a harness boundary
   (fence, gate, schema), never a longer prompt.
2. **Never trust self-reported completion** — verify against artifacts.
   This applies to YOUR OWN work too: run the thing, read the file back,
   check the test actually fails without the fix.
3. **Fork-verification protocol** (2026-07-02/03) — claims from parallel/
   delegated work get independently verified before landing.
4. **Separate "should we" from "how would we"** — implementation sessions
   implement; if a should-we question surfaces, record it (BACKLOG +
   GOAL_BRAIN line per SF-13) instead of deciding it inline.
5. **Retention decree** — never add a deletion path;
   `tests/test_no_silent_deletion.py` will catch you, but don't make it.
6. **DEFAULTS census** — every new config key needs a DEFAULTS.md row with
   reasoning (`tests/test_defaults_doc.py` enforces).

## When stuck

Escalate honestly and cheaply: state what was tried, what failed, what the
design doc assumed vs what the tree shows. A blocked chunk with an honest
note beats a creatively unblocked one. (This is the same contract Maro's own
workers are held to — MISSING_INPUT escalation over fabrication.)
