package run

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The work-dir binding (audit §2a, §4.3): a run works in ONE directory, and
// its attempt config says which and how it came by it. A run that continues
// a stopped run works where that run worked — its files, its half-done
// state, its idea of "the current directory" — unless the operator named
// another; a fresh run works in the workspace's own work/ (or wherever the
// operator pointed it). The binding is a claim the fold checks against the
// continuation record and the source's own config, and every invocation
// that carries a working directory carries the attempt's. Python's edge is
// the project binding (operator > landscape > named > minted); Go has no
// projects, so the directory is the whole of it.
//
// Precedence: the operator's --work, then the continued run's dir, then the
// default. Attempts after the first repeat the first's binding: a resumed
// attempt works where the run works, not where the resuming process
// defaults to.

// WorkBinding says how a run came by its work dir.
type WorkBinding string

const (
	// WorkDefault: the driver's default (the workspace's own work/; "" in
	// tests, which is the backend's default and records nothing on the
	// invocations).
	WorkDefault WorkBinding = "default"
	// WorkOperator: named on the command line (or by the caller).
	WorkOperator WorkBinding = "operator"
	// WorkContinued: the directory the continued run worked in, taken from
	// its attempt-1 config. Present only on a run whose continuation claim
	// was not refused and whose source recorded a work dir.
	WorkContinued WorkBinding = "continued"
)

var workBindings = map[WorkBinding]bool{WorkDefault: true, WorkOperator: true, WorkContinued: true}

// workOf is where a run works: its bound attempt's config's dir; for a run
// that predates the binding, the one dir every call of it that carried one
// recorded (the cwd on the invocation was the only record then, and every
// call of an attempt ran in its dir — the planner's too, so a plan is
// followed where it was made; review r3 of the grounding gate: an attempt
// that died after its plan and before its first execute left no execute to
// read, and its plan was executed in today's default) — "" when it has no
// attempt, ran with none, or its calls disagree (review r1: an old run
// that worked under --work must be continued THERE, not in today's
// default).
func workOf(rs *RunState) string {
	if rs == nil || len(rs.Attempts) == 0 {
		return ""
	}
	if b := boundAttempt(rs); b != nil {
		return b.Attempt.Config.Work
	}
	dir := ""
	for _, a := range rs.Attempts {
		for _, is := range a.Invocations {
			if c := is.Invocation.Cwd; c != "" {
				if dir != "" && c != dir {
					return ""
				}
				dir = c
			}
		}
	}
	return dir
}

// workedIn: some call of the run ran in dir (the dir was there, and the
// run's files are in it).
func workedIn(rs *RunState, dir string) bool {
	for _, a := range rs.Attempts {
		for _, is := range a.Invocations {
			if is.Invocation.Cwd == dir {
				return true
			}
		}
	}
	return false
}

// boundAttempt is the attempt whose config answers for the run's work dir:
// the first bound one — attempt 1, or for a run that predates the binding,
// the attempt that adopted it (review r2: attempt 1 alone let a migrated
// run move on its next resume). nil = no attempt is bound.
func boundAttempt(rs *RunState) *AttemptState {
	for _, a := range rs.Attempts {
		if a.Attempt.Config.WorkBinding != "" {
			return a
		}
	}
	return nil
}

// bindWork decides the attempt's work dir. The run's first bound attempt
// decides; later attempts repeat it (one work dir per run). A continued
// dir must still exist for every attempt that will work there — and so
// must any dir the run has worked in, whatever bound it (review r3: an
// `operator`-bound dir that was gone was re-created empty under the old
// name and the resume went on in it as if the run's files were there); a
// bound dir no call has run in yet is made as usual.
func (d *Driver) bindWork(rs *RunState) (string, WorkBinding, error) {
	if b := boundAttempt(rs); b != nil {
		cfg := b.Attempt.Config
		if cfg.WorkBinding == WorkContinued || (cfg.Work != "" && workedIn(rs, cfg.Work)) {
			if err := existingWork(cfg.Work); err != nil {
				return "", "", err
			}
		}
		return cfg.Work, cfg.WorkBinding, nil
	}
	if len(rs.Attempts) > 0 {
		// a run that predates the binding: where its executes ran
		// (`operator`: the run was pointed there, by whom the record
		// does not say), else the resuming driver's own choice
		if w := workOf(rs); w != "" {
			return w, WorkOperator, nil
		}
	}
	return d.absWork(d.Work, d.WorkDefault, rs)
}

func (d *Driver) absWork(named, def string, rs *RunState) (string, WorkBinding, error) {
	abs := func(p string) (string, error) {
		if p == "" {
			return "", nil
		}
		a, err := filepath.Abs(p)
		if err != nil {
			return "", fmt.Errorf("%w: work dir %q: %v", ErrConfig, p, err)
		}
		return a, nil
	}
	if named != "" {
		a, err := abs(named)
		return a, WorkOperator, err
	}
	if rs.Continuation != nil && rs.Continuation.Refused == "" && rs.SourceWork != "" {
		if err := existingWork(rs.SourceWork); err != nil {
			return "", "", err
		}
		return rs.SourceWork, WorkContinued, nil
	}
	a, err := abs(def)
	return a, WorkDefault, err
}

// work is the working directory for every request of an attempt: its
// config's, made to exist (mkdir -p; 0755). Tool-less requests run there
// too, not only the tool-bearing ones (comparison rerun 2026-09-06: a
// tool-less planner inherited the LAUNCHER's cwd, the CLI told it that was
// its working directory, and it baked that absolute path into a step the
// tool-bearing execute then followed — the file landed outside the work
// dir). The work dir is what "the current directory" means to the run, for
// every call that could name it.
//
// A CONTINUED dir is never created: it is where the source's files are,
// and a path that no longer exists is not that (review r1: mkdir would
// hand the continuation an empty dir under the old name and call it the
// source's). The run stops before the call with a clear error; restore
// the dir and resume.
func (d *Driver) work(cfg ConfigSnapshot) (string, error) {
	if cfg.Work == "" {
		return "", nil
	}
	if cfg.WorkBinding == WorkContinued {
		return cfg.Work, existingWork(cfg.Work)
	}
	if err := os.MkdirAll(cfg.Work, 0o755); err != nil {
		return "", fmt.Errorf("%w: work dir %q: %v", ErrConfig, cfg.Work, err)
	}
	return cfg.Work, nil
}

// existingWork is the continued dir's precondition: it exists and is a
// directory.
func existingWork(dir string) error {
	st, err := os.Stat(dir)
	if err != nil || !st.IsDir() {
		return fmt.Errorf("%w: the run's work dir %q is gone: restore it and resume", ErrConfig, dir)
	}
	return nil
}

// checkWork executes the binding over one invocation as it attaches: an
// execute ran exactly in the attempt's work dir; any other call that
// carries a working directory carries that one. An attempt that predates
// the binding (no work_binding in its config) is held to nothing here —
// the attempt rule refuses such a config once the journal shows bound ones.
func checkWork(rs *RunState, a *AttemptState, is *invoke.State) error {
	inv, cfg := is.Invocation, a.Attempt.Config
	if cfg.WorkBinding == "" {
		return nil
	}
	if inv.Purpose == invoke.PurposeExecute && inv.Cwd != cfg.Work {
		return fmt.Errorf("run: %s attempt %d execute invocation %s ran in %q but the attempt works in %q", rs.Run, a.Attempt.Attempt, inv.ID, inv.Cwd, cfg.Work)
	}
	if inv.Cwd != "" && inv.Cwd != cfg.Work {
		return fmt.Errorf("run: %s attempt %d %s invocation %s ran in %q but the attempt works in %q", rs.Run, a.Attempt.Attempt, inv.Purpose, inv.ID, inv.Cwd, cfg.Work)
	}
	return nil
}

// checkWorkBinding executes the binding over an attempt as it starts: a
// later attempt repeats the run's first bound attempt's (or, when every
// earlier attempt predates the binding, is held to the binding's meaning
// like a first attempt and to where the run's executes ran); a
// `continued` binding names exactly
// where the run's unrefused source worked; a run that continues a run
// which recorded a work dir does not fall back to the default (the
// operator may override — that is `operator`); and once the journal shows
// bound configs, an unbound one is a config the engine no longer writes.
func checkWorkBinding(rs *RunState, x *RunAttempt, runs map[record.RunID]*RunState, firstBound uint64) error {
	cfg := x.Config
	if cfg.WorkBinding == "" {
		if firstBound != 0 && x.Seq > firstBound {
			return fmt.Errorf("run: %s attempt %d started with no work binding after the journal shows them", x.RunID, x.Attempt)
		}
		return nil
	}
	if x.Attempt > 1 {
		if b := boundAttempt(rs); b != nil {
			first := b.Attempt.Config
			if first.Work != cfg.Work || first.WorkBinding != cfg.WorkBinding {
				return fmt.Errorf("run: %s attempt %d moved the work dir to %q (%s) from attempt %d's %q (%s)", x.RunID, x.Attempt, cfg.Work, cfg.WorkBinding, b.Attempt.Attempt, first.Work, first.WorkBinding)
			}
			return nil
		}
		// every earlier attempt predates the binding: this attempt is the
		// run's first bound one and answers for the binding's meaning
		// (below) — and for where the run's own executes already ran
		if w := workOf(rs); w != "" && w != cfg.Work {
			return fmt.Errorf("run: %s attempt %d binds %q but the run's executes ran in %q", x.RunID, x.Attempt, cfg.Work, w)
		}
	}
	continued := rs.Continuation != nil && rs.Continuation.Refused == ""
	var src *RunState
	if continued {
		src = runs[rs.Continuation.Source]
	}
	switch cfg.WorkBinding {
	case WorkContinued:
		if !continued {
			return fmt.Errorf("run: %s attempt 1 claims a continued work dir but continues nothing", x.RunID)
		}
		if w := workOf(src); w == "" || w != cfg.Work {
			return fmt.Errorf("run: %s attempt 1 claims to work where %s worked, but that was %q, not %q", x.RunID, rs.Continuation.Source, w, cfg.Work)
		}
	case WorkDefault:
		if continued {
			if w := workOf(src); w != "" {
				return fmt.Errorf("run: %s attempt 1 continues %s, which worked in %q, but took the default work dir %q", x.RunID, rs.Continuation.Source, w, cfg.Work)
			}
		}
	}
	return nil
}
