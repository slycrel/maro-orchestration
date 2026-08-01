---
status: record
---

# Adversarial review — recon emission + watch instrument (2026-08-01)

Post-land review of the day's two implementation commits, per the arc's
standing discipline (3 Codex lenses via `codex exec`; opposite-model rule).

- **9967c68** — cuts-lane recon emission: `_cuts_plan` tags probes
  deterministically, gated on `planner.recon_flavor`; recon steps exempt
  from milestone expansion.
- **575aa65** — recon watch instrument: `discretion_readout.recon_summary`
  with since-first-seen denominators.

Reviewers: Skeptic (correctness), Architect (structure), Minimalist
(necessity). Every claim verified against the tree before acting.

## Intent

Deterministically tag cuts-lane probes as recon steps (the cuts lane
returns before the taught decompose, so prompt-side emission couldn't
reach the probe phase), and add an honest since-first-seen recon
tabulation to the discretion readout.

## Verdict: PASS with fixes

No high-severity findings. 7 deduped findings, **7/7 verified real, 0
hallucinated** (the clean streak holds). 5 fixed in this commit, 1
re-scoped to BACKLOG, 1 deferred with a named trigger.

## Findings

1. **[medium — FIXED]** Corpus reader crashed on valid-JSON non-dict
   payloads: `d.get("steps")` sat OUTSIDE the try (discretion_readout.py:341),
   so a loop log containing `[]` raised AttributeError and killed the whole
   readout; a dict with non-list `steps` was silently dropped while still
   counted in `files_read`. (Skeptic 1, Architect 2.) Principle:
   boundary-discipline / prove-it-works. Fix: schema validation at the
   ingestion boundary; invalid files fold into `files_failed`.

2. **[medium — FIXED]** Since-first-seen ordering was a lexical string
   sort over raw `started_at` (line 349). Mixed UTC offsets mis-order
   (`08:00-04:00` = 12:00Z sorts before `11:00+00:00`), and a tagged loop
   MISSING its timestamp sorted first (`""`), making the cohort ALL loops —
   recreating the exact pooling defect the instrument exists to avoid.
   (Skeptic 3, Architect 3.) Fix: tz-aware `fromisoformat` parse
   (naive → UTC), unorderable files fold into `files_failed`, sort by
   instant; pinned with a mixed-offset test and a missing-timestamp test.

3. **[medium — FIXED]** The "no recon-tagged step in any loop log yet"
   branch — the early-watch verdict this instrument was built to deliver —
   omitted the `files_failed` caveat (only the tagged branch rendered it).
   A corrupt first post-emission log carrying the only tags would read as
   an authoritative zero. (Skeptic 2, Architect 4, Minimalist 1 —
   three-lens convergence.) Fix: caveat renders in the no-tag branch too;
   wording covers unreadable AND schema-invalid.

4. **[medium — FIXED]** The fix commit's stated root cause was wrong:
   `RECON_FLAVOR_RULES` is appended to `extras` (planner.py:870) BEFORE
   the cuts block (937), and `draw_cuts` splices `context_extras` into its
   system prompt (313–314) — so "draw_cuts never sees RECON_FLAVOR_RULES"
   (the `_cuts_plan` docstring) was false. The empirical outcome stood
   (live smokes emitted zero tags even with the rules present — the cuts
   JSON format dominates), so the deterministic fix was the right change
   with a wrong mechanism claim. It also left a reachable hole: a
   model-authored bare `[recon]` in a probe was preserved as-is — a
   permanent voi-missing row. (Skeptic 4.) Principle: fix-root-causes.
   Fix: RECON_FLAVOR_RULES filtered out of the draw_cuts context (single
   deterministic emitter, prompt noise removed from the token-capped cuts
   call), docstring corrected, bare `[recon]` probes re-tagged with the
   deterministic VOI; VOI-bearing model tags still preserved.

5. **[medium — re-scoped to BACKLOG]** The milestone-expansion exemption
   applies to EVERY recon-tagged step, not just cuts probes — a broad
   prompt-emitted recon step ("survey every service …") flagged as a
   milestone now runs as one oversized execution. (Architect 1,
   Minimalist 2.) Lead call: rejected as an immediate change — expanding a
   recon step is semantically wrong regardless of origin (sub-decompose
   rebuilds it into a commit-shaped sub-plan, destroying the VOI question
   and honest recon verification), and oversized recon is an
   emitter-contract violation the recon JUDGE + the readout's
   blocked/other buckets are built to surface. The reviewers' upgrade
   path is real, so it's a named BACKLOG edge: carry flavor+VOI through
   expansion if the corpus shows broad recon steps in practice.

6. **[low — FIXED]** A nonexistent/mistyped `--runs-root` produced
   `sourced: True` with 0 loops instead of an honest refusal.
   (Architect 2 tail.) Fix: existence check → not-sourced with the path
   in the reason, rendered by the report.

7. **[low — deferred, trigger named]** Loop logs are written with plain
   `write_text` (loop_artifacts.py:233) — a racing reader can see partial
   JSON. Deferred: the window is milliseconds once per loop finalize,
   self-healing across readout invocations, and now VISIBLE via finding
   3's render fix. Revisit if `files_failed` shows up persistently
   nonzero on live readouts.

## What Went Well

- The since-first-seen cohort design itself drew no challenge from any
  lens — the denominator architecture held; the findings were all about
  the corpus boundary feeding it.
- Killswitch discipline (emission-only gating, unconditional detection)
  and the single-corpus sourcing rule both passed untouched.
- Three independent lenses converged on the same top defects with zero
  hallucinated claims — the review-to-fixpoint pipeline is earning its
  keep.

## Lead Judgment

- Findings 1–4, 6: accept — verified, cheap, each now pinned by a test.
- Finding 5: reject the narrowing, accept the concern as a BACKLOG edge
  (evidence-gated by the very instrument under review).
- Finding 7: defer with trigger — runtime-writer changes don't belong in
  a readout review-fix commit, and the failure mode is now visible.
