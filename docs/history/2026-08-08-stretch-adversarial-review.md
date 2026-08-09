---
status: record
---

# Adversarial review — go-nuts stretch (2026-08-08)

Cross-model review (3 codex reviewers: Skeptic / Architect / Minimalist)
over the six stretch chunks: 26d3b58 (re-mint tombstones), aebf844
(inspector cadence), 42417df (node promotion age+content), a87348f
(match-tier telemetry), bc613de (skill pedigree), 97bb780 (world-facts
slice 1). Every finding verified against code before acting
(verify-before-fix; historical hallucination base rate ~30–50% — this
round ran unusually true: 9 of 12 confirmed).

## Intent

Whether the six chunks achieve their stated designs well — not whether
the designs are right (those were Jeremy's decided asks).

## Verdict: CONTESTED → fixes applied same-session

Three HIGHs were real and are fixed; two findings rejected as
misreadings of scope; one accepted as a documented limitation.

## Findings and outcomes

| # | Finding (lens) | Verdict | Outcome |
|---|---|---|---|
| A | `bool(parsed["valid"])` promotes on the string `"false"` through the fail-closed age path (Skeptic+Architect, HIGH) | CONFIRMED | Strict verdict parse: only JSON `true` / literal `"true"` approve |
| B | `--remint-pending` alone measures a decisively negative Δ and still clears probation (Architect, HIGH) | CONFIRMED | The strike-3 lane now applies both effect routes by definition ("sets the stamp whichever way it lands"); routes carry their own Δ-bar guards |
| C | Strike-3 event has no runtime consumer; CLI limit drops rows (Skeptic, HIGH) | REJECTED | Queue-by-event + deliberate CLI-driven spend is the decided design (no-silent-spend); `dropped` count is visible in the census |
| D | A watch row that tenure-promotes to LONG exits the MEDIUM-only selector — queued event unfulfillable (Architect, HIGH) | CONFIRMED | Selector scans MEDIUM+LONG; demote stamp and watch-clear are tier-aware; `_is_delta_demoted` joined the LONG injection filter |
| E | Anecdotal facts are an unchecked injection channel with "treat as known" authority (Skeptic, HIGH) | CONFIRMED | Deterministic `injection_guard` scan at declaration; flagged facts dropped fail-closed |
| F | Parallel paths don't checkpoint world facts (Skeptic+Architect, MED) | REJECTED as regression | Parallel fan-out/DAG runs have no checkpoint/resume machinery at all — pre-existing scope, now named in DEFAULTS + design doc |
| G | One corrupt checkpoint row (`int()` on junk) discards the whole restored ledger (both, MED) | CONFIRMED | Per-row try in `from_list` |
| H | Type-corrupt cadence counter wedges the inspector lane permanently (Skeptic, LOW) | CONFIRMED | Field-level guards; corrupt fields self-heal to 0 |
| I | Disabled cadence still counts + creates the state file; enabling later fires immediately (Minimalist, MED) | CONFIRMED | `cadence <= 0` short-circuits before the tick |
| J | `world_facts.enabled=false` gates capture but not render of checkpoint-restored facts (Minimalist, MED) | CONFIRMED | Kill switch gates the render site too |
| K | Ten judged-invalid oldest candidates starve the whole 433-node promotion backlog forever (Minimalist, HIGH) | CONFIRMED | Terminal-rejection stamp (`promotion_rejected_*`); re-enters only when `times_applied` grows past the stamp |
| L | Pack import doesn't normalize/type-check foreign domain/tags — `"tags": "Research"` becomes character tags (Architect, LOW) | CONFIRMED | Boundary normalization at pack import + the same list guard in `dict_to_skill` |

## What went well (all three reviewers)

Match-tier telemetry wiring, the inspector's locked RMW cadence counter
structure, and the archive-as-tombstone-store design drew no structural
findings. The Architect explicitly cleared the manifest wiring and
cadence locking.

## Lead judgment notes

- C's rejection is scope, not dismissal: if the strike-3 event
  accumulates unactioned in practice, wiring a consumer (heartbeat
  lane) is the follow-up — evidence-gated, not pre-built.
- F stays a named limitation until parallel runs grow checkpoint
  machinery of their own; bolting fact-carry onto a lane with no resume
  would be dead code.

---

# Round 2 — review of the fix layer (same day)

Jeremy: *"I've seen that be meaningful in the past... Want to run one
more against your changes?"* Same three Codex lenses, cumulative diff
(six chunks + round-1 fix layer, 173KB), prompts directed at fix-layer
defects and forbidden from re-reporting round-1 outcomes.

## Verdict: CONTESTED → fixes applied same-session

Nine findings; seven confirmed and fixed, two rejected. Round 2 earned
its keep exactly as the fixpoint practice predicts: five of the seven
confirmed findings were defects **in the round-1 fix layer itself**.

## Findings and outcomes

| # | Finding (lens) | Verdict | Outcome |
|---|---|---|---|
| R2-A | `--remint-pending` clears the watch to "measured" when the demote route is killswitch-disabled — a decisive Δ laundered by a CONFIG refusal (Skeptic, HIGH) | CONFIRMED | `resolve_remint_watch` gained a neutral-band check: probation ends only on a measurement inside (demote_max, promote_min). Route eligibility is the bars'; route application is the flags'; clearing keys on the bars alone |
| R2-B | Round-1's injection scan sits at `clean_declared` (completion-tool boundary) — `record_step_world_facts` and checkpoint `from_list` bypass it (Skeptic, HIGH/MED) | CONFIRMED | Scan moved to ledger ingress (`observe` + `from_list`) via `_scan_clean`, fail-closed; `clean_declared` keeps its copy so flagged rows never consume the cap |
| R2-C | Round-1's tag fix normalized pack import + `dict_to_skill` but LLM mint sites still iterate raw `tags` — `{"tags": "research"}` mints character tags that keyword-match everything (Architect, MED) | CONFIRMED | Shared `normalize_tags` in skill_types; all four mint sites (extract, synthesize, curation companion, pack import) use it; read paths pass `cap=None` |
| R2-D | Rejection stamp writes CURRENT `times_applied` under the lock, not the judged snapshot — evidence arriving mid-judgment silently counted as already-rejected (Architect, MED) | CONFIRMED | Stamp carries the snapshot count the judge saw; mid-judgment evidence earns a re-judge next sweep |
| R2-E | Cadence self-heal accepts negative counters — `runs_since_inspect: -10^6` defers inspection ~forever (Minimalist, MED) | CONFIRMED | `max(0, ...)` clamps on both fields |
| R2-F | Cadence consumed before the inspection runs — a crash after the counter write skips the firing (Minimalist, MED) | REJECTED | At-most-once tick is the evolver-lane precedent; consistency over new machinery |
| R2-G | Router path stamps all winners `"router"` though `route_skills` returns per-skill methods — degraded batches record false provenance (Skeptic, MED) | CONFIRMED | Per-skill `RouteResult.method` preserved; record-level telemetry says `"mixed"` when not all router |
| R2-H | Round-1's strict verdict made `{"valid": "maybe"}` a terminal rejection an age-only candidate can never outgrow (Architect, MED) | CONFIRMED | Unrecognized-but-parseable verdicts return `judged=False` (retryable, no stamp). Deliberate divergence: total parse failure still fails open — a reply that dodges the question is weak evidence; garbage means the validator is broken |
| R2-I | `LESSON_REMINT_PATTERN` captains-log event is write-only (Minimalist, LOW) | REJECTED | Operator-audit surface by design — same decided scope as round-1 C |

## Pin tests

Every confirmed fix landed with a pin: decisive-Δ refusal +
disabled-route laundering (test_delta_replay), ingress/restore scans
(test_world_facts), `normalize_tags` contract + string-tags mint +
mixed-router provenance (test_skills), retryable verdicts +
snapshot-count stamps (test_knowledge_web), negative-counter clamp
(test_agent_loop).

## Side-find (not this lane's, documented for the M1 lane)

`tests/test_handle.py::TestTemplatePlaceholderProvenance::
test_other_placeholder_dialects_resolve_too` (landed in 64eac95) is
environment-dependent: `_OUTPUT_CLAIM_RE`'s path charset excludes `)`,
so `artifacts/out-%(step)d.txt` truncates to `artifacts/out-%(step`
before `_TEMPLATE_MARKERS` can recognize the named-printf form — and
the truncated token only survives `_path_shaped` on hosts where an
`artifacts/` directory exists under a provenance base (true on this
box's repo root, false in CI/worktrees). Red here, green in CI. Left
for the M1 lane's round 3 rather than editing provenance.py under an
active concurrent review.
