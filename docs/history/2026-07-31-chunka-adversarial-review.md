---
status: record
---

# Chunk A Adversarial Review — camera frames log-forward (2026-07-31)

Post-land review of 184e8b2 (scored retrieval + camera-frame logging +
readout), per the per-chunk discipline. Three Codex lenses (Large change:
Skeptic / Architect / Minimalist) via `codex exec` against the full
`git show 184e8b2 -- src/ tests/ docs/DEFAULTS.md` diff (1243 lines).

## Intent

Instrument the recall lesson-selection fork FORWARD — log what the
chooser could see (scored candidate sets, substrate sizes), what it chose
(durable IDs), with the ranker's raw scores — without changing selection
behavior (plain rankers = strip(scored variants), pinned) and without the
camera ever harming the run it films. Ship the readout consumer in the
same chunk.

## Verdict: CONTESTED

Seven distinct findings after dedup; **7/7 verified real against the
tree, 0 hallucinated** (eleventh clean round). One rated high by the
Skeptic (the 30s lock stall) with the Minimalist rating the same finding
medium — high-severity finding without severity consensus = CONTESTED.
All seven accepted and fixed in this record's companion commit.

## Findings (verified, ordered by severity)

1. **[high — Skeptic; medium — Minimalist] Camera write can stall a
   recall for the global 30s file-lock deadline.** `log_fork_frame`
   called `locked_append` with the configured default deadline
   (`file_lock._lock_timeout_s()` → 30s); under contention the recall
   blocks the full deadline before the catch-all drops the frame —
   "never raises" did not mean "never harms latency".
   **Fix:** `locked_write`/`locked_append` accept a per-call `timeout_s`
   override; camera passes `_LOCK_TIMEOUT_S = 2.0`. Pinned by a
   real-contention test (holder thread + 0.2s override raises
   `FileLockTimeout` fast) and a wiring test (camera passes ≤5s).

2. **[medium — Skeptic + Minimalist] Verdict join dropped scored untyped
   selections.** `camera_readout` hard-coded the `agenda` candidate set;
   recall legitimately selects scored `untyped` lessons when agenda has
   fewer than three, so an agenda-empty run reported `chosen —` despite
   real scored choices. **Fix:** the join now walks
   (`agenda`, `untyped`) with per-frame chosen dedup (untyped is a
   superset of agenda).

3. **[medium — Architect] Fallback recalls produced a false "nothing
   chosen" frame.** When ranked retrieval throws, the legacy
   `inject_lessons_for_task` path renders lessons but records no
   citations/candidates — the frame block still ran and logged empty
   chosen against a run that DID render lessons, silently corrupting the
   chosen==rendered claim. **Fix:** the fallback sets
   `extra.degraded = "legacy_fallback"` on the frame; pinned
   (`test_fallback_frame_marked_degraded`).

4. **[medium — Architect] Verdict means blended incomparable ranker
   families.** Frames record `extra.ranker`, the readout itself printed
   "comparable within one ranker family only" — then averaged hybrid and
   tfidf scores together. **Fix:** verdict buckets key on
   (verdict, ranker); one row per family, caveat reworded.

5. **[medium — Skeptic] Torn/unparsable frame lines were hidden from
   coverage.** `_load_frames` silently discarded parse failures while
   coverage counted only parsed frames — lost data presented as lower
   activity. **Fix:** torn lines counted and surfaced as a WARNING line
   in the coverage section.

6. **[medium — Minimalist] Stale run-dir paths were resurrected as
   orphans.** Only `None` was rejected; a ContextVar retaining a
   deleted/never-created run path had `<stale>/source/` recreated by
   `mkdir(parents=True)` — violating the "drop, don't orphan" claim.
   **Fix:** `Path(rd).is_dir()` guard; pinned.

7. **[low — Skeptic + Minimalist; medium — Architect] Logged scores were
   rounded, not raw.** `round(_s, 6)` in the recall payload contradicted
   the frame's raw-score contract, and `score_share` (4dp) never summed
   exactly to 1. **Fix:** score rounding removed (raw floats persist;
   pinned against `query_lessons_scored` output); share rounding kept
   and honestly documented in the camera_log docstring (tied triple →
   0.3333 each).

## What went well

- The structural-equivalence approach survived all three lenses: no
  reviewer found a behavioral drift between plain and scored rankers —
  the byte-identical selection pins did their job.
- The killswitch (stringly-false tolerant), no-run-dir drop, and
  never-raises envelope were attacked and held.
- Consumer-first shipped as designed: every finding against the readout
  was about honesty of aggregation, not absence of a consumer.

## Lead judgment

All seven accepted. No false positives this round — each finding named a
real gap between a documented claim ("raw scores", "drop don't orphan",
"never harms the run", "comparable within one family") and the shipped
code; exactly the review lens this instrumentation chunk needed, since
its entire value is honest telemetry. The Skeptic's high rating on the
stall is fair for doctrine ("camera never harms the run") even though
practical contention on a per-run append is rare — the 2s override is
cheap insurance, and the seam (per-call `timeout_s`) is additive, so no
existing caller changes behavior.
