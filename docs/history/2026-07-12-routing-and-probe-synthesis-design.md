---
status: record
---

# Routing live-data signal + verifier-synthesis first slice

> **STATUS: BOTH PARTS SHIPPED 2026-07-12.** Part A (needs-live-data routing
> signal) and Part B (B1 Deliverable.shape, B2 shape-conditional MUST +
> waiver logging, B3 probe-env hardening) all landed the same session this
> design was written in. Closed to history rather than kept as a
> `dormant-design` doc since there's no remaining open chunk — see
> MILESTONES.md `-5` #4/#5 for the shipped implementation detail (exact
> line numbers below may have drifted; the design intent and DECISION
> markers are what's durable here). `docs/VERIFY_LEARN_ARC.md` V0's stated
> hard dependency (B3) is now satisfied.

**Status:** design pass, written 2026-07-12 (Fable-handoff session). No code
changed by this pass. Two sibling gaps with one root — *the system's judgment
about what a goal needs (live data, runtime proof) is inferred after the fact
instead of declared up front* — sliced into Sonnet/Opus-sized chunks.
Judgment calls tagged `DECISION (provisional)` — greppable. All file:line
references verified at commit ffff3f6.

Part A closes the routing half of the Manti canonical-case failure
(`docs/CAPABILITIES.md` "The canonical simple case"). Part B is the first
real slice of the long-standing "verifier synthesis / runtime-probe bias"
BACKLOG item (the "scope's other half"), scoped to what pushes the shipped
post-hoc compensators upstream — NOT the full BDD red-green loop.

---

## Part A — needs-live-external-data routing signal

### The problem, precisely

"Where can I get non-ethanol gas in or around Manti, Utah?" routed NOW at
0.85 confidence and answered from model knowledge with a how-to-search list —
the passenger-does-the-steps anti-pattern Jeremy adjudicated "an abject
failure." Three code facts make this structural, not bad luck:

1. **The classifier prompt teaches the misroute.** `_CLASSIFY_SYSTEM`'s NOW
   examples explicitly include *"Quick lookups ('what is the current BTC
   price?')"* (`src/intent.py:93`) — a question that CANNOT be answered
   without live data, listed as the canonical NOW case. The heuristic
   fallback pulls the same direction: `what's the current/latest/today's`
   are NOW patterns (`src/intent.py:144`).
2. **The NOW lane mechanically cannot fetch live data.** `_run_now` is a
   single tool-less completion (`src/handle.py:325-366`, `_NOW_SYSTEM` at
   handle.py:189-193). Live-data questions are un-answerable there by
   construction — this is a *capability* mismatch, the same class as the
   file-deliverable misroute (8ed0a09).
3. **Interactive NOW has no safety net by design.** The self-verdict +
   `now_lane.escalate_on_not_achieved` machinery (a1f472f) only runs on the
   task path (`origin is not None` gate, handle.py:894-895); interactive
   calls keep raw speed. So for the primary human-facing path, routing is
   the ONLY place this can be fixed.

### Design

**A1. In-schema classifier signal.** Add `needs_live_data: bool` to the
`_CLASSIFY_SYSTEM` JSON output (`src/intent.py:102-103`) — the first
auxiliary field in the schema. Definition for the prompt: *true when a
correct answer requires information that changes over time or is locally
situated — current prices/availability/hours, "near me"/named-place
inventory, weather, schedules, recent events — i.e. anything the model
cannot know reliably from training data.* Parse beside lane/confidence
(intent.py:121-128); absent field defaults false (fail-open to today's
behavior).

> **DECISION (provisional):** the signal is an LLM-schema field, not a regex.
> Rationale: the two shipped routing corrections (file-deliverable override
> intent.py:55-67, complex-directive escalation handle.py:202-269) are both
> deterministic post-hoc overrides, and that was right for them — file paths
> and sequencing language are lexical. Live-data-ness is semantic ("what's
> the capital of France" vs "what's gas near Manti" share surface shape);
> the regex version IS today's `_NOW_PATTERNS`, and it points the wrong way.
> The heuristic fallback (intent.py:163-191) gets a *small* lexical
> approximation (below) only because it must work with no LLM.

**A2. Capability override, same template as 8ed0a09.** In `classify()`
directly after the file-output override (intent.py:55-67):
`if lane == "now" and needs_live_data → ("agenda", max(confidence, 0.8),
"Needs live external data — NOW lane cannot fetch it")`. Applies to
interactive AND task paths — it's routing, not verdict machinery, so the
interactive raw-speed contract is untouched (a fast wrong answer isn't
speed).

**A3. Fix the teaching examples.** Rewrite `_CLASSIFY_SYSTEM`'s NOW example
to a stable-knowledge lookup ("what does HTTP 429 mean?"); move
current-price/availability asks to the AGENDA examples with the live-data
rationale. Flip the `what's the current/latest/today's` heuristic patterns
(intent.py:144) from NOW-indicators to AGENDA-indicators under the same
config flag — they are literally live-data phrasing.

**A4. Config + defaults.**

> **DECISION (provisional):** `now_lane.live_data_routing`, code-default
> **ON** — including fresh installs. This deliberately differs from
> `escalate_on_not_achieved` (default OFF, no-silent-spend): that flag
> re-runs a goal a user already got an answer for; this one routes the goal
> where it can be answered at all *before* spending anything on a doomed
> NOW call. The counter-argument (AGENDA costs more than NOW) is real but
> is an envelope problem — the Manti envelope work (cuts-first, ladder
> breaker, boot-tax trims) is the mitigation, and a wrong answer at any
> price violates the UX contract. Flag exists for reversal; DEFAULTS.md row
> with this reasoning; Jeremy can flip the fresh-install posture at review.

### Acceptance

- The Manti canonical case routes AGENDA **naturally** (no `--lane` force) —
  update the CAPABILITIES.md catalog row (currently `target`, "routing +
  cost envelope still fail the contract") when it does.
- Stable-knowledge NOW lookups still route NOW: `TestLiveDataOverride` in
  `tests/test_intent.py` beside `TestFileOutputOverride` (:44) — cases both
  directions + absent-field default + heuristic-fallback flip.
- Observability free: the override's reason string lands in the routing
  decision like 8ed0a09's does.

**Chunk sizing: one Sonnet session** (schema field + override + prompt
examples + heuristic flip + DEFAULTS row + tests).

---

## Part B — verifier-synthesis first slice (probe honesty)

### The problem, precisely

Closure keeps blessing (or failing) runs on evidence it never gathered
honestly. Three mechanisms, each now located:

1. **The bias asymmetry.** `_CLOSURE_PLAN_SYSTEM` says behavioral probes are
   *"Prefer[red]"* (closure_verify.py:48-57) but fast/safe/read-only is a
   *MUST* (closure_verify.py:70-71) — the LLM resolves the tension by
   emitting greps. The slycrel-go specimen: `modality_distribution={"static":
   4, "process": 1}`, zero http/ws, on a goal explicitly about a server with
   a browser client — while three curls would have settled it in 5 seconds.
2. **The compensator is post-hoc.** `_detect_behavioral_gap`
   (closure_verify.py:1012-1070) correctly downgrades all-static blessings of
   runtime work — but it can only *veto*, producing false-ish negatives,
   never the missing probe. And its runtime/document discriminator is keyword
   inference over deliverable prose (`_deliverables_corroborate_runtime`,
   closure_verify.py:1073-1094) — the c37f42e over-fire ("Proposal violates
   process logic" matching `\bprocess\b`) was this inference misfiring.
3. **The wrong-cwd class survives at one seam.** Probes run via
   `subprocess.run(..., cwd=cwd)` (closure_verify.py:648-651) where the cwd
   chain (closure_verify.py:472-497) can still resolve to `None` → **the
   probe inherits Maro's launch cwd** (closure_verify.py:640). This is the
   dominant false-verdict source: 4/5 dogfood closures false-negatived on
   wrong-cwd/privilege verifier errors while the work was correct
   (`docs/history/2026-07-09-decisions-for-jeremy.md` D1/E6), and it's the
   named fix lever from the done-vs-achieved analysis ("probe-env hardening,
   not threshold tuning").

### Design — three chunks, independently shippable

**B1. `Deliverable.shape` becomes first-class.** Add
`shape: "document" | "runtime" | "data"` to `Deliverable`
(src/scope.py:134-154), declared by the LLM at scope time (one field in the
deliverable line format, `_parse_deliverable_line` scope.py:241-266; absent
→ `None`). `_deliverables_corroborate_runtime` consults declared shapes
first and keeps the keyword regex ONLY as fallback for legacy/unshaped
deliverables. The closure plan prompt's deliverables block states each
deliverable's shape so plan synthesis sees it too.

> **DECISION (provisional):** three values, not two — `data` (a dataset/
> ledger/index that should be probed by content queries, not process
> liveness) is distinct from `document` in probe vocabulary, and collapsing
> it into either neighbor reproduces a known error direction (content-free
> "file exists and non-empty" checks on data deliverables).

**B2. Plan-prompt rebalance: shape-conditional MUST.** For any
runtime-shaped deliverable, ≥1 behavioral probe (http/ws/process/browser
modality) becomes a **MUST**, with an explicit escape hatch: the plan may
omit it only by stating why it's impossible in this environment, and that
waiver is logged (`behavioral_probe_waived: <reason>` beside
`modality_distribution` in the CLOSURE_VERDICT event, closure_verify.py:891).
Simultaneously soften the speed MUST for behavioral probes: allow up to the
real `timeout_per_check` (30s — the prompt's "<15s" already disagrees with
the code, closure_verify.py:443 vs :70) and keep <15s as guidance for static
checks. The existing scaffolding examples (boot-in-background + curl +
cleanup trap, closure_verify.py:48-57) stay — they're good; the MUST is what
was missing.

**B3. Probe-env hardening.** (a) Never execute checks with `cwd=None`: if
the resolution chain (closure_verify.py:472-497) ends empty, mark all checks
`inconclusive` with an `env_unresolved` marker instead of running them
somewhere arbitrary — an honest "couldn't verify" beats a confident verdict
from the wrong directory. (b) Environment-error confidence cap: when >half
of executed checks are inconclusive for environment reasons (the
`_check_outcome` signatures, closure_verify.py:317-345: not-found, exit
126/127, permission-denied, verifier-authored SyntaxError), cap verdict
confidence below the 0.7 demotion threshold and say so in the summary — the
verifier's own tooling failure must not fail the goal (the D1 monitor_diagnose
specimen: correct diagnosis failed at 0.25 because closure couldn't run
privileged journalctl).

> **DECISION (provisional):** the full BDD red-green loop (synthesize probe →
> break code on purpose → confirm probe catches it → fix → green) stays
> deferred. B1-B3 are the honest-measurement prerequisites: until probes run
> in the right directory and runtime work gets runtime probes, a red-green
> pair would be theater on bad plumbing. Revisit after modality/false-negative
> numbers move.

### Acceptance / measurement (all riding shipped instrumentation)

- `modality_distribution` on runtime-shaped goals: zero all-static blessed
  closures (Signal: `behavioral_gap_downgrade` events should approach zero
  because the gap stops being planned in, not because detection weakened).
- False-negative rate on the dogfood corpus class: the 4/5 wrong-cwd false
  negatives (runs in `docs/history/2026-07-09-decisions-for-jeremy.md` D/E)
  re-verified against B3 behavior — expected: inconclusive-capped, not
  failed.
- Tests: `tests/test_director.py` (`TestDetectBehavioralGap` :773,
  `TestVerifyGoalCompletion` :951 — extend, don't fork), `tests/test_scope.py`
  for B1 parsing, new `TestProbeEnvHardening` beside the cwd fallback test
  (test_director.py:973).

**Chunk sizing: B1 one Sonnet session; B2 one Sonnet session (prompt + waiver
logging + tests); B3 one Opus session** (verdict-confidence semantics deserve
the stronger model; it touches the honesty of every closure).

---

## Explicit non-goals (this pass)

- No new lanes, no errand lane (Qix-cuts decree stands — routing fixes route,
  planning taste lives in cuts).
- No verifier LLM-tier changes, no threshold tuning (done-vs-achieved verdict:
  keep 0.7).
- No red-green/mutation machinery (deferred above).
- Part A does not touch the NOW self-verdict or its defaults — the interactive
  gap is closed by routing, the task-path net stays as shipped.
