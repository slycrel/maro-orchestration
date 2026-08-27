# Backlog — Deferred Items, Ideas, and Known Issues

Single canonical location for everything we've identified but haven't done yet.
Read this at the start of every session. Update it as items are completed or new ones emerge.

**Completed items live in [BACKLOG_DONE.md](BACKLOG_DONE.md)** — move items there with their full context when they ship; that file is the archive of what we've already decided, tried, or superseded, and it's ingested by `dev-recall` for historical context.

Checkbox conventions: `[ ]` open, `[x]` closed (often struck-through),
`[~]` partial/parked with a stated revive condition.

Last reviewed: 2026-08-11 (maintenance sweep, Jeremy's "history-cleanup
of the backlog" ask). 30 fully-closed `[x]` sub-blocks (766 lines) moved
verbatim to BACKLOG_DONE.md "Moved 2026-08-11 — maintenance sweep";
parent sections stay with their open remnants. Also fixed: the
risk-minting DECIDED/open contradiction, the 2026-08-04 stub reduced to
its standing trigger, stale count notes. ~5716 → ~4950 lines. Previous
pass: 2026-08-05 (below).

Previous review: 2026-08-05 (archiving pass, Jeremy's rider "reduce the
backlog's scope of done items", mid-LT-4). Moved to BACKLOG_DONE.md
"Archived from BACKLOG 2026-08-05" with context intact: the LT-1
round-by-round records (scoreboard + standing lessons + the open
provenance-vs-closure decision retained as a stub), the quality-gate
600-char arc, and five fully-shipped finds from 2026-08-02..04 (playbook
exits, dynamic-guardrail, un-contest stale-write, store round-trip probe,
slug disambiguation — one-line stubs with standing triggers retained).
5592 → ~4070 lines. Previous pass: 2026-07-27 (below).

Prior triage note, 2026-07-27 (archiving pass, Jeremy's ask — "consider
archiving on the backlog, I think it's getting pretty big"). The
accumulated triage-pass history (eighteenth through twenty-fifth passes,
incl. the VERIFY_LEARN V2–V5 narrative) and all fully-closed entries
moved to BACKLOG_DONE.md "Archived from BACKLOG 2026-07-27" with context
intact: R2/R3/R4/R5 reviews (all residuals closed), R6 (E watch-item
retained below), C4-BOX burn-in (flip stub retained), -1 Purgatorio r2
graduates, #22 shipped trail (residuals retained), DONE pointer stubs,
install-trial residuals (watch-item retained), launch-content arc (open
remainders retained), and the 2026-07-04 stale-drop record. Previous
full triage: 2026-07-04.

---

## Actionable Stack

Ordered open work that matters. Top of the list is next.

### DECISION NEEDED — the Go port's first hard ceiling: three modules parse Python with CPython's `ast` (FOUND 2026-08-26, go-port)

Not a divergence, a **boundary**. Three runtime modules parse Python source
with CPython's own `ast` module, and Go has no expression of it — not a
harder one, not a slower one, none.

| module | lines | `ast.` uses | what it parses for | reached from |
|---|---:|---:|---|---|
| `codebase_graph.py` | 492 | 18 | the call graph injected into planning context | `loop_planning` |
| `bughunter.py` | 345 | 10 | Maro's static self-scan | `doctor` |
| `artifact_check.py` | 735 | 5 | `_python_is_inert` — layer 2 of the fabrication check | `loop_execute`, `loop_planning`, `agent_loop`, `run_curation` |

Two of the three are on the **core loop**. `ast.parse` is not a library the
port can re-implement from a spec — it is CPython's grammar, its
version-specific node set, and its exact error behaviour, and
`_python_is_inert` branches on node TYPES (`ast.FunctionDef`, `ast.AnnAssign`,
`ast.Expr` wrapping an `ast.Constant`), which no regex approximates. A Go
reimplementation is a Python parser: a project, not a tranche, and every
version skew against the interpreter actually running the workspace's code
is a silent wrong answer inside a check whose whole purpose is catching a
fabricated claim.

**Three options, none free — Jeremy's call:**

1. **Leave these three in Python.** The port becomes a hybrid runtime with a
   named Python boundary. Cheapest; makes "Go replaces Python" a claim with
   an asterisk.
2. **Shell out** (`python3 -c`, results as JSON). Correct by construction,
   and re-introduces interpreter start-up on the core loop — the cost the
   port exists to remove.
3. **Substitute a cruder Go-side rule** (regex / tokeniser). Not a port: a
   different check that agrees most of the time. L46 in its purest form,
   with an unbounded divergence count.

**Not blocking now, and SMALLER than first stated (measured 2026-08-26).**
The first version of this entry said layer 2 was out of scope. Capturing the
ground truth says something better: **layer 2 is portable except for the one
predicate.** Patching `_python_is_inert` with a scripted per-file answer lets
a probe measure the whole of `_python_candidates` and `_inert_output_verdict`
— the two-part claim gate, candidate collection, dedup by resolved path, all
three fail-open returns — with `ast` nowhere in the picture. 191 fixtures
were captured that way before any Go was written.

So the Go seam is an injected predicate,
`IsInert func(source string) (inert bool, known bool)`, and roughly **twenty
lines** of CPython stay un-portable, not 735. The boundary is still real and
the decision is still yours; what changes is its size. Option 2 shrinks to
one question about one file rather than a module living behind a subprocess,
and option 1 leaves a predicate in Python rather than a check.

The full write-up is in `go/PORT.md`.

### system_health.render_snapshot dies on a hand-edited snapshot (FOUND 2026-08-26, go-port system_health slice 1)

`memory/system_health.json` is operator-editable by design — it is the seed
of the maro-level systemic-metadata store, and `_history_of` goes out of its
way to tolerate a hand-edit (a non-list `history`, non-dict entries inside
one, all dropped silently). `load_snapshot` is the same: five different
kinds of corruption all collapse to "no snapshot yet".

`render_snapshot` is not. Measured against CPython 3.14:

| edit | result |
|---|---|
| `"processes": [ ... ]` | `AttributeError: 'list' object has no attribute 'items'` |
| `"processes": {"a": "note to self"}` | `AttributeError: 'str' object has no attribute 'get'` |
| `"processes": {"a": {"status": []}}` | `TypeError: cannot use 'list' as a dict key (unhashable type: 'list')` |

The third is the one nobody would guess: the sort key is
`order.get(p.get("status"), 3)`, a DICT LOOKUP, so an unhashable status is a
raise rather than a miss that falls to rank 3.

So `python3 -m system_health` — the only way an operator reads this file —
tracebacks on exactly the input the rest of the module was written to
survive. And the CLI's `--probe` path runs the cycle FIRST and renders
second, so a corrupt entry also means the operator never sees the report of
the cycle that just ran.

Reproduced faithfully in the Go port (fixtures R11, R12, R14, R15, R16) and
filed rather than fixed, because "make the renderer tolerant" is a Python-
side decision with a UX question in it. Three candidate resolutions:

1. **Tolerate, like the readers do** — a non-dict entry renders as
   `[?] name — <unreadable entry>`. Consistent with `_history_of`, and the
   operator sees which key is broken.
2. **Fail with a pointer** — catch and print
   `snapshot is malformed at processes.<key>: <repr>`, since a traceback
   from a status CLI is never the right output.
3. **Leave it.** The snapshot is machine-written; a hand-edit that breaks it
   is the operator's problem and a traceback names the line.

Worth noting the module already has an opinion: everything else here
tolerates. The renderer is the outlier, and it is the only part an operator
touches.

### sheriff.check_project can never return "warning" (FOUND 2026-08-26, go-port sheriff slice 1)

`SheriffReport.status` is documented as
`healthy|warning|stuck|dormant|failed|paused|unknown`, and `check_project`
has two branches that assign `"warning"`. **Neither is reachable.**

The proof is three lines of the function:

- `no_artifacts` is appended only `if doing_items:`
- `artifact_stale` is appended only `if age_min > window and doing_items:`
- `items_stuck_doing` is appended whenever `doing_items`

So any run that records a stall problem has also recorded
`items_stuck_doing`, and the status ladder tests
`"repeated_decisions" in problems or "items_stuck_doing" in problems`
FIRST. The stall branch and the catch-all `else` below it are both dead.
Confirmed empirically as well: the 41-fixture CPython ground-truth pass for
this chunk reaches `healthy`, `stuck`, `dormant`, `failed`, `paused` and
`unknown` — six of the seven — and no fixture reaches `warning`, including
the ones written specifically to try (an empty `artifacts/` with items in
progress comes back `stuck`).

Consequences worth deciding on, not just noting:

- An operator reading `docs`/`--help` is told to expect a status the system
  cannot emit, and `maro sheriff --all` output can never show it.
- The stall diagnosis text — *"items in progress but no recent artifact
  activity"* — is strictly more informative than the loop text that
  displaces it, and it is the one that never prints.
- Anything downstream branching on `warning` (dashboards, the heartbeat
  rollup's consumers) has a dead arm.

**Three candidate resolutions**, none taken:

1. Accept and document: drop `warning` from the status vocabulary for
   projects, and delete both branches. Cheapest, loses the diagnosis text.
2. Reorder the ladder so a stall outranks a bare `items_stuck_doing`, and
   keep `stuck` for the repetition signal. Changes live behaviour on this
   box — every currently-`stuck` stalled project would start reporting
   `warning`, which is a real change to what heartbeat escalates.
3. Stop recording `items_stuck_doing` when a stall problem is also present,
   so the two are mutually exclusive by construction.

**This is a Python-side defect, not a port defect.** The Go port reproduces
the dead branches deliberately (with the reasoning at the site) rather than
"fixing" them, because a port that silently emits a status the original
cannot is the worse bug. Whichever resolution wins has to land in
`src/sheriff.py` first and be re-ported after.

### Python-side: the fabrication check misses any write claim that names a file BEFORE the preposition (FOUND 2026-08-26, artifactcheck slice 1 — MEASURED)

`_OUTPUT_CLAIM_RE` spells the gap between the verb and the preposition as
`[^.\n]*?`. A period in that window ends the match. Since the window is
exactly where a source filename or a version number lives, the most
natural phrasing an agent uses when it names both a source and a
destination is not detected as a write claim **at all** — which means
`check_fabrication` never runs on it, and a fabricated write goes
unchallenged.

Measured against `src/artifact_check.py` on CPython 3.14.3:

```
'I saved the report to output/results.txt'      -> ['output/results.txt']
'I saved report.md to output/results.txt'       -> []
'Wrote the summary.json contents to final.json' -> []
'I saved results v1.2 to out.txt'               -> []
```

The first line is the only one of the four that a reviewer would call the
unusual phrasing.

**PYTHON-side, filed not fixed.** The Go port reproduces the gap exactly
(fixture E36) and must keep reproducing it until the Python changes — a
port that detects claims the original ignores would report fabrication the
production system does not. The fix is a Python decision about the window:
either allow a period inside it (and accept the false positives that
sentence-crossing brings) or bound the window to the clause. Whichever
wins lands in `src/artifact_check.py` first and is re-ported after.

### Python-side: the stdout exclusion list is a bare prefix alternation (FOUND 2026-08-26, artifactcheck slice 1 — MEASURED)

`_STDOUT_CLAIM_RE`'s exclusion arm is `(?:file|to|into|path|dir)` with no
trailing word boundary, so it suppresses on a PREFIX, not a word. Measured:

```
'output to stdout'  -> stdout claim: True
'output tomorrow'   -> False      ("to" prefix)
'output files'      -> False      ("file" prefix)
'output directory'  -> False      ("dir" prefix)
'output pathology'  -> False      ("path" prefix)
'output intoxicated'-> False      ("into" prefix)
```

Every line but the first is a suppression the list did not intend. This
one is much lower stakes than the entry above — it only affects whether a
claim is classified as *stdout* rather than as a file write — but it is
the same species and should be decided in the same pass.

**PYTHON-side, filed not fixed**, same reasoning: the port reproduces the
prefix behaviour, and a mutation that adds `pytext.WordEnd` to the Go arm
is caught by the fixture table, which is how it was found.

### Go port: `CheckExecutionClaim` has no caller, and its transcript decode is a contract nothing enforces (FOUND 2026-08-26, artifactcheck r2)

`internal/artifactcheck.CheckExecutionClaim` is fully ported and fully
differentialled, and nothing in the Go tree calls it. When a caller is
written, HOW it decodes the tool transcript is part of the answer:

```
                       Python json.loads      Go encoding/json
{"command": 5}         int    -> str "5"      float64 -> "5.0"
{"command": {"a": 1}}  dict   -> "{'a': 1}"   map     -> refused by pyval.Str
```

`asMapping` accepts `map[string]any` because `isinstance(te, dict)` does —
that part is right and is r1's F1. But accepting the SHAPE is not accepting
the value TYPES, and the fix for one quietly reads as a blessing for the
other. The caller must decode Python-typed (`jsonx.ObjectOrdered` /
`pyval.Obj`), and today nothing says so except a doc comment.

X25-X28 pin the rendering for int / float / list / bool, so the port is
correct for every input it is GIVEN correctly. The open item is the
enforcement, which belongs with the caller and not before it.

**r3 update (2026-08-26): the doc comment was not merely unenforced, it was
CONTRADICTED.** `asMapping` did not accept `pyval.Obj` at all — it is a
slice and matched neither type-switch arm — so a transcript decoded exactly
as the paragraph above demands answered `judged=true, "no execution in
transcript"`, a clean bill for a fabrication. Fixed, and X29-X33 now drive
the prescribed spelling. The open item stands unchanged: nothing in the
tree calls the function, so nothing enforces the decode at a boundary. When
a caller lands, the decode is part of its review, not an afterthought.

### Go port: 33 sites create directories 0755 where Python creates 0775 (FOUND 2026-08-26, census)

`Path.mkdir()` creates with `0o777 & ~umask`; `os.MkdirAll(p, 0o755)`
creates with `0o755 & ~umask`. **The umask on this box is 0002**, so the
Python engine writes 0775 and the Go engine writes 0755 — group write,
present in one runtime and absent in the other, on every run directory,
project directory, memory directory and pack import target the port
creates through a hardcoded literal.

`internal/record/filemode.go` already carries the right answer as
`NewDirMode` (0o777, with MkdirAll applying the umask), and 28 call sites
use it. 33 do not, plus 7 more that spell the value `0o777` inline and are
right by accident. The full site list, the Python-side counts (185 default
`mkdir`, 3 default `makedirs`, exactly ONE deliberate `mode=0o700` in
`src/web_fetch.py:494`), and the reason this is not a `sed` are in
`scratchpad/dirmode_census.md`.

Not a sed because the mode must be justified against the PYTHON call each
site ports. `web_fetch`'s cache dir is 0700 on purpose; a blanket rewrite
would widen it when that module lands.

Nothing in the suite can catch this class: the differential fixtures
compare returned VALUES, and a directory's mode is a property of the
filesystem afterwards that no probe reads back. The write-path comparison
harness would close the whole class in one assertion.

### Python-side: CPython's two `fromisoformat` implementations disagree (FOUND 2026-08-26, artifactcheck r1 — L47)

`datetime.fromisoformat` has a C accelerator in `_datetimemodule.c` and a
pure-Python parser in `_pydatetime.py`, and they are not the same parser.
Measured on CPython 3.14.3, three inputs where they part:

```
"2026-08-22T12:34:56.Z"          _pydatetime: ValueError   C: 12:34:56+00:00
"2026-08-22T12:34:56.123456_Z"   _pydatetime: ValueError   C: ...123456+00:00
"2026-08-22T12:34:56_Z"          _pydatetime: ValueError   C: 12:34:56+00:00
```

All three are the fraction/stray-character corner. The C one is what runs on
every build this project will ever see, so the port matches the C and the
differential drives the C — but the readable source being wrong is worth
having written down, because reading `_pydatetime.py` is the obvious way to
port this function and it produces a parser that is wrong in a direction no
review would question.

**Nothing to fix here.** Filed as a standing note for the next person who
ports a datetime builtin: `python3 -c "import _datetime"` succeeding means
the readable source is not the specification (L47).

### Go port: the naive-datetime residuals `parseISO` reproduces narrowly (FOUND 2026-08-26, artifactcheck r1)

Two named limits in `isoTimestamp`, both measured, both currently
unreachable from this project's own producers:

- A NAIVE `0001-01-01` has no `.timestamp()` at all — CPython's `_mktime`
  probes the local UTC offset by constructing neighbouring datetimes, and
  one of them lands in year 0. `0001-01-02` onward is fine, and the AWARE
  form of the same instant is fine. The port reproduces the single failing
  date rather than modelling `_mktime`'s probe.
- Nothing in the port consults `fold`, so a naive stamp inside a DST repeat
  hour takes CPython's fold=0 answer by construction rather than by choice.
  (CORRECTED 2026-08-26 by artifactcheck r2: this row originally also
  claimed the repeat hour was unmodelled. It is not — the two runtimes
  agree there. The DST *gap* was the divergence, by exactly 3600 s, and it
  is fixed in `pyMktime` with W20 pinning both transitions.)

Every producer feeding this is aware — `datetime.now(timezone.utc)` at
checkpoint.py:346, background.py:169, interrupt.py:821, mission.py:1274,
proc_lock.py:92 — so the naive branch exists for hand-written stamps and old
data only. If a naive producer ever appears, both of these stop being
residuals and the `_mktime` probe has to be ported properly.

### Go port: `internal/missionrun` has no test file at all (FOUND 2026-08-26, go-port coverage census)

A per-package census of the Go tree — Go lines vs test lines, counted in
Python after a first shell attempt reported a nonsense ratio for this very
package (a `*_test.go` glob leaked through as a literal) — surfaced one
package with **zero** test files. `internal/missionrun` is not a stub; it
carries real behaviour and is imported by the command layer.

Every other package in the tree has at least a differential or a pin. This
one has neither, which means nothing in it is compared against CPython and
nothing in it would notice a revert. It is the only place in the port where
the honest coverage claim is "none".

**Not a mechanical fix.** The work is a probe + fixture table like the ones
the other packages carry, and it should be scheduled as its own slice rather
than bolted onto whatever chunk notices it next.

### Python-side: `artifact_check.py`'s three-literal token guard has a dead clause (FOUND 2026-08-26, artifactcheck slice 1 — L4)

`extract_write_claims` guards its token with:

```python
if not tok or tok in ("/", "./", "../") or tok.endswith("/"):
```

All three literals end in `/`, so the middle test can never be the reason a
token is rejected — the `endswith("/")` immediately after it subsumes every
one of them. Found by mutation, not by reading: deleting the arm from the Go
port left all 191 fixtures green, which is the only way a subsumed clause
announces itself.

**PYTHON-side, filed not fixed.** The port keeps three tests because the
Python has three; a port that "cleans up" a dead branch is a port whose
diff no longer explains itself. If the Python is ever tidied, the port
follows — not the other way round.

### Go port: `artifact_check.py` slice 2 — the scavenging detector (~250 lines)

Slice 1 ported lines 1–483 (the fabrication check: write-claim extraction,
the two regexes, the snapshot/compare walk, `check_execution_claim`). The
package doc names the cut. What remains is the out-of-fence access
detector — a separate mechanism with its own regex family:

- `_in_fence`, `fence_allow_roots`, `goal_declared_roots`
- `detect_out_of_fence_access`
- `_ABS_PATH_RE`, `_BASH_CD_RE`, `_BASH_REL_WRITE_RE`

The bash-command regexes are the risk: they are the third place in the port
where a Python pattern has to survive translation to RE2, and the first two
each produced a finding. Budget a probe-first pass (L49) before any Go.

**The lookbehind blocker is SOLVED on paper (2026-08-26), design in
`scratchpad/artifactcheck_slice2_design.md`.** All three lookbehinds
(`_ABS_PATH_RE`, `_BASH_REL_WRITE_RE`, `_GOAL_PATH_RE`) are fixed-width
single-character negative classes, and the two obvious translations are
both wrong. Consuming the preceding character advances the scan one
position too far. Scanning the core pattern and FILTERING in Go is worse:
Python's engine RETRIES one character later rather than dropping the
position, and a later start inside the same region can succeed — measured,
not reasoned:

```
'x/a@/b'     python ['/b']       core scan ['/a@/b']
'e/f@/g@/h'  python ['/g@/h']    core scan ['/f@/g@/h']
```

The class is non-empty because `@`, `+` and `-` may appear IN a path
component and may not appear BEFORE one. So the filter translation loses a
real fence escape. The answer is a retry loop that reproduces the engine
(`p = start + 1` on a lookbehind failure, `p = end` on a match), with the
lookbehind and the Unicode `\b` in `\btee` both spelled in Go via
`pytext.IsWordChar` rather than in the RE2 pattern.

### Go port: the `projects/` half of the mkdir-inside-a-name family (FOUND 2026-08-26, go-port chunk A scoping — L48)

Chunk A ported the side effect for the **memory** family only:
`config.memory_dir()` mkdirs, so `EnsureMemoryDir` does too, and the
callers that stand for a Python `memory_dir() / name` line now carry it.
Five of `config.py`'s seven path helpers behave this way — `memory_dir`,
`output_dir`, `projects_dir`, `skills_dir`, `personas_dir` — while
`secrets_dir` and `playbook_path` do not.

**PARTLY DONE.** The three ENUMERATORS now create, each measured against
CPython (`internal/orch/projectsdir_diff_test.go`, three workspace
shapes):

```
fresh workspace   py list_projects() -> []   projects/ EXISTS after
                  go (before)        -> nil  projects/ did not
shadowed by file  py list_projects   raises FileExistsError
                  py list_missions   raises FileExistsError
                  py resolve_project_slug  SWALLOWS, returns its slug
```

That last row is why the differential measures all three together: a test
covering only `list_projects` would have licensed "propagate everywhere"
or "best-effort everywhere", and both are wrong for two of the three.
`ListProjects` propagates, `ListMissions` answers empty (no error
channel — the named residual, filed with introspect's two), and
`SlugResolver` is best-effort.

**STILL OPEN — `ProjectDir` and its path family.** In Python
`project_dir(slug)` is `projects_root() / slug`, so resolving ANY
per-project path creates `projects/`. In the port `ProjectDir`,
`NextPath`, `DecisionsPath`, `RisksPath`, `ProvenancePath` and
`PriorityPath` are pure joins — **32 non-test call sites plus 6 test
files**, which is a signature change of its own and was deliberately not
ridden in on the enumerator slice. Pinned meanwhile by
`TestProjectDirIsStillAPureJoin`, so it is a recorded fact rather than a
comment asserting one.

Reachability is narrow and worth stating: the divergence is confined to
READ-ONLY paths. Any caller that goes on to write creates the
per-project directory with parents, and `projects/` appears anyway.

Also still open: **three** dead `.exists()` guards, not one — an **L4**
family. `orch_items.list_projects`, `sheriff.check_all_projects` and
`mission.list_missions` each call `projects_root()` and then test
`if not <root>.exists()`, which cannot fire because the line above just
created the directory. The port reproduces all three, now dead for the
same reason rather than by accident, and each is named at its site.
Decide whether reproducing them stays the posture — but do not silently
drop them.

`output_dir` / `skills_dir` / `personas_dir` are not yet surveyed at all.

**`internal/missionrun` has NO test files.** Surfaced by battery mutant
PJ-6, which reverts `SlugResolver` to a pure join and survives by
construction — there is nothing in that package to catch anything. It was
left in the mutation list and left surviving rather than trimmed, because
a list pruned to what the suite already covers cannot report a hole like
this. Any package with `[no test files]` is in the same position; that
census has not been run.

### Go port: two JSON emitters, and the second one knows it (FOUND 2026-08-26)

`internal/pyjson` and `internal/pyval` both exist to be THE emitter that
stops per-package JSON drift, and each says so in its header. pyjson owns
the single-line JSONL lane (`Ordered`, `Value`, `String`, ensure_ascii,
HTML-off, float spelling). pyval owns the indent-2 sidecar lane
(`DumpsIndent2`, `DumpsIndentN`, `DumpsIndentNSorted`, `DumpsIndent2Raw`)
plus the ordered `Obj`/`List` carrier — and it calls into `pyjson.Value`
for scalars, so the split is not clean either.

This is already known and deliberate. `pyval.go`'s own header:

> WHERE THIS BELONGS: internal/pyjson, next to Ordered. It is parked here
> because pyjson was under adversarial review when the project ledger
> needed it, and moving a file someone is reviewing is how a round's
> findings stop landing against the thing that was reviewed. `pids.go` had
> already rolled its own indent renderer for one specific shape, so this
> is the second instance — which is the threshold at which the duplication
> stops being cheap. Both fold into pyjson together.

pyjson is no longer under review, so the parking reason has expired.

**The cost is measured, not hypothetical.** Writing the sheriff slice-2
design note, I grepped `internal/pyjson` for indent support, found none,
and concluded slice 2 had to add it to a shared package. I then wrote the
whole renderer — layout struct, empty-container rule, item-separator rule,
a CPython differential, a 12-mutation battery, 12/12 caught — before
discovering `pyval` had shipped the same operation months earlier, already
differential-tested with its own anti-vacuity guard. An hour, and the
duplicate was written by the person writing the note that warns about
duplication (L14, one level up).

The transferable rule, worth more than the fold itself: **grep the TREE
for a capability, not the package you expect to find it in.** The design
note now carries the correction.

Not urgent — nothing is wrong on disk, both lanes are pinned against
CPython. The fold is a tidy-up with a real prerequisite: `pyval.Obj` is
the ordered carrier and `pyjson.Ordered`'s map+modeled-keys signature is a
different shape, so folding means picking ONE carrier, which is a
decision, not a move.

### Go port: the directory-MODE census — 34 sites still pass a literal `0o755` (FOUND 2026-08-26, go-port chunk A)

`record.NewDirMode` is `0o777`, which is what `Path.mkdir()` passes, and
the umask narrows it — 0o775 on this box, 0o755 under a service with
umask 022, exactly as Python would. A Go site that hard-codes `0o755`
produces 0o755 **regardless of umask**, so the two runtimes disagree on
any host whose umask is not 022, and the difference is observable
(`stat`, and a group-writable workspace shared with the Python runtime).

Chunk A converted two sites as a side effect of the work it was already
doing (`metrics/recorder.go`'s second MkdirAll, and the drain-lock's own
mkdir, which was deleted rather than converted). **34 remain**, found by
`grep -rn "MkdirAll" go/ | grep 0o755`.

Not a mechanical sweep: each one needs the same per-site question the
memory family got — does the Python line it ports call a helper that
mkdirs at pathlib's default, or does it pass an explicit mode? Some
Python sites DO pass `0o755` deliberately. A blanket replace would be a
guess dressed as a fix, and the mutation battery would not catch it
because both modes create a working directory.

### Go port: two path readers swallow a failure their Python twins raise (FOUND 2026-08-26, go-port chunk A — named residual)

Widening `MemoryDir` to a `(string, error)` pair pushed an error return
into every caller, and three of them have nowhere to put it:

- `introspect.LoadLoopEvents` returns `nil` — CPython's
  `_load_loop_events` has **no** `try` and raises.
- `introspect.LatestLoopID` returns `"", false` — same, `_load_latest_loop_id`
  raises.
- `orch.IsDrainRunning` returns `false` — CPython raises out of
  `is_drain_running`. A Go bool has no third state, and `false` lets a
  SECOND drain start on a workspace whose `memory/` cannot be created.

All three are pinned by tests (`memorydir_diff_test.go`'s
`TestTheDrainLockPredicateForksOnAnUncreatableMemoryDir` and the
introspect store's per-site comments), so the divergence is a recorded
fact rather than an omission. `metrics` is deliberately NOT on this list:
`spend_today`, `spend_for_loops` and `load_step_costs` each wrap their
whole body — path helper included — in `except Exception`, so the port's
`return 0` / `return nil` is faithful. That was **checked, not assumed**;
an earlier note in the same file claimed the opposite.

The fix is to widen the two `introspect` signatures. Cheap, but it
touches every caller and belongs in its own chunk rather than riding in.

### Go port: an escaped lone surrogate is a STATE divergence, not a byte one (FOUND 2026-08-26, tasks r1 HIGH)

`pyval`'s existing lone-surrogate residual describes the `ensure_ascii=True`
writer, where CPython re-escapes `\ud800` and the write succeeds — a byte
difference. On an **`ensure_ascii=False`** writer (which `task_store` is)
CPython must actually UTF-8-ENCODE the string, and a lone surrogate cannot
be encoded. Measured on 3.14.3, driving `fail("t1")` over a row whose
`note` is `"x\ud800y"`:

| | CPython | Go |
|---|---|---|
| the verb | `UnicodeEncodeError` | succeeds |
| the row | byte-identical, still `claimed` | rewritten, now `failed` |
| the field | untouched | `x\ufffdy` — original bytes gone |

So the two runtimes disagree about whether the verb RAN, about the task's
state, and about the row's contents — and this runtime's write is
unrecoverable. That is a different class of problem from the cosmetic
residual already recorded.

**The fix** is the one `pyval.go` already names: decode string tokens with a
surrogate-preserving decoder AND teach `encodeString` to re-emit `\udXXX`,
so the encoder can refuse the way CPython's does. It is a rewrite of that
file's two ends, which is why it is here and not done.

**Not** a guard in `readRaw`: CPython's READ succeeds, so refusing at read
time breaks `list_tasks` and `status_summary` — a third behaviour, matching
neither runtime.

Pinned by `internal/tasks`' `TestAnEscapedLoneSurrogateIsANamedDivergence`,
which measures CPython every run and fails loudly in EITHER direction: if
CPython changes, or if this port stops diverging.

### Go port: `json.Number` non-finite literals — decide allow_nan per writer (FOUND 2026-08-26, tasks r1)

`pyjson.Value` now re-renders float literals through `FloatRepr` (the tasks
r1 HIGH fix), which closed `2.50 -> 2.5`, `1E5 -> 100000.0` and the rest.
One arm is deliberately left alone: a literal that parses to inf or nan is
still ECHOED verbatim, so `1e400` stays `1e400` where CPython's
`json.dumps` writes `Infinity`.

Both are valid JSON and both read back as `inf` in Python, so nothing is
lost today — but the adjacent `float64` arm REFUSES non-finite instead, and
having two answers in one function is a decision waiting to be made. The
real question is per-WRITER: `json.dumps` defaults to `allow_nan=True`, and
which of this port's writers should carry `allow_nan=False` has never been
enumerated. Do that enumeration first; the code change is small either way.

### Go port: `pyval` has no big-int lane (FOUND 2026-08-26, SystemMetrics chunk)

CPython's `int` is arbitrary precision. `pyval.Plain` resolves an integral
JSON literal through `ParseInt`, which is int64, so **any stored integer
past 2^63 silently becomes a float64**. It is not a rendering bug in one
consumer — every reader in the port that goes through `Plain` has it.

Measured, live: a `total_tokens_in` of `100000000000000000000` renders
`100,000,000,000,000,000,000` under CPython's `{:,}` and `1e+20` under the
port, because the value reached the float lane. Token counts are the
reachable case (a corrupt or synthesised stamp), but ids and byte counts
are the same shape.

Not worked around in the renderer on purpose — a local patch there would
hide the hole from every other reader that has it. Pinned by
`TestWideIntegerTokenTotalIsANamedDivergence` in `internal/metrics`, which
goes red the day a big-int lane lands.

Open question when someone picks this up: whether the lane is
`*big.Int` in `Plain`'s output (which every type switch in the port then
has to handle) or a narrower "keep the literal" value that only the
formatters understand. The second is cheaper and leaves arithmetic
broken; the first is correct and touches everything.

### Go port: port CPython's `list.sort` to `pyval` (FOUND 2026-08-26, metrics r4 finding 1)

Go has no sort that reproduces CPython's when the comparator is
INCONSISTENT, which is what a NaN makes it. Measured over 153 NaN-bearing
lists: `sort.Float64s` disagrees with `list.sort()` on 136,
`sort.SliceStable` with a `<` comparator on 78. There is no cheap spelling
that agrees — the answer is a faithful timsort (binary insertion below the
minrun threshold, then the merge policy), which is a bounded, highly
testable piece of work: the corpus above already exists at
`scratchpad/nan_sort_cases.json` and can be regenerated at any size.

TWO consumers are already waiting, which is what lifts this above a
curiosity:

1. `computeRunCostP90` — a run card with a NaN `total_cost_usd` (writable:
   `json.dumps` emits bare `NaN` by default and `json.loads` accepts it)
   moves the p90 by one index. Measured on 8 cards `1..7, NaN`: CPython
   answers 7.0, the port answers 6.0. Pinned as a NAMED DIVERGENCE by
   `TestRunCostP90NaNSortIsANamedDivergence` — that test goes RED when this
   lands, which is the signal to delete it.
2. `format_metrics_report` (NOT YET PORTED) sorts `by_model` on `-cost`,
   where a NaN lands in the MIDDLE: `[ma=nan, mb=1.0, mc=5.0]` renders
   `mc, ma, mb`. The next chunk hits this immediately.

Do it before the SystemMetrics chunk, or that chunk ships its own named
divergence for the same missing primitive.

### Go port: `pyval.DumpsStruct` refuses values `json.dumps` accepts, and the row is silently dropped (FOUND 2026-08-26, metrics r4 finding 3)

`RecordStepCost` does `line, err := pyval.DumpsStruct(row); if err != nil {
return row }` (`internal/metrics/recorder.go:181`). CPython's
`json.dumps` has no refusal for these inputs, so Python writes a row where
Go writes NOTHING and reports success by returning the built row.

Two measured triggers:

- a non-finite `cost_usd`: `record_step_cost(..., provider_cost_usd=inf)`
  appends a 16-key row with `"cost_usd": Infinity` in CPython. Reachability
  is thin but not closed — `step_exec.py:1690`, `loop_blocked.py:533`,
  `loop_parallel.py:195` and `loop_execute.py:1937` all reach
  `provider_cost_usd` through a bare `float(...)` with no non-finite guard.
- INVALID UTF-8 in `StepText`/`Goal` (e.g. raw subprocess stdout): CPython
  writes `"caf\udce9"` and never refuses on string content.

The fix is a `pyval` contract decision, not a metrics one: give
`DumpsStruct` an `Infinity`/`-Infinity`/`NaN` lane and a lone-surrogate
lane matching `json.dumps`. Same shape as the filed `LoadsMap`
skip-vs-abort question — decide the two together, since both are "the port
refuses where CPython proceeds".

### Go port: `maro introspect` never writes its captain's-log DIAGNOSIS event (FOUND 2026-08-26, go-port r4)

`introspect.diagnose_loop` takes `emit_log_event: bool = True`, and on any
non-healthy class it writes a captain's-log DIAGNOSIS event
(`src/introspect.py:599-611`). The Go port hard-codes the FALSE path in
both `DiagnoseLoop` and `DiagnoseLatest`, so `maro introspect <loop-id>`
mutates the event log in Python and does not in Go.

This was a considered deferral once — the reason given was "it belongs
with the captain's-log port". **That reason expired.** The captain's-log
port landed as `record.Recorder.EventNoted` writing
`memory/captains_log.jsonl`, and graduation and scans already use it.
Nothing is in the way but the work, and for three review rounds the
comment read as a decision rather than a gap. (Round 4's lens: a deferral
whose rationale has expired is indistinguishable from a live one.)

What the port has to reproduce, all of it differential-worthy:

- summary `f"Loop {loop_id}: {failure_class} ({severity}). {len(done)}/{len(profiles)} steps done."`
- context keys `severity`, `steps_done`, `steps_blocked`, `tokens`
- `note=recommendation[:200] if recommendation else None` — a CODE POINT
  clip, and empty-string-to-None
- the bare `except Exception: pass` that makes the whole write
  best-effort, so a broken log must not fail a diagnosis

Note the test consequence, which is the reason this is worth doing rather
than documenting forever: `cli_diff_test.go` gives each runtime its own
store copy PRECISELY because Python writes here and Go does not, and it
now pins that asymmetry with a before/after workspace snapshot. Closing
this gap turns that pin red — which is the pin working, not a regression.

### Go port: outcome rows are written without `cost_usd`, and a Python consumer DROPS them (FOUND 2026-08-26, go-port scoping)

`record.WriteOutcome` omits `cost_usd` with a comment saying the Python
estimator was not ported yet. It is now — `metrics.EstimateCost` landed
with the pricing table — and Python's `record_outcome`
(`memory_ledger.py:574`) writes
`estimate_cost(tokens_in, tokens_out, model=model or None)` on every row.

The consequence is worse than a zero. `evolver_scans.py:488` builds its
cost sample as
`[o.get("cost_usd", 0.0) for o in outcomes if isinstance(o.get("cost_usd"), (int, float))]`
— a missing key fails the isinstance test, so a Go-written outcome falls
OUT of the sample rather than reading as zero spend. Every cost analysis
over a mixed store silently measures only the Python-written rows.

Blocked on an import cycle: `internal/metrics` imports `internal/record`
for the jsonl append helpers, so `record` cannot import `metrics` back.
The fix is to lift the pricing table and `EstimateCost` into a leaf
package (`internal/pricing`) that both import — small, and it also stops
the cost constants living in the same package as the ledger readers.

Do this before any Go-side runtime writes real outcomes.

### Python bug: a negative token count inverts `identify_expensive_patterns` (FOUND 2026-08-26, go-port chunk scoping)

Not a port gap — a bug in `src/metrics.py` that reading the function
closely enough to port it turned up. Measured against the real
`estimate_cost`, not reasoned:

    estimate_cost(in=-5000, out=0)        -> -0.015
    estimate_cost(in=-5000, out=0, cr=100)-> -0.015   (cache_read clamps to 0)
    estimate_cost(in=1000,  out=-5000)    -> -0.072
    estimate_cost(in=-5000, out=1000)     ->  0.0     (lands exactly on the gate)

`cache_read = max(0, min(cache_read_tokens, tokens_in))` clamps to zero when
`tokens_in` is negative, so `fresh_in` keeps the negative value and the cost
goes negative with it. `identify_expensive_patterns` then computes a negative
`avg_cost`, and its `type_avg > avg_cost * 1.5` test **inverts** — measured,
`1.0 > -1.0 * 1.5` is True.

Run end-to-end against the real functions — two outcomes, one at 1M input
tokens under task_type `cheap`, one at -5M under task_type `neg` — this is
what `format_metrics_report` actually prints:

```
--- By Task Type ---
  cheap: 1 runs, 100% success, avg 0ms, $3.000000 total
  neg: 1 runs, 100% success, avg 0ms, $-15.000000 total

--- Cost Optimization Suggestions ---
  ! 'cheap' tasks cost 3.000000 USD avg (-0.5x the overall average).
    Consider using MODEL_CHEAP or reducing max_tokens.

--- By Model ---
  unknown: 2 runs, $-12.000000 total, -4,000,000 tokens
```

The advice names the CHEAPER of the two task types, quotes "-0.5x the
overall average" as its justification, and the model line reports negative
spend over a negative token count. Nothing raises; the report is confident
and wrong end to end.

The `if avg_cost == 0.0: return []` gate does not catch it; that gate exists
to make the division safe, and it only excludes exact zero.

Reachable from the store, not just in theory: outcome rows are JSON, and
nothing validates the sign of `tokens_in` / `tokens_out` on the way in.
CPython's `json.loads` also accepts bare `Infinity`, `-Infinity` and `NaN`,
which reach the same arithmetic (`estimate_cost(nan, 0)` -> `nan`;
`estimate_cost(-inf, inf)` -> `nan`; plain `Infinity` gives `inf`, which is
at least well-ordered).

The NaN case has a second consequence in `format_metrics_report`, whose
`sorted(by_model.items(), key=lambda x: -x[1].total_cost_usd)` produces a
position-dependent garbage order around a NaN (measured: `[a=nan, b=1.0,
c=5.0]` -> `['c','a','b']`). Go's `sort.SliceStable` runs a different
comparison sequence and will land somewhere else, so this is also the first
place the port may need a NAMED divergence it cannot close.

Fix is small and belongs on the Python side first — clamp at the estimator
(`tokens_in`/`tokens_out` `max(0, ...)`) or reject non-finite values at
ingest — but it is a behaviour change to a shipped function, so it wants
Jeremy's call rather than a quiet repair. **The port reproduces the bug
as-written in the meantime**, with fixtures pinning it; a port that silently
fixed it would hide the finding and break the differential.

### Go port: `SuccessfulRunCostP90` has no default window, and no caller yet (FOUND 2026-08-26, go-port metrics r3)

Python spells it `successful_run_cost_p90(limit=RUN_COST_CARD_LIMIT)` — the
window is a KEYWORD DEFAULT, so every caller that does not think about it
samples 200 cards. The Go port spells it `SuccessfulRunCostP90(ws string,
limit int)`, so the default lives at the call sites instead. There are
currently **no production call sites at all**: `RunCostCardLimit` is
referenced only by its own declaration and `recorder_diff_test.go:805`.

The constants differential therefore pins the NUMBER for parity while the
behaviour it names is unexpressed. A future caller passing a different
window makes the two runtimes sample different card sets — feeding
different p90s into the warn line and the 4×p90 auto kill-line — and
nothing in the differential would see it, because the differential asserts
the constant matches CPython's, not that anything uses it.

Fix before the first caller lands, not after: a defaulting wrapper
(`SuccessfulRunCostP90Default(ws)`, or a negative-limit sentinel meaning
"the default") so the default is expressed ONCE in the port the way it is
expressed once in Python. Cheap now, and a silent sampling divergence
later.

Same family as the `AnalyzeStepCosts(nil)` sentinel already named in
`analysisWindow`'s comment: both are Python keyword defaults that the port
hoisted to the caller, and both are invisible until a second caller exists.

### Go port: decide whether `LoadsMap`'s non-object refusal should abort the call (FOUND 2026-08-26, go-port metrics r2 — L1)

`pyval.LoadsMap` refuses a row that is valid JSON but not an object, and
every caller treats that refusal as "skip this row". CPython does not
behave that way, and the divergence is already NAMED at `pyval.go`'s
`LoadsMap` as a deliberate choice — but it was never adjudicated, and
three call-site comments described it as `except: continue`, which it is
not. Those comments are now correct; the behaviour question is open.

What CPython actually does: `json.loads("[1,2]")` SUCCEEDS. The
`AttributeError` comes from the `.get()` on the NEXT line, which sits
OUTSIDE the `try` (`metrics.py:234-239`, `:352-357`, `:279-283`), so it
aborts the whole call. Measured:

```
one bad row among good ones     PY spend_today     0.0     GO  1.5
                                PY spend_for_loops 0.0     GO  1.5
a `null` run card among ten     PY p90             None    GO  9.0
```

The argument for the current behaviour is that skipping costs one row and
keeps the rest, where CPython throws the whole answer away. That is a
value judgement about which is *better*, not about which is faithful —
and this chunk has now been wrong three times in a row about exactly that
kind of judgement (the tuple-as-set read, `costUSDOf` swallowing a raise,
`decodeReplace`'s run-collapsing were each defended by a comment and each
turned out reachable and failing open).

Reachability is genuinely thin, which is why it is filed and not fixed: a
crash-torn append does not produce valid non-object JSON. A hand-edited
or foreign-written row does.

Not a free change — `LoadsMap` has callers across the port and its error
is currently untyped (`fmt.Errorf`), so matching CPython means adding a
sentinel and deciding per call site. Worth doing deliberately or not at
all; do not let it ride in as a side effect of another chunk.

### Knowledge edges are minted but never traversed — the graph is queried like a flat list (FOUND 2026-08-21, link-farm round-3 run 92491e53, verified on dev Mac)

Surfaced as a byproduct of a documented PASS: the round-3 assessment declined
to backlog a graph-engineering-101 tweet because maro already implements
everything it describes — and in proving that, read the architecture closely
enough to find the gap the tweet's payoff line points at.

The facts, independently verified against the checkout (not taken from the
run's self-report — its own claim-review lane flagged the crux fact as
needing confirmation, and it confirmed):

- `KnowledgeEdge` exists (`knowledge_web.py` — source/target/typed
  relation/weight), edges are WRITTEN (`append_knowledge_edge`, called from
  `knowledge_bridge.py`), and `load_knowledge_edges()` has **zero callers**
  outside its own definition.
- `query_knowledge()` is TF-IDF text search over node title/description/tags
  only. `relation`, `weight`, and edge structure are never consulted by any
  retrieval path — "multi-hop reasoning", the payoff the graph shape exists
  for, is structurally unreachable.

So the K2 layer pays the write cost of a graph and collects the read benefit
of a list. Decision-shaped, not a rider: either (a) wire edge traversal into
retrieval (e.g. one-hop expansion from TF-IDF seeds, relation-weighted),
with an A/B against text-only recall before it earns a default; or (b)
conclude edges have no consumer worth building and stop minting them —
subtract-before-add cuts both ways, and this entry deliberately does not
presume (a). Candidate input for the memory-as-module bake-off (arc -1) and
a natural first nominee for the subtraction audit (Vision/Deferred) if (b).

**Direction adjudicated (Jeremy, 2026-08-21): (a)** — *"that's definitely
something I'd like to see upgraded to use."* The A/B gate stands: traversal
earns a default with recall evidence, not by decree.
- [x] Measure first: how many edges exist on the box, what relations, would
      any live recall have changed with one-hop expansion? Then wire
      relation-weighted expansion behind a flag and A/B against text-only.

**Measured 2026-08-21, and the answer reframed the build.** All 2124 edges
were lf-↔lf- from the one-time April link-farm import — relation uniformly
`related`, weight uniformly 0.5, ZERO first-party edges, zero edges minted
since April. The entry's "edges are WRITTEN" premise was formally true and
practically false: `record_skill_knowledge_edge` (the only first-party
writer) had zero callers — both `outcome_to_knowledge` call sites omitted
`skills_used` — and its edges targeted `outcome:<id>` pseudo-nodes no
traversal could reach anyway. Since the live recall lane excludes lf-
nodes by decree, one-hop expansion would have changed **zero** recalls,
structurally, forever. The read side needed a write side first.

**SHIPPED 2026-08-21 (both halves + backfill):**
- *Write:* `knowledge_web.derive_coderivation_edges` — deterministic,
  zero-LLM, idempotent: `co_derived` edges between first-party nodes
  sharing an `outcome:<id>` source (weight 0.5/0.7/0.9 by shared-outcome
  count, max-wins per pair over the append-only store; lf- never
  participates). Rides `run_skill_maintenance` beside candidate promotion
  (`knowledge.edge_derivation_enabled`, ON default — no spend) + CLI
  `python3 -m knowledge derive-edges`. Backfilled live: 430 edges from
  provenance already on the node rows (485/572 first-party nodes gained
  ≥1 edge; second run appended 0 — idempotency proven on the real store).
  The dead `record_skill_knowledge_edge` + `skills_used` plumbing removed
  (remove-don't-disable).
- *Read:* one-hop expansion in `query_knowledge` behind
  `knowledge.edge_expansion` (OFF default per the A/B gate; ON this box):
  neighbours inherit `seed × weight × 0.5` when that beats their own
  lexical score — surfaces lexically-distant siblings, never displaces a
  stronger direct hit, never widens the eligible pool
  (status/confidence/lf- filters bind neighbours too). Rendered
  expansions stamp `KNOWLEDGE_EDGE_EXPANSION` (the A/B denominator) and
  a `[linked]` marker. Post-review semantics: expansion counts only when
  it changes MEMBERSHIP of the rendered top-k — set unchanged means the
  ON arm returns the text-only ranking verbatim, so the event never
  fires on reorder/decoration-only recalls.
- *Adversarial r1* (4× sonnet-medium fallback, codex capped): REJECT →
  fixed same session. Two VERIFIED HIGHs — the A/B denominator fired on
  membership-identical recalls (Architect), and one malformed weight row
  silently killed both halves forever (Expert QA; loader now coerces
  with skip-and-count). Full ledger, fix list, and rejected-findings
  rationale: `docs/history/2026-08-21-edge-review-r1.md`. Side
  deletions: dead `build_wiki_link_edges`/`extract_wiki_links` sibling
  removed. Deferred with named triggers: adjacency cache / sweep
  watermark (if either store 10×s), edge decay (if a source-retraction
  path is ever built), centrality size-normalizer (metric revisit — the
  percentile fix was measured worse; threshold widening recorded in
  test_codebase_graph.py).
- *Adversarial r2* (same 4-lens fallback, on the r1 fix commit): REJECT
  again → fixed same session. The r1 "corrected" 2/120 receipt measured
  the wrong layer (raw query sets at min_confidence=0.0, no char
  budget); and the loader validated `weight` but not endpoint ids (a
  null id TypeError'd the writer's `sorted()` snapshot with the drift
  warning never firing). Loader now validates the whole row (ids
  non-empty strings; weight numeric, non-bool, in [0,1] — bound check
  covers NaN/±inf); `_render_knowledge_entries` extracted so the replay
  measures the literal render layer. Ledger + rationale:
  `docs/history/2026-08-21-edge-review-r2.md`.
- *Offline readout at the PRODUCTION layer* (500 recent real goals,
  rendered-set comparison at min_confidence=0.3 + max_chars=600,
  read-only — `scripts/replay_edge_expansion.py`): **0/500 recalls
  changed.** Query-level membership changes on 29/500 (5.8%), but the
  600-char render budget truncates every entrant before render (damped
  boost ≤ 0.45×seed sorts entrants to the back of the top-5; 600 chars
  renders ~2–4 entries). **Expansion is live but rendered-inert on
  current traffic** — the earlier 4/120 and 2/120 figures were
  artifacts of laxer measurement layers. ~55 pins in
  tests/test_knowledge_edges.py incl. a known-gap pin on the truncation
  undercount.
- [ ] **UNBLOCKED 2026-08-21** — Jeremy killed the 600-char recall
      override same day ("by now you know my position on arbitrary
      character limits... 600 chars is kind of silly low"; the value
      traced to April's 9e3d46e7 with zero rationale). recall.py now
      passes no override, so inject's own default budget (1200) is the
      one budget in play — pinned by
      `test_recall_passes_no_budget_override`, and the replay script
      reads the budget from the signature so receipts can't drift.
      Post-change readout: expansion changes **22/500 rendered recalls
      (4.4%)** (was 0/500 at the starved budget; 29/500 at query level
      — the remaining 7 are the circuit-breaker's honest tail). A/B
      against text-only recall on live traffic (the default-flip gate):
      KNOWLEDGE_EDGE_EXPANSION events are the denominator — now
      accruing; judge whether edge-surfaced nodes correlate with better
      outcomes once enough events land. Interpretation caveat (r1
      Skeptic): edge-surfaced renders bump `times_applied`, which feeds
      base lexical ranking — a node's later lexical rise may be
      expansion feedback, not lexical merit.

### dev-recall missed a decree we had written down — ranking, not coverage (FOUND 2026-08-20, Jeremy: "makes me a little worried")

**The incident.** Asked whether we could route step work to cheap models, I
reported that per-step tiering was never wired. Wrong: it shipped as Phase 57
and was removed 2026-07-21 under the MID-floor decree. Jeremy caught it from
memory. `dev-recall` — the tool whose entire job is "why did we decide X" — did
not surface the arc, and I only found it via `git log -S classify_step_model`.
His concern is the right one: *"how we missed this (and recall didn't know
anything about that arc) that makes me a little worried as well."*

**Root cause: vocabulary mismatch under lexical-only retrieval.** The content
was in the index the whole time. I queried in the vocabulary of the *code
comment* — "execution floor MID per-step cheap downgrade removed" — and got
nothing useful. The record uses different words: *"execution defaults unified at
MID"*, *"local-model wiring removed"*. Querying with those returns the right doc
as the **top hit** (`docs/history/2026-07-21-chunk1-adversarial-review.md`). BM25
has no bridge between the two phrasings.

**This is the already-measured MH #8 gap, biting on a real question.** The
memory_quality eval scores paraphrase queries at **hit@1 2–6%** vs 46–80% on the
lexical lane. This incident converts that from a benchmark number into a worked
example with a cost attached: a wrong answer to Jeremy, caught only because he
personally remembered a decision from a month earlier. That is not a
reproducible safety net. **Recommend this be treated as evidence for the
memory-as-module bake-off (MILESTONES arc -1)**, which is exactly the axis a
third-party store would improve.

**Second, independent defect — the index silently rots. FIXED 2026-08-20.**
Nothing triggered ingest: it was manual-only per CLAUDE.md, so nobody ran it.
Found at **5 days stale**, and it had missed the GOAL_BRAIN rotation entirely —
`20a917ef` moved 556KB→173KB of decisions and journal into
`docs/history/goal-brain-*.md`, and all three rotated files held **0 chunks**.
So for four days the compiled record's own history was invisible to recall by
construction. Re-ingest recovered them (47/21/23 chunks). Fixed at the source:
`scripts/land.sh` now runs `correspondence ingest --since 1d` after a successful
push (~0.4s over 817 files, non-fatal, quiet unless it errors). Landing is when
repo docs change, so it is the honest trigger.

**Still open after that fix:** the ranking half. A rotation-shaped hazard also
remains — content moved between files is only as findable as the next ingest,
and nothing verifies that a rotation preserved retrievability. Worth a cheap
guard: after any doc rotation, query for a distinctive string from the moved
content and confirm it still returns.

### The "async tail" is not async — it is reordering. Make it a real process spawn (Jeremy, 2026-08-20) — **SHIPPED 2026-08-20, OFF by default pending box burn-in**

Jeremy: *"That's not really async if we're blocking the CLI call right? is this
an exec level spawn of another process to make it truly async in cases like
that? Seems like something that's solveable with a little effort."* Correct on
both halves.

**What Phase 1 actually does.** `_defer_learning_post_notify` (`handle.py:190`)
appends a callable to an in-process dict `_POST_NOTIFY_LEARNING`.
`_drain_deferred_learning` (`handle.py:194`) pops and calls them **synchronously,
same process, same thread**. The maintenance twin (`defer_maintenance_post_notify`
/ `drain_deferred_maintenance`, `loop_finalize.py:581`) is the same shape. So the
tail is *reordered* to run after the notification, not *backgrounded*. A
notify/Telegram consumer sees the answer first and genuinely benefits; a caller
waiting on process exit — `python3 -m handle`, any script, any CI step — waits
for the entire tail and gets nothing from Phase 1.

**Measured cost of that gap** (run 2026-08-19, `research/2026-08-19-sol-advisor-efficiency-claim.md`):
deliverable written at 14m37s, process alive until 31m08s — **16m31s, 53% of wall
clock, after the answer existed**. Closure audit, adversarial claim review, lesson
extraction, ~10 skill-promotion validations, ~8 skill rewrites, ~10 knowledge-node
validations, evolver, business signal scan.

**Why a spawn is the clean fix and not a workaround.** `handle.py:208-215` already
records the hazard in-process deferral creates: the maintenance twin had to be
moved into a *different module* because `python -m handle` executes as `__main__`,
so `loop_finalize`'s `import handle` loads a **second copy** whose registry the
finalize block never drains (found in 3-lens review of `707a541`). That is a
module-identity bug class that in-process deferral keeps generating; a separate
process with an explicit state handoff eliminates it by construction rather than
by careful placement.

**The seam already exists.** The tail is keyed by `handle_id`/`loop_id`, and run
records are already durable on disk — so a child process can re-open the run by
id instead of inheriting objects. Missing piece is a standalone entry point:
there is no `finalize-tail` verb today; `_finalize_cli_deferred_learning`
(`cli.py:640`) takes an in-memory result object. Shape of the work: (1) add
`maro finalize-tail --loop-id X` that reconstructs from disk, (2) have the parent
double-fork/`spawn` it and exit after the answer, (3) keep the current in-process
path as the fallback when spawn is unavailable, (4) decide the contract for a run
whose tail is still in flight when the next run starts (the existing lock files
suggest the answer is already partly there).

**Watch-item inherited from Phase 1** (still open, listed under the original
async-tail entry): `handle()`'s `_hid=None` exception path can strand registered
callables. A spawn design should make stranding impossible rather than rarer.

**SHIPPED 2026-08-20** (`src/tail_jobs.py`, record:
`docs/history/2026-08-20-async-tail-process-spawn.md`). All four steps of
the named shape, and the watch-item with them.

The move that made it possible is the one the entry called for without
naming: a closure cannot cross a process boundary and a module-level dict
cannot survive one, so **the registration became a serializable record**
appended to `<run_dir>/build/tail_jobs.jsonl`, and the drain became a
function over that store. `maro finalize-tail --handle-id X` reconstructs
and runs it; `handle()`'s finalize calls `drain_or_spawn`, which either
starts a detached child (`start_new_session`, stdin `/dev/null`, output to
`build/tail.log` — an inherited pipe would keep `out=$(maro handle ...)`
blocked until the last writer closed it) and returns, or runs the same jobs
in the same place phase 1 ran them. **Both lanes run the same executor over
the same records** — a fallback that re-implements the work is a sibling
that drifts.

That also retires the module-identity bug class by construction rather than
by careful placement: a store keyed by handle_id has no module identity to
get wrong.

One correction to this entry's premise: "run records are already durable on
disk" is true of everything EXCEPT the field the tail actually reads.
`build/loop-*-log.json` persists `result_length`, not `result`, and
`build/loop-*-step-NN.md` is a rendered artifact with a synthesized header,
not the field. So the step outcomes ride the handoff whole, serialized from
the objects the parent already holds — the "explicit state handoff" half.
The adapter travels as `backend` + `model_key` and the child rebuilds
(exact, then same-model auto, then default: a tail on a neighbouring backend
beats no tail).

Contract for step (4), overlapping tails: **one tail process per handle_id**
(claim row + host-scoped `os.kill(pid, 0)` liveness; EPERM counts as alive).
Tails for DIFFERENT runs may overlap — they already do, because heartbeat
runs skill maintenance on its own tick, and every store these phases touch
is lock-protected. Serializing across runs would be a guarantee this
codebase has never had.

The **watch-item is answered by the record, not by care**: a stranded tail
is now discoverable. `find_stranded()` reports pending jobs with no live
claim, `maro finalize-tail --sweep` drains them, and heartbeat runs that
sweep on its health tick with a 1800s grace window so it cannot race a child
that is still starting. Every job kind is idempotent, so a late drain
repeats nothing. A job that RAISES is marked done with its error rather than
left pending — it already had its effect on whatever it touched, and a sweep
would repeat that half.

The store is append-only on purpose: two processes write it, and the
ten-round destructive-rewrite arc above is the record of what
read→transform→rewrite does to a store under exactly those conditions.

`tail.spawn` is **OFF by default** (DEFAULTS.md): the spawn does not change
what the tail does, but it changes where its LLM spend and store writes
happen. Probed: 30 tests + `tests/mutation/tail_jobs.json` 28/28 (27 on the
first sweep; the survivor was real — age is not abandonment).

*Its adversarial r1 (2026-08-20, FOUR codex seats — Skeptic, Architect,
Minimalist, Expert QA): **REJECT.** Six HIGHs, all six reproduced, zero
hallucinations, and the top finding is the chunk's own commit message turned
around. **Append-only is not atomic.** Byte-level safety says no line is
overwritten and says nothing about a decision made from a read that a later
write depends on — and every state-dependent write here was one of those:
`_next_seq` read the rows and appended outside the lock (two registrars
allocate `seq: 1` twice, both lines on disk, one job invisible because the
executor is keyed by seq — probed literally), and `run_jobs` checked the
standing claim before appending its own, so "one tail process per handle_id"
was a comment rather than a mechanism. Both go through one locked
read-decide-append now. Next: **the default lane had lost the run's adapter**
— `handle()` passed `adapter=None`, so even with `tail.spawn` OFF the tail
rebuilt from the recorded identity, dropping a FailoverAdapter's live state
and any injected adapter, which makes "phase-1 behaviour exactly" false in the
one place it was load-bearing (`_handle_impl` builds its own adapter when the
caller passes none, so the object is not recoverable from `handle()`'s scope;
it is remembered at record time now). **"Every job kind is idempotent" was too
broad** — `run_post_run_maintenance` advances DURABLE cadence counters, so a
child that died after a tick would have it counted twice by the sweep; the
hazard did not exist in phase 1 because phase 1 had no sweep, i.e. the
recovery mechanism introduced it and a correct-looking blanket claim hid it.
Per-kind now: learning re-drains, maintenance whose drain already started is
surfaced under `needs_operator`. **A failed job was visible nowhere** — done,
so not pending, and pending was all `find_stranded` reported, two lines under
a comment claiming otherwise. Also: append failures on claim/done/release were
computed and discarded; the sweep truncated candidates to `limit * 4`
newest-first BEFORE filtering, and heartbeat passes `limit=3`, so twelve
healthy recent runs could hide an old abandoned tail forever; an unreadable
store read as an empty one; a failed surface refresh was swallowed at debug
level; and `bool("false")` is True, so a quoted YAML value turned the spawn ON
— the one direction the OFF default exists to prevent. **The one the seats did
not find**, caught while reading during the tree freeze: `runs.current_run_dir()`
is a ContextVar, so the spawned child pinned nothing, and `record_llm_call`
NO-OPS with no run-dir active with record-mode ON by default — the spawned
lane would have stopped capturing the tail's LLM calls into `build/calls/` and
the run card's `n_calls`, counted from those files, would have under-reported
calls the run paid for. Four reviewers read a process-spawn diff and none
asked what ambient process state the child does not inherit; worth carrying as
a lens gap. Receipts: spec 28 -> 50, 50/50 after (the first post-fix sweep
returned six SKIPs — anchors bound to lines my own fixes had rewritten,
re-anchored before it was called green — and two survivors, both single-kind
fixtures that could not tell a whole-run drain from a filtered one); tests
30 -> 47; suite green. Record:
`docs/history/2026-08-20-async-tail-process-spawn.md`.*

*Its adversarial r2 (2026-08-20, FOUR fresh codex seats on the whole chunk
+ the r1 fix layer, primed with r1's findings): **REJECT — and the fix-layer
statistic held a second time.** Every HIGH lives in the r1 fixes. Top
finding, 4/4 seats independently: **the crash evidence could be laundered by
recovery itself** — `_drain_started` read the LAST claim row, so the first
partial sweep's own claim+release made the SECOND sweep read "never started"
and re-run maintenance that had already ticked durable cadence counters; the
same store-global bit also stranded UNTOUCHED maintenance forever when a
child died between learning and maintenance. One mistake, two failures:
store-global evidence for a per-job question. Per-job `started` rows now,
judged per job. Also: the adapter cache was per-handle and lifecycle-unaware
(last registration won; the escalation early drain forgot maintenance's
adapter; a successful spawn leaked one adapter per run in long-lived
callers) — keyed (handle_id, seq) now; `_transact` could run UNLOCKED via
locked_write's environment fallback (file_lock grew require=True, defaults
unchanged) — the r1 race wearing the fix's clothes; a malformed `spec`
(string, valid JSONL) raised BEFORE the containment try and crash-looped the
spawned child with the claim never released — decoding inside the belt,
release in finally; my own r1 `scan_cap=2000` was the limit*4 starvation one
magnitude up AND magic-number enforcement against the standing observational
decree — deleted; refresh failures had a record and no READER — surfaced in
state/CLI/sweep/heartbeat; `"ok": "false"` (string) read as success;
refresh now follows ATTEMPTS not successes (the r1 test had pinned the false
premise that failed = nothing happened); `_strict_bool` takes only 0/1
numerically (bool(nan) is True); finalize-tail grew `--force` and honest
exit codes. Deferred with premises: phase-result honesty (the runners
swallow their own sub-failures — identical to phase-1 inline behaviour, its
own chunk), cwd parity child-vs-inline (unmeasured; box runs from the
checkout root), pid-reuse fingerprinting (documented + --force overrule, not
engineered). Receipts: tests 47 -> 60; spec 50 -> 64, 64/64 FIRST sweep (9
re-anchored, 1 deleted with its code, 15 added); suite green. Signal worth
keeping: r2 found no defect in the r1 STORE design — transaction,
append-only, records — only in its policy edges. The rounds are narrowing,
and the r2 fix layer is itself unreviewed. Record:
`docs/history/2026-08-20-async-tail-process-spawn.md`.*

**Open residuals, in the order burn-in should look at them:**

- [x] **Box burn-in + the flip — FLIPPED 2026-08-21 (Jeremy: "Let's make the flip").** `tail.spawn` defaults True; `MARO_TAIL_SPAWN` env override added (test-suite pin + operator kill-switch). The 53% figure is phase 1's cost on one
      run; what a real workload's wall clock looks like with `tail.spawn`
      ON is unmeasured, and is the evidence the flip needs. Watch: does the
      answer's caller actually exit at the answer, does the child's tail
      finish, do the surfaces (card, report, captains-log slice) come out
      the same as an inline run's.
      **First evidence, 2026-08-20 (run `0ebadc02-plucky-ember`, a real
      link-farm review goal, `tail.spawn: true` box-wide, clean r12
      clone):** CLI exited at 05:33:52 UTC — the same second the spawn was
      stamped — and the detached child ran the tail until 05:41:34:
      **7m42s (~27% of total wall clock) the caller did not wait for.**
      Both jobs done ok, claim released, run card re-curated by the child
      at 05:41:33 (after the tail's cost rows), and 1 LLM call captured
      into `build/calls/` AFTER the parent exited — the ContextVar pin
      doing its job in production. Store event sequence exactly as
      designed: job, job, spawn, claim, done, done, release. Note
      `tail.spawn: true` is now LIVE in the box workspace config (burn-in
      decree 2026-08-20), so organic runs are accumulating more evidence;
      the fresh-install default stays OFF until Jeremy flips. Runs 2-3
      (f89bf29b, 92491e53) repeated the pattern on the r2 fix layer —
      per-job `started` rows live in production. **Deliberate crash test
      2026-08-21 (Jeremy's ask): SIGKILL mid-maintenance, 5s into real
      work.** Sweep surfaced `needs_operator` and did NOT re-run; second
      sweep identical (no evidence laundering); heartbeat's grace window
      held; cadence counters frozen until the OPERATOR drained it
      deliberately (`finalize-tail`, exit 0) — single tick, measured, and
      the whole incident reads off eleven rows of the append-only store.
      Full table in the history record. **The go/no-go checklist is filled
      from this side; the fresh-install flip is Jeremy's call.**
- [ ] **`knowledge_web.maybe_consolidate()` is still in-process** — same
      `finally` block, after the tail dispatch. Marker-gated to ~once per
      24h, but when it fires the caller waits for the dream cycle (decay +
      a size-gated LLM compress). It is post-answer work that this chunk's
      own logic says should be a job; left alone because the ask named the
      tail phases and moving a non-run-scoped phase into a per-run store is
      its own decision. **This is the remaining "still busy after the
      answer" surface.**
- [ ] **Census the in-process registries before deleting them.** They
      survive as the fallback for a handle that owns no run-dir, which is
      rare — and their own refresh blocks are largely inert on that lane
      anyway (no run dir to re-render). Worth a census, not a guess.
- [x] ~~The r1 fix layer is unreviewed~~ — r2 ran 2026-08-20 (four fresh
      seats), REJECT, six findings fixed same-day (see the r2 entry above).
      The r2 fix layer is now the unreviewed one; round 3 is optional —
      the rounds are visibly narrowing (no store-design findings left,
      policy edges only) and burn-in evidence is the better next instrument.
- [ ] **Phase-result honesty** (r2 deferral, Expert QA): the tail runners
      (`finalize_deferred_learning`, `run_post_run_maintenance`) swallow
      their own subsystem failures by original design, so a job whose
      sub-phase failed still records `ok: true` and the failed lane stays
      empty. Identical to phase-1 inline behaviour — not a spawn regression
      — but the durable record now EXISTS and could carry per-phase results
      if the runners returned them. Its own chunk; touches the phase
      contracts, not the store.
- [ ] **cwd parity, child vs inline** (r2 deferral, Architect): the spawned
      child runs from the repo root; the inline lane inherits the caller's
      cwd. Premise for deferring: the box invokes from the checkout root, so
      the lanes agree in production today. Re-check if a caller ever
      dispatches from elsewhere.

### Director worker-review is ungated — we lose the sol-advisor efficiency comparison on our default path (FOUND 2026-08-19, maro self-analysis)

`_review_worker_output` (`src/director.py:832`) fires a second LLM call after
**every** `dispatch_worker` — both the initial site (`:527`) and the revision-loop
site (`:565`) — with no gate on ticket size, triviality, or risk. On rejection the
loop repeats the whole dispatch+review cycle (`MAX_REVIEW_ROUNDS = 2`, `:56`), so a
rejected ticket pays it twice. That is structurally the tax Daniel Mac measured
(https://x.com/daniel_mac8/status/2089768482824921127): spec out, then pay again to
read the result back.

A bypass does exist — `run_director(skip_if_simple=...)` routes single-scope,
≤15-word directives straight to `run_agent_loop` (`_is_simple_directive`, `:262`) —
but it **defaults to `False`**, where sol-advisor's comparable `solo` mode is its
recommended default. So on our default path, on the bounded single-context tasks
his N=10 test used, we would lose the same comparison.

**Why it is not a snap fix:** the tradeoff is review-skip risk vs. token savings,
and it deserves its own evaluation rather than a reaction to one thread. We have
the instrumentation to actually settle it — `~/.maro/workspace/memory/step-costs.jsonl`
carries per-call `tokens_in/out`, `cache_read_tokens`, `cost_usd`, `model`, `loop_id`
(4,806 records as of 2026-08-19), which is more than the original experiment had.
The honest test is a size/risk-gated review bypass measured against ungated review
on matched tickets, scored on cost-per-accepted-outcome, not tokens.

Full analysis: `research/2026-08-19-sol-advisor-efficiency-claim.md`.

**Urgency note (2026-08-21, from the scaling-agent-systems audit):** the
box logs show this path is production-DORMANT — zero director/worker/review
events in 7,120 captains_log rows; all step-cost records are the solo loop;
no telegram listener running. The ungated review tax is real but currently
un-paid. When the bypass A/B does run, the DeepMind/MIT paper
(`research/2026-08-21-scaling-agent-systems-audit.md`) supplies the prior:
centralized review is the right SHAPE (best error containment, 4.4× vs
17.2×), and the cost to beat is its measured 3.9× efficiency penalty.

### Link-farm research leads — four adjudicated steal candidates (FOUND 2026-08-20/21, runs 0ebadc02/f89bf29b/92491e53; Jeremy: "not 'next', but probably worth a visit before later")

Promoted from the link-farm-maro-scan burn-in series' deliverables (full
artifacts with verification trails live in the box project dir;
`ranks-4-7-assessment.md` also records three documented PASSes — ranks 4, 6,
7 — so the non-entries have reasons, not silence). Citations verified
against `link-farm/db/ai_links.db` by the runs, read-only.

**1. Compare the worker/reviewer pipeline against Vercel's "Foreman" 4-station design.**
- [ ] Pull Foreman's station breakdown (Classifier/Analyst/Implementer/
      Reviewer, each sandboxed, reviewer on a DIFFERENT model vendor seeing
      only the pushed branch — never the implementer's reasoning) and diff
      against `container_exec.py` + the worker/reviewer flow.
- [ ] Why: same-vendor review has a known blind spot (a model under-catches
      its own mistake class — the exact premise of our cross-model
      adversarial-review skill, which currently exists at the DEV layer but
      not inside maro's own closure/review stages). Spike: implementer and
      reviewer on different model families in separately-sandboxed
      `container_exec` invocations; compare catch rate on a known-flawed
      sample. Source: Granite @granite0x,
      https://x.com/granite0x/status/2087960767287230592 (2026-08-14).

**2. Re-check default fan-out width against the DeepMind/MIT coordination-cost study.** ← **DONE 2026-08-21** (dev Mac session; full writeup with the box-log audit: `research/2026-08-21-scaling-agent-systems-audit.md`)
- [x] Get the actual paper — found: "Towards a Science of Scaling Agent
      Systems" (arXiv:2512.08296, Nature MI 8:1157, DeepMind+MIT). Curve
      extracted: ~45% single-agent-baseline saturation threshold
      (β̂=−0.236, p=0.004); range +80.8% (decomposable) to −70.0%
      (sequential planning); SWE-bench Verified — ALL four MAS variants
      under solo (−2.1% to −14.9%); error amplification centralized 4.4×
      → independent 17.2×; turns T=2.72(n+0.5)^1.724; comm overhead
      58–515%. Caveat: R²cv only 0.37–0.41, and every benchmark task fits
      one context — cross-context orchestration (maro's core loop) is
      outside what was measured.
- [x] Audit our own logs — **the multi-agent arm is EMPTY**: all 4,919
      step-cost records are the solo loop; 0 director/worker/review
      events in 7,120 captains_log rows; the 205 events.jsonl "director"
      matches are goals ABOUT director code; no telegram listener
      process. The Director path is code-live, production-dormant, so the
      pattern cannot reproduce retrospectively. Measurable instead: our
      verify lane = 12.4% of spend ($52/$419) buying the same
      error-containment mechanism the paper prices at +285% comm/3.9×
      efficiency in centralized MAS. The "guard against reflexive
      fan-out" therefore landed as doctrine at the decision sites, not a
      code hook: evidence notes added to "Concurrent milestone-area
      agents" and "Director worker-review" entries; lead #3 (topology
      search) drops in priority — the paper measured that search space as
      mostly negative above the threshold. Provenance loop closed: this
      finishes Jeremy's incomplete 2026-08-14 `751e2dea` dispatch,
      building on (not rediscovering) its partial ANSWER. Source: Yarchi
      @undefinedki,
      https://x.com/undefinedki/status/2087634870260449474 (2026-08-14).

**3. Automated multi-agent topology search — read as the complement to #2.**
   *(Priority DOWN after #2 landed 2026-08-21: the scaling-agent-systems
   paper measured this search space and found it mostly negative for
   SWE-shaped work above the ~45% saturation threshold; a topology search
   earns tokens only in the sub-threshold or decomposable regimes. Keep
   sequenced, revisit if we enter those regimes.)*
- [ ] Google/Cambridge line: search sub-agent prompts + communication graphs
      (mutation/pruning, compute-optimal topology per task) instead of
      hand-designing. Our workflow patterns are hand-designed topologies; if
      searched graphs consistently win, the fixed pattern library is leaving
      performance on the table. Deliberately sequenced AFTER #2 lands — a
      complement, not independent work. Source: marfin @marfinxx,
      https://x.com/marfinxx/status/2087671840596459629 (2026-08-13).

**4. Evaluate vercel-labs/deepsec as an agent-driven vulnerability harness** (carried Jeremy's `flag: review` from save time).
- [ ] Pull the repo's actual approach (verified live: "a security harness
      for finding vulnerabilities in your codebase powered by coding
      agents"). maro's current surfaces are narrower: `security.py` is
      prompt-injection detection on fetched content, `bughunter.py` is
      fixed-rule AST static analysis, `/security-review` is prompted LLM
      review — none is an agentic vulnerability hunter.
- [ ] Run deepsec against a known branch (or deliberately-vulnerable sample)
      and compare findings against `bughunter.py` + `/security-review` on
      the same input: replacement, complement, or redundant. Source: David
      Ondrej @davidondrej1,
      https://x.com/davidondrej1/status/2087862257279459422 (2026-08-14,
      notes=`flag: review`).

### File-derived mutation coverage — sweep the rest of the tree (Jeremy, 2026-08-16)

- [ ] **Run a file-derived mutation sweep over everything that hasn't
  had one.** Method is written down in `docs/HOUSE_STYLE.md` step 3 —
  derive the mutation list by READING THE FILE, not from your own diff.
  A diff-derived list tests whether your fixes are pinned; a
  file-derived list tests whether the behavior is. Jeremy called it
  2026-08-16 off the §14a slice-3 evidence: *"sounds like we need it."*

  **Why, in one number:** five adversarial review rounds (r1–r5, four
  lenses each, ~50 fixes) walked past **12 real gaps** on one file
  surface. Two rounds of diff-derived must-detect harnesses scored 6/15
  and then 15/15 and *still* missed them, because they only ever aimed
  where the fixes had been aimed. The worst find was not a bug in the
  code at all — the e2b83703 decree ("scope is not a ranking input") had
  two guards, a grep tripwire and a behavioral test, and a live
  `sim *= 1.25 if lesson.scope == "method"` inside the actual ranker
  passed both. **A guard that cannot fail is worse than no guard: it is
  a standing claim that the rule is enforced.**

  **Defect classes it finds** (all four seen in one sweep, none found by
  review): guards that cannot fail; tests that pass for a reason other
  than the one in their docstring (usually because the subject is
  reached through a caller that produces the same observable on its
  own); duplicated mirrors of a constant where fixing two of three reads
  as done; and printed/logged fields that can be replaced by constants
  with a green suite — which is exactly the operator-facing half of any
  instrument.

  **Already swept — name it precisely, it is a SURFACE, not whole
  modules.** The §14a scope/stamp/portability surface only:
  `knowledge_web.py` (scope screens, tiered-store load/rewrite/mutate,
  quarantine, the reinforce heal, `_tfidf_rank_scored`),
  `camera_readout.py` (portability census, `_lesson_origins`,
  `_stamp_coverage`, `_scope_rollup`, `_print_portability`), and
  `pack.py`'s lesson transport border. 46 mutations across five
  harnesses, all now must-detect. Weaker/diff-derived only:
  `memory.py`'s `as_typed_lesson`/`_parse_typed` and `portability.py`'s
  cache-refresh logging. **Everything else in `src/` (178 modules) has
  never had one** — including the other ~85% of `knowledge_web.py`
  (4398 lines) and `camera_readout.py`'s frame/verdict sections, which
  sit in files this arc only partially covered. Do not read "swept" off
  the filename.

  **Start by covering everything else** (Jeremy's framing). Not
  178 modules of brute force — order by where a false green actually
  costs something:
  1. **Modules whose tests claim to enforce a decree or invariant.**
     This is where the scope finding came from, and the failure is
     silent by construction. Candidates: the defaults registry + census
     tripwire, the provenance gate, the dispatch envelope's extraction
     exclusion, `lesson_provenance`, the Δ-gate floors, the data-
     retention guarantees in `memory_ledger`/`knowledge_web`.
  2. **Data-integrity boundaries** — anything that parses untrusted or
     off-disk JSON, or rewrites a file readers depend on. The store
     wedges this arc found were all this shape.
  3. **Operator-facing output** — readouts, captain's log, status
     lines. Printed fields were replaceable by constants across the
     board here; assume that generalizes until probed.
  4. Recent heavy churn, last.

  **Two riders.** (a) Build the runner ONCE. This arc produced five
  throwaway harnesses in a scratchpad; a `scripts/mutate.py` taking a
  spec file (anchor / replacement / test target, must-match-exactly-once)
  is ~80 lines and makes the sweep repeatable and reviewable instead of
  re-derived each time. Do that first, then sweep — it is the difference
  between a session's effort and a standing capability. (b) **Record
  EQUIVALENT mutants as equivalent** rather than contorting a test to
  kill one; that is how a suite starts testing its own mocks. One in the
  §14a sweep was genuinely unreachable and is documented as such.

  Cost is real and worth stating: each mutation is one targeted test-file
  run (seconds), but the sweep is only as good as the reading behind the
  list, which is the expensive part and does not parallelize well across
  agents that haven't read the module. Record: r4b/r5 in
  `docs/history/2026-08-16-14a-slice3-review-arc.md`.

  **Runner SHIPPED 2026-08-16** (`scripts/mutate.py`, spec convention +
  coverage ledger in `tests/mutation/README.md`). Runs against a
  `git archive` copy by default, fails on an anchor that doesn't match
  exactly once, and supports `equivalent` with a required reason.

  **Tier-1 progress.** Two decree surfaces swept, opposite outcomes.
  `provenance_gate.json` — landed red at 6/18, CLOSED same day (below).
  `dispatch_envelope.json`
  — 20 mutations, CLOSED 2026-08-16 at 20/20: 12 on the first pass, seven
  gaps fixed, one equivalent recorded with its probe. Both DECREE
  mutations (leak operator prose into the goal; drop the operator channel)
  were DETECTED, so the extraction exclusion is genuinely enforced — the
  first tier-1 surface to hold. `defaults_census.json` — 15 mutations,
  CLOSED 2026-08-16 at 15/15 from **3/14**, the worst first pass yet: the
  DEFAULTS.md tripwire could be gutted whole (forward census → `[]`,
  reverse census → `[]`) with a green suite, because it had no seam for
  synthetic input and so was untestable by construction. Fixed with
  `(src_root, doc_text)` parameters defaulting to the live repo plus 16
  must-detect fixtures. `retention_decree.json` — 15 mutations, CLOSED
  2026-08-16 at 15/15 from **4/13**: the 2026-07-10 retention decree's
  tripwire had the same disease, all three assertions gutted with a green
  suite, and its four DETECTED verdicts all routed through the single
  stale-entry assertion that `stale = []` removes. Fixed the same way
  (seam + 21 fixtures), plus the latent glob→rglob gap.
  `delta_gate_floors.json` — 36 mutations, CLOSED 2026-08-16 at 36/36
  from **33/35**, the best first pass of the arc: five numeric floors,
  five killswitches and every finite/call-floor/spread/stratum/
  replay-error screen across four parallel routes were already pinned.
  Two survivors, both probed equivalent (a boundary pre-check redundant
  with its in-lock twin — the twin's unique TOCTOU job is now pinned;
  and a point-estimate check strictly subsumed by the ±spread band
  below it). `provenance_gate.json` — CLOSED 2026-08-16 at 19/19 from the
  6/18 it landed red at; the four `unverified` enforcement survivors split
  two equivalent / two real under the probe, and pinning one of them
  surfaced a mirror the first spec had missed. **Tier-1 is CLEAR.**

  **Tier 2 — data-integrity boundaries — scoped 2026-08-16 by census, not
  by guesswork.** 94 modules parse off-disk structured data; 143 of those
  parse sites drop the record on error and say nothing, across 52 modules,
  led by `memory_ledger.py` (16), `knowledge_web.py` (9),
  `evolver_store.py` (8), `skills.py` (7), `loop_report.py` (6),
  `run_curation.py` / `evolver_scans.py` (5 each). The thesis is narrower
  than "these drops are bugs": **for a JSONL store the drop is usually
  right — one bad append must not truncate the read of everything after
  it. The silence is the defect.** A read that returns 40 of 41 rows is
  indistinguishable from a store that holds 40, which contradicts both
  the retention decree ("the path is part of the result") and
  artifacts-over-streams.
  - *Slice 1 — SHIPPED 2026-08-16 (`240ab7f9`).* `src/jsonl_utils.py`,
    the one shared reader (9 callers), was itself one of the 143. It now
    returns a `SkipReport` via `read_jsonl_tail_counted` and logs a
    WARNING naming the store from `read_jsonl_tail`; missing ≠ unreadable
    ≠ dropped, blanks are not loss, and a `limit=N` read marks its counts
    as a lower bound rather than passing a tail total off as a file total.
    Collapsing the two near-copy loops into one `_classify` also killed a
    latent divergence class (they had already diverged once, over
    `UnicodeDecodeError`). `jsonl_utils.json`, 32 mutations, 32/32 —
    see the README note on why a co-written sweep is the weak kind of
    green. Side-find: a `if buffer: yield buffer` block in
    `_iter_lines_reverse` was unreachable in unmutated code and removed.
  - *Slice 2 — SHIPPED 2026-08-16.* `tests/test_no_silent_drop.py`, a
    ratchet rather than the red gate that was planned. The rule is narrow
    and stated in the docstring: an `except` handler whose `try` contains
    a json/yaml/pickle/tomllib load, inside a loop in the same function,
    whose body is only `pass`/`continue`/`break`. Under that rule the
    count is **137 per-record drops in 49 modules across 122 functions**
    (inside a wider 313 silent handlers over a parse call across 83
    modules; the "143 across 52" in the slice-1 note was an ad-hoc scan
    from before the rule was pinned and is superseded). All 137 land in
    `UNREVIEWED_SILENT_DROPS` as **debt, not approval** — new ones trip,
    fixing one means deleting its line in the same diff, and
    `REVIEWED_SILENT_DROPS` stays empty until somebody actually looks at
    a site and writes down why the silence is right. Keyed
    `(module, function) -> count`, which **settles the open retention
    allowlist-granularity decision by building option (b)** rather than
    asking: a second drop in a listed function trips, and half-fixing one
    trips the stale check so the freed slot can't be quietly reused.
    20 must-detect fixtures in both directions plus a non-vacuity check
    that SRC resolves to a real tree. `silent_drop_census.json`, 32
    mutations, 32/32.
  - *Slice 3 — memory_ledger STARTED 2026-08-16, two destruction bugs
    fixed.* The census pointed here and reading the file found the
    payload: **`deduplicate_lessons` deleted rows it could not parse.**
    It rebuilt `lessons.jsonl` from the parsed rows alone, so a torn
    append or a schema-drifted row was destroyed by the next dedup, and
    `before` already excluded it so neither the stats nor the log could
    show the loss. Its three siblings (`_rewrite_lessons_file`,
    `_reinforce_flat_row`, the `_mark`/`_stamp` in-place edits) had
    preserved unparseable rows for months — **the outlier was invisible
    from inside the module**, and the repo's own §14a r4 note asserts the
    flat store preserves them, true of three paths and false of the
    fourth. `compress_old_outcomes` had the adjacent shape: it deleted its
    whole compressed range including lines that contributed nothing to the
    batch summary, destroying rather than compressing them. Both fixed
    with counts + WARNINGs; `lesson_sweep.json`, 24 mutations, 24/24.
    Silent-drop debt 137 → 135, and the ratchet's stale check caught both
    cleared entries on its first real use.
    - *memory_ledger CLOSED 2026-08-17 — two more bug families, both
      found by probe before editing.* The remaining 14 sites split 8 pure
      reads / 6 in-place stampers. The reads were worse than expected:
      every one called `path.read_text()` on the whole file, so **one
      non-UTF-8 byte anywhere in a store made `load_lessons`,
      `load_outcomes`, `load_task_ledger` and `load_compressed_batches`
      return EMPTY** (41 healthy rows → 0, the `UnicodeDecodeError`
      swallowed by an outer `except Exception: pass`), and made
      `load_outcome_by_loop_id` and `outcome_row_has_step_lessons`
      **RAISE into their callers** — they guard `except OSError`, and a
      decode error is a `ValueError`. Exactly the Tier-0 #3 failure
      `jsonl_utils` was written to end, still live in the flat lane
      because these loaders predate it. All eight now route through
      `read_jsonl_tail_counted` via `_read_store` (announces the
      SkipReport, names the store) and `_rows_as`, which reports **schema
      drift separately from corruption** — JSON the current dataclass
      rejects is a different loss from JSON that will not parse, and
      collapsing them hides which one is growing.
    - *The six stampers are REVIEWED, not fixed* — the rewrite rejoins the
      whole `lines` list, so a torn row is skipped by the search and
      re-emitted verbatim. But a reason string in a dict is a claim, so
      the property is pinned by `TestTheStampersPreserveWhatTheyCannotParse`
      (six cases, each with a premise assertion that the stamp lands *past*
      the torn line) and by `stamp_preserve.json`. Debt 135 → 121 + 6
      reviewed.
    - **Writing those entries exposed a hole in the ratchet itself: the
      census keyed on a BARE function name.** `memory_ledger.py` has five
      distinct `_stamp` closures, so one REVIEWED entry would have blessed
      all five — reviewing one site silently approving four nobody read,
      the "guard that cannot fail" shape. Keys are now qualified
      (`outer.inner`, `Class.method`), which also split
      `memory_backends.py`'s single `read_all` entry: it had been covering
      **both** `JSONLBackend` and `SQLiteBackend`. Two collisions in one
      50-module scan, neither visible without going looking.
    - *Adversarial round on the closure chunk, 2026-08-17 (sonnet-medium
      lane, codex capped): REJECT round 1, fixed same session.* Consensus
      HIGH, probe-confirmed: all six REVIEWED stampers still whole-file
      strict-decoded, so ONE torn byte raised UnicodeDecodeError out of
      every stamper (`except OSError` misses a ValueError) — a permanent,
      DEBUG-swallowed stamping outage per torn store, in the very
      functions just marked "safe by construction." The pinning tests
      shared the blind spot (ASCII torn lines only). Root-cause fix:
      surrogateescape on `locked_rmw`/`atomic_write` (byte round-trip for
      all 16 rmw-using modules) + `_store_text` for the inline reads;
      probed clean. Also: value-diff vacuity assertions (the `lessons`
      key was pre-seeded, so `field in stamped` couldn't fail for one
      stamper), census module keys now relative paths (basename was the
      function-half collision one axis over), family B was three loaders
      not two, spec 16→23 mutations 23/23. Full record:
      `docs/history/2026-08-17-memory-ledger-stamper-review.md`.
    - *knowledge_web CLOSED 2026-08-17 — the worst finding of the arc,
      probed live before editing:* **one crash-torn byte in a tiered
      lessons store + the next consolidation cycle = the ENTIRE TIER
      WIPED** (573 bytes → 0, probed). `load_tiered_lessons`' strict
      whole-file read swallowed the decode error and returned [] for 3
      healthy rows (family A), `_quarantine_unparseable` was blind the
      same way, and `_mutate_tiered_lessons` — every reinforcement, GC,
      promotion, refight — rebuilt the store from the empty load. The
      r3/r4 quarantine sidecar never fired: it handled rows that PARSE
      wrong, and the destruction came from bytes that DECODE wrong. The
      guard was just more code reading the file with the same bug.
      Node/edge loaders + `_bump_node_times_applied` raised
      `UnicodeDecodeError` past `except OSError` (family B);
      archive/remint/canon readers were family-A silent (the remint one
      silently erasing Δ-gate strike lineage). Fix: the four
      memory_ledger helpers generalized into `jsonl_utils`
      (`read_jsonl_announced`/`read_rows_as`/`store_text`/`loads_clean`,
      memory_ledger aliases them — 7 stamp_preserve mutations re-filed,
      re-run 27/27); six loaders converted to announced byte-level
      reads; quarantine round trip byte-safe + taint-refusing, sidecar
      gets ORIGINAL bytes, and it now returns rows it could NOT move so
      both rewrite paths carry them verbatim (its failure log already
      promised exactly that while the callers deleted them); unreadable
      store aborts the rewrite instead of wiping it; bump/promote
      rewrites REVIEWED as node-stampers (byte-safe, launder-proof,
      pinned). Census debt 121+6 → 113+8. 14 new tests,
      `knowledge_web_preserve.json` 18 mutations 18/18 first pass.
      Record: `docs/history/2026-08-17-knowledge-web-byte-safety.md`.
    - *Adversarial rounds on the knowledge_web chunk, same day (sonnet-
      medium lane): r1 five lenses REJECT — 4-lens consensus HIGH in the
      fix layer (quarantine partial-failure duplication: append-success +
      shrink-failure returned durable rows as "stranded" → unbounded
      sidecar duplicates), plus the sidecar COUNT still strict-reading
      the bytes it exists to count (reports 0 exactly when quarantine
      fired), the raw-read OSError swallow one function downstream of the
      destruction fix, and SIX stale scope_14a anchors (reviewer said 4).
      r2 three lenses on the fixes: raw=True raise had overloaded "skip
      decay math" — five pure readers inherited crash semantics → own
      `for_rewrite` flag (QA dissented, dissent chased, majority right);
      sidecar dedup read now degrades the dedup not the quarantine. r3
      declined with reasons on the record. One sweep survivor along the
      way was honest (the dedup mutant outlived a test that never
      re-ran quarantine over a dirty file) → both-writes-fail
      convergence test. Final: 23/23 + 35/35 + 27/27, suite 9434.
      Both rounds in the history record.*
    - *evolver_store CLOSED 2026-08-17 — the suggestions ledger, last
      named surface of slice 3. Probed live before the fix: one torn
      byte emptied `load_suggestions` AND `get_suggestion` (the row the
      V2 auto-revert authority guard re-reads just before an
      irreversible revert — family A), raised UnicodeDecodeError from
      `suggestion_is_applied`/`apply_suggestion`/`revert_suggestion`
      (family B), and blinded `_save_suggestions`' dedup scan so a
      re-derived identical suggestion duplicated — the 81-duplicate
      calibration bug resurrected by one byte. Fix: six loaders on
      jsonl_utils announced reads; the three keyed rewrites (apply's
      `_merge`, revert's `_drop_constraint`/`_mark_reverted`) parse via
      `loads_clean` so a byte-tainted line never key-matches and is
      re-emitted verbatim (family C — `_mark_reverted` re-dumps every
      row it parses, the launder exposure). Census 8 sites cleared
      (baseline now 95 unreviewed + 10 reviewed). 8 new tests,
      `evolver_store_preserve.json` 12 mutations 12/12 first pass,
      suite 9442. Record:
      `docs/history/2026-08-17-evolver-store-byte-safety.md`.
      Adversarial r1 same day (5 lenses, sonnet-medium): REJECT —
      5/5-consensus HIGH, the chunk fixed three of FIVE keyed rewrites;
      `dismiss_suggestion._merge` + `stamp_verification._merge` (the
      unattended V2 cadence stamper) still laundered. Census-invisible
      by construction (their except bodies re-emit the line), caught
      only by the watch-list sibling sweep. Fixed same round:
      loads_clean swap x2, tainted-twin pins x2, mutations 16 total,
      record corrected. Rejected: get_suggestion early-exit perf.*
    - *Slice 3's named surfaces are DONE (memory_ledger 16,
      knowledge_web 9, evolver_store 8). Remaining census debt (95
      unreviewed sites / 87 functions + 12 reviewed as of 2026-08-17,
      led by skills.py 7) is tripwire-guarded, not scheduled — burn
      down opportunistically when touching a file, per the census
      contract.*
    - *Tree-wide DESTRUCTIVE-subset sweep 2026-08-17 (the evolver r2
      lesson generalized: the dangerous shape is not silence, it is
      drop + write-back — a dropped row in a read is recoverable, a
      dropped row in a rewrite is gone). Wrote an AST scanner for
      parse-loops that drop AND write back; 58 drop sites narrowed to
      ~25 write-back candidates. Top hit: **skills.py — the
      knowledge_web tier-destruction chain in a hotter store.** Probed
      live: one torn byte + one `record_skill_outcome` (fires on EVERY
      skill invocation) took skill-stats.jsonl from 4 lines to 1,
      silently; and one torn byte made every `save_skill` raise,
      write-locking the skill library until hand repair. SHIPPED:
      shared `_read_skill_stats`/`_write_skill_stats` (stranded rows —
      unparseable AND keyless — ride the rewrite verbatim), all 7
      skills.py census sites cleared, 8 tests,
      `skills_preserve.json` 9 mutations 9/9 first pass. Scanner kept
      at `scripts/scan_destructive_rewrites.py`; remaining RISK
      candidates NOT yet triaged at the time — CLOSED 2026-08-20, see
      the triage entry below.*
    - *Destructive-rewrite triage CLOSED 2026-08-20: all 70 RISK sites
      read by hand — 3 real defects (6 scanner sites), 64 false
      positives, each FP's reason tabled in
      `docs/history/2026-08-20-destructive-rewrite-triage.md` so the
      next run of the scanner starts from known-benign sites rather
      than unknowns. The three: (1) **`doctor.cleanup_workspace_skills`
      — DESTRUCTIVE**, and the sharpest framing of this family yet
      because it is a REPAIR verb an operator runs *because* the store
      looks wrong. Probed live: one non-UTF-8 byte crashed it outright,
      and a truncated row (what a crashed append actually leaves) went
      4 lines in / 3 out while the closing summary said "0 total"
      removed — the summary counts only what the verb MEANT to delete,
      so a destroyed row reports as zero. Side-find fixed with it: the
      verb and doctor's duplicate check both hardcoded
      `~/.maro/workspace/memory/skills.jsonl` while every runtime
      caller resolves through `config.workspace_root()`, so under a
      MARO_WORKSPACE override doctor rewrote a store the running system
      was not using. (2) **`interrupt.InterruptQueue`** — `_read_lines`
      strict-decoded and feeds `peek()`, which GATES
      poll/clear/is_empty, so one torn byte killed the operator's whole
      control channel: stop/pivot messages and the kill switch's own
      STOP interrupt stopped reaching a running loop until hand repair.
      Loud (loop_post_step logs an ERROR per step) but total. Both
      `_mark_applied` merges also re-dumped every parsed row —
      loads_clean now sends tainted twins down the preserve branch that
      already existed. (3) **`gc_memory._gc_outcomes`** — the r4
      deferral, closed: probed (2,1,0) healthy -> (0,0,0) after one torn
      append, "nothing to collect" forever with zero log signal while
      the store grew without bound. Receipts: 15 tests each written
      against a live probe first, `interrupt_gc_doctor_preserve.json`
      19 file-derived mutations 19/19 first pass, census 2 sites
      cleared (84 unreviewed sites / 76 functions + 13 reviewed), suite
      9622 verified in an isolated worktree at HEAD+change. Counting
      note worth keeping: the scanner's own summary line contains the
      word RISK, so `grep -c RISK` reports 71 for 70 sites — the
      triage counts were re-derived by set-difference before the record
      was written.
    - *Its adversarial r1 (2026-08-20, FIVE codex seats — the cap
      lifted, so a true opposite-model round): **REJECT, 5/5 consensus
      HIGH — and the HIGH was mine again**, the same shape the previous
      round caught. The doctor fix took the lock only around the WRITE,
      so a `save_skill()` landing between the snapshot and the lock was
      overwritten by the stale snapshot: a lost update, in a repair
      verb, introduced by the commit that was fixing data loss — with a
      code comment claiming the race was fixed. Read now happens inside
      the lock. **Probe note worth keeping: an in-process probe of that
      race CANNOT FAIL** (locked_write is reentrant, so a same-process
      writer takes the lock the cleanup already holds); the pin forks a
      real subprocess, which waits 1.3s for the lock. Five more held and
      were fixed: (a) `loads_clean` refuses byte TAINT, not wrong SHAPE
      — `[]`/`null`/`"x"` are valid taint-free JSON that reached .get()
      and raised AttributeError, which peek()'s handler does not catch,
      so the control channel still went down on a different input (3
      lenses); (b) `"applied": "false"` is legal JSON and truthy, so a
      STOP read as already-delivered and vanished with no warning — now
      strictly `is True`; (c) the cleanup filtered stale rows by ID, so
      a healthy skill sharing a stale row's id was destroyed and the
      summary counted only the stale one (probed 2 in / 0 left / "1
      removed") — filters by row now; (d) `splitlines()` also breaks on
      U+2028/U+2029, legal INSIDE a JSON string, so a rewrite turns one
      valid row into two invalid fragments — fixed in all three files,
      arc-wide sweep of the idiom BACKLOG'd below; (e) "verbatim"
      strand-and-carry stripped the line before carrying it. GC's count
      finding was **half right and worth recording as such**: an
      identical post-scan append cannot cost data (an outcomes line
      equal to an old one carries that same old timestamp, so
      collecting it is correct), but the RETURNED COUNTS came from the
      out-of-lock scan, so GC could delete two rows and report one —
      classification now happens again inside the lock. Uncollectable
      rows became visible (`GCReport.outcomes_uncollectable` + summary
      line): they can never age out, and the retention decree forbids
      deleting them, so visibility is the answer, not collection.
      Receipts: 12 more tests (all four verified as failing against the
      pre-fix code), spec 19 -> 29 (28 detected + 1 marked EQUIVALENT
      surviving as claimed), suite 9634 in an isolated worktree.*
    - *Owed from r1, with reasons: (1) **`interrupt.poll()` marks a row
      applied BEFORE the caller applies it** — a crash in that window
      loses the message permanently. At-most-once, not exactly-once (3
      lenses, verified). Moving the mark later just trades it for
      double-delivery, so the fix is a claim/ack protocol with an
      idempotent apply keyed on interrupt id — its own chunk,
      pre-existing, not touched here. (2) An arc-wide sweep of
      `store_text(...).splitlines()` -> `.split("\n")`: every store
      hardened in this arc shares the U+2028 framing bug, and only the
      three files touched on 2026-08-20 are fixed. Low exposure today
      (our writers use json.dumps defaults, which escape those
      characters; the risk is foreign or hand-edited rows) but it is
      the same one-line change everywhere. (3) A quarantine/repair verb
      for rows GC can never collect — count is now visible, the
      remedy is not.*
    - *Its adversarial r2 (2026-08-20, three codex seats on the r1 fix
      layer): **REJECT — and for the SECOND round running the top
      finding was a regression the previous round's fix introduced.**
      r1 made the `applied` read strictly `is True` to stop a truthy
      `"false"` from swallowing a STOP; that closed the drop and opened
      its mirror — every LEGACY truthy value (`"true"`, `1`) flipped
      from applied to pending, so a historical interrupt is
      **re-delivered and applied twice**, then silently rewritten as a
      boolean (3/3 seats, probed). Both fixes treated a three-valued
      question as two-valued. `_applied_state()` now answers True /
      False / **None** — None meaning *this flag cannot be read*, a
      third answer rather than a default; legacy `"true"`/`"false"`/`0`
      /`1` are recognized as the compatibility boundary they are and
      anything else strands-and-announces. Also fixed: (a) **a dict is
      not yet a Skill** — the r1 shape guard accepted every JSON object,
      and `_skill_hash_is_stale` returns "not stale" for anything it
      cannot build, so a forged object carrying a healthy skill's
      content_hash plus a higher score won the dedup and DELETED the
      healthy row (probed: healthy gone, forgery kept, no warning);
      `dict_to_skill(row)` now validates at the boundary. (b) GC's
      `freed` sampled `st_size` before the lock, charging a concurrent
      RETAINED append against the freed count and reporting a NEGATIVE
      number — a successful collection described as having grown the
      store (probed `(2, 1, -4097)`); the delta is computed inside the
      locked transform now. (c) The drift gate accepted regressions:
      re-introducing the KNOWN REAL defect at `interrupt.py:poll`
      passed `--check` (in SITES so not untriaged, in FIXED so not
      stale) — `compare()` returns `regressed = live & FIXED` and exits
      nonzero. (d) **The r1 lock test could pass with the lock
      removed** — spawn + `sleep(1.0)` + assert both rows survive is
      satisfied by a child that appends AFTER cleanup finishes;
      demonstrated against the lock-removal mutant. The child now
      handshakes (LOCK_NB probe until refused -> marker -> append) and
      the parent asserts `blocked`. (e) The scanner gate had no
      must-detect fixture; `compare()` was split out pure and is tested
      on all three failure directions. **Accepted as designed** (now a
      comment, not a thing to re-derive): GC skips the locked pass when
      the unlocked pre-scan finds nothing, so a row expiring in that
      window waits for the next tick — a latency choice, not data loss.
      **Deferred for Jeremy:** `locked_write` FAILS OPEN on a corrupt
      `.lock` (logs a warning, proceeds unlocked), handing the
      lost-update race back to every caller including this repair verb
      — reproduced, but it is documented deliberate behaviour in a
      primitive the whole tree shares, so fail-closed is a decision
      about every caller at once. Receipts: spec 29 -> 35, **35/35
      accounted for** (33 detected + 2 equivalent surviving as
      claimed). One equivalence is itself a result: `doctor shape` was
      DETECTED in r1 and became unfalsifiable in r2 because the new
      `dict_to_skill` validation raises on every non-dict JSON value —
      marked with that reason, with row-shape detection carried by
      `doctor schema` instead. Four mutations came back SKIP (stale
      anchors from the r2 rewrites); a SKIP is not a pass and all four
      were re-anchored before the sweep was called green.*
    - *Its adversarial r3 (2026-08-20, FIVE codex seats on the r2 fix
      layer): **REJECT, and for the THIRD round running the top finding
      was a defect the previous round's fix introduced** — 5/5 consensus
      this time. r2 answered "a dict is not yet a Skill" with
      `dict_to_skill(row)`, which is a CONSTRUCTOR: Python does not
      enforce dataclass annotations, so `description=7` sails through,
      `compute_skill_hash` raises on it, `_skill_hash_is_stale` catches
      that and answers "not stale", and the forgery — carrying the
      healthy row's declared content_hash and a later created_at — wins
      the dedup and DELETES the healthy skill. Probed against the r2
      code: 2 rows in, only `forged` out. New `skill_types.
      validate_skill_row()` proves a row before it may take part in any
      decision about which rows to remove (required keys, content fields
      that are text — proven by hashing them, string identity/timestamp
      fields, list-of-string lists, finite ranking numbers); all 423 live
      rows validate, and that negative control is a test. The mechanism
      was the error DIRECTION: "can't verify -> keep" is the right
      retention instinct and the wrong membership answer. **Sharpest
      finding of the round (Expert QA): the scanner had walked out of its
      own field of view.** It matched only `.splitlines()`, and this arc
      CONVERTED every site it hardened to `.split("\n")` (splitlines
      breaks on U+2028/U+2029, legal inside a JSON string) — so reverting
      `interrupt.poll` to the exact destructive shape produced ZERO hits,
      and r2's `regressed = live & FIXED` gate could never fire for 6 of
      its 8 entries. Scanner now takes both idioms; blast radius measured
      BEFORE the change (+10 sites, 5 RISK, all playbook.py markdown /
      read-only) and triaged, manifest now 75 sites / 69 FPs. Side-find:
      the scanner's docstring claimed RISK required "drops on a parse
      failure" — it never tested that, the claim had no executing line,
      and the 64-of-70 FP rate is the consequence; docstring corrected,
      code left conservative. Also fixed: (a) poll()/clear() stranded
      rows in SILENCE — both preflight with an unlocked peek() then
      re-read under the lock, so a row that turns unreadable in that
      window is withheld and carried with nobody told, and if the
      delivered interrupt stops the loop no later peek() ever runs (3
      seats); both locked transforms count and announce now. (b) GC
      announced `locked - unlocked` through a warning that formats it as
      a count — a repaired row printed "gc: -1 unparseable row(s) …
      kept" (2 seats); one absolute announcement per run now. (c) A
      locked pass that removes nothing no longer rewrites — the old
      shape re-joined and normalized framing, so a snapshot with no
      trailing newline came back one byte LARGER and GC reported
      freed=-1 for collecting nothing. (d) **The r2 drift-gate tests
      proved `compare()`, not the gate** (4 seats): the only test running
      the executable asserted the CLEAN baseline exits 0, so `main()`'s
      `return 1` -> `return 0` passed the whole file — CI told green
      while a regression ships. main() pinned directly both directions +
      negative control + must-detect mutation. (e) **The r2 freed-byte
      test could not fail** (Minimalist): it appended inside
      `_store_text`, which in the pre-fix code ran BEFORE the
      `stat().st_size` sample, so the old implementation also reported
      positive freed. Repointed to hook `locked_rmw`, and the mutation
      replaced with a FAITHFUL revert instead of a `freed = 0` stand-in
      — deriving must-detect mutations from the FILE not the diff is the
      house rule this violated. NARROWED rather than fixed: GC skips the
      locked pass when the pre-scan finds nothing, so on that branch the
      reported counts are the unlocked snapshot; r2's comment claimed
      otherwise and now states the true thing (nothing was mutated, so
      there is no mutation to misdescribe). Receipts: spec 35 -> 44,
      **44/44 accounted for on the first pass** (42 detected + the 2
      standing equivalents), suite 9664 passed.*
    - *Carried lesson, the one worth reusing: **a fix can blind the
      detector to its own subject.** Nothing about that shows up as a
      failing test or a red CI run — it shows up as an instrument that
      reports zero forever. After changing an idiom, re-run the detector
      against the REVERTED code and prove it still finds it. "Found 0"
      is a claim and needs an executing line like any other.*
    - *Its adversarial r4 (2026-08-20, FIVE codex seats on the r3 fix
      layer): **REJECT, 5/5 HIGH — the FOURTH round running whose top
      finding is a defect the previous round's fix introduced.** r3
      proved rows were well-formed; five seats independently found what
      that does not answer — **schema validation cannot establish
      provenance.** Dedup still grouped by the content_hash the row
      DECLARES ABOUT ITSELF, so a row that validates perfectly still
      nominates itself as a duplicate of a healthy skill and evicts it.
      Five smuggling shapes (different description / steps_template /
      optimization_objective under the victim's hash, an unnamed extra
      field, the id fallback) = five angles on one keying bug. Fixed at
      the KEY, not the field list: `_dedup_identity(row)` canonicalizes
      everything in the stored row that says what the skill DOES (all
      keys minus a named bookkeeping set), the declared hash never
      enters the decision, and the id fallback is gone — all five shapes
      probed, all now keep both rows. The bookkeeping set is now the
      security boundary, hence the `doctor dedup scope` mutation. Also
      fixed: (a) **`return old` is not "decline the write"** —
      `locked_rmw` writes back whatever it is handed, so r3's GC no-op
      fix still rewrote and re-inoded the file for a pass that collected
      nothing; added a `None` sentinel (96 call sites, additive), pinned
      by spying atomic_write AND asserting the inode. (b) r3 moved the
      announcement below the lock and orphaned the failure paths: GC
      returned (total,0,0) with no warning and no `uncollectable` stat
      on a failed lock/read/write, and interrupt returned `[]` — which
      the loop reads as "no interrupts" — for a failed commit, so a
      STOP that was on disk and seen by the preflight silently was not
      delivered. Both now say what did not happen. (c) the queue
      announced twice per pass (peek + locked transform);
      `peek(announce=False)` is the preflight form. (d) three more
      framing idioms still invisible to the scanner — `readlines()`,
      `split(b"\n")` (which jsonl_utils itself uses) and
      `split(sep="\n")`. Receipts: spec 44 -> 59, `59/59 accounted
      for`. **The survivors are the interesting part:** the first sweep
      came back 55/59 with four VALIDATOR mutants surviving — because
      the dedup fix removed the consequence the end-to-end tests
      measured (once junk cannot evict, "the healthy row survives" holds
      whether or not junk was admitted). Not a dead guard: admission has
      its own consequence (an admitted row is re-serialized, a stranded
      one rides through byte for byte), so the answer was direct
      rejection tests plus a byte-for-byte strand assertion. One of the
      four, `doctor validator (hash)`, looked genuinely equivalent —
      every field compute_skill_hash touches is also in _STR_FIELDS —
      until the shape that distinguishes them turned out to be a **lone
      surrogate**: it IS a str, passes every isinstance check, and dies
      on .encode(). The mutant that looked unfalsifiable was pointing at
      this arc's own subject; marking it `equivalent` would have deleted
      the one guard that catches byte taint at the schema boundary.*
    - *Its adversarial r5 (2026-08-20, FIVE codex seats on the r4 fix
      layer): **REJECT, 5/5 HIGH — the FIFTH round running whose top
      finding is a defect the previous round's fix introduced.** Seven
      findings, all seven reproduced before being touched, zero
      hallucinations. (1) **An exclusion list is a denylist, and a
      denylist guarding a destructive decision fails open.** r4 keyed
      dedup on behaviour (right) by naming twelve fields "bookkeeping"
      and ignoring them (wrong): `circuit_state` decides whether a
      skill MATCHES AT ALL (skills.py find_matching_skills), so rows
      differing only there are not identical copies — probed both
      directions, a forged open row evicting a healthy closed one and a
      forged closed row resurrecting a circuit-broken skill; and
      `failure_notes`/`source_loop_ids` are EVIDENCE, so deleting the
      row destroys it. Identity is now three names — id, content_hash,
      created_at — everything else must match, future fields included.
      Measured first: on the 423-row live store the tight identity
      finds exactly as many duplicate groups as r4's list (zero), so
      the strictness costs nothing observable and "identical" becomes
      literally true. (2) **The validator proved the COERCED value**:
      every check read getattr(skill, ...) after dict_to_skill, which
      applies int()/float()/normalize_tags, so `consecutive_failures:
      "7"` was proven an int, `utility_score: true` a float, `tags:
      "not-a-list"` a list — and those are the fields dedup ignores, so
      the forgery still won on created_at. Checks now run on raw
      d[name]; construction after; coercion stays in the tolerant READ
      path. Re-probed live: 423 rows, 0 stranded. (3) **A queue of only
      unreadable rows went silent** — r4 muted the preflight so the
      locked pass could own the announcement, but poll/clear return
      BEFORE the locked pass when nothing is deliverable, so a corrupt
      STOP alone in the queue produced no delivery, no warning, no
      trace. One announcement per pass, from whichever branch actually
      runs. (4) **loads_clean accepted two more corrupt shapes**: a
      surrogate written as a JSON ESCAPE (pure ASCII on disk, invisible
      to the raw-line scan, parses to exactly what a torn byte
      produces — only hashed fields caught it), and DUPLICATE OBJECT
      NAMES (`{"applied": false, "applied": true}` reads as applied
      because json.loads keeps the last — a STOP swallowed with no
      warning). Both now strand. (5) **Scanner blind spots + a weak OK
      verdict**: a separator hoisted to a local and plain iteration
      over an open handle were invisible, and OK was a substring test,
      so a rewrite parsing with bare json.loads that merely MENTIONED
      loads_clean reported OK — and the vanished leg counted it as
      watched and healthy. Blast radius measured before shipping: 69 ->
      77 RISK, +8, none lost, all eight hand-triaged the same day (new
      `clean-then-raw` FP category for the four memory_ledger stampers,
      whose bare json.loads re-parses a line their own loads_clean scan
      already proved clean). `doctor.run_doctor` LEFT the FIXED set
      with the reason recorded: nothing in it regressed, r5 refuted the
      VERDICT that put it there — a drift gate cannot tell "the code
      regressed" from "the rule got stricter", so that resolution is a
      hand re-read and a written reason, never a widened exemption.
      Receipts: spec 59 -> 69, 69/69 accounted for. Eight existing
      mutants came back SKIP on the first sweep (stale anchors from
      r5's own rewrites) and were re-anchored before it was called
      green — a SKIP is not a pass, and a spec that silently skips is
      the same failure as a scanner that reports zero.*
    - *Carried lesson from r5, the general form of the r3 one: **an
      exclusion list guarding a destructive decision is a denylist and
      fails open on everything nobody thought of**, including fields a
      future commit adds. It appeared twice in one fix layer — twelve
      "bookkeeping" fields in the dedup key, and an OK verdict meaning
      "mentions the safe parser somewhere". Both fixes are the same
      move: state the small set you can prove and treat the rest as
      unproven. And measure the strictness rather than assuming it —
      "that would be too strict" is exactly the claim that needs an
      executing line.*
    - *Its adversarial r6 (2026-08-20, FIVE codex seats on the r5 fix
      layer): **REJECT — sixth round, sixth fix-layer HIGH.** Six
      findings, all six reproduced, zero hallucinations for the second
      round running. (1) **Absence is not a default for the fields this
      verb ACTS ON.** r5's raw-value checks used `if name in d`, so an
      absent field was fine — but a missing `content_hash` makes the
      stale check answer "not stale" (nothing to compare), `created_at`
      is the tiebreaker, and BOTH are excluded from the dedup identity,
      so neither absence shows up as a difference. Probed end to end: a
      clone with the hash omitted and a later timestamp validated,
      grouped, won, and DELETED the verified row. Both required now;
      the live store carries both on 423/423. (2) **The taint check
      could take the channel down**: `_carries_surrogate` recursed,
      JSON nested ~600 deep (which json.loads parses fine) blew the
      stack, and RecursionError is not a JSONDecodeError — so it flew
      through the `except (JSONDecodeError, TypeError)` every caller
      uses to strand a bad row, killing InterruptQueue.poll() before it
      could announce. Iterative now; a shared helper with 84 call sites
      does not get to raise what its callers do not catch. (3) The raw
      scan covered only U+DC80-DCFF (the surrogateescape range), so a
      lone HIGH surrogate from anywhere else was admitted and re-dumped
      as a clean escape — whole U+D800-DFFF block now. (4) **The
      tiebreaker compared TEXT, not time**:
      `2026-01-01T00:00:00+14:00` sorts after
      `2025-12-31T23:00:00-12:00` lexically and BEFORE it in real time,
      so the older row was kept and the newer deleted — both valid,
      nothing in the output saying which went; ranks by parsed instant
      now (naive read as UTC — both shapes are live and max() over
      mixed awareness raises). (5+6) Scanner: the "no bare json.loads"
      rule matched ONE SPELLING, so `import json as j` and `from json
      import loads` walked past it — any non-clean `loads` call counts
      now, whatever module; and separator resolution kept the LAST
      binding in AST-walk order (not control flow), so a conditional
      binding or a later reassignment made a live JSONL rewrite vanish
      from the scan — one binding proving non-newline, or it is
      framing. Receipts: spec 69 -> 76, 76/76 (2 SKIPs re-anchored
      first); scanner blast radius on the real tree ZERO (77 RISK
      before and after, manifest green).*
    - *Its adversarial r7 (2026-08-20, FIVE codex seats on the r6 fix
      layer): **REJECT — seventh round, seventh fix-layer HIGH.** Seven
      findings, all seven reproduced, zero hallucinations for the third
      round running. (1) **The rewrite REORDERED the store, and order
      decides which skill is live.** `load_skills` reads the file in
      reverse and lets the last row for an id win; the rewrite appended
      stranded rows after admitted ones, and r6's stricter validator
      strands more — so a legacy row sharing an id with a verified one
      was promoted from ignored to LIVE, in a run that printed "0
      removed" and "kept in place". Every byte survived; the meaning
      did not. Written in read order now, and the positions ride a side
      table keyed by object identity: stamping `row["__ordinal"]` would
      have joined `_dedup_identity`, made every row unique, silently
      disabled the dedup, and shipped the key into the store. (2) **A
      mixed-awareness group cannot be ranked and r6 ranked it** —
      `replace(tzinfo=utc)` is not a conversion, it asserts a fact the
      row does not carry, and r6 deleted a row on that invented
      instant; undecidable groups are kept whole with a reason. (3)
      **The PARSER's own recursion limit was still uncaught**: r6 fixed
      the walk and left `json.loads`, whose RecursionError is not a
      JSONDecodeError — a ~50k-deep row killed `poll()` before it could
      strand or announce. (4) The r6 walk's memory followed WIDTH
      (MemoryError on a valid 5M-item row under a 96 MiB cap) — stack
      of iterators now, storage follows depth. (5) **A verdict about
      safety cannot be read off an identifier**: r6's better spelling
      rule died four ways — `from json import loads as parse` invisible,
      `parse_json = json.loads` invisible, and `from json import loads
      as _loads_clean` TRUSTED; parser identity now comes from the
      binding, raw beating every naming convention. (6) `sep: str =
      "\n"`, `sep += "\n"` and `(sep := "\n")` were not counted as
      bindings, so a live JSONL rewrite vanished from the scan
      entirely — neither RISK nor OK. (7) A repair verb that destroys a
      row must NAME it: "keeping best of 3 identical copies of 'x'"
      identifies rows by the two things that cannot tell them apart;
      each kept and removed row now carries id and created_at, and
      "unprovable as a skill" is no longer summarised as corruption.
      Receipts: spec 76 -> 93, 93/93 first sweep (11 anchors moved by
      the r7 fixes re-anchored first, 1 new equivalent recorded); 9811
      tests pass; scanner blast radius ZERO for the second round
      running (77 RISK before and after, manifest green).*
    - *Its adversarial r8 (2026-08-20, FIVE codex seats on the r7 fix
      layer): **REJECT — eighth round, eighth fix-layer HIGH.** Six
      findings accepted, all six reproduced, zero hallucinations for the
      fourth round running, and THREE of them are the same mistake in
      three files: a denylist. (1) `loads_clean` translated
      `RecursionError` — the one class r7 had met — and r8 walked past
      it with the next: CPython caps int-from-string conversion at 4300
      digits and raises ValueError, so a 5000-digit number in a queue
      row killed `InterruptQueue.poll()` before it could strand or
      announce. Same finding as r7, one exception class over, inside
      r7's own fix. General rule now: if the parser did not return a
      value, the line does not parse. (2) The doctor's corrupt-vs-
      unprovable split matched the exception's class NAME against three
      strings; a 401-digit `success_rate` (valid JSON, readable bytes,
      OverflowError) was reported as byte corruption. Parse and
      validation are separate try blocks now and the kind is recorded
      where it is known. (3) The scanner kept a fallback for the
      conventional SPELLING — added so r7's own fixtures would pass — so
      `from untrusted_parser import loads_clean` was trusted on the name
      alone, in the round whose finding was "a verdict cannot be read
      off an identifier". (4) Parser identity was module-wide, so a
      parameter, a default argument or a local rebinding shadowed the
      import and still read OK; shadowing revokes now, with the
      must-detect other half pinned (half this codebase imports the
      wrapper INSIDE the function). (5) r7 enumerated binding NODE
      TYPES and r8 found the two it had not thought of — a tuple target
      and a `match` capture — each making a live JSONL rewrite vanish
      from the scan entirely; the census counts Store-context names now.
      (6) 5/5 seats independently: r7 named the ROWS it destroys and
      never named the FILE — the path is now on the header, every
      stranded row, the strand summary and the closing count, and the
      stale branch names created_at like the duplicate branch. REJECTED
      one finding (loads_clean admits NaN/Infinity): json.dumps re-emits
      both verbatim so a rewrite carries them faithfully, and the
      ranking inputs are already proven finite — recorded below as a
      strictness question, not a defect. Receipts: spec 93 -> 110 with
      SIX survivors on the first sweep (five were holes in the new
      tests), 110/110 after; 9837 tests pass; scanner blast radius ZERO
      for the third round running.*
    - *Its adversarial r9 (2026-08-20, FIVE codex seats on the r8 fix
      layer): **REJECT — ninth round.** Six findings, all six
      reproduced, zero hallucinations for the fifth round running, and
      for the FIRST time the top finding is not in the previous round's
      fix — it is older than the arc. **`str.strip()` is not JSON
      whitespace.** Every reader here parsed `raw.strip()` and carried
      `raw`; JSON's whitespace is space/tab/CR/LF only, so
      `"\u2028" + a valid row` parsed after stripping, was admitted, and
      came back re-serialised with those bytes gone and nothing
      announced — and `if not raw.strip(): continue` deleted a row of
      U+00A0 outright. The finding named doctor; the sibling census
      found four more, two of them worse: `gc_memory` read the timestamp
      that AUTHORIZES a delete out of the laundered copy, and
      `skills.save_skill` parsed AND WROTE the stripped copy. One helper
      answers all five (`jsonl_utils.is_frame_blank`: only the empty
      fragment is framing). Also: `loads_clean` now refuses
      NaN/Infinity/-Infinity — the finding r8 REJECTED, correctly
      reasoned and wrongly concluded, because a row does not have to be
      laundered to do damage, only ADMITTED (probed: it joins a dedup
      group and evicts the older row); zero live rows carry the tokens.
      Scanner: `ast.walk(fn)` descended into nested scopes so a nested
      import re-proved the outer function's raw parameter; a dotted
      proof outlived its receiver (`def rewrite(path, jsonl_utils)`);
      module identity was still a suffix test (`vendor.jsonl_utils`
      trusted, and the r8 fixture had tested the shape that cannot
      fire); and a bare-name dict in the call-graph leg let an unrelated
      `B.save` make a destructive helper VANISH from the scan — the
      fourth disappearance this arc has paid for. Receipts: spec 110 ->
      129 with FIVE survivors on the first sweep (all holes — the
      interrupt tests' "whitespace row" was a literal space, which
      every json.dumps row already contains, so the assertion could
      not fail), 129/129 after; 9867 tests pass; scanner blast
      radius ZERO for the fourth round running.*
    - *Its adversarial r10 (2026-08-20, FIVE codex seats on the r9 fix
      layer): **REJECT — tenth round.** Seven findings, all seven
      reproduced, zero hallucinations for the sixth round running, and
      FOUR of the five seats reached the top finding independently by
      four different routes — the strongest consensus of the arc.
      **The shared READER admits what every writer refuses.** Nine
      rounds hardened the write paths; `read_jsonl_announced` — the one
      door most loaders here use — still parsed with bare `json.loads`
      on a `bytes.strip()`ed copy, so the whole launder chain
      reassembled from the read side: `load_skills` built a Skill from
      a row `loads_clean` rejects, `_save_skills` wrote a CLEAN
      re-serialised copy AND stranded the raw one, and the laundered
      twin (landing last) then won last-row-wins. The same door admitted
      `NaN`, duplicate names and `\x0b{...}`. Fixed at the helper;
      census first: 141,094 live rows across 1,061 stores, ZERO flips.
      Also: `_read_skill_stats` was the last read->rewrite pair still on
      the r8 idiom and destroyed data BOTH ways (a U+2028 row split in
      two and rejoined with LF under a log line saying "verbatim"; a
      U+00A0 row deleted uncounted); both skill writers let an
      UNPROVABLE row decide its own removal, so a schema-drifted row was
      deleted by an unrelated outcome update — the rule
      `validate_skill_row`'s docstring has stated since r3, with two
      callers never moved onto it; `_save_skills` appended strandees to
      the TAIL of a last-row-wins store, which is a promotion, not
      preservation; the interrupt PREFLIGHT kept the `l.strip()` idiom
      r9 removed from its own merge loops, so a queue of nothing but an
      unreadable row reported empty in silence; and the scanner's cycle
      detection was still keyed by bare name, so reversing two
      definitions made a destructive reader vanish from the scan — the
      fifth disappearance this arc has paid for. Side-find, the only
      site this round FOUND rather than re-found:
      `memory_backends.JSONLBackend.read_all` was carrying families A
      and B at once next to a `rewrite()` whose own comment names the
      read->transform->rewrite pattern. Gate drift handled out loud:
      making the scanner lexical moved NINE sites' framing into their
      `locked_rmw` closures, so the manifest records each move with the
      inner site that owns it now plus a fifth leg (`blind`) that
      re-checks the twin is still visible — an exemption that keeps
      paying for itself. Receipts: spec 129 -> 146 with SEVEN needing
      work on the first sweep (five were holes in this round's own new
      tests — the U+2028 fixture never held a U+2028 because json.dumps
      escapes it; the ordinal test put the carried row first, where
      tail-append lands it in the same place; the "broken" row used a
      field dict_to_skill assigns without complaint; nothing
      distinguished the proof from the constructor because every fixture
      used a row BOTH reject; and the deep-nesting test went vacuous in
      this round, when the taint gate tightened past its payload),
      146/146 after; 9898 tests pass;
      manifest green at 72 RISK sites; the reader's stricter parse cost
      248ms -> 1098ms on the largest live store and was brought to 730ms
      by replacing a per-character surrogate loop with
      `isascii()` + a UTF-8 encode.*
    - *Its adversarial r11 (2026-08-20, five codex seats on the r10 fix
      layer): **REJECT — eleventh round.** Ten findings, all ten
      reproduced, zero hallucinations for the SEVENTH round running,
      and the HIGH was UNANIMOUS — all five seats, five independent
      routes, the same two lines: **`rewrite(read_all(...))` deletes
      the strandees `read_all` announces.** r10's announced reader
      strands a torn row correctly and returns a list WITHOUT it;
      `rewrite()` wrote that list back, so the documented composition
      deleted the exact row the log had promised was safe — and the
      r10 test NAMED for this composition never called `rewrite()`.
      Fixed where the destruction lives: `rewrite()` re-reads under its
      own lock and carries every line `loads_clean` refuses, verbatim.
      The round's organizing lesson: **constructible ≠ provable ≠
      deliverable.** `dict_to_skill` and `Interrupt.from_dict` are
      constructors, not validators, and the gap between a tolerant
      loader and a strict writer is a LAUNDER MINT — a stored
      `"utility_score": "1.0"` loaded fine and `_save_skills` emitted a
      normalized clone (float 1.0) that won last-row-wins over the
      operator's bytes. One admission predicate on both ends (admitted
      == provable; `load_skills` on `validate_skill_row`, id claimed
      only AFTER proof — which also closed the shadow-delete) kills the
      clone structurally. Also: `_prove_line` — a writer must not mint
      what its own reader strands (`json.dumps` writes `NaN` and clean
      `\udcXX` escapes by default; save now aborts BEFORE the store is
      touched); an undeliverable interrupt was marked applied on disk
      and lost on retry (`_prove_deliverable` before every applied-mark
      — poll, clear, peek); JSON `1`/`true` collide as one Python dict
      key so the keyed stats rebuild silently deleted a row (non-string
      ids strand now); routine counter bumps rebuilt stats rows from
      the model and deleted every field it doesn't know (recorders now
      merge over the stored row); a proof inside a GENERATOR EXPRESSION
      certified a raw rewrite (deferred code may never run —
      asymmetric rule: clean-in-genexp proves nothing, raw-in-genexp
      still poisons, eager comprehensions keep proof value);
      `json.JSONDecoder().decode` was invisible to the scanner; a
      MOVED site coming back under its OUTER name passed the gate
      (sixth leg `resurfaced`, the only watch on the twinless
      `llm.py:_run_subprocess_safe` — exemption doctrine's fifth
      application); and F4 ACCEPTED WITH REASON, pinned: the final torn
      frame gains a terminator LF on purpose, because preserving the
      missing LF lets the next append concatenate into the torn
      fragment. Censuses before the flips, all zero-cost: 423/423
      skills, 2/2 interrupts deliverable, 203/203 stats string-keyed,
      no NaN in either store; manifest green at 72 RISK sites.
      Receipts: spec 146 -> 165 (5 re-anchors first — a SKIP
      is not a pass), 163/165 on the first sweep with both gaps
      teachers: the clear-launder survivor was a hole r11's own fix
      created (`_prove_deliverable` strands a field-poor row on its
      own, so the torn fixture stopped distinguishing the taint door
      from the proof door — the killing fixture must be
      deliverable-shaped with one raw byte in `message`), and the
      NaN-proof mutant is a marked twin-lock equivalent
      (`_loads_clean`'s `parse_constant` refuses the token
      `allow_nan=False` would not mint — same abort either way).
      165/165 after; 24 new must-detect tests incl. rewriting the r10
      backend test whose NAME promised the composition it never ran,
      and an accept-with-reason pin on F4.*
    - *Its adversarial r12 (2026-08-20, five codex seats on the r11 fix
      layer): **REJECT — twelfth round.** Eight findings, all eight
      reproduced, zero hallucinations for the EIGHTH round running.
      The r11 proof proved parse-clean, not admission (`_prove_line`
      on bare `_loads_clean` while the reader admits via
      `validate_skill_row` — a constructible `tier=7` skill replaced
      the healthy row and stranded on the next load); every generic
      JSONL writer could outrun its reader (`json.dumps` writes lone
      surrogates as clean `\udcXX` escapes and NaN as the CPython
      token — new shared door `jsonl_utils.prove_record_line` behind
      backend append/rewrite and the stats writer, payload built
      before the write so refusal leaves the store intact); the
      interrupt QUEUE accepted under a weaker predicate than its
      consumers deliver by (post() now proves the line + deliverability
      before locked_append — an operator STOP with a surrogate was
      acknowledged and never deliverable); the rewrite strand rule
      mirrored the parser, not the reader (clean non-dict JSON —
      `null`, arrays — deleted; now stranded the way read_all
      excludes them); the stats store repeated the skills arc one
      round behind (coercing `SkillStats.from_dict` laundered drifted
      rows, `bool("false")` is True — new raw-value
      `validate_skill_stats_row`, census 203/203 before the flip;
      recorders refuse non-string/non-encodable ids at the door;
      duplicate string ids compact last-wins but ANNOUNCED now); the
      decoder rule fell to five spellings, one per seat (import
      alias, object alias, bound method, AnnAssign, raw_decode —
      provenance now resolved to a fixpoint, walrus too, all six
      fixtures RISK, bytes-decode control holds, blast radius
      unchanged 72 RISK); and the manifest resurfaced leg was blind
      to an OK-verdict resurfacer (now reads `seen` — ANY verdict
      falsifies the move premise). F5 ACCEPTED WITH REASON, pinned:
      hash equality stays out of the admission predicate — the
      content_hash is a tamper-evident tripwire, not a boundary (no
      secret key: a forger writes a valid hash; stranding would
      misfile legitimate operator hand-edits while stopping no
      attacker). Receipts: spec 165 -> 185 (8 re-anchors first — a
      SKIP is not a pass; 1 new twin-lock equivalent, the
      prove_record_line NaN leg, same shape as r11's), 16 new
      must-detect tests. Sweep: 185/185 accounted for on the FIRST pass — 179 detected +
      all 6 marked equivalents (5 standing + this round's new
      twin-lock) surviving as claimed; the arc's first
      zero-survivor, zero-SKIP first run.*
    - *Its adversarial r13 (2026-08-20, five codex seats on the r12 fix
      layer): **REJECT — thirteenth round, findings narrowing** (no
      unanimous HIGH; mostly r12's own new code + twins the arc had
      not visited). All probed real — NINTH zero-hallucination round.
      Presence-is-not-absence (validate_skill_stats_row read d.get(),
      so a stored JSON null rode the absence exemption and bool(None)
      laundered it to false — every modeled field now checks `name in
      d`, present null strands); recorder doors (truthy "false"
      recorded a failure as a success; NaN telemetry discarded behind
      a normal return — both TypeError at the door now, wrappers name
      path+skill); _write_skill_stats proves the reader's FULL
      predicate; _archive_skills — the retention decree's own writer —
      proves every line before any append; rewrite audit honesty
      (strandees FIRST, announce after commit, I/O failures propagate
      loudly with the path); new transform() = read→fn→write under ONE
      lock (bare read_all→rewrite is an undecidable lost-update race;
      no production caller composes it today, so API hardening) with a
      lock-held pin; SQLite twin brought to full doctrine parity (one
      MARO_MEMORY_BACKEND flip from live, had silent-drop +
      DELETE-all); scanner provenance now a lattice (ctor-assign
      aliases to fixpoint, method-alias chains, tuple destructuring,
      _parser_names on _bindings, dotted attribute paths, class-level
      self.* map across sibling methods) — blast radius 72 RISK
      unchanged. JUDGED not fixed: admission stays TYPE-level
      (plausibility auditing is an inspector's job; total_uses=-4 is
      faithfully representable — documented in the validator
      docstring). Receipts: spec 185 -> 209 (7 re-anchors first),
      16 new must-detect tests. Sweep: 208/209 first pass; the one
      survivor was the guard-in-front-of-a-guard hole again (the
      composition test matched a substring both guards emit, so the
      read-side drop hid behind the rewrite's carry-through) —
      fixture now pins the read's own announcement; 209/209 after.*
    - *Its adversarial r14 (2026-08-21, five codex seats on the r13 fix
      layer): **REJECT — fourteenth round, still narrowing in kind**
      (one unanimous HIGH; every finding in r13's own new surface or a
      twin it named but did not convert). All 13 deduped findings
      probed real — TENTH zero-hallucination round. The contract did
      not travel (r13's transform() reached JSONL only — now abstract
      on MemoryBackend, and SQLite got a real one: BEGIN IMMEDIATE
      before the read, proven emissions, vouched-only deletes, whole
      rollback; threaded barrier test pins the append-cannot-land-
      inside-the-window property); a failed SQLite read returned []
      (indistinguishable from verified-empty — a transient lock error
      fed rewrite an empty list and deleted the store; raises now);
      transform honored fail-open (bare locked_write degrades unlocked
      under MARO_FILELOCK_FAIL_OPEN — require=True now, contended
      fail-open raises before fn); identity joined the stats predicate
      (validate_skill_stats_row now requires non-empty string
      skill_id; writer also refuses map-key/row disagreement); the
      stats writer kept the pre-r13 ordinal (strandees ride FIRST
      now); a pure read claimed a rewrite (read announces
      exclusion-from-this-read; carry-through moved to the writer,
      after commit); the router trained on rows every reader strands
      (bare json.loads + int()/float() coercion laundered drifted rows
      into confident evidence — rides _read_skill_stats now); the
      archive batch could split mid-write (one locked append per
      batch; duplicate-on-retry accepted: noise, not loss, in an
      append-only retention store); scanner provenance stopped at four
      more boundaries (instance-held ctor aliases, class-body
      bindings, same-module inheritance, positional-only receivers —
      plus the destructured-parser leg and a THIRD private copy of the
      binding walk in _shadowed, found by this round's own negative
      control; all fixpoints now, blast radius unchanged 72 RISK).
      JUDGED not fixed: the framing LF on a torn final strandee —
      accept-and-pin (payload bytes unchanged, round-trip stable; the
      LF is required framing once strandees ride first; pinned
      byte-for-byte in both writer families). Receipts: spec
      209 -> 230 (7 re-anchors first), 31 new must-detect tests.
      Sweep: 229/230 first pass (223 detected + 6 standing
      equivalents); the survivor taught about the FIX, not the tests
      — the inner class-walk fixpoint was a redundant second loop
      (scan_module's outer class-graph fixpoint re-seeds until
      convergence either way), so the redundant loop was removed
      rather than marked equivalent and the mutant retargeted at the
      outer fixpoint; 230/230 after.*
    - *Carried lesson from r9: **the idiom everyone writes is the one
      nobody reviews.** `line = raw.strip()` sat in five readers,
      survived eight adversarial rounds whose subject was "what can a
      rewrite do to bytes it cannot read", and survived because it looks
      like tidying rather than a decision. When auditing a destructive
      reader, list every transformation between the bytes on disk and
      the value the decision is made from, and require each one to be
      either identity or announced. Corollary from the NaN reversal:
      **"it round-trips faithfully" is not the same as "it may take part
      in the decision"** — when rejecting a finding, name the property
      you are relying on and check that it is the property that
      matters.*
    - *Carried lesson from r10: **a safety rule enforced on the write
      path and not the read path is not enforced.** Nine rounds hardened
      every rewrite against byte-tainted rows; the chain reassembled
      itself through the LOADER, because the writers then serialise the
      in-memory OBJECT faithfully — which is a clean copy of a row
      nobody vouched for. Four seats found it from four call sites in
      one round: that is what a rule with a missing half looks like from
      outside. Corollaries: **a scope-aware rule applied to two of its
      four scans is not scope-aware** (the half that still walks is the
      half that decides — same shape as r10's name-keyed `seen` beside a
      name-collecting `by_name`, and its converted-two-of-three merge
      loops); and **an exemption must carry a proof that keeps being
      checked** — `FIXED` needed `regressed`, `regressed` needed
      `vanished`, and r10's `MOVED` needed `blind`, or it is a deletion
      with a comment on it.*
    - *Carried lesson from r11: **constructible ≠ provable ≠
      deliverable.** A constructor that assigns fields is not a
      validator, a validator on the writers is not one on the loader,
      and a row that loads is not yet one the consumer can act on. The
      launder twin (r10: read side; r11: the loader/writer GAP) keeps
      reassembling from whichever half of the round trip trusts a
      different predicate than the other — the structural kill is ONE
      admission predicate on both ends, admitted == provable, and a
      writer that re-reads its own emission through the reader's door.
      Corollary: **a test whose name promises a composition must run
      the composition** — the r10 backend test named read-then-rewrite
      and called only the read, which is how the round's unanimous HIGH
      shipped under a green suite.*
    - *Carried lesson from r12: **provenance, not final call syntax,
      decides — and a rule keyed on spelling invites one bypass per
      spelling.** All five seats independently walked past the r11
      decoder rule, each with a different standard Python spelling of
      the same dataflow (import alias, object alias, bound method,
      AnnAssign, walrus, raw_decode). A syntactic match on the shapes
      you have met is the denylist lesson (r8) wearing AST clothes;
      the fix is to track where the VALUE came from, resolved to a
      fixpoint, and let the call site inherit the verdict. Two
      corollaries: **a queue must not accept under a weaker predicate
      than its consumer delivers by** (the producer is a door too —
      prove at post(), not just at poll()); and **the strand rule must
      mirror the reader's full admission, not just its parser** (what
      read_all announces-and-skips was never in the caller's list, so
      the rewrite deletes it unless the re-read strands it).*
    - *Carried lesson from r13: **doctrine that does not travel to the
      twins is local luck.** Three rounds of JSONL doors while the
      selectable SQLite twin kept silent-drop + DELETE-all; the
      retention archive was a writer nobody audited; _parser_names
      kept a private Assign-only walk one round after _bindings
      learned the other forms. When a rule hardens, enumerate its
      twins by ROLE (config twin, sibling writer, private copy of a
      shared walk) and convert them in the same commit. Corollary:
      **presence is not absence** — d.get() collapses "stored null"
      into "not stored" and exempts exactly the value the constructor
      launders most quietly (bool(None)); check membership, then the
      value.*
    - *Its adversarial r15 (2026-08-21, five codex seats on the r14 fix
      layer): **REJECT, still narrowing** — 7 deduped findings against
      13, two HIGH clusters, every finding again the prior round's fix
      stopping one twin short. All probed real — ELEVENTH consecutive
      zero-hallucination round. The require-lock fix did not travel
      (both skill-stats RMW recorders rode bare locked_write — fail-open
      lost one of two concurrent outcomes with two normal returns;
      require=True now, contended fail-open pin + structural keyword
      pin); a failed stats write returned an ordinary None (warn-and-
      return converted disk-full into apparent success — recorders
      RAISE now; all three production callers already wrap and degrade
      visibly); the duplicate lane still announced from the read (a
      pure read logged "will be compacted by the next rewrite" —
      _StatsRead carries .compacted, the read announces an exclusion
      from itself, the writer announces compaction after commit); the
      strict-reader fix missed the sibling input (router's skills.jsonl
      side raw-loaded rows validate_skill_row rejects into training
      features — rides read_jsonl_announced + validate_skill_row now;
      the site left the scanner's view because the framing moved into
      the shared reader, recorded in the manifest, 71 RISK); the class
      graph read bases literally (Alias = Base and Base[str] both
      severed provenance — module-level alias lattice over the shared
      binding walk, Subscript unwrap, ambiguity unions toward RISK,
      negative control pins aliases cannot MINT provenance); the
      retention archive rode the page cache while the delete it
      justifies fsyncs (locked_append gains require=/durable= — flush,
      fsync file, fsync new-file parent dir; the archive uses both);
      and a committed sqlite transform reported "store unchanged" on a
      post-commit close failure (committed flag; the message now says
      the store HOLDS the transform, do not retry). REJECTED with
      reasons: Minimalist's cross-module conservative-RISK ask for the
      scanner — contradicts r11 receiver-decides; cross-module, the
      receiver's own module owns the proof.*
    - *Carried lesson from r14: **the fix for "doctrine must travel"
      must itself travel — convert the twins in the SAME commit, not
      the same arc.** r13 wrote the twin-conversion lesson and its own
      fix layer shipped transform() to one backend of two, an ordinal
      rule to one writer of two, and a shared binding walk to two call
      sites of three; r14 was almost entirely that gap. Enumerate the
      twins BEFORE writing the fix, and let the round's own fixtures
      double as the census. Corollaries: **an error result must not be
      a valid value** (returning [] for a failed read hands the caller
      a verified-empty store; raise, or return a type that cannot be
      consumed); and **a degraded mode must not include the failure
      the feature exists to prevent** (a one-lock transaction that
      honors fail-open is the race wearing the fix's clothes —
      require the lock).*
    - *Its adversarial r16 (2026-08-21, five codex seats on the r15 fix
      layer): **REJECT — the property census proved itself**: 9 deduped
      findings, three HIGH clusters, all probed real (TWELFTH
      zero-hallucination round). Absence was read as a decision
      (_save_skills deleted any proven row absent from the caller's
      unlocked snapshot — concurrent saves silently destroyed, no
      archive copy; drops must now be NAMED via dropped_ids, absence is
      carried; cull/retirement/rollback name theirs; residual: a named
      id updated after selection archives the pre-update version,
      upgrade edge = in-lock transform primitive); the r15 raise
      travelled but its callers didn't (memory_ledger's per-id
      attribution loop → partial batch → retry double-count; batch
      record_skill_injection_outcomes now, one write or none;
      marker-window residual recorded next to interrupt F9); the alias
      lattice stopped at the module (class-body/factory-local aliases +
      rebound class names severed provenance — every scope feeds the
      map, rebinding unions both); the durable append could fuse rows
      onto a torn tail (LF-frames the fragment first); save_skill/
      _save_skills/locked_rmw joined the require-lock property;
      _save_skills raises and names the store (was warn-and-None —
      "retired" with every skill live); recorder error coverage widened
      to the whole transaction + DEBUG catch sites promoted to WARNING;
      commit() raising after durability now says outcome-UNKNOWN;
      _StatsRead survives the copy protocols.*
    - *Its adversarial r17 (2026-08-21, five codex seats on the r16 fix
      layer): **REJECT — the collision twin**: 12 deduped clusters,
      three multi-seat HIGHs, all probed real (THIRTEENTH
      zero-hallucination round). Possession was read as ownership
      (_save_skills let a stale snapshot's copy replace a concurrently
      REVISED live row — updated_ids is the write twin of dropped_ids:
      only a named id takes the caller's version, unnamed rows carry
      verbatim from the live store, stale copies cannot resurrect
      deleted rows, contradictory intent refused; all 11 call sites
      name their writes — A/B retirement also mutates its promoted
      parents, which only the census caught); locked_append's tail
      inspection failed open (unreadable tail now refuses the append);
      evolver skill_create rollback deleted every name-match with no
      retention (created id minted at capture + recorded in the audit
      row, rollback by exact id, archive-before-delete; bookkeeping
      failure warned not swallowed); the attribution marker was trusted
      by presence, checked outside any lock, and its write failure lied
      post-commit (check→batch→marker under the stats lock, content
      validated — invalid = UNKNOWN never auto-re-applied, honest split
      messages); manifest ids str()-coerced into stats identities (both
      readers admit strings only, announced); batch recorder
      double-counted duplicates / would iterate a bare string (doors
      added); SQLite append/rewrite joined transform's commit-boundary
      contract; scanner's flattened alias map minted false RISK from
      unrelated scopes (per-class lexical-chain resolution, negative
      control pinned). Partial accept: backend rewrite()'s "announced
      by design" docstring claim was false and is corrected — the
      omission-deletion interface itself is BACKLOG'd below (unkeyed
      collections, zero production callers, transform() is the
      sanctioned API). Rejected: writer-side manifest validation
      (receiver-decides, r11/r15 precedent). Mutation spec 258 → 279
      (11 re-anchored, 21 new; the r16 structural pin was
      indentation-bound and is now regex-based).*
    - *Its adversarial r18 (2026-08-21, five seats on the r17 fix
      layer — **sonnet-medium lane**, codex usage-capped until 08-27;
      same-model fallback declared per the skill): 12 findings, six
      clusters, all probed real. The marker validated its verdict by
      TYPE not value (a legitimately corrected re-stamp was silently
      absorbed and skill stats kept the stale verdict forever — now
      compared by value and announced, never auto-re-applied); r17's
      own tail append resurrected deliberately dropped rows when a
      named id had vanished (naming is not creation — dropped and
      announced, the deletion stands); the evolver audit row and its
      action read the world twice (a racing create made the audit row
      record a phantom skill_create for what ran as an overwrite —
      one snapshot drives both now); a mutated-but-unnamed row died in
      silence (divergence warning added — the omission twin of the
      contradiction ValueErrors); commit-boundary honesty narrowed to
      sqlite3.Error (widened; close-bomb tests pin the committed
      branch); drop announcements count physical rows; hash backfill
      scoped to named writes. Mutation spec 279 → 290 (4 re-anchored).*
    - *Carried lesson from r18: **a claim must be checked against the
      world it claims about** — shape validation (well-formed id, a
      bool, a parseable row) catches corruption; only world validation
      (does the row still exist, does the value match, did the action
      read the same world the record describes) catches staleness.*
    - *Open item (r18, Architect — scale note, deferred): the
      attribution seam serializes ALL runs' verdict stamps behind the
      one global skill-stats lock and holds it across manifest/marker
      I/O; correctness-safe, but a choke point if injection volume
      grows. Upgrade edge: a per-loop marker lock under the run dir,
      reserving the stats lock for the stats mutation.*
    - *Its adversarial r19 (2026-08-21, five sonnet-medium seats on
      the r18 fix layer): 12 findings, four clusters, all probed. The
      r18 divergence warning asserted "the caller's edit" for what is
      usually legitimate concurrent staleness (message now names both
      causes); the r18 snapshot-reuse WIDENED the update path's stale
      window and save_skill's whole-row replace silently reverted
      concurrent field advances — an open breaker reset, a demotion
      undone (snapshot now decides classification only, the mutated
      row is re-read fresh at the last moment, a vanished row refuses
      the update); the ghost message said "concurrently removed" for
      rows physically present but unprovable and for ids never created
      (three truths told apart, hedged where ids are unrecoverable);
      transform()'s fn-attribution judged accepted with a comment.
      Mutation spec 290 → 295 (2 re-anchored).*
    - *Its adversarial r20 (2026-08-21, five sonnet-medium seats on
      the r19 fix layer): 11 findings, four clusters, all probed real —
      each one the previous round's fix missing from the operation next
      to it. Named DROPS never got r19's three-way treatment (a drop on
      a stranded row silently no-op'd; a drop leaving an unprovable
      duplicate said nothing — stranded_dropped / partially_dropped now
      announced like writes, five seats HIGH); the ghost hedge counted
      only byte-tainted rows and asserted absence over id-less
      unprovable rows (unprovable_unnamed now hedged); r19's fresh
      re-read left the TOCTOU open — no lock spanned read→write, so
      save_skill's blind upsert still reverted breaker trips landing in
      the tail (whole RMW now inside locked_write(require=True),
      reentrancy composes; structural source-order pin because a
      behavioral race test cannot see reentrant composition);
      revert_suggestion restored the snapshot value over LATER
      concurrent edits reporting reverted:True, and the apply clobbered
      unannounced (revert refuses — "blind restore refused"; apply
      announces the clobber and names the audit row's snapshot basis).
      Judged, not fixed: a distinct "vanished" suggestion status (log
      names the cause; enum churn buys nothing). Mutation spec
      295 → 302 (3 re-anchored).*
    - *Open item (r20, deferred): unprovable duplicate rows are
      carried verbatim forever — a store that keeps tainting
      accumulates zombie twins that partial drops must keep
      announcing. The fix is a repair verb (parse, prove, rewrite or
      quarantine by operator decision), not more carrying; build it
      when a real store shows accumulation.*
    - *Its adversarial r21 (2026-08-21, five sonnet-medium seats on
      the r20 fix layer): three clusters, all probed — and all three
      the r20 lesson firing on its first outing, r20's own fixes
      missing from the operation next to them. The drop buckets were
      built from strand_ids so a byte-tainted or id-less row's named
      drop landed in NO bucket (silent no-op, "removed" fired as an
      unhedged completeness claim while a tainted duplicate survived
      — new unaccounted_dropped bucket announced with the hedge when
      unreadable rows rode the rewrite; provably absent drops stay
      silent; the removed claim hedges); revert_suggestion held no
      lock across its read→guard→write, the same-commit twin of the
      apply path r20 locked (whole branch now reads fresh and writes
      inside locked_write(require=True), structural pin); and the
      blind-restore guard was SKIPPED when suggestion_text was falsy
      — reachable today via an empty LLM suggestion — reinstating the
      pre-r20 blind restore (an unverifiable revert now refuses).
      Accepted LOW: stranded messages count logical ids not physical
      rows. Mutation spec 302 → 308 (3 re-anchored, incl. the r20
      revert-guard mutant retargeted at the split comparison door).*
    - *Its adversarial r22 (2026-08-21, five sonnet-medium seats on
      the r21 fix layer): three clusters — and the arc's first round
      where whole clusters came back "confirmed sound" under probing
      (the r21 drop partition and the skill_update lock both held).
      The skill_create revert archived the stale pre-lock snapshot —
      a concurrent edit racing the revert vanished from live store
      AND archive, the retention decree's own recovery path failing
      exactly when invoked (branch now reads/archives/writes under
      one lock; the shared pre-lock snapshot REMOVED so no branch has
      a stale authority to shadow); r21's fail-closed guard conflated
      absent with empty — an empty-suggestion apply wrote "" as the
      description, a fully verifiable value, and refusing it made
      those applies permanently un-revertible (absent still refuses,
      empty verifies); and the r21 lock had no committed behavioral
      pin — this round's own attempt PROVED the standing rationale
      (in-thread injection rides the reentrancy), so the pins are
      structural: no-read-precedes-the-first-lock, create-branch
      source order, plus a live archive-content test. Mutation spec
      308 → 312 (6 re-anchored).*
    - *Open item (r22, deferred): verify_post_apply logs a refused
      revert as a warning and moves on, where evolver_scans stamps
      degraded_revert_failed BLOCKING for the same outcome. Residual
      is legacy-rows-only after the absent/empty split; upgrade edge
      is porting the stamp, not a new verdict enum mid-arc.*
    - *Its adversarial r23 (2026-08-21, five sonnet-medium seats on
      the r22 fix layer): convergence signal — the only multi-seat
      agreement was a TEST-honesty finding: the r22 archive-content
      test passed on the pre-fix code (four seats each reran it
      against f7d0fdf3; the injection rides the single load_skills
      call in both shapes) — DELETED, the mutation-wired structural
      pins are the honest coverage. Production: an unrecognized
      before_state.type fell through both dispatch branches to the
      shared success tail — reverted:True, detail "", applied
      flipped, captain's log stamped, for a revert that never ran
      (now refuses by name); non-string suggestion_text slipped
      every door differently — 0→"" could blind-restore over a live
      "", a list refused via the misleading door, an int threw into
      the generic handler (present-but-not-a-string is now CANNOT
      VERIFY). Mutation spec 312 → 314.*
    - *Open item (r23, Architect, deferred): the r22 create-revert
      lock holds _archive_skills' durable fsync'd append (different
      file, nested lock) inside the skills-store lock — every
      concurrent skill writer blocks for the fsync. Deliberate
      correctness-over-throughput; revisit only if a latency hunt
      lands here.*
    - *Its adversarial r24 (2026-08-21, five sonnet-medium seats on
      the r23 fix layer): **CONVERGED — no HIGH or MEDIUM survived
      any seat's verification**, the arc's closing standard met.
      Every seat independently reran the r23 pins against pre-fix
      code, re-proved the deleted test decorative, probed the typed
      guard and fail-closed else with corrupted rows, and traced
      both production callers. Six LOWs applied in the closing
      commit: diagnostic names which door fired (absent vs
      wrong-typed), the else logs like its siblings + truncates
      repr(state_type), dead clause collapsed, the 0-over-live-""
      blind-restore fixture added (the specific hazard the
      parametrized test never reached), one history receipt
      corrected. Mutation spec closed at 314 after six zero-survivor
      first-pass sweeps this segment (r17–r24, spec 279 → 314).
      Lessons 19–25 minted; full arc record in
      docs/history/2026-08-20-destructive-rewrite-triage.md.*
    - *Carried lesson from r23: **prove the test against the defect,
      not against the fixed code.** A regression test's birth
      certificate is a run against the code it claims to catch — the
      discipline the mutation spec enforces mechanically, applied to
      hand-written tests where no runner checks it. Corollaries: a
      dispatcher's ELSE is part of its contract (fallthrough + a
      shared success tail = a forged receipt), and evidence has a
      TYPE — prove the record is text before comparing it.*
    - *Carried lesson from r22: **retention is a read too — archive
      the world you delete, not the world you planned to delete.** A
      recovery path built from stale bytes fails exactly when
      invoked. Corollary: fail-closed has a precision obligation —
      "cannot verify" must mean CANNOT, not "the evidence is falsy";
      refusing a verifiable case deletes the undo button and calls
      it safety.*
    - *Carried lesson from r21: **a guard that cannot verify must
      refuse, and a claim of completeness must hedge over everything
      it could not read.** `if evidence and evidence_says_stop:` reads
      as caution and is its opposite — the less evidence, the more
      freely the destructive path runs. Fail closed; hedge the
      announcement; the guard and the announcement are siblings of
      the verb they protect.*
    - *Carried lesson from r20: **a fix teaches one verb; ask which
      verbs share its preconditions before the reviewers do.** Writes
      learned three truths, drops kept two; the re-read shrank the
      window, the lock was the answer; the apply got snapshot
      discipline, the revert kept trusting its own. Corollary: a
      DELETION is a write — same proof standard, same three-way
      honesty, same refusal to report an effect it cannot
      demonstrate.*
    - *Carried lesson from r19: **decide from the snapshot, write from
      the world — and announce only what you proved.** A snapshot is
      the right authority for a decision, the wrong authority for the
      bytes written; an operator-facing message may state only what
      the scan proved, and names the ambiguity it cannot resolve —
      a guessed cause is how the one true firing gets ignored.*
    - *Carried lesson from r17: **a mutable interface must take its
      writes by name, not by possession** — "this id is in my list" is
      ambiguous between "I changed it" and "I happened to load it",
      and the writer resolved the ambiguity destructively; every
      mutation class carries explicit per-id intent, everything
      unnamed defaults to carry. Corollary: **presence is not proof —
      an idempotence token must be validated against what completion
      would have written, and checked inside the transaction boundary
      it guards.***
    - *Carried lesson from r15: **enumerate the twins by the PROPERTY,
      not by the file.** r14's census converted the twins visible from
      the defect's own file, and each fix still stopped one twin short —
      the twin lived behind a different spelling of the same property
      (every RMW over a keyed store, every destructive-claim log line,
      every training input — not "what else is in this file").
      Corollaries: **a retention copy must be at least as durable as
      the deletion it authorizes**, and **a post-failure message must
      state what the store HOLDS, not what the code intended**.*
    - *Carried lesson from r16 (two halves): **changing a callee's
      failure contract re-opens every caller as new surface — the
      composition path is part of the fix layer** (r15 made the
      recorders raise; the per-id caller loop turned that raise into a
      partial batch with a retry double-count; callers were censused
      for "do they crash", not "does their loop still compose"); and
      **a destructive interface must take its deletions by name, not by
      omission** ("absent from the list" cannot distinguish "I decided"
      from "I never saw it" — an explicit dropped_ids makes the
      decision the caller's and turns every unnamed absence into a
      carry).*
    - *Open item (r17, Architect — real, interface-level, deferred):
      the backend ABC's `rewrite(collection, records)` deletes by
      omission over unkeyed collections and cannot announce the loss;
      transform() is the sanctioned mutation API and rewrite() has no
      production caller outside transform's in-lock delegation. Upgrade
      edge: retire rewrite() from the public surface or give the ABC a
      deletions-by-name contract; docstrings corrected r17 to stop
      claiming the loss is announced.*
    - *Open design item (r12, Failure Operator — real, pre-existing,
      out of the byte-safety arc's scope): `poll()` durably writes
      `applied=True` BEFORE the loop side applies anything
      (`loop_post_step.apply_interrupt_fn`), so a crash between poll
      and apply loses a fully deliverable STOP — the retry converges
      to "nothing pending". The byte-safety doors cannot fix this;
      it is delivery semantics: separate claiming from
      acknowledgement (inflight lease/attempt token, idempotent
      apply, ack after the loop-side effect commits, startup
      re-delivers expired claims — or an intent-application journal
      keyed by interrupt id). Design work, not a patch.*
    - *Open item (r14, QA — real, display-surface, deferred):
      `packaging_readout._read_jsonl` walks stores with bare
      `json.loads` + `except: continue`, the same raw-loads shape the
      router fix removed. It feeds a human readout, not a trainer or a
      rewrite, so the stakes are presentation, not evidence or data —
      convert it to the announced strict reader when next touching
      that module.*
    - *Carried lesson from r8, the arc's most durable: **naming the
      cases you have met is a denylist, and a denylist in a safety check
      fails open on the case nobody has met yet.** Three files, one
      mistake: a list of exception classes to translate, a list of
      exception names to classify by, a list of AST node types that bind
      a name. The general form was available in all three and is shorter
      than the list. Where it genuinely is not available, count what you
      can prove and treat the rest as unproven — never a list that grows
      by one each round. Corollary from the same sweep: **a count-based
      assertion cannot fail in the direction it was written for** ("the
      path appears on >= 3 lines" passed with any one of the four
      announcements stripped).*
    - *Open, not a defect (r8, Minimalist): `loads_clean` admits the
      non-standard JSON constants `NaN`, `Infinity` and `-Infinity`.
      They round-trip faithfully through `json.dumps`, so nothing in
      this arc's doctrine is broken — but a STRICT third-party reader of
      one of our stores would reject the line. Whoever owns cross-reader
      compatibility should decide whether `parse_constant` should refuse
      them; the blast radius is 84 call sites, so measure before
      flipping.*
    - *Carried lesson from r7: **preserving every byte is not the same
      as preserving the meaning.** Every rule this arc wrote down
      guards the bytes — strand what you cannot read, carry it
      verbatim, announce the drop, never rewrite from the short list —
      and the r7 top finding broke none of them while still changing
      which skill the system executes. When a store's readers derive
      meaning from anything other than a row's own content (position,
      adjacency, file identity), that property IS part of the data and
      belongs in the preserve tests. Ask not "did I lose a row" but
      "could a reader tell the difference".*
    - *Carried lesson from r6, the one that makes six rounds worth it:
      **a correct refactor can delete a guard that was load-bearing
      under another name.** r5 replaced "validate the constructed
      Skill" with "validate the stored row" — strictly more correct —
      and silently removed the required-field check, because the old
      version had enforced it BY ACCIDENT (dict_to_skill defaulted a
      missing hash to "", and the empty check fired on the default).
      Nothing in the diff looked like a removal; no test failed; and
      the guard that vanished was one the previous round had
      specifically added. When a check MOVES, the question is not "is
      the new check better" but "what was the old one catching that
      nobody wrote down" — and the only reliable answer is a mutation
      the old code kills and the new code does not.*
    - *Adversarial r4 on the skills chunk (5 lenses, sonnet-medium):
      REJECT -> fixed. **The HIGH was the chunk's own regression**, which
      is exactly where the watch-list says to look first: making
      `load_skills` DEGRADE instead of raise left the in-memory list one
      row short, and `_save_skills` — a full rewrite from that list, fed
      by 8 call sites including `update_skill_utility` (every skill
      match) — made the loss durable. Before the chunk: loud crash, no
      data loss. After: silent deletion. Now strands-and-carries
      (deliberate caller drops still drop, pinned both ways). Also
      fixed: reads degrade / writes abort split on the OSError contract
      (3 lenses converged — `get_all_skill_stats` had inherited the
      writer's raise); a test for the safety-critical OSError branch
      (Experimentalist: it had zero coverage); `save_skill`'s own census
      reason (the shared `_UPSERT_STAMPER` pointed at another chunk's
      test class and mutation file); and **the scanner itself** — it was
      blind to the split-helper shape THIS chunk introduced
      (read-helper + write-helper + orchestrator, none holding both
      markers), found independently by 3 lenses. Rewritten to follow
      same-module call graphs, with `tests/test_scan_destructive_-
      rewrites.py`: 6 fixtures including the blind-spot must-detect, a
      read-only negative control, and a regression pin that the live
      skills helpers stay visible. Mutations 9 -> 13, 13/13.*
    - *Deferred from r4, with reasons (all verified real, none in the
      chunk's scope): (1) `gc_memory._gc_outcomes` strict-decodes
      outcomes.jsonl inside a bare `except Exception: return 0,0,0` — one
      torn byte silently DISABLES outcome retention forever, with no log
      line at all (Skeptic + QA). Not destructive (no rewrite happens),
      but the "silent full disable" flavor of the same debt; wants the
      same announced-read treatment. (2) A byte-tainted twin of a live
      skill id is preserved forever and never id-matches, so repeated
      saves of that id accumulate duplicate rows with no repair path —
      forensic-preservation by design and `load_skills` resolves it
      correctly (last-wins), but the store never self-heals; wants a
      deliberate repair verb, not a silent auto-drop. (3)
      `memory_ledger._maybe_record_skill_injection_outcomes` writes its
      idempotence marker only after the whole loop, so a mid-loop raise
      leaves partial attribution uncommitted and a re-stamp
      double-counts (Architect) — pre-existing, needs its own chunk.*
    - *Tier 3 (operator-facing output) OPENED 2026-08-17 with
      `loop_report.py` as the double-payoff target: probed six live
      torn-store defects first (worst: ONE torn byte in
      captains_log_slice.jsonl killed the entire run report; a torn
      metadata.json vanished the run from the index), fixed with
      announced reads + operator-visible degrades, cleared all 6
      loop_report census entries, and caught a second-order launder
      (`store_text` + plain `json.loads` parses byte-tainted JSON;
      the strict HTML writer then crashed one seam later) — all 8
      single-file JSON reads now `loads_clean`. Landed `5cf6f60f`.
      Sweep `loop_report_truth.json`, 37 mutations: first pass 27/37
      (2 anchor SKIPs, 8 SURVIVED — all real, zero equivalents,
      clustered exactly on the tier-3 thesis: printed fields and
      index plumbing nothing asserted); closed with 7 tests + 1
      assertion; final 37/37. Record:
      `docs/history/2026-08-17-loop-report-truth-sweep.md`. Adversarial
      r1 (sonnet-medium, 5 seats): 5/5 PASS, 2 verified MEDIUMs fixed
      same session (torn-card index row got a visible marker; a vacuous
      `"done" in content` assertion de-vacuized) — spec now 39, record
      `docs/history/2026-08-17-loop-report-sweep-review.md`. Deferred
      from r1 with reasons: (a) the `loads_clean(store_text)` +
      dict-check + warn-degrade idiom is duplicated 8× in
      loop_report.py — extract a single-object analog of
      `read_jsonl_announced` into jsonl_utils as its own chunk
      (Architect + Minimalist convergent, 4-wants-extraction);
      (b) `run_curation.surface_step_flags` strict-reads the same
      captains_log_slice.jsonl this chunk fixed (`except OSError`
      misses UnicodeDecodeError) — NAMED THE NEXT TIER-3 STOP.*
    - *Tier-3 stop 2 SHIPPED same day (2026-08-17): the step-flags
      lane. Probe-first found the day's worst defect —
      `refresh_step_flags._merge` DESTROYED a torn run_card (parsed as
      `{}`, rewrote the whole card as a step_flags stub; the backfill
      walks every run), plus the family-B crash and the launder shape.
      Fixed preserve-don't-destroy + announced read + card-visible
      `slice_loss` note; census 94 unreviewed / 86 functions + 12
      reviewed; `step_flags_truth.json` 8/8 first pass (co-written,
      weak green). Record:
      `docs/history/2026-08-17-step-flags-torn-store.md`. Its
      adversarial r2 (3 seats): Architect HIGH verified — the SAME
      destructive shape one function up
      (`refresh_run_card_classification._merge` parsed a torn card as
      `{}` and rewrote maintenance keys away, torn bytes overwritten);
      fixed preserve-THEN-rebuild (timestamped sidecar + WARN +
      loads_clean; self-heal contract kept), 3 pins, spec 11/11. Its
      r3 fix-layer seat: PASS + one MEDIUM lead verified
      (`mark_skill_candidate_consumed._stamp` plain-json.loads
      launder; one-word loads_clean swap, spec 12/12) — fixpoint
      declared. Review record:
      `docs/history/2026-08-17-step-flags-review.md`. The two r2
      MEDIUM strict run_card readers CLOSED 2026-08-18
      (shadow_lane._primary_comparison_fields WARNs on
      exists-but-unreadable, absent-by-age stays silent by design;
      camera_readout counts + prints unreadable cards beside its
      torn-frame warning — cleared camera_readout.main's census entry
      as a side effect, census 93 unreviewed / 85 functions + 12
      reviewed; `run_card_readers.json` 6/6 with tainted-valid twins
      in both modules). Remaining tier-3 surfaces: camera_readout
      frame/verdict sections, captain's log render,
      discretion_readout, status lines.*
    - *rules/background follow-on SHIPPED 2026-08-17 (from evolver r2's
      QA sibling sweep, verified over Minimalist's dissent):
      `save_rule._upsert` + `_append_task_log._merge` were the
      DESTRUCTIVE subset of the debt — keyed rewrites that re-dumped
      every row (laundering) and deleted torn lines outright on every
      save/start/poll. Reshaped to the verbatim-preserve pattern;
      `load_rules`/`_load_task` announced; census 92+12; 5 tests, 7
      mutations 7/7, suite 9448. Its own adversarial r3
      (Skeptic+Architect+QA): convergent MEDIUM fixed (the deliberate
      drift-reads-as-absent semantic was untested — reverting it passed
      the suite; now pinned + mutation-guarded); Skeptic HIGH refuted
      by invariant (atomic rewrite keeps at most one clean row per id,
      so a torn newest row cannot serve a stale older one — documented
      at the read site); 2 LOWs deferred with reasons in the commit.
      Fixpoint declared: r3 fixes are a test, a comment, and a blank
      line — no new code paths to re-review.*

  **Three of the four surfaces swept so far were tripwires, and two could
  not fail.** The code a decree protects tends to be fine; the guard
  claiming to protect it tends not to be. Enforcement written as a test
  attracts less scrutiny than enforcement written as a feature, because a
  passing test looks like evidence.

  **Method note for the rest of the sweep: when the surface IS a guard,
  mutate the guard.** The question stops being "do the tests catch a
  code change" and becomes "can this guard fail at all?" Give the checker
  its inputs as parameters (defaulting to the live ones, so the deployed
  guard is unchanged), then inject one violation per fixture and assert
  it is named — and pin the exemptions too, since a quiet census is only
  trustworthy if you can show what it stays quiet about. Watch for
  *accidental* detection: the census's three DETECTED verdicts all came
  from breaking hard enough on real data to raise a false positive, which
  reads identical to real coverage in the runner output.

  **New defect class, from the envelope sweep: two guards, one test.**
  `_safe_name` blocks traversal twice (basename call + character
  whitelist) and the traversal test passes with either one removed, so it
  pinned neither. Marking both equivalent would have been the lazy read;
  the fix is to ask what job only one guard does — here the whitelist also
  scrubs spaces/`;`/newlines/`$(...)` out of a dispatcher-chosen filename,
  which nothing tested. Redundancy in the code is fine; redundancy that
  makes a test unable to distinguish its subject is not.

- [ ] **Decide the retention allowlist's granularity — a second deletion
  inside an already-allowed function ships silently.** Surfaced by the
  2026-08-16 retention sweep and named in
  `tests/test_no_silent_deletion.py`'s Limits rather than fixed
  drive-by, because it changes the maintenance contract for every future
  contributor. The allowlist is keyed `(module, function)`, so the 29
  allowed functions are a blind spot: an `.unlink` of a temp file
  escalating to an `rmtree` over a run dir inside `cleanup_step_artifacts`
  or `prune_run` would not trip the wire. **Options:** (a) leave it,
  accepting that the 29 are reviewed once and trusted after; (b) record
  the multiset of call shapes per site, so `.unlink` → `rmtree` trips but
  a line move does not; (c) key by call line, which is precise and churns
  on every refactor. (b) looks like the honest-good-enough slice. Related
  limit, no action proposed: a shell-out `rm` bypasses the AST scan
  entirely — none in src/ today, verified 2026-08-16.

  **Update 2026-08-16: there is now a worked precedent to decide against
  instead of a hypothetical.** The silent-drop tripwire
  (`tests/test_no_silent_drop.py`, same day) was built on a counting key
  — `(module, function) -> count` — because this item recommended it.
  Live experience so far: the baseline is 122 entries and the count adds
  no meaningful noise, and the stale check has to compare counts rather
  than membership, which is four extra lines. The retention list is 29
  entries, so the cost there is smaller still. The remaining argument for
  (a) is that a count is not the *shape* — `.unlink` becoming `rmtree`
  inside an allowed function still reads as one deletion — so counting
  alone does not close the escalation hole this item is actually about.
  A shape multiset (option (b) as written) does.

  **Second update 2026-08-17: the key's FUNCTION half has an independent
  defect, and it is not hypothetical either.** The silent-drop census
  keyed on a bare function name and that turned out to merge unrelated
  sites: five distinct `_stamp` closures in `memory_ledger.py` shared one
  key, and `memory_backends.py`'s `read_all` entry covered both
  `JSONLBackend` and `SQLiteBackend`. Reviewing either would have blessed
  the other. Fixed there by qualifying the name (`outer.inner`,
  `Class.method`). **The retention allowlist has the same latent hazard
  and is clean today** — checked 2026-08-17, none of its 28 entries
  resolves to more than one def in its module — so this is a
  cheap-now/expensive-later change rather than a live bug. Fold it into
  whichever option is chosen; the two halves of the key are independent
  and qualification is the smaller of the two.

  **Recommendation unchanged, now with the caveat that a bare count would look like a fix
  without being one.**

- [ ] **Let a goal carry files — images first.** Found live: Jeremy's
  goal was *"find this pictured study"* with a screenshot of the paper's
  first page. `handle` takes `message` as text only, so the operator (me,
  this time) had to hand-transcribe the title, authors, legible abstract
  and a prose description of Figure 1 into the goal text. That works and
  it is also the whole problem: **the transcription becomes an
  unattributable claim inside the goal.** The run then had to spend
  steps deciding how much to trust it, and got that right — it flagged
  "cosine similarity" as transcription-only after finding the term in
  none of its six retrievals, because the term came from my description
  of the figure, not from the paper's abstract. A correct outcome
  reached expensively, and only because the transcriber happened to
  label the provenance.
- [ ] **Where it has to reach.** The dispatch envelope already carries
  an artifact channel (`store_attachments`, provenance sidecar,
  `land_in_run_dir` — the Poe/Hermes return path), so the *transport*
  mostly exists; what does not exist is (a) a CLI/entry seam for
  attaching a local file to a goal, (b) any vision-capable path in the
  adapter suite for reading one, and (c) a provenance stance — an
  attachment is operator-supplied, so it rides the operator/ancestry
  channel, never the extraction lane (the 2026-07-29 envelope decree
  applies unchanged). The named residual in that arc — a CONTAINERIZED
  worker can read neither attachment copy — becomes load-bearing here
  rather than evidence-gated, since the box runs `container: on`.
- [ ] **Decide the honest degrade.** If no vision path is available for
  the chosen model, the options are refuse, or transcribe-and-label. The
  run above shows transcribe-and-label works IF the label survives into
  every downstream claim. Make that structural, not a habit of whoever
  typed the goal.

### Reference corpora are read from stale local clones, and the container cannot see them at all (FOUND 2026-08-16, live run)

- [ ] **Two independent blockers, one symptom.** The same run was told
  the skill under study "can be found in the link farm". It reported the
  path did not exist and fell back to the URL — honest, and wrong twice
  over: (1) `~/claude/link-farm` was **20 commits behind** its GitHub
  remote on BOTH the Mac and the box (local `2026-07-31`, remote
  `2026-08-14`), and the missing commits included
  `283a6e5 "Add vetted ste skill (ASD-STE100) with security audit
  record; link to Ruben Hassid post"` — i.e. the exact artifact, with a
  vetting record, sitting unpulled; (2) even pulled, the path is outside
  the container write scope, so the executor gets
  `container mount: rw root /home/clawd/claude/link-farm is outside the
  container write scope … refused`. Jeremy's model was right — the sync
  commits and pushes md/html/sqlite every run — the consumers just never
  pull. Both clones are current as of 2026-08-16.
- [ ] **FETCH, don't pull — Jeremy's call 2026-08-16**: *"I'm tempted to
  say always fetch to determine staleness/availability when working with
  an external origin repo… that would make all the content available
  even if the current branch isn't fully up to date."* This is stronger
  than pull-before-read for three reasons worth keeping: (a) `git fetch`
  **mutates no working tree**, so it is safe under concurrent sessions —
  the shared-tree rule in CLAUDE.md forbids exactly the tree-mutating ops
  a pull performs; (b) it yields the staleness FACT (`HEAD..origin/main`
  count and dates) which is what a run should report rather than
  silently reading old rows; and (c) it makes **all remote content
  readable without merging** — `git show origin/main:skills/ste/SKILL.md`
  would have answered this run's question on a branch 20 commits behind.
  Silent staleness is the same false-green shape as a guard that cannot
  fail: the corpus answered, it just answered as of two weeks ago.
- [x] **Container reachability — CLOSED 2026-08-16 by config, no code.**
  `executor.container_extra_mounts` already existed for exactly this
  (read-only reference mounts; `DEFAULTS.md`: "add from evidence, same
  posture as `validate.write_fence_allow`") and already carried the maro
  repo — Jeremy's instinct that we should treat local artifacts the way
  we treat maro's own source was describing a mechanism that was
  already there. `/home/clawd/claude/link-farm` added to the box's
  workspace config (backup: `config.yml.bak-20260816`). Read-only is the
  correct grant: nothing should ever write to a cited corpus.
- [ ] **The two decisions interact, and the interaction is a
  constraint:** a read-only mount means the CONTAINER cannot fetch —
  a fetch writes to `.git`. So "always fetch" has to be a **host-side
  pre-flight**, before the container starts, not something the worker
  does mid-step. That is also the better place for it: one fetch per
  run, its result reportable at plan time, rather than N workers racing
  the same remote. Build it as a pre-flight step that fetches every
  configured reference mount and stamps freshness into the run's
  context.
- [ ] **Plan-time honesty, still open.** A goal citing a path outside
  the workspace subtree that is NOT in the mount config remains
  structurally unreadable, and today that surfaces as a mount refusal
  buried in step logs. It should be a loud message at plan time —
  same class as the container verb-parity honest-absence work.

### Reading a cited corpus: read the store, not its export (Jeremy, 2026-08-16 — and NO trusted-source class)

- [x] **DECIDED, not built: there is no special class of trusted source
  data.** Jeremy, on being offered a source-vs-ask trust-tier design:
  *"I don't really want a class of trusted source data, so I'm fine with
  maro knowing the concept of the link tree on the web to use as opposed
  to some sort of special class of data, that seems like a
  distraction."* Recorded so the idea does not get re-proposed: a
  curated collection is a PLACE TO LOOK, not a privileged evidence
  grade. The link farm's value is durability — Jeremy captures things
  there so they survive the original going away — not authority. Runs
  should keep grounding claims in what they retrieved and saying where
  it came from, exactly as they do now.
- [ ] **Consume the authoritative store, not a lossy export.** Live
  finding, and the one real defect here: the run read
  `link-farm/posts_final_v3.json` — a JSON export — and correctly
  reported what it contained. The authoritative store is
  `db/ai_links.db`, which carries `post_thread_segments`,
  `enrichment_status`, curator `notes` and per-post review flags the
  export drops. A consumer reading the export gets a thinner view and
  cannot tell. Where a corpus offers both, prefer the store and say
  which one was read.
- [ ] **The gap Jeremy actually cares about is a SCHEMA gap, in
  link-farm, not maro.** He pasted in the full text of the reply email
  that carried the `ste` skill; it is not in the corpus. Cause,
  verified 2026-08-16: **no table has anywhere to put it** — no
  `email`/`body`/`attachment`/`page`/`html`/`raw` column exists in any
  table, and `posts`' only long-text fields are `summary`, `content`
  (the tweet) and `notes` (176 chars of curator flag here). Not a
  scraper failure; there is no structure to capture it. The durable
  shape of the ask: a gated link whose content was obtained
  out-of-band (an email wall, a signup) needs a place to live beside
  the post, with its own provenance ("obtained manually by the
  operator, 2026-08-14"), because that is exactly the content the web
  will not serve later.
- [x] **NOT a gap: the un-captured 30-deep thread reply.** Corrected
  by Jeremy — link-farm's multi-segment capture is for an OP's own
  multi-post threads, by design, not for replies. He posted the image
  directly precisely so nothing had to go digging. My earlier framing
  of this as a capture miss was wrong.

### Concurrent milestone-area agents — why is the path A→B→C and not all three at once? (Jeremy, 2026-08-16; **v1 milestone-DAG SHIPPED + FLIPPED ON 2026-08-22**)

**SHIPPED 2026-08-22 (dev Mac), Jeremy's go: "I'd like to use concurrency
where we can… milestones are a good place to start" + flip decree "we
should flip and gate the flip back if it doesn't work right honestly."**
`Milestone.depends_on` (ids; ORDERING only, never gates on outcome —
preserves the sequential walk's continue-past-failure semantics exactly),
decompose declares edges as earlier-index refs (cycle-free by
construction; absent/malformed → chain to predecessor = old behavior;
refs to dropped milestones discarded), legacy mission.json loads as a
chain. `_run_milestone_dag` runs ready milestones concurrently
(`mission.parallel_milestones` **default ON — the flag is the revert
lever**; `mission.milestone_workers` default 2; ContextVar propagation
like the feature pool; malformed/cyclic dep sets from the load path fall
back to list order instead of deadlocking; thread-crash backstop marks
the milestone failed). Whole-mission saves serialized behind an
in-process lock. The flip is inert until a decomposition explicitly
emits independent milestones — undecorated decompositions execute
byte-identically to the sequential walk. Tests:
`tests/test_mission_parallel.py` (13: parse/chain/invalid-ref/round-trip/
legacy-load/concurrency-barrier/dependency-order/failure-parity/
revert-lever/stall-fallback/thread-crash); full suite 10240 green.
Step-level concurrency deliberately NOT this chunk (Jeremy: "more
planner type work… maybe we can get into that later" — recorded at the
step-skeleton entry). Remaining below: the schedule-which-areas
question, merge story for out-of-order findings, budget model — v1 runs
what decompose declares independent and nothing more.

**Adversarial round same session (4× sonnet-medium fallback, codex
capped til 08-27): REJECT → fixed to green**
(`docs/history/2026-08-22-milestone-dag-adversarial-review.md`). The
standouts, all VERIFIED then fixed: the revert lever broke on a quoted
YAML `"false"` (`bool("false") is True` — new `config.get_bool`, string
normalization); the stall-fallback lane lacked the crash backstop its
docstring claimed; a milestone-thread crash left zero durable evidence
at default verbosity (now log.warning + immediate persist);
chain-shaped missions now BYPASS the DAG entirely (`_is_chain_shaped`)
so the flip is literally inert until independence is declared. Two
follow-ups filed here, not silently deferred:
- [ ] **Worktree-per-sibling isolation:** in-process sibling loops
      (concurrent milestones AND the pre-existing feature fan-out) share
      one project checkout — phase-3b worktree isolation only covers the
      LoopBusy path siblings never take (interrupt.py comment corrected;
      it over-claimed). v1 mitigation is the decompose contract ("do not
      declare independent if they'd edit the same files; when in doubt,
      keep the dependency"). The build: provision a worktree per
      top-level sibling loop unconditionally, reusing
      `loop_parallel._run_in_step_worktree`'s pattern + merge-back.
      Evidence gate: first burn-in mission with declared independence
      shows file-level contention (or doesn't).
- [ ] **Wire drain_next_mission into the DAG:** the heartbeat resume
      lane is DAG-naive v1 (list order stays topologically valid, so
      correct but never concurrent). Prerequisite named in its
      docstring: its loop diverged from run_mission long ago (no
      validation gate, no hooks, own status vocabulary) — reconcile
      that divergence first, don't just swap the scheduler in.
- [ ] **UNSETTLED from the round, with its probe:** real-adapter
      interleaving under the new width (all tests stub the adapter).
      Probe: first box burn-in mission with declared independence —
      watch `subprocess_fork_masters.json` churn + per-call latency
      during overlap. Premise expires if fork-master keying or
      session-reuse config changes.
- [ ] **Named upgrade edge:** the `bool(get(...))` coercion pattern
      exists at other config call sites (this round fixed only the
      revert lever + added the shared `config.get_bool`); sweeping the
      rest is caps-sweep-shaped work — measure which flags are
      operator-touched before bulk-converting.

- [ ] **Jeremy, verbatim:** *"why aren't we running concurrent agent
  processes (i.e. multiple map location build-outs) to speed up the
  general run? i.e… if we think we should go A → B → C, we can probably
  work on things in all 3 milestone areas to reach out in different
  directions towards our pathfinding, if we're thinking about this in
  the treasure map sense."*
- [ ] **This is a DIFFERENT axis from the parallelization already
  filed**, and the distinction is the whole item. "Step-skeleton
  parallelization" (2026-07-28, below) parallelizes step-shaped chunks
  *within one run's* skeleton. This asks for **multiple agent processes
  fanning out across milestone AREAS at once** — pathfinding in several
  directions to learn which direction is real, where the parallelism is
  the search strategy rather than a throughput trick. Reaching toward B
  and C early is how you discover that B is a dead end before A's work
  commits to it.
- [ ] **What already exists to build on, so this is not from zero:**
  in-process run isolation (`c8ec0af`, concurrency phase 1), isolated
  worktree per sub-agent, the recursion decree (sub-goal spawning is
  never foreclosed), §13b revisitable milestones with reopen
  conditions, and the §13d side-quest DAG proposal. The missing pieces
  are a scheduler that decides WHICH areas are worth a concurrent
  probe, a merge story for findings that arrive out of order, and a
  budget model — N concurrent explorers is N× spend, so the value has
  to come from pruning, not from doing everything.
- [ ] **The honest open question before any build:** the treasure-map
  framing pays only if the areas are genuinely independent. Where B's
  shape depends on what A learns, a concurrent B is speculative work
  that may be discarded — which is fine as PATHFINDING (cheap probes,
  expect to throw away) and expensive as BUILD-OUT. Decide which one
  this is, per area, and the scheduler follows. Pairs with the
  thread-architecture arc and with "Open-thread structure".
- [ ] **Evidence note, CORRECTED 2026-08-22 (Jeremy pushed back on the
  2026-08-21 version — "sounds like you took the paper at face value" —
  and he was right):** the DeepMind/MIT paper
  (`research/2026-08-21-scaling-agent-systems-audit.md`) measures N
  agents coordinating on ONE bounded task — same prompt, one answer.
  That regime gates **same-milestone fan-out** (never point multiple
  agents at one milestone: 4.4–17.2× error amplification, super-linear
  turn overhead). It does NOT gate what this entry actually asks for —
  **cross-milestone parallelism**, distinct work items with near-zero
  inter-agent communication, which is the paper's *winning* decomposable
  regime (+80.8% column). The first version of this note conflated the
  two; likewise "our logs show only solo runs" is a design artifact, not
  evidence solo is optimal — the system was built sequential, so the
  comparison never could exist.
- [ ] **Why maro is sequential — mechanics, not doctrine (verified in
  code 2026-08-22):** (1) `Milestone` (`src/mission.py:53`) has NO
  dependency field — the schema literally cannot express "B is
  independent of A"; ordering is a flat list from `decompose_mission`,
  so sequential execution is a structural default, never a considered
  per-mission decision. (2) `run_mission` walks milestones strictly
  serially with a validation gate between each (linear-pipeline
  assumption baked in); features within a milestone already parallelize
  (max 2). (3) `drain_next_mission` holds a one-mission-at-a-time lock.
  (4) `run_parallel_loops` (`src/agent_loop.py:838`, goal-level
  concurrency, max 3) has **zero callers** — concurrency phase 1
  (`c8ec0af`) even fixed its ContextVar/run-dir isolation, then nothing
  ever wired it. Same shape as the knowledge-edges find: capability
  minted, never traversed.
- [ ] **The concrete build shape, when taken up:** teach
  `decompose_mission` to emit `depends_on` edges (it already structures
  milestones with an LLM call — the flat list becomes a DAG for free);
  run ready milestones (deps met) concurrently via the existing
  `run_parallel_loops` + phase-1 isolation, one agent per milestone,
  validation gates unchanged; keep the honest costs on the table —
  shared-store write contention (largely addressed by the 2026-08 lock
  hardening, verify), and learning-transfer loss during overlap
  (parallel milestone B can't recall A's fresh lessons for the overlap
  window — bounded by run length, and the pathfinding case explicitly
  doesn't want that coupling anyway). A/B against the sequential
  baseline stays the bar for making it a default, per house gate
  discipline — but the *question* is now open, not avoided.

### Portability weighting v2 — selection-bias exploration (accepted v1 residual, 2026-08-15)

- [ ] **Watch for winner-take-all entrenchment in portability-weighted
  lesson selection; add an exploration mechanism if it shows.** §14a
  slice 2 r1 (architect, verified reasoning): foreign-citation evidence
  is run-level, not causally isolated per lesson — a boosted lesson
  present in runs that succeed for unrelated reasons accrues favorable
  evidence, while rivals below the top-3 can never earn their own ≥3
  citations (cold-start starvation). The Beta posterior assumes
  unbiased draws; deterministic top-3 selection breaks that. Amplifier:
  the one-run cache lag means a newly-boosted-but-bad lesson can be
  selected 2-3× in a tight burst before its first failure verdict
  lands. Named falsifier to watch (camera frames carry
  `extra.portability_adjusted`): a lesson holding a boosted slot ≥N
  consecutive selections for one goal-shape while same-pool rivals stay
  evidence-starved. If seen → v2 = epsilon-slot or Thompson-sample one
  of the 3 injection slots. Bounds that keep v1 honest meanwhile: only
  6 lessons qualify at ≥3 today, weight ∈ [0.8, 1.71] live, and
  weighting rides ONE slot-set of a multi-source selection.

### §14a scope stamp — the census comparison is not readable yet (evidence-gated, 2026-08-15)

- [ ] **Re-read `camera_readout --portability`'s by-scope rollup once
  stamped lessons start clearing the 3-verdict bar; only then judge
  method-vs-world transferability.** Slice 3 shipped the mint-time
  stamp and the cross-tab that consumes it, but the stamp is
  prospective by construction: at ship, all 79 cited lessons were
  minted pre-stamp, so every one lands in `unstamped` and the
  method/world buckets are empty. The readout says so in words rather
  than printing an empty comparison as if it were a finding. What the
  pre-ship probes DID establish, on the existing corpus with a
  post-hoc labeller: of the 6 lessons carrying ≥3 verdicted foreign
  citations every resolvable one is method-scope, and only 5
  world-scope lessons have ANY foreign citation — consistent with
  §14a's "learning is almost entirely methodology", and the reason
  scope was deliberately kept OUT of ranking. This entry is what turns
  that into evidence instead of a prior. Kill criterion for the stamp
  itself: if after ~30 stamped-and-cited LESSONS (not the other "~30"
  the readout prints above the scope table — that one is the slice-1
  gate and counts foreign verdicted CITATIONS) the two buckets'
  pooled portability is indistinguishable, the categorical axis is not
  earning its place and slice 3 should be reconsidered against the
  contested-14a alternative (structural invariance, READING_QUEUE
  2026-08-11).
- [ ] **Do not backfill the legacy rows to fill the buckets faster.**
  Probed and refused at ship: labellers disagree on the base rate by
  ~2× (production lane ~81% method, hosted-free ~44% on the same
  runs), so a column mixing instruments would not be comparable to
  itself — which is the one property the census needs. If backfill
  ever happens it must carry a per-row labeller stamp and be bucketed
  separately.
- [x] **Stamp coverage is instrumented and WENT NON-ZERO 2026-08-16** — `5/199 rows stamped (method 5 / world 0 / malformed 0)`, newest mint `2026-08-16T17:10:59`, from the first organic goal run since the stamp landed. This settles the open question the entry named: the write path fires on the production lane, so the earlier `0/195` was *no runs had happened*, not a silently ignored extraction schema. First real signal is 5 method / 0 world — small n, but pointing the same way as §14a's prior. The citation-gated comparison stays empty until stamped rows accrue foreign verdicted citations; that is the next thing to watch, not this.
  `camera_readout --portability` prints a store-wide, NOT citation-gated
  coverage line (`N/M rows stamped`, method/world/malformed, newest
  stamped mint) and shouts when it is 0 with a non-empty store. Added
  r3 because everything else about the stamp is citation-gated, so a
  dead write path printed the same reassuring "predates the stamp … by
  construction" line as a healthy one. **At r3 ship the live store read
  0/195** — the write path had not fired once in production (all rows
  predate the 2026-08-15 20:12 MDT landing). Note the counter reports
  IMPORTED stamps separately (r4): a pack import proves another
  machine's writer fired, not this box's, and must not silence the
  alarm. The writer WAS live-fired
  end-to-end against a real model in a temp workspace (two lessons, both
  stamped, correct types) and the full mint → store → census chain was
  walked, but that is not the same as production evidence. First
  organic run should flip this to non-zero; if it does not, the
  extraction schema is being ignored on the production lane and that is
  the bug, not the empty buckets.
- [ ] **The imported-row refusal voids the verdict rather than
  partitioning it.** Since r3/r4 an imported lesson that carries
  verdicted foreign citations in either compared bucket blocks the
  readable-comparison verdict (`camera_readout._print_portability`).
  Correct per e2b83703 — never pool across labellers — but coarser than
  the decision implies: the upgrade is to compute the comparison over
  locally-minted rows only and report imported ones as their own
  labeller cohort. r4 already narrowed the trigger from "a bucket
  CONTAINS an imported row" to "an imported row's verdicts are IN the
  pooled figure", so an uncited pack row no longer voids anything. Do
  the partition if a pack import ever lands cited stamped rows; until
  then the void is the honest reading.
- [ ] **Numeric coercion is load-time and field-typed, not universal.**
  `load_tiered_lessons` coerces `score`/`confidence` to float and every
  int-annotated field via `_coerce_int_fields`, because comparisons on
  those fields sit in code paths with no enclosing try (one string score
  wedged every write on a tier; one string `sessions_validated` killed
  promotion and GC every cycle). Anything comparing a NEW non-numeric
  field — or a str field against a str constant — is outside that net.
  The general answer would be a schema validator at the store boundary;
  not built, deliberately, until a third instance shows up.
- [ ] **Per-step mints (`extract_step_lessons`) stamp nothing — accepted
  v1 boundary, revisit only if the bucket stays empty.** Slice 3 covered
  the two run-level mint paths (finalize + deferred); the per-step
  extractor is a separate function with its own inline prompt and parser,
  so it was left alone rather than changed unprobed. Sized before
  accepting: 6/188 live rows and 14/516 archived, all `provisional` —
  and provisional rows are barred from every injection surface until a
  confirming re-record, which arrives through the run-level path and
  fills the empty stamp (fill-if-empty). So the rows that can actually
  be cited do get stamped, just later. If the census's stamped bucket
  fills too slowly to read, extending the per-step prompt is the first
  lever — it needs its own leak probe (the run-level prompt needed two
  iterations to stop putting scope values in the type slot).

### Shadow lane — batch adjudication tooling (evidence-gated)

- [ ] **Build the shadow adjudicator at first ~10 completed pairs (or
  2026-09-13, whichever first).** Deliberately NOT built with v1 —
  judging hypothetical data. Cross-model comparison pass over
  `memory/shadow_ledger.jsonl` pairs (union/miss scoring per
  docs/history/2026-08-13-star-vs-harness-comparison.md); keep/kill
  signals pre-registered in docs/SHADOW_LANE_DESIGN.md; verdict lands
  in GOAL_BRAIN Decisions either way. Watch items until then: ledger
  rows with `primary_cost_usd: null` (accepted r3 residual — card
  exists before cost finalizes; the first live fire be7c618a hit
  exactly this) may need a reconciliation touch-up pass at
  adjudication time; fallback `ledger-row.json` files in arm dirs
  escape the daily cap until reconciled.

### Container verb parity + container auth expiry watch (FOUND 2026-08-13, A/B-4 re-test setup — the dispatch lane was silently DOWN)

Two coupled finds from one diagnosis (full trail in the A/B-4 entry):

- [x] **(b) SHIPPED 2026-08-13: lane-aware execute prompt** —
  `EXECUTE_SYSTEM_CONTAINER` render carries honest-absence blocks
  (names NO fetch/read CLI, teaches targeted grep/sed extraction +
  state-what-you-didn't-read) and `execute_system_for_lane()` selects
  by config intent; degrade-to-host only UNDER-advertises. The
  original hole: the image bakes node/claude/git/python3 only,
  `_read_cli_path()` resolves to live-repo paths that
  `build_mount_map` hard-excludes, so every containerized teaching
  measurement (A/B-4 included) was structurally unable to invoke.
  **3-lens review of (b) + the auth breaker 2026-08-13 — REJECT,
  fixed same day (7d470be):** require-mode host bypasses closed
  (suppression + missing-cwd now refuse under require; attribution
  follows the ACTUAL lane), notification guaranteed-or-retried +
  its Telegram/bootstrap consumer side wired, FailoverAdapter stands
  down for container-owned auth errors (no shared-circuit trip, no
  contradictory host-/login alert), breaker transitions serialized,
  detail secret-scrubbed, probe shape-only, doctor/health honest on
  unreadable markers + docker-down. Verdict: docs/history/
  2026-08-13-container-auth-breaker-verb-parity-adversarial-review.md.
  **Residual (filed, not built):** typed per-call lane pass-through
  (prompt-lane and dispatch-lane each read config independently; a
  mid-step config flip can mismatch them for one step — rare,
  self-corrects; the exception-path fail direction IS fixed toward
  under-advertising).
- [x] **(a) SHIPPED 2026-08-13 per Jeremy's morning decree** ("keys
  should be injected into the container with ENV values at spin-up
  time, not hosted directly... host values stay stored and maintained
  on the host"; backup lane = read-only mounted config dir, noted not
  built). Image r3 bakes the maro VERBS, never keys: `COPY src/` +
  `maro-read`/`maro-fetch` shims + apt python3-yaml/-requests (still
  no pip in the image — runtime supply-chain stance unchanged).
  Spin-up injection: `hosted_free_container_env()` (keys from host
  env/credentials .env + `MARO_HOSTED_FREE_ENABLED` consent CARRIER —
  host config always wins; requires image_bakes_verbs + host consent +
  a key) rides the docker CLIENT's env with bare `-e NAME` flags, so
  values never appear in docker argv/host process listings.
  `image_bakes_verbs()` (tag revision >= 3, custom images conservative
  False) gates a third execute-prompt render (container-with-verbs,
  baked names only) and relaxes the planner read-verb gate to
  un-suppressed r3+ container lanes (baked name taught). Live-smoked
  end-to-end: containerized `maro-read` answered a real sub-query via
  Groq with injected keys and an honest receipt.
  **2-lens adversarial review same day — REJECT, fixed same session
  (verdict: docs/history/2026-08-13-executor-r3-adversarial-review.md,
  0/9 reviewer hallucination):** consent carrier now honored only
  inside a container (/.dockerenv marker — a stray host export can
  never authorize egress); lane AVAILABILITY (docker probe + auth
  breaker) joins both the prompt render and the plan-time teach gate
  (a mode-on degrade was flipping the verbs prompt from under- to
  over-advertising); injected key values scrubbed from captured
  output at the capture seam (`[REDACTED:<NAME>]` — in-container
  `env` can no longer persist values into transcripts); exposure
  claims reworded to exactly what holds (argv clean, client env
  owner-scoped /proc, container env = the decree's accepted exposure).
  **Residuals (filed, not built):** true TOCTOU (lane dies between
  render/plan and dispatch — loud command-not-found, worker falls
  back); tag revision is operator declaration not capability evidence
  (retagged r2-as-r3 lies; runtime shim probe is the upgrade if that
  trust misplaces); container hosted-free runs on DEFAULT provider
  order/models (host config overrides don't transport — ride the env
  pattern when it first bites); r3 src snapshot is build-time
  (rebuild + revision bump moves baked verb behavior).
- [x] **Container OAuth expiry is invisible until the lane dies.**
  maro-claude-auth seeded 07-14 interactive; expired ~08-12; every
  agenda dispatch with executor steps then died at step-execute
  (two runs interrupted before diagnosis).
  **SHIPPED 2026-08-13 as a reactive auth breaker** (Jeremy re-seeded
  the volume same morning; `container: on` restored): a containerized
  call failing with a CLI login-failure signature trips
  `container_exec` breaker state → `on` degrades to host/fence-only,
  `require` refuses, one `backend_actionable` Telegram with re-seed
  instructions; self-clears via a cheap credentials-file check (live
  shape + newer-than-trip, ≤1 docker cat / 5 min — no token spend,
  no flapping on server-side revocation). Doctor row + system_health
  `container_auth` row (SILENT while tripped). Design:
  CONTAINER_EXECUTOR_DESIGN.md §3 "Auth breaker". Chose the breaker
  over the scheduled-login_probe watch: zero happy-path spend, and
  the failure itself is the most reliable detector.
  **Skeptic review 2026-08-13 (Jeremy: "adversarial-review all the
  changes"): 5 findings, 3 fixed / 2 dispositioned.** Fixed: (1) the
  breaker now searches the FULL structured CLI error text (4000 chars;
  raw-stdout fallback 2000) instead of the 300-char display detail, +
  wording-variant markers ("oauth session has expired", "oauth access
  token expired", ...); (3) notify throttle in a sidecar that SURVIVES
  clear_auth_breaker (6h) — a touch-without-reseed flap can't spam
  Telegram (also mutes finding 2's double-notify). Accepted residuals:
  (2) unlocked read-then-write races self-heal within one recheck TTL,
  worst case one duplicate notify; (4) marker breadth
  ("authentication_error" etc.) on CLI-authored error text — false
  trips only degrade-to-host and the notification names the state.
  Rejected: (5) corrupt-marker fail-open — the next auth failure
  overwrites the corrupt file, self-healing by construction.

### MEDIUM→LONG promotion can lose a lesson outright on destination-write failure (FOUND 2026-08-11, adversarial review of the rationale-erosion chunk — pre-existing, HIGH; **FIXED same day, Jeremy: "let's fix that now"**)

Destination-first write (stage under MEDIUM lock → append-if-absent
under LONG lock → remove under MEDIUM lock with guard re-check);
record archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".
Residual watch-item: an abort's LONG rollback interleaving a
concurrent decay-cycle read reduces to the PRE-fix loss mode, never a
new one — a real WAL is the stronger fix if it ever fires.

### Async the post-run tail — return the result, don't make the user wait for closure + learning (Jeremy, 2026-08-11; **PHASE 1 SHIPPED 2026-08-12**)

Maintenance/evolver phase (2026-08-12) and answer-first notify with a
follow-up verdict stamp (2026-08-13) both shipped; full record
archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16". Standing
watch-items from the first-cut review, accepted-not-fixed:
- `inspector.run_cadence` reads the PREVIOUS cadence firing's summary
  on cadence-fire runs (default OFF).
- Gate-escalation retry can't see same-run maintenance products
  (missing nice-to-have, not a poisoned input).
- `handle()`'s `_hid=None` exception path can strand registered
  callables (shared with the learning-lane registry) — fix both
  together if it ever bites.

### Workspace export/import — live-tested box→M1 2026-08-12: file copy is exact, but four classes don't transfer (Jeremy: "I suspect that won't get us 'everything' and that's important to understand")

Ship record (round-1 export test, hardened round-2, archive-format
v2 + meta/ area, 3-lens reviews, `maro-export inspect`, meta-staging
decision) archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16"
— location gaps CLOSED as of format v2; residual gaps are semantics,
not fidelity.

**Export-side placeholders (successor design, sketched 2026-08-18)** —
`docs/PATH_PORTABILITY_DESIGN.md`. Jeremy's call after reviewing the
residual: substitute `$MARO_ROOT/`-style placeholders at EXPORT and expand
at import, rather than rewriting source absolutes into local absolutes on
the way in ("correct going out and not painful going back in"). Measured
motivation: **2,029 files / 25,278 occurrences still embed `/home/clawd`
after the current rewrite**, the largest bucket (6,150) being the
pre-rename repo root, which is stale on BOTH machines. Design carries the
root table (docker mounts are identity-mapped, so the only non-identity
root is `/tmp` scratch; the self-dev clone is a distinct root), the
owned-vs-observed rule (never substitute a scavenge/fence finding — the
absolute string IS the evidence), and invertibility as a hard requirement
(archives are byte-faithful today and the lesson-ledger restore relied on
it; Jeremy's ship-both-forms suggestion satisfies it by construction, kept
at manifest level so two copies cannot drift per-record). Forward-only:
`path_rewrite` stays for legacy archives. NOT STARTED — spec only.

**Path-token rewriting — SHIPPED 2026-08-16 as shape (b)**, on
Jeremy's call to stop deferring it ("the intent was to do it later, not
kick it down the road perpetually"; filed 2026-08-13 with his worry on
record: *"I'm a little concerned that's setting us up for troublesome
bugs in the future if we don't go there"*). `src/path_rewrite.py` is the
shared transform, wired into BOTH transfer lanes; record with the
trap list and the shape comparison archived: BACKLOG_DONE.md §"Moved
from BACKLOG 2026-08-16". Residuals, none blocking:
- **Only recorded install roots are mapped** — workspace, `~/.maro`,
  repo. In the box copy that is ~73% of embedded occurrences plus the
  9,242 naming the checkout; the rest stay as they are, deliberately —
  2,169 under `.poe/`, 1,212 under `.openclaw/`, 1,193 naming the
  PRE-RENAME checkout path (`claude/openclaw-orchestration`, which the
  current `repo_root` does not match), 801 a stale worktree, and loose
  `$HOME` paths
  (a stale path is inert, a confidently-wrong one resolves somewhere
  real). If those ever matter, the answer is more RECORDED roots at
  export, not a broader regex.
- **Shape (c) — a runtime path-alias layer — is still the durable
  end-state** if cross-machine sharing becomes routine; (b) buys the
  working copy without touching every consumer.
- **A digest recorded INSIDE the workspace over a rewritten file goes
  stale.** Nothing verifies such a digest today, and the archive's own
  shape digest reads archive member sizes, so import verification is
  unaffected. `path-rewrite.json` names every file touched, which is
  what makes any future staleness auditable.
- **Existing archives predate `source.repo_root`** (added at export
  2026-08-16). Importing `~/maro-box-export-v3.tar.gz` maps the
  workspace and `~/.maro` but not the checkout. Re-export from the box
  to get the third root.


### Session-fork lane for claude -p (Jeremy idea 2026-08-08) — **SHIPPED same day, opt-in; daemon variant = residual edge**

`subprocess.session_fork` (~5× lower cost/quota burn, ~34% lower API
latency vs bare `-p`); record archived: BACKLOG_DONE.md §"Moved from
BACKLOG 2026-08-16". Residuals: process-boot latency ~2–3s/call
remains (warm-daemon/Agent-SDK variant is the upgrade if boot time
ever matters at census scale); fork session files accrete under
~/.claude/projects — opt-in cleanup only, per data retention.

### Re-run identity — a re-dispatched goal should KNOW it's a re-run, with prior art, not rediscover (or misread) its own history (OPENED 2026-08-09, Jeremy; **v1 SHIPPED 2026-08-10**)

**v1 SHIPPED 2026-08-10** — deterministic prior-attempts brief
(dispatch navigator + AGENDA run context), adversarially reviewed and
hardened (10 fixes); record archived: BACKLOG_DONE.md §"Moved from
BACKLOG 2026-08-16".

**Residuals filed (decision-shaped, not riders):**
- Specialty AGENDA modes (`direct:`, `mode:thin`, `pipeline:`,
  `team:`) return before the ENTIRE `_extra_ctx_parts` assembly —
  recall, completion standard, persona, scope AND this brief (3-lens
  consensus). Pre-existing architecture, not a regression; the durable
  fix is centralizing AGENDA context assembly before mode dispatch,
  which changes what those modes see and needs its own chunk.
- CLI-lane runs (`maro run` / `maro resume`) never write intake rows,
  so the brief can't see them (it now says so honestly). Durable fix:
  one attempt-start recording owner used by every execution entry
  point.
- Perf posture: ~100ms per scan at 57k rows, twice per autonomous
  dispatch (navigator + run seams computed independently; views can
  also skew across the gap). Acceptable today; if the ledger keeps
  growing, the fix is one immutable snapshot per dispatch + a derived
  rebuildable exact-key index, not two lifetime scans.

**Live miss attached 2026-08-21 (Jeremy: attach it, "pretend it hasn't
[been addressed] and see what happens when we look again"):** on
2026-08-14 — four days AFTER v1 shipped — Jeremy pushed the Yarchi
fan-out-study link through directly (`751e2dea-merry-thicket`:
"Please ask maro to research this against our own codebase", ended
**incomplete, goal_achieved=False**). On 2026-08-20/21 the link-farm
burn-in runs (`0ebadc02`, `f89bf29b`) reviewed and then RECOMMENDED
the same study — citing its exact numbers (260 configs, 17.2x) —
without either run discovering the prior incomplete attempt. Nothing
misfired by v1's own lights: the goals share no wording, only a
SUBJECT (the same X status id, which sits in the run artifacts and
the link-farm DB, not in the later goals' text). That is the gap to
examine fresh: prior art keyed by ENTITY (URL/status-id/repo), not
goal similarity — the same entity-not-lexical shape as the dev-recall
ranking miss (Actionable Stack, 2026-08-20), and plausibly the same
fix surface. Concrete acceptance probe when someone picks this up:
re-dispatch any goal naming that study and the brief should carry
751e2dea's incomplete attempt.
- [ ] Entity-keyed prior art: decide the join key set (URLs / X status
      ids / repo slugs in goal text AND in cited artifacts), where it
      lives (intake rows? a derived index?), and whether the
      memory-as-module bake-off (arc -1) already owns this.


### MH. Model-or-Harness taxonomy — the ADOPT edges from maro's self-evaluation (OPENED 2026-08-09, from runs de790c13 + 6fa41f96)

Two dispatched runs evaluated maro against arXiv:2607.28802 ("Model or
Harness?", Raj et al. — 41 failure modes localized to component-interaction
edges with a fault side): cold `de790c13-eager-lichen` (2026-08-08, demoted
by the since-fixed brace-template provenance costume, closure-contested)
and its verbatim warm replay `6fa41f96-stout-quartz` (2026-08-09,
achieved=True, closure 0.82), which audited and corrected the cold run's
artifacts rather than redoing them. Deliverable: workspace
`projects/httpsxcomxudong…evaluate/FINAL_VERDICT.md` + `artifacts/
step-8-adopt-skip-rulings.json` + `step-9-adversarial-verification.json`.
**Corrected tally: ALREADY_HAVE 7 / ADOPT 12 / SKIP 22.**

The ADOPT edges, keeping FINAL_VERDICT.md's own numbering (#2 was
retracted by the step-9 adversarial pass — see below):

- **#1 Specification Gaming (model—grader), CRITICAL** — closure's checks
  are command+exit_code pairs, structurally gameable; the verification
  trust model has no defense against the failure mode aimed at it.
  Highest leverage of the twelve. **v1 BUILT 2026-08-10** (detection +
  trust-degradation, deliberately NOT prevention): the gameable class
  is an achieved verdict resting entirely on static checks —
  `closure.pass_audit` (OFF fresh installs, ON this box) gives those
  one adversarial refutation call on the shared audit evidence lane;
  a typed refutation caps confidence below the 0.7 learning floor and
  stamps `mh_class: specification_gaming_candidate` — never flips the
  verdict (no new false-demotion lane). **Prevention half v1 SHIPPED
  2026-08-12** (`src/execution_receipts.py` + pass-audit wiring): the
  recorder's call files (build/calls tool_events — written by the
  harness at call time, executor can't forge post-hoc) are digested
  into the pass-audit prompt as a three-valued receipt block: process
  work recorded / NO process work recorded / record UNAVAILABLE (no
  signal — absence of record is never evidence of absence). Auditor
  taught: artifacts claiming process work with no recorded execution =
  positive refutation evidence; receipts sit OUTSIDE the untrusted-
  artifact fence. Same posture as v1: grounds the audit, never flips a
  verdict, never blocks closure, rides `closure.pass_audit` (no new
  flag). **Skeptic round 2026-08-12** (codex, 1-lens; all 5 findings
  verified REAL — 0% hallucination this round) → hardened same day:
  (1) `is_error` now carried into receipts + `[HARNESS FLAGGED ERROR]`
  in the digest; runner-marker match relabeled a TEXT heuristic with
  read-the-command-line caveat (`echo pytest` genuinely receipts an
  echo); (2) receipt content fenced (`<<<BEGIN/END HARNESS RECEIPTS>>>`
  + data-never-instructions doctrine — the trust claim covers the
  record, not the executor-authored text inside it); (3) partial
  records honest: unreadable/malformed files COUNTED, cap truncation
  flagged, "NONE recorded" only on a complete record ("none among the
  READABLE records" otherwise), all-unreadable → UNAVAILABLE; (4)
  bounds: 8MB/file + 1000-file scan cap, oversized counts unreadable;
  (5) adversarial pins for all of the above (echo look-alike verbatim,
  failed-runner flag, incomplete-never-claims-absence, fence-in-prompt).
  **Fixpoint round 2 2026-08-12** (codex, inline-prompt after two
  0%-CPU hangs on file-pointer prompts): 6 more findings, again ALL
  verified real → fixed same night: (1) schema-invalid call files
  (valid JSON, non-list tool_events) now COUNTED unreadable; (2) the
  big one — narrow runner regex + "complete record refutes" doctrine
  was a false-degradation lane for jest/vitest/gradle/bazel projects:
  regex widened, NONE claim scoped to "KNOWN patterns (not
  exhaustive)", no-match digest now shows a sample of actual recorded
  commands, auditor told to judge from commands not the summary line;
  (3) newline-in-command harness-line forgery (fake "RECORD
  INCOMPLETE" injection) → `_display()` flattens CR/LF; also
  fence-spoof neutralizer (`neutralize_fence_text` mangles `<<<`)
  added to BOTH receipt and artifact-evidence lanes (self-found:
  artifact excerpts could close their own fence early — injection_guard
  has no fence-marker pattern); (4) glob discovery bounded via islice
  (junk-spammed calls dir can't force unbounded glob+sort); (5)
  listing/clip truncation now visible ("showing first 8 of N",
  "[digest truncated for length]"); (6) bogus cap falls back instead
  of raising. 30 pins. **Round 3 2026-08-12 — FIXPOINT DECLARED**: 3
  findings (1H/1M/1L, again all real), pure display-accounting class →
  fixed: error AGGREGATE line independent of display caps + error-
  flagged rows sort to the front of every bounded listing (a failed
  9th runner can't hide behind 8 benign look-alikes); type-corrupt
  tool events (non-string command, non-dict event/input) counted as
  `malformed_events` → RECORD INCOMPLETE (no-command events still skip
  silently — that's normal Read/Write traffic); zero-rows-with-
  corruption names the right UNAVAILABLE reason (not "record mode
  off"). 35 pins. Severity trend 3H→2H→1H-narrow, round-3 class was
  accounting not architecture → stopped per converges-by-3-4. Ops
  note: codex CLI flaky tonight — file-pointer prompts hung twice at
  0% CPU; inline-everything + foreground + timeout is the reliable
  shape. Deferred, evidence-gated: mechanical receipt→check matching
  as a closure-check INPUT (skip-the-LLM upgrade) — build if
  pass-audit stamps accumulate showing receipts alone would have
  decided.
- **#3 Observation Failure (env—model), high** — `step_exec.py`
  `_summarize_tool_events` truncates 2000 chars / 50 events silently
  (same class as the arbitrary-truncation audit already on this backlog).
  **BUILT 2026-08-09**: cut outputs now carry
  `…[output truncated: +N chars in the full transcript artifact]` +
  `output_truncated: True`, and event lists cut at 50 append a
  `[transcript truncated]` sentinel naming shown-of-total — caps bound
  the view, the transcript artifact stays the full copy
  (artifacts-over-streams). Pins in test_step_exec.py.
- **#4 Instruction-Grader Mismatch (owner—model), med-high, INFERRED** —
  closure checks derive from the owner instruction; drift = pass-but-wrong.
  **RE-VERIFIED 2026-08-11 → mostly ALREADY_HAVE; one evidence
  improvement SHIPPED.** The ruling's premise under-counted the existing
  anti-drift structure: check generation is anchored by scope
  failure-modes (inversion), explicitly-declared deliverables with
  pre-flighted preconditions, and a ground-truth file inventory ("probe
  these exact paths, do not invent names"); each check carries a
  declared binding (`failure_mode` + `description` in the plan schema);
  runtime-shaped deliverables REQUIRE a behavioral probe with explicit
  waiver accounting; and post-#1, `pass_audit` second-opinions
  all-static positives with the goal in hand. Shipped: both audit lanes
  (pass_audit + verdict_audit) now render each check's DECLARED PURPOSE
  beside its command — a check verifying something other than what it
  claims was previously hidden behind a bare command line (pinned in
  test_closure_verdict_audit.py). Named residuals, not built: (a) the
  plan's `failure_mode` field is dropped at check execution
  (check_results keep description/command/modality only) — preserving
  it would enable a mechanical drift census over closure_verdicts.jsonl;
  (b) the scope-absent lane (scope_generation OFF, scope-raw-FAILED
  runs) does its own inversion from the goal alone — the least-anchored
  derivation path, no cheap fix shape identified.
- **#5 Tool Feedback Neglect (model—tool)** — `tool_transcript` already
  logs `is_error`; cheapest build (classifier over existing data).
  **BUILT 2026-08-09** (with #10/#11/#12 — see the classifier note below).
- **#6 Communication Failure (subagent edge)** — subagent I/O captured
  raw; under-reporting to the parent plausible and undetected.
  **BUILT 2026-08-11** (detection-only, the #5/#10–12 pattern).
  Live-source re-verification first (caveat (a), and it narrowed the
  build): the compile window's 4000-char cut is already MARKED
  (`context_budget.clip`, truncation-audit idiom), so visibility was
  covered — the gap was purely that nothing checked whether worker
  content SURVIVED into the compiled report. `director._report_echo`:
  mechanical contact floor sharing `memory_bridge.distinctive_terms`
  (one extraction rule, no drift), asymmetry INVERTED vs slice_echo and
  documented as such — compilers paraphrase, so True is weak coverage
  evidence, but False (fewer than 3 distinctive terms from the whole
  worker output in the report) is a DROPPED worker. Stamped on
  `WorkerResult.report_echoed` in the LLM compile path only (dry-run and
  exception fallback concatenate verbatim — a check that could not fail
  proves nothing → None), persisted per-worker in the director log, and
  a DONE worker with False emits `WORKER_REPORT_OMISSION`
  (mh_edge subagent / mh_class communication_failure_candidate —
  candidate-grade, advisory, never control flow; blocked workers
  excluded, their absence is already visible via status). Corpus
  context, measured on box: 30 legacy director logs / 49 worker rows,
  median result 5,278 chars, **59% exceed the 4000-char compile
  window** — when this lane runs, the compiler is selecting from a
  clipped majority, which is exactly where drops happen. Honest bound:
  old logs store result_length only, so no retro detection; the lane is
  low-traffic (director mostly bypassed via skip_if_simple), so the
  signal accrues only when multi-worker runs happen. 11 pins
  (tests/test_report_echo.py); local-import shadowing gotcha caught by
  the existing WORKER_SLICE_INJECTED pin (a function-local `log_event`
  import would have unbound the module-level name for the whole
  function).
- **#7 Overgeneralization (model—memory), INFERRED** — cheap relabel of
  `memory_ledger._maybe_emit_contradiction_candidate` (L983).
  **BUILT 2026-08-09**: CONTRADICTION_CANDIDATE events now carry
  `mh_edge: model-memory` / `mh_class: overgeneralization_candidate`
  (candidate-grade until the adjudicator rules).
- **#8 Missed Read (model—memory), INFERRED-cost** — `memory_quality.py`
  is a working offline hit@1/hit@5/MRR eval with ZERO call sites; adopt =
  wire it live. Batch-CLI→per-run cost was not scoped; "cheap" unverified.
  **COST SCOPED 2026-08-11 (measured live, full box corpus) — and the
  measurement surfaced the real finding.** Cost answer: one full-corpus
  eval = 1,957 items / 2,011 queries, **zero LLM tokens** (paraphrase
  queries are pre-generated and cached; self/probe queries are pure
  extraction), ~50s wall dominated by the jsonl lane (23.7ms/query;
  sqlite-fts5 is 1.2ms) — so "cheap" is TRUE for periodic wiring and
  false for per-run (a minute per run for a slowly-moving corpus metric
  is waste). Wiring shape when built: heartbeat/GC-cadence trend row
  (weekly-ish), report already lands in `output/memory_quality/`.
  **The finding: semantic retrieval is near-dead on both adapters.**
  The module's own fair lane (LLM-paraphrase queries, checker verified:
  expected-item scoring by content sha1, spot-checked queries are
  meaning-preserving rewords) scores **hit@1 2.0% / hit@5 8.2% (jsonl)
  and 6.1% / 14.3% (sqlite-fts5)** vs the lexically-biased self lane's
  46%/80% (jsonl) and 80%/89% (sqlite). n=49 (cache vintage 2026-07-08;
  binomial 95% upper bound on 2.0% is still only ~11%) — a run asking
  memory for something in words other than the stored ones essentially
  never gets it. This QUANTIFIES the Missed Read edge and is direct
  input to the memory-as-module bake-off priority (MILESTONES arc -1,
  memory_port): the incumbent adapters lose on exactly the semantic
  axis a 3rd-party store would bring. Regenerating/extending the
  paraphrase cache to the current corpus (cheap-tier calls,
  `scripts/gen_paraphrase_queries.py`) is part of any wiring build.
  READING_QUEUE row added — wire-at-cadence vs bake-off-priority is
  Jeremy's call.
- **#9 Instruction-Following Failure (owner—model)** — relabel
  `checks_run`/`failed_checks`, already structured. **BUILT
  2026-08-09**: an incomplete verdict with concrete failed checks gets
  `mh_edge: owner-model` / `mh_class: instruction_following_failure` on
  both the CLOSURE_VERDICT event and the persisted
  closure_verdicts.jsonl row; label absent when the shape doesn't hold.
- **#10 Malformed Arguments / #11 Tool Hallucination / #12 Tool Recovery
  Failure (model—tool)** — three mechanical classifiers over
  `tool_transcript` (args+is_error split-out; called-name vs registry;
  N-consecutive-failures joined with `stuck_loop`). **BUILT 2026-08-09**,
  together with #5: `introspect.classify_tool_pathologies` (pure, no
  LLM), stamped at the SOURCE in step_exec (transcripts on disk are
  keyed by step number only — later loops overwrite them, so
  diagnose-time attribution can't be trusted), riding `step_done`
  events into `diagnose_loop` as four new FAILURE_CLASSES with recovery
  plans (none auto-apply). Live-source re-verified per caveat (a) and
  corpus-smoked over all 610 persisted transcripts:
  **tool_hallucination 178 (29%!)** — mostly inner sessions calling
  lowercase `bash`/nonexistent names, detected by the
  "No such tool available" signature and kin to the
  advertised-but-absent-tools item below (which now has a per-run
  detection lane instead of an offline census); tool_feedback_neglect
  ≤93 (upper bound — offline smoke assumed done status);
  tool_recovery_failure 7; tool_arg_malformed 0 (env-failure
  signatures deliberately excluded per the LT-4 container audit).
  Classes claim the diagnosis only when no structural class did;
  evidence appends regardless.
- **#13 Delegation Failure (subagent edge), INFERRED** — extend
  `attribution.failed_skill` toward scope/dependency mismatch vs
  Task-call input; assumes Task-call input shape, not re-verified.
  **BUILT 2026-08-11** (detection-only) — and caveat (a) earned its keep
  again: re-verification showed the assumed "Task-call input shape" does
  NOT exist (delegation input is the ticket+context strings; the block
  signal is `flag_blocked`'s free-text reason), so the honest mechanical
  floor is `attribution.delegation_gap` — a provision-shaped keyword
  lane over blocked reasons ("not provided" / "no access" / "unclear
  which" / …), deliberately NOT a lexical-contact check (workers name
  the missing thing in the ticket's own vocabulary, so the #6 echo
  design cannot discriminate here — considered and rejected). Wired at
  the director's post-compile candidate loop: BLOCKED worker +
  provision-shaped reason → `delegation_gap: true` on the director-log
  row + `WORKER_DELEGATION_GAP` event (mh_edge subagent / mh_class
  delegation_failure_candidate — candidate-grade by construction:
  parent-vs-worker FAULT can't be settled mechanically). Same honest
  bound as #6: forward-only, low-traffic lane. Pins in
  test_attribution.py + test_report_echo.py.

Plus two SKIP rulings the verdict flags as genuine unresolved gaps, not
dismissals: **Memory Following Failure** (`memory_slice_injected` is an
A/B flag with zero consumers verifying behavior followed the injected
content — the verdict's "single most actionable finding"; **ADDRESSED
2026-08-09**: `memory_bridge.slice_echo` — mechanical lexical-contact
lower bound, deliberately named "echo" not "followed" (True = real
contact, False = weak evidence, None = nothing to judge) — wired at
both director dispatch sites onto `WorkerResult.memory_slice_echoed`
and into the director-log worker_results payload, so the A/B now has a
behavior column, not just an exposure column) and **Memory Rationale
Erosion** (compress/dedup rewrite records, nothing checks rationale
drift — **ADDRESSED 2026-08-11 in both dedup lanes**: at >0.8 word
overlap the dropped ~20% can be exactly the operative clause ("when Y"
vs "when NOT Y"), and both merge paths discarded the incoming/dropped
text silently — a retention-decree violation (decay trust, never
data). Now the survivor keeps absorbed texts under `merged_variants`
(capped at `memory_ledger._MERGED_VARIANTS_CAP`=5 per row — beyond it
the merge still counts via times_reinforced; bounded, NAMED loss):
(a) the sweep lane, `deduplicate_lessons` near-dup merge; (b) the live
lane, `record_tiered_lesson`'s two dedup scans →
`_reinforce_tiered_lesson(incoming_text=…)` — contested rows keep
variants too (refight evidence, like the frozen counter), and
prompt-derived re-records still never reach reinforce (provenance
gate), so instruction text cannot land in stores. 5 pins
(test_memory.py, test_tiered_memory.py). Remaining lanes, named not
silent: playbook curation already archives the full prior version
(append-only) before compress; the three-layer outcome compression
removes raw outcomes after LLM-summarizing them by design
(space-reclaim with keep_recent guard + outcome_ids provenance) —
whether the summary preserves enough rationale is a separate question
from these merge fixes). **Codex 2-lens adversarial review same day
(scope fdb11d1..bf05be3): REJECT-as-reviewed → remediated same
session — 8th round in this arc to earn its cost, with repros.**
Fixed: (1) the flat LIVE lane (`_store_lesson` near-dup reinforce) was
a THIRD merge path still discarding text — and the UU-4 dual-write's
flat half, so same-id tiered/flat rows silently diverged →
`_absorb_variant` is now the ONE owner of the union rule, used by all
three lanes; (2) dedup dropped an absorbed row's own merged_variants
(merge not closed under its own output) → closure union in both exact
and near branches; (3) the outside-the-lock dedup match could attach a
variant to a refight-REVISED row → similarity rechecked against the
live row inside the lock before attaching (the counters/provisional
side of that race is PRE-EXISTING — residual below); (4) cap bounded
count not bytes (1.8MB-row probe) → per-variant clip at 500 chars,
marked cut; (5) pack import silently dropped merged_variants →
carried, validated, re-bounded locally; (6) refight revise could leave
the new canonical duplicated inside variants → pruned; (7) MH #13
keywords fired on adapter failures ("LLM call failed: no access…") →
`WorkerResult.blocked_origin` (worker/adapter/empty) and the
classifier reads worker-authored reasons only; (8) the "prompt text
cannot land here" comment overclaimed → bounded honestly (gate-off
exposes canonical text identically; variants never MORE exposed than
the store). **Residuals filed:** MEDIUM→LONG promotion atomicity (new
top-of-stack entry — pre-existing HIGH; FIXED same evening); reinforce-race
counters/provisional-clear on refight-revised rows (pre-existing —
**CLOSED 2026-08-11 late evening**: reinforcement is now version-bound
whole-hog, not just the variant attach — `matched_lesson_text` rides
both dedup call sites and a mid-flight revision voids the entire
sighting as a no-op; by-id reinforcement passes no matched text and is
exempt by design. Live-store census on box, same evening: 0
interrupted-move leftovers, 0 LONG duplicate ids, 0 cross-type twins —
the promotion-loss and sweep bugs never fired in production; 96 UU-4
dual-write pairs are live, so the flat/tiered consistency fixes are
load-bearing forward); per-variant provenance if
pack quarantine ever needs to filter variants.

**Fixpoint round (2026-08-11 evening, Jeremy's ask: review work+fixes
as one whole; Codex 2-lens over 579cc6d..8b5bf05): REJECT again — the
round found what per-delta review couldn't, all fixed same session.**
(1) HIGH, pre-existing made worse: the flat reinforce paths mutated a
PRE-LOCK stale copy and the per-id wholesale rewrite made concurrent
reinforcements last-writer-wins (variants AND counters silently
vanished; the tiered lane fixed this identical class 2026-08-04) →
`_reinforce_flat_row`: single-row locked RMW, mutation applies to the
on-disk row, unparseable lines preserved verbatim. (2) HIGH,
pre-existing: the SWEEP merged across task types against the live
lanes' documented contract ("identical text under a different task
type is a separate lesson") — deleting rows the live lanes keep,
breaking UU-4 same-id joins → sweep is task_type-scoped now, and the
survivor absorbs the dropped row's reinforcement HISTORY (+1+absorbed,
not +1 — that counter is refight evidence). (3) clip-before-equality
broke identity: a >500-char canonical's exact re-record stored its own
clipped twin as a variant, and stored clips re-clipped into fresh
twins (marker not idempotent) → identity judged before clipping,
marker-carrying texts never re-clip, canonical stripped for compare.
(4) the round-1 similarity recheck was NOT identity ("always
validate…"→"never validate…" scores 0.88) → variant attach now binds
to the EXACT matched canonical text (byte-equal), not a similarity
floor. (5) pack import's skipped_identical early-exit lost foreign
variants (transport order-dependent) → collision unions variants into
the local twin (data crosses, trust does not); intra-artifact
duplicates closed (identity snapshot updated after append). Rejected
with rationale: typed blocked-origin shape (the "" default not
classifying externally-built results is the conservative design for a
candidate lane). Residual added: import-side near-dup canonical
reconciliation policy (foreign near-dups still append a second row —
trust-aware near-merge is its own design). Suite 8259/0 skipped.

Standing caveats to honor before building ANY of these: (a) every ruling
is static-source-reading — "code path present," never "problem solved";
(b) the list is 8 source-grounded + 4 inferred, and step 9 FALSIFIED one
source-grounded claim (cold's ADOPT-critical #2, Indirect Prompt
Injection — `security.py:scan_external_content` is already wired at
`loop_execute.py:352`; reclassified ALREADY_HAVE-partial), so each edge
deserves the same live-source re-verification #2 failed, not a free pass
for citing a file name; (c) both runs' artifacts kept growing
prose-vs-JSON drift (three instances found) — trust the patched JSONs
(`step-8` rulings + `step-4-reclassification.json`) only after checking
their tallies against `FINAL_VERDICT.md`'s corrected banner.

### Closure Signal-1 "not executed" downgrade — a false-demotion costume in the fd483efb fix's sibling lane (FOUND 2026-08-09, run 18773dfa; **fixes SHIPPED same day; re-stamp EXECUTED same day**)

Fix (document-only stand-down + verdict-audit pass) and its
adversarial-review hardening shipped 2026-08-09; record archived:
BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".

**Residuals filed from the review (real, pre-existing or shared):**
- `_failed_check_file_evidence`'s original failed-check lane accepts
  absolute/`../` paths despite its cwd-containment docstring — same leak
  shape the audit lane now guards; fixing it changes long-shipped verdict
  evidence behavior (self-inspection checks legitimately reference maro
  source), so it needs its own decision, not a rider.
- Contested/disputed neutrality is honored at exactly ONE seam, and the
  entry's old headline was wrong — **re-measured 2026-08-16, correcting
  "nothing reads it in learning-adjacent consumers".** What IS wired:
  both contested lanes converge (`provenance.contested_by_closure` and
  `closure.verdict_audit.disputed` → `_contested_verdict_loop_ids` in
  `handle.py:2972,3218`) into `_dl_skip` →
  `finalize_deferred_learning(skip_loop_ids=…)`, so a contested run does
  NOT vote in lesson minting; `runs.stamp_run_verdict_contested`
  persists it and `rerun_identity.py:179` reads it back. That is real
  coverage and should not be rebuilt.

  What remains is narrower and structural, so keep the prescription:
  (1) the state is a metadata stamp plus an **ad-hoc `set[str]` local to
  one `handle.py` function** — there is no typed contested field on the
  outcome model, so nothing outside that function can ask "was this
  contested?" without re-deriving it; (2) `recall.py`,
  `outcome_policy.py` and `loop_finalize.py` contain **no reference to
  contested at all** (grepped, empty), so a contested run's outcome
  still counts normally wherever those compute success rates or
  repeat-pressure — `strategy_evaluator` scoring raw
  `goal_achieved=False` as full failure is the same shape; (3)
  enforcement sits at one call site, so any other path that finalizes
  learning inherits nothing. Sibling-lane shape: the fix is one typed
  field consulted by a shared policy, not a second skip-list.
- A deterministic downgrade on an all-passed check set can never engage
  `closure_restart` (restart requires a non-pass, audit-eligibility
  requires zero fails) — the module's "over-eager demotion costs one
  bounded restart" safety argument does not hold for exactly the shape
  the audit targets. Pre-existing; decide whether an audit-agreed
  downgrade should be restart-worthy.

Both false-demoted runs re-stamped 2026-08-09T12:06Z (record
archived with the original finding).

### Live-writer census findings — one open of four decision-shaped items (OPENED 2026-08-06; three shipped, archived 2026-08-11)

The 2026-08-06 live-writer census (method: a gate is only as live as its
input's writer; full LIVE/SUSPECT/DEAD record with evidence in
`docs/history/2026-08-06-live-writer-census.md`) fixed the mechanical
finds same-day (three more `use_count` corpses + the self-variant A/B
bug, f71be8d). These four survivors each need a decision or a design
pass, not a quiet fix:

- [ ] **5b residual: drop orch_root()'s prototype layout — churny
  test-sweep cleanup (deferred by Jeremy's call 2026-08-06; writers
  migration SHIPPED same day, see BACKLOG_DONE).** The 5b writers
  migration moved all runtime-data anchoring off `orch_root()`
  (hooks/.hooks → workspace w/ legacy read-fallback, persona agents/
  manifest → output_root, projectless director logs → output_root,
  checkpoint fallback → workspace w/ read-fallback to old location,
  git-cwd/BACKLOG consumers → new `repo_root()`). What remains before
  `orch_root()` can become a pure code-root resolver (repo detection
  always, no `<ws>/prototypes/maro-orchestration` shape): (1) the
  `_ws_pinned` test-isolation guard in `orch_root()` — droppable only
  after confirming no remaining orch_root consumer writes data under a
  pin; (2) ~8 test files seed `workers/` at the proto path
  (test_orch_core, test_cli, test_build_loop_script, …) and would need
  re-seeding at the repo-detected root or a workers override; (3) the
  latent sharp edge this would fix: under any workspace pin with no
  proto dir, `orch_root()` points at a nonexistent path, so
  `workers_root()`/repo personas are unresolvable (workspace-arg
  build-loop runs live with this today). Mostly mechanical churn, low
  production value until someone hits (3) for real.

### SP. Session-protocol arc — two-box Hermes dispatch, interactive goals, effort UX (OPENED 2026-07-15, Jeremy)

The umbrella for the next big lane; full skeleton + stance decrees in
**`docs/SESSION_PROTOCOL_DESIGN.md`** (iterate there; decree record in
GOAL_BRAIN Decisions 2026-07-15). New box arrives 2026-07-16 → Hermes on it as
a real end-user interface dispatching to Maro here. Staged: prove ssh/tailscale
dispatch slice first (enqueue from box B, run_card back), then effort+consent →
live progress query → next-pending-step injection (seam refactor first) →
clarification loop. Pre-box actionable now: **§6 seam inventory** (where
step-context is assembled; what a typed, provenance-stamped injection input
needs). **DONE 2026-07-15 → design doc §6a** (verdict: qualified one-seam;
work list = typed contributions, parallel fan-out gap, context-only interrupt
intent). Side find, fixed same day: verify/threshold director-escalate replies
were clobbered by the carry-forward consume before reaching the next step
(`loop_execute.py`, pinned by `test_adaptive_escalate_reply_reaches_next_step`).
**Seam refactor v1 SHIPPED 2026-07-15** (see BACKLOG_DONE for the full
adversarial-review record): typed `ContextContribution` ledger, `maro
interrupt --intent note`, parallel-batch threading. Remaining §6a gaps
(run-scoped `ancestry_context_extra` shapes, resume step-text mutation,
worker lane) stay open in the design doc; `director_evaluate
(trigger="injection")` decided + ENABLED on this box 2026-07-16
(DEFAULTS.md row; the "evening decision session" framing here had gone
stale — corrected 2026-07-18).
Parallel, box-independent: tier-up test goals (flagship: the 5–6yr
Telegram trading-channel corpus → backtested strategy, research-only —
CAPABILITIES.md Tier 5). Related standing items it touches: escalation channel
(the substrate go-between decree IS msg-4's foundation), portable
learning (promoted: the data layer that makes "active orchestrator" a runtime
fact), container-on posture for network-sourced goals (revisit at Hermes
go-live).


### LT. Live-learning test arc — target burndown, cold/warm deltas, capability ladder (OPENED 2026-07-31, Jeremy)

*LT-5 (sonnet-min-bar re-run, 2026-08-06): verdict flipped to
achieved at lower cost — record archived: BACKLOG_DONE.md §"Moved
from BACKLOG 2026-08-16".*

Run live learning tests against goals we have **claimed but not probed**,
measure whether learning actually levels up, and write down the capability
progression those tests are climbing. The corpus problem is already solved
— `docs/CAPABILITIES.md` holds 5 tiers of real asks under the claimed≠probed
rule, and `research/ai-failure-task-patterns.md` holds 24 external failures
in 6 families (7 already folded into the tiers). What's missing is
**evidence**: the majority of catalog rows read `target` ("we believe
current machinery covers it, unproven").

Six shape decisions at open (Jeremy, 2026-07-31 — 5 and 6 same day, on
review of the first draft):

1. **Weighted to target-burndown**, not net-new. Every run should also
   settle a standing claim. Net-new entries only where a corpus family is
   thin in the tiers — Family 3 (tool-use/execution verification) and
   Family 6 (agency/trust violations).
2. **Instrumentation is a prerequisite, and wider than verdicts.** Jeremy:
   "let's fix this, and audit anything we might want written down by the
   runs that isn't already; we've already got lots of words on paper
   trails, but I think we may be missing some of that still; the more we
   can examine after (or even during) the runs, the better; both at the
   edges of steps and in the different processing layers within the
   overall harness."
3. **"Leveled up" = cold-vs-warm re-run delta.** Every test goal runs
   twice — once with the lesson/skill store cold, once warm. The delta
   (cost, steps, verdict, which lesson/skill was cited) is the evidence.
   Doubles spend; needs no new machinery and can't be self-graded.
4. **The ladder gets its own short doc** — `docs/CAPABILITY_LADDER.md`.
   CAPABILITIES.md stays the goal well; the ladder is the progression map.
5. **A failure is never a success.** No goal may be graded pass-by-refusing;
   goals shaped that way get reframed with a positive deliverable, and a
   genuine miss is an unbuilt bridge, recorded as not-achieved. Full decree
   in LT-1.
6. **Trace the work and write it all down, before the batch runs.** Each
   decision, LLM prompt and output, step plan, and artifact — durable and
   meaningfully reachable by all three consumers (report, tests, mining).
   The review pass comes first because "we keep stumbling on data that we
   thought we had but didn't." Full decree + first-pass findings in LT-0(d).

Vocabulary is already decreed — do not coin a parallel one.
`COMPOUND_THINKING_DESIGN.md` §8: capability edges are the only edges that
persist off the map, and "the tech tree IS the skill library / evolver." A
level-up paid once amortizes across the goal family. The bridges in this
arc are tech-tree nodes; the top rung of every ladder is a **skill
capture**, which is what makes the rung amortize instead of evaporate.

- [ ] **LT-0 — verdictability + paper-trail audit (PREREQ, blocks LT-1).**
  A batch that can't be examined afterward teaches nothing; the
  2026-07-29 packaging census already found real holes.
  - [x] **(a) Verdict-blind lanes — CLOSED 2026-07-31** (chunk B;
    record archived 2026-08-16).
  - [x] **(b) done-without-closure tripwire — SHIPPED 2026-08-02**
    (record archived 2026-08-16).
  - [x] **(c) Run-dir record census — TOOL BUILT 2026-07-31; BOX RUN +
    CORRECTED BASELINE settled 2026-08-01.** Census tool
    (`scripts/provenance_census.py` + wrapper), pre-registered
    predictions, both instrument fixes (era-split defect;
    day-granularity first-seen), and the BLOCKERS retraction
    (recall_citations 73% and skills_manifest 94% post-ship — the
    denominator defect a third and fourth time) archived:
    BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16". Settled
    reading: nothing in the paper trail blocks LT-1; surviving gaps
    are EDGE 2's ~19% silent call-record drop (below), step-*.md at
    50% of July runs, skill_attribution 57% (by-design). The SF-13
    decree pipe for this arc ran on the box (verified in
    decisions.jsonl 2026-08-16).
    - [x] **LIVE VERIFICATION 2026-08-01 — run `fd00c7be-humble-ferret`
      (agenda, local ledger census, 6m47s, $2.02).** The whole rail works
      end to end on a real run: `recall_citations.json` present and
      populated (4 rule ids + 3 lesson ids), `skills_manifest.jsonl` present
      with BOTH stages (decompose 2 skills, curated_summaries 1),
      `build/calls/` capturing 10 calls with **zero unlabeled purposes**,
      `loop_id` on the outcome row, and a closure verdict
      (`goal_achieved=True`, source `closure`, confidence 0.9). Deliverable
      was faithful and self-critical (it flagged a 26.7% stuck rate in its
      own sample — see the spin-off item below).
      **EDGE 6 paid for itself immediately.** Per-layer token attribution,
      previously unanswerable without prompt-sniffing:
      `step-execute` 381,197 in (**87%**) vs the ENTIRE orchestration stack
      (routing + clarity + scope + cuts + 3× decompose-candidate +
      decompose-compose) at ~56,000 (13%). One step burned **273,738 input
      tokens to run `wc -l` + `tail -3`**. That is the errand-envelope
      problem measured and attributed rather than inferred — and it says
      the lever is worker re-read churn inside the executor, not harness
      overhead. Backend is subprocess `claude -p` by deliberate choice
      (Jeremy 2026-08-01, "don't sweat the overruns"), so the repeated
      max_tokens cap overruns are expected and not a defect to chase.
      **NOT yet verified: the empty-case recording**, which is what the
      fixes were actually about. This run cited lessons and matched skills,
      so it exercised the populated path. The 10%/55% coverage came from
      runs where nothing was injected; that half proves out either on a run
      that matches nothing or by re-censusing once several August runs
      exist and watching coverage move toward 100%.
    - [ ] **EDGE 2 sharpened — it is a real silent drop, not early deaths.**
      22 of 114 July runs captured zero LLM calls. Of those 22, **16
      produced a plan** (decompose is an LLM call, so calls provably
      happened) and **11 reached `status=done`** with a `run_card`. A
      completed, planned, curated run with zero captured I/O cannot be
      explained by dying early — recording silently dropped. The census now
      computes this discriminator itself. NOW-lane drop rate (4/8) runs
      higher than agenda (17/105) but n is small.
      **Residual root-cause identified 2026-08-02 (specimen dig, three of
      the 22):** the drop is LANE-shaped. All examined zero-call runs came
      through the queue/task lane (origin `job_id: task-…` or null);
      every interactive `python3 -m handle` run in the LT series recorded
      fully. And `handle_queue.py`'s continuation branch says why in its
      own comment: it "deliberately bypasses handle()" and runs
      `run_agent_loop` inside `with scoped_run_dir(None)` — clearing the
      run-dir for drain-batch hygiene ("clear any stale run-dir left by
      an earlier task in the same drain batch"), which as a side effect
      makes **every continuation-lane run record zero LLM calls by
      construction** (record_llm_call no-ops silently with no pinned
      dir — the exact EDGE-2 silence). UU-1's fix doesn't reach this:
      these calls SUCCEED, they just have nowhere to record. Fix
      direction is a design call, not a one-liner: the continuation lane
      should own (or re-pin) a run-dir rather than run dirless — that
      touches loops-ledger/run-ref-index semantics of what a
      continuation's run identity IS, so decide deliberately, don't
      patch. Until then: queue-lane runs are known-blind on LLM I/O and
      any census read should segment by lane.
      **Jeremy's direction (2026-08-02, gut-check, mapped against the
      machinery and it fits):** full restarted run → **new id + prior
      run's id saved as parent** (pedigree link); interrupted-and-resumed
      → **same id, treated as a pause, same mechanisms.** Mapping: the
      parent link is exactly the existing ancestry pattern
      (`Origin.parent_handle_id` / `resumed_from` — already durable index
      keys); same-id resume is exactly the loops-ledger shape (one run
      dir hosts several loops; resume appends a loop row) and rides the
      just-shipped §13e pause machinery (typed pause_reason + real
      resume contract; paused-is-a-state decree 7afe8b3a). **The one
      base the gut-check didn't name, and the branch test the lane
      needs: terminal closure.** A queued "continuation" is today either
      shape: prior run reached terminal closure (judged) and this is a
      fresh attempt continuing the mission → RESTART, new handle_id +
      parent; prior run paused/died mid-flight (budget, kill, operator)
      → RESUME, same handle_id, new loop row. Cleanly decidable from
      what's already stamped (ended_at / goal_verdict_source /
      pause_reason). Two semantic consequences to hold in the design:
      **verdict counting** — restarts stay separate outcome rows (per-
      attempt honesty, ancestry ties them), resumes must not double-
      count (one row per run identity, per verdict_flow semantics); and
      **artifact continuity** — resume writes into the same run dir,
      restart gets a fresh dir and reaches prior artifacts via the
      project dir, which persists across both.
      **RATIFIED 2026-08-02 (Jeremy): "continuation-as-resume should
      result in the same as an uninterrupted run… continuation-as-new-
      attempt should result in the archaeology tie, but otherwise have
      its own separate run with data."** Plus his rider: **deletion
      safety** — any UI/tool that deletes goals/runs must copy or
      reference-check so data referenced by another run isn't silently
      destroyed. Design answer chosen for that rider (retention-decree-
      shaped, no copies, no refcount machinery): parent links are IDs
      not paths, and a restart reads prior artifacts via the project
      dir, so deleting a parent run dir breaks provenance depth, not
      function — therefore prune/delete tools do a **reverse lookup on
      the run-ref index for children naming the target as
      parent/resumed_from, and SURFACE the tie instead of silently
      deleting** (refuse-or-warn; operator decides — same posture as
      stale-clone surfacing). Copying was rejected (duplicates data,
      violates single-source); refcounting rejected (the index already
      gives cheap reverse lookup).
      **Implementation decisions taken with the ratification (stated,
      overridable):** (1) resume-≡-uninterrupted needs one explicit
      mechanism — the interrupted segment's partial outcome row is
      marked superseded by the resume's final row (verdict_flow counts
      events; a resume must not read as two attempts); cost/tokens SUM
      across segments, elapsed sums ACTIVE segments (a 3-day pause is
      not 3 days of work). (2) **Restarts re-enter through handle(), not
      direct-to-loop** — a retry after a judged failure must face the
      recall guard (the ~25× repeat-burn protection) and fresh
      scope/routing; resumes stay direct but re-pin the existing run dir
      and append a loop row. (3) Terminal-closure stamps
      (ended_at/goal_verdict_source/pause_reason) are the discriminator;
      if a queued task is ambiguous (no stamps either way), fail toward
      RESTART — a spurious new-id-with-parent is archaeology noise,
      a spurious same-id resume corrupts a closed run's record.
      **BUILT 2026-08-02** (Jeremy's clarifications folded in): the
      `loop_continuation` branch now discriminates on strict-affirmative
      resume (`pause_reason` set AND no `goal_verdict_source`; all else
      restarts). RESUME re-pins the parent run dir (EDGE-2 closed for
      this lane), appends the loop to the ledger, restamps
      status/ended_at, indexes, and marks older same-handle outcome rows
      `superseded_by` — **addendum, never overwrite, per Jeremy's
      clarification** ("a supersede without an overwrite"): rows keep
      every field, gain the marker
      (`memory_ledger.mark_outcomes_superseded`, same locked pattern as
      verdict stamping). RESTART goes through the full `handle()` front
      door (Jeremy: "a retry is still a run with extra context and
      data") — recall guard sees it, fresh scope, seeded context rides
      `operator_context` so it renders provenance-labeled, archaeology
      tie on origin; `force_lane="agenda"` keeps the
      skip-reclassification optimization. 8 new pins
      (tests/test_continuation_identity.py) + 1 deliberate inversion
      (test_escalation's dirless-lane pin → restart-shape pin). Suite
      7326 green.
      **Jeremy's cycle caution (a restart could just replay a
      hard-failure loop "with more pitfalls" — recursion injection,
      killer step cycles):** partially covered NOW by routing restarts
      through handle() — the recall repeat-guard (the ~25× repeat-burn
      protection) sees every retry, and deep-recursion check-ins ride
      origin depth. NOT yet covered: a hard depth cap on restart chains,
      and the guard keys on goal similarity so a NARROWED restart
      (revised goal) partially evades it. Left open deliberately —
      revisit when a live chain demonstrates the gap.
      **Residual (small):** budget-ceiling continuations only become
      resumes once the budget break-site stamps `pause_reason` (§13e
      vocabulary exists; verify the ceiling path stamps it — until then
      those passes restart, which is safe but loses same-run identity).
    - **Verdictability resolved by the per-month table**: 2026-04 1272 rows
      100% blind / 0 verdicted, 2026-06 66 rows 100% blind, **2026-07 112
      rows 52.7% blind, 41 verdicted.** The 96.3% headline was 1272 April
      rows. July's 52.7% still predates most of chunk B (shipped 07-31), so
      the live rate should be read from `verdict_flow`, not here.
    - [ ] **Store-history mismatch worth a look before LT-1 joins anything:**
      outcomes.jsonl has 1272 rows stamped 2026-04 but the workspace holds
      only 2 April run dirs, and **zero outcome rows for May** against 476
      May runs. The two stores' histories do not line up, so the
      runs↔outcomes joinable universe is roughly the July era (~112 rows /
      114 runs), not 1450/734. Cause unverified — likely the outcomes ledger
      predating run dirs, possibly a backfill stamp. Worth 10 minutes before
      any cold/warm join is built on it.
  - [ ] **(d) Full provenance trace — the decree, and the review pass that
    precedes the batch.** Jeremy 2026-07-31: "we should be capturing each
    decision, LLM prompt and output, step plan, and other artifacts along
    the way; they should be meaningfully available to the end user via the
    report we have as well as usable for testing and post-processing
    examination of the data… We keep stumbling on data that we thought we
    had but didn't for various reasons." **Three consumers, all binding:**
    the run report (human), the test suite, and post-hoc mining. A stage
    that writes nothing durable is a hole in all three at once.

    **First-pass trace (2026-07-31, dev-Mac read of the code — findings,
    not yet box-verified):**
    - Confirmed sound: `runs.open_run` pins the run-dir *before*
      `intent.classify` (handle.py:862 vs :961), so the routing call is
      inside record-mode's window; `record_llm_call` is a single seam over
      every backend in `FailoverAdapter.complete` (llm.py:713), scrubbed
      via `secret_scrub`; `scope.md` / `resolved_intent.md` /
      `recall_citations.json` / `skills_manifest.jsonl` are real run-dir
      writes; the report already renders timeline, steps, LLM calls,
      verdict, environment, run activity, decision points, and operator
      injections.
    - **EDGE 1 — the first decision of every run is unrecorded as data.**
      `intent.classify` returns lane + confidence + reason +
      introspects_self; only `lane` reaches `metadata.json`
      (`write_metadata`'s field set is fixed: handle_id, nickname, prompt,
      lane, model, started_at, ended_at, status, pid, + extra). The
      confidence and the reason go to stderr under `--verbose` and
      nowhere else. There is **no captain's-log event for classification**
      (no INTENT_* type exists). The rationale is reconstructable only by
      hand-parsing the `purpose="classify"` call record. This is the exact
      decision that produced the Manti Run-1 misroute — and a cold/warm
      batch measuring routing behavior has no queryable field for it.
    - **EDGE 2 — record-mode is silently conditional.** `record_llm_call`
      returns None (no error, no event) when recording is off OR no
      run-dir is pinned. `_current_run_dir` is a **ContextVar**: threads
      that don't inherit it write nothing. `loop_parallel` handles this
      correctly (`contextvars.copy_context().run`, loop_parallel.py:469)
      and `agent_loop`'s multi-goal pool opts out deliberately
      (agent_loop.py:803, each goal owns its own run-dir) — but the
      failure mode is *silence*, so any future call path off the pinned
      context loses its prompt/response with nothing to detect it by.
      Wants a tripwire: a run that finalizes with zero call records when
      recording is enabled should say so out loud.
    - **EDGE 3 — coverage is unmeasured.** No census exists for "did this
      run write the artifacts it should have." Sub-item (c) is that
      census; it needs to run on the box (the 3-run Mac sample is
      unrepresentative).
    - **The census measures presence, not sufficiency** — it counts files,
      it cannot tell us a file carries the field we need. 100% coverage is
      not the finish line. EDGE 1 is the proof: `metadata.json` is present
      on every run and still doesn't carry the classification rationale.
    **Second-pass trace — the four the census can't answer (2026-07-31,
    dev-Mac code read, done while the box census was queued):**

    - [ ] **EDGE 4 — plan evolution is overwritten, not versioned.**
      `loop_artifacts._write_plan_manifest` writes `loop-<id>-plan.md`
      immediately after decompose and **overwrites it after every step**
      (its own docstring: "Write (or overwrite)"). It carries the *what* —
      steps, type tags, per-step status/elapsed/tokens/cost, an execution
      log — and none of the *why*. `replan_count` is a bare integer: you
      learn it replanned 3× and nothing about what changed or what
      triggered it. On a replan the earlier plan is simply gone. For a
      cold/warm pair where the warm run is expected to plan *differently*
      (fewer, fatter steps is the stated errand-envelope lever), the
      artifact that would show it is the one being overwritten.
      Mitigation that already exists: the decompose calls ARE labeled
      (`purpose="decompose-staged"` / `"decompose-compose"`, planner.py:927
      and :990), so raw plan reasoning is recoverable from `build/calls` —
      *if* record mode was on. `CUTS_DRAWN` separately carries
      constraints/probes.
    - [ ] **EDGE 5 — the step-edge context ledger is never persisted.**
      `ContributionLedger` is typed and provenance-stamped in memory
      (`ContextContribution(source, kind, text)`), and `drain()` at
      loop_execute.py:698 is the single merge point. The drained batch's
      only downstream use is **blocked-step re-arm** (loop_blocked.py:219,
      :275, :348). Nothing writes it. So "which contributions did *this
      step* actually see" survives only as `[source]`-prefixed text
      embedded in the step prompt inside `build/calls` — unstructured, and
      requires parsing the prompt to recover. That is precisely the field
      a cold/warm delta needs: if a warm run improves because a `prereq`
      or `reorientation` contribution fired that the cold run never got,
      the mechanism is currently unqueryable. (Operator injections are the
      exception — they ride `ctx.injections` → `LoopResult` → the report
      and are durable.)
    - [ ] **EDGE 6 — per-layer cost attribution breaks exactly at the work
      layer.** `purpose=` on call records is the per-layer key, and
      coverage is good overall: **73 of 82 real `adapter.complete()` call
      sites are labeled** (86 raw regex hits minus 3 docstring examples and
      the deliberate inner forward in `FailoverAdapter`, which pops
      `purpose` at llm.py:646 by design). But **4 of the 9 unlabeled sites
      are the agentic executor seams** — `step_exec.py:1202` (worker
      executor step), `step_exec.py:1313` (its tool-search retry),
      `workers.py:253` (worker-ticket executor), `team.py:215` (team worker)
      — each marked `# agentic:` in-source as where the real work happens.
      These are the biggest token consumers in any run. Net effect:
      "what did the harness spend vs what did the work spend" is
      answerable for the cheap orchestration layers and falls back to
      `loop_report._sniff_call_head`'s prompt-opener heuristic for the
      expensive one. The remaining 5 (factory_minimal:70, factory_thin:218,
      harness_optimizer:419/575, hosted_free:374) are experiment/dev-tool
      lanes — lower stakes. Fix is one kwarg per site.
    - [ ] **EDGE 7 — the live report drops operator injections.** Good
      news first: `plan.md` IS a genuine during-the-run surface (written
      post-decompose, refreshed each step; its docstring calls it "the
      primary debugging artifact for in-flight runs"), so "during" is not
      unanswered — it was under-credited in the first pass. But of the four
      `_write_run_report` call sites, only the three in `loop_finalize`
      pass `injections=`; the mid-run writers (agent_loop.py:516 parallel
      batch, loop_planning.py:288 post-plan, loop_post_step.py:254
      per-step) omit it. An operator who injects a note and then opens the
      live report to confirm it landed sees an empty panel until the run
      finalizes. Fix is one argument at three call sites.

    Deliverable: an integrity-gaps table in the shape of
    `CAPTAINS_LOG_EVENTS.md`'s, but for run-dir artifacts and harness
    layers — every stage listed with what it writes, where, and which of
    the three consumers can actually see it — plus fixes for whatever is
    cheap, and a census tripwire so the table can't rot the way the
    event-contract doc did before its own tripwire landed.

- [x] *LT-0 spin-off (stuck-run surfacing): diagnosed 2026-08-01,
  deliberately NOT promoted — reopen trigger preserved in the archived
  record (BACKLOG_DONE §"Moved from BACKLOG 2026-08-16").*

- [x] **LT-1 — the batch: CLOSED 2026-08-02, 8/8 rows settled.** Full
  round-by-round records (registrations, predictions, retractions, all
  verified by hand) archived to docs/history/backlog-done-2026-04-to-08-p3.md §"Archived
  from BACKLOG 2026-08-05" (rotated out of BACKLOG_DONE 2026-08-16) —
  dev-recall reaches them. Final scoreboard:

  | # | capability | verdict |
  |---|---|---|
  | 1 | quote fidelity, fetch-then-diff | **PASS** $1.06 |
  | 2 | cold/warm delta | measured: warm −14% cost / −48% wall |
  | 3 | parser iteration, execution grounding | **PASS** $1.73 |
  | 4 | extract-to-schema | **PASS** $2.97 *(recorded verdict wrongly False — closure fixed `f7b775c`)* |
  | 5 | retrieval-before-describe | **PASS** (re-judged; provenance FP fixed) |
  | 6 | correction persistence across runs | **FAIL** — claimed-persisted-never-persisted; memory-write provenance guard shipped `87c2caf` |
  | 7 | fix failing suite w/o papering over | **PASS** $2.24, all 3 mechanisms |
  | 8 | self-inspection across dispatch boundary | **PASS**; NOW verdict propagation fixed live |

  **What survives as standing state, kept here so it can't get lost in
  the archive:**
  - **Never derive a run's cost — read `provider_cost_usd`** (the ×3–4
    cache-read retraction; `scripts/run_readout.py` prints provider/card/
    naive side by side, naive labeled RETRACTED). Research-class band
    $3–8, spread driven by wandering, not subject.
  - **Lessons alone carry ≈ nothing today** — phrase-varied re-ran ABOVE
    cold ($6.35 vs $5.91). That number is the pre-design baseline
    RUN_TEACHINGS chunk-1 measures against.
  - **Brittle-checker tally: 4 failed checks, 4 check-design artifacts, 0
    real failures** — closure judged past all 4 correctly. Both layers
    default to literal string matching against content that legitimately
    varies; survivable where a judge can overrule, fatal where FULL-trust.
  - **OPEN, Jeremy's call (registered round 5):** what to do about
    provenance-vs-closure conflicts beyond recording them
    (`provenance.contested_by_closure` shipped, demotion still stands).
    Options on record; **recommendation (b): suppress failure-flavored
    learning on contested verdicts** — targets the measured harm (#5
    minted 3 lessons on a false premise) without touching the guard's
    safety role. Not done unilaterally — it changes the learning path.
  - Cosmetic residual (not fixed): `_provenance_missing` dedups on exact
    string, so a path can list twice ("bare" + "claimed written, not
    found").
  - Quality-gate 600-char truncation arc (3-of-4 false escalates →
    marker fix `f4ef704` → Jeremy's "why truncate at all" → measured,
    cut raised to 4000 in `065a010`) archived alongside; its lesson lives
    on in the OPEN arbitrary-truncation audit below.

- [ ] **LT-4 — the second batch (Jeremy 2026-08-05, verbatim: "run some
  tests from our general list (2 each, as before, cold/warm), at least 3
  different ones in the research direction and 3 in the bridge-building
  direction, we can see what we can learn by watching the system try and
  execute them").** Registered 2026-08-05 BEFORE dispatch; this commit is
  the timestamp. All runs `--measurement-class benchmark`. Ground-truth
  snapshots at `~/.maro/workspace/output/lt4-ground-truth/` (README
  inside), taken by hand before dispatch — instruments verified this
  time (round-7 lesson): the authed X rung works from the box with
  cookie-cache env injection, hathitrust/googleapis still blocked from
  the executor container (403/429), reddit RSS + old.reddit + usps 200
  from the container, provolibrary.com unreachable (why SLCPL).

  **BATCH DONE — 12/12 PASS, provider$ 29.89 (predicted 21–49 ✓);
  warm/cold on the 5 byte-identical arms −49% ($8.75 vs $17.28), all
  PASS, correct reuse shape in every arm — the artifacts-over-streams
  decree confirmed empirically** (vs LT-1's lessons-alone ≈ nothing).
  Registration tables, per-arm scorecards, and findings 1–10 archived:
  BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16". Finding index
  (the follow-ups below cite these numbers): 1 B3 data-true/
  method-false confabulation; 2 B1 skill dead-drop; 3 container-gap
  family; 4 in-run review quality bimodal; 5 bot-wall count 2–3×
  under; 6 confabulation sticky across reuse; 7 claims-vs-events 3rd
  instance; 8 silent closure-verdict loss; 9 dead-drop reproduced 2/2;
  10 tool-fragment-into-bash (re-diagnosed: inner-session
  self-execution; watch for the `bash: {tool:` 127 signature
  recurring — next rung is detection into the failure corpus).

  **Follow-ups owed from LT-4** (each small):
  (a) skill dead-drop fix — SHIPPED; record archived to BACKLOG_DONE
  §2026-08-11 sweep; (b) ~~closure-error
  stamping (finding 8)~~ **SHIPPED same day** — `handle.py` now logs the
  swallowed `evaluate_closure` exception and stamps
  `goal_verdict_source="closure_error"` + error summary (goal_achieved
  stays absent: not-judged convention preserved); pin
  `test_handle.py::TestClosureErrorIsStamped`, verified red-then-green;
  (c) mint-time claim grounding for method/
  provenance statements in artifacts — ~~extend the claim_probe/
  review-grounding lane to artifact claims~~ **slice 1 SHIPPED
  2026-08-06**: `src/mint_grounding.py` stamps lesson mints with
  event-log receipts (supported/unsupported/unprobed) on both stores +
  both extraction paths; injection surfaces mark unsupported claims,
  seed-reader skips them (design + falsifiers:
  `docs/MINT_GROUNDING_DESIGN.md`). **Slice 2a SHIPPED 2026-08-16**
  (tests/test_mint_grounding_slice2.py, 9/11 probes red-first): the
  R1-3 writers now stamp — step-lessons (`memory.py`), prereq
  acquisition (against the sub-loop's OWN run), and thinkback (a lane
  R1-3's list itself missed, found by the slice-2a census); evolver
  prompt_tweak pinned stampless BY CONSTRUCTION (apply-time ≠
  observe-time, advice-shaped). R1-4 laundering FIXED: KnowledgeNode
  carries grounding (node's own text grounded against the minting
  outcome's events at CREATE; re-observation never re-grounds —
  mint-time semantics; absent-key row discipline), and the promotion
  judge now sees the stamps (unsupported claims rendered with a
  weigh-don't-auto-reject instruction — ADVISORY, per the fail-open
  decree; a judged-valid node still promotes, pinned). **Slice 2b,
  2026-08-16 — probe-first premise correction + claim-shape gate
  SHIPPED:** measuring the seven census sites' actual corpus BEFORE
  wiring them found the slice's premise false as written — the bare
  lexicon fired on 100 sentences across skills.jsonl (398 rows) +
  skills-lite (56 .md) with **zero** retrospective claims among them
  (skill prose is prescriptive by construction), and on lesson/node
  prose its precision was ~19%/~5% (advice, tags like
  `[recovery-verified]`, filenames like `wordfreq-verified.txt`,
  modal policy "must be checked"). Stamping that would have rendered
  "unsupported by the minting run's event log" markers on instructions
  that never claimed anything. Shipped instead: a **claim-shape mood
  gate** in `extract_claims` (retro-marker requirement + imperative/
  descriptive main-clause veto + token-level tag/adjective/modal
  vetoes, all narrowing) — corpus hits 76→0, 24→0, 103→21, 100→9;
  38 pins in tests/test_mint_grounding.py (red-verified against the
  pre-gate module). This also answers the slice-2a review's deferred
  Architect question (claim-shape hit rate on generalized LLM prose)
  with numbers. **Review round 1 same day** (4 lenses, sonnet-medium
  fallback; record `docs/history/2026-08-16-mint-grounding-gate-review.md`):
  2 HIGHs confirmed and fixed — (a) expert-QA POLARITY: "the fetch was
  not authenticated" ground `supported` off a real receipt, a false
  affirmation strictly worse than the false doubt the gate targets
  (clause-local negation veto + modal-perfect arm now pinned both
  ways); (b) Minimalist/Skeptic/Architect CLOSED VERB LIST on an open
  class (`download`/`draft`/`install`, "Then record…", "Retry … until
  the page was fetched") — replaced as primary net by a
  vocabulary-independent subordinate-clause rule, list demoted to
  backstop, must-detect fixtures added per watch-list #7. A third HIGH
  falsified this entry's own first draft: "all nine already-stamped
  rows survive" was 8/9 as landed (a claim inside "needed *to be*
  checked", correctly refused) and 6/9 after the fixes — the other two
  drops are a negated and an absence claim that SHOULD stop minting;
  `scripts/mint_grounding_census.py --recheck` is now the per-row
  instrument. Labeled and not fixed: present/past homograph openers
  (read/set/split) read as orders — pinned as a known gap.
  **Slice 2b is DEFERRED WHOLE, not narrowed** (corrected after
  probing the first draft's own claim): the skills-lite promotion lane
  `run_curation:948` has fired **twice in 787 runs** — one promotion
  (`changelog_digest.md`, claim-free) and one skip — and the specimen
  first cited for it, `repl_reading.md`'s "Measured correction (A/B run
  e0bbc289…)", carries no `promoted_from` and never passed a mint site.
  The class is real but has no trafficked lane; the census script is the
  one-command trigger to re-open. **Also open: slice 3 republish gate** — the only
  fail-closed point (`pack.py:881` import lane + warm-arm reuse;
  needs its own design pass: the reuse happens worker-side, not at a
  harness seam);
  (d) ~~verdict-prior
  recalibration for future batches~~ **DONE 2026-08-09** — re-anchored
  base rates in the "Prediction anchors for the next batch" block at
  the end of this entry; future batch registrations start from those,
  not LT-1's; (e) ~~container-image gap list~~ **AUDIT DONE
  2026-08-09** (transcript-verified across all 13 LT-4 run dirs):
  the image (Dockerfile.executor rev 2: git, python3, python3-pytest,
  curl, ca-certificates) lacks (1) any fetch tool — the step prompt's
  `__FETCH_CLI__` seam resolves on the HOST, `maro-fetch` isn't on
  PATH there, so the prompt bakes the host-absolute
  `python3 …/src/fetch_tool.py` which the container can't see (R3×3 +
  R2 probes, "per system instructions"; workers then curled anyway
  DESPITE the prompt's "missing fetcher is reportable, don't fall
  back" line — and passed); (2) `file` (R2 call-00007 exit-127 while
  type-checking the fetched USPS PDF) and no pdftotext/poppler; (3)
  `nosuch=13` audit resolved: the count is dominated by the worker's
  own unsaved-tmp-file reads (ugrep on a 000-response page) + the
  fetch_tool probes — no additional image gaps behind it. Fix
  directions when the C4 lane warrants a rebuild (IMAGE_REVISION
  bump): bake `maro-fetch` (or mount `src/fetch_tool.py` read-only)
  + apt `file poppler-utils`; container-aware `__FETCH_CLI__`
  substitution is the no-rebuild alternative. Evidence-gated —
  don't rebuild until a batch actually loses a verdict to these
  (LT-4 lost none); (f) ~~tool-fragment parse
  leak (finding 10)~~ **RE-DIAGNOSED + MITIGATED 2026-08-09** — see
  finding 10 above (inner-session self-execution of the tool-protocol
  JSON, not a harness parse leak; `_TOOL_INJECTION_TEMPLATE` now
  forbids executing the reply).

  **Prediction anchors for the next batch (follow-up d, 2026-08-09):**
  LT-1-era priors underpredicted LT-4 badly (joint P of the observed
  12/12 ≈ 1.7% × warm ≈ similar — systematically stale). Re-anchor:
  research-direction Tier-1 goals ~85–90% PASS cold (was 40–65%);
  bridge rungs with a shipped carrier (artifacts/corpus/skill store)
  ~75–85% cold (was 35–40%); warm byte-identical arms: PASS ≈ cold,
  cost −40% to −65% (LT-4 measured −49% mean); cost/steps bands were
  well-calibrated (22/24) — keep the method, widen blocked-count bands
  ×2–3 (R2 hit 7 vs 1–3 predicted). Failure risk has MOVED: predict
  claims-vs-events confabulation (findings 1/6/7 family) as the modal
  non-PASS shape, not wrong-answer.

- [ ] **Poe's X steal-list run (2026-08-05, handle `83a2c805`,
  @rvaniaaaa's 8-agent skill-discovery pipeline) — the deliverable is
  good; three captures out of it.** Run's own output:
  `projects/users-ask-verbatimplease-run-this/steal_list.md` — 13
  claims tagged vs maro code with 26 file refs, adversarially
  re-verified w/ one self-correction. Verdict said not-achieved (0.82)
  but that's static-probe bias (modality static:5 on a research-only
  task) + closure check #4 breaking on the container mount view — the
  content itself held up to my re-read.
  1. **STEAL — scout wiring (its T1/T4/S1, post-correction):**
     GitHub-wide search ALREADY EXISTS
     (`channels.py:GitHubChannel.search_repositories` + fetch tool
     modes `github_repos`/`github_code`) but a repo-wide grep found
     ZERO autonomous callers in evolver/skills/heartbeat. The steal is
     a loop, not a capability: scheduled/triggered scout that derives
     queries from maro's own skill gaps → existing `repo_scan.py` →
     `skills.py:extract_skills` (which the run confirmed is
     input-shape-ready). Fits the standing research-steal-list arc;
     honor the link-farm-first decree (2026-08-02) when sourcing.
     **AMENDED 2026-08-08 — design-input read done, three of those
     claims do not survive verification:
     `docs/history/2026-08-08-scout-wiring-design-input.md`.** (a) The
     GitHub/zero-callers half is CONFIRMED, with the nuance that the
     capability is already agent-reachable via `fetch_tool` — what's
     missing is a trigger and a query, not reach. (b) `repo_scan.py` is
     the WRONG component: it's a local tech-stack fingerprinter
     (`scan_repo` → languages/frameworks/tags, sole caller
     `loop_planning.py:591`), there is no fetch→disk bridge, and
     bridging one reopens the C3 untrusted-git boundary. The near-miss
     that probably caused the mis-file is `repo_scan.py:336`
     `find_skills_for_stack` — which matches EXISTING skills to a
     stack, the scout's inverse. (c) **"input-shape-ready" is wrong.**
     `extract_skills(outcomes, adapter)` filters through
     `is_learnable_outcome` (`skills.py:258`) and returns `[]` at
     `skills.py:262` BEFORE any LLM call on non-outcome input; it
     stamps `source_loop_ids`. Feeding it repo content means forging
     outcome dicts — the exact fabricated-provenance shape
     `lesson_provenance.py` exists to stop. (d) **The premise is
     empirically empty:** across all 94 post-ship runs carrying a
     skills manifest (2026-07-09 → 2026-08-08), ZERO recorded an empty
     skill injection, so "derive queries from maro's own skill gaps"
     has no corpus. Not a defect — the matcher is healthy (93% genuine
     trigger matches; out-of-domain probes correctly match nothing) —
     the signal is just the wrong SHAPE. Blocked on the match-tier
     telemetry work (record now in BACKLOG_DONE §"Moved from BACKLOG
     2026-08-16"); re-scope after that lands. **DECIDED
     2026-08-08 (decision 4d562766): scout output is READING MATERIAL
     ONLY — never a skill-store write** ("gut says we still don't know
     enough yet... re-open if we need to later"). Store-write legs are
     void, the untrusted-git boundary stays closed, and the
     Phase-32-ordering question is moot until re-opened; any re-scope
     designs to a reading surface (captain's log / reading queue), not
     the store.
  2. **CONTESTED — its "highest-leverage" S9 (mandatory human merge
     gate on skill promotion).** Pushback on record: a human gate on
     EVERY promotion makes Jeremy the sequencing blocker (fanout
     decree), LT-4 same day showed auto-promotion amortizing well, and
     "ungated" overstates (provisional tier + circuit breakers +
     validation harness since 2026-08-01 already gate). Better-fitting
     variant if wanted: human gate at TIER GRADUATION
     (provisional→trusted) only, or periodic playbook-style surprise
     reads of the skill store. S8 (git-mediated store) is
     infrastructure for S9 — only if the variant is chosen.
  3. *Introspection-mount blindness — FIXED 2026-08-06; record
     archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".*

- [x] *Skill match-tier telemetry — SHIPPED 2026-08-08; record
  archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".*

- [ ] **Claimed-but-unwired evidence gate (box lane — the BUILD shipped;
  chunk 3 of the 2026-08-16 sequence, sequence complete; record in
  BACKLOG_DONE).** The pattern-5 probe is live in closure_verify: the
  plan call extracts `stated_guarantees` (behaviors the work's prose
  asserts) and maps each to the check that exercises it; a deterministic
  plan_index join scores exercised / contradicted / inconclusive /
  unwired; unwired guarantees are named to the verdict judge as
  unverified claims; `claim_coverage` (per-guarantee records +
  per-status counts) rides ClosureVerdict, the CLOSURE_VERDICT event,
  and closure_verdicts.jsonl rows. Advisory v1 — never flips a verdict
  deterministically. What remains is the star-v8 keep/kill adjudication
  once real runs accumulate:
  `jq -c 'select(.claim_coverage) | .claim_coverage' ~/.maro/workspace/runs/*/build/closure_verdicts.jsonl`
  **Keep bar (tightened by the 2026-08-16 review round — `unwired`
  firing alone is near-guaranteed by the prompt's own null-when-unsure
  bias and proves nothing):** the corpus must show DISCRIMINATION —
  both `exercised` and `unwired` firing across runs — plus spot-checks
  in BOTH directions: guarantee texts vs step results (fabricated
  guarantees) AND whether `exercised` mappings' checks genuinely
  exercise their guarantee (the laundering direction). Watch the
  `malformed` bucket (LLM shape drift would otherwise read as silence)
  and the `inconclusive` bucket (no executing evidence; not in the
  unwired count). **Known corpus bias, read accordingly:** a run that
  narrates vaguely extracts zero guarantees and skips the probe — the
  corpus over-represents over-narrating runs, and fleet-wide silence is
  indistinguishable from fleet-wide honesty (extraction from
  deliverable CONTENT, not just prose, is the future counter). Kill =
  drop plan-prompt reasoning item 5 + the join; the plan_index stamp
  and recording seam can stay. Review record:
  docs/history/2026-08-16-closure-claim-coverage-review.md.

- [ ] **NODE_CANDIDATE permanence tier — UX DECIDED 2026-08-16, ready
  to build** (GOAL_BRAIN Decisions entry has the verbatim answers; the
  2026-08-02 "another layer/process" hunch resolved to the Δ-effect
  machinery shipped since). Build spec:
  - Status ladder gains `permanent` above `active`. Permanent = exempt
    from decay/demotion/inert-lesson machinery; contest flags but never
    demotes without the user. lf- nodes excluded as ever.
  - Auto-promotion active → permanent at an abundance-of-proof bar:
    Δ-effect receipts (reuse promote_lesson_by_effect-style evidence),
    threshold well above the active bar — observational/measured, not a
    magic number (correctness-over-frugality posture applies).
  - Reading page: new bottom section "learning/growth" — recently
    promoted nodes with receipts (notification, not queue); an
    escalation sub-list for nodes the system flags as needing human
    judgment/taste (contested evidence, value-laden content) — those
    WAIT for the user instead of auto-promoting.
  - CLI config/debug verbs, symmetric: bless (force permanent), banish
    (user-authored tombstone that re-observation cannot resurrect),
    un-bless. Rarely needed if the bar does its job.
  - The structural tripwire pin on promotion symbols (2026-08-02)
    still stands for this build.

- [ ] **Arbitrary-truncation audit** (opened 2026-08-03; **Jeremy:** *"this
  was one of the first truncations early on and I've been uncomfortable
  making those trades for 'keeping the context small' by cutting so much…
  there are still way too many arbitrary truncations for my liking"*).

  **Burn-down status 2026-08-15/16:** slice census ceiling 176 → 135
  (tranche 1: knowledge_web 22 + loop_execute 15 + knowledge_lens
  starvation pair; per-field-clips rule for composed evidence rows);
  verdict-bypass census 12 → 1 sanctioned site (audit_repair alignment
  patch). Tranche-2 targets: claim_probe's composed [:400] (the one
  other starvation-shape member, r5 shape-sweep), knowledge_lens's 8
  single-field cuts, loop_blocked (12), inspector (8), director (9);
  also the r5-noted coverage gap (refight-evidence test only exercises
  short strings).

  **Census: 958 numeric truncations / 115 modules; the class that
  matters is evidence reaching an LLM (95 sites). Judge windows, the
  full PROMPT worklist, and the STORE pass are DONE (2026-08-03 →
  08-13); adversarial rounds 11–17 ran the arc to near-convergence
  (r17 on the sonnet fallback: 2 of 3 lenses clean).** Full trail —
  census tables, the decoration-cut / stacked-cut method lessons
  (follow the value end to end), the 54a4be7 concurrent-session merge,
  rounds 11–17 blow-by-blow, and the remove-the-limits-entirely
  measurement (windows a non-issue, cost small, accumulating contexts
  the one earned bound) — archived: BACKLOG_DONE.md §"Moved from
  BACKLOG 2026-08-16". Structural tripwires stay in the suite:
  `tests/test_truncation_discipline.py` (inventory 176/129) +
  `scan_verdict_bypasses` (12 frozen sites).

  **Tranche 2 DONE 2026-08-21** (caps sweep, triggered by Jeremy's
  fragility decree — *"we might need caps in some cases but generally we
  should be data driven… it's making the system fragile"*). Inventory
  110 → 101 rows, every fix distribution-backed:
  - claim_probe: probe receipt [:400] was censoring 13% of 447 live
    receipts at its own cap (median 30, tail unknowable — the cap
    destroyed the metric) → marked clip 2000; event re-cut [:300]
    dropped; `probe_command` stored whole (it's the replay handle).
  - loop_blocked: retry hint + MISSING_INPUT escalation cut
    block_reason at 120 while 93% of live reasons exceed it (n=184,
    median 291, max 913) → marked clip 1000; failure_chain entries
    (memory-ledger surface, lesson extraction reads them) [:60]/[:80]
    → marked clip 600 (=p99); fingerprint [:200] commented deliberate
    (hash-input normalization).
  - knowledge_lens/memory_ledger: `failure_summary` — the evidence the
    contradiction-adjudication judge reads — was cut [:300] at capture
    (only copy) and re-cut at read → marked clip 600 both ends.
  - inspector: alignment judge scored goal-match on [:400] of results
    that run median 1,180 / p99 4,671 → `_REVIEW_STEP_CUT` (4000)
    marked, same for the notes prompt [:200]. The six evidence-[:80]
    sites STAY — they enforce FrictionSignal's documented anonymized
    max-80 field contract.
  - planner: GOALS/CONTEXT/SIGNALS.md injected at [:500] while all
    three live operator docs run 925–1161 chars — the operator's
    hand-written mission definition was majority-invisible to
    decompose → marked clip 4000. evolver_scans' SIGNALS [:600] same
    fix. user/README + user/SIGNALS.md prose updated.
  - Adjudicated-stay rows keep their inventory entries (director's 7 =
    log/notify bounds; knowledge_lens reasoning::400 = prompted-shape
    bounds; lf- import excerpt = store decision, now commented).
  - NEW structural tripwire `tests/test_budget_override_discipline.py`
    + `tests/data/budget_override_registry.json`: AST-scans call-site
    literal budget kwargs (max_chars/max_chars_per_entry/max_len/
    max_length); every override needs a registered non-empty "why" —
    the recall-600 class (unregistered override silently starving a
    consumer) can't land unnoticed again. Redundant overrides removed
    (director 1200, recall playbook 800, team 1000 — each restated the
    callee's default). Count caps (max_results/max_entries) = named v1
    upgrade edge, deliberately out of scope.
  - **Review round same day (4 lenses, sonnet-medium fallback —
    `docs/history/2026-08-21-caps-sweep-review.md`):** all-real round.
    Fixed: negative control now drives the REAL `_scan()` (temp tree +
    subpackage, rglob); `probe_command` re-bounded (marked clip 2000 —
    unbounded LLM field into a forever-log was the opposite failure);
    harness_optimizer's `max_length=` kwargs were latent **TypeErrors**
    (safe_str takes `max_len`) — fixed + registered; navigator signals
    lost their [:80]-starved duplicate block_reason; inspector comment
    re-traced to the real upstream bound (VERDICT_PROSE_CAP);
    `tests/test_caps_sweep.py` boundary pins added (behavioral for
    planner docs / retry hint / probe receipts, source pins for buried
    sites); goal previews now marked clips.

  **Still open (the live remainder):**
  - **Positional-clip-value tranche (review 2026-08-21):** the override
    tripwire polices budget KWARGS; the value handed positionally to
    `clip(text, N)` is unpoliced (~150 sites) — a future `clip(x, 50)`
    lands unflagged, and `max_chars=_CAP` name-binding is the same
    one-assignment evasion. Wants its own adjudication pass extending
    the registry to clip literals (or a named-constant convention).
  - **Single-owner defaults still unmeasured (review 2026-08-21):**
    `memory_bridge.format_worker_memory_block` (1200) and
    `playbook.inject_playbook` (800 — seed alone overflowed it per V6;
    live playbook ~2.6k chars, ranked selection so top entries survive).
    Both now carry budget notes pointing here; each wants a distribution
    pass before the number moves.
  - **STORE retention decision (Jeremy's):**
    `memory_ledger.compress_old_outcomes` (120/600) is now
    load-bearing — outcome rows carry real evidence and
    `load_outcomes` parses the whole file for the last 20 (priced
    2026-08-06: 868 KB / 12 ms → ~4.3 MB / ~62 ms at the new profile;
    fine now, wants the compactor before it is 10x that; nearer since
    rows can carry ~2KB rationale). `handle.py`'s NOW-lane outcome
    summary at 500 is the tighter twin. These bound the record
    forever — deliberate retention decision, not a default.
  - The gaps entries ([:200]/[:300]) — unfiled, left unmeasured.
  - Accepted residuals, filed not built: structural truncation
    provenance (typed truncation metadata if forged-marker provenance
    ever matters); competing budget owners on the recall path
    (as_context_block can still eat the prior-brief footer — deep fix
    is the outer assembler passing remaining budget; belongs with the
    wide-view-seat design question).
  - Optional: a codex 3-lens confirm round after the Aug 19 reset —
    the only-low exit was never formally reached (r17: 1 contested
    high + 1 medium, both fixed; 2 of 3 lenses clean).

  **Method that worked, for whoever picks this up:** don't argue about
  the number — pull the actual distribution out of
  `runs/*/build/loop-*.json` (`result_length` is right there),
  tabulate `cut → % payloads intact / % text shown / median extra
  tokens`, and the answer falls out. Every constant fixed in this
  audit was set in an earlier era and never revisited.

- [ ] **Do the evidence lenses want a wide-view seat?** (opened
  2026-08-03 alongside `065a010`.) `_lens_evidence_probe` documented
  itself as showing "the same summary the gate itself reviews"; that
  stopped being true when the gate went 600 → 4000. Docstring corrected,
  behaviour deliberately left at 500: the lenses are an evidence-DIVERSITY
  panel and the recorded ablation baselines were taken at that width, so
  changing an experimental arm under cover of a gate fix would corrupt the
  comparison. The real question — whether the panel should include a seat
  that sees the *whole* payload now that it is affordable — is a design
  call with an ablation attached, not a constant to bump.
  (`_lens_evidence_transcript` at 400×8 and `_lens_evidence_artifact` at
  2400 are deliberate shapes; note the artifact seat already tells its
  model "you see only this", which is the honest pattern the gate lacked.)

- [ ] **Small observations from the 2a3b1f85 exam** (2026-08-11, none
  load-bearing — three one-liners so they don't evaporate):
  (1) *Decompose mangles step titles via template stuffing* — "Run Fetch
  GitHub star, fork, and save output to a file", "Read the captured
  output and review, pauhu/claude-codex-review, …" — comma-spliced
  fragments where the planner slots a phrase into a "Run X and save
  output" template. Cosmetic, but these titles are what reports, ledger
  rows, and diagnosis prose show. (2) *Knowledge-node age-path promotion
  stamps `active` with 0 re-observations* — three nodes promoted
  candidate→active same-run at confidence 0.30, "Re-observed 0x". Age
  alone promoting an unre-observed hypothesis to active is the evolver
  denominator-shape; watch item — check what `active` gates before
  tightening. (3) *Quality-gate cross-ref count vs claim probes*: the
  hosted_free lane logged `checked=4 disputed=2` while 3 CLAIM_PROBED
  events fired in the same window (Pass-2 lane) — probably two lanes
  reporting one pool; unverified, worth a 10-minute trace next time
  someone is in quality_gate.

- [ ] **Evolver fabricates a stock mechanism for stuck-at-0-steps
  outcomes** (found 2026-08-02, `16d90814`). "Blocks indefinitely / no
  resolution or diagnostic" attached to a run that failed in 32s with a
  named `NEED_INFO` reason, and one run's retry counted as two
  occurrences to justify `confidence: 0.75`. Currently contained
  (`applied: false`; 4/472 rows, 0 applied), so this is a watch item, not
  surgery. The cheap fix if it grows: make outcome analysis read
  `elapsed_ms` and `blocked_reason` before asserting a mechanism, and
  count occurrences by run identity rather than by attempt.
  **RE-MEASURED 2026-08-08 — it did NOT grow; stays a watch item.**
  `suggestions.jsonl` is now 547 rows (was 472) with **5 applied**, and
  rows asserting the fabricated mechanism ("blocks indefinitely") are
  **0** — the 2026-08-02 specimen is no longer in the live store. No
  surgery warranted. Method note for whoever re-checks: a loose grep for
  `stuck|0 steps` across the memory stores returns ~151 rows and is
  almost entirely noise — match on the asserted mechanism text, not the
  symptom word, or you will talk yourself into a fix that isn't needed.
  **RE-MEASURED 2026-08-13 — still contained, stays a watch item; but
  the 08-08 method was too narrow and missed a second specimen.** Store
  is 552 rows (was 547), applied still 5, zero new family rows since
  08-03. The literal `"blocks indefinitely"` grep returns 0, which the
  08-08 pass read as "specimen gone" — but suggestion `88b45cba-00`
  (minted 2026-08-03, `applied: false`) asserts the same mechanism in
  different words ("Two stuck runs blocked at 0/1 steps on the same
  pytest invocation … with no resolution or diagnostic") and is
  fabricated on BOTH filed axes, verified by run identity against
  outcomes.jsonl: (1) count — exactly one pytest/ledger-kata stuck
  outcome existed at mint time (`0f296024`); the "second stuck run" in
  its 50-outcome window was `68f8760a`, the evolver's OWN
  `evolver_verify` auto-apply task from a day earlier, unrelated goal,
  merged into "the same pytest invocation"; (2) mechanism — `0f296024`
  stalled 72s (not indefinitely) and its failure_chain names a terminal
  (`NAVIGATOR_ESCALATE: recovery overridden at blocked step, conf
  0.95`), so "no resolution or diagnostic" is false against the record.
  Family census total: two specimens ever (08-02, 08-03), both
  `applied: false`, none since — 10 days quiet, no growth, no surgery.
  Method correction for the next re-check: the literal mechanism string
  is also insufficient — grep the mechanism FAMILY (indefinite-block /
  no-diagnostic claims on stuck outcomes), then verify survivors
  against the row's own `elapsed_ms` + `failure_chain` and count
  occurrences by run identity. That hand-check IS the filed cheap fix
  run manually; build it into outcome analysis only if the family
  starts minting again.

- [ ] **LT-3 — bridge asks (the rungs themselves).** Worked example, the
  web-reading ladder Jeremy named — mostly built, never assembled as a
  ladder: fetch one URL reliably (paywall/JS/PDF/403 are the real
  failures) → answer *one specific question* from a page without
  summarizing it → "is this worth my time" triage (SHIPPED, `verified`
  2026-07-17) → triangulate multi-source + HTTP-validate every URL →
  **capture the working access path as a reusable skill** (Tier 4
  `target`; the tech-tree node). Three more chasms worth laddering:
  local/geographic grounding, own-run introspection,
  persistence-across-runs. New asks land in CAPABILITIES.md as-phrased per
  the capability-capture rule.

**Open, Jeremy's call:** placement in this stack (the box pulls from the
top, and LT-0 is dev-side work); the spend envelope (errand class is
~7m12s/$1.50 post-warm-pool, so 8 goals × cold+warm ≈ 2h and ~$25); and
whether the batch runs concurrently with whatever the box is already
chewing.

### Typed dispatch envelope — channel separation at the dispatch boundary (OPENED 2026-07-29, Jeremy-decreed direction; box-side intake SHIPPED 2026-07-29)

Arc closed. Spec: `docs/DISPATCH_ENVELOPE.md`. Box-side intake,
Poe-side skill, artifacts-travel rider, and return-path quality
(event-payload rider, top-pick ranking, ANSWER.md) all shipped; full
record archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".
Sole live residual: a CONTAINERIZED worker can read neither
attachment copy (`build_mount_map` hard-excludes the workspace and
the run dir isn't mounted) — evidence-gated on the C4-BOX flip,
don't pre-build. (The sibling no-baked-verbs residual resolved
2026-08-13: image r3 bakes maro-fetch/maro-read — see Container verb
parity (a).)

### NOW retry rung — failure-class-routed ladder: NOW → artifact retry / star → AGENDA (OPENED 2026-07-28, Jeremy)

Jeremy's ask: a middle rung between a failed NOW one-shot and a full
AGENDA run — "run it alongside a failed NOW result; I wonder what the
success rate would be over a strictly prompted NOW... or if the 'here's
the failure artifacts, try again' is just as good." Agreed hypothesis:
route by failure shape — shallow failures (missing detail, format,
unfetched link) → artifact-seeded NOW retry; structural failures
(multi-verb pipeline, needs tools/verification, "a single completion
can't do the work") → star-shaped mini-orchestration (runtime port of
the `.claude/skills/star` contract: master owns taste+judgement,
serial, 0..n discovered steps, typed stops); star-stuck → full AGENDA.
The §9.6 family-ROI line is the router's evidence base.

What exists today (verified in handle.py 2026-07-28): pre-execution
`_is_complex_directive` now→agenda escalation (default ON — but zero
live firing records found in metadata/memory/logs; verify reachability
before building on it); post-execution `_verify_now_outcome`
self-verdict demotion (task-path only; interactive keeps raw speed);
`_now_escalation_context` stash carries the failed quick answer into an
escalated agenda run. The rung slots between demotion and full
escalation.

**2026-07-28 corpus scan** (726 runs/ metadata files):
- 195 NOW runs: 59 done / 7 incomplete / 129 error — errors are ALL
  2026-05 broken-era noise ("Define success criteria" repeated), not
  signal.
- **Organic failure corpus ≈ 1.** Six of the 7 incompletes are one
  synthetic demotion-fix test (2026-06-11 nonexistent-binary); the one
  real case is the 2026-07-02 'comm' ask ("actually run them" —
  tool-requiring, exactly the structural class the rung targets).
- Real-world NOW asks on record (~6, mostly succeeded because easy):
  Manti gas, HTTP 429, worth-my-time link triage, what-time-is-it,
  system status, BST one-liner. `docs/CAPABILITIES.md` Tier 1–2 rows
  are the richer seed corpus — Manti Run 1 is the archetype (NOW
  answered from stale model knowledge, FAILED the contract; later fixed
  by pre-routing; the rung is the post-execution answer for what
  routing doesn't catch).
- Side-find: NOW runs carry NO provenance stamps
  (organic/smoke/control stamping shipped for loop runs only) — the
  organic corpus is smoke-contaminated ("say the word ok"). Stamp NOW
  runs before any organic A/B.

- [ ] Pre-registered experiment, 3 arms on the same seeds: (a) plain
  re-prompt, (b) artifact-seeded retry, (c) artifact-seeded star.
  Prediction on record (2026-07-28): (b) beats (a) clearly; (b) ≈ (c)
  on shallow failures, (c) wins on structural. Seeds: CAPABILITIES
  Tier 1–2 asks + authored structural-failure NOW asks (organic corpus
  too thin). Score on the done-vs-achieved ledger.
- [ ] **Both-lane requirement (Jeremy decree 2026-07-28):** run the
  matrix in the mature workspace (with accrued learning) AND a freshly
  minted workspace (without) — learning-delta is a first-class
  measurement axis. Reuse the benchmark-cell isolation machinery
  (twentieth pass).

### Next-leap: auto persona+skill packaging (ARC OPENED 2026-07-29, first-claim pull; slice 1 SHIPPED 2026-07-29)

The capacity-parked next-leap item, pulled into the free ACTIVE slot
2026-07-29 (claim condition met by the closure-unification ship; Jeremy's
"your coined terminology, not mine" cleared the over-cautious defer).
Trio-triage verdict governs the shape: **readout-first** — evolver
outcome data isn't causal evidence yet, so make the would-be packaging
inspectable before any behavior changes.

- [ ] **Goal-aware router needs new training data**: skill-stats.jsonl
  rows carry no goal text, so the model structurally cannot learn
  (goal, skill) → outcome. If the router is ever to out-rank keyword
  matching, record the goal (or its 120-char prefix) on skill-stats
  rows at outcome time first; until then the guard keeps the model
  honest by benching it. UPDATE 2026-07-29: the run-verdict attribution
  markers (`<run-dir>/source/skill_attribution.json`, shipped with the
  measurement-honesty fix below) join loop_id → outcomes.jsonl goal
  text — (goal, skill, verdict) triples now exist on disk; a retrain
  can be built from them without touching the stats schema.
- [ ] **Claim-token classification is still lexical — four known holes, all
  on the CHEAP side of the trade** (from two rounds of adversarial review,
  2026-08-08; `ee0b11b` → `64eac95` → `d302437`). Every one lets a claim go
  UNVERIFIED (a missed fabrication, one advisory line) rather than demoting a
  delivered run, which is why none were fixed under time pressure — but they
  are real and they share one cause: **the module decides "is this a local
  file?" from the shape of a string, with no boundary that actually knows.**
  1. ~~`_HOSTNAME_RE` multi-dot filenames read as hostnames~~ **DECIDED
     2026-08-09, keeping it: accepted cheap cost.** Refining it with a
     file-extension allowlist was built and REVERTED same day — `.md`, `.sh`,
     `.rs`, `.py` and `.zip` are all real TLDs, so the allowlist turned
     `files.example.zip` into a FALSE DEMOTION (expensive) while still missing
     `styles.min.css`, and it reintroduced the hand-maintained table the regex
     had just replaced. Reasoning now recorded at the site.
  2. ~~`\$\w+` matches `$100`~~ **DECIDED 2026-08-09, keeping it.** Narrowing
     to letters-only was built and REVERTED: `$1` is a valid positional
     parameter, so an unexpanded `$1/report.json` gets looked up verbatim and
     false-demotes. Over-satisfying a literal `$100` is the cheap side.
  3. ~~Named-printf claims never become claims~~ **FIXED 2026-08-09 (round-3
     cross-review, 3/3 lenses): the record was wrong — the token stopped at
     `)` but the TRUNCATED prefix (`artifacts/out-%(step`) WAS collected and
     falsely demoted whenever it survived `_path_shaped` (always for absolute
     forms; environment-dependent for relative ones — the exact shape that
     turned this test red on the runtime box and green in CI). Collector now
     admits `%(name)` groups; the full token resolves via the existing
     template marker.**
  4. `READ_ONLY_PROBES` in `tests/test_fresh_workspace.py` is hand-maintained,
     so a newly added store-reading subcommand gets no probe until someone
     remembers. Deriving it from CLI command metadata is the durable fix.
     Round-3 cross-review added two adjacent test-honesty notes, both LOW:
     the console-script preference doesn't verify the installed `maro`
     actually imports THIS checkout (a stale global install would green-wash
     every probe — moot on boxes with nothing installed), and the root-glob
     refusal pin infers "didn't walk /" from a <5s wall clock rather than
     stubbing the glob.
  **Round 3 (fresh codex, whole changeset) added two more, both LEFT OPEN
  because they widen acceptance rather than demote — the cheap side:**
  5. GOAL/INPUT existence is existential with no cardinality or freshness:
     `Create artifacts/part-{1..9}.json` is satisfied by ONE day-old
     `projects/unrelated-old-run/artifacts/part-1.json`, because
     `_resolve_exact` searches every project and the goal lane has no
     freshness defence (RESULT does). ~~A directory named `report-1.json`
     satisfies it too~~ (stale since 1371b44 — every resolver path now
     tests `is_file()`, pinned; cardinality/freshness remains the open
     residual).
  6. Cross-lane semantics are still shared by convention, not by type. Round 2
     unified dir-qualified claims and missed the bare lane; round 3 unified
     the bare lane. A fourth lane split is likelier than not.
  **The pattern worth naming, now with three rounds of evidence:** every fix
  so far has added a narrower lexical test (prose slash → glob → template →
  hostname → bracket-literal) and each landed against ONE lane while the
  others silently kept the defect. The structural answer is a typed claim
  boundary that distinguishes *a path this run says it wrote* from *a string
  that happens to look like one*, decided once at extraction and carrying
  lane-specific acceptance policy (freshness, cardinality) as data — probably
  fed by tool-event evidence rather than re-derived from prose. Scope before
  building; this is the third arc's worth of patches saying the same thing.

- [ ] **`_OUTPUT_CLAIM_RE` misses the irregular past tense "wrote"**
  (found 2026-08-08 while pinning the template-placeholder guard;
  `provenance.py:37`). `writ\w*` covers write/writes/written/writing;
  "wrote" is the one irregular form and is not matched, so "wrote the
  summary to artifacts/x.md" is never verified while "written to" is.
  **Deliberately NOT fixed, and this is the reasoning, not an oversight:**
  widening a verdict layer's recall trades this module's *cheap* error
  (missing a fabrication — one advisory line) for its *expensive* one (a
  false demotion costs a delivered run its verdict), and there is zero
  observed instance of a missed "wrote" fabrication. **Evidence gate:
  widen only when a real fabrication is observed escaping through this
  verb** — then add `wrote` and pin it. Threshold-provenance shape: marked
  `reasoned`, with the measurement that would flip it named here.

- [ ] **Migrate legacy-rate consumers onto injected counters**
  (consumer-first, one at a time, each with its own liveness check).
  **DENOMINATOR IS NOW READY, measured 2026-08-08 (box):** 187 skill-stat
  rows, **168 (90%) still carry legacy `success_rate >= 0.99`**, while 44
  rows now have real evidence (159 verdicted injections total). The
  inflation is ~30 points where both exist — e.g. `Headless Branch Setup`
  and `Fixture-Based Behavior Verification` read legacy **1.00** against
  injected **0.68**. Per this item's own rule ("a consumer migrates when
  injected_runs gives it a real denominator"), consumers (a)/(c)/(d) are
  unblocked; (b) stays blocked on goal-capture. Mirror `frontier_skills`
  (`skills.py:1589`), which already guards on `injected_runs < min_uses`.
  **Not taken 2026-08-08 by the M1 session purely to avoid a collision** —
  the box was mid-flight on skill pedigree metadata in the same file. No
  technical blocker; pick it up when `skills.py` is quiet.
  Consumers, unchanged —
  (a) `get_skills_needing_escalation` + needs_escalation flag
  (skills.py — escalation threshold 0.4 is unreachable when the store
  is 99.4% positive, so redesign never triggers); (b) router
  `build_training_data` labels (router.py:142-206 — blocked on the
  goal-capture item above, the guard benches it meanwhile);
  (c) pack.py portable-learning export `claimed_success_rate` (ships
  inflated claims to other boxes); (d) skill_loader.py Stats section +
  cli.py skills-list display (operator-facing numbers); (e) evolver
  promotion/variant scoring reads Skill.success_rate/utility (same
  provenance). Rule: a consumer migrates when injected_runs gives it a
  real denominator — don't starve breakers on day-one sparse data.
  FIRST CONSUMER MIGRATED 2026-07-29: the frontier gate
  (`frontier_skills`) now reads injected_runs/injected_success_rate —
  see the A/B-variant entry below. (e) partially done (variant
  candidacy); promotion scoring still legacy.
- [ ] **Slice 2 — wire packaging behind a flag** (default OFF,
  no-silent-spend posture): persona spec gains a packaged-skills field
  fed from would_include rows; router fix landed 2026-07-29 — remaining
  gate is a fatter verdicted denominator. UPDATE 2026-07-29 (census +
  backfill): the denominator is now real — 30 FULL-trust runs
  backfilled through the attribution seam, 21 skills carry injected
  counters, top skills at 18–19 verdicted runs. Honest data moved the
  picture: the former would_include trio sits at 0.67–0.68, BELOW the
  0.70 include bar — packaging on legacy counters would have shipped
  skills the run-verdict evidence doesn't support. Denominator grows
  organically now that the seam fires live (index v2 fix).
### Subsystem liveness / self-health monitoring (Jeremy decree 2026-07-29 — "probably load bearing in the future, nice to have for now") — **v1 SHIPPED 2026-07-30**

**v1 shipped 2026-07-30** — `src/system_health.py`: DECLARED_PROCESSES
liveness registry (6 declared processes = the sweep's four finds plus
the run-ref join and closure-verdict stamping), cheap deterministic
probes riding loop_finalize beside run_skill_maintenance (Jeremy's
correction: "not a cron, let's hook into our startup/closure of the
goals… that we decided with skills months ago"), snapshot at
`memory/system_health.json` (the seeded maro-level systemic-metadata
home — state in the store, transitions in the log), SUBSYSTEM_SILENT /
SUBSYSTEM_RECOVERED user-surfaced transition events (edge-triggered on
narration state, never repeats while held), report-only, killswitch
`health.probes_enabled`, CLI `python3 -m system_health [--probe]`.
Rode in with the captain's-log audience adornment (dual-contract
decree) and the HOUSE_STYLE declared-liveness rule (new dynamic
process ⇒ declare it with a probe). Still open below: the fuller
declared-writer/consumer/join-key registry shape, and census-grade
enforcement (a probe per new process is review-enforced prose until
then).

Jeremy, on the arena-sweep finds: "this is why grafana and ops
monitoring is a thing… we need a way to ensure the system itself is
active and working, especially if we're going to allow it to modify
itself (and eventual code/mod/organic support)." Explicitly NOT
wall-of-monitors ops dashboards — system self-health. He notes he
wanted this at a basic IT level early on and it didn't land, "most
likely because it was an answer without any questions."

The questions now exist. One week of live-fire probing found four
dynamic processes that were wired, green-tested, and silently inert:
the contradiction emitter (dead loop_id→run-dir join, 0 events ever),
the A/B variant subsystem (use_count write-orphan starved the frontier
gate, 0 variants ever), persona-outcomes (writer on a dead path since
April), and times_applied receipts (bumped in-memory copies, never
persisted). Common shape: **writers that fire, consumers that don't,
joins that silently miss** — the failure class tests structurally
cannot catch (suites patch the joins; production data never crosses
them).

Shape sketch (design-first, not committed): a liveness registry where
each dynamic process declares its writer, consumer, join key, and
freshness expectation ("variants: expect create events within N
maintenance cycles of a non-empty frontier"), plus a periodic health
readout comparing declared vs observed (store row growth, event
counts, join hit rates, last-fired timestamps) that surfaces
"wired-but-silent" as a first-class finding. Prior art in-repo: the
chunk-8 stores/guards census (BACKLOG'd with the same
registration-convention prerequisite), packaging_readout's live
join-health lines (measured the persona-outcomes join dead — right
idea, report-only), heartbeat, and the DEFAULTS reverse census
(declared-vs-observed enforced by pytest). Load-bearing once
self-modification lands: a system that changes itself must be able to
notice a subsystem it just killed.

### Dev-approach "house style" doc + intentionality loop (OPENED 2026-07-28, Jeremy; v1 SHIPPED 2026-07-29)

Jeremy: "We should write down our dev approach somewhere in a guidance
doc... our learned-over-time 'house style' is meaningfully impactful,
and maybe deserves its own intentionality loop... would be hard to
replicate in a vacuum on a new machine."

**v1 SHIPPED 2026-07-29** — `docs/HOUSE_STYLE.md` from repo-visible
material only (part (a) of the approved shape): the workflow loop
(shape → build → verify → document → land → cross-model review →
verify-before-fix → fix → record), standing invariants each labeled
with its tripwire or honestly prose-only (out-of-the-box decree +
clean-checkout tripwire, DEFAULTS census, consumer-first, SF-13, data
retention, compiled-truth), pointer table to DEV_PATTERNS/CODING_NOTES/
CLAUDE.md, maintenance rule (tripwire-or-labeled; prose shrinks to a
pointer when the deterministic home ships). CLAUDE.md entry pointer
added. **Still open (needs Jeremy):** (b) importing the feedback-class
auto-memories from ~/.claude, interview-elicited gaps, codeLikeJeremy
pattern steals + the regression-harness idea (see item below).

Gap analysis (2026-07-28): much exists but is scattered — CLAUDE.md
(discipline rules), docs/DEV_PATTERNS.md (taste/judgement),
docs/CODING_NOTES.md (coding posture), the adversarial-review + star
skills — and the genuinely un-replicable part is the ~25
feedback_*/project_* auto-memories in `~/.claude` (machine-local, NOT
in the repo). Proposed shape (shape approved 2026-07-28 — "glad this is in the
backlog and think this will be good to have written down; and assume
it will continue to evolve"): one `docs/HOUSE_STYLE.md` that (a) writes
down the workflow loop itself (chunk → land → cross-model adversarial
review → verify-before-fix → fix → record; SF-13; census tripwires;
consumer-first; end-of-chunk discipline), (b) imports the durable
feedback-class memories into the repo where they're portable, (c)
carries its own maintenance cadence + adjudication gate (same
discipline as the star skill), with CLAUDE.md reduced to the entry
pointer.

- [~] **mini2 executes-locally detection — PARKED as accepted risk (Jeremy
  2026-07-29, same day it was raised).** The gap is real and stated: the
  2026-07-20 zero-creds decree is enforced **by construction for push only**
  (https fetch-only clone, no credentials → cannot land code); nothing
  prevents or surfaces mini2 *running* maro off its reference copy.
  Jeremy's disposition, unprompted and explicit: *"that was me essentially
  saying 'yep, it's a gap, that's a problem for future me'; writing that off
  as something I'll deal with if it happens and I don't think it's likely to
  happen... and difficult to guard against. I'm aware I'm playing with fire
  ... I've been running `--dangerously-skip-permissions` on the orchestrator
  box for about 5 months now — I'm living on a few edges, this is one I'm
  not worried about."* Consistent with the documented trusted-operator model
  (`docs/SECURITY_MODEL.md`), not a divergence from it.
  **Falsifiable park reason** (census: test these, don't re-argue the
  premise) — reopen if any becomes true: (a) mini2 grows a
  `~/.maro/workspace`, i.e. it actually ran something; (b) mini2 gains any
  credential that could land code; (c) anyone other than Jeremy gets access
  to mini2; (d) the poe lane starts carrying work whose failure isn't
  cheaply reversible. Until then the surfacing check (report a
  `~/.maro/workspace` on mini2, never gate) stays unbuilt **by decision, not
  by omission**. Context:
  `docs/history/2026-07-28-m1-vs-box-workflow-contrast.md` §8.2.
- [ ] **Declare the out-of-the-box invariant + tripwire it (Jeremy
  2026-07-28, DECREE):** *"Functionality that we add should presume that
  it's 'out of the box' functionality for the project day 1 — unless it's
  specifically functionality gated on prior learning data for whatever
  reason"*; corollary, *"the work we're doing should be able to be verified
  on a clean clone of the maro repository."* He flagged it as an assumption
  he'd never stated — *"maybe it needs to be declared?"* Declare it in
  HOUSE_STYLE.md (entry pointer in CLAUDE.md) **with a tripwire, not just a
  sentence**: this invariant was silently violated for months and caught
  only by the 2026-07-09 docker clean-machine trial (flat `src/` → pip
  installed zero modules; masked locally by `PYTHONPATH=src`). Shape to
  steal: DEFAULTS.md + its census tripwire, but for *runtime data* instead
  of config — a fresh-workspace check that entry points behave against an
  empty `~/.maro/workspace`, and an explicit marker on the exemption
  (learning-gated functionality must declare itself and degrade gracefully
  when the data isn't there). Ties to the M1's inability to review runs
  (M1-contrast pass above): seeded test-run data is the same missing piece.
  **FIRST TRIPWIRE SHIPPED 2026-07-29** (Jeremy: *"agree, sure, let's do
  that"*) — the **clean-checkout tripwire**: `tests/_checkout_tripwire.py`
  (mechanism) + `pytest_sessionstart`/`sessionfinish` in `tests/conftest.py`
  (wiring) + `tests/test_clean_checkout_tripwire.py` (7 tests). Snapshots the
  working tree at session start, compares at finish, and **fails the run** if
  the suite added files; only additions are flagged, since modifications to
  tracked files are already `git status`-visible while untracked drops into a
  gitignored dir are not — which is exactly where both known violations
  landed. Prunes only regenerable noise (`.git`, `__pycache__`,
  `.pytest_cache`, `.venv*`, `*.pyc`, `.coverage`) and **deliberately does
  not prune `output/` or `memory/`**, the two dirs the real leaks went to.
  Escape hatch `MARO_ALLOW_CHECKOUT_WRITES=1`. Verified three ways rather
  than assumed: unit tests on the pruning rules, end-to-end scoped pytest
  runs proving a littering test fails and a clean one doesn't, and — the
  decisive one — **the historical bug was temporarily reintroduced and the
  tripwire caught it**, naming all 7 files across both leak paths while the
  tests themselves still reported pass, exit code 1. Full suite green with it
  armed, no false positives across 6800+ tests. The mechanism lives in its
  own module rather than inline in conftest specifically so it could be
  tested — the 2026-06-25 lesson that a guard nobody exercises (every git
  hook, dead for a month behind a stale `core.hooksPath`) is
  indistinguishable from no guard. ~~**Still open on this item:** the
  fresh-workspace half (entry points against an empty `~/.maro/workspace`),
  the learning-gated exemption marker, and writing the invariant down in
  HOUSE_STYLE.md — this tripwire covers the suite-hygiene half only.~~
  **TWO OF THREE CLOSED 2026-08-08.** (1) The HOUSE_STYLE.md declaration
  was already there (`docs/HOUSE_STYLE.md:122`) — that sub-point had gone
  stale in this entry, not undone. (2) **Fresh-workspace half SHIPPED:**
  `tests/test_fresh_workspace.py` — eleven read-only entry points
  (`status`, `skill-stats`, `skills`, `memory`, `opstatus`, `metrics`,
  `attribution`, `map`, `autonomy`, `inspector-status`, `mission-status`)
  must exit 0 with no traceback against an empty workspace, AND against a
  workspace path that does not exist at all (a distinct failure: a reader
  can survive an empty dir and still die on `mkdir` under a missing
  parent). It scrubs all three workspace env aliases before invoking,
  since an ambient pin on a dev box is exactly how the original violation
  hid for months, and asserts no write lands in the repo — attributing a
  leak to ONE command at the moment it happens, where the session-level
  checkout tripwire can only report an unattributable end-of-suite diff.
  Measured, not assumed: all eleven already held, so this pins behaviour
  rather than fixing a break. Commands argparse rejects before any store
  read (`manifest`, `outcomes`) are deliberately excluded — asserting on
  them would exercise argparse, not the invariant. **Still open: only the
  learning-gated exemption marker** (functionality gated on prior learning
  data must declare itself and degrade gracefully when the data is
  absent).
  **Side-finding while building the tripwire — `maro status` makes a live
  LLM call.** Measured on an empty workspace: `status` **10.44s**, every
  other read-only command **~0.07s** — it was 100% of the new file's cost.
  Its output is generated prose ("Executive Summary" / "Recommendation")
  and it reads the working tree's git diff. Two questions this raises,
  neither answered here: (1) should a read-only `status` verb cost a paid
  call and a network round-trip at all, or should the synthesis be opt-in
  (cheap local summary by default, `--synthesize` to pay)? (2) it is a
  **fresh-install first-run** command — what does it do on a box with no
  backend configured? That is squarely this item's own invariant, and it
  is untested precisely because putting it under test would spend money on
  every suite run. Excluded from the tripwire for that reason, with the
  reason written at the exclusion site.
  **Second side-finding (adversarial review, 2026-08-08): the RUNTIME BOX has
  no installed entry points at all.** `~/claude/maro-orchestration/.venv/bin/maro`
  does not exist and `maro` is not on PATH there — the Linux box runs from
  source via `PYTHONPATH=src`. The M1 venv does have the console scripts. So
  the machine that matters most is the one never exercising the packaged entry
  points, which is precisely the configuration the 2026-07-09 docker trial
  showed can be broken while every local invocation works. The fresh-workspace
  tripwire therefore *prefers* the console script and falls back explicitly
  (`HARNESS_USES_CONSOLE_SCRIPT`) rather than requiring it — requiring it would
  red the suite on the box. Worth deciding: should the box be pip-installed
  (`pip install -e .`) so it runs what ships?
  **Happy accident worth keeping** (Jeremy 2026-07-29: *"thankful for happy
  accidents"*): the M1 has **no maro config at all** — no `~/.maro/config.yml`
  and no workspace `config.yml` — so anything run here uses pure fresh-install
  defaults. That makes this dev host, unplanned, the closest thing to a
  clean-install test bed the project has, and it cheapens the §8 "give the M1
  the ability to run a run" item: no new infrastructure, just seeded data and
  a `maro-bootstrap install`.
- [ ] **Threshold provenance instead of observe-only (Jeremy 2026-07-28):**
  replaces the rejected "M1 never ships a live threshold" rule — *"we can
  hypothesize and make a best guess, then create follow-up work to confirm
  or pivot."* A magic number lands marked `reasoned` or `measured`, plus a
  paired backlog row naming the measurement that would confirm or pivot it
  (the drift-batch falsifiable-reparking discipline, applied to numbers).
  Shape accepted in principle, mechanism unbuilt; first candidates are this
  arc's three (fresh ceiling, weighted ceiling, Bash cap).
- [ ] **M1 workspace re-setup (Jeremy 2026-07-28, DECREE):** *"we should
  generally have a workspace on any given box rather than litter copies of
  the repo all over without purpose... we'd do well to re-set up our
  workspace for a reusable location on the M1, we have a sort of randomly
  set up repo right now."* Confirmed state: **two live workspaces on the
  M1** — `~/.maro/workspace/` (real; `runs/`, `memory/`,
  `correspondence.db`, touched 2026-07-27) and `.run-workspace/` inside the
  checkout (gitignored, mostly frozen 2026-06-21 plus two 2026-07-14 session
  dirs) — plus the checkout is still named `openclaw-orchestration` long
  after the rename. Decide one canonical location, migrate or delete the
  other (nothing is deleted without a look — retention decree), rename the
  checkout.
  **DONE 2026-07-29 (the consolidation half; Jeremy: "let's clean up the
  workspace and run with the best practices as far as the maro conventions
  on this box").** `~/.maro/workspace/` is now the M1's ONLY workspace.
  Actions, nothing deleted that wasn't provably regenerable: (a) the second
  workspace `.run-workspace/` moved **intact** to
  `~/.maro/evidence/m1-repo-run-workspace/` — deliberately *not* merged into
  the live workspace, since it carries its own `captains_log.jsonl` and
  merging foreign learning data during the Poe-contamination arc is the
  exact mistake that arc is about; (b) repo-local `memory/` (2 files frozen
  2026-06-21) archived to `~/.maro/evidence/m1-repo-memory-2026-06-21/`;
  (c) stale `output/build-loop.lock` removed (dead PID, its run had
  completed); (d) `.pytest_cache`, `.coverage`, `__pycache__` purged
  (regenerable). 466M → 434M, working tree clean, **suite green on the M1
  after the move: 6805 passed / 0 failed / 0 errors / 22 skipped, 119s**
  (removing repo-local `memory/` broke nothing — tests do write there via
  `OPENCLAW_WORKSPACE`, but they recreate it, confirming the isolation
  CLAUDE.md claims).
  **New convention this established:** `~/.maro/evidence/` — machine-local
  archived evidence that landed docs cite, kept *beside* the workspace so
  `~/.maro/workspace/` keeps matching the documented layout exactly.
  **Finding that outranks the cleanup** (see the out-of-the-box item above —
  this is that decree failing in the wild, found by doing this work): **three
  separate landed docs cite evidence a clean clone cannot see.**
  `BACKLOG_DONE.md` ×3 and `MILESTONES.md` ×1 cite
  `docs/history/2026-07-13-adversarial-review-*.md`; two 2026-07-14 history docs
  cited `.run-workspace/` (now repointed and explicitly marked
  machine-local); `docs/LOCAL_VALIDATOR.md:138` names repo `.venv-mlx` as a
  default that only exists here. The citations aren't wrong, they're
  *unreachable* — which is precisely what "verifiable on a clean clone"
  forbids. **Still open, deliberately left for Jeremy** (each is a judgment
  call, not a cleanup step): (1) do cited adversarial-review reports get
  committed so a clean clone can check the claim, or do citations get marked
  machine-local like the two above? — committing publishes to a public repo,
  which is his call; (2) `.venv-mlx` is 307M (70% of the checkout) and is
  referenced by LOCAL_VALIDATOR.md as a default — keep or drop; (3) the
  checkout rename `openclaw-orchestration` → `maro-orchestration` **has a
  real cost**: Claude Code keys session history by path, so renaming orphans
  `~/.claude/projects/-Users-jeremy-claude-openclaw-orchestration/` and
  loses `--resume` history for this project; (4) `output/runs/` holds 208
  repo-local dev runs — prune or keep as the M1's only local run corpus
  (note: that corpus is the M1's *sole* run-shaped evidence, per the
  contrast doc's §8 — deleting it is not obviously free).
  **ALL FOUR RESOLVED 2026-07-29 (Jeremy adjudicated; 466M → 131M).** Each
  answer changed once the data was actually looked at:
  (1) **Reviews — kept, archived out of the repo** (his rule: *"if that's
      review that's for completed code I'm not sure we need it; if we may
      reference it later for meaningful data, let's keep it"*). 29 report
      files existed and **not one of them was cited by anything**; the four
      citations in BACKLOG_DONE/MILESTONES name *different* files that are
      **not on the M1 at all and were never in git** (two of the four
      already self-describe as "box-local", so check the orchestrator box
      before calling them lost — the other two now carry the same marker).
      The surviving 29 are the sort of thing HOUSE_STYLE's regression
      harness would want as fixtures, so they live in
      `~/.maro/evidence/adversarial-reviews-2026-07/` rather than being
      deleted or published.
  (2) **`.venv-mlx` — was exactly the hole he suspected, removed.** The
      local rung was REMOVED 2026-07-21 by decree ("local LLMs are in the
      way for now"), `LOCAL_VALIDATOR.md` is `status: record`, and **zero
      live code under `src/` or `scripts/` references mlx** — the 307MB venv
      was pure residue. Deleted only after pinning revival cheap:
      `docs/data/venv-mlx-requirements-2026-07-29.txt` (mlx 0.31.2 / mlx_lm
      0.31.3 / transformers 5.12.1), noted in LOCAL_VALIDATOR.md's header.
      **Rider:** the bake-off *results* were sitting in gitignored `output/`
      too — 4 measured JSON files (accuracy/latency/per-case rows for
      qwen2.5-coder-3b and three vibethinker variants). Those are the data
      the doc's revival trigger would be judged against, so they were
      **promoted into the repo** at `docs/data/validator-bakeoff-*.json` —
      the one place committing was clearly right.
  (3) **Rename — symlink does NOT work, but the fix is trivial anyway.**
      Verified: `os.getcwd()` resolves symlinks (`PWD` keeps the link path,
      `getcwd()` returns the real one), so a symlink at the old location
      would still key sessions to the new path. The actual fix is to rename
      `~/.claude/projects/-Users-jeremy-claude-openclaw-orchestration/`
      (93 sessions, 36M) to match the new path key. Caveat found: each
      `.jsonl` embeds `"cwd": "/Users/jeremy/claude/openclaw-orchestration"`
      internally, so the rename is best-effort — and his own fallback
      ("worst case we go spelunking in jsonl files") is correct, they are
      plain JSONL. **DECIDED 2026-07-29 — rename SKIPPED** (Jeremy: *"let's
      skip the rename, agree it's not worth the effort"*). The checkout stays
      `openclaw-orchestration`; the GitHub repo is `maro-orchestration` and
      the local directory name is cosmetic. Do not re-raise: the cost is 93
      sessions of `--resume` history for a directory name, and that trade was
      weighed and rejected, not deferred.
  (4) **Run corpus — not evidence, and the real find was a leak.** The
      audit killed the premise: all 107 runs were **byte-identically
      trivial** — every `item.txt` == `"repo local"`, every status `done`,
      every one the synthetic `repo-local-check` smoke item. Not "early runs
      with bad data" and not "legit failed runs"; the same nothing, 107
      times. **Cause: `tests/test_build_loop_script.py` cleaned up its
      inputs but not its outputs**, leaking one run dir per suite execution
      since 2026-06-21 (plus 110 matching `output/heartbeat/runs/` records
      on a second path). Purging without fixing that would have regrown it.
      Fixed by snapshotting both output dirs before the subprocess and
      removing only what the test itself created — verified delta=0 on both
      paths, and the loose `build-loop-status.json`/`.lock` it also dropped
      are now cleaned when the test created them. One specimen of each kept
      in `~/.maro/evidence/m1-repo-local-smoke-specimen/`; the rest purged.
  **Correction to this item's own earlier text:** it claimed the corpus was
  "the M1's sole run-shaped evidence, deleting it is not obviously free."
  That was wrong — it was test litter, and deleting it cost nothing. The
  M1's lack of run-shaped evidence (contrast doc §8) is unchanged by this,
  which if anything sharpens the point: the M1 had 107 run dirs and zero
  runs.
- [ ] **Iteration protocol (Jeremy 2026-07-28):** the workflow spec in
  the open-thread entry below is a *partial dump* by his own flag —
  ">30 years... I've got reflexes and intuitions I likely can't
  directly name." Protocol: after the first pass or two, he shares real
  work-side changes and we **quiz him on the thought process behind
  them** — "I'm a much better question answerer than an essay writer."
  Interview-shaped elicitation for actionable unlocks; open-ended
  questions stay welcome for context-gathering — his same-day
  correction: "'never' is pretty strong... I like to ramble and answer
  more open-ended questions with talking about context." (Runtime
  journal amended accordingly: 83f06acf supersedes e2124d71 — style
  matched to purpose, not quiz-always.)
- [ ] **CodeLikeJeremy PoC as pattern input (Jeremy 2026-07-28):** his
  work-side `/codeLikeJeremy` skill (machine-local zip at
  `~/claude/strands-review-poc-master-skills-codeLikeJeremy.zip`) — a
  measured behavioral profile from ~1,600 commits + 63 review comments,
  with a regression harness. Work artifacts stay out of this repo
  (boundary decree); the *patterns* are directly stealable for
  HOUSE_STYLE.md: (a) descriptive-vs-prescriptive split — measured
  behavior beside the written standard, divergences labeled
  "Jeremy-specific, not incorrect"; (b) per-context profiles (style
  varies by repo — the M1-vs-maro contrast, independently reinvented);
  (c) author/reviewer asymmetry + **negative space as signal** (what he
  does NOT push back on is data); (d) hedge-language as a
  preference-vs-principle type marker; (e) a dated normative-override
  layer that beats measured densities (our GOAL_BRAIN quoting
  convention, rediscovered); (f) **the headline steal: a style doc
  with a regression harness** — real-issue fixtures, roll the repo
  back, fresh agent + skill makes the change, score against his actual
  diff. Decree-with-tripwire applied to style; HOUSE_STYLE.md can be
  tested the same way (fixtures = real maro sessions).
  **HOLD 2026-08-02 (Jeremy):** don't build the import yet — "I have
  another arc I'm working on at work (a full panel review skill that is
  codeLikeJeremy on steroids) and I think we can steal some more ideas
  from that in a few days, when that's a bit more fleshed out."
  Revisit when he brings the fleshed-out version (~2026-08-05+).

### Open-thread structure — beyond the backlog (DISCUSSION OPEN, updated 2026-07-28)

Resolved 2026-07-28: narrative spine RATIFIED (with same-day UX
amendments), THREAD[slug] markers DECLINED (Jeremy: "let's not
overengineer our dev workflow that's already becoming a bit
intense"), cap mechanics BLESSED, ACTIVE/PARKED split RATIFIED.
Record archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".
Standing revisit trigger: the next thread census re-asks the markers
question with fresh drift data; any lost-thread/premise-drift
incident before then reopens it immediately.

### Standing trigger from the 2026-08-04 closed finds (records in docs/history/backlog-done-2026-04-to-08-p3.md §2026-08-05 + BACKLOG_DONE §2026-08-11)

- **Store round-trip probe** (`scripts/store_roundtrip.py`) — 18 stores,
  0 undeclared drops; the guardrail bug was isolated, not a family; two
  orphan stores confirmed deliberately-retired. **Standing trigger, kept
  visible per the standing-condition lesson: wire it into the self-health
  lane when a second store breaks or at that lane's next pass.**

### Write-before-Read prompt fix: not yet measurable (measured 2026-08-04)

The `FILE EDITS` rule (`ed09b33`, landed 2026-08-03T01:02Z) was recorded
with "residue counter will show whether the 65-turn class actually bends
on future runs." Measured today. **It doesn't say yet, and the first
denominator I reached for was the wrong one.**

Post-fix corpus: 5 runs, 197 tool events, **0** `File has not been read
yet`. Against the pre-fix rate per *tool event* (66/4724 = 1.40%) that's
`P(0) = 0.06` — tempting. But the right denominator is write/edit calls,
not all tool events, and the post-fix corpus contains **8** of them
(pre: 434, rate 15.2%). `P(0 | n=8) = 0.30`. That is no evidence at all.

**Verdict: no signal, need ~20 post-fix write/edit calls before the
question is even askable** (at 15.2%, `0.848^20 ≈ 0.04`). Recorded so the
next person doesn't read "0 occurrences" as "fixed". Unchanged and
separate: `complete_step` tool-missing continues (16 in those 5 runs) —
that's the entry above that declines the surgery on purpose.

### 6287e494: an operator contest that isn't on disk, unexplained

The captain's log has 11 `LESSON_CONTESTED` events. Ten are `tier=medium`
and all ten are contested on disk today. The eleventh — `6287e494`,
`2026-08-02T07:26:31Z`, the **only** LONG contest ever attempted, Jeremy's
L4 surprise read ("tighter step count and smaller code surface isn't going
to improve the plan, just add constraints to make it 'cheaper'") — showed
`contested: {}`.

What was ruled out, not assumed:
- `contest_lesson` works on a LONG-only lesson today, tested in isolation.
- Its source at `06d145b` (the commit of that session) is byte-identical
  to current, so this is not a since-fixed bug in the verb.
- The stale-write hazard above is **not** a demonstrated cause here: that
  row's `times_reinforced` is 7 and `last_reinforced` is 2026-06-12, so no
  post-contest reinforcement ever ran on it.

So the disappearance stands unexplained, and I'd rather leave it named
than invent a story. It matters more for LONG than MEDIUM: LONG is
decay-free, so contestation is its *only* retirement path — a lost LONG
contest is permanent, while a lost MEDIUM one merely delays a row that was
going to decay anyway. That asymmetry is why this is worth a
watch-item rather than a shrug.

**Jeremy's decision is now in effect.** Contest re-applied 2026-08-05
with his verbatim reason and original source stamp (`operator:surprise-
read-chunk-1(2026-08-01)`); LONG store backed up first
(`lessons.jsonl.bak-2026-08-04-precontest`). Verified off the injection
surface afterwards. Until then the lesson was live in injection at
`score: 1.0` and was the top canon candidate at lowered thresholds — i.e.
the surprise-read outcome he asked for had been quietly reverted for
three days.

**Watch:** if a second LONG contest goes missing, that's the signal to
stop guessing and add a write-side read-back assertion to
`contest_lesson`. One instance isn't enough to justify the machinery.

### R6-E. lesson_text embeds truncated goal previews (anchoring risk) — watch-item

The one open residual of the R6 VERIFY_LEARN_ARC V4/R5 V4/V5 review
(everything else archived to BACKLOG_DONE 2026-07-27). `lesson_inject`
is ON (since 2026-07-14) so the A/B is running; first live numbers
(chunk-7 readout, 2026-07-22): with_lessons 58% (15/26) vs baseline 41%
(49/120) — directional positive, small n. Re-evaluate the goal-preview
anchoring risk once the comparison has real n; pre-optimizing the prompt
before then is guessing.

### Step prompts advertise tools the subprocess backend doesn't have — 245 wasted calls, 5.2% of all tool events (FOUND 2026-08-02)

Found by the residue counter on its first pass, which is the point of
building it.

**Live detection since 2026-08-09:** `introspect.classify_tool_pathologies`
(MH taxonomy build) stamps `tool_hallucination` per step on the same
"No such tool available" signature — per-run diagnosis lane instead of
an offline census; 2026-08-09 corpus smoke put it at 178 of 610
transcripts (29%). The fix itself (reconcile advertised vs offered)
remains this item.

**Measured across the whole workspace:** 4,720 tool events, **245 "No such
tool available" (5.2%), spread across 69 of 96 runs (72%)**. By name:
`complete_step` 144, `fetch` 81, then `Bash`/`bash` 7 (a case mismatch),
`register_tool` 4, `flag_stuck` 4, `create_team_worker` 3, `read`/`Read` 2.

**Mechanism, verified on a real call record** (run `9d88acf2`,
`backend=subprocess`, `purpose=step-execute`): the step prompt says *"Use
inject_steps in your complete_step call to add 1-3 research/verification
sub-steps"* and *"Call flag_stuck with reason NEED_INFO: [describe what's
missing]"*. Those tools are real and registered (`tool_registry.py:342`) —
for the API tool-calling path. The subprocess backend runs `claude -p`,
which exposes Claude Code's own toolset, not maro's registry. So the model
is instructed to use affordances that do not exist in its execution
context, obeys, and gets an error. `step_exec.py` is already
backend-aware in several places (the `("subprocess", "codex")` branches
for ASYNC_ESCAPE, ENV_CLAIM, DELIVERABLE_PATH); **prompt assembly is
not.**

**CORRECTED 2026-08-02, same session — I overclaimed the severity, then
measured it.** My first write-up called `inject_steps`/`flag_stuck` "a
silently dead capability" on the default backend. Two checks changed the
verdict:
1. **Vintage:** `_fetch_cli_path` (the existing workaround for exactly
   this, `4879f78`) landed 2026-07-27. Rate **pre-ship 5.28%** (193/3657)
   vs **post-ship 4.89%** (52/1063) — so the problem is current, not a
   historical artifact, but that earlier fix barely moved it.
2. **Backend census:** **every step-execute call in the workspace is
   subprocess — 89 of 89.** There is no API-backend production traffic.
   `inject_steps` appears in **zero** recorded responses, ever.

So it is not "dead on the default backend" — it is **vestigial**: it has
never run in production here, and nothing has visibly missed it. Steps
that lack information already do the right thing without it (4a wrote
"insufficient information found" three times and passed). **I asserted
harm without measuring it, which is the claimed≠probed shape pointed at
my own claim.**

**Revised cost, honestly small:** 2–11 missed calls per run — cents.
The prompt also spends tokens on a tool contract that cannot bind on 100%
of production traffic.

**Therefore not fixed, deliberately.** The natural fix (make the
tool-contract lines backend-conditional, or teach the subprocess path to
parse `NEED_INFO:` out of prose) touches the most load-bearing paragraph
of `EXECUTE_SYSTEM`, which is heavily tuned and has a harness optimizer
pointed at it — a risky surgery for a cents-per-run payoff and a
capability nobody has missed. Recorded per the recovery-over-correctness
posture: take the coarse truth, don't churn on the details. Revisit if
API-backend traffic ever becomes real, or bundle it into the next
deliberate `EXECUTE_SYSTEM` pass.

*(Two closed sub-findings — the Write-before-Read fix and the
`is_error` undercounting measurement lesson, both pinned — archived:
BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".)*

### 22. Capabilities catalog — open residuals (shipped trail archived)

Catalog + canonical Manti case + blank-slate skill set + cuts-first v0 +
session-reuse spike/prototype all SHIPPED (full trail archived to
BACKLOG_DONE 2026-07-27; canonical measurements in
`docs/CAPABILITIES.md`). Still open:

- [ ] **Errand-envelope target (~1–3 min / cents): re-measure DONE
  2026-07-29, target still open but the lever changed.** Clean live run
  (0c833432-hardy-haven): 6 steps, **7m12s** wall vs the 16m43s July-11
  baseline — the warm-pool fix landed as predicted (tight-loop
  between-call gaps 15–23s ≈ 12–15s pool + model time; 24 calls captured
  in build/calls, first live proof of the always-wrap record seam).
  Verdict on the next lever: closure plan→verdict→quality gate ran ~8s
  serial — the closure ∥ quality-gate safe pair is NOT worth building.
  The actual tail is the adversarial claim review (~57s, and it fired
  live, contesting unverified version/format/line-count claims — working
  as designed). Remaining distance to 1–3 min is step count × model
  time, i.e. plan-shape (fewer, fatter steps for errand-class goals),
  not orchestration overhead. Boot-tax prompt half + answer-first
  delivery already shipped.
- [ ] **Standing habit:** capture real asks as-phrased into
  `docs/CAPABILITIES.md` (also the CLAUDE.md capability-capture rule
  since 2026-07-11).
- [ ] **(Vision)** shared trusted skill directory + cross-instance
  learning share — see Vision section entry.

### 0. Test corpus — capture the missing layers (forward record-mode + full archive)

**Shipped 2026-06-26 (the "now" half):** `scripts/harvest_corpus.py` distills the
live workspace history (`runs/` 569 captains-log slices + `projects/`) into
deduped fixture slices under `tests/fixtures/orchestration_corpus/` (thinned
slices committed, full git-ignored + reproducible). 24 slices, 5,646 raw records;
`tests/test_orchestration_corpus.py` proves consumability + regression-guards the
quality-gate escalate formula against 122 real verdicts (0 mismatches). Workspace
data is preserved, not deleted.

**Shipped 2026-06-26 (forward record-mode + curation):**
**Remaining ("later"):**
- [ ] **Full raw archive (optional).** If/when `runs/`+`projects/` (~79M) get
  pruned, snapshot the full (non-thinned) slices somewhere durable first — they're
  only reproducible while the workspace exists.
### 1. Bound worker writes — residual: Bash write shapes the fence can't see

**Shipped arc archived 2026-07-04** — the full write-fence history (cwd
root-cause + soft fence, projectless-run fence hole, scavenge detection,
cwd-drift tracking, tier-a demotion, Jeremy's enable + same-day narrowing
with `/tmp` + goal-declared roots) lives in BACKLOG_DONE.md ("BACKLOG #1:
write fence — shipped arc") and `docs/BOUNDED_WORKSPACE.md`.

- [ ] **Residual: Bash write shapes the regex can't see** — `cp`/`mv`/`sed -i`
  targets, subshell/pushd cds stay invisible to `detect_out_of_fence_access`
  (documented in `docs/BOUNDED_WORKSPACE.md` known holes). Extend from real
  `SCAVENGE_DETECTED` evidence, not speculation. Current state: detection
  always-on (`validate.scavenge_detect`), enforcement **code-default ON
  since 2026-07-09** (1.0 posture flip; this box had run it enabled since
  2026-07-04 with no false positives — opt out via
  `validate.write_fence: false`, see docs/DEFAULTS.md). Reads stay
  unrestricted by design (logged, not blocked); a read-restricting mode
  remains possible if scavenge read rows ever show real contamination.
  **Evidence refresh 2026-07-14:** searched the unified workspace, run slices,
  repo experiment output, committed corpus, and pre-unification workspace
  locations. No `SCAVENGE_DETECTED` / `FENCE_WRITE_BLOCKED` row or recorded
  `cp`/`mv`/`sed -i` miss survives. The only concrete historical evasion is
  run `668e46d1`'s `cd` + relative redirect, already fixed and regression-pinned
  in `TestScavengeCwdDrift`. Leave this residual evidence-gated; do not add
  speculative shell parsing until a real missed transcript lands.

### 17. Run-visibility residuals (2026-07-09 real-data review)

All four original sub-items shipped 2026-07-09 (two concurrent sessions —
see BACKLOG_DONE for both): contextvar loop_id threading + purpose stamping
(this session), live-report post-curation refresh + NOW-lane mini-reports
(concurrent session, superseding this session's own narrower post-curation
refresh attempt — see BACKLOG_DONE for the reconciliation note).

- [ ] **Index rebuild is O(all runs) at every finalize** (~277ms at 668
  dirs, via the post-curation hook). Fine now; revisit around ~10k run dirs
  (incremental index, or rebuild only on viz/backfill).

- [ ] **Revisit sweep is O(all runs + full log corpus) per heartbeat and
  dead ends are never pruned** (accepted r2 residual, 2026-08-15 architect
  MEDIUM — accepted because both passes are linear, <1s at 786 runs, and
  query_log's full-corpus scan happens regardless of window). Revisit at
  the same ~10k-run-dirs horizon as the index rebuild above; likely
  answer is a max-age cap on matchable dead ends (a 2-year-old dead end
  reopened by a new skill is noise anyway) or the shared incremental
  index.

---

### REPL-reading for large documents/corpora (SIDEQUEST OPENED 2026-08-02, Jeremy)

Steps 1+2 both shipped (skills/repl_reading.md protocol,
2026-08-02; maro-read CLI / read_query.py recursive sub-calls,
2026-08-11); A/B-1 through A/B-4 + retest judged; teaching fix +
planner lever (READ_QUERY_STEP_RULES, skeptic round 4/4 fixed)
shipped 2026-08-13. Full build + A/B trail — incl. Jeremy's ask,
the denominator-lesson correction, and the contest-cascade
resolution — archived: BACKLOG_DONE.md §"Moved from BACKLOG
2026-08-16". Still open, standing trigger (not yet judged):
  **A/B-5 PREDICTION REGISTERED 2026-08-13 (before any dispatch):** on
  the next naturally corpus-shaped dispatch (host lane), (i) the PLAN
  contains >=1 step naming the sub-query CLI, and (ii) the step
  transcripts show >=1 actual invocation. Falsifiers: (a) plan never
  names the verb -> planner guidance-form teaching is also insufficient;
  next lever is deterministic (post-decompose step rewrite keyed on
  file-size stat, or the #22 errand envelope), not more prompt wording;
  (b) plan names it but the worker still reads/greps instead -> habit
  overrides explicit step text; lever moves to verification (a named-
  command step that didn't run the command fails its verify question).
  Cost axes deliberately NOT re-registered (A/B-4's plan-shape confound:
  step-count/plan-weight dominated tokens); direction is noted
  qualitatively if (i)+(ii) hold. Corpus-run intermediates the run
  GENERATES mid-flight (A/B-4's 566KB audit_grep.txt) are out of this
  lever's reach by construction — the planner can't name files that
  don't exist yet; that residual belongs to the executor axis (exhausted)
  or the verification axis, not to more planner wording.
- Relation to existing items: this is the generalized form of the #22
  errand-envelope lever and the designated reader for RUN_TEACHINGS
  chunk-1's input stage; it is NOT a replacement for the cheaper
  patch/diff-edit fix already noted there.

### DiffCodeGen steal-list — execution-based consensus over paid LLM judging (run 8b8671bd, 2026-08-06)

Run 73426541 reviewed arXiv:2605.20473 (DiffCodeGen: generate diverse
code candidates, execute against fuzzed inputs, cluster by behavior,
take the medoid — no LLM judge) against Maro. Claims verified locally
same day: the council and 3-candidate compose exist as described, and
the mechanism gap (no fuzzing/behavioral-clustering/medoid anywhere)
holds across all of src/, not just the run's 13-file sample. Full brief:
`~/.maro/workspace/runs/8b8671bd-warm-lichen/artifact/steal-list-brief.md`
(+ mechanism-benchmark-map.md alongside). Kept items:

- [ ] **P0-1 (design default, zero build):** any future capability that
  generates multiple code/patch candidates selects by deterministic
  execution-based consensus, not a paid LLM judge (paper: +2.7–9.5pp
  accuracy, ~5x time / ~25x token reduction vs LLM-judged baseline).
  Attach this as the default design reference when such a ticket appears.
- [ ] **P0-2 (near-term, gated on cost data):** audit `quality_gate.py`'s
  3-seat council call-sites for sub-cases judging testable/executable
  artifacts rather than prose; replace those with execution-based checks.
  Pull actual council/compose call-volume + cost telemetry FIRST to size
  ROI before committing build effort.
- Dropped from the run's list as speculative beyond the paper's evidence:
  plan-compose replacement (plans aren't executable; different signal
  needed) and clustering prose verdicts (paper only benchmarks code).

### Loop lane has no risk-minting path (found via 8b8671bd stub, 2026-08-06)

Jeremy spotted a served `RISKS.md` that was a 42-byte "(fill in)" stub
(run 73426541 / 8b8671bd-warm-lichen). Shipped same day: `ensure_project`
no longer mints RISKS/PROVENANCE stubs (lazy-create on first append was
already the behavior of `append_section_lines`), and curation excludes
`RISKS.md` from deliverables. The open design question underneath:
`append_risk` fires only from the orch item lane on a *blocked* item
(`orch.py:352`) — the agent loop writes DECISIONS at plan/post-step/
finalize but has no path that mints run-discovered risks/unknowns into
the project's RISKS.md, even though step results carry them (steps emit
"UNKNOWNS RESOLVED" blocks; scope-parse failures land only in
`build/scope-raw-FAILED.txt`). Should-we before how-would-we: decide
whether open risks/unknowns from loop runs belong in project RISKS.md
(would also feed the era-00 "RISKS.md as reviewer input" item above), or
whether the closure/verdict layer is the canonical risk record and
RISKS.md stays an orch-lane-only artifact. **DECIDED + BUILT 2026-08-10
(Jeremy: mint to RISKS.md):** `loop_finalize._mint_run_risks_to_project`
— post-closure, mints the verdict's gaps (≤3) + scope-parse-failure
sentinel via `append_risk`; idempotent per loop, audit-held loops never
mint, `project.risk_mint` killswitch (DEFAULTS.md row). Family answer
now consistent: mid-run steps → NEXT.md ledger (2026-08-09), risks/
unknowns → RISKS.md (this), verified findings → closure/verdict layer
stays canonical.

Third specimen, same session (generalizes the item: **loop→project
record mirroring is partial**): initial plan steps are mirrored into the
project NEXT.md ledger (`loop_planning.py:99` `append_next_items`), but
steps added mid-run never are — 8b8671bd's steps 3–7 (the ones that
produced the deliverable) render as `ledger #-1` in the plan log and
have no project-ledger entry; the plan header's "Progress: 7/3 done" is
the same fact showing through. **Mirroring half FIXED 2026-08-09** (it
extends the existing initial-plan/interrupt-path convention, no new
decision): both inject paths (sequential `_process_done_step` +
parallel batch) now `append_next_items` before splicing, degrading to
the old `-1` sentinel on ledger failure; pinned
(`test_injected_steps_mirrored_into_project_ledger`). The risk-minting
should-we itself was DECIDED + BUILT 2026-08-10 (stamp above; this line
previously contradicted it — fixed in the 2026-08-11 sweep); what stays
open is the FAMILY question below. And the steal-list findings themselves
only reached BACKLOG because Jeremy asked (captured 25a621e). Whatever
the risk-minting decision is, it should probably answer for the family:
which loop outputs (risks, mid-run steps, verified findings) are owed a
durable project-record write, and which records are canonical elsewhere.

One-specimen robustness note, same run: the scope pass failed to parse
(`build/scope-raw-FAILED.txt`) because the scoper emitted preamble prose
plus a `powershell`-labeled fence instead of bare output; run degraded
gracefully but lost scope injection. If a second specimen appears, a
strip-prose/fences pre-parse pass in `handle.py`'s scope path is the
cheap fix.

## Vision / Deferred

### Subtraction audit — the maro-shaped trimming pass (Jeremy, 2026-08-21, "for later")

Jeremy, during the async-tail burn-in: *"at some point we're going to be
working against our working system with all the bolt-on 'good ideas'...
pretty sure we have a ways to go yet before we quite get there"* — and the
aspiration named out loud: end up like the SpaceX booster, *"much much
cleaner, simpler, and more efficient... I'm not saying we're that level of
smart, but we're consistent and that's not nothin'."*

The shape: a periodic maro run (or small series) whose GOAL is subtraction —
"name the three mechanisms whose removal would cost least, with evidence" —
rather than addition. Instrumentation to feed it already exists and keeps
getting better (dev-status fan-out rate, the no-stopping-rule census,
per-mechanism cost rows, shadow-lane comparisons); the precedent exists too
(sandbox.py retired at −1,670 LOC as an unwired prototype, 2026-07-13;
scan_cap deleted one round after it shipped, 2026-08-20). What does NOT
exist is a recurring lens that treats removal as a first-class deliverable
with the same rigor additions get (probes, adversarial review of the
"nothing needs this" claim — a deletion's evidence bar is HIGHER, per the
untrusted-deletion lessons).

Deliberately "for later" — not evidence-gated, DATE-gated by feel: worth
running once the current arc (async-tail flip, byte-safety convergence,
treasure-map) quiets down. When it runs, pair it with the retention decree
(surface, let the operator decide — the audit NOMINATES, Jeremy deletes)
and the [[feedback_correctness_over_frugality]] posture: measured removal
candidates, never vibes.

- [ ] First subtraction-audit run: pick the instrument set, run the
      nomination goal, adjudicate with Jeremy.

### Cheap-tier step execution — DEFERRED to a future optimization pass (Jeremy, 2026-08-20)

**Status: parked deliberately, not queued.** Jeremy, 2026-08-20: *"let's revisit
during an optimization pass in the future… no need to keep that bookmark front
and center, that can live in the later."* Moved out of the Actionable Stack the
same day it was written there. The MID-floor decree **stands** — this is a named
future revisit, not an open question.

**Direction for when it comes up** (his words): *"We should A/B test that, gather
data, and let maro itself decide what models to use; probably ideally let it
learn and train, though that might be too complicated."* The target is therefore
**not** a hand-tuned step_type→tier policy table — it is maro selecting its own
models from measured outcomes, with learning the choice as the stretch goal.
Don't ship a static table and call it done.

**The genuinely under-tested part**, which narrows the question: *"IIRC we
settled on mid model + low thinking to keep it 'cheap', but possible we didn't
test it as thoroughly as we should have. I think at the time it seemed heavy for
something we could throw a little spend at."* So the open empirical question is
not "should we use cheap models" — it is **whether mid + low-thinking was ever
properly measured as the cheap configuration**, given the original call was made
partly on not-worth-the-effort grounds rather than on data.

**History that makes this a re-test, not a build (corrected 2026-08-20):**

**This item previously said "the classifier and the tier selector exist and are
not connected — a wiring gap, not missing infrastructure." That was wrong** and
is corrected here, because it would send the next session off to build something
that was deliberately deleted.

**What actually happened.** Per-step tiering *shipped* — Phase 57, "Adaptive
Model Tiering", 2026-04-06 (`docs/history/ROADMAP_ARCHIVE.md`): `classify_step_model`
chose a tier from step content, plus retry- and verify-failure escalation. It was
then **deliberately removed** on 2026-07-21 in `b6fd4881` (authored by Jeremy)
under the **2026-07-20 decree "execution defaults unified at MID"**. From that
commit message: *"scope-lift block + classify_step_model cheap-downgrade deleted
… the user/CONFIG.md template no longer ships the cheap pin that silently
recreated the split."* `classify_step_model` has 0 hits in `src/` today.

**What survives** is escalation-only, and it is intact: `_step_tier_overrides`
and `_session_tier_floor` in `loop_execute.py`, resolved by `_select_step_adapter`
(`loop_execute.py:126`). Its own comment states the rule — *"Execution floor is
MID (2026-07-20 decree — per-step cheap downgrade removed); only a raised session
floor re-tiers here."* So tiers move UP from MID on failure signals and never
DOWN on triviality. CHEAP is never selected for step work **by decree, not by
omission.**

**Why it was removed — the evidence is on record.** `docs/history/2026-03-31-factory-mode-findings.md`
§3: factory_thin on Haiku burned **1,512K tokens vs Mode 2's 344K — 4.4×** on the
same goal, one research step alone taking 560K, *"because Haiku lacks the output
compression judgment that Sonnet applies."* Verdict: *"even with the fix, Haiku's
verbosity means factory_thin on Haiku is not reliably cheaper than Mode 2 on
Sonnet … The model cost advantage disappears."* Cheap models are verbose, and
verbosity eats the per-token discount. A separate 2026-06-21 finding killed the
local rung on latency (~10s/step on this box).

**The idea is not dead — it was moved to where it pays.** `src/hosted_free.py` is
live: ladder is Tier-0 deterministic → hosted-free (Groq/Gemini free tiers) →
paid, aimed at **validation**, which that module calls the highest-volume call
class and *"the biggest avoidable token sink."* Opt-in, default off. So cheap
models were applied to the high-volume/low-judgment class and withdrawn from the
low-volume/high-judgment one. That is arguably the correct resolution of the
"cheap model does the step work" idea, not a failure of it.

**Pre-registered revival trigger already exists** (`docs/LOCAL_VALIDATOR.md:17-24`):
revive the local rung *"if the hosted free tiers churn away"* — providers cut free
quotas, keys die, breakers trip chronically. Re-entry path = the bakeoff
methodology in that doc + the kept corpus `tests/fixtures/validation_cases.json`
+ the pre-2026-07-21 implementation in git history.

**So the open work is a RE-TEST, not a build.** The 4.4× is a 2026-03-31 fact
about then-current cheap models; model capability moves. The re-test is cheap
now: replay the corpus against current CHEAP-tier models, score on
cost-per-accepted-outcome via `step-costs.jsonl`, and compare against the
recorded 4.4× baseline. If verbosity has closed, that is the evidence that
reopens the decree — which is Jeremy's call to reverse, not a session's.

### Codex (and maybe Grok) as primary executor — gated revisit of the stay-first-party call (Jeremy, 2026-08-11)

His phrasing: "at some point we should be testing codex as primary, and
maybe grok too... but let's get more consistently good results before we
do that." This REVISITS BACKLOG_DONE item 24's model-route decision
(2026-07-11/12, resolved stay-first-party for the orchestrator + workers)
— that resolution stands for now; this entry is the named future
exception, explicitly gated on "consistently good results" first, so the
A/B has a stable baseline to compare against. Prior art when picked up:
item 24's analysis, the `("subprocess", "codex")` backend branches
already in the codebase (codex is exercised today as the adversarial
reviewer, so the adapter path is warm), and the xAI Grok adapter
(`backend 'xai'`, ee6de2c) with Jeremy's console.x.ai credits. Shape
when live: run a small goal battery (LT-style pre-registered rubrics)
with executor swapped per-arm, verdict layer held constant — measure
achieved-rate, verdict cleanliness, and provider cost, not vibes.

### Invalid-assumption detection above the micro level (Jeremy seed, 2026-08-02)

Seeded while green-lighting the CASCADE contests: "that brings up a
good point — we should have a way (eventually?) to figure out invalid
assumptions, more than just failures at a micro level… not sure what
that looks like yet though." Today's machinery catches bad premises
only where they surface as individual failures (provenance guard,
contradiction flow, contest verb, closure disagreement) — all
micro-level, one artifact at a time. The gap: an assumption that
quietly shapes many decisions (a lesson corpus, a config posture, a
"we can't do X" belief) has no detector until something breaks loudly
enough to audit. Possible shapes when this gets picked up: assumption
provenance (what facts does this plan/lesson REST on — the
evidence_sources stamp is a start), periodic re-probe of load-bearing
beliefs (terrain verify_probe generalized), or contradiction sweeps
across stores rather than within them. Not a build item yet — the
phrasing is the value. Related: contest-on-bad-provenance decree,
terrain teachings (cheap re-checkability), hypotheses lane.

### Verbal UX for orchestration (2026-07-28, Jeremy — revisit trigger named)

Jeremy, during the compound-thinking spitfire: "we sort of started
hand-waving at a verbal UX for all of this. If we don't have a backlog
item to revisit this (later, after we start seeing larger successes
more often), maybe as part of optimization when real time makes more
sense." No such item existed — this is it. Not a build item: revisit
when (a) larger successes are landing often enough that interaction
cadence, not capability, is the bottleneck, or (b) a real-time
optimization pass makes latency work worthwhile anyway. Prior art
touchpoint: GOAL_BRAIN:2174's note that voice UX masks planning
latency. Ties to the escalation-surface decree (the substrate
go-between IS the surface) — a verbal surface would be another face of
the same go-between, not a new channel architecture.

### Two small refactors from the 2026-07-18 adversarial review (low, grab when nearby)

- **NOW-lane prompt assembly is duplicated** between `handle._run_now` and
  `conductor._handle_now_lane` (both: enrich_step_with_urls + link-read
  system suffix + degrade-on-failure). Two copies is at the repo's
  3-wants-extraction edge; if a third NOW surface appears or the enrichment
  pattern changes again, extract a shared pure builder (structured state in,
  (system, user) out) into web_fetch or a small now_lane module.

### Time blindness — LLMs don't experience ideas over time (2026-07-11, Jeremy)

Jeremy (closing theme, verbatim): "humans perceive stories and ideas
over time (as we experience them) and LLMs... don't. That's a
communication blind spot. We might need to fight some kind of time
blindness between prompts, even in the same session, I think it's
getting worse rather than better here and there, and sometimes it
matters a lot."

No concrete goal yet — recorded well per his ask. Candidate starting
hooks when this gets a session: (a) age-stamp injected evidence and
recalled context (a lesson from February reads identically to one from
yesterday today — staleness is invisible to the model); (b) elapsed-
time awareness between steps and between sessions (the run knows wall
clock; the model is never told "your last step was 40 minutes ago" or
"this thread went quiet for 3 days"); (c) ordering/decay in recall —
dev-recall and memory injection currently rank by relevance, with time
as a hidden variable; (d) the captain's-log slice already carries
timestamps — surfacing them *into prompts* is cheap and measurable.
Related evidence: the godot retrospective (agenda-state divergence over
a long session = time blindness inside one session), stale-source
dissent handling in the Manti runs (the system already fights data
staleness — this extends the fight to its own conversational state).

**First slice — SHIPPED 2026-07-15** (vehicle added 2026-07-12 handoff
audit; hooks (d)+(a) only). What shipped: `src/age_stamp.py` +
`memory.age_stamps` flag (hardcoded default OFF; byte-identical
off-path test-enforced, worker_slice pattern); age suffixes at the
three seams — worker slice (`memory_bridge.stamp_items_with_age` +
director wiring), recall() loop slice, `inject_lessons_for_task` (the
decompose seam turned out to BE recall()'s loop slice — seams b/c
converge); `[time]` elapsed-gap line (≥10 min module constant) via the
contribution ledger; `age_stamped: true` on WORKER_SLICE_INJECTED /
RECALL_PERFORMED only-when-stamped. Adversarial review (FIX_FIRST →
fixed same pass): legacy rows missing `recorded_at` no longer fabricate
a load-time "learned today" stamp (absence preserved as `""` at BOTH
raw-row loaders — dedup previously froze fabricated dates to disk);
time contributions are recomputed-never-replayed
(`ContributionLedger.drop_source` at both drain points — the re-arm
paths replay every other source correctly); batch-boundary monotonic
capture pinned (mutant killed); future timestamps render date-only, no
"ago" claim. Eyeballed on real box data (273 lessons, all dated) →
flag ON this box per the vehicle. Natural second slice: the graveyard
block in recall() ("resurrected from decay") injects with no age.
Hooks (b) full elapsed-time model — incl. cross-process resume gaps
("this run was interrupted 3 days ago"; checkpoint knows
`in_flight`/`started_at`, the monotonic capture is in-process only) —
and (c) time-aware recall *ranking* stay in this vision entry — (c)
especially must not ship as a silent relevance-formula change; it's a
verify→learn-adjacent measurement question.

### Perspective / camera rotation — bringing the human lens functionally (2026-07-11, Jeremy)

Jeremy (closing theme, verbatim): "I've talked about rotation, and
zooming in and out for seasoned developers. That's really just
re-framing and adjustment of perspective (from a game engine camera
type perspective), and I think the same holds true for ideas. LLMs have
ridiculous access to data, language and information. But the
perspective isn't the same at all. We need to help bring the 'human'
perspective, both innate and skilled usage of, into things at least in
a more functional light... Watching you react to seeing the
orchestration finding some of the perspective that is much more easily
discoverable from an end-user perspective makes me happy -- we're
getting there to a degree, but I'd like to refine that."

Standing direction, not a task. Constraints already on record: fixes
belong in inference moves (scope, memory, inversion, rotation), NOT
prompt taxonomies (feedback_inference_not_prompting); cuts-first
planning is the first shipped rotation-like move (narrowing = zoom).
What "refine" plausibly means next: (a) named lens/rotation moves the
planner or navigator can *choose* (invert, zoom-out-to-goal,
zoom-in-to-specimen, end-user-seat) the way draw_cuts chooses probes;
(b) the corpus arc as evidence — the end-user perspective (what a
person actually asked, what they actually got) surfaced failure
patterns that code-side inspection never would; institutionalize that
seat in review/verify stages; (c) ties to the "are we re-inventing
reasoning-model behavior?" open question — same kill-test posture
applies to any lens machinery.

**First slice — VEHICLE (added 2026-07-12 handoff audit; deliberately
the smallest honest step, per the kill-test posture):** the end-user
seat (b) only, because it's the one lens with live evidence behind it.
Concretely: closure verification and adversarial review each gain an
explicit end-user-seat pass — "answer as the person who asked: did I
get what I asked for, in the form I could actually use?" — as a named
section of the existing prompts (no new LLM call, no lens registry).
The Manti NOW failure is the canonical specimen (a *where* question
answered with no *where*); the shipped `_NOW_VERIFY_SYSTEM` non-answer
judging (a1f472f) is the pattern to generalize. Acceptance: on the
existing dogfood/closure corpus, the seat catches ≥1 real miss that
current checks pass (candidate: run 315ebffb's on-topic-but-wrong-
subject haiku) without new false demotions. Named lens *moves* for the
planner/navigator (a) stay vision — they must survive the kill-test
("would the same-tier model with identical context do this unprompted?")
before earning machinery.

### Learning-trust maintenance — "usage only" vs "learning" sessions (vision, 2026-07-12, Jeremy)

Jeremy (closing the container-executor design conversation, verbatim —
full quote in GOAL_BRAIN Decisions 2026-07-12): skill poisoning and
self-learning edges, "same sort of thing in both directions... ultimately
we will likely need 'usage only' vs 'learning' sessions, the ideal being
scanning and auto-upgrades by the system itself, which is a neverending
quest, same as a virus scanner solving protecting an individual
workstation; one way to solve it but a constant maintenance headache.
We'll get into all that more later."

Direction, not design. Doors already built when this gets a session:

- **Usage-only mode is a flag, not an architecture:** the learning
  ingestion side is already gateable per-run — `defer_learning`
  (loop_types/handle), the crystallization gates in finalize, the
  skills-lite promotion switch (`skills.lite_promotion`). A
  `learning: off` session = those seams held closed for the run, artifacts
  still produced, nothing ingested. Cheap when wanted.
- **The scanning/auto-upgrade half** is the verify→learn arc's
  expectation→verdict→demote lifecycle pointed at learned artifacts —
  VERIFY_LEARN_ARC.md V2 (cadence verdicts + auto-revert) and V3
  (graduation class-specific auto-verify), both SHIPPED 2026-07-14, are the seed
  machinery; the virus-scanner
  analogy's "constant maintenance" is exactly why it must ride existing
  cadence hooks, never a daemon.
- **Trust boundaries already on record:** imports arrive contested
  (PORTABLE_LEARNING_DESIGN §8, ratified), skills-lite injection_guard +
  quarantine (cs-r2-01 family), never-auto-adopt. The both-directions
  concern (poisoned learning leaking OUT too) is the export half —
  `secret_scrub` + pack sealing cover the mechanical side; content-level
  export scanning is unaddressed and belongs to this item.

### Shared trusted skill directory + cross-instance learning (vision, 2026-07-10, Jeremy)

"Maybe a shared and trusted directory to pull from at a later time,
crowd-sourced or not" — the blank-slate pre-installed set (item 22) is the
seed; sharing is the scale-out. Ties directly to "sharing learning across
instances" as a later feature: skills are the portable unit today
(`maro-import` already merges with quarantine + provenance), lessons ride
the same rails later. Trust boundary notes recorded in CAPABILITIES.md so
we don't relearn them: a shared directory is a supply chain (cs-r2-01's
threat model at internet scale) — provenance required, same
injection/dangerous-pattern gates as skills-lite, imports arrive as
reviewable candidates never auto-trusted. Direction, not design; wants its
own pass when it ships publicly.

**Refined 2026-07-11 (Jeremy, after the social_search arc):** "an opt-in
brain for the users of this orchestration, to share knowledge and skills;
sourced, with pedigree, maro-graduated and proven skills only, and only
opt-in overall from the user's standpoint, the sharing and details are
maro-as-clients talking to a coordination server. But that's for later."
Architecture sketch this adds to the 07-10 direction: client-server (a
coordination server, not peer-to-peer), pedigree/provenance as a
first-class field, the graduation machinery as the quality gate (only
maro-graduated skills are shareable), and opt-in as a hard product
stance — default is fully local. Trigger insight was the Reddit/X access
recipes: platform-access knowledge is exactly the kind of
expensive-to-discover, cheap-to-share, decays-over-time artifact a
coordination brain is for. Still later; still direction, not design.

### Post-Purgatorio decision batch (2026-07-09, Jeremy — quotes in GOAL_BRAIN Decisions)

- [ ] **Official scheduler/timer layer (later; auto-resume rides it — session-protocol arc raises its priority, see SP entry).**
  Jeremy: "maybe we need a more general official scheduler/timer that the
  user can hook into/see/manage if they wish." A visible, user-managed
  timer surface (list/inspect/disable) — coexists with the no-cron
  invariant, which bans *hidden self-rearming* schedules, not an official
  transparent one. Auto-resume of interrupted runs ((h) deferred half)
  becomes this layer's first consumer; heartbeat scheduling may too,
  pending the SF-1 supervision decision.
- [ ] **Knowledge-web read side: wire it properly (later, KEEP) — TRACED
  2026-07-13, premise was wrong, real prerequisite work identified before
  any read-side code can pay off.** Descoped from 1.0 docs (node store +
  BM25 is the honest claim) but explicitly kept: "I'd like to keep it on
  the list. I think it could be really powerful if done well (and right
  now sounds like it isn't)." That instinct was correct — traced the real
  `~/.maro/workspace/memory/knowledge_{nodes,edges}.jsonl` data before
  writing any code, and the backlog's own framing ("write side + 2124
  edges exist; read side has zero callers") undersold the actual gap:

  - **All 2124 edges connect only `lf-` (link-farm import) nodes to other
    `lf-` nodes — zero edges touch the 252 real, system-authored
    orchestration nodes** (insight/pattern/principle/technique/tool). Not
    a sampling artifact — checked exhaustively: 2124 both-lf, 0 both-real,
    0 mixed. `build_wiki_link_edges` (the only code that could produce a
    `related` edge with two node-id endpoints) has **zero production
    callers** — these edges came from whatever process bulk-imported the
    link-farm content, not from anything in `src/`.
  - **Zero of the 252 real nodes' descriptions contain `[[wiki-link]]`
    markup** — checked every one. `build_wiki_link_edges` would produce
    zero edges even if run against them today; nobody authors nodes with
    that convention, so the mechanism the read side was meant to traverse
    is dead on the write side that actually matters.
  - Net effect: wiring `load_knowledge_edges` into
    `inject_knowledge_for_goal` as originally conceived (walk a matched
    node's edges, inject adjacent nodes) would do **nothing** for a real
    goal (the orchestration knowledge that matters has no edges to walk),
    or — if scoped to all nodes including `lf-` — would inject arbitrary
    link-farm co-occurrence pairs (e.g. a financial candlestick model
    linked to a genome-sequencing tweet linked to an open-source research
    agent, weight uniformly 0.5, "related" for everything) into goal
    context as if they were meaningfully connected. That's noise, not the
    "Correspondence" payoff — exactly what Jeremy's instinct flagged.
  - **Separate, smaller pre-existing note (not this item's blocker, just
    surfaced by the same trace):** `inject_knowledge_for_goal`'s existing
    TF-IDF node query (`domain=None` from `recall.py`) already ranks
    `lf-` link-farm nodes in the same domains as real orchestration
    insights (`orchestration`, `tooling`, etc.) — a goal could already be
    injected raw curated-tweet content today if it scores well, independent
    of edges. Not investigated further; flagging so it isn't
    re-discovered as a surprise later.

  **Fix direction, in order:** (1) **DECIDED 2026-08-02 (Jeremy,
  decree-class): lf- nodes are a third-party data resource, not maro
  knowledge** — "treat like a 3rd party website for gathering data,
  because it is"; never injected into goal context as learned
  knowledge, end user need not know the source exists. Exclusion
  shipped same day (see knowledge_web lf- injection guard + tests);
  the corpus stays queryable as a reference source. (2) if adjacent-
  knowledge retrieval over the *real* knowledge base is still wanted, build
  an actual edge-generation mechanism for it — since manual `[[wiki-link]]`
  authoring isn't a convention anyone follows, the realistic option is an
  LLM-assisted "does this new node relate to an existing one" pass at
  node-creation/crystallization time (same shape as the skill_candidate
  catch-up sweep shipped this session), not the existing regex-only
  `build_wiki_link_edges`; (3) only then does wiring `load_knowledge_edges`
  into injection have real signal to traverse. Left as `[ ]` — this is a
  design decision (what should the graph even encode) before it's an
  engineering task, not something to improvise past Jeremy's own stated
  uncertainty about doing it well.

  **Placement wart accepted as-is (Jeremy 2026-08-03):** after the
  disposition-hardening pass (both import lanes stamp lf-, bridge never
  dedups against reference rows, promotion skips reference rows —
  b8b9840), Jeremy: "it probably does need some cleanup later, but I'm
  ok with it as-is for now." The eventual cleanup is a separate
  reference store so third-party data stops living inside
  knowledge_nodes.jsonl and the prefix carve-outs become unnecessary —
  natural piece of the artifacts-over-streams arc, not urgent while the
  carve-outs hold.

### Graph memory + recursive-orchestration scoped memory (2026-06-21, vision)

**RESOLVED 2026-07-07/08 — this entry was stale until 2026-07-09.** Direction
decided 2026-07-07 (memory becomes a module; see GOAL_BRAIN.md Decisions),
bake-off same day picked a self-built sqlite3+FTS5 adapter over TencentDB
Agent Memory / Mem0 / Zep-Graphiti (`docs/history/2026-07-07-memory-bakeoff.md`),
shipped same day (`src/memory_sqlite.py`). Worker-recall-slice §7 A/B completed
2026-07-08 (16 clean runs, every measure favors the slice or ties) and Jeremy
flipped it on as the hardcoded default (`memory.worker_slice`, see
`docs/DEFAULTS.md` and `docs/history/2026-07-08-worker-slice-ab.md`). Original
brief: `docs/history/2026-07-04-memory-decision-brief.md`. **One residual not
yet decided:** the fastembed+sqlite-vec semantic lane is still gated behind
"only if BM25 measures insufficient" — full-corpus verdict (1,652 items, see
GOAL_BRAIN.md 2026-07-07/08 entries) showed sqlite-fts5 wins hit@1 + 5×
latency but loses hit@5/MRR to token-overlap; whether that's "insufficient"
enough to build the semantic lane is unmeasured/undecided. (2026-07-09
review: confirmed stays-gated, nothing blocked on it — revisit only when
organic worker-slice retrieval misses surface, with the paraphrase-lane
numbers as the evidence file.)

Durable replacement for the fixed-size inter-step truncation caps (the 800/500/200 band-aids
above — lossy fixed-array-vs-string, the kind of thing that's bitten us). Jeremy's framing:
orchestration is likely "recursive — orchestration all the way down," so a memory layer must
support **scoped/hierarchical** access — a sub-agent reads its own scope PLUS the higher
orchestration scope, built generically enough to serve both. Pairs with CAG-style caching so
sub-agents lever cached static context instead of re-ingesting. See memory
`project_retrieval_graph_memory_direction` + `project_recursive_orchestration_memory`.
NOTE: this replaces the *caps*, not the token-explosion *leak* — justify it on its own merits
(truncation is a band-aid), not on the 485K number. Ties to hybrid-retrieval priority
(start BM25+embedding, SQLite adjacency, not Neo4j until thousands of nodes).
Input from docs refactor (2026-07-04): dev-recall (`correspondence.py`) turned out to be
pure FTS5/BM25 — no embeddings ever existed despite the old "sqlite-vec" docstring — and it
had silently indexed a pre-rename ghost clone for 7 weeks (fixed: pruned + full re-ingest).
Two lessons for this design: (a) the "hybrid" in hybrid retrieval is still 100% unbuilt,
BM25 alone is what we run on today; (b) any index needs a staleness/provenance check
(sources-on-disk assertion) or it rots invisibly. `lat.md/` + `lat_inject.py` fate also
folds into this decision (see docs/INDEX.md note).

Fresh evidence + decree (2026-07-29, token-cap decree — see GOAL_BRAIN): run
ba58f96c (third self-diagnosis dispatch) step 3 asked one agentic call to
digest a 292KB session transcript and got liveness-killed at 469s; steps 1–2
had already burned 1.7M tokens re-reading prior-run jsonls. Jeremy: "stop
trying to manage those [token caps] up front and start trying to be more
clever about what we pull in from a large doc." That is THIS item: a
retrieval handle over large artifacts (read the slice you need, not the file)
is the fix; the cap-side half (uniform runaway ceiling, no per-call magic
numbers) already shipped in the same-day cap-and-kill-reason chunk.
Accepted residual (Jeremy, same day): the 16000 ceiling is itself a magic
number — "fixing magic numbers with magic numbers … we will likely still
have to revisit that later." Revisit lands here: when the retrieval handle
exists, the right ceiling question becomes measurable (observed no_tools
output distribution) instead of guessed.

### Design constraint: decay trust, never data


- [ ] **Design constraint, not a task: decay trust, never data.** Append-only
  evidence layer stays perfect (the computerization edge over human forgetting);
  only compiled-truth confidence decays. Crystallization Stages 4–5 must be
  demotable back to language form — world-change is the frequent trigger,
  model upgrades the rare one. Partially embodied: "Decay-by-invalidation v0"
  (`knowledge_lens.py`, 2026-06-11) decays Stage-5 rule *trust* on recorded
  contradictions without touching data — but `knowledge.py` demotion only goes
  Stage 5→Stage 4 (rules→skills), NOT back to language form (Stage 2/3). The
  language-form demotion path is the part still open. Input to the memory
  architecture decision.

### File-claim fabrication — residuals (v1 guards SHIPPED 2026-06-26, archived to BACKLOG_DONE)

The three shipped layers (FS-diff missing-artifact, inert-output AST,
execution-contradiction) moved to BACKLOG_DONE 2026-07-04; the guard lives in
`loop_execute.py` post-split (originally wired in agent_loop.py). Kept here:
the rejected design (a documented trap) and the deliberately-deferred shapes.

- **REJECTED: no-path-write layer.** Prototyped (write-ish words + empty diff +
  no path named) and reverted same day: it is **absence-based, not
  evidence-based** — an empty workspace diff does not prove fabrication
  (analysis/planning steps and out-of-workspace writes legitimately leave it
  empty). It false-positived on 4 real test completions in the full suite. A
  verifier that hallucinates is its own failure mode; the guard now only fires on
  positive evidence (a named-but-absent file, or an inert file vs a concrete
  output claim).

- [ ] **Remaining exec-fabrication shapes (deliberately deferred — false-positive
  risk).** Two cases the v1 contradiction check intentionally does NOT flag,
  because each can fire on legitimate runs (same lesson that killed the
  no-path-write layer): (a) **"claims execution but ran nothing"** — the per-step
  transcript can't see a prior step's legitimate run, so absence ≠ proof; (b)
  **partial** — some commands succeeded and a later/key one failed; telling the
  test command from setup needs intent modeling, and fix-then-succeed is
  legitimate. Revisit only with a sharper signal (e.g. matching the claimed test
  count against the real `tool_result`), not a looser gate.

### Step-skeleton parallelization — refinable contracts, reopen-as-normal (vision, 2026-07-28, Jeremy)

Jeremy, verbatim: "For maro I envision a point where we parallelize
chunks of work that are all step-shaped (rather than what I think is a
super linear path we are currently taking through everything). Router
maybe needs to be smarter in order to do that; there's a front loading
component there and a 'redo but with re-shaping' context there as well
when we find step dependencies and need to revisit/refine 'finished'
step work. Which feels like a different way to organize/approach a
sidequest really, so maybe there's something there."

**Re-affirmed 2026-08-22** while flipping milestone-DAG parallelism on:
*"I do feel as though some of our steps could be run concurrently…
that's more planner type work than it is step work directly, maybe we
can get into that later."* Still design input, deliberately sequenced
AFTER the milestone lane proves out — the planner emitting concurrency
structure (a step DAG) is this entry's build, and the milestone
scheduler now shipping is its smaller sibling and its evidence source.

Reading (agreed in-session): decompose emits a **contract skeleton** —
step stubs with broad-brush contracts that harden as work proceeds
(mirrors Jeremy's stub-then-spiral dev workflow) — and reopening a
"finished" step on a discovered dependency is a **normal move, not a
failure**. Hooks already shipped: §13b revisitable milestones (stop
verdicts carry evidence + reopen conditions), §13d side-quest DAG
proposal, goal ancestry, recursion decree (sub-goal spawning never
foreclosed). Belongs to the thread-architecture implementation arc —
design input, not a build item yet. Pairs with the Actionable Stack
"Open-thread structure" entry: same shape, built twice deliberately
(dev-lane prototype first, runtime second).

### DESIGN SPACE — Thread Architecture (2026-04-26 sketch; narrow navigator SHIPPED, full reframe unbuilt)

**Doc:** `docs/THREAD_ARCHITECTURE.md` (the sketch + decisions + open list)
**Conversation log:** `docs/conversations/2026-04-26-thread-architecture.md` (literal transcript)
(The `arch/thread-navigator` branch was merged to main via 131d629 and deleted — no separate branch anymore.)

The 1-shot-first DISCUSS item (formerly here) expanded into a full architectural sketch over a 7-turn planning conversation. Rather than just inverting the planning default, the conversation reframed the unit of orchestration to **thread**, with a per-turn `navigator → work → navigator` loop, navigator-selected personas, sub-thread fork/collate, build-folder-as-thread-residence, and crystallization (Stages 1–5) as the navigator's improvement path.

**Status 2026-07-04 — distinguish shipped from unbuilt.** The *narrow* navigator
is real and live: dispatch + blocked-step judge (`navigator_shadow.py`), per-thread
goal brain (`thread_brain.py`), escalate cutovers enacted on this box (MILESTONES
#1/#2), thread-brain maintenance closed (MILESTONES #3). The *full reframe* —
per-turn navigator→work→navigator loop, sub-thread fork/collate (gated on
MILESTONES #4 async fork join), navigator-selected personas per turn, thread as
the unit of orchestration — is NOT built. The 9 open decisions in the doc need
re-scoping against what shipped before any further implementation.

**1-shot-first** is preserved as one move-shape the navigator picks per turn (not the default; navigator decides whether to plan or execute). Existing planning scaffolding (`decomposition_too_broad`, mid-loop redecompose, scope-as-armor) probably shrinks but does not delete — Jeremy pushed back on aggressive deletion (Tesla-vs-driver: confident-sounding LLM ideas without critical-thinking-edges drift, because people's context ≠ LLM context).

**Adjacent items that should be re-evaluated under this frame** (2026-07-04: two struck as shipped):
- Intent resolution (next entry) — folds into "fork+collate" sub-thread mechanism
- Captain's log infrastructure-vs-visibility (new) — should be demoted to data, not infrastructure
- ~~Persona auto-selection~~ — SHIPPED (`persona_for_goal`, c964d3b; wired in conductor + handle)
- ~~Recall() interface~~ — SHIPPED (`src/recall.py`, 9f1a43a)
- Crystallization Stage 5 (existing gap in `KNOWLEDGE_CRYSTALLIZATION.md`) — the navigator's cheaper-over-time mechanism
- Shared-learning portability (new) — self-learned artifacts should survive HDD loss / orchestrator switch

**Part 2 — Compound-thinking review (added 2026-07-20, Jeremy).** Re-review the
thread/navigator design from the practical question: does the system choose the
smallest useful shape of work, or does it turn a simple ask into a large
orchestration ritual? The target is not brute-force decomposition or a new
"reasoning model." It is an external cognitive architecture that can: (a) keep a
true one-shot as a one-shot; (b) split genuinely independent questions into
small, concurrent, independently verifiable children; (c) sequence only real
dependencies; and (d) preserve/merge the resulting evidence without losing the
user's context.

- **Review the current move set** (`execute`, `fork`, `collate`, `extend`,
  `close`, `escalate`) and identify the minimum decision inputs and output
  contracts needed to distinguish one-shot / fan-out / sequence / monitor.
  Do not add an always-plan phase or a generic task taxonomy.
- **Use organic acceptance cases**, including the Manti non-ethanol-gas ask,
  link-farm triage ("is this worth my time?"), a bounded code change
  (inspect → change → test → verify), and a genuinely parallel comparison.
  Compare the chosen shape against a direct one-shot baseline on usefulness,
  wall time, cost, and whether the final answer contains the requested result.
- **Concurrency is conditional, not aspirational:** fan out only when children
  have independent inputs/side effects and a defined collation artifact.
  Each child must leave inspectable evidence; a failed child returns its
  partial result plus its exact blocker, not a generic planner failure.
- **Keep the human context as a first-class input.** The work-shape decision
  must preserve stated outcome, constraints, prior thread artifacts, and user
  preferences; decomposition is a tool for reducing work, not a license to
  replace the user's intent with a planner's abstraction.
- **Bitter Lesson posture:** cite `docs/history/2026-03-30-bitter-lesson-analysis.md`
  as a historical design lens, but do not treat it as a deletion mandate. Its
  earlier suggestion that parallel fan-out/locks are merely "how" needs
  reconciliation with observed cases: lightweight harnessing, durable context,
  boundaries, and verifiable collation may increase usable model capability.
  The review should retain only mechanisms that beat the one-shot baseline on
  real tasks, and explicitly remove or demote mechanisms that do not.

**Deliverable:** a short decision memo that updates
`docs/THREAD_ARCHITECTURE.md` only after the organic-case review: retained work
shapes, rejected/removed ceremony, required evidence contracts, and the first
small implementation or measurement slice. No broad rewrite before that memo.

### Intent resolution — naming the "side-quests before decompose" shape (discovered 2026-04-18)

Run 7 of slycrel-go surfaced (again) that "done" means "the plan we guessed
up front got executed," not "the goal's artifact exists." The server was
built. The browser client wasn't — and the prompt explicitly said "browser
as a client." Closure missed it because closure checks against the plan's
deliverable list, and the plan's deliverable list was itself a 1-shot guess.

We keep writing pieces that nibble at this (`scope.py`, closure,
inversion, ralph, director-restart) and stopping there. The structural
phase missing is: **delay decomposition until intent-resolution
side-quests have settled the unknowns.** See
`docs/INTENT_RESOLUTION_DESIGN.md` for the full sketch + the minimum
experiment proposal.

**Partially shipped (2026-04-23, ResolvedIntent v0):** the deliverable-map
prompt + resolved-intent artifact schema subs moved to BACKLOG_DONE — shipped
as `scope.py` ResolvedIntent/Deliverable + `generate_resolved_intent()`,
persisting `resolved_intent.md`. Side-quest orchestration remains open.

- [x] **Minimum experiment — RESOLVED 2026-07-09 (Jeremy): accept v0 on
  organic evidence, retroactive A/B dropped.** The done-vs-achieved corpus
  analysis (1.0 arc) is the cheaper honest check on the closure ceiling.
  Full context in BACKLOG_DONE.
- [ ] **Pivot reuse across goal-family reruns.** (Narrowed 2026-07-04: the
  infrastructure half exists — per-project persistent dirs under
  `~/.maro/workspace/projects/<slug>/` are live and goal-slug-bound. What's
  missing is the *reuse* logic: a rerun/rephrase of the same goal family
  neither detects nor feeds prior side-quest artifacts back as context. The
  `polymarket-edges` ledger pattern (project_polymarket_edges.md memory) is
  the proof of value; generalize that.)
  **Deterministic project-family reuse shipped 2026-07-14:** every full AGENDA
  run now persists its resolved project in run metadata; dispatch/loop recall
  treats the same project as a family match even when the rephrase has low word
  overlap, and injects a bounded inventory of durable project/artifact paths so
  the planner can inspect and reuse prior side-quest products. Literal project
  names (for example `polymarket-edges`) already bind project-less dispatches
  through `_match_existing_project`. No LLM or embedding call was added.
  **Residual:** a rephrase that neither supplies nor names the old project still
  mints a new goal slug and cannot be safely joined semantically. Keep this item
  open for an evidence-backed family resolver; do not lower the 0.9 Jaccard
  threshold and risk unrelated-project context contamination.

### Modular refactoring (AFK-friendly chunks, queued 2026-04-18) — deferred chunks

Jeremy's framing: LLMs don't feel rework cost the way humans do, so our
codebase has accumulated seams that are hidden (not broken, just hostile
to the next edit). These chunks are sized so one session can ship one of
them cleanly without needing real-time direction. Pick any of them when
looking for an AFK-friendly chore. Principles in `docs/CODING_NOTES.md`.

- [ ] **Test clutter trim.** Jeremy's outside-in-testing posture
  applied to the suite: tests that poke private functions with mocked
  collaborators and assert call-shape are performative. Sweep tests
  touched during recent refactors and mark ones that would break on
  a rename-without-behavior-change — delete the clearest offenders,
  keep anything covering a module boundary or regression. Don't do
  a mass pass; trim opportunistically when editing neighboring code.
  (Tracked as a posture, not a standalone chunk.)

### Storage decision — sqlite indexer (deferred)

- [ ] **Storage decision (deferred).** JSONL captain's log is fine for within-run analysis. Sqlite *indexer* on top (not replacement) is the right pattern when cross-run queries become routine — "median treat-vs-control delta across N runs," "all CLOSURE_VERDICT < 0.5 in last 30 days." Defer until we have a concrete query we keep wanting.

### Step-to-goal elevation

- [ ] **Step-to-goal elevation.** When a step's elapsed time or token
  spend crosses a threshold, pause it, capture its state, respawn as a
  child goal with its own decompose/execute/verify loop, merge result
  back. Invasive (state handoff + result merge + parent-loop resumption);
  wait for heartbeat signal to tell us *which* steps actually need this
  before building.

### Phase 65 — Constraint/Premise Orchestration (MVE live here; deeper expansion deferred)

See `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`. **Current truth 2026-07-14:** the MVE shipped behind `scope_generation` (fresh-install default OFF because it adds an LLM call). The runtime box audited in July explicitly opted in and injected ResolvedIntent from 2026-07-09; this M1 dev host currently has no Maro config and therefore remains OFF. The 2026-04-22 six-run A/B's reliable signal was plan compression, and the old runtime control-only configuration was corrected 2026-07-09. `scope_ab_skip` remains only as an experiment control and should be unset in real configs. Items below are the still-open design questions for expansion beyond the single-persona MVE.

- [x] ~~BLOCKER: Autonomous-path behavior.~~ Resolved as shipped: no gate — scope output is logged and used as planner context, exactly the "log for post-hoc review, continue, no gate" default this blocker recommended.
- [x] ~~BLOCKER: A/B mechanism.~~ Resolved as a mechanism and actually run:
  the 2026-04-22 A/B used 3 inject + 3 control goals. Its reliable primary
  signal was plan compression (8 steps versus 15–40). The apparent 3/3 versus
  1/3 clean-run gap was confounded by recovery bugs in two long control plans,
  so closure-quality improvement remains under-tested and is not claimed here.
  `scope_ab_skip` survives only to reproduce a control arm.
- [x] ~~BLOCKER: Cost ceiling.~~ Resolved for the MVE by the per-run + daily budget gates shipped 2026-07-01 (`budget.per_run_usd` / `budget.daily_usd`). Reconsider a scope-specific sub-budget only if Phase 65 expands into multiple calls/personas; it is not a live blocker today.
- [ ] **Gate heuristic.** Design's "AGENDA goals above N words" is wrong (short goals often benefit most, long ones often don't). Needs an actual judgment signal — possibly complexity classifier, or "use for goals with ≥3 deliverables."
- [x] ~~Triad vs. single persona.~~ Resolved as shipped: single persona, per the review's recommendation. Triad remains unvalidated and should stay out unless ablation shows different constraint lines (next bullet).
- [ ] **Persona content vs. costumes.** Design assumes personas produce genuinely different perspectives. Current `persona.py` is largely system-prompt overrides + skeptic modifier. Validate that PM/engineer/architect personas *actually* draw different inversion lines (not just prompt flavor) before investing in triad.
- [ ] **Scope: verification sibling.** Design addresses the *planning* phase. Biggest defect in the system is in the *verification* phase — slycrel-go "passed" because nobody ran a browser. Constraint-setting alone won't close this gap. Needs sibling design for ground-truth verification (real browsers, real endpoints, real test execution — not LLM judgment).
- [ ] **Completion-standard coexistence.** Design says "completion standard is subsumed." Migration plan needed: does completion-standard still run during rollout? If both, do they contradict?
- [ ] **continuation_depth interaction.** Phase 64 restart carries ancestry context across boundaries. Constraints/premises must also be preserved (or explicitly refreshed) across restart. Design is silent.
- [ ] **Concurrent-loop interaction.** `team:` and DAG executor run parallel workers. Do they share the constraint set? Who catches cross-worker conflicts that individually-satisfy-but-together-violate? Unspecified. *2026-07-09 note: the concurrency-hardening arc (fail-closed file_lock, admission gate, worktree isolation) made parallel workers **file/git-safe** — this item is the remaining **semantic** layer (shared constraint set, cross-worker conflict detection) and is explicitly a follow-up, not covered by that arc.*

### Verifier synthesis as a deliverable (scope's other half)

**First real slice SHIPPED 2026-07-12 →
`docs/history/2026-07-12-routing-and-probe-synthesis-design.md` Part B** (Deliverable.shape,
shape-conditional behavioral-probe MUST with logged waiver, probe-env
hardening incl. the cwd=None residual; chunks B1–B3, see MILESTONES -5). The
full BDD red-green loop below stays deferred until the honest-measurement
prerequisites ship (now satisfied) — this entry remains the long-arc record.

Two residual gaps surfaced by adversarial-review pass 3 (2026-07-12, scoped
skeptic pass on the pass-2 fix commit `0621417`) — both judged real,
in-scope for the full BDD loop below, not for B1-B3, and documented in-code
rather than fixed with a fragile heuristic:
- [ ] **Waiver content isn't judged, only presence.** `behavioral_probe_waived`
  suppresses the B2 MUST (`closure_verify._detect_behavioral_gap` Signal 3)
  on ANY non-empty string — a pretextual waiver ("static compile proves it")
  bypasses it exactly as well as a genuine one. Needs an LLM judge (new
  verifier-LLM scope) or would otherwise require the external-taxonomy
  approach this function's own docstring says to avoid. See
  `closure_verify.py` Signal 3 comment. Pinned so this is testable-against,
  not just prose: `tests/test_director.py::TestDetectBehavioralGap::
  test_known_gap_pretextual_waiver_still_suppresses_signal3` — flip the
  assertion once waiver-content judging ships.
- [ ] **A "fail" outcome isn't checked for relevance, only cleanliness.**
  B3(b)'s confidence cap (narrowed in pass 2 to exempt any clean
  `outcome=="fail"` from capping) can't distinguish a real, meaningful
  failure from a brittle/irrelevant check the plan LLM wrote badly — both
  now uncap the same way. Mechanically irreducible with only
  pass/fail/inconclusive counts; needs a check-to-deliverable relevance
  signal that doesn't exist today. Accepted per an explicit asymmetric-cost
  argument (over-eager demotion costs one bounded `closure_restart`; a
  wrongly-suppressed real failure silently poisons `goal_achieved`) — see
  `closure_verify.py` B3(b) comment. Pinned: `tests/test_director.py::
  TestProbeEnvHardening::test_known_gap_irrelevant_fail_still_exempts_
  confidence_cap` — flip once a check-to-deliverable relevance signal
  exists.
- [ ] **Heuristic live-data regex misses named-place phrasing.**
  `_LIVE_DATA_RE` (no-LLM fallback path, `intent.py`) only catches
  current/latest/today wording; asks like "where can I get non-ethanol gas
  near Manti, Utah" still route NOW even though the LLM path correctly
  routes the same question AGENDA via `needs_live_data`
  (`test_llm_needs_live_data_forces_agenda`). Confirmed still-open by 3
  independent adversarial reviewers 2026-07-12; accepted as a deliberately
  narrow lexical approximation (design doc DECISION at
  `docs/history/2026-07-12-routing-and-probe-synthesis-design.md:70`), not
  a bug to chase. Pinned: `tests/test_intent.py::TestLiveDataOverride::
  test_known_gap_named_place_live_data_not_caught_by_heuristic`.

- [ ] **Verifier synthesis phase.** Dream-level: orchestrator builds its own verifier when none exists, rather than degrading to LLM judgment or failing as "hard." Framing: BDD + TDD. Scope declares Given/When/Then (what must be true for "done"). Execution includes a mandatory red-green pair: synthesize an executable probe, break the code on purpose to confirm it catches the failure, fix the code, probe goes green. The probe is a first-class checked-in artifact.

  **Needs additional scoping (Jeremy, 2026-07-12, agreed).** Three concrete
  residual-risk pin tests now point at this item (waiver content unjudged,
  fail-relevance unjudged, heuristic regex gap — see above) on top of the
  original slycrel-go motivating anecdote below. No longer just an
  open-ended "dream-level" aspiration; worth a real scoping pass (MVE
  sizing, which open question (a)-(d) below gates first) before treating
  it as a queueable chunk. Not started — noted, not yet scheduled.

  Motivation: slycrel-go "done" run (loop `bd9b581c`, 2026-04-16, 1.55M tokens, status=done) passed `go build` while nothing exercised the binary. Three real bugs (`atomicWrite` race, silent `os.Executable` error, ignored write errors) survived untouched — caught only by the follow-up `identify-and-fix-the-3` review run. Scope alone would have named the gap; a synthesized probe would have closed it.

  Replay result after Phase 65 + closure wiring: materially better, but still half-real. The replay refused to mark the branch done, yet the decisive catch was static: closure found hallucinated `xterm.js` claims in the work summary via repo inspection, not via booting the server or exercising the client. This is progress, but it exposes the remaining defect precisely.

  **Concrete defect: runtime-probe bias.** Closure-plan synthesis defaults to static/code-inspection probes (`grep`, `test -f`, source reads) even when the prompt explicitly permits live checks. In the slycrel replay all generated checks stayed static; none started the server, hit `/health`, opened a websocket, or drove browser/client behavior. The verifier is real enough to catch hallucinated code content, but still weak on unexercised runtime behavior.

  **Likely cause:** the current prompt rewards checks that are fast, safe, read-only, and self-cleaning, but does not provide cheap lifecycle scaffolding for runtime probes (boot ephemeral server, wait for readiness, hit endpoint, clean up). The LLM is taking the path of least resistance, not refusing in principle.

  **MVE:** one goal class ("build X that does Y") requires scope to declare ≥1 executable probe (shell script, curl+WS, Playwright spec). Step graph adds a mandatory "probe-fails-on-broken-code → probe-passes-on-fixed-code" pair. Compare outcome quality + regression rate vs checklist-complete path.

  **Implementation direction for the first real slice:**
  - add lightweight runtime-probe scaffolding examples to the closure plan prompt (boot in background, readiness wait, cleanup trap)
  - require at least one behavioral probe for runtime-delivering goals unless the planner explicitly explains why it is impossible in this environment
  - log probe modality for evals (`static`, `process`, `http`, `ws`, `browser`) so closure quality can be measured instead of guessed

  **Secondary issue:** probe brittleness/calibration. One replay check false-positive'd because the grep pattern for `RemoteAddr.*username` was stricter than the real log line. After runtime-probe bias, harden probe robustness so static checks do not become noisy theater.

  **Open questions:**
  (a) recursion — who verifies the verifier? Bounded version: the "break it on purpose" step IS the verifier-of-verifier.
  (b) which goal class first — probably build/implement missions, since research/report missions have softer success criteria.
  (c) interaction with completion-standard — does the probe subsume it, or both run?
  (d) cost ceiling — synthesizing + running a probe adds LLM calls and execution time; need per-goal budget.

  Related: BDD (Given/When/Then framing), TDD (red-green cycle), property-based testing (∀ operation, property holds), mutation testing (probe-of-probe bounded version). Sibling of Phase 65 "Scope: verification sibling" blocker above — this IS that sibling. **Cross-link:** also the sibling of the Actionable "Closure treats failed-to-run commands as checks-passed" item — runtime-probe bias is closure *choosing* static over behavioral probes; the closure-failed-to-run item is closure *mis-reading* the behavioral probes it does choose. Same root: the verdict is decoupled from whether the thing was verified.

  **Replay raw numbers** (evidence for the bias finding above): `~/.maro/workspace/projects/slycrel-replay/artifacts/summary.json` — `complete=False, confidence=0.35, 3/5 checks passed`. The two failing probes: (i) overly-strict grep for `!RemoteAddr.*username` false-positived on a legit log line `log.Printf(... username, r.RemoteAddr)`; (ii) `grep -qi xterm web/*` correctly caught that the work summary hallucinated xterm.js integration. The `_CLOSURE_PLAN_SYSTEM` prompt (now at `closure_verify.py:29`, imported by director.py post-split) says "Commands must be fast (<15s), safe (read-only or self-cleaning), exit 0 on success. Wrap background processes with `timeout` and always clean up PIDs" — permits live probes but nudges toward grep via path-of-least-resistance. (2026-07-04 status: the "implementation direction" modality-logging bullet above is SHIPPED — closure logs probe modality, and `_detect_behavioral_gap` / `_detect_diagnosis_gap` exist in closure_verify.py. The scaffolding-examples + required-behavioral-probe bullets and open questions (a)–(d) remain open.)

  **Second full run (2026-04-17, after observability fixes) — modality chart is stark.** CLOSURE_VERDICT event recorded `modality_distribution={"static": 4, "process": 1}`, zero http/ws/browser — on a goal explicitly about "headless server with browser as a client." Closure's own summary admits: *"Gap: runtime validation (server startup + browser connection) was not performed."* Yet it still returned `complete=True confidence=0.92`. Manual post-hoc runtime probe (3 curl calls, ~5 seconds): `/health → 200`, `/ → 200`, `/ws → 101 upgrade`, server logs `player "test" connected/disconnected`. The thing works; closure lucked into being right via static checks. The cheap, mechanical proof would have been three curls — and the system *had time*: the loop ran 810s / 3M tokens / 39 steps. Budget was not the constraint; scaffolding was.

  **Cross-cutting: adversarial review was the hallucinator on this run.** The loop's own adversarial review contested "Go not installed on this machine" and "headless-browser-client branch does not exist" — both false (Go 1.24.2 at `~/go/bin/go`, branch at `origin/headless-browser-client@4fdf0202`). Step output was substantially accurate; the review fabricated contradictions. Suggests the review path needs the same inversion-at-verification discipline: dispute a claim → run the probe that settles it. Currently reviews reason from priors without grounding.

### Composable decision-point hooks (design exploration)

- [ ] **Composable decision-point hooks** — (2026-07-04 correction: `step_events.py` was built, accumulated zero real handlers, and was PRUNED in the repo-wide refactor — see REFACTOR_PLAN. The live interception surfaces are inspector observation, quality gate, and prompt injection of standing rules/lessons/skills into decompose.) These aren't composable: you can't say "after decompose, before execution, run extra verification on steps 3 and 5." MTG-style stack where effects can be intercepted at targeted points. For now, prompt-stage injection is sufficient. Revisit when operational experience shows which decision points actually need interception. Key constraint: any self-extensibility must be human-gated (see evolver guardrail auto-apply fix).

### Phase Transition Contracts (architecture — revisit after operational data)

- [ ] **Formal stage contracts between pipeline phases** — Currently phase transitions are implicit: decompose outputs strings, execute takes strings, finalize takes outcomes. No typed contracts, no hard validation gates between phases. Pre-flight is advisory-only (loop proceeds regardless). Trajectory check is the first real mid-pipeline gate. Need: (1) typed output contracts per phase (not just "a list of strings" but "atomic steps that cover the goal scope"); (2) hard gates that re-plan or abort instead of proceeding with garbage input; (3) audit which existing checks are load-bearing vs noise. The Starship optimization: delete the advisory checks that never change behavior and replace with fewer, harder gates. Defer until operational data shows which gates actually matter.

### Phase 38 subpackage move

- [ ] **Phase 38 subpackage move** — src/ is flat, now at ~130 modules (was 49 when this was written). Successor plan: `docs/REFACTOR_PLAN.md` Tier 4 is this same move, sized against current reality. Deferred (33+ imports per group), revisit when it causes real problems.
  The `orch.py` legacy-trio removal rides this move (trio deprecated
  2026-07-09, stderr warnings + tripwire live; stub archived to
  BACKLOG_DONE 2026-07-27): remove `maro tick`/`loop`/`plan` and
  promote the path/NEXT.md layer as the real orchq/paths subsystem.

### Agentic verifier for large artifacts

- [ ] **Agentic verifier for large artifacts.** Today the validator sees a bounded
  in-context slice of the result (`validate.max_input_chars`; hosted-free uses
  its own `input_char_budget`). For multi-KB artifacts, stuffing the whole thing
  into context is wasteful — a tool-using verifier that reads the artifact
  selectively (grep/read a temp file) is the better pattern. Scope it as an
  opt-in verifier tier, not the default. (2026-07-21: local-model caveats
  removed with the local rung; the pattern itself stands.)

### Store/guard enforcement censuses — gated on a registration convention (swarm-review chunk 8, 2026-07-22)

Chunk 8 shipped the mechanical half: the DEFAULTS.md **reverse census**
(`test_every_documented_key_has_a_reader` — a documented flag nothing in
src/ reads fails the suite; wrapper reads and f-string-constructed keys
resolved by AST shape, zero hand-maintained exemptions). The other two
wiring-inventory checks CANNOT ship the same way yet, per the checkpoint:

- [ ] **(a) Stores census** — every store file (jsonl/md under the
  workspace) must name a live writer AND reader. Prerequisite: store
  paths declared through one helper/registry the census can walk
  (today they're ad-hoc `_x_path()` functions across modules — a census
  without the registry is a hand-maintained rot list).
  **Spec correction 2026-08-04: existence isn't the check.** The
  dynamic-guardrail store had a writer AND a reader AND passing tests on
  both, and had never loaded a row in its life. An existence census calls
  that healthy. The check that catches it is *counting* — rows on disk vs
  rows the loader hands back. Shipped as a diagnostic ahead of the
  registry: `scripts/store_roundtrip.py` (its record is archived in
  docs/history/backlog-done-2026-04-to-08-p3.md §2026-08-05; the wire-into-health-lane trigger is kept in
  the 2026-08-04 shipped-finds stub above). The registry still
  belongs here; when it lands, it replaces that script's hand-written
  probe table rather than adding a second thing to maintain.
- [ ] **(c) Guards census** — every installed guard must be probed as
  *firing*, test_git_guard-style (installed/runtime state + a
  production-data-shape pin). Prerequisite: a guard manifest consumed
  by BOTH the installer and the census. Lesson pinned in the plan: the
  impact scanner passed its tests and never fired — "a test exists"
  proves nothing about a guard.

When either registry ships, the census follows in the same chunk
(consumer-first: convention and enforcer land together).

### Coherence signal upgrade (chunk-9 item 2, later half — decided 2026-07-27)

Now-lane (decided, ships with chunk-9 wiring): the coherence question
("does the assembled path still tell the original story?") rides the
existing closure/navigator judgment seams — a question added to seams
that already run, not a new subsystem. This item is the later half:
revisit with live data, confirm the seam-riding version actually fires
(or visibly misses), and only then upgrade lost-the-plot to its own
tracked signal with a readout. Consumer-first both times: the upgrade
needs evidence of misses, not vibes. Decision 704420c7; design
§6/§10 "Coherence vs done-means" (COMPOUND_THINKING_DESIGN.md).

### §9.3 declare-blocked — named v1 cuts (2026-07-29)

The §9.3 structural declare-blocked v1 shipped at the closure-restart
boundary (evaluate_closure `prior_verdict` fingerprint comparison; see
COMPOUND_THINKING_DESIGN 2026-07-29 addendum). Post-land adversarial
review (docs/history/2026-07-29-declare-blocked-adversarial-review.md):
CONTESTED → remediated same session, 2/2 findings verified real —
status honesty now rides the declaration (a declared-blocked "done"
demotes to incomplete regardless of the 0.7 confidence bar), and
fingerprint material is now command+exit+output-slice signatures, so
broad commands failing differently no longer false-stall. Extensions
cut by name, consumer-first when their evidence arrives:

1. **Main-gate prior-verdict join.** The main closure gate at
   continuation_depth>0 has no baseline in hand — declining an
   evidence-free restart BEFORE it fires (rather than declaring blocked
   after) needs a persistence join. **Join material now exists
   (2026-07-29 persist-the-artifacts chunk, Jeremy's "fix the lineage"
   call):** metadata.json `loops[]` carries loop_reason/parent_loop_id/
   continuation_depth per loop, and build/closure_verdicts.jsonl
   carries every verdict's failed-check signatures + fingerprint in
   order — the join is now a local read of the SAME run dir. Zero
   depth≥2 restarts exist live today — build when the restart-boundary
   declaration's log line (or offline analysis of the persisted
   fingerprints) shows the case occurring.
2. **Redecompose plan-fingerprint convergence** (loop_blocked). The
   `_REDECOMPOSE_THRESHOLD = 2` cap is budget-shaped; the structural
   twin is: fingerprint successive decompositions and stop
   re-decomposing when the PLANS stop changing, not when the counter
   hits 2. Same shape as §9.3, one seam over.
3. **Fingerprint coarsening — DECLINED (Jeremy 2026-07-29: "fix the
   lineage, not the wording").** Live recon: in both fully-recoverable
   historical restart pairs, closure checks were LLM-regenerated with
   different wording — identity matching missed them. The
   false-POSITIVE half was fixed in the 2026-07-29 review remediation
   (signatures now carry failure output, so broad commands failing
   differently no longer collide). The loosening direction (fuzzy
   wording/artifact matching) is declined in favor of persistence:
   build/closure_verdicts.jsonl now keeps every attempt's fingerprint
   and per-check evidence, so miss-rate is measurable offline instead
   of argued about. Reopen only with that measurement in hand.

### Tire-runs tangent — deferred findings (2026-07-27, Opus independent review)

From the three Poe-dispatched tire-research runs (record:
`docs/history/2026-07-27-tire-runs-examination.md`). The four
runtime fixes + closure-skip observability shipped same day; these are
the bigger design items that came out of the same evidence, deliberately
deferred rather than silently dropped:

- **Token-lean fetch for subprocess workers + mid-step token
  brake.** Both halves SHIPPED 2026-07-27; two adversarial-review
  rounds remediated at merge. Build/verification trail archived:
  BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".

  **Still open:**
  - **Read tool has no per-call cap knob.** Bash-cap equivalent doesn't
    exist CLI-side; if the CLI ever grows one, wire it into
    `_bash_output_cap_env`'s sibling.
  - **Codex lane has no brake.** `CodexCLIAdapter` streams a different
    event shape; the token brake and Bash cap are claude-lane only.
  - **Container-mode CLI resolution — UNVERIFIED.** `maro-fetch` is now
    a console entry point and `_fetch_cli_path` prefers it, but nothing
    proves it resolves inside the executor image; the host-path fallback
    certainly does not. Needs a real-docker E2E that runs a research goal
    with `executor.container=on` and asserts the fetch command ran.
    Until then, container workers may still fall back to curl.
  - **`skill_loader` progressive disclosure is not wired.** `load_full()`
    has exactly one production caller (`scope.py`, hardcoded to
    `resolve_ambiguity`); the step executor never calls it, so a matched
    skill's BODY never reaches the worker — only the summary reaches the
    planner. **Docstring corrected 2026-07-28** to mark step 2 PARTIALLY
    WIRED and state the consequence (skill-body prose is documentation,
    not instruction; anything that must bind worker behavior belongs in
    EXECUTE_SYSTEM). Wiring `load_full()` at the executor is still open —
    the "stop implying it" half is done, the "wire it" half is not.
- **Success accounting vs answer quality.** `stuck → failed`
  (`run_curation.py`) even when partial_rescue holds contract-meeting
  deliverables — run 3 recorded "failed" with 2 of 3 tiers purchase-ready,
  and the rescue summary surfaced only one. The Opus review's headline:
  execution got monotonically better across the three runs while the
  recorded verdict got monotonically worse. Also gates learning:
  goal_achieved=False skips lesson crystallization, so the system's best
  run taught it nothing. Belongs to the stop-verdict/compound-thinking
  agenda (chunk 9), not a quick fix.
  *Chunk-9 #4 update (2026-07-27):* the typed stop verdict now rides
  beside status — a cap-stuck run carries `out-of-budget` with evidence,
  so consumers can tell it from a goal-failure stuck (and the four
  raw-status consumers were rewired to honor it). REMAINING: the
  success_class itself still maps cap-stuck → "failed"; rebucketing
  cap-stuck-with-deliverables (e.g. to partial, or closure-on-stop
  judging the deliverable) is a separate judged decision — the tire
  run 4 needle doc (2026-07-27) is the live specimen.
  *Star recon numbers (2026-07-27, chunk-9 #2 exercise —
  docs/history/2026-07-27-chunk9-2-recon-diagnosis-star.md):* the
  cap-stuck family is 9 of 726 runs in the live store (all
  `cost_budget=$2.00 + slush` endings; 2 more cap-shaped land as
  stranded/done); zero stamped stop_verdicts pre-date the fc93dfa rail,
  so any legacy rebucket needs a stuck_reason-derived fallback (the cap
  evidence lives in `build/loop-*-log.json`, NOT metadata — heavier
  read). Forward runs carry the verdict; the open call is vocabulary
  (new class vs verdict-aware readers).
- **Trust-aware success classification (design question, per-step-learning
  review 2026-07-27, Architect finding — rejected as a chunk defect,
  real as architecture).** `classify_outcome` and `is_learnable_outcome`
  consume raw `goal_achieved` without consulting `verdict_trust()` — ALL
  verdict branches, not just the new achieved-not-done ones (done+True →
  "success" is equally trust-blind). A DIRECTIONAL (confidence < 0.7)
  judged verdict can therefore set a gating success_class, while the
  seams with teeth (V2 cadence windows, contradiction emitter's era-10
  single-gate law, evolver scans) correctly filter on trust. Question:
  should curation-time classification apply the same law (e.g. a
  directional verdict classifies as if unjudged), or is label-layer
  trust-blindness fine because every learning consumer re-checks? If
  changed, it's a consumer census across all verdict branches at once —
  rows carry `goal_verdict_confidence`, so the data is already there.
- **Closure fail-open posture.** With skip_detail now recorded, the
  remaining question is design: should closure return complete=True when
  the goal carries an explicit output contract and verification never
  ran? Ties into the stop-path survey's fail-open conflations.
- **Stop-verdict deliberate cuts (chunk-9 #4, 2026-07-27).** Seams left
  unstamped, each a scoped follow-up, none load-bearing for the
  choke-point accounting that shipped
  (docs/history/2026-07-27-stop-verdict-split.md):
  - *Parallel/fan-out lane* — `loop_parallel` builds its LoopResult
    outside `_build_result_and_finalize`; a branch-level stop verdict
    needs an aggregation rule (which branch's ending IS the run's?).
  - *Navigator escalate* — who-decides-next, not a stop; stamp only if
    a navigator decision ever terminates a run directly.
  - *DirectorResult/WorkerResult + build_loop_runner vocabularies* —
    separate status vocabularies with their own consumers
    (handle_queue.py drain, director review loop); unify or bridge
    when those consumers need typed endings.
- **Paused-state upgrade edges (§13e slice 1 shipped 2026-07-31, decree
  7afe8b3a).** Typed pause reasons landed (stop_verdicts.py PAUSE_*
  vocabulary, stamp_pause rail, writer sites, run-card forwarding). The
  honest-good-enough boundary, each edge independently upgradeable:
  - *Reserved-reason stamp sites* — `llm-unreachable`, `no-tokens`,
    `disk-full` exist in the vocabulary with NO writer yet. Natural seams:
    the adapter failover ladder's terminal failure (llm-unreachable), the
    budget breaker (no-tokens), an ENOSPC catch at artifact-write sites
    (disk-full). Consumer-first is satisfied (run cards already forward);
    each stamp is a small scoped add.
  - *Live pause/resume lifecycle* — today "paused" is observed provenance
    on runs that already stopped; there is no commanded `paused` status a
    running loop enters and resumes from. If/when built, the resume seam
    is `_find_resumable_runs` (stranded already resumes manually).
  - *Sheriff naming collision* — project-level `.maro-paused` marker
    (sheriff.py → orch_items status "paused") is the same word with a
    different lifecycle (project intake gate vs run state). Unify the
    vocabulary or rename one when the run-level state goes commanded.
  - *Untyped interrupt edges* — loop_finalize merge-failure paths
    (~:358-401) and the agent_loop fence path set external-interrupt
    without a pause reason; type them if their frequency ever matters
    (census: they're rare).
  - *Residual raw-status reads* — strategy_evaluator/attribution are
    interrupt-aware but still consume raw status on non-interrupt rows;
    pause_family gives them a cleaner join when next touched.
  - *Stranded outcome-ledger gap* (slice-1 review #5) — the sweep stamps
    metadata only; a writer that died mid-finalize leaves an untyped (or
    absent) outcome row. A ledger back-stamp wants the loop_id join the
    LT arc is repairing; card provenance is already correct via metadata.
  - *Legacy cards untyped* (slice-1 review #6) — run cards curated before
    slice 1 (census: 57 stranded, 24 clarification_needed) never get the
    fallback typing; `list_runs` returns stored cards verbatim.
    Recuration under a drifted card schema is riskier than the value —
    revisit only if a consumer needs typed history, denominators live in
    `output/provenance_census/`.
  - *Lifecycle predicates* (slice-1 review #8, architect) — when a
    commanded pause ships, expose `is_paused_status()`/reason-derivation
    from the pause domain instead of leaning on the INTERRUPT_STATUSES
    alias in consumers.
- **Fail-open judge-error edges (§13e slice 2 shipped 2026-07-31).**
  `judged: bool` markers landed on StepVerdict/ArtifactVerdict/
  QualityVerdict + honest thread-brain `verify-error:` lines + GATE_ERROR
  events + inspector unjudged-alignment caps. The deliberate cuts, each
  independently upgradeable:
  - *Learning writers are still judged-blind* — loop_post_step feeds
    `update_skill_utility(success=True)` / `record_variant_outcome` /
    `record_skill_outcome` off `passed` alone; an unjudged fail-open pass
    still earns skill credit (matches the verify-off baseline, so it's
    defensible — but now the `judged` bit is there to consume when we
    decide credit should require a judgment).
  - *Judged-denominator readout* — discretion_readout gate-family tables
    now have GATE_ERROR rows + `judged` in event context; a
    judged-vs-unjudged column would show how often the gate actually
    judges in production (census said: parse errors are real).
  - *`alignment_score_avg` mixes judged and display-default scores* —
    inspector report aggregates still average the 0.7 unjudged display
    value alongside real LLM scores; split or annotate when the report
    is next touched.
  - Third gate (artifact-check error → unjudged stamp) + the
    2026-08-06 readout-run findings and fixes archived:
    BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".
- **Recon-flavor upgrade edges (chunk-9 #2 runtime slice shipped
  2026-08-01, docs/history/2026-08-01-recon-flavor-runtime.md).** The
  `[recon: <decision>]` tag + map-edit execution contract + map-change
  verification question landed; the honest-good-enough boundary, each
  edge independently upgradeable:
  - *Structured map_edits return* — recon results are shaped by prompt
    only; a `map_edits` field on complete_step (the chunk-3 `decisions`
    pattern: validated, journaled, carried uncompressed) is the upgrade
    when a consumer that reads structured map edits exists (reassess
    seam, or the minimal map schema §12 nudge 4 says must emerge by
    subtraction — never build the store first).
  - *VOI hard-gate* — bare `[recon]` keeps its flavor with voi ""
    (demoting it would hand the step the WRONG verification question);
    outcome rows carry the denominator (`flavor` without
    `recon_decision`). Gate at parse time only if live data shows
    ritual-exploration recon surviving verification.
  - *Probe-armed recon verification* — the recon verify question demands
    claims name what settles them, but the verifier doesn't RUN probes;
    wiring claim_probe (chunk-5b read-only allowlist machinery) into
    recon verification is its own slice.
  - *Parallel/fan-out lanes skip step verification entirely* —
    pre-existing lane property for ALL steps ("Ralph verify is not run
    in parallel mode — session-level state"), which recon inherits: a
    tagged step in a fan-out lane gets the map-edit execution contract
    but no map-change verification. Upgrade rides whatever fixes
    parallel-lane verification generally, not a recon-specific patch.
  - *Recon flavor-carry through milestone expansion* — the expansion
    exemption applies to EVERY recon-tagged step, not just cuts probes
    (2026-08-01 adversarial review, Architect+Minimalist): a broad
    prompt-emitted recon step flagged as a milestone now runs as one
    oversized execution. Deliberate for now — expanding a recon step
    sub-decomposes it into a commit-shaped sub-plan, destroying the VOI
    question; oversized recon is an emitter-contract violation the recon
    JUDGE + readout blocked/other buckets surface. Upgrade path if the
    corpus shows broad recon in practice: carry flavor+VOI through
    expansion instead of exempting. Related deferral: loop-log writes
    are plain `write_text` (loop_artifacts.py:233) — racing readers see
    partial JSON as a visible `files_failed` count; make atomic only if
    live readouts show it persistently nonzero.
- **Persona auto-selection misroute.** Run 3 routed to creative-director
  (conf 0.8) for a spec/pricing research task. Harmless here; worth a
  look if it recurs on research-shaped goals.

### Swarm-review chunk-1 batch adds (2026-07-21)

Recorded in one pass per the checkpoint decree ("add all BACKLOG entries
in chunk 1") so every deferred item from the knowledge journey, the
plan-revision review, the Phase 0.5 battery, and the wiring inventory is
a deliberate drop with a paper trail — not a silent one.

**Checkpoint revival dispositions (Jeremy 2026-07-21, review-confirmed
BACKLOG; era refs in `docs/KNOWLEDGE_JOURNEY.md` + era files):**

- [ ] Also-After hooks — structural attachment point for post-goal review (era 01)
- [ ] RISKS.md as reviewer input — swarms shouldn't re-flag accepted risks (era 00)
- [ ] Decision-gated-ping escalation shape (era 00)
- [ ] Blind persona-panel tiebreaker for contested verdicts (era 09; designed, never productized)
- [ ] Signal-source rotation, runtime half — navigator stuck-step move (era 05; distinct from chunk-5 review lenses)
- [ ] Exception-vs-break lifecycle — justified-exception events (era 04; waits on a consumer per consumer-first)
- [ ] REASSESS full 7-question overlay (era 06)
- [ ] Recurring doc-grounding census cadence (era 06)
- [ ] Promotion-side yield starvation (era 03)
- [ ] Periodic hand-adjudicated burn-in ritual — impossible-control goals, verdicts vs artifacts on disk (era 08; the only method that caught verdict-layer bugs automated metrics blessed)

**Battery side-finds V3/V4: both SHIPPED — moved to BACKLOG_DONE.md
2026-08-02 (V3 promote path shipped that day; V4 shipped 2026-07-29).**

*(V3's lesson-side twin ship record and the era-file C-tier drop
decisions archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16";
the twin also has its own earlier BACKLOG_DONE record.)*

**Wiring-inventory surprises (2026-07-21 agent-reported; **VERIFIED
2026-07-29** — verify-before-fix pass adjudicated all 8 against the
tree: 7 CONFIRMED, 1 mischaracterized. Full docket with current
line numbers + smallest consumer-first next move per row:
`docs/history/2026-07-29-wiring-claims-verification.md`. Items stay
open — verification ≠ repair; each needs a wire-or-retire decision):**

- [ ] `task_ledger.jsonl` WRITE-ORPHAN — **VERIFIED**: writer live (loop_execute.py:1377), `load_task_ledger` has zero runtime callers (def + re-export + tests only)
- [ ] Outcome-compression pipeline DEAD at runtime — **VERIFIED**, with nuance: fully unit-tested (test_memory.py:527-705), zero runtime callers — the "a test exists proves nothing" case; outcomes.jsonl grows unbounded
- [ ] `knowledge_edges.jsonl` dead both ends — **VERIFIED**: writer gated on `skills_used` (knowledge_bridge.py:397-400) which both live call sites (memory.py:717/:881) omit; `build_wiki_link_edges` + `load_knowledge_edges` zero callers
- [x] `times_applied += 1` in-memory only — **VERIFIED** at knowledge_web.py:1787 (`inject_knowledge_for_goal`); never written back — **SHIPPED 2026-07-29** (receipt write-back thread): `_bump_node_times_applied` locked raw-dict rewrite (unknown keys survive — data retention) called for rendered nodes only; pinned incl. unrendered-node-no-receipt + future-key survival; live-verified accruing. Bonus: the same thread found and fixed the BIGGER twin — the recall LOOP slice (the live main-loop lesson surface) bypassed `_increment_times_applied` entirely, so all 338 lessons.jsonl rows and every tiered row sat at times_applied=0 while the render promised "applied Nx" receipts; now rendered-only lessons accrue (same law as citations), live-verified in medium+long tiers, canon-candidate pathway (CANON_APPLY_THRESHOLD=10) reachable for the first time.
- [ ] Persona template memory seam dormant — **VERIFIED + worse**: persona.py:412 imports nonexistent `intent.classify_intent` → always excepts → task_type permanently "general"; AND no persona file uses the template vars
- [ ] `hypotheses.jsonl` no runtime injection reader — **VERIFIED** (pack.py CLI only; other hits are prose/docstrings); internal confirm→promote loop intact — arguably working as designed
- [x] Verification calibration cluster dead — **VERIFIED + extended**: one copy (knowledge_lens.py:1264/1349/1383), zero runtime callers of writer OR consumers; only tests touch it → Phase 60 "DONE" shipped symbols+tests without the runtime wiring. **CLOSED 2026-08-08 — removed by `e425f6f`** (your call on live-writer census survivor 1, decision `1addc859`): `record_verification`'s claimed caller never existed and `calibrated_alignment_threshold` had zero `src/` callers. Verified on the tree today — the cluster is gone from `knowledge_lens.py`, leaving only the removal note at the site ("resurrect from git only WITH a live writer and a live consumer wired in the same change"). `verification_outcomes.jsonl` kept per data retention; three test classes removed with it.
- [x] `RULE_GRADUATED` "second phantom event" — **MISCHARACTERIZED, closed as record-fix**: emitter exists and works (`maro-knowledge graduate` → rules.py:271-273), just zero live firings ever; right in practice, wrong in mechanism — nothing to build (docket row 8)
- [ ] Director/dispatch context omits the playbook (wiring row 17) — CONFIRMED structurally 2026-07-21 (chunk 2): `RecallResult.as_context_block` renders only lessons/standing_rules/decisions/knowledge; playbook (+ graveyard, failure_notes, learning_activity) reach only the loop slice via `as_loop_block`. The playbook module docstring claimed director injection for months. Decide whether the director prompt should carry playbook wisdom (and which other substrates), then wire with a liveness test — consumer-first.
- [x] Record-mode never fires on single-backend boxes — CONFIRMED 2026-07-21, **SHIPPED 2026-07-29** (autonomous batch): `build_adapter(auto)` now always wraps in `FailoverAdapter`, len==1 included — the wrapper is the one seam carrying record-mode capture, the runaway-cost meter, and the utility-call cap warning, and the bare-adapter fast path left all three dark on subprocess-only boxes (`n_calls: 0` on every run despite record-mode default-ON; evidence: run c772366a-wily-badger). The `MARO_BACKEND` env override (still the auto path) wraps too. Pins inverted in test_llm.py (single-backend → wrapped, backend property forwards). Side-find fixed in the same commit: both `cross_ref.py` adapter fallbacks passed a model tier as the positional *backend* arg (`build_adapter("cheap")` → AssertionError → caught → silent empty-report/dry-run degrade every time the fallback path ran); now `model=` keyword, call-shape pinned in test_cross_ref.py.
- [ ] Ancestry write-side unification (recursion prerequisite) — thread_brain and ancestry.json are separate write paths; at fork time they must be one record or children inherit divergent truth. Named a fork-implementation prerequisite in the THREAD_ARCHITECTURE fork-contract note (2026-07-21, chunk 3); GOAL_BRAIN open thread. Deliberately NOT part of the swarm-review arc — queue for the thread-architecture implementation arc.

### LeAct acceptance filter for minted reasoning traces (2026-07-31, from dispatched run 4125f34e-azure-haven)

Δ-gate route to LONG shipped (delta_replay.py +
promote_lesson_by_effect, 2026-08-06/07), demotion + tombstones +
noise calibration (2026-08-08), competence-redundancy decay v1
(inert_lesson_by_effect, 2026-08-13). The doorless
get_canon_candidates threshold this section still called "next
chunk" ALSO shipped 2026-08-13 — see BACKLOG_DONE §"Doorless
canon threshold (V3's lesson-side twin)". Full amendment trail
archived: BACKLOG_DONE.md §"Moved from BACKLOG 2026-08-16".

**Still open:**
- §5 cuts stay cut: evolver/thinkback trace scoring, un-contest
  verb (also tracked in MILESTONES.md).
- v2 deferred candidates from the 2026-08-13 adversarial review:
  (a) census discovery is MEDIUM-only — already-LONG rows can't
  be measured for inertness, yet internalized LONG lessons are
  exactly the original steal's decay target; (b) replay arm
  construction appends absent lessons under the LONG header
  regardless of tier — a tier-aware arm builder would remove the
  residual prompt-shape confound.

### Standing test-goal menu (future ideas)

- [ ] **Polymarket behavioral test** — "Analyze 400M+ Polymarket trades to find behavioral patterns among top wallets — what do winners do differently?" (from hrundel75 link)
- [ ] **"Get Jeremy rich" prompt** — long-term, after trading patterns are validated and backtested. Baby steps.

### Conservative — verify before dropping

These four are kept (not deleted) this triage pending verification against current code/data.

- [ ] **done != achieved, confirmed on organic runs — and the gap is large.** (verify before dropping)
  First organic batch through the new goal-verdict metadata (2026-06-12, 5
  real goals): 4 came back `done` but only **1** had `goal_achieved=True`. The
  three done-but-not-achieved (health-report refresh, roadmap audit, weekly
  digest) all wrote a structurally-correct artifact the closure verdict judged
  as falling short — "file created and non-empty" / "5/6 checks" — at low
  confidence (0.2–0.35). Two implications: (1) the done≠successful split is
  doing exactly its job — without it this batch reads as 80% success; with it,
  20% genuinely achieved, the rest flagged for review. Validates Jeremy's
  "done as 'I did it' not 'it worked'" concern with live data. (2) The verdict
  confidences are *low* — these are doubt flags, not definitive failures, and
  they correctly stay `done` (below the 0.7 demotion threshold) rather than
  flipping to incomplete. Open question worth watching: is the closure verifier
  systematically harsh on build-artifact goals (false-negative achievement), or
  are these outputs genuinely thin? Needs a few more organic batches + spot
  audits before trusting the rate. Don't tune the threshold on n=5.
  **Update 2026-07-04: the data now exists** — ~68 judged runs with verdict
  metadata on disk (~26 achieved). The gate is analysis, not data: re-run the
  done-vs-achieved rate check on the full corpus before touching thresholds.
  **ANALYSIS RUN 2026-07-09** (`docs/history/2026-07-09-done-vs-achieved.md`,
  1.0 item (b)): 72 verdict runs, era-segmented at 90b4d1b (55 poisoned
  excluded). Clean era n=17: done 65%, achieved 53%; organic slice n=10:
  raw achieved 40%, corrected ~60-70% after spot-audit (2 of 4
  done-but-not-achieved were verifier false negatives: verbatim-grep on
  paraphrase tasks, wrong-section grep on append-only ledgers; +1
  false-on-its-evidence at conf 0.95 via probe-env mismatch). Closure IS
  systematically harsh on build-artifact goals, but errs safe: zero false
  blessings post-fix, all false negatives below the 0.7 demotion threshold.
  **Verdict: keep 0.7; 1.0's gap is packaging, not closure quality.** Fix
  lever = probe-env hardening (cd to goal-named repo, cap confidence on
  environment-error signatures), not threshold tuning. Standing caveat: raw
  goal_achieved understates organic success ~20-30 points — don't feed it
  unadjusted into verify→learn; re-run at organic n≈30.
  **Prospective gate shipped 2026-07-14:** normal `maro handle` / `maro run`
  work now stamps `measurement_class=organic` into both run metadata and the
  durable outcome row; synthetic callers can explicitly select `smoke`,
  `control`, or `benchmark`, and dry runs are excluded. `handle_id` on the
  outcome collapses restarted loops to one run. Run
  `scripts/verdict-gap-stats.sh` (or `--json`) to see the n≈30 gate. It counts
  only judged, explicitly-organic rows; missing/legacy labels remain unknown
  and raw `goal_achieved` is named as an uncorrected verdict rate. Current
  unified ledger: 3 unknown legacy rows, 0 prospective organic rows, gate not
  due. The old n=10 hand-classified slice is historical evidence, not silently
  carried into the new counter. Keep this item open only until the report says
  the manual artifact re-audit is due.
  **Tangential architecture finding (2026-07-14 adversarial review):**
  `handle_task`'s budget-ceiling `loop_continuation` lane still calls
  `run_agent_loop` directly, outside the normal run ownership + closure-verdict
  lifecycle. This change now carries the parent handle/class explicitly, clears
  stale ambient run context, and conservatively lets the newer continuation row
  make the top-level request organic-but-unjudged. That prevents metric
  contamination, but it also exposes the larger pre-existing gap: a successful
  multi-pass continuation never receives the terminal closure verdict that
  would let that request enter the judged cohort. Fix by giving continuation
  consumption the shared run/closure lifecycle (not by teaching this report to
  bless the earlier partial pass). This belongs with the dedicated
  Verify→Learn/closure design arc; it is too large to smuggle into a stats
  report. **2026-07-29 rider (persist-the-artifacts review, minimalist):**
  the same `scoped_run_dir(None)` detachment also means queued continuations
  persist NO `loops[]` lineage and no closure_verdicts.jsonl — both new
  writers key off `current_run_dir()` and will start working here the moment
  this lane gets its run-dir lifecycle; nothing extra to build then.

- [~] **`decomposition_too_broad` residual.** (verify before dropping) The cache-aware conversion (2026-06-22) removed the observed noise source; remaining open question is whether a step doing genuinely >200K *fresh* tokens on an otherwise-successful run should warn at all, or only when the loop also shows stress (blocked steps / budget exhaustion). Revisit only if a real fresh-heavy run flags spuriously. (Full block archived to BACKLOG_DONE; this is the residual watch-item.)

- [ ] **Per-class routing (gathering shadow-eval data).** (verify before dropping — open children retained) Expect high agreement on
  verifiable code/math steps, low on fuzzy research-quality steps. Once the
  `--agreement` table has enough rows, route only the classes where the local judge
  earns it (per-class `min_certainty`); keep the rest on the paid path. Don't trust
  benchmark parity globally.
  **First data (2026-06-23, n=29, qwen2.5-coder:3b vs paid):** overall agreement
  96.6%, **0 false_pass across every class** (the dangerous direction — local PASS /
  paid FAIL — never happened). Per class: analyze 4/4, exec_command 4/4, synthesize
  3/3, read_artifact 1/1 all 100%; `general` 16/17 (94.1%) with the lone miss a
  **false_fail** (local FAIL@0.90 vs paid PASS on a routine file-save — local was
  *too strict*, costs a wasted escalation, not a missed defect). Surprise: the fuzzy
  synthesize/analyze essay-critique steps held at 100% — divergence showed up on a
  mundane `general` step, not the subjective work we expected to break it.
  Calibration: 0.9–1.0 bucket = 96.6% (slightly overconfident, erring strict).
  **Caveat: 29 rows is a smoke sample, not enough to set thresholds.** Next: a larger
  deliberate batch (more runs with diverse step mixes) before committing per-class
  `min_certainty` — and watch specifically for any `false_pass`, since that's the
  only error direction that can let a real defect through.
  **Larger batch (2026-06-24, n=42):** 92.9% overall, and the **first `false_pass`
  appeared** — `general` class, local PASS@**1.00** vs paid FAIL. The step was
  "list skills/ and save the listing to `artifacts/skills-listing.txt`"; the worker
  saved to a *different* path and narrated success. Local can't see the artifact
  never landed where asked — a requirement/side-effect miss, not a confidence
  problem (it fired at max confidence). Concrete classes held: exec_command 5/5,
  analyze 5/5, synthesize 3/3 — 100%, 0 false_pass; read_artifact 4 (75%, all misses
  false_fail/safe). **Decision: do NOT set per-class `min_certainty`.** (a) The
  safe-class n (3–5) is too small to justify lowering thresholds; (b) the danger
  class `general` can't be made safe by a threshold — the false_pass was at conf
  1.00. The lever the data actually points at is **provenance verification** (did
  the side effect land / was the requirement met?), which is the same root as the
  fabricated-input bug and is exactly the closure-verdict-provenance-net item above.
  So #3 feeds #2. Keep global `min_certainty: 0.6`; revisit per-class only after the
  safe-class corpus is much larger. Full write-up: `docs/LOCAL_VALIDATOR.md`.

### Run visibility residual — general-purpose server question (main entry → BACKLOG_DONE 2026-07-09)

- [ ] **Deferred: does a live server surface belong at all?** The static
  per-run report + cross-run index (`src/loop_report.py`) shipped and merged
  2026-07-09 — full history in BACKLOG_DONE.md "Run visibility: static
  per-run report + cross-run index". What that build deliberately excludes,
  and what the 2026-07-02 dashboard archive left open: a live (auth'd,
  read-only-by-default) server view, and whether goal-submission/replay
  controls ever belong in the same surface. Needs product discussion first;
  static files are the answer until cross-run browsing becomes a real habit.

### Install trial residual — $HOME haiku.txt watch-item

The 2026-07-09 docker clean-machine trial's blocker + residuals are all
shipped (full record archived to BACKLOG_DONE 2026-07-27). One
watch-item remains:

- [~] **E2E run left a second haiku.txt at `$HOME`** — investigated
  2026-07-09, unreproduced post-#16 (utility-call tool strip likely
  mooted it); detection hole by design (`detect_out_of_fence_access`
  scans absolute paths only). Watch: next docker/clean-machine trial
  must persist the workspace and grep FENCE/SCAVENGE rows before
  teardown.

### Initial-public-release — open remainders (shipped arc archived)

The (e)/(f)/(g)/(i) launch-content items are SHIPPED (full record
archived to BACKLOG_DONE 2026-07-27; decree quotes in GOAL_BRAIN
Decisions 2026-07-09). Sequencing constraint that survives: (g) needs
design sign-off before any public release. Still open:

- [ ] **(g) known-gap: filename scrubbing.** Artifact filenames /
  manifest `path` strings / REVIEW.md headings are not
  identifier-scrubbed — only artifact *content* passes
  `scrub_identifiers`; the human review gate is the only backstop. The
  correct fix is a filename-rewrite decision that also changes how
  `adopt()` derives live filenames from quarantined names — revisit on
  a real case, not speculatively.
- [~] **(h) auto-resume graduated shape — discussion wanted.** Manual
  resume + classify/message/checkpoint/sweep slices shipped; billing
  failover RATIFIED OFF 2026-07-16; the 1-auto-resume cap NOT ratified
  ("likely right decision is not binary") — graduated shape per
  error-class / cost-budget / per-goal consent, see
  `docs/BACKEND_RESILIENCE_DESIGN.md` annotations. The session-protocol
  arc raises its priority (a dead run behind a network edge is
  invisible — see SP entry).

---

Full history in [BACKLOG_DONE.md](BACKLOG_DONE.md).
