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
    out["ok"] = True
except BaseException as e:
    out["ok"] = False
    out["cls"] = type(e).__name__
    out["msg"] = str(e)

after = p.read_bytes()
out["unchanged"] = before == after
out["after"] = after.decode("utf-8")
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

		// The healthy row, so the table is not one-sided: without it every
		// assertion below would hold for a package that raised on
		// everything.
		{"a claim on a well-formed row", "claim", `{
			"job_id": "task-mal13", "lane": "agenda", "status": "queued",
			"attempt": 0, "artifact_paths": {}, "timestamps": {},
			"blocked_by": []}`},
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
				After     string `json:"after"`
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
				pe, ok := gotErr.(*pyval.PyErr)
				if !ok {
					t.Errorf("%s raised %v, which carries no exception class; "+
						"CPython raises %s: %s", c.verb, gotErr, want.Cls, want.Msg)
				} else {
					if pe.Class != want.Cls {
						t.Errorf("%s raises %s, CPython raises %s",
							c.verb, pe.Class, want.Cls)
					}
					if pe.Msg != want.Msg {
						t.Errorf("%s message = %q, CPython says %q",
							c.verb, pe.Msg, want.Msg)
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
			raw, err := os.ReadFile(TaskPath(goWS, jobID))
			if err != nil {
				t.Fatal(err)
			}
			if normTask(t, string(raw)) != normTask(t, want.After) {
				t.Errorf("the written row is not CPython's:\n go: %s\n py: %s",
					normTask(t, string(raw)), normTask(t, want.After))
			}
		})
	}
}

// normTask blanks the pid and every timestamp — a wall clock and a process
// id are not comparable across two runtimes, and everything else in the row
// is.
func normTask(t *testing.T, raw string) string {
	t.Helper()
	var v map[string]any
	if err := json.Unmarshal([]byte(raw), &v); err != nil {
		t.Fatalf("the row is not JSON: %v\n%s", err, raw)
	}
	delete(v, "claimed_by_pid")
	if ts, ok := v["timestamps"].(map[string]any); ok {
		for k := range ts {
			ts[k] = "<ts>"
		}
	}
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
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
