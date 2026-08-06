---
status: record
---

# 2026-08-06 — Adversarial review: full 24-hour diff (36afc98^..eee238d)

Cross-model review (Claude host → 9 Codex reviewers, `codex exec`,
read-only). The 24h window (2026-08-05 08:43 → 2026-08-06 02:28, 40
commits, ~1,900 src/script/deploy lines) was split into three runs by
subsystem, each with the full Skeptic + Architect + Minimalist panel.
Panels ran serially (box limits); all 9 output files landed non-empty.
Every accepted finding below was verified against the repo per
verify-before-fix (several reviewer "highs" died there — see Lead
Judgment). Commit 74722ee landed after the review range and is not
covered.

Reviewer transcripts: session scratchpad `adversarial-review-24h/`
(not retained in repo).

---

## Run 1 — Memory / mint-grounding (65e2e6c, 20337f3, 54a4be7 + kin)

**Intent:** minted lessons carry positive-evidence receipts; ungrounded
or dishonest mint paths closed (slice 1 of MINT_GROUNDING_DESIGN).

**Verdict: CONTESTED** — the mechanism ships and its scoping is honest,
but two confirmed defects recreate the exact false-support class (B3)
the module exists to refuse.

### Accepted findings

1. **[high, CONFIRMED]** `src/mint_grounding.py:249` — family-level
   fallback stamps `status="supported"` with an unrelated event's
   receipt when no keyword ties (`elif untied_status == "supported"`).
   A run whose only fetch is `curl https://status.example/health` will
   stamp "the dataset was fetched from vendor.example" as supported.
   The `note` records the caveat but consumers only render markers for
   *unsupported* — a family-level match displays as clean. All three
   reviewers converged here. Fix shape: untied concrete-family claims
   should land `unprobed`, not `supported`.
2. **[high, CONFIRMED]** `src/mint_grounding.py:94` — `_AUTH_MARK`
   still matches `\blogin\b|passw|credential` anywhere in the command,
   so an anonymous `curl https://public.example/login` stamps an
   "authenticated fetch" claim supported. Same class as the pinned
   `token=a` false-support the lexicon was already hardened against;
   these three bare-word alternates escaped the credential-shaped-value
   requirement.
3. **[medium, CONFIRMED — design gap, not slice-1 defect]** Grounding
   is optional at `record_tiered_lesson` (`knowledge_web.py:447`);
   step-lesson extraction (`memory.py:601`), `loop_finalize.py:595/635`
   recovery lessons, `prereq.py:185`, `evolver_store.py:458` mint
   unstamped. Slice 1 only claimed the two reflect paths, so this is
   scoping — but the loop_finalize recovery writers have `loop_id` in
   hand and could stamp cheaply. Backlog-shaped.
4. **[medium, PLAUSIBLE — slice-2/3 planning input]** The knowledge
   layer launders groundings: `outcome_to_knowledge` /
   `knowledge_bridge.py:391` carry only `outcome:<id>`; `KnowledgeNode`
   has no grounding field; reviewers claim reinforcement/LONG-promotion
   never consult grounding, so a known-unsupported lesson can propagate
   into decay-free knowledge with the stamp stripped. Belongs in the
   slice-2/3 design, explicitly.
5. **[low, PLAUSIBLE]** Widened 24K evidence window
   (`loop_finalize.py:684`) feeds raw step output into lesson
   extraction unlabeled — prompt-injection surface. Partially covered
   by the lesson_provenance mint gate; worth a pin test that an
   instruction-shaped payload in step output gets quarantined.

### Rejected reviewer claims

- "Fail-open on missing call records is a high" — explicit design
  decision, docstring + §4 falsifier on record. Working as decided.
- "Narrow lexicon leaves claims unstamped" — the >30% unprobed
  falsifier is the named trigger for v2; monitored, not a defect.
- "`has_unsupported()` unused" — it's the slice-3 seam; trivial.

### What went well

Absent-key discipline on both stores, live validation against a real
specimen run during the build, and the falsifier section mean the
fail-open posture is honest and measurable. The `[claims: N✓/N✗/N?]`
census tag gives the unprobed-rate falsifier its measurement surface
on day one.

---

## Run 2 — Run lifecycle / verdicts / reporting (e9e6828, e769dc4, 2c470d9, 46290ce, 6994eb0 + kin)

**Intent:** every run without a verdict says why; run pages surface all
ranked deliverables with correct attribution; honest denominators.

**Verdict: CONTESTED** — the tested paths work, but three verified
holes remain in exactly the denominator the change set out to close.

### Accepted findings

1. **[high, CONFIRMED]** `src/handle.py:2778` — a crashed closure judge
   writes `closure_error` to run *metadata* only; the outcome-ledger
   row is never stamped, and because `goal_verdict_source` is now
   present, `close_run`'s never-stamped fallback is suppressed. The
   ledger — the verdicted denominator slice 2 of next-leap gates on —
   keeps the silent hole for precisely the crash case this commit
   handles.
2. **[high, CONFIRMED]** `src/runs.py:771` — the fallback stamp reads
   singular `metadata.loop_id`, which (per the repo's own comment at
   runs.py:86–88) stopped being stamped; modern agenda runs carry only
   `loop_ids`. The `DONE_WITHOUT_VERDICT` tripwire still fires, but
   the `stamp_outcome_verdict` half is dead code for current runs.
3. **[medium-high, CONFIRMED]** `interrupted` runs escape both markers:
   `EXECUTION_FINISHED_STATUSES = ("done", "stuck")` and the
   errored-run branch tests literal `status == "error"`, while backend
   failures are deliberately converted to `interrupted` in
   loop_execute. Such runs get neither `run_errored` nor
   `closure_never_stamped`.
4. **[medium, PLAUSIBLE]** `closure_error` stamping covers only the
   first closure call — re-verification after closure restart and the
   quality-escalation path still swallow judge exceptions
   (`handle.py:2533`, `handle.py:3164`). Not hand-verified; check when
   fixing (1).
5. **[medium, CONFIRMED]** Curation caps are silent: 3 deliverables on
   the card, 12 served (`_SERVED_ARTIFACTS_CAP`), first-wins on
   basename collision — all deliberate and commented, but nothing
   marks the truncation, so a 13th ranked deliverable or a
   basename-colliding runner-up vanishes without trace. Violates the
   no-silent-caps bar, not the ranking design.
6. **[medium, CONFIRMED]** `src/loop_report.py:814` — step attribution
   matches served artifacts by basename only; two steps writing
   `draft/summary.md` and `final/summary.md` both claim the single
   served `summary.md`. False provenance on the surface that promises
   attribution.
7. **[low-medium, CONFIRMED]** `run_curation.py` card write drops
   `goal_verdict_source`, so the new "why there's no verdict" reasons
   don't reach the card's verdict render (summary text does travel).
8. **[low-medium, PLAUSIBLE]** The "loop done — run finalizing" badge
   keys on `metadata.status == "running"`, which no production path
   sets; the badge may be unreachable outside its test.
9. **[low, PLAUSIBLE]** Artifact hrefs are HTML-escaped but not
   URL-encoded (`#`/`?` in filenames break links); bundle/patch files
   get links the viz server's extension allowlist then denies.

### What went well

The no-steps closure skip and errored-run marker do what their tests
pin; the tripwire-event pattern (count the absence) is the right
mechanism; full-answer preservation and served-URL wiring close the
83a2c805 lost-deliverable failure for the common case.

---

## Run 3 — Skills / execution infra (f71be8d, a0bae77, da4eb72, fd57a56, 0c23a5a, a1add58, bd9b852, 65d05da, 6527c9e + kin)

**Verdict: REJECT (skill-promotion portion); CONTESTED elsewhere** —
the revived promotion gate has a confirmed bug that re-deadens it on
the repair path, and the A/B mechanism the census just patched is
structurally contaminated.

### Accepted findings

1. **[high, CONFIRMED]** `src/skills.py:1275/1304` — when validation
   fails once and repair succeeds, `skills[i]` is replaced with the
   repaired object but `tier = "established"` is set on the *stale*
   original no longer in the list. `SKILL_PROMOTED` fires, the ID is
   returned as promoted, and the save persists the repaired skill
   still-provisional. The exact announced-but-structurally-dead class
   a0bae77 exists to fix, alive on its repair path.
2. **[high, CONFIRMED]** A/B arms are not exclusive:
   `find_matching_skills` has no `variant_of` filter, so a challenger
   sharing triggers matches alongside its parent (`[P,C]` or `[C,C]`
   after per-row routing in `loop_planning.py:479`), and
   `loop_post_step.py:1016` credits *every* keyword match at outcome
   time regardless of injection. Challenger-retirement and promotion
   decisions run on contaminated, non-comparative trials.
3. **[medium-high, CONFIRMED]** `maybe_auto_promote_skills`'s `limit`
   caps *successful promotions*, not LLM candidates — a pool of
   never-passing provisionals gets validated + up to 3 rewrites each,
   every sweep, with `promoted` never advancing. The spend the cap
   claims to bound is unbounded on the failure path.
4. **[medium-high, CONFIRMED]** The revived promote/demote/rewrite
   gates key on `use_count`/`SkillStats.total_uses`, which
   `skill_types.py:68` itself documents as inflated bystander
   attribution ("consumers should prefer injected_* where present").
   Decision-shaped: either the gates move to `injected_runs` or the
   census's un-deadening re-animates gates on dishonest data.
5. **[medium, CONFIRMED]** `scripts/tree-triage.sh:65` — any tracked
   path missing from the tree is "Always stale"; another session's
   intentional unstaged `git rm`/`rm` is classified stale and `--fix`
   restores it, violating the script's own "real work untouched"
   contract — the same two-valued-signal failure it was built after.
6. **[medium, CONFIRMED]** The container partial-view prompt
   (`step_exec.py:1204`) keys on configuration, not actual
   provisioning: when Docker degrades to host execution
   (`container_exec.py:528`) the worker is still told it is in a
   container, can't see `.git`, and must not run git — steering an
   introspection worker away from evidence it actually has.
7. **[medium, PLAUSIBLE]** `claim_verifier.py:371` suffix matching can
   verify a relative-path claim against an unrelated same-suffix file
   elsewhere in the project, muting the hallucination signal the
   verifier exists to raise.
8. **[low-medium, PLAUSIBLE]** Skill promotion is a read → LLM-validate
   → write-all transaction with the lock only on the final save;
   concurrent finalizes can drop intervening pool mutations.
   (Serialize-shared-state; concurrent sessions are the norm here.)

### Rejected / downgraded reviewer claims

- "Executor `-r<N>` tag can't name exactly one image because
  `node:22-slim`/apt are mutable" — true and worth knowing, but
  inherent to any non-digest-pinned build; the tag scheme fixed the
  *self-inflicted* ambiguity. Decision-shaped footnote (pin base by
  digest if the audit bar demands it), not a defect in the change.

### What went well

The census method itself (live-writer? executed check?) is what found
the dead gates; pytest-in-image and the tag-revision scheme close real
operational failures; the PROMPT clip() sweep and partial-view notice
are honest-by-default moves; tree-triage's core stale-vs-real hashing
logic is sound for the modify case.

---

## Overall

**CONTESTED** across the window; the skill-promotion revival (run 3
findings 1–4) is the one cluster I'd call REJECT — the gate the commit
declares revived is still dead on its repair path and feeds on
attribution the repo itself labels dishonest. Highest-leverage fix
order: R3-1 (stale-object promote), R2-1/R2-2 (closure_error ledger
stamp + loop_ids fallback), R1-1/R1-2 (family-level fallback +
bare-word auth marks), R3-2 (A/B exclusivity), then the honest-caps
markers (R2-5) and tree-triage deletion case (R3-5).

Cross-model stats for the record: 9 reviewers returned 21 distinct
findings after dedup; 13 CONFIRMED against code, 6 PLAUSIBLE
(unverified but coherent), 2 rejected outright + several downgraded as
named design decisions (fail-open, lexicon narrowness, curation
ranking) — consistent with the ~30–50% verify-before-fix prior, on the
low end because reviewers were pointed at real diffs with repo access.
