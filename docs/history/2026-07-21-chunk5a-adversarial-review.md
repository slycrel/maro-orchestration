---
status: record
---

# Chunk-5a Adversarial Review — gate second-family check (441f4cf)

Per-chunk review discipline (Jeremy's /goal 2026-07-21). Three Codex lenses
(`codex exec`, opposite-model rule) against the chunk-5a diff
(adf0574..441f4cf), prompts = stated intent + lens + mapped brain principles
+ full diff + verify-against-tree instruction. Review ran 2026-07-21 late;
remediation landed 2026-07-22. Raw reviewer output preserved at session
scratchpad `adv-review-c5a.9XUKdr/` (ephemeral; this record is the durable
summary).

## Intent

Give the post-loop quality gate a decorrelated free rung that STACKS rather
than substitutes: on a paid Pass-1 PASS, exactly one hosted-free
second-family call judges the same payload; the agreement outcome is
recorded (QUALITY_GATE_SECOND_FAMILY event + `QualityVerdict.second_family`)
and never acted on. Authority comes from A/B agreement data (chunk-7
readout) or not at all.

## Verdict: PASS — findings fixed same session

First PASS of the arc (chunks 1–4 ran CONTESTED/REJECT). No high-severity
findings; top severity medium, with two-lens agreement on one issue. All
four deduped findings verified against the tree before acting: **4/4 real,
0 hallucinated** — fifth consecutive clean round (historical reviewer
hallucination rate 30–78%). Notably, no reviewer contested the core
flag-only design.

## Findings (deduped, severity order)

1. **[medium — Skeptic+Architect+Minimalist, unanimous]** Quoted-string
   killswitch bypass: `config.get` returns raw YAML nodes, so
   `quality_gate.second_family_check: "false"` (quoted) arrived as a truthy
   string and the check still ran — violating the stated inertness
   invariant. `hosted_free_enabled()` normalizes exactly this case; the new
   read didn't. Principle: boundary-discipline. **Fixed**: same
   normalization at the read site ("false"/"0"/"no"/"off" → False), pinned
   (`test_config_quoted_false_string_disables`).
2. **[low → systemic — Architect]** `QUALITY_GATE_SECOND_FAMILY` missing
   from `docs/CAPTAINS_LOG_EVENTS.md`, which claims to be the full event
   contract. Verification generalized the finding: **13 event types** had
   shipped without rows (drift since the 2026-06-24 sweep — spanning
   chunks 2/4/5a but also CUTS_DRAWN, FENCE_*, NAVIGATOR_ADJUDICATED,
   VALIDATION_LADDER, EVOLVER_VERDICT, WORKER_SLICE_INJECTED,
   STEP_CEILING_ENFORCED, BOUNDARY_EXPANDED). **Fixed structurally**: all
   13 rows backfilled (each verified against its emit site's actual context
   dict) + a census tripwire
   (`test_event_contract_doc_covers_all_types`) so the doc can never
   silently drift again — the DEFAULTS.md census precedent applied to the
   event contract.
3. **[low — Minimalist]** Positive-path tests read real box config for the
   new key — a box with the killswitch off would have failed (or silently
   skip-tested) the ON-path tests. Principle: cost-aware-delegation /
   hermeticity. **Fixed**: `_hosted()` helper now forces the killswitch ON
   via a targeted `config.get` wrapper; the OFF/quoted-false tests use the
   same `_force_config` seam.
4. **[low — Architect]** `second_family` is an untyped dict where the gate
   already has typed verdict objects (`CouncilVerdict`). **Rejected**: the
   durable contract is the captain's-log event row (documented in the event
   contract + DEFAULTS), not the in-process field; the only current
   consumers are tests; `contested_claims: List[dict]` is equal-standing
   precedent. Consumer-first says the dataclass earns its keep when the
   chunk-7 readout (which reads events, not this field) or a real in-process
   consumer arrives.

## What Went Well

- No reviewer contested the stack-don't-substitute shape, the
  flag-only invariant's implementation, the UNDECIDED weak-judge mapping,
  the NO_VERDICT denominator honesty, or the placement after Pass 1.
- Minimalist explicitly confirmed the core behavior matched the stated
  invariants before raising its findings.
- The consent split (hosted_free egress gate vs. per-feature killswitch)
  was read as correct boundary layering by the lens whose job is to attack
  boundaries.

## Lead Judgment

- F1 accept — unanimous and mechanically verified; the killswitch that
  can't kill is the exact class DEFAULTS.md exists to prevent. The fix
  copies the neighboring normalization rather than inventing a helper.
- F2 accept and widen — the reviewer found one missing row; verification
  found thirteen. The census tripwire is the real fix; the backfill is just
  paying the accumulated debt. This converts a low doc nit into the chunk's
  most valuable outcome.
- F3 accept — my own tests violated the hermeticity rule this repo already
  learned (MARO_USER_DIR isolation, 2026-07-01).
- F4 reject with rationale recorded above — typed-object symmetry is real
  but consumer-first wins until something in-process actually reads the
  field.

Affected suites green post-fix; full suite via `scripts/test-safe.sh` for
the land.
