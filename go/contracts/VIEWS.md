# Views — the projector's mapping table (design note §13)

Every view the projector writes under `views/<generation>/` has a row: the
population it reads, the kinds it projects, the promise level, and what it
omits. A shared-spec view (a `docs/CONTRACTS.md` B-entry) must be EXACT to
its versioned wire contract; a successor-own view declares its own shape.

| view | population | kinds | promise | shape | omits |
|---|---|---|---|---|---|
| `thoughts.jsonl` | production | `thought_stored` | successor-own (no Python twin) | one JSON object per line: `seq`, `id`, `hash`, `kind`, `bytes`, `encoding`, `at` | header `run_id`, `attempt`, `subject`, `supersedes` (thoughts are workspace-scope) |
| `outcomes.jsonl` | production | `run_transition` where `to=recorded` | **shared — CONTRACTS B6 outcomes-ledger v1, EXACT** | one B6 row per recorded attempt: `outcome_id` (8-hex, derived from the transition id), `goal` (the goal thought, whole), `summary`, `task_type`=`now` (B6 carries the lane there; `lane` is a B3 key and is NOT emitted), `status` (`done` for complete/partial, `stuck` for failed), `lessons` (the tail's lesson handles), `tokens_in`/`tokens_out`/`elapsed_ms`/`cost_usd`/`model`, `recorded_at`, `handle_id` (8-hex, derived from the run id); tri-state verdict per rule A6: `goal_achieved`/`goal_verdict_source`/`goal_verdict_confidence` present ONLY when the closure resolution is `achieved`/`not_achieved` (`unknown` = unjudged = keys absent); `goal_verdict_source` is `now_self_verdict` when the effective verdict is the agent's own claim, `closure` otherwise | every candidate verdict and the resolution rule (the Resolution record has them); the invocation/receipt ids; the delivery state (mission outcome is a separate fold, not a B6 column); `loop_id`, `failure_chain`, `recovery_steps` (no producer here); `measurement_class` (omitted until experiments, step 10) |
| `lessons.jsonl` | production (whole-file: the learn fold at the announced head, not a line per record) | `learned_revision` + `learned_transition` + `application`/`policy_application` (exposure counts) | **shared — CONTRACTS B7 lesson-stores v1, flat `Lesson` shape, EXACT** | one row per item whose CURRENT lesson revision is selectable or quarantined: `lesson_id` (8-hex, derived from the item id), `task_type` (the family key; empty = any), `outcome` `""`, `lesson` (the lesson_text thought, whole), `source_goal` `""`, `confidence` by stage (provisional 0.7 / effective 0.8 / canon 0.9 / contested 0.5 / quarantined 0.3 — the readers rank by it, so it orders as the ladder does), `times_applied` (exposures of the revision), `times_reinforced` 0, `recorded_at` (the revision's `at`, Python isoformat `+00:00`), `minted_from` `outcome` for tail-learned rows else absent, `contested{reason,source,contested_at}` on quarantined rows ONLY (kept on disk, excluded from injection — the B7 vocabulary for it), `imported{source,why}` on rows that entered through the pack | candidate and observed revisions (unproven; B7 has no non-injecting stamp that means "unproven" rather than "retired", and an unproven Go lesson must not become an injected Python one), tombstones (archive), policies (process data, never text), `goal_achieved`/`goal_verdict_source`/`grounding`/`merged_variants` (no producer here); the medium/long tiers (the Go ladder is the stage; there is no tier file). Location: `views/current/lessons.jsonl` — a Python reader is pointed at a COPY under its own `memory/` (design §13: separate roots, D10) |

## Not projected (declared)

| B-entry | why |
|---|---|
| B1 workspace-root, B2 write-discipline | a Go workspace is its own root (D10); the Python discipline governs the Python root only |
| B3 run-dir, B4 call-records, B5 run-card | run state is the journal + thought store; there is no per-run directory to mirror. The delivered answer reaches the operator through the delivery record, not a run card |
| B8 captains-log, B9 events | no producer: the journal IS the event log, and the captain's log is a Python-side narrative |
| B10 playbook, B11 handle-inputs, B12 unregistered | no producer |

## Carriers (not views)

The **native pack** (`maro-go pack export|import`, `internal/pack`) carries
what no B-entry can: revisions, transitions, exposures, experiments,
attestations — exactly as the source journal framed them, plus the cited
lesson_text thoughts by hash. It is Go-only causal history. An import
never adopts a stage: every current lesson of the source (not tombstoned,
not quarantined) enters HERE as a fresh candidate with `import`
provenance citing the source revision, idempotent per source revision.
`pack import-python <dir>` reads a Python workspace's three B7 tiers the
same way (highest tier wins per `lesson_id`; prompt-minted, contested and
provisional rows are skipped by name). No round trip is claimed.

Watermark: `views/published` holds the highest Seq the `views/current`
generation reflects. Readers of the workspace edge read `current` and the
watermark; "committed but not yet published" is invisible to them by
design.
