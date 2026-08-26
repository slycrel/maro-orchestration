package tasks

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
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
			"job_id": "task-mal33", "lane": "agenda",
			"attempt": 0, "timestamps": {}, "blocked_by": []}`},
		{"an archive on a row whose status is a number", "archive", `{
			"job_id": "task-mal34", "lane": "agenda", "status": 5,
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

// pyDepSrc measures what `_read_task` does when the dependency path cannot
// even be STATTED. `Path.exists()` is
//
//	try: self.stat() except (OSError, ValueError): return False
//
// so it swallows every stat failure, not just ENOENT — and `blocked_by` is
// a foreign-writable list, so those paths are reachable from a file another
// box wrote. Three shapes cover the classes: a component past NAME_MAX
// (ENAMETOOLONG), a path descending through a regular file (ENOTDIR), and
// an embedded NUL (ValueError, which is not an OSError at all).
const pyDepSrc = `
import json, sys
import task_store

deps = json.loads(sys.argv[1])
task_store._tasks_dir().mkdir(parents=True, exist_ok=True)
(task_store._tasks_dir() / "seed.json").write_text("{}")
out = {}
try:
    task_store.enqueue("dep probe", job_id="n1", blocked_by=deps)
    out["ok"] = True
except BaseException as e:
    out["ok"] = False
    out["cls"] = type(e).__name__
    out["msg"] = str(e)
out["written"] = task_store.task_path("n1").exists()
print(json.dumps(out))
`

// TestAnUnstattableDependencyIsAbsentNotAnError pins the ENOENT-only guard
// that adversarial tasks-r1 found: CPython accepts these rows and this port
// refused them, so the two runtimes disagreed about which work the queue
// would ever run.
func TestAnUnstattableDependencyIsAbsentNotAnError(t *testing.T) {
	for _, c := range []struct {
		name string
		dep  string
	}{
		{"a dependency id past NAME_MAX", strings.Repeat("d", 300)},
		{"a dependency id descending through a regular file", "seed.json/inner"},
		{"a dependency id with an embedded NUL", "dep\x00x"},
	} {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			// The queue DIRECTORY has to exist before the cycle check runs.
			// Without it the path walk fails at the missing parent with
			// ENOENT — which os.IsNotExist DOES catch — so two of the three
			// fixtures passed against the unfixed code and tested nothing.
			// (Caught by running them against the pre-fix source, which is
			// the only thing that distinguishes a guard from a decoration.)
			// The ENOTDIR case additionally needs a regular file to descend
			// through, and seeding both in BOTH trees is what makes the two
			// runtimes comparable.
			seed := func(root string) {
				td := filepath.Dir(TaskPath(root, "seed"))
				if err := os.MkdirAll(td, 0o777); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(filepath.Join(td, "seed.json"),
					[]byte("{}"), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			seed(dir)

			pyWS := t.TempDir()
			seed(pyWS)
			probe := pyprobe.Probe{Marker: "task_store.py", Workspace: pyWS,
				Guard: tasksGuard}
			var py struct {
				OK      bool   `json:"ok"`
				Cls     string `json:"cls"`
				Msg     string `json:"msg"`
				Written bool   `json:"written"`
			}
			probe.RunJSON(t, pyDepSrc, &py, pyprobe.Arg(t, []string{c.dep}))
			if !py.OK || !py.Written {
				t.Fatalf("CPython refused the row (%s: %s) — if that is now "+
					"true, this port's ENOENT-only guard was right and the "+
					"stat-swallow in readRaw must come back out", py.Cls, py.Msg)
			}

			if _, err := Enqueue(dir, Options{JobID: "n1", BlockedBy: []string{c.dep}}); err != nil {
				t.Fatalf("CPython accepted this row and wrote n1.json; Go "+
					"raised %v", err)
			}
			if _, err := os.Stat(TaskPath(dir, "n1")); err != nil {
				t.Fatalf("n1.json was not written: %v", err)
			}
		})
	}
}

// pySurrogateSrc drives `fail` over a row carrying an ESCAPED lone
// surrogate. The escape is valid UTF-8 in the file — it is seven ASCII
// bytes — so no decoder refuses it on the way in.
const pySurrogateSrc = `
import json, sys
import task_store

p = task_store.task_path("t1")
before = p.read_bytes()
out = {}
try:
    task_store.fail("t1", "boom")
    out["ok"] = True
except BaseException as e:
    out["ok"] = False
    out["cls"] = type(e).__name__
    out["msg"] = str(e)
after = p.read_bytes()
out["unchanged"] = before == after
out["status"] = json.loads(after.decode("utf-8", "replace"))["status"]
print(json.dumps(out))
`

// TestAnEscapedLoneSurrogateIsANamedDivergence pins a divergence this port
// cannot close without a rewrite it has not earned yet — and pins it in the
// direction that makes the day it IS closed loud.
//
// CPython: `json.loads` happily produces the str `x\ud800y` (Python strings
// may hold lone surrogates), and then `write_text(encoding="utf-8")` cannot
// ENCODE it. `_atomic_write`'s `except BaseException` unlinks the temp file,
// so the verb raises UnicodeEncodeError and the row is byte-identical
// afterwards — still `claimed`.
//
// This port: `encoding/json` substitutes U+FFFD at decode, Go strings cannot
// hold a lone surrogate at all, and by write time the information is gone.
// The verb SUCCEEDS, the row becomes `failed`, and `x\ud800y` is rewritten
// as `x�y` — bytes nobody can recover.
//
// So the two runtimes disagree about whether the verb succeeded, about the
// task's state, and about the row's contents. That is worse than the
// cosmetic residual `pyval` already names (which describes the
// ensure_ascii=TRUE writer, a different writer with a different outcome).
//
// The fix is a surrogate-preserving decoder in pyval plus an encodeString
// that can re-emit `\udXXX` — named in pyval.go and in BACKLOG. It is NOT
// a guard in readRaw: CPython's READ succeeds, so refusing here would break
// list_tasks and status_summary, which is a THIRD behaviour rather than
// either runtime's.
func TestAnEscapedLoneSurrogateIsANamedDivergence(t *testing.T) {
	const row = `{"job_id": "t1", "status": "claimed", "attempt": 1,` +
		` "timestamps": {"queued_at_utc": "2026-01-01T00:00:00Z"},` +
		` "note": "x\ud800y"}`

	// --- what CPython does, measured every run rather than recalled ---
	pyWS := t.TempDir()
	pyPath := filepath.Join(pyWS, "output", "queues", "tasks", "t1.json")
	if err := os.MkdirAll(filepath.Dir(pyPath), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(pyPath, []byte(row+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	probe := pyprobe.Probe{Marker: "task_store.py", Workspace: pyWS,
		Guard: tasksGuard}
	var py struct {
		OK        bool   `json:"ok"`
		Cls       string `json:"cls"`
		Unchanged bool   `json:"unchanged"`
		Status    string `json:"status"`
	}
	probe.RunJSON(t, pySurrogateSrc, &py)
	if py.OK || py.Cls != "UnicodeEncodeError" || !py.Unchanged ||
		py.Status != "claimed" {
		t.Fatalf("CPython's behaviour CHANGED: ok=%v cls=%s unchanged=%v "+
			"status=%s — want a UnicodeEncodeError over an untouched, "+
			"still-claimed row. Re-derive this divergence before trusting "+
			"the assertion below.", py.OK, py.Cls, py.Unchanged, py.Status)
	}

	// --- what this port does, and the day it stops, this test goes red ---
	dir := t.TempDir()
	goPath := TaskPath(dir, "t1")
	if err := os.MkdirAll(filepath.Dir(goPath), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(goPath, []byte(row+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Fail(dir, "t1", "boom"); err != nil {
		t.Fatalf("the divergence is CLOSING: Fail raised %v where it used "+
			"to succeed. If pyval grew a surrogate-preserving decoder, this "+
			"test is what should now assert AGREEMENT — check the class is "+
			"UnicodeEncodeError and the row is untouched, then delete this "+
			"whole test in favour of an ordinary malformed_diff fixture.", err)
	}
	after, err := os.ReadFile(goPath)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(after), "�") {
		t.Fatalf("the surrogate no longer becomes U+FFFD; the decoder "+
			"changed and this divergence needs re-deriving:\n%s", after)
	}
	if !strings.Contains(string(after), `"status": "failed"`) {
		t.Fatalf("Go wrote the row but not the status change:\n%s", after)
	}
}
