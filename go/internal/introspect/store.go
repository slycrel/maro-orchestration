package introspect

import (
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// EventsPath is introspect._events_path(): <workspace>/memory/events.jsonl.
//
// Python wraps the import of `orch_items.memory_dir` in a bare try/except
// and falls back to `Path.cwd() / "memory" / "events.jsonl"`. That fallback
// is NOT ported, and the omission is deliberate rather than an oversight:
// it fires only when importing orch_items raises, which on a working
// installation never happens — and when it does fire it silently repoints
// the whole store at whatever directory the process happens to be in. A
// second store-routing rule that disagrees with the first is the exact
// shape of the 2026-08-16 live-ledger incident. One resolution order,
// passed in as an argument.
// It CREATES memory/: `_events_path()` is `memory_dir() / "events.jsonl"`
// and memory_dir mkdirs (orch.EnsureMemoryDir). Resolving this path is a
// filesystem operation in the original, not a join.
func EventsPath(ws string) (string, error) {
	dir, err := orch.EnsureMemoryDir(ws)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "events.jsonl"), nil
}

// DiagnosesPath is introspect._diagnoses_path(), and creates memory/ for
// the same reason EventsPath does.
func DiagnosesPath(ws string) (string, error) {
	dir, err := orch.EnsureMemoryDir(ws)
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, "diagnoses.jsonl"), nil
}

// The three readers below do NOT agree with each other about what a
// failure here means, and the port follows each one rather than picking a
// house rule:
//
//   - LoadLoopEvents  (_load_loop_events)   has NO try. CPython raises.
//   - LatestLoopID    (_load_latest_loop_id) has NO try. CPython raises.
//   - LoadDiagnoses   (load_diagnoses)      wraps the whole body,
//     `_diagnoses_path()` included, in `except Exception: pass` and
//     returns the results built so far — so empty, which is what this
//     returns too. FAITHFUL, not a residual.
//
// RESIDUAL, named, for the first two only: they return a plain slice and
// a plain (string, bool), so a memory/ that cannot be created reads as
// "no events" instead of stopping the caller. Widening those two
// signatures is a separate change with a caller ripple; BACKLOG'd and
// pinned in knowngap_test rather than left silent.

// LoadLoopEvents is introspect._load_loop_events.
//
// The match is a PREFIX, not an equality: `e.get("loop_id","").startswith(
// loop_id)`. Two consequences worth naming because neither is obvious from
// the call site.
//
// An EMPTY loopID matches every event, in both runtimes — `"".startswith(
// "")` is True and so is every other string's. A caller that reaches here
// with an unset id diagnoses the whole store rather than nothing.
//
// NAMED DIVERGENCE on the row side: Python calls `.startswith` on whatever
// `loop_id` holds, so a row whose loop_id is a number raises AttributeError
// and takes the whole load down. The port reads a non-string loop_id as
// absent and skips the row. CPython crashes; the port answers. Same shape
// as the `detail`/`goal` divergences in Diagnose, and pinned the same way.
//
// Rows are read through the ORDERED reader, and the reason is not what it
// first looks like. Nothing writes an event back: the diagnosis row is
// built from DiagnosisKeys explicitly, so an event's key order cannot
// reach the store through this path at all. (An earlier draft of this
// comment claimed it could, which was a coverage claim about a mechanism
// that does not exist.) The real reason is that the ordered reader is the
// decoder this package's fixtures already use — `jsonx.ObjectOrdered` —
// and Diagnose takes `pyval.Obj`. Reading production through the plain
// decoder and the tests through the ordered one would give one store two
// readers that resolve numbers differently, which is the divergence the
// two readers' own parity test exists to prevent.
func LoadLoopEvents(ws, loopID string) []pyval.Obj {
	path, perr := EventsPath(ws)
	if perr != nil {
		return nil
	}
	rows, _ := record.ReadAllCountedOrdered(path)
	var out []pyval.Obj
	for _, e := range rows {
		if strings.HasPrefix(evStr(evGet(e, "loop_id", "")), loopID) {
			out = append(out, e)
		}
	}
	return out
}

// LatestLoopID is introspect._load_latest_loop_id: the loop_id of the LAST
// event that carries one, or false when the store has none.
//
// Python's gate is truthiness (`if lid:`), not "is a non-empty string", so
// an event stamped with the number 5 is selected there and skipped here.
// That divergence resolves downstream rather than here: CPython goes on to
// call `_load_loop_events(5)`, whose `.startswith(5)` raises TypeError. The
// port declines to select a row it could only mis-handle, and the honest
// consequence is that it may return an OLDER id where CPython would have
// crashed. Pinned by a fixture whose last row carries a numeric loop_id.
//
// The `path.exists()` pre-check in Python is not spelled separately here:
// a missing store reads as zero rows through the same never-raises reader,
// which is the answer that check exists to produce.
func LatestLoopID(ws string) (string, bool) {
	path, perr := EventsPath(ws)
	if perr != nil {
		return "", false
	}
	rows, _ := record.ReadAllCountedOrdered(path)
	for i := len(rows) - 1; i >= 0; i-- {
		if id := evStr(evGet(rows[i], "loop_id", "")); id != "" {
			return id, true
		}
	}
	return "", false
}

// SaveDiagnosis is introspect.save_diagnosis: append one row to
// diagnoses.jsonl, creating the directory if needed.
//
// It takes a POINTER because Python mutates the diagnosis it was handed —
// `diag.recorded_at` is stamped here, at persistence, not at construction,
// and a caller that saves then reads `recorded_at` off its own object is
// relying on that. A value receiver would silently break those callers
// while every test that only inspects the FILE kept passing.
//
// `now` is a parameter for the same reason: a caller replaying or
// backfilling supplies its own stamp, and a test must be able to.
// A non-empty RecordedAt is respected, never overwritten.
func SaveDiagnosis(ws string, d *LoopDiagnosis, now time.Time) error {
	path, perr := DiagnosesPath(ws)
	if perr != nil {
		return perr
	}
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return err
	}
	if d.RecordedAt == "" {
		d.RecordedAt = pyval.NowISO(now)
	}
	line, err := d.MarshalRow()
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
	if err != nil {
		return err
	}
	if _, err := f.WriteString(line + "\n"); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

// LoadDiagnoses is introspect.load_diagnoses: recent diagnoses, NEWEST
// FIRST, up to `limit` rows that actually rehydrate.
//
// Three behaviours here are easy to lose in a port and all three are
// load-bearing.
//
// 1. It reads the WHOLE store and then limits, rather than reading a tail
// of `limit` lines. Python says why in a comment: a raw record can fail
// LoopDiagnosis construction, and the caller wants `limit` VALID rows, not
// `limit` raw lines. A tail read would quietly return fewer.
//
// 2. `limit=0` returns ONE row, not zero. Python appends first and checks
// `len(results) >= limit` after, so the first valid row is already in the
// list when the check fires. Same for any negative limit. This is not a
// nicety: it is the shape of the load_outcomes(limit=0) divergence one
// package over, and the port that "fixes" it disagrees with the runtime
// it shares a store with. Replicated, and pinned by a fixture.
//
// 3. A row missing loop_id, failure_class or severity does not rehydrate.
// Those three are the dataclass's required positional fields, so
// `LoopDiagnosis(**subset)` raises TypeError and the row is SKIPPED, not
// defaulted. Every other field defaults.
//
// NAMED DIVERGENCE on field types. Python's dataclass does no type
// checking, so a row carrying `"total_tokens": "many"` rehydrates with a
// STRING in an int field and renders `tokens=many` in summary(); a float
// 5.0 renders `tokens=5.0`. The port's fields are typed, so it coerces
// (non-numerics to 0, floats truncated) and renders `tokens=0` / `tokens=5`.
// The divergence is confined to READING: nothing rehydrated here is written
// back, so a mistyped row in the shared store cannot be rewritten into a
// differently-mistyped row by this runtime.
func LoadDiagnoses(ws string, limit int) []LoopDiagnosis {
	path, perr := DiagnosesPath(ws)
	if perr != nil {
		return nil
	}
	rows, _ := record.ReadAllCountedOrdered(path)
	var out []LoopDiagnosis
	for i := len(rows) - 1; i >= 0; i-- {
		d, ok := diagnosisFromRow(rows[i])
		if !ok {
			continue
		}
		out = append(out, d)
		if len(out) >= limit {
			break
		}
	}
	return out
}

// diagnosisFromRow is `LoopDiagnosis(**{k: d[k] for k in fields if k in d})`.
//
// The comprehension filters by PRESENCE, and no type checking happens
// anywhere — a dataclass constructor takes whatever it is handed. So a
// present null lands in the field as None and `summary()` renders
// `severity=None`, measured. The string fields therefore go through
// pyval.Str rather than a `.(string)` assertion: Str IS Python's str(),
// so None renders "None", 5 renders "5" and 5.0 renders "5.0" — the same
// bytes CPython's f-string produces. An assertion returning "" for all
// three would have made three different rows look identical.
//
// The three REQUIRED fields are tested for presence separately from their
// value, because a present null does not raise: only a MISSING key makes
// `LoopDiagnosis(**subset)` a TypeError, and that is what skips the row.
func diagnosisFromRow(r pyval.Obj) (LoopDiagnosis, bool) {
	for _, k := range []string{"loop_id", "failure_class", "severity"} {
		if _, ok := r.Get(k); !ok {
			return LoopDiagnosis{}, false
		}
	}
	str := func(key string) string {
		v, ok := r.Get(key)
		if !ok {
			return ""
		}
		return pyval.Str(pyval.Plain(v))
	}
	d := LoopDiagnosis{
		LoopID:         str("loop_id"),
		FailureClass:   str("failure_class"),
		Severity:       str("severity"),
		Recommendation: str("recommendation"),
		TotalTokens:    evInt(evGet(r, "total_tokens", 0)),
		TotalElapsedMS: evInt(evGet(r, "total_elapsed_ms", 0)),
		StepsDone:      evInt(evGet(r, "steps_done", 0)),
		StepsBlocked:   evInt(evGet(r, "steps_blocked", 0)),
		StepsTotal:     evInt(evGet(r, "steps_total", 0)),
		Project:        str("project"),
		RecordedAt:     str("recorded_at"),
	}
	// `evidence` is a list of strings in every row either runtime writes,
	// but the store is a text file and neither runtime validates it.
	//
	// A member that is not a string goes through pyval.Str for the same
	// reason the scalar fields do: every consumer that RENDERS an evidence
	// line uses an f-string, and an f-string over the integer 5 is "5".
	// Dropping it instead would be a silent loss with a shorter list as
	// the only symptom. The one consumer shape this does NOT match is
	// `"\n".join(evidence)`, which raises TypeError in CPython on a
	// non-string member where the port joins cleanly.
	//
	// A non-list `evidence` (Python carries the bare value; measured) is
	// left EMPTY here rather than wrapped in a one-element list, because a
	// one-element list is a shape no writer produces and every reader
	// would believe.
	if lst, ok := evGet(r, "evidence", nil).(pyval.List); ok {
		for _, item := range lst {
			d.Evidence = append(d.Evidence, pyval.Str(pyval.Plain(item)))
		}
	}
	return d, true
}

// DiagnoseLatest is introspect.diagnose_latest().
//
// Python returns None when the store names no loop at all. It does NOT
// return None for a loop whose events are all gone — `diagnose_loop` on a
// prefix that matches nothing answers with the artifact_missing class,
// which is a diagnosis and not an absence.
//
// This is `diagnose_loop`'s emit_log_event=FALSE path. Python's default is
// True and writes a captain's-log DIAGNOSIS event for every non-healthy
// class, inside a bare try/except. That write is a side effect of
// diagnosing rather than part of the answer — and it is an OPEN GAP, not a
// blocked one: the captain's-log port landed as `record.Recorder.EventNoted`
// and nothing is in the way but the work. See DiagnoseLoop in cli.go for
// the shape it has to reproduce, and BACKLOG for the entry.
func DiagnoseLatest(ws string) (LoopDiagnosis, bool) {
	loopID, ok := LatestLoopID(ws)
	if !ok {
		return LoopDiagnosis{}, false
	}
	return Diagnose(LoadLoopEvents(ws, loopID), loopID, ""), true
}
