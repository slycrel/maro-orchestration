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

**Claim-shape gate (measured 2026-08-16, shipped with slice 2b's
census).** The lexicon finds the *vocabulary* of a method claim; on its
own it cannot tell an assertion from advice, because English past
participles double as adjectives ("verified output"), as tags
(`[recovery-verified]`) and as filenames (`wordfreq-verified.txt`).
Measured over the live box corpus:

| Store | lexicon-only hits | after the gate | true claims |
|---|---|---|---|
| skills.jsonl (398 rows) | 76 | 0 | 0 |
| skills-lite .md (56 files) | 24 | 0 | 0 |
| lessons live+archive (914 rows) | 103 | 21 | ~19 |
| knowledge nodes (1,250 rows) | 100 | 9 | ~5 |

So the gate is a **mood test**: the sentence must report what happened
(an auxiliary — "was fetched", "had been confirmed", "did not correlate"
— or a past-tense verb taking an object), that report must sit in the
main clause rather than a subordinate one ("confirm it *was retrieved*",
"until the page *was fetched*"), its polarity must be positive ("the
fetch *was not* authenticated" reports the opposite), and the lexicon hit
itself must read as a verb rather than a tag, an adjective or a modal
policy ("must be checked", "could have fetched"). Every rule narrows: a
gated-out claim mints no stamp, which is the pre-grounding status quo.

The polarity and subordinate-clause rules came from **review round 1**
(2026-08-16, 4 lenses): grounding "the fetch was not authenticated"
returned `supported` with a real receipt — a false *affirmation*, which
is strictly worse than the false doubt the gate was built to stop — and
the imperative veto's closed verb list was evaded by every verb nobody
enumerated (`download`, `draft`, `install`, and "Retry the fetch **until**
the page was fetched"). The clause rules need no verb vocabulary, which
is why they are the primary net and the list is the backstop. Same round
falsified this doc's own first count: "all nine already-stamped live rows
survive" was 8/9 as landed (one row's claim sat in "needed *to be*
checked", correctly refused as policy), and 6/9 after the round-1 fixes —
the two further drops are a negated claim and an absence claim, both of
which SHOULD stop minting. `scripts/mint_grounding_census.py --recheck`
is the per-row instrument for that audit; totals cannot see it.

The doctrine is not new
— the module always documented imperative advice as never-stamped; the
gate is the implementation catching up, and it answers the slice-2a
review's deferred Architect question (claim-shape hit rate on generalized
LLM prose) with corpus numbers rather than an estimate.

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
   **Slice 2a (2026-08-16) — writer completion + the R1-4 laundering
   fix:** the remaining lesson writers stamp (step-lessons, prereq
   against the sub-loop's own run, thinkback — a lane the R1-3 census
   missed); evolver prompt_tweak documented stampless by construction
   (apply-time ≠ observe-time, advice-shaped text). KnowledgeNode
   gains `grounding`: the bridge grounds each node's OWN text against
   the minting outcome's events at create (never the lesson's stamps —
   different prose, would misattribute), re-observation never
   re-grounds (mint-time semantics), stampless rows keep the absent-key
   shape, and the promotion judge renders unsupported claims with a
   weigh-don't-auto-reject instruction — advisory, per the no-new-judge
   decree. Pins: tests/test_mint_grounding_slice2.py.
2. **Skill mint** — skills-lite + provisional mints: method claims in
   the skill body vs the minting run's tool events. Stamp travels in
   frontmatter (`grounding:` block) + companion Skill record.
   Slice-2a census of the sites: `evolver_store` (×2),
   `loop_finalize:1072`, `run_curation:948` (the LT-4 finding-2
   auto-promotion lane), `skill_lifecycle` (×2), `skills.py:315`.
   **Premise correction, 2026-08-16 (slice 2b, probe-first):** the
   corpus says skill bodies do not carry the claims this slice was
   written to stamp. Skill prose is prescriptive by construction —
   imperative steps and third-person descriptions — and across all 454
   rows of the two skill stores the gated extractor finds **one**
   sentence, itself a false positive from a sentence-split artifact.
   Wiring seven writers to stamp nothing is cost without traffic, so
   the LLM-minted lanes (`skills.py:315` crystallization,
   `skill_lifecycle` synthesis + A/B challengers, `evolver_store`
   apply) are **stampless by construction**, the same finding as the
   slice-2a `prompt_tweak` ruling and for the same reason: what they
   write is advice, not a report.
   **And the seventh site has no traffic either** (probed after the
   first draft of this entry claimed otherwise): `run_curation:948`,
   the skills-lite promotion lane, shipped 2026-07-09 and has fired
   **twice in 787 recorded runs** — one promotion (`changelog_digest.md`,
   whose only lexicon hits were fixture commit messages) and one
   dangerous-pattern skip. The specimen this entry first cited for that
   lane, `repl_reading.md` ("**Measured correction (A/B run e0bbc289,
   2026-08-02)**" plus a token-count measurement), carries no
   `promoted_from` — it was hand/evolver-authored and never passed a
   mint site at all. It still proves the *class* is real: durable
   injected advice can carry an empirical claim every later run
   inherits. But the gate cannot see that shape either (a bare
   measurement report has no auxiliary), so it is evidence for §4's
   lexicon-widening trigger, not a lane to wire.
   **Slice 2b is therefore deferred whole, not re-scoped.** Re-open it
   when `scripts/mint_grounding_census.py` shows claim-bearing skill
   prose — the census is the trigger, and it is one command.
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
- **Precision falsifier (added 2026-08-16, the twin of the unprobed
  rate):** if a hand-read sample of stamped rows shows most stamps
  landing on text that asserts nothing, the extractor is mislabelling
  advice as provenance and the stamps are noise — that is a gate
  failure, not a widening trigger. The measured baseline to beat is in
  §2's table; re-run it against the live stores, not against fixtures.
- **Third-party claims are a known false-alarm class** (found in the
  same census): node prose crystallized from external sources carries
  claims about *someone else's* work — "The system was tested across
  1,980 sessions" — and grounding those against the minting run's
  events lands them `unsupported`, which reads as doubt about a claim
  the run never made. Recorded, not fixed: self-claim vs reported-claim
  needs a discriminator the v1 lexicon does not have.

## 5. Cuts

No new judge (decided). No LLM in the v1 join. No run-narrative in v1.
No retroactive re-stamping of existing stores (stamps accrue on new
mints; the old corpus ages out via decay). No standalone store — stamps
live ON the minted items.
