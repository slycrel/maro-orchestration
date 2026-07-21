---
status: history
---

# Verify→Learn arc, 0.8.0 on PyPI, portable learning

*2026-07-10 – 2026-07-15*

226 commits from 60ef89f (07-10 03:06, "Purgatorio r2: full 7-eye re-run vs 2026-07-09 baseline") to 42201fc (07-16 01:14, "CAPABILITIES: capture cross-box dispatch-via-Hermes as verified Tier 2 entry" — the tail commits land the Hermes second box at the very end of the range). Representative commit 3c0d72f (07-15 07:46, "recall() loop-slice lesson icon: verdict-preferred (SF-2)"). The era where the learning loop closed on itself: every applied change declares its expected signal at birth, gets a behavioral verdict at cadence, and no learning consumer may read a verdict except through a single trust gate — plus the project went public on PyPI and made its learning portable in the same week.

## Architecture as it was

At 3c0d72f: a single Python package on one Ubuntu box — flat src/ (153 ls-tree entries: 152 .py modules plus the maro_assets subtree, the only subpackage). README framed it as an "Autonomous agent framework that holds its agents accountable": goal in, decomposed plan, Director/Workers/Inspector execution, then verification of whether the goal was actually achieved, not just whether the loop finished. Run loop staged across loop_init/planning/execute/post_step/parallel/blocked/finalize/report; interactive NOW lane in handle.py/intent.py; navigator (navigator.py + navigator_shadow.py) running shadow decisions with an agreement table as cutover evidence. Learning state: append-only JSONL ledgers under ~/.maro/workspace/memory/ as source of truth, sqlite index an explicitly rebuildable cache (docs/PORTABLE_LEARNING_DESIGN.md §0: "Portability = move the log; indexes take care of themselves").

**The defining change — verify→learn CLOSED 2026-07-14** (docs/VERIFY_LEARN_ARC.md: "The whole arc SHIPPED 2026-07-14"):

- **V1:** every applied evolver suggestion and graduation rule gets an expected_signal at birth (evolver_store.py; all 9 graduation templates declare failure_class_rate down).
- **V2:** verify_applied_suggestions() in evolver_scans.py rides the existing evolver cadence hook — no daemon — rendering confirmed/degraded/inconclusive verdicts over before/after windows. Symmetric authority: self-applied changes auto-revert on degradation; human-applied changes are never auto-reverted, only surfaced (§3 DECISION).
- **V3:** graduation rules get behavioral verdicts on their own failure class via an events-log join giving diagnoses a time axis (1274/1277 historical diagnoses covered).
- **V4/V5:** navigator half closed — LLM adjudication of divergences at cadence (NAVIGATOR_ADJUDICATED rows); pipeline_right clusters crystallize into navigator-scoped lessons injected into decide(), A/B-gated behind navigator.lesson_inject — enabled 07-14 on Jeremy's "turn it on please" (GOAL_BRAIN.md:3011 at 3c0d72f; :3014 at HEAD).
- **Trust gate:** verdict_trust() in memory_ledger.py is the single-source policy for which verdicts any learning consumer may count (§4: closure_unverifiable and env-capped verdicts excluded everywhere).

**Portable learning** shipped 07-13 as src/pack.py (chunks 1-4: b497cdc, 6a62bbf, af3c2ce, 44b7875): export→scrub→human review→seal, then import with trust demotion — standing rules demote to Hypothesis with counters reset, lessons enter MEDIUM tier capped at 0.5, skill stats become imported.claimed_*; skills/personas always quarantine, never land live; adopt is the explicit promotion step. Docstring honesty framing: "A pack is a letter — you proofread letters." maro-import stayed the separate trust-neutral machine-migration tool.

**Distribution went public the same week:** 0.8.0 to PyPI 07-12 as a name-reserve (77db43c; OIDC trusted publishing, manual workflow_dispatch, dry_run default true), immediately preceded by a full git-history privacy rewrite force-pushed the same day (~1101/1114 SHAs rewritten; docs/history/2026-07-12-git-history-privacy-scan.md).

**Era-end additions still warm:** container executor (container_exec.py) designed 07-12, burned in 07-15 (C4-BOX) with a real containment gap found and fixed — but the box deliberately left container: off; test suite 6171 tests / 78.04% coverage / honest-full 117.8s after the 07-14 truth pass; session-protocol arc opened 07-15 (docs/SESSION_PROTOCOL_DESIGN.md, §6 injection-seam v1 with typed ContributionLedger, dcbc821) anticipating Hermes on 07-16.

## Discoveries & aha moments

### Design judgment is the scarce resource — the Fable handoff (07-12)
With top-tier-model access believed ending ~07-13, the project reframed what the expensive model is FOR: not implementation, but adjudicating queued decision debt and front-loading design so successor Sonnet/Opus sessions inherit execution-shaped work. The handoff's own audit: "The scarce resource was design judgment: container executor decided but undesigned... verify→learn decreed but unwritten... ~20 Jeremy-gated decisions queued." Design briefs with sized chunks (V0–V5, C1–C4) became the standing handoff format; the 07-15 decree re-affirmed the resulting pattern — backlog items via sub-agent code-writing → verify → adversarial-review → fix → commit ("that pattern has served us well the past few days").
Evidence: docs/history/2026-07-12-fable-handoff.md at 3c0d72f; ec72118; GOAL_BRAIN.md:1966-1984 at 3c0d72f; GOAL_BRAIN 2026-07-15 third-round entry.

### An unverified verifier poisons learning — B3 before any verdict consumer (07-12)
The arc's hard sequencing dependency: probe-env hardening (B3) had to ship before any verdict-window logic, because "the 4/5 dogfood false-negative rate meant the pre-B3 verdict stream would have taught the verifier's cwd bugs as behavioral regressions." The system would have learned its own verifier's bugs as facts about itself. Corollary shipped as verdict_trust(): learning may only consume verdicts through a single-source trust policy, never raw.
Evidence: docs/VERIFY_LEARN_ARC.md §4 ("Hard dependency, sequencing-critical — SATISFIED 2026-07-12"); docs/history/2026-07-12-routing-and-probe-synthesis-design.md Part B.

### The impact scanner had been dead on production data its whole life (07-14)
While building V2: scan_evolver_impact windowed outcomes on created_at/timestamp — but real Outcomes carry recorded_at, so the warn path "had been dead on production data (only test fakes matched)." A monitoring feature that passed its tests had never once fired in production. Fixed in the same chunk.
Evidence: docs/VERIFY_LEARN_ARC.md §7 V2 entry; 7e7e455.

### Symmetric authority: the system may only undo what it did without a human (07-12 designed, 07-14 shipped)
"Anything the system applied without a human... it may revert without a human. Anything a human applied... is never auto-reverted... the system cleaning up its own mess is the point, overriding the operator is not." This made auto-revert a safety mechanism (default ON) rather than an autonomy risk; V3 inherited it unchanged.
Evidence: docs/VERIFY_LEARN_ARC.md §3 DECISION block; 584b902 (adversarial hardening kept the authority re-check just before irreversible revert).

### "Buildable now" — the missing time axis was recoverable from existing data (07-14)
Codex (parallel session) had documented V3 as design-blocked; Jeremy overrode: buildable now — both named prerequisites already existed as V1/V2 artifacts. Then the "timestamped diagnoses don't exist" blocker dissolved: an events-log join on loop_id gave ~99% of historical diagnoses a time coordinate (1274/1277 on the box), making the per-class verdict path live on the full ledger instead of dormant awaiting new data. Lesson: audit what shipped chunks already provide before declaring a dependency.
Evidence: 9040298, 93ad8d0, e792768; docs/VERIFY_LEARN_ARC.md §3 V3 paragraph.

### The "full" test suite was silently incomplete (07-14)
Project-wide pytest addopts had been removing slow tests from every command documented as full — "The old 141s baseline was incomplete because it omitted slow tests, so the new full result is both faster and strictly more honest." 6333→6171 tests, honest-full 117.8s, slow lane green in 13.0s. A green suite is a claim needing the same positive-evidence scrutiny as any other verdict.
Evidence: GOAL_BRAIN.md:124-135 at 3c0d72f; BACKLOG_DONE.md "Test-suite truth and reduction pass".

### Imports are contested-by-birth — trust never travels (07-13)
Portable learning's core insight: the trust-demotion shape already existed (rule contestation, decay-trust-never-data), so shared learning arrives demoted and earns local trust the same way an organic observation would — rules become hypotheses with counters reset, lessons cap at 0.5, skill stats become claimed_* only. No claim of mechanical anonymization: scrub + mandatory human review — "A pack is a letter — you proofread letters."
Evidence: src/pack.py docstring at 3c0d72f; docs/PORTABLE_LEARNING_DESIGN.md §0/§2b; b497cdc, 6a62bbf, af3c2ce, 44b7875, c321722, d47bf22.

### Known-gap pin tests: accepted residual risk becomes a checkable artifact (07-12)
Jeremy's accept-not-close posture got a mechanical form: each accepted verifier-synthesis gap got a test_known_gap_* test asserting today's imperfect behavior, "so 'revisit later' has a concrete artifact to flip once the underlying fix ships" — not a silent permanent waiver. Now a standing project convention.
Evidence: 015daf9 (tests/test_director.py, tests/test_intent.py); auto-memory project_known_gap_pins.md.

### De-1.0: the version number had become an accidental prioritization scheme (07-15)
Three days after publishing 0.8.0, Jeremy killed 1.0 as a gate: "1.0 was useful as something to work toward" but became "an alternate form of prioritization that's semi-unintended… 0.8 was the 1.0 bar… we did that work regardless of name, and the line is being arbitrarily held now." A pass removed the 1.0 gating line from all live surfaces; 1.0 relabeled "initial public release" — later, NOT a work gate.
Evidence: GOAL_BRAIN 2026-07-15 second-round Decisions (~lines 2165-2192 at 3c0d72f); first-round decree (3), line 2148.

### Measure the spike before designing: session reuse won the spike, lost the prototype (07-14)
Investigated as a measured spike first (Jeremy 07-11: pick it up "as its own measured spike, not as a rider on other work"). Spike: 31.6% wall and 75.0% cost reduction, 10/10 correctness. Production prototype same day: correctness tied, cost 27.9% lower at n=1, but NO executor speed benefit and 5.7% slower end-to-end — executor.session_reuse shipped default OFF as "a cost hypothesis, not a demonstrated latency win." The failed first spike protocol was retained rather than laundered.
Evidence: docs/history/2026-07-14-session-reuse-spike.md; 2026-07-14-session-reuse-prototype.md; BACKLOG.md:965-1006 at HEAD.

### Containment must be proven structurally, not behaviorally (07-15)
C4-BOX burn-in found the container design trusted goal-declared mount roots: a hostile goal declaring a host-secret path got it bind-mounted rw (real docker cat printed the canary). Fix pair: whitelist rw mounts to the workspace subtree AND a deterministic structural containment probe. The sharper insight: the behavioral probe was inconclusive because the worker refused the hostile goal — containment proof must be independent of model behavior.
Evidence: GOAL_BRAIN 2026-07-15 C4-BOX entry (lines 2096-2119 at 3c0d72f); docs/CONTAINER_BURN_IN.md.

### The taste-and-discretion decree cluster (07-10 – 07-13) — added post-verification; the excavation missed a parallel arc
The same window carried a second thread the sections above skip, and it is the direct ancestor of the 07-20 swarm-review session's "pattern of discretion" framing:
- **The Manti "abject failure" → capabilities catalog decree** (07-10): a NOW-lane answer so bad it minted `docs/CAPABILITIES.md` and the capability-capture rule — asks get recorded as-phrased, in-session (GOAL_BRAIN ~1757–1790).
- **Cuts-first planning v0 (Qix-cuts decree, SHIPPED 07-10, 068eddd):** both human approaches to the Manti ask were cuts-first — constraints-with-basis before planning; implemented as `draw_cuts` (GOAL_BRAIN ~174, 1791–1819).
- **AI-failure-pattern corpus** including Family 6 (agency/trust violations) lands in CAPABILITIES.md (GOAL_BRAIN ~266, 1897); the July Godot replay finding (agenda-state divergence, not capability) later serves as load-bearing evidence in the cuts-first kill-test (GOAL_BRAIN ~2384).
- **Time-blindness decree** (07-11, GOAL_BRAIN ~1872): "we might need to fight some kind of time blindness"; first slice shipped in-window (e156416, 07-15 — age stamps on injected memory + step-gap line).
- **Camera/perspective-rotation decree** (07-11, GOAL_BRAIN ~1881), explicitly tied to inference-not-prompting — the connective tissue between era 04's inversion whiteboard, era 05's lost signal-source rotation, and era 12's personas-stay decision.
- **Escalation-channel decree** (07-12, GOAL_BRAIN ~1908): the substrate LLM go-between IS the official escalation surface — resolved the last open 1.0 design item.
- **Recursive-goal check-in decree** (GOAL_BRAIN ~2442), resolving half of era 09's recursion-decree open thread.
- **Knowledge-web read-side trace** (07-13): the "2,124 edges await a reader" premise proven wrong — all edges link-farm noise; disposition NOT built (GOAL_BRAIN ~2521–2549). See era 09's corrected entry.
- Smaller in-window ships the sections above omit: the worker async-escape family — 7 mechanisms from the polymarket-edges r2–r4 saga (BACKLOG_DONE ~3340, 07-12) — and the AUDIT-INCOMPLETE delivery posture + second-resume refusal (docs/history/2026-07-14-audit-delivery-and-resume-admission.md).

## Pros vs today's architecture

- **Single-box, single-process-family simplicity:** planner, workers, learning, verification, interface — one repo, one box, one workspace. No cross-box dispatch gate, no forced-command SSH, no propose-lane PR flow, no split-brain memory question. The whole system auditable by reading one tree (git ls-tree 3c0d72f; Hermes cross-box only lands at 30d38d5/42201fc, the last commits of the range).
- **No-daemons held absolutely:** every verification and learning pass rode existing run-finalization cadence hooks. "No daemons — every verification pass rides run finalizations... Nothing schedules itself" (VERIFY_LEARN_ARC §2). No background failure modes; 'off' meant off.
- **Consumer-first discipline:** no verification feature landed without the thing that acts on its output in the same chunk — V2 shipped with verdict_trust() as first consumer, V4 with the --agreement adjudicated breakdown, V5 with lesson + injection + A/B flag in one chunk (VERIFY_LEARN_ARC §2, §7).
- **The design-brief handoff format** (Fable writes sized chunks with DECISION tags; cheaper models execute) was a genuinely good operating model born of scarcity — V0–V5 shipped in two days with adversarial review at each step (2026-07-12-fable-handoff.md; commits 82b17b8 through 26825e9, all 07-14).
- **Honesty artifacts everywhere:** known-gap pin tests, honest "unverifiable" parking instead of eternal pending ("an honest unverifiable beats an eternal pending", §3), the retained failed spike protocol, the self-scrubbed privacy-scan doc documenting its own redaction. Negative results were first-class records.

## Cons vs today's architecture

- **No real end-user interface:** the NOW lane was the only human-facing path, its routing freshly patched (needs_live_data) after the Manti "abject failure"; no second-box agent, no Telegram concierge on this stack. *resolved-since:* Hermes on mini2 with Telegram LIVE 07-16 (30d38d5/42201fc; auto-memory project_hermes_swap_plan, project_mini2_setup).
- **Container executor burned in but OFF on the box** — real workloads still ran uncontained at era end ("Box left container: off overnight"). *resolved-since:* GOAL_BRAIN.md:3058 at HEAD: "2026-07-16 (system) — container-on day one caught a verification-layer bug, fixed same morning."
- **navigator.lesson_inject enabled but unmeasured** — the V5 A/B had one lesson and zero measured effect at era end. *still-present:* no measured A/B verdict entry in GOAL_BRAIN through 2026-07-20; auto-memory says "watch A/B".
- **Enrichment provenance captured but unconsumed:** the 07-15 decree shipped the raw-vs-enriched pair as "captured data for later" with "nothing downstream is enrichment-aware yet." *still-present:* at HEAD only ~6 src files mention enrich*, consistent with capture-not-consume.
- **docs/history/CHANGELOG.md frozen at 1.21.0 / 2026-06-21** — the whole era (verify→learn, PyPI, pack) never entered the changelog; GOAL_BRAIN/BACKLOG_DONE carry the real record. *still-present:* byte-identical at HEAD 6659bf5.
- **maro-pack shipped with no consumer:** no recorded pack export/import between real users or boxes since chunk 4. The 07-16 Hermes work took the propose-lane route instead. *still-present.*
- **Filename-scrubbing known-gap in pack export deferred** at review closure (paths inside artifact filenames not scrubbed like content). *still-present:* d47bf22 ("the deferred filename-scrubbing known-gap recorded so it isn't lost").

## What we believed then

- **"1.0.0 stays the real-readiness release"** (GOAL_BRAIN 07-12 PyPI-posture decision) — overturned by the 07-15 de-1.0 decree: 1.0 relabeled "initial public release", NOT a work gate; "0.8 was the 1.0 bar."
- **Top-tier-model access was ending for good** ("top-tier-model (Fable) access ended ~2026-07-13; implementation continues on Sonnet/Opus") — access returned (auto-memory project_budget_posture: "Highest Claude tier"); the scarcity model was temporary, though the handoff pattern it produced was kept on merit.
- **"failure_class_rate windows still need timestamped diagnoses, which don't exist"** (V2 ship note) — disproved within hours by V3's events-log join: 1274/1277 historical diagnoses got a time coordinate from data already on disk (93ad8d0, e792768).
- **"The full test suite is the 141s run"** — the global addopts silently excluded the slow lane; the honest full suite was both smaller and faster once measured truthfully (GOAL_BRAIN.md:124).
- **"scan_evolver_impact warns on impact regressions"** — the warn path had never fired on production data (created_at vs recorded_at; VERIFY_LEARN_ARC §7 V2).
- **Briefly post-spike: "session reuse is a wall-clock win"** (31.6% faster in the spike) — the production prototype showed no executor speed benefit and 5.7% slower end-to-end; only the cost effect survived, at n=1.

## Lost good ideas

- **Per-boundary session reuse (executor.session_reuse):** one headless claude session per cut/boundary segment, rotated with distilled state at boundaries. Fully built, hardened, adversarially reviewed — shipped default OFF 07-14. *Why lost:* the decision keyed on latency (no speed win, 5.7% slower e2e), the cost result was n=1, and the same week's budget-posture decree ("cost is not the end-all-be-all") removed the pressure. *Worth reviving, cheaply:* machinery exists behind a flag; a counterbalanced batch of 5-10 real goals would settle whether the 75% spike-level cost reduction generalizes. Long research segments with heavy shared context are the natural first target.
- **maro-pack's bootstrap-sharing half** — a curated pack one user hands another so a fresh install doesn't start from zero, with the seal/review lifecycle and contested-by-birth demotion. *Why lost:* no second user materialized; when the second box arrived (mini2, 07-16), Hermes took the propose-lane/dispatch route and split-brain memory was parked as a "phantom sidequest" (GOAL_BRAIN 07-15 decree 8). *Worth reviving* when either trigger fires: a fresh Maro install anywhere, or the parked Hermes/Maro shared-memory question reopening — the 07-15 decree itself named portable learning "the enabling data layer" for the active-orchestrator topology.
- **Verifier synthesis proper** — the full up-front declare-what-proof-this-goal-needs loop, of which 07-12 shipped only the first slice (Deliverable.shape, shape-conditional behavioral-probe MUST, probe-env hardening). *Why lost:* explicitly deferred with owner agreement (271890f: "Note: verifier-synthesis phase needs additional scoping (Jeremy, agreed)"); the three accepted residual gaps were pinned as known-gap tests (015daf9). *Worth reviving:* the three pinned tests (waiver content unjudged, fail-relevance unjudged, heuristic live-data regex misses) are standing, greppable entry points — flipping them is the designed resumption path.
- **The Fable-handoff operating model as deliberate practice** — spend the strongest model exclusively on adjudicating decision debt and writing execution-shaped design briefs, then hand off. *Why lost:* born of forced scarcity; when top-tier access returned, the explicit design-then-execute session split stopped being a named discipline (the sub-agent work pattern survived, the "design-judgment budget" framing did not). *Worth reviving partially,* as a cost/attention discipline: the V0–V5 arc (brief 07-12, all five chunks shipped 07-14 by cheaper models with adversarial review) is the strongest throughput evidence in the repo's history.

## Sources

- `git log --since=2026-07-10 --until=2026-07-16` in /home/clawd/claude/maro-orchestration (226 commits, boundaries verified)
- `git show` at 3c0d72f: docs/VERIFY_LEARN_ARC.md (full), docs/PORTABLE_LEARNING_DESIGN.md, src/pack.py docstring, README.md, docs/history/2026-07-12-fable-handoff.md, 2026-07-12-git-history-privacy-scan.md, 2026-07-12-routing-and-probe-synthesis-design.md, 2026-07-14-verdict-persistence-contract.md, 2026-07-14-session-reuse-spike.md, docs/history/CHANGELOG.md; GOAL_BRAIN.md (Compiled truth :110-175; Decisions 07-10..07-15, lines 1671-2220; lesson_inject :3011); BACKLOG_DONE.md; `git ls-tree 3c0d72f src/` (153 entries)
- Key commit diffs: 015daf9, d47bf22, 77db43c, 5f36f90, 7e7e455, 584b902, 9040298, 93ad8d0, e792768, 271890f, ec72118, b497cdc, 6a62bbf, af3c2ce, 44b7875, c321722, dcbc821, 60ef89f, 3c0d72f, 42201fc
- HEAD (6659bf5, 2026-07-20) checks: GOAL_BRAIN container/lesson_inject/pack references, BACKLOG.md:960-1010 session-reuse entries, CHANGELOG staleness
- auto-memory: project_verify_learn_arc.md, project_portable_learning_shipped.md, project_known_gap_pins.md, project_budget_posture.md, project_hermes_swap_plan.md
- Verification pass 2026-07-21: 22 load-bearing claims checked against HEAD 6659bf5 — all 22 CONFIRMED, zero refuted. Two precision notes applied here: src/ at 3c0d72f is 153 ls-tree entries = 152 .py modules + the maro_assets subtree (not "153 modules"); the "turn it on please" lesson_inject entry sits at GOAL_BRAIN.md:3011 at 3c0d72f (:3014 is its HEAD position).
