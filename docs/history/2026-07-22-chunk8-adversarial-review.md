---
status: record
---

# Chunk-8 Adversarial Review — 2026-07-22

Per-chunk review discipline (Jeremy's /goal). Reviewed: commit `a6cabd7`
(chunk 8 — the enforcement pin: DEFAULTS.md reverse census). Final review
of the swarm-review arc. Reviewers: 2 Codex lenses (`codex exec`, opposite
model — Medium change), parallel, read-only against the live tree.

reviewer_cli=codex; both output files non-empty (skeptic8.md,
architect8.md).

## Intent

`test_every_documented_key_has_a_reader`: every dotted key documented in a
DEFAULTS.md table row must have a reader in src/ — rot fails the suite.
Read-detection mechanical (AST census / whole-key literal / f-string
prefix + suffix literal), zero hand-maintained exemptions; the sketched
"DEFAULTS.md column" deliberately subtracted as a rot list.

## Verdict: REJECT (as reviewed) → remediated same session

One high with full cross-lens consensus. **2 distinct findings after
dedup, both verified real against the tree, 0 hallucinated — ninth
consecutive clean round.** Both fixed and mutation-proven before this
record.

## Findings

1. **[high, BOTH LENSES] Multi-key table cells escaped the census**
   (Skeptic + Architect; prove-it-works / boundary-discipline). The key
   extraction regex `^\| \`...\`` captured only the row-leading key, so
   the seven keys documented as siblings in a shared cell —
   `recall.guard_window_minutes`, `captains_log.rotate_keep`,
   `navigator.shadow_blocked_step`, `recursion.checkin_jitter_max`,
   `telegram.chat_ids`, `validate.output_provenance`,
   `validate.result_provenance` — never entered the reverse census at
   all (89 captured vs 96 actual; the Skeptic's exact count verified).
   Deleting `recall.guard_window_minutes`'s reader
   (handle_queue.py:130) would not have failed the new test, directly
   violating the pin's stated contract. **Fixed**: the parser takes ALL
   dotted backticked keys from each row's key cell. All 96 keys resolve
   against live readers; mutation-proven in the failure direction (a
   phantom second-position key injected into an existing cell fails the
   suite naming it — the review's exact escape route).

2. **[low] The census scanned `src/*.py`, not `src/**`** (Skeptic;
   prove-it-works). The repo has a nested package (`src/maro_assets/`);
   a config read moving into any nested module would ship undocumented
   (forward lane) or read as dead (reverse lane). **Fixed**: both lanes
   rglob. Collateral find while fixing: the literal-scan dict was keyed
   by basename, which under rglob silently drops duplicate-basename
   files (`__init__.py`) — now keyed by relative path.

## Lead Judgment

- Accept 1: unanimous, verified to the digit, and it broke the pin's own
  contract sentence. The enforcement pin's first adversarial test was
  its own review — appropriate ending for the arc.
- Accept 2: the claim "reader in src/" must mean the tree, not one
  directory level; cheap fix, applied to both census directions so the
  lanes can't drift apart.

## What Went Well

- Neither reviewer faulted the core design: the three-shape mechanical
  read-detection (AST / literal / f-string prefix), the zero-exemption
  stance, the column subtraction, or the BACKLOG'd stores/guards
  deferral with named prerequisites.
- The mutation-test discipline transferred: the original commit proved
  the tripwire fires on a phantom row; the remediation proved it fires
  on the newly-covered second-position shape too.
- Ninth consecutive review round with zero hallucinated findings —
  every reviewer code claim across the whole arc verified true.
