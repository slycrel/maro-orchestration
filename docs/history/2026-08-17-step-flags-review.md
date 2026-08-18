---
status: record
---

# Adversarial review — step-flags torn-store chunk (2026-08-17 → 08-18)

Round on the landed range `2d7a6014..4700fcc6`. Sonnet-medium fallback
lane, three seats (Skeptic / Architect / Expert QA), serialized. Lane
incident worth recording: nested `claude -p` calls launched from
*background* shell tasks died twice with a bare "Execution error" and
the wrapper tasks were killed; the identical invocations succeeded run
in the foreground. Box was healthy both times (load < 1, 13 GB free).
Review seats on this box run foreground until that's understood.

## Verdict: Skeptic PASS, QA PASS, Architect REJECT → HIGH verified, fixed, re-swept

### The Architect HIGH (verified real, fixed same session)

`refresh_run_card_classification._merge` — three functions up from the
chunk's fix, in the same file — carried the identical destructive
shape: `json.loads(old)` → `except: card = {}` →
`card.update(deepcopy(rebuilt))`, writing back a card containing only
the rebuilt pure-curation fields. A torn `run_card.json` lost every
maintenance-owned key AND its own bytes (the only copy) in one write.
Live-reachable from `audit_repair._refresh_surfaces`, the
verdict-orphan sweep, and two `handle.py` drain paths. The existing
`test_classification_refresh_rebuilds_corrupt_card` started from a
contentless corrupt card, so it proved the rebuild and could never see
the loss. The charter for this seat was precisely the sibling sweep,
and the finding is the sweep working: the chunk fixed the defect where
the probe found it and did not look one function up.

**Fix — preserve-then-rebuild, not refuse:** unlike `refresh_step_flags`
(purely additive; refusing costs nothing), classification refresh is
how a card self-heals after audit repair — a pinned 2026-08-13
contract with callers that depend on `None`-vs-card. So the honest
shape is different here: the unreadable original is sidecarred
(`run_card.json.unreadable-<utc>`), the loss is WARNed with the
sidecar named, `loads_clean` refuses the tainted-but-valid launder
shape, and the rebuild proceeds. Three pins (corrupt path sidecar +
warning; tainted-valid sidecar + taint-free rewrite; maintenance keys
survive a healthy merge — the contract the destructive path violated,
previously untested). Spec 8 → 11, all DETECTED.

### Accepted MEDIUM — Architect: two more strict run_card readers

`shadow_lane.py` (cost-comparison read) and `camera_readout.py`
(per-run card in the frames walk) still strict-read `run_card.json`
under broad excepts — family-A silent-skip, read-only, nothing
destroyed. Both modules are on the census under other functions; these
sites weren't counted by the scanner. Not fixed here (chunk
discipline); camera_readout was already a named remaining tier-3
surface and **shadow_lane's cost read joins the list** (BACKLOG
updated).

### The clean seats

Skeptic traced both `_merge` failure paths end-to-end (surrogateescape
round-trip → byte-identical preserve confirmed), verified all 8
original anchors match exactly once (including the two `if skip:`
depth-siblings), and confirmed the census removal is structurally
justified. QA confirmed every new pin actually reaches `_merge`
(probe non-empty in all four), the byte-identity assertions aren't
satisfiable by reformatting, the healthy-slice "absent = nothing
fired" contract is still pinned, and the filter mutant is killed
non-vacuously by the fixture's SCAVENGE_DETECTED bystander. Skeptic's
one LOW confirmed the record's own disclosure (slice_loss reaches no
HTML surface yet) rather than finding anything new.

## Health signal

The r1→r2 chain is doing exactly what the sibling-sweep charter says:
r1 (loop_report) named this chunk; this chunk's r2 named the
classification-refresh sibling; each finding was one seam over from a
fix, not a re-derivation of tested claims. The recurring lesson across
all three: **fixing the probed site is half the chunk — the sweep for
its siblings is the other half**, and reviews keep finding the half
that was skipped.
