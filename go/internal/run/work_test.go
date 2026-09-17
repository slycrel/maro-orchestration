package run

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// A run works in one directory and its attempt config says which and how:
// the operator's --work, else where the run it continues worked, else the
// driver's default. A continuation with no operator dir works where its
// source worked — every execute of it runs there, and the invocations say
// so; the operator's dir overrides; a source that recorded no dir binds
// nothing (the default, honestly). A plain follow of a finished run is not
// a continuation and takes the default.
func TestContinuationWorksWhereTheSourceWorked(t *testing.T) {
	h := open(t)
	base := t.TempDir()
	srcWork, def, named := filepath.Join(base, "src"), filepath.Join(base, "default"), filepath.Join(base, "named")
	// A: a stopped run the operator pointed at srcWork
	d := h.driver(scripted(outward, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
	d.Fresh, d.Work, d.WorkDefault = true, srcWork, def
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	if !Stopped(a) || a.Latest().Attempt.Config.Work != srcWork || a.Latest().Attempt.Config.WorkBinding != WorkOperator {
		t.Fatalf("a: stopped=%v config=%+v", Stopped(a), a.Latest().Attempt.Config)
	}
	// B continues A with no --work: it works in srcWork, bound `continued`
	exec := scripted(outward, okCall)
	led := h.ledger()
	lin, err := LineageOf(led, HandleOf(a.Run))
	if err != nil {
		t.Fatal(err)
	}
	db := h.driver(exec, nil)
	db.After, db.WorkDefault = lin, def
	if _, err := db.Run(ctxBg, []byte(goalFlaky+" (retry)"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	b := h.newestRun(t)
	if b.Continuation == nil || b.Continuation.Source != a.Run || b.SourceWork != srcWork {
		t.Fatalf("b continuation: %+v source_work=%q", b.Continuation, b.SourceWork)
	}
	if cfg := b.Latest().Attempt.Config; cfg.Work != srcWork || cfg.WorkBinding != WorkContinued {
		t.Fatalf("b config: work=%q binding=%q", cfg.Work, cfg.WorkBinding)
	}
	if len(exec.Seen) != 1 || exec.Seen[0].Cwd != srcWork {
		t.Fatalf("b execute: %+v", exec.Seen)
	}
	if s := Summarize(b); s.Work != srcWork || s.WorkBinding != WorkContinued {
		t.Fatalf("summary: work=%q binding=%q", s.Work, s.WorkBinding)
	}
	if _, err := os.Stat(def); !os.IsNotExist(err) {
		t.Fatalf("the default dir was made although nothing worked there: %v", err)
	}
	var seen bool
	for _, e := range h.events {
		if e.Stage == "work" && e.Detail == srcWork+" (continued)" {
			seen = true
		}
	}
	if !seen {
		t.Fatalf("no work event: %+v", h.events)
	}
	// C: the operator names a dir on a continuation: the operator wins
	h2 := open(t)
	a2 := h2.stoppedNow(t, goalFlaky)
	// a2 ran with no work dir (the harness's default); give the source one
	// by hand is not possible after the fact — so make a bound source
	da2 := h2.driver(scripted(outward, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
	da2.Fresh, da2.Work = true, srcWork
	if _, err := da2.Run(ctxBg, []byte("Rotate the API key"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a3 := h2.newestRun(t)
	exec3 := scripted(outward, okCall)
	lin3, err := LineageOf(h2.ledger(), HandleOf(a3.Run))
	if err != nil {
		t.Fatal(err)
	}
	dc := h2.driver(exec3, nil)
	dc.After, dc.Work, dc.WorkDefault = lin3, named, def
	if _, err := dc.Run(ctxBg, []byte("Rotate the API key (retry)"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	c := h2.newestRun(t)
	if cfg := c.Latest().Attempt.Config; cfg.Work != named || cfg.WorkBinding != WorkOperator || exec3.Seen[0].Cwd != named {
		t.Fatalf("c: config=%+v cwd=%q", cfg, exec3.Seen[0].Cwd)
	}
	// D: a source that recorded no work dir (a2) binds nothing: the default
	exec4 := scripted(outward, okCall)
	lin4, err := LineageOf(h2.ledger(), HandleOf(a2.Run))
	if err != nil {
		t.Fatal(err)
	}
	dd := h2.driver(exec4, nil)
	dd.After, dd.WorkDefault = lin4, def
	if _, err := dd.Run(ctxBg, []byte(goalFlaky+" (retry)"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	dr := h2.newestRun(t)
	if dr.Continuation == nil || dr.Continuation.Refused != "" {
		t.Fatalf("d continuation: %+v", dr.Continuation)
	}
	if cfg := dr.Latest().Attempt.Config; cfg.Work != def || cfg.WorkBinding != WorkDefault || exec4.Seen[0].Cwd != def {
		t.Fatalf("d: config=%+v cwd=%q", cfg, exec4.Seen[0].Cwd)
	}
	// E: a plain follow of a finished run (dr) is no continuation: default
	exec5 := scripted(outward, okCall)
	lin5, err := LineageOf(h2.ledger(), HandleOf(dr.Run))
	if err != nil {
		t.Fatal(err)
	}
	de := h2.driver(exec5, nil)
	de.After, de.WorkDefault = lin5, named
	if _, err := de.Run(ctxBg, []byte(goalFlaky+" (again)"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	e := h2.newestRun(t)
	if e.Continuation != nil || e.Latest().Attempt.Config.WorkBinding != WorkDefault || exec5.Seen[0].Cwd != named {
		t.Fatalf("e: continuation=%+v config=%+v", e.Continuation, e.Latest().Attempt.Config)
	}
	// everything above folds from disk
	h.restart()
	h.ledger()
	h2.restart()
	h2.ledger()
}

// The binding survives the kill: a continuation that died after attempt 1
// started resumes under a driver that knows only the default, and its
// attempt 2 works where attempt 1 did (the run's dir, not the resuming
// process's).
func TestWorkBindingSurvivesTheResume(t *testing.T) {
	h := open(t)
	base := t.TempDir()
	srcWork, def := filepath.Join(base, "src"), filepath.Join(base, "default")
	d := h.driver(scripted(outward, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
	d.Fresh, d.Work = true, srcWork
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	lin, err := LineageOf(h.ledger(), HandleOf(a.Run))
	if err != nil {
		t.Fatal(err)
	}
	db := h.driver(scripted(outward, okCall), nil)
	db.After, db.WorkDefault, db.CrashAt = lin, def, "after_executing"
	if _, err := db.Run(ctxBg, []byte(goalFlaky+" (retry)"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("crash: %v", err)
	}
	// the dir gone between the crash and the resume: no attempt 2 is
	// written (review r2: attempt 1's check alone let a resume commit an
	// attempt it could not work in)
	if err := os.RemoveAll(srcWork); err != nil {
		t.Fatal(err)
	}
	h.restart()
	if _, err := h.driver(scripted(outward, okCall), nil).Resume(ctxBg); !errors.Is(err, ErrConfig) || !strings.Contains(err.Error(), "is gone") {
		t.Fatalf("gone on resume: %v", err)
	}
	if b := h.newestRun(t); len(b.Attempts) != 1 {
		t.Fatalf("an attempt was written for a gone dir: %d", len(b.Attempts))
	}
	if err := os.MkdirAll(srcWork, 0o755); err != nil {
		t.Fatal(err)
	}
	h.restart()
	exec := scripted(outward, okCall)
	dr := h.driver(exec, nil)
	dr.Work, dr.WorkDefault = filepath.Join(base, "other"), def // even a named dir on the resuming driver does not move the run
	reps, err := dr.Resume(ctxBg)
	if err != nil || len(reps) != 1 {
		t.Fatalf("resume: %v %d", err, len(reps))
	}
	b := h.newestRun(t)
	if len(b.Attempts) != 2 {
		t.Fatalf("attempts: %d", len(b.Attempts))
	}
	for i, at := range b.Attempts {
		if at.Attempt.Config.Work != srcWork || at.Attempt.Config.WorkBinding != WorkContinued {
			t.Fatalf("attempt %d: %+v", i+1, at.Attempt.Config)
		}
	}
	if len(exec.Seen) != 1 || exec.Seen[0].Cwd != srcWork {
		t.Fatalf("resumed execute: %+v", exec.Seen)
	}
	if Stopped(b) {
		t.Fatalf("b: %+v", MissionOf(b))
	}
}

// Must-detect fixtures for the binding: each forged history is wire-valid
// record by record (the door takes it) and refused by the fold's cross-
// record rule — one history per forgery, with a control that folds.
func TestForgedWorkBindingIsRefused(t *testing.T) {
	base := t.TempDir()
	srcWork, other := filepath.Join(base, "src"), filepath.Join(base, "other")
	// setup: A stopped in srcWork; a goal G that follows A, taken in but
	// not started; the forged attempt is built from the engine's own config
	type fx struct {
		h   *harness
		a   *RunState
		g   *Goal
		fam *FamilyAssessment
	}
	setup := func(t *testing.T) fx {
		h := open(t)
		d := h.driver(scripted(outward, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
		d.Fresh, d.Work = true, srcWork
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		a := h.newestRun(t)
		ref, _ := h.st.Put(thought.Goal, []byte(goalFlaky+" (retry)"))
		g, fam := Intake([]byte(goalFlaky+" (retry)"), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		g.Parent, g.Root = a.Goal.ID, a.Root
		if err := IntakeCommand(ctxBg, h.j, nil, g, fam); err != nil {
			t.Fatal(err)
		}
		return fx{h, a, g, fam}
	}
	// workClaim is the engine's own claim record for run on source
	workClaim := func(h *harness, g *Goal, source, run record.RunID) *Continuation {
		return &Continuation{Header: header(runRef(run), run, 0, "continuation/1"), Goal: g.ID, Source: source, How: ContinuedAfter, AsOf: h.j.Head()}
	}
	// attempt builds attempt 1 of a new run for g with the binding given
	attempt := func(t *testing.T, f fx, run record.RunID, work string, binding WorkBinding) []record.Record {
		lled, _ := learn.Fold(f.h.j.Production())
		rs0 := &RunState{Run: run, Goal: f.g, Root: f.g.Root}
		pol := learn.SelectPolicy(lled, learn.Query{Scope: scope(rs0), Standing: learn.Selectable})
		pol.Header = header(runRef(run), run, 1, "policy_selection/1")
		d := f.h.driver(scripted(toolless), nil)
		d.validate()
		cfg, _ := d.config(LaneNow, pol)
		cfg.Work, cfg.WorkBinding = work, binding
		att := &RunAttempt{Header: header(runRef(run), run, 1, "run_attempt/1"), Goal: f.g.ID, Family: f.fam.ID, Config: cfg}
		recs := []record.Record{pol}
		for i, rule := range lled.PolicyRules(pol) {
			recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, run, 1, "policy_application/1"), Item: pol.Enabled[i].Item, Revision: pol.Enabled[i].Revision, Selection: pol.ID, Rule: rule})
		}
		return append(recs, att, &Transition{Header: header(runRef(run), run, 1, "run_transition/1"), To: Created})
	}
	t.Run("control: the claim then the bound attempt folds", func(t *testing.T) {
		f := setup(t)
		run := record.RunID(record.NewID())
		if err := forge(t, f.h, "forge/w0", workClaim(f.h, f.g, f.a.Run, run)); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, f.h, "forge/w0b", attempt(t, f, run, srcWork, WorkContinued)...); err != nil {
			t.Fatalf("the control was refused: %v", err)
		}
	})
	t.Run("operator on a continuation folds, wherever it points (the stated limitation)", func(t *testing.T) {
		f := setup(t)
		run := record.RunID(record.NewID())
		if err := forge(t, f.h, "forge/w0c", workClaim(f.h, f.g, f.a.Run, run)); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, f.h, "forge/w0d", attempt(t, f, run, other, WorkOperator)...); err != nil {
			t.Fatalf("operator is the operator's: %v", err)
		}
	})
	t.Run("an invocation before its attempt", func(t *testing.T) {
		h := open(t)
		exec := scripted(outward, okCall)
		d := h.driver(exec, nil)
		d.Work, d.CrashAt = srcWork, "after_execute"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		real := rs.Latest().Invocations[0]
		twin, recs := invocationTwin(t, rs, real, real.Invocation.Request)
		twin.Invocation.Cwd = other
		for _, r := range recs {
			r.Head().Attempt = 2 // attempt 2 does not exist yet: the call could never attach, and no rule would see its cwd
		}
		if err := forge(t, h, "forge/w8", recs...); err == nil || !strings.Contains(err.Error(), "arrived before the attempt") {
			t.Fatalf("err=%v", err)
		}
		// attempt 0 of a run is the landscape's call and nothing else: an
		// execute there would never attach and could still be cited
		h2 := open(t)
		exec2 := scripted(outward, okCall)
		d2 := h2.driver(exec2, nil)
		d2.Work, d2.CrashAt = srcWork, "after_execute"
		if _, err := d2.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs2 := h2.only()
		real2 := rs2.Latest().Invocations[0]
		twin2, recs2 := invocationTwin(t, rs2, real2, real2.Invocation.Request)
		twin2.Invocation.Cwd = other
		for _, r := range recs2 {
			r.Head().Attempt = 0
		}
		if err := forge(t, h2, "forge/w8b", recs2...); err == nil || !strings.Contains(err.Error(), "names no attempt") {
			t.Fatalf("attempt 0: %v", err)
		}
	})
	t.Run("a migrated run: attempt 2 is held to the binding's meaning, and what it adopts is kept", func(t *testing.T) {
		// attempt 1 predates the binding (the first attempt in the journal,
		// so no watermark refuses it); attempt 2 is the run's first bound
		// one and continues nothing: `continued` is refused, `default`
		// folds — and then answers for the run: attempt 3 elsewhere is a
		// move (review r2), and the summary reports the adopted dir
		mk := func(t *testing.T) (*harness, record.RunID, *Goal, *FamilyAssessment) {
			h := open(t)
			ref, _ := h.st.Put(thought.Goal, []byte(goalFlaky))
			g, fam := Intake([]byte(goalFlaky), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
			if err := IntakeCommand(ctxBg, h.j, nil, g, fam); err != nil {
				t.Fatal(err)
			}
			run := record.RunID(record.NewID())
			ls := &Landscape{Header: header(runRef(run), run, 0, "landscape/1"), Goal: g.ID, AsOf: h.j.Head(), Rule: LandscapeNoCandidates, Floor: LandscapeFloor, TopK: LandscapeTopK, Relation: RelationFresh}
			if err := forge(t, h, "forge/w9", ls); err != nil {
				t.Fatal(err)
			}
			recs := attempt(t, fx{h, nil, g, fam}, run, "", "")
			recs = append(recs, &Transition{Header: header(runRef(run), run, 1, "run_transition/1"), From: Created, To: Executing}, &Transition{Header: header(runRef(run), run, 1, "run_transition/1"), From: Executing, To: Recoverable, Reason: "forged"})
			if err := forge(t, h, "forge/w9b", recs...); err != nil {
				t.Fatalf("the unbound attempt 1 was refused: %v", err)
			}
			return h, run, g, fam
		}
		// attemptN forges attempt n (recovering from n-1, whose config it copies) with the binding given
		attemptN := func(t *testing.T, h *harness, run record.RunID, g *Goal, fam *FamilyAssessment, n uint32, work string, binding WorkBinding) error {
			rs := h.only()
			lled, _ := learn.Fold(h.j.Production())
			pol := learn.SelectPolicy(lled, learn.Query{Scope: scope(rs), Standing: learn.Selectable})
			pol.Header = header(runRef(run), run, n, "policy_selection/1")
			cfg := rs.Latest().Attempt.Config
			cfg.Policy, cfg.Mechanisms = pol.ID, map[learn.Mechanism]bool{}
			for m, on := range pol.Snapshot {
				cfg.Mechanisms[m] = on
			}
			cfg.Work, cfg.WorkBinding = work, binding
			att := &RunAttempt{Header: header(runRef(run), run, n, "run_attempt/1"), Goal: g.ID, Family: fam.ID, Config: cfg, RecoversFrom: n - 1}
			recs := []record.Record{pol}
			for i, rule := range lled.PolicyRules(pol) {
				recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, run, n, "policy_application/1"), Item: pol.Enabled[i].Item, Revision: pol.Enabled[i].Revision, Selection: pol.ID, Rule: rule})
			}
			recs = append(recs, att, &Transition{Header: header(runRef(run), run, n, "run_transition/1"), To: Created})
			return forge(t, h, fmt.Sprintf("forge/w9c/%d", n), recs...)
		}
		h, run, g, fam := mk(t)
		if err := attemptN(t, h, run, g, fam, 2, other, WorkContinued); err == nil || !strings.Contains(err.Error(), "continues nothing") {
			t.Fatalf("continued: %v", err)
		}
		h, run, g, fam = mk(t)
		if err := attemptN(t, h, run, g, fam, 2, srcWork, WorkDefault); err != nil {
			t.Fatalf("the migrated default was refused: %v", err)
		}
		if s := Summarize(h.only()); s.Work != srcWork || s.WorkBinding != WorkDefault {
			t.Fatalf("summary of the migrated run: %q (%s)", s.Work, s.WorkBinding)
		}
		if err := forge(t, h, "forge/w9d", &Transition{Header: header(runRef(run), run, 2, "run_transition/1"), From: Created, To: Executing}, &Transition{Header: header(runRef(run), run, 2, "run_transition/1"), From: Executing, To: Recoverable, Reason: "forged"}); err != nil {
			t.Fatal(err)
		}
		if err := attemptN(t, h, run, g, fam, 3, other, WorkDefault); err == nil || !strings.Contains(err.Error(), "moved the work dir") || !strings.Contains(err.Error(), "from attempt 2's") {
			t.Fatalf("attempt 3 moved: %v", err)
		}
		// and the driver keeps it too: a resume binds attempt 3 to attempt
		// 2's (its own history: the refused forgery above is in the journal)
		h, run, g, fam = mk(t)
		if err := attemptN(t, h, run, g, fam, 2, srcWork, WorkDefault); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, h, "forge/w9e", &Transition{Header: header(runRef(run), run, 2, "run_transition/1"), From: Created, To: Executing}, &Transition{Header: header(runRef(run), run, 2, "run_transition/1"), From: Executing, To: Recoverable, Reason: "forged"}); err != nil {
			t.Fatal(err)
		}
		h.restart()
		exec := scripted(outward, okCall)
		dr := h.driver(exec, nil)
		dr.WorkDefault = other
		if _, err := dr.Resume(ctxBg); err != nil {
			t.Fatal(err)
		}
		if rs := h.only(); len(rs.Attempts) != 3 || rs.Attempts[2].Attempt.Config.Work != srcWork || rs.Attempts[2].Attempt.Config.WorkBinding != WorkDefault || exec.Seen[0].Cwd != srcWork {
			t.Fatalf("resumed attempt 3: %+v cwd=%v", rs.Latest().Attempt.Config, exec.Seen)
		}
		// and a continuation of the migrated run works where it ADOPTED
		// (review r3): the source's attempt 1 is unbound, attempt 2 adopted
		// srcWork; the child, started under another default, records that
		// as its source's dir, binds `continued` to it, executes there, and
		// folds again from disk
		h, run, g, fam = mk(t)
		if err := attemptN(t, h, run, g, fam, 2, srcWork, WorkDefault); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, h, "forge/w9f", &Transition{Header: header(runRef(run), run, 2, "run_transition/1"), From: Created, To: Executing}, &Transition{Header: header(runRef(run), run, 2, "run_transition/1"), From: Executing, To: Recoverable, Reason: "forged"}); err != nil {
			t.Fatal(err)
		}
		h.restart()
		stop := h.driver(scripted(outward, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
		stop.WorkDefault = other
		if _, err := stop.Resume(ctxBg); err != nil {
			t.Fatal(err)
		}
		src := h.only()
		if !Stopped(src) || workOf(src) != srcWork {
			t.Fatalf("the migrated source: stopped=%v works in %q", Stopped(src), workOf(src))
		}
		lin, err := LineageOf(h.ledger(), HandleOf(run))
		if err != nil {
			t.Fatal(err)
		}
		child := scripted(outward, okCall)
		dc := h.driver(child, nil)
		dc.After, dc.WorkDefault = lin, other
		if _, err := dc.Run(ctxBg, []byte(goalFlaky+" (retry)"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		check := func(when string) {
			c := h.newestRun(t)
			if c.Run == run || c.Continuation == nil || c.Continuation.Refused != "" || c.Continuation.Source != run || c.SourceWork != srcWork {
				t.Fatalf("%s: child continuation=%+v source_work=%q", when, c.Continuation, c.SourceWork)
			}
			if cfg := c.Latest().Attempt.Config; cfg.Work != srcWork || cfg.WorkBinding != WorkContinued {
				t.Fatalf("%s: child config work=%q binding=%q", when, cfg.Work, cfg.WorkBinding)
			}
		}
		check("live")
		if len(child.Seen) != 1 || child.Seen[0].Cwd != srcWork {
			t.Fatalf("child execute: %+v", child.Seen)
		}
		h.restart()
		check("refolded from disk")
	})
	t.Run("an AGENDA plan call that ran elsewhere", func(t *testing.T) {
		h := open(t)
		exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo})
		d := h.agenda(exec, judge)
		d.Work, d.CrashAt = srcWork, "after_plan"
		if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		var plan *invoke.State
		for _, is := range rs.Latest().Invocations {
			if is.Invocation.Purpose == invoke.PurposePlan {
				plan = is
			}
		}
		if plan == nil || plan.Invocation.Cwd != srcWork {
			t.Fatalf("plan call: %+v", plan)
		}
		twin, recs := invocationTwin(t, rs, plan, plan.Invocation.Request)
		twin.Invocation.Cwd = other
		if err := forge(t, h, "forge/w10", recs...); err == nil || !strings.Contains(err.Error(), "plan invocation") || !strings.Contains(err.Error(), "but the attempt works in") {
			t.Fatalf("err=%v", err)
		}
		// the control: the same twin in the attempt's dir folds
		h2 := open(t)
		exec2, judge2 := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo})
		d2 := h2.agenda(exec2, judge2)
		d2.Work, d2.CrashAt = srcWork, "after_plan"
		if _, err := d2.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs2 := h2.only()
		for _, is := range rs2.Latest().Invocations {
			if is.Invocation.Purpose == invoke.PurposePlan {
				_, recs := invocationTwin(t, rs2, is, is.Invocation.Request)
				if err := forge(t, h2, "forge/w10b", recs...); err != nil {
					t.Fatalf("control: %v", err)
				}
			}
		}
	})
	t.Run("continued binding to a dir the source did not work in", func(t *testing.T) {
		f := setup(t)
		run := record.RunID(record.NewID())
		if err := forge(t, f.h, "forge/w1", workClaim(f.h, f.g, f.a.Run, run)); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, f.h, "forge/w1b", attempt(t, f, run, other, WorkContinued)...); err == nil || !strings.Contains(err.Error(), "claims to work where") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("continued binding on a run that continues nothing", func(t *testing.T) {
		f := setup(t)
		// G follows A, but A is claimed by nobody here and the forged run
		// carries no claim: the attempt-1 gate fires only once the journal
		// shows claims, so make one first on another goal
		if err := forge(t, f.h, "forge/w2", attempt(t, f, record.RunID(record.NewID()), srcWork, WorkContinued)...); err == nil || !strings.Contains(err.Error(), "continues nothing") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("default binding on a continuation whose source worked somewhere", func(t *testing.T) {
		f := setup(t)
		run := record.RunID(record.NewID())
		if err := forge(t, f.h, "forge/w3", workClaim(f.h, f.g, f.a.Run, run)); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, f.h, "forge/w3b", attempt(t, f, run, other, WorkDefault)...); err == nil || !strings.Contains(err.Error(), "took the default work dir") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("no binding after the journal shows them", func(t *testing.T) {
		f := setup(t)
		run := record.RunID(record.NewID())
		if err := forge(t, f.h, "forge/w4", workClaim(f.h, f.g, f.a.Run, run)); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, f.h, "forge/w4b", attempt(t, f, run, "", "")...); err == nil || !strings.Contains(err.Error(), "no work binding after the journal shows them") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("attempt 2 moved the work dir", func(t *testing.T) {
		h := open(t)
		d := h.driver(scripted(outward, okCall), nil)
		d.Work, d.CrashAt = srcWork, "after_executing"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		a1 := rs.Latest()
		if err := forge(t, h, "forge/w5", &Transition{Header: header(runRef(rs.Run), rs.Run, 1, "run_transition/1"), From: Executing, To: Recoverable, Reason: "forged"}); err != nil {
			t.Fatal(err)
		}
		lled, _ := learn.Fold(h.j.Production())
		pol := learn.SelectPolicy(lled, learn.Query{Scope: scope(rs), Standing: learn.Selectable})
		pol.Header = header(runRef(rs.Run), rs.Run, 2, "policy_selection/1")
		cfg := a1.Attempt.Config
		cfg.Policy, cfg.Mechanisms = pol.ID, map[learn.Mechanism]bool{}
		for m, on := range pol.Snapshot {
			cfg.Mechanisms[m] = on
		}
		cfg.Work, cfg.WorkBinding = other, WorkOperator
		att := &RunAttempt{Header: header(runRef(rs.Run), rs.Run, 2, "run_attempt/1"), Goal: rs.Goal.ID, Family: rs.Family.ID, Config: cfg, RecoversFrom: 1}
		recs := []record.Record{pol}
		for i, rule := range lled.PolicyRules(pol) {
			recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, rs.Run, 2, "policy_application/1"), Item: pol.Enabled[i].Item, Revision: pol.Enabled[i].Revision, Selection: pol.ID, Rule: rule})
		}
		recs = append(recs, att, &Transition{Header: header(runRef(rs.Run), rs.Run, 2, "run_transition/1"), To: Created})
		if err := forge(t, h, "forge/w5b", recs...); err == nil || !strings.Contains(err.Error(), "moved the work dir") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("an execute that ran elsewhere", func(t *testing.T) {
		h := open(t)
		exec := scripted(outward, okCall)
		d := h.driver(exec, nil)
		d.Work, d.CrashAt = srcWork, "after_execute"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		real := rs.Latest().Invocations[0]
		twin, recs := invocationTwin(t, rs, real, real.Invocation.Request)
		twin.Invocation.Cwd = other
		if err := forge(t, h, "forge/w6", recs...); err == nil || !strings.Contains(err.Error(), "but the attempt works in") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("an execute with no dir under a bound attempt", func(t *testing.T) {
		h := open(t)
		exec := scripted(outward, okCall)
		d := h.driver(exec, nil)
		d.Work, d.CrashAt = srcWork, "after_execute"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		real := rs.Latest().Invocations[0]
		twin, recs := invocationTwin(t, rs, real, real.Invocation.Request)
		twin.Invocation.Cwd = ""
		if err := forge(t, h, "forge/w7", recs...); err == nil || !strings.Contains(err.Error(), "but the attempt works in") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("the door: a relative dir, a continued binding with none, a binding out of vocabulary", func(t *testing.T) {
		f := setup(t)
		for _, c := range []struct {
			work    string
			binding WorkBinding
			want    string
		}{{"relative", WorkOperator, "not absolute"}, {"", WorkContinued, "names the dir"}, {"", WorkOperator, "names the dir"}, {srcWork, "", "together"}, {srcWork, "minted", "out of vocabulary"}} {
			recs := attempt(t, f, record.RunID(record.NewID()), c.work, c.binding)
			var att *RunAttempt
			for _, r := range recs {
				if x, ok := r.(*RunAttempt); ok {
					att = x
				}
			}
			if err := att.ValidateWire(); err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("%s/%s: %v", c.work, c.binding, err)
			}
		}
	})
}

// A continued dir that is gone stops the continuation before its first
// attempt (never an empty dir under the old name); restored, the run
// resumes there.
func TestContinuedWorkDirMustExist(t *testing.T) {
	h := open(t)
	base := t.TempDir()
	srcWork := filepath.Join(base, "src")
	d := h.driver(scripted(outward, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
	d.Fresh, d.Work = true, srcWork
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	if err := os.RemoveAll(srcWork); err != nil {
		t.Fatal(err)
	}
	lin, err := LineageOf(h.ledger(), HandleOf(a.Run))
	if err != nil {
		t.Fatal(err)
	}
	exec := scripted(outward, okCall)
	db := h.driver(exec, nil)
	db.After, db.WorkDefault = lin, filepath.Join(base, "default")
	_, err = db.Run(ctxBg, []byte(goalFlaky+" (retry)"), DeliveryPolicy{Required: TransportAccepted})
	if !errors.Is(err, ErrConfig) || !strings.Contains(err.Error(), "is gone") {
		t.Fatalf("gone: %v", err)
	}
	if _, serr := os.Stat(srcWork); !os.IsNotExist(serr) {
		t.Fatal("the gone dir was re-created")
	}
	var b *RunState
	for _, rs := range h.ledger().Runs {
		if rs.Continuation != nil {
			b = rs
		}
	}
	if b == nil || b.Continuation.Source != a.Run || len(b.Attempts) != 0 || len(exec.Seen) != 0 {
		t.Fatalf("the claim stands, no attempt: %+v calls=%d", b, len(exec.Seen))
	}
	// restored: the resume works there
	if err := os.MkdirAll(srcWork, 0o755); err != nil {
		t.Fatal(err)
	}
	h.restart()
	exec2 := scripted(outward, okCall)
	dr := h.driver(exec2, nil)
	dr.WorkDefault = filepath.Join(base, "default")
	if _, err := dr.Resume(ctxBg); err != nil {
		t.Fatal(err)
	}
	b = h.newestRun(t)
	if cfg := b.Latest().Attempt.Config; cfg.Work != srcWork || cfg.WorkBinding != WorkContinued || len(exec2.Seen) != 1 || exec2.Seen[0].Cwd != srcWork {
		t.Fatalf("resumed: %+v calls=%+v", cfg, exec2.Seen)
	}
}

// Where a run that predates the binding worked is what its executes
// recorded: one dir they all agree on; none, or disagreement, is "".
func TestWorkOfALegacyRun(t *testing.T) {
	mk := func(cwds ...string) *RunState {
		a := &AttemptState{Attempt: &RunAttempt{}}
		for _, c := range cwds {
			a.Invocations = append(a.Invocations, &invoke.State{Invocation: &invoke.Invocation{Purpose: invoke.PurposeExecute, Cwd: c}})
		}
		a.Invocations = append(a.Invocations, &invoke.State{Invocation: &invoke.Invocation{Purpose: invoke.PurposeJudge, Cwd: ""}})
		return &RunState{Attempts: []*AttemptState{a}}
	}
	if w := workOf(mk("/srv/job", "/srv/job")); w != "/srv/job" {
		t.Fatalf("agreeing: %q", w)
	}
	if w := workOf(mk("/srv/job", "/srv/other")); w != "" {
		t.Fatalf("disagreeing: %q", w)
	}
	if w := workOf(mk()); w != "" {
		t.Fatalf("no executes: %q", w)
	}
	if w := workOf(nil); w != "" {
		t.Fatalf("nil: %q", w)
	}
	bound := mk("/srv/job")
	bound.Attempts[0].Attempt.Config.Work, bound.Attempts[0].Attempt.Config.WorkBinding = "/bound", WorkOperator
	if w := workOf(bound); w != "/bound" {
		t.Fatalf("bound wins: %q", w)
	}
}
