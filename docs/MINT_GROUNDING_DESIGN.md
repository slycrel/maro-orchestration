---
status: living
---

# Mint-Time Grounding — evidence receipts on learning-store mints

**Decided shape (Jeremy, 2026-08-06):** "evidence will help with
certainty, we don't need a fresh set of eyes for this with another
judge." Annotation with event-log receipts, fail-open; **no new judge**.
Fail-closed only where reuse *republishes* a claim. Decision journal
`09fc42da`; GOAL_BRAIN 2026-08-06 record.

## 1. Problem — the claims-vs-events family

Minted artifacts carry method/provenance claims the run's own event log
contradicts or cannot support, while the *data* stays real. Five logged
instances across two model tiers (model-independent by LT-5):

| Specimen | Claim | Event-log truth |
|---|---|---|
| LT-4 B3 | "authenticated CLI fetch" | unauthenticated r.jina.ai render |
| LT-4 B1w | "blocks confirmed this session" | zero probe events in the session |
| LT-4 B3w | (reuse) republished B3's label untouched | no re-verification events |
| LT-5 c3c58c40 step 8 | "second independent authenticated fetch supplied this step" | zero fetch tool events in that step's record; x.com 403'd; content from step 2's syndication-CDN fetch |
| LT-5 reviewer | dismissed the TRUE a0bae77 discovery as "hallucination signature" | the settling probe (one curl) was never run |

The last row is why **annotation, not another judge**: heuristic judges
err in both directions — they let fabricated provenance through AND
kill true novel observations. Closure can't catch this either (it
verifies deliverables, not the provenance story attached to them).

## 2. Shape

At mint time, join each claim-bearing statement against the run's
**existing** ground truth — call records' `tool_events` (byte-level,
already captured per step), `outcomes.jsonl`, `skills_manifest.jsonl` —
and stamp the minted item with receipts:

```json
"grounding": [
  {"claim": "fetched via syndication CDN", "status": "supported",
   "receipts": ["build/calls/call-00010.json#tool_events[7]"]},
  {"claim": "authenticated fetch", "status": "unsupported",
   "receipts": [], "note": "no fetch-family tool events in minting step"}
]
```

Three statuses only: `supported` (receipt found), `unsupported`
(event log affirmatively lacks the event family the claim requires),
`unprobed` (claim shape we don't parse — honest default, never guessed).
**Fail-open:** an `unsupported` stamp never blocks the mint; consumers
weigh it. **Fail-closed at republish only:** a reuse path that would
copy a claim forward (the B3w case) refuses `unsupported` provenance
claims — republishing is the act that launders them into new provenance.

Zero new LLM calls in v1: claim detection is a verb-pattern lexicon over
the observed family (fetch/auth/verify/confirm/test/run + "this
session"/"this step" locality), joined deterministically against tool
event names/inputs. The family we've actually logged is narrow;
LLM claim-extraction is a v2 upgrade edge, not a v1 dependency
(cuts-first). `claim_probe.py` stays the *contestation* lane (shell
probes for reviewer disputes); this is the *mint* lane (receipts from
events that already happened). Shared vocabulary, different verbs.

## 3. Slices

1. **Lesson mint** — **SHIPPED 2026-08-06** (`src/mint_grounding.py`;
   pins in `tests/test_mint_grounding.py`). Both extraction paths stamp
   (finalize-time `reflect_and_record` AND the deferred lane organic
   runs actually take); stamps ride both stores on the UU-4 shared-id
   rows (`TieredLesson.grounding` + flat `Lesson.grounding`,
   absent-key discipline). Consumers: both injection surfaces render a
   marker for unsupported claims, and CLI readouts show a
   `[claims: N✓/N✗/N?]` census tag (the unprobed-rate falsifier's
   measurement surface). A seed-reader skip shipped alongside but the
   S2 seed block was removed the same night on the A/B verdict, taking
   that consumer with it. Live validation during the build: grounding
   LT-5's own step-8 specimen text against c3c58c40's real event log
   produced auth=unsupported + fetch=supported with the exact
   syndication-CDN receipt (`call-00010#tool_events[8]`) hand-traced
   during LT-5 — and the first lexicon draft marked auth supported off
   `token=a` (a dummy public URL parameter), so credential markers now
   require credential-shaped values; that false-support is pinned.
   Tightened same-day on the 24h-diff adversarial review (R1-1/R1-2):
   family-level fallback support now applies only to claims naming no
   identifier-shaped specifics — "fetched from api-a.example" lands
   unprobed rather than riding api-b's receipt — and the bare
   login/passw/credential auth markers (which matched an anonymous
   `curl .../login` URL) were replaced with assigned-value forms. Both
   pinned in tests/test_mint_grounding.py. Slice 1 sub-slice: the
   loop_finalize recovery-lesson writers now stamp too (loop_id was
   already in hand).
2. **Skill mint** — skills-lite + provisional mints: method claims in
   the skill body vs the minting run's tool events. Stamp travels in
   frontmatter (`grounding:` block) + companion Skill record.
3. **Republish gate** — artifact-reuse and warm-arm lanes: copying a
   provenance claim forward requires `supported` or re-derivation.
   The ONLY fail-closed point.
4. **Run-narrative grounding** (later, explicitly out of v1) — step
   summaries/RESULT.md ride the same join once the store lanes prove
   the mechanism.

## 4. Falsifiers (named, per the design-review corollary)

- If the next LT batch shows the same claims-vs-events recurrence rate
  in *stamped* stores as LT-4/LT-5 unstamped baselines, the annotation
  isn't changing behavior — revisit fail-open.
- If >30% of stamps land `unprobed` on organic runs, the verb lexicon
  is too narrow to matter — that's the trigger for LLM extraction (v2),
  not for silently widening regexes.
- If the republish gate ever blocks a claim that manual review finds
  true-but-unlogged, log it as a counter-specimen — the LT-5 reviewer
  case warns exactly here.

## 5. Cuts

No new judge (decided). No LLM in the v1 join. No run-narrative in v1.
No retroactive re-stamping of existing stores (stamps accrue on new
mints; the old corpus ages out via decay). No standalone store — stamps
live ON the minted items.
