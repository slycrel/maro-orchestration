---
status: record
---

# Chunk-2 Adversarial Review — Playbook Repair (257b34d)

Per-chunk discipline from Jeremy's 2026-07-21
/goal ("use the adversarial review skill after each chunk"). Reviewers ran on
the opposite model (3 × `codex exec`, read-only, lenses: Skeptic / Architect /
Minimalist), each with the stated intent + lens-mapped brain principles + the
full 257b34d diff. Raw outputs: session scratchpad `adv-review-c2.OiyoSp/`.

## Intent

Repair the playbook knowledge channel: ranked injection (learned-over-seed,
newest first, dedup, 800-char budget) replacing the head-window horizon bug,
plus a `curate_playbook` verb riding the dream cycle (deterministic dedup +
size-gated CHEAP LLM compress with hard validation; archive-before-write;
kill-switch). Director-path omission (wiring row 17) explicitly out of scope.

## Verdict: CONTESTED

Six real findings, zero hallucinated (0/6 — second consecutive clean round;
historical reviewer-claim error rate 30–78%). One high-severity finding (F1)
drew High from two lenses and Medium from the third — consensus on substance,
split on severity, hence CONTESTED not REJECT. All six verified against the
tree before any fix (verify-before-fix rule).

## Findings (deduped; severity is the post-verification lead rating)

1. **[high] F1 — LLM call inside the write lock** (playbook.py:456→470; all
   three lenses). `curate_playbook` held `locked_write(path)` across the
   CHEAP compression call. A slow/hung call holds the playbook lock for the
   whole round trip; concurrent `append_to_playbook` writers (evolver applied
   insights, Signals) hit the 30s `FileLockTimeout` and lose entries
   (evolver_store.py:449 swallows the failure). Violates
   serialize-shared-state-mutations / boundary-discipline: external work does
   not belong in a file critical section.
2. **[high] F2 — `_valid_compression` doesn't enforce its stated guarantees**
   (playbook.py:403-418; all three lenses). Bullet floor used
   `int(old*0.6)` — 3 bullets could compress to 1 (33%) and pass; header
   check was substring (`## Cost` "preserved" by `## Costly`); attributions
   were set-based, so duplicate attributions collapse undetected. The guard
   that makes LLM rewriting safe was soft exactly where it claimed to be hard.
3. **[medium] F3 — dedup ran before ranking** (playbook.py:191; all three
   lenses). First-occurrence-wins meant a seed bullet sharing a normalized
   core with a later learned entry silently discarded the learned/attributed
   copy before learned-over-seed ranking ever ran; an older learned dup beat
   a newer one. The survivor must be chosen by rank, not file position.
4. **[medium] F4 — budget contract leak** (playbook.py:205-228; Architect).
   The top `## Operational Playbook` header was emitted outside the
   `max_chars` accounting, so `inject_playbook(max_chars=800)` could return
   >800 chars. Callers (recall.py) trust the cap.
5. **[medium] F5 — full-file rewrite via plain `write_text`**
   (playbook.py:498 + pre-existing append at :320; Skeptic). The repo has
   `atomic_write` (file_lock.py:212); a crash mid-rewrite leaves a corrupt
   live playbook that the injection path happily reads. Own-every-file: the
   append path had the same pattern and is fixed too.
6. **[low] F6 — newest-first invisible in rendered output** (playbook.py:222;
   Skeptic/Minimalist). Selection ranked newest-first but rendering re-sorted
   bullets into file order, so the consumer-visible block showed older
   learned entries first.

## Lead Judgment

- **F1 accept** — restructured to snapshot-under-lock → compute (dedup + LLM)
  unlocked → reacquire + compare-and-swap (file changed underneath → skip
  this cycle, next dream cycle retries).
- **F2 accept** — exact header-line Counter checks (occurrence-counted),
  Counter-based attribution preservation, `math.ceil` bullet floor.
- **F3 accept** — rank first, dedup in rank order; highest-ranked copy
  survives.
- **F4 accept** — top header now counted in the budget;
  `len(result) <= max_chars` pinned by test.
- **F5 accept** — `atomic_write` in both `curate_playbook` and
  `append_to_playbook`.
- **F6 accept in part** — bullets within a section now render in rank order
  (newest learned first). The section *grouping* itself stays: grouping by
  file-order sections is deliberate document shape for the prompt, and no
  reviewer argued the sections themselves should reorder.

## What Went Well

Reviewers found no issue with: the dream-cycle seam choice (riding
`maybe_consolidate` instead of a new cadence), the archive-before-write /
abort-if-unarchivable data-retention design, the kill-switch + DEFAULTS
census wiring, the never-raises boundary at the dream-cycle call site, and
keeping row-17 director wiring out of scope (consumer-first). The
verify-before-fix pass confirmed every reviewer citation — second
consecutive 0% hallucination round using intent + principles + full-diff
prompts with tree access.
