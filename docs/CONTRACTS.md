---
status: living
---

# Workspace Artifact Contracts — the registry (v1)

*First written 2026-08-28 (Go-successor Phase 1a). This document names every
on-disk contract the execution backbone touches in `~/.maro/workspace/`
(resolved by `config.workspace_root()`), so a second engine (the Go
successor) and this Python engine can share one workspace and evolve without
release coordination. Scope is the BACKBONE's stores (B1-B11); the rest of
the workspace is enumerated but unregistered (B12). Every claim below is
grounded in code; **when this doc
and the code disagree, the code wins — then fix this doc in the same
commit.** Ownership: this repo owns every contract listed here. Changes to a
shape start here, as a diff to this file plus the writer.*

---

## A. The rules, adapted to workspace files

The workspace is a shared database. Every artifact in it — a JSONL row, a
`metadata.json`, a markdown playbook — is a payload crossing an engine
boundary the moment a second process can read it. Both engines are writers
*and* readers, so both sets of obligations bind both engines.

1. **Contracts are owned, and this repo owns them.** The shape of every
   workspace artifact is defined here (dataclasses + this registry), not by
   whatever a reader happens to expect. A second engine consumes these
   definitions; it requests changes as changes to this repo, it does not
   fork the shape.

2. **Writers only add.** A writer may add a new file, a new optional field,
   a new value to an enum-like field. It may never remove, rename, retype,
   or change the *meaning* of anything already published. Meaning is part of
   the shape: a field that keeps its name and type but changes units, basis,
   or what it includes breaks every reader while passing every schema check.
   A meaning change is a new field beside the old one, never a mutation.

3. **Readers tolerate growth.** A reader must survive things it has never
   seen: unknown keys on a row (never a parse failure), unknown enum-like
   values (map to an explicit, sane default — never an exception, and never
   silently the *most trusting* bucket), longer lists, new files in a
   directory it globs. A reader may rely only on what this registry
   promises; behavior that merely happens to be true today (field always
   populated, list always length 1, files always written by one process) is
   not a contract.

4. **The read-modify-write trap is the file version of dropped fields.**
   Any code that loads rows into a typed struct, mutates, and rewrites the
   file will silently destroy every field it didn't know about. A rewriting
   reader must round-trip unknown keys verbatim (rewrite from the raw
   parsed dict / raw line, not from the struct) or be redesigned to patch
   only its own keys. This is the single most important rule for two-engine
   coexistence — see §C2 for where Python currently violates it.

5. **Nothing is removed except by deprecate → measure → migrate → remove.**
   Mark the field/file deprecated here (name the replacement), measure that
   nothing reads it, give consumers a window, then remove. Field-level reads
   of a file are generally unobservable — so a field documented
   **guaranteed** is effectively a permanent commitment. Publish fields as
   optional by default; promotion optional→guaranteed is allowed later —
   but promotion demands more of every still-running old WRITER, so it
   carries a precondition: measure that every live writer already emits the
   field (or run a migration barrier that retires the ones that don't)
   BEFORE promoting. A promotion satisfied by writers emitting `""` is a
   lie, not a promotion. The reverse (guaranteed→sometimes-absent), and
   making an optional input required, are breaking — requiredness is a
   one-way door: over time a contract may only promise readers more and
   demand less of writers, with measured-writer-capability promotion as
   the one narrow exception.

6. **Absence is meaningful and deliberate here.** Several stores use
   *absent key = not judged / not applicable* (never `null`, never a zero
   value). A writer in any language must implement omit-when-empty for the
   fields so marked below; emitting `null` or a default where Python omits
   the key is a meaning change (rule 2). This bites Go specifically:
   tri-state booleans need `*bool` + omitempty, not `bool`.

7. **EXPERIMENTAL means breakable.** A contract or field marked
   experimental below may change freely until declared published. The
   moment something beyond its own author depends on it in anger, it is
   published regardless of the label. Everything in this registry is
   **published** unless marked otherwise — the live workspace has 800+ runs
   and ~40 ledgers depending on these shapes.

8. **Only what's written here is promised — and only what's registered
   may be written.** File mtimes, directory iteration order, lock-file
   leftovers, log phrasing, report HTML — none of it is contractual unless
   an entry says so. This registry covers the BACKBONE's contracts, not the
   whole workspace: the live workspace holds ~46 JSONL stores plus
   checkpoints, projects, and state files beyond the 11 entries below (see
   §B12). A second engine MUST NOT write an unregistered store — reading
   one is at-own-risk, writing one is a contract violation by definition,
   because there is no contract. Registration is how a store becomes
   writable from a second engine.

9. **Elements are soft or hard, and the axis is per ELEMENT, never per
   contract.** A soft element is open-world ("at least these"): object
   fields, list lengths, optional additions — safe direction is ADD,
   breaking direction is remove/rename/retype. A hard element is
   closed-world ("exactly these"): closed vocabularies, accepted-input
   shapes, storage widths — safe direction is LOOSEN, and *adding a value*
   is the breaking-ish direction (every reader must now handle it). Rules
   2-3 as written are the soft-element rules. For hard elements: (a)
   **readers only loosen** — an engine may widen what it accepts, never
   narrow it outside the deprecation lifecycle; (b) a reader meeting an
   unknown hard value picks by consequence: value merely displayed → a
   harmless default; value GATES behavior (trust, learning eligibility,
   access) → the LEAST-privileged bucket, never the most (C0.7 fixed the
   one place Python had this backwards); no safe reading exists →
   reject/quarantine explicitly, which is a legitimate modeled outcome,
   not intolerance. (c) Wherever a soft element flows into a hard one — a
   free-string wire value into a typed field, an open vocabulary into a
   closed switch — that seam is a REQUIRED assertion in whichever engine
   reads it. Each registry entry below marks its closed vocabularies;
   everything unmarked is soft.

---

## B. The registry

Format per entry: **name vN** — path · writer · readers · shape · status ·
quirks a second engine must know. "Required" means the writer always emits
it today AND readers may rely on it; "optional" means readers must tolerate
absence. All timestamps are UTC ISO-8601 strings unless noted.

### B1. workspace-root-and-config v1

- **Path:** workspace root = `$MARO_WORKSPACE` (then legacy
  `$OPENCLAW_WORKSPACE`, `$WORKSPACE_ROOT`), default `~/.maro/workspace`
  (`src/config.py:33-39`). Config files: `~/.maro/config.yml` (user tier;
  dir overridable via `$MARO_USER_DIR`, `src/config.py:121-131`) and
  `<workspace>/config.yml` (workspace tier).
- **Writer:** the operator (hand-edited YAML). The engine only reads.
- **Readers:** everything, via `config.get(dotted.key, default)`
  (`src/config.py:204-220`); e.g. `llm._get_backend_order`
  (`src/llm.py:3593`), `file_lock._lock_timeout_s` (`src/file_lock.py:70-79`).
- **Shape:** free-form nested YAML maps. Merge: workspace over user, nested
  dicts merged exactly one level deep (`src/config.py:190-197`). Precedence
  everywhere: env var > merged config > hardcoded default. Missing/malformed
  file reads as `{}` (`src/config.py:142-152`). Defaults are registered in
  `docs/DEFAULTS.md`.
- **Status:** published. `MARO_WORKSPACE` is the one env pin a second engine
  must honor; the legacy names are deprecated (kept for old deployments).
- **Quirks:** config is mtime-cached per (path-pair) and self-invalidates
  (`src/config.py:171-201`) — a second engine gets the same "operator edit
  visible without restart" behavior only if it re-stats. Overlay convention:
  `skills/`, `personas/`, `user/` resolve workspace-copy-over-repo-copy
  (`src/config.py:97-114`).

### B2. write-discipline v1 (meta-contract: locking, atomicity, encoding)

Not a file — the discipline every writer of a *shared* workspace file must
follow. Cross-process, therefore cross-engine.

- **Definition:** `src/file_lock.py`. Exclusive lock = `flock(LOCK_EX)` on a
  sidecar file named `<data-file>.lock` in the same directory
  (`src/file_lock.py:130`). Deadline: config `file_lock.timeout_s` /
  `$MARO_FILELOCK_TIMEOUT_S`, default 30s. The failure posture is
  fail-closed on BOTH halves: on TIMEOUT writers raise unless the
  operator opts into fail-open, and a `.lock` sidecar that cannot be
  CREATED at all (read-only fs, permissions) now follows the same posture
  — `require=True` and the fail-closed default raise; only explicit
  fail-open proceeds unlocked, with the timeout branch's log + events-feed
  observability. (Was **DEFECT C0.6**, two parts — the create-failure
  hole let every `require=False` caller proceed unlocked regardless of
  posture, and `_fail_open` read config with `bool(...)` so the string
  `"false"` enabled fail-open. Both FIXED 2026-08-28, `1e5c77fe`:
  create-failure mirrors the timeout branch, and the config parse is
  strict — bools, 0/1, and true/false-shaped strings only; anything else
  is fail-closed with a warning.) Full-file rewrites go through
  `atomic_write` — temp file in the same dir, fsync, `os.replace`, target's
  permission bits preserved (`src/file_lock.py:229-281`). JSONL appends go
  through `locked_append` — one JSON object per line, writer adds the
  newline, and it repairs a crash-torn unterminated tail before appending
  (`src/file_lock.py:283-330`).
- **Readers' guarantee:** a reader sees either the old or the new complete
  file, never a partial; JSONL readers must still skip malformed lines
  (crash debris predating the framing fix is in the live stores).
- **Status:** published. A Go engine sharing a live workspace MUST use the
  same `.lock` naming, flock semantics, and atomic-replace pattern, or the
  two engines corrupt each other.
- **Wire format (all JSON/JSONL in this registry):** UTF-8. Python's
  `json.dumps` will happily EMIT `NaN`/`Infinity`, which are not JSON and
  which Go's encoder rejects — writers in any engine must not emit them
  (Python callers that can carry floats must sanitize), and a reader
  hitting one should quarantine the row, not crash the store. Duplicate
  keys are undefined behavior — Python and Go both keep the last one, but
  no writer may emit them. Absent key, explicit `null`, `""`, and `[]` are
  FOUR distinct wire states (rule A6 leans on absent-vs-null); "required"
  in this doc means the KEY is present — whether the VALUE may be null is
  stated per field (e.g. `tokens_in` int|null is key-required,
  value-nullable).
- **Quirks:** (a) One deliberate exception: `memory/events.jsonl` is
  appended UNLOCKED. Every field is capped AND the encoded line is
  measured before write, so a line stays under PIPE_BUF=4096 bytes
  (single O_APPEND write is atomic on Linux) — enforced since C0.3
  (2026-08-28, `1e5c77fe`; see B9). Any writer of that file inherits the
  <4096-byte line obligation. (b) Byte-safety: ledger rewrites read with
  `surrogateescape` and write it back, so one torn byte can ride through a
  rewrite verbatim instead of voiding the file (`src/file_lock.py:237-247`,
  history in `src/knowledge_web.py:976-982`). (c) Additional process-level
  conventions on the same workspace: per-project admission slot (flock, held
  for the run's life — `src/loop_init.py:417-427`, `src/interrupt.py`) and
  pidfiles under `<workspace>/run/` (`src/proc_lock.py`). An engine that
  runs goals must honor the admission gate or two runs stomp one project.

### B3. run-dir v1 (run directory layout + metadata.json)

- **Path:** `runs/<8-hex-handle_id>-<nickname>/` (`src/runs.py:84-97`);
  nickname is a deterministic function of handle_id (`src/runs.py:61`).
- **Writer:** `runs.create_run_dir` (`src/runs.py:310-407`) seeds the
  skeleton `source/ build/ artifact/` + `source/prompt.txt` (verbatim goal,
  first writer wins); `runs.write_metadata` (`src/runs.py:410-461`) and the
  `stamp_run_*` family (`src/runs.py:464-1114`) maintain `metadata.json`
  under `locked_rmw`; `runs.finalize_run` (`src/runs.py:1116`) stamps
  `status`/`ended_at` under the same `locked_rmw`, preserving unknown
  keys; an unparseable metadata.json is parked verbatim in a
  `metadata.json.corrupt` sidecar (with a warning) before finalize stamps
  a fresh object. (Was **DEFECT C0.2** — a bare read→mutate→
  `atomic_write` with no lock that could silently lose a concurrent
  stamp, and a parse failure wholesale-replaced the file with `{}` plus
  two keys. FIXED 2026-08-28, `9807fe6f`.)
- **Readers:** `runs.resolve_run_dir` + the `.run-ref-index-v2` derived
  index (`src/runs.py:100-308`); `run_curation._read_meta`
  (`src/run_curation.py:126`); `loop_report` (`src/loop_report.py:1728-1733`);
  `camera_readout` (`src/camera_readout.py:235`); heartbeat's stranded-run
  sweep (pid liveness).
- **Shape of `metadata.json`** (one JSON object; core keys required, all
  others optional):
  - `handle_id` str, required — the run's identity; join key everywhere.
  - `nickname` str, required — human handle, derived, never authoritative.
  - `prompt` str, required — the goal as routed (post-prefix-strip).
  - `lane` str|null — `"now"` | `"agenda"`; how the goal was routed.
  - `model` str|null — symbolic tier ("cheap"/"mid"/"power") or raw name.
  - `started_at` required / `ended_at` str|null — lifecycle bounds;
    `started_at` is preserved across rewrites (`src/runs.py:437-438`).
  - `status` str|null — process status; vocabulary in `src/stop_verdicts.py`
    (`done`, `stuck`, plus interrupt/pause statuses, :80-119). Unknown
    values must bucket, not crash (see B5).
  - `pid` int, optional — owner process, first writer wins; the stranded
    sweep's liveness probe (`src/runs.py:441-444`).
  - Verdict block, all optional, absent-until-judged: `goal_achieved`
    bool (ABSENT = unjudged — rule A6), `goal_verdict_source` str,
    `goal_verdict_summary` str, `verdict_history` list (superseded verdicts,
    append-only), `stop_verdict`/`stop_evidence`, `verdict_pending` dict
    (async closure marker; `resolved_at` set when done). Writers:
    `stamp_run_verdict` (`src/runs.py:696`), `stamp_run_stop_verdict`
    (`src/runs.py:767`).
  - `loops` list of dicts, optional, append-only — loop lineage
    (`stamp_run_loop_lineage`, `src/runs.py:514`); authoritative attempt
    order for reports.
  - Anything else (`project`, `origin`, experiment arms, audit flags) is
    caller-stamped extra: readers must ignore unknown keys.
- **Other run-dir files:** `source/` = compile inputs (prompt.txt,
  environment.json `src/runs.py:1595`, skills manifest, goal_brain.md);
  `build/` = process byproducts (calls/ → B4, trace.jsonl, reports,
  captains_log_slice.jsonl → B8); `artifact/` = deliverables. The
  `.run-ref-index-v2/` lookup index is derived and disposable — never a
  source of truth.
- **Status:** published.
- **Quirks:** metadata is merge-on-write: existing keys survive rewrites,
  and caller extras never override the core set (`src/runs.py:446-453`) — a
  second engine must implement the same merge, not blind overwrite. Verdict
  keys follow absent-not-null. `create_run_dir` is idempotent by design
  (restart re-entry).

### B4. call-records v1 (`build/calls/call-NNNNN.json`)

- **Path:** `<run-dir>/build/calls/call-%05d.json`, one file per LLM call,
  sequence rebuilt from directory contents after crash. Publication is
  exclusive-create (`os.open(O_CREAT|O_EXCL|O_WRONLY)`): on collision the
  loser rescans the directory and retries on the next free number
  (bounded), so write-once is ENFORCED, not conventional. The in-process
  counter (`_CALL_COUNTERS` + `threading.Lock`) remains the fast path.
  (Was **DEFECT C0.4** — the counter is process-local and publication was
  a non-exclusive `write_text`, so two processes sharing a run dir could
  allocate the same seq and the second overwrote the first's record.
  FIXED 2026-08-28, `9807fe6f`; the interim second-engine prohibition is
  lifted — a cross-engine writer using the same O_EXCL discipline is
  safe.)
- **Writer:** `runs.record_llm_call` (`src/runs.py:1510-1568`), invoked from
  the single FailoverAdapter seam on every success (`src/llm.py:800-810`)
  and on failed/killed attempts (`src/llm.py:868-877`). Secret-scrubbed via
  `secret_scrub.scrub` before write. Record-mode default ON; `MARO_RECORD=0`
  disables — so the directory may legitimately be absent or sparse.
- **Readers:** `loop_report` (`src/loop_report.py:1029`), `delta_replay`
  (`src/delta_replay.py:118`), `mint_grounding` — lesson receipts cite
  `build/calls/<file>#tool_events[i]` (`src/mint_grounding.py:447-456`).
- **Shape** (verified against live records):
  - `seq` int, required — matches the filename number.
  - `backend` str / `model` str — which adapter and resolved model.
  - `prompt` str / `response` str — full rendered text, post-scrub.
  - `tool_events` list, required (may be empty) — inner-agent tool receipts,
    each `{name, input, output, is_error, id}` (`src/llm.py:297-301`).
    Ground truth for "did it really run/write X". Only subprocess backends
    populate it; readers must treat empty as "no receipt", not "no work".
  - `tokens_in` / `tokens_out` int|null — null = backend didn't report.
  - `max_tokens_requested` int|null — the requested cap.
  - `purpose` str — caller-stamped label ("classify", "decompose", ...);
    open vocabulary, empty on legacy rows.
  - `cost_usd` float — provider-billed; `0.0` means "not reported", NOT free.
  - `error` str — empty on success; non-empty marks an ATTEMPT record whose
    `response` is salvage, not a result (`src/runs.py:1554-1561`).
  - `ts` — write time.
- **Status:** published (the replay corpus and grounding receipts depend on
  it).
- **Quirks:** written WITHOUT lock — safe because publication is
  exclusive-create (C0.4): files are write-once as an enforced property.
  Capture is fail-open: absence of a record proves nothing.

### B5. run-card v1 (`run_card.json`)

- **Path:** `<run-dir>/run_card.json`.
- **Writer:** `run_curation.curate_run` → `_write_run_card`
  (`src/run_curation.py:1732-1748`, `:1725-1729`, locked + atomic; written
  twice: pure curation first, then maintenance annotations).
  `refresh_run_card_classification` (`src/run_curation.py:1753`) rebuilds
  pure fields post-hoc. Called from `runs.close_run`
  (`src/runs.py:1207,1355-1356`).
- **Readers:** `loop_report` (`src/loop_report.py:936,1136,1838-1843`),
  `decision_prior.load_decision_prior` (`src/decision_prior.py:100-124`),
  `metrics` (`src/metrics.py:283`), `evolver` (`src/evolver.py:460`),
  `notify_telegram` (`src/notify_telegram.py:253`), `outcome_policy`
  (`src/outcome_policy.py:59-60`), `delta_replay`
  (`src/delta_replay.py:466`).
- **Shape** (key groups; full key list is open-ended by design — card
  builders add fields):
  - Identity/echo: `handle_id`, `nickname`, `goal`, `lane`, `model`,
    `status`, `started_at`, `ended_at` — copied from metadata at curation.
  - `success_class` str, required — THE classification. Vocabulary today:
    `success`, `done-not-achieved`, `done-unverified`,
    `done-verdict-pending`, `achieved-not-done`, `partial`, `failed`,
    `interrupted`, `unknown` (`src/run_curation.py:181-231`). Enum-like and
    expected to grow; readers must map unknown values to their fail-closed
    branch (learning eligibility already does:
    `src/outcome_policy.py:16-17,59-60` — not in the allowlist ⇒ not
    learnable).
  - Verdict echo: `goal_achieved` (absent-until-judged), `goal_verdict_source`,
    `goal_verdict_summary`, `stop_verdict`, `stop_evidence`.
  - Mineable inventory: `inventory`, `result_excerpt`, `result_path`,
    `answer_summary`, `answer_source`, `total_cost_usd`, `decision_prior`,
    `partial_rescue`, `mineable`, audit flags.
  - `_curation` / `_maintenance` dicts — the curator's own execution
    provenance (`completed`/`failed`/`skipped_dependency` lists).
    **Underscore-prefixed keys are writer-private**: no other reader may
    depend on them, but `maintain_run_card` requires its own
    (`src/run_curation.py:1711-1717`) — see C2.6.
- **Status:** published.
- **Quirks:** the card is derived — metadata.json is authoritative evidence,
  the card is the curated view; on disagreement metadata wins
  (`src/camera_readout.py:235` states this explicitly). Cards are
  regenerated post-hoc (audit repair), so readers must tolerate a card newer
  than its run.

### B6. outcomes-ledger v1 (`memory/outcomes.jsonl`)

- **Path:** `memory/outcomes.jsonl`, append-mostly JSONL; overflow
  compressed to `memory/compressed_outcomes.jsonl`.
- **Writer:** `memory_ledger.record_outcome` (`src/memory_ledger.py:497-642`,
  `locked_append`), called from `loop_finalize` via `reflect_and_record`
  (`src/loop_finalize.py:768`) and the NOW lane (`src/handle.py:1532`).
  Post-hoc mutators, all raw-row-preserving locked RMW:
  `stamp_outcome_verdict` (`src/memory_ledger.py:760-859`; caller
  `src/handle.py:3222`), `annotate_outcome_lessons`
  (`src/memory_ledger.py:1255`), `mark_outcomes_superseded`
  (`src/memory_ledger.py:694-757`), `compress_old_outcomes` (drops exact
  raw lines only — keyed merge, `src/memory_ledger.py:2233-2246`).
- **Readers:** `evolver` (`src/evolver.py:7,37`), `outcome_policy`
  (`src/outcome_policy.py:38-70`), `verdict_flow`, deferred learning via
  `load_outcome_by_loop_id` (`src/memory_ledger.py:1230-1251`), recall's
  repeat-guard, `verdict_trust` policy (`src/memory_ledger.py:138-201`).
- **Shape** (dataclass `Outcome`, `src/memory_ledger.py:44-87`; serialization
  discipline `_verdict_row`, `:462-494`):
  - `outcome_id` str, required — 8-hex uuid.
  - `goal` str / `summary` str, required — what was asked / what happened.
  - `task_type` str — open-ish vocabulary ("research"/"build"/"ops"/
    "general"/"now"/"agenda"...).
  - `status` str, required — process status, `done` | `stuck` (+ others via
    direct callers). done ≠ achieved.
  - `lessons` list[str] — extracted lesson texts (may be filled post-hoc).
  - `tokens_in`/`tokens_out`/`elapsed_ms` int, `cost_usd` float, `model` str.
  - `recorded_at` required — record time (NOT verdict time).
  - `failure_chain` list[str] / `recovery_steps` int — retry trajectory.
  - **Tri-state verdict** (rule A6 — key ABSENT when unjudged, never null):
    `goal_achieved` bool; `goal_verdict_source` str (vocabulary at
    `src/memory_ledger.py:66` + `src/stop_verdicts.py:125-152`, open — see
    C2.4); `goal_verdict_confidence` float; `goal_verdict_at` (stamped when
    the verdict lands post-hoc); `verdict_history` list — prior verdicts a
    re-stamp superseded, append-only (`src/memory_ledger.py:819-827`).
  - `stop_verdict` / `stop_evidence` / `pause_reason` — CLOSED vocabularies
    owned by `src/stop_verdicts.py:58-74,156-169`; ingress drops
    off-vocabulary values (`src/memory_ledger.py:556-573` — see C1.3).
    Empty values are omitted from the row.
  - `loop_id` / `handle_id` — join keys to the run dir; omitted when empty.
    `loop_id` is how the post-closure verdict finds this row (newest match
    wins, `src/memory_ledger.py:796-808`).
  - `dry_run` bool, `lesson_extraction_status` ("deferred"/"completed"/
    "failed"/""), `lesson_extraction_count` int, `measurement_class`
    ("organic"/"smoke"/"control"/"benchmark"/""; omitted when empty —
    never inferred after the fact).
  - `superseded_by` / `superseded_at`, optional — resume collapsing marker;
    old rows keep every other field untouched.
- **Status:** published. This is the learning pipeline's spine.
- **Quirks:** (1) The absent-key discipline is load-bearing: ~1400 legacy
  rows established "missing key = not judged" and every consumer relies on
  it. (2) All mutators preserve unknown keys (they patch raw dicts /
  drop raw lines) — the store that got RMW right first; the lesson stores
  now match via `_extras` round-tripping (C0.1). A Go engine must match.
  (3) An unverifiable closure must never erase an existing judged verdict
  (`src/memory_ledger.py:775-777`). (4) Judged rows may carry
  `verdict_excluded: true` (additive, C1.2/C0.7): the exclusion-class
  stamp `verdict_trust` honors before any source-value match; absent on
  non-excluded rows.

### B7. lesson-stores v1 (`memory/lessons.jsonl` + `memory/{medium,long}/lessons.jsonl`)

- **Path:** flat legacy store `memory/lessons.jsonl`; tiered stores
  `memory/medium/lessons.jsonl` (decaying) and `memory/long/lessons.jsonl`
  (promoted-permanent). Removed rows go to `memory/lessons_archive.jsonl`
  (append-only; retention decree: decay trust, never data). Rows minted by
  one run are DUAL-WRITTEN flat + tiered sharing one `lesson_id`.
- **Writers:** tiered: `knowledge_web.record_tiered_lesson`
  (`src/knowledge_web.py:423`) → `_append_tiered_lesson` (`:705-707`,
  locked append of `asdict(TieredLesson)`); flat:
  `memory_ledger._store_lesson` (`src/memory_ledger.py:1403`) from
  `record_outcome`. Mutations (reinforce/promote/GC/contest) rewrite the
  tier via `_mutate_tiered_lessons` (`src/knowledge_web.py:1244-1266`).
  Provenance stamped at mint by `src/lesson_provenance.py`; grounding by
  `src/mint_grounding.py`.
- **Readers:** injection: `recall.py:661-692` (`query_lessons_scored`),
  `memory.load_lessons`/`load_tiered_lessons`
  (`src/memory_ledger.py:1762`, `src/knowledge_web.py:933`); the ATIF
  stats block (`src/memory.py:409`); evolver, graduation, pack.py
  (portable-learning export, `src/pack.py:376-381`).
- **Shape** (tiered row = dataclass `TieredLesson`,
  `src/knowledge_web.py:156-266`; the flat `Lesson` at
  `src/memory_ledger.py:178-218` is a subset + the same stamps):
  - Core: `lesson_id` (shared join key across stores), `task_type`,
    `outcome`, `lesson` (the text — what-not-how form), `source_goal`,
    `confidence`, `recorded_at`.
  - Tier mechanics: `tier` ("medium"|"long"), `score` (stored as of
    `last_reinforced`; **decay is a read-time derivation and stored scores
    are never overwritten with decayed values** —
    `src/knowledge_web.py:944-950`), `sessions_validated`, `times_applied`,
    `times_reinforced`, `novelty`.
  - Trust/quarantine stamps, each with absent-or-zero-value = full citizen:
    `provisional` bool; `minted_from` ("" legacy | "outcome" | "prompt" —
    prompt-derived rows are quarantined from every injection surface);
    `minted_by` ("" | "thinkback" | "evolver"); `contested` dict
    ({reason, source, contested_at} — retirement-by-contradiction, sticky);
    `grounding` list ([{claim, family, status:
    supported|unsupported|unprobed, receipts}] — receipts point into B4);
    `scope` ("" | "method" | "world" — mint-time provenance, labeller-
    dependent, never a ranking input); `merged_variants` list[str]
    (absorbed near-dup texts); `delta_evidence` dict; `canon` dict;
    `imported` dict (pack provenance); `evidence_sources` list;
    `lesson_type` ("execution"/"planning"/"recovery"/"verification"/
    "cost"/"").
  - Old rows deserialize missing fields to dataclass defaults — every field
    added since Phase 16 followed writers-only-add.
- **Status:** published. Field-level churn is the highest of any store —
  which is exactly why rule A4 matters here. The C2.1/C2.2 RMW trap
  (filtered-constructor round-trips stripping unknown keys store-wide) is
  FIXED (C0.1, 2026-08-28, `010d3d11`): both stores rehydrate with an
  `_extras` rider and every rewrite/promotion/archive/resurrection path
  merges it back, declared fields winning on collision.
- **Quirks:** (1) Unparseable rows are QUARANTINED to a sidecar before any
  rewrite, never deleted (`src/knowledge_web.py:1256-1263`). (2) Rewrites
  must reload raw+unlimited inside the lock — a limited or decayed load
  used as a rewrite source truncates or compounds-decays the store
  (`src/knowledge_web.py:955-967`). (3) LONG rows never decay; `contested`
  is their only retirement path. (4) The flat store's `_verdict_row`
  omit-when-empty discipline applies (`src/memory_ledger.py:486-493`).

### B8. captains-log v1 (`memory/captains_log.jsonl`)

- **Path:** `memory/captains_log.jsonl`; size-gated rotation to
  `captains_log.<stamp>.jsonl` siblings (never deleted,
  `src/captains_log.py:632,466-476`). Rotation writes the archive, then
  atomically REPLACES the live file (`atomic_write`: temp file +
  `os.replace`) — lock-free readers see the old complete log or the new
  retained tail, never an empty/partial file. (Was **DEFECT C0.5** — the
  live file was truncate-rewritten in place with `write_text` inside the
  writer lock, which readers don't take. FIXED 2026-08-28, `1e5c77fe`;
  B2's atomicity guarantee now covers this path.) Per-run slice copied to
  `<run-dir>/build/captains_log_slice.jsonl` at finalize
  (`src/runs.py:1785`).
- **Writer:** `captains_log.log_event` (`src/captains_log.py:560-626`,
  locked append; best-effort by default, `raise_on_error` for
  delivery-sensitive callers). ~35 modules call it — it is the append-only
  event bus.
- **Readers:** `load_log`/`query_log`/`timeline`/`event_slice`
  (`src/captains_log.py:709,791,868,978`), `runs.slice_log_for_run`,
  the health lane, viz server.
- **Shape:** `timestamp` required; `event_type` str required — open
  vocabulary, ~60 registered types with prose meanings in `EVENT_TYPES`
  (`src/captains_log.py:367`) and the full contract in
  `docs/CAPTAINS_LOG_EVENTS.md`; `subject` str; `summary` str; `audience`
  required — DERIVED: "user" iff event_type ∈ USER_SURFACED_EVENTS else
  "system" (`src/captains_log.py:412,437-444` — unknown types default to
  "system": the correct tolerant-reader shape); optional `context` dict
  (free-form), `note`, `loop_id`, `handle_id`, `related_ids` — all omitted
  when empty.
- **Status:** published (event-type list grows freely; that's the design).
- **Quirks:** the per-run slice is carved by BYTE OFFSET between run open
  and close, so a slice legitimately contains concurrent runs' rows —
  filter by `handle_id`/`loop_id`, never assume the slice is one run's
  rows (`src/captains_log.py:587-598`). `handle_id` is optional today
  (older rows lack it) — see C1.5.

### B9. events-and-notify v1 (`memory/events.jsonl` + the substrate hook)

- **Path:** `memory/events.jsonl` (mixed machine feed); escalation-class
  events additionally write one durable file each under
  `output/escalations/` (`src/notify.py:94-127,183-187`).
- **Writers:** `observe.write_event` — called per step/loop AND by every
  `notify.emit` (`src/notify.py:142-174`). Deliberately unlocked; every
  string field is capped (including `event_type`/`project`/`loop_id`/
  `status`/`model`) AND the ENCODED line is measured before write — on
  overflow the writer sheds `tool_pathologies`, then byte-budgets each
  field (`_truncate_encoded`, binary search on the json-encoded prefix)
  so line+LF provably fits <PIPE_BUF. (Was **DEFECT C0.3** — five fields
  uncapped, caps were CHARACTER caps while the obligation is BYTES
  (`json.dumps` ASCII-escapes, so 200 chars can be far more than 200
  bytes), and nothing measured the encoded line. FIXED 2026-08-28,
  `1e5c77fe`; <PIPE_BUF is now enforced, with a must-detect pin test —
  the C1.6 line-size pin ships with it.) Cross-cutting: `file_lock`
  itself reports lock timeouts and lock-create failures here
  (`src/file_lock.py:216-238`) — so this writer must never take a lock.
- **Readers:** polling substrates (official poll surface per
  `src/notify.py:20-29`), `maro-observe events` tail
  (`src/observe.py:565`), heartbeat health probes.
- **Shape:** `event_type` (open: step_done/step_stuck/loop_start/loop_done/
  run_completed/escalation/file_lock_timeout/...), `ts`, then capped
  projections: `goal`[:80], `project`, `loop_id`, `step`[:120], `step_idx`,
  `status`, `tokens_in/out`, `cache_read_tokens`, `model`, `elapsed_ms`,
  `detail`[:200], optional `tool_pathologies` (≤3, capped).
- **The hook half:** when config `notify.command` is set and event_type ∈
  `notify.events`, the command runs with the full payload JSON on stdin
  plus env `MARO_EVENT_TYPE` / `MARO_HANDLE_ID` / `MARO_STATUS` /
  `MARO_RUN_DIR` (`src/notify.py:189-214`). This is the substrate
  integration contract (`docs/SUBSTRATE_INTEGRATION.md`) — a Go engine
  must reproduce it SEMANTICALLY: same command resolution, same env
  variables, same payload keys, one JSON object on stdin. Byte-identical
  JSON serialization is NOT required (key order and whitespace may
  differ); hooks must parse, not string-match.
- **Status:** published. The projection caps are contractual (they are
  what makes the unlocked write safe), not style — fully enforced since
  C0.3, with the encoded-line measurement as the backstop.

### B10. playbook v1 (`playbook.md` + `playbook_history/`)

- **Path:** `<workspace>/playbook.md` (`src/config.py:78-80`); every
  curation/compression archives the prior full text to
  `playbook_history/` first (`src/playbook.py:557-583`).
- **Writers:** `playbook.append_to_playbook` (`src/playbook.py:422-556`,
  locked; evolver + graduation + canon door), `seed_playbook` (`:179`),
  `curate_playbook` (LLM compression, validity-gated, `:651`), the operator
  by hand.
- **Readers:** `inject_playbook` (`src/playbook.py:347`) via
  `recall` (`src/recall.py:855-859`) into director/decompose context;
  `parse_entries` (`src/playbook.py:317-346`) for census/adjudication.
- **Shape (a markdown grammar, line-anchored):** sections are `## <name>`
  lines; an entry is exactly ONE line starting `- ` (writers collapse
  embedded newlines — a multi-line entry could spoof a section header,
  `src/playbook.py:451-457`); entries ≤ ~500 chars; optional trailing
  provenance marker `*(from <source> · alarm <key> @YYYY-MM-DD)*` — the
  `alarm <key>` form marks a re-readable alarm that is REPLACED in place on
  re-fire and expires after 14 days without one
  (`src/playbook.py:230-234,248-315`). Unknown sections and non-entry
  prose lines are legal and preserved.
- **Status:** published. Entries are priors ("usually, do this"), not
  rules — that meaning is part of the contract (operator decree 2026-08-02,
  stated in the live file's header).
- **Quirks:** appends dedup on exact core-text match; compression must pass
  `_valid_compression` or is discarded; the history dir means no playbook
  text is ever truly lost.

### B11. handle-inputs v1 (`memory/handle_inputs.jsonl`)

- **Path:** `memory/handle_inputs.jsonl` — intake identity: one row per
  `handle()` call, pre-routing.
- **Writer:** `src/handle.py:1171-1191` (locked append).
- **Reader:** `rerun_identity` — the deterministic re-run/similarity brief
  (`src/rerun_identity.py:96-112`).
- **Shape:** `handle_id` str, `raw_input` str (verbatim, prefixes intact),
  `ts`; optional `dry_run` bool (present-and-true only), `origin` dict.
- **Status:** published (small, but a correctness source for re-run
  detection — a second engine accepting goals must write it).

### B12. Unregistered stores (the honest census)

The live workspace holds far more than B1-B11. As of 2026-08-28, `memory/`
alone carries ~44 distinct JSONL stores (plus checkpoints, projects/,
skills/, personas/, state files). Registered above: outcomes, lessons
(flat+tiered), captains_log, events, handle_inputs. Everything else is
**unregistered** — per rule A8 a second engine MUST NOT write any of these
until a session registers them; reading is at-own-risk (shapes are whatever
the Python writer currently does):

`task_ledger`, `knowledge_nodes`/`knowledge_edges`, `standing_rules`,
`decisions`, `skills`/`skill-stats`, `step-costs`, `step_traces`,
`inspection-log`, `suggestions`/`suggestion_outcomes`, `diagnoses`,
`change_log` (top + medium/), `interrupts`, `background-tasks`,
`hypotheses`, `mission-log`, `heartbeat-log`, `hook-log`, `imports`,
`calibration`/`preflight_calibration`, `canon_stats`, `constraint_log`,
`dynamic-constraints`, `evolver-baselines`, `lessons_archive`,
`navigator_lessons`, `persona-dispatch-log`/`persona-outcomes`,
`sandbox-audit`, `shadow_ledger`, `verification_outcomes`,
`module/memory_events`, plus backup dirs.

This list is a census snapshot, not a contract; its job is to make the
registry's coverage boundary explicit instead of implied. Promotion path:
a store becomes writable-by-both by getting its own B-entry, grounded in
its writer/reader code the way B1-B11 are.

---

## C. Findings: the upgrade queue

Ranked. "Sharpening" = expressible as a pure addition under rule A2.
Items marked *(judgment)* are my call, not a measured fact.

### C0. Coexistence prerequisites — verified defects (adversarial round 1, 2026-08-28)

Every item here was VERIFIED against the code by the 2026-08-28 codex
review round (2 reviewers, 8/8 ledger entries verified). These were not
sharpenings — they were places where the Python engine did not honor the
contract this doc states, and each one is a precondition for a second
engine sharing the workspace. **ALL EIGHT SHIPPED 2026-08-28** (commits
`010d3d11`, `9807fe6f`, `1e5c77fe`, `061fc10e`), each with a must-detect
test that fails on the pre-fix code. The corresponding B-section defect
notes are updated in place.

1. **Lesson-store RMW raw round-tripping** (tiered + flat — C2.1/C2.2
   promoted). — **SHIPPED `010d3d11`**: unknown keys ride rehydrated rows
   as an `_extras` attribute and every rewrite/promotion/archive/
   resurrection/clone path merges them back (declared fields win on
   collision). Chosen over raw-dict-patching rewrites as the smaller
   honest diff; applied identically to both stores. Unblocks C1.1's
   writer stamp — a stamp the next reinforcement strips is worse than
   none.
2. **`finalize_run` under `locked_rmw`** — preserve unknown keys, never
   replace the file with `{}` on parse failure (B3 defect note). —
   **SHIPPED `9807fe6f`**: same locked_rmw shape as the stamp_run_*
   family; unparseable bytes parked in a `metadata.json.corrupt` sidecar.
3. **events.jsonl line-size enforcement** — byte-cap every field
   (including `event_type`/`project`/`loop_id`/`status`/`model`), measure
   the ENCODED line, single `O_APPEND` write; pin test with must-detect
   fixtures (a field built to blow the cap) + negative control (B9
   defect note; extends C1.6). — **SHIPPED `1e5c77fe`** (C1.6's pin test
   included).
4. **Call-record seq allocation via exclusive create** (`O_EXCL` + retry
   on collision) so write-once becomes enforced, not conventional (B4
   defect note). — **SHIPPED `9807fe6f`**.
5. **Atomic captains-log rotation** — archive copy then `os.replace` of
   a fresh truncated file (or equivalent), so readers never see a
   half-rotated live log (B8 defect note). — **SHIPPED `1e5c77fe`** (via
   `file_lock.atomic_write`).
6. **Strict boolean config parse at the `file_lock` boundary** — the
   string `"false"` must not enable fail-open; and decide the
   create-failure posture explicitly (respect `fail_open`/`require`
   rather than silently proceeding unlocked) (B2 defect note). —
   **SHIPPED `1e5c77fe`**: create-failure mirrors the timeout branch
   (fail-closed default, loud fail-open), with its own events-feed
   report (`file_lock_create_failed`).
7. **Unknown `goal_verdict_source` → LEAST-privileged trust bucket**
   (rule A9's gates-behavior arm; C2.4). Ship with C1.2's
   `verdict_excluded` flag so the fix is additive, not a re-guess. —
   **SHIPPED `061fc10e`**: unknown sources route to
   `VERDICT_TRUST_EXCLUDED`; the known-source census lives in
   `memory_ledger._KNOWN_VERDICT_SOURCES` (add new sources there in the
   same commit that writes them); both judge choke points stamp
   `verdict_excluded: true` beside `closure_unverifiable`.
8. **Tolerant `RunRecord` loader** (C2.3) — filtered kwargs + defaults +
   logged rejection, the `tail_jobs.py:570-585` shape. — **SHIPPED
   `061fc10e`** (`orch_items._run_record_from_dict`; the silent-drop
   census row for `_load_run_records` is deleted).

### C1. Additive sharpenings

1. **Writer/engine stamp on written artifacts.** Nothing written today
   names its producer beyond `pid` (`src/runs.py:441-444`); a shared
   workspace with two engines can't attribute a malformed row or a shape
   regression to an engine. Add an optional `writer` field —
   `{engine: "python"|"go", version, schema?}` — to metadata.json extras
   (`src/runs.py:446-449` already merges extras), call records
   (`src/runs.py:1537`), outcomes rows and lesson rows. Readers need no
   change (unknown-key tolerance). Highest leverage per line of any item
   here — sequenced after C0.1/C0.2, which SHIPPED 2026-08-28: the lesson
   rewriters and `finalize_run` now round-trip unknown keys, so the stamp
   survives every rewrite. UNBLOCKED. Two stamp semantics, don't conflate
   them:
   `created_by` (first writer, set once) vs `last_written_by` (updated on
   every rewrite); note `metadata.json` extras merge via `setdefault`
   (`src/runs.py:446-449`), which makes a naive `writer` extra
   first-writer-wins — i.e. `created_by` semantics whether you meant it
   or not.
2. **Verdict-trust exclusion should be a flag, not a value match.** —
   **SHIPPED with C0.7 (`061fc10e`)**: rows accept an additive
   `verdict_excluded: true`; `verdict_trust` honors the flag before any
   value match, and both judge choke points (`record_outcome`,
   `stamp_outcome_verdict`) stamp it beside `closure_unverifiable`. A new
   exclusion-class source from either engine is safe by construction —
   and even an unstamped unknown source now lands in EXCLUDED, not FULL
   (C0.7 closed C2.4).
3. **Preserve off-vocabulary stop verdicts instead of erasing them.**
   `record_outcome` silently blanks an unknown `stop_verdict` /
   `pause_reason` at ingress (`src/memory_ledger.py:556-573`). Correct
   instinct (one vocabulary owner), destructive remedy: when a NEWER writer
   legally adds a value, an older engine's ingress destroys the evidence.
   Additive fix: keep the typed field blanked, add `stop_verdict_raw` with
   the dropped value. The vocabulary itself (`src/stop_verdicts.py:58-74`)
   becomes a registry-owned enum both engines import/generate from.
4. **One canonical `status` vocabulary, declared once.**
   `metadata.json.status` values are defined piecemeal:
   `src/stop_verdicts.py:80-119` (interrupt/finished sets) vs
   `run_curation`'s own `_PARTIAL_STATUSES`/`_FAIL_STATUSES`
   (`src/run_curation.py:77-78`). The unknown→"unknown" bucket
   (`src/run_curation.py:229-230`) is correctly tolerant, but a Go engine
   has to reverse-engineer the value set from two files. Declare it in
   `stop_verdicts.py` alone and in this doc; run_curation imports it.
   *(judgment: pure consolidation, no behavior change.)*
5. **Promote `handle_id` on captains-log rows optional→guaranteed.**
   The attribution gap is measured (only 60% of rows carried `loop_id`;
   `src/captains_log.py:587-598`) and the fix is already writing
   `handle_id` when a run context exists (`:599-605`). Once every writer
   runs inside a run context or stamps "" explicitly, promote it —
   optional→guaranteed is the allowed direction (rule A5) and byte-offset
   slices stop needing timestamp guesses.
6. **Write the PIPE_BUF line rule into the events contract.** — **SHIPPED
   with C0.3 (`1e5c77fe`)**: B9 states the rule, the writer enforces it
   on the ENCODED line, and the must-detect pin tests
   (`tests/test_observe.py`) assert every emitted line < 4096 bytes so a
   field added later can't silently break the atomicity assumption.
7. **Declare `_`-prefix as writer-private namespace in run cards** (B5
   does now): `_curation`/`_maintenance` are the curator's own state
   (`src/run_curation.py:1711-1717`). Naming the convention keeps a second
   engine from ever depending on them — and licenses each engine its own
   private keys without collision (e.g. `_go_curation`).
8. **Pin nullable-int for call-record token fields.** `tokens_in`/
   `tokens_out` are int-or-null (`src/runs.py:1544-1545`) and live records
   contain both. B4 pins it; readers written in Go must use `*int`, and
   neither engine may "clean it up" to always-0 (0 and null mean different
   things — same trap as `cost_usd` 0.0 ≠ free).

### C2. Intolerant readers (latent breakage today, coexistence blockers tomorrow)

Items 1-4 were promoted to C0 and are FIXED (2026-08-28); their text is
kept as the record of the defect class.

1. **Tiered lesson store: the canonical read-modify-write trap.** —
   **FIXED (C0.1, `010d3d11`).** `_mutate_tiered_lessons` rebuilt the
   whole tier from `asdict(TieredLesson)` after loading rows through a
   FILTERED constructor, so any field legally added by a newer writer
   (either engine) was silently stripped from every row on the next
   reinforcement, promotion, GC, or contest — store-wide, permanent.
   Fix shape chosen: carry unknown keys through rehydration
   (`_tiered_lesson_from_dict` stores them as `_extras` on the object;
   `_tiered_lesson_row` merges them back at every serialization site,
   `_clone_tiered_lesson` carries them across `dataclasses.replace`).
2. **Flat lesson store, same class.** — **FIXED (C0.1, `010d3d11`),
   same `_extras` pattern**: `_lesson_from_row` attaches unknown keys,
   `_verdict_row` restores them — covering `_rewrite_lessons_file`,
   `_reinforce_flat_row`, and `deduplicate_lessons`.
3. **`RunRecord(**data)` — unfiltered kwargs, then a silencer.** —
   **FIXED (C0.8, `061fc10e`)**: `_run_record_from_dict` filters kwargs
   and defaults required fields (the `tail_jobs.py:570-585` shape);
   `_load_run_records`' blanket except is now narrow
   (`OSError`/`ValueError`) with a logged reason per skipped file. One
   legal additive field no longer vanishes every run record from
   listings.
4. **Unknown verdict source ⇒ maximum trust.** — **FIXED (C0.7,
   `061fc10e`)**: the unknown-enum default now points at the LEAST
   permissive bucket (`VERDICT_TRUST_EXCLUDED`); known sources are
   enumerated in `_KNOWN_VERDICT_SOURCES` and keep their exact prior
   mapping. Paired with C1.2's `verdict_excluded` flag as designed.
5. **Ingress erasure of unknown enum values.**
   `src/memory_ledger.py:556-573` (detailed in C1.3): the reader half of
   `record_outcome`'s contract destroys a newer writer's legal
   `stop_verdict`/`pause_reason` value instead of preserving it as
   unknown. Latent cross-engine data loss, not a crash — harder to notice.
6. **`maintain_run_card` hard-requires its private structure.**
   `src/run_curation.py:1711-1717` raises `ValueError` unless the card
   carries `_curation` with exactly the three outcome lists. Mechanism
   corrected per review: today's only PRODUCTION maintenance caller builds
   its own card in-process first, so the strict check never sees a foreign
   card on that path — the real exposure is every path that RELOADS a
   card from disk and then treats its `_curation` as its own (audit
   repair, `src/run_curation.py:1767-1800`, and any future
   maintenance-on-reloaded-card). Latent, not live — which is exactly the
   kind of reader rule A9 says to fix before a second writer exists.
   Needs either the C1.7 namespace rule honored (skip maintenance when
   `_curation` is another engine's) or a graceful "not mine, rebuild"
   path.
7. **Alternate memory backend event replay.** `memory_jsonl._apply`
   (`src/memory_jsonl.py:88-93`): `MemoryItem(**d)` / `MemoryEdge(**...)`
   unfiltered — an added field drops the event ("skipping bad event") and,
   because this is a replayed event LOG, the item is missing from the
   rebuilt store forever after. Bake-off seam, not production
   (`MARO_MEMORY_BACKEND`), but it's the store a port is most likely to
   copy — fix before anyone does.

---

*Maintenance: this file follows the currency rule (repo CLAUDE.md) — a
session that changes any writer or reader named here updates the entry in
the same commit. New contracts the backbone grows (e.g. checkpoints,
knowledge nodes/edges if they become backbone) get entries; version tags
bump only on a published, deliberate shape change, which per rule A2 should
be rare and additive.*

*Versioning honesty: the `v1` tags above are DOC-REVISION markers — nothing
on disk carries them, so no reader can dispatch on them. A store gains a
wire-observable version only when a `schema_version` field (or the C1.1
writer stamp's `schema` slot) is actually written; until then, "v1" means
"the shape this doc describes as of its last update", nothing stronger.
When a wire version does ship, it is itself an additive optional field
under rule A2 — absent means v1 by definition, forever.*
