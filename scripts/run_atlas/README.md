# Run Atlas

A zoomable map of the whole orchestration process, with any individual run's
route overlaid on it.

    python3 scripts/run_atlas/extract_paths.py process_paths.json
    python3 scripts/run_atlas/build.py process_paths.json run_atlas.html

`extract_paths.py` walks every rundir in `$MARO_WORKSPACE/runs` (default
`~/maro-box-copy/workspace`) and reconstructs which pipeline nodes each run
touched. `build.py` inlines that JSON into `template.html`; the result is a
single self-contained page.

## What it is reconstructing

`LoopPhase` is never persisted -- `ctx.phase` is in-memory only. Every node in
the map is therefore *inferred* from artifacts that do persist: the loop log,
the captain's-log slice, `closure_verdicts.jsonl`, call records, and the run
card. The topology itself is declared in `template.html` (`PHASES` / `EDGES`)
with a code anchor on each node; when the pipeline changes, edit it there.

## Evidence strength

Nodes are marked attributed (`e:"a"`) or windowed (`e:"w"`). The captain's-log
slice is a byte range of the *global* log from the run's start offset to EOF,
so it carries concurrent runs' events and a post-`ended_at` learning tail.
Events carrying the run's own `loop_id` are attributed; events with no
`loop_id` that merely fall in the run's time window are windowed and render
dashed. Roughly 85% of `log_event()` call sites never pass a `loop_id`, so
windowed is common and is a real uncertainty, not a rendering detail.
