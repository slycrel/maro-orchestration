# Views — the projector's mapping table (design note §13)

Every view the projector writes under `views/<generation>/` has a row: the
population it reads, the kinds it projects, the promise level, and what it
omits. A shared-spec view (a `docs/CONTRACTS.md` B-entry) must be EXACT to
its versioned wire contract; a successor-own view declares its own shape.

| view | population | kinds | promise | shape | omits |
|---|---|---|---|---|---|
| `thoughts.jsonl` | production | `thought_stored` | successor-own (no Python twin) | one JSON object per line: `seq`, `id`, `hash`, `kind`, `bytes`, `encoding`, `at` | header `run_id`, `attempt`, `subject`, `supersedes` (thoughts are workspace-scope) |

Watermark: `views/published` holds the highest Seq the `views/current`
generation reflects. Readers of the workspace edge read `current` and the
watermark; "committed but not yet published" is invisible to them by
design.
