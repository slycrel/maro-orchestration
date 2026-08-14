---
status: record
---

# Whole-changeset adversarial review — 2026-08-13 (24h stretch)

"Just for fun, run the adversarial review on the entire changeset" (Jeremy).
Range `9772cb7..HEAD` — 35 commits, ~6,400 insertions across 42 files,
spanning six arcs (execution receipts, planner read-verb lever, container
verb parity + auth breaker, executor image r3 + key injection, workspace
archive v2 export/import, learning-layer decay/canon). Three codex CLI
lenses (Skeptic + Architect + Minimalist), each prompted to hunt the
CROSS-CHUNK interactions that per-chunk reviews structurally can't see.

Every arc had already been reviewed individually (most to fixpoint). This
pass's marginal value was the seams BETWEEN arcs reviewed in isolation.

## Verdict: CONTESTED → 5 fixed same session, 4 filed

9 real findings (reviewer hallucination 0 — every code claim verified
before action). The whole-changeset framing paid off: the single most
valuable find (receipts false-clean) is a pure cross-chunk seam, and the
highest-severity one (scrub over-match) was a live regression introduced
hours earlier by a per-chunk review FIX.

### Fixed

1. **[high — FIXED] Output scrub corrupted every container stream response.**
   The r3 review-fix pass (b5754c8) scrubbed the whole injected env map,
   including the consent carrier `MARO_HOSTED_FREE_ENABLED="1"` — so
   `str.replace("1", ...)` blanket-replaced every `1` in captured output,
   turning `{"num_turns":1}` into invalid JSON the moment keys inject.
   A regression my own earlier fix created, live on the box (r3 image
   built, keys injecting). Fix: `_scrub_secret_values` skips the consent
   carrier by name and any value shorter than 8 chars; only real provider
   key values scrub. (llm.py; Minimalist #1.)

2. **[high — FIXED] Archive config redaction leaked secrets two ways.**
   (a) A YAML anchor aliases a secret out from under its credential key
   (`api_key: &s SECRET` / `label: *s`) into a benign key `_is_cred_key`
   never flags — `label: SECRET` shipped in the export. (b) A `!!binary`
   secret under a real credential key is `bytes`, which the str-only leaf
   check skipped — shipped verbatim. Fix: redact str AND bytes under
   credential keys, plus a whole-tree sweep replacing any leaf equal to a
   collected secret value (closes the alias copy). Numeric/bool leaves
   under cred-shaped keys still survive — `max_tokens`, `token_budget` are
   settings, not secrets (pinned). (scripts/maro_export.py; Architect #1.)

3. **[high — FIXED] Untrusted import had no workspace byte budget
   (decompression bomb).** Only `meta/` members had a size cap; workspace
   members — the main payload — had none, so a tiny gzip declaring a
   multi-TB regular file passed the member-count check and inflated until
   the destination disk filled. Fix: per-file (4 GiB) + aggregate (16 GiB)
   caps enforced as members stream in `_scan_and_classify`, raising
   `_ArchiveCapExceeded` before any mutation; `member.size` is the tar
   header's true uncompressed size, so the bomb is rejected pre-extraction.
   (3/3 lens consensus — Skeptic #3, Architect #3, Minimalist #2.)

4. **[high — FIXED] Receipts counted a FAILED capture-backend attempt as
   clean, enabling a false "ZERO executions" refutation.** Pure cross-chunk:
   the receipts auditor (reviewed to fixpoint) assumed every subprocess
   record has parsed `tool_events`; the failed-attempt recorder (runs.py
   UU-1, a different chunk) writes `backend=subprocess, tool_events=[],
   error=...` because the adapter raises BEFORE parsing events. A failed
   attempt that ran `pytest` then a clean retry → the audit rendered
   "RECORD PRESENT, ZERO executions" — a factually false absence claim.
   runs.py already states "consumers treat error-records as attempts, not
   results"; the receipts consumer didn't. Fix: an error-stamped record is
   non-capturing (blind) — degrades to PARTIAL COVERAGE / non-capturing,
   never the clean positive refutation. (execution_receipts.py; Architect
   #4.)

5. **[medium — FIXED] Planner read-verb killswitch ignored string "false".**
   `bool(config_value)` on YAML `planner.read_query_steps: "false"` is
   True → still taught `maro-read`, despite the config-off contract. Fix:
   string-normalize like the sibling `executor.read_query` switch.
   (planner.py; Architect #5.)

### Accepted, not fixed (filed to BACKLOG § whole-changeset residuals)

6. **[medium/high latent] `require` + verb parity enforced only in
   ClaudeSubprocessAdapter** — any other executor adapter (codex) bypasses
   the container decision entirely; `require` (isolation-or-nothing) is
   silently void under `MARO_BACKEND=codex`. Not hit on this box (executor
   is always subprocess). Structural fix (move decision to the executor
   seam) deferred. (Skeptic #1.)
7. **[medium] `--expect-sha256` TOCTOU** — digest and extraction reopen the
   pathname independently; a concurrent swap imports uninspected (but still
   safety-filtered) bytes. (Skeptic #2 / Architect #2.)
8. **[medium] `<3.11.4` unfiltered-fallback symlink-order escape** — dead on
   this box (Python 3.14); real for old install targets. (Skeptic #4.)
9. **[low/medium] Canon door text-identity race + marker false-verify** —
   narrow concurrent operator/GC window. (Architect #6 / Minimalist #3.)
   Plus: **exporter emits hardlinks its importer drops** (Minimalist #4) —
   box has none, portable feature gap.

## What went well

No lens faulted: the no-pip supply-chain stance, keys-never-in-layers,
bare `-e NAME` argv hygiene, the suppression-never-selects-lane invariant,
the fail-closed exception paths, the receipts malformed/clip/partial-
coverage honesty (finding 4 was the ONE seam it missed, from an
adjacent chunk), the archive's path-traversal / symlink-target / special-
file allowlist.

## Lead judgment

Findings 1–5 fixed — all real, all reachable (1 was live), all the change
failing a stated guarantee. 6 is the most consequential residual (a
security control void by config) but is latent on this box and wants a
structural seam change, not a "for fun" patch — filed with the exact
repro and fix. 7–9 are narrow or environment-dead; filed. The exercise
earned its cost: a whole-changeset pass caught a live regression and a
cross-chunk evidence-integrity bug that five separate per-chunk fixpoint
reviews structurally could not.
