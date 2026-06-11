# Navigator Decision Schema (goal-brain sequencing, step 4)

**Status:** pinned 2026-06-11. Types live in `src/navigator.py` (schema + validation
only — no decision logic, no callers; the prompt is step 5).
**Upstream:** `GOAL_BRAIN.md` (step 1), pressure-test findings (step 2),
`docs/RECALL_DESIGN.md` (step 3 — the navigator slice contract this consumes).
Sequencing per `docs/conversations/2026-05-18-memory-and-goal-brain.md`:
artifact → recall() → **schema** → prompt.
**Downstream:** the navigator prompt (step 5) emits this schema's envelope; the
shadow harness (step 5) records the instrumentation tuple defined here.

This is `THREAD_ARCHITECTURE.md` Open Decision #1, the one flagged "most
load-bearing artifact." Everything here is a v1 pin under the make-a-call
invariant — reversible, and expected to be reshaped by shadow-mode data.

---

## The turn contract

Every thread turn is `navigator → work → navigator`. The navigator receives a
`NavigatorInput`, returns one `NavigatorDecision`. The work LLM advises (its
recommendation is data); the navigator decides (its move is authority).

```
NavigatorInput ──▶ navigator (LLM, tiered) ──▶ NavigatorDecision ──▶ turn runner
                                                      │
                                              NAVIGATOR_DECIDED event
                                  (state digest, decision, tier — the gbrain-style
                                   tuple; outcome + downstream signal join later)
```

## The moves (six) + the admission (one)

| Move | Meaning | Work turn fired? |
|---|---|---|
| `extend` | The thread needs another **thinking** turn: produce or refine an artifact — plan, scope, resolved intent, open-questions list. Mutates the thread's understanding, not the world. | yes (plan-shaped) |
| `execute` | Do the next concrete piece of work: run a tool, write code, fetch, send. Mutates the world. | yes (execution-shaped) |
| `fork` | Spawn N child threads, each with its own goal + context. v1 join is **sync** (parent turn resumes when children settle). | no (spawns children) |
| `collate` | Fire a work turn that consumes named children's artifacts and synthesizes one. A collate can fail, retry, or further fork — it's a normal turn. | yes (plan-shaped) |
| `close` | The thread ends: deliverable landed, or the thread is deliberately set aside. Requires a closure type and an explicit disposition for **every open child** (see below). | no |
| `escalate` | Deliberate hand-up to Director/human: genuine ambiguity, conflicting goals, irreversible action, or exhausted idunno chain. Carries a specific question + options considered. | no |
| `idunno` | **Not a move — an admission.** "I cannot decide this turn." Never reaches the turn runner as an action; the harness re-runs the *same input* at the next tier (Haiku → Sonnet → Opus). Top tier still idunno → converts to `escalate` with the accumulated confusions as context. | no |

Two distinctions worth their cost:

- **`escalate` ≠ `idunno`.** Escalate is a confident decision that a human is the
  right next step (the navigator *knows* — e.g. "this deletes production data").
  Idunno is honest uncertainty about what the next step even is. Conflating them
  either wastes expensive tiers on decisions a cheap tier correctly knows it can't
  make alone, or sends "I'm confused" to the user dressed as a question.
- **`extend` ≠ `execute`.** Both dispatch the work LLM, but extend writes into the
  thread's understanding (`source/`, `build/`) and execute changes the world. The
  Tesla pushback lives here: planning isn't forced (today's bug) and isn't deleted —
  it's `extend`, picked when warranted. The split also gives instrumentation the
  plan/act ratio per thread for free.

What is deliberately **not** a move: `retry` (an `execute` with adjusted
instruction — say so in `reasoning`), `wait` (no async join in v1; sync fork makes
it meaningless), `delegate-to-director` (that's `escalate`; Director has two
callsites, not a per-turn role).

## NavigatorInput — what the navigator always sees

Pinned fields (dataclass in `src/navigator.py`):

| Field | Type | What / why |
|---|---|---|
| `goal` | str | The thread's goal text, verbatim. |
| `goal_brain` | str | The thread's goal-brain content — the intent anchor, injected **whole, every turn**. This is the intent-preservation mechanism ("we're not escaping LLM trust, we're redistributing it"). v1 stand-in: scope.md / resolved_intent.md / project GOAL_BRAIN.md until per-thread goal-brains exist (open question, step 5+). |
| `thread` | dict | Ancestry — `ThreadIdentity` shape from recall() (parent goal, handle chain, source). |
| `turn_index` | int | 0-based turn counter for this thread. |
| `last_work` | WorkReport \| None | What the last work turn returned (None on turn 0). |
| `open_children` | list[ChildSummary] | **Every child not yet dispositioned, always present, never elided.** |
| `recall_block` | str | `recall(slice="navigator")` output — lessons, rules, decisions, knowledge, prior attempts (contract in RECALL_DESIGN.md). |
| `budget` | dict | Spent vs caps: tokens, cost, wall-clock, turns. The navigator sees burn rate; running hot is a legitimate reason to close or escalate. |
| `constraints` | str | Scope / completion standard / standing constraints, where present. |

`WorkReport` — the 2026-06-10 visibility decision made concrete
(recommendation + structured signals by default, full output pullable):

```python
@dataclass
class WorkReport:
    move: str             # which move produced it: extend | execute | collate
    status: str           # ok | failed | partial
    summary: str          # work LLM's compact self-report
    recommendation: str   # advisory: "done", "this should fork", "need X"
    signals: dict         # structured facts: artifacts written, errors, cost, duration
    output_ref: str       # path to full output on disk — pull on demand, not injected
```

`ChildSummary` — the fan-out lesson ("we'd follow one thread of many and never
go back and revisit") as a schema rule, not a prompt hope:

```python
@dataclass
class ChildSummary:
    handle_id: str
    goal: str
    state: str            # open | done | failed | abandoned
    artifact_ref: str     # "" until the child lands one
```

Unfinished children are structurally impossible to forget: they ride in every
`NavigatorInput`, and `close` validates against them (below). This is the runtime
half of the GOAL_BRAIN "fan-out recoverability" open question — visibility is
solved at the schema layer; *revisit policy* stays judgment (prompt, step 5).

## NavigatorDecision — the return envelope

One flat JSON envelope for every move including idunno. One shape because the
prompt (step 5) has to emit it reliably and the parser has to be boring:

```json
{
  "move": "execute",
  "reasoning": "one short paragraph — why this move, why now",
  "confidence": 0.8,
  "payload": { ...move-specific, see below... }
}
```

- `reasoning` is **required and non-empty** for every move. It is the heart of the
  instrumented tuple — the crystallization substrate. A decision without reasoning
  is unlearnable-from.
- `confidence` ∈ [0,1]. Low confidence is *not* idunno — it's a decision made under
  uncertainty, and the calibration signal we'll want later (decisions at 0.4 that
  keep working out = a tier that's better than it thinks; the reverse = drift).

Per-move payload (required keys validated in code):

| Move | Required payload keys | Optional |
|---|---|---|
| `extend` | `instruction`, `expected_artifact` | `persona`, `tier` |
| `execute` | `instruction` | `expected_artifact`, `persona`, `tier` |
| `fork` | `children`: list of `{goal, context}` (1–8) | per-child `persona` |
| `collate` | `instruction`, `child_handle_ids` (non-empty) | `persona`, `tier` |
| `close` | `closure` (delivered \| abandoned \| superseded \| folded_into_parent), `verdict` | `artifact_summary`, `children_disposition` |
| `escalate` | `question`, `why` | `options` (list) |
| `idunno` | `confusion` | `missing` (list — what info would unblock) |

**The close rule (schema-enforced):** a `close` decision is invalid unless
`children_disposition` maps **every** `open_children` handle_id to
`done | abandoned | absorbed`. You cannot close a thread while a child dangles
undispositioned. Abandoning a child is allowed — silently forgetting one is not.
This is the single hardest constraint in the schema and it's deliberate: it is
the 2026-05-18 fan-out lesson converted from a meta-constraint into a validator.

Fork cap of 8 children is a made call (runaway-fan-out backstop, same spirit as
the recall guard); config later if data argues.

## Tiering and the idunno chain

The envelope is tier-agnostic. The harness owns the chain:

1. Run navigator at tier N with `NavigatorInput`. Record `(input_digest, decision, tier)`.
2. `move == "idunno"` → re-run the **same input** at tier N+1, appending prior
   confusions to the input (they're data, same as a work recommendation).
3. Top tier idunno → synthesize an `escalate` whose `question` is built from the
   accumulated `confusion`/`missing` fields. Mark it `escalated_via: "idunno_chain"`
   in instrumentation so deliberate escalates and exhausted chains are separable
   in the data.

## Instrumentation — the tuple, from day one

Every navigator invocation (shadow or live) emits one `NAVIGATOR_DECIDED`
captain's-log event (visibility + crystallization substrate, per the demotion
audit — nothing reads it for control flow):

```
NAVIGATOR_DECIDED {
  turn_index, tier, move, confidence,
  input_digest: {goal_preview, turn_index, open_children, has_last_work,
                 last_work_status, recall_chars, goal_brain_chars, budget},
  reasoning, payload_digest, elapsed_ms,
  shadow: true|false,
  pipeline_actual: <what the static pipeline did, when shadow>  # divergence signal
}
```

This is the `(thread_state_snapshot, navigator_decision, outcome,
downstream_signal)` tuple from the 2026-05-18 decision — state as digest (full
state already lives in the run dir), decision in full, outcome and downstream
signal joined later by turn linkage. Static now, crystallize later.

## v1 runs in shadow mode — the navigator does not get the wheel

The pin that keeps this fix-in-place instead of rewrite-by-stealth: **the first
navigator deployment is decide-only.** At existing pipeline decision points
(dispatch, post-step, closure), the shadow harness builds a `NavigatorInput` from
what the pipeline already knows, asks the navigator, logs the decision *alongside
what the pipeline actually did*, and changes nothing. Divergence between
navigator-said and pipeline-did is the cheapest possible evaluation data:

- High agreement on a decision class → the navigator is ready to own that class
  (and conversely, the pipeline's hard-coded behavior there was already fine).
- Systematic divergence → either the navigator is wrong (fix prompt) or the
  pipeline is (we just found a bug with a price tag on it).

The work LLM's `recommendation` field already exists in spirit (closure verdicts,
step results); the shadow harness adapts what's on disk rather than asking
anything new of the loop. Cutover happens per decision class, earned by shadow
agreement — never big-bang. This mirrors how the dispatch guard shipped: advisory
injection first, enforcement on the narrow path the data justified.

## Worked examples (sanity check against real history)

**The 2026-05-17 repeat burn** (same goal, ~25 runs / 35 min): turn 0, navigator
sees `recall_block` with "24 prior attempts, all stuck/error" and `budget` showing
burn. Correct move: `escalate` ("this goal has failed 24 times in 35 minutes —
approach is wrong, options: change approach / drop / human input"), not a 25th
`execute`. The dispatch guard (step 3) already hard-codes the floor of this
judgment; the navigator is its judgment-shaped generalization — the guard stays
as the deterministic backstop (mechanics in code, judgment in language).

**Reddit/Marketplace/Craigslist** (THREAD_ARCHITECTURE's fan-out): turn 0 →
`fork` with 3 children `{goal: "check <site> for X", context: item details}`.
Children execute and close. Parent resumes: `open_children` all `done` →
`collate` over the 3 artifact refs. Collate's WorkReport ok → `close`
(`closure: delivered`, disposition: all 3 `done`).

**One child dies** (same fan-out, Craigslist child `failed`): parent navigator
sees `state: failed` in `open_children`. Its options are all legal and all
judgment: `execute` (retry the lookup itself), `fork` (respawn the child),
`collate` over the two survivors, then `close` with the failed child
dispositioned `abandoned` — *"partial results: 2 of 3 sources."* What it cannot
do is close without saying which. (THREAD_ARCHITECTURE Open Decision #2 — fork
failure semantics — is hereby resolved at the schema layer: failures stay
visible, disposition is mandatory, *policy* is the navigator's call per turn.)

## What this is not

- **Not the prompt.** Step 5. The schema is what the prompt must emit; nothing
  here tells the navigator *when* extend beats execute — that's judgment and it
  lives in language, not in this file.
- **Not a runner.** `src/navigator.py` holds types + validation + parsing only.
  Building the turn loop before the prompt exists would invert the sequencing.
- **Not new authority.** Shadow mode changes no behavior. The existing pipeline
  keeps the wheel until shadow data earns cutover, class by class.

## Step-5 outcome — prompt + shadow replay round 1 (2026-06-11)

The prompt lives in `src/navigator_prompt.py` (`SYSTEM_PROMPT` + `render_input` +
`decide()` with the tier chain); the replay harness in `src/navigator_shadow.py`
(`python3 -m navigator_shadow <handle-id>... --point dispatch|closure|both`).
Replay rebuilds recall context **as of the run's start time**, so the navigator
sees what the system knew then, not now.

Seven decisions over five historical runs (live LLM calls, cheap→mid→power chain):

| Run | Point | Pipeline did | Navigator said | Tier | Verdict |
|---|---|---|---|---|---|
| `e0760af0` "Read CONSTRAINT_ORCHESTRATION_DESIGN.md, note open questions" | dispatch | execute (done) | `execute` 0.95 | cheap | agree |
| `e0760af0` same | closure | ended done | `close` delivered 0.88 | cheap | agree |
| `d5136cef` "Adversarial verification…" (the 2026-05-17 burn, 8 prior attempts, 6 stuck) | dispatch | execute (stuck) | `escalate` 0.85 — "well above the three-failure threshold… systemic blocker" | cheap | navigator right |
| `d5136cef` same | closure | ended stuck (era behavior: requeue verbatim) | `escalate` 0.92 | cheap | navigator right |
| `711ab9a9` goal `[after:1]` (decompose-chop fragment) | dispatch | execute (error) | cheap **idunno** → mid `close` abandoned 0.88 — "sequencing marker, not a task… spawn artifact" | mid | navigator right, chain worked |
| `25490296` "Rate each finding: strong/moderate/weak/contested" (fragment, 8 prior) | dispatch | execute (error) | `escalate` 0.95 | cheap | navigator right |
| `fe22dd61` "Read the captured output and identify 3-5 core dimensions (when to persist" (truncated) | dispatch | execute ("done") | cheap **idunno** → mid `escalate` 0.88 — caught the mid-clause truncation | mid | navigator right |

What round 1 establishes:
- **Every divergence favored the navigator**, and each one maps to a documented
  failure class from the step-2 pressure test (repeat burn, chopped fragments,
  missing referents). The cheap tier correctly applied history-outranks-optimism
  on its own; the `[after:1]` root-cause read ("spawn artifact… re-enqueue with
  the actual goal text") came from a Haiku-class admission plus one Sonnet-class
  turn.
- **Tier economics work as designed**: 5/7 decided at cheap, 2 needed mid (both
  after an honest cheap-tier idunno), 0 reached power. The idunno chain fired
  twice and converted confusion to a decision both times.
- **Known bias, do not over-read**: this panel was hand-picked to include the
  known-bad runs. It shows the navigator catches the failure classes we already
  knew about; it does NOT measure the false-escalate rate on healthy goals — a
  navigator that nannies every legitimate goal into escalation would be worse
  than the pipeline. Round 2 must be a *random* sample, and over-escalation is
  the specific thing to count.

## Shadow round 2 — random sample, false-escalate rate (2026-06-11)

Seeded random sample (seed 42), stratified 12 done / 4 stuck / 4 error from the
459 eligible runs, excluding round-1's five. Dispatch point, 20 live decisions.
Moves: 8 escalate, 6 extend, 4 execute, 2 close. Tiers: 16 cheap, 4 mid, 0 power.

**The question round 2 asked: does the navigator nanny healthy goals?**

- The sample contained 6 clearly well-formed goals ("Read
  docs/ADVERSARIAL_VERIFICATION.md…", "Write docs/research/
  productive_persistence.md following the README format…", "Research
  zoom-in/zoom-out metacognition…", "Read artifacts/persistence_recall.txt and
  identify unaddressed sources…", …). **False escalates on these: 0/6.** Four
  drew `execute`; two drew `extend` (one planning turn — mild conservatism on
  under-scoped research/update goals, not a human interrupt).
- **All 8 escalates targeted debris or burn**: chop fragments ("md [after:7]",
  "[after:1]" with 16/19 prior errors, "Define success criteria" with no
  referent, "md files and verify each claim…"), or referent-less plan steps with
  long failure histories.

**What the random sample exposed that the biased panel couldn't:** 11 of 20
goals were decompose-chop fragments or referent-less plan steps — *including
most of the pipeline-"done" ones*. Pipeline `done` is not ground truth for goal
health ("py design implications in a final section" ran to "done" twice in the
sample). The navigator independently flagged the debris class every time
regardless of recorded status — the step-2 pressure-test finding, reconfirmed
at random-sample scale.

Emergent behaviors worth keeping an eye on (all desirable, none prompted for
explicitly):
- **Dedup via recall**: a goal with 4 prior `done` runs drew `close` ("the goal
  was delivered") instead of a 5th execution — dispatch-time dedup falling out
  of history-injection.
- **The chain corrects in both directions**: on "Read
  artifacts/persistence_recall.txt…", cheap idunno'd (couldn't confirm the file
  exists) and mid overrode with `execute` plus a locate-then-read plan —
  tier escalation isn't a caution ratchet.
- **Honest confidence**: one debris escalate came in at confidence 0.05 — the
  field is being used as designed, not pegged high.

Verdict: the dispatch decision class is strong enough to earn the next
integration step (live shadow at dispatch, still decide-only). Cutover still
waits on live-shadow agreement data.

## Live dispatch shadow (wired 2026-06-11)

`navigator_shadow.shadow_dispatch_live()` is called from `handle_task()`'s
autonomous requeue path, immediately after the dispatch-guard verdict. It is
decide-only and failure-isolated (never raises, never alters dispatch); the
guard remains the deterministic floor. Mechanics:

- **One recall, two consumers** — handle_task computes the dispatch-slice
  `RecallResult` once; the guard takes `dispatch_signals()` from it, the
  shadow takes the thread identity + context block. No extra file scanning
  per dispatch.
- **`pipeline_actual.move_equivalent`** is the guard verdict: `"execute"`
  (dispatch proceeded) or `"guard_refused"` (guard blocked it). With
  `live: true` plus `job_id`/`source`, every NAVIGATOR_DECIDED row is a
  navigator-vs-pipeline agreement datapoint.
- **Config-gated, off in code** — `navigator.shadow_dispatch` defaults False
  (a model call per dispatch is real spend); this box opts in via
  `~/.poe/workspace/config.yml`. `navigator.shadow_tiers` defaults
  `["cheap"]`: live shadow wants decision volume, not chain depth — a cheap
  idunno is recorded as the synthesized escalate (`escalated_via:
  "idunno_chain"`) and is distinguishable in analysis.
- Skipped on `dry_run`, same as the guard (previews burn nothing).

Smoke-verified live 2026-06-11: real cheap-tier call returned `execute` at
0.92 on a well-formed goal; NAVIGATOR_DECIDED landed in the workspace
captain's log with `pipeline_actual.live: true`.

Analysis query, when enough rows accumulate (the `*` spans rotated archives —
the log rotates at 5MB as of 2026-06-11):
`cat captains_log*.jsonl | grep NAVIGATOR_DECIDED | jq 'select(.context.pipeline_actual.live)'`
— agreement rate per move class is the cutover evidence.

## Open ends carried forward

- **Per-thread goal-brain creation** — `NavigatorInput.goal_brain` assumes one per
  thread; today only the project's own exists. v1 stand-in: scope/resolved-intent.
  Real answer is a step-5+ design (likely: seeded at thread kickoff from goal +
  scope, system-maintained sections updated at close).
- **Async fork join + `wait`** — deferred until a real thread needs it; sync join
  covers the worked examples we have.
- ~~**Shadow round 2: random sample, count over-escalation**~~ — done same day,
  results above: 0/6 false escalates on well-formed goals; all 8 escalates
  targeted debris/burn.
- ~~**Live shadow callsite**~~ — wired 2026-06-11 (section above):
  `shadow_dispatch_live()` from handle_task, sharing the guard's recall,
  config-gated off in code. Next: accumulate live agreement data, then the
  per-class cutover discussion.
- **Fork cap (8), confidence semantics** — made calls; revisit against
  NAVIGATOR_DECIDED data.
