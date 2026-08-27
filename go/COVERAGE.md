# Where the port actually is

Measured 2026-08-26, on the `go-port` branch. Every number here is
computed, not remembered — the commands that produce them are at the
bottom, so this file can be re-derived rather than trusted.

This exists because "how far along is the port" has been answered from
memory for weeks, and the honest answer has two halves that point in
opposite directions. Both halves are below.

## The half that looks good

| | |
|---|---|
| Go packages | 46 |
| Production lines | 59,447 |
| Test lines | 81,834 |
| Test : production | **1.38 : 1** |
| Packages with a live CPython differential | **37 of 46** (56,870 lines — 95.7% of production) |

A "live CPython differential" means the test suite starts a real
interpreter, runs the Python function on the same input, and compares. It
is not a transcribed expectation. 25 packages go through the `pyprobe`
harness; 12 more shell out to `python3` directly.

The nine without one, and whether that is a gap:

| package | prod | why |
|---|---|---|
| `llm` | 937 | LLM adapters — the answer is a network call, there is nothing to compare |
| `workers` | 343 | one persona-framed LLM call; same |
| `planner` | 128 | LLM decompose; same |
| `pyprobe` | 275 | **is** the differential harness |
| `testenv` | 50 | test scaffolding |
| `selfimprove` | 111 | composition only — sequences packages that each have their own |
| `missionrun` | 97 | wiring only — **and it has no test file at all** |
| `recall` | 542 | **a real gap**: deterministic, read-only, one test file, no CPython comparison |
| `provenance` | 94 | **a real gap**: small, deterministic, 49 test lines |

So: two genuine differential gaps (`recall`, `provenance`), one package
with no tests whatsoever (`missionrun`), and seven where the absence is
structural rather than owed.

## The half that does not

| | |
|---|---|
| Python modules in `src/` | 183 |
| Python lines | 132,730 |
| Modules the Go tree references at all | 65 |
| Modules with **no Go reference anywhere** | **118 (59,962 lines, 45.2%)** |

"References" is the loosest possible test — the module's filename appears
somewhere in the Go tree, including in a comment. A module can be
referenced and barely ported. So 45.2% unported is a **floor**, not an
estimate: the true figure is worse, and nothing here measures how much
worse.

The largest untouched modules:

```
2,705  loop_report.py          1,110  quality_gate.py
2,135  run_curation.py         1,060  persona.py
1,676  container_exec.py       1,001  doctor.py
1,522  orch_bridges.py           871  agent_loop.py
1,231  navigator_shadow.py       843  skill_lifecycle.py
1,186  eval.py                   817  camera_readout.py
1,174  correspondence.py         797  audit_repair.py
1,172  web_fetch.py              768  harness_optimizer.py
1,149  tail_jobs.py              759  shadow_lane.py
```

`agent_loop.py` on that list deserves a flag: `internal/loop` ports the
loop's phases, but the top-level entry module itself is not referenced.

## What this means for "try using the port"

The two engines were compared on 2026-08-26 over the live workspace with
six read-only renderers: **6 identical, 0 differ**. That is real evidence,
and it is evidence about *read* paths on *one* box's data.

What it is not evidence about:

- **write** paths, which is where two runtimes sharing one store can
  corrupt each other rather than merely disagree (the harness for this is
  designed but unbuilt — `scratchpad/write_path_harness_design.md`);
- any of the 118 unported modules, which includes the container executor,
  the web fetcher, the persona system, and the quality gate;
- anything LLM-mediated, which is most of what the framework *does*.

The port is deep where it is deep. It is roughly half the surface, tested
harder than the original, and it cannot yet run a mission end to end.

## The standing question this does not answer

Jeremy, 2026-08-22: *"I'm questioning a little the wisdom of the port;
hopefully we're spending time to find meaningful edges, though feels a
little like one step forward and 3 back. Will reserve full judgement once
we actually try using the port."*

The "meaningful edges" half has an answer, and it is the strongest
argument the port has: the review ledger records the divergences found by
porting, and a large share of them are bugs or latent bugs in behaviour
**both** runtimes now share, found only because two implementations had to
agree. `review/findings.jsonl` is the record; `docs/REVIEW_PATTERNS.md`
computes the recurrence counts.

The "one step forward and 3 back" half is the table above, and it is a
fair complaint. 45% of the Python has no Go at all, and the tranches that
do exist keep taking four to seven review rounds each.

## Re-deriving these numbers

```bash
cd /home/clawd/claude/maro-wt-goport

# package table: prod/test lines and differential kind
python3 - <<'PY'
import pathlib
go = pathlib.Path("go/internal")
for d in sorted(go.iterdir()):
    if not d.is_dir(): continue
    tests = list(d.glob("*_test.go"))
    prod = sum(sum(1 for _ in p.open(errors="replace"))
               for p in d.glob("*.go") if not p.name.endswith("_test.go"))
    test = sum(sum(1 for _ in p.open(errors="replace")) for p in tests)
    kind = "none"
    for p in tests:
        t = p.read_text(errors="replace")
        if "pyprobe." in t:
            kind = "pyprobe"; break
        if "python3" in t and "exec.Command" in t:
            kind = "shell-out"
    print(f"{d.name:15} {prod:6} {test:6} {kind}")
PY

# python-side coverage floor
python3 - <<'PY'
import pathlib
src = pathlib.Path("/home/clawd/claude/maro-orchestration/src")
blob = "\n".join(p.read_text(errors="replace")
                 for p in pathlib.Path("go").rglob("*.go"))
mods = {p.stem: sum(1 for _ in p.open(errors="replace"))
        for p in sorted(src.glob("*.py"))}
un = {m: n for m, n in mods.items() if (m + ".py") not in blob}
print(len(mods), sum(mods.values()), "|", len(un), sum(un.values()))
PY
```
