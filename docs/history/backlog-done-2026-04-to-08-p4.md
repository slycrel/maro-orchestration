---
status: record
---

# BACKLOG_DONE archive — part 4 (2026-08-16 BACKLOG split sweep)

The 26 records swept out of BACKLOG.md on 2026-08-16 (the overdue
split; the live file was 393KB, past the 256KB Read limit). Kept as
a segment rather than in BACKLOG_DONE.md because the batch would
have pushed that file back over the same limit. Verbatim blocks;
each arc's live residuals stay in BACKLOG.md as stubs. dev-recall
ingests this file.

## Moved from BACKLOG 2026-08-16 (the overdue split; BACKLOG.md was 393KB, past the 256KB Read limit)

Long-shipped arcs and closed records swept here with context
intact; each arc's live residuals stay in BACKLOG.md as compact
stubs pointing back at these records. Blocks are verbatim.

### from BACKLOG: MEDIUM→LONG promotion destination-write loss — FIXED 2026-08-11

**FIXED 2026-08-11** (`knowledge_web._move_medium_to_long`, both routes):
the move is now destination-first — stage under the MEDIUM lock (in-lock
boundary guards unchanged, both ends of the move), append-IF-ABSENT under
the LONG lock (idempotent: closes the concurrent-double-promotion race
the old pop-atomicity closed, and makes retry-after-crash safe), then
remove under the MEDIUM lock with a guard re-check — a boundary stamp
landing mid-move ABORTS the promotion and rolls the LONG copy back (a
contested row must never stand in decay-free LONG). A crash between the
two locks leaves the row in BOTH tiers; `run_decay_cycle` reconciles
(the MEDIUM copy of a LONG id is an interrupted-move leftover, dropped —
LONG-membership read INSIDE the MEDIUM lock, serialized against the
mover). Named residual window: an abort's LONG rollback interleaving a
concurrent decay cycle's read — microseconds, needs a mid-move contest
stamp AND a cycle at that instant, and it reduces to the PRE-fix loss
mode, never a new one; a real WAL is the stronger fix if it ever fires.
Also honest: MEDIUM-row updates landing inside the stage→remove window
are lost with the removal (±1 reinforcement — same class as the old
post-pop window). 4 pins (test_tiered_memory.py) + 3 shape pins updated
(`reconciled` in cycle stats).

Original finding:

Both promotion routes remove the row from MEDIUM under that tier's lock,
then append it to LONG as a separate operation
(`knowledge_web.py` ~L1741 and ~L1869 as of `bf05be3`). An `OSError`
from `_append_tiered_lesson` between the two leaves the lesson in
NEITHER tier — reproduced by the reviewer; the auto-reinforcement caller
catches the exception and continues, so the loss is silent and
permanent (all merged_variants included). Violates the retention decree
harder than anything the erosion chunk fixed. Fix shape:
destination-first write (append to LONG, then remove from MEDIUM —
crash duplicates are reconcilable by the dedup sweep; disappearance is
not), or a recoverable move marker/WAL. Sized as its own chunk: both
promotion routes + the crash-window reconciliation story + pins.

### from BACKLOG: Async post-run tail — PHASE 1+2 SHIPPED (2026-08-12/13)

**PHASE 1 SHIPPED 2026-08-12** — the maintenance/evolver phase (the ~70%
slice) no longer runs inline before closure. `_finalize_loop`'s five
maintenance blocks (skill maintenance, health probes, statistical scans,
run-cadence evolver, run-cadence inspector) extracted to
`loop_finalize.run_post_run_maintenance()`; when the caller has promised
a post-notify drain (**explicit `defer_maintenance` flag, threaded
run_agent_loop → ctx → finalize; handle.py is the only setter**) it
registers into `loop_finalize._POST_NOTIFY_MAINTENANCE` (moved out of
handle.py 2026-08-12: `python -m handle` runs handle as __main__, so a
handle-owned registry split into two module copies and stranded every
deferred callable on the box's documented dispatch lane — 3-lens review
of 707a541, Architect HIGH; loop_finalize is only ever imported
canonically) — a twin of the
answer-first learning registry, drained by handle()'s finalize AFTER the
run_completed notify and after the learning drain (promotions see this
run's crystallization one cycle earlier; nothing here feeds the run
card, so no refresh). All other callers and registration failures run it
inline — maintenance moves in time, never silently drops. The deferred
callable re-enters the loop's `captains_log.loop_id_scope` so
maintenance events stay loop-attributed. Deliberately NOT drained at the
quality-gate early-drain site: the escalated retry needs the failed
loop's LESSONS, not its promotions, and putting the multi-minute tail
ahead of the retry is exactly the wait the decree removes. Crash before
the drain loses at most one cadence cycle; every sub-system is
threshold/cadence-based and re-fires on the next run's tail, and
heartbeat still runs skill maintenance on its own tick. Pins:
tests/test_agent_loop.py (defer/inline/CLI-shape/dry-run/registration-
failure fallback/scope attribution), tests/test_handle.py
`TestAsyncMaintenanceTail` (registry drain semantics,
notify→learning→maintenance ordering through handle(), early-drain
leaves maintenance registered). Suite 8266/0 skipped.
**Live-verified on the M1 2026-08-12, two instrumented runs (notify/
drain seams wrapped, pre-registered pass criteria): both PASS.** Run 1
(`e3b2e8d3`, $1.18, achieved/closure) proved the mechanism — drain n=1
after notify, after learning. Run 2 (`c58a9186`, $1.34, achieved/
closure, warm: same goal → reused run-1's fork-master, 4 of 7 steps
read_artifact auditing run 1's note) proved the magnitude — run 1's
fresh data gave maintenance real work, and **136.7s of skill-promotion
validations (3 LLM calls) ran entirely post-notify; post-notify tail
182.3s total**. Under pre-phase-1 ordering that maintenance would have
sat before closure, ahead of the user's answer.

**Codex 2-lens adversarial review of the first cut (6f58bf3) — REJECT,
consensus HIGH, fixed same session (7th earning round for this arc's
modules):** the first cut inferred the drain contract from
`defer_learning + handle_id`, but the direct-CLI lanes (`maro run`
cli.py:716, `maro resume` cli.py:2406) set defer_learning=True, finalize
learning THEMSELVES (`_finalize_cli_deferred_learning` calls
finalize_deferred_learning directly — no registry involved), and drain
nothing — their whole maintenance tail would have silently dropped,
violating the entry's own "never silently drop" contract. Fix: the
explicit `defer_maintenance` opt-in above. Second accepted finding:
deferral ran maintenance outside `loop_id_scope`, orphaning SKILL_* /
EVOLVER_* captains-log attribution — fixed with the scope re-enter.
**Residuals from the review, accepted + filed rather than fixed:**
(a) deferred maintenance events/calls land AFTER `close_run` cut the
captains-log slice and built the card, so run_card `n_calls` + the
run's log slice under-report the tail — same class as the deferred
LEARNING lane has had since 2026-07-17, and the cost half was already
filed below; treat as one visibility item (card/slice/cost) in phase 2
or the cost-truth follow-on. (b) When `inspector.run_cadence` is
enabled, the quality gate now reads the PREVIOUS cadence firing's
inspector summary (one-firing lag on cadence-fire runs only; default
OFF). (c) The gate-escalation retry (and mid-run restart-chain loops)
no longer see same-run maintenance products — concretely, a skill whose
circuit opened THIS run stays open (excluded from recall) instead of
possibly entering the retry half-open-rewritten; accepted: the failure
mode is a missing nice-to-have, not a poisoned input, and the decree
targets exactly that pre-answer wait. Revisit if a gate-escalated retry
is ever observed failing on a skill the old ordering would have
rewritten. (d) Pre-existing, now shared by both registries: an
exception path where handle() can't recover the handle id (`_hid=None`)
strands registered callables in a long-lived process — same class as
the learning lane, fix both together if it ever bites.

**PHASE 2 + VISIBILITY SHIPPED 2026-08-13 (7cba250 + c928833):**
answer-first notify at final-step compile with an explicit
`verdict_pending` metadata marker; the verdict follows as a
`run_verdict` event (revised answer re-sent only when a gate
escalation changed it, sha-compared). Handle lane only, killswitch
`notify.verdict_followup` (default ON), non-done terminals stay
synchronous. The marker is the durable contract: curation class
`done-verdict-pending` (tri-state — any stamped verdict outranks),
close_run's never-stamped tripwire stands down while ACTIVE and
regains authority at finalize (resolution deliberately precedes the
finalize close), and `audit_repair.sweep_verdict_orphans`
(heartbeat-wired, shared repair pidfile, 1h grace over the marker's
`since`) stamps crashed-mid-tail runs `verdict_pending_orphaned` —
achieved stays None, marker resolves only AFTER the durable ledger
stamp, and resolve-only when a real verdict landed. Visibility half:
`metrics.tail_cost_scope` wraps closure/gate/drains (escalation
re-run outside the scope; executor calls excluded at the llm seam) so
the tail's ~30 calls now write provider-priced loop-joined
step-costs rows; `record_llm_call` carries per-call `cost_usd`; a
drained tail triggers re-slice + curator refresh + re-render.
Watch-fors resolved as designed: answer synthesis runs verdict-blind
at the early close (the post-verdict refresh re-pays it only when
`curation.answer_synthesis` is ON — 2 curations per run instead of
1); goal_achieved consumers at notify time already tolerated
unjudged, and the pending class keeps them honest. NOTE for the box:
the early close adds one extra `close_run` (snapshot/curate) per done
run — watch the first live runs' tails for surprises. Cross-model
3-lens review ran same day (see docs/history record).

Original entry:

His phrasing: "at some point we should async the 'cleanup and therefore'
part of the job at the end and return the run's result; no need to make
the end user wait for closure while all that runs." Data from run
`2a3b1f85-sunny-wren` (clean agenda research run, same day): final step
ended 23:35:56, closure verdict 23:43:28, claim probes 23:44:25, run
done 23:44:30 — **the user-facing answer waited 8m34s after the work was
finished, ~40% of a 22-minute run's wall clock**. Slice timestamps
decompose it: **~6min (70%) is evolver/maintenance running INLINE
BEFORE closure** (5 skill-promotion validations 23:36:31-23:40:06,
skill rewrite, 3 knowledge-node promotions, A/B challenger — ~28 Sonnet
calls), then ~1min closure, ~1min gate incl. the adversarial claim
pass. Lesson extraction is ALREADY correctly deferred post-notify (the
2026-07-17 answer-first work, `_defer_learning_post_notify` /
`_drain_deferred_learning` in handle.py) — the maintenance phase just
never joined it. **Two-phase shape, cheap win first:** (1) route the
maintenance/evolver phase through the EXISTING defer hook (or
heartbeat) — no new mechanism, no verdict semantics change, recovers
~70% of the tail; (2) the real design chunk: notify/reply at
final-step compile with "verdict pending", closure verdict arrives as
a follow-up stamp (verdict_history already supports supersede), sweep
for crash-orphaned unstamped runs via the repair-audits contract.
Watch-fors: the answer synthesis (`curation.synthesize_answer`)
currently rides the tail — move ahead or deliver raw result first;
consumers of goal_achieved at notify time must tolerate pending (they
already tolerate unjudged); concurrency is NOT new — concurrent runs
already run these tails against the shared stores under existing locks
(memory ledger, knowledge tiers, index.html.lock), deferral only moves
WHEN the same process does the work; heartbeat-routing WOULD need
single-flight dedup. And the **tail's ~30 calls are invisible in
`total_cost_usd` today** — the b05af61 cost-truth lane sums loop-step
provider costs only, so the card under-reports true run cost by the
whole maintenance tail (fold into the async chunk or fix separately).

### from BACKLOG: Workspace export/import — rounds 1-3, format v2, reviews, inspect, meta-staging (2026-08-12/13)

Ran `scripts/maro_export.py export` on the box (389MB / 23,555 files →
108MB tar.gz, niced, mid-cook), scp'd (sha256 match both ends), imported
into a fresh M1 location via `MARO_WORKSPACE=~/maro-box-copy/workspace`.
**File-level reconciliation EXACT: 23,555 − 37 excluded = 23,518
imported.** The copy is served correctly by real tooling: 1,504 outcome
rows via `load_outcomes` (beware its default `limit=20` when counting),
152 run cards, `correspondence.db` PRAGMA integrity_check ok — noting it
was hot-copied while the box was mid-run; **export has no quiesce/
snapshot story**, sqlite survived this time, not guaranteed.

**What does NOT transfer, by class:**
1. **User-tier config — behavior, not data.** `~/.maro/config.yml` lives
   OUTSIDE the workspace (model prefs, yolo, `scope_generation: true`,
   adaptive_execution, notify/telegram, viz), as does
   `~/.maro/experiments/`. Runs against an imported copy behave like a
   fresh install for those (scope injection off, different model prefs) —
   an uncontrolled condition delta for any A/B run against copied data.
2. **Deliberate exclusions, one over-broad.** secrets (1) +
   telegram_offset.txt (1) = by design. But the `logs/` exclusion was
   path-COMPONENT based and ate 35 logs-component files — archived
   clones imported fsck-clean but reflog-incomplete. *(Correction
   2026-08-13: only the 11 under `projects/**/.git/logs/*` were
   verified reflogs; the 24 under `archive/` left the box before being
   classified — the v1 note labeled all 35 "reflogs" from a truncated
   head listing. The over-exclusion itself was real either way.)*
   FIXED 2026-08-12: pattern anchored to workspace-root `logs/`.
3. **Embedded machine semantics ride along but don't resolve.** 5,358
   files (~23%) contain `/home/clawd` absolute paths (checkpoints,
   artifact pointers, metadata); 6 symlinks point at box paths — and
   `runs/*/scratch/venv/bin/python3 → /usr/bin/python3` RESOLVES on
   macOS to the wrong binary rather than dangling, the sneakier failure.
   Reading is fine; anything that resolves paths cross-machine (resume,
   replay, provenance path checks) will misbehave.
   `state/subprocess_fork_masters.json` carries box CLI session ids —
   meaningless off-box, self-heals (evict → re-seed bare).
4. **Import is a MERGE, not a restore.** Extract overwrites archive
   files and leaves everything else — importing over a non-empty
   workspace yields an unmarked hybrid. Fresh-dir import (as done here)
   sidesteps it; a `--clean` / manifest mode is the fix if this ever
   becomes a real restore lane.

Cosmetic: export's "N files" count includes directory entries (reported
29,123 vs 23,555 real files) — the number lies to a reconciler.

**ROUND 2 — delete, re-export, re-import with the hardened tool
(2026-08-13): the exporter now captures everything a tar CAN carry.**
Name-level reconciliation (pre/post box file-list snapshots vs archive
members vs imported tree, box mid-cook): 23,700 pre-export files →
23,696 archived → 23,696 imported, archive↔copy delta ZERO. Predicted
exclusions = exactly [secrets/.env, telegram_offset.txt]. The only
other absentees were one cold backup db's `-wal`/`-shm` sidecars —
**by design, and proven lossless: the folded snapshot matches the
box's db+wal view row-for-row across all 9 tables, integrity ok.**
Reflogs 11/11 ship (over-exclusion fix confirmed live). Honest counts
confirmed ("23,696 files (+5,640 dirs/links)"). 600-mode files arrive
600. Box has no sockets/fifos/hardlinks to lose. correspondence.db 15
tables both sides. **Residual gaps are now location and semantics, not
fidelity** — unchanged classes: user-tier `~/.maro/config.yml` +
`~/.maro/experiments/` (outside the workspace), 5,401 files embedding
`/home/clawd` paths, 6 box-target symlinks, ownership mapped to the
importing user.

**ROUND 3 — archive format v2 (2026-08-13, Jeremy's export-scope
decree: "the behavior IS data … all of our metadata"): the location
gaps are CLOSED, carried under a new `meta/` area.** user-config.yml
(credential-shaped values redacted at export; box config verified
credential-free) + experiments/ (380 files) now ride the archive;
machine-pointing symlinks travel as `meta/symlinks.json` data instead
of links (chained-link aware — the venv python→python3→/usr/bin chain
correctly classifies external; internal relative links still ship as
links); `meta/provenance.json` carries exporter identity, source
roots, tool fingerprint, a manifest digest (integrity hint, NOT
tamper-proof — no signing yet, deliberately labeled), and a custody
chain every import appends to — groundwork for the future
injection-guard/sharing security layer. Import stages all meta
non-destructively under `<ws>/.import-meta/` (behavior never applied
silently); `--apply-meta` places user config with a backup. Live
box→M1 round-trip: digest OK, custody chain
export(clawd@…)→import(jeremy@…), v1 archives still import clean.
Provenance's first catch: **the Ubuntu box's hostname is still
`jeremy-Macmini-2014`.** REMAINING (inherent semantics, not location):
embedded `/home/clawd` paths inside artifacts/checkpoints —
provenance now records `source.workspace_root` so a future consumer
CAN rewrite them; ownership maps to the importing user. Live copy:
`~/maro-box-copy/workspace` (`MARO_WORKSPACE`); prior copies kept aside
at `workspace.pre-import-*` (prune when convenient).

**3-lens Codex review of format v2 (c257a48) — REJECT, all fixed
2026-08-13; every accepted finding reproduced first.** The review's
frame was correct: an archive is UNTRUSTED input on import, and the
first cut trusted it. Three HIGHs: (1) the line-based credential
redactor leaked nested/block/inline YAML secrets AND clobbered benign
`max_tokens` — replaced with structural YAML redaction (whole-word
credential-key match, string-values-only so numeric settings survive,
fail-CLOSED when the YAML won't parse: config not exported rather than
shipped raw); (2) import extracted hostile member types/links —
`filter="tar"` permitted FIFOs and absolute symlink targets, the
<3.11.4 fallback was fully unfiltered; a crafted `workspace/leak →
/etc/passwd` imported as a live link — now every member is preflighted
against a type + link-target allowlist BEFORE any mutation, with
`filter="data"` defense-in-depth; (3) `--apply-meta` applied a STALE
config from a prior import when the current archive had none — meta now
stages into a fresh per-import dir `<ws>/.import-meta/import-<ts>/` and
apply is gated on the current archive's provenance. Mediums fixed: no
format gate (a v99 archive silently half-imported → refused before
mutation); malformed provenance crashed AFTER extraction (→ validated/
coerced to a safe shape first); terminal injection via archive-authored
provenance (CR/newline/ANSI forged fake digest lines → sanitized +
capped before print); digest overstated coverage ("manifest verified" →
"workspace shape digest, names+sizes only, self-attested, UNSIGNED",
custody key `shape_verified`); custody didn't survive a hop (re-export
now carries the prior chain forward: export→import→export); zip-bomb
exposure (meta streamed with per-file/member-count/provenance-size/
custody-print caps); secret-shaped meta now screened at import too.
Lows: cyclic symlink aborted export (→ recorded external); experiment
external symlinks routed through symlinks.json for parity; script
fingerprint stored full-length. +22 pins (TestV2ReviewHardening +
additions). Live box→M1 round-trip clean. Archive
`~/mbx-v3b.tar.gz` (regenerate as needed). Suite 8366/0 skipped.

**`maro-export inspect ARCHIVE` — SHIPPED 2026-08-13 (overnight).**
Look-before-you-import for the sharing use case: read-only, prints
provenance + custody, verifies the workspace-shape digest against the
archive's own bytes, and previews what import WOULD skip (unsafe member
types/links, secret-shaped meta, traversal) — nothing extracted or
mutated. **Cross-model 2-lens review RAN 2026-08-13 (the deferred pass;
defensive framing worked, no cyber-filter trip) — REJECT, all 5 findings
fixed same day (9c4d23c):** exit codes now fail closed (3 newer / 2
mismatch / 5 malformed provenance / 4 unsafe present / 0 clean only),
one shared streaming classifier feeds both inspect preview and import
enforcement (the two had already diverged on meta screens, exclusions,
and provenance selection; import's own quadratic partition fixed by the
same pass), provenance format must be a canonical int and import
refuses malformed provenance outright, archive-authored text sanitized
at every print, and inspect prints the archive sha256 which import can
pin via --expect-sha256. Verdict:
docs/history/2026-08-13-maro-export-inspect-adversarial-review.md.
15 pins (TestInspect + TestInspectReviewHardening). Re-verified on the
real box archive (23,696 files, 0 unsafe; its digest MISMATCH is
genuine hot-export drift, identical under the pre-refactor tool).
Suite 8464/0 skipped.

**Meta staging placement — DECIDED 2026-08-13 (delegated: "up to
you"):** stays under the workspace at `<ws>/.import-meta/`, now one
subdir PER import (`import-<ts>/`) after the v2 review (each import's
meta + custody isolated, so a later import can't apply an earlier one's
stale config). Jeremy's gut ("operational/working data → under the
workspace") is what's built: staged meta travels with the workspace
copy, gets moved aside together by `--clean`, and re-exports skip the
whole `.import-meta` subtree (while READING its newest provenance to
carry custody forward). The `meta/`-vs-`workspace/` split inside the
ARCHIVE is a format concern (routing + back-compat), not a statement
about where meta lives at rest.

### from BACKLOG: Workspace export/import — review-hardened fixes of 707a541

**Review-hardened same day (3-lens Codex review of 707a541 — REJECT →
fixed):** all reproduced before fixing. (1) traversal guard was
`str.startswith` — `<ws>-evil` passed as inside `<ws>`, and on Python
≤3.11.3 the filter-less extract fallback made it a real
out-of-workspace write (consensus HIGH, 3× reproduced) → `relative_to`
containment; (2) `_snapshot_sqlite` built its `file:` URI by f-string —
a `?` or `#` in the filename truncated the path and "succeeded" with an
EMPTY snapshot (HIGH) → `as_uri()` percent-encoding + page-count parity
required for success; (3) snapshots were tarred with the temp file's
0644/now metadata, broadening a 0600 db on restore → source TarInfo
carried onto snapshot bytes; (4) `--clean` aside names collided at
1-second resolution → uniqued, and its non-atomicity documented (aside
always holds complete pre-import state; recovery = rename back);
(5) import counter counted dirs as files → files/others split like
export. Pins: TestReviewHardening in test_conductor_export.py.

### from BACKLOG: Session-fork lane for claude -p — SHIPPED 2026-08-08, default ON

His phrasing: "storing some meta-data and having a master subagent
process fork over and over, driven by the on-disk meta-data, to get
around the bootstrapping costs" — clarified to mean **fork the
session, not the process**, which the CLI already supports
(`--resume MASTER --fork-session`). Benchmarked same day
(`~/.maro/workspace/output/claude-fork-bench/`, n=6/arm, haiku): bare
`-p` re-creates ~12k cache tokens EVERY call (per-invocation context
is never a byte-stable prefix across processes — the slack-bridge
no-cache finding, now with receipts) vs ~250 for forks; **~5× lower
cost/quota burn, ~34% lower API latency**, wins even on unique
prompts. SHIPPED as `subprocess.session_fork` (default OFF,
docs/DEFAULTS.md): ClaudeSubprocessAdapter seeds one master per
(binary, model, no_tools, cwd), persists ids to
`<workspace>/state/subprocess_fork_masters.json`, forks per stateless
call; executor sessions + container calls stay bare; evicted masters
fall back bare and re-seed. Live-smoked on the real CLI. **Default ON
since same day** (Jeremy: "Let's turn that on by default"; flip landed
after the in-flight Δ retest finished — journal 8aa8463b). **Residuals:**
(1) ~~flip ON~~ DONE; (2)
process-boot latency (~2–3s/call) remains — the warm-daemon/Agent-SDK
variant (one long-lived worker consuming job files) is the upgrade
edge if boot time ever matters at census scale (966 replays ≈ 40–50
min of boot); (3) fork session files accrete under ~/.claude/projects
— opt-in cleanup only, per data retention.

### from BACKLOG: Re-run identity v1 — ship + adversarial-review record (2026-08-10)

**v1 SHIPPED 2026-08-10** (`src/rerun_identity.py`, killswitch
`rerun.brief` default ON — deterministic, read-only, zero LLM spend;
DEFAULTS.md row): normalized exact-text match over
`memory/handle_inputs.jsonl` → complete prior-attempts list, each verdict
read WITH STANDING from run metadata (`operator_restamp` renders as "do
not read it as failure", contested as "anecdote, not ground truth") →
provenance-labeled brief injected at both consumer seams: (a) the
dispatch navigator (`NavigatorInput.prior_attempts_block`, rendered with
an explicit "when recall disagrees, THIS wins" precedence note; threaded
handle_queue → shadow_dispatch_live; `origin["rerun"]`
{count, prior_handles} stamps run metadata for analysis), and (b) the
AGENDA run context (handle() `_extra_ctx_parts` beside dispatch recall —
the brief's guidance says build on deliverables, verify before
overwriting; a top-deliverables listing from the shared project dir rides
along). 28 pins (tests/test_rerun_identity.py). **Named v1 cuts:** exact
normalized match only (a prefix-bearing variant won't match its bare
sibling); newest 12 matches get metadata resolution, older rows counted
not inspected; dry-run previews dropped as non-attempts; the
deterministic recall guard deliberately untouched (its contested-
neutrality hole is the shared typed-contested-state residual above);
fuzzy/paraphrase matching stays a separate later decision.

**Adversarial review round (2026-08-10, Codex ×3 lenses):
REJECT-as-reviewed → remediated same session** (7th time this arc's
reviews have earned their cost; both injection findings came with
working repros). Fixed: (1) worker-controlled FILENAMES with embedded
newlines forged brief structure inside the navigator's
highest-precedence section → control-char/length rejection at the
boundary, quoted rendering, explicit untrusted-data label; (2) the
"COMPLETE" header vs 5-shown cap could hide a restamped success behind
five failures — the exact misread the module exists to prevent → every
inspected attempt renders, only the uninspected tail collapses, and the
claim names its own bound (handle-dispatch record; CLI-lane runs
absent); (3) physical file order treated as chronology (workspace
import appends history) → stable sort on row timestamps; intake write
upgraded to `file_lock.locked_append` + `dry_run` stamped at write;
(4) dry-run previews consumed the 12-slot resolution budget → budget
counts real attempts (ceiling 36 reads); (5) non-dict JSON rows /
metadata arrays escaped the per-line guard and silently emptied ALL
history → isinstance guards, per-row degradation; (6) restamp direction
was assumed positive — an operator correcting success→False got
achieved-shaped language → direction-aware wording; (7) unjudged rows
now keep verdict source/contest/stop provenance; (8) the brief could
name a project the recent-5 menu binder would then REJECT → exact-
history projects are a second offer surface (binder union +
navigator-prompt affordance note); (9) `origin["rerun"]` declared in
the Origin TypedDict; (10) prefilter deleted (speculative, had an
escape-encoding edge); guidance paragraph tightened;
`recall.dispatch_inject`'s "amnesiac" DEFAULTS row corrected (two
history lanes now); handle_task integration test added. 40 pins total;
suite 8142/0 skipped.

### from BACKLOG: Re-run identity — original entry (founding decree + specimens)

Original entry (re-run identity):

His phrasing, watching the 4th verbatim dispatch of the Model-or-Harness
self-eval: *"a re-run should know it's a re-run, with prior art, instead
of believing all the data is it's own directly."* The specimens, one per
layer:
- **Dispatch layer:** the 4th dispatch was PREVENTED — the navigator's
  1200-char recall served it run 18773dfa's (then-false, since
  restamped) "prior attempts failed by claiming completion" verdict with
  no provenance framing, no marker that the record was contested, no
  sight of the two successful attempts, and no signal that this was a
  deliberate verbatim re-dispatch. It escalated at conf 0.95 on a
  one-sided fragment it treated as its own knowledge. (Same disease
  shape as the 2026-08-03 bounded-judge finds: a consumer given a
  bounded, unattributed view treating it as the whole truth.)
- **Run layer:** runs 2 and 3 only BENEFITED from prior art via
  project-slug collision + forensics — run 3's first two steps were
  archaeology ("ls -la to confirm whether FINAL_REPORT.md actually
  exists (recall lists only FINAL_VERDICT.md)"), and its own minted
  lesson was that recall served an incomplete fragment of the prior
  deliverable set. Discovery worked but was paid in steps, every time,
  and is luck-dependent.

Fix shape: re-run identity is deterministically detectable at intake —
`handle_inputs.jsonl` records every raw dispatch, so a normalized hash
of `user_ask` at enqueue/handle time yields "N prior attempts, handles
X/Y/Z" with zero LLM spend. Inject a compact **prior-attempts brief** as
a typed, provenance-labeled channel (the dispatch-envelope pattern:
history rides its own channel, never becomes the goal): per prior
handle — date, verdict WITH STANDING (achieved / contested / operator-
restamped), stop verdict if any, top deliverable paths in the shared
project dir. Consumers: (a) the dispatch navigator — its prior for
"verbatim re-dispatch, latest attempt succeeded" must differ from
"fails repeatedly"; (b) the planner/scope — step 1 becomes "build on
<prior deliverable>" instead of archaeology. Complete-by-construction
(all attempts, not top-k recall snippets), which also fixes the
navigator's one-sided sample. Dovetails with the contested-neutrality
residual above (verdict standing unenforced in consumers) — the brief
carries standing explicitly. Exact-hash first; fuzzy/paraphrase match
is a later, separate decision (five-word-slug collision hazard is the
cautionary sibling).

Supporting datapoint (2026-08-09, run 4 `6b14e413-cobalt-orchard` after
the operator re-stamp): with clean records, the re-dispatch converged on
"no rewrite needed" in ONE inventory step and named all three prior
loops by id — clean records make the archaeology cheap, but it was still
archaeology (steps spent rediscovering provenance the intake layer
already knows). The brief would hand that over for free.

### from BACKLOG: Closure Signal-1 downgrade — fix + adversarial hardening (SHIPPED 2026-08-09)

**SHIPPED 2026-08-09 (same-day, Jeremy: "go ahead and fix that... sure
seems like we need a verdict verification pass"):** (a) Signal 1 now
stands down when every deliverable EXPLICITLY declares a document/data
shape — deliberately narrower than fix-shape (a) below: the decided call
in `test_signal1_admission_ignores_deliverable_shape` (unshaped
document-looking deliverables must NOT suppress a self-contradiction
admission) is honored, and the B1 declared-shape-is-authoritative
doctrine carries the stand-down instead
(`closure_verify._declared_all_document_deliverables`). (b) The systemic
net is the new **verdict-audit pass** (`closure.verdict_audit`, default
ON, DEFAULTS.md row): a negative verdict with zero cleanly-failed checks
and ≥1 clean pass gets one second-opinion call holding the goal, the
verdict's own reasoning, the check outcomes, and ground-truth excerpts of
the files the checks referenced. Disagreement cancels a pending
deterministic downgrade outright; a judge-asserted False gets ONE retry
with the objection attached; a retry that maintains False stands, stamped
`verdict_audit.disputed`, and handle.py routes the loop into the existing
contested learning holdout — closure-internal negatives finally have a
contest lane (fix-shape (c) below). Covers the no-ResolvedIntent lane the
Signal-1 gate can't reach (both 18773dfa and its warm predecessor had
`scope-raw-FAILED.txt`, so no deliverable shapes existed at closure
time). Tests: `tests/test_closure_verdict_audit.py` + Signal-1 gate cases
in `test_director.py`; full suite 8055 passed / 0 skipped.

**Adversarial review round (2026-08-09, Codex ×3 lenses): REJECT on the
first cut — 5 consensus ship-blockers, all fixed same-day before the box
opted in.** The review earned its cost a 5th time in this module: (1) a
retry adapter crash escaped to the function-wide handler and returned the
null "verification did not run" verdict, ERASING the real negative and
its checks → retry is now locally error-bounded, original verdict
preserved, `retry_failed` stamped; (2) a retry flipping to achieved
bypassed every deterministic downgrade detector (they ran while
complete=False and stood down) → retry verdicts now re-run through both
detectors (`retry_redowngraded`); (3) cancel authority covered Signals
2/3 + diagnosis, whose structured runtime evidence the auditor's own
"absence isn't failure" doctrine is blind to → cancel scoped to Signal 1
only (keyed structurally via `_detect_behavioral_gap_ex`'s signal id, not
a reason-string literal), others record `disputed`; (4) truthiness
coercion at the LLM boundary (`"agrees": 0`, `"complete": "false"`) →
exact JSON booleans required, plus a 0.6 confidence floor for the
disagreement to act; (5) worker-authored artifact excerpts reached the
auditor un-scanned → workspace containment + injection_guard scan
(fail-closed withhold) + explicit untrusted-data fencing. Also flipped
the config default OFF per the fresh-installs-conservative convention
(this box opts in via workspace config).

### from BACKLOG: Closure Signal-1 downgrade — re-stamp EXECUTED + original finding record

**Re-stamp EXECUTED 2026-08-09T12:06Z** — both false records corrected
to `goal_achieved=True` / `goal_verdict_source=operator_restamp` (loops
`eff982cc` for 18773dfa, conf 0.78; `da047274` for de790c13, conf 0.85;
`stop_verdict=lost-the-plot` cleared, original kept under
`stop_verdict_superseded`), across outcomes.jsonl (via
`memory_ledger.stamp_outcome_verdict` + locked rewrite), metadata.json,
and run_card.json; originals preserved under `*_superseded` keys; script
kept at `~/.maro/workspace/output/operator-restamp-2026-08-09.py` on the
box (copied out of volatile /tmp 2026-08-11). Trigger: the 4th verbatim
dispatch (run `6b14e413-cobalt-orchard`) was PREVENTED by the navigator
conf-quoting the poisoned record — the false verdict's first live
downstream victim (specimen recorded in the re-run identity entry
above). Post-re-stamp, the re-dispatch ran clean: achieved=True via
closure (0.75), no downgrade, no contest, `verdict_audit` trail `{}`
(correctly idle — nothing negative to audit), $2.82. Four-run verdict
series: false-demoted / clean / false-demoted / clean+guarded. Verified
on-box 2026-08-10 (outcomes.jsonl rows + run-4 metadata).

Original finding record:

Specimen: `18773dfa-olive-wren`, the 3rd verbatim replay of the
Model-or-Harness self-eval (see MH. entry above). The run did the right
thing end to end: recognized FINAL_VERDICT.md (post step-9) as the
authoritative deliverable, wrote `artifacts/discrepancy-crosscheck.md`
(reconciling five tally sources — the only broken one is FINAL_REPORT.md's
stale 6/21) + `artifacts/closure-note.md`, surfaced the one real residual
(FINAL_REPORT.md:87-91's source-paper methodology critique — κ=0.76
caveat, interaction-centric-vs-Maro-topology mismatch — never ported into
FINAL_VERDICT.md), and HONORED the envelope's research-only constraint by
recommending rather than performing the 3-bullet port. Closure judged
"Goal achieved," 5/5 checks passed, 0 failed. Then the deterministic
post-processor downgraded it: `closure_verify.py`'s **Signal 1**
(`_RUNTIME_GAP_ADMISSION`, L1578, branch `(?:not|never|…)
(?:…|executed|…)`) matched the summary's honest "not executed" — a phrase
whose OBJECT was the optional recommended follow-up the constraint
forbade — and stamped achieved=False, conf 0.78,
`stop_verdict=lost-the-plot`.

Three compounding problems: (1) **fix-every-lane, again** — the
function's own docstring records this exact false-positive class
(fd483efb, 2026-07-11: document-only goal, bare `\bprocess\b` hit
downgraded a 5/5-checks 0.98 verdict) and the fix gated Signal 2 with
deliverables-corroboration; Signal 1 kept the defect (no document-only
carve-out at all). (2) **No contest lane** — provenance-vs-closure
contests don't apply to a closure-internal downgrade, and the `f7b775c`
evidence-of-failure floor doesn't reach this path (conf 0.78 > 0.7, and
the downgrade is deterministic, not judge-asserted), so this false
not-achieved STANDS in learning — unlike the contested cold-run demotion,
which was held out. (3) The demotion punished exactly the honest-
disclosure behavior we want runs to keep (cf. the LT-1 #7-attempt-1
escalation specimen).

Fix shape, in order of durability: (a) extend the document-only/
static-modality guard to Signal 1 — when every deliverable is a document
and all checks passed, a prose phrase-match is weak evidence (the
docstring already argues this for Signal 2); (b) require the matched
admission to BIND to a goal deliverable rather than any
recommendation/optional item in the summary+gaps window (object check,
not another regex literal — the provenance-costume lesson); (c) decide
whether deterministic closure downgrades should get the same
contest/holdout treatment provenance demotions got on 2026-08-02.
Operator decision pending either way: re-stamp `18773dfa` (and
`de790c13`, still False on the record from the brace-template costume) or
leave both false verdicts standing.

### from BACKLOG: LT-5 sonnet-tier steal-list re-run — verdict flipped to achieved (2026-08-06)

**LT-5 (2026-08-06, Jeremy: "worth a re-run of this test directing
sonnet as the min bar?"): sonnet-tier arm of the 83a2c805 steal
analysis — verdict FLIPPED to achieved at LOWER provider cost** ($3.69
vs $3.87; 8 steps vs 12 — the tier premium was repaid by not needing a
recovery loop). Quality clearly up (grep-grounded adopt/adapt/reject
mapping, dissent integrated). The claims-vs-events family recurred AT
SONNET (fabricated "authenticated fetch" provenance, zero fetch events
in the claiming step) → model-independent, strengthens follow-up (c)
mint-time grounding. Mount-blindness recurred too but sonnet routed
around the trap (raw.githubusercontent fetch instead of expecting a
local checkout) — refined thesis: structure sets the traps, tier
changes how often you fall in. NEW specimen: in-run reviewer DISMISSED
a true discovery (the a0bae77 use_count fix, verified real) on a
"hallucination signature" heuristic instead of probing — inverse of
verify-before-fix; positive-evidence principle applies to skeptics
too. Full record + pre-registered predictions:
`~/.maro/workspace/output/steal-rerun-sonnet/predictions.md`. Opus
advisor fired 8× (baseline 1×) — the escalation ladder engages more at
higher base tier; worth a look when model-routing exploration resumes.

### from BACKLOG: LT-0(a) verdict-blind lanes — CLOSED 2026-07-31

  - [x] **(a) Verdict-blind lanes — CLOSED 2026-07-31 by chunk B**
    (concurrent session, not this arc; GOAL_BRAIN "Chunk B shipped:
    verdict-blind lanes closed + verdict-flow readout"). `_verify_post_apply`
    now stamps `goal_achieved` from the test suite (the suite IS the judge);
    interactive NOW is judged via the hosted-free family only;
    `stamp_outcome_verdict` stamps `goal_verdict_at` so framing→verdict
    delay is measurable. **Its live census independently confirms this
    arc's pooling correction from the other side** — "agenda flow is
    healthy since W28 (18/34 → 6/12 judged); the 3% all-time stock is
    April-era pre-verdict rows (no backfill, by decree)." So the census's
    96.3%-unverdictable headline was stock, not a live leak.
    **`python3 -m verdict_flow` is the authority on verdictability from
    here — not this census**, which now says so in its own output.

### from BACKLOG: LT-0(b) done-without-closure tripwire — SHIPPED 2026-08-02

  - [x] **(b) done-without-closure tripwire — SHIPPED 2026-08-02.** 5 of 51
    loop_id-era agenda rows are status=done with no verdict in either store
    (closure never ran, not a stamp miss); 2 were same-day, so it's live
    and low-rate. Shipped: `DONE_WITHOUT_VERDICT` captain's-log event in
    `runs.close_run` — fires when a done agenda run's metadata carries no
    `goal_verdict_source` (NOW lane + dry runs exempt), emitted before the
    log slice so it rides the run's own slice; user-surfaced (honesty
    family). Marks the gap, doesn't adjudicate — audit-repair can still
    stamp later. Tests in `test_runs.py`; docs/CAPTAINS_LOG_EVENTS.md row.

### from BACKLOG: LT-0(c) run-dir record census — tool, predictions, corrected baseline, BLOCKERS retraction (settled 2026-08-01)

  - [x] **(c) Run-dir record census — TOOL BUILT 2026-07-31; BOX RUN DONE
    same day.** Baseline committed (deliberate gitignore exception, per the
    re-run+diff protocol below): `output/provenance_census/census.{json,txt}`
    — 734 runs, commit 861ab28, generated 2026-08-01T05:22Z. Predictions
    scored against the pre-registered table:
    - `build/calls/` **came back otherwise — and the "otherwise" reading
      was WRONG, my instrument's fault (corrected 2026-08-01).** The
      scored line said "record mode is effectively off in production."
      It is not. The 12.6%-all-history / 50%-post-era pair pooled 619 runs
      that PREDATE record mode (shipped 2026-06-26) with runs that could
      have used it, and the post-era bucket was **n=8**. The monthly
      series says: 2026-04 0%, 2026-05 0% (n=476), 2026-06 0% (n=141,
      feature shipped on the 26th), **2026-07 80.7% (92/114)**. Record
      mode works. The real, much narrower finding: **~19% of July runs
      still capture zero calls** — the ContextVar/lane hole of EDGE 2, at
      a fifth of runs rather than seven-eighths. Separately true and worth
      recording: the **pre-2026-06-26 corpus (~619 runs) is permanently
      blind on LLM I/O** — not a bug, the feature did not exist — so
      retrospective prompt/response mining of that history is impossible.
    - **The real alarm the pooled table buried: `recall_citations.json` at
      10% of July agenda runs** (2.1% all-history). This is the artifact
      naming *which lessons a run cited* — the exact rail a cold/warm
      delta needs to attribute an improvement to a specific lesson. The
      pre-registered reading said "all-absent = the citation join is dead
      and cold/warm attribution has no rail"; 10% is not all-absent but is
      close enough to no rail that LT-1 cannot measure what it exists to
      measure until this is fixed. Its partner
      `skills_manifest.jsonl` sits at 55% of July runs — no manifest, no
      attribution, so the skill gets neither credit nor blame.
      **These two, not record mode, are the batch blockers.**
    - outcomes `no_loop_id` **came back otherwise** — 96.3% (1397/1450),
      far beyond NOW-lane-sized; unverdictable by task_type is dominated
      by `agenda` (1256). Agenda runs are losing the join too. **Caveat
      pending re-run:** this number has the same pooling hazard as
      `build/calls/` above and the first census could not rule it out —
      `census_outcomes` had no date bucketing, so pre-loop_id-stamping
      rows are pooled in. The tool now emits a per-month verdictability
      table; **re-run before acting on 96.3%.** The finding is directionally
      real either way (only 41 rows in all of history carry a verdict), but
      the fix differs: a live join failure gets wired now, a historical
      one only means the denominator has to be rebuilt going forward.
    - scope/resolved_intent 87.5% post-era, recall_citations 100% post-era,
      skill_attribution low-by-design — all roughly as predicted.
    - Environment.json/skills_manifest/checkpoint/run_card post-era at
      100% but tiny all-history denominators; per-stage table in census.txt.
      July-only rates from the monthly series: environment 60%,
      skills_manifest 55%, checkpoint 48%, run_card 93%, step-*.md 50%,
      scope/resolved_intent 80%, plan.md 96%, report.html 90%,
      captains_log_slice 92%. `skill_attribution.json` 29% stays
      correct-by-design (FULL-trust verdicts only).
    - [x] **Instrument fixed 2026-08-01 — the era split was the defect.**
      A single late boundary (2026-07-29) left n=8 post and pooled
      pre-feature history into the whole-history column, which is how a
      working feature got scored as a production outage. The census now
      emits a **per-month coverage series** (artifacts) and a **per-month
      verdictability series** (outcomes), marks thin denominators (<20
      runs) with `~`, and says in both the code comment and the rendered
      header that the monthly view is load-bearing and the era columns must
      not be read alone. **Re-run on the box** to get a corrected baseline;
      diff against the committed one.
    `scripts/provenance_census.py` + `scripts/provenance-census.sh`.
    Covers 16 artifacts across 10 harness stages (intake, scoping, recall,
    skill-injection, llm-io, events, planning, step-exec, reporting,
    curation, attribution), plus the verdictability join from
    `outcomes.jsonl` that (a) and (b) need denominators for.

    **BOX HANDOFF — run this, hand back `census.json`:**
    ```bash
    cd /home/clawd/claude/maro-orchestration
    git pull
    setsid nohup bash scripts/provenance-census.sh &   # detached
    # artifacts → output/provenance_census/{census.json,census.txt}
    ```
    **Same trip — the SF-13 runtime pipe for this arc's decrees.** These
    six are in GOAL_BRAIN.md already; `decisions.jsonl` lives in the
    *workspace* memory dir, so the pipe only reaches recall when it runs
    **on the box** — running it on the dev Mac would write to that
    machine's fossil workspace and never surface to a run. Paste-ready:
    ```bash
    cd /home/clawd/claude/maro-orchestration
    D() { PYTHONPATH=src python3 -m knowledge_lens decision "$1" --rationale "$2" --domain learning-tests; }
    D "Live-learning test batch weights toward burning down CAPABILITIES.md target rows, not net-new goals" \
      "The corpus problem is solved; the evidence problem is not. Every run should also settle a standing claim."
    D "Instrumentation is a prerequisite to the learning batch, and wider than verdicts" \
      "Audit what the runs should be writing down and aren't — at step edges and across harness layers — because we keep stumbling on data we thought we had but didn't."
    D "Learning is measured as a cold-vs-warm re-run delta" \
      "Every test goal runs twice, store cold then warm. Needs no new machinery and cannot be self-graded."
    D "The capability ladder gets its own doc, separate from the goal catalog" \
      "CAPABILITIES.md stays the goal well; docs/CAPABILITY_LADDER.md is the C0-C5 progression map and the blank-slate acceptance tests."
    D "A failure is never a success: no goal may be graded pass-by-refusing" \
      "An expected failure is a goal we haven't engineered or learned to solve yet. Reframe such goals with a positive deliverable; record a genuine miss as not-achieved and feed it to the ladder, so the store cannot learn that declining is what wins."
    D "Trace the full run paper trail before spending on the batch" \
      "Each decision, LLM prompt and output, step plan and artifact must be durable and reachable by the report, the tests, and post-hoc mining."
    D "A coverage percentage is uninterpretable without knowing what its denominator contains" \
      "The first census pooled 619 pre-record-mode runs with post-feature runs and scored a working feature as a production outage; a pre-registered prediction table does not protect against a denominator defect."
    ```
    Safe to run against a working box **by construction**: read-only,
    stdlib-only, and it deliberately does **not** import from `src` (the
    sibling stats scripts do) — importing `config`/`ancestry` would load
    workspace config and let the probe perturb the state it is measuring.
    Wrapper runs it at `nice -n 15`.

    Three design points that keep the numbers honest, worth knowing before
    reading the output:
    - **Per-lane applicability.** A NOW one-shot has no decompose phase, so
      a missing loop plan there is correct, not a gap. Only artifacts
      expected for that lane count against it.
    - **In-flight runs are excluded** from coverage. A run mid-flight
      legitimately lacks finalize artifacts; counting it would manufacture
      findings.
    - **Era bucketing at 2026-07-29** (the attribution seam's ship date).
      A single whole-history rate would understate today's coverage and
      hide a live regression, so pre/post columns are reported separately.

    **Predictions, written down BEFORE the run** (claimed≠probed applied to
    our own audit — so a surprising result can't be rationalized after the
    fact):
    | Artifact | Expected | If it comes back otherwise |
    |---|---|---|
    | `build/calls/` | near-100% post-era (record mode is documented default-ON) | EDGE 2 is live and large — record mode is effectively off in production and we have been reasoning about prompts we never kept |
    | `source/scope.md`, `resolved_intent.md` | high on agenda runs (scope_generation is OFF for fresh installs but ON on this box since 2026-07-09) | the box's scope lane is not writing what we think it is |
    | `source/recall_citations.json` | present on agenda runs that recalled anything | all-absent = the chunk-4 citation join is dead, and cold/warm attribution has no rail |
    | `source/skill_attribution.json` | **LOW, and that is correct** — it only stamps on FULL-trust verdicts | do not read this row as a hole; it is gated by design |
    | outcomes `no_loop_id` | roughly NOW-lane-sized | a larger share means agenda runs are losing the join too |

    **The dev-Mac numbers are not a baseline.** The local workspace holds 3
    runs, all from 2026-07-12 — a fossil predating several of these seams.
    Recorded here so nobody later cites them as a "before" picture. (Local
    output for shape-check only: 0/3 `build/calls/`, 0/1 scope +
    resolved_intent + recall_citations on the single agenda run, 2/3
    outcomes rows unverdictable, both NOW.)

    **Re-run + diff is the protocol.** The first box `census.json` is the
    committed baseline; after fixes, re-run and diff rather than re-eyeball.
    Read the whole census before fixing anything — fixing the loudest row
    first is how you miss the pattern underneath it.

    **CORRECTED BASELINE (box re-run 2026-08-01, commit 312b6a6, same 734
    runs on the fixed instrument). Settled reading:**
    | artifact | all | since | first seen | verdict |
    |---|---|---|---|---|
    | `build/calls/` | 13% | **81%** | 2026-07 | works; 19% live drop |
    | `run_card.json` | 14% | **93%** | 2026-07 | fine |
    | `captains_log_slice` | 89% | 89% | 2026-04 | fine |
    | `plan.md` | 94% | 94% | 2026-04 | fine |
    | `scope.md` / `resolved_intent` | 42% | 42% (**80% in July**) | 2026-04 | improving: May 17 → Jun 74 → Jul 80 |
    | `skills_manifest.jsonl` | 11% | **55%** | 2026-07 | **BLOCKER** |
    | `recall_citations.json` | 2% | **10%** | 2026-07 | **BLOCKER** |
    | `skill_attribution.json` | 6% | 29% | 2026-07 | correct-by-design |

    - [x] ~~**BLOCKERS for LT-1, confirmed not artifacts:** `recall_citations`
      10% and `skills_manifest` 55% … the batch cannot measure what it
      exists to measure until these are fixed.~~ **RETRACTED 2026-08-01 —
      this was wrong, and it was the SAME denominator defect a third and
      fourth time, at finer granularity each time.** I asserted it twice
      (here and in a commit message); the correction:
      - `recall_citations.json`'s writer shipped **2026-07-21** (afe5c5a,
        chunk-4 contradiction wiring). The July bucket pooled **90 runs
        where the file was IMPOSSIBLE** with 15 where it was possible.
        True post-ship rate: **11/15 = 73%**, not 10%.
      - `skills_manifest.jsonl`'s writer shipped **2026-07-09** (f49f318).
        Post-ship: **58/62 = 94%**, not 55%.
      - `scope.md`'s 42% was the *other* defect: 18% across May's 321
        step-shaped pseudo-runs vs **80% on July goal-runs**.
      **Nothing in the paper trail is a blocker.** The rail is in far better
      shape than I reported. What survives as a genuine gap is EDGE 2's
      ~19% silent call-record drop (independently confirmed by the
      plan-produced/reached-done discriminator, so not a ship-date artifact),
      `step-*.md` at 50% of July runs, and `skill_attribution` at 57%
      (by-design, FULL-trust only).
      **The empty-case fix (landed 54ac079) is still correct and worth
      keeping** — it converts an ambiguous absence into an explicit empty
      record, which the cold arm of a cold/warm pair needs. But it is a
      refinement for the residual 4/15 and 4/62 runs, **not an unblocking**.
      Do not let the fix's existence imply the batch was blocked.
      **Instrument fixed again:** first-seen now infers at **day**
      granularity from the earliest run that actually produced the artifact,
      not month. Documented caveat, because it bounds the claim: first-
      OBSERVED is a proxy for the ship date and anchors late when early
      post-ship runs legitimately had nothing to record — `recall_citations`
      reads 100%/n=11 from 07-27 where the true 07-21 figure is 73%. Better
      than months; still not a substitute for knowing the commit.

### from BACKLOG: LT-0 spin-off: stuck runs not surfaced — diagnosed, deliberately not promoted (2026-08-01)

- [x] **SPIN-OFF from the LT-0 verification run: stuck runs are not being
  auto-recovered — DIAGNOSED 2026-08-02, no live gap; NOT promoted.**
  The census read (last-30 = 21 done / 8 stuck / 1 restart; the run's
  words: "stuck runs are largely being left stuck rather than
  auto-recovered") had two candidate readings; the sample separated them
  cleanly in favor of **"stuck is a correct terminal state here and the
  number is just honest."** Joined the 7 recent agenda stuck rows to
  their diagnoses + captain's-log events:
  - 4 × `token_explosion`/`cost_spike` (1e976f5a, f2e2765e, 597558f4,
    3289a1b6) and 1 × `adapter_timeout` (ba58f96c) — every one maps to
    `auto_apply=False, risk=medium` in `introspect._RECOVERY_TABLE`, so
    Phase-45 auto-recovery **correctly declined to fire**: blind re-runs
    of a token explosion burn money without changing the read pattern
    (the real fix lane is the retrieval-handle arc). All five got
    quality-gate ESCALATE attention instead.
  - 1 × navigator escalate-act honest stop (25f6d875, conf 0.92,
    "heuristic recovery overridden") — **stuck by design**.
  - 1 × budget-breaker death (3a4e2692) — its class fix already shipped
    2026-07-29 (auto breaker + escalate-death delivery).
  - The mechanism itself is alive: 12 AUTO_RECOVERY firings ever
    (retry_churn 9, decomposition_too_broad 2, empty_model_output 1;
    last 2026-07-03) — it fires when its low-risk classes appear; the
    recent stuck population just isn't those classes.
  - The 3 recent `evolver_verify` stuck rows carry no loop_id, so
    per-loop diagnosis can't run on them — that's the standing
    "NOW + evolver_verify lanes are verdict-blind" item's territory,
    not a recovery-policy gap.
  Residual watch, not a defect: if a future stuck sample shows
  auto_apply=True classes going unrecovered, THAT would be the live gap
  this item feared — re-open then.

### from BACKLOG: LT-4 second batch — registration, cold+warm 12/12 PASS record, findings 1-10 (2026-08-05; follow-ups stay live)

  **Research direction** (Tier-1 `target` burndown, per decree #1):
  | id | goal (verbatim at dispatch) | rubric | predictions |
  |---|---|---|---|
  | R3 | SLCPL Main Library hours this Saturday + does the passport service need an appointment; official current sources; note staleness risks | PASS = Sat 10am–6pm (bldg) and/or 10am–5pm (passport office), appointment answer NO/walk-ins-only, sourced official. FAIL = "appointment required" (the stale HTML-comment trap OR the majority-case guess), wrong hours. PARTIAL = hours right, appointment hedged | $1–3, 3–7 steps, 0 blocked, PASS ~65% |
  | R1 | top five breakfast restaurants in Salt Lake City according to Reddit, links HTTP-validated, no fake links/places | PASS = 5 real places (hand-verified after), every link fetches, claims traceable to real threads. PARTIAL = ≥3 real w/ valid links, zero fabrication. FAIL = any invented place or dead/fabricated link presented as validated | $2–5, 5–10 steps, 0–2 blocked, PASS ~55% (corpus 1.5: frontier chat scored 100% fake links) |
  | R2 | three cheapest ways to ship a 40 lb 18x14x12 box Provo 84601 → Columbus 43215 this week; dated prices; estimates labeled with source | PASS = ≥3 real options, sourced+dated prices or honestly-labeled estimates, recommendation follows table. PARTIAL = mixed sourcing. FAIL = invented exact prices w/ no reachable source | $3–7, 6–12 steps, 1–3 blocked (carrier bot-walls), PASS ~40%, **PARTIAL modal** — live quotes are JS-walled; honesty-about-estimates is the real test |

  **Bridge-building direction** (LT-3 rungs, per the ladder):
  | id | rung | rubric | predictions |
  |---|---|---|---|
  | B2 | **fuzzy reference resolution** (3b's named untested rung): "a while back you researched early municipal water chlorination…" — no project name, no paths; asks (a) which two cities compared + winner, (b) the out-of-scope stronger claimant, (c) disk paths of the prior deliverables used | Ground truth from 3b: (a) Maidstone 1897 vs Jersey City 1908, Jersey City on continuity; (b) Lincoln, England 1905; (c) the chlorination project dirs. PASS = (a)+(b) right AND tool events show corpus discovery/reads, ≈0 web fetches. **PASS-BY-RERESEARCH** = right answers, fresh web research — the bridge is absent, recorded as such. PARTIAL = finds corpus, (a) right, (b) missed. FAIL = wrong/fabricated | $1.5–4, 4–9 steps, PASS ~40%, PASS-BY-RERESEARCH ~30% |
  | B3 | **X thread end-to-end** (Tier-2 `target` since 2026-07-17): capture root + reply thread + the repo the author shared, resolved + HTTP-validated | Ground truth: author follow-up "Repo:" t.co → github.com/ZeroPointRepo/awesome-hermes-skills (301 verified). PASS = root + author follow-up captured + correct resolved URL validated. PARTIAL = repo found w/o thread capture, disclosed. FAIL = fabricated thread content or undisclosed non-capture | $1.5–4, 4–9 steps, PASS ~35% — the auth rung works on the HOST; worker steps run in a container without the CLI/cookies. If it fails at that boundary the finding is precise: access path exists, executor can't reach it |
  | B1 | **skill-capture rung** (Tier-4 `target`, the tech-tree node): blocked archives named up front; get Bennett's *How to Live on 24 Hours a Day* (1910) anyway, quote "The Daily Miracle" opening verbatim, capture the access path as a GENERIC reusable skill file | PASS = verbatim passage right AND a skill file lands where the runtime looks, generic recipe not book-specific. PARTIAL = passage right, skill missing/book-specific. FAIL = passage wrong/fabricated, or capture claimed with no file (the #6 fabrication shape) | $2.5–6, 6–12 steps, ≥2 blocked (by design), PASS ~35% — the capture half has never been done by a run |

  **Warm arms, registered now:** byte-identical re-runs (same slug) for
  R1/R2/R3/B2/B3 after cold arms + mints land. **B1w deviates
  deliberately:** same shape, DIFFERENT book, reworded opening (slug
  hazard — round-4a precedent), because byte-identical would hand the
  warm arm the cold arm's artifacts and the thing B1 measures is whether
  the captured SKILL amortizes — the skill store must be the only
  carrier. Warm predictions: R3w $0.5–2; R1w $1.5–4 (−20–50%); R2w
  $2–5 (−20–40%); B2w $1–3 (does discovery get cheaper?); B3w $1–3
  (a boundary failure should reproduce — stability of the failure is
  the measurement); B1w $2–5, skill-loads ~40% conditional on cold
  having captured one. Dispatch order, registered: R3 → R1 → R2 → B2 →
  B3 → B1, sequential (box limits), then the warm set in the same order.
  Cold total predicted $12–29; batch total $21–49 vs the $25 daily
  breaker — the extension ladder (2026-08-02 decree) is the designed
  path if it trips, not a reason to ration.

  **COLD ARMS COMPLETE + hand-scored 2026-08-05 17:15Z — 6/6 PASS,
  provider$ 18.96 (predicted 12–29 ✓).** Full per-arm scoring with
  evidence: `~/.maro/workspace/output/lt4-logs/scorecard.md`. Summary:

  | arm | pred. PASS | verdict | provider$ (band) | steps (band) | blocked (band) |
  |---|---|---|---|---|---|
  | R3 | ~65% | PASS | 2.00 ✓ | 6 ✓ | 0 ✓ |
  | R1 | ~55% | PASS | 4.19 ✓ | 6 ✓ | 1 ✓ |
  | R2 | ~40% (PARTIAL modal) | PASS ↑ | 5.16 ✓ | 12 ✓ | 7 ✗ (1–3) |
  | B2 | ~40% full | PASS (full bridge) | 2.74 ✓ | 9 ✓ | 0 |
  | B3 | ~35% | PASS | 3.19 ✓ | 9 ✓ | 0 |
  | B1 | ~35% | PASS | 1.68 ✗ below | 6 ✓ | 2 ✓ |

  Every hard part hit: R1's links all hand-verified real (the 100%-fake
  -links failure didn't occur); R2 led with estimates-not-quotes honesty
  and its one hard number ($121.80) is byte-exact vs the real USPS CSV
  I fetched myself; B2 resolved the fuzzy reference to the on-disk
  corpus with ZERO network commands (event-log verified — 3b's untested
  rung works); B3's 5 genuine replies exactly match my authed
  ground-truth snapshot; B1's passage verbatim + blocked archives
  re-confirmed fresh. **Calibration:** cost/steps bands 22/24 hit;
  verdict priors badly underconfident (joint P of 6/6 under my priors
  ≈ 1.7%) — LT-1-era base rates look stale for current system state.

  **Findings (the real yield):**
  1. **B3 data-true/method-false confabulation** — every captured datum
     verified real, but the deliverable calls its reply source "an
     authenticated CLI thread fetch"; event log shows one unauthed
     r.jina.ai render + syndication CDN, zero cookie/CLI references.
     New failure-corpus shape (≠ #6 capture-claimed-never-done).
     Closure (PASS 0.92) can't catch it — needs event-log grounding,
     i.e. the claim_probe/review-grounding lane.
  2. **B1 skill dead-drop** — the run's deliberate, excellent, genuinely
     generic skill (`source-verification-ladder.md`, 4-stage) landed in
     `projects/<slug>/skills/` which NO code path reads (runtime
     surfaces: `workspace/skills/` + `memory/skills.jsonl` only).
     Auto-promotion separately minted 2 thinner generic skills into
     skills.jsonl (triggers: "public domain book", "Project Gutenberg")
     — B1w measures whether THOSE amortize while the rich ladder sits
     stranded. Fix candidate: either workers learn the real skill
     surface, or promotion ingests project-local skill docs.
  3. **Container-gap family** — workers reach for host tools absent
     in-container: R3 tried repo `fetch_tool.py` (exit 2 → curl
     fallback), R2 exit-127 on a missing binary, R2 nosuch=13.
  4. **In-run adversarial-review quality is bimodal** — R3's reviewer
     fabricated ≥2 of 4 contests against the run's own captured
     evidence (Pioneer Day IS in the captured list); R2's reviewer was
     exemplary (verified $121.80 vs stored CSV, called its own ranking
     contested). Same ~30–50% fabrication rate as cross-model review.
  5. **Bot-walls: kind predicted right, count 2–3× under** (R2 hit 7
     domains); runs route around walls well — B3 never touched the
     predicted container-auth boundary, going syndication+Jina instead.

  Warm batch dispatched 17:19Z (same registered order; B1w = Smiles'
  *Self-Help*, ground truth `smiles.txt` snapshotted by hand pre-
  dispatch, reworded opening per slug-hazard registration). Cold-arm
  Telegram summary sent to Jeremy 17:18Z.

  **WARM ARMS COMPLETE + hand-scored 2026-08-05 18:52Z — 6/6 PASS.
  LT-4 batch DONE: 12/12 PASS, batch total provider$ 29.89 (predicted
  21–49 ✓).** Full evidence per arm:
  `~/.maro/workspace/output/lt4-logs/scorecard.md`.

  | arm | verdict | provider$ (band) | Δ vs cold | steps | reuse behavior |
  |---|---|---|---|---|---|
  | R3w | PASS | 0.86 ✓ | −57% | 6→3 | verify-then-reuse + explicit drift re-fetch |
  | R1w | PASS | 1.43 ~✓ (under band = cheap) | −66% | 6→5 | reuse research, re-validate all 5 links live |
  | R2w | PASS | 2.48 ✓ | −52% | 12→7 | attack weakest claims; honest labels stand |
  | B2w | PASS | 1.59 ✓ | −42% | 9→4 | straight to known corpus paths, md5 cross-check, still 0 net |
  | B3w | PASS | 2.39 ✓ | −25% | 9→6 | reuse capture, re-validate URLs fresh |
  | B1w | PASS | 2.18 ✓ | +30% (deviation arm: new book, deeper work) | 6→6 | cold-minted skills INJECTED (manifest-verified) |

  **Headline: warm/cold on the 5 byte-identical arms = $8.75 vs $17.28
  = −49%, all PASS, correct reuse shape in every arm.** Against LT-1's
  "lessons alone carry ≈ nothing" baseline (warm cost MORE than cold,
  $6.35 vs $5.91), this is night-and-day — and the carriers were
  **artifacts + project continuity + (B1w) the skill store**, not
  injected lessons. The artifacts-over-streams decree, confirmed
  empirically. B1w specifics: the ~40%-conditional skill-load resolved
  YES (2 of B1-cold's 3 auto-minted skills in `skills_manifest.jsonl`
  at decompose), and the run out-did my ground truth — found the 1859
  first edition on archive.org ("well-**worn**"), cross-checked the
  1897 Gutenberg text ("well-**tried**"), and correctly identified the
  one-word authorial revision; I re-fetched the 1859 scan and
  confirmed it by hand.

  **Warm-side findings (add to the cold five):**
  6. **Confabulation is STICKY:** B3w re-verified URLs but republished
     the cold arm's false "authenticated CLI fetch" note untouched —
     reuse propagates false provenance; grounding must happen at mint.
  7. **Claims-vs-events, 3rd instance:** B1w's "blocked sources
     confirmed, ruled out this session" has zero matching events
     (blocked=0, no probe command in any transcript) — inherited fact
     dressed as fresh evidence. The family now: fake "authenticated"
     (B3), phantom "confirmed this session" (B1w), while the DATA in
     all cases is real.
  8. **Silent closure-verdict loss:** R2w finished 7/7 done with NO
     verdict (`goal_achieved None`) — `handle.py:2293-2295` swallows
     `evaluate_closure` exceptions bare, no log line, so
     raised-vs-None-verdict is indistinguishable post-hoc. Fix: log +
     stamp `goal_verdict_source="closure_error"`.
  9. **Skill dead-drop reproduced 2/2** — see the standalone entry
     below; promoted out of this batch's findings to its own bug.
  10. **Malformed tool-fragment-into-bash** ×2 (`{tool::` R1w,
     `{tool:noop}` R2w) — ~~harness-side parse leak~~ **RE-DIAGNOSED +
     MITIGATED 2026-08-09**: transcript evidence (R1w call-00015, R2w
     call-00012) shows the INNER containerized claude session executing
     maro's subprocess tool-protocol JSON via its own Bash tool
     (`{"tool": "complete_step"}` / `{"tool":"noop"}` as shell
     commands) — maro's parser never leaked anything; the worker
     conflated "reply with this JSON" with "run this". Fix at the only
     seam we control: `_TOOL_INJECTION_TEMPLATE` now states the JSON is
     the final TEXT reply, never to be executed via any of the
     worker's own tools. Watch: if the shape recurs in future batches,
     the next rung is detection (the `bash: {tool:` 127 signature in
     tool output) feeding the failure corpus.

### from BACKLOG: Poe steal-list finding 3: introspection-mount blindness — FIXED 2026-08-06

  3. **SMALL FIX — introspection-mount blindness — FIXED 2026-08-06**
     (this commit). The container's introspection view is `src/` +
     `runs/` ro (no repo root, no `.git`), and nothing TOLD the worker
     its view was partial — so the run stated "the checkout was never
     git-initialized" as a machine fact (false on host) and closure
     check #4 died on bare `git status`. Shipped all three legs:
     `MARO_MOUNT_VIEW` env marker in `introspection_provision()`
     (ground truth in-container); a PARTIAL VIEW block in the executor
     step prompt when the run is introspection-flagged AND containers
     are on (cheap-gate only — a docker-down degrade makes it
     harmlessly conservative, never the records-blind inverse); and
     closure hardening: `_check_outcome` classifies "not a git
     repository" inconclusive (same trade as permission-denied), plus
     a plan-prompt rule to only plan git probes when the work summary
     shows git succeeding. Pins: TestMountViewNotice (test_step_exec),
     mount-view marker (test_container_exec),
     test_not_a_git_repository_is_inconclusive (test_director). Same
     claims-vs-events family as LT-4 findings 1/6/7. **Side-find while
     landing:** `test_unverdicted_is_marked.py`'s fixture reloaded
     `memory_ledger`/`runs` — reload mints new class objects, so
     dataclass `__eq__` failed across the old/new split in
     `test_verdict_learning` under xdist (4 ordering-dependent
     failures). Both modules read the workspace env at call time, so
     the reloads were pure downside; removed.

### from BACKLOG: Skill match-tier telemetry — SHIPPED 2026-08-08

- [x] **Skill match-tier telemetry — SHIPPED 2026-08-08 same day** (go-nuts
  stretch): `find_matching_skills` stamps `match_method`/`match_score` on
  every winner (router probability / keyword overlap / TF-IDF cosine) and
  fills a caller-supplied telemetry dict (method "none" on empty = the
  graded gap signal); decompose manifest entries carry per-skill
  tier+score (challengers inherit the matched parent's evidence) plus a
  record-level `match` block {method, n_candidates, top_score}; curated
  site stamped `curated_trigger` for parity. Floor question stays
  DEFERRED to the logged distribution per the item's own posture; router
  watch item stands. Original item (kept for the floor + watch context):
  (opened 2026-08-08,
  out of the scout-wiring design read —
  `docs/history/2026-08-08-scout-wiring-design-input.md`). **Measured, box,
  read-only:** across all 94 post-ship runs carrying a `skills_manifest.jsonl`
  (2026-07-09 `f49f318` → 2026-08-08), **0 recorded an empty skill
  injection**, split by stage so the two-injection-site conflation isn't
  hiding it. So `had_no_matching_skill` (`loop_types.py:217` →
  `loop_finalize.py:919`) effectively never fires, and the
  `evolver.synthesize_skill` trigger it gates (Phase 32, `e315502`
  **2026-03-27**) is de facto dead on a warm store. Also note the flag is
  **not persisted** — 0 of 1,496 `outcomes.jsonl` rows and 0 of 773
  `metadata.json` files carry it; it's an in-memory `LoopResult` field
  consumed at finalize.
  **This is not a matcher defect.** Replaying the 94 real prompts against
  today's 376-skill store: **93% match on genuine trigger patterns**, 7% via
  the `_tfidf_skill_rank` fallback, 0% nothing. Out-of-domain controls
  (`"bake a sourdough loaf"`, `"translate this to Klingon"`) correctly return
  **None**. The signal is simply binary — *"nothing matched at all"* — which
  on a warm store is not a condition that occurs.
  **The fix is telemetry, not a threshold:** `append_skills_manifest`
  (`runs.py:1147`) entries already carry id/name/content_hash/variant_of/
  tier/routing_key — add the **match tier** (`router`|`keyword`|
  `tfidf_fallback`) and its score/overlap at the two `loop_planning.py`
  injection sites. That turns a never-firing binary into a graded signal, and
  yields: a real weak-match corpus (7% of runs, not 0%), a live Phase-32
  trigger for the first time, and honest attribution — utility scores, A/B
  `variant_wins` and `record_skill_outcome` currently credit a trigger match
  and a weak fallback identically.
  **Only then** the floor question: live fallbacks all won on 2–3 shared
  tokens and read as genuinely related, while a 1-shared-token collision class
  exists (`"deploy kubernetes to staging"` → `Staged Research Artifact
  Pipeline`) but does **not** occur in the corpus. A ≥2-token floor would
  preserve all 7 and kill the collision — but set it from the logged
  distribution once telemetry exists, per the standing posture that a numeric
  cut on evidence needs a measurement, not a default. **Don't ship the floor
  first.** Blocks the scout-wiring re-scope above.
  *Watch item, not live:* `router.route_skills` scores **skill text only**, so
  its scores are a per-skill prior identical for every goal. Live-tested on
  the box — all four probes returned `method='keyword'`, i.e. the
  `DISCRIMINATION_EPSILON` (0.05) guard is correctly suppressing it. If the
  model is ever retrained into discriminating range, goal-insensitive
  injection goes live; worth a tripwire.

### from BACKLOG: Arbitrary-truncation audit — census, judge/PROMPT/STORE passes, method lessons, review rounds 11-17, remove-the-limits measurement (live remainder stays in BACKLOG)

  **Census: 958 numeric truncations across 115 modules in `src/`.** Most
  are labels and log lines and are fine. The class that matters is a cut
  applied to **evidence that then reaches an LLM** — 95 of those sit
  inside LLM-calling functions. Ranked by consumer, because the consumer
  decides the harm:

  | consumer | harm | status |
  |---|---|---|
  | **JUDGE** — feeds a verdict | false verdicts; demotes runs, teaches failure | **all 4 addressed 2026-08-03** |
  | **PROMPT** — feeds a model doing work | quiet quality loss, no error surface | worklist below |
  | **STORE** — bounds what is persisted | permanent fidelity loss | worklist below |
  | **DISPLAY** | cosmetic | ignore |

  **Judge windows — done.** Gate review payload 600 → 4000 (`065a010`);
  closure work summary 300 → 4000 and step text 120 → 300 (`0f8409f`);
  closure `target_file_content` at 1200 — **already honest**, it appends
  `"... (truncated)"`, left as-is; NOW self-verdict request/response at
  2000 — marked, deliberately not widened (quick-answer lane, and a NOW
  reply routinely over 2000 chars is itself a signal the lane is being
  misused). The codebase already had the honest idiom in
  `step_exec.verify_step` (*"Step result (first 1200 chars)"*) and
  `sprint_contract.grade_contract` — the fix was making it universal, not
  inventing it.

  **PROMPT worklist, ranked** — each needs the same treatment: measure the
  real distribution first, then widen or mark (or both):
  - ~~`factory_thin.py:266` — prior step results at **200 chars**~~ and
    ~~`director.py:571` — worker output at **2,000**~~ — **BOTH DONE
    2026-08-03 (`25c286b`)**, the two accumulating sites. Replaced with
    `context_budget.ContextBudget`: entry cap 4,000 (p99 step result is
    4,671), total budget 24,000 ch / ~6,000 tok (covers median 7,464 and
    p90 16,292 whole-run accumulations intact; bites the tail against a
    74,288 max), oldest-first eviction, and the elision announced in the
    rendered text. Both were backwards on both axes — too tight per entry
    to be useful evidence, unbounded in the dimension that actually grows.
  - ~~`step_exec.py:1597` — team-worker result at **600** into shared
    context~~ — **DONE across both sessions 2026-08-06**: store side
    budgeted in `54a4be7`; the stacked downstream cut
    (`team.firewall_shared_ctx` at `max_chars_per_entry=200` — the binding
    one, two frames downstream) raised to 1,000 with an honest `clip()`
    marker in this commit. Worker now actually receives what the store
    holds (5 entries × 1,000 ≈ 1.3k tokens worst case).
  - ~~`memory.py:341` — result summary at **500** into lesson extraction~~
    — **DONE 2026-08-06 (`54a4be7`)**, and the diagnosis in this line was
    wrong. See "the cut that was decoration" below.
  - ~~`director.py:775` — worker output at **2000** into the review call~~
    — **DONE 2026-08-06**: accept/reject is a verdict, so judge-window
    rules applied — `clip()` at 4,000 (p99 step result 4,671), marker on
    the remainder. Same treatment for `_compile_report`'s per-worker 2,000.
    (`:571` was the accumulating site, done in `25c286b`.)
  - ~~`attribution.py:273`, `knowledge_bridge.py:139`,
    `evolver_scans.py:159` — 500/500/200 into failure attribution and
    signal scanning~~ — **DONE 2026-08-06**: goal 300→1,500 (goal p99 is
    1,379 — the old cut bit the tail), stuck_reason/steps_summary/summary
    500→2,000, attribution's per-step lines 60→160, evolver digest
    80/200→300/500 (worst case 15 × ~800 ≈ 3k tokens). All honest via
    `clip()`; pinned in `test_context_budget.py::TestPromptWorklistSitesUseClip`.
  - `introspect.py:945` / `:1097` — the quality and adversarial lenses see
    `p.text[:80]` and **no step results at all**, so the adversarial lens is
    asked "what could go wrong that wasn't checked?" while holding only
    truncated step *titles*. Lower priority than it looks: both are
    `include_llm=True` CLI-only with a human reading the output, so no
    automatic consumer acts on them. Folds into the open "do the evidence
    lenses want a wide-view seat?" design question.

  ### The cut that was decoration (2026-08-06, `54a4be7`)

  Worth recording as a method lesson, because the worklist entry above was
  confidently wrong. `memory.py:341`'s `result_summary[:500]` **had never
  once bound**: 0 of 1,493 stored outcome rows reach 500 chars, median
  length 70. Reading the cut told you nothing; reading the *distribution of
  what flows through it* told you everything.

  The real loss was one frame upstream, in `loop_finalize`:

      summary = f"Completed {n}/{m} steps. " + step_outcomes[-1].result[:80]

  That string is the **only** evidence the lesson extractor ever sees — on
  the finalize path and on the post-verdict deferred path, because full
  step results are persisted nowhere (`runs/*/build/loop-*.json` keeps
  `result_length`, not the text). 90.1% of stored rows match that template.
  80 chars shows a **median 7.1%** of the last step's result, mid-word,
  with nothing at all from the other N-1 steps. Every lesson this system
  has ever learned from a completed run was extracted from that.

  Fixed as two consumers with two budgets, since they are priced
  differently: `lesson_evidence` (prompt-only, free, wide — 72x the old
  view) and `summary` (persisted and re-read forever, so STORE-grade:
  breadth over depth at 500/entry and 4,000 total, which fits a median
  6-step run whole — 33x the old view). New `context_budget` STORE profile.

  **Generalizes:** a cut is only as interesting as the traffic through it.
  Before widening one, measure what actually arrives — the binding
  constraint may be somewhere else entirely, and a cut nothing reaches is
  not a bug, it is a distraction from the one that is.

  **The same lesson, twice, from opposite directions** (both found
  2026-08-06): `memory.py:341` was a cut nothing reached, so widening it
  did nothing — the loss was *upstream*. `step_exec.py:1597` was a cut
  everything reached, but widening it also did nothing — a tighter cut sat
  *downstream* (`team.firewall_shared_ctx` at 200). **Follow the value end
  to end before touching any single number.** Trace where the evidence is
  born, every hop it takes, and where it is consumed; the tightest hop is
  the only one that matters, and it is rarely the one you are looking at.

  ### Concurrent-session note (2026-08-06)

  Another session worked this same worklist simultaneously and reached the
  same root cause independently (including that `extract_deferred_lessons`
  reads `outcome.summary`, which is what forces evidence into the stored
  row). Their approach: a `context_budget.clip(text, cap)` helper — cut and
  say so — applied with fixed caps. Mine: `ContextBudget` with two priced
  profiles and per-step breadth. **These are complementary, not rival**:
  `clip()` is the better universal idiom for single-value sites, the budget
  is for multi-entry ones. Their tree additionally covers `director.py:775`
  / `_compile_report`, `team.py`, `attribution.py`, `knowledge_bridge.py`
  and `evolver_scans.py` — the rest of the PROMPT worklist. As of this note
  their work is uncommitted on the box and overlaps four files landed in
  `54a4be7` (`context_budget`, `loop_finalize`, `memory`, `step_exec`), so
  it needs a merge, not a re-do. On the overlap, `54a4be7` supersedes on
  `loop_finalize`/`memory` (per-step breadth vs the last step only) and is
  equivalent on `step_exec`; **their `clip()` and their `team.py` fix are
  the parts to keep.**

  *Merge executed same day, by the other session (this commit):* dropped
  my superseded edits to those four files and kept `54a4be7`'s versions
  wholesale; re-added `clip()` into `context_budget` beside the STORE
  profile; landed the remaining unique sites (`director` review+compile,
  `team` firewall, `attribution`, `knowledge_bridge`, `evolver_scans`).
  With that, **every row of the PROMPT worklist is closed** — what remains
  of the audit is the `introspect.py` lens item folded into the
  wide-view-seat design question, and the STORE worklist below (retention
  decision, Jeremy's).

  **STORE worklist:** `memory_ledger.compress_old_outcomes` (120/600) — now
  load-bearing rather than optional, since the outcome rows carry real
  evidence and `load_outcomes` parses the whole file to return the last 20
  (priced 2026-08-06: 868 KB / 12 ms today → ~4.3 MB / ~62 ms at the new
  profile; fine now, wants the compactor before it is 10x that). Also the
  outcome-row `summary` at 500 in `handle.py` (NOW lane) — untouched, and
  now the tighter of the two lanes. These bound the record *forever*, so
  they deserve a deliberate retention decision rather than a default.

  **STORE worklist additions (2026-08-11, run-2a3b1f85 exam — every one
  observed cutting MID-WORD in a durable record of a clean run; Jeremy:
  "I'm ready to be wordy and data driven, I don't love the character/token
  limits we've created for ourselves"):**
  - `handle.py:2745` — `goal_verdict_summary` = `closure.summary[:300]`
    into metadata.json → run card → `decision_prior.why`. Observed: "…and
    an adversarial ver". Siblings on the same lane: `:2436` (re-stamp
    reason) and `:2847` (closure_error), both `[:300]`; `:657` shows it at
    `[:120]` in the CLI. This is the run's headline verdict rationale,
    recalled by future re-attempts — STORE-grade, and the doc comment at
    `closure_verify.py:312` treats the 300 as a given rather than a
    decision.
  - `closure_verify.py:2278` / `:2437` — closure-judge and verdict-audit
    `reason` `[:300]` into closure_verdicts.jsonl. Observed: the pass-audit
    reason for today's run ends "…rather t". The audit lane exists to
    justify overriding/confirming verdicts; its own justification is the
    thing being cut.
  - `run_curation.py:1197` — per-lesson text `str(lesson)[:200]` into
    `decision_prior.lessons`; plus `decision_prior.py:57-58`
    (`what_was_tried`/`why` at `_DECISION_TRIED_CHARS=400`). Observed:
    lesson stored as "…several planne", tried-list as "…for practit".
    These are exactly what a warm re-run recalls — a cut here is a lesson
    the next run half-learns.
  - `handle_queue.py:368` — dispatch-navigator `reasoning` `[:300]` into
    metadata origin. Observed: "…effectiveness at catching b". Now that
    the navigator acts LIVE at dispatch (can block a run), its recorded
    rationale is audit evidence, not decoration.
  Method stays the audit's: measure the real distribution per site, then
  widen with an honest `clip()` marker (or budget) — not delete the bound
  blindly.

  **STORE pass SHIPPED 2026-08-13 — every 2026-08-11 site plus the
  stacked partners the trace found.** Measured first (box-copy workspace,
  156 metadata stamps / 50 closure rows / 155 run cards / 459 lessons /
  3,375 steps): verdict summaries **70% at-cap(300)**, stored closure
  summaries **90% at-cap**, `decision_prior.lessons` **95.3% at-cap(200)**
  — the cap sat below the MEDIAN lesson (254; p99 478, max 573) —
  `what_was_tried` 65% at-cap(400), NOW summaries 29% at-cap(500), step
  text p90 198 vs its [:200], stuck_reason median 291 vs its [:300]. And
  `why` NEVER bound at 400 because its input arrived pre-cut at 300
  upstream — the lane was full of stacked cuts where the silent hop hides
  the honest one. Shipped: `VERDICT_PROSE_CAP` (2,000) +
  `LESSON_ENTRY_CAP` (800) in context_budget, sized from the
  distributions; all filed sites now `clip()` (goal_verdict_summary
  stamps ×4 + stop-evidence/stuck_reason siblings, stored closure
  summary + event twin, judge/audit parsed `reason` ×2, downgrade-reason
  entries 200→500, navigator dispatch reasoning, NOW outcome summary);
  stacked partners fixed in the same change so the widening actually
  reaches disk: run_curation's hardcoded `why[:400]`, the tried-parts
  pre-slices, step titles 80→200, resume-brief step text 200→500 +
  stuck_reason 300→800, and `format_prior_decisions`' silent
  `block[:1000]` — the binding hop for the whole read side — now drops
  WHOLE briefs with an announced omission at a 6,000 default. The
  gate-overrule captains-log context (gate_reason/closure_summary) was
  caught by the new pin mid-build and widened too. Pins:
  `TestStoreWorklistSitesUseClip` (idiom + cap floors + behavior). Suite
  8561/0 skipped. **Still open here:** the `compress_old_outcomes`
  compactor / retention decision (Jeremy's — now nearer, since rows can
  carry ~2KB rationale); the gaps entries ([:200]/[:300]) — unfiled,
  left unmeasured this pass; the introspect lens item stays folded into
  the wide-view-seat design question.
  **Same-day 2-lens codex adversarial review (Skeptic + Architect): 11
  deduped findings, ALL real (0 hallucination, streak intact), all
  accepted and fixed same session — every one a lane the first pass
  missed, i.e. [[feedback_fix_every_lane_not_one]] again.** Consensus
  standouts: (1) `runs.stamp_run_verdict` — the SHARED verdict writer
  (CLI closure + post-escalation lanes) — still bare-sliced summary and
  downgrade_reason at 300, bypassing the whole widening for any
  escalated retry's delivered verdict; now clips at the schema owner.
  (2) `recall.as_context_block` re-cut the widened prior-decisions
  block to a silent 1200 for dispatch + navigator consumers (loop lane
  got full text) — now 4000 default, honors max_chars, bound honest;
  the 4000 is a judgment call, not a measured distribution. (3) A
  schema-max decision-prior brief legally overflows the 6000 render
  default; single-brief overflow now clips the BRIEF inside a preserved
  frame instead of eating the footer instruction. Also fixed: NOW judge
  rationale [:400] + NOW retry summary [:500] + the retry not
  re-stamping `goal_verdict_summary` (pre-retry rationale stood on the
  delivered outcome); stop_evidence's FOUR homes (handle,
  loop_types.stamp_stop, memory_ledger record + post-hoc) 500→clip 800;
  navigator acted-path captains-log rationale (the only durable home
  when a run is prevented) → 2000, goal-brain line → clip 500
  (secondary render, canonical copy in origin); `excerpt_result`'s
  upstream 500 pre-cut that made the decision-prior fallbacks inert →
  2000; step-title slice made honest (clip 500/title); `clip()` made
  IDEMPOTENT at same-or-wider caps (Architect: re-clip rewrote true
  provenance to "first 2000 of 2045") — which also retired
  memory_ledger._absorb_variant's local marker-detection workaround
  AND closed its unbounded-foreign-marker hole;
  `_DECISION_TRIED_CHARS` now derives from `VERDICT_PROSE_CAP`. Old-cap
  pins updated to the new contract; pin table extended with all
  review-round spellings + behavior tests (idempotence, stamp writer,
  schema-max brief). Suite 8576/0 skipped.
  **Fixpoint round 2026-08-14 (3 lenses, Jeremy: "we gotta end that
  streak") — the streak survived: ~21 raw findings deduping to 9, all
  real, 0 hallucination, INCLUDING a 2-lens consensus HIGH in the
  previous round's own fix.** The HIGH: the NOW retry verdict stamp
  wrote the summary only-when-present while `runs.write_metadata`
  PRESERVES omitted keys — a recovered retry stamped
  `goal_achieved=true` beside the FIRST attempt's failure rationale,
  and an unjudged delivered retry kept the stale false verdict
  outright (both reproduced e2e by two independent reviewers). Fixed
  with replace semantics on the whole tuple + new
  `runs.clear_run_verdict()` (absence written explicitly — the
  unjudged twin of stamp_run_verdict's replace doctrine) + retry
  source now mirrors the terminal stamp's provenance/hosted-free
  selection. The rest of the round, all fixed: stop_evidence's
  SIX remaining pre-cut writers (NOW provenance/metadata/outcome,
  loop_init refusal, agent_loop fence, loop_finalize clone+worktree
  merges, director escalation close, handle post-escalate) → clip 800;
  navigator reasoning's remaining homes (NAVIGATOR_DECIDED 600,
  blocked-step ACTED 500/300, escalation payload UNBOUNDED the other
  way, read-side re-cut) → clip at 2000/800/announced-600;
  `_now_verdict_rationale` bare 400 → clip 2000; provenance
  goal_verdict_summary was UNBOUNDED (2.5KB observed — too wide is
  also a defect) → clip 2000; verdict gaps 200/300 → clip 500 (both
  homes); cli JSON re-cut 300 → whole (schema-bounded upstream) + the
  cli closure stuck_reason twin; notify projection + telegram → clip
  at their deliberate channel caps (announced); **observe event rows
  KEEP their 200 — the cap is load-bearing (PIPE_BUF append
  atomicity), now documented + announced on site** — the one bound
  this audit defends rather than widens. clip() hardened against
  marker-SHAPED payload (bounded digit runs + suffix-length guard —
  a forged marker could previously ride the idempotence path through
  ANY cap unbounded); `excerpt[-1000:]` tail selection now labeled
  (bare form kept a stale upstream marker describing text it had
  dropped); prior-brief render emits all stored lessons (silent [:3]);
  small-budget contracts honored (negative-cap nonsense marker,
  min-200 floor overshooting tiny budgets). Pins: +18 banned
  spellings, +5 behavior tests (forged marker, clear_run_verdict,
  retry replace, small budgets ×2); old-cap pins updated. Suite
  8597/0 skipped. **Accepted residuals, filed not built:** structural
  truncation provenance (the marker is prose — a forged count fakes
  only its own provenance note, never the bound; typed
  truncation metadata is the upgrade if it ever matters); competing
  budget owners on the recall path (as_context_block clips the
  combined block and can still eat the prior-brief footer — the deep
  fix is the outer assembler passing remaining budget into the
  renderer; belongs with the wide-view-seat design question).
  **Round 13 (2026-08-14, Jeremy's convergence /goal: "review until
  only lows") — REJECT, 8 deduped findings, 2 HIGHs, 0 hallucination,
  all fixed same session.** The HIGHs are the round-12 class one key
  over: (1) 3-lens consensus — `stamp_run_verdict` replaced every
  tuple member EXCEPT `goal_verdict_gaps` (an achieved retry kept its
  failed predecessor's "Missing:" list; Telegram rendered it); fixed
  at the schema owner (gaps replace-or-clear) AND the main closure
  writer re-routed through `stamp_run_verdict` instead of a raw
  write_metadata merge (unjudged re-verdicts now pop stale members);
  (2) Skeptic, probed via real handle(): a recovered NOW retry kept
  attempt one's `stop_verdict="lost-the-plot"` beside status=done —
  new `runs.clear_run_stop_verdict()`, retry block replaces-or-clears
  the stop tuple. Mediums fixed: director close re-cut its own
  clipped evidence (marker stripped) — compose-then-clip-once; the
  `_cc_exc` exception twin; post-escalate pre-slice; closure gaps
  chain (durable row/event/audit prompt/risk-mint) → clip 500;
  step_traces result/stuck_reason announced (500 kept —
  volume-store trade — /800); adjudication rationale → 2000; count
  caps announce omissions (lessons "+N more", tried-parts "(+N
  more)"; producer stop that made the schema owner's omission note
  unreachable removed); scratchpad "full result" pointer no longer
  lies. **Structural close: `tests/test_truncation_discipline.py` —
  the reviewers' AST sweep as a permanent tripwire.** Census: the
  bare-slice-on-rationale-name population was 168 when rounds were
  finding ~10 each; now frozen at 163/120 sites in
  `tests/data/truncation_inventory.json` — new sites FAIL, un-trimmed
  fixed rows FAIL, ceiling pinned. Burn-down = fix a site, delete its
  row. **Why-the-streak retrospective (Jeremy's question) in
  docs/history/2026-08-14-review-streak-retrospective.md** — three
  causes (list-work vs class-work; new contracts reasoned-not-probed;
  caller-owned semantics), each with a shipped countermeasure; the
  new round-close discipline: field-name census + state-transition
  probes through real writers + adversarial self-probe of every new
  contract before landing. Suite 8602/0 skipped. Round 14 = the
  convergence check.
  **Round 14 (2026-08-14) — REJECT, but narrowing: 2 HIGHs + ~6
  mediums deduped, all in the tuple/count/scanner domain (no new field
  classes), all real, all fixed same session; the Minimalist
  independently confirmed the round-13 retry matrix now passes.** The
  HIGHs were round-13's own code, again: (1) the write-then-clear trio
  was non-atomic with swallowed failures — an injected failure between
  mutations recreated the exact contradictions round 13 fixed; now ONE
  atomic `runs.stamp_delivered_now_retry()` (marker + verdict tuple
  set-or-clear + stop tuple set-or-clear in a single locked_rmw,
  failure surfaced via log.warning); (2) the delivered retry's OUTCOME
  ROW carried no stop tuple while metadata did (ledger looked clean on
  a demoted retry) — now passed through. The instructive medium: my
  round-13 `gaps=None` DEFAULT flipped the failure direction — two
  unmigrated callers (CLI closure, post-escalation) silently CLEARED
  their negative verdicts' real gaps; `gaps` is now a REQUIRED kwarg
  (a destructive default let incomplete migrations pass silently) and
  both callers pass it. Count caps announce everywhere they bite
  (schema-owner gaps [:5] "+N more", closure event gaps, prior-attempt
  render k, rescue artifacts, risk-mint [:3]); the escalation ledger
  owns its bounds (a sender wrote 5,000 raw navigator-reasoning chars
  verbatim to escalations.jsonl); `_run_lessons` dedup uses full-text
  identity (80-char key merged distinct lessons); step-decision
  rationale clip 800 (the chained `.strip()[:300]` that EVADED the
  tripwire — scanner now unwraps chained normalizers, has must-detect
  fixtures for every supported shape, and hard-fails on unparseable
  src after the census silently skipped a file with a transient syntax
  error and mislabeled its sites "new"). Inventory rebuilt: 164/120.
  Suite 8604/0 skipped. Round 15 next.
  **Round 15 (2026-08-14) — first HIGH-free Skeptic verdict of the
  arc, but 2-lens HIGH elsewhere: my "make gaps required" fix missed a
  FOURTH caller found only by alias-aware AST census (`_srv_err`,
  post-escalate closure_error path) — its TypeError was swallowed by
  the blanket except, so a crashed post-escalation verifier left the
  SUPERSEDED attempt's verdict standing with zero trace. The lexical
  caller grep failed exactly like the lexical pin table did. Fixed +
  the except now logs.** Also fixed, all real: (1) the verdict tuple
  finally has ONE implementation — `runs._apply_verdict_tuple` +
  `_clear_verdict_keys` shared by stamp_run_verdict /
  stamp_delivered_now_retry / clear_run_verdict (the retry stamp's
  hand copy had already drifted: judged transitions left stale
  confidence/downgrade/gaps — 3-lens); (2) count-note COMPOSITION
  bugs: the risk-mint omission note was rendered before the 3-line
  cap and got capped into a lie ("three below" over two survivors, a
  middle gap silently dropped) — selection first, note last, as an
  annotation line; the prior-attempts note could be popped by the
  budget loop and counted as one omitted attempt while hiding its own
  tally, plus a len(seen) remainder guess that counted duplicates —
  reworked to collect-then-cap with the count as metadata; (3) the
  escalation ledger's field whitelist missed live sender aliases
  (`reasoning`/`summary_for_user` hit disk at 5,000 chars unmarked) —
  added, typed-schema noted as the deeper fix; (4) the scanner now
  recurses into arbitrary call args (`scrub(str(detail))[:300]` was
  certified out of the census; the breaker-reason site itself fixed to
  clip 500). Retry matrix test now asserts the COMPLETE key set after
  every transition. Suite 8604/0 skipped. Round 16 next.
  **Round 16 (2026-08-14) — REJECT, narrower again: the consensus HIGH
  is a FAILURE-PATH class, not a new field class — every shared
  verdict/stop writer converts persistence failures to an unchecked
  `return None` (both remaining lenses injected a locked_rmw failure
  and watched the superseded tuple stand with zero trace; the round-15
  caller-side warning couldn't see an error swallowed INSIDE the
  writer). Fixed: all four writers log.warning on failure before
  returning None; plus the zero-check hole (a shipped post-escalation
  rerun whose closure ran 0 checks had NO clearing arm — the parent's
  tuple stood as the delivered attempt's; now clears, honoring the
  2026-07-02 no-record-of-null-verdicts decision while refusing to
  inherit), contested standing joined the verdict tuple
  (`goal_verdict_contested/_by` — an uncontested replacement left the
  predecessor's disputed marker and rerun_identity told future runs to
  distrust the NEW verdict), audit_repair.error bounded at entry
  (10KB exception rode the canonical record unbounded, duplicated),
  closure audit retry_failed 200→clip 800, escalation whitelist +
  revert_detail, decision-prior render gets a documented 512 minimum
  budget (kills the last degenerate bare slice) and reads at most k+1
  cards (exact omission counting had opened 200 cards to render 3
  briefs — "N+" says more exist), scanner upgraded again
  (family-preferring candidate resolution, mapping-subscript keys,
  method-name identity; +12 pre-existing sites surfaced → inventory
  176/129 with the two-directions note on the ceiling). Suite 8640/0
  skipped.
  **Convergence loop PAUSED 2026-08-14 before round 17: the codex
  account hit its usage limit (resets Aug 19 ~10:35 PM) — all three
  round-17 reviewers died at spawn, confirmed by a direct probe.**
  State at pause: every accepted finding from rounds 11-16 fixed and
  landed; round 16's Skeptic verdict was the arc's first with no high;
  the exit condition (nothing above low from a genuine 3-lens attempt)
  has NOT been reached. Resume = re-run the 3-lens fixpoint review
  against the then-HEAD arc diff (`git diff b2c49a4^ HEAD -- src
  tests`), fresh lenses, same convergence framing as this entry's
  round history. NOTE for the other session: the box's own
  adversarial-review rounds share this codex account and will hit the
  same wall until the reset.
  **Round 17 RUN 2026-08-14 on the SAME-MODEL fallback (Jeremy: sonnet
  medium via `claude -p`; "the majority of the adversarial review is
  prompt + clean slate, the other model is bonus 20-30%") — and the
  fallback earned its keep: Skeptic found 2 real probe-confirmed
  findings, Minimalist returned the arc's FIRST clean lens verdict
  (probes run, "genuine convergence for this lens"), Architect filed no
  defects, only the structural recommendation below.** Fixed same
  session: (1) `audit_policy.error` was bounded on only ONE of its two
  producer branches — the round-16 fix covered the exception path while
  a write_failed status rode through unbounded (50KB probe; the
  Minimalist disputed reachability — current producers are short — but
  the mechanism was real and the fix is one line); (2) the
  post-escalation lane consulted its disputed verdict-audit only as
  in-memory routing — the exact 2026-08-09 "routing state is not
  verdict state" pattern reintroduced in the sibling lane, so a
  disputed escalated verdict rendered to future runs as ordinary
  uncontested evidence; the durable contested stamp now mirrors the
  main lane. Architect's recommendation SHIPPED as structure: a second
  frozen census (`scan_verdict_bypasses` +
  tests/data/verdict_bypass_inventory.json, 12 sites) — raw
  write_metadata/stamp_run_metadata calls carrying verdict/stop tuple
  keys are frozen debt; NEW bypasses fail the suite (route through the
  runs.py schema owners). Known census limit: dict LITERALS only —
  variable-built extras (e.g. the NOW terminal `_now_extra`) are not
  yet seen. Suite 8645/0 skipped. **Loop closed per Jeremy's
  "last round" instruction; the only-low exit was never formally
  reached (r17: 1 high-rated w/ contested severity + 1 medium, both
  fixed; 2 of 3 lenses clean). Optional: a codex 3-lens confirm round
  after the Aug 19 reset.**

  **Method that worked, for whoever picks this up:** don't argue about the
  number — pull the actual distribution out of `runs/*/build/loop-*.json`
  (`result_length` is right there), tabulate `cut → % payloads intact / %
  text shown / median extra tokens`, and the answer falls out. Every
  constant fixed today was set in an earlier era and never revisited; the
  gate's 600 dated to the gate's first commit, four months and one model
  tier ago.

  ### What if we removed the limits entirely? (measured 2026-08-03, Jeremy asked)

  1,760 step results across 268 runs. **Three different questions get
  conflated under "truncation"; only one of them has a real answer of
  "keep a bound".**

  | | median | p99 | max |
  |---|---|---|---|
  | one step result | 1,168 ch (292 tok) | 4,671 (1,168) | **20,534 (5,134)** |
  | one call payload, last 6 steps | 6,134 (1,534) | 20,400 (5,100) | 25,335 (6,334) |
  | whole-run accumulation | 7,464 (1,866) | 28,547 (7,137) | 74,288 (18,572) |

  - **Context windows are a non-issue.** The worst call payload ever
    recorded is **3.2% of a 200k window**; the worst whole-run
    accumulation is **9.3%**. Zero of 268 payloads would come close.
  - **Cost is small.** Untruncated per-call payloads run ~1,534 tokens
    median vs ~450 today — roughly **$0.005/call** at mid pricing, p99
    ~$0.015.
  - **The threat is already bounded upstream.** `step_exec` calls the
    executor with **`max_tokens=4096`** (~16k chars), and the observed max
    step result is 20,534 chars — consistent with that cap, imperfectly
    enforced (`llm.py:722` warns when a backend ignores it). So the
    downstream cuts are a *second, tighter, unmeasured* bound stacked on a
    real one. That is the crux: they were protecting against a runaway the
    executor already prevents.
  - **The one place a bound genuinely earns its keep: ACCUMULATING
    contexts** (`completed_context += …` per step in `factory_thin.py`,
    `director.py`). Those are quadratic in step count — re-sent volume is
    **7,457 tokens median but 65,890 at p99 and 313,271 at max**, on runs
    up to 34 steps. Note the subprocess backend bills re-sent context as
    **cache reads at 0.1×**, so the dollar impact is ~10× softer than the
    token volume suggests — but the volume is real and this is where a cap
    is doing work rather than cargo cult.
  - **What I could NOT measure: whether more context makes the output
    better.** Nothing in the run corpus isolates that, and the effect is
    not monotonic in general. So "remove the bound" is defensible on cost,
    window and upstream-safety grounds; the quality claim would need an
    A/B, and I am not asserting it.

  **Conclusion:** for single-value evidence cuts, the case for keeping
  tight bounds is essentially empty — widen to a generous ceiling and mark
  the remainder. For accumulating contexts, keep a bound but set it from
  the distribution (a whole-run p99 of ~7k tokens says the cap belongs
  near there, not at 200 chars/step). STORE cuts stay a genuine trade
  because disk is forever.

### from BACKLOG: Typed dispatch envelope — arc closed (2026-07-29 → 2026-08-06)

Spec is written: **`docs/DISPATCH_ENVELOPE.md`**. Follow-on to the
db37d525 contamination repair (provenance gate SHIPPED 2026-07-29 —
the load-bearing mint-side fix; this is the channel-separation half).
A dispatch payload may be typed JSON — `user_ask` (verbatim, the goal
closure judges), `operator_context` (labeled advisory, never learnable),
`attached_artifacts` (stored with provenance sidecars),
`operator_constraints` (scoped-to-run, structurally barred from lesson
extraction). Prose dispatches keep working conservatively. UX decree
(Jeremy 2026-07-28): machine-to-machine ONLY — never force a human into
the envelope; "we're going to want the direct ask and the prompt
separately."

**Box-side intake SHIPPED 2026-07-29** (spec steps 1+2):
`src/dispatch_envelope.py` (parse fail-loud on declared-but-malformed,
labeled `operator_block` renderer, attachment store under
`output/dispatch-artifacts/<job_id>/` with sha256+source sidecars,
basename-only names); `handle_queue.handle_task` re-parses the task
reason so recall guard / navigator / handle() / closure / lessons all
key on `user_ask` (origin stamped `dispatch_envelope`); new
`operator_context` param on `handle()` rides `_extra_ctx_parts` →
`ancestry_context_extra` (context channel, never the goal — extraction
exclusion falls out because `reflect_and_record` receives goal only,
pinned by interface test); `dispatch.py cmd_enqueue` validates at the
boundary (malformed → exit 2, nothing queued; record displays user_ask
+ envelope meta, queue carries raw payload). 34 tests
(test_dispatch_envelope.py + hermes/handle pins). **Delivery rendering
box-side SHIPPED 2026-07-29** (trio-triage DO_NOW 3/3): `cmd_result`
emits a `delivery` block (`you_asked` verbatim + `dispatched_with`
envelope meta) for envelope dispatches only; dispatch record keeps
`user_ask` untruncated past the 500-char display copy; prose keeps the
pre-envelope contract. Note: like
prior_context, the operator channel reaches AGENDA-lane runs only —
the NOW lane has no ancestry context by existing contract.

**~~Poe-side skill + delivery rendering~~ — SHIPPED 2026-08-01**:
mini2-maro-dispatch-SKILL.md v0.3.0 teaches envelope construction
(shape + field authority, the write-file `dispatch $(cat ...)`
invocation that survives SSH quoting, refuse-don't-fallback on
malformed JSON) and delivery-block rendering (lead with `you_asked`
answered; `verbatim: false` renders "as recorded", never quoted as
exact). Repo copy = source of truth; installed to mini2 via the
header's scp path.

**~~Artifacts-travel-with-dispatch rider~~ — SHIPPED 2026-08-01**
(smaller than sketched — no gate changes needed; attachments already
crossed the SSH lane inside the payload): `runs.create_run_dir` copies
the dispatch's stored attachments + provenance sidecars into
`<run_dir>/fetch-raw/dispatch/` when origin carries the envelope
marker (the linkage rides origin because no run dir exists at dispatch
time — the navigator-rationale pattern). Fail-soft: the operator
block's dispatch-side paths still serve the subprocess lane if the
copy fails. Named residual edge: a CONTAINERIZED worker can't read
either copy — build_mount_map hard-excludes the workspace and the run
dir isn't mounted; if the container lane flips on for dispatched runs,
attachments need a landing under the mounted cwd (or prompt inlining
with a size cap). Evidence-gated on the C4-BOX flip, don't pre-build.
Second residual on the same flip (adversarial review 2026-08-11): the
executor image bakes neither `maro-fetch` nor `maro-read`, but
EXECUTE_SYSTEM advertises both via host-path substitution — inside a
container those commands don't resolve and the worker falls back to
direct reads/curls, the exact costs the verbs exist to avoid. Bake the
package (and hosted-free env plumbing for maro-read, or advertise
conditionally) before any `executor.container` flip.

**Return-path quality — artifact links in the event payload + top-pick
ranking (OPENED 2026-08-05, from the 83a2c805 Poe steal run; Jeremy:
"we should probably upgrade the run page so it dynamically surfaces
the allowed artifacts… and also maybe consider a way to get a better
summary")**. Diagnosis, all verified on the run: the return event
already carries `answer_summary` + `deliverable_content`
(mini2-maro-inbox.sh composes from them), and the card's LLM
`answer_summary` was actually decent — the mush came from the PAYLOAD:
(1) curation's top pick was the recovery loop's AUDIT_NOTE, not
steal_list.md (recency rank — a post-hoc audit loop's file outranks
the primary deliverable by construction), so Poe answered the ask from
the wrong file; (2) `steal_list.md` was never copied into the served
tree, so no link existed anywhere ("I can't actually see the
steal_list.md without digging it up from the box"); (3) two step-N
execution logs filled the other deliverable slots. **SHIPPED with this
entry (run-page half, "functionally for now")**: `locate_deliverables`
now serves ALL ranked candidates (cap 12, `step-` logs excluded,
first-wins on basename collision, `card.served_artifacts` in rank
order) and the runs index links every `<run>/artifact/*` servable —
the viz allowlist already permitted the path; nothing linked it.
83a2c805 backfilled by hand (steal_list + evidence JSONs served,
verified 200 via the live server). ~~Remaining, cheap, in order~~ **ALL THREE SHIPPED 2026-08-06:**
(a) event-payload rider — notify-hermes.sh enriches the completion
event with `served_artifact_urls` (card.served_artifacts ×
notify.viewer_url) and the mini2 inbox prompt tells Hermes to link the
deliverable (repo copy scp-installed to mini2:bin/maro-inbox.sh);
(b) top-pick ranking — `_rank` gains a post_hoc term AFTER the
name-hint term: files written after the first non-initial loop started
lose to primary-loop files, hinted recovery deliverables still win
(calm-echo stays fixed; named limitation: unhinted recovery
deliverables lose to primary prose — acceptable until a specimen);
(c) truncation-cap verdict — it's a flagged line-boundary trim, not
decapitation, BUT the 900-char cap destroyed the only copy of the
LLM synthesis: full text now lands in `<run>/artifact/ANSWER.md`
(served + index-linked via straggler scan). Pins for (b) and (c) in
test_run_curation.py.

**2026-08-06 follow-through (Jeremy's nits on the run page):** artifacts
now link IN-ROW at the step whose call record shows the Write/Edit
(ground truth = tool_events; Bash-written files still reachable via a
new Outcome-panel artifact list), and the detail panel renders JSON
responses indented + tool events as per-event collapsibles instead of
one stringify blob. Also killed a report-truth bug his "completely done
by haiku?" question uncovered: the subprocess adapter attributed calls
to `modelUsage.keys()[0]`, but the CLI lists its internal auxiliary
haiku first — a `--model opus` advisor call recorded as haiku
(`_main_model_from_usage` now matches the requested tier, falls back to
highest-costUSD; pinned in TestMainModelFromUsage). So 37/38 of that
run's calls were genuinely haiku (MODEL_DEFAULT=cheap, dispatch named
no model, per-phase tier routing unbuilt; adaptive supervision is
cheap-by-design) — but the strategic-advisor call requested opus and
was mislabeled.

### from BACKLOG: Open-thread structure — census + adjudications, resolved 2026-07-28

Jeremy: work fans out consequences faster than the linear chat walks
them; partially-opened paths never get fully revisited and it has
bitten us; "I'm not sure just adding to 'the backlog' is quite enough."
Framing agreed in-session: retention is solved (BACKLOG_DONE +
dev-recall + history docs) — **attention** is the gap; BACKLOG is a
flat list whose triage requires manual whole-file reads. Also his
verdict on the obvious alternative: "I had hoped that a simple sort of
epic/issue style flat backlog with sub-items was enough, but fighting
those branches eventually overwhelms me and I lose the plot."

**Design inputs — Jeremy's own dev workflow IS the requirements spec
(2026-07-28, near-verbatim):** he stubs modules/classes/methods first —
the skeleton is the plan, living in the medium of the work itself;
`//todo` comments are attachment points for "thoughts along the way
that need referenced later but no concrete attachment point in the
organization itself"; he "sees a path through", architects the module
shapes surfacing that path, then digs into details; he spirals over
modules to refine path thinking where unknowns remain, trusting the
skeleton to hold everything he's not currently touching. Standing
constraint (his recurring gut-feel, restated this session): keep
Claude's persistence-shaped thinking and his **timeline/story-shaped
memory** aligned — the structure needs BOTH views.

**Proposal v2 (2026-07-28, awaiting Jeremy's reaction):**
1. **Thread census first** (unchanged) — sweep BACKLOG / GOAL_BRAIN
   open threads / history-doc residuals / wiring-inventory surprises
   into a deduped inventory with parent provenance, state (untouched /
   partially-walked / blocked-on-Jeremy), last-touched. Now also
   produces the initial ACTIVE/PARKED split and validates the marker
   convention against the real thread shapes found. Census before
   structure — prior passes at this problem (graph-memory direction,
   recursive-orchestration memory, thread architecture) committed to
   shape before knowing the territory.
2. **Narrative spine** — a dev-lane captain's log (append-only, one
   paragraph per session close: what happened, what it opened, what it
   parked). Timeline-shaped for Jeremy's memory; entries name thread
   slugs so the two views link. Near-zero cost; end-of-chunk
   discipline gains one line.
3. **Attachment-point markers** — the `//todo` translation:
   `THREAD[slug]` markers in docs/code at the point of relevance; a
   deterministic collector generates the map + staleness ranking
   (markers are the durable copy; the view is regenerable). BACKLOG
   then holds arcs/epics only; leaf threads live where they attach —
   position carries the context that flat entries must rebuild in
   prose.
4. **Working-set cap** — explicit ACTIVE set (3–7 threads, the
   spiral), everything else PARKED (trusted skeleton); the surfacer
   proposes promotions only when ACTIVE has room. Attention budget as
   a first-class constraint, not a virtue.

Runtime mirror (deliberate, not coincidence): see Vision entry
"Step-skeleton parallelization" — the dev-side structure is the cheap
prototype of the runtime step-DAG. Jeremy: "we're organizing all of
this to a degree on the way I think and I'll take that as a sign we're
starting to really find some of the foundations."

**Census DONE 2026-07-28** → `docs/history/2026-07-28-thread-census.md`
(star use #3). 56 threads / 7 states; headline: **7 premise-drift
finds** — 6 queued for re-adjudication as one proposed ACTIVE batch,
1 (the C4 flip) resolved on the spot against live-box evidence — plus
7 stale doc lines currency-fixed in the census commit. Marker
convention validated with three rules the sweep forced: history docs
never carry live markers; doc-archival explicitly sweeps forward
obligations to BACKLOG/GOAL_BRAIN or declares them dead; every
PARKED/BLOCKED entry states its reason as a **falsifiable claim** so a
future pass can test it (the six drifts are exactly reasons falsified
silently). **Proposals 2–4 ADJUDICATED 2026-07-28 (Jeremy, same evening):**
(2) Narrative spine RATIFIED after reading the first real entry
("that's pretty good, I like that it's terse but cites references") —
with same-day amendments shipped: date headers for multi-day
run-on sessions, plain-language rule (gloss codenames on first use),
and a viz-server Dev log tab next to Runs/Reading ("gives me a 1-stop
shop to go find references"). (3) `THREAD[slug]` markers + collector
DECLINED for now — "let's keep the existing setup... let's not
overengineer our dev workflow that's already becoming a bit intense";
the census + falsifiable-park-reasons discipline stands alone.
**Revisit rider (falsifiable):** the next thread census re-asks this
question with fresh drift data; any lost-thread/premise-drift
incident before then reopens it immediately. (4) Cap mechanics:
self-serve promotion with notification BLESSED ("happy to have your
help here"); Jeremy explicitly reserves tossing prioritization to the
wind for his own agendas — that's legitimate, never a process
violation; Claude surfaces genuine priority questions when they'd
change the dev cycle. His standing concern to design against: "being
able to keep tabs on everything."

**Split RATIFIED 2026-07-28** ("Sure, sounds good"): ACTIVE =
threads 1–5 + drift-cleanup batch + 1 free slot, cap 7. Same day
(loose-ends pass): drift find #5 closed both halves (suite exit 0 +
fragile seam deleted in swarm chunk 1); free-slot trio examined — census
#31 (depth-cap) and #32 (backend-resilience pair) were ALREADY SHIPPED
(2026-07-12 / 2026-07-09; stale doc annotations inherited by the sweep,
BACKEND_RESILIENCE_DESIGN.md currency-fixed), so **the free slot =
closure-check unification (census #30)** — SHIPPED 2026-07-28: unified
at the decision layer as `director.evaluate_closure()` (deterministic
verdict→DirectorDecision mapping, zero new LLM calls); the census's
literal fold ("retire `ClosureVerdict`", 3 call sites) turned out stale
on both counts — four call sites (post-escalate re-verify was
uncounted), and the verdict carries burn-in verdict-integrity machinery
every consumer stamps from, so it survives as the evidence record on
`DirectorDecision.closure_verdict` (spec correction recorded in
ADAPTIVE_EXECUTION_DESIGN.md Trigger Point 3). Adversarial review DONE
2026-07-29 (3 Codex lenses vs d9f9968, PASS with one fix — first-try
PASS; 3/3 findings cited real code, 0 hallucinated; 1 accepted →
AST census tripwire pinning that no production caller bypasses
evaluate_closure, mutation-proven; 2 rejected as documented design.
Record: docs/history/2026-07-29-closure-unification-adversarial-review.md).
**Drift batch
ADJUDICATED same day (Jeremy)** — all four re-parked on falsifiable
reasons (GOAL_BRAIN Dormant threads + Decisions 2026-07-28): heartbeat
gate re-based on hosted-free; next-leap packaging capacity-parked with
**first claim on the next free ACTIVE slot**; thread-arch flows
as-we-go via the census-tested Remaining-pieces ledger
(THREAD_ARCHITECTURE.md L1–L5); Mage parks under the graph-memory
direction. Census follow-ups all closed — nothing from the census
awaits Jeremy anymore except the ACTIVE work itself.

### from BACKLOG: Step-prompt tool advertising — closed sub-findings (Write-before-Read fix + is_error measurement lesson)

**Sub-finding, separate class:** of the 400 `is_error` events workspace-
wide, 239 are tool-missing and **157 are unexplained residue** —
dominated not by network trouble but by tool-protocol mistakes:
**65 × `Write` "File has not been read yet. Read it first"**, 11 × `Read`
on a nonexistent path, 9 × `Read` on a directory (EISDIR), 5 × curl
connect failures, **3 × `Read` "content exceeds maximum allowed size"**
(the standing repl_reading motivation, now with a count), 2 × unknown
skill. The Write-before-Read class alone is 65 avoidable turns and looks
like a one-line prompt fix. **FIXED 2026-08-02:** additive `FILE EDITS`
section in `EXECUTE_SYSTEM` (read-before-overwrite, new files exempt) —
deliberately NOT touching the tool-contract paragraph the entry above
declines to operate on; pinned in
`test_escape_patterns.py::TestPromptContract`. Residue counter will show
whether the 65-turn class actually bends on future runs.

**And the measurement lesson that fell out:** of ~160 recognizable blocks
workspace-wide, **only 4 carried `is_error`**. `curl` exits 0 and prints
the 403 body, so a block is normally a *successful* tool call containing
failure text. Anything that treats `is_error` as the failure denominator
undercounts blocks by ~97% — pinned in `tests/test_run_readout.py` so it
can't regress. Same shape as the AWS-WAF 202: the transport succeeds and
the semantics fail.

### from BACKLOG: REPL-reading sidequest — Jeremy's ask, denominator-lesson correction, Steps 1+2 builds, A/B-1..4 + retest, planner lever (all SHIPPED; A/B-5 trigger stays live)

Jeremy's ask: articles on "repl-like reading of very large documents"
claiming far better accuracy than standard LLM practice — "grep-guessing
has limited utility, and the larger the dataset (and reading comprehension
surface) the more likely it is to fail."

**CORRECTION 2026-08-02 — the articles ARE in the link farm; my "not
there" was the arc's denominator lesson in a fifth costume.** I searched
the BOX's copy (315 posts, frozen 2026-04-11 — it's what
import_link_farm.py points at); all six RLM posts date 2026-04-15
onward. Searched a corpus that ended four days before the first post
existed, reported absence. Jeremy re-checked with the fresh clone (747
posts through 2026-07-31) and found six: Weitekamp "RLMs are the new
reasoning models" (04-21, the survey — links alexzhang13/rlm, dspy.RLM,
ax-llm/ax, rawwerks/rlm-cli); Trampoline-AI/predict-rlm (production
implementation); **Sam Hogan "HALO: using RLMs to build self-improving
agents" (05-15) — an RLM mining hundreds of thousands of execution
traces for recurring harness failure modes, then auto-fixing the
harness (AppWorld 73.7→89.5, SWE-Bench 65→74) — which is maro's
evolver/introspect lane with an RLM reader, the most directly
maro-shaped datapoint of the six**; local recursive LMs trained with RL
(05-19); GEPA (05-02, same limitation via prompt optimization instead);
Garry Tan "Resolvers" (04-15, tangential).
- [x] **Side-finding: lf- re-import DONE 2026-08-02.** The box's clone
  was already synced through 2026-07-31 (747 posts); the stale thing was
  the imported store, not the clone. Re-ran `import_link_farm.py`:
  432 new nodes (315 existing deduped by URL-hash id, 0 orphaned),
  nodes 696 → 1128; all six RLM posts present. **Edge-contamination
  outcome, checked against the standing item first: 0 new edges** — the
  topic-edge builder skips tags with >50 members and every non-generic
  tag now exceeds that, so the 2124-edge lf-lf pile did not grow.
  Honest caveat: the pre-existing TF-IDF injection exposure (lf- nodes
  rankable into live goal context, see the knowledge-web read-side item)
  now spans 1128-node corpus instead of 696. (STALE CLAIM CORRECTED
  2026-08-16 rotation audit: this used to say the lf- exposure question
  was "still Jeremy's open call" — he decided it 2026-08-02: lf- nodes
  are a third-party data resource, not maro knowledge; see the lf-
  disposition entry below and goal-brain-journal-2026-08a.md L521.)
- **Durable rule (add to the denominator family): before declaring a
  thing absent from a corpus, verify the corpus's vintage covers the
  period where it would exist.**

The work itself — **Recursive Language Models** (Zhang, Kraska &
Khattab per the survey's credits): the document NEVER enters the
context window — it lives
as a variable in a Python REPL, and the model peeks/slices/greps it
programmatically and **recursively sub-calls itself (or a cheaper model)
on located regions**, composing the answer. Claimed results: a small
model in the harness beating its larger sibling by >100% on long-context
benchmarks (OOLONG-family), 10M+ token corpora handled, sidesteps
context rot. Caveats stated up front: author-run benchmarks; overhead
can INVERT the win on small documents; community replication
mixed-positive.

**Why it fits us unusually well — three live specimens from this arc:**
1. **The re-read churn** (top cost lever): workers LOAD artifacts into
   context per step — 3.45M tokens to read local files in the $11.25
   run, 1–2M-token step-execute calls throughout the series. RLM-style
   discipline (artifacts stay on disk; bounded peeks + targeted slices)
   is the concrete mechanism behind the #22 "fewer, fatter steps" lever.
2. **3b's grep was located-comprehension, not guessing** — grep to find,
   read the hit region — and it worked at this corpus size. Jeremy's
   scaling worry is real though, and M14's fresh lesson says the same
   ("grep catches absence, not scope over-reach"): paraphrase, tables,
   and cross-references defeat keyword location. The RLM upgrade is
   outline-first + recursive sub-reads of located regions +
   quote-verification against the actual slice.
3. **RUN_TEACHINGS extraction** must read big evidence trails without
   eating them — a bounded REPL-read is the natural implementation of
   its "read the evidence layer" input contract.

**Build posture — discipline before machinery. DECREED 2026-08-02
(Jeremy): build it in up front as a direct capability — "rather than
waiting for maro to discover this sort of thing… either as a skill
(agree that's a great place to start) or whatever might be lightweight
and maintainable over time."** Step 1 is greenlit:
- [x] **Step 1 BUILT 2026-08-02: `skills/repl_reading.md`** — protocol:
  budget-first, size-check, outline-don't-read, locate-then-read-the-
  region (with grep's comprehension limits stated), quote-verified-
  against-slice with file:line provenance, map-reduce for genuinely
  large surfaces, honesty stamps ("what I did NOT read"), and the five
  observed anti-patterns. Parses in the loader; matches corpus-reading
  goals for role=worker. **A/B prediction, registered before dispatch:
  same corpus + related question, skill available → $3–6 if the
  protocol bends the read curve; ~$11 (the 3b baseline) if it doesn't
  inject or doesn't change behavior.** Confound noted honestly: the
  corpus now also contains 3b's final_verdict.md (slightly richer than
  the 3b arm saw), so read the tokens_in delta as the primary signal,
  not the dollars alone.
  **RE-JUDGE OF #5 DONE 2026-08-02 (Jeremy: "good with re-judging the
  existing artifacts"), and the false verdict had already cascaded.**
  - **Closure had independently judged it `complete=True @ 0.75, 3/4
    checks`** — on disk in the run's `closure_verdicts.jsonl` the whole
    time. The provenance false positive overrode a genuine pass.
  - **Fix verified against the REAL specimen, non-vacuously:** replaying
    the fixed guard over the run's reconstructed 25,884-char step text
    (asserted the glob claim is present, so the test can't pass by
    reading nothing) → the `artifacts/OL*.json` flag is gone and no
    other flag appears. First attempt at this check WAS vacuous (0 chars
    — the loop log stores `result_length`, not text); caught and redone
    from the step artifacts.
  - **Verdict re-stamped** (`stamp_outcome_verdict`, re-stampable by
    design): `goal_achieved=True`, source
    `closure_reverdict_after_guard_fix`, conf 0.75. Original False and
    its timestamp stay in the row's history — data not rewritten, the
    correction is additive.
  - [x] **CASCADE RESOLVED 2026-08-02 — all three contested on Jeremy's
    go.** Original finding: the false demotion drove the diagnosis to
    conclude `tool_missing`, and finalize minted 3 `achieved=False`
    lessons (`80f47016` tool_missing, `65e49b27` tool_preflight,
    `50c68716` self_cert_unreliable) whose stated rationale — fetch
    "wasn't available", output "unverifiable" — was false (the run
    fetched six real OpenLibrary records with HTTP 200s). Jeremy's call,
    given to both sessions: "contest loses us efficiency, but helps stay
    correct… we might churn way worse with some bad assumptions rather
    than contesting and re-learning." M1 session executed the contests
    16:57 (both stores, leak-checked across tiered/flat injection +
    load_lessons; contest-on-bad-provenance entry now archived at
    docs/history/goal-brain-journal-2026-08a.md L325). No manual
    re-mint of `50c68716`'s advice — contest-and-relearn is the decreed
    path; if it's real it re-derives from honest evidence. Spawned idea
    captured below: invalid-assumption detection above micro-failures.
  - **Gotcha recorded (cost me a wrong-path write):** `OPENCLAW_WORKSPACE`
    is a LEGACY pin — setting it forces `orch_root()/memory`
    (`<ws>/prototypes/maro-orchestration/memory`), NOT the live ledger.
    Ad-hoc scripts touching live memory must use **`MARO_WORKSPACE`** or
    **`MARO_MEMORY_DIR`**. The stamp correctly returned `missing` instead
    of creating a phantom ledger — the guard behaved.

  **A/B RESULT (run `e0bbc289-sunny-pine`, 2026-08-02): $13.73 / 4.29M-in
  / 18.7min vs baseline $11.25 / 3.45M / 16.7min — NEITHER predicted
  tier, and the miss is the finding.** The protocol was followed
  *beautifully*: 34 size-checks, 34 grep-locates, 26 slice-reads, only 2
  whole-file cats, and the best honesty section any run has produced (an
  explicit "Artifacts NOT read" list incl. "already confirmed
  irrelevant; not re-read this pass"). Cost went UP anyway because **on
  the subprocess executor, token cost scales with TOOL TURNS, not bytes
  read** — each of ~94 round-trips re-sends the growing step
  conversation, so many tiny reads cost more than a few fat ones;
  several wc+grep+sed triples ran against 25-line files where one cat
  was strictly cheaper. Closure 0.75 (vs 0.93), deliverable quality
  high. **Skill revised same day to state the protocol in TURNS** (one
  corpus-wide orientation command, whole-reads for small files batched
  into single commands, locate+read combined, multi-region seds).
  Second A/B with the turn-based protocol is the natural next probe.
  **A/B-2 PREDICTION REGISTERED 2026-08-09 (before dispatch):** same
  corpus, fresh related question (Maidstone/BMJ side — the prior
  verdict files cover it only at summary level, so the answer requires
  the raw evidence). Predicted: tool turns **< 50** (vs A/B-1's ~94),
  tokens_in **< 3.4M** (below the 3.45M baseline despite the corpus
  having since grown), cost below the $11.25 baseline; protocol
  adherence visible as combined locate+read turns and batched
  small-file reads; honesty section retained. **Falsifier on record:**
  if turns drop but tokens_in stays ≥ 4M, the turn-resend theory is
  wrong — conversation growth is driven by something other than tool
  round-trips and the skill can't bend cost on this transport.
  **A/B-2 RESULT (run `4133114b-swift-falcon`, 2026-08-09, dispatched
  ~2h after registration): ALL THREE AXES CONFIRMED, falsifier not
  triggered.** 48 tool events (vs A/B-1's ~94 — halved), tokens_in
  2.70M (vs 4.29M A/B-1 / 3.45M baseline), cost $1.49 on the
  estimate lane the old figures used (vs $13.73 / $11.25) and $2.92
  provider-truth (the new cost lane's first live full-run readout:
  7/7 rows provider-priced, est/truth ratio 0.51 ≈ the 0.44 corpus
  mean). Quality UP: closure 0.92 (vs 0.75 A/B-1), wall 20min (vs
  48), protocol visibly followed — combined locate+read turns,
  full-reads for small files, quote reconciliation by re-reading the
  actual line (the step-6 "one long unwrapped line" catch), honesty
  accounting 8-read/38-not-read. **Honest caveat: the question was
  narrower than A/B-1's** (one named opinion + corroboration vs
  corpus-wide role reconstruction), so part of the drop is
  question-shape — but turn-count halving at higher closure is the
  predicted mechanism behaving as theorized. Turn-based revision
  KEEPS. Step-2 (recursive sub-calls) gate: evidence now favors "the
  curve bends"; still wait for a same-shape replication before
  building executor machinery.
  **A/B-3 PREDICTION REGISTERED 2026-08-10 (before dispatch; the
  same-shape replication the step-2 gate demands):** corpus-WIDE
  question this time — full litigation reconstruction across all three
  opinions + secondary sources, matching A/B-1's breadth so the
  narrowness caveat can't explain the result. Predicted vs A/B-1's
  94 turns / 4.29M tokens_in / closure 0.75: turns **≤ 60**, tokens_in
  **≤ 3.2M**, closure **≥ 0.75** at comparable deliverable quality.
  **Falsifier:** turns/tokens back near A/B-1 levels on the wide shape
  = A/B-2's win was question-narrowness, not the turn-based protocol,
  and the step-2 gate stays closed.
  **A/B-3 RESULT (run `29c8bced-gilded-forge`, 2026-08-10): ALL THREE
  AXES CONFIRMED on the wide shape, falsifier not triggered.** 52 LLM
  calls (≤60 predicted; A/B-1 was 94), tokens_in 2.72M (≤3.2M
  predicted; A/B-1 4.29M), closure confidence 0.9 (≥0.75 predicted),
  goal achieved, provider cost $3.76, 8/8 steps done 0 blocked. The
  narrowness caveat is dead: same corpus-wide breadth as A/B-1 at
  ~55% of the calls and ~63% of the tokens with higher closure.
  Turn-based protocol replicates. **Step-2 (recursive sub-calls) gate
  is now OPEN** — same-shape replication demanded by A/B-2's caveat is
  delivered; executor machinery for recursive sub-calls is buildable
  when queue priority allows. The
  accuracy/honesty half of the RLM direction is confirmed worth keeping
  regardless; the cost half INVERTS on a per-turn-resend transport, which
  bounds where step-2 (recursive sub-calls) can pay on this backend.
  **Pause-stamp residual VERIFIED same day, result reframed:** the
  ceiling path doesn't stamp — and §13e's vocabulary had NO budget
  value by design ("out-of-budget" is a STOP verdict, not a pause). So
  budget-ceiling continuations are restarts under current decree; if
  Jeremy wants multi-pass budget runs as same-identity resumes, that's
  a §13e vocabulary decision. **His call CAME same day (extension-ladder
  decree, GOAL_BRAIN 2026-08-02): `budget-decision` is now an
  operator-class pause reason** — stamped when a run breaches its
  token/cost budget a third time after two one-run-budget extensions;
  that pass ends `interrupted` (closure skips it) so the user's
  follow-up rides the same-identity RESUME lane. The max_iterations
  ceiling keeps its own continuation/escalation machinery (work already
  continues there — its "no stamp" behavior stands, out-of-budget stop
  verdict unchanged for that path and for `budget.extension_ladder:
  false`).
- ~~**Step 1 (cheap, testable): `skills/repl_reading.md`** — a reading~~
  protocol skill: outline/TOC first; never cat whole files; targeted
  slices by offset; sub-verify every quote against its slice; explicit
  budget per read. A/B it on a corpus goal we already have a baseline
  for — the 3b shape at $11.25/3.45M-in is the registered control.
  Success = the token curve bends materially on the same deliverable
  quality.
- [x] **Step 2: recursive sub-calls — v1 SHIPPED 2026-08-11** (gate
  opened by A/B-3's same-shape replication). Shape: NOT an executor-seam
  tool — on the subprocess transport the inner session has shell, so the
  verb is a CLI it shells out to: `maro-read "question" files...`
  (src/read_query.py, console script + __READ_CLI__ advertisement in
  EXECUTE_SYSTEM mirroring maro-fetch, repl_reading §5b teaches it).
  A hosted-free model (validation ladder) reads bounded slices
  out-of-band and returns only answer + honesty receipt — file bytes
  never enter the step conversation, one bash turn replaces
  outline+N-read turns. Gates: `validate.hosted_free.enabled` egress
  consent (a key is authentication, not consent) + provider keys;
  `executor.read_query` killswitch (DEFAULTS row); NO paid fallback —
  never spends money. Bounded: ≤8 files/~48KB slices/≤700-tok answer,
  no-tools single-shot (can't recurse deeper). Live-smoked on
  DEFAULTS.md (2 locator fixes from smoke: selectivity-ordered regions,
  head sub-budget) + a run's closure_verdicts.jsonl — sub-second, $0,
  correct with file:line receipts. OPEN evidence question: does a real
  corpus run USE it and does the curve bend further?
  **A/B-4 PREDICTION REGISTERED 2026-08-11 (before dispatch):** same
  corpus + breadth as A/B-3, DIFFERENT question axis (evidence-building
  story, not litigation history — an identical question would trigger
  the new re-run brief and point the run at A/B-3's finished answer,
  contaminating the measurement). With maro-read advertised: LLM calls
  **<= 45** (vs 52), tokens_in **<= 2.2M** (vs 2.72M), closure
  **>= 0.75**, AND >=1 maro-read invocation visible in the step
  transcripts. **Falsifiers:** (a) zero invocations -> the
  EXECUTE_SYSTEM advertisement is insufficient teaching, fix the
  teaching before re-testing the verb; (b) invocations but calls/tokens
  ~= A/B-3 -> the verb doesn't bend the curve on this shape; record
  honestly, step-2 stays shipped as a capability, not a cost win.
  **A/B-4 JUDGED 2026-08-11 (run 2a3bb789-lucid-forge): PREDICTION
  FAILED on all four axes.** 59 calls (vs <=45; A/B-3 = 52), tokens_in
  5.32M (vs <=2.2M; A/B-3 = 2.72M), goal_verdict_confidence 0.6
  (vs >=0.75; below the 0.7 trust floor -> DIRECTIONAL), and **zero
  maro-read invocations** — the EXECUTE_SYSTEM advertisement rode every
  executor payload (confirmed in call JSONs) but no worker invoked the
  verb; the 225KB 1908-opinion fulltext (the exact target shape) was
  read via Read tool + grep instead. **Falsifier (a) fires:** the
  advertisement alone is insufficient teaching — and per the verified
  skill_loader gap, EXECUTE_SYSTEM is the ONLY teaching surface workers
  see (repl_reading §5b never reaches them). Teaching fix owed before
  any re-test. Honest confounds, cutting BOTH ways: (1) the token blowup
  is dominated by step 8 (adversarial citation re-verification, 3.17M
  tokens_in alone — 60% of run); steps 1–7 totaled ~2.15M, under the
  2.2M line, so the plan SHAPE (a heavier 8-step plan than A/B-3 drew)
  is a major confound in the cost axes; (2) but zero invocations is
  clean and unconfounded — the verb cannot bend any curve if it is
  never called. Run quality itself was good: verbatim-accurate quotes,
  its own step-8 audit caught + fixed 10 drifted citations,
  goal_achieved=true — the 0.6 confidence is the judge's, not a broken
  deliverable. Step-2 stands as shipped capability; cost-win claim
  remains UNPROVEN. **Teaching fix shipped same day:** EXECUTE_SYSTEM
  block rewritten from preference ("prefer one sub-query") to a
  decision rule (check size first; >~50KB + need-answers-not-editing ->
  sub-query, do NOT read into context) and now states the economics
  (per-turn re-send makes a 200KB read cost ~50k tokens EVERY remaining
  turn); repl_reading §5b aligned + workspace copy synced. RE-TEST on
  the next naturally corpus-shaped dispatch — not another same-corpus
  run (question-axis exhaustion; third run would hit the re-run
  identity brief or contrive an axis). Prediction for that re-test:
  >=1 invocation on any file >50KB, or falsifier (a) fires AGAIN and
  the next lever is planner-level (plan steps naming the verb), not
  more prompt wording. **RE-TEST DISPATCHED 2026-08-12 (Jeremy: "fire
  up a sub-agent for the A/B-4 re-test"):** corpus =
  finish-and-correct-the-tire (1.25MB raw HTML capture + 92KB listings
  file — different domain from the exhausted chlorination corpus),
  goal = audit wheel_spec.md claims against the raw captures with
  exact quotes (answers-not-editing over >50KB files by construction;
  verb not named in the goal). Prediction unchanged from above,
  registered before dispatch. **ENVIRONMENTAL CONFOUND FOUND during
  re-test setup (2026-08-13), recolors the original A/B-4 verdict:**
  the first two re-test dispatches died at step-execute — the
  maro-claude-auth container OAuth (seeded 07-14) expired ~08-12,
  dispatch lane DOWN. Diagnosing that surfaced the bigger fact: the
  executor image bakes only node/claude/git/python3, `maro-read` is on
  no PATH anywhere, so EXECUTE_SYSTEM advertises
  `python3 /home/clawd/claude/maro-orchestration/src/read_query.py` —
  a path build_mount_map HARD-EXCLUDES from containers (live repo +
  workspace root exclusion). **A containerized worker could never have
  invoked the verb no matter how good the teaching** — A/B-4
  (lucid-forge ran containerized) measured teaching+environment
  conflated; zero attempts (vs zero successes) still indicts the
  advertisement, but falsifier (a)'s "teaching fix owed" was only half
  the story. Same holds for __FETCH_CLI__. Re-test therefore runs with
  `executor.container: off` (TEMP — also restores the downed dispatch
  lane; re-seed command in the workspace config comment) so it
  measures TEACHING alone on host executors, where the advertised
  path exists. Container verb parity filed as its own item below.
  **RE-TEST JUDGED 2026-08-13 (run be7c618a-patient-delta, host
  executors, verb reachable): FALSIFIER (a) FIRES AGAIN — cleanly this
  time, environment confound removed.** goal_achieved=True; the run
  faced multiple >50KB files (92KB step-5-output.txt listings, its own
  566KB audit_grep.txt which it then "manually traced"), the
  read_query.py advertisement appeared in 6 of its prompts, and the
  worker made ZERO invocations — it chose exhaustive grep sweeps +
  manual tracing instead (AUDIT.md names the method). Zero attempts
  with the verb reachable and taught = the pre-registered consequence
  stands: **the next lever is planner-level (plan steps naming the
  verb), not more prompt wording.** Prompt-teaching axis is now
  exhausted on two corpora with the environment clean.
  **PLANNER LEVER SHIPPED 2026-08-13 (overnight):**
  `planner.READ_QUERY_STEP_RULES` — decompose is taught (guidance-form,
  recon/world-facts precedent) to write the sub-query invocation INTO
  extraction-step text for likely-large (>~50KB) files, making the verb the
  stated work instead of an ambient capability. Gates:
  `planner.read_query_steps` emission switch (DEFAULTS row) AND
  `executor.read_query` killswitch AND container mode off (never
  advertise a command the executor environment can't run — container
  verb-parity principle; mode not suppression state, since plan text
  outlives its writing moment and a suppressed run can RESUME
  containerized; lane-detection exceptions fail closed). Taught in
  assembled + staged lanes, excluded from the cuts JSON prompt. 9 pins
  (tests/test_read_query_steps.py). **Same-day skeptic review: 4/4
  findings real, all fixed** — (1) plan-time suppression snapshot vs
  resume-time lane (the gate now keys on config mode only), (2)
  exception fail-open -> fail closed, (3) taught example now
  single-quotes question + paths (spaced paths, JSON step arrays), (4)
  pin that the taught command IS step_exec's resolved CLI.

### from BACKLOG: Tire-runs: token-lean fetch + mid-step token brake — SHIPPED 2026-07-27 build/review narrative (its Still-open sub-list stays live)

- **Token-lean fetch for subprocess workers + mid-step token brake.**
  Run 3 step 4 burned 2.14M input tokens ($1.21) curling raw retailer
  HTML; the capped Jina path (`web_fetch.py`) is architecturally
  unreachable from the `claude -p` backend (`fetch_tool.py` docstring
  says subprocess workers "have their own fetch tools") — the live
  backend on this box bypasses the one seam that would contain the
  blowup. The 200K/step figure is diagnostic, not a brake. Cost control
  has to live at the substrate boundary.

  **Fetch half SHIPPED 2026-07-27** (branch `token-lean-fetch`): the seam
  is now reachable from a shell — `python3 src/fetch_tool.py <url>` (and
  the `web_fetch.py` delegate) returns the capped markdown chain, and the
  EXECUTE_SYSTEM "URL FETCHING" block names that command with an
  absolute path resolved at import, replacing the old text that told
  workers a non-pre-fetched URL was simply "unavailable" (which left
  curl as the only move). Chain hardened to Jina → **Cloudflare
  markdown.new** → raw HTTP+BS4: Jina was a single point of failure that
  403s/rate-limits, and a worker with no working markdown tier falls
  back to curl. Measured on en.wikipedia.org/wiki/Tire: 754,447 chars
  (~188K tokens) raw → 20,279 (~5K) through the chain, 97.3% reduction.
  Raw HTML is captured full-fidelity to `<rundir>/fetch-raw/` (content-
  addressed, `index.jsonl` manifest) with only a one-line path pointer in
  context, so a later step can re-extract JSON-LD/tables the markdown
  drops without re-fetching — verified by pulling a `datePublished` field
  out of a capture the markdown had dropped. Deliberately NOT under
  `<rundir>/build/`: captures are unscrubbed third-party HTML and
  viz_server serves that tree.

  **Adversarial review 2026-07-27 (3 Codex lenses) returned REJECT by
  consensus; remediated in `d3bda62`.** Findings that were real and are
  now fixed: capture-store symlink handling (a planted `<digest>.html`
  was reused and its path handed to the model as something to parse — a
  file-disclosure primitive; a fixed `.tmp` name was both a planting
  target and a two-lane race), a symlinked capture dir redirecting
  captures into the viz-served `build/` tree, no egress policy (the
  capture path made a second unproxied request, so a page could return
  clean markdown via Jina and redirect the capture at cloud metadata),
  credentials/query stored plaintext in the manifest, no disk budget,
  "full fidelity" that was a 500KB decoded prefix, and a Cloudflare
  string-valued `result` envelope parsed as failure. The review earns
  its cost again — the 6612-green suite caught none of these.

  **Mid-step token brake SHIPPED 2026-07-27** (same branch, follow-up
  chunk). Two enforcement points, both at the substrate boundary:
  (1) per-tool-call ingest cap — agentic subprocess calls now carry
  `BASH_MAX_OUTPUT_LENGTH` (config `llm.subprocess.bash_max_output_chars`,
  default 24000 chars; `MARO_BASH_MAX_OUTPUT_CHARS` overrides, 0 disables),
  which makes the CLI ITSELF persist oversized Bash output to a file and
  put only a capped slice in the model's context — verified live on CLI
  2.1.220 (100K-char output → exactly-cap-sized slice in the transcript,
  including through `ClaudeSubprocessAdapter.complete` end-to-end); covers
  curl/cat/multi-MB JSON alike, and the keys are forwarded through the
  container lane's explicit env passthrough. (2) per-call accumulation
  brake — a stream-side probe (`llm._build_step_token_brake`) sums uncached
  ingest (input + cache_creation, deduped per API message id) and kills the
  subprocess past `llm.subprocess.fresh_input_ceiling_tokens` (default
  300K; `MARO_SUBPROCESS_FRESH_INPUT_CEILING`, 0 disables) →
  `TokenRunawayError` → error_class `token_runaway` → the STEP blocks and
  the run continues (deliberately unlike budget_runaway, which stops the
  run). The one thing maro canNOT do was verified and documented in
  `llm.py`: tool results cannot be rewritten before the model sees them —
  the NDJSON is the CLI's after-the-fact echo of its own loop, so
  truncating in `_parse_stream_json` would only falsify maro's records.
  Same-seam fixes riding along, both from live NDJSON evidence: the
  stream cost probe double-counted usage ~2x (one assistant event per
  content block, same message id + usage — now deduped), and the
  rate-limit retry dropped `env_extra` (a capped call retried uncapped).
  The EXECUTE_SYSTEM "curl is fine for JSON APIs" carve-out is closed with
  size-aware guidance (`head -c 20000` / curl -o + jq) and discloses the
  harness truncation so workers plan around the persisted file. Tests:
  tests/test_step_token_brake.py (28).

  **Adversarial review 2026-07-27 (3 Codex lenses vs 344973f): REJECT →
  remediated (acc5eda) → merge-gate round found the remediation's own gap
  (empty loop_status coerced to "stuck" — no-retry delivered, run-continues
  NOT) → fixed at merge. Full record:
  docs/history/2026-07-27-token-brake-adversarial-review.md.**

  **Resolved at merge (2026-07-27):**
  - **Brake ceiling calibration — MEASURED.** 123 cost-metered steps on
    this box: healthy fresh ingest cost-bounded at p95 ≈ 259K, pathology
    band 337-478K → 300K default confirmed. (Raw run-log `tokens_in` is a
    trap metric for this: includes cache reads + pre-fix double-count.)
  - **Cache-read amplification — ceiling #2 shipped.** Weighted ingest
    (fresh + cache_read/10) vs `llm.subprocess.weighted_input_ceiling_tokens`
    (default 600K ≈ 2.3× healthy p95; `trigger="weighted"` on the error).
  - **Read bypass — ACCEPTED as Bash-only, guarantee restated** (DEFAULTS
    row): fetch-extract reads are the sanctioned lane; a wholesale Read of
    a large capture lands as fresh ingest and the accumulation ceilings
    are the backstop. The Read tool has no CLI knob (recorded, below).

### from BACKLOG: Tire-runs: fail-open judge-error edges — third gate + 2026-08-06 readout findings/fixes (deliberate-cut bullets stay live)

  - **A third gate joined the family after this census closed
    (2026-08-03).** The node-promotion validator
    (knowledge_web.py:1952/1956) shipped 2026-08-02 with battery V3
    explicitly adopting *"the same degradation contract as skill
    promotion"* — correct hygiene individually (`judged=False` stamped),
    but it makes **three gates on the learning path that decline to
    decide in the same direction**. The judged-denominator readout above
    is now the load-bearing one of these edges, not a nice-to-have: all
    three carry the `judged` bit and nothing counts how often promotion
    credit accrues through an unjudged pass. Raised by the Opus-5
    2026-08-03 contrast; full context in the LeAct item below.
  - **Readout RUN 2026-08-06 — and the denominators told a different
    story than the question assumed.** Per gate:
    1. *Skill-promotion validation:* **zero SKILL_PROMOTED events since
       2026-06-11** — the harness has never stamped a production
       promotion, and not because validation fails. The use gate read
       `Skill.use_count`, whose only writer was removed 2026-07-29 as
       dead code ("sat at 0 for 312/314") — so the gate could never
       pass. Store census: **376 provisional / 0 established**;
       use_count 0 for 374/376; **134 skills eligible on the real
       signal** (SkillStats `total_uses` ≥ 5 + utility ≥ 0.7; top:
       783 uses at 98.2% success, held provisional forever). Same
       family as the dynamic-guardrail lane that never loaded a row.
       **FIXED same day:** gate reads `max(use_count,
       stats.total_uses)`, capped 10 promotions/sweep (node-promotion
       shape) so the backlog drains over ~14 maintenance sweeps
       instead of one 134-skill validation burst. Dead-gate pin:
       `test_skills.py::test_maybe_auto_promote_reads_stats_uses`.
       Judged/unjudged stamps start accruing with the first real
       promotions — check back after a few sweeps.
    2. *Node promotion:* 0 events, legitimately — shipped 3 days ago
       and no candidate has been independently re-observed 2× yet.
    3. *artifact_check:* **the denominator is not recorded anywhere.**
       The unjudged fail-open is a `log.info` (loop_execute ~1312);
       run records keep `result_length`, not text; no captain's-log
       event; journal retains nothing. Retroactively unmeasurable.
       ~~Cheap wire when wanted~~ — **WIRED 2026-08-06** (this
       commit): per-step tri-state `artifact_check`
       ("judged"/"unjudged"/"" = check not run) stamped in
       loop_execute, carried on StepOutcome, persisted per step in
       `loop-*-log.json` plus `totals.artifact_checks_judged` /
       `artifact_checks_unjudged`. History before this commit stays
       unmeasurable; the denominator accrues from here. Pins:
       test_agent_loop `test_artifact_check_denominator_persisted` +
       `test_artifact_check_error_stamps_unjudged`.

### from BACKLOG: Swarm-review: V3 lesson-side twin (double-recorded) + era-file C-tier drops

- [x] **V3's lesson-side twin (doorless canon threshold) — SHIPPED
  2026-08-13 (1f34ca9), moved to BACKLOG_DONE.md with full context.**
  Door built, Δ-gate-aware: `promote_canon_lesson` operator verb →
  playbook.md Canon section; surfacer excludes Δ-demoted/Δ-inert rows
  and annotates measured Δ. This closed the LeAct go's second piece.

**Era-file C-tier drops (grounding-checker review §C — out-of-arc,
batched here so the drop is deliberate; full context in each era file's
"lost good ideas" section):** Loop-Sheriff one-pager; degrade-don't-idle;
metric-thresholded promotion gates (era 00). UX timing SLA numbers;
per-phase cost models (01). SOURCES.md landed-where log;
runtime-awareness/tool-latency injection; research-doc-with-parallels
format (02). Bi-temporal fields; queryable git; @lat backlinks;
PASS/FAIL-gate template; `await:<kind>` (03). Constraint-level outcomes;
human-gate-at-constraint-altitude; eval train/test holdout (04). Parallel
watcher; elephant invariant; agenda-reframing/propose-better-goal;
cross-run convergence cap; blocking ask() (05). Per-claim ratings format
(06). GOAL_BRAIN compaction (07 — separately queued post-drift-review).
Semver CHANGELOG; rung-ladder DoD form (08). Fastembed/sqlite-vec gate
re-check (09). Session-reuse cost A/B; verifier-synthesis resumption;
enrichment consumption; Fable-handoff discipline (10). Drift review
specced-never-executed; closure-latency-before-notify (11). None is
load-bearing for chunks 3-5 (review-confirmed); pull individually on a
real trigger, don't sweep.

### from BACKLOG: LeAct acceptance filter — full amendment trail (Opus blind contrasts 1+2, Δ-gate build, demotion/tombstones, competence-redundancy decay v1) — SHIPPED through 2026-08-13

- [x] **SHIPPED 2026-08-09 (§5 cut B — structural half)**: thinkback
  mints now enter the tiered store PROVISIONAL (excluded from every
  injection surface; citizenship only via confirmed-context re-record or
  measured positive Δ through `confirm_lesson_by_delta`) with
  `minted_by="thinkback"`; evolver prompt_tweak mints stamp
  `minted_by="evolver"` (not provisional — own EVOLVER_VERDICT
  lifecycle); `delta_replay --origin` measures trace mints as a class.
  The filter's verdict gates tiering, as required below. What remains
  operator-driven by design: actually RUNNING the measurement campaign
  on live trace mints (CLI spend posture), and the follow-on
  competence-redundancy-decay steal stays queued. Original item:
  Steal from LeAct (arXiv 2607.21856), surfaced by the Hermes minimal-prompt
  research run: keep a sampled reasoning trace only if it **measurably raises
  the probability of reproducing a known-correct action** — an outcome-gated
  acceptance filter on explanations, not just on changes. Mapped onto us:
  `thinkback.py` (single-run LLM narration, no quantitative filter),
  `evolver.py` (LLM pattern-spotting, no trained selection objective), and
  the near-miss `VERIFY_LEARN_ARC` — which gates learning on MEASURED outcome
  but verifies applied CHANGES, not REASONING TRACES. The gap: nothing applies
  the measurable-improvement filter to the explanations thinkback/evolver
  mint, so a plausible-but-wrong narrative ships as a lesson exactly like a
  validated one (same failure family as the ablation's confabulation finding —
  confident wrong theories defeat retrospective review). Minimum experiment:
  score a minted trace by whether injecting it improves replay outcome on the
  run family it was minted from (the navigator A/B rails already exist).
  Consumer-first: the filter's verdict must gate minting or tiering, not just
  annotate. No successor-arc decree stands (verify-learn closed V1–V5) — this
  queues as a discrete item, not an arc reopen.

  **Amended 2026-07-31** (Opus-5 blind contrast, every code claim verified vs
  tree at badee73 — full record + accuracy scorecard:
  `docs/history/2026-07-31-leact-opus-contrast.md`). Deltas folded in:
  - **Blind-spot/tenure diagnosis**: the original entry's worry is false
    positives surviving; the opposite failure is also live — true novel
    positives dying. Tenure requires sessions_validated ≥ 3, incremented only
    on similarity>0.8 restatement (knowledge_web.py:339/719), and a lesson
    that fills a blind spot is by definition one the extractor won't
    independently re-derive 3×. Δ-gating fixes both directions at once: it
    flips selection pressure from "agrees with the corpus" to "changes
    behavior". (Engages Jeremy's "maro is starting to think like me" concern
    directly — the tenure rule actively selects for teacher-copying.)
  - **Stratify rule-vs-reason before reading results**: lessons that ARE their
    own action ("run tests before claiming done") have Δ≈0 definitionally
    (LeAct §6 self-referential collapse). Unstratified aggregate Δ is
    uninterpretable; discovering the corpus split is part of the experiment.
  - **No-logprobs workaround**: LeAct App D.2.1 action-match reward over M
    samples; the hosted-free rung makes M replays ~$0.
  - **Follow-on steal (sequences after the gate)**: competence-redundancy
    decay — retire a lesson when the model follows it unprompted (internalized,
    Δ≈0 vs current competence), replacing calendar 0.85/day. Needs the same
    replay harness. **Jeremy concurs 2026-07-31 (decree 55c877da)**: calendar
    decay was "mostly placeholder"; and the tenure side — leverage successful
    one-offs rather than requiring 3x independent re-derivation; repeat
    patterns are better-than-luck signals to EXAMINE, not a tenure gate.
  - **Free diagnostic, runnable any time**: surprise-count — Jeremy reads the
    long-tier corpus and counts entries that SURPRISE him (not wrong ones).
    Zero = collapse empirically confirmed; nonzero = the seed set the gate is
    evaluated against. Pair with the chunk-6 novelty-at-mint field.
    **Chunk-1 read landed 2026-08-01** (L1–L4 + M1–M15; full record + verbatim
    reads in `docs/history/2026-07-31-lesson-corpus-surprise-read.md`):
    mirroring collapse REFUTED (11/19 flagged) — but only M4 + M12 are
    positive surprise (the Δ-gate seed set so far); six are
    operator-certified contradictions (L4, M6, M8, M9, M13, M14 — the test
    corpus a retirement-by-contradiction mechanism has been missing); two
    route to existing threads (M7 → side-quest identification, M11 → fetch
    verb). Recurring critique in 4 reads: lessons minted at the wrong
    altitude — *how* (named procedure) where they should record *what*
    ("2 trusted sources") — candidate mint-time rule, pending Jeremy.
    Raw-corpus reading judged too tedious as an instrument; the remainder is
    re-issued in the same doc as **8 family judgments + optional 5-row tail
    sample** instead of 116 rows.
  - **Corrections (don't inherit Opus's errors)**: CANON_APPLY_THRESHOLD
    surfaces candidates that never promote (same shape battery V3 had for
    knowledge nodes until its promote path shipped 2026-08-02 — canon-lesson
    promotion still has none);
    extraction IS verdict-aware (extract_deferred_lessons, data-r2-01) — we're
    at the outcome-filtered baseline the paper calls insufficient, not naive;
    build/calls record-mode capture is dead on single-backend boxes, so replay
    fixtures need another source (**this correction is itself now STALE — see
    the 2026-08-03 prerequisite census below; capture went live 2026-07-29**);
    oracle anchoring exists but only negative
    (chunk-4 contradiction path) or population-level (navigator A/B 58% vs
    41%) — per-lesson positive Δ is the genuinely missing piece.
  - ~~**Revisit trigger**: don't build while the verdict denominator starves
    (4 known-arrival verdict events as of 2026-07-31). Revisit when
    `verdict_flow` shows real week-over-week verdict arrivals (Chunk B closed
    the plumbing; flow measurable from 2026-07-31 forward).~~ — **TRIGGER
    FIRED 2026-08-03**, see census below.

  **Amended 2026-08-03 (Opus-5 second blind contrast — Jeremy re-ran the
  same reviewer against the tree after ~214 commits / +9.1k lines in
  `src/`. Every code claim on both sides re-verified against the tree at
  25c286b before folding. Full record, accuracy scorecard (10/10
  substantively correct this round), prerequisite census, and the source
  text reproduced verbatim — the original lives only at machine-local
  `~/claude/opus-feedback.txt` —
  `docs/history/2026-08-03-leact-opus-contrast-round2.md`.)**

  Its one-line summary is worth keeping verbatim: *"you closed the
  upstream half of the LeAct gap and left the downstream half alone"* —
  trusting the right state–action pairs (the oracle) is built; keeping
  the right explanations (Δ) is not. It also names why that order was
  correct rather than lucky: a Δ measured over a corpus polluted by runs
  that fabricated their own memory writes would have returned noise we'd
  have believed, and LT-1 #6 says that pollution was real and invisible.

  - **REVISIT TRIGGER FIRED — the denominator no longer starves.**
    Measured 2026-08-03 via `PYTHONPATH=src python3 -m verdict_flow`:
    **67 judged rows** all-time (was 4 known-arrival at 2026-07-31),
    with arrivals two weeks running — **W31: 18 verdict events, W32: 9**
    (agenda 14/8, evolver_verify 1/1, now 3/0). Sources: closure 56,
    provenance 6, deterministic_tests 2, now_self_verdict_free 2,
    closure_reverdict 1. Stamped-later median record→verdict **1.4m**.
    The stated condition for building was "real week-over-week verdict
    arrivals"; that is now satisfied on the instrument we said would
    decide it.
  - **PREREQUISITE CENSUS — all four are in place; one was listed as
    blocking and isn't.** The contrast named what a Δ gate needs; each
    checked against the tree:
    1. *Oracle on the learning path* — LIVE.
       `provenance.memory_provenance_unverified()` (provenance.py:732)
       + `memory_claims_unverifiable()` (:785) kept structurally
       separate so an unverifiable claim can't launder into an
       accusation; learning deferred behind `learning_allowed` at three
       sites (handle.py:2596/2729/3058).
    2. *Held-out set + pre-registered predictions* — LIVE. The LT-1
       batch (CLOSED 2026-08-02, 8 arms, corpus/rubric/prediction
       registered before dispatch, `#6 = FAIL as predicted`). The
       contrast's read: this is the scaffolding a Δ gate needs and the
       reason it wasn't runnable before — *you can't score marginal
       contribution without a held-out set and a prediction registered
       in advance.*
    3. *Per-call replay corpus* — **LIVE, and this retires the
       2026-07-31 correction above.** Record-mode capture stopped being
       dark on single-backend boxes when `build_adapter(auto)` started
       always wrapping in `FailoverAdapter` (SHIPPED 2026-07-29, wiring
       row above). Verified 2026-08-03: **116 run dirs carry
       `build/calls/`**, populated 20–45 calls each on recent runs, and
       — the part that matters — *on the LT-1 arms themselves*
       (`01e55212` 20, `2738d9c0` 45, `d9607baa` 30). Each record is
       `{seq, backend, model, prompt, response, tool_events, tokens_in,
       tokens_out, purpose, error, ts}` — replayable as-is. So the
       fixture source and the pre-registered held-out set are **the same
       runs**. Neither contrast noticed this; it is the single largest
       reduction in remaining work.
    4. *With/without harness* — LIVE, `src/lens_ablation.py`
       (costume-arm vs evidence-arm), plus `src/compact_ab.py` already
       doing action-match-style with/without for one skill.
  - **NEW GAP the backlog never carried (both contrasts flagged it; it
    never became a row).** The S2 seed-reader still prepends a
    high-quality lesson as a **style exemplar**: memory.py:267 emits
    *"High-quality lesson example (emulate this style and
    specificity)"*. LeAct Fig 3b found the **no-verbatim stratum had
    higher mean positive Δ** — style-copying the teacher produced worse
    traces; their backward prompt deliberately gives only a redacted
    qualitative summary of the expert policy to stop the generator
    copying. Called *"the cheapest experiment on the list"* in both
    reads. Note the seed-reader **has** been touched since (it now
    excludes `minted_from == "prompt"` and contested seeds, chunk-1
    surprise-read fallout) — so the code moved and the *style-emulation
    instruction survived untested*, which is exactly how a caveat gets
    lost. Minimum experiment: A/B the exemplar block against a redacted
    qualitative variant ("lessons at this altitude record *what*, not
    *how*") over minted-lesson quality; the harness in (4) already runs
    this shape. ~~**Do this before the Δ gate**~~ — **RAN + ACTED
    2026-08-06** (sanctioned subagent A/B, 13 runs × 2 arms, 28 cheap
    calls; full writeup + repro:
    `~/.maro/workspace/output/seed-reader-ab/RESULTS.md`). Verdict
    inconclusive-leaning-supported: the STRONG claim (verbatim style
    cloning) refuted at n=13 — zero bigram overlap with the seed — but
    the contamination mechanism shows at the shape/label level: seeded
    arm minted the seed's lesson_type 3.5× as often (6/25 vs 2/29,
    across 6 unrelated runs incl. successful ones; Fisher p=0.125) and
    its lessons were ~60% more similar to each other (cross-run difflib
    0.107 vs 0.066, jackknife-stable), while quality proxies stayed
    flat (removal cost nothing measurable). Bonus: the store's top seed
    was itself procedure-form — "emulate this style" on a lesson
    violating the 2026-08-02 what-not-how rule. **Action: S2 seed
    exemplar REMOVED from the extraction prompt** (the tested arm was
    removal; a redacted-guidance successor was NOT tested and would
    need its own A/B — the harness reruns cheaply). Pin:
    `test_mint_form.TestNoSeedExemplar`. Δ-gate corpus-contamination
    precondition cleared; ~~the gate's one remaining input is Jeremy's
    priority decision~~ **priority CALLED 2026-08-06** ("prep the gate
    for the next run") — build brief `docs/DELTA_GATE_BUILD_BRIEF.md`,
    MILESTONES queue head, decision 623eb056. The next session builds
    the wire.
  - **Fail-open now triples in the same direction** — cross-ref the
    "Fail-open judge-error edges" item above, which tracks these
    individually but not as a population. Three gates on the learning
    path now decline to decide the same way: `skills.py` promote
    validation, `artifact_check.py:391/482`, and — landed 2026-08-02
    with battery V3, *after* the fail-open census — the node-promotion
    gate (knowledge_web.py:1952/1956, explicitly adopting *"the same
    degradation contract as skill promotion"*). Each is individually
    defensible and all three stamp `judged=False`, which is the right
    hygiene; the systemic point is that a run can now accrue promotion
    credit through three unjudged passes and nothing counts how often
    that happens. The `judged` bit exists at all three — a
    judged-vs-unjudged denominator readout is the cheap next move, not
    flipping any gate to fail-closed.
  - **Edges the contrast missed — don't re-litigate these, they're
    settled or already answered:**
    - *The surprise-count diagnostic it proposed was already run, and
      refuted its own hypothesis.* Chunk-1 read landed 2026-08-01
      (`docs/history/2026-07-31-lesson-corpus-surprise-read.md`):
      mirroring collapse **REFUTED** — 11/19 flagged, of which M4 + M12
      are positive surprise (the Δ-gate seed set), six are
      operator-certified contradictions, two route to existing threads.
      Both surprise reads CLOSED 2026-08-02. The Aug-3 pass doesn't
      mention it; "collapse confirmed" is not an open question.
    - *Jeremy already decreed the tenure fix direction* (2026-07-31,
      decree 55c877da): calendar decay was "mostly placeholder";
      leverage successful **one-offs** rather than requiring 3×
      independent re-derivation; repeat patterns are better-than-luck
      signals to EXAMINE, not a tenure gate. The Aug-3 read re-states
      the tenure critique as open. The *diagnosis* is closed; only the
      *wire* is open.
    - *Canon-lesson promotion still has no promote path* — and the
      contrast praised its twin without noticing. `get_canon_candidates`
      (knowledge_web.py:1663) surfaces on `CANON_APPLY_THRESHOLD = 10`
      and **nothing promotes them**; the node-side twin
      (`promote_knowledge_candidates`) shipped 2026-08-02 as battery V3
      and drew the Aug-3 praise. Same defect shape, one lane over,
      still open. Now newly reachable, too: the receipt write-back
      thread (2026-07-29) made `times_applied` actually accrue on
      lessons for the first time, so candidates will start arriving at
      a threshold with no door behind it.
  - **Net remaining work is one wire, not an arc.** Quoting the
    contrast, which matches the census: *"What's still missing is a
    single wire: a retention path to long-tier that runs on measured
    effect instead of on being said three times."* Concretely — a
    second, effect-based route to LONG that sits beside
    `score >= PROMOTE_MIN_SCORE and sessions_validated >=
    PROMOTE_MIN_SESSIONS` (knowledge_web.py:561, verified unchanged
    2026-08-03), scoring action-match over M replays of a captured
    LT-1 step with and without the candidate lesson injected. Sequence:
    seed-reader A/B first (contamination check, cheap) → stratify
    rule-vs-reason → Δ route to tenure → competence-redundancy decay.
    ~~Still needs a Jeremy go~~ **GO GIVEN 2026-08-12** ("go when
    you're ready for it, no reason to wait" — decision 807572df). By
    then the sequence was mostly built (this closing line had gone
    stale): seed-reader A/B ran 2026-08-06, strata + Δ-promotion route
    shipped 2026-08-06/07 (delta_replay.py + promote_lesson_by_effect),
    demotion + tombstones + noise calibration 2026-08-08. The go
    therefore lands on the remaining pieces: **competence-redundancy
    decay** (the sequence's last step), and the doorless
    `get_canon_candidates` threshold one lane over. §5 cuts
    (evolver/thinkback trace scoring, un-contest verb) stay cut.
    **Competence-redundancy decay v1 SHIPPED 2026-08-13 (1411f4f)**:
    `inert_lesson_by_effect` stamps `route="effect-inert"` on a PRECISE
    full-floor null — |Δ| ≤ 0.02 AND jackknife spread ≤ 0.02 (precision
    substitutes for dominance; spread<|Δ| is unsatisfiable at Δ≈0), ≥6
    error-free oracle calls, reason stratum, killswitch
    `knowledge.effect_inert_enabled`. Frees the decision-injection slot
    (both MEDIUM and LONG filter sites) but does NOT block tenure —
    inert = redundant, not harmful, and the exclusion is route-based/
    tier-agnostic so it survives promotion. Distinct from
    `route="measured"` (noisy null keeps circulating). Census:
    `delta_replay --inert`, applied weakest-signal-last after
    promote/confirm/demote; strike-3 remint lane untouched. Same
    operator acting standard as demotion (two agreeing full-set 0-error
    runs). 15 pins + LESSON_DELTA_INERT event + 3 DEFAULTS rows.
    "Replaces calendar 0.85/day" from the original steal is deliberately
    NOT v1 — calendar decay still runs; inert only takes the injection
    slot. Retiring calendar decay would need inert coverage across the
    corpus, and only 51 oracle calls exist. **Remaining from the go:
    doorless `get_canon_candidates` threshold** (canon-lesson promote
    path — next chunk).
    **Adversarial review (1-lens codex Skeptic, 2026-08-13, 5 findings
    all code-verified):** fixed same-day — expected_lesson text binding
    on the inert stamp (confirm-route discipline; census passes the
    measured snapshot) + DEFAULTS killswitch wording honesty (census
    reports raw Δ/spread, no would-stamp field). Rejected: stale-
    measurement overwrite race (measurement-replaces-measurement is
    decreed; two-agreeing-full-set-runs operator standard covers it,
    same as demote). Deferred v2 candidates: (a) census discovery is
    MEDIUM-only — already-LONG rows can't be measured for inertness,
    yet internalized LONG lessons are exactly the original steal's
    decay target; (b) replay arm construction appends absent lessons
    under the LONG header regardless of tier (pre-existing harness
    shape shared by promote/confirm/demote since 08-06, validated
    against known specimens — a tier-aware arm builder would remove
    the residual prompt-shape confound).
