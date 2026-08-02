---
status: record
---

# Probe-timing record dig (2026-08-02)

Jeremy, adjudicating RUN_TEACHINGS_DESIGN §4a/§4d, recalled an earlier
decision that checks are planned as steps rather than machinery-executed
("I didn't write those directly, so the best source would be digging into
the session jsonl to verify"). A search agent swept the 37 session
transcripts on this box, the 756 runtime transcripts, GOAL_BRAIN,
BACKLOG(+DONE), docs/, and the FTS index. Every load-bearing citation
below was independently re-verified against the source before recording.

## Finding 1 — his recollection is exact, dated 2026-07-27

The §14 "diagnosis at the failure boundary" exchange (session `438e91bd`,
2026-07-27). Jeremy, verbatim:

> "I'm not sure at first glance if the step should run the mini-test or
> if it should recommend that as a sidequest; letting the planner reroute"

Outcome, recorded same day (GOAL_BRAIN 2026-07-27, decision `7061e85e`;
COMPOUND_THINKING_DESIGN §14): **diagnosis ownership split — the step
diagnoses and recommends; the planner decides and routes.** The step exits
`blocked_on(cause, what-would-be-different, proposed-experiment)` as an
observation. Two structural reasons the step can't own the experiment:
dedup only exists at the map (N blocked steps → ONE side-quest), and the
planner may route away regardless.

**The carve-out** (the exception Jeremy half-remembered as "proving the
existing data rather than gathering as the step work"): a step may
*substantiate* its hypothesis with evidence gatherable **inside its
already-granted scope and budget** — the `which <tool>` probe class.
"That's not routing, it's making the recommendation falsifiable."

## Finding 2 — the one sanctioned machinery execution is claim-settling

`claim_probe.py` / `settled_by_command`: the **review layer** runs a
contesting reviewer's read-only, <15s, single-line probe to settle an
existing claim. This is the exception shape mechanized — the claim already
exists with evidence; the probe proves or dismisses it, never gathers.
Reinforced 2026-08-01: the recon-flavor runtime slice deliberately cut
"no probe execution at verify time" (COMPOUND_THINKING_DESIGN:614).

## Finding 3 — §4a/§4d have no prior decision behind them

No transcript or doc on this box contains any prior discussion of
teaching-freshness probes. Both DECISIONs originate, provisional, with the
2026-08-01 UU-2 authoring session (whose own transcript isn't on this
box). They are proposals awaiting Jeremy — nothing was being ratified.

## Finding 4 — "bridge building sub-goal arc" identified

The capability-acquisition side-quest thread. Phase 27 "Prerequisite
Knowledge Sub-Goals" (March, the kanji→learn-Japanese scenario) → the
chasm/balloon exchange (2026-07-22, compound-thinking: "build a hot air
balloon to fly over the chasm… level up our tech tree") → ratified
2026-07-27, Jeremy verbatim: "building a toolset to use to cross the
chasm… literally learning a language to cross the chasm" — "the run-scale
twin of the lesson loop." His scientific-method question in that same
message produced §14 minutes later: **the bridge arc and probes-as-steps
are one exchange.** The side-quest runtime seam remains deliberately
unbuilt (§13d design-only; per-step-learning and recon slices both cut it
pending the seam).

## Adjudication material for §4a/§4d

- **§4a's injection-time re-probe is supported by analogy, not by a
  recorded decision**: a stored terrain fact is an existing claim with
  evidence; re-probing before the planner relies on it is the claim_probe
  shape (proving, not gathering). Consistency requirement from §14: on
  probe FAILURE the machinery may only flip the teaching to grey and
  record the contradiction — any route-around or bridge-building stays a
  planner move. Machinery never self-serves recovery.
- **§4d exists to break a real deadlock the draft never names**:
  provisional teachings are excluded from injection, and §4a runs probes
  only at injection — so a terrain teaching could never confirm. Two
  coherent resolutions:
  1. **Mint-time probe** (§4d as drafted) — extraction verifies its own
     minted claim before granting confirmed status. claim_probe-analog at
     the extraction layer; contradicts §4a's "never auto-executed at
     mint" as written.
  2. **Probe-gated first injection** — provisional terrain teachings MAY
     be offered for injection iff their probe passes at that moment; the
     pass IS the confirmation event. Preserves "never execute at mint,"
     runs the probe at the freshest possible moment (when the fact is
     about to be relied on), and matches §13b's
     reopen-condition-on-cached-observation semantics.

  **Recommendation: (2).** It resolves the §4a/§4d contradiction
  structurally instead of by exception, and the probe fires exactly when
  its answer matters. Jeremy's call.
