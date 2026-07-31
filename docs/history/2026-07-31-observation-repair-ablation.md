---
status: record
---

# Observation-Repair Ablation — Measurement #1 (2026-07-31)

**Status: history — completed measurement.** The first artifact after the
taste-lens panel closed itself with "the next artifact must be a
measurement, not a document"
(`docs/history/2026-07-30-taste-lens-panel.md`, round 3). This is the ml
lens's never-run test of the player-inversion thesis: *our worst failures
are immersion breaks (broken observations shown to the player), not
capability gaps (a weak player).*

Harness + raw results preserved at
`~/.maro/experiments/observation-repair-2026-07-31/` (run_ablation.py,
ablation_results.json). Full harness listing in Appendix A; raw results in
Appendix B.

---

## Pre-registration (written before any output)

Verbatim from the harness docstring:

> - Recovery threshold: >=70% of repaired-arm replays recover => thesis
>   survives; the context assembler is the roadmap.
> - Broken arms must reproduce the failure; a broken arm that self-rescues
>   counts AGAINST pure engine-blaming for that anecdote.
> - Scoring: rewriter fork deterministic (clear bool); extraction fork by
>   the shipped provenance classifier (prompt-echo count) + lesson count;
>   godot fork by response content (root-cause/verify vs another
>   same-axis tweak), quoted in the artifact.

Design: 3 canonical failures × 2 arms (broken observation as-lived vs
hand-repaired observation ONLY — never the player, never the ask) × 2
replays. Player = today's box models (subprocess backend), which is the
system the thesis must hold for going forward.

The three failures, grounded in primary artifacts:

1. **rewriter/cobalt-pine** (run `c63b76be-cobalt-pine`, 2026-07-17,
   model=cheap, died `clarification_needed` in 58s): goal rewrite went
   off-script and replaced the X URL with "the referenced thread"; the
   clarity check then flagged the corrupted goal. Broken = the post-rewrite
   goal exactly as the clarity check received it (from
   `build/calls/call-00003.json`); repaired = the original
   `source/prompt.txt` with the URL. Fork = the real `check_goal_clarity`
   on the cheap adapter. Deterministic recovery = `clear: true`.
2. **db37d525/patient-nettle** (run `4d20b559-patient-nettle`, 2026-07-27;
   GOAL-MATCH verified against the quarantined lesson's `source_goal`):
   lesson extraction saw the full dispatch prompt as "the goal" and minted
   a scaffolding echo (the do-not-escalate lore) alongside one genuine
   lesson. Broken = full dispatch prompt as goal; repaired = the
   hand-enveloped bare user ask (task + tire facts kept; operator-behavior
   paragraphs dropped — the `dispatch_envelope` extraction-exclusion
   applied by hand). Fork = the real `extract_lessons_via_llm` with the
   run's actual outcome-row summary; both arms scored by the shipped
   `classify_lesson_provenance` against the FULL original prompt.
   Recovered = >0 lessons AND 0 prompt-derived.
3. **godot font saga** (reconstructed verbatim from
   `~/.maro/experiments/godot-replay-2026-04-22/analysis.md`): four
   commits tuning line_separation/antialiasing while the bundled font
   silently failed to load and a SystemFont fallback rendered instead; the
   Godot console error was printing the whole time but was never shown to
   the player. Broken = the fork after the 4th same-axis commit (code,
   commit list, soft user feedback; NO console). Repaired = + the verbatim
   console line (`ERROR: No loader found for resource:
   res://fonts/Mx437_IBM_VGA_9x16.ttf`). MID adapter, temperature 0.7,
   scored by reading (keyword flags advisory only).

---

## Results

| failure | arm | r1 | r2 | metric |
|---|---|---|---|---|
| rewriter | broken | recovered | recovered | `clear: true` (deterministic) |
| rewriter | repaired | recovered | recovered | `clear: true` |
| extraction | broken | recovered | recovered | 2, 3 lessons; **0 prompt-derived** |
| extraction | repaired | recovered | recovered | 2, 3 lessons; **0 prompt-derived** |
| godot | broken | **failed** | **failed** | wrong theory, no load-verify (read below) |
| godot | repaired | recovered | recovered | exact root cause + concrete next action |

**Pre-registered headline: repaired-arm recovery 6/6 = 100% ≥ 70% — the
thesis survives.** Every failure recovers when only the observation is
repaired. The context assembler is the roadmap.

**The honest contrast note:** repaired-arm recovery only carries causal
weight where the broken arm still fails. Two of the three broken arms
self-rescued (rewriter 2/2, extraction 2/2) — per the pre-registration,
that counts against pure engine-blaming for those anecdotes. What it
actually means: those two failures **no longer reproduce** on today's
engine+player. Both have plausible engine-side fixes shipped since
(`_rewrite_loses_referent` URL-preservation guard, cited to cobalt-pine in
its docstring; extraction-prompt evolution + provenance era), and the
player tier itself has moved — the replay cannot distinguish engine-fixed
from player-smarter. Either way they are **stale evidence** for the
thesis. The effective causal sample is one failure family (godot), n=2
per arm.

## The godot pair — the load-bearing result

Broken r2 (recap: four commits in, seams unchanged, no console):

> "Stop touching font/line-spacing settings — four commits into that lane
> with zero visible change means you're not fixing the actual cause. …
> **Next action:** set nearest-neighbor texture filtering on the node
> drawing the logo — `texture_filter = CanvasItem.TEXTURE_FILTER_NEAREST`"

Broken r1 is the same shape: "that invariance means the cause isn't in
glyph rendering or line spacing, so continuing to tune those is a dead
end" → confident atlas-bleed/texture-filter theory.

Both broken replays **escape the literal same-axis trap** — today's MID
player does not commit a 5th line_separation tweak, which the April
player (and the April human pair) did. But neither replay questions
whether the font ever loaded, and neither proposes a verification step
that would surface it. The player fills the missing axis with a coherent,
confident, *wrong* theory on the axes it can see — texture filtering
would have been a 5th no-op commit, just on a fresh axis (the seams came
from the fallback font's glyph metrics, not GPU sampling).

Repaired r1 (+ verbatim console line):

> "The four commits are all no-ops: `load()` is failing (per the console
> error), so `vga` falls back to `SystemFont`, and every setting you've
> tuned … lives inside `if vga is FontFile`, which never runs. You've been
> polishing a font that isn't the one rendering."

→ then the exactly-correct next action (missing `.import` sidecar; open
in editor / `godot --headless --import`; commit the sidecar). Repaired r2
identical in substance, plus a filename-case check. This matches the
2026-04-22 human replay finding (console paste → instant recovery), now
reproduced 2/2 under today's player.

**Upgrade to the finding:** the cost of a missing observation on today's
player is not repetition — it is *confabulation*. The player no longer
loops; it invents a plausible wrong theory and moves with confidence.
That is arguably worse for an autonomous system: the same-axis loop is
detectable by convergence checks (Phase 62 brake, closure fingerprints);
a confident fresh-axis wrong theory defeats them — each attempt looks
like progress. This makes the round-3 "missing axes are invisible to any
retrospective readout" point concrete: no post-hoc scorer of the broken
transcript could see what was absent. The repair had to come from the
world (the console), i.e. from the observation channel — exactly what the
camera-axes/log-forward work (Chunk A, in flight) and the context
assembler are for.

## Secondary observation — broken observations cost wall-clock even when they recover

rewriter broken 22.0s/22.0s vs repaired 10.0s/14.0s; godot broken
48.1s/54.1s vs repaired 18.0s/24.0s; extraction shows no signal
(22–32s both arms). Two of three failure families ~2× slower on the
broken observation — consistent with the model writing its way through
confusion (the max_tokens overshoot warnings show longer outputs on
broken arms). n is tiny and subprocess latency is noisy; recorded as an
observation, not a claim.

## Caveats

- **Today's-player caveat (pre-noted):** the "frozen player" is the
  current subprocess-backend model tier, not the model that failed in
  April/July. That is the right player for a forward-looking thesis and
  the wrong one for historical attribution; both statements are true.
- **Reconstruction fidelity:** the godot fork is a faithful but
  hand-built reconstruction (verbatim code/commits/feedback from the
  replay analysis); the other two forks replay the real recorded inputs
  through the real shipped functions.
- **Keyword scorer limits:** the harness's godot keyword flags misfire
  (repaired arms flagged `same_axis=true` for *mentioning* the settings
  while explaining the no-op) — scoring followed the pre-registered
  by-reading rule; flags kept in the raw data as advisory.
- **n=2 per arm.** The godot separation is 0/2 vs 2/2 with the same
  content both sides; clean but small. Replay-twice was the pre-committed
  noise control, and neither godot arm split across replays.

## What this changes

1. **Thesis survives its first measurement** — observation repair alone
   recovers every canonical failure; where the failure still reproduces,
   the repair flips it 0/2 → 2/2. The context assembler stays the
   roadmap, now with its first controlled evidence.
2. **Two of the three canonical anecdotes are retired as evidence** —
   rewriter/cobalt-pine and db37d525 no longer reproduce and should not
   be cited as live failures of the current system (they remain valid as
   history and as regression targets).
3. **The live failure class is godot-shaped: missing axes.** Detection
   cannot be retrospective (confabulation defeats transcript review);
   the fix is widening what the player can see — camera-frame logging
   (Chunk A), the overdraw/coverage axes, and eventually the assembler
   choosing observations deliberately.
4. **Convergence brakes are not enough.** The failure mode they catch
   (same-axis looping) is the one today's player already avoids; the one
   it commits (confident fresh-axis wrong theory) they cannot catch.

## Appendix A — harness (run_ablation.py, verbatim)

See `~/.maro/experiments/observation-repair-2026-07-31/run_ablation.py`
for the runnable copy. Key structure: real `check_goal_clarity` /
`extract_lessons_via_llm` / `classify_lesson_provenance` seams (zero
store writes — extraction-only calls); broken goals taken from the
recorded call/prompt artifacts byte-for-byte; asserts pin the
URL-presence contrast before any model call.

## Appendix B — raw results

Preserved at
`~/.maro/experiments/observation-repair-2026-07-31/ablation_results.json`
(12 rows: failure/arm/replay/metrics/latency, full godot responses
truncated at 900 chars each).
