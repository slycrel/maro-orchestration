---
status: record
---

# Fixpoint round — re-review of the whole-changeset fixes (2026-08-13)

"Do we run one more adversarial review on things?" (Jeremy). This is the
review-to-fixpoint round: the code under review is commit 505260d — the
FIX COMMIT produced by the previous (whole-changeset) adversarial review.
2 codex lenses (Skeptic + Architect) on the fix diff only. It was justified
by precedent, not ritual: earlier this session a review's own fix
introduced a live regression (the scrub `"1"` bug), so fixes in this
codebase had a demonstrated defect rate — and this round confirmed it.

## Verdict: REJECT → all real findings fixed same session

Both lenses independently converged on three issues (strong signal), plus
each found unique high-value defects. ~7 unique real findings; the receipts
fix was explicitly CLEARED by both ("conservatively correct" / "sound").
Every code claim was verified against the repo before fixing.

### Fixed

1. **[high] Config redaction: value-equality sweep did too little AND too
   much.** The whole-changeset fix added a sweep that replaced any leaf
   *equal* to a collected secret value. Both lenses broke it:
   - **Over-correction** (Skeptic #4 / Architect #2): `api_key: prod` +
     `environment: prod` → the benign `environment` value was destroyed
     because it equalled a 4-char secret. A restored config silently loses
     a legitimate setting.
   - **Alias-in-key miss** (Skeptic #1): an anchor aliased into a KEY
     position (`benign: {*s: allowed}`) leaked — the sweep touched values
     only.
   - **Unhandled containers** (Architect #1): `!!set` / `!!omap` under a
     credential key parse to `set` / list-of-tuples, which `_redact_tree`
     neither traversed nor redacted — so `_redact_config_text` returned the
     raw YAML with `ok=True`, shipping the secret and violating fail-closed.

   Fix: redact by **object identity**, not value equality. PyYAML resolves
   an alias to the SAME object as its anchor (verified: `data["a"] is
   data["b"]` is True), while two coincidentally-equal scalars are distinct
   objects — so identity catches every real alias (key or value) and never
   touches a look-alike. Redacted objects are pinned in a list so their
   `id()` can't be reused mid-walk. Unredactable containers under a
   credential context now raise `_UnredactableShape` → fail closed.

2. **[medium] Scrub 8-char floor left short real keys in the transcript**
   (Skeptic #2 / Architect #4). The injection boundary accepts every
   non-empty provider value, but the scrub skipped anything under 8 chars —
   the two boundaries disagreed. The `"1"` regression's real guard is the
   carrier NAME-skip, not a length test; the floor was redundant and
   harmful. Fix: drop the floor, scrub every non-carrier value.

3. **[medium] Scrub replacement order could expose a secret's tail**
   (Skeptic #3). Insertion-order replacement meant a shorter secret that is
   a prefix of a longer one, redacted first, left the longer's suffix
   (`prefix01` before `prefix0123456789` → `23456789` survived). Fix:
   replace longest-value-first.

4. **[medium] Import byte caps could not restore a legitimate large
   archive** (Architect #3). Export has no size cap; import rejects
   >4 GiB/file and >16 GiB total. A real 17 GiB workspace exported fine but
   could not be re-imported, with no override. Fix: `MARO_IMPORT_MAX_FILE_
   BYTES` / `MARO_IMPORT_MAX_TOTAL_BYTES` env overrides (defaults unchanged;
   the bomb defense stays for untrusted archives; garbage env fails safe to
   default), and the cap error now names the override.

5. **[low] Planner killswitch `""` disagreed with its sibling** (Skeptic #5
   / Architect #5). `planner.read_query_steps: ""` suppressed teaching while
   `executor.read_query: ""` stayed enabled. Fix: extracted a shared
   `read_query.normalize_flag` used by both, so the token sets can't drift.

### Cleared by both lenses

- The receipts error-record change (error-stamped records excluded from
  capture coverage) — "conservatively correct: their writer records no
  `tool_events`, while any present events are still retained as rows."
- The decompression-bomb cap is enforced before extraction and
  `_ArchiveCapExceeded` is handled in both inspect and import lanes.

## Lead judgment

All five accepted and fixed — every one was a fix that traded correctness
for a new defect (over-redaction that eats settings, under-redaction that
ships secrets, a cap that bricks legitimate restores). The identity-based
redaction is the notable upgrade: it replaces a value-equality heuristic
(which cannot distinguish an alias from a coincidence) with the actual
structural fact PyYAML exposes, so it is precise in both directions rather
than tuned. This is the fixpoint converging — round 1 (whole-changeset)
found 9, this round 7, and the defects are now in the fixes' own edges
rather than the original code. One more round is unlikely to clear its
cost; calling this the fixpoint for the arc.

## Process note

Confirmed the skill's new file-on-stdin invocation worked end-to-end at
this prompt size; both reviewers produced clean output on the first run.
