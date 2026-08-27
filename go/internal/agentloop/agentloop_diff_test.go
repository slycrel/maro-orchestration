package agentloop

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyargparse"
)

// The probe drives the REAL agent_loop.main and agent_loop.run_parallel_loops
// with run_agent_loop replaced, and nothing else. What is compared is what an
// operator sees: the exit code, stdout, stderr (argparse's usage block
// included, verbatim), and the keyword arguments the loop would have been
// called with.
//
// COLUMNS=80 is exported by the probe. argparse re-wraps usage and help to
// the terminal width, so an unpinned width makes the comparison a function
// of whoever ran the test.

// NO FIELD HERE CARRIES `omitempty`, and internal/portguard enforces it.
// A scenario struct is the wire format to the probe: omitempty deletes a
// field whose value is the Go zero, and whatever default the probe then
// supplies is a value the fixture never chose. Defaults belong in the
// scenario BUILDERS below, where both languages read the same number.
type mainRun struct {
	Status  string `json:"status"`
	Summary string `json:"summary"`
}

type parBeh struct {
	Sleep   float64  `json:"sleep"`
	RaiseOn []string `json:"raise_on"`
}

type scenario struct {
	Kind       string   `json:"kind"`
	Name       string   `json:"name"`
	Argv       []string `json:"argv"`
	Run        *mainRun `json:"run"`
	Goals      []string `json:"goals"`
	MaxWorkers int      `json:"max_workers"`
	Beh        *parBeh  `json:"beh"`
}

func mainCases() []scenario {
	var sc []scenario
	add := func(name string, argv ...string) {
		if argv == nil {
			argv = []string{}
		}
		sc = append(sc, scenario{Kind: "main", Name: name, Argv: argv})
	}
	add("one-word-goal", "ship")
	add("multi-word-goal-is-joined", "ship", "the", "thing")
	add("goal-with-inner-double-space", "a  b")
	add("help-long", "--help")
	add("help-short", "-h")
	add("help-before-goal", "-h", "ship")
	add("help-after-goal", "ship", "-h")
	add("no-arguments-is-required-goal")
	add("only-an-option-is-still-required-goal", "--bogus")
	add("unknown-option-after-goal", "ship", "--bogus")
	add("project-long", "ship", "--project", "p1")
	add("project-short", "ship", "-p", "p1")
	add("project-equals", "ship", "--project=p1")
	add("project-glued-short", "ship", "-pp1")
	add("project-abbreviated", "ship", "--proj", "p1")
	add("project-missing-value", "ship", "--project")
	add("model-and-project", "ship", "-m", "anthropic/x", "-p", "p1")
	add("max-steps", "ship", "--max-steps", "3")
	add("max-steps-equals", "ship", "--max-steps=3")
	add("max-steps-invalid", "ship", "--max-steps", "x")
	add("max-steps-unicode-digits", "ship", "--max-steps", "٣")
	add("max-steps-negative", "ship", "--max-steps", "-1")
	// 2**63-1 exactly: proves the parser does not ERROR on a number no int32
	// would hold, and lands on a value both languages spell identically.
	// Past this point Python keeps full precision and the port saturates;
	// that divergence is real, inconsequential (both are step counts no run
	// reaches), and deliberately not pinned by a test — see PORT.md.
	add("max-steps-int64-max", "ship", "--max-steps", "9223372036854775807")
	add("max-iterations", "ship", "--max-iterations", "99")
	add("dry-run", "ship", "--dry-run")
	add("verbose-long", "ship", "--verbose")
	add("verbose-short", "ship", "-v")
	add("backend-valid", "ship", "--backend", "anthropic")
	add("backend-short", "ship", "-b", "xai")
	add("backend-invalid", "ship", "--backend", "bogus")
	add("backend-missing-value", "ship", "-b")
	add("backend-abbreviated", "ship", "--back", "codex")
	add("ambiguous-long-prefix", "ship", "--ma", "3")
	add("ambiguous-max-prefix", "ship", "--max", "3")
	add("terminator-then-goal", "--", "ship")
	add("terminator-covered-span", "a", "--", "b")
	add("terminator-uncovered-span", "a", "b", "--", "c")
	add("dash-only-is-a-goal", "-")
	add("negative-number-is-a-goal", "-1ship")
	add("token-with-space-is-a-goal", "-a b")
	add("flag-with-equals-is-refused", "ship", "--dry-run=x")
	add("glued-flag-tail", "ship", "-vp", "p1")
	add("double-flag-glued", "ship", "-vv")
	add("glued-flag-tail-then-equals", "ship", "-vv=x")
	// The tail names no option, so it joins the extras WITH the dash it was
	// glued to: `unrecognized arguments: -x`, not `x`.
	add("glued-flag-unknown-tail", "ship", "-vx")
	add("short-flag-equals", "ship", "-v=x")
	add("project-empty-value", "ship", "--project=")
	add("project-then-option", "ship", "--project", "--model", "x")
	add("two-terminators", "a", "--", "b", "--", "c")
	add("dot-number-is-a-goal", "-.5")
	add("unicode-digit-dash-is-a-goal", "-\u0665ship")
	add("stuck-run-exits-1", "ship")
	sc[len(sc)-1].Run = &mainRun{Status: "stuck", Summary: "did not finish"}
	sc = append(sc, scenario{Kind: "main", Name: "empty-summary",
		Argv: []string{"ship"}, Run: &mainRun{Status: "done", Summary: ""}})
	for i := range sc {
		if sc[i].Goals == nil {
			sc[i].Goals = []string{}
		}
		if sc[i].Run == nil {
			sc[i].Run = &mainRun{Status: "done", Summary: "SUMMARY"}
		}
		if sc[i].Beh == nil {
			sc[i].Beh = &parBeh{RaiseOn: []string{}}
		}
	}
	return sc
}

func parallelCases() []scenario {
	sc := []scenario{
		{Kind: "par", Name: "par-empty-goals", Goals: []string{}, MaxWorkers: 3},
		{Kind: "par", Name: "par-empty-goals-zero-workers", Goals: []string{}, MaxWorkers: 0},
		{Kind: "par", Name: "par-zero-workers-with-goals",
			Goals: []string{"a"}, MaxWorkers: 0},
		{Kind: "par", Name: "par-negative-workers",
			Goals: []string{"a", "b"}, MaxWorkers: -2},
		{Kind: "par", Name: "par-order-preserved",
			Goals: []string{"a", "b", "c", "d", "e", "f"}, MaxWorkers: 2},
		{Kind: "par", Name: "par-workers-exceed-goals",
			Goals: []string{"a", "b"}, MaxWorkers: 9},
		{Kind: "par", Name: "par-single",
			Goals: []string{"only"}, MaxWorkers: 1},
		{Kind: "par", Name: "par-first-goal-raises",
			Goals: []string{"a", "b", "c"}, MaxWorkers: 3,
			Beh: &parBeh{RaiseOn: []string{"a"}}},
		{Kind: "par", Name: "par-later-goal-raises",
			Goals: []string{"a", "b", "c"}, MaxWorkers: 3,
			Beh: &parBeh{RaiseOn: []string{"c"}}},
		{Kind: "par", Name: "par-two-goals-raise-first-wins",
			Goals: []string{"a", "b", "c"}, MaxWorkers: 3,
			Beh: &parBeh{RaiseOn: []string{"b", "c"}}},
	}
	for i := range sc {
		if sc[i].Argv == nil {
			sc[i].Argv = []string{}
		}
		if sc[i].Run == nil {
			sc[i].Run = &mainRun{Status: "done", Summary: "SUMMARY"}
		}
		if sc[i].Beh == nil {
			sc[i].Beh = &parBeh{}
		}
		// 0.05s is long enough that a wave finishes before the next starts
		// and short enough that ten scenarios cost half a second. It lives
		// here, once, rather than as a default on each side.
		if sc[i].Beh.Sleep == 0 {
			sc[i].Beh.Sleep = 0.05
		}
		if sc[i].Beh.RaiseOn == nil {
			sc[i].Beh.RaiseOn = []string{}
		}
	}
	return sc
}

// waves collapses the observed start ORDER into the only part of it that is
// determined. With `effective` workers, FIFO submission fixes which goals are
// in flight together, but not the order in which those threads happen to
// reach the recording lock — that is a race in CPython and in Go alike.
// Comparing raw order would be flaky; comparing the SET per wave is not, and
// it is still the property that separates a FIFO queue from a semaphore over
// one goroutine per goal (which would put goal 5 in wave 0).
func waves(started []string, effective int) [][]string {
	out := [][]string{}
	if effective <= 0 {
		effective = 1
	}
	for i := 0; i < len(started); i += effective {
		j := i + effective
		if j > len(started) {
			j = len(started)
		}
		chunk := append([]string(nil), started[i:j]...)
		sort.Strings(chunk)
		out = append(out, chunk)
	}
	return out
}

// effectiveWorkers mirrors the pool size the port computes, for wave sizing
// only — the port's own capping is what the `peak` field tests.
func effectiveWorkers(s scenario) int {
	e := s.MaxWorkers
	if len(s.Goals) < e {
		e = len(s.Goals)
	}
	return e
}

func srcDirAL(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func runProbe(t *testing.T, scs []scenario) []map[string]any {
	t.Helper()
	dir := t.TempDir()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	spec := filepath.Join(dir, "s.json")
	if err := os.WriteFile(spec, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "probe.py.tpl", srcDirAL(t), spec)
	out, err := cmd.Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			t.Fatalf("probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("probe failed: %v", err)
	}
	var recs []map[string]any
	if err := json.Unmarshal(out, &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func canon(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var round any
	if err := json.Unmarshal(b, &round); err != nil {
		t.Fatal(err)
	}
	b2, err := json.MarshalIndent(round, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	return string(b2)
}

// goMain builds the record the probe's run_main builds.
func goMain(s scenario) map[string]any {
	var calls []map[string]any
	status, summary := "done", "SUMMARY"
	if s.Run != nil {
		status, summary = s.Run.Status, s.Run.Summary
	}

	var out bytes.Buffer
	code, err := Main(s.Argv, func(a Args) (RunResult, error) {
		// The keyword set `main` passes, spelled the way Python spells it.
		calls = append(calls, map[string]any{
			"goal": a.Goal, "project": strPtr(a.Project),
			"model": strPtr(a.Model), "backend": strPtr(a.Backend),
			"max_steps": a.MaxSteps, "max_iterations": a.MaxIterations,
			"dry_run": a.DryRun, "verbose": a.Verbose})
		return RunResult{Status: status, Summary: summary}, nil
	}, &out)

	stderr, _, _ := pyargparse.ExitStatus(err)
	rec := map[string]any{"name": s.Name, "exit": code,
		"stdout": out.String(), "stderr": stderr, "calls": orEmpty(calls)}
	return rec
}

func goParallel(s scenario) map[string]any {
	beh := s.Beh
	sleep := beh.Sleep
	var mu sync.Mutex
	var started []string
	inflight, peak := 0, 0

	res, err := RunParallelLoops(s.Goals, s.MaxWorkers, func(goal string) (string, error) {
		mu.Lock()
		started = append(started, goal)
		inflight++
		if inflight > peak {
			peak = inflight
		}
		mu.Unlock()
		time.Sleep(time.Duration(sleep * float64(time.Second)))
		mu.Lock()
		inflight--
		mu.Unlock()
		for _, g := range beh.RaiseOn {
			if g == goal {
				return "", fmt.Errorf("boom %s", goal)
			}
		}
		return "R:" + goal, nil
	})

	rec := map[string]any{"name": s.Name}
	if err != nil {
		cls := "ValueError"
		var mw ErrMaxWorkers
		if !errors.As(err, &mw) {
			cls = "ValueError" // the stub raises ValueError too
		}
		rec["error"] = cls + ": " + err.Error()
	} else {
		rec["results"] = res
	}
	if started == nil {
		started = []string{}
	}
	rec["started"] = waves(started, effectiveWorkers(s))
	rec["peak"] = peak
	return rec
}

// pyParallel re-reads the probe's record and applies the same wave
// normalization, so both sides are compared at the same resolution.
func pyParallel(s scenario, rec map[string]any) map[string]any {
	raw, _ := rec["started"].([]any)
	started := make([]string, 0, len(raw))
	for _, v := range raw {
		started = append(started, fmt.Sprint(v))
	}
	rec["started"] = waves(started, effectiveWorkers(s))
	return rec
}

func strPtr(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

func orEmpty(v []map[string]any) []map[string]any {
	if v == nil {
		return []map[string]any{}
	}
	return v
}

func firstDiff(want, got string) string {
	w := strings.Split(want, "\n")
	g := strings.Split(got, "\n")
	var b strings.Builder
	n := len(w)
	if len(g) > n {
		n = len(g)
	}
	shown := 0
	for i := 0; i < n && shown < 10; i++ {
		wl, gl := "", ""
		if i < len(w) {
			wl = w[i]
		}
		if i < len(g) {
			gl = g[i]
		}
		if wl != gl {
			fmt.Fprintf(&b, "line %d:\n  py: %s\n  go: %s\n", i+1, wl, gl)
			shown++
		}
	}
	return b.String()
}

func TestMainMatchesCPython(t *testing.T) {
	scs := mainCases()
	py := runProbe(t, scs)
	if len(py) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios", len(py), len(scs))
	}
	for i, s := range scs {
		got := canon(t, goMain(s))
		want := canon(t, py[i])
		if got != want {
			t.Errorf("scenario %q diverges:\n%s", s.Name, firstDiff(want, got))
		}
	}
}

// TestRunParallelLoopsMatchesCPython compares results, error class and
// message, the ORDER tasks started in, and the peak concurrency.
//
// Start order and peak are the two properties a straightforward rewrite
// gets wrong, and they are only observable because run() is instrumented:
// a semaphore-per-goroutine port has the right results and the wrong start
// order, and an uncapped one has the right start order and a peak equal to
// the goal count.
func TestRunParallelLoopsMatchesCPython(t *testing.T) {
	scs := parallelCases()
	py := runProbe(t, scs)
	if len(py) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios", len(py), len(scs))
	}
	for i, s := range scs {
		got := canon(t, goParallel(s))
		want := canon(t, pyParallel(s, py[i]))
		if got != want {
			t.Errorf("scenario %q diverges:\n%s", s.Name, firstDiff(want, got))
		}
	}
}

// TestVerboseIsAlwaysTrue pins the `or True` at main's call site directly.
// The differential covers it too, but only as one field of one record; a
// reader who deletes the line deserves a test whose NAME says what broke.
func TestVerboseIsAlwaysTrue(t *testing.T) {
	for _, argv := range [][]string{{"ship"}, {"ship", "-v"}, {"ship", "--verbose"}} {
		var got bool
		var out bytes.Buffer
		if _, err := Main(argv, func(a Args) (RunResult, error) {
			got = a.Verbose
			return RunResult{Status: "done"}, nil
		}, &out); err != nil {
			t.Fatalf("%v: %v", argv, err)
		}
		if !got {
			t.Errorf("%v passed verbose=false; Python's `args.verbose or True` "+
				"is True for every input", argv)
		}
	}
}

// TestParseArgsKeepsVerboseAsGiven is the other half: the flag is still
// PARSED, because parsing is where a wrong `-v` is refused. Folding the
// `or True` into the parser would make `--verbose=x` legal.
func TestParseArgsKeepsVerboseAsGiven(t *testing.T) {
	a, err := ParseArgs([]string{"ship"})
	if err != nil {
		t.Fatal(err)
	}
	if a.Verbose {
		t.Error("ParseArgs invented a verbose it was not given")
	}
	if _, err := ParseArgs([]string{"ship", "--verbose=x"}); err == nil {
		t.Error("--verbose=x was accepted; argparse refuses an explicit " +
			"argument to a flag")
	}
}

// TestRunErrorExitsOne covers the one branch of Main the CPython
// differential cannot reach.
//
// Python's `main` does not catch anything from run_agent_loop: an exception
// escapes to the interpreter, which prints a traceback and exits 1. The
// probe would have to reproduce that traceback to compare it, and the
// traceback is CPython's, not this code's — so the differential stubs a
// loop that always returns and the status code is pinned here instead.
// Measured, not assumed: `python3 -c "raise ValueError('x')"` exits 1.
func TestRunErrorExitsOne(t *testing.T) {
	var out bytes.Buffer
	code, err := Main([]string{"ship"}, func(Args) (RunResult, error) {
		return RunResult{}, errors.New("loop blew up")
	}, &out)
	if err == nil {
		t.Fatal("the loop's error was swallowed")
	}
	if code != 1 {
		t.Errorf("exit %d; an uncaught exception exits 1, and 2 is "+
			"argparse's usage status — a script that tells them apart "+
			"would read a crash as a typo", code)
	}
	if out.Len() != 0 {
		t.Errorf("printed %q; nothing is printed when the loop never "+
			"returned a result", out.String())
	}
}
