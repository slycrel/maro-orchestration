---
status: record
---

# Adversarial Review: Portable Learning (Chunks 1–4)

**Date:** 2026-07-13
**Scope:** Combined diff `271890f..44b7875`, restricted to the portable-learning
file set (excludes interleaved unrelated container-executor work):
`src/pack.py`, `src/secret_scrub.py`, `src/workspace_import.py`,
`src/knowledge_web.py`, `src/skills.py`, `docs/PORTABLE_LEARNING_DESIGN.md`,
`docs/MIGRATION.md`, `tests/test_pack.py`, and related.
**Reviewers:** Codex (`codex exec`) — Skeptic, Architect, Minimalist. 3 reviewers
per the Large-change tier (200+ lines / 5+ files).
**Method:** Per `adversarial-review` skill — reviewers run read-only, output
files verified non-empty before synthesis, findings independently re-checked
against source before Lead Judgment.

## Intent

Implement `docs/PORTABLE_LEARNING_DESIGN.md` end to end across 4 chunks:
migration semantics + trust model (1), provenance fields + identifier
scrubbing (2), `maro-pack export`/`seal` (3), `maro-pack import`/`adopt` (4).
Core guarantee under test: imports are *contested-by-birth* — nothing from
an external pack is trusted at face value, trust is earned locally the same
way a local observation earns it, and the only mechanical guarantees are
secret-shaped-string scrubbing + known-identifier redaction + a mandatory
human review gate before sealing (never mechanical anonymization).

## Verdict: CONTESTED → RESOLVED

3 high-severity findings with reviewer consensus (would have been REJECT).
All 3 were confirmed real against the actual code and fixed in this pass,
along with 6 medium and 2 low findings. 1 medium finding deferred with
recorded rationale. See Fixes Applied below.

## Findings

1. **[high]** `--target` not honored by trust-bearing writers. `import_pack()`
   resolves `target` into `ws` but rules/hypotheses/lessons/skill-record
   writers go through global helpers (`_hypotheses_path()`,
   `_append_tiered_lesson()`, `save_skill()`) that read the active
   `$MARO_MEMORY_DIR`/config, not `ws`. A `--target` import could write
   Class C learning into the *active* workspace instead of the target, while
   the audit ledger claims it landed in the target — split, silently
   incorrect state.
   - Lens: Architect + Minimalist (independently), also implicit in Skeptic's
     test-fixture note.
   - Principle: boundary-discipline, outcome-oriented-execution.
   - Recommendation: scope the `$MARO_MEMORY_DIR` env var to the target
     workspace for the duration of the write loop.

2. **[high]** Sealed-pack artifact contents are not integrity-checked on
   import. Export records a per-artifact `sha256` in the manifest; import
   only verified the `REVIEW.md` tamper hash, never the artifacts
   themselves. An attacker (or a bad transfer) could alter
   `artifacts/memory/standing_rules.jsonl` after sealing while `REVIEW.md`
   stays byte-identical, and import would accept it — breaking the "human
   reviewed what will actually ship" guarantee.
   - Lens: Skeptic, Architect, Minimalist — all three independently.
   - Principle: boundary-discipline, prove-it-works.
   - Recommendation: verify every manifest-declared `sha256` against actual
     archive member bytes before any mutation; fail closed on mismatch.

3. **[high]** Manifest `relpath`/`path` and CLI `label` are untrusted input
   usable for path traversal. Quarantine paths were built by joining these
   directly with no containment check; a crafted `../../` path or label
   could write outside `imports/<label>/`, including onto live
   skills/memory files, bypassing `adopt`'s explicit-gate guarantee entirely.
   - Lens: Skeptic, Architect, Minimalist — all three independently.
   - Principle: boundary-discipline.
   - Recommendation: reject absolute paths, `..` segments, and path
     separators in labels at a single choke point before any path is used.

4. **[medium]** Malformed rows mid-import can leave partial, unaudited state.
   The import loop wrote each artifact as it was processed but only appended
   the `imports.jsonl` audit row after every artifact succeeded. A single bad
   row later in the pack (e.g. non-numeric `score`) would raise, aborting the
   call — earlier writes stayed on disk with no audit trail.
   - Lens: Skeptic, Architect, Minimalist — all three independently.
   - Principle: outcome-oriented-execution, prove-it-works.
   - Recommendation: contain row-level failures per-row (record as a
     `malformed_skipped` outcome) rather than letting one bad row abort the
     whole call.

5. **[medium]** `scrub()`/`scrub_identifiers()` recurse into dict *values*
   only — keys pass through unscrubbed, narrower than the docs' "applies to
   every string" framing.
   - Lens: Architect, Minimalist (partial — framed as filename/path scope).
   - Principle: boundary-discipline.
   - Recommendation: recurse into string keys with the same scrub calls.

6. **[medium]** `maro-import`'s audit rows have no `action` field, while
   `maro-pack import`/`adopt` do — inconsistent ledger schema exactly where
   provenance-changing operations are supposed to be distinguishable.
   - Lens: Skeptic.
   - Principle: boundary-discipline.
   - Recommendation: add `"action": "workspace_import"`.

7. **[medium]** `adopt()`'s "never overwrite a live file" guarantee has a
   TOCTOU race: `dest.exists()` check and `dest.write_text()` write were two
   separate operations, so a concurrent adopt/write between them could
   silently overwrite a file that arrived in the gap.
   - Lens: Skeptic.
   - Principle: serialize-shared-state-mutations (concurrency-hardening
     standard already applied elsewhere in this codebase).
   - Recommendation: atomic `O_CREAT|O_EXCL` create, treating `FileExistsError`
     as the existing skip case.

8. **[medium]** Incoming `row["imported"]` provenance was discarded and
   replaced wholesale instead of nested under `imported.original_provenance`
   as the design doc explicitly requires (line 168) — multi-hop packs would
   lose chain-of-custody metadata.
   - Lens: Minimalist.
   - Principle: boundary-discipline; explicit design-doc requirement, not
     discretionary.
   - Recommendation: nest existing `imported` under `original_provenance`
     when present, before building the fresh contested-by-birth fields.

9. **[low]** Imported skill records kept the origin's `tier` verbatim instead
   of resetting to `"provisional"` — contested-by-birth should apply to tier
   the same way it applies to confirmations/contradictions/score.
   - Lens: Skeptic.
   - Principle: boundary-discipline (design intent, not explicit spec text —
     no explicit tier-reset requirement exists in the design doc for skill
     records specifically).
   - Recommendation: always import as `"provisional"`, keep origin tier under
     `imported.original_tier`.

10. **[low]** `docs/MIGRATION.md` self-contradicted: one section said
    "import/adopt are next" while another said the full lifecycle ships.
    - Lens: Architect.
    - Principle: foundational-thinking (docs at a trust boundary should not
      be internally inconsistent).
    - Recommendation: remove the stale "next" phrasing.

11. **[medium, deferred]** Artifact *filenames*, manifest `path` strings, run
    IDs, and REVIEW.md section headings are not scrubbed — only artifact
    *content* is. A skill/persona filename containing a username or hostname
    would leave the box unredacted.
    - Lens: Minimalist.
    - Principle: subtract-before-you-add / outcome-oriented-execution.
    - Recommendation (not implemented this pass): see Lead Judgment.

## What Went Well

- Bi-temporal correctness (`last_reinforced` set to import time, not
  origin's) was already correct — no reviewer found fault with the decay-math
  interaction, despite it being exactly the kind of subtle timing bug this
  class of feature invites.
- The trust-demotion mechanics themselves (rules→hypotheses, lesson score
  cap, skill stats reset to `imported.claimed_*`, content-hash dedup) drew no
  correctness findings from any of the three reviewers — the core
  contested-by-birth design is sound as implemented.
- The two-tool split (`maro-import` trust-neutral vs `maro-pack`
  trust-demoting) was not challenged as a design choice by any reviewer —
  the boundary between them held up under an architecture-focused lens.

## Lead Judgment

1. **Accept** — `--target` scoping. Confirmed by reading `_hypotheses_path()`
   / `_append_tiered_lesson()` / `save_skill()` call chains; a real
   silently-wrong-workspace bug, not a style nit. Fixed via
   `_memory_dir_override()` context manager scoping `$MARO_MEMORY_DIR` for
   the write loop.
2. **Accept** — artifact sha256 verification. The manifest already carries
   the hash; not verifying it defeats the seal's entire tamper-evidence
   purpose for zero implementation cost saved. Fixed.
3. **Accept** — path traversal. Unanimous across all three lenses, concrete
   and exploitable. Fixed via `_safe_relpath`/`_safe_label` at the single
   dispatch choke point (`_artifact_relpath`) plus label validation in both
   `import_pack()` and `adopt()`.
4. **Accept** — partial/unaudited imports on malformed rows. Real gap between
   "written" and "audited" states; per-row containment is the right fix
   (matches the existing per-row `JSONDecodeError` handling already present,
   just widened to all exceptions). Fixed.
5. **Accept** — dict-key scrubbing. Low current risk (Class C schemas use
   fixed key names) but the docs claim "every string" and the fix is
   mechanical and cheap. Fixed in both `scrub()` and `scrub_identifiers()`.
6. **Accept** — `maro-import` missing `action` field. Cheap, closes a real
   ledger-schema inconsistency the design explicitly calls for. Fixed.
7. **Accept** — `adopt()` TOCTOU. This codebase already treats
   check-then-write races as a real hazard class elsewhere (concurrency-
   hardening arc); no reason this call site should be the exception. Fixed
   via `O_CREAT|O_EXCL`, with dry-run kept as a non-atomic report-only path
   (dry-run never writes, so the race has no consequence there).
8. **Accept** — provenance nesting. Not discretionary: the design doc states
   this verbatim at line 168. Fixed across all four `_import_*` helpers.
9. **Accept, downgraded rationale** — skill tier reset. No explicit spec text
   requires this (confirmed by re-reading design doc §3), but it's the
   obviously-correct extension of contested-by-birth already applied to
   every other skill-record field, and leaving it inconsistent would be a
   worse outcome than fixing it. Fixed; original tier preserved under
   `imported.original_tier`.
10. **Accept** — MIGRATION.md contradiction. Trivial, no reason not to. Fixed.
11. **Reject-for-now, documented gap** — filename/path/manifest-string
    scrubbing. This is real but its correct fix is bigger than this pass:
    `adopt()` derives the *live* filename directly from the quarantined
    artifact's name, so scrubbing filenames at export time changes what
    `adopt` promotes and how collisions are detected — that's a design
    decision (rewrite-on-export vs. flag-and-let-human-rename-during-review)
    that shouldn't be rushed into the same session as the other 10 fixes.
    Recording as a known-gap: filenames, manifest `path` strings, and
    REVIEW.md headings are not currently identifier-scrubbed; the human
    review gate is the only backstop for this specific vector today. Revisit
    when there's a concrete case (e.g. a real skill filename carrying an
    identifier) rather than speculatively.

## Fixes Applied

- `src/pack.py`: `_safe_relpath`/`_safe_label` validation at the artifact
  dispatch choke point and in both `import_pack()`/`adopt()`; per-artifact
  sha256 verification before any mutation; `_memory_dir_override()` scoping
  `$MARO_MEMORY_DIR` to `--target` for the entire write loop; per-row
  `try/except Exception` containment with `malformed_skipped` outcome in all
  four `_import_*` helpers; provenance nesting under
  `imported.original_provenance` when incoming rows already carry
  provenance; skill-record tier always imported as `"provisional"` with
  `imported.original_tier` preserved; `adopt()`'s never-overwrite guarantee
  made atomic via `os.O_CREAT | os.O_EXCL`.
- `src/workspace_import.py`: audit rows now carry
  `"action": "workspace_import"`.
- `src/secret_scrub.py`: `scrub()` and `scrub_identifiers()` now scrub
  string dict keys, not just values.
- `docs/MIGRATION.md`: removed the stale "import/adopt are next"
  contradiction.
- Known-gap recorded (not fixed): artifact filenames / manifest path strings
  / REVIEW.md headings are not identifier-scrubbed — see Lead Judgment #11.

Full 169-file suite + `tests/test_pack.py` (57 tests) green after fixes.
