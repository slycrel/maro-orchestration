package orch

import (
	"context"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The milestone DAG scheduler and the validation gate — the rest of
// mission.py's planning half.
//
// `run_one` is a parameter in Python and stays one here, which is what
// makes this portable ahead of the agent loop: the scheduler's contract
// is entirely about ORDER and TERMINALITY, and a fake run_one exercises
// all of it.
//
// CONCURRENCY, STATED HONESTLY (rewritten after adversarial mission-r1
// MEDIUM; the previous version of this paragraph was wrong in three
// separate ways and is worth recording as a caution).
//
// It claimed a mutex here was "the price of the same behaviour in a
// language that means it", that `go test -race` proved it necessary, and
// that PersistFn was "called under the lock". In fact:
//
//   - Python's _mark_crashed takes NO lock (mission.py:386-396). There
//     was no Python behaviour being paid for.
//   - markCrashed's only callers are the scheduler's own goroutine — the
//     stall lane and the completion drain — so the mutex was never once
//     contended. Deleting it outright left `-race` green.
//   - The race it was described as closing was still open: PersistFn
//     walks EVERY milestone, including ones whose runOne is still
//     writing to them, and runOne never took the lock.
//
// What is actually true: the scheduler mutates only terminal milestones
// and only from its own goroutine, so it needs no lock of its own.
//
// NAMED RESIDUAL — a PersistFn that reads milestones the scheduler has
// not yet marked terminal races with those milestones' own bodies. Python
// has the same LOGICAL tearing (a whole-mission snapshot is not atomic
// under the GIL either) but no undefined behaviour; Go has both. It
// cannot be closed at this layer, because runOne is an injected seam and
// locking around it would serialise the concurrency the DAG exists to
// buy. It closes in slice 3, where run_mission supplies the real persist
// and can snapshot under the same lock its milestone bodies write under.
// Until then: do not pass a PersistFn that reads non-terminal milestones.

// RunOne executes one milestone. It returns an error where the Python
// callable would raise; the scheduler's backstop treats the two the same.
type RunOne func(ctx context.Context, idx int, ms *Milestone) error

// DAGOptions carries the scheduler's injected seams.
type DAGOptions struct {
	MaxWorkers int
	// LogFn is the verbose-gated operator line. Python's log_fn.
	LogFn func(string)
	// PersistFn saves the mission mid-flight. Optional in Python and
	// optional here. Called on the SCHEDULER's goroutine, never a
	// milestone's — see the package doc's named residual for what that
	// does and does not make safe.
	PersistFn func()
	// WarnFn is the module logger's warning channel — the DURABLE half.
	// Python calls log.warning beside every log_fn here precisely because
	// a verbose-gated line is not evidence, so the two are separate
	// seams rather than one.
	WarnFn func(string)
}

// RunMilestoneDAG is _run_milestone_dag: execute milestones as a
// dependency DAG, MaxWorkers at a time.
//
// `depends_on` is ORDERING only, deliberately matching the sequential
// walk's semantics: a dependent starts once its dependencies reach a
// TERMINAL status, regardless of whether they passed. The sequential loop
// always continued past failed milestones ("later milestones may have
// independent sub-goals"), and gating on outcome would silently change
// what a mission produces. Dependency ids that don't name a milestone in
// this mission are ignored — there is nothing to wait for.
//
// Cycle-freedom is a property of the DECOMPOSE writer (earlier-index refs
// only), not of the field: LoadMission accepts any string ids, so a
// hand-edited mission.json can encode a genuine cycle. That cannot
// deadlock — when no milestone is ready and none is running, the
// remainder executes in list order with a logged warning — and both the
// stall lane and the pool lane share the same crash backstop
// (mark-failed, warn, persist, never propagate).
func RunMilestoneDAG(ctx context.Context, m *Mission, runOne RunOne, opts DAGOptions) {
	if opts.MaxWorkers < 1 {
		opts.MaxWorkers = 2
	}
	logFn := opts.LogFn
	if logFn == nil {
		logFn = func(string) {}
	}
	warnFn := opts.WarnFn
	if warnFn == nil {
		warnFn = func(string) {}
	}

	known := map[string]bool{}
	for i := range m.Milestones {
		known[m.Milestones[i].ID] = true
	}
	terminal := map[string]bool{}
	submitted := map[string]bool{}

	// No mutex: every call below is on this goroutine, and the milestone
	// it touches has already returned from runOne. See the concurrency
	// note in the package doc for the residual this does NOT cover.
	markCrashed := func(ms *Milestone, err error) {
		// Backstop for anything the milestone body's own guards miss:
		// fail the ONE milestone, leave durable evidence, keep the
		// mission going. A verbose-gated log_fn alone is not evidence,
		// which is why warnFn fires too.
		ms.Status = "failed"
		res := fmt.Sprintf("error: %v", err)
		ms.ValidationResult = &res
		logFn(fmt.Sprintf("  milestone thread error: %s: %v",
			pyval.Repr(ms.Title), err))
		warnFn(fmt.Sprintf("mission_dag_thread_crash id=%s milestone=%s exc=%v",
			m.ID, ms.ID, err))
		if opts.PersistFn != nil {
			opts.PersistFn()
		}
	}

	ready := func(ms *Milestone) bool {
		for _, d := range ms.DependsOn {
			if known[d] && !terminal[d] {
				return false
			}
		}
		return true
	}

	type done struct {
		idx int
		err error
	}
	completions := make(chan done)
	slots := make(chan struct{}, opts.MaxWorkers)
	inFlight := 0

	for len(terminal) < len(m.Milestones) {
		for i := range m.Milestones {
			ms := &m.Milestones[i]
			if submitted[ms.ID] || !ready(ms) {
				continue
			}
			submitted[ms.ID] = true
			inFlight++
			go func(idx int, ms *Milestone) {
				slots <- struct{}{}
				defer func() { <-slots }()
				completions <- done{idx, runOne(ctx, idx, ms)}
			}(i, ms)
		}

		if inFlight == 0 {
			// Nothing is ready and nothing is running: the depends_on
			// graph is malformed (a cycle, or a ref that resolves only
			// to itself). Run the remainder in list order rather than
			// deadlock, and RETURN — matching Python, which does not
			// re-enter the scheduling loop afterwards.
			for i := range m.Milestones {
				ms := &m.Milestones[i]
				if submitted[ms.ID] {
					continue
				}
				logFn(fmt.Sprintf(
					"milestone DAG stall (malformed depends_on) — running %s in list order",
					pyval.Repr(ms.Title)))
				// deps is rendered with Python's str() of a LIST, not Go's
				// %v: log.warning("... deps=%s", ms.depends_on) prints
				// ['m1'], and %v on a []string prints [m1]. WarnFn is the
				// durable half of this evidence, so a log parser keyed on
				// the Python spelling missed every Go row (adversarial
				// mission-r1 MEDIUM).
				warnFn(fmt.Sprintf("mission_dag_stall id=%s milestone=%s deps=%s",
					m.ID, ms.ID, pyval.ReprStrings(ms.DependsOn)))
				if err := runOne(ctx, i, ms); err != nil {
					markCrashed(ms, err)
				}
				submitted[ms.ID] = true
				terminal[ms.ID] = true
			}
			return
		}

		// FIRST_COMPLETED: take one, then drain whatever else has
		// already finished, exactly as Python's `wait` returns a SET.
		d := <-completions
		inFlight--
		if d.err != nil {
			markCrashed(&m.Milestones[d.idx], d.err)
		}
		terminal[m.Milestones[d.idx].ID] = true
		for drained := true; drained && inFlight > 0; {
			select {
			case d := <-completions:
				inFlight--
				if d.err != nil {
					markCrashed(&m.Milestones[d.idx], d.err)
				}
				terminal[m.Milestones[d.idx].ID] = true
			default:
				drained = false
			}
		}
	}
}

// ValidateMilestone is _validate_milestone: the LLM gate between a
// milestone's features finishing and the milestone being called done.
//
// It DEFAULTS TO PASS, at every one of its four exits — no criteria, dry
// run, no adapter, unparseable answer. Python's comment says why ("don't
// get stuck in validation loops") and the shape is deliberate, so a Go
// port that failed closed on an adapter error would quietly turn a
// transient outage into a stuck mission.
func ValidateMilestone(
	ctx context.Context,
	ms *Milestone,
	project string,
	a llm.Adapter,
	dryRun bool,
) bool {
	if len(ms.ValidationCriteria) == 0 {
		return true
	}
	if dryRun || a == nil {
		return true
	}

	var features, criteria string
	for i := range ms.Features {
		f := &ms.Features[i]
		if i > 0 {
			features += "\n"
		}
		features += "- " + f.Title + ": " + f.Status
		// `if f.result_summary` is TRUTHINESS: an empty summary adds no
		// suffix at all, and the clip is 200 CODE POINTS.
		if f.ResultSummary != nil && *f.ResultSummary != "" {
			features += " — " + pyval.Clip(*f.ResultSummary, 200)
		}
	}
	for i, c := range ms.ValidationCriteria {
		if i > 0 {
			criteria += "\n"
		}
		criteria += "- " + c
	}

	userMsg := "Milestone: " + ms.Title + "\n\n" +
		"Validation criteria:\n" + criteria + "\n\n" +
		"Completed features:\n" + features + "\n\n" +
		"Did this milestone succeed? Respond with JSON only."

	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: validateSystem},
		{Role: "user", Content: userMsg},
	}, llm.Options{
		MaxTokens:   256,
		Temperature: 0.1,
		Purpose:     "milestone validation",
	})
	if err != nil {
		return true // Python's blanket `except Exception: pass`
	}
	data, jerr := jsonx.ObjectOrdered(contentOrEmpty(resp))
	if jerr != nil {
		return true
	}
	// EQUIVALENT-MUTANT NOTE: deleting this changes no verdict, because
	// Get("passed") on an empty object reports absent and the absent
	// branch below returns true as well. It stays because it is where
	// Python's `if data:` actually is, and the two stop agreeing the
	// moment the absent-key default stops being true.
	if len(data) == 0 {
		return true // `if data:` — an empty dict is falsy
	}
	// bool(data.get("passed", True)): absent is True, and PRESENT is
	// truthiness, not a bool cast. `"passed": "false"` is a non-empty
	// string, so it passes; `"passed": null` does not.
	v, present := data.Get("passed")
	if !present {
		return true
	}
	return pyval.Truthy(v)
}
