// Package syshealth ports the portable core of Python system_health.py:
// the snapshot store, the probe-cycle state machine, and the renderer.
//
// The module exists for a decree (Jeremy, 2026-07-29): "we need a way to
// ensure the system itself is active and working, especially if we're going
// to allow it to modify itself." The defect class it catches is writers that
// fire with consumers that never run — subsystems wired end to end, green in
// the suite, and not executing in production. SILENT is a finding, not an
// error; nothing here changes runtime behaviour.
//
// WHAT IS NOT HERE, NAMED:
//
//   - The seven declared probes (_probe_run_ref_index,
//     _probe_skill_attribution, _probe_contradiction_lifecycle,
//     _probe_variant_ab, _probe_lesson_receipts, _probe_closure_verdicts,
//     _probe_container_auth). Each reads a DIFFERENT live store, so each is
//     its own tranche with its own fixtures. They arrive here as injected
//     Declarations, which is the seam that makes the state machine testable
//     at all — in Python they are a module-level list, and the cycle cannot
//     be exercised without monkey-patching it.
//   - _narrate_transition, a captains_log write. RunCycle RETURNS the
//     narrations it decided on instead of performing them, which is also
//     what the Python's own ordering demands: narrate only AFTER the
//     snapshot recording `narrated` has persisted, or a failed write leaves
//     the log claiming the user was told while the state machine re-narrates
//     forever. The reverse trade — write lands, log append fails, the line is
//     lost — is accepted there and inherited here: the snapshot still says
//     SILENT.
//   - The locked_write around the whole cycle. Python holds the snapshot's
//     lock across read-modify-write so concurrent finalizers serialise
//     instead of both reading narrated=None and double-narrating; that is
//     still the CALLER's job here, and r3 corrected the reason given for it.
//     It used to say "because RunCycle does not touch the disk at all",
//     which stopped being the point when r1 introduced RunAndPersist:
//     RunAndPersist DOES perform the load..write, so a caller reading that
//     sentence and concluding this package needs no lock would reproduce
//     exactly the double-narration race the Python comment
//     (system_health.py:539-543) exists to prevent. The rule is that the
//     lock must be held across the whole RunAndPersist CALL.
//   - `verbose`, which prints `[health] name: STATUS — evidence` per
//     declaration AS THE CYCLE RUNS. Nothing branches on it — it is the
//     --probe flag's progress output. A caller wanting it must print from
//     the DECLARATIONS as it walks them, not from the returned snapshot:
//     that map is in prior-insertion order rather than declaration order,
//     and it also holds processes this cycle never probed, still carrying
//     their stale status and evidence (fixture C26). An earlier draft of
//     this note said "print from the returned snapshot, in the same order",
//     which is wrong on both counts.
//   - main, the CLI.
//   - The module LOGGER, and it is an observable emission rather than an
//     absence. `logger = logging.getLogger(__name__)` (system_health.py:47)
//     and `logger.debug("health probe cycle failed (non-fatal): %s", exc)`
//     (:610) fire on EVERY error lane in the table on Summary, under the
//     logger name `system_health`, with a message text an operator greps.
//     This package has no logging seam at all, so a caller reproducing the
//     error lanes from this file would never learn that line exists. Found
//     by r4 (F5) — the list's entire value is completeness, and it was
//     silently missing three of its nine top-level omissions.
//   - The three shared probe HELPERS `_memory_dir`, `_recent_outcomes` and
//     `_run_source` (system_health.py:101-131). The first bullet says the
//     seven declared probes are each their own tranche; these sit BELOW
//     that line — module-level helpers the probes share — so no per-probe
//     tranche owns them and nothing above claimed them.
package syshealth

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The three statuses. SILENT means "wired but not observably executing" —
// deliberately not an error, and deliberately not OK.
const (
	OK      = "OK"
	Silent  = "SILENT"
	Unknown = "UNKNOWN"
)

const (
	// HistoryKeep is the per-process ring-buffer depth in the snapshot.
	HistoryKeep = 8
	// StreakForSilent is how many consecutive observations a conditional
	// expectation must hold before a cross-cycle probe may call SILENT: one
	// bad cycle is noise, a streak is a finding. Unused HERE — it is the
	// probes' constant — and exported anyway because the probe tranches land
	// later and this is the contract they read it from.
	StreakForSilent = 3
	// CandidateStarvationHours and VariantStaleDays are the same shape:
	// probe constants, ported now so seven later tranches do not each invent
	// their own copy.
	CandidateStarvationHours = 48
	VariantStaleDays         = 7
)

// Declaration is sheriff-style: a dynamic process declares what "alive"
// looks like and ships a cheap deterministic probe.
//
// Probe returns (status, evidence, observation) and MAY return an error.
// Python documents its probes as "never raises" and then shields every call
// anyway, which is the honest reading: the shield exists because the promise
// is not enforceable.
type Declaration struct {
	Name        string
	Description string
	Expectation string
	// Probe takes a POINTER because Python hands the probe the LIVE entry
	// out of `processes`, so anything it writes into prior is visible to
	// the `prev_status = prior.get("status")` and `entry = dict(prior)`
	// that follow. A pyval.Obj passed by value gets half of that right and
	// the halves are hard to tell apart: Set on an EXISTING key writes
	// through the shared backing array (propagates, faithful), while Set
	// on a NEW key appends to the local slice header and is silently lost.
	// r4 F7 — latent today because no shipped probe mutates prior, but it
	// is the seam-invented hazard class that
	// TestRunCycleDoesNotMutateTheProbesObservation covers on the OTHER
	// side of this same call, with nothing covering this side.
	//
	// The pin is TestAProbeWritingIntoPriorIsSeenByTheCycle, and it was
	// checked the only way that means anything: a copy of the tree with
	// BOTH this signature and its fixtures reverted to the value receiver,
	// where the new-key case fails and the existing-key case still passes.
	// A battery mutant that flips only this line does not compile — the
	// fixtures pass a pointer — and P14 says such a mutant is reported as
	// caught while proving nothing, which is why it was not left to one.
	Probe func(prior *pyval.Obj) (string, string, pyval.Obj, error)
}

// Narration is one transition the cycle decided to tell the user about.
// RunCycle returns these rather than logging them; see the package doc.
type Narration struct {
	Decl     Declaration
	Status   string
	Evidence string
}

// Summary is the cycle's return value.
//
// Skipped and Error are separate fields rather than one "why it stopped",
// because they are different events at different points. Skipped is the
// config gate, decided before any probe runs, so Ran is 0.
//
// Error is Python's blanket `except`, and it is WIDER than the cycle. It
// wraps FIVE things, and `Ran` differs across them — measured, because the
// first draft of this comment asserted "by the time it fires the probes
// have already run and Ran is already non-zero" and that is false for the
// first row. (It said FOUR through r3; see the third row.)
//
//	config.get raises          ran=0, transitions=0, nothing written
//	_snapshot_path mkdirs      ran=0, transitions=0, NO PROBE CALLED
//	locked_write times out     ran=0, transitions=0, NO PROBE CALLED
//	an unparseable cycle       ran=N, transitions=0, nothing written
//	_write_snapshot raises     ran=N, transitions=0, NOTHING NARRATED
//
// The THIRD row is r4 F1, and this table said "four things" for three
// rounds without it. `with locked_write(_snapshot_path())` is inside the
// try, and locked_write raises FileLockTimeout when the lock is contended
// and fail-open is off — which is the DEFAULT. Measured on 3.14.3 with the
// lock held by another process and MARO_FILELOCK_TIMEOUT_S=0:
//
//	{"ran": 0, "silent": [], "transitions": 0,
//	 "error": "file_lock: could not acquire <ws>/memory/system_health.json.lock
//	           within 0.0s (holder alive?). Set MARO_FILELOCK_FAIL_OPEN=1 ..."}
//	probes called: 0     snapshot written: no
//
// It is neither of its neighbours: memory/ is fine, so it is not the mkdir
// lane, and Ran is 0 rather than N, so it is not the write lane. This
// package DELEGATES the lock to its caller (see the package doc), which
// makes the omission worse rather than better — a caller built from this
// table takes the lock, gets a timeout, and has nothing telling it the
// faithful answer is ran=0 with a 200-code-point-clipped message and no
// narration. It is the case the lock exists for: two loop_finalize cycles
// racing on one box.
//
// A sixth thing is inside the try and cannot contribute a row: the
// narration loop. `_narrate_transition` wraps its ENTIRE body — the import
// included — in `try: ... except Exception: pass`, so nothing it does can
// reach this handler. An earlier version of this table listed it as a
// measured row ("ran=N, transitions already assigned"); r3 pointed out that
// no input produces it, which makes "measured" the wrong word for a lane
// that does not exist. It is named here so the next reader does not go
// looking for the fixture.
//
// The SECOND row is r3 F1 and it is the one a port is most likely to miss,
// because `config.memory_dir()` mkdirs as a side effect of resolving a path
// and `run_health_probes` resolves that path as the ARGUMENT to
// locked_write. A workspace whose memory/ cannot be created therefore fails
// before the load and before the loop — fixtures C48/C49.
//
// The FIFTH row is the one a split port drops on the floor: the probes ran,
// the cycle produced narrations, and the write failed — so `transitions`
// must be 0 and the pending narrations must be discarded, or the log claims
// the user was told about a state that never persisted. RunCycle cannot see
// that lane; RunAndPersist owns it, and is the reason it exists.
type Summary struct {
	Ran         int
	Silent      []string
	Transitions int
	Skipped     string

	// Error is a POINTER because absence and empty are different answers.
	// Python writes `summary["error"] = str(exc)[:200]` unconditionally
	// once the except fires, and `str(exc)` is "" for an exception raised
	// with no message — `RuntimeError()`. Measured through the real
	// run_health_probes on CPython 3.14.3:
	//
	//	{"ran": 0, "silent": [], "transitions": 0, "error": ""}
	//
	// A string field with "" as its sentinel drops that key, making an
	// aborted cycle indistinguishable from a clean zero-declaration one —
	// and any Go caller testing `sum.Error != ""` concludes the cycle
	// succeeded when it never ran a probe. Found in r4; every fixture in
	// the suite raised WITH a message, which is the side of the input
	// space where the two spellings agree.
	Error *string
}

// setError is `summary["error"] = str(exc)[:200]`: it sets the key even
// when the clipped message is empty.
func (s *Summary) setError(msg string) {
	clipped := pyval.Clip(msg, 200)
	s.Error = &clipped
}

// ToDict renders Summary in Python's INSERTION order: `ran`, `silent` and
// `transitions` are seeded at the top of the function so all three are
// present even on the paths that never finish, and `skipped` / `error` are
// added later on their own paths.
//
// The order is a real contract — any caller that json.dumps this without
// sort_keys writes it — and it is NOT checked by the differential, which
// routes the summary through a Go map and compares semantically. The pin is
// TestSummaryToDictKeepsCPythonsInsertionOrder, a direct string comparison,
// added after r1 found this doc and the battery's M6 label asserting
// opposite contracts with nothing checking either.
func (s Summary) ToDict() pyval.Obj {
	o := pyval.Obj{}
	o.Set("ran", s.Ran)
	sil := pyval.List{}
	for _, n := range s.Silent {
		sil = append(sil, n)
	}
	o.Set("silent", sil)
	o.Set("transitions", s.Transitions)
	if s.Skipped != "" {
		o.Set("skipped", s.Skipped)
	}
	if s.Error != nil {
		o.Set("error", *s.Error)
	}
	return o
}

// SnapshotPath is `_snapshot_path()`: `config.memory_dir() / "system_health.json"`.
//
// It CREATES the memory directory, and that is not a convenience — it is
// where Python does it. `config.memory_dir()` is
//
//	def memory_dir() -> Path:
//	    p = workspace_root() / "memory"
//	    p.mkdir(parents=True, exist_ok=True)
//	    return p
//
// so the directory comes into existence as a side effect of ASKING FOR THE
// PATH, and every caller of `_snapshot_path()` — `load_snapshot`,
// `_write_snapshot`, and `run_health_probes`' own `with
// locked_write(_snapshot_path())` — inherits it. r3 found the port had made
// this a pure `filepath.Join` with the only mkdir inside WriteSnapshot,
// which moves the directory's creation from BEFORE the probe loop to AFTER
// it. Measured on CPython 3.14.3 with a read-only workspace root:
//
//	CPython: {"ran": 0, "silent": [], "transitions": 0,
//	          "error": "[Errno 13] Permission denied: '<ws>/memory'"}
//	         probes called: 0
//	Go (before this fix): ran=1, silent=["p1"], probes called: 1
//
// Two runtimes disagreeing on whether seven live-store probes execute at
// all is the L48 shape at its plainest: every decision was right and the
// SHAPE was wrong.
//
// record.NewDirMode, not 0o755: Python's Path.mkdir passes 0o777 and lets
// the umask narrow it, so on this box (umask 0002) CPython creates memory/
// as 0o775 and a hard-coded 0o755 creates it 0o755 — a group-writable store
// becoming group-read-only, which is exactly the difference that bites a
// second user or a container uid. That is r1's F3, and it lives here now
// rather than in WriteSnapshot because this is where Python's mkdir is.
//
// NAMED residual: the error TEXT. Python's is
// `[Errno 13] Permission denied: '<path>'` and Go's is
// `mkdir <path>: permission denied` — the same residual `dispatch/
// envelope.go`'s `oserr` names, and the differential elides it by shape on
// both sides rather than pretending the two agree.
// It calls orch.EnsureMemoryDir rather than re-spelling the mkdir, which
// r4 F6 found it doing: byte-identical logic, a fourth private copy of an
// idiom in a port whose own comments keep naming three-copies-of-one-helper
// as the defect shape. EnsureMemoryDir's doc names THIS package's r3
// finding as its motivating case and states the rule — "if the line it
// ports says memory_dir(), it wants this one" — and `_snapshot_path()` is
// exactly `from config import memory_dir; return memory_dir() / "..."`.
// Sharing it also inherits its named residual (Python's relocation
// fallback, not ported) instead of quietly not having one.
func SnapshotPath(ws string) (string, error) {
	dir, err := orch.EnsureMemoryDir(ws)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "system_health.json"), nil
}

// LoadSnapshot is `load_snapshot`: the file's object, or an EMPTY one.
//
// Five different failures collapse to the same answer on purpose: no file,
// unreadable file, undecodable bytes, unparseable JSON, and valid JSON that
// is not an object. Measured — `[1,2]`, `"hello"` and `null` all give `{}`.
// A port that distinguished them would have to invent a caller for the
// distinction, and there is none: every caller reads "no snapshot" as
// "first cycle".
//
// The SIXTH failure does not collapse, and it is why this returns an error
// at all: `path = _snapshot_path()` is the first statement, and it MKDIRS.
// A workspace whose memory/ cannot be created makes `load_snapshot` RAISE,
// where all five lanes below return {}. Callers must not read that error as
// "first cycle" — CPython's does not.
func LoadSnapshot(ws string) (pyval.Obj, error) {
	path, err := SnapshotPath(ws)
	if err != nil {
		return nil, err
	}
	if _, err := os.Stat(path); err != nil {
		return pyval.Obj{}, nil
	}
	raw, rerr := os.ReadFile(path)
	if rerr != nil {
		return pyval.Obj{}, nil
	}
	text, derr := pyval.DecodeUTF8Strict(raw)
	if derr != nil {
		return pyval.Obj{}, nil
	}
	v, jerr := pyval.LoadsOrdered(text)
	if jerr != nil {
		return pyval.Obj{}, nil
	}
	if o, ok := v.(pyval.Obj); ok {
		return o, nil
	}
	return pyval.Obj{}, nil
}

// WriteSnapshot is `_write_snapshot`: indent=1, sort_keys=True, one trailing
// newline, written atomically.
//
// All three are load-bearing and none is a default. indent=1 is this
// writer's own width (the tree holds both 1 and 2 within one module
// elsewhere, so it is not a house constant); sort_keys is what keeps a
// re-serialised snapshot from rewriting every line, and it recurses into
// nested dicts; the trailing newline is added by the CALLER in Python
// (`... + "\n"`), not by dumps.
func WriteSnapshot(ws string, snap pyval.Obj) error {
	// SnapshotPath performs Python's mkdir — see r1's F3 and r3's F1 there.
	path, err := SnapshotPath(ws)
	if err != nil {
		return err
	}
	body, err := pyval.DumpsIndentNSorted(snap, 1)
	if err != nil {
		return err
	}
	return record.AtomicWrite(path, []byte(body+"\n"))
}

// HistoryOf is `_history_of`: the dict entries of prior["history"], or
// nothing. A non-list history, and every non-dict element inside a list one,
// are dropped silently — the snapshot is operator-editable, and this is
// where a hand-edit stops being fatal.
func HistoryOf(prior pyval.Obj) pyval.List {
	out := pyval.List{}
	v, _ := prior.Get("history")
	lst, ok := asList(v)
	if !ok {
		return out
	}
	for _, h := range lst {
		if o, isObj := asDict(h); isObj {
			out = append(out, o)
		}
	}
	return out
}

// asDict and asList are `isinstance(v, dict)` / `isinstance(v, list)`.
//
// They exist because this package was asserting `v.(pyval.Obj)` inline at
// SEVEN sites — r3 counted; the comment said six — while every pyval helper
// it calls in the same breath — Truthy, Str,
// TypeName — ALSO accepts a plain `map[string]any` as a dict. r1 found the
// contradiction in one expression: RenderSnapshot handed a map[string]any
// would fail its assertion and then build the error message
// "'dict' object has no attribute 'items'" out of TypeName, which is both
// self-refuting and a divergence, since CPython renders that snapshot fine.
//
// pyval's decoder never produces a Go map, so this is latent rather than
// live; it is a hand-built caller's lane. The conversion sorts keys, because
// a Go map HAS no order and this module's output order is observable
// (render order, ToDict order, the `{**obs, "at": now}` ordinal). Sorted is
// the only deterministic choice available, and a caller who needs Python's
// insertion order must hand in a pyval.Obj — which is what the decoder does.
func asDict(v any) (pyval.Obj, bool) {
	switch t := v.(type) {
	case pyval.Obj:
		return t, true
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		o := make(pyval.Obj, 0, len(keys))
		for _, k := range keys {
			o.Set(k, t[k])
		}
		return o, true
	}
	return nil, false
}

// asList carries a []string arm because pyval does: Truthy, TypeName and
// Repr all call a []string a Python list (str.go:283, pyops.go, str.go:102),
// and r2 found the one site where leaving it out diverged — a process
// `status` of []string fell to the rank switch's `default` and ranked 3,
// where CPython raises the unhashable-key TypeError. pyval knows exactly two
// Go-native container shapes, `map[string]any` and `[]any`/`[]string`, so
// with this arm the pair covers the hand-built-caller lane completely rather
// than merely claiming to.
func asList(v any) (pyval.List, bool) {
	switch t := v.(type) {
	case pyval.List:
		return t, true
	case []any:
		return pyval.List(t), true
	case []string:
		out := make(pyval.List, len(t))
		for i, s := range t {
			out[i] = s
		}
		return out, true
	}
	return nil, false
}

// RunCycle is the read-modify half of `run_health_probes`: it takes the
// loaded snapshot and returns the one to write, the narrations to perform
// after that write lands, and the summary. A nil snapshot means "write
// nothing" — see the error lane below.
//
// It does NOT own the config gate, the write, or `transitions`. Python
// assigns `summary["transitions"]` AFTER `_write_snapshot` returns, inside
// the same try, so a failed write leaves it 0 with narrations pending and
// undelivered. RunCycle cannot observe that, so it does not claim to:
// Transitions stays 0 here and RunAndPersist sets it. A caller driving
// RunCycle directly and reading `len(narrations)` is asserting the write
// succeeded — which is exactly the bug, so drive RunAndPersist instead.
//
// `now` is the clock, seamed as a function rather than a value because
// Python calls `datetime.now(timezone.utc).isoformat()` once per declaration
// AND once more for updated_at. Within one declaration the same instant
// stamps the observation, checked_at and last_transition.at; across
// declarations they are separate calls. Passing one string would erase a
// difference the shape actually has (L48).
//
// The narration rule is the part a port gets wrong by reading the status
// pair instead of the field. It is edge-triggered on what the user was LAST
// TOLD (`narrated`), not on prev != next:
//
//   - SUBSYSTEM_SILENT fires when a process reaches SILENT and `narrated` is
//     not already "silent". That covers UNKNOWN→SILENT, which is how the
//     streak probes arrive, and first-observation SILENT, which has no
//     previous status at all.
//   - SUBSYSTEM_RECOVERED fires when a process reaches OK and `narrated` IS
//     "silent". That covers SILENT→UNKNOWN→OK — a probe that broke in the
//     middle still owes the user the recovery line.
//   - A held state never repeats into the log.
func RunCycle(snapshot pyval.Obj, decls []Declaration, now func() string) (pyval.Obj, []Narration, Summary) {
	summary := Summary{Silent: []string{}}

	// `processes = snapshot.get("processes")`, replaced WHOLESALE when it is
	// not a dict. A list here is not merged or salvaged, it is dropped.
	var processes pyval.Obj
	if v, ok := snapshot.Get("processes"); ok {
		if o, isObj := asDict(v); isObj {
			processes = o
		}
	}
	if processes == nil {
		processes = pyval.Obj{}
	}

	var pending []Narration
	for _, decl := range decls {
		var prior pyval.Obj
		if v, ok := processes.Get(decl.Name); ok {
			if o, isObj := asDict(v); isObj {
				prior = o
			}
		}
		if prior == nil {
			prior = pyval.Obj{}
		}

		status, evidence, obs, perr := decl.Probe(&prior)
		if perr != nil {
			// `except Exception as exc` — a broken probe reports UNKNOWN and
			// does not take the cycle down. The message is clipped at 120
			// CODE POINTS, and the prefix is part of the readout.
			status, evidence, obs = Unknown,
				"probe failed: "+pyval.Clip(perr.Error(), 120), pyval.Obj{}
		}
		summary.Ran++
		if status == Silent {
			summary.Silent = append(summary.Silent, decl.Name)
		}

		prevStatus, _ := prior.Get("status")
		stamp := now()

		// `entry = dict(prior)` — a SHALLOW copy, so keys this module has
		// never heard of survive the update. That is the data-retention rule
		// rather than tidiness: the snapshot is the seed of the maro-level
		// systemic-metadata store, and a field an operator added by hand
		// must not be deleted by a cycle that did not recognise it.
		entry := make(pyval.Obj, len(prior))
		copy(entry, prior)

		history := HistoryOf(prior)
		if len(obs) > 0 {
			// `if obs:` — an EMPTY observation is falsy, so it appends
			// nothing AND takes no timestamp. A probe with nothing to say
			// leaves the ring buffer alone.
			//
			// `{**obs, "at": now}` appends "at" when it is new and
			// OVERWRITES a probe-supplied one IN PLACE. Obj.Set is exactly
			// that rule, so the observation is copied and Set rather than
			// rebuilt.
			//
			// What C13 pins is the VALUE — a probe returning
			// {"at": "1999", "n": 1} gets the cycle's clock in it. The
			// POSITION is not pinned and cannot be from here: the snapshot is
			// written sort_keys=True and "at" < "n", so the stamped
			// observation renders identically whichever ordinal the rule
			// picked. Obj.Set is used because it is the faithful rule, not
			// because a fixture proved the ordinal.
			stamped := make(pyval.Obj, len(obs))
			copy(stamped, obs)
			stamped.Set("at", stamp)
			history = append(history, stamped)
			if len(history) > HistoryKeep {
				history = history[len(history)-HistoryKeep:]
			}
		}
		entry.Set("status", status)
		entry.Set("evidence", evidence)
		entry.Set("description", decl.Description)
		entry.Set("expectation", decl.Expectation)
		entry.Set("history", history)
		entry.Set("checked_at", stamp)

		narrated, _ := prior.Get("narrated")
		wentSilent := status == Silent && narrated != "silent"
		recovered := status == OK && narrated == "silent"
		if wentSilent || recovered {
			lt := pyval.Obj{}
			lt.Set("from", prevStatus)
			lt.Set("to", status)
			lt.Set("at", stamp)
			entry.Set("last_transition", lt)
			if wentSilent {
				entry.Set("narrated", "silent")
			} else {
				entry.Set("narrated", "ok")
			}
			pending = append(pending, Narration{decl, status, evidence})
		}
		processes.Set(decl.Name, entry)
	}

	// AFTER the loop, not before: Obj is a slice, and Set on the local
	// `processes` reallocates it. Python mutates one dict in place and can
	// assign it up front; a Go port that stored the slice header first
	// writes a snapshot missing every process the loop appended. The ordinal
	// is the same either way — nothing else is inserted meanwhile — and
	// sort_keys erases it in the file regardless.
	snapshot.Set("processes", processes)
	snapshot.Set("updated_at", now())
	next, err := nextCycle(snapshot)
	if err != nil {
		// `int("abc")` raises INSIDE the blanket try, so the cycle aborts
		// before the write: nothing persists, no narration happens, and
		// `ran` still counts the probes that already ran. The file on disk
		// is untouched, unparseable counter and all.
		summary.setError(err.Error())
		return nil, nil, summary
	}
	snapshot.Set("cycle", next)
	return snapshot, pending, summary
}

// RunAndPersist is the whole of `run_health_probes` minus the narrating: the
// config gate, the load, the cycle, the write, and the ONE blanket `except`
// that covers all four. It returns the narrations to perform rather than
// performing them (see the package doc), and it is the entry point callers
// should use — RunCycle alone cannot see two of the four error lanes.
//
// `cfg` is `config.get("health.probes_enabled", True)`, returning the raw
// value rather than a bool because Python spells the gate `bool(cfg_get(...))`
// and the collapse is observable: 0, "", None and False skip the cycle while
// the STRING "no" is a non-empty string and therefore RUNS it. It may return
// an error, because the import-and-call is inside the try and a config layer
// that raises is measured to give {"ran": 0, "transitions": 0, "error": ...}
// with nothing written — the one lane where Ran is still 0 when Error is set.
//
// The lock is still the caller's (package doc): Python holds the snapshot's
// lock across load..write, and nothing here takes it.
func RunAndPersist(ws string, decls []Declaration, cfg func() (any, error), now func() string) (Summary, []Narration) {
	summary := Summary{Silent: []string{}}

	enabled, err := cfg()
	if err != nil {
		summary.setError(err.Error())
		return summary, nil
	}
	if !pyval.Truthy(enabled) {
		summary.Skipped = "health.probes_enabled is off"
		return summary, nil
	}

	// `with locked_write(_snapshot_path())`: the path expression is evaluated
	// BEFORE the lock and before load_snapshot, and evaluating it mkdirs. A
	// workspace whose memory/ cannot be created therefore fails HERE, with
	// ran=0 and silent=[] and not one probe called — fixtures C48/C49.
	// Reaching it through LoadSnapshot alone would already have run them.
	if _, perr := SnapshotPath(ws); perr != nil {
		summary.setError(perr.Error())
		return summary, nil
	}
	prior, lerr := LoadSnapshot(ws)
	if lerr != nil {
		// Unreachable while SnapshotPath is idempotent and just succeeded,
		// and ported anyway: in Python this is the same statement raising
		// inside the same try, and a port that dropped the lane would be
		// deciding the mkdir cannot fail twice.
		summary.setError(lerr.Error())
		return summary, nil
	}
	snap, pending, summary := RunCycle(prior, decls, now)
	if snap == nil {
		// The cycle aborted (an unparseable `cycle` counter). Its summary
		// already carries Ran, Silent and Error; there is nothing to write
		// and nothing to narrate.
		return summary, nil
	}
	if err := WriteSnapshot(ws, snap); err != nil {
		// The lane a split port drops: probes ran, narrations were decided,
		// and the file did not land. Python never reaches
		// `summary["transitions"] = ...`, so it stays 0 AND the pending
		// narrations are discarded — the log must not claim the user was
		// told about a state that was never recorded, because the state
		// machine will re-decide the same transition next cycle. Fixtures
		// C45/C46.
		summary.setError(err.Error())
		return summary, nil
	}
	summary.Transitions = len(pending)
	return summary, pending
}

// nextCycle is `int(snapshot.get("cycle", 0) or 0) + 1`.
//
// Four lanes — restart, increment, raise, refuse — and the increment is the
// same `or` collapse sheriff's dormancy threshold has. The two raising rows
// are one lane with two exception classes:
//
//	absent / 0 / "" / None / False  -> 1   (the counter restarts)
//	"41" / 41 / 2.9 / True          -> that value + 1, TRUNCATED toward zero
//	"abc"                           -> ValueError, and the whole cycle aborts
//	[1] / {"a": 1}                  -> TypeError, likewise
//	9223372036854775807 / 1e19      -> REFUSED, a known gap; CPython computes
//
// The raising lane is why this returns an error instead of defaulting: a
// port that quietly restarted the counter on a corrupt value would write a
// snapshot CPython refuses to write. They are DIFFERENT exception classes
// with different messages, and the summary carries the message, so both are
// pinned — fixtures C36/C37 (ValueError), C40/C41 (TypeError), C42 (True is
// an int, not a rejection). r1 found the TypeError arm named here and reached
// by nothing, which is a doc claiming coverage the fixtures did not have.
//
// The refusing lane is this port's own, and r2 found it unnamed: CPython's int
// is arbitrary precision, so a counter at 9223372036854775807 simply becomes
// 9223372036854775808 and the snapshot is written. A Go int cannot hold that,
// and `n + 1` WRAPS silently — it would durably write -9223372036854775808,
// which is the one outcome pyval.ErrIntTooLarge's own doc forbids for a
// value that gets WRITTEN ("it refuses and skips the write rather than
// emitting a number CPython would never produce"). So the increment is
// range-checked and takes the refusal, which is the lane a counter PAST
// int64 already took through pyval.Int. It is a known gap, not a fix:
// knowngap_test.go asserts the divergence so closing it fails a test.
//
// The asymmetry is worth naming: RenderSnapshot prints such a counter
// correctly, because pyval.reprNumber keeps an integer literal verbatim
// rather than forcing it through an int64. Only the WRITER is bounded.
func nextCycle(snapshot pyval.Obj) (int, error) {
	v, ok := snapshot.Get("cycle")
	if !ok || !pyval.Truthy(v) {
		return 1, nil
	}
	n, err := pyval.Int(v)
	if err != nil {
		return 0, err
	}
	if n == math.MaxInt {
		return 0, pyval.ErrIntTooLarge
	}
	return n + 1, nil
}

// RenderSnapshot is `render_snapshot`.
//
// It RAISES on malformed data rather than tolerating it, and that is ported
// rather than softened: a `processes` that is a list dies on `.items()`, and
// an entry that is a string dies on `.get`. Both keep CPython's
// AttributeError wording, because this is the CLI's only output and a Go
// port that printed a placeholder would be telling the operator the snapshot
// is fine.
//
// Python's signature is `render_snapshot(snapshot=None)` and loads from
// disk when the argument is omitted. That default is NOT ported: `None` and
// "an empty snapshot" are distinguishable arguments there and would both be
// a nil Obj here. Its only caller in the tree is `main()`, which omits the
// argument, and `main()` is not ported — so the load belongs at the call
// site as `RenderSnapshot(LoadSnapshot(ws))` — whose error is the mkdir
// lane, not a missing snapshot.
//
// The header's `.get(key, "?")` is the trap worth the comment: a default
// applies only when the key is ABSENT. `{"updated_at": null}` renders the
// four characters `None`, not `?`. Measured both ways.
func RenderSnapshot(snap pyval.Obj) (string, error) {
	lines := []string{"# System health — dynamic-process liveness", ""}
	procsVal, _ := snap.Get("processes")
	if !pyval.Truthy(procsVal) {
		// `if not snap.get("processes")` — TRUTHINESS, so an empty dict is
		// "no snapshot yet" exactly like a missing key. A cycle that ran
		// with no declarations reads as never having run.
		lines = append(lines, "No snapshot yet — probes run at goal-run finalization "+
			"(or: python3 -m system_health --probe).")
		return strings.Join(lines, "\n"), nil
	}
	procs, ok := asDict(procsVal)
	if !ok {
		return "", &pyval.PyErr{Class: "AttributeError", Msg: fmt.Sprintf(
			"'%s' object has no attribute 'items'", pyval.TypeName(procsVal))}
	}

	// The sort key is a TUPLE — `(order.get(status, 3), name)` — so this is
	// one sort, not two, and the name half makes it total: there is no
	// tie-break left to get wrong (P11 does not apply here, but only
	// because the key is total; that is worth writing down rather than
	// assuming).
	//
	// Note the two different defaults for the same field: the sort key reads
	// `.get("status")` with NO default, so a MISSING status ranks under None
	// (3, beside the unrecognised ones), while the display reads
	// `.get("status", "?")`. R17 pins the SORT half — it needs two processes,
	// because with one process every rank produces the same single line — and
	// R8 pins the display half.
	order := map[string]int{Silent: 0, Unknown: 1, OK: 2}
	rank := make([]int, len(procs))
	for i, f := range procs {
		p, isObj := asDict(f.Val)
		if !isObj {
			// Raised by the SORT KEY, which CPython evaluates before
			// `sorted` yields anything — and after the header lines were
			// appended to a list that the raise discards. Nothing is
			// observable but the exception, so building it here is faithful.
			return "", &pyval.PyErr{Class: "AttributeError", Msg: fmt.Sprintf(
				"'%s' object has no attribute 'get'", pyval.TypeName(f.Val))}
		}
		st, _ := p.Get("status")
		switch s := st.(type) {
		case string:
			if r, found := order[s]; found {
				rank[i] = r
			} else {
				rank[i] = 3
			}
		case pyval.List, pyval.Obj, []any, []string, map[string]any:
			// `order.get(unhashable)` is a TypeError, not a miss. Ported for
			// the same reason as the AttributeErrors above: the alternative
			// is inventing a rank CPython never computes.
			//
			// The wording is MEASURED on 3.14, which wraps the classic text
			// in a "cannot use X as a dict key" frame; 3.12 and earlier
			// raise the bare "unhashable type: 'list'". If this differential
			// ever fails only here, an interpreter changed under it — that
			// is a fixture question, not a port defect.
			return "", &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
				"cannot use '%s' as a dict key (unhashable type: '%s')",
				pyval.TypeName(st), pyval.TypeName(st))}
		default:
			rank[i] = 3
		}
	}
	lines = append(lines, fmt.Sprintf("Updated: %s  (cycle %s)",
		getOr(snap, "updated_at", "?"), getOr(snap, "cycle", "?")), "")

	idx := make([]int, len(procs))
	for i := range idx {
		idx[i] = i
	}
	sort.SliceStable(idx, func(a, b int) bool {
		if rank[idx[a]] != rank[idx[b]] {
			return rank[idx[a]] < rank[idx[b]]
		}
		return procs[idx[a]].Key < procs[idx[b]].Key
	})
	for _, i := range idx {
		f := procs[i]
		p, _ := asDict(f.Val)
		lines = append(lines,
			fmt.Sprintf("[%s] %s — %s", getOr(p, "status", "?"),
				f.Key, getOr(p, "description", "")),
			"    expectation: "+getOr(p, "expectation", ""),
			"    evidence:    "+getOr(p, "evidence", ""))
		if lt, _ := p.Get("last_transition"); lt != nil {
			// `isinstance(lt, dict)` — a string last_transition is SKIPPED,
			// not rendered and not fatal. The one tolerant branch in a
			// function that otherwise raises.
			if o, isObj := asDict(lt); isObj {
				from, _ := o.Get("from")
				to, _ := o.Get("to")
				at, _ := o.Get("at")
				lines = append(lines, fmt.Sprintf("    last transition: %s → %s at %s",
					pyval.Str(from), pyval.Str(to), pyval.Str(at)))
			}
		}
		lines = append(lines, "")
	}
	return strings.Join(lines, "\n"), nil
}

// getOr is `d.get(key, default)` inside an f-string: absent gives the
// default, present-and-null gives the four characters "None", and a
// non-string value is str()'d rather than assumed — nothing validates the
// snapshot's types.
func getOr(o pyval.Obj, key, def string) string {
	v, ok := o.Get(key)
	if !ok {
		return def
	}
	return pyval.Str(v)
}
