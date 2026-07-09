---
status: record
---

# Decisions for Jeremy — 2026-07-09 autonomous 1.0 session

The /goal directive was "use your best judgement, note it for discussion
later, and we can revisit." This is the consolidated note. Four buckets:
judgment calls I already made (ratify or reverse — all reversible),
design decisions parked in shipped design docs, audit-born decisions,
and dogfood findings that shape (f)'s writeup.

Everything here has a pointer to fuller context; nothing needs
re-reading first — each entry is self-contained enough to decide on.

---

## A. Judgment calls made this session (ratify or reverse)

1. **garrytan removed from ship set AND routing.** Named-person likeness
   + known to brute-force Opus every step (cost footgun). The review
   *pattern* survives de-personified as the code_review skill (dogfood
   run in flight). Repo file kept; routing entry deleted (e0811c7).
2. **Never-ship set:** jeremy, poe, companion, garrytan,
   psyche-researcher personas + test-fixture skills stay in the repo but
   out of the wheel, enforced by census test. Box-specific specs belong
   in the workspace override layer. (Poe stays available in-repo as the
   optional persona, per the rename decree.)
3. **data-01 fixture purge executed before the learning-ON dogfood
   runs** (rather than letting runs learn from April test junk and
   purging later). Long tier 26→4, lessons 290→188, module store
   414→308. Full backup at
   `~/.maro/workspace/memory/backup-2026-07-09-data01/` — reversible.
4. **monitor_diagnose graduated by hand into the ship set.** Built by
   Maro itself in dogfood run 1; I reviewed content, added provenance
   footer, added to SHIPPED (c2609da). Precedent: Maro-built skills
   enter the catalog through hand review, not auto-promotion.
5. **Blanket `skills/` gitignore removed** after finding it had silently
   kept the entire skills half of the ship set out of git (fresh clone
   = 0 skills in the wheel). Warning comment left in .gitignore
   (c2609da).
6. **(h) auto-resume deferred past 1.0.** This box's crash-loop history
   argues for proving the manual `maro resume` path first. Shipped
   instead: stranded-state sweep (detect + notify, default-on) + manual
   resume. Auto-resume design exists (requeue, capped at 1) when wanted.
7. **notify DEFAULT_EVENTS grew two events:** `backend_actionable` and
   `stranded_run` are default-on (documented in DEFAULTS.md). Rationale:
   both are exactly the "silent stranding" class (h) exists to kill; a
   user who mutes them chose to.
8. **Run-4 dogfood goal relaunched with a self-contained format spec**
   after the first attempt stuck on a repo-relative path unreachable
   from the worker cwd. The stuck itself was CORRECT behavior (see D3).

## B. Design decisions parked in shipped docs (review when convenient)

- **Portable learning (g)** — 8 provisional decisions in
  `docs/PORTABLE_LEARNING_DESIGN.md` §8. The consequential ones: packs
  exclude raw runs by default; imported artifacts arrive at reduced
  trust (0.5 cap / hypothesis tier); skills/personas never auto-adopt
  (quarantine + explicit adopt); scrub guarantee is "mechanical +
  mandatory human review", no anonymization claim; minimum 1.0 slice =
  chunks 1–4 (export/seal/import/adopt). **Not yet implemented** —
  awaiting this review.
- **Backend resilience (h)** — 9 provisional decisions greppable as
  `DECISION (provisional)` in `docs/BACKEND_RESILIENCE_DESIGN.md`.
  Slices 1–3 (its own recommended 1.0 minimum) are now SHIPPED, so the
  live ones to sanity-check: 6 error classes; auth/billing = one
  failover attempt + ALWAYS notify (no silent absorption of dead auth);
  steps are at-least-once with FS-diff context on resume; NOW-lane
  resume out of scope. Still dormant by my call: auto-resume (A6).

## C. Audit-born decisions (Purgatorio eyes 1–6; full detail in docs/audit-2026-07/RECONCILIATION.md)

1. **Supervision story + enablement (SF-1, the biggest one).** The
   heartbeat has never beaten, the evolver has never run, bootstrap's
   generated heartbeat unit cannot start (`sheriff.py --heartbeat`
   isn't a flag — 30s crash loop), and two contradictory unit
   definitions exist. Needs: pick ONE supervision mechanism (the
   openclaw-gateway user-unit pattern is the local proof), fix
   bootstrap's ExecStart, and decide whether/when the heartbeat+evolver
   get burn-in hours on this box. Off switches stay off — enabling is
   yours alone.
2. **1.0 isolation story (cs-04).** Live executor = `claude -p
   --dangerously-skip-permissions`, no sandbox; sandbox.py has zero
   callers; SECURITY_MODEL.md claims per-skill sandboxing. Options:
   wire the sandbox opt-in, container-recommended install, or honest
   "trusted-operator" framing. Either way SECURITY_MODEL.md gets
   rewritten to the real stack (I can do the rewrite once you pick the
   frame).
3. **What is `user/` at 1.0 (docs-02/04).** Your GOALS/CONTEXT/SIGNALS
   (with medical details) are git-tracked and injected 500 chars each
   into every stranger's decompose prompt; user/CONFIG.md is an
   undocumented config lane (yolo, model tiers). Proposal: neutral
   templates in repo, your real files move to the workspace overlay,
   lane documented or folded into YAML config. Privacy review before
   any public 1.0 tag.
4. **Scope / Phase 65 posture (arch-01/02/03).** The box has run the
   *losing* A/B arm since April (`scope_generation: true` +
   `scope_ab_skip: true` = pay for scope, never inject) while docs say
   "dormant"; the one adjudicated experiment says INJECT wins; two more
   paid experiment datasets were never analyzed. Decide: inject / fully
   off / keep record-only — and whether to adjudicate or write off the
   two orphan datasets.
5. **slack-bridge (ops-04).** Dead in every dimension (source deleted,
   no unit) but holds live-looking Slack tokens in `.env`. Revive or
   remove; if remove, revoke the tokens.
6. **The release act itself (land-01/02).** `pip install
   maro-orchestration` 404s; version says 0.5.0; `.github/workflows/`
   is empty on a public repo. PyPI name check + publish + a basic
   pytest workflow are the missing 1.0 items nobody listed. (I can
   build the CI workflow without a decision; PyPI naming/publishing is
   yours.)
7. **README repositioning (land-10..15).** Hermes Agent (212k stars)
   owns "the self-improving AI agent" with a shipped loop; our evolver
   has zero production hours. Eye 6's verdict: lead with the shipped
   accountability layer instead — done≠achieved verdicts, claim
   checking, fail-closed spend caps, replay capture, portable learning
   (no fetched peer advertises ANY of these) — and stage the
   self-improvement claim to what verifiably fires. Cheap, high-value,
   but it's the product's face: your call.
8. **Verdict-blind learning (SF-2 / data-02).** 0/1381 outcomes rows
   carry goal_achieved; every learning consumer equates done with
   success. Fix shape is clear (tri-state field, prefer-verdict
   consumers) — mostly needs a green light + sequencing vs the
   verify→learn arc.
9. **Knowledge web: wire or descope (SF-8).** Edges frozen at the April
   import, read-side has zero callers, 2 known-fabricated lat.md nodes
   still injected (I'll delete those two regardless — pure hygiene).
   For 1.0 docs: call it a "node store with BM25 retrieval" or invest
   in the graph read-side.
10. **Small but standing:** `blocked_backend` as a first-class status
    value (from (h) slice 1 — currently rides `blocked` + structured
    class); refight_rule unreachable (arch-04) and nightly-eval rewire
    (arch-05) — both ride SF-1's vehicle decision; hermes trial
    containers are `restart=unless-stopped` and need `docker rm` when
    you call the trial done (ops-12).

## D. Dogfood (f) findings so far (shapes the crystallization audit)

1. **Closure is harsh on build-goals** (run 1, monitor_diagnose): Maro
   produced a correct root-cause diagnosis (casper-md5check on
   disk-installed Mint) with quoted evidence and a good skill file —
   closure judged `goal_achieved=false @0.25` because its verifier
   couldn't re-run privileged journalctl. Live specimen of data-02's
   verdict-noise; the honest self-learning number for 1.0 will be
   depressed by this class.
2. **Persona routing has no meta-goal awareness** (run 1):
   "build a monitor/diagnose SKILL" routed to health-researcher @0.892
   on the keywords in the goal text. Keyword routing can't tell a goal
   ABOUT diagnosis from a goal on diagnosis.
3. **Ralph-verify + MISSING_INPUT escalation worked exactly right**
   (run 4 attempt 1): unreachable input → refused to fabricate →
   escalated. Positive specimen for the escalate-only dispatch default.
   Goal-authoring lesson: worker goals need self-contained inputs (repo
   paths aren't reachable from project cwd).
4. **Cosmetic:** memory_bridge ingest stats key sources by filename, so
   the three lessons.jsonl paths collide in the stats dict (counts
   still correct in aggregate).

## E. Eye 7 (forward historian) — decision-shaped output

Full findings: `docs/audit-2026-07/findings-historian.md` (10 findings,
promises ledger, 10 clean checks). The clean checks matter: GOAL_BRAIN's
four quoted decrees verified word-for-word against session transcripts,
and the 2026-07-02 nine-item disposition list was fully honored. The
record is accurate where it exists; the failures are omissions.

1. **ToS posture on the `claude -p` lane (hist-02).** You've voiced the
   worry twice ("I think that's against the license; I'm probably
   pushing it a little with -p usage") and it's recorded nowhere, while
   README recommends that lane to strangers as the no-key-needed
   default. Recommending it to strangers is a different posture than
   using it yourself. Decide the 1.0 framing: keep recommending,
   caveat it, or demote it behind the API-key lane in docs. (No code
   change either way; this is a documentation-stance decision.)
2. **iMessage + Hermes-swap record (hist-01) → item (a) input.** Your
   Hermes-swap-on-new-hardware decision and iMessage preference lived
   only in auto-memory; I've back-filled GOAL_BRAIN Decisions entries
   (factual record maintenance, no new decisions made). The real ask:
   when we have the item (a) escalation-channel conversation, iMessage
   belongs on the candidate list (needs a Mac — pairs with the
   new-machine plan), and every current notify lane is
   Telegram-hardwired.
3. **Adversarial-review ship skill (hist-06) — decree at risk →
   RESOLVED same day.** Your "should probably be one of our skills we
   ship with" was not satisfied by the shipped (e) set behind a closed
   checkbox. Reopened as an explicit (e) remainder; hours later dogfood
   run 4 completed and its code_review skill graduated after hand
   verification (3/3 planted bugs confirmed with reproductions I re-ran
   myself; red herring correctly refuted, not reported). The skill IS
   the decreed pattern — mandatory attack-your-own-candidates pass,
   evidence-gated confirmed/speculative split. Ship set is now 12
   skills, 3 of them Maro-built. Nothing left to decide here; recorded
   because the near-miss (decree almost lost behind a closed checkbox)
   is the SF-13 pattern in action.
4. **PUBLISH_CHECKLIST adopt-or-retire + version scheme (hist-03).**
   The repo shipped v0.1.0/v0.2.0 before, and the March
   PUBLISH_CHECKLIST already gates on the exact classes Purgatorio
   re-derived (no personal data, clean-workspace install, version tag).
   Versioning is currently three-way incoherent (tags v0.2.0, CHANGELOG
   1.19.0, pyproject 0.5.0). My recommendation: adopt the checklist as
   the 1.0 gate scaffold, version 1.0.0 at tag time, retire the
   CHANGELOG's internal 1.x numbering to a build/era number. Your call
   on the scheme.
5. **Systemic fix adopted (SF-13):** decree-class statements reach
   auto-memory but not GOAL_BRAIN when a conversation ends without a
   work chunk (5 specimens). New session-close rule: any Jeremy
   statement worth an auto-memory write is also worth a GOAL_BRAIN
   Decisions line. Flagging so you know why GOAL_BRAIN grows a few
   dated entries that aren't attached to shipped work.
6. **Closure-verdict noise — two more graded specimens (runs 2, 5).**
   Run 2's closure claimed output files were never created (they exist;
   verifier resolved `output/` from the wrong cwd). Run 5's closure
   claimed the top-ranked triage item lacked urgency language (rank 1
   IS the planted urgent escrow wire, deadline text intact) — while the
   same run's adversarial review caught a REAL phantom-conflict wart.
   Pattern across all five dogfood runs: the adversarial layer is
   precise, the closure verifier's environment (cwd, privileges) is the
   dominant false-verdict source. This is SF-2/item-(b)'s
   verifier-environment half; it should shape which verdicts learning
   trusts.
