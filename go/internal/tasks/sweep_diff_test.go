package tasks

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The three SWEEPS — list_tasks, status_summary, recover_stale_claims —
// read every file in the queue directory, and what they do with a row they
// cannot understand is a contract: CPython's `_read_task` is bare
// `json.loads`, so a file holding the literal `null` comes back as None and
// is indistinguishable from a missing file to all six callers. One such row
// is SKIPPED. A port that refuses it aborts the sweep instead, and a drain
// loop then sees an erroring queue while every genuinely queued task
// stalls (adversarial r11 round 4, MEDIUM).
//
// recover_stale_claims is the other half: it reads `claimed_by_pid` RAW and
// hands it to `_pid_alive`, whose first line is `pid <= 0` — a comparison
// that RAISES for a string. One row with a string pid aborts the whole
// sweep in CPython, where a port reading it through an int helper answered
// 0, called the claim stale, and released a task CPython never touches.
const pySweepSrc = `
import json, os, sys
import task_store

def _try(fn):
    try:
        return {"ok": True, "value": fn()}
    except BaseException as e:
        return {"ok": False, "cls": type(e).__name__, "msg": str(e)}

def _files():
    out = {}
    for p in sorted(task_store._tasks_dir().glob("*.json")):
        out[p.name] = p.read_text(encoding="utf-8")
    return out

verb = sys.argv[1]
if verb == "list":
    ans = _try(lambda: [t.get("job_id") for t in task_store.list_tasks()])
elif verb == "list_queued":
    ans = _try(lambda: [t.get("job_id")
                        for t in task_store.list_tasks(status_filter="queued")])
elif verb == "summary":
    ans = _try(lambda: {json.dumps(k): v
                        for k, v in task_store.status_summary().items()})
elif verb == "recover":
    ans = _try(task_store.recover_stale_claims)
else:
    raise SystemExit("unknown verb " + verb)

ans["files"] = _files()
print(json.dumps(ans))
`

func TestTheQueueSweepsMatchCPython(t *testing.T) {
	// A pid that is certainly not running: the max on Linux is well under
	// this, so the row is genuinely stale on both sides.
	const deadPID = "999999"

	for _, c := range []struct {
		name string
		verb string
		rows map[string]string
	}{
		{"a null row is skipped by list", "list", map[string]string{
			"t1": `null`,
			"t2": `{"job_id":"t2","status":"queued","lane":"agenda",
				"attempt":0,"timestamps":{},"blocked_by":[]}`,
		}},
		{"a null row is skipped by the filtered list", "list_queued",
			map[string]string{
				"t1": `null`,
				"t2": `{"job_id":"t2","status":"queued","lane":"agenda",
					"attempt":0,"timestamps":{},"blocked_by":[]}`,
			}},
		{"a null row is skipped by the summary", "summary", map[string]string{
			"t1": `null`,
			"t2": `{"job_id":"t2","status":"queued","lane":"agenda",
				"attempt":0,"timestamps":{},"blocked_by":[]}`,
		}},
		{"a null row is skipped by the stale sweep", "recover",
			map[string]string{
				"t1": `null`,
				"t2": `{"job_id":"t2","status":"claimed","claimed_by_pid":` +
					deadPID + `,"lane":"agenda","attempt":1,
					"timestamps":{},"blocked_by":[]}`,
			}},

		// The status key is the RAW value, and json.dumps renders a
		// non-string key by its JSON spelling. Folding them to "unknown"
		// merges buckets CPython keeps apart.
		{"a numeric status is its own bucket", "summary", map[string]string{
			"t1": `{"job_id":"t1","status":5,"lane":"agenda","attempt":0,
				"timestamps":{},"blocked_by":[]}`,
			"t2": `{"job_id":"t2","status":null,"lane":"agenda","attempt":0,
				"timestamps":{},"blocked_by":[]}`,
			"t3": `{"job_id":"t3","status":true,"lane":"agenda","attempt":0,
				"timestamps":{},"blocked_by":[]}`,
			"t4": `{"job_id":"t4","lane":"agenda","attempt":0,
				"timestamps":{},"blocked_by":[]}`,
		}},

		// A dict is UNHASHABLE, so `counts[status] = ...` raises TypeError
		// and the whole summary is gone. Folding it into an "unknown"
		// bucket answers a number CPython never produces, on a surface an
		// operator reads to decide whether the queue is healthy.
		{"an unhashable status raises out of the summary", "summary",
			map[string]string{
				"t1": `{"job_id":"t1","status":{"a":1},"lane":"agenda",
					"attempt":0,"timestamps":{},"blocked_by":[]}`,
			}},
		{"a list status raises out of the summary", "summary",
			map[string]string{
				"t1": `{"job_id":"t1","status":["queued"],"lane":"agenda",
					"attempt":0,"timestamps":{},"blocked_by":[]}`,
			}},

		// The pid comparison. A string pid raises out of the sweep and
		// leaves its row exactly as it was.
		//
		// ONE row on purpose: recover_stale_claims iterates an UNSORTED
		// glob in CPython and a sorted one here, so a fixture with a
		// raising row beside a releasable one asserts a filesystem
		// ordering, not a behaviour — it passed or failed on readdir
		// order. A fixture whose answer depends on something neither
		// runtime promises is a fixture that tests nothing dependable.
		{"a string pid aborts the stale sweep", "recover", map[string]string{
			"t3": `{"job_id":"t3","status":"claimed","claimed_by_pid":"1234",
				"lane":"agenda","attempt":1,"timestamps":{},"blocked_by":[]}`,
		}},
		{"a dead numeric pid alone is released", "recover", map[string]string{
			"t4": `{"job_id":"t4","status":"claimed","claimed_by_pid":` +
				deadPID + `,"lane":"agenda","attempt":1,
				"timestamps":{},"blocked_by":[]}`,
		}},
		{"a falsy pid is left claimed", "recover", map[string]string{
			"t5": `{"job_id":"t5","status":"claimed","claimed_by_pid":0,
				"lane":"agenda","attempt":1,"timestamps":{},"blocked_by":[]}`,
			"t6": `{"job_id":"t6","status":"claimed","claimed_by_pid":null,
				"lane":"agenda","attempt":1,"timestamps":{},"blocked_by":[]}`,
		}},
		// `pid <= 0` sees a BOOL as an int: False is 0 and takes the
		// early return, True is 1 and reaches os.kill — which on pid 1
		// answers PermissionError, caught by `except OSError: return
		// True`. A port that handed False to os.kill(0, 0) would signal
		// its OWN process group and call the claim live.
		{"a false pid is left claimed", "recover", map[string]string{
			"t8": `{"job_id":"t8","status":"claimed","claimed_by_pid":false,
				"lane":"agenda","attempt":1,"timestamps":{},"blocked_by":[]}`,
		}},
		{"a true pid is pid 1", "recover", map[string]string{
			"t9": `{"job_id":"t9","status":"claimed","claimed_by_pid":true,
				"lane":"agenda","attempt":1,"timestamps":{},"blocked_by":[]}`,
		}},
		// os.kill takes a C int. A pid past that range is an OverflowError
		// out of the sweep, not a wrapped small number that might match a
		// LIVE process — the wrap is the dangerous half, because releasing
		// a claim held by a running worker is how two workers get the same
		// task.
		{"a pid past C int aborts the sweep", "recover", map[string]string{
			"ta": `{"job_id":"ta","status":"claimed",
				"claimed_by_pid":100000000000000000000000,
				"lane":"agenda","attempt":1,"timestamps":{},"blocked_by":[]}`,
		}},

		// The other side of that bound, which the overflow row alone
		// cannot show: `pid <= 0` returns False BEFORE os.kill is
		// reached, so a hugely negative pid is dead rather than an
		// OverflowError. A port that tested the magnitude first would
		// abort the sweep here.
		{"a hugely negative pid is dead, not an overflow", "recover",
			map[string]string{
				"tb": `{"job_id":"tb","status":"claimed",
					"claimed_by_pid":-100000000000000000000000,
					"lane":"agenda","attempt":1,"timestamps":{},
					"blocked_by":[]}`,
			}},
		// Below the C-int range but INSIDE int64, which the wide-literal
		// row cannot reach: it returns on its sign before pidAliveInt is
		// called at all, so only this row can see a symmetric bound.
		{"a negative pid inside int64 is dead, not an overflow", "recover",
			map[string]string{
				"tj": `{"job_id":"tj","status":"claimed",
					"claimed_by_pid":-10000000000,"lane":"agenda","attempt":1,
					"timestamps":{},"blocked_by":[]}`,
			}},

		// And the C-int bound itself, from the side that does NOT raise:
		// with only the past-the-bound row, `pid >= 2**31` and
		// `pid > 2**31` both pass.
		{"the largest pid os.kill accepts", "recover", map[string]string{
			"tc": `{"job_id":"tc","status":"claimed","claimed_by_pid":2147483647,
				"lane":"agenda","attempt":1,"timestamps":{},"blocked_by":[]}`,
		}},
		{"one past the largest pid os.kill accepts", "recover",
			map[string]string{
				"td": `{"job_id":"td","status":"claimed",
					"claimed_by_pid":2147483648,"lane":"agenda","attempt":1,
					"timestamps":{},"blocked_by":[]}`,
			}},

		// `if pid and not _pid_alive(pid)` SHORT-CIRCUITS, and three
		// falsy values raise inside _pid_alive: "", [] and {} are all a
		// TypeError at `pid <= 0`. Python never gets there; a port that
		// evaluates both sides first aborts the sweep on a row CPython
		// walks straight past.
		{"an empty-string pid is falsy, not a raise", "recover",
			map[string]string{
				"te": `{"job_id":"te","status":"claimed","claimed_by_pid":"",
					"lane":"agenda","attempt":1,"timestamps":{},
					"blocked_by":[]}`,
			}},
		{"an empty-list pid is falsy, not a raise", "recover",
			map[string]string{
				"tf": `{"job_id":"tf","status":"claimed","claimed_by_pid":[],
					"lane":"agenda","attempt":1,"timestamps":{},
					"blocked_by":[]}`,
			}},
		{"an empty-dict pid is falsy, not a raise", "recover",
			map[string]string{
				"tg": `{"job_id":"tg","status":"claimed","claimed_by_pid":{},
					"lane":"agenda","attempt":1,"timestamps":{},
					"blocked_by":[]}`,
			}},

		// `task["status"]` and `task["job_id"]` are SUBSCRIPTS in the
		// sweep, so a claimed row missing either raises out of it.
		{"a row with no status aborts the sweep", "recover",
			map[string]string{
				"th": `{"job_id":"th","claimed_by_pid":` + deadPID + `,
					"lane":"agenda","attempt":1,"timestamps":{},
					"blocked_by":[]}`,
			}},
		{"a releasable row with no job_id aborts the sweep", "recover",
			map[string]string{
				"ti": `{"status":"claimed","claimed_by_pid":` + deadPID + `,
					"lane":"agenda","attempt":1,"timestamps":{},
					"blocked_by":[]}`,
			}},

		{"a float pid raises where os.kill refuses it", "recover",
			map[string]string{
				"t7": `{"job_id":"t7","status":"claimed",
					"claimed_by_pid":999999.5,"lane":"agenda","attempt":1,
					"timestamps":{},"blocked_by":[]}`,
			}},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			for _, ws := range []string{pyWS, goWS} {
				if err := os.MkdirAll(TasksDir(ws), 0o755); err != nil {
					t.Fatal(err)
				}
				for id, body := range c.rows {
					if err := os.WriteFile(TaskPath(ws, id),
						[]byte(body), 0o644); err != nil {
						t.Fatal(err)
					}
				}
			}

			var want struct {
				OK    bool              `json:"ok"`
				Value json.RawMessage   `json:"value"`
				Cls   string            `json:"cls"`
				Msg   string            `json:"msg"`
				Files map[string]string `json:"files"`
			}
			pyprobe.Probe{
				Marker:    "task_store.py",
				Workspace: pyWS,
				Guard:     tasksGuard,
			}.RunJSON(t, pySweepSrc, &want, c.verb)

			var got any
			var gotErr error
			switch c.verb {
			case "list", "list_queued":
				filter := ""
				if c.verb == "list_queued" {
					filter = "queued"
				}
				var ts []Task
				ts, gotErr = List(goWS, filter)
				ids := []any{}
				for _, tk := range ts {
					v, _ := tk.Get("job_id")
					ids = append(ids, v)
				}
				got = ids
			case "summary":
				var counts map[string]int
				counts, gotErr = StatusSummary(goWS)
				// The probe keys its answer with json.dumps of the raw
				// key, which for a string is a QUOTED string — match it.
				out := map[string]int{}
				for k, v := range counts {
					out[jsonKeyOf(t, k)] = v
				}
				got = out
			case "recover":
				var ids []string
				ids, gotErr = RecoverStaleClaims(goWS)
				out := []any{}
				for _, id := range ids {
					out = append(out, id)
				}
				got = out
			}

			if (gotErr == nil) != want.OK {
				if gotErr != nil {
					t.Fatalf("%s raised %v; CPython answered %s",
						c.verb, gotErr, want.Value)
				}
				t.Fatalf("%s answered %v; CPython raises %s: %s",
					c.verb, got, want.Cls, want.Msg)
			}

			if want.OK {
				// Decoded on both sides rather than compared as text: the
				// summary is a MAPPING, Go marshals map keys sorted and
				// CPython preserves insertion order, and neither order is
				// part of the contract. The lists are compared in order,
				// because theirs is.
				var wantV, gotV any
				if err := json.Unmarshal(want.Value, &wantV); err != nil {
					t.Fatal(err)
				}
				gotRaw, err := json.Marshal(got)
				if err != nil {
					t.Fatal(err)
				}
				if err := json.Unmarshal(gotRaw, &gotV); err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(gotV, wantV) {
					t.Errorf("%s = %s, CPython says %s",
						c.verb, gotRaw, want.Value)
				}
			} else {
				cls := pyval.ClassOf(gotErr)
				if cls == "" {
					t.Errorf("%s raised %v, which carries no exception "+
						"class; CPython raises %s: %s",
						c.verb, gotErr, want.Cls, want.Msg)
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
			}

			// The FILES, always. A sweep's return value says nothing about
			// whether it released a task, and "released a row CPython left
			// claimed" is the divergence that matters to the next claimer.
			for name, pyBody := range want.Files {
				raw, err := os.ReadFile(TasksDir(goWS) + "/" + name)
				if err != nil {
					t.Errorf("the port removed %s: %v", name, err)
					continue
				}
				if normSweep(t, string(raw)) != normSweep(t, pyBody) {
					t.Errorf("%s diverges after %s:\n go: %s\n py: %s",
						name, c.verb, raw, pyBody)
				}
			}
		})
	}
}

// jsonKeyOf renders a Go map key the way the probe rendered CPython's:
// json.dumps of the key object. The Go side already holds the key in its
// JSON-key spelling, so a string key is quoted and everything else is
// passed through as the literal it already is.
func jsonKeyOf(t *testing.T, k string) string {
	t.Helper()
	switch k {
	case "null", "true", "false":
		return k
	}
	if len(k) > 0 && (k[0] == '-' || (k[0] >= '0' && k[0] <= '9')) {
		var probe any
		if err := json.Unmarshal([]byte(k), &probe); err == nil {
			return k
		}
	}
	b, err := json.Marshal(k)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// normSweep parses and re-marshals so key order does not decide the
// comparison — the sweeps rewrite a row wholesale, and it is the CONTENT
// that is the claim here. The malformed-row differential compares bytes.
func normSweep(t *testing.T, raw string) string {
	t.Helper()
	var v any
	if err := json.Unmarshal([]byte(raw), &v); err != nil {
		return raw // `null` and any torn row compare as themselves
	}
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
