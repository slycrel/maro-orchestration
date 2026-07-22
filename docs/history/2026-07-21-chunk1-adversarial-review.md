---
status: record
---

# Chunk-1 adversarial review — verdict + fixes (2026-07-21)

First run of the per-chunk adversarial-review discipline (Jeremy's /goal
2026-07-21: "use the adversarial review skill after each chunk"). Three
Codex reviewers (`codex exec`, read-only, opposite-model rule) ran the
Skeptic / Architect / Minimalist lenses against landed commit `b6fd488`
(chunk 1: MID floor, local-wiring removal, factory adjudication). Every
finding was verified against the tree before acting (verify-before-fix);
fixes landed as the follow-up commit this doc rides in.

## Intent (as given to reviewers)

Chunk 1 of the swarm-review repair arc: unify execution defaults at MID
via the role registry, remove local-model wiring (ladder = Tier-0 →
hosted-free → paid), adjudicate the factory experiment, ship the typed
finding-code vocabulary, save the report-only wiring inventory, plus
bookkeeping. Reviewers judged whether the commit achieves that intent —
not whether the intent is right.

## Verdict: CONTESTED → all high-value findings fixed same-day

No reviewer called REJECT; all three said "mostly achieves the intent,
but residual cheap paths and a weaker-than-claimed read boundary remain."
Six findings accepted (all verified true), one rejected. Reviewer
hallucination rate this round: **0/7 checked claims wrong** — every cited
file:line was real (a first; the historical rate is 30–78%).

## Findings (deduped; severity; disposition)

1. **[medium, all 3 lenses — FIXED]** `parse_finding_codes()` silently
   dropped unknown `FINDING[...]` stamps, so a typo'd code would vanish
   from the chunk-7 readout instead of forcing repair. The read boundary
   is now strict by default (raises, naming the unknown codes); tolerant
   readers opt in with `strict=False` and own surfacing
   `parse_unknown_codes()`. Pin: `test_parse_unknown_code_raises_by_default`.
2. **[medium, Skeptic+Architect — FIXED]** `factory_thin`'s standalone
   surface still defaulted CHEAP (`run_factory_thin(model="cheap")`,
   `maro-factory-thin --model` default) while the `mode:thin` path through
   handle.py got MID — the exact split chunk 1 claimed to remove, behind a
   different entry point. Both defaults now `mid`;
   `docs/EXECUTION_FLOW.md` "Haiku, fast" label corrected.
3. **[medium, Skeptic — FIXED]** Blocked-step recovery still built CHEAP
   adapters: the refinement-hint call (`step_exec.py`
   `_generate_refinement_hint`) and the timeout-split call
   (`loop_blocked.py _generate_timeout_split`). Both are
   execution-shaping calls (the hint steers a MID retry; the split
   rewrites the plan), so both now ride
   `assign_model_by_role("worker")` — tier ownership stays in the role
   registry, not scattered `MODEL_CHEAP` pins.
4. **[medium, Architect — FIXED]** Intent classification rode the shared
   run adapter, which chunk 1 bumped to MID — the worker default leaked
   backward into a decreed-CHEAP classifier role, and `handle()` vs
   `conduct()` enforced different role semantics. `classify()` now gets
   its own `assign_model_by_role("classifier")` adapter (falls back to
   the run adapter if the build fails). First cut of this fix broke a
   regression test and was refined: a **caller-injected** adapter is the
   test/scripted seam and carries every call including classification —
   the classifier-role adapter is built only when handle built the run
   adapter itself.
5. **[medium, Minimalist — FIXED]** `validator_roi.py` never counted the
   live ladder's `tier="hosted-free-decisive"` rows (only
   `local-decisive`/`escalated`/`paid`), so the ROI report showed zero
   savings from the current free rung — corrupting the exact signal the
   tool exists to provide. Reworked to free-decisive accounting (hosted +
   historical local), `hosted_free:` source recognition, renamed report
   keys (`free_decisive`, `avg_free_latency_ms`, …), docstring/CLI
   updated. Pin: `test_hosted_free_decisive_rows_counted`.
6. **[low, Minimalist — FIXED]** Living docs advertised removed/unwired
   surfaces: `README.md` "Local-first validation" bullet (cited deleted
   `local_models.py` + a local-era 82% stat) rewritten for the
   hosted-free ladder; `user/README.md` rows for `max_steps`,
   `always_skeptic`, `notify_on_complete` now marked **not yet wired**
   (matching `user/CONFIG.md`, which already said so).
7. **[low, Skeptic — REJECTED]** "Retired `docs/LOCAL_VALIDATOR.md` body
   still references deleted code." The frontmatter note explicitly frames
   the body as the pre-removal methodology record ("do not treat as
   current wiring") — that's what `status: record` means. Editing the
   body would falsify the record; the top-of-file framing is the cure and
   it's already there.

## What went well (per reviewers)

The main handle/loop MID path, the local-wiring removal itself, the
factory adjudication paper trail, and the wiring-inventory provenance
framing drew no findings. The commit's smoke evidence (model=mid per
step, hosted-free decisive, no ollama) held up under adversarial reading.

## Lead judgment / lessons

- The decree-defeat pattern chunk 1 itself discovered (config pin,
  post-escalate hardcode) had **three more instances** the smoke run
  couldn't see: alternate entry points (factory_thin CLI), recovery
  paths (hint/split), and role leakage (classifier on the worker
  adapter). A smoke run exercises one path; adversarial review swept the
  others. The per-chunk review discipline earned its keep on round one.
- `FINDING[GAP_UNDERSTATED]` applies to chunk 1's own claim "the
  cheap-vs-mid execution split is retired": verification added scope
  (three more residual cheap paths beyond the two found at ship time).
