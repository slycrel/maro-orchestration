---
status: record
---

# GOAL_BRAIN journal archive — 2026-08-01 → 2026-08-08

Rotated out of GOAL_BRAIN.md on 2026-08-16 (Jeremy: the live file had grown past 8k lines / 556KB — beyond whole-file readability). This archive remains part of the same append-only record: entries here are verbatim, in their original order, and dev-recall ingests this file. Entries from 2026-08-09 onward stay live in GOAL_BRAIN.md.

- **2026-08-01** — Live-learning test arc opened (Jeremy, decree-class ×6;
  BACKLOG "LT."; pipe to decisions.jsonl PENDING — must run on the box, see
  below). **(1) Burndown over net-new:** the corpus problem is solved
  (CAPABILITIES.md tiers + the 24-entry failure corpus); what's missing is
  evidence, so weight the batch toward converting `target` rows to
  verified-or-broken, net-new only where a corpus family is thin (3
  tool-use/execution, 6 agency/trust). **(2) Instrumentation is a
  prerequisite, and wider than verdicts:** "audit anything we might want
  written down by the runs that isn't already… the more we can examine after
  (or even during) the runs, the better; both at the edges of steps and in
  the different processing layers." **(3) "Leveled up" = cold-vs-warm re-run
  delta** — every test goal runs twice, store cold then warm; the delta is
  the evidence. Doubles spend, needs no new machinery, can't be self-graded.
  **(4) The capability ladder gets its own doc** (`docs/CAPABILITY_LADDER.md`,
  C0–C5); CAPABILITIES.md stays the goal well. **(5) A failure is never a
  success:** "an expected failure is just a goal we haven't engineered (or
  learned) to solve yet, not a success." No goal may be graded
  pass-by-refusing — reframe it with a positive deliverable (the evidence
  trail, not the decline); a genuine miss is an unbuilt bridge, recorded
  not-achieved and fed to the ladder. This is what keeps the cold/warm delta
  honest: the store must not be able to learn "declining is what wins here."
  **(6) Trace the work and write it all down, before the batch runs** — each
  decision, LLM prompt and output, step plan, and artifact, durable and
  reachable by all three consumers (report, tests, mining), "because we keep
  stumbling on data that we thought we had but didn't."
- **2026-08-01** — Census-reading correction (Claude, self-caught; no Jeremy
  decree — recorded because the lesson is durable and the wrong number was
  briefly in the repo). The first provenance-census box run scored
  `build/calls/` as "record mode is effectively off in production — 641
  settled runs captured ZERO LLM calls." **That reading was wrong, and the
  defect was in the instrument.** A single late `ERA_BOUNDARY` (2026-07-29)
  left n=8 in the post bucket and pooled 619 runs that PREDATE record mode
  (shipped 2026-06-26) into the whole-history column, so a working feature
  scored as an outage. Monthly series: 04 0%, 05 0% (n=476), 06 0% (n=141),
  **07 80.7% (92/114)**. The real finding is ~19% of July runs still capture
  zero calls (the EDGE-2 ContextVar/lane hole), plus the permanent fact that
  the pre-2026-06-26 corpus is blind on LLM I/O and cannot be mined
  retrospectively. **The pooled table also buried the actual batch
  blockers:** `recall_citations.json` at 10% of July agenda runs and
  `skills_manifest.jsonl` at 55% — the two artifacts that name which lessons
  a run cited and which skills it was given, i.e. the entire cold/warm
  attribution rail. Instrument fixed same day: per-month coverage + per-month
  verdictability series, auto-inferred first-seen month with a "since"
  column (the header now says to read `since`, not `all`), thin denominators
  (<20 runs) marked `~`. **Durable rule: a coverage percentage is
  uninterpretable without knowing what its denominator contains** — pooling
  pre-feature runs with post-feature runs manufactures outages, and a
  pre-registered prediction table does not protect against it. Chunk B's
  independent live census reached the same conclusion from the other side
  ("the 3% all-time stock is April-era pre-verdict rows"), which is also why
  `python3 -m verdict_flow` — not this census — is the authority on
  verdictability.
- **2026-08-01** — Chunk-9 #2 runtime slice SHIPPED (continuing the
  delegated build order 8c7f5068; star half 2026-07-27). Recon is now a
  typed step flavor in src/: inline `[recon: <decision it informs>]` tag
  (the [after:N]/[boundary] convention — survives manifests/resume/
  splits with zero plumbing), RECON_FLAVOR_RULES + VOI gate taught in
  decompose behind `planner.recon_flavor` (EMISSION-only killswitch;
  detection at every consumer unconditional per the chunk-6 precedent),
  map-edit execution contract + `flavor`/`recon_decision` outcome stamp
  on every outcome shape, and the map-change verification question
  (_VERIFY_RECON_STEP_SYSTEM — honest negatives PASS) at both ladder
  tiers by construction. Bare [recon] keeps flavor (demotion would
  assign the deliverable question — the exact dishonesty this fixes).
  Cuts as named upgrade edges in BACKLOG ("Recon-flavor upgrade edges");
  blocked_on/§14 graduation stays corpus-gated per the 07-27 record's
  own routing. 16 pins in tests/test_step_flavor.py. Record:
  docs/history/2026-08-01-recon-flavor-runtime.md. Same session:
  promote-validation wired (c45110c) and the census M1 handoff closed
  (312b6a6 — seven decrees piped to the runtime journal; the sidequest's
  two rail numbers confirmed live: recall_citations 10% /
  skills_manifest 55% since-first-seen).
- **2026-08-01** — CI red = signal, keep the census (Jeremy asked
  "increase visibility or disable if noise" after
  test_history_docs_are_records went red on CI). Adjudicated signal: the
  red was two frontmatter-less history docs from a doc-only land, fixed
  by a concurrent session (a11501e) within the hour — the tripwire did
  its job. Resolution shipped (7da4597): land.sh pre-land structural
  gate runs the frontmatter/DEFAULTS censuses against the exact SHA
  being landed (temp worktree, diff-scoped, --skip-checks bypass) — the
  doc-only lands that skip the suite are exactly the ones that leaked
  this class to CI. Standing gap, Jeremy's half: the box's gh token is
  dead (401), so Actions status is invisible from here; a re-minted
  token (actions:read) unlocks a post-land CI watcher + Telegram-on-red.
- **2026-08-01** — max_tokens stays ADVISORY on the subprocess backend
  (Jeremy, after the cuts-lane smokes surfaced the 957>700 warning:
  "I'm not sure we should be pushing so hard on the max_tokens angle,
  for the moment I'm fine with it being advisory as opposed to
  binding"). Don't build per-call token-cap enforcement unprompted —
  the warning line is sufficient visibility, and the
  caps-are-circuit-breakers posture (2026-07-29) is about spend
  breakers, not truncating individual calls. Revisit only if overshoot
  starts costing something real (spend breaker trips, context blowups
  traced to it).
- **2026-08-01** — Recon emission gap CLOSED end-to-end (Jeremy: "Let's
  fix the tags" → "Let's keep going"). Three landings: (1) 9967c68 —
  live goals routed through the cuts-first lane shipped textbook recon
  probes untagged (the lane returns before the taught decompose);
  `_cuts_plan` now tags probes deterministically (VOI = the boundary
  plan), same `planner.recon_flavor` killswitch, milestone expansion
  exempts recon steps. (2) 575aa65 — watch instrument:
  `discretion_readout.recon_summary` tabulates the durable loop-log
  corpus with SINCE-FIRST-SEEN denominators (the same-day LT-retraction
  lesson applied at build time). (3) Post-land adversarial review (3
  Codex lenses, PASS with fixes; 7/7 verified, 0 hallucinated): corpus
  boundary hardened (non-dict payloads, schema-invalid files, tz-aware
  instant ordering — a missing timestamp could have pooled the whole
  pre-instrument corpus into the cohort), no-tag report branch now
  carries the incompleteness caveat, RECON_FLAVOR_RULES filtered from
  the draw_cuts context (the fix commit's "draw_cuts never sees the
  rules" claim was FALSE — rules rode extras into the cuts prompt; zero
  tags anyway, deterministic emitter now sole and stated correctly),
  bare model-authored [recon] re-tagged with deterministic VOI.
  Exemption-breadth + atomic-writer findings BACKLOG'd/deferred with
  named triggers. Record:
  docs/history/2026-08-01-recon-adversarial-review.md.
- **2026-08-01** — UU-1-class + learning-altitudes directions (Jeremy, on
  the cold-arm forensics; pipe to runtime journal owed on next box trip).
  **(1) Silent record-loss edges:** "my gut knows [UU-1] is there in way
  more edges than we think… we should fix those as they come up; might be
  worth some error handling assumption checks as well. Happy path coding
  isn't just a human problem, LLMs are as likely to do that as anyone."
  Fix-as-found posture (no dedicated sweep decreed); idea captured,
  explicitly flagged maybe-scope-creep by Jeremy: a **4th adversarial-
  review persona — expert QA** owning error/kill/sad-path record loss
  ("I bet [it] would find edges") — try on ONE review before
  institutionalizing. **(2) UU-2 → architecture direction:** current
  learning is "sort of a 1-trick pony, it's only trying to learn what we
  told it to try learning"; wants goal-level "what the run overall
  teaches" feeding the general learning system, scoped to greater-
  orchestration vs class-of-work; lighthouse-through-the-fog metaphor =
  recognize the KIND and carry approach knowledge for the kind.
  Architected same day → `docs/RUN_TEACHINGS_DESIGN.md` (dormant-design,
  reading queue): teaching kinds terrain/landmark-class/self, extraction
  from the TRAIL not summary[:500] (the verified structural root cause,
  three ways), scope field, probe-verified terrain, cuts-time injection.
  4 provisional DECISIONs await ratification. Warm arm e2f4578b running
  concurrently — its cost tier is the pre-registered live test of what
  today's funnel can do without this design.

- **2026-08-01** — Portable-learning transport health pass (Jeremy: "make
  sure our import/export shareable run data stuff is in good shape…
  since we are already 2 boxes deep in some cases"; context: "real
  progress on compound thinking in our test thread on the M1"). Both
  lanes exercised end-to-end on REAL workspace data, hermetically
  (scratch targets, live workspace untouched): maro-pack
  export→scrub→import→adopt (317 skill records, 4 rules→hypotheses, 145
  lessons→MEDIUM, skill quarantine→adopt; scrub clean — 0 identifier
  leaks, 52 [HOME]/5 [USER] redactions) and maro-import machine-merge
  (real run dirs incl. LT-1's 4bf7f761-merry-magpie with
  imported_from.json provenance, decisions.jsonl travels via the ledger
  glob, exact-line dedup idempotent on re-run). One real hole found and
  fixed: the provenance-gate stamp (minted_from, 2026-07-29 — post-dates
  the pack) did NOT survive transport — import dropped the field AND
  bypassed the classify choke point, so export→import laundered a
  quarantined lesson into an injectable one (db37d525 class, via pack).
  Fixed both halves: export drops quarantined rows
  (quarantined_rows_skipped rides the manifest; protects pre-gate
  importers like PyPI 0.8.0), import carries the stamp + re-classifies
  unstamped rows + carries provisional; live filter verified on the one
  real quarantined lesson in the medium store. lesson_type vocab de-duped
  to knowledge_web._LESSON_TYPES. Pinned:
  tests/test_pack.py::TestProvenanceTransport. Filename-scrubbing
  known-gap stands by decree (revisit on a real case). Design doc §3
  addendum: docs/PORTABLE_LEARNING_DESIGN.md.

- **2026-08-01** — Pack-transport adversarial review (post-land on
  4d9f995; 2 Codex lenses, PASS with fixes; 6/6 verified, 0
  hallucinated — streak holds). The laundering fix held; the border
  still trusted the STAMP'S VALUE: noncanonical/foreign-claimed
  minted_from carried verbatim past the exact-match quarantine (both
  lenses independently), non-string source_goal TypeError'd into a
  silent clean import, non-object JSONL rows crashed export (DEV_PATTERNS
  #7 again — non-dict payload), all-quarantined artifacts vanished with
  their skip count. All four fixed + pinned (stamp enum-normalized,
  classifier runs on EVERY row, conservative union — either side saying
  "prompt" quarantines; origin "outcome" citizenship does not survive
  the border, contested-by-birth applies to provenance too). Two
  residuals accepted with named triggers, both empirically grounded
  (imports.jsonl: no real pack import has ever run — no laundered rows
  exist anywhere). Record:
  docs/history/2026-08-01-pack-transport-adversarial-review.md.
- **2026-08-01** — Lesson-corpus surprise read, chunk 1 (Jeremy; L1–L4 +
  M1–M15; verbatim reads + tally:
  `docs/history/2026-07-31-lesson-corpus-surprise-read.md`). Mirroring
  collapse REFUTED (11/19 flagged), but the rubric needed a direction
  split the binary missed: positive surprises **M4 + M12** seed the
  Δ-gate; negative surprises **L4, M6, M8, M9, M13, M14** are
  operator-certified contradiction/retirement candidates (first real test
  corpus for retirement-by-contradiction — nothing has ever been retired
  that way); **M7/M11** are system-gap flags routing to the existing
  side-quest-identification and fetch-verb threads. Standing taste signal
  (4 of 11 reads): lessons minted at the wrong altitude — *how* (named
  procedure) where they should record *what* ("2 trusted sources");
  candidate mint-time rule, awaiting Jeremy's confirmation before it
  reaches runtime. Raw-corpus reading judged too tedious as an
  instrument ("figure out a better way to get this information") —
  remainder re-issued as 8 family judgments + optional 5-row tail sample.
- **2026-08-02** — Three decrees from the sidequest review (Jeremy; pipe
  to runtime journal owed next box trip). **(1) Build-in-up-front
  posture:** for capability classes we can see coming (first instance:
  RLM-style REPL-reading of large documents), don't wait for maro to
  discover them through friction — "let's build it into the system as a
  direct capability up front; either as a skill… or whatever might be
  lightweight and maintainable over time." repl_reading skill greenlit
  (BACKLOG sidequest item; A/B against the registered $11.25/3.45M 3b
  baseline). **(2) Continuation identity RATIFIED:** resume ≡
  uninterrupted run (same id, same mechanisms, one outcome row);
  new-attempt = own run + archaeology tie (parent id). Discriminator =
  terminal-closure stamps; ambiguity fails toward restart. **(3)
  Deletion safety:** UIs/tools deleting goals/runs must not silently
  destroy data referenced by another run — resolved as
  reverse-lookup-and-surface on the run-ref index (no copies, no
  refcounts), per the retention decree. Context note recorded with the
  sidequest: my "the RLM articles aren't in the link farm" was FALSE —
  searched the box's 315-post snapshot frozen 2026-04-11; all six posts
  postdate it (fresh clone: 747 posts). Fifth bite of the denominator
  family; durable rule added: absence claims require the corpus vintage
  to cover the period where the thing would exist. Side-finding: the
  box's lf- knowledge import is 432 posts stale — resync+reimport filed.
- **2026-08-02** — Run-teachings DECISIONs adjudicated (Jeremy, from the
  reading queue). **§4b RATIFIED** — extend the tiered store, don't build
  a new one — with a wider decree attached: *"there will be multiple
  kinds of learning just like there are multiple kinds of steps, and the
  same mechanism should handle the lifecycle (and likely there will be
  nuance between the flavors)."* (Piped to runtime journal this session.)
  **§4a + §4d HELD** — Jeremy recalls the earlier probe discussions
  concluding checks are *planned as steps*, not machinery-executed
  ("that was a possible step itself based on research findings"), tied to
  the bridge-building sub-goal arc; candidate exception: hypothesis
  already backed by data (proving vs gathering). He didn't author those
  decisions — session-record dig dispatched to recover the original
  before ratifying; §4d's mint-time probe shortcut rides on the same
  answer (and as-written §4a/§4d contradict each other — flagged in the
  doc). **§4c NEEDS CONTEXT** — worked-examples expansion added to the
  design doc (§4c-expanded); two residual sub-calls queued for his read:
  terrain at its own pre-planning surface?, self-teachings fully out of
  runs or caveat-grade visible?
- **2026-08-02** — **What-not-how mint rule CONFIRMED** (Jeremy, closing
  the surprise-read altitude critique): lessons record the right *result*
  to ask for, not the procedure — *"if we're asking for work, how is ok,
  but usually we aren't — so asking for the right result is the more
  important part."* He connects the prediction-contrast practice to the
  same principle (registered result-predictions, not procedure specs).
  Piped to runtime journal this session; mint-site implementation pass
  queued in BACKLOG (acceptance: chunk-1 negative examples re-mint as
  observations).
- **2026-08-02** — **Retirement-by-contradiction GREEN-LIT** (Jeremy:
  "might be time to level the decay up, I'm fine with improving this").
  Wire the lesson store to the contested/grey-flip contradiction flow
  rules already have; acceptance corpus = the six operator-certified
  contradictions from surprise-read chunk 1 (L4, M6, M8, M9, M13, M14).
  Queued in BACKLOG.
- **2026-08-02** — Probe-timing record dig COMPLETE (follow-up to the
  §4a/§4d hold; forensics:
  `docs/history/2026-08-02-probe-timing-record-dig.md`, citations
  re-verified against sources). Jeremy's recollection was exact: the
  2026-07-27 §14 decision (`7061e85e`) put discriminating tests in the
  PLAN (step diagnoses + recommends via `blocked_on`; planner routes),
  with the substantiate-within-granted-scope carve-out as the
  proving-vs-gathering exception; claim_probe's machinery execution is
  confined to settling existing claims at the review layer; the
  2026-08-01 recon slice explicitly cut probe-execution-at-verify. No
  prior decision on teaching-probe timing exists — §4a/§4d originate
  provisional with the UU-2 session. Dig also surfaced the unnamed §4d
  deadlock (provisional never injects + probes only at injection ⇒
  terrain never confirms) and recommends **probe-gated first injection**
  over the mint-time shortcut; §4a/§4d remain HELD awaiting Jeremy's
  call on that pair, with the §14 consistency requirement pinned (probe
  failure ⇒ grey + record only; recovery routing stays planner-owned).
  "Bridge-building sub-quest arc" identified as the capability-acquisition
  side-quest thread (Phase 27 → chasm/balloon → 07-27 "toolset to cross
  the chasm" ratification) — same exchange that produced §14.
- **2026-08-02** — **§4a/§4d RATIFIED via probe-gated first injection**
  (Jeremy, post-dig: "agree, let's do as you proposed, that seems to work
  well there"). §4a as amended: probes never execute at mint; they run
  under the read-only guard at injection time (staleness re-check for
  confirmed facts, confirmation gate for provisional terrain). §4d
  rewritten: the mint-time shortcut is out; a provisional terrain
  teaching may be offered for injection iff its probe passes at that
  moment — the pass IS the confirmation event; a failure flips to grey
  with the record kept. §14 consistency requirement pinned throughout:
  machinery never self-serves recovery — routing stays planner-owned.
  All four run-teachings DECISIONs now adjudicated (4b/4a/4d ratified;
  4c's two sub-calls still open on the reading queue). Standing context
  from Jeremy same message: M1 sessions are landing concurrent changes;
  "feel free to dig in where it makes sense" — green-lit BACKLOG chunks
  are the sanctioned lane.
- **2026-08-02** — **Retirement-by-contradiction SHIPPED** (the green-lit
  chunk, same day). `TieredLesson.contested` mirrors the standing-rule
  grey flip: `contest_lesson()` stamps both stores (flat ledger included
  — UU-4 shared ids; it feeds recall/bootstrap independently, so a
  tiered-only flip would have leaked), excluded from every injection
  surface, never promotes/confirms; adjudication's lesson-branch honest
  no-op replaced with a real contest; operator verb `maro-memory
  contest`. Two design points worth remembering: (1) dedup re-sightings
  bump `times_reinforced` only — score and decay anchor FREEZE, so a
  frequently re-derived contested MEDIUM row still retires on the decay
  schedule, and the frozen sighting count is the evidence input for a
  future lesson-refight slice (the V1 cut: contested is sticky, no
  un-contest verb until that slice); (2) for decay-free LONG rows
  contestation IS the retirement mechanism — there was previously none.
  Acceptance corpus applied live: all six chunk-1 contradictions
  contested with Jeremy's verbatim reads as reasons (L4=6287e494 long;
  M6=9d6b63fe, M8=c304b9b2, M9=c85c9a09, M13=47e8f5e3, M14=655ea616);
  injection/query leak-check clean; 6 LESSON_CONTESTED events on the
  captain's log. Tests `tests/test_lesson_contested.py` (15) + the
  superseded honest-no-op pin rewritten; full suite green.
  *Correction (same day):* "full suite green" at land was FALSE — the
  commit shipped two EVENT_TYPES without census contract rows, both
  captains-log censuses went red on CI (and locally: the green claim
  came from reading `$?` after a `| tail` pipe, which reports tail's
  exit, not pytest's — verification theater). The M1 session fixed
  forward within the hour (`6920046`, pin 75→77 + doc rows); suite
  re-verified green at converged HEAD with the exit code captured
  directly. Standing lesson: never judge a suite through a pipeline.
- **2026-08-02** — Contest-on-bad-provenance rule (Jeremy, decree-class;
  runtime journal pipe owed): asked whether to retire three lessons minted
  from a run whose achieved=False was a provenance-guard false positive —
  **"if they are real (even with bad provenance) we should in theory
  re-learn them, so we just waste efficiency, not correctness."** All three
  contested (80f47016 tool_missing, 65e49b27 tool_preflight, 50c68716
  self_cert_unreliable), both stores, leak-checked clean across tiered
  injection / flat injection / load_lessons. The rule generalizes: a lesson
  whose PREMISE is contaminated goes, regardless of whether its advice
  might stand — re-derivation from honest evidence is cheap; a false
  premise injected into future runs is not. This is the first live use of
  the other session's contest verb, and the first case where the
  contamination came from a MACHINE error rather than a bad run.
- **2026-08-02** — **Certainty vs probability, and recovery over
  correctness** (Jeremy, decree-class; posture, not a mechanism). Prompted
  by four brittle closure checks that all failed for check-design reasons:
  **"let's try and be careful about absolute certainty vs probability
  based thinking. We don't have to know for sure all the time (sometimes
  you can't know, sometimes you make do with the information you have, and
  sometimes 2 + 2 is provable and we can be certain)… at some point it
  becomes more simple to accept the coarse grained truths and slide over
  the details than it does to get mired in the details and churn on
  figuring out why we're not quite right. Uh… good enough is good enough?
  easier to do with data, much harder to do with decisions and that's
  where it taints the downstream in various ways."** And the second half,
  which is the actionable one: **"I think more often it's less about being
  correct up front and more about how well you recover when you're wrong.
  Both versions of incorrect can lead to a production outage and impact
  recovery time; each has their pros and cons and each has efficiency and
  waste in them; not right or wrong, just problem set trade-offs."**
  **This replaces the framing I proposed the same day.** I had written
  "keep deterministic guards advisory unless their matching is provably
  exact" — a correctness frame, and probably unachievable. The two live
  incidents say otherwise: the provenance guard and closure's checks were
  *both* wrong, and only one caused damage. The guard was FULL-trust with
  no downstream able to overrule it, so a brittle match silently flipped
  an honest run to achieved=False. Closure was equally wrong four times
  and recovered inside the same run, because a judge sat above the checks.
  **The design rule is therefore about recovery paths proportional to
  confidence, not about accuracy:** a verdict layer that admits no
  overrule must earn that standing, and most should not have it. Applies
  directly to the open watch-item that closure CONFIDENCE is contaminated
  by check-design quality — the fix is not better checks, it is making the
  score decomposable so "the work is weak" and "our checks are weak" route
  to different recoveries. First application: `scripts/run_readout.py`'s
  triage classifies only what it can classify with confidence and dumps
  the rest into a visible, countable residue rather than guessing.
- **2026-08-02** — **False premises: best-guess what was meant, don't
  eliminate the bad question** (Jeremy; runtime journal `f7cb7c3a`):
  **"People have false premises all the time (bad upfront prompt) and it
  causes all sorts of problems. In that framing we don't want to eliminate
  that possibility, but we do want to make our best guess on what the user
  meant; that might mean an immediate ask for clarification or making some
  assumptions… both the behavior and the general 'it's ok to ask a bad
  question' type arc."** Plus the follow-through: *"If we can unlock better
  behavior with prompting up front, and lean into the corrections in the
  reporting angle… a direct answer, and a good educated guess for
  additional downstream findings/data… getting more than what was asked
  for 'free'."* SHIPPED into `EXECUTE_SYSTEM` (`f0c51d1`), not the intake
  pass — the `clarity check` already runs and already carries an unused
  `question` field, but it measures **clarity, not soundness** and passed
  two clear-but-FALSE goals the same day. A false premise is also
  *discoverable*, so by that check's own rules it must never become an ask.
  Both additions cost nothing when idle: the premise rule emits only when a
  premise is actually false; bonus findings are final-step-only, capped at
  2, observed-not-speculated, and omittable. Precedent both directions the
  same day — `ea4ebe4a` corrected my false premise from evidence and still
  delivered; `fcc12c02` silently absorbed a redundant correction for a fact
  it already held. The capability is present; what was missing is the
  invitation and the timing.
- **2026-08-02** — **Contested verdicts are ignored for learning, not
  suppressed** (Jeremy, wording his own): asked whether to stop
  failure-flavored learning on runs where the provenance guard and closure
  disagree — **"b sounds good, though I might phrase it like 'we ignore
  contested results for learning'… add them as anecdotes, but don't move
  for or against the learning either way. Suppress sounds more like block
  and ignore."** The distinction is load-bearing and matches the standing
  decay-trust-never-data constraint: the outcome row is still written with
  its full evidence; it simply doesn't get a vote. SHIPPED by routing
  contested loops into the EXISTING unresolved-verdict-audit lane
  (`skip_loop_ids`) rather than inventing a suppression path — that lane
  already means "recorded, learning deferred." Motivating damage: #5's
  false demotion minted three lessons on a fabricated premise that had to
  be hand-contested. Companion half: `provenance.contested_by_closure`
  records the disagreement in metadata so it is findable at all.
- **2026-08-02** — **What-not-how mint-form pass SHIPPED** (the second
  green-lit chunk; decree entry above). Shared `_LESSON_FORM_RULES`
  composed into `_REFLECT_SYSTEM` and `_STEP_LESSON_SYSTEM` (deferred
  extraction rides the former); thinkback `key_lessons` scoped to
  observation form while step reviews/retry_strategy stay prescriptive —
  the decree's "asking for work" carve-out made explicit at the one site
  that does both. Deterministic finalize templates reframed
  (`_recovery_plan_lesson_text`/`_auto_diagnosis_lesson_text`: diagnosis
  as observation, action marked advisor-proposed unverified; prefix and
  determinism preserved so existing pins and dedup-reinforce hold).
  Design points: (1) structural M14 fix — reflect/deferred/finalize
  tiered mints stamp `evidence_sources=[loop:<id>]` (all minted `[]`
  before; Phase 60's citation penalty finally has something to reward)
  and reinforce merges incoming evidence refs capped at 8, giving rows a
  "repeated across runs X, Y" record instead of a bare counter —
  contested rows excluded (their frozen counter stays the refight-slice
  input); (2) seed-reader side-find — `_seed_lesson_block` could serve
  contested-LONG L4 as the style example, now filtered alongside
  quarantined. Live acceptance on the real adapter: all three certified
  shapes re-mint clean (M13 → disjoint-sources requirement as
  observation; M9 → "blocker in both this attempt and the prior
  dispatch; recurred despite retry"; M14 → no self-credit clause).
  Honest residual: LLM output can still phrase a soft "should" when it
  cites its evidencing cost in the same sentence — that is observation-
  grounded advice, not the certified defect; not chased.
- **2026-08-02** — **Guidance-form decree (Jeremy, decree-class, playbook
  surprise read):** "I think we want to say 'usually, do this' instead of
  'it has to look like this'. Otherwise we run the risk of an LLM doing
  what we ask, rather than what it might be capable of." Injected
  guidance (playbook, and by extension any every-run prose) is a **prior
  the run weighs, not a requirement it obeys** — command-form guidance
  caps capability at the guesser's imagination. Companion certified call:
  step-count caps for narrow goals are "the same as a 200 char limit on
  output; we simply don't know what a run might take" — inappropriate,
  removed not rephrased. Applied same day across live playbook + repo
  seed (see next entry). Sibling of the what-not-how mint decree
  (2026-07-31): that one governs what MEMORY mints, this one governs how
  INJECTED GUIDANCE speaks.
- **2026-08-02** — **Playbook surprise read APPLIED** (second
  self-maintained corpus operator-read; reactions + full disposition in
  `docs/history/2026-08-02-playbook-surprise-read.md`). Rewrite of live
  playbook + `_SEED_CONTENT` per certified reads: P2 step-cap removed;
  P3/P4/P13/P14 → usually-form; P8 hardcoded path → `config.
  workspace_root()`; P9 retired (blocked-step investigation is systemic
  now — his "should already be in step meta-data?" hunch was right);
  P10 "Reject vague goals" → clarification-first (his multi-step-
  sidequest concern — nothing structural denied them, the injected prose
  was the risk); P17/P21+P22 completed from recovered untruncated
  originals; P18 July TODO + all 4 frozen drift alarms + all 4 parked
  Signals removed (P24 answer: signals were NOT post-run work that
  happens — `auto_enqueue_signals` defaults false and nobody reviews the
  hold queue). Root cause of the truncations fixed: bare
  `suggestion_text[:200]` at both evolver append sites removed;
  `append_to_playbook`'s 500-char honest-ellipsis cap is the only clip.
  Pre-rewrite state archived to `playbook_history/`. Mechanism gaps
  (alarm expiry, signal review surface) → BACKLOG.
- **2026-08-02** — **Decision-list batch: Jeremy answered 8 of 9 pending
  asks in one message** (decree-class; individual entries follow). #4
  (LT-1 batch) explicitly left to the M1 session ("let's leave this to
  the other session"). His close: "that's not everything, but should
  cover a lot of that" — thread-structure Proposal v2 reaction and §10
  scoping discussion remain open. Addendum to the contest-on-bad-
  provenance entry above — the go he gave this session carried the
  rationale in his own words: **"contest loses us efficiency, but helps
  stay correct… we might churn way worse with some bad assumptions
  rather than contesting and re-learning."** (Contests were executed by
  the M1 session 16:57; runtime journal pipe done this session — was
  owed.) New idea seeded in the same breath, captured to BACKLOG:
  **"we should have a way (eventually?) to figure out invalid
  assumptions, more than just failures at a micro level… not sure what
  that looks like yet."**
- **2026-08-02** — **§4c-expanded ANSWERED (terrain + self-teachings),
  plus a new planner ask** (Jeremy, decree-class): terrain planning gets
  the same surface shape as the existing planner "just as a different
  piece… seems maybe cleaner to have them separate to start" — a
  separate call in addition to the planner (maybe parallel), with his
  tension named for the record: **"the overall planner should be doing
  this already IMO, so this might be a pre or post step for the planner,
  depending on the approach."** Self-teachings: caveat-grade injection
  OK — **"injectable as a single win type seed for a learning"**, should
  factor well with import/export of maro data generally, **"as long as
  it has no solely time-based expiration… really we want learning to be
  data driven in all the shapes."** Folded-in NEW ASK: **"we need to add
  non-action types to the planner. I think 2 types of world facts:
  anecdotal/accidentally found and hypothesis type findings (pattern
  recognition/ideas)."** He skim-reviewed the doc, open to more
  discussion. READING_QUEUE row → Done; world-fact plan-item types →
  BACKLOG (design needed before build).
- **2026-08-02** — **Budget extension ladder decreed** (Jeremy,
  decree-class; bandaid, his word): **"out of budget becomes a
  notification the first time with a budget extension (just add a run's
  worth of budget for the extension), and the same for the second
  extension, with a pause-ask-user the third time in the same run. For
  now we want to get this working, I do want to be budget optimized, but
  I want it to work and be expensive rather than not work and be budget
  friendly. So keep the budget hooks, but no big stoppage yet for
  that."** Amends the 2026-07-29 caps=circuit-breakers decree at the
  enforcement edge only: the breaker still fires and still records, but
  firing now means notify + one-run-budget extension (twice) then
  pause-ask-user — not kill. Spend-UX decree still governs the
  notification's language (effort words on the conversation channel,
  dollars in internals).
- **2026-08-02** — **NODE_CANDIDATE promotion mirrors skills** (Jeremy,
  decree-class): **"same as skills, promoted to maro-local usable, up to
  the user to pick permanence. later we'll have to refine that UX
  (smells like auto-mode settings for prompting), but I think we need
  the user involved for permanent vs useful. And there might be another
  layer/process in there… Down the road though. Open to discussion on
  that if there might be a better answer now."** Shape decided, build
  queued (BACKLOG item updated); the structural tripwire pin on
  promotion symbols stands for whoever builds it.
- **2026-08-02** — **Link-farm lf- nodes are a third-party data
  resource, not maro knowledge** (Jeremy, decree-class): asked whether
  the end user should understand/see that link-farm informs goal
  research — **"no, that's a resource to use towards goals, not
  something the end user should need to understand or be aware of;
  treat like a 3rd party website for gathering data, because it is
  (just happens to be me doing both, they're overlapping but not the
  same)."** Disposition: lf- nodes stay OUT of live goal-context
  knowledge injection — consultable as a research corpus, never
  injected as learned knowledge. Resolves the TF-IDF injection-exposure
  caveat on the 2026-08-02 re-import and sets the edge-contamination
  item's fix direction (1).
- **2026-08-02** — **House-style memory import (b) on HOLD** (Jeremy):
  **"I have another arc I'm working on at work (a full panel review
  skill that is codeLikeJeremy on steroids) and I think we can steal
  some more ideas from that in a few days, when that's a bit more
  fleshed out."** Revisit when he brings it (~few days from
  2026-08-02).
- **2026-08-02** — **Budget extension ladder SHIPPED** (same-day
  follow-through on the decree above). Between-step token and cost
  breakers in `loop_execute` now walk the ladder when work remains: one
  shared breach counter per run; breaches 1–2 add one run's worth (the
  ORIGINAL cap) on top and continue with a `BUDGET_EXTENDED` log row
  (dollars internal) + `effort_note` in effort language; breach 3 ends
  the loop `interrupted` with the new operator-class pause
  `budget-decision` (§13e vocabulary grew its budget value after all —
  the A/B-day residual reversed by his own call), so the follow-up
  rides the same-identity RESUME lane. Design points: cost extensions
  lift the runaway-meter ceiling in place
  (`llm.raise_cost_meter_ceiling`) or the meter would refuse calls at
  1.5x the ORIGINAL budget and the extension would be dead on arrival;
  explicit 0 budgets still mean "spend nothing" (never extended);
  final-step carve-out, runaway circuit, daily gate, and the
  max_iterations continuation lane all unchanged. Killswitch
  `budget.extension_ladder` (DEFAULTS.md row) restores the hard stops
  — the pre-existing hard-stop pin now runs under it. Bonus fix while
  there: a paused run's channel line was the generic "error: Loop
  ended with status: interrupted" — typed pauses now emit a "paused"
  event in plain words (delivery decree). Full suite green (7501).
- **2026-08-02** — **NODE_CANDIDATE → active promotion SHIPPED** (V3
  battery find; same-day follow-through on the "same as skills" decree
  above). `knowledge_web.promote_knowledge_candidates()` flips earned
  candidates: times_applied ≥ 2 AND confidence ≥ 0.4 — on a candidate,
  times_applied counts exactly the bridge's dedup-upsert re-observations
  (the injection bump touches ACTIVE nodes only), so the gate reads
  "independently re-derived at least twice since mint". Gate is
  epsilon-tolerant (two +0.05 float bumps land at 0.3999…, a bare ≥ 0.4
  would hold every legitimately earned node forever). Mirrors skill
  promotion end to end: rides `run_skill_maintenance` with the same
  adapter, optional LLM gate stamped passed/unjudged/skipped
  (fail-open), `KNOWLEDGE_NODE_PROMOTED` captain's-log event
  (user-surfaced, census 80), cap 10/sweep. lf- reference-corpus nodes
  never promote (third-party data can't launder into maro-learned
  knowledge). The `TestCandidateInvisibilityPin` tripwire was rewritten
  to exercise earning: born invisible → fresh mint doesn't qualify →
  re-observed 2x promotes and surfaces through the live recall chain.
  Permanent-vs-useful user gate deliberately NOT built — that's the
  "auto-mode settings" UX layer Jeremy parked for down the road.
- **2026-08-03** — **lf- disposition hardened** (out of Jeremy's
  "shouldn't lf- promote like any other 3rd party data?" question —
  answer: promotion is the trust verb for maro-DERIVED knowledge, and
  genuine third-party data never promotes because it never enters the
  store; lf- is the anomaly that got materialized INTO
  knowledge_nodes.jsonl, and the prefix carve-outs make it behave like
  the external website it is). Two holes found answering it, both
  closed: (1) the second import lane (`knowledge import-links` CLI verb
  → knowledge_web.import_link_farm) minted UNMARKED rows — dodging the
  query exclusion and the promotion skip by construction; never used
  against the live store (census: 747 lf-, 394 bridge candidates, 0
  unmarked third-party rows), now stamps the lf- prefix and mints
  ACTIVE like the script lane. (2) The bridge's dedup upsert matched
  against lf- rows — a maro lesson title-colliding with a reference
  row (Jaccard ≥ 0.7) would have reinforced the third-party row
  instead of minting a first-party node, silently swallowing the
  learning into a corpus that never injects and never promotes. The
  bridge now skips reference rows when deduping. The lane by which lf-
  data DOES earn trust is unchanged and correct: a run consults the
  reference corpus, succeeds, the bridge mints a maro-authored
  candidate citing the lf- URL in sources, and THAT earns promotion by
  re-derivation.

- **2026-08-03** — **Arbitrary truncation is a standing concern, not a
  one-off bug** (Jeremy, decree-adjacent): *"this was one of the first
  truncations early on and I've been uncomfortable making those trades
  for 'keeping the context small' by cutting so much… there are still
  way too many arbitrary truncations for my liking at this point."*
  Context: he asked why the quality gate truncated evidence at all, after
  I had fixed the *symptom* (a judge escalating on absence) while
  defending the cut with an unmeasured claim that widening "costs tokens
  and only moves the cliff". Measurement inverted it. **Standing
  posture: a numeric cut on evidence is a decision that needs a
  measurement, not a default** — pull the real distribution from
  `runs/*/build/loop-*.json` (`result_length`), tabulate `cut → %
  payloads intact / % text shown / median extra tokens`, and let the
  answer fall out. Cuts that feed a JUDGE are the highest-harm class
  (they fabricate verdicts); cuts that feed a PROMPT degrade quietly;
  cuts that bound a STORE lose fidelity forever and are the only ones
  with a genuine cost trade. Shipped under this: gate 600→4000
  (`065a010`), closure work summary 300→4000 (`0f8409f`), NOW
  self-verdict marked, plus the two epistemic guards (`f7b775c`,
  `f4ef704`). Remaining worklist in BACKLOG "Arbitrary-truncation audit".

- **2026-08-03** — **LeAct Δ-gate: the revisit trigger has FIRED**
  (measurement, not decree — Jeremy's ask was "make sure our maro
  backlog and work is on track" against a second Opus-5 blind contrast
  of the repo, `~/claude/opus-feedback.txt` Aug-3 section). The
  2026-07-31 entry above says *"build stays gated on the verdict-flow
  revisit trigger"*; **that gate is now open and this supersedes it.**
  `src/verdict_flow.py` reads **67 judged rows** (was 4 known-arrival at
  2026-07-31) with arrivals two weeks running — W31 18, W32 9; sources
  closure 56 / provenance 6 / deterministic_tests 2 / now_self_verdict 2.
  Prerequisite census found **all four Δ-gate ingredients live**, one of
  which the backlog still listed as blocking: record-mode capture went
  live 2026-07-29 (`build_adapter(auto)` always wraps in
  `FailoverAdapter`), so **116 run dirs carry replayable per-call records
  — including the LT-1 arms themselves** (`01e55212` 20 calls,
  `2738d9c0` 45, `d9607baa` 30). The pre-registered held-out set and the
  replay-fixture source are the same runs; the "replay fixtures need
  another source" correction is retired. Contrast's framing, kept: *the
  upstream half of the LeAct gap is closed (oracle-anchored learning),
  the downstream half (Δ) is not* — and that order was right, since Δ
  measured over a corpus polluted by runs that fabricated their own
  memory writes would have returned believable noise (LT-1 #6 says the
  pollution was real and invisible). Remaining work is **one wire**: a
  second, effect-based route to LONG beside `score >= 0.9 and
  sessions_validated >= 3` (knowledge_web.py:561, verified unchanged).
  **New row the backlog never carried across two contrasts:** the
  seed-reader still says *"emulate this style and specificity"*
  (memory.py:267) while LeAct Fig 3b found the no-verbatim stratum
  scored higher — sequenced BEFORE the Δ gate, since style-copying would
  contaminate the corpus Δ is measuring. Also newly noted: fail-open now
  **triples in the same direction** on the learning path (skills,
  artifact_check, and the 2026-08-02 node-promotion gate) — the cheap
  move is the judged-vs-unjudged denominator readout, not flipping any
  gate closed. Full amendment in BACKLOG LeAct entry (`015c4a3`).
  **Open for Jeremy: priority against SP / world-facts, and a go on the
  seed-reader A/B.**

### 2026-08-04 — Three exit-path fixes: slug collisions, evolver write-side, playbook alarms
Unblocked backlog work while the two LeAct decisions wait. Common shape:
mechanisms that were documented as working and weren't, and context that
could get in but never out.

**Project slugs (`da82f1a`).** `_goal_to_slug` is the first five words, so
"tell me about the book…" merged unrelated goals into one project and the
second read the first's artifacts as its own prior work — invisible to the
scavenge and provenance guards, because those files really are present.
`resolve_project_slug` is the single mint point now; it changes nothing
unless the slug carries no subject AND the hit project's recorded Mission
shares no subject word with the incoming goal, which is the 3b
"collision on subject = continuity" rule encoded rather than described.
Measured before landing: 757 run records, 296 projects, 22 multi-goal
projects, **0 would have split**. Two pre-existing holes closed with it
(loop_init stamps the resolved project into run metadata; two
`run_curation` recomputes now prefer it) — without those a disambiguated
run's artifacts get looked for in the project it was disambiguated away
from.

**Evolver write-side (`043e4bb`).** `GUIDANCE_FORM_RULES` puts the
2026-08-02 usually-form decree in the generator, not only in the seed the
operator fixed by hand; it reaches tiered lessons too, since `prompt_tweak`
mints one. And the dynamic-guardrail lane **had never loaded a row**:
`added_at` was written ISO and compared to epoch seconds, so the TTL check
raised and the per-row except dropped the entry whole; and `pattern` was
the LLM's prose, which as a regex needs a step to repeat the sentence.
Probed live: 1 row on disk, 0 loaded. Fixing only the stamp would have
armed a lane that writes sentences into a matcher, so both went together.
The existing test asserted the defect — it passed a regex as `suggestion`
and checked it landed as `pattern`, green all along because nothing ever
ran writer through reader.

**Playbook exits (`69923d3`, `c1eb565`).** Held signals proposed autonomous
work and were parked in the playbook "for human review" — a non-seed
section, so they ranked as *learned* and outranked the curated seed in
every director and decompose call, with no way out. They hold at the same
gate guardrails use now (apply = accept, `--dismiss` = the exit that never
existed), and `status`/`block_reason` joined the dataclass they had been
written past since the gate landed, so `--list` can finally see that a row
is held. Alarms got a key and a last-read date: same check re-read replaces
in place, silence past `playbook.alarm_ttl_days` expires it at curation.
Both alarm-minting scanners are templates, not LLM output, so the form
rules couldn't reach them — reworded here. **The falsifier for "mechanism,
not cleanup": two more calibration near-dups had accreted in the two days
after the operator collapsed the first batch by hand.**

**Contest persistence (`5adefab`).** `_reinforce_tiered_lesson` wrote the
caller's pre-lock copy of the row back wholesale — bystanders reloaded
fresh inside the lock, the target row alone did not — so a contest landing
mid-flight was reverted, and the contested check read that same stale copy
and granted a confirmation. Repro'd: `contested: False | score 1.0 |
sessions_validated 1`. Fixed by mutating inside the locked callback against
the fresh row.

Found while asking why `6287e494` — the **only** LONG contest ever
attempted, Jeremy's L4 surprise read on the tighter-max_steps recovery
lesson — showed `contested: {}`. The stale-write hazard is **not** proven
to be the cause there (that row saw no post-contest reinforcement), and the
disappearance stays recorded as unexplained rather than pinned on a
convenient culprit. **His decision is now in effect**: contest re-applied
to the live LONG store with his verbatim reason and original source stamp,
store backed up first, verified off the injection surface. For three days
the outcome he'd asked for had been silently reverted, with the lesson live
in injection at score 1.0. LONG is decay-free, so contestation is its only
retirement path — a lost LONG contest is permanent where a lost MEDIUM one
just delays a row that was going to decay anyway. Watch-item: a second
missing LONG contest is the trigger for a write-side read-back assertion,
one instance isn't.

### 2026-08-05 — LT-4 batch complete: 12/12 PASS, warm = −49% on identical arms

Jeremy's directed batch (his words: "run some tests from our general
list (2 each, as before, cold/warm), at least 3 different ones in the
research direction and 3 in the bridge-building direction"). Registered
before dispatch (`36afc98`), hand-scored against hand-snapshotted ground
truth, results landed (`4f7a2a8` cold, `066a41e` full). Evidence:
`~/.maro/workspace/output/lt4-logs/scorecard.md`.

What the batch established: **the warm delta is real now, and artifacts
are the carrier.** Byte-identical warm re-runs cost half of cold ($8.75
vs $17.28) with the correct reuse shape in every arm — verify-then-reuse
with fresh drift checks, re-validating perishable claims, attacking
weakest claims only. LT-1's "lessons alone carry ≈ nothing" baseline is
inverted by artifact/project-continuity carriers, which is the
artifacts-over-streams decree confirmed by measurement. B1w additionally
proved the SKILL store amortizes: cold-minted skills injected into the
deviation arm (different book) via skills_manifest, run out-performed my
own ground truth (found the 1859 first-edition "well-worn" vs 1897
"well-tried" one-word revision; I verified the scan by hand).

The finding family that matters going forward: **provenance claims
inflate beyond the event log while the data stays real** — B3 labeled an
unauthenticated jina render "authenticated CLI fetch"; B1w claimed
blocks "confirmed this session" with zero probe events; and B3w showed
the false label SURVIVES reuse (warm republished it untouched). Closure
can't catch any of this (it would need event-log grounding). Mint-time
claim grounding is follow-up (c) in the LT-4 BACKLOG entry. Also out of
the batch: skill dead-drop bug (standalone BACKLOG entry, reproduced
2/2 — workers write skills where nothing reads), silent closure-verdict
loss (R2w, bare except at handle.py:2293), and verdict-prior staleness
(12/12 PASS against 35–65% priors — recalibrate future batches on LT-4).

### 2026-08-05 — Run pages surface all allowed artifacts; briefs ride the payload, not a re-synthesis

Jeremy (2026-08-05, on the 83a2c805 Poe steal run): *"we probably need
a better way to give a brief of the outcome, in addition to all of the
artifacts. I can't actually see the steal_list.md without digging it up
from the box, we should probably upgrade the run page so it dynamically
surfaces the allowed artifacts (functionally for now, and at some point
a bit more UX love would go a ways…). And also maybe consider a way to
get a better summary."*

Run-page half SHIPPED same day: `locate_deliverables` serves every
ranked candidate (cap 12, step-N logs excluded, `served_artifacts` on
the card) and the runs index links each `<run>/artifact/*` servable —
the viz allowlist already permitted the path; nothing had linked it.
83a2c805 backfilled (steal_list.md clickable, verified 200). Summary
half diagnosed, not built: the return event already carries a decent
LLM `answer_summary`; the mush came from the payload's top pick being
the recovery loop's AUDIT_NOTE plus demoted-verdict framing. Follow-ups
(artifact URLs in the completion event, primary-loop-first ranking,
truncation-cap check) in BACKLOG § Typed dispatch envelope →
return-path quality.

### 2026-08-06 — PROMPT truncation worklist closed (two sessions, one merge)

The remaining rows of the arbitrary-truncation audit's PROMPT worklist
are done. Two sessions hit the list simultaneously and found the same
method lesson from opposite ends: `54a4be7` (other session) proved
`memory.py:341` was a cut nothing reached — the real loss was
`loop_finalize`'s 80-char summary, the only evidence lesson extraction
ever sees — while this session found `step_exec`'s 600-char hand-off was
a cut everything reached but widening it alone was inert, because
`team.firewall_shared_ctx` re-clipped to 200 two frames downstream.
**Follow the value end to end; the tightest hop is the only one that
matters.** Merge per the coordination note left in BACKLOG: `54a4be7`'s
per-step-breadth versions kept on `loop_finalize`/`memory`/`step_exec`
+ STORE-profile `context_budget`; this session added the universal
honest-cut idiom `context_budget.clip(text, cap)` and closed the rest —
director review (verdict-feeding → judge-window 4,000) and compile,
team firewall 200→1,000, attribution 300/500/500→1,500/2,000/2,000,
knowledge_bridge 300/500→1,500/2,000, evolver digest 80/200→300/500.
Still open from the audit: introspect lens widths (folded into the
wide-view-seat design question) and the STORE retention decision
(Jeremy's — now load-bearing, since outcome rows carry real evidence).

### 2026-08-06 — Skill promotion was structurally dead for 8 weeks; fixed, capped, and the fail-open readout ran

The judged-vs-unjudged denominator readout (the LeAct amendment's named
"cheap move") ran and found the question moot for the biggest gate:
**zero SKILL_PROMOTED events since 2026-06-11.** The promotion gate read
`Skill.use_count`, whose only writer was removed 2026-07-29 as dead code
— a permanently-zero counter, so no skill could ever promote. The
validation harness Jeremy ordered 2026-08-01 ("let's fix the promote
validation") was wired onto a gate that could not fire. Store census:
376 provisional / 0 established; 134 skills eligible on real usage
(SkillStats: top skill 783 uses at 98.2% success, provisional forever).
Fixed by gating on `max(use_count, SkillStats.total_uses)` with a
10-promotions/sweep cap (the node-promotion shape) so the backlog
drains over ~14 sweeps rather than one 134-skill LLM-validation burst.
Same dead-lane family as the dynamic-guardrail lane and the skill
dead-drop. Node-promotion gate: denominator legitimately empty (3 days
old). artifact_check: the judged bit is never persisted — that
denominator is retroactively unmeasurable; cheap wire named in BACKLOG
(count judged/unjudged into loop-log totals). Watch: first maintenance
sweeps will emit up to 10 SKILL_PROMOTED events each with
validation stamps — the readout becomes readable then.

### 2026-08-06 — LT-5 sonnet arm: verdict flips to achieved at LOWER cost than the haiku baseline

Jeremy's directed re-run of the 83a2c805 steal analysis with sonnet as
the min bar (byte-identical goal, fresh project, benchmark class):
**done/success/achieved, provider $3.69 vs the haiku baseline's $3.87**
— 8 steps vs 12, the tier premium repaid by not needing a recovery
loop. Compound-thesis evidence cuts both ways honestly: quality and
verdict improved with tier, AND the failure families recurred at
sonnet (claims-vs-events provenance confabulation with zero backing
events; container mount-blindness stated as machine fact) — they're
model-independent, but a stronger model routed AROUND the mount trap
rather than falling in. Refined claim, per the pre-registered
prediction that took damage: structure sets the traps; tier changes
how often you fall in. New specimen: the in-run adversarial reviewer
dismissed a TRUE discovery (the same-day a0bae77 skills.py fix) on a
"hallucination signature" heuristic without probing — the
positive-evidence principle binds skeptics too. Full scored record:
`~/.maro/workspace/output/steal-rerun-sonnet/predictions.md`. Also
shipped on Jeremy's nit: report pages now badge "loop done — run
finalizing" while closure/curation/evolver still run (his "stuck?"
read was the page conflating loop-terminal with run-terminal).

### 2026-08-06 — Backlog cherry-pick sweep: three more no-decision items closed

Continuing Jeremy's standing directive ("cherry pick from the backlog
to close some things out"), three landed chunks: **65d05da** —
claim-verifier resolves relative claimed paths in subdirectories (the
bounded tree walk now indexes relpaths; whole-path suffix match, so
"tests/test_ledger.py" verifies when the goal pointed into a subdir
while wrong-dir claims still can't match; third-in-family false
positive, first two cost verdicts). **e0cd654** — token-budget breach
note now carries ~$ est. spend alongside the token count (tokens
proved the misleading unit: cache reads bill ~0.1×). **a1add58** —
mount-blindness fixed all three legs (MARO_MOUNT_VIEW env marker,
PARTIAL VIEW step-prompt block for introspection-flagged container
runs, closure "not a git repository" → inconclusive + plan-prompt
rule). Side-find: the 08-06 test_unverdicted_is_marked fixture's
importlib.reload split dataclass identity under xdist — 4
ordering-dependent failures in test_verdict_learning; reloads removed
(both modules read env at call time). Suite 7700 green. Remaining
backlog items in this sweep's scope are all decision-gated (world-facts
§7, LeAct A/B go, LT-4 direction, STORE retention, evidence-lens seat).

### 2026-08-06 — Decisions: mint-grounding shape, skill dead-drop direction, seed-reader A/B go

Three Jeremy calls in one message (~01:30 local):
1. **Mint-time grounding = evidence annotation, not another judge.**
   Quote: "Agree with your rationale; evidence will help with certainty,
   we don't need a fresh set of eyes for this with another judge."
   Scope ratified: start at the learning-store mints (lessons, skills,
   teachings), annotate-with-receipts fail-open; fail-closed only where
   reuse republishes (B3w propagation). Run-narrative grounding rides
   the same probe later.
2. **Skill dead-drop fix = promotion-side ingest** (my recommendation,
   his "I don't have a strong opinion... let's go with your
   recommendation"). Project-dir skill files enter the ONE vetted
   write-path (provisional mint + validation harness + provenance
   stamps), not a second parallel promoter.
3. **Seed-reader style-exemplar A/B: GO, subagent-managed.** Quote:
   "I'm ok starting a subagent to manage the A/B testing." memory.py:274
   "emulate this style and specificity" vs LeAct Fig 3b's
   diversity-preserving form; runs BEFORE the Δ gate per the queue row.
   World-facts / LeAct docs remain queued ("still feels a bit like
   homework, it will happen eventually").

### 2026-08-06 — Seed-reader A/B ran; S2 exemplar removed on the verdict

Same night (~02:15 local), subagent-managed per decision 3 above, 13
runs × 2 arms, boundary-clean (no store writes, no repo edits by the
harness). Verdict **inconclusive-leaning-supported**: no verbatim style
cloning (zero seed-bigram overlap either arm — LeAct's strong claim not
reproduced at this n), but the seeded arm minted the seed's lesson_type
3.5× as often (6/25 vs 2/29 across 6 unrelated runs, Fisher p=0.125)
and its lessons were ~60% more homogeneous cross-run (jackknife-stable)
while every quality proxy stayed flat — the exemplar bought nothing
measurable. The then-top seed was itself procedure-form, i.e. "emulate
this style" pointed at a what-not-how violation. **Acted same session:
S2 seed block removed from the extraction prompt** (tested arm WAS
removal; good-system-citizen: remove, don't disable). Redacted-guidance
successor deliberately not adopted — untested; the harness at
`~/.maro/workspace/output/seed-reader-ab/` reruns cheaply if wanted.
Pin: `test_mint_form.TestNoSeedExemplar`. This clears the Δ-gate's
corpus-contamination precondition; the gate now waits only on Jeremy's
priority call (vs SP / world-facts).

### 2026-08-06 — Decision: Δ-gate priority CALLED; next session builds it

On session wrap (~02:40 local), after the A/B readout: "The future is
now" + **"Sure, prep the gate for the next run, which will be after I
clear the session."** The owed priority call is made: **Δ-gate ahead of
SP / world-facts**, and the next session's work is the build. Prep
landed same session: `docs/DELTA_GATE_BUILD_BRIEF.md` (the one wire,
scouted surfaces, sequencing instrument→stratify→wire→census, named
falsifiers, slice-1 cuts) + MILESTONES queue head. World-facts §7 read
stays queued ("homework").

**Executed 2026-08-06/07 overnight (the sanctioned next-session build,
run under the same night's "fix those, then the gate" grant): Δ-gate
slice 1 SHIPPED.** Instrument standalone (4879ef2), pre-registered
validation clean — 324 hosted-free replays, 0 errors: known-effective
Δ=+0.59, known-inert Δ=−0.06, rule-stratum Δ=−0.15 (negative miss
recorded as a finding: off-topic injection distracts decisions);
separation criterion passes, so the effect route wired (cbee97a):
`promote_lesson_by_effect` behind killswitch
`knowledge.effect_promotion_enabled` (Δ≥0.30, ≥6 calls, jackknife<Δ,
reason-stratum, tenure's boundary guards), CLI-driven measurement only.
Record: `docs/history/2026-08-06-delta-gate-validation.md`. Jeremy's
keep/adjust call rides the routes census (READING_QUEUE row
2026-08-07).

### 2026-08-08 — Decision: negative-Δ demotion direction SANCTIONED, gated on agreeing data

Jeremy, after reading the validation record + census readout (his words):
"it's reasonably clear that negative [Δ] should be used for
demotion/decay... but I suspect it's also not that simple either;
because other contexts might end up promoting on the same data";
"I lean towards implementing this assuming more testing gets us agreeing
data. I'm good with haiku or even sonnet-low spend for this testing";
"I'm very happy the gate is working and want to see more data there as
well"; "we may need to be open to adjust some of our calculation
variables when the data leads us there."

Operational meaning: (1) negative-Δ demotion/decay graduates from brief
§5 cut to conditionally-approved — build IF census round 2 confirms the
negative signs are stable; (2) Δ is decreed surface-scoped — a decision-
surface negative demotes from decision injection, it does NOT condemn
the lesson globally; other surfaces need their own reward design before
they can promote on the same data; (3) haiku/sonnet-low replay spend is
cleared for Δ testing (standing, not one-shot); (4) gate calculation
variables (Δ floor 0.30, min calls 6, jackknife dominance) are priors,
adjustable when data leads — shades-of-grey expectation on record.
Census round 2 launched same session (retest ×3 full-call + stratified
×9 + rule ×2, checkpointed, subprocess haiku,
`~/.maro/workspace/output/delta-gate-v1/census_round2.json`).

**Resolution (same day, 2026-08-08):** round 2 completed (3-way sharded,
966 replays, 0 errors; merged result
`~/.maro/workspace/output/delta-gate-v1/census_round2_merged.json`).
Retest 3/3 same-sign (−0.137/−0.059/−0.078) — the agreeing-data
condition MET → demotion built and landed per the conditional grant:
`knowledge_web.demote_lesson_by_effect` + `--demote` census CLI,
surface-scoped exactly per (2) (stamp = excluded from
inject_tiered_lessons + tenure-to-LONG blocked; no score/ledger/query
changes; a later qualifying positive measurement replaces the stamp;
killswitch `knowledge.effect_demotion_enabled` in docs/DEFAULTS.md).
Nothing stamped yet — running `--demote` on the three retested
negatives is queued behind Jeremy's read (READING_QUEUE 2026-08-08 row),
alongside the first live (4)-class question: the sweep's two positive
method-shaped lessons (+0.200/+0.167, jackknife-dominant) sit UNDER the
0.30 promote floor.

**2026-08-08 later:** Jeremy: "go ahead and stamp now" — all three
retested negatives stamped via demote_lesson_by_effect with the round-2
evidence (injection surface verified clean of them). Promote-floor: he's
on the fence ("might be too early yet"); a full-51-call retest of the
two sweep positives (61e4cbd7 +0.200, 32a656a5 +0.167) is running as
the winner's-curse check (top-2-of-9 selection inflates single
measurements; same test-retest bar demotion cleared) — floor
recommendation rides its result.

### 2026-08-08 — Decision: session-fork lane ON by default; promote floor HOLDS on retest evidence

Jeremy on the fork lane (his words): "Let's turn that on by default, we
can wait until after our test is done if it would negatively impact it";
confirmed scope question ("Is this something maro in general is going to
be upgraded by, or just our testing?") — answer on record: general, every
stateless subprocess call. Flip landed after the in-flight Δ retest
completed (mid-run flip would have changed the measurement substrate).
Journal 8aa8463b. Also his framing of the day: "-p track" accepted for
now, pure-API track deferred until economics change — consistent with
the standing budget posture (don't re-pitch API keys).

Promote-floor resolution (data, not decree): the two sweep positives
FLIPPED SIGN on full-51-call retest (+0.200→−0.059, +0.167→−0.137) —
winner's-curse selection artifact confirmed; **floor stays 0.30**; new
instrument rule of thumb recorded in the validation doc: subset
measurements triage, only full-oracle-set evidence acts. Second full-set
retest of the two flipped lessons running (agreement → demote stamps
under the same two-measurement standard).
- **2026-08-08** — Reading-queue decision batch (Jeremy, all six open asks
  cleared in one pass):
  1. **Re-mint policy = the gentle variant**: tombstone survives GC and
     tracks demotion history; a re-minted lesson circulates normally
     while gathering data ("don't immediately dismiss... let them gather
     more data until we know it's a pattern"); 3rd re-mint forces a
     full-set re-measurement which sets the stamp whichever way it
     lands. Noted future variants, not built: strict stamp-rides
     ("more straightforward"), experimental both-ways-viewable path
     ("probably over-complicating it").
  2. **Scout output is reading material only — no store writes** ("okay
     with this for now; gut says we still don't know enough yet...
     re-open if we need to later"). Legs 3–4 of the steal item void;
     untrusted-git boundary stays closed.
  3. Live-writer survivors: **Phase-60 verification-calibration loop
     REMOVED** (not disabled — off-switches-stay-off; "makes me a
     little sad... agree, we can resurrect if needed"); **inspector gets
     the evolver's run-finalize cadence lane** (Jeremy's recalled
     resolution was the evolver's — inspector never got it) plus a
     periodic larger-cleanup pass rider; **node promotion judged on
     age+content** (re-observation design empirically starved: 1/433
     in 8 weeks).
  4. World-facts §7: **hypothesis quarantine at injection mirrors the
     provenance pattern**; planner FACT: emission stays slice 3; cap
     sizes are build-time tuning. Jeremy expects testing/review rounds
     to surface the edges — open to discussion as they appear.
- **2026-08-08** — Go-nuts stretch (Jeremy AFK, "I'm no longer a blocker")
  executed the whole batch as landed chunks: re-mint tombstones
  (26d3b58), inspector run-cadence + deep pass (aebf844, live at
  run_cadence 10), node promotion age+content (42417df), match-tier
  telemetry (a87348f), skill pedigree origin/domain/tags (bc613de),
  world-facts slice 1 with §7.1 hypothesis quarantine + checkpoint
  carry (97bb780). Cross-model adversarial review of the six chunks ran
  same-day (verify-before-fix per standing rule). World-facts slices
  2–3 and the §5 cuts remain queued.
