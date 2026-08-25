package tasks

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// A task file on disk is not a shape this package controls. It arrives
// from another box, from an older schema, from a hand-edit during an
// incident — and Python reads it with SUBSCRIPTS: `task["status"]`,
// `task["attempt"] += 1`, `task["timestamps"][key] = ...`. Every one of
// those raises KeyError for a row that lacks the key, and the raise is the
// contract: the verb fails, the file is left exactly as it was, and the
// caller sees the task still queued.
//
// The port had `.get`-style reads with Go zero values behind them, which
// turns a malformed row into a SILENTLY DIFFERENT one: a missing status
// read as "" and compared, a missing attempt restarted at 1, a missing
// timestamps object synthesised and written. The last is the worst of the
// three — it writes a task CPython leaves untouched, so the two runtimes
// disagree about whether the row is still claimable (adversarial r11
// round 3, MEDIUM).
//
// Nothing measured any of it: every fixture in this package is built by
// Enqueue, which cannot produce a row missing a key.
const pyMalformedSrc = `
import json, os, sys
import task_store

verb, job_id = sys.argv[1], sys.argv[2]
p = task_store.task_path(job_id)
before = p.read_bytes()

out = {}
try:
    if verb == "claim":
        task_store.claim(job_id, pid=os.getpid())
    elif verb == "complete":
        task_store.complete(job_id, {"a": "/x/y"}, "success")
    elif verb == "fail":
        task_store.fail(job_id, "boom")
    elif verb == "archive":
        task_store.archive(job_id)
    out["ok"] = True
except BaseException as e:
    out["ok"] = False
    out["cls"] = type(e).__name__
    out["msg"] = str(e)

# archive UNLINKS the row on success, so "the file is gone" is one of the
# outcomes this verb can produce and not a probe failure.
if p.exists():
    after = p.read_bytes()
    out["unchanged"] = before == after
    try:
        out["after"] = after.decode("utf-8")
    except UnicodeDecodeError:
        # One fixture seeds bytes this runtime cannot decode at all. That is
        # the case under test, not a probe failure — report a sentinel and
        # let the comparison below assert on the raw bytes instead.
        out["after"] = "<not-utf-8>"
else:
    out["unchanged"] = False
    out["after"] = None
print(json.dumps(out))
`

func TestMalformedTaskRowsRaiseTheWayPythonDoes(t *testing.T) {
	for _, c := range []struct {
		name string
		verb string
		// row is the task file's content, spelled as raw JSON so a key can
		// be genuinely ABSENT rather than zero.
		row string
	}{
		{"a claim on a row with no status", "claim", `{
			"job_id": "task-mal01", "lane": "agenda",
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"a claim on a row with no attempt", "claim", `{
			"job_id": "task-mal02", "lane": "agenda", "status": "queued",
			"timestamps": {}, "blocked_by": []}`},
		{"a claim on a row with no timestamps", "claim", `{
			"job_id": "task-mal03", "lane": "agenda", "status": "queued",
			"attempt": 0, "blocked_by": []}`},
		// The subscript is on the OUTER object, so a scalar there is an
		// item-assignment TypeError rather than a KeyError.
		{"a claim on a row whose timestamps is a string", "claim", `{
			"job_id": "task-mal04", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": "none", "blocked_by": []}`},
		{"a claim on a row whose timestamps is a list", "claim", `{
			"job_id": "task-mal05", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": [], "blocked_by": []}`},
		// `task["attempt"] += 1` is Python's own +, so a float attempt
		// stays a float and a string one CONCATENATES-or-raises.
		{"a claim on a row whose attempt is a float", "claim", `{
			"job_id": "task-mal06", "lane": "agenda", "status": "queued",
			"attempt": 2.0, "timestamps": {}, "blocked_by": []}`},
		{"a claim on a row whose attempt is a string", "claim", `{
			"job_id": "task-mal07", "lane": "agenda", "status": "queued",
			"attempt": "2", "timestamps": {}, "blocked_by": []}`},
		{"a claim on a row whose attempt is null", "claim", `{
			"job_id": "task-mal08", "lane": "agenda", "status": "queued",
			"attempt": null, "timestamps": {}, "blocked_by": []}`},

		// The claim's OTHER read of a stale row. `claimed_by_pid` is a
		// `.get`, so a missing key is None and falsy; the value itself is
		// handed raw to `_pid_alive`, whose `pid <= 0` raises for a
		// string — but only when the value is TRUTHY, because Python's
		// `and` short-circuits.
		{"a claim on a claimed row with a string pid", "claim", `{
			"job_id": "task-mal14", "lane": "agenda", "status": "claimed",
			"claimed_by_pid": "1234", "attempt": 1, "timestamps": {},
			"blocked_by": []}`},
		{"a claim on a claimed row with an empty-string pid", "claim", `{
			"job_id": "task-mal15", "lane": "agenda", "status": "claimed",
			"claimed_by_pid": "", "attempt": 1, "timestamps": {},
			"blocked_by": []}`},
		{"a claim on a claimed row with a dict pid", "claim", `{
			"job_id": "task-mal16", "lane": "agenda", "status": "claimed",
			"claimed_by_pid": {"pid": 1}, "attempt": 1, "timestamps": {},
			"blocked_by": []}`},
		{"a claim on a claimed row with no pid key at all", "claim", `{
			"job_id": "task-mal17", "lane": "agenda", "status": "claimed",
			"attempt": 1, "timestamps": {}, "blocked_by": []}`},
		{"a claim on a claimed row whose pid is dead", "claim", `{
			"job_id": "task-mal18", "lane": "agenda", "status": "claimed",
			"claimed_by_pid": 999999, "attempt": 1, "timestamps": {},
			"blocked_by": []}`},

		// blocked_by — the ONE field rounds 3 and 4 never opened, because
		// every fixture in this file spells `"blocked_by": []`. Python
		// ITERATES the raw value: a str goes by character, a dict by key,
		// and None/int/float/bool are a TypeError. A type assertion to a
		// list answered "no dependencies" for all of them, so the port
		// claimed and rewrote rows CPython refuses to touch.
		{"a claim on a row whose blocked_by is null", "claim", `{
			"job_id": "task-mal19", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": null}`},
		{"a claim on a row whose blocked_by is a number", "claim", `{
			"job_id": "task-mal20", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": 5}`},
		{"a claim on a row whose blocked_by is a bool", "claim", `{
			"job_id": "task-mal21", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": true}`},
		// Iterable, so no TypeError — but each CHARACTER is a dep id, and
		// the first one names a file that does not exist.
		{"a claim on a row whose blocked_by is a string", "claim", `{
			"job_id": "task-mal22", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": "abc"}`},
		{"a claim on a row whose blocked_by is a dict", "claim", `{
			"job_id": "task-mal23", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": {"dep-x": 1}}`},
		// A non-string ELEMENT is not skipped: it is interpolated into a
		// path by an f-string, so it blocks the claim as a missing dep.
		{"a claim on a row whose blocked_by holds a number", "claim", `{
			"job_id": "task-mal24", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": [5]}`},
		{"a claim on a row whose blocked_by holds a null", "claim", `{
			"job_id": "task-mal25", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": [null]}`},
		{"a claim on a row whose blocked_by holds a dict", "claim", `{
			"job_id": "task-mal26", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": [{"a": 1}]}`},
		// The empty container, which IS iterable and blocks nothing — so
		// the table is not "every odd blocked_by refuses".
		{"a claim on a row whose blocked_by is an empty string", "claim", `{
			"job_id": "task-mal27", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": ""}`},
		{"a claim on a row with no blocked_by key at all", "claim", `{
			"job_id": "task-mal28", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}}`},

		{"a complete on a row with no artifact_paths", "complete", `{
			"job_id": "task-mal09", "lane": "agenda", "status": "claimed",
			"attempt": 1, "timestamps": {}, "blocked_by": []}`},
		{"a complete on a row whose artifact_paths is a list", "complete", `{
			"job_id": "task-mal10", "lane": "agenda", "status": "claimed",
			"attempt": 1, "artifact_paths": [], "timestamps": {},
			"blocked_by": []}`},
		{"a complete on a row with no timestamps", "complete", `{
			"job_id": "task-mal11", "lane": "agenda", "status": "claimed",
			"attempt": 1, "artifact_paths": {}, "blocked_by": []}`},

		{"a fail on a row with no timestamps", "fail", `{
			"job_id": "task-mal12", "lane": "agenda", "status": "claimed",
			"attempt": 1, "artifact_paths": {}, "blocked_by": []}`},

		// BYTES, not JSON — the axis every other row in this table shares a
		// blind spot on, because each of them is valid UTF-8 by
		// construction. `read_text(encoding="utf-8")` refuses a file with an
		// undecodable byte and no verb touches it; Go's decoder substitutes
		// U+FFFD, hands the caller an ordinary row, and the next write
		// re-encodes the replacements onto disk. The unchanged-file
		// assertion below is the one that matters here: this is not two
		// runtimes disagreeing about a value, it is one of them destroying
		// bytes the other can still see.
		//
		// Both message widths, so the port's decoder is pinned on the
		// sentence and not only on the refusal.
		{"a claim on a row holding an undecodable byte", "claim",
			"{\n\t\t\t\"job_id\": \"task-mal29\", \"lane\": \"agenda\", " +
				"\"status\": \"queued\",\n\t\t\t\"attempt\": 0, " +
				"\"note\": \"\xff\", \"timestamps\": {}, \"blocked_by\": []}"},
		{"a claim on a row truncated mid-sequence", "claim",
			"{\n\t\t\t\"job_id\": \"task-mal30\", \"lane\": \"agenda\", " +
				"\"status\": \"queued\",\n\t\t\t\"attempt\": 0, " +
				"\"note\": \"\xf0\x9f\", \"timestamps\": {}, \"blocked_by\": []}"},

		// The healthy row, so the table is not one-sided: without it every
		// assertion below would hold for a package that raised on
		// everything.
		{"a claim on a well-formed row", "claim", `{
			"job_id": "task-mal13", "lane": "agenda", "status": "queued",
			"attempt": 0, "artifact_paths": {}, "timestamps": {},
			"blocked_by": []}`},
		// archive was the sibling rounds 3 to 5 never enumerated. It
		// subscripts `task["status"]` and interpolates the RAW value into
		// its message, and the port read both through GetString — which
		// answers "" for a missing key and for every non-string, so a
		// foreign row got a message naming no status at all and a
		// RuntimeError where CPython raises KeyError (adversarial r11
		// round 6, MEDIUM).
		{"an archive on a row with no status", "archive", `{
			"job_id": "task-mal29", "lane": "agenda",
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a row whose status is a number", "archive", `{
			"job_id": "task-mal30", "lane": "agenda", "status": 5,
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a row whose status is null", "archive", `{
			"job_id": "task-mal31", "lane": "agenda", "status": null,
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a row whose status is a bool", "archive", `{
			"job_id": "task-mal32", "lane": "agenda", "status": true,
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a row whose status is a list", "archive", `{
			"job_id": "task-mal33", "lane": "agenda", "status": ["done"],
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a row whose status is a float", "archive", `{
			"job_id": "task-mal34", "lane": "agenda", "status": 2.0,
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a row whose status is a dict", "archive", `{
			"job_id": "task-mal35", "lane": "agenda", "status": {"a": 1},
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		// The queued case, which is the ordinary refusal, and the two the
		// verb ACCEPTS — a table where every row raises never proves the
		// accepting arm still accepts.
		{"an archive on a queued row", "archive", `{
			"job_id": "task-mal36", "lane": "agenda", "status": "queued",
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a done row", "archive", `{
			"job_id": "task-mal37", "lane": "agenda", "status": "done",
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a failed row", "archive", `{
			"job_id": "task-mal38", "lane": "agenda", "status": "failed",
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		// A row that is not a mapping AT ALL belongs to the sweep
		// differential's archive verb, not here: this harness reads the
		// fixture's own job_id out of the JSON to name the file, so a
		// fixture with no mapping has no name.
	} {
		t.Run(c.name, func(t *testing.T) {
			var row map[string]any
			if err := json.Unmarshal([]byte(c.row), &row); err != nil {
				t.Fatal(err)
			}
			jobID, _ := row["job_id"].(string)
			if jobID == "" {
				t.Fatal("every fixture needs a job_id")
			}

			seed := func(ws string) {
				if err := os.MkdirAll(TasksDir(ws), 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(TaskPath(ws, jobID),
					[]byte(c.row), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			pyWS, goWS := t.TempDir(), t.TempDir()
			seed(pyWS)
			seed(goWS)

			var want struct {
				OK        bool   `json:"ok"`
				Cls       string `json:"cls"`
				Msg       string `json:"msg"`
				Unchanged bool   `json:"unchanged"`
				// A POINTER: archive's success removes the file, and the
				// probe answers null for it. A plain string would read
				// that as "" and compare it against real bytes.
				After *string `json:"after"`
			}
			pyprobe.Probe{
				Marker:    "task_store.py",
				Workspace: pyWS,
				Guard:     tasksGuard,
			}.RunJSON(t, pyMalformedSrc, &want, c.verb, jobID)

			var gotErr error
			switch c.verb {
			case "claim":
				_, gotErr = Claim(goWS, jobID, os.Getpid())
			case "complete":
				_, gotErr = Complete(goWS, jobID,
					pyval.Obj{{Key: "a", Val: "/x/y"}}, "success")
			case "fail":
				_, gotErr = Fail(goWS, jobID, "boom")
			case "archive":
				_, gotErr = Archive(goWS, jobID)
			default:
				t.Fatalf("unknown verb %q", c.verb)
			}

			if (gotErr == nil) != want.OK {
				if gotErr != nil {
					t.Fatalf("%s raised %v; CPython succeeded", c.verb, gotErr)
				}
				t.Fatalf("%s succeeded; CPython raises %s: %s",
					c.verb, want.Cls, want.Msg)
			}

			if !want.OK {
				cls := pyval.ClassOf(gotErr)
				if cls == "" {
					t.Errorf("%s raised %v, which carries no exception class; "+
						"CPython raises %s: %s", c.verb, gotErr, want.Cls, want.Msg)
				} else {
					if cls != want.Cls {
						t.Errorf("%s raises %s, CPython raises %s",
							c.verb, cls, want.Cls)
					}
					if gotErr.Error() != want.Msg {
						t.Errorf("%s message = %q, CPython says %q",
							c.verb, gotErr.Error(), want.Msg)
					}
				}
				// The FILE is the half that matters to the next reader. A
				// raise that still rewrote the row would leave the two
				// runtimes disagreeing about whether the task is claimable,
				// and the exception class alone cannot show that.
				if !want.Unchanged {
					t.Fatalf("CPython raised AND rewrote the file — this "+
						"test's premise is wrong for %q", c.name)
				}
				raw, err := os.ReadFile(TaskPath(goWS, jobID))
				if err != nil {
					t.Fatalf("the port removed the task file on a raise: %v", err)
				}
				if string(raw) != c.row {
					t.Errorf("the port rewrote the row CPython left alone:\n"+
						" go: %s\n was: %s", raw, c.row)
				}
				return
			}

			// The success half is compared by BYTES, minus the two fields
			// that cannot match across runtimes.
			//
			// archive UNLINKS the row it accepted, so "gone" is a real
			// success outcome. The probe reports it as a null `after`, and
			// the two runtimes have to agree about the DISAPPEARANCE as
			// well as about the bytes — a port that archived without
			// removing would otherwise pass here.
			_, statErr := os.Stat(TaskPath(goWS, jobID))
			if want.After == nil {
				if statErr == nil {
					t.Fatalf("%s left the row in place; CPython removed it",
						c.verb)
				}
				return
			}
			if statErr != nil {
				t.Fatalf("%s removed the row; CPython left it: %v",
					c.verb, statErr)
			}
			raw, err := os.ReadFile(TaskPath(goWS, jobID))
			if err != nil {
				t.Fatal(err)
			}
			if normTask(t, string(raw)) != normTask(t, *want.After) {
				t.Errorf("the written row is not CPython's:\n go: %s\n py: %s",
					normTask(t, string(raw)), normTask(t, *want.After))
			}
		})
	}
}

// normTask blanks the pid and every timestamp — a wall clock and a process
// id are not comparable across two runtimes, and everything else in the row
// is.
//
// It decodes and re-encodes through pyval rather than encoding/json, and the
// difference is the whole point. A `map[string]any` round-trip erases two
// axes this file's own table exists to pin:
//
//   - NUMBER SPELLING. Every JSON number lands as float64 and re-encodes in
//     Go's shortest form, so Python's `3.0` and a port's `3` both render as
//     `3`. The "attempt is a float" fixture is there because `task["attempt"]
//     += 1` is Python's +, which keeps a float a float — and under the old
//     normalizer that fixture could not fail. Measured: `{"attempt":3.0}`
//     round-tripped to `{"attempt":3}`.
//   - KEY ORDER. Go marshals a map with its keys SORTED; Python writes the
//     dict's insertion order. A port that rebuilt the row in a different
//     order wrote a file no byte-comparison would accept, and passed here.
//
// pyval.LoadsOrdered keeps both (ordered fields, json.Number), and
// DumpsCompactPy is CPython's own separators and escaping — so the claim
// that this comparison is byte-level is true of everything left after the
// two masks.
func normTask(t *testing.T, raw string) string {
	t.Helper()
	v, err := pyval.LoadsOrdered(raw)
	if err != nil {
		t.Fatalf("the row is not JSON: %v\n%s", err, raw)
	}
	o, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("the row is not an object: %s", raw)
	}
	out := pyval.Obj{}
	for _, f := range o {
		switch {
		case f.Key == "claimed_by_pid":
			continue // a process id is not comparable across runtimes
		case f.Key == "timestamps":
			// Only a MAPPING of stamps is masked. A row whose timestamps is
			// a string or a list is a fixture in this table, and its value
			// has to keep comparing exactly.
			if ts, isObj := f.Val.(pyval.Obj); isObj {
				masked := pyval.Obj{}
				for _, kv := range ts {
					masked = append(masked, pyval.Field{Key: kv.Key, Val: "<ts>"})
				}
				out = append(out, pyval.Field{Key: f.Key, Val: masked})
				continue
			}
			out = append(out, f)
		default:
			out = append(out, f)
		}
	}
	s, err := pyval.DumpsCompactPy(out)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// The resolver under test is task_store's own, so that is what gets
// asserted — the rule from the 2026-08-16 live-ledger overwrite is that a
// writing probe checks where it is about to write, not that it trusts the
// environment variable it was handed.
const tasksGuard = `
import os as _o
import task_store as _ts
_d = str(_ts._tasks_dir())
if not _d.startswith(_o.path.realpath(_o.environ["MARO_WORKSPACE"])) and \
   not _d.startswith(_o.environ["MARO_WORKSPACE"]):
    raise SystemExit("refusing to run: _tasks_dir() resolved to %s" % _d)
`

var _ = filepath.Join
