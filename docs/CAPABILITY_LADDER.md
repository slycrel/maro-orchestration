---
status: living
---

# Capability ladder — checkpoints and the bridges between them

**What this is:** the progression map. `docs/CAPABILITIES.md` is the goal
well — real asks, as phrased, each marked `verified` / `target` /
`aspirational`. This doc says what those goals *add up to*, in what order,
and what has to be built to get from one checkpoint to the next.

Opened 2026-08-01 as BACKLOG item **LT-2** (Jeremy: the ladder gets its own
doc; CAPABILITIES.md stays the goal well).

**Vocabulary is inherited, not invented.** `COMPOUND_THINKING_DESIGN.md` §8
already names this shape: capability edges are the only edges that persist
off the map, and *"the tech tree IS the skill library / evolver."* A level-up
paid once amortizes across the whole goal family. Every rung here is a
tech-tree node; the top rung of every ladder is a **skill capture**, because
that is what makes the rung amortize instead of evaporate with the run that
earned it.

---

## The two rules that keep this honest

**1. A failure is never a success.** (Jeremy, decree, 2026-08-01.) "An
expected failure is just a goal we haven't engineered (or learned) to solve
yet, not a success." No rung is passed by declining. A goal whose only
success condition is a refusal gets reframed with a positive deliverable —
usually *the evidence trail* rather than the verdict alone. A genuine miss is
recorded `not-achieved` and becomes the next rung's target.

**2. Claimed ≠ probed.** Inherited from CAPABILITIES.md. A checkpoint is
passed only when its goals read `verified` with a run pointer. `target`
means we believe it works and haven't shown it. A checkpoint made of
`target` rows is a checkpoint we have not reached.

---

## Checkpoints

Each checkpoint is a small named goal set. The goals live in
CAPABILITIES.md; this table says which ones constitute the rung and what
capability the rung actually buys.

| | Checkpoint | What it means | Passed when |
|---|---|---|---|
| **C0** | Know what you don't know | Answers honestly at the edge of knowledge; distinguishes "I retrieved this" from "I recall this"; says "unable to verify" *with the trail that shows the attempt* | Long-tail entity goals and unsourceable-claim goals both deliver evidence trails, not fabrications and not bare refusals |
| **C1** | Fetch & ground | Gets a real document and answers *from it* — paywalls, JS, PDFs and 403s handled or honestly reported | Quote-the-doc and read-this-page goals verified |
| **C2** | Triangulate & resist fabrication | Multiple sources, disagreement adjudicated, every URL validated before it ships | Multi-source errand research verified end to end, incl. the cost envelope |
| **C3** | Execute & check | Claims about code/data are executed, not asserted; claimed-vs-actual diffed automatically | Iterate-until-output and schema-extraction goals verified |
| **C4** | Persist & reuse | A lesson learned in run A measurably changes run B; a working access path becomes a skill | Cold/warm delta shows a real difference attributable to a named lesson or skill |
| **C5** | Self-direct across ticks | Standing work that fires on transition, escalates rarely and correctly, and compounds across days | Multi-tick watch goal verified across ≥2 real transitions |

**Where we actually are (2026-08-01):** C0–C3 are mostly `target` — believed
covered, largely unproven, which is exactly what the LT-1 batch exists to
settle. **C4 is instrumented but unproven**, and until 2026-08-01 was not
even measurable: the cold/warm attribution rail (`recall_citations.json`,
`skills_manifest.jsonl`) recorded nothing when nothing was injected, so a
cold run and a broken run were indistinguishable on disk (BACKLOG LT-0).
C5 has one `target` row and no lived example.

**C4 is the real prize and the real risk.** Everything below it is
capability a good single-shot model plus tools can approximate. C4 is the
first rung where *orchestration* — memory that survives the run — is doing
work nothing else can do. It is also the rung whose evidence we have been
worst at collecting.

---

## Ladders: the bridges between checkpoints

A checkpoint is a state; a ladder is how you climb to it. Each rung is a
small ask that is useful on its own and strictly easier than the one above.
**The last rung of every ladder is the skill capture** — the tech-tree node
that makes the climb permanent.

### Ladder A — read a webpage quickly and efficiently (C1 → C2)

Jeremy's worked example, 2026-07-31: *"reading a webpage quickly and
efficiently is something that people are generally going to want to do;
that's a mixed bag."* Mostly built, never assembled as a ladder.

| Rung | Ask | Status |
|---|---|---|
| A1 | Fetch one URL and return what it actually says | partly built (`web_fetch`, jina reader rung); paywall/JS/PDF/403 behavior unproven |
| A2 | Answer **one specific question** from a page — without summarizing the whole thing | `target` (`skills/web_extract.md`) |
| A3 | "Is this worth my time?" — a ~2-min opinionated read, not a claims matrix | **`verified` 2026-07-17** — the one rung genuinely done |
| A4 | Triangulate the claim across sources; HTTP-validate every URL before it ships | `target` — corpus 1.5 (100% fake links) is the failure this closes |
| A5 | **Capture the working access path as a reusable skill** | `target` — done by hand for Reddit/X (`skills/social_search.md`); the target is Maro doing it itself |

A3 being the only `verified` rung is worth noting: it was verified because a
real ask arrived and was run, not because it was planned. That is the
pattern the whole catalog is built on.

### Ladder B — ground an answer in the here and now (C1 → C2)

| Rung | Ask | Status |
|---|---|---|
| B1 | Answer a question about a *named place* using live sources | `target` — the Manti canonical case; content verified, cost envelope still fails |
| B2 | Prefer the official/primary source over the aggregator, and say which you used | `target` |
| B3 | Adjudicate when sources disagree, and show the disagreement | `target` |
| B4 | Capture the source-preference ordering as a reusable skill | not started |

### Ladder C — see your own work (C3 → C4)

The introspection ladder. Everything here was structurally blocked until
2026-07-18 and is still unproven end to end.

| Rung | Ask | Status |
|---|---|---|
| C1r | Read your own run record and report what happened | `target` — needs in-container record access |
| C2r | Diagnose *why* a run ended the way it did, from evidence not narration | `target` |
| C3r | Propose a change, with every premise verified before acting | `verified` shape (self-speedup dogfood — the verify gate IS the use case) |
| C4r | Apply it, then verify the change actually helped | shipped machinery (VERIFY_LEARN_ARC V2/V3), unproven on a live arc |

### Ladder D — remember across runs (C4)

**The load-bearing ladder.** This is the one the LT-1 batch measures.

| Rung | Ask | Status |
|---|---|---|
| D1 | Record what was injected into a run — which lessons, which skills | **fixed 2026-08-01** (LT-0); coverage was 10%/55% because absence and emptiness were the same state |
| D2 | Correct a fact mid-run; the correction survives to a later run | untested — corpus 4.5 is the failure this closes |
| D3 | A lesson learned on goal A measurably helps goal B in the same family | untested — the cross-goal transfer prize |
| D4 | The system proposes the skill capture itself, unprompted | `aspirational` |

D1 is not a capability a user would ever ask for. It is on the ladder
because **D2 and D3 cannot be measured without it** — a rung that exists to
make the rungs above it observable.

---

## How a rung gets promoted

Same discipline as the catalog, plus one addition:

1. Run the ask. Real phrasing, not cleaned up.
2. Judge it against the rung's stated deliverable — **never against a
   refusal**. If it declined, it did not pass.
3. `verified` needs a date and a run pointer. Anything else stays `target`.
4. **A miss is written down as the next bridge**, not as a failed test. It
   names a capability edge; that is a finding, not a defeat.

## What this doc is not

Not a roadmap and not a queue — MILESTONES.md owns ordering. Not a design
doc: each rung's *how* lives in its own design doc or skill file. This is
the map that says which chasm we are standing at and what the bridge would
have to be.
