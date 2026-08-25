package handlequeue

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/tasks"
)

// drain_task_store is the heartbeat's mouth: whatever it selects runs, and
// whatever it skips waits. Three things in it are decided by Python
// OPERATORS rather than by any explicit policy, and each one is a place a
// port can silently disagree:
//
//   - `t.get("source") in sources` is membership over a TUPLE — `==`
//     against each element, which never raises — while
//     `t.get("job_id") in job_ids` is membership over a SET, which hashes
//     and CAN raise. The two sit in one comprehension joined by `and`, so
//     the short-circuit decides whether a hostile job_id on an unrelated
//     row takes the whole drain down or is never looked at.
//   - the set hashes by VALUE: `True` and `1` and `1.0` are one element,
//     and `4242` is not `"4242"`.
//   - `list_tasks` is called OUTSIDE every try. Python's only guard up
//     there is `except ImportError` around the module import, so a queue
//     holding one row the sweep cannot read aborts the drain and the
//     heartbeat sees it — a port that swallows it turns a broken queue
//     into a silently idle one.
//
// The stub `handle` module below is not a shortcut: handle.py's line 4172
// re-exports handle_task FROM handle_queue, so `_handle_mod.handle_task`
// IS the function under test. Only the terminal `handle()` — the lane this
// port has not reached — is faked, and it is faked on both sides.
const pyDrainSrc = `
import json, os, sys, types
import task_store, handle_queue

_calls = []

class _Res:
    pass

_argv = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

def _make_result():
    r = _Res()
    if "status" in _argv:
        r.status = _argv["status"]
    if "result" in _argv:
        r.result = _argv["result"]
    return r

def _fake_handle(reason, **kw):
    _calls.append(reason)
    return _make_result()

_stub = types.ModuleType("handle")
_stub.handle_task = handle_queue.handle_task
_stub.handle = _fake_handle
sys.modules["handle"] = _stub

def _seed(rows):
    d = task_store._tasks_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name, text in rows:
        (d / name).write_text(text, encoding="utf-8")

def _files():
    out = {}
    d = task_store._tasks_dir()
    if d.exists():
        for p in sorted(d.glob("*.json")):
            out[p.name] = p.read_text(encoding="utf-8")
    return out

def _events():
    import observe
    p = observe._events_path()
    if not p.exists():
        return []
    return [l for l in p.read_text(encoding="utf-8").splitlines() if l]

def _try(fn):
    try:
        return {"ok": True, "value": fn()}
    except BaseException as e:
        return {"ok": False, "cls": type(e).__name__, "msg": str(e)}

verb = sys.argv[1]

if verb == "drain":
    _seed([(r["name"], r["text"]) for r in _argv["rows"]])
    kw = {"dry_run": True}
    if "max_tasks" in _argv:
        kw["max_tasks"] = _argv["max_tasks"]
    if "sources" in _argv:
        kw["sources"] = tuple(_argv["sources"])
    if "job_ids" in _argv:
        kw["job_ids"] = set(_argv["job_ids"]) if _argv["job_ids"] is not None else None
    res = _try(lambda: handle_queue.drain_task_store(**kw))
    print(json.dumps({"res": res, "calls": _calls,
                      "files": _files(), "events": _events()},
                     sort_keys=True))

elif verb == "enqueue":
    res = _try(lambda: handle_queue.enqueue_goals(
        _argv["goals"], sequential=_argv["sequential"]))
    print(json.dumps({"res": res, "files": _files()}, sort_keys=True))

elif verb == "enqueue_one":
    res = _try(lambda: handle_queue.enqueue_goal(
        _argv["goal"], reason=_argv.get("reason", "")))
    print(json.dumps({"res": res, "files": _files()}, sort_keys=True))
`

// pyDrainGuard is the caller's own half of the write refusal: task_store
// resolves its own directory, so the assertion is against the path IT
// answers rather than against the env var this test set.
const pyDrainGuard = `
import os as _os
import task_store as _ts
_want = _os.path.realpath(_os.environ["MARO_WORKSPACE"])
_got = _os.path.realpath(str(_ts._tasks_dir()))
if not _got.startswith(_want):
    raise SystemExit("probe would write to %r, outside %r" % (_got, _want))
`

type drainAnswer struct {
	Res struct {
		OK    bool            `json:"ok"`
		Value json.RawMessage `json:"value"`
		Cls   string          `json:"cls"`
		Msg   string          `json:"msg"`
	} `json:"res"`
	Calls  []any             `json:"calls"`
	Files  map[string]string `json:"files"`
	Events []string          `json:"events"`
}

// volatile masks the three fields neither runtime promises to agree on: a
// wall-clock stamp read a second apart on a loaded box, a uuid4 run_id, and
// the claiming process's own pid. Each is masked by SHAPE, not blanked —
// a row that LOSES the field, or grows a non-uuid there, still diverges,
// because presence is the half that says the row was rewritten at all.
var (
	stampRe = regexp.MustCompile(`"(\w*_at_utc|created_at|updated_at)":\s*"[^"]*"`)
	uuidRe  = regexp.MustCompile(`"run_id":\s*"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"`)
	pidRe   = regexp.MustCompile(`"claimed_by_pid":\s*\d+`)
	tsRe    = regexp.MustCompile(`"ts":\s*"[^"]*"`)
	jobRe   = regexp.MustCompile(`task-\d{8}T\d{6}Z-[0-9a-f]{8}`)
)

func maskVolatile(s string) string {
	s = stampRe.ReplaceAllString(s, `"$1":"<stamp>"`)
	s = uuidRe.ReplaceAllString(s, `"run_id":"<uuid>"`)
	s = pidRe.ReplaceAllString(s, `"claimed_by_pid":<pid>`)
	s = tsRe.ReplaceAllString(s, `"ts":"<stamp>"`)
	return s
}

// maskMinted also masks a MINTED job id, for the enqueue fixtures where
// neither runtime can produce the other's. The id's shape is asserted
// separately, so a port that stopped minting one would still be caught.
func maskMinted(s string) string {
	return jobRe.ReplaceAllString(maskVolatile(s), "<jobid>")
}

func normFiles(t *testing.T, files map[string]string, mask func(string) string) map[string]string {
	t.Helper()
	out := map[string]string{}
	for name, text := range files {
		var v any
		if err := json.Unmarshal([]byte(text), &v); err != nil {
			out[mask(name)] = mask(text)
			continue
		}
		b, err := json.Marshal(v)
		if err != nil {
			t.Fatal(err)
		}
		out[mask(name)] = mask(string(b))
	}
	return out
}

func normLines(lines []string, mask func(string) string) []string {
	out := []string{}
	for _, l := range lines {
		var v any
		if err := json.Unmarshal([]byte(l), &v); err != nil {
			out = append(out, mask(l))
			continue
		}
		b, _ := json.Marshal(v)
		out = append(out, mask(string(b)))
	}
	return out
}

func goFiles(t *testing.T, ws string) map[string]string {
	t.Helper()
	out := map[string]string{}
	paths, err := filepath.Glob(filepath.Join(tasks.TasksDir(ws), "*.json"))
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(paths)
	for _, p := range paths {
		b, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		out[filepath.Base(p)] = string(b)
	}
	return out
}

func goEvents(t *testing.T, ws string) []string {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(ws, "memory", "events.jsonl"))
	if err != nil {
		if os.IsNotExist(err) {
			return []string{}
		}
		t.Fatal(err)
	}
	out := []string{}
	for _, l := range strings.Split(string(b), "\n") {
		if l != "" {
			out = append(out, l)
		}
	}
	return out
}

// row is one seeded task file, written as raw text so a fixture can hold a
// value Go's own encoder would refuse to produce.
type row struct {
	Name string `json:"name"`
	Text string `json:"text"`
}

// baseTask is make_task's shape, and it is the whole shape on purpose.
// `claim` SUBSCRIPTS `task["timestamps"]`, so a row seeded without it is a
// KeyError at the claim and every later line of the drain — the routing,
// the terminal status, the event — is never reached. A fixture set built
// from a partial row reports agreement while testing nothing, which is
// what this one did on its first pass (caught by the battery, not by the
// green run).
func baseTask(jobID, source string) map[string]any {
	return map[string]any{
		"job_id":  jobID,
		"run_id":  "fixed-run-" + jobID,
		"lane":    "agenda",
		"source":  source,
		"reason":  "r-" + jobID,
		"status":  "queued",
		"attempt": 0,

		"parent_job_id":      "",
		"blocked_by":         []any{},
		"continuation_depth": 0,
		"origin":             map[string]any{},
		"timestamps": map[string]any{
			"queued_at_utc":   "2026-01-01T00:00:00Z",
			"claimed_at_utc":  "",
			"finished_at_utc": "",
		},
		"claimed_by_pid": nil,
		"result_status":  "",
		"artifact_paths": []any{},
		"error":          "",
	}
}

func rowOf(t *testing.T, jobID string, m map[string]any) row {
	t.Helper()
	b, err := json.Marshal(m)
	if err != nil {
		t.Fatal(err)
	}
	return row{Name: jobID + ".json", Text: string(b)}
}

// task is a well-formed queued row; over applies field overrides, which is
// how a fixture holds a value make_task would never write.
func task(t *testing.T, jobID, source string, over map[string]any) row {
	t.Helper()
	m := baseTask(jobID, source)
	for k, v := range over {
		m[k] = v
	}
	return rowOf(t, jobID, m)
}

// withoutKey is a fixture that LOSES a field, which is a different
// document from one that never had a schema.
func withoutKey(m map[string]any, key string) map[string]any {
	delete(m, key)
	return m
}

// rawRow is a file whose CONTENT is arbitrary — the shape list_tasks has to
// read before any of drain's own logic runs.
func rawRow(name, text string) row { return row{Name: name, Text: text} }

type drainCase struct {
	name     string
	rows     []row
	maxTasks *int
	sources  *[]string
	jobIDs   *[]any
	status   string
	result   string
}

func TestDrainTaskStoreMatchesCPython(t *testing.T) {
	three, zero, one, neg := 3, 0, 1, -1
	empty := []string{}
	onlyEsc := []string{"loop_escalation"}
	noIDs := []any{}

	over := func(jobID, source string, kv ...any) row {
		m := map[string]any{}
		for i := 0; i+1 < len(kv); i += 2 {
			m[kv[i].(string)] = kv[i+1]
		}
		return task(t, jobID, source, m)
	}
	plain := func(jobID string) row { return task(t, jobID, "user_goal", nil) }

	cases := []drainCase{
		{name: "plain three", rows: []row{plain("j1"), plain("j2"), plain("j3")}},
		{name: "cap at one", maxTasks: &one, rows: []row{plain("j1"), plain("j2")}},
		// max_tasks=0 is `queued[:0]` — the caller asked for none, and a
		// port that reads 0 as "unset" runs three.
		{name: "cap at zero", maxTasks: &zero, rows: []row{plain("j1"), plain("j2")}},
		// `queued[:-1]` drops the LAST element rather than clamping.
		{name: "negative cap", maxTasks: &neg, rows: []row{plain("j1"), plain("j2")}},
		{name: "more queued than cap", maxTasks: &three, rows: []row{
			plain("j1"), plain("j2"), plain("j3"), plain("j4")}},
		// A source outside the tuple is skipped, and an ABSENT source is
		// None, which is not in the tuple either.
		{name: "unknown and absent source", rows: []row{
			over("j1", "hermes_dispatch"),
			rowOf(t, "j2", withoutKey(baseTask("j2", "user_goal"), "source")),
			plain("j3")}},
		// A NUMERIC source: `5 in ("loop_continuation", ...)` is `==`
		// against three strings and never raises.
		{name: "numeric source", rows: []row{
			over("j1", "", "source", 5), plain("j2")}},
		{name: "empty sources tuple", sources: &empty, rows: []row{plain("j1")}},
		{name: "narrowed sources", sources: &onlyEsc, rows: []row{
			plain("j1"), over("j2", "loop_escalation")}},
		// job_ids is a SET. An empty one is not None and drains nothing.
		{name: "empty job_ids set", jobIDs: &noIDs, rows: []row{plain("j1")}},
		{name: "job_ids picks one", jobIDs: &[]any{"j2"}, rows: []row{
			plain("j1"), plain("j2"), plain("j3")}},
		// `4242 in {"4242"}` is False: a set does not compare across the
		// number/string line.
		{name: "job_ids number vs string", jobIDs: &[]any{"4242"}, rows: []row{
			over("4242", "user_goal", "job_id", 4242)}},
		// ...and `True in {1}` IS True, because a set hashes by value.
		//
		// The FILE NAME is what task_path spells for that job_id, not the
		// key: `claim(True)` opens "True.json". A fixture named otherwise
		// fails its claim on both sides and the selection difference it
		// was written to show becomes invisible — which is how the first
		// cut of these three reported agreement while testing nothing.
		{name: "job_ids bool matches one", jobIDs: &[]any{1}, rows: []row{
			over("True", "user_goal", "job_id", true)}},
		// json.RawMessage, because Go's encoder writes float64(1.0) as `1`
		// — which turns this fixture back into the integer one and hides
		// the fold it was written to show.
		{name: "job_ids float matches int", jobIDs: &[]any{1}, rows: []row{
			over("1.0", "user_goal", "job_id", json.RawMessage("1.0"))}},
		// The key PREFIXES are not decoration: without them a string
		// element spelled like a numeric key would match a numeric row,
		// where Python's set keeps the two identities apart.
		{name: "job_ids string never collides with a numeric key",
			jobIDs: &[]any{"i:1"}, rows: []row{
				over("1", "user_goal", "job_id", 1)}},
		{name: "job_ids int matches int", jobIDs: &[]any{1}, rows: []row{
			over("1", "user_goal", "job_id", 1)}},
		{name: "job_ids string matches string", jobIDs: &[]any{"j1", "j9"},
			rows: []row{plain("j1"), plain("j2")}},
		// The comprehension's `and` SHORT-CIRCUITS: this row's job_id is
		// unhashable, but its source never matched, so nothing hashes it.
		{name: "unhashable job_id behind a source miss", jobIDs: &[]any{"j2"}, rows: []row{
			over("j1", "nope", "job_id", []any{1, 2}), plain("j2")}},
		// ...and when the source DOES match, the same row takes the whole
		// drain down. Python does not catch it: the comprehension is above
		// every try in the function.
		{name: "unhashable job_id aborts the drain", jobIDs: &[]any{"j2"}, rows: []row{
			over("j1", "user_goal", "job_id", []any{1, 2}), plain("j2")}},
		{name: "dict job_id aborts the drain", jobIDs: &[]any{"j2"}, rows: []row{
			over("j1", "user_goal", "job_id", map[string]any{"a": 1})}},
		// list_tasks is called outside every try, so a row it cannot read
		// aborts the drain rather than being skipped.
		{name: "junk row aborts list_tasks", rows: []row{
			rawRow("bad.json", `5`), plain("j1")}},
		{name: "string row aborts list_tasks", rows: []row{
			rawRow("bad.json", `"queued"`), plain("j1")}},
		// A file holding `null` is a MISSING task, skipped by the sweep.
		{name: "null row is skipped", rows: []row{
			rawRow("bad.json", `null`), plain("j1")}},
		// The terminal's status decides complete() vs fail().
		{name: "handle returns error", status: "error", result: "boom",
			rows: []row{plain("j1")}},
		{name: "handle returns error with no result", status: "error",
			rows: []row{plain("j1")}},
		// `getattr(_res, "status", "done") or "done"` — an EMPTY status is
		// falsy and becomes "done", not "".
		{name: "handle returns empty status", status: "", rows: []row{plain("j1")}},
		{name: "handle returns a custom status", status: "partial",
			rows: []row{plain("j1")}},
		// The drained event carries the task's raw fields; a reason that
		// cannot be sliced means no row at all.
		{name: "numeric reason kills the event", rows: []row{
			over("j1", "user_goal", "reason", 5)}},
		{name: "dict reason kills the event", rows: []row{
			over("j1", "user_goal", "reason", map[string]any{"a": 1})}},
		{name: "list reason slices", rows: []row{
			over("j1", "user_goal", "reason", []any{"a", "b"})}},
		{name: "long reason is cut at eighty", rows: []row{
			over("j1", "user_goal", "reason", strings.Repeat("x", 200))}},
		{name: "depth rides the detail", rows: []row{
			over("j1", "user_goal", "continuation_depth", 2, "parent_job_id", "p1")}},
		// The detail is str() of the RAW value: a float depth is "2.0".
		{name: "float depth in the detail", rows: []row{
			over("j1", "user_goal", "continuation_depth", 2.5)}},
		{name: "string depth in the detail", rows: []row{
			over("j1", "user_goal", "continuation_depth", "deep")}},
		// ...but the ROUTER reads the same field through int(), inside a
		// narrow except: a float logs as 2, a string falls back to 0, and
		// an infinity is an OverflowError that fails the task.
		{name: "unparseable depth still routes", rows: []row{
			over("j1", "user_goal", "continuation_depth", []any{1})}},
		// A numeric job_id rides the event RAW and names its own file.
		{name: "numeric job_id", rows: []row{
			over("77", "user_goal", "job_id", 77)}},
		// A task that is blocked is still "queued", and the drain does not
		// consult blocked_by at all — claim() is what refuses it.
		{name: "blocked task is claimed and refused", rows: []row{
			over("j1", "user_goal", "blocked_by", []any{"nope"})}},
		// The drain hands complete() a job_id it took from the ROW, and
		// complete() spends it twice under different rules: task_path()
		// str()s it, while `blocked_by.remove()` compares it by VALUE.
		// A numeric 4242 therefore unblocks the dependent listing 4242 and
		// leaves the one listing "4242" blocked — the two dependents here
		// disagree only if the drain passes the value through, so a
		// call-site that collapses it to its spelling first flips them.
		{name: "numeric job_id unblocks by value not spelling", rows: []row{
			over("4242", "user_goal", "job_id", 4242),
			over("dep_num", "user_goal", "blocked_by", []any{4242}),
			over("dep_str", "user_goal", "blocked_by", []any{"4242"})}},
		// The same seam with a float: str(1.0) is "1.0", and only the
		// dependent holding the FLOAT is released.
		{name: "float job_id unblocks by value not spelling", rows: []row{
			over("1.0", "user_goal", "job_id", json.RawMessage("1.0")),
			over("dep_f", "user_goal", "blocked_by", []any{json.RawMessage("1.0")}),
			over("dep_s", "user_goal", "blocked_by", []any{"1.0"})}},
		// A row missing `timestamps` is a KeyError inside claim, which the
		// drain catches: skipped, not processed, and no event.
		{name: "row without timestamps cannot be claimed", rows: []row{
			rowOf(t, "j1", withoutKey(baseTask("j1", "user_goal"), "timestamps")),
			plain("j2")}},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			pyWS := t.TempDir()
			goWS := t.TempDir()

			argv := map[string]any{"rows": c.rows}
			if c.maxTasks != nil {
				argv["max_tasks"] = *c.maxTasks
			}
			if c.sources != nil {
				argv["sources"] = *c.sources
			}
			if c.jobIDs != nil {
				argv["job_ids"] = *c.jobIDs
			}
			if c.status != "" || c.name == "handle returns empty status" {
				argv["status"] = c.status
			}
			if c.result != "" {
				argv["result"] = c.result
			}

			var py drainAnswer
			pyprobe.Probe{Marker: "handle_queue.py", Workspace: pyWS,
				Guard: pyDrainGuard}.
				RunJSON(t, pyDrainSrc, &py, "drain",
					pyprobe.Arg(t, argv))

			// Seed the Go side with the identical bytes.
			dir := tasks.TasksDir(goWS)
			if err := os.MkdirAll(dir, 0o777); err != nil {
				t.Fatal(err)
			}
			for _, r := range c.rows {
				if err := os.WriteFile(filepath.Join(dir, r.Name),
					[]byte(r.Text), 0o644); err != nil {
					t.Fatal(err)
				}
			}

			calls := []any{}
			o := Options{DryRun: true, Log: func(string, ...any) {},
				Fallback: func(_ context.Context, _ string, tk pyval.Obj,
					_ Options) (any, error) {
					r, _ := tk.Get("reason")
					calls = append(calls, pyval.Plain(r))
					return fakeResult{status: c.status,
						hasStatus: c.status != "" || c.name == "handle returns empty status",
						result:    c.result, hasResult: c.result != ""}, nil
				}}
			d := DrainOptions{}
			if c.maxTasks != nil {
				d.MaxTasks, d.HasMaxTasks = *c.maxTasks, true
			}
			if c.sources != nil {
				d.Sources = *c.sources
			}
			if c.jobIDs != nil {
				// NewJobIDSet, not a hand-rolled loop. This test WAS the
				// hand-rolled loop, and it was the only thing in the tree
				// that knew the set is keyed by HashKey rather than by the
				// raw id — so the whole differential passed while the
				// exported field's own documentation said nothing about it
				// and no production caller could have got it right.
				var err error
				d.JobIDs, err = NewJobIDSet(*c.jobIDs...)
				if err != nil {
					t.Fatalf("fixture job ids %v: %v", *c.jobIDs, err)
				}
			}

			n, err := DrainTaskStore(context.Background(), goWS, o, d)

			if py.Res.OK {
				if err != nil {
					t.Fatalf("CPython returned %s, Go raised %s: %v",
						py.Res.Value, pyval.ClassOf(err), err)
				}
				var want int
				if uerr := json.Unmarshal(py.Res.Value, &want); uerr != nil {
					t.Fatal(uerr)
				}
				if n != want {
					t.Errorf("processed: CPython %d, Go %d", want, n)
				}
			} else {
				if err == nil {
					t.Fatalf("CPython raised %s(%s), Go returned %d",
						py.Res.Cls, py.Res.Msg, n)
				}
				if got := pyval.ClassOf(err); got != py.Res.Cls {
					t.Errorf("class: CPython %s, Go %s (%v)",
						py.Res.Cls, got, err)
				}
				if err.Error() != py.Res.Msg {
					t.Errorf("message:\n  CPython %q\n  Go      %q",
						py.Res.Msg, err.Error())
				}
			}

			if !reflect.DeepEqual(normAny(py.Calls), normAny(calls)) {
				t.Errorf("handle() calls:\n  CPython %#v\n  Go      %#v",
					py.Calls, calls)
			}
			wantFiles := normFiles(t, py.Files, maskVolatile)
			gotFiles := normFiles(t, goFiles(t, goWS), maskVolatile)
			if !reflect.DeepEqual(wantFiles, gotFiles) {
				t.Errorf("task files:\n  CPython %v\n  Go      %v",
					wantFiles, gotFiles)
			}
			wantEv := normLines(py.Events, maskVolatile)
			gotEv := normLines(goEvents(t, goWS), maskVolatile)
			if !reflect.DeepEqual(wantEv, gotEv) {
				t.Errorf("events:\n  CPython %v\n  Go      %v", wantEv, gotEv)
			}
		})
	}
}

// normAny round-trips through JSON so a Go int and a CPython int compare
// equal after the probe's own encoding.
func normAny(v any) any {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("<unmarshalable %v>", v)
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		return fmt.Sprintf("<undecodable %s>", b)
	}
	return out
}

// fakeResult stands in for whatever the terminal lane returned. It answers
// the two getattr lookups drain makes, and answers NEITHER when the fixture
// says so — which is the escalation branch's real shape.
type fakeResult struct {
	status    string
	hasStatus bool
	result    string
	hasResult bool
}

func (f fakeResult) StatusAttr() string {
	if !f.hasStatus {
		return ""
	}
	return f.status
}

func (f fakeResult) ResultAttr() any {
	if !f.hasResult {
		return ""
	}
	return f.result
}
