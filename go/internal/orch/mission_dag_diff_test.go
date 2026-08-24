package orch

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"runtime"
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
	// The scheduler's three OTHER outputs, none of which this harness
	// looked at until mutants that deleted the durable warning, deleted
	// the mid-flight persist, and rendered `deps=` with Go's %v instead
	// of Python's str() all survived the whole suite (adversarial
	// mission-r1 MEDIUM and its battery). goDAG passed neither LogFn nor
	// WarnFn nor PersistFn, so the entire operator surface was invisible.
	//
	// Messages are compared as SORTED sets: Python emits them from
	// whichever thread got there first, which is not a promise either
	// runtime makes.
	Logs     []string `json:"logs"`
	Warns    []string `json:"warns"`
	Persists int      `json:"persists"`
}

// pyDAGSnippet builds a mission from a spec, runs it through CPython's
// own _run_milestone_dag, and prints the normalised run.
//
// The run_one it injects blocks on a barrier for every milestone named in
// `slow`, which is how overlap is OBSERVED rather than inferred from
// timing: two milestones overlap iff both can sit in the barrier at once.
const pyDAGSnippet = `
import json, sys, threading, logging, mission

logs, warns, persists = [], [], []

class _Capture(logging.Handler):
    def emit(self, record):
        warns.append(record.getMessage())

mission.log.addHandler(_Capture())
mission.log.setLevel(logging.WARNING)

spec = json.loads(sys.argv[1])
ms = []
for i, s in enumerate(spec['milestones']):
    ms.append(mission.Milestone(
        id=s.get('id') or ('m%d' % i), title=s['title'], features=[],
        validation_criteria=[],
        status='pending', depends_on=list(s.get('depends_on', []))))
m = mission.Mission(id='mi', goal='g', project='p', milestones=ms,
                    status='running', created_at='t')

lock = threading.Lock()
active, overlaps, order = set(), set(), []
gate = (threading.Barrier(spec['barrier'], timeout=spec['barrier_timeout_s'])
        if spec['barrier'] > 1 else None)
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

# max_workers 0 means "let the default apply", which is the only way to
# compare Python's default kwarg against Go's zero-value floor.
#
# It is ALSO the harness's own limit, stated so the corpus cannot be
# misread as coverage it does not have: 0 and negatives can never be
# posed to Python at all, because ThreadPoolExecutor(max_workers=0)
# raises ValueError before the scheduler is reached. So the case below
# compares Go's floor against Python's KWARG DEFAULT, and no case here
# asks what either runtime does when a config value of 0 reaches the
# call site. That question belongs to slice 3 and is flagged at the
# floor in mission_dag.go.
kw = {} if spec['max_workers'] == 0 else {'max_workers': spec['max_workers']}
mission._run_milestone_dag(m, run_one, logs.append,
                           persist_fn=lambda: persists.append(1), **kw)
print(json.dumps({
    'ran': order,
    'statuses': [x.status for x in m.milestones],
    'results': [x.validation_result or '' for x in m.milestones],
    'overlaps': sorted([list(p) for p in overlaps]),
    'logs': sorted(logs),
    'warns': sorted(warns),
    'persists': len(persists),
}))
`

// dagMS is the per-milestone spec. The ID is normally left blank and
// filled in as m0, m1, ... on both sides; naming it explicitly is how
// DUPLICATE ids get pinned, and load_mission does not check uniqueness.
type dagMS struct {
	ID        string   `json:"id,omitempty"`
	Title     string   `json:"title"`
	DependsOn []string `json:"depends_on,omitempty"`
}

type dagSpec struct {
	Milestones []dagMS `json:"milestones"`
	MaxWorkers int     `json:"max_workers"`
	Barrier    int     `json:"barrier"`
	// BarrierTimeoutS is stamped by pyDAG from barrierTimeout — which is
	// also what the Go barrier waits — so the two runtimes cannot drift
	// apart on it. It is not part of any case literal.
	BarrierTimeoutS float64  `json:"barrier_timeout_s"`
	Slow            []string `json:"slow,omitempty"`
	Fail            []string `json:"fail,omitempty"`
}

func pyDAG(t *testing.T, spec dagSpec) dagRun {
	t.Helper()
	spec.BarrierTimeoutS = barrierTimeout.Seconds()
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
		id := s.ID
		if id == "" {
			id = "m" + strconv.Itoa(i)
		}
		m.Milestones = append(m.Milestones, Milestone{
			ID: id, Title: s.Title, Status: "pending",
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

	var logMu sync.Mutex
	var logs, warns []string
	persists := 0
	RunMilestoneDAG(context.Background(), m, runOne, DAGOptions{
		MaxWorkers: spec.MaxWorkers,
		LogFn: func(line string) {
			logMu.Lock()
			logs = append(logs, line)
			logMu.Unlock()
		},
		WarnFn: func(line string) {
			logMu.Lock()
			warns = append(warns, line)
			logMu.Unlock()
		},
		// Counts only; it must not READ milestones — see the named
		// residual in mission_dag.go's package doc.
		PersistFn: func() {
			logMu.Lock()
			persists++
			logMu.Unlock()
		},
	})
	sort.Strings(logs)
	sort.Strings(warns)

	r := dagRun{Ran: order, Overlaps: [][]string{},
		Logs: logs, Warns: warns, Persists: persists}
	if r.Logs == nil {
		r.Logs = []string{}
	}
	if r.Warns == nil {
		r.Warns = []string{}
	}
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
// rather than hanging, matching threading.Barrier(timeout=...) — the same
// number the Python snippet uses, threaded through the spec JSON from the
// constant below so the two cannot drift. A test harness that can deadlock
// is worse than one that can fail.
//
// It was 1s and that was too tight. Under full-suite load on this box the
// "DEFAULT worker count" case flaked (go: [] py: [[A B]]): both runners
// were genuinely admitted, but the second took over a second to be
// scheduled, the guard fired, and the case read as "could not overlap".
// It passed 6/6 in isolation, which is the signature of a load flake and
// not of a port defect. The cost of a longer wait is paid only by the
// cases that are SUPPOSED to time out (a chain, a cycle, a 1-worker cap),
// so 4s buys 4x the scheduling headroom for a few seconds of suite time.
const barrierTimeout = 4 * time.Second

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
	// A one-shot timeout guard matching threading.Barrier(timeout=...):
	// if the barrier can never fill — fewer concurrent runners than n,
	// which is exactly what a NON-concurrent port produces, and also what
	// a max_workers cap produces on purpose — release rather than hang, so
	// the test FAILS on the overlap comparison instead of timing out with
	// no diagnosis.
	go func() {
		<-time.After(barrierTimeout)
		b.mu.Lock()
		b.open = true
		b.cond.Broadcast()
		b.mu.Unlock()
	}()
	for !b.open {
		b.cond.Wait()
	}
}

func assertDAGRunsAgree(t *testing.T, spec dagSpec, got, want dagRun) {
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
	// The operator lines are BYTES, not decoration: Python renders a
	// title with !r and a depends_on list with str() of a LIST, and the
	// warning channel is the DURABLE half of the stall/crash evidence.
	if fmt.Sprint(got.Logs) != fmt.Sprint(want.Logs) {
		t.Errorf("log_fn lines differ\n go: %q\n py: %q", got.Logs, want.Logs)
	}
	if fmt.Sprint(got.Warns) != fmt.Sprint(want.Warns) {
		t.Errorf("warnings differ\n go: %q\n py: %q", got.Warns, want.Warns)
	}
	if got.Persists != want.Persists {
		t.Errorf("persist_fn calls: go %d, py %d", got.Persists, want.Persists)
	}
	if fmt.Sprint(got.Results) != fmt.Sprint(want.Results) {
		t.Errorf("validation_result differs\n go: %v\n py: %v",
			got.Results, want.Results)
	}
	// Overlap is only comparable when a BARRIER forces the question.
	// With Barrier 1 nothing blocks, so whether two ready milestones are
	// in run_one at the same instant is pure timing on both runtimes —
	// CPython's GIL makes it rare for a body this short and Go's
	// goroutines make it likely, and `-race` shifts the odds again. The
	// harness caught itself here: "a crash marks one milestone and the
	// mission continues" reported `go [[A B] [A C]]  py []` under -race
	// and agreed without it. Neither answer is wrong; the comparison was.
	//
	// A barrier turns it into an OBSERVATION: two milestones overlap iff
	// both can sit in the barrier at once, and a barrier that cannot fill
	// releases on its timeout so the case fails rather than hangs. Cases
	// whose non-overlap is structural (a chain, a stall lane) carry a
	// barrier for exactly this reason.
	if spec.Barrier > 1 && fmt.Sprint(got.Overlaps) != fmt.Sprint(want.Overlaps) {
		t.Errorf("the set of milestones allowed to overlap differs\n go: %v\n py: %v",
			got.Overlaps, want.Overlaps)
	}
}

func ms(title string, deps ...string) dagMS {
	return dagMS{Title: title, DependsOn: deps}
}

// msID gives a milestone an explicit id, which is only interesting when
// two of them share one.
func msID(title, id string, deps ...string) dagMS {
	return dagMS{ID: id, Title: title, DependsOn: deps}
}

type dagCase struct {
	name string
	spec dagSpec
}

// dagCorpus is hoisted out of the test so the concurrency guard below can
// READ it. The guard used to build its own two-milestone spec, so it
// could not notice the corpus losing every case that actually overlaps —
// the one thing it exists to catch (adversarial mission-r1 HIGH).
func dagCorpus() []dagCase {
	type mst = dagMS
	return []dagCase{
		// The barrier is what makes "one at a time" an assertion rather
		// than an accident: all three are slow, so a port that ignored
		// depends_on would fill it and the overlap set would be
		// non-empty. It can never fill here, so both sides release on
		// the timeout with an empty set.
		{"a chain runs one at a time", dagSpec{
			Milestones: []mst{ms("A"), ms("B", "m0"), ms("C", "m1")},
			MaxWorkers: 2, Barrier: 2, Slow: []string{"A", "B", "C"}}},

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
		// The stall lane runs on the scheduler's own thread, so its
		// milestones cannot overlap either — again only assertable with
		// a barrier in play.
		{"a two-cycle stalls into list order", dagSpec{
			Milestones: []mst{ms("A", "m1"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 2, Slow: []string{"A", "B"}}},

		{"a self-cycle stalls", dagSpec{
			Milestones: []mst{ms("A", "m0"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1}},

		{"a partial cycle runs the reachable part first", dagSpec{
			Milestones: []mst{ms("A"), ms("B", "m2"), ms("C", "m1")},
			MaxWorkers: 2, Barrier: 1}},

		{"a crash inside the STALL lane is caught too", dagSpec{
			Milestones: []mst{ms("A", "m1"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1, Fail: []string{"A"}}},

		// The stall lane's two evidence lines are BYTES. Every other
		// case here has a bare-alphabetic title and at most one
		// dependency, so nothing saw Python's !r quote flip or the
		// ", " join inside str() of a list.
		{"a stalled milestone with a quoted title and TWO deps", dagSpec{
			Milestones: []mst{ms("A'B", "m1", "m2"), ms("C", "m0"), ms("D", "m0")},
			MaxWorkers: 2, Barrier: 1}},

		// A dependency ID with a quote in it: str() of a LIST renders
		// each element with repr(), so one quote-bearing id flips that
		// element to double quotes while its siblings stay single.
		{"a stalled milestone whose dep id contains a quote", dagSpec{
			Milestones: []mst{ms("A", "it's", "m1"), ms("B", "m0")},
			MaxWorkers: 2, Barrier: 1}},

		{"a crash line renders the title with Python's !r", dagSpec{
			Milestones: []mst{ms("A'B"), ms("C")},
			MaxWorkers: 2, Barrier: 1, Fail: []string{"A'B"}}},

		// max_workers 0 = "apply the default on both sides". Python's is
		// the kwarg default of 2 and Go's is the zero-value floor; a
		// floor of 1 would silently serialise every mission whose caller
		// left the field alone. Barrier 2 is what makes the difference
		// observable.
		//
		// Read this as what it is: an assertion that Go's floor equals
		// Python's kwarg default. It is NOT an assertion about what
		// happens when a 0 reaches either scheduler, which the harness
		// cannot pose (see the note in the snippet above).
		{"the DEFAULT worker count admits an overlap", dagSpec{
			Milestones: []mst{ms("A"), ms("B")},
			MaxWorkers: 0, Barrier: 2, Slow: []string{"A", "B"}}},

		// Duplicate ids are reachable from a hand-edited mission.json —
		// load_mission does not check uniqueness. The stall lane keys on
		// `submitted`, so the second copy is skipped and `terminal` never
		// reaches len(milestones); Python RETURNS out of the stall lane
		// rather than re-entering the scheduling loop, and a port that
		// merely `continue`d would spin forever here.
		{"duplicate milestone ids still terminate", dagSpec{
			Milestones: []mst{msID("A", "dup"), msID("B", "dup", "ghost")},
			MaxWorkers: 2, Barrier: 1}},

		{"an empty mission terminates", dagSpec{
			Milestones: []mst{}, MaxWorkers: 2, Barrier: 1}},

		{"one milestone", dagSpec{
			Milestones: []mst{ms("A")}, MaxWorkers: 2, Barrier: 1}},
	}
}

func TestTheMilestoneDAGMatchesCPythons(t *testing.T) {
	for _, tc := range dagCorpus() {
		t.Run(tc.name, func(t *testing.T) {
			want := pyDAG(t, tc.spec)
			assertDAGRunsAgree(t, tc.spec, goDAG(t, tc.spec), want)
		})
	}
}

// The corpus above is only worth its runtime if it contains a case where
// milestones actually DID overlap. Without one, every comparison is
// "nothing overlapped == nothing overlapped", which a port that ran
// everything sequentially would pass.
func TestTheDAGCorpusActuallyObservesConcurrency(t *testing.T) {
	// The REAL corpus. A private spec proved only that SOME spec overlaps,
	// which stays true no matter what the corpus degrades into.
	pyOverlapping, goOverlapping := 0, 0
	for _, tc := range dagCorpus() {
		if len(pyDAG(t, tc.spec).Overlaps) > 0 {
			pyOverlapping++
		}
		if len(goDAG(t, tc.spec).Overlaps) > 0 {
			goOverlapping++
		}
	}
	if pyOverlapping == 0 {
		t.Fatal("no case in the corpus makes CPython's own scheduler " +
			"overlap two milestones; every overlap comparison in " +
			"TestTheMilestoneDAGMatchesCPythons is then " +
			"\"nothing == nothing\", which a sequential port passes")
	}
	if goOverlapping == 0 {
		t.Fatal("the Go scheduler never overlapped anything across the " +
			"whole corpus — it produces the same missions and buys none " +
			"of the concurrency the DAG exists for")
	}
	t.Logf("cases observing overlap: py=%d go=%d of %d",
		pyOverlapping, goOverlapping, len(dagCorpus()))
}

// Which milestones are ADMITTED first is deterministic in Python and was
// not here. Python submits every ready milestone into a
// ThreadPoolExecutor whose SimpleQueue is FIFO, so with max_workers=2
// and four ready milestones, milestones 0 and 1 start first, every time.
// The old Go spawned a goroutine per milestone and let them race for a
// buffered semaphore, so any two of the four could win (adversarial
// mission-r4 LOW).
//
// It was never a data race — both runtimes run the admitted N
// concurrently — but independent milestones share one project working
// directory by design, so admission order decides which pair can
// collide, and one runtime picking deterministically while the other
// does not makes a store fork unreproducible.
//
// Run enough times that a scheduler-order fluke cannot pass by luck: the
// old code failed this within a handful of iterations.
func TestDAGAdmissionFollowsListOrderLikePythonsFIFO(t *testing.T) {
	const iterations = 60
	for it := 0; it < iterations; it++ {
		m := &Mission{ID: "m", Milestones: []Milestone{
			{ID: "a", Title: "A"}, {ID: "b", Title: "B"},
			{ID: "c", Title: "C"}, {ID: "d", Title: "D"},
		}}

		var mu sync.Mutex
		var started []string
		release := make(chan struct{})
		admitted := make(chan struct{}, len(m.Milestones))
		// Hold the first pair until BOTH have been seen, so they cannot
		// finish and let a later milestone in before the check. Opened
		// by a WATCHER goroutine, not by the test body: the DAG has not
		// returned at that point, and closing it afterwards deadlocks
		// the scheduler against its own workers.
		go func() {
			<-admitted
			<-admitted
			close(release)
		}()

		RunMilestoneDAG(context.Background(), m,
			func(_ context.Context, _ int, ms *Milestone) error {
				mu.Lock()
				started = append(started, ms.ID)
				mu.Unlock()
				admitted <- struct{}{}
				<-release
				return nil
			},
			DAGOptions{MaxWorkers: 2, LogFn: func(string) {}, WarnFn: func(string) {}})

		mu.Lock()
		order := append([]string(nil), started...)
		mu.Unlock()
		if len(order) != len(m.Milestones) {
			t.Fatalf("iteration %d: %d milestones ran, want %d", it, len(order), len(m.Milestones))
		}
		// The first MaxWorkers admitted must be the first two in list
		// order — that is what the FIFO queue guarantees.
		first := map[string]bool{order[0]: true, order[1]: true}
		if !first["a"] || !first["b"] {
			t.Fatalf("iteration %d: admission order %v — the first two admitted "+
				"were not milestones a and b, so Go is not FIFO where Python is",
				it, order)
		}
	}
}

// ThreadPoolExecutor spawns worker threads LAZILY — measured on this
// box, max_workers=100000 with three submits keeps three threads alive.
// The Go pool spawned opts.MaxWorkers goroutines regardless of how much
// work there was, and mission.milestone_workers is operator-set, so a
// fat-fingered value was a memory event on one runtime only — and a
// mission that dies that way writes a different store than one that
// completes (adversarial mission-r5 LOW).
func TestDAGWorkerCountFollowsTheWorkNotTheConfig(t *testing.T) {
	before := runtime.NumGoroutine()

	m := &Mission{ID: "m", Milestones: []Milestone{
		{ID: "a", Title: "A"}, {ID: "b", Title: "B"},
	}}
	peak := before
	var mu sync.Mutex
	RunMilestoneDAG(context.Background(), m,
		func(_ context.Context, _ int, _ *Milestone) error {
			mu.Lock()
			if n := runtime.NumGoroutine(); n > peak {
				peak = n
			}
			mu.Unlock()
			return nil
		},
		DAGOptions{MaxWorkers: 5000, LogFn: func(string) {}, WarnFn: func(string) {}})

	// Two milestones must never need thousands of goroutines. The bound
	// is generous on purpose — this is a "did we spawn MaxWorkers"
	// check, not a precise accounting of the runtime's own goroutines.
	if peak-before > 100 {
		t.Fatalf("peak goroutines rose by %d for 2 milestones with "+
			"MaxWorkers=5000 — the pool is sized by the config, not the work",
			peak-before)
	}
}

// A panic in a milestone body must fail THAT MILESTONE and let the
// mission continue, which is what Python gets by construction: the
// worker thread's exception is captured by the Future and re-raised at
// fut.result(), inside the scheduler's try, so _mark_crashed runs.
//
// A Go panic in a worker goroutine has no such boundary and takes the
// process down, losing every other milestone's in-flight work and
// writing nothing (adversarial mission-r5 LOW). markCrashed's own doc
// calls itself the backstop for "anything the milestone body's own
// guards miss", and a panic is exactly that.
func TestAPanickingMilestoneFailsOnlyItself(t *testing.T) {
	m := &Mission{ID: "m", Milestones: []Milestone{
		{ID: "a", Title: "A"}, {ID: "b", Title: "B"}, {ID: "c", Title: "C"},
	}}
	var warns []string
	var mu sync.Mutex

	RunMilestoneDAG(context.Background(), m,
		func(_ context.Context, _ int, ms *Milestone) error {
			if ms.ID == "b" {
				panic("the milestone body exploded")
			}
			ms.Status = "completed"
			return nil
		},
		DAGOptions{
			MaxWorkers: 2,
			LogFn:      func(string) {},
			WarnFn: func(s string) {
				mu.Lock()
				warns = append(warns, s)
				mu.Unlock()
			},
		})

	byID := map[string]*Milestone{}
	for i := range m.Milestones {
		byID[m.Milestones[i].ID] = &m.Milestones[i]
	}
	if got := byID["b"].Status; got != "failed" {
		t.Errorf("the panicking milestone must be marked failed, got %q", got)
	}
	if byID["b"].ValidationResult == nil ||
		!strings.Contains(*byID["b"].ValidationResult, "panic") {
		t.Errorf("the panic must leave durable evidence on the milestone, got %v",
			byID["b"].ValidationResult)
	}
	for _, id := range []string{"a", "c"} {
		if got := byID[id].Status; got != "completed" {
			t.Errorf("milestone %s must still complete, got %q", id, got)
		}
	}
	// warnFn is the DURABLE half of the evidence, per markCrashed's doc.
	var sawCrash bool
	for _, w := range warns {
		if strings.Contains(w, "mission_dag_thread_crash") {
			sawCrash = true
		}
	}
	if !sawCrash {
		t.Errorf("no mission_dag_thread_crash warning was emitted: %v", warns)
	}
}
