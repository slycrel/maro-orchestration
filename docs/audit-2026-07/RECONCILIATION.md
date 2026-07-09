# Purgatorio reconciliation — FINAL (all seven eyes)

**Status:** FINAL, 2026-07-09. All seven eyes complete: 1 (ops census),
2 (data/learning-store), 3 (backward archaeology), 4 (docs coherence),
5 (code-vs-spec + security), 6 (external landscape), 7 (forward
historian). Per docs/PURGATORIO_AUDIT.md: overlap is a feature — a gap
found independently by multiple eyes is almost certainly real.

Raw findings: findings-ops.md (15), findings-data.md (13),
findings-archaeology.md (11), findings-docs.md (11),
findings-code-security.md (7), findings-landscape.md (15),
findings-historian.md (10) — **82 findings**, plus 52 explicit clean
checks, a 9-entry link-farm re-audit, and a promises ledger (open
commitments recovered from raw history).

---

## Super-findings (merged, triangulated)

### SF-1 — The self-improvement engine has zero production hours, and the record pretends otherwise
**Members:** ops-01, ops-02, ops-03, ops-05, ops-08, arch-04, arch-05,
arch-06, docs-03, docs-06, data-09. **Severity: blocker-for-1.0** (1.0
explicitly claims "self-learning in the build-out").

The strongest triangulation in the audit — four eyes hit it
independently. The heartbeat has no scheduler and has never beaten under
the Maro name (ops-01); the evolver has never run (ops-02, three
independent probes); 243 accreted skills are all provisional with
essentially zero applications (data-09). On top of the dead engine, the
*record* rots in layers: ratified decisions ride the dead vehicle
(refight_rule structurally unreachable, arch-04; nightly eval's only
caller is the dead heartbeat, yet was cited to close a BACKLOG item,
arch-05), GOAL_BRAIN asserts an in-process heartbeat fallback that does
not exist (arch-06), README sells self-improvement as ambient default
behavior (docs-06), and the shipped bootstrap generates a heartbeat unit
that **cannot start** — `sheriff.py --heartbeat` is not a flag; exit 2,
30s crash loop forever (docs-03), with a second, contradictory unit
definition in deploy/.

**Resolution shape (one arc, not eleven fixes):** (1) pick ONE
supervision story — the openclaw-gateway user-unit pattern ops-15
documents is the local proof — and delete the losing unit definition;
(2) fix bootstrap's ExecStart; (3) burn in evolver production hours
before any 1.0 self-learning claim; (4) reword README's two ambient
passages to "when the heartbeat loop is enabled (opt-in)"; (5)
GOAL_BRAIN corrections for arch-05/06. Enabling the heartbeat/evolver on
this box is **Jeremy's call** (off switches stay off). Eye 7 adds a
required input to that call: the 2026-06-21 heartbeat-gate design
conversation (hist-07 — free local model answers "is there work?",
escalate to paid only on yes; the real work is the context assembler)
is Jeremy's stated preference for what a heartbeat should be, and it
must be on the table when the supervision story is picked.
*Partial movement this session:* the (f) dogfood runs are the first
deliberate learning-ON production hours the system has ever had.

### SF-2 — The learning layer is verdict-blind: done≠achieved never reached the stores
**Members:** data-02, data-07, data-08, + live dogfood specimen.
**Severity: blocker-for-1.0.**

Not one of 1381 outcomes rows carries `goal_achieved`; verdicts live
only in run metadata, which no learning consumer reads. The evolver,
inspector, lesson extractor, and recall's repeat-guard all equate
`status=="done"` with success — on Jeremy's own organic evidence (4/5
done, 1 achieved) every success-rate the learning loops compute is
inflated, and lessons are extracted from goal-failed runs as if they
succeeded. Corollaries: 5 hermes-trial imports sit unverdicted
(data-07), 12 pre-fix June `True` verdicts are indistinguishable from
post-fix ones (data-08). Live specimen from dogfood run 1
(6dfaec5d-keen-alder): Maro produced a *correct, evidence-quoted*
diagnosis and a good skill, and closure judged it `goal_achieved=false
@0.25` because the verifier could not reproduce privileged journalctl
output — the harsh-on-build-goals face of the same coin: verdicts are
both absent where needed and noisy where present.

**Resolution shape:** plumb tri-state `goal_achieved` (absent=unjudged)
into record_outcome/reflect_and_record; teach evolver/inspector/recall
to prefer it; stamp verdict provenance (judged_at / judge version) going
forward; annotate the 12 pre-fix rows. This IS 1.0 item (b)'s store-side
half.

### SF-3 — Test junk was live in production learning paths (purged; residuals remain)
**Members:** data-01 (**resolved this session**), data-06, data-13.
**Severity: was blocker; residuals real-but-deferrable.**

The purge executed before the (f) learning-ON runs: long tier 26→4,
top-level lessons 290→188, module store rebuilt 414→308, 0 junk
remaining, backup at `~/.maro/workspace/memory/backup-2026-07-09-data01/`.
Open residuals: provenance-era guard on bridge ingest (so pre-isolation
junk can't recur), import provenance stamping (data-06 — rides 1.0 item
(g), where provenance is a hard prerequisite for sharing learning), and
the cosmetic litter list (data-13).

### SF-4 — The scope/Phase 65 story: the experiment ran, the winning arm lost the config war, the docs say "dormant"
**Members:** arch-01, arch-02, arch-03. **Severity:
real-but-deferrable, but must be adjudicated before 1.0 flag defaults
are finalized.** **Jeremy decision.**

The box has run scope-generation live since ~April in the A/B-*control*
configuration (`scope_generation: true` + `scope_ab_skip: true`): every
AGENDA run pays the scope LLM call, the scope is never injected, yet
ResolvedIntent flows into closure verification — while DEFAULTS.md and
CLAUDE.md call the feature dormant/paused. The one adjudicated
experiment (2026-04-22) says **inject** wins (plan compression 8 vs
15–40 steps); two further paid A/B datasets (04-25, 04-26) were never
even read. And the 2026-07-09 "accept ResolvedIntent v0 on organic
evidence" decision rests on evidence generated under this misdescribed
config.

**Decision needed:** pick the flag posture deliberately (inject / off /
keep paying for record-only), adjudicate or write off the two orphan
experiments, correct DEFAULTS/CLAUDE/GOAL_BRAIN to match reality.

### SF-5 — `user/` ships Jeremy's personal data and is an invisible config lane
**Members:** docs-02, docs-04, docs-05. **Severity: blocker-for-1.0
(privacy).**

`user/GOALS.md`/`CONTEXT.md`/`SIGNALS.md` are git-tracked with identity
and medical details, and planner.py injects 500 chars of each into
**every decompose prompt** resolved from the install dir — a stranger's
clone plans their goals with Jeremy's health context in the prompt.
Simultaneously `user/CONFIG.md` is a load-bearing config lane (`yolo`,
model tiers, MCP servers) that DEFAULTS.md's census structurally cannot
see and README never mentions. One "what is user/ at 1.0" decision
resolves the whole cluster: neutral templates in the repo, Jeremy's real
files move to a private overlay (workspace), the lane gets documented
or folded into YAML config.

### SF-6 — The security posture strangers are told about does not exist; the real one has named gaps
**Members:** docs-01, docs-08, docs-09, cs-04, cs-01, cs-02, cs-03,
cs-05; ops-13 folds in here. **Severity: blocker-for-1.0** (cs-04 +
docs-01), with real-but-deferrable satellites.

Triangulated by three eyes independently (docs, ops, code). The live
executor runs `claude -p --dangerously-skip-permissions` with full tool
access and **no sandbox** (cs-04; `sandbox.py` has zero runtime
callers) — while the living SECURITY_MODEL.md tells a stranger "every
skill runs sandboxed" and documents an env var (`POE_ENV_FILE`) no code
reads. Eye 5's threat model of the *actual* stack: the write fence is
diagnostic-only with a known relative-`..` blind spot (cs-01); secret
scrubbing misses Telegram-token / high-entropy / JSON-serialized
classes on sinks that feed shareable artifacts (cs-02); closure
verification runs LLM-authored shell with `shell=True`, no allowlist,
with untrusted worker output in the prompt — a real indirect
prompt-injection → shell path (cs-03); the worker push guard is an
accident-guard, trivially unset by the guarded party (cs-05); and three
services listen on 0.0.0.0 on a box with money attached (ops-13).

**What held (don't re-litigate):** done≠achieved verdict flow is clean
(fail-open null verdict excluded, worker "done" can never self-award
`goal_achieved`); viz_server traversal defense solid; only
`yaml.safe_load` in the codebase; injection_guard's source allowlist
resists keyword-stuffing.

**Resolution shape:** (1) rewrite SECURITY_MODEL.md to the verified
actual stack (cwd-bind + constraint gates + injection_guard + scavenge
diagnostics + budget caps), label sandbox "built, unwired", fix
POE_ENV_FILE→MARO_ENV_FILE, sweep rename ghosts — eye 5's evidence
makes this writable now; (2) **Jeremy decision:** the 1.0 isolation
story (container / user-namespace / opt-in sandbox wiring / honest
"trusted-operator-only" framing); (3) backlog the satellites (fence
enforcement-or-containment, scrub patterns, closure-command
restriction, push-guard framing).

### SF-7 — Lying-state litter: dead lanes that look alive, silent fallbacks to stale data
**Members:** ops-04, ops-06, ops-07, ops-12, ops-14, data-10, docs-10,
docs-11. **Severity: real-but-deferrable, one credential decision
urgent-ish.**

slack-bridge is dead in every dimension yet holds live-looking Slack
tokens in `.env` (ops-04 — revive-or-remove, and revoke tokens if
remove); telegram_listener looks alive on disk but froze in April;
eight modules silently fall back to the repo-local stale `memory/`
copies instead of erroring (data-10 — the ghost-index pattern waiting
to recur), and README's benchmarking examples actually read that stale
copy (docs-11); assorted dead pidfiles, a decorative tmux-claude unit,
and the hermes trial containers set to auto-resurrect
(`unless-stopped`) — the latter must be `docker rm`'d when the trial
chunk ends per the good-system-citizen rule. **Done 2026-07-09:**
both containers removed after verifying all state was on host bind
mounts; recreate anytime via `~/claude/hermes-maro-trial/docker-compose.yml`.

### SF-8 — The knowledge "web" is write-only and injects known fabrications
**Members:** data-05, arch-07, arch-08, arch-09. **Severity:
real-but-deferrable (docs descope may be needed for 1.0 honesty).**

All 2124 edges date to the April import; the read side
(`load_knowledge_edges`) has zero external callers — the accepted
memory direction's Phase 2 shipped only its BM25 half. Two lat.md nodes
flagged as fabricated on 2026-07-04 (citing nonexistent files) are
still actively injected into planner/director prompts (arch-07 —
cheapest fix in the audit: delete/correct 2 nodes).
`record_rule_wrong_answer` remains unwired (arch-09). Either wire the
read side or descope "knowledge web" claims in 1.0 docs to "node store
with BM25 retrieval".

### SF-9 — May 2026 is a month-sized hole in the learning record
**Members:** data-03 (contradicts ops-10's "hiatus" spend reading).
**Severity: real-but-deferrable.**

476 May run dirs and 965 captains-log events exist, but outcomes,
step-costs, and daily logs have zero May rows — activity happened,
recording didn't. Investigate the May-era wiring break enough to trust
that the *current* recording path doesn't share it, and document the
hole so no analysis treats May as "no activity".

**Mechanism named by eye 7 (hist-04), partially verified:** commit
9496f11 (2026-05-06) exports `POE_ORCH_ROOT="$REPO_ROOT"` for every
build-loop invocation, and May's 25 commits are dominated by the
build-loop/cron cluster — May's cron-driven activity wrote state under
repo-local roots, not the canonical workspace. Verification 2026-07-09:
the pin is real in the diff, but **this checkout's repo-local
`memory/` holds only April dry-run rows (688 outcomes, all 2026-04,
zero May)** — so the May rows, if they were written at all, went to
whichever checkout the May cron actually ran from (since cleaned or
elsewhere), or the pinned path still failed to record. Either way the
suspect stands, the current recording path post-dates the no-cron
invariant (2026-06-10) and the workspace unification (2026-07-03,
BACKLOG #-1), and the hole should be documented as unrecoverable
rather than investigated further. Same split-brain class the
unification fixed, one era earlier.

### SF-10 — The lesson-promotion funnel has never promoted
**Members:** data-04 (mechanism), data-09 (skills twin — also in SF-1).
**Severity: real-but-deferrable.**

10/10 consolidation cycles: 279 decayed, 95 gc'd, 0 promoted; the only
promotions ever (4) came from the reinforcement-time hook. Decay
economics guarantee gc-before-promote for anything not re-hit within
days. Measure the desired funnel rate before tuning — and note the (f)
dogfood crystallization audit is the first data point on what the
funnel does under deliberate use.

### SF-12 — Release table stakes are missing; the differentiation story is inverted
**Members:** land-01, land-02, land-10 (+ land-11..15 positioning,
land-03..09 gaps). **Severity: blocker-for-1.0** (land-01/02/10).

The release act itself isn't on the (a)–(h) list: `pip install
maro-orchestration` 404s on PyPI and pyproject still says 0.5.0
(land-01); `.github/workflows/` is an **empty directory** on a public
repo — no CI, no badge, nothing enforcing PUBLISH_CHECKLIST's "pytest
passes" gate (land-02). And the README's headline claim
("meta-evolver reviews failure patterns every 10 minutes") is exactly
the tagline Hermes Agent (212k stars) owns with a *shipped* loop, while
Maro's evolver has zero production hours — SF-1's docs face, sharpened
into a credibility hole by the landscape (land-10).

The inversion (land-11..15): orchestration mechanics are commoditized;
what NO fetched peer advertises is Maro's shipped accountability layer
— done≠achieved verdicts + claim verification (zero README presence,
land-12), local free replay capture (LangGraph's equivalent is
commercial, land-13), no-telemetry + default-on fail-closed spend caps
(land-14), portable learning with provenance (land-15). **Positioning
verdict:** reposition 1.0 as "the autonomous agent framework that
verifies its own work and can't silently spend your money"; stage the
self-improvement claim to what verifiably fires. Deferrable gaps:
examples/ gallery, user-docs subset + help pointer, MCP invisible in
README despite a working client (land-06), local-model lane
undocumented (land-07); watch: streaming, OTel exporter; ignore:
Windows (one WSL2 line).

### SF-13 — Decree-class statements reach auto-memory but not the repo record
**Members:** hist-01, hist-05, hist-07, hist-08, hist-09 + four
promises-ledger OPEN-unrecorded rows. **Severity: real-but-deferrable
(systemic; one structural fix).**

Eye 7's diff of raw session transcripts against the compiled record
found the same failure five times: Jeremy makes a decision or ask in
conversation, it lands in Claude Code auto-memory, and the repo record
never hears about it. The Hermes-swap decision + iMessage interface
preference (hist-01) live only in auto-memory while GOAL_BRAIN still
says "steal-from-don't-migrate, stance unchanged"; the heartbeat-gate
design direction (hist-07 — free local model answers "is there work?",
the owner's stated preference for what a heartbeat should even be) is
absent from SF-1's supervision conversation; the budget-posture decree
(hist-08) has no GOAL_BRAIN Decisions entry, so a future session
honoring "GOAL_BRAIN wins" could legitimately re-pitch an API key; the
"run this prompt with this persona" pattern ask (hist-05) was captured
as prose and dropped as work — re-improvised by hand three times since
(docs brain trust, bake-off, Purgatorio itself); the cross-run
retry-with-prior-context ask (hist-09) is unlinked to the re-attempt
hinter TODO it maps to.

The mechanism: end-of-chunk discipline updates GOAL_BRAIN for *work*,
but conversations that end without a work chunk (the Hermes wrap-up,
the budget aside) leave no repo trace. **Structural fix (one rule):
any Jeremy statement worth an auto-memory write is also worth a
GOAL_BRAIN Decisions line — apply at session close.** The factual
back-fills (hist-01, hist-08 Decisions entries; hist-09 provenance
stamp) are safe to do autonomously; done this session.

### SF-14 — Release amnesia: the repo has shipped before and the 1.0 arc doesn't know it
**Members:** hist-03; feeds SF-12. **Severity: real-but-deferrable
(but resolve before tagging).**

Git tags v0.1.0 and v0.2.0 exist (March, April — the latter shipped
"portable installs", which later regressed into the pip-never-worked
finding), and docs/PUBLISH_CHECKLIST.md (2026-03-10, dormant) already
gates release on exactly the classes this audit re-derived from
scratch: no personal data (= SF-5), clean-workspace bootstrap (= the
install residuals), CHANGELOG + version tag. Versioning is incoherent:
git tags say v0.2.0, docs/history/CHANGELOG.md says 1.19.0, pyproject
says 0.5.0. **Resolution:** adopt-or-retire PUBLISH_CHECKLIST as the
1.0 gate scaffold (it's a ready-made checklist for SF-5/SF-12), and
pick ONE version scheme before any 1.0 tag.

### Eye-7 singles
- hist-02 — Jeremy's standing ToS worry about the `claude -p` lane ("I
  think that's against the license; I'm probably pushing it a little
  with -p usage") is recorded nowhere, while README recommends that
  lane to strangers as the no-key-needed default. Recommending a lane
  to strangers is a different posture than using it yourself.
  **Jeremy decision** on 1.0 framing; goes to the item (a)
  conversation alongside hist-01.
- hist-06 — the adversarial-review ship-skill decree ("That, or a
  flavor of it, should probably be one of our skills we ship with") is
  at risk behind the closed (e) checkbox; only vehicle is dogfood run
  4's code_review skill. **Reopened as an explicit (e) remainder** —
  gated on run-4 graduation or hand-built.
- hist-10 — CLAUDE.md "repo not renamed on GitHub" contradicted the
  remote URL beside it. **Fixed inline 2026-07-09.**

### SF-11 — Verified good (record these, they're signal too)
ops-10 (no silent token burner; ≤$2.79/day; $229.38 all-time), ops-11
(git hooks healthy post-fix, tripwired), ops-15 (openclaw-gateway is
the supervision pattern to copy), data-12 (module store
sqlite==JSONL exact; dev-recall docs lane fresh; medium tier clean
post-Jun-23), 24 docs clean checks (quickstart commands, backend order,
budget defaults, DEFAULTS spot-checks all hold), 13 archaeology clean
checks (checkpoint wiring real, no-daemons consolidation holds, merged
branches really merged), 10 historian clean checks (the 2026-07-02
nine-item disposition list fully honored; GOAL_BRAIN's four quoted
decrees verified word-for-word against session transcripts — the
record's quotes are accurate, not paraphrase-drifted; director-
clarification, cache-aware meter, harvest-corpus asks all shipped and
recorded).

### Unmerged singles
- ops-09 — 63% of step-cost rows have empty `model`; blinds the next
  burner hunt. Backlog (populate at record time).
- ops-13 — three services listen on 0.0.0.0 on a box running an agent
  with money attached (novnc :6080, nginx :8088, vnc :5900). Folded
  into SF-6.
- cs-06 — `router.py` pickle.load of a workspace file: latent code-exec
  gadget, currently unreachable via import whitelist. Must be resolved
  (JSON/joblib-safe) before item (g) bundle-import ships.
- cs-07 — env>config precedence is per-call-site discipline, not
  centralized; corroborates docs-05. Low backlog (helper function).
- arch-10 — origin/factory branch (5 commits, benchmark results) is
  work finished, pushed, and absent from the record. Adjudicate:
  merge learnings / archive / delete.
- arch-11 — GOAL_BRAIN (wins-by-decree doc) is stale on thread-brain
  maintenance vs MILESTONES. Goal-brain correction.
- docs-07 — arch-platform.md documents the wrong backend failover
  order (the mandatory pre-read for platform work). Fixed-inline.
- data-11 — dev-recall session-transcript lane 84 days stale; **must
  re-ingest before eye 7** (chat-history mining reads that index).
- Dogfood specimen (this session, run 6dfaec5d): persona router sent a
  skill-building meta-goal to health-researcher @0.892 because the
  goal *text* contained "diagnosis/health" — keyword routing has no
  meta-goal awareness. Feeds the (f) writeup.

---

## FINAL 1.0-blocker list (all seven eyes)

| # | blocker | members | state |
|---|---|---|---|
| 1 | Self-improvement claims vs zero production hours + broken supervision story | SF-1 | open; supervision decision = Jeremy |
| 2 | Verdict-blind learning stores | SF-2 | open; = 1.0 item (b) store-side |
| 3 | `user/` personal data shipped + injected; invisible config lane | SF-5 | open; privacy review before any tag |
| 4 | Security: no-sandbox live path + SECURITY_MODEL.md misrepresentation | SF-6 (cs-04, docs-01) | open; doc rewrite now writable; isolation story = Jeremy |
| 5 | Test junk live in learning paths | SF-3 | **resolved 2026-07-09** (purge + rebuild); ingest guard residual |
| 6 | Heartbeat unit from bootstrap crash-loops on clean install | in SF-1 | open; fixed-inline candidate |
| 7 | Not installable by name (no PyPI, version 0.5.0) — the release act itself | SF-12 (land-01) | open; name-availability check then publish at tag |
| 8 | CI is an empty directory on a public repo | SF-12 (land-02) | open; standard pytest workflow + badge |
| 9 | README self-improvement headline untenable vs landscape + idle evolver | SF-12 (land-10) ↔ SF-1 (docs-06) | open; cheap messaging fix, reposition on accountability layer |

Adjudicate-before-1.0 (not blockers, but shape decisions Jeremy is
about to make): SF-4 (scope posture), SF-7's slack-bridge credential
decision, SF-8's docs-descope-or-wire choice, hist-02 (ToS posture on
the recommended `claude -p` lane — pairs with the item (a)
conversation), hist-06 (adversarial-review (e) remainder — decree at
risk, vehicle in flight), SF-14 (PUBLISH_CHECKLIST adopt-or-retire +
version scheme before tagging).

Eye 7 changed no blocker's status: its findings are record-integrity
(SF-13), release-history (SF-14 — sharpens blocker #7's "pick a
version"), and inputs to decisions already queued (hist-01/02/07 → item
(a) and SF-1; hist-04 → SF-9 closed-as-documented). The blocker list
above stands as the final Purgatorio output.

---

## Disposition

Consolidated decision brief for Jeremy:
`docs/history/2026-07-09-decisions-for-jeremy.md` (buckets A–D +
Section E for eye 7). Factual record back-fills (GOAL_BRAIN Decisions
entries for hist-01/hist-08, hist-09 provenance stamp, hist-10 CLAUDE.md
reword) applied 2026-07-09. Everything else waits on the brief.
