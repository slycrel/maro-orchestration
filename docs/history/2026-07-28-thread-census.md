---
status: record
---

# Thread Census — 2026-07-28

The first full inventory of open threads across every fan-out surface:
BACKLOG.md, GOAL_BRAIN.md (Threads + Open questions), MILESTONES.md,
all 123 top-level `docs/*.md` + `docs/history/*.md`, and the
machine-local auto-memories. Run as the first deliverable of the
"Open-thread structure — beyond the backlog" BACKLOG item (Jeremy,
2026-07-28: "let's get started on the census"), and as star-pattern
use #3.

**Method.** Inline pass over BACKLOG / GOAL_BRAIN / MILESTONES /
auto-memories; delegated marker sweep over docs/ + docs/history/
(439 raw marker hits across 85 files, every candidate region
dispositioned as open-as-written vs closed-in-doc with verbatim
quotes + file:line). Ten sweep claims spot-verified against the tree
before merging: **10/10 held verbatim** — the first delegated recon in
this repo's recorded history with a zero-hallucination sample
(historical rate 30–78%; the strict quote-verbatim/file:line contract
appears to be the difference — worth reusing).

**Not swept** (follow-up pass if wanted): `docs/audit-2026-07*/`,
`docs/conversations/`, `docs/knowledge-layer/`, `docs/research/`
(its `README.md` carries its own 6-question queue),
`docs/history/knowledge-journey/`.

---

## 1. State taxonomy

| State | Meaning | Health |
|---|---|---|
| ACTIVE | In the current working set | healthy if ≤ cap |
| BLOCKED-ON-DATA | Waiting on organic accrual | healthy **iff** a readout exists |
| BLOCKED-ON-JEREMY | Needs his call | healthy iff he knows it's queued |
| PARKED-BY-DECREE | Deliberately parked, reason recorded | healthy |
| UNTOUCHED | Opened, never walked | the debt |
| PREMISE-DRIFTED | Parked/blocked thread whose premises later changed — blocker cleared, substrate removed, or claim went stale — and nobody reconciled | **the headline state** |
| UNANCHORED | Lives only in machine-local memory, no repo record | fragile |

Two disposition classes for sweep residuals that are *not* threads:

- **ACCEPTED-RESIDUAL** — documented, usually pin-tested, deliberately
  not fixed (e.g. file_lock 5s-degrade, `_path_shaped` pins,
  queue-drain window). Healthy; listed once here, never again.
- **ARCHIVE-ERA** — prototype-era "Still pending" blocks with no
  forward obligation unless deliberately resurrected (ROADMAP_ARCHIVE
  Phases 23/24/25/27/28/39, NeMo residuals, Phase-61 candidates,
  session-22 queue, 2026-03-31 steal-source ledger, CHANGELOG
  shorthand A/B, the 2026-05-12 research briefs built from stale
  sources). The census records the *class*; resurrect by choice, not
  by rediscovery.

## 2. Headline: premise drift is real and has a shape

Seven confirmed cases where a thread's park reason or blocking premise
expired and nothing noticed — six needing re-adjudication, one resolved
on the spot. This is the failure mode Jeremy described ("we open paths
and never quite revisit the full work") made concrete:

1. **Heartbeat-gate design** (memory, 2026-06-21) — premised on the
   `local_models.py` qwen ladder, **removed 2026-07-21** (swarm chunk 1).
   Needs re-base onto hosted-free or explicit retirement.
2. **Next-leap persona/skill packaging** (memory, 2026-04-09) — blocked
   on "token/process stability," since satisfied (Phase 62 brake,
   artifacts-over-streams). Blocker cleared; thread never resurfaced.
3. **Thread-architecture implementation arc** — GOAL_BRAIN dormant as
   "parked pending goal-brain sequencing"; sequencing **completed**.
   The park may still be right; the recorded reason is dead.
4. **Mage correspondence memory** — parked "downstream of recall()
   shape"; recall() shape answered 2026-06-10. Same shape as #3.
5. **GOAL_BRAIN dormant "10 pre-existing test failures / fragile
   fail-safes" (2026-06-10)** — suite has been green through the entire
   swarm arc (land.sh gates on it). Claim stale; verify + strike or
   re-scope. (Fail-safe fragility in parallel/DAG runners may still be
   real — the *test-failures* half is what drifted.)
6. **Director-clarification design** (memory, 2026-04) — half absorbed
   by the SP arc's clarification-loop stage; the YOLO-option +
   config-defaults half is anchored nowhere current.
7. **BACKLOG's own C4 entry** — said "the box runs `container: off` —
   the flip stays Jeremy's call" while the live config has run
   `executor.container: on` since 2026-07-16 (SESSION_PROTOCOL Q7;
   verified against the live box during the census). The primary work
   queue carried an already-decided Jeremy-gate for 12 days. Unlike
   1–6 this resolved immediately on evidence: C4 closed in this commit
   (design §9 note; entry archived to BACKLOG_DONE with the story).

Plus the **stale-currency doc lines** proven during the census and
fixed in this commit (currency rule):

- `docs/SECURITY_MODEL.md` §2 — said "Implementation not started;
  container OFF until C4" — C1–C4 shipped; Jeremy flipped this box ON
  2026-07-16.
- `CLAUDE.md` open-design-spaces row — said adaptive execution
  "Dormant design — not started" — Phases A–C shipped 2026-04-15
  (verified live in src: `director_replan_count`,
  `loop_status="restart"`); only Phase D + closure unification remain.
- `docs/THREAD_ARCHITECTURE.md` header — said "No implementation yet"
  while its own Open-decisions list records 4 of 9 resolved.
- `docs/PORTABLE_LEARNING_DESIGN.md` §8 — said chunks 1–4 "queued in
  MILESTONES" — shipped 2026-07-13.
- `docs/SESSION_PROTOCOL_DESIGN.md` §12 — said portable-learning
  decisions "await Jeremy" (ratified 2026-07-12) and hosted-free
  "awaiting API keys" (live 2026-07-16).

And one **resolved-by-reorganization** exhibit (the inverse failure —
a thread that *looked* open but closed silently): the inverted-Kadavath
citation, flagged as unfixed in three successive 2026-05-12 passes,
now survives only inside `status: record` history docs; the living
`VERDICT_INDEX.md` carries only the corrected THEORY-009 finding and
`productive_persistence.md` no longer cites Kadavath. No edit needed —
but nothing recorded the closure until this census walked it.

**The lesson in one line:** park reasons and blockers are claims about
the world; nothing re-checks them when the world moves. The ledger
below (and the conventions in §5) is the fix being trialed.

## 3. The inventory

56 threads after dedup (47 inline + 9 net-new from the docs sweep).
Parent provenance and last-touch are given where they change the
disposition.

### ACTIVE (current de-facto working set)

1. **SP session-protocol arc** (Jeremy 2026-07-15; dispatch LIVE
   2026-07-16). Open inside it: §6a gaps 3–4, gap 5, interactive-lane
   stages, and the delivery-loop's last unbuilt piece — the
   effort-estimate + consent message (SESSION_PROTOCOL Q1/Q2,
   KNOWLEDGE_JOURNEY through-line 6).
2. **NOW retry rung** (opened 2026-07-28; 5 open boxes incl. both-lane
   testing decree; prereqs: NOW provenance stamping,
   `_is_complex_directive` reachability).
3. **House-style doc + intentionality loop** (2026-07-28; M1-contrast
   pass; quiz-shaped iteration protocol; CodeLikeJeremy PoC as
   pattern input — see BACKLOG entry).
4. **Open-thread structure** (2026-07-28) — this census is its first
   deliverable; conventions proposal in §5.
5. **Chunk-9 discussion remainder** (§9.7/§9.9/§10 calibration —
   awaiting Jeremy's evening-conversation window).

### BLOCKED-ON-DATA (each verified to have a readout — all healthy)

6. R6-E lesson_text anchoring (readout: `navigator_shadow --agreement`).
7. Navigator close-cutover (organic close divergences; 4 so far, all
   synthetic — DUMB_LOOP_AUDIT:185).
8. Blocked-step escalate re-verify (MILESTONES 2).
9. Standing-rule end-to-end observation (production hours).
10. Recall guard thresholds (RECALL_GUARD_TRIPPED — RECALL_DESIGN:198).
11. Fan-out revisit policy (NAVIGATOR_DECIDED rows; the prompt half of
    thread-arch decision #1 is the same thread).
12. Escalate organic accrual (MILESTONES 1; first organic escalation
    fired — Telegram rendering worth a glance).
13. Star strategy-selection corpus (KEEP'd 2026-07-28; rows accrue).
14. lesson_inject A/B (chunk-7 first numbers: 58% vs 41%, small n).
15. DUMB_LOOP recoverable-focused accrual batch (false-escalate rate —
    DUMB_LOOP_AUDIT:13). *(net-new from sweep)*
16. Planning-depth shadow cutover (NAVIGATOR_SCHEMA:532; same
    per-class-cutover template as close). *(net-new from sweep)*

### BLOCKED-ON-JEREMY (four live after the census — he knows about all of them)

17. ~~C4 container flip~~ — **closed during the census** (drift find
    #7 above): box ON since 2026-07-16; fresh-install default was a
    made decision (stays OFF, BURN_IN §6), not a pending one.
18. Escalation "complex later" half — capability-investment scoring,
    side-quest recommendation at the failure boundary
    (2026-07-27-escalation-payload.md:75).
19. Auto-resume graduated shape (item (h), deferred past 1.0 —
    consistent across 3 docs).
20. Filename-scrub known-gap (g) — gates public pack sharing.
21. Close-cutover adjudication when evidence lands (overlaps 7).

### UNTOUCHED (opened, never walked — the real debt)

22. #1 Bash write shapes the fence can't see (BACKLOG).
23. #17 run-visibility O(all-runs) residual (~10k-run trigger).
24. Run-visibility server question (product discussion).
25. Verifier synthesis as deliverable (4 boxes; "repeatedly named,
    never scheduled" per the fable handoff — still true).
26. Store/guard censuses (gated on registration convention — chunk 8
    BACKLOG'd with prerequisites named).
27. Record-mode dead on single-backend boxes (structural, recorded
    2026-07-21).
28. Ancestry write-side unification (recursion prerequisite —
    THREAD_ARCHITECTURE:159; queued for the thread-arch arc).
29. Director-playbook wiring row 17 (structurally confirmed
    2026-07-21; decision unmade).
30. **Closure-check unification** (`verify_goal_completion` →
    `director_evaluate(trigger="closure")`, retire `ClosureVerdict`) —
    Phase C leftover, deferred 2026-04-15, named twice
    (ADAPTIVE_EXECUTION:221, ROADMAP_ARCHIVE:1508), 3+ months old.
    *(net-new from sweep)*
31. **Depth-cap unification** — ratified as "still open" 2026-07-12
    (BACKEND_RESILIENCE:18), repeated in the fable handoff, never
    scheduled. Small chunk + tripwire. *(net-new from sweep)*
32. **Backend-resilience gaps**: in-flight-step visibility ("missing
    entirely," :254) + `recover_stale_claims` has no scheduled caller
    (:255). Cross-box dispatch raised the stakes (SESSION_PROTOCOL
    §12). *(net-new from sweep)*
33. **Stage 2→3 crystallization pathway** — CANON_CANDIDATE /
    LESSON_RECOVERED reserved-intentionally-pending
    (CAPTAINS_LOG_EVENTS:231); no automated implementation.
    *(net-new from sweep)*
34. **Landing-synthesis closure interaction** — the stop-path survey's
    load-bearing "verify before wiring" residual; the stop-verdict
    split answered the chunk-9a question but not this verification
    specifically (2026-07-23-stop-path-survey.md:105). *(net-new)*
35. **Lost-the-plot metadata stamp** (stop-verdict-split:130, deferred
    at ship). *(net-new from sweep)*
36. **Fetch-to-disk verb** for `claude -p` workers — the named next
    lift of artifacts-over-streams (ARCHITECTURE_OVERVIEW:254).
    Anchored in Vision; no build item yet. *(net-new from sweep)*
37. Cross-agent claim challenge — deferred twice (Phase 61 candidate,
    ROADMAP_ARCHIVE:1879/1937); partially absorbed by chunk-5b's
    council lenses; the mid-run persona-B-challenges-worker retry is
    not built. Absorbed-in-part; residual is small. *(net-new)*

### PARKED-BY-DECREE (healthy — reason recorded and still valid)

38. Phase 65 constraint orchestration — parked on the unresolved
    "1-shot-first frame" question (CONSTRAINT_ORCHESTRATION_REVIEW:239
    = REASSESS_LINEAGE:106 — same question, two docs). The six
    `[scope-deferred]` log markers are its greppable attachment
    points.
39. Per-phase tier routing (garrytan decree: don't resurrect).
40. Model-route budget lane (designed, unfunded by decree).
41. Swarm chunk-1 batch drops (23 deliberate-drop records).
42. Graph memory / retrieval handle (vision; revisit-if named).
43. Step-skeleton parallelization (vision, 2026-07-28).
44. Fork/join semantics + async `wait` (gated on fork firing).
45. Migration/pack hardening — signing, richer scrub, imported-skill
    A/B (post-1.0 by decree).
46. Mid-loop constraint machinery (triad, violation detection,
    lifecycle — CONSTRAINT review "do not implement until the frame
    resolves").
47. Provenance/enrichment deep halves (two-author provenance,
    mis-enrichment detection — SESSION_PROTOCOL:361, "deliberately
    deferred").

### PREMISE-DRIFTED (see §2 — the six cases)

48–53. Heartbeat-gate; next-leap packaging; thread-arch park reason;
Mage-correspondence park reason; "10 test failures" dormant claim;
director-clarification YOLO half. **Proposed: one cleanup batch
re-adjudicates all six** (re-base, resurrect, re-park with a current
reason, or retire — each is a 10-minute call, and four are Jeremy
calls).

### UNANCHORED (machine-local memory only)

54. Hallucination-reduction remaining levers (sharper exec checks;
    claim-typed verifier registry). Partial repo anchor found: the
    2026-05-12 calibration-audit prerequisite + VERDICT_INDEX P-queues
    are adjacent, but the *levers* live only in memory.
55. Extensibility/plugin vision (MTG stack analogy).
56. PM/dev workflow via GitHub Issues on orchestrator-test-recipes
    (dormant since the propose-lane shipped?). — Polymarket-edges
    workspace practice and the grok-response-2/3 files were also
    checked: the former is workspace-real (fine), the latter presumed
    processed (round-5 memory postdates them).

## 4. Docs-sweep residual ledger (condensed)

Everything the sweep found open-as-written that is NOT promoted to a
thread above, by disposition. Full verbatim quotes + file:line for
every row were captured at sweep time; this table is the durable
index of them.

**ACCEPTED-RESIDUAL (documented, mostly pin-tested — no action):**
RECURSIVE_CHECKIN queue-drain window; RUN_VISIBILITY file_lock
5s-degrade, snapshot collision window, orphan cleanup, meta-of-meta
view, inter-step badges, mtime guard, index staleness hook, project
filter; `_path_shaped` opposite-direction pair (pinned);
stop-verdict-split merge-failure ordering gap; container leaked-while-
process-alive sweep limitation; C1 CLI re-pin (procedural, recurs per
build); `current_run_dir` thread-local landmine (dead path today,
tracked in BACKLOG worktree item).

**CONDITIONAL-REOPEN (named trigger, healthy):** ARCHITECTURE_NON_GOALS
×3 (MCP-default, Neo4j, provider portability); SUBSTRATE_INTEGRATION
no-REST posture; MODEL_ROUTE Fireworks fee; graph storage
(multi-hop-query trigger); quarantine-scratch rung (poisoning
specimen); decision-kind taxonomy (fourth writer); mid-run decision
dedup (first hit); legacy class rebucket (consumer misread);
`_BlockDecision` graduation seam (wire when seam exists); lesson-store
contested tier (consumer-first); skill/playbook freshness (staleness
in practice); codex payload-first check (rc≠0-with-output); agentic
deep-eval verifier (LOCAL_VALIDATOR); BDD red-green loop
(modality numbers); landmark graph (behind consumers).

**ANCHORED-ELSEWHERE (pointer rows — BACKLOG/MILESTONES already carry
them):** DUMB_LOOP provenance net; CAPTAINS_LOG #8 integrity follow-up;
REFACTOR_PLAN Tier-4 move + CLI messages + orchq placement +
observability-dashboard revisit; NAVIGATOR_SCHEMA per-turn maintenance
(MILESTONES #5) + fork cap revisit; RECALL_DESIGN deferred edges +
semantic-family inference; DEFAULTS Phase-65 discussion + API lane;
CAPABILITIES promotion waits (X-thread e2e, self-inspection);
KNOWLEDGE_JOURNEY consent message (→ SP arc); EXECUTION_FLOW
patterns-worth-revisiting (dormant-design context); DRIVER_AND_WATCHER
open questions (superseded-in-part by thread-arch);
COMPOUND_THINKING §10 open questions + taste-consequence edge
(→ chunk-9 remainder); tire-runs deferred findings (BACKLOG'd;
mid-step brake already shipped); per-step-learning discretion tally;
2026-07-21 chunk-1 residual cheap paths (recorded in review doc).

**ARCHIVE-ERA (no forward obligation; resurrect deliberately):** the
class list in §1. Notable inside it: Phase 27's "sub-goal knowledge
acquisition — PENDING" is the one archive-era item that keeps rhyming
with live work (escalation payload, capability catalog) — if anything
gets pulled forward on purpose, it's that.

**CLOSED-ELSEWHERE (doc says open, world says closed — history docs
stay as-written by record convention; closure noted here):**
factory-vs-Mode-2 comparison (Phase 49 RESOLVED 2026-07-21); Phase 40
TODO row (DONE 2026-04-04); Phase 42/46 unwired claims (since DONE);
cost_usd hardcoded 0.0 (superseded by metrics + chunk-7 EFFORT
readout); memory-bakeoff "awaiting Jeremy" (decided — module arc);
git-history privacy sequence (0.8.0 published 2026-07-15;
RESOLVED-ACCEPT 2026-07-16); portable-learning §8 "awaiting review"
(ratified 2026-07-12); MEMORY_ARCHITECTURE 2026-03 open questions
(superseded-in-part by the 2026-07-07 module decision — annotated in
the doc this commit); Kadavath propagation gap (resolved by
reorganization — §2); `[DESIGN PROPOSAL]` tag residual (target file
since reworded); 05-12 "Scope Orchestration not started" (scope.py
shipped 2026-04-23 — known-stale at write time).

## 5. Marker-convention validation

Tested the proposed `THREAD[slug]` attachment-point convention against
the shapes the census actually found:

- **Code-located threads** — precedent exists and works: Phase 65's
  `[scope-deferred]` log markers were designed exactly as greppable
  think-harder-here-later points (PHASE_65_IMPLEMENTATION_PLAN:80).
  `THREAD[slug]` generalizes this. Validated.
- **Doc-located threads** — the sweep is the argument: 439 fuzzy-
  vocabulary hits needed ~45 context reads to disposition. A
  deliberate marker makes the collector a grep. Validated, with one
  rule discovered: **history docs must never carry live markers.**
  Records are as-written; a THREAD marker in docs/history is a census
  error by definition.
- **The missing convention the sweep exposed:** docs become records
  *without* their forward obligations being swept forward — that's how
  ~40 open-as-written residuals ended up in history files. Proposed
  rule: **at doc-archival time, open items are explicitly moved to
  BACKLOG/GOAL_BRAIN or declared dead in the archival commit.** This
  is the doc-lifecycle analog of BACKLOG→BACKLOG_DONE moves.
- **The hard case confirmed:** the six premise-drifted threads had no
  repo location a marker could attach to — they lived in memory files
  and park-reason clauses. Markers can't fix this; only a ledger with
  re-checkable park reasons can. Proposed rule: **every PARKED/BLOCKED
  entry states its reason as a falsifiable claim** ("parked because X
  is true") so a future census — or a cheap deterministic check — can
  test it. The six drifts in §2 are exactly the entries whose reasons
  were falsified silently.

## 6. Proposed ACTIVE/PARKED split (for Jeremy)

Working-set cap 7. Proposed ACTIVE: threads 1–5 plus **the
premise-drift cleanup batch (48–53) as one slot**; one slot free.
Everything else PARKED/BLOCKED with the state + reopen condition this
census assigned. The UNTOUCHED list (22–37) is the pool the free slot
draws from — suggested first pulls, by age × load-bearing-ness:
closure-check unification (30), depth-cap unification (31), and the
backend-resilience pair (32).

Maintenance: the census is a snapshot; the ledger lives in GOAL_BRAIN
Threads (which this census now reconciles). Next census when the
convention changes or ~quarterly — whichever comes first.
