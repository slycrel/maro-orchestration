package orch

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// The scheduler's contract is about ORDER and TERMINALITY, and both
// runtimes take `run_one` as a parameter — so the differential drives
// each side with a run_one that records what it was handed and when.
//
// What is compared is NOT wall-clock interleaving, which is a scheduler
// detail neither runtime promises. It is the three things a shared store
// can see: which milestones ran, what each one's status ended as, and
// which pairs were allowed to OVERLAP. The last is the whole point of the
// DAG — a port that silently ran everything sequentially would produce
// identical missions and be wrong.

// dagRun is a normalised execution of one mission.
type dagRun struct {
	Ran      []string   `json:"ran"`      // titles, in completion order
	Statuses []string   `json:"statuses"` // per milestone, in list order
	Results  []string   `json:"results"`  // validation_result, "" for null
	Overlaps [][]string `json:"overlaps"` // sorted pairs seen concurrently
}

// pyDAGSnippet builds a mission from a spec, runs it through CPython's
// own _run_milestone_dag, and prints the normalised run.
//
// The run_one it injects blocks on a barrier for every milestone named in
// `slow`, which is how overlap is OBSERVED rather than inferred from
// timing: two milestones overlap iff both can sit in the barrier at once.
const pyDAGSnippet = `
import json, sys, threading, mission

spec = json.loads(sys.argv[1])
ms = []
for i, s in enumerate(spec['milestones']):
    ms.append(mission.Milestone(
        id='m%d' % i, title=s['title'], features=[], validation_criteria=[],
        status='pending', depends_on=list(s.get('depends_on', []))))
m = mission.Mission(id='mi', goal='g', project='p', milestones=ms,
                    status='running', created_at='t')

lock = threading.Lock()
active, overlaps, order = set(), set(), []
gate = threading.Barrier(spec['barrier'], timeout=1) if spec['barrier'] > 1 else None
fail = set(spec.get('fail', []))

def run_one(idx, milestone):
    with lock:
        for other in active:
            overlaps.add(tuple(sorted((other, milestone.title))))
        active.add(milestone.title)
    if gate is not None and milestone.title in spec.get('slow', []):
        try: gate.wait()
        except Exception: pass
    with lock:
        active.discard(milestone.title)
        order.append(milestone.title)
    if milestone.title in fail:
        raise RuntimeError('boom')
    milestone.status = 'done'

mission._run_milestone_dag(m, run_one, lambda s: None,
                           max_workers=spec['max_workers'])
print(json.dumps({
    'ran': order,
    'statuses': [x.status for x in m.milestones],
    'results': [x.validation_result or '' for x in m.milestones],
    'overlaps': sorted([list(p) for p in overlaps]),
}))
`

type dagSpec struct {
	Milestones []struct {
		Title     string   `json:"title"`
		DependsOn []string `json:"depends_on,omitempty"`
	} `json:"milestones"`
	MaxWorkers int      `json:"max_workers"`
	Barrier    int      `json:"barrier"`
	Slow       []string `json:"slow,omitempty"`
	Fail       []string `json:"fail,omitempty"`
}

func pyDAG(t *testing.T, spec dagSpec) dagRun {
	t.Helper()
	b, err := json.Marshal(spec)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyDAGSnippet, string(b))
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDirOrch(t))
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe FAILED (exit %d):\n%s",
				ee.ExitCode(), ee.Stderr)
		}
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("python3 is present but the probe could not run: %v", err)
	}
	var r dagRun
	if err := json.Unmarshal(out, &r); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, out)
	}
	return r
}

func goDAG(t *testing.T, spec dagSpec) dagRun {
	t.Helper()
	m := &Mission{ID: "mi", Goal: "g", Project: "p", Status: "running"}
	for i, s := range spec.Milestones {
		m.Milestones = append(m.Milestones, Milestone{
			ID: "m" + strconv.Itoa(i), Title: s.Title, Status: "pending",
			DependsOn: s.DependsOn,
		})
	}
	slow := map[string]bool{}
	for _, s := range spec.Slow {
		slow[s] = true
	}
	fail := map[string]bool{}
	for _, s := range spec.Fail {
		fail[s] = true
	}

	var mu sync.Mutex
	active := map[string]bool{}
	overlaps := map[string]bool{}
	var order []string
	gate := newBarrier(spec.Barrier)

	runOne := func(_ context.Context, _ int, ms *Milestone) error {
		mu.Lock()
		for other := range active {
			pair := []string{other, ms.Title}
			sort.Strings(pair)
			overlaps[pair[0]+"\x00"+pair[1]] = true
		}
		active[ms.Title] = true
		mu.Unlock()

		if gate != nil && slow[ms.Title] {
			gate.wait()
		}

		mu.Lock()
		delete(active, ms.Title)
		order = append(order, ms.Title)
		mu.Unlock()

		if fail[ms.Title] {
			return errors.New("boom")
		}
		ms.Status = "done"
		return nil
	}

	RunMilestoneDAG(context.Background(), m, runOne,
		DAGOptions{MaxWorkers: spec.MaxWorkers})

	r := dagRun{Ran: order, Overlaps: [][]string{}}
	if r.Ran == nil {
		r.Ran = []string{}
	}
	for i := range m.Milestones {
		r.Statuses = append(r.Statuses, m.Milestones[i].Status)
		if m.Milestones[i].ValidationResult == nil {
			r.Results = append(r.Results, "")
		} else {
			r.Results = append(r.Results, *m.Milestones[i].ValidationResult)
		}
	}
	var pairs []string
	for k := range overlaps {
		pairs = append(pairs, k)
	}
	sort.Strings(pairs)
	for _, k := range pairs {
		parts := strings.SplitN(k, "\x00", 2)
		r.Overlaps = append(r.Overlaps, parts)
	}
	return r
}

// barrier is a rendezvous for n goroutines that RELEASES ON TIMEOUT
// rather than hanging, matching threading.Barrier(timeout=5). A test
// harness that can deadlock is worse than one that can fail.
type barrier struct {
	n    int
	mu   sync.Mutex
	cond *sync.Cond
	seen int
	open bool
}

func newBarrier(n int) *barrier {
	if n <= 1 {
		return nil
	}
	b := &barrier{n: n}
	b.cond = sync.NewCond(&b.mu)
	return b
}

func (b *barrier) wait() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.seen++
	if b.seen >= b.n {
		b.open = true
		b.cond.Broadcast()
		return
	}
	// A one-shot timeout guard matching threading.Barrier(timeout=1):
	// if the barrier can never fill — fewer concurrent runners than n,
	// which is exactly what a NON-concurrent port produces, and also what
	// a max_workers cap produces on purpose — release rather than hang, so
	// the test FAILS on the overlap comparison instead of timing out with
	// no diagnosis.
	go func() {
		<-time.After(1 * time.Second)
		b.mu.Lock()
		b.open = true
		b.cond.Broadcast()
		b.mu.Unlock()
	}()
	for !b.open {
		b.cond.Wait()
	}
}

func assertDAGRunsAgree(t *testing.T, got, want dagRun) {
	t.Helper()
	// Completion ORDER is not a promise either runtime makes when two
	// milestones run concurrently, so it is compared as a SET. What
	// order does pin is that every milestone ran exactly once.
	gs, ws := append([]string{}, got.Ran...), append([]string{}, want.Ran...)
	sort.Strings(gs)
	sort.Strings(ws)
	if fmt.Sprint(gs) != fmt.Sprint(ws) {
		t.Errorf("different milestones ran\n go: %v\n py: %v", got.Ran, want.Ran)
	}
	if fmt.Sprint(got.Statuses) != fmt.Sprint(want.Statuses) {
		t.Errorf("statuses differ\n go: %v\n py: %v", got.Statuses, want.Statuses)
	}
	if fmt.Sprint(got.Results) != fmt.Sprint(want.Results) {
		t.Errorf("validation_result differs\n go: %v\n py: %v",
			got.Results, want.Results)
	}
	if fmt.Sprint(got.Overlaps) != fmt.Sprint(want.Overlaps) {
		t.Errorf("the set of milestones allowed to overlap differs\n go: %v\n py: %v",
			got.Overlaps, want.Overlaps)
	}
}

func ms(title string, deps ...string) struct {
	Title     string   `json:"title"`
	DependsOn []string `json:"depends_on,omitempty"`
} {
	return struct {
		Title     string   `json:"title"`
		DependsOn []string `json:"depends_on,omitempty"`
	}{title, deps}
}

func TestTheMilestoneDAGMatchesCPythons(t *testing.T) {
	type mst = struct {
		Title     string   `json:"title"`
		DependsOn []string `json:"depends_on,omitempty"`
	}
	for _, tc := range []struct {
		name string
		spec dagSpec
	}{
		{"a chain runs one at a time", dagSpec{
			Milestones: []mst{ms("A"), ms("B", "m0"), ms("C", "m1")},
			MaxWorkers: 2, Barrier: 1}},

		{"two independent roots OVERLAP", dagSpec{
			Milestones: []mst{ms("A"), ms("B")},
			MaxWorkers: 2, Barrier: 2, Slow: []string{"A", "B"}}},

		{"a diamond overlaps only in the middle", dagSpec{
			Milestones: []mst{ms("A"), ms("B", "m0"), ms("C", "m0"), ms("D", "m1", "m2")},
			MaxWorkers: 2, Barrier: 2, Slow: []string{"B", "C"}}},

		// Three independent roots that all WANT to overlap, against a
		// cap of one. Both runtimes' barriers time out unfilled and the
		// overlap set comes back empty — which is the assertion. With
		// Barrier:1 nothing blocked and the case could not tell a
		// working cap from no cap at all.
		{"max_workers caps the overlap at one", dagSpec{
			Milestones: []mst{ms("A"), ms("B"), ms("C")},
			MaxWorkers: 1, Barrier: 2, Slow: []string{"A", "B", "C"}}},

		// depends_on is ORDERING, never a gate on OUTCOME: the sequential
		// walk always continued past a failure, and changing that would
		// silently change what a mission produces.
		{"a dependent runs even though its dependency FAILED", dagSpec{
			Milestones: []mst{ms("A"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1, Fail: []string{"A"}}},

		// And again where the rule is VISIBLE. The case above cannot see
		// it: a port that gated on outcome simply stalls, and the stall
		// lane runs B anyway, to the same status, in the same order. Two
		// dependents on a failed dependency separate them — released
		// together they overlap, stalled they run one at a time (found by
		// mutation, not by reading).
		{"two dependents on a FAILED dependency are released together", dagSpec{
			Milestones: []mst{ms("A"), ms("B", "m0"), ms("C", "m0")},
			MaxWorkers: 2, Barrier: 2,
			Slow: []string{"B", "C"}, Fail: []string{"A"}}},

		{"a crash marks one milestone and the mission continues", dagSpec{
			Milestones: []mst{ms("A"), ms("B"), ms("C")},
			MaxWorkers: 2, Barrier: 1, Fail: []string{"B"}}},

		// Unknown ids name nothing to wait for.
		{"a dependency on a milestone that is not here is ignored", dagSpec{
			Milestones: []mst{ms("A", "ghost"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1}},

		// The case above cannot actually SEE the rule: a port that treated
		// an unknown id as a blocker stalls, and the stall lane runs the
		// same two milestones to the same statuses. Only OVERLAP tells them
		// apart — both roots are ready here, and a stalled port runs them
		// one at a time (found by mutation, not by reading).
		{"two milestones on the same unknown dependency still OVERLAP", dagSpec{
			Milestones: []mst{ms("A", "ghost"), ms("B", "ghost")},
			MaxWorkers: 2, Barrier: 2, Slow: []string{"A", "B"}}},

		// Cycles cannot deadlock: the stall lane runs the remainder in
		// LIST order and returns.
		{"a two-cycle stalls into list order", dagSpec{
			Milestones: []mst{ms("A", "m1"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1}},

		{"a self-cycle stalls", dagSpec{
			Milestones: []mst{ms("A", "m0"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1}},

		{"a partial cycle runs the reachable part first", dagSpec{
			Milestones: []mst{ms("A"), ms("B", "m2"), ms("C", "m1")},
			MaxWorkers: 2, Barrier: 1}},

		{"a crash inside the STALL lane is caught too", dagSpec{
			Milestones: []mst{ms("A", "m1"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1, Fail: []string{"A"}}},

		{"an empty mission terminates", dagSpec{
			Milestones: []mst{}, MaxWorkers: 2, Barrier: 1}},

		{"one milestone", dagSpec{
			Milestones: []mst{ms("A")}, MaxWorkers: 2, Barrier: 1}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			want := pyDAG(t, tc.spec)
			assertDAGRunsAgree(t, goDAG(t, tc.spec), want)
		})
	}
}

// The corpus above is only worth its runtime if it contains a case where
// milestones actually DID overlap. Without one, every comparison is
// "nothing overlapped == nothing overlapped", which a port that ran
// everything sequentially would pass.
func TestTheDAGCorpusActuallyObservesConcurrency(t *testing.T) {
	spec := dagSpec{
		Milestones: []struct {
			Title     string   `json:"title"`
			DependsOn []string `json:"depends_on,omitempty"`
		}{ms("A"), ms("B")},
		MaxWorkers: 2, Barrier: 2, Slow: []string{"A", "B"},
	}
	want := pyDAG(t, spec)
	if len(want.Overlaps) == 0 {
		t.Fatal("CPython's own scheduler never overlapped two independent " +
			"roots; the overlap comparison in every other case is vacuous")
	}
	got := goDAG(t, spec)
	if len(got.Overlaps) == 0 {
		t.Fatal("the Go scheduler ran two independent roots sequentially — " +
			"it produces the same mission and buys none of the concurrency " +
			"the DAG exists for")
	}
}
