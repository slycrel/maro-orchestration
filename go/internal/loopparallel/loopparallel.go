// Package loopparallel ports the SCHEDULERS of loop_parallel.py -- the
// two functions that decide which steps run, in what grouping, and what
// a step that never ran is told about itself.
//
// PORTED: _run_steps_parallel and _run_steps_dag, minus the work each
// step does. Both take the step body as a RunFn, because everything
// inside `_run_one` below the scheduling is a different tranche:
//
//	_execute_step                the executor lane (internal/loop/exec.go)
//	_run_in_step_worktree        internal/worktree, and the run-dir tranche
//	security.scan_external_content   the post-step scan, unported
//	metrics.record_step_cost     the ledger write
//
// NOT PORTED, and named so the boundary is a declaration rather than an
// omission: _drain_pending_context is here (it is pure), but
// _run_parallel_batch and _run_parallel_path -- the two folds that turn
// outcome dicts into StepOutcomes and a LoopResult -- are slice 2. They
// reach loop_post_step, orch and metrics, and none of those have a Go
// twin that a fold could call yet.
//
// WHAT IS ACTUALLY INTERESTING HERE is not the thread pool. It is that
// three of the four ways a step can fail to produce an outcome have a
// DIFFERENT SENTENCE attached, that sentence reaches an operator, and
// two of the four are reachable from a plan a planner wrote:
//
//	"parallel execution error: %s"           the step raised
//	"parallel fan-out timeout (%ds)"         the batch ran out of time
//	"missing from fan-out results"           defensive, unreachable today
//	"dag: upstream dep did not complete"     a dep never resolved
//	"dag timeout (%ds)"                      the dag ran out of time
//	"dag execution error: %s"                the step raised
//
// A dependency tag naming a step that does not exist ("[after:9]" on a
// three-step plan) lands on the fourth of those, forever, and no error
// is raised anywhere. So does a self-dependency. Both are pinned.
package loopparallel

import (
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// DefaultStepTimeout is the "600" literal in
// `int(os.environ.get("MARO_STEP_TIMEOUT", "600"))`, kept as the STRING
// CPython parses rather than the number it yields. The default travels
// through int() exactly like an operator-supplied value does, so a port
// that stored 600 would silently stop testing the parse it shares.
const DefaultStepTimeout = "600"

// FanoutTimeout is that expression. The second return is the exception
// int() would have raised, which is NOT caught anywhere in
// loop_parallel.py -- a MARO_STEP_TIMEOUT of "10m" does not fall back to
// 600, it kills the fan-out before a single step is submitted.
//
// present is whether the variable is SET; Python's os.environ.get only
// reaches the default when the key is absent, so an empty MARO_STEP_TIMEOUT
// is `int("")` and raises rather than defaulting.
func FanoutTimeout(raw string, present bool) (int, error) {
	if !present {
		raw = DefaultStepTimeout
	}
	return pyval.Int(raw)
}

// DrainPendingContext is _drain_pending_context: one delivery boundary
// for a whole batch.
//
// Returns (incremental, ancestry). A nil ledger is the getattr-defensive
// arm -- Python reaches it through a test stub with no ledger attribute,
// and a Go caller reaches it through a LoopContext built without the
// factory, which is the same absence.
func DrainPendingContext(ledger *looptypes.ContributionLedger, ancestry string) (string, string) {
	if ledger == nil {
		return "", ancestry
	}
	// Wall-clock claims never replay: a re-armed [time] line is stale by
	// the time a batch drains it. The drop happens BEFORE the drain and
	// before the empty check, so it is not conditional on there being
	// anything else to render.
	ledger.DropSource("time")
	rendered := looptypes.RenderContributions(ledger.Drain())
	if rendered == "" {
		return "", ancestry
	}
	if ancestry != "" {
		return rendered, ancestry + "\n\n" + rendered
	}
	return rendered, rendered
}

// RunFn is one step's body. depCtx is the completed_context the step
// sees -- always empty on the fan-out path by design, and the rendered
// upstream results on the DAG path.
//
// A non-nil error is the step RAISING, which the schedulers turn into a
// blocked outcome carrying str(exc). It is not a Go-style failure to be
// returned upward: _run_steps_parallel and _run_steps_dag never raise
// out of a step, and a port that propagated would lose the other N-1
// steps' results.
type RunFn func(stepIdx int, stepText string, depCtx []string) (pyval.Obj, error)

// blockedOutcome is the shape every filler shares. Note what is NOT in
// it: no "summary" on the dag or missing-result fillers, and no
// "confidence". A reader doing outcome.get("summary", "") gets "" for a
// dag timeout and a real sentence for a fan-out timeout, which is a
// difference in the DICTS and not in this helper.
func blockedOutcome(reason string) pyval.Obj {
	return pyval.Obj{
		{Key: "status", Val: "blocked"},
		{Key: "stuck_reason", Val: reason},
		{Key: "result", Val: ""},
		{Key: "tokens_in", Val: 0},
		{Key: "tokens_out", Val: 0},
	}
}

// RunStepsParallel is _run_steps_parallel: every step independent, all
// submitted at once, outcomes returned in step-index order.
//
// The error return is CPython's ValueError, not a Go convention.
// `ThreadPoolExecutor(max_workers=n)` raises when n <= 0, and n here is
// `min(max_workers, len(steps))` -- so an EMPTY step list raises rather
// than returning an empty list, and so does a parallel_fan_out of 0.
// _run_steps_dag does not share that arithmetic and answers differently
// for the same empty plan; both are fixtured.
func RunStepsParallel(steps []string, maxWorkers, timeoutSecs int, run RunFn) ([]pyval.Obj, error) {
	nWorkers := maxWorkers
	if len(steps) < nWorkers {
		nWorkers = len(steps)
	}
	if nWorkers <= 0 {
		return nil, &pyval.PyErr{Class: "ValueError",
			Msg: "max_workers must be greater than 0"}
	}

	var mu sync.Mutex
	byIdx := map[int]pyval.Obj{}
	deadline := time.After(time.Duration(timeoutSecs) * time.Second)
	sem := make(chan struct{}, nWorkers)
	done := make(chan struct{})
	var wg sync.WaitGroup

	for i, s := range steps {
		wg.Add(1)
		go func(i int, s string) {
			defer wg.Done()
			sem <- struct{}{}
			defer func() { <-sem }()
			oc, err := run(i+1, s, nil)
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				// str(exc), not repr: the operator reads the message,
				// and a port that spelled the class here would fork a
				// line that is grepped side by side with CPython's.
				//
				// EQUIVALENT MUTANT: `%s` with err.Error() and `%v` with
				// err produce the same bytes, because %v on an error
				// calls Error(). The spelling is deliberate anyway --
				// err.Error() says the MESSAGE is what travels, where %v
				// would leave a reader to check what the verb does for
				// this type.
				byIdx[i+1] = pyval.Obj{
					{Key: "status", Val: "blocked"},
					{Key: "stuck_reason", Val: fmt.Sprintf("parallel execution error: %s", err.Error())},
					{Key: "result", Val: ""},
					{Key: "summary", Val: fmt.Sprintf("step %d failed in fan-out", i+1)},
					{Key: "tokens_in", Val: 0},
					{Key: "tokens_out", Val: 0},
				}
				return
			}
			byIdx[i+1] = oc
		}(i, s)
	}
	go func() { wg.Wait(); close(done) }()

	select {
	case <-done:
	case <-deadline:
		// Python fills the un-finished futures and leaves the pool's
		// __exit__ to join them. The steps keep running; their results
		// are DISCARDED, because outcomes_by_idx already has a row.
		mu.Lock()
		for i := range steps {
			if _, ok := byIdx[i+1]; !ok {
				byIdx[i+1] = pyval.Obj{
					{Key: "status", Val: "blocked"},
					{Key: "stuck_reason", Val: fmt.Sprintf("parallel fan-out timeout (%ds)", timeoutSecs)},
					{Key: "result", Val: ""},
					{Key: "summary", Val: fmt.Sprintf("step %d timed out in fan-out", i+1)},
					{Key: "tokens_in", Val: 0},
					{Key: "tokens_out", Val: 0},
				}
			}
		}
		mu.Unlock()
	}

	mu.Lock()
	defer mu.Unlock()
	out := make([]pyval.Obj, 0, len(steps))
	for i := range steps {
		oc, ok := byIdx[i+1]
		if !ok {
			// "shouldn't happen, but defensive" -- and its dict is a
			// character short of the others: no "summary" key at all.
			//
			// EQUIVALENT MUTANT, and honestly so: no input reaches this
			// line on either runtime. Every future either resolves and
			// writes a row, or is filled by the timeout arm above, so
			// the map is complete by the time the loop reads it. The
			// wording is untestable BECAUSE the branch is unreachable,
			// and it stays because Python has it -- this port
			// reproduces Python, including its dead defences (L31 says
			// the answer to a survivor is sometimes to delete the code;
			// that is not this one, because deleting it would make the
			// two files disagree about what happens next).
			oc = blockedOutcome("missing from fan-out results")
		}
		out = append(out, oc)
	}
	return out, nil
}

// DepContext is the completed_context a DAG step is handed: its DIRECT
// deps' results, in ascending dep order, each clipped.
//
// The two clips are Python string slices and therefore count CODE
// POINTS, not bytes -- a dep step whose text is CJK loses two thirds of
// its label to a byte-counting port. A dep with an empty result
// contributes NO LINE (not an empty one), which is how a blocked
// upstream disappears from the downstream prompt entirely rather than
// announcing itself.
func DepContext(steps []string, deps map[int][]int, results map[int]pyval.Obj, stepIdx int) ([]string, error) {
	n := len(steps)
	idxs := append([]int(nil), deps[stepIdx]...)
	sort.Ints(idxs)
	var out []string
	for _, depIdx := range idxs {
		var depResult any = ""
		if oc, ok := results[depIdx]; ok {
			if v, present := oc.Get("result"); present {
				depResult = v
			}
		}
		depText := ""
		if depIdx >= 1 && depIdx <= n {
			depText = steps[depIdx-1]
		}
		// `if dep_result:` and `dep_result[:600]` are not string
		// operations in Python and are not spelled as one here: an
		// outcome whose "result" is a LIST is truthy when non-empty,
		// slices to a list, and renders through str() inside the
		// f-string. Reading it as a string instead would drop the line
		// entirely (L18 -- a value arrives with a type, and something
		// reads the type away).
		if !pyval.Truthy(depResult) {
			continue
		}
		head, err := pyval.SliceHead(depResult, 600)
		if err != nil {
			// An int result raises TypeError HERE, before _execute_step
			// is called, and nothing in _run_one catches it -- the
			// future carries it out and the scheduler spells it
			// "dag execution error: 'int' object is not subscriptable".
			// Returning it is what keeps that sentence CPython's.
			return nil, err
		}
		out = append(out, fmt.Sprintf("Step %d (%s):\n%s",
			depIdx, pyval.Clip(depText, 60), pyval.Str(head)))
	}
	return out, nil
}

// RunStepsDAG is _run_steps_dag: submit what has no pending deps, and
// when a step lands, submit whatever it just unblocked.
//
// max_workers is passed to the pool UNCHANGED here -- no min() with the
// step count -- so the empty-plan and zero-fan-out cases part company
// with RunStepsParallel: an empty plan returns an empty list, and only a
// max_workers of 0 or less raises.
func RunStepsDAG(steps []string, deps map[int][]int, maxWorkers, timeoutSecs int, run RunFn) ([]pyval.Obj, error) {
	if maxWorkers <= 0 {
		return nil, &pyval.PyErr{Class: "ValueError",
			Msg: "max_workers must be greater than 0"}
	}
	n := len(steps)

	remaining := map[int]map[int]bool{}
	for i := 1; i <= n; i++ {
		set := map[int]bool{}
		for _, d := range deps[i] {
			set[d] = true
		}
		remaining[i] = set
	}

	var mu sync.Mutex
	var wg sync.WaitGroup
	results := map[int]pyval.Obj{}
	active := map[int]bool{}
	type landed struct {
		idx int
		err error
	}
	ch := make(chan landed, n)
	sem := make(chan struct{}, maxWorkers)

	submitReady := func() {
		for idx := 1; idx <= n; idx++ {
			if _, done := results[idx]; done {
				continue
			}
			if active[idx] {
				continue
			}
			if len(remaining[idx]) > 0 {
				continue
			}
			active[idx] = true
			wg.Add(1)
			go func(idx int) {
				defer wg.Done()
				sem <- struct{}{}
				defer func() { <-sem }()
				mu.Lock()
				depCtx, depErr := DepContext(steps, deps, results, idx)
				mu.Unlock()
				var oc pyval.Obj
				err := depErr
				if depErr == nil {
					oc, err = run(idx, steps[idx-1], depCtx)
				}
				if err == nil {
					// _run_one writes its own result before the future
					// resolves, which is why a dependent submitted in
					// the same tick can already see it.
					mu.Lock()
					results[idx] = oc
					mu.Unlock()
				}
				ch <- landed{idx, err}
			}(idx)
		}
	}

	mu.Lock()
	submitReady()
	timedOut := false
	deadline := time.After(time.Duration(timeoutSecs) * time.Second)
	for len(active) > 0 && !timedOut {
		mu.Unlock()
		var l landed
		select {
		case l = <-ch:
		case <-deadline:
			mu.Lock()
			for idx := range active {
				if _, ok := results[idx]; !ok {
					results[idx] = blockedOutcome(
						fmt.Sprintf("dag timeout (%ds)", timeoutSecs))
				}
			}
			timedOut = true
			continue
		}
		mu.Lock()
		delete(active, l.idx)
		if l.err != nil {
			results[l.idx] = blockedOutcome(
				fmt.Sprintf("dag execution error: %s", l.err.Error()))
		}
		// Only steps that are neither finished nor in flight have their
		// dep sets touched. discard() on a set that never held the index
		// is a no-op, which is why an out-of-range dep is never removed
		// by anything and the step it guards is never submitted.
		//
		// EQUIVALENT MUTANT: dropping the in-flight test changes no
		// answer. A step that is in flight already had an empty dep set
		// when it was submitted, and by the time anything consults that
		// set again the step has a row in results -- on the raising path
		// too, which the main loop writes -- so submitReady skips it on
		// the results check first. The guard is CPython's spelling, and
		// it is kept for that reason rather than for an effect.
		for idx := 1; idx <= n; idx++ {
			if _, done := results[idx]; done {
				continue
			}
			if active[idx] {
				continue
			}
			delete(remaining[idx], l.idx)
		}
		submitReady()
	}
	mu.Unlock()

	// THE TIMEOUT FILLER IS NOT THE LAST WORD, and this is the
	// difference the differential found. `break` leaves the while loop,
	// but the `with ThreadPoolExecutor(...)` block has not exited yet,
	// and its __exit__ is shutdown(wait=True) -- so every in-flight step
	// runs to completion and _run_one WRITES ITS OWN RESULT before the
	// return statement is ever reached, overwriting the
	// "dag timeout (Ns)" row that was just put there for it.
	//
	// So that sentence only survives for a step that never finishes at
	// all, or one that RAISES (the raising arm writes nothing, and the
	// main loop's except is unreachable after the break). Its
	// DEPENDENTS, meanwhile, keep "upstream dep did not complete" --
	// a plan where the upstream visibly succeeded and the downstream
	// says it did not.
	//
	// _run_steps_parallel does NOT share this: there, only the main
	// thread writes outcomes_by_idx, from f.result(), so its own
	// timeout filler is final. Two paths, one clock, opposite answers.
	wg.Wait()

	mu.Lock()
	defer mu.Unlock()
	out := make([]pyval.Obj, 0, n)
	for i := 1; i <= n; i++ {
		oc, ok := results[i]
		if !ok {
			oc = blockedOutcome("dag: upstream dep did not complete")
		}
		out = append(out, oc)
	}
	return out, nil
}

// StepLabel and DAGStepLabel are the labels the two schedulers hand
// _run_in_step_worktree, which is not a display string: worktree.provision
// builds a BRANCH from it (`maro/<loop_id>/<label>`), so the two spellings
// are the reason a fan-out step and a dag step of the same number do not
// collide on one repo.
//
// They are here rather than in the worktree tranche because the sequence
// that produces them is the scheduler's, and a port that regenerated them
// at the worktree end would be inventing a name CPython did not.
func StepLabel(stepIdx int) string    { return fmt.Sprintf("step%d", stepIdx) }
func DAGStepLabel(stepIdx int) string { return fmt.Sprintf("dagstep%d", stepIdx) }
