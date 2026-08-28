# Go-tree triage — mining the old port for the spiritual successor

*Written 2026-08-28 against `/home/clawd/claude/maro-wt-goport/go/` (read-only pass).
Companion to `planning/python-arch-map.md`. This is a HIGH-LEVEL bin sort, not a code
review: 1-3 lines per package, classifications name paths so they can be checked.
Method: per-package production line counts, package doc comments (the port's convention
is that every package doc names what it ports and what it deliberately does not),
PORT.md doctrine sections, and a grep-level emulation-import census.*

## Headline

- **MINE: 56 packages, ~74,300 production lines** (55 under `internal/` + `cmd/maro`)
- **EMULATION: 8 packages, ~8,842 production lines** (`pytext, pyval, pypath, pyos,
  pyjson, pyurl, pyargparse, pydifflib`)
- **TOOLING: 3 internal packages (~495 lines) + `tools/` (~2,280 py lines + batteries)**

The emulation layer is only ~10% of the mass — but its *imports* reach 56 of 57
non-emulation packages (see §5), so "strip the emulation" is not a delete, it is a
per-call-site substitution whose cost varies wildly by package. The good news: the two
heaviest habits (`pyval.Obj` ordered-dict records, `pytext.Strip/Lower` string parity)
are mostly mechanical to replace once the successor stops promising byte-identical
output and starts promising schema-compatible output.

Test mass is enormous (~135k lines of `_test.go`, roughly 1.8x production) and mostly
differential/parity tests against CPython — valuable as a *findings record* (PORT.md
indexes them), not as a suite to carry forward.

---

## 1. Size table (production lines, `_test.go` excluded)

| Package | Prod | Bin | | Package | Prod | Bin |
|---|---|---|---|---|---|---|
| internal/skills | 6582 | MINE | | internal/syshealth | 940 | MINE |
| internal/orch | 3971 | MINE | | internal/llm | 937 | MINE |
| internal/pyval | 3818 | EMU | | internal/pyargparse | 894 | EMU |
| internal/record | 3182 | MINE | | internal/graduation | 883 | MINE |
| internal/loopfinalize | 3009 | MINE | | internal/pathrewrite | 876 | MINE |
| internal/introspect | 2902 | MINE | | internal/guard | 759 | MINE |
| internal/loop | 2875 | MINE | | internal/pyurl | 724 | EMU |
| internal/pack | 2769 | MINE | | internal/worldfacts | 720 | MINE |
| internal/metrics | 2613 | MINE | | internal/dispatch | 667 | MINE |
| internal/evolver | 2287 | MINE | | internal/handlequeue | 629 | MINE |
| internal/agentloop | 2281 | MINE | | internal/runtrace | 628 | MINE |
| internal/artifactcheck | 2233 | MINE | | internal/recall | 557 | MINE |
| internal/director | 1997 | MINE | | internal/now | 474 | MINE |
| internal/pytext | 1945 | EMU | | internal/pypath | 473 | EMU |
| internal/worktree | 1864 | MINE | | internal/pyjson | 470 | EMU |
| internal/scans | 1848 | MINE | | internal/config | 460 | MINE |
| internal/persona | 1717 | MINE | | internal/heartbeat | 459 | MINE |
| internal/tasks | 1678 | MINE | | internal/pyprobe | 445 | TOOL |
| cmd/maro | 1439 | MINE | | internal/budget | 432 | MINE |
| internal/playbook | 1410 | MINE | | internal/pydifflib | 409 | EMU |
| internal/runs | 1407 | MINE | | internal/jsonx | 409 | MINE |
| internal/closure | 1370 | MINE | | internal/terrain | 402 | MINE |
| internal/receipts | 1236 | MINE | | internal/intent | 390 | MINE |
| internal/looptypes | 1163 | MINE | | internal/loopinit | 343 | MINE |
| internal/mintground | 1158 | MINE | | internal/workers | 343 | MINE |
| internal/claimverify | 1157 | MINE | | internal/stopverdicts | 263 | MINE |
| internal/inspector | 1108 | MINE | | internal/procid | 250 | MINE |
| internal/sheriff | 1106 | MINE | | internal/scrub | 221 | MINE |
| internal/loopparallel | 1089 | MINE | | internal/outcomepolicy | 160 | MINE |
| internal/preflight | 1050 | MINE | | internal/provenance | 152 | MINE |
| internal/scope | 1049 | MINE | | internal/planner | 128 | MINE |
| internal/knowledge | 1042 | MINE | | internal/selfimprove | 111 | MINE |
| internal/notify | 1036 | MINE | | internal/pyos | 109 | EMU |
| | | | | internal/missionrun | 97 | MINE |
| | | | | internal/testenv | 50 | TOOL |
| | | | | internal/portguard | 0 (test-only) | TOOL |

---

## 2. MINE — domain logic worth reusing (coupling: light = swap a few calls; heavy = shape dictated by Python parity)

**Kernel / platform (matches arch-map's de-facto kernel):**
- `internal/config` — two-tier YAML, one-level merge, env precedence, honors
  `MARO_WORKSPACE` and deliberately not `MARO_HOME`. Light coupling. Nearly done as-is.
- `internal/llm` — the adapter seam: subprocess `claude -p` backend (flag-for-flag,
  stream-json parse), Anthropic API backend, scripted `Fake`, `Options.Purpose` on every
  call. Light. This is the seam the arch map says to build first; it exists and is clean.
- `internal/record` — outcomes/captains-log writers, `file_lock` port (flock +
  atomic_write, torn-tail framing), rotation, the deliberately-unlocked events.jsonl
  PIPE_BUF lane. Heavy byte-compat *by design* — but that byte-compat IS the workspace
  contract the successor keeps, so most of the "heaviness" survives on purpose.
- `internal/runs` — run-dir create, metadata.json seed/merge, closure-verdict stamp.
  Moderate. The writer half of the contract recall reads.
- `internal/budget` — caps-as-structs with mandatory `Why`, `Clip` as the only
  shortening idiom. Light. Better than the Python (see §4).
- `internal/metrics` — pricing table, cache-aware cost estimation, step-type
  classifier. Light.
- `internal/jsonx` — tolerant JSON extraction from model output (llm_parse.extract_json).
  Light; needed under any regime that parses LLM responses.
- `internal/notify` — substrate hook + escalation context. Moderate.
- `internal/procid` — pidfile/process discipline. Light.
- `internal/worktree` — git-worktree isolation + scratch-clone lane. Moderate-heavy
  (10 files import emulation; much of that is path/logging parity, replaceable).

**Backbone chain (handle → intent → loop phases → step_exec → record → closure → runs):**
- `internal/intent` — NOW/AGENDA classify with heuristic fallback + capability
  overrides. Light.
- `internal/now` — the NOW lane incl. tri-state now-verify judge. Light.
- `internal/agentloop` — fan-out entry + spine; note `run_agent_loop` itself was NOT
  ported (the package doc says so out loud). Moderate.
- `internal/looptypes` — StepOutcome/LoopResult/LoopContext/phase state machine. Light.
  The typed core the arch map wants ("these semantics should be types").
- `internal/loopinit` — the budget gate (spend circuit-breaker, pre-token). Light.
- `internal/planner` — decompose (128 lines; operator docs ride whole). Light.
- `internal/preflight` — plan critic. Light-moderate.
- `internal/loopparallel` — parallel + DAG step schedulers, step body as `RunFn`.
  Moderate (Obj-based folds).
- `internal/loop` — execute + the blocked-step recovery ladder (zoom metacognition,
  split helpers, refinement hints). Moderate. This is the closest thing to step_exec.
- `internal/loopfinalize` — Phase G: LoopResult build, artifacts, crystallization,
  deferred learning. Heavy-ish (pyos error spelling, byte-shaped artifacts).
- `internal/closure` — inversion-planned probes run MECHANICALLY, LLM interprets.
  Light-moderate. The done≠achieved sign-off.
- `internal/recall` — the memory seam, read-only *stronger than Python* (no
  times_applied write-back). Light (pypath only).
- `internal/stopverdicts`, `internal/outcomepolicy` — typed vocabulary leaves. Light,
  cheap, keep.

**Memory / knowledge / learning:**
- `internal/skills` (biggest package) — skill store/retrieval/mint/failure-attribution
  with full byte-hygiene posture. Heavy (19 emulation-importing files) but the domain
  logic (promotion, scoring, attribution) is real.
- `internal/knowledge` — minimal Python-schema store surface (hypotheses + tiered
  lessons, asdict field parity, variant-union). Moderate; the schema is the contract.
- `internal/playbook` — "the most shape-sensitive file ported": shared markdown both
  runtimes append/dedup/expire. Heavy; under a Go-only successor the byte paranoia
  relaxes to schema fidelity.
- `internal/pack` + `internal/scrub` + `internal/provenance` — the portable-learning
  lifecycle, cross-runtime proven 2026-08-22 (Python→Go→Go→Python full circle, seals
  verified by receiver recomputation). Pack is heavy on canonical-JSON bytes, but the
  digest IS the interchange contract — keep it. provenance/scrub are light.
- `internal/mintground`, `internal/receipts` — mint-time grounding receipts + record-mode
  call files as closure-audit evidence. Moderate. Direct ports of decreed invariants.
- `internal/pathrewrite` — host-move path fixer for workspace data. Moderate.

**Quality / self-improvement:**
- `internal/inspector`, `internal/evolver`, `internal/scans`, `internal/graduation`,
  `internal/introspect`, `internal/selfimprove` — the whole self-improvement cadence,
  composed in `selfimprove` because Go made the import cycle explicit. introspect is
  moderate-heavy (15 emulation-importing files); the rest light-moderate. Reviewed to
  fixpoint 2026-08-23 (r1-r5); some of the best-audited code in the tree.
- `internal/syshealth` — probe-cycle state machine + snapshot renderer (the liveness
  decree). Moderate (Obj-heavy render).
- `internal/artifactcheck`, `internal/claimverify` — zero-LLM ground-truth checks
  (done-claims vs disk; cited files/symbols exist). artifactcheck went through 12
  parity review rounds on CPython filesystem semantics — the *idea* mines cleanly, the
  os.walk/sort parity shape does not. Moderate-heavy / moderate.
- `internal/guard` — prompt-injection scan for self-stored text before auto-apply.
  Light (pytext only).

**Interface / orchestration:**
- `internal/director` + `internal/workers` — plan/delegate/review + persona-framed
  workers with blocked_origin taxonomy. Moderate / light. (Arch map: consider splitting
  director into three roles; the Go code already separates closure into its own package.)
- `internal/dispatch` — typed dispatch envelope, two attachment lanes as a trust
  boundary. Moderate.
- `internal/persona` — persona compose w/ frontmatter, workspace-over-repo. Moderate.
- `internal/orch` — the project ledger (NEXT.md line-identity grammar, DECISIONS/RISKS).
  Heavy: markdown where identity is the line number; second-largest package. The
  contract is essential, the byte-level care partially relaxes Go-only.
- `internal/tasks` + `internal/handlequeue` — task store + enqueue seam. Moderate/light.
- `internal/heartbeat`, `internal/sheriff` — deterministic halves ported, orchestration
  halves explicitly NAMED not-ported. Light/moderate.
- `internal/terrain`, `internal/worldfacts`, `internal/runtrace`, `internal/scope` —
  run-scoped memories (blocked hosts, world facts, edge trace, scope prompts). Light
  (worldfacts uses pydifflib ratio — any similarity metric substitutes).
- `internal/missionrun` — 97-line cycle-breaking wiring; keep the *lesson* (design the
  DAG), not the package.
- `cmd/maro` — CLI wiring for run/pack/task/metrics/introspect/selfimprove. MINE for
  its command inventory; its argparse-mimicking flag order (`flagorder.go`) is
  emulation-shaped and should be rewritten with idiomatic flags.

## 3. EMULATION — stays behind as reference

- `internal/pyval` (3818) — Python value semantics: ordered-dict `Obj`, truthiness,
  repr, float repr, int ops, `json.dumps(indent=2)` writer. The single biggest
  coupling surface (889 `pyval.Obj` uses tree-wide).
- `internal/pytext` (1945) — str primitives: Strip/Lower/casefold, `\s`/`\w`/`\d`
  Python regex classes, fnmatch, surrogate-tolerant decode.
- `internal/pyargparse` (894) — CPython argparse `_parse_known_args`. Pure parity.
- `internal/pyurl` (724) — urlparse().hostname incl. exception parity.
- `internal/pypath` (473) — pathlib.PurePosixPath edge cases vs path/filepath.
- `internal/pyjson` (470) — CPython json byte shapes (ensure_ascii, separators).
  *Partial exception:* the canonical-dumps used by pack seals is an interchange
  contract, not emulation — if pack survives, that one function survives with it.
- `internal/pydifflib` (409) — SequenceMatcher.ratio(). Swap for any similarity.
- `internal/pyos` (109) — `[Errno N]` OSError text shape.

## 4. TOOLING

- `internal/pyprobe` (445) — the ONE shared CPython differential harness (consolidated
  from eight). Parity-specific; retire with the parity goal. Keep as reference for
  harness discipline (env/cwd hygiene decides whether a differential is a test of code
  or of the room).
- `internal/testenv` (50) — isolates test binaries from the operator's real machine
  (born from tests messaging Telegram via the live notify.command). **Keep under any
  regime.**
- `internal/portguard` (test-only) — guards that probes default away from the live
  workspace. Parity-era shape, but the "tests must not touch the live workspace"
  tripwire itself carries forward.
- `tools/engine-compare.py` + `tools/write-compare.py` — run both engines over a copied
  workspace, byte-diff outputs (the 2026-08-26 6/6-identical run). **The most valuable
  tooling in the tree for the successor**: exactly the workspace-boundary comparison a
  behavior-contract regime needs; relax byte-diff to schema-diff where the contract
  relaxes.
- `tools/mutate-*.py` + `tools/batteries/*.json` (~30 batteries) — mutation batteries.
  Parity-specific payloads; the *method* (must-detect mutations derived from the file,
  site-uniqueness asserts, ask-the-compiler) transfers per L50/feedback_mutation_from_file.
- `tools/port-status.py` → PORT_STATUS.md — generated denominator of "what claims to be
  ported". Parity-specific but useful once more during planning as the inventory of
  what exists; retires after.
- `crossrt_smoke.sh` — pack cross-runtime circle. Keep as long as pack interop with
  Python is a goal.

## 5. Cross-cutting emulation coupling (grep census, production files only)

Every MINE package except `internal/missionrun` imports at least one emulation package.
Files-importing-emulation per package (top): skills 19, introspect 15, record 13,
orch 13, persona 12, pack 11, worktree 10, loopfinalize 9, loop 8, runs 7, preflight 7.

By function, the coupling is three habits, not fifty:
- `pyval.Obj/List/Field` (889/177/34 uses) — records held as ordered dynamic maps to
  preserve foreign-file key order. In a Go-owned workspace, typed structs with fixed
  marshal order replace nearly all of it; keep `Obj` only where the successor rewrites
  files a *Python* writer may still own (playbook, orch ledger — if dual-runtime
  operation is kept at all).
- `pytext.Strip/SpaceClass/Lower/Repr/FoldI/Word*` (~700 uses) — Python whitespace/case
  semantics inside prompts, dedup keys, and regexes. Where the string never round-trips
  to a Python reader, `strings.TrimSpace`/`ToLower` substitute one line at a time.
- `pyval.Truthy/PyErr/NowISO/Clip/DumpsCompactPy` (~350 uses) — Python truthiness at
  config/JSON edges, error-text and timestamp shapes. Truthiness disappears with typed
  config; NowISO/timestamp shape should be *kept deliberately* (it's a workspace schema
  choice, cheap to standardize on).

Estimated strip cost: mechanical for ~2/3 of sites (light packages), real redesign for
the heavy five (skills, orch, playbook, record, pack) — and for three of those five the
"Python shape" is actually the shared-workspace contract the successor intends to honor,
so the decision is contract-scoping, not code translation.

## 6. Backbone mining shortlist (vs arch-map §2 chain + kernel)

1. `internal/llm` — the adapter seam + subprocess backend + Fake. Kernel item #1.
2. `internal/config` — kernel; near-drop-in.
3. `internal/record` — file_lock + outcomes + captains log + events lane. Kernel; the
   locking/atomicity discipline is the cross-engine contract itself.
4. `internal/runs` — run-dir lifecycle + verdict stamp. Kernel.
5. `internal/looptypes` — the typed StepOutcome/LoopResult/LoopContext core.
6. `internal/loop` + `internal/loopparallel` + `internal/loopinit` — execute, blocked
   ladder, schedulers, budget gate (the step_exec + phases segment).
7. `internal/closure` — probes-run-mechanically closure verdict (done≠achieved).
8. `internal/intent` + `internal/now` — the two-lane intake front of the chain.
9. `internal/loopfinalize` + `internal/recall` — the memory-record and memory-read ends
   of the loop's seam to memory.
10. `internal/budget` + `internal/jsonx` — small, load-bearing, already better than the
    Python; use as-is.

Notably absent from the tree: handle.py's orchestration itself (`_handle_impl`) and
`run_agent_loop`'s full body were never ported — the port left the god-function behind.
The successor gets to design that spine fresh, which the arch map wanted anyway.

## 7. Port-made improvements worth keeping (frank)

Sampled from PORT.md's doctrine table and package docs; these are places the port is
*better* than the Python and the successor should keep the design, not just the code:

1. **Budget rationale as a field** (`internal/budget`): `Budget{Name, Limit, Why}` with
   a package test failing on empty `Why`; `Clip` as the only truncation idiom. Replaces
   two Python AST-scanner tripwires with the type system.
2. **The resolved store is an argument** (`internal/record`: `record.New(workspaceDir)`;
   `cmd/maro` prints the resolved workspace before any write). Structural answer to the
   2026-08-16 live-ledger overwrite incident.
3. **`llm.Options.Purpose` mandatory at every call site** — the named-seams discipline
   as API shape, and the recorder rides the same seam.
4. **Explicit import DAG** — cycles surfaced as tiny composition packages
   (`internal/missionrun`, `internal/selfimprove`) instead of lazy in-function imports.
   Exactly the L48/P4 lesson made structural; the successor should start from this DAG.
5. **`internal/recall` read-only for real** — even Python's times_applied write-back is
   excluded; failures degrade to named "knows nothing" sources, never swallowed.
6. **`internal/testenv`** — test binaries structurally isolated from the operator's
   machine (born from a real incident). Adopt on day one.
7. **events.jsonl unlocked-by-design** (`record.AppendUnlockedLine`): field-capped rows
   under PIPE_BUF so a single O_APPEND write is atomic without recursing into the lock
   machinery that reports lock timeouts. Keep the reasoning and the cap.
8. **Errors returned or recorded, never swallowed** — the failed step carries its real
   reason into the failure chain, replacing Python's except-pass audit culture.

## 8. Recommended stance for the plan

Treat the tree as three strata: (a) the **kernel + backbone packages in §6** — mine
aggressively, most survive with light emulation-stripping; (b) the **heavy
shared-surface packages** (skills, orch, playbook, record's byte shapes, pack) — the
decision is *which workspace contracts the successor honors*, and each byte-parity
choice should be re-made as a schema choice; (c) the **emulation + parity tooling** —
leave in place as a reference quarry (PORT.md's findings ledger and the differential
tests document real CPython behaviors the contracts embed, e.g. NowISO shape, canonical
digest, `\s` vs `\S` provenance lane). Do not port the parity tests; port
engine-compare/write-compare's workspace-boundary method instead.
