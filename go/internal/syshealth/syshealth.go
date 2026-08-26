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
//     instead of both reading narrated=None and double-narrating; that is the
//     CALLER's job here, because RunCycle does not touch the disk at all.
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
package syshealth

import (
	"fmt"
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
	Probe       func(prior pyval.Obj) (string, string, pyval.Obj, error)
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
// wraps four things, and `Ran` differs across them — measured, because the
// first draft of this comment asserted "by the time it fires the probes
// have already run and Ran is already non-zero" and that is false for the
// first row:
//
//	config.get raises          ran=0, transitions=0, nothing written
//	an unparseable cycle       ran=N, transitions=0, nothing written
//	_write_snapshot raises     ran=N, transitions=0, NOTHING NARRATED
//	the narration loop raises  ran=N, transitions already assigned
//
// The third row is the one a split port drops on the floor: the probes ran,
// the cycle produced narrations, and the write failed — so `transitions`
// must be 0 and the pending narrations must be discarded, or the log claims
// the user was told about a state that never persisted. RunCycle cannot see
// that lane; RunAndPersist owns it, and is the reason it exists.
type Summary struct {
	Ran         int
	Silent      []string
	Transitions int
	Skipped     string
	Error       string
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
	if s.Error != "" {
		o.Set("error", s.Error)
	}
	return o
}

// SnapshotPath is `config.memory_dir() / "system_health.json"`.
func SnapshotPath(ws string) string {
	return filepath.Join(orch.MemoryDir(ws), "system_health.json")
}

// LoadSnapshot is `load_snapshot`: the file's object, or an EMPTY one.
//
// Five different failures collapse to the same answer on purpose: no file,
// unreadable file, undecodable bytes, unparseable JSON, and valid JSON that
// is not an object. Measured — `[1,2]`, `"hello"` and `null` all give `{}`.
// A port that distinguished them would have to invent a caller for the
// distinction, and there is none: every caller reads "no snapshot" as
// "first cycle".
func LoadSnapshot(ws string) pyval.Obj {
	path := SnapshotPath(ws)
	if _, err := os.Stat(path); err != nil {
		return pyval.Obj{}
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return pyval.Obj{}
	}
	text, derr := pyval.DecodeUTF8Strict(raw)
	if derr != nil {
		return pyval.Obj{}
	}
	v, jerr := pyval.LoadsOrdered(text)
	if jerr != nil {
		return pyval.Obj{}
	}
	if o, ok := v.(pyval.Obj); ok {
		return o
	}
	return pyval.Obj{}
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
	path := SnapshotPath(ws)
	// record.NewDirMode, not 0o755: Python's Path.mkdir passes 0o777 and lets
	// the umask narrow it, so on this box (umask 0002) CPython creates
	// memory/ as 0o775 and a hard-coded 0o755 creates it 0o755 — a
	// group-writable store becoming group-read-only, which is exactly the
	// difference that bites a second user or a container uid. AtomicWrite one
	// line down already spells the rule; this MkdirAll runs first, so the
	// narrower constant was winning.
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
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
// They exist because this package was asserting `v.(pyval.Obj)` inline at six
// sites while every pyval helper it calls in the same breath — Truthy, Str,
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

func asList(v any) (pyval.List, bool) {
	switch t := v.(type) {
	case pyval.List:
		return t, true
	case []any:
		return pyval.List(t), true
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

		status, evidence, obs, perr := decl.Probe(prior)
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
		summary.Error = pyval.Clip(err.Error(), 200)
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
		summary.Error = pyval.Clip(err.Error(), 200)
		return summary, nil
	}
	if !pyval.Truthy(enabled) {
		summary.Skipped = "health.probes_enabled is off"
		return summary, nil
	}

	snap, pending, summary := RunCycle(LoadSnapshot(ws), decls, now)
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
		summary.Error = pyval.Clip(err.Error(), 200)
		return summary, nil
	}
	summary.Transitions = len(pending)
	return summary, pending
}

// nextCycle is `int(snapshot.get("cycle", 0) or 0) + 1`.
//
// Three lanes, and the middle one is the same `or` collapse sheriff's
// dormancy threshold has:
//
//	absent / 0 / "" / None / False  -> 1   (the counter restarts)
//	"41" / 41 / 2.9 / True          -> that value + 1, TRUNCATED toward zero
//	"abc"                           -> ValueError, and the whole cycle aborts
//	[1] / {"a": 1}                  -> TypeError, likewise
//
// The last two lanes are why this returns an error instead of defaulting: a
// port that quietly restarted the counter on a corrupt value would write a
// snapshot CPython refuses to write. They are DIFFERENT exception classes
// with different messages, and the summary carries the message, so both are
// pinned — fixtures C36/C37 (ValueError), C40/C41 (TypeError), C42 (True is
// an int, not a rejection). r1 found the TypeError arm named here and reached
// by nothing, which is a doc claiming coverage the fixtures did not have.
func nextCycle(snapshot pyval.Obj) (int, error) {
	v, ok := snapshot.Get("cycle")
	if !ok || !pyval.Truthy(v) {
		return 1, nil
	}
	n, err := pyval.Int(v)
	if err != nil {
		return 0, err
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
// site as `RenderSnapshot(LoadSnapshot(ws))`.
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
	// `.get("status", "?")`. R8 pins both halves at once.
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
		case pyval.List, pyval.Obj, []any, map[string]any:
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
