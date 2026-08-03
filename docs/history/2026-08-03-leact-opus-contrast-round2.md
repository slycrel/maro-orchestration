---
status: record
---

# LeAct × maro — Opus-5 blind contrast, round 2 (2026-08-03)

**Status: record.** Second pass of the same instrument: Jeremy re-ran the
Opus-5 cowork session against the public repo after ~214 commits / +9.1k
lines in `src/`, and brought the output here for verification. Round 1 is
`docs/history/2026-07-31-leact-opus-contrast.md` — read it first; this
file only carries what *changed*.

Every checkable code claim was verified against the tree at `25c286b`
before this record was written. The source text lives at
`~/claude/opus-feedback.txt` (machine-local, not in git) — **the Aug-3
section is reproduced verbatim in the appendix below** so this record
survives the box.

## Accuracy scorecard — round 2

**10 checkable claims, 10 substantively correct, 0 wrong.** A marked
improvement on round 1 (8 exact / 1 flat wrong / 2 overstated / 1
arithmetic slip) and well clear of the historical 30–50%-wrong rate for
blind reviewer claims. Line numbers drift by a few lines in three places;
the mechanisms are described exactly.

| Claim | Verified |
|---|---|
| `memory_provenance_unverified()` is a zero-LLM raw scan of durable stores | ✅ provenance.py:732 |
| `memory_claims_unverifiable` kept structurally separate from the accusing category | ✅ provenance.py:785 |
| Advisory-only, never touches `status`/`goal_achieved` | ✅ (provenance.py:240 comment states the fail-open rationale) |
| Learning deferred behind the closure/provenance verdict | ✅ `learning_allowed` at handle.py:2596 / **2729** (claimed 2735) / 3058 |
| Contested loops held out — "recorded, but not voting for or against" | ✅ handle.py **2750-2755** (claimed 2754), verbatim log string |
| `contest_lesson` + flat-ledger contested stamp, excluded from retrieval | ✅ knowledge_web.py:953 |
| `times_applied` defect caught empirically — "1/647 live nodes" | ✅ knowledge_web.py:**1850-1857** (claimed 1854), verbatim |
| Tenure gate unchanged: `score >= 0.9 AND sessions_validated >= 3` | ✅ knowledge_web.py:**558-566** (claimed 561); constants at :72-73 are exactly 0.9 and 3 |
| Seed-reader still says "emulate this style and specificity" | ✅ memory.py:267, verbatim |
| Node-promotion gate adopts skills' fail-open contract | ✅ knowledge_web.py:1952/1956 |

Its one-line thesis, kept because it's the right frame: **"you closed the
upstream half of the LeAct gap and left the downstream half alone."**
Trusting the right state–action pairs (the oracle) is built; keeping the
right explanations (Δ) is not.

## The one genuinely new finding

**The seed-reader style-exemplar experiment never became a backlog row,
across two contrasts.** memory.py:267 prepends a high-quality lesson as a
style exemplar to emulate; LeAct Fig 3b found the *no-verbatim* stratum
had higher mean positive Δ — style-copying the teacher produced worse
traces, which is why their backward prompt gives only a redacted
qualitative summary of the expert policy. Called "the cheapest experiment
on the list" in both rounds and actioned in neither.

Sharpening the diagnosis: the seed-reader **has** been modified since
round 1 (it now excludes `minted_from == "prompt"` and contested seeds,
chunk-1 surprise-read fallout). So the code moved and the untested
style-emulation instruction survived the edit. That is precisely how a
caveat gets lost, and it argues for sequencing this **before** the Δ gate
— Δ measured over a style-copied corpus is measuring contamination.

## Prerequisite census — the part neither contrast performed

Round 2 says the remaining work is "a single wire". Checking that against
the tree found **all four Δ-gate ingredients live, one of which our own
backlog still listed as blocking**:

| Ingredient | State | Evidence |
|---|---|---|
| Oracle on the learning path | LIVE | `memory_provenance_unverified` + `learning_allowed` gating (above) |
| Held-out set + pre-registered predictions | LIVE | LT-1 batch CLOSED 2026-08-02, 8 arms, rubric+prediction registered before dispatch, `#6 = FAIL as predicted` |
| Per-call replay corpus | **LIVE — blocker was stale** | 116 run dirs carry `build/calls/`, 20–45 calls each, **including the LT-1 arms** (`01e55212` 20, `2738d9c0` 45, `d9607baa` 30). Records are `{seq, backend, model, prompt, response, tool_events, tokens_in, tokens_out, purpose, error, ts}` |
| With/without harness | LIVE | `src/lens_ablation.py`; `src/compact_ab.py` already does action-match-shaped with/without for one skill |

The third row is the load-bearing correction. The BACKLOG carried
*"build/calls record-mode capture is dead on single-backend boxes, so
replay fixtures need another source"* — true when written 2026-07-31,
made **false on 2026-07-29** by a fix landed under a *different* backlog
item (`build_adapter(auto)` always wrapping in `FailoverAdapter`, the
record-mode wiring row). Nothing connected the two.

Consequence worth stating plainly: **the pre-registered held-out set and
the replay-fixture source are the same runs.** That is a large reduction
in remaining work and neither contrast noticed it, because it required
reading two backlog items against each other rather than reading the
code.

## The revisit trigger had already fired

The LeAct item deferred the build behind a named, measurable condition:
*"don't build while the verdict denominator starves (4 known-arrival
verdict events as of 2026-07-31); revisit when `verdict_flow` shows real
week-over-week verdict arrivals."*

Measured 2026-08-03 (`PYTHONPATH=src python3 -m verdict_flow`):

- **67 judged rows** all-time, up from 4 known-arrival
- arrivals two weeks running — **W31: 18, W32: 9**
- sources: closure 56, provenance 6, deterministic_tests 2,
  now_self_verdict_free 2, closure_reverdict_after_guard_fix 1
- stamped-later record→verdict median **1.4m** (n=21)

The condition we wrote as deciding this was satisfied days before anyone
looked. See the standing-condition row added to `docs/CAPABILITIES.md`
first-party failure corpus — this and the stale blocker above are two
instances of the same defect and the reason an outside reader found them.

## What round 2 missed — recorded so it isn't re-litigated

1. **The surprise-count diagnostic it proposed was already run, and
   refuted its own hypothesis.** Chunk-1 read landed 2026-08-01
   (`docs/history/2026-07-31-lesson-corpus-surprise-read.md`): mirroring
   collapse **REFUTED** — 11/19 flagged, M4 + M12 positive surprise (the
   Δ-gate seed set), six operator-certified contradictions, two routed to
   existing threads. Both surprise reads CLOSED 2026-08-02. Round 2 does
   not mention it; "is it collapsing onto Jeremy" is not an open question.
2. **The tenure direction was already decreed** (Jeremy, 2026-07-31,
   piped 55c877da): calendar decay was "mostly placeholder"; leverage
   successful one-offs rather than requiring 3× independent re-derivation;
   repeat patterns are a better-than-luck signal to EXAMINE, not a tenure
   gate. Round 2 restates the tenure critique as open — the *diagnosis*
   is closed, only the *wire* is open.
3. **Canon-LESSON promotion still has no promote path**, and round 2
   praised its twin without noticing. `get_canon_candidates`
   (knowledge_web.py:1663) surfaces at `CANON_APPLY_THRESHOLD = 10` and
   nothing promotes them; the node-side twin
   (`promote_knowledge_candidates`) shipped 2026-08-02 as battery V3 and
   drew the praise. Newly reachable, too: the 2026-07-29 receipt
   write-back made `times_applied` accrue on lessons for the first time,
   so candidates will now genuinely arrive at a doorless threshold. Filed
   as its own BACKLOG row.

## Fail-open now triples in the same direction

Individually defensible, all three stamping `judged=False` (correct
hygiene); the systemic point is new. Three gates on the learning path now
decline to decide the same way: `skills.py` promote validation,
`artifact_check.py:391/482`, and — landed 2026-08-02 with battery V3,
*after* the fail-open census closed — the node-promotion gate
(knowledge_web.py:1952/1956, explicitly adopting "the same degradation
contract as skill promotion"). A run can accrue promotion credit through
three unjudged passes and nothing counts how often that happens. The
cheap move is the judged-vs-unjudged denominator readout (the `judged`
bit exists at all three), not flipping any gate to fail-closed.

## Sequencing verdict

Build order was right and not by luck: Δ measured over a corpus polluted
by runs that fabricated their own memory writes would have returned
believable noise, and LT-1 #6 says that pollution was real and invisible.

Remaining: **one wire** — a second, effect-based route to LONG beside
`score >= PROMOTE_MIN_SCORE and sessions_validated >= PROMOTE_MIN_SESSIONS`,
scoring action-match over M replays of a captured LT-1 step with and
without the candidate lesson injected.

Order: seed-reader A/B (contamination check, hours) → stratify
rule-vs-reason before reading any aggregate → Δ route to tenure →
competence-redundancy decay.

**Gated on Jeremy** for priority against the SP and world-facts threads,
and a go on the A/B — readiness is measurable, sequencing against work he
is driving is not. `docs/READING_QUEUE.md` row 2026-08-03.

---

## Appendix — source text, verbatim

Reproduced from `~/claude/opus-feedback.txt` (Aug-3 section), which is
machine-local and outside git. Markdown as written; source line
references are the reviewer's own and are adjudicated in the scorecard
above.

> *** Aug 3, 2026 ***
> Jeremy: Changes still in flight, we're not done yet, but made a lot of progress. Mind pulling the repo and re-comparing?
>
> Substantial pull. Let me see the scope, then re-check each specific claim.
>
> 214 commits, +9k lines in src. Let me check my specific claims against the new code.
>
> Pulled — 214 commits, +9.1k in `src/`. The picture changed meaningfully, and in a specific way: **you closed the upstream half of the LeAct gap and left the downstream half alone.** That's the right build order, and I'll explain why at the end.
>
> ## What's genuinely different now
>
> **A silent oracle now watches the learning path.** [`provenance.py`](src/provenance.py) grew `memory_provenance_unverified()` — a zero-LLM raw scan of every durable JSONL store that checks whether a run's claimed persistence actually landed. That's the `claim_verifier` pattern aimed at memory, which is precisely the class I flagged as unmined. The commit's own framing is better than mine: *"the same shape as a bogus 'saved to artifacts/foo.md' file claim, but aimed at memory, where the bill is paid invisibly by a future run."*
>
> Two things I'd single out as unusually well-calibrated:
>
> - **You downgraded the oracle to match its exactness.** Advisory-only, never touching `status`/`goal_achieved`, with the stated reason: *"a guard whose claim side is a regex over prose has not earned that standing however exact its lookup half is."* LeAct's oracles get trusted because they're exact; yours is half-exact, and you priced it accordingly rather than letting it vote.
> - **`memory_claims_unverifiable` kept structurally separate from the accusing category**, so an unverifiable claim can't be laundered into an accusation. That's the failure mode a weaker implementation would have shipped.
>
> **Bad runs can no longer teach.** This is the big one. Learning is now deferred until the closure/provenance verdict is stamped ([handle.py:2735](src/handle.py:2735)), gated on `_closure_audit.learning_allowed`, and contested loops are held out — *"recorded, but not voting for or against"* ([handle.py:2754](src/handle.py:2754)). Previously the pipeline was run → extract → promote-on-restatement with no external check anywhere. Now there's an oracle-anchored veto at the source.
>
> **Retirement-by-contradiction has teeth.** [`contest_lesson`](src/knowledge_web.py:953) plus the flat-ledger `contested` stamp, excluded from retrieval by default, and contested rows get *"sighting counted, nothing else moves."* This materially weakens the ratchet I described last time — a contradiction now retires a lesson rather than needing to out-vote its confirmations. (It's LLM-judged via `CONTRADICTION_CANDIDATE`, with unjudged never contesting, so it's conservative rather than oracle-backed — but the asymmetry I complained about is gone.)
>
> **And you'd already caught the `times_applied` defect empirically.** [knowledge_web.py:1854](src/knowledge_web.py:1854): *"1/647 live nodes ever showed times_applied > 0, and the query-time receipt boost was dead by construction."* You measured it. The new `promote_knowledge_candidates()` gate also isn't the thing I criticized — on *candidates*, `times_applied` counts independent re-derivations, because the injection bump only touches ACTIVE. So my "exposure not effect" critique doesn't apply to that path. Re-derivation is still consensus rather than effect, but it's no longer self-fulfilling.
>
> ## What's unchanged
>
> - **Tiered lesson promotion is exactly as it was**: `score >= 0.9 AND sessions_validated >= 3` ([knowledge_web.py:561](src/knowledge_web.py:561)), `sessions_validated` still incrementing on similarity>0.8 restatement. The tenure route is still consensus, and the ~11-day novelty runway still doesn't lead anywhere different.
> - **The seed-reader still says "emulate this style and specificity"** ([memory.py:267](src/memory.py:267)). The Fig 3b caveat remains untested — still the cheapest experiment on the list.
> - **Fail-open propagated** rather than got fixed: the new node-promotion LLM gate explicitly adopts *"skills' fail-open contract."* Defensible for a cycle you don't want blocked, but it means three gates now decline to decide in the same direction.
>
> ## Why the order was right, and what's now unlocked
>
> Both halves of LeAct matter: trusting the right state–action pairs (the oracle) and keeping the right explanations (Δ). You built the first. Had you built Δ first, you'd have been measuring marginal contribution over a corpus polluted by runs that fabricated their own memory writes — the LT-1 #6 incident says that pollution was real and invisible. The measurement would have returned noise and you'd have believed it.
>
> The thing I didn't expect is that you also built the apparatus. The LT-1 series — *"register corpus, held-out set, rubric and predictions before dispatch,"* then PASS/FAIL against the registered prediction, with `LT-1 #6 = FAIL as predicted`, `Correct my own overclaim`, and `Retract the "LT-1 blockers" claim` — is pre-registration with held-out sets and honest retraction. That's the exact scaffolding a Δ gate needs and the reason it wasn't runnable before: you can't score marginal contribution without a held-out set and a prediction registered in advance.
>
> So the next increment is smaller than it was two conversations ago. You have the oracle, the held-out discipline, the replay corpus, and `lens_ablation`'s with/without harness. What's still missing is a single wire: a retention path to long-tier that runs on measured effect instead of on being said three times.
