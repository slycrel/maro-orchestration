---
status: record
---

# Chunk 5b Adversarial Review — Evidence-Diverse Lenses (2026-07-22)

Per-chunk review discipline (Jeremy's /goal, 2026-07-21): every swarm-review
chunk gets a post-land adversarial review + fix commit before the next opens.

- **Reviewed**: commit f49666b (chunk 5b — persona_dispatch owner,
  evidence-path council lenses + ladder, lens_ablation harness, cross-ref
  hosted-free research lane; 17 files, ~1900 insertions)
- **Reviewers**: 3 Codex lenses (Skeptic / Architect / Minimalist), opposite
  model per skill rule, spawned as parallel `codex exec` with lens text +
  mapped brain principles + full diff + verify-against-tree instruction
- **Verdict: CONTESTED** — two High findings, no cross-reviewer High
  consensus (probe-bypass rated High by Skeptic / Medium by Minimalist;
  shell-guard High raised by Architect alone)
- **Verification**: 7/7 findings' code claims checked against the tree —
  all accurate, 0 hallucinated (**sixth consecutive clean round**)

## Findings & dispositions

1. **[High/Arch] Probe commands cross the trust boundary unvalidated** —
   `claim_probe` runs reviewer-authored `settled_by_command` with
   `shell=True`; "read-only" was prompt text, not enforcement. Pre-existing
   exposure (Pass-2 contestations, verification_agent), but 5b widened it:
   a council seat whose *job* is authoring probes, runnable on weaker
   hosted-free models, judging content that can include fetched web text
   (real prompt-injection chain). **ACCEPTED — fixed at root**:
   `probe_command_rejected()` mechanical guard in claim_probe — shlex-parsed
   with operator tokenization, allowlisted head commands only (no python/
   sed/awk/bash/xargs/echo), git restricted to read subcommands, find/curl
   stripped of mutating flags, no substitution/redirect/chaining (single
   pipes between allowlisted commands OK — the prompt's own examples use
   one). Blocked → `probe_status="blocked"`: concern STANDS (unrunnable
   neutrality — the guard can degrade calibration data, never dismiss a
   claim). CLAIM_PROBED still emitted for blocked probes. Pinned in new
   tests/test_claim_probe.py (allow/block table + never-executes +
   neutrality + event).

2. **[High-Skeptic/Med-Minimalist] Probe-armed string concerns bypass probes
   but keep WEAK** — the tolerance path appended plain-string concerns
   unprobed and untagged, indistinguishable from probe-survived ones.
   **ACCEPTED IN PART**: strings now tagged `[probe:unprobed]` + logged, so
   downstream consumers and the readout see the seat degraded to costume
   mode. **REJECTED the implied downgrade** of the seat's WEAK: unprobed
   concerns stand per claim_probe conservatism — only a probe that RAN and
   exited 0 dismisses; formatting non-compliance must not silence a
   possibly-real concern. Pinned (test_string_concerns_tagged_unprobed).

3. **[Med, unanimous 3/3] Council event drops the free round's per-seat
   evidence when paid confirmation runs** — `seats` serialized only the
   acting round; the free round collapsed to a `free_round_weak` count,
   destroying lens/verdict/source/finding_codes/probe_dismissed exactly
   where the A/B readout needs them. **ACCEPTED**: event now carries
   `free_seats` (same row shape) whenever a confirmation ran. Pinned.

4. **[Med/Arch] Empty paid confirmation logged as confirmation** — paid
   round returning zero parsable votes left `confirmation_ran=True`,
   summary "confirmed_by_paid", free seats acting silently. **ACCEPTED**:
   empty confirmation now sets `free_flag_unconfirmed=True`; summary says
   "confirmed_by_paid" only when the paid round actually voted — "paid
   disagreed" and "paid failed to vote" never conflate. Pinned.

5. **[Med 3/3] Cross-ref zero-claim runs emitted no event** — the lane
   could run, extract nothing, and vanish from the captain's log, biasing
   the readout toward productive calls. **ACCEPTED**: event emits whenever
   the lane ran; zero-claim rows are denominator data. Pinned.

6. **[Low/Skeptic] `dispatch_prompt(system=None)` raises AttributeError**
   despite the never-raises contract. **ACCEPTED**: normalized at entry.
   Pinned.

7. **[Low/Arch] artifact_only seat "not final-deliverable-ONLY" (sees the
   goal)** — **REJECTED**: the shipped lens contract already states "You
   see ONLY the goal and the final deliverable"; goal-visibility is
   explicit and deliberate (the seat judges goal-satisfaction, blind to
   *process*). The mismatch was the review prompt's intent-summary
   compression, not the tree.

## Collateral

- factory_thin renders a `blocked` probe suffix; CLAIM_PROBED /
  QUALITY_GATE_COUNCIL contract rows updated (probe_status vocabulary,
  free_seats field); arch-quality skill notes the guard.
- Pre-existing tests using non-allowlisted probe commands (`true`/`false`/
  `does-not-matter`/`sleep`) updated to allowlisted equivalents.

## What went well (per reviewers)

Ladder semantics (free-first, paid-confirmation, degraded-free fallback)
survived all three lenses intact; no reviewer contested the
weaker-never-overrules design or the persona_dispatch adapter policy; the
hermetic-test seam (autouse hosted-free off) drew no findings.
