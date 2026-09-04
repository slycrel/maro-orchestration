# Views — the projector's mapping table (design note §13)

Every view the projector writes under `views/<generation>/` has a row: the
population it reads, the kinds it projects, the promise level, and what it
omits. A shared-spec view (a `docs/CONTRACTS.md` B-entry) must be EXACT to
its versioned wire contract; a successor-own view declares its own shape.

| view | population | kinds | promise | shape | omits |
|---|---|---|---|---|---|
| `thoughts.jsonl` | production | `thought_stored` | successor-own (no Python twin) | one JSON object per line: `seq`, `id`, `hash`, `kind`, `bytes`, `encoding`, `at` | header `run_id`, `attempt`, `subject`, `supersedes` (thoughts are workspace-scope) |
| `outcomes.jsonl` | production | `run_transition` where `to=recorded` | **shared — CONTRACTS B6 outcomes-ledger v1, EXACT** | one B6 row per recorded attempt: `outcome_id` (8-hex, derived from the transition id), `goal` (the goal thought, whole), `summary`, `task_type`=`lane`=`now`, `status` (`done` for complete/partial, `stuck` for failed), `lessons` (empty until the tail, step 9), `tokens_in`/`tokens_out`/`elapsed_ms`/`cost_usd`/`model`, `recorded_at`, `handle_id` (8-hex, derived from the run id); tri-state verdict per rule A6: `goal_achieved`/`goal_verdict_source`/`goal_verdict_confidence` present ONLY when the closure resolution is `achieved`/`not_achieved` (`unknown` = unjudged = keys absent); `goal_verdict_source` is `now_self_verdict` when the effective verdict is the agent's own claim, `closure` otherwise | every candidate verdict and the resolution rule (the Resolution record has them); the invocation/receipt ids; the delivery state (mission outcome is a separate fold, not a B6 column); `loop_id`, `failure_chain`, `recovery_steps` (no producer here); `measurement_class` (omitted until experiments, step 10) |

Watermark: `views/published` holds the highest Seq the `views/current`
generation reflects. Readers of the workspace edge read `current` and the
watermark; "committed but not yet published" is invisible to them by
design.
