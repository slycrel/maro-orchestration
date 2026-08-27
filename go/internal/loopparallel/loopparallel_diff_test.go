package loopparallel

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

func srcDirLP(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// The probe drives the REAL _run_steps_parallel and _run_steps_dag with
// two things replaced, and both replacements are the point:
//
//   - _execute_step, because the step body is a different tranche and
//     running it would put an LLM call inside a unit test;
//   - _run_in_step_worktree, because it is not inert. It asks llm for the
//     run-scoped cwd and, if that is a git checkout, PROVISIONS A GIT
//     WORKTREE and merges it back. A test of this port already created a
//     linked worktree of the maro repository and left an autocommit on the
//     branch it checked out (2026-08-27, L56). That happened once and
//     will not happen from here.
//
// Everything else is real: the pool, the ready/unblock arithmetic, the
// timeout arms, the dep-context assembly and every sentence a step that
// never ran is given.
//
// The security scan inside _run_steps_parallel's _run_one is NOT stubbed
// and is NOT ported. The probe deep-copies each canned outcome before
// handing it over and reports every index the scan mutated, so a corpus
// that grows into that surface fails LOUDLY here instead of quietly
// disagreeing with a Go side that has no scan at all.
const parallelProbe = `
import copy, json, os, sys, threading, time
sys.path.insert(0, sys.argv[1])
import loop_parallel as lp

LOCK = threading.Lock()

def run_one(sc):
    execs = []
    labels = []
    pre = {}
    beh = sc.get("beh") or {}

    def stub_wt(step_label, run_fn):
        with LOCK:
            labels.append(step_label)
        return run_fn()

    def stub_exec(**kw):
        idx = kw["step_num"]
        with LOCK:
            execs.append({"idx": idx,
                          "dep_ctx": list(kw["completed_context"]),
                          "total": kw["total_steps"]})
        b = beh.get(str(idx)) or {}
        if b.get("sleep"):
            time.sleep(b["sleep"])
        if b.get("raise_empty"):
            raise ValueError()
        if b.get("raise"):
            raise ValueError(b["raise"])
        out = copy.deepcopy(b.get("out", {"status": "ok", "result": ""}))
        with LOCK:
            pre[idx] = (copy.deepcopy(out), out)
        return out

    lp._execute_step = stub_exec
    lp._run_in_step_worktree = stub_wt

    if sc.get("timeout") is None:
        os.environ.pop("MARO_STEP_TIMEOUT", None)
    else:
        os.environ["MARO_STEP_TIMEOUT"] = sc["timeout"]

    common = dict(goal="g", adapter=object(), ancestry_context="anc",
                  tools=[], verbose=False, max_workers=sc["max_workers"],
                  project_dir="", shared_ctx=None, incremental_context="inc")
    try:
        if sc["kind"] == "dag":
            deps = {int(k): set(v) for k, v in (sc.get("deps") or {}).items()}
            outcomes = lp._run_steps_dag(steps=sc["steps"], deps=deps, **common)
        else:
            outcomes = lp._run_steps_parallel(steps=sc["steps"], **common)
    except BaseException as exc:
        return {"ok": False, "class": type(exc).__name__, "msg": str(exc)}

    # The object the stub HANDED OVER, compared with itself: the scan
    # sanitizes in place. Comparing against outcomes[i-1] instead would
    # also flag a late result the scheduler discarded, which is a
    # scheduling answer and not a scan.
    mutated = sorted(i for i, (before, obj) in pre.items()
                     if json.dumps(before) != json.dumps(obj))
    execs.sort(key=lambda e: e["idx"])
    return {"ok": True,
            "outcomes": [json.dumps(o) for o in outcomes],
            "execs": execs,
            "labels": sorted(labels),
            "scan_mutated": mutated}

print(json.dumps([run_one(sc) for sc in json.loads(sys.stdin.read())]))
`

type lpBeh struct {
	Raise      string
	RaiseEmpty bool
	Sleep      float64
	Out        pyval.Obj
	HasOut     bool
}

type lpScen struct {
	name       string
	kind       string
	steps      []string
	deps       map[int][]int
	maxWorkers int
	timeout    *string
	beh        map[int]lpBeh
	slow       bool
	why        string
}

type lpExec struct {
	Idx    int      `json:"idx"`
	DepCtx []string `json:"dep_ctx"`
	Total  int      `json:"total"`
}

type lpResult struct {
	OK          bool     `json:"ok"`
	Class       string   `json:"class"`
	Msg         string   `json:"msg"`
	Outcomes    []string `json:"outcomes"`
	Execs       []lpExec `json:"execs"`
	Labels      []string `json:"labels"`
	ScanMutated []int    `json:"scan_mutated"`
}

func lpStr(s string) *string { return &s }

// A step's canned outcome. The keys are spelled in the order
// _execute_step's real returns spell them, because the comparison is over
// json.dumps of the whole dict and key ORDER is part of that (L35).
func ocDone(result string) pyval.Obj {
	return pyval.Obj{
		{Key: "status", Val: "done"},
		{Key: "result", Val: result},
		{Key: "summary", Val: "did it"},
		{Key: "tokens_in", Val: 10},
		{Key: "tokens_out", Val: 20},
	}
}

func ocOK(result any) pyval.Obj {
	return pyval.Obj{
		{Key: "status", Val: "ok"},
		{Key: "result", Val: result},
	}
}

// A 620-rune CJK result and a 70-rune CJK step text: the two clips in
// DepContext are Python STRING slices and count code points. A
// byte-counting port keeps a third of each, and the difference lands in
// the downstream step's prompt.
var (
	cjkLongResult = strings.Repeat("研究", 310)
	cjkLongStep   = strings.Repeat("課題", 35)
)

func lpScenarios() []lpScen {
	three := []string{"alpha", "beta", "gamma"}
	return []lpScen{
		// ---- _run_steps_parallel -------------------------------------
		{name: "parallel-happy", kind: "parallel", steps: three, maxWorkers: 3,
			beh: map[int]lpBeh{
				1: {Out: ocOK("a"), HasOut: true},
				2: {Out: ocOK("b"), HasOut: true},
				3: {Out: ocOK("c"), HasOut: true},
			},
			why: "three independent steps, outcomes in index order"},
		{name: "parallel-one-worker", kind: "parallel", steps: three, maxWorkers: 1,
			beh: map[int]lpBeh{1: {Out: ocOK("a"), HasOut: true}},
			why: "min(max_workers, len(steps)) is 1; the other two take the default outcome"},
		{name: "parallel-more-workers-than-steps", kind: "parallel",
			steps: []string{"only"}, maxWorkers: 9,
			why: "min() clamps DOWN, so this is a 1-worker pool and not an error"},
		{name: "parallel-raises", kind: "parallel", steps: three, maxWorkers: 3,
			beh: map[int]lpBeh{2: {Raise: "boom"}},
			why: "str(exc) reaches the operator inside 'parallel execution error: ...'"},
		{name: "parallel-raises-with-no-message", kind: "parallel", steps: three,
			maxWorkers: 3, beh: map[int]lpBeh{2: {RaiseEmpty: true}},
			why: "str(ValueError()) is the EMPTY string, so the sentence ends in a colon and a space"},
		{name: "parallel-all-raise", kind: "parallel", steps: three, maxWorkers: 3,
			beh: map[int]lpBeh{1: {Raise: "a"}, 2: {Raise: "b"}, 3: {Raise: "c"}},
			why: "every index is filled independently; nothing propagates"},
		{name: "parallel-empty-plan", kind: "parallel", steps: nil, maxWorkers: 4,
			why: "min(4, 0) is 0 and ThreadPoolExecutor REFUSES it -- an empty plan RAISES here"},
		{name: "parallel-zero-fan-out", kind: "parallel", steps: three, maxWorkers: 0,
			why: "...and so does a configured fan-out of zero"},
		{name: "parallel-negative-fan-out", kind: "parallel", steps: three, maxWorkers: -1,
			why: "the boundary on the other side of it"},
		{name: "parallel-done-runs-the-security-scan", kind: "parallel",
			steps: []string{"one"}, maxWorkers: 1,
			beh: map[int]lpBeh{1: {Out: ocDone("a plain sentence with nothing to sanitize"), HasOut: true}},
			why: "status done reaches an UNPORTED surface; the probe reports if it changed anything"},
		{name: "parallel-outcome-missing-status", kind: "parallel",
			steps: []string{"one"}, maxWorkers: 1,
			beh: map[int]lpBeh{1: {Out: pyval.Obj{{Key: "result", Val: "r"}}, HasOut: true}},
			why: "the scheduler does not default the status -- it returns the dict it was given"},

		// ---- _run_steps_dag ------------------------------------------
		{name: "dag-chain", kind: "dag", steps: three, maxWorkers: 3,
			deps: map[int][]int{2: {1}, 3: {2}},
			beh: map[int]lpBeh{
				1: {Out: ocOK("first result"), HasOut: true},
				2: {Out: ocOK("second result"), HasOut: true},
				3: {Out: ocOK("third result"), HasOut: true},
			},
			why: "1 -> 2 -> 3; each step sees exactly its DIRECT dep"},
		{name: "dag-diamond", kind: "dag",
			steps: []string{"root", "left", "right", "join"}, maxWorkers: 4,
			deps: map[int][]int{2: {1}, 3: {1}, 4: {2, 3}},
			beh: map[int]lpBeh{
				1: {Out: ocOK("r"), HasOut: true}, 2: {Out: ocOK("l"), HasOut: true},
				3: {Out: ocOK("R"), HasOut: true}, 4: {Out: ocOK("j"), HasOut: true},
			},
			why: "the join sees both, in ASCENDING dep order regardless of completion order"},
		{name: "dag-deps-given-out-of-order", kind: "dag",
			steps: []string{"root", "left", "right", "join"}, maxWorkers: 4,
			deps: map[int][]int{2: {1}, 3: {1}, 4: {3, 2}},
			beh: map[int]lpBeh{
				1: {Out: ocOK("r"), HasOut: true}, 2: {Out: ocOK("l"), HasOut: true},
				3: {Out: ocOK("R"), HasOut: true}, 4: {Out: ocOK("j"), HasOut: true},
			},
			why: "sorted(), so the tag's order does not reach the prompt"},
		{name: "dag-no-deps-at-all", kind: "dag", steps: three, maxWorkers: 3,
			beh: map[int]lpBeh{1: {Out: ocOK("a"), HasOut: true}},
			why: "an empty dep map is the fan-out shape, and dag reaches it too"},
		{name: "dag-dep-out-of-range", kind: "dag", steps: three, maxWorkers: 3,
			deps: map[int][]int{2: {9}},
			beh:  map[int]lpBeh{1: {Out: ocOK("a"), HasOut: true}, 3: {Out: ocOK("c"), HasOut: true}},
			why:  "'[after:9]' on a three-step plan: step 2 NEVER RUNS and nothing raises"},
		{name: "dag-self-dependency", kind: "dag", steps: three, maxWorkers: 3,
			deps: map[int][]int{2: {2}},
			why:  "a step waiting on itself is the same silent stall"},
		{name: "dag-two-step-cycle", kind: "dag", steps: three, maxWorkers: 3,
			deps: map[int][]int{1: {2}, 2: {1}},
			why:  "...and so is a cycle; step 3 still completes"},
		{name: "dag-upstream-raises", kind: "dag", steps: three, maxWorkers: 3,
			deps: map[int][]int{2: {1}, 3: {2}},
			beh:  map[int]lpBeh{1: {Raise: "upstream boom"}},
			why:  "a raising dep does NOT block its dependents: the filler has a result of '' and step 2 runs with an EMPTY dep context"},
		{name: "dag-dep-with-empty-result", kind: "dag",
			steps: []string{"root", "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}},
			beh:  map[int]lpBeh{1: {Out: ocOK(""), HasOut: true}},
			why:  "an empty result contributes NO LINE, not an empty one"},
		{name: "dag-dep-result-is-a-list", kind: "dag",
			steps: []string{"root", "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}},
			beh:  map[int]lpBeh{1: {Out: ocOK(pyval.List{"a", 1, nil, true}), HasOut: true}},
			why:  "`if dep_result:` and `[:600]` are not string operations: a list is truthy, slices, and renders through str()"},
		// The FALSY non-strings, which are what separate `if dep_result:`
		// from a `dep_result == ""` shortcut: both of these skip the line
		// in CPython, and the shortcut renders "[]" for the first and
		// raises TypeError on the second.
		{name: "dag-dep-result-is-an-empty-list", kind: "dag",
			steps: []string{"root", "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}},
			beh:  map[int]lpBeh{1: {Out: ocOK(pyval.List{}), HasOut: true}},
			why:  "an empty list is FALSY and contributes no line"},
		{name: "dag-dep-result-is-zero", kind: "dag",
			steps: []string{"root", "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}},
			beh:  map[int]lpBeh{1: {Out: ocOK(0), HasOut: true}},
			why:  "so is 0 — and it never reaches the slice that would raise"},
		{name: "dag-dep-result-is-an-int", kind: "dag",
			steps: []string{"root", "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}},
			beh:  map[int]lpBeh{1: {Out: ocOK(7), HasOut: true}},
			why:  "7[:600] raises INSIDE _run_one, before _execute_step: 'dag execution error: ...'"},
		{name: "dag-clips-count-code-points", kind: "dag",
			steps: []string{cjkLongStep, "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}},
			beh:  map[int]lpBeh{1: {Out: ocOK(cjkLongResult), HasOut: true}},
			why:  "60 and 600 are CHARACTERS; a byte-counting port keeps a third of each"},
		{name: "dag-empty-plan", kind: "dag", steps: nil, maxWorkers: 4,
			why: "the asymmetry with the fan-out path: no min(), so an empty plan returns []"},
		{name: "dag-zero-fan-out", kind: "dag", steps: three, maxWorkers: 0,
			why: "...but a zero max_workers still refuses"},
		{name: "dag-one-worker-serializes", kind: "dag",
			steps: []string{"root", "left", "right", "join"}, maxWorkers: 1,
			deps: map[int][]int{2: {1}, 3: {1}, 4: {2, 3}},
			beh: map[int]lpBeh{
				1: {Out: ocOK("r"), HasOut: true}, 2: {Out: ocOK("l"), HasOut: true},
				3: {Out: ocOK("R"), HasOut: true}, 4: {Out: ocOK("j"), HasOut: true},
			},
			why: "the same answers through a pool that can only hold one"},

		// ---- the timeout arms ----------------------------------------
		{name: "parallel-timeout", kind: "parallel", steps: three, maxWorkers: 3,
			timeout: lpStr("1"), slow: true,
			beh: map[int]lpBeh{
				1: {Out: ocOK("fast"), HasOut: true},
				2: {Sleep: 3},
				3: {Sleep: 3},
			},
			why: "the sentence and the number in it, MEASURED rather than transcribed"},
		// The ONLY way `dag timeout (Ns)` survives to a reader. The
		// filler is overwritten by any step that finishes normally after
		// the break (see RunStepsDAG), so a step that finishes by
		// RAISING is what leaves it standing: _run_one writes nothing on
		// that path, and the main loop's except is unreachable once the
		// while has broken. Without this row the sentence is in the
		// source and in no answer, which is where the battery found it.
		{name: "dag-timeout-then-the-step-raises", kind: "dag",
			steps: []string{"root", "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}}, timeout: lpStr("1"), slow: true,
			beh: map[int]lpBeh{1: {Sleep: 3, Raise: "late boom"}},
			why: "the timeout filler stands, because the late finish had nothing to write"},
		{name: "dag-timeout", kind: "dag",
			steps: []string{"root", "leaf"}, maxWorkers: 2,
			deps: map[int][]int{2: {1}}, timeout: lpStr("1"), slow: true,
			beh: map[int]lpBeh{1: {Sleep: 3}},
			why: "a dag timeout fills the in-flight step AND leaves its dependent to the unreached filler -- two DIFFERENT sentences from one clock"},
	}
}

func scenPayload(scs []lpScen) (string, error) {
	var out pyval.List
	for _, sc := range scs {
		beh := pyval.Obj{}
		keys := make([]int, 0, len(sc.beh))
		for k := range sc.beh {
			keys = append(keys, k)
		}
		sort.Ints(keys)
		for _, k := range keys {
			b := sc.beh[k]
			row := pyval.Obj{}
			if b.Raise != "" {
				row = append(row, pyval.Field{Key: "raise", Val: b.Raise})
			}
			if b.RaiseEmpty {
				row = append(row, pyval.Field{Key: "raise_empty", Val: true})
			}
			if b.Sleep != 0 {
				row = append(row, pyval.Field{Key: "sleep", Val: b.Sleep})
			}
			if b.HasOut {
				row = append(row, pyval.Field{Key: "out", Val: b.Out})
			}
			beh = append(beh, pyval.Field{Key: fmt.Sprint(k), Val: row})
		}
		deps := pyval.Obj{}
		dk := make([]int, 0, len(sc.deps))
		for k := range sc.deps {
			dk = append(dk, k)
		}
		sort.Ints(dk)
		for _, k := range dk {
			var l pyval.List
			for _, v := range sc.deps[k] {
				l = append(l, v)
			}
			deps = append(deps, pyval.Field{Key: fmt.Sprint(k), Val: l})
		}
		steps := pyval.List{}
		for _, s := range sc.steps {
			steps = append(steps, s)
		}
		var to any
		if sc.timeout != nil {
			to = *sc.timeout
		}
		out = append(out, pyval.Obj{
			{Key: "kind", Val: sc.kind},
			{Key: "steps", Val: steps},
			{Key: "deps", Val: deps},
			{Key: "max_workers", Val: sc.maxWorkers},
			{Key: "timeout", Val: to},
			{Key: "beh", Val: beh},
		})
	}
	return pyval.DumpsCompactPy(out)
}

// goRun replays one scenario through the Go schedulers with the same
// canned behaviour, recording what each step was handed.
func goRun(sc lpScen) (outcomes []pyval.Obj, execs []lpExec, labels []string, err error) {
	var mu sync.Mutex
	run := func(idx int, text string, depCtx []string) (pyval.Obj, error) {
		mu.Lock()
		total := len(sc.steps)
		execs = append(execs, lpExec{Idx: idx, DepCtx: depCtx, Total: total})
		if sc.kind == "dag" {
			labels = append(labels, DAGStepLabel(idx))
		} else {
			labels = append(labels, StepLabel(idx))
		}
		mu.Unlock()
		b := sc.beh[idx]
		// Sleep FIRST, then raise: a row that does both is asking what
		// happens when a step finishes badly AFTER the deadline, and
		// raising early would answer a different question. The probe's
		// stub is ordered to match.
		if b.Sleep != 0 {
			time.Sleep(time.Duration(b.Sleep * float64(time.Second)))
		}
		if b.RaiseEmpty {
			return nil, &pyval.PyErr{Class: "ValueError", Msg: ""}
		}
		if b.Raise != "" {
			return nil, &pyval.PyErr{Class: "ValueError", Msg: b.Raise}
		}
		if b.HasOut {
			return b.Out, nil
		}
		return pyval.Obj{{Key: "status", Val: "ok"}, {Key: "result", Val: ""}}, nil
	}
	timeoutSecs := 600
	if sc.timeout != nil {
		n, terr := FanoutTimeout(*sc.timeout, true)
		if terr != nil {
			return nil, nil, nil, terr
		}
		timeoutSecs = n
	}
	if sc.kind == "dag" {
		outcomes, err = RunStepsDAG(sc.steps, sc.deps, sc.maxWorkers, timeoutSecs, run)
	} else {
		outcomes, err = RunStepsParallel(sc.steps, sc.maxWorkers, timeoutSecs, run)
	}
	sort.Slice(execs, func(i, j int) bool { return execs[i].Idx < execs[j].Idx })
	sort.Strings(labels)
	return outcomes, execs, labels, err
}

func TestTheSchedulersMatchCPython(t *testing.T) {
	all := lpScenarios()
	var scs []lpScen
	for _, sc := range all {
		if sc.slow && testing.Short() {
			continue
		}
		scs = append(scs, sc)
	}
	payload, err := scenPayload(scs)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", parallelProbe, srcDirLP(t))
	cmd.Stdin = strings.NewReader(payload)
	out, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v\n%s", perr, out)
	}
	var want []lpResult
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if len(want) != len(scs) {
		t.Fatalf("probe answered %d of %d scenarios", len(want), len(scs))
	}

	var raised, returned int
	for i, sc := range scs {
		w := want[i]
		gotOutcomes, gotExecs, gotLabels, gotErr := goRun(sc)

		if !w.OK {
			raised++
			if gotErr == nil {
				t.Errorf("%s: CPython raised %s(%q) and Go returned %d outcome(s)\n  %s",
					sc.name, w.Class, w.Msg, len(gotOutcomes), sc.why)
				continue
			}
			if cls := pyval.ClassOf(gotErr); cls != w.Class || gotErr.Error() != w.Msg {
				t.Errorf("%s: the EXCEPTION differs\n  go %s(%q)\n  py %s(%q)\n  %s",
					sc.name, cls, gotErr.Error(), w.Class, w.Msg, sc.why)
			}
			continue
		}
		returned++
		if gotErr != nil {
			t.Errorf("%s: Go raised %v where CPython returned %d outcome(s)\n  %s",
				sc.name, gotErr, len(w.Outcomes), sc.why)
			continue
		}
		if len(w.ScanMutated) > 0 {
			t.Errorf("%s: the security scan inside _run_one CHANGED outcome(s) %v -- "+
				"the corpus has reached an UNPORTED surface and this comparison is "+
				"no longer about the scheduler\n  %s", sc.name, w.ScanMutated, sc.why)
		}
		if len(gotOutcomes) != len(w.Outcomes) {
			t.Errorf("%s: go returned %d outcome(s), py %d\n  %s",
				sc.name, len(gotOutcomes), len(w.Outcomes), sc.why)
			continue
		}
		for j, oc := range gotOutcomes {
			got, derr := pyval.DumpsCompactPy(oc)
			if derr != nil {
				t.Fatalf("%s: outcome %d would not render: %v", sc.name, j+1, derr)
			}
			if got != w.Outcomes[j] {
				t.Errorf("%s: step %d's OUTCOME differs\n  go %s\n  py %s\n  %s",
					sc.name, j+1, got, w.Outcomes[j], sc.why)
			}
		}
		if !eqExecs(gotExecs, w.Execs) {
			t.Errorf("%s: the steps that RAN, or what they were handed, differ\n  go %v\n  py %v\n  %s",
				sc.name, gotExecs, w.Execs, sc.why)
		}
		if strings.Join(gotLabels, ",") != strings.Join(w.Labels, ",") {
			t.Errorf("%s: the WORKTREE LABELS differ (these become branch names)\n  go %v\n  py %v\n  %s",
				sc.name, gotLabels, w.Labels, sc.why)
		}
	}
	// A corpus that stopped reaching one of the two answers would keep
	// passing while measuring half the surface.
	if raised == 0 || returned == 0 {
		t.Fatalf("the corpus reaches only one outcome shape: raised=%d returned=%d",
			raised, returned)
	}
}

func eqExecs(a []lpExec, b []lpExec) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Idx != b[i].Idx || a[i].Total != b[i].Total {
			return false
		}
		if len(a[i].DepCtx) != len(b[i].DepCtx) {
			return false
		}
		for j := range a[i].DepCtx {
			if a[i].DepCtx[j] != b[i].DepCtx[j] {
				return false
			}
		}
	}
	return true
}

// MARO_STEP_TIMEOUT is read with `int(os.environ.get(..., "600"))` and
// nothing catches what int() raises, so a malformed value does not fall
// back to the default -- it kills the fan-out before a step is
// submitted. The corpus is built from what CPython's int() ACCEPTS,
// which is a wider language than strconv.Atoi's: underscores between
// digits, a leading plus, surrounding whitespace from all 29 of Python's
// space characters, and DECIMAL DIGITS FROM ANY SCRIPT.
var timeoutCorpus = []struct {
	raw     string
	present bool
}{
	{"", false}, // unset: the "600" default, which travels through int() too
	{"600", true},
	{"", true}, // SET and empty is int(""), which raises -- not the default
	{"1", true},
	{"0", true},
	{"-5", true},
	{"+60", true},
	{"6_0", true},  // PEP 515 separators are legal in int(str)
	{"_60", true},  // ...but not leading
	{"60_", true},  // ...or trailing
	{" 60 ", true}, // ASCII whitespace is stripped
	// int() skips leading space with Py_UNICODE_ISSPACE, which is the
	// same predicate str.strip() uses -- so the whole 29-character set
	// works, not just ASCII. Measured, because the first draft of this
	// comment claimed the opposite about NO-BREAK SPACE.
	{"\u00a060", true},             // NO-BREAK SPACE
	{"\u200960", true},             // THIN SPACE
	{"\u000b60\u001c", true},       // LINE TABULATION / FILE SEPARATOR
	{"6\u06600", true},             // two SCRIPTS in one number: also legal
	{"\U0001d7d2\U0001d7d0", true}, // MATHEMATICAL BOLD digits, five blocks in one range
	{"\u0660\u0666\u0660", true},   // ARABIC-INDIC digits: int() reads 060
	{"\uff16\uff10", true},         // FULLWIDTH digits: 60
	{"60.0", true},
	{"0x3c", true},
	{"10m", true},
	{"abc", true},
	{"true", true},
	{"  ", true},
	{"\u00b2", true}, // SUPERSCRIPT TWO is a digit to isdigit() and not to int()
}

func TestFanoutTimeoutMatchesCPython(t *testing.T) {
	type row struct {
		Raw     string `json:"raw"`
		Present bool   `json:"present"`
	}
	var in []row
	for _, c := range timeoutCorpus {
		in = append(in, row{c.raw, c.present})
	}
	payload, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,os,sys\n"+
			"rows = json.loads(sys.argv[1])\n"+
			"res = []\n"+
			"for r in rows:\n"+
			"    if r['present']:\n"+
			"        os.environ['MARO_STEP_TIMEOUT'] = r['raw']\n"+
			"    else:\n"+
			"        os.environ.pop('MARO_STEP_TIMEOUT', None)\n"+
			"    try:\n"+
			"        res.append([True, int(os.environ.get('MARO_STEP_TIMEOUT', '600')), '', ''])\n"+
			"    except BaseException as exc:\n"+
			"        res.append([False, 0, type(exc).__name__, str(exc)])\n"+
			"print(json.dumps(res))",
		string(payload)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want [][]any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var ok, raised int
	for i, c := range timeoutCorpus {
		w := want[i]
		gotN, gotErr := FanoutTimeout(c.raw, c.present)
		if w[0].(bool) {
			ok++
			if gotErr != nil {
				t.Errorf("MARO_STEP_TIMEOUT=%q (present=%v): go raised %v, py read %v",
					c.raw, c.present, gotErr, w[1])
				continue
			}
			if float64(gotN) != w[1].(float64) {
				t.Errorf("MARO_STEP_TIMEOUT=%q: go %d, py %v", c.raw, gotN, w[1])
			}
			continue
		}
		raised++
		if gotErr == nil {
			t.Errorf("MARO_STEP_TIMEOUT=%q: go read %d where CPython raised %s(%q)",
				c.raw, gotN, w[2], w[3])
			continue
		}
		if cls := pyval.ClassOf(gotErr); cls != w[2].(string) || gotErr.Error() != w[3].(string) {
			t.Errorf("MARO_STEP_TIMEOUT=%q: the EXCEPTION differs\n  go %s(%q)\n  py %s(%q)",
				c.raw, cls, gotErr.Error(), w[2], w[3])
		}
	}
	if ok == 0 || raised == 0 {
		t.Fatalf("the corpus reaches only one answer: ok=%d raised=%d", ok, raised)
	}
}

// _drain_pending_context is short, pure, and was the part of this chunk
// with NO differential: five of the battery's mutations survived it,
// including one that never consumed the ledger at all. A helper being
// obvious is not coverage.
//
// The probe drives the REAL function against a stand-in context, which
// is what its own getattr-defensive arms invite: `ctx` is only ever read
// through getattr here, so a SimpleNamespace is not a weaker fixture
// than a LoopContext -- it is the shape the docstring names.
const drainProbe = `
import json, sys, types
sys.path.insert(0, sys.argv[1])
import loop_parallel as lp
import loop_types as lt

out = []
for sc in json.loads(sys.stdin.read()):
    if sc["ledger"] is None:
        ctx = types.SimpleNamespace(ancestry_context=sc["ancestry"])
    else:
        led = lt.ContributionLedger()
        for source, kind, text in sc["ledger"]:
            led.append(source, kind, text)
        ctx = types.SimpleNamespace(ancestry_context=sc["ancestry"],
                                    pending_context=led)
    inc, anc = lp._drain_pending_context(ctx)
    left = 0 if sc["ledger"] is None else len(ctx.pending_context._pending)
    out.append([inc, anc, left])
print(json.dumps(out))
`

type drainScen struct {
	name     string
	ancestry string
	ledger   [][3]string // nil means NO pending_context attribute at all
	noLedger bool
	why      string
}

func drainScenarios() []drainScen {
	return []drainScen{
		{name: "no ledger attribute at all", ancestry: "anc", noLedger: true,
			why: "the getattr-defensive arm: ancestry travels through untouched"},
		{name: "no ledger and no ancestry", noLedger: true,
			why: "...and both empty is still two empty strings, not one"},
		{name: "an empty ledger", ancestry: "anc", ledger: [][3]string{},
			why: "nothing rendered means the ancestry is handed back UNCHANGED, with no separator"},
		{name: "one contribution", ancestry: "anc",
			ledger: [][3]string{{"operator", "note", "ship it"}},
			why:    "the rendering, and the two-newline join onto the ancestry"},
		{name: "one contribution and NO ancestry",
			ledger: [][3]string{{"operator", "note", "ship it"}},
			why:    "an empty ancestry must not gain a leading separator"},
		{name: "several contributions from several sources", ancestry: "anc",
			ledger: [][3]string{
				{"operator", "note", "ship it"},
				{"escalation", "reply", "yes, proceed"},
				{"operator", "note", "and hurry"}},
			why: "render_contributions groups and orders; the order is part of the answer"},
		{name: "ONLY a time contribution", ancestry: "anc",
			ledger: [][3]string{{"time", "gap", "4 hours passed"}},
			why:    "drop_source runs BEFORE the empty check, so this renders nothing and returns the bare ancestry"},
		{name: "a time contribution among others", ancestry: "anc",
			ledger: [][3]string{
				{"operator", "note", "ship it"},
				{"time", "gap", "4 hours passed"},
				{"escalation", "reply", "yes"}},
			why: "the [time] line is dropped and the others survive -- a stale wall-clock claim never replays"},
		{name: "whitespace-only text is dropped by append", ancestry: "anc",
			ledger: [][3]string{{"operator", "note", "   \u001f  "}},
			why:    "the ledger's own strip is Python's 29-character one, so U+001F makes this empty"},
		{name: "a contribution whose text needs stripping", ancestry: "anc",
			ledger: [][3]string{{"operator", "note", "  ship it  "}},
			why:    "...and the same strip on a real one"},
		{name: "an ancestry that already ends in newlines", ancestry: "anc\n\n",
			ledger: [][3]string{{"operator", "note", "ship it"}},
			why:    "the join is unconditional: four newlines, not two"},
		{name: "non-ascii source and text", ancestry: "anc",
			ledger: [][3]string{{"op\u00e9rateur", "note", "exp\u00e9die \u7814\u7a76"}},
			why:    "the rendering is not ASCII-only anywhere"},
	}
}

func TestDrainPendingContextMatchesCPython(t *testing.T) {
	scs := drainScenarios()
	var payload pyval.List
	for _, sc := range scs {
		var led any
		if !sc.noLedger {
			rows := pyval.List{}
			for _, r := range sc.ledger {
				rows = append(rows, pyval.List{r[0], r[1], r[2]})
			}
			led = rows
		}
		payload = append(payload, pyval.Obj{
			{Key: "ancestry", Val: sc.ancestry},
			{Key: "ledger", Val: led},
		})
	}
	in, err := pyval.DumpsCompactPy(payload)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", drainProbe, srcDirLP(t))
	cmd.Stdin = strings.NewReader(in)
	out, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v\n%s", perr, out)
	}
	var want [][]any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if len(want) != len(scs) {
		t.Fatalf("probe answered %d of %d scenarios", len(want), len(scs))
	}

	var rendered, bare int
	for i, sc := range scs {
		var ledger *looptypes.ContributionLedger
		if !sc.noLedger {
			ledger = looptypes.NewContributionLedger()
			for _, r := range sc.ledger {
				ledger.Append(r[0], r[1], r[2])
			}
		}
		gotInc, gotAnc := DrainPendingContext(ledger, sc.ancestry)
		wantInc, _ := want[i][0].(string)
		wantAnc, _ := want[i][1].(string)
		wantLeft := int(want[i][2].(float64))
		if gotInc != wantInc {
			t.Errorf("%s: the INCREMENTAL rendering differs\n  go %q\n  py %q\n  %s",
				sc.name, gotInc, wantInc, sc.why)
		}
		if gotAnc != wantAnc {
			t.Errorf("%s: the ANCESTRY differs\n  go %q\n  py %q\n  %s",
				sc.name, gotAnc, wantAnc, sc.why)
		}
		// The ledger must be CONSUMED, and a comparison of return values
		// alone cannot see that: a drain replaced by a peek returns the
		// same two strings and leaves the batch to be delivered twice.
		gotLeft := 0
		if ledger != nil {
			gotLeft = ledger.Len()
		}
		if gotLeft != wantLeft {
			t.Errorf("%s: %d record(s) left pending, py leaves %d — a "+
				"boundary that does not CONSUME delivers its batch again\n  %s",
				sc.name, gotLeft, wantLeft, sc.why)
		}
		if wantInc != "" {
			rendered++
		} else {
			bare++
		}
	}
	if rendered == 0 || bare == 0 {
		t.Fatalf("the corpus reaches only one answer: rendered=%d bare=%d",
			rendered, bare)
	}
}
