package run

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/regression"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// Regression obligations (LoopsBench item 2): step 1 runs `make test` and
// sees it pass (the scripted transcript says so); step 2 "edits"; at
// closure the engine re-runs `make test` for real in the work dir. With a
// Makefile that now fails, the judge's `achieved` is refuted by the
// observation and the closure resolves not_achieved mechanically; with
// one that passes, the observation supports it and achieved stands.

const closureAchievedSure = `{"outcome": {"type": "choice", "choice": "achieved", "confidence": 0.9, "why": "both steps done", "falsifiers": ["the tests fail"]}}`

var outwardExec = invoke.Capabilities{Name: "scripted-exec", Model: "exec", ActsOutward: true}

// regressionBackends: step 1 executes with one shell effect whose output
// is `stepOut`; step 2 executes with no effects.
func regressionBackends(effect invoke.ScriptedEffect, closure string) (*invoke.Scripted, *invoke.Scripted) {
	exec := &invoke.Scripted{Caps: outwardExec, Calls: []invoke.ScriptedCall{
		{Response: []byte("ran the suite: it passed"), Effects: []invoke.ScriptedEffect{effect}},
		{Response: []byte("edited the source")},
	}}
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{
		{Response: []byte(intentClear)}, {Response: []byte(planTwo)}, {Response: []byte(judgeDone)}, {Response: []byte(judgeDone)}, {Response: []byte(closure)},
	}}
	return exec, judge
}

func makeTestEffect(out string) invoke.ScriptedEffect {
	return invoke.ScriptedEffect{Op: "Bash", Input: []byte(`{"command": "make test", "description": "run the suite"}`), Output: []byte(out)}
}

func writeMakefile(t *testing.T, dir, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, "Makefile"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

const (
	makePass = "test:\n\t@echo suite ok\n"
	makeFail = "test:\n\t@echo 1 failed\n\t@false\n"
)

func TestRegressionRerunRefutesAnAchievedClosure(t *testing.T) {
	for _, c := range []struct {
		name     string
		makefile string
		outcome  regression.Outcome
		result   verdict.ObsResult
		closure  string
		rule     string
	}{
		{"regression", makeFail, regression.Fail, verdict.Refuted, "not_achieved", "refuted_by_observation:regression_rerun"},
		{"still passes", makePass, regression.Pass, verdict.Supported, "achieved", ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := open(t)
			work := t.TempDir()
			writeMakefile(t, work, c.makefile)
			exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
			d := h.agenda(exec, judge)
			d.Work = work
			rep, err := d.Run(ctxBg, []byte("Fix the parser and keep the suite green"), DeliveryPolicy{Required: TransportAccepted})
			if err != nil {
				t.Fatal(err)
			}
			if rep.Mission.Closure != c.closure {
				t.Fatalf("closure %s: %+v", rep.Mission.Closure, rep.Mission)
			}
			rs := h.only()
			a := rs.Latest()
			if len(a.Regression) != 1 || len(a.Observations) != 1 {
				t.Fatalf("reruns=%d observations=%d", len(a.Regression), len(a.Observations))
			}
			rr, o := a.Regression[0], a.Observations[0]
			if rr.Step != 1 || rr.Invocation != a.Steps[0].Invocation || rr.Dir != work || strings.Join(rr.Argv, " ") != "make test" || rr.Outcome != c.outcome || rr.Why != "" {
				t.Fatalf("rerun: %+v", *rr)
			}
			if o.Check != verdict.CheckRegressionRerun || o.Result != c.result || o.Confidence != 1 || o.Evidence[0].ID != string(rr.ID) || a.observationOf(rr.ID) != o {
				t.Fatalf("observation: %+v", *o)
			}
			// the resolution names the observation and re-derives with it
			if rs.Closure == nil || len(rs.Closure.Observations) != 1 || rs.Closure.Observations[0] != o.ID || rs.Closure.Outcome != c.closure || !strings.HasPrefix(rs.Closure.Rule, c.rule) {
				t.Fatalf("resolution: %+v", rs.Closure)
			}
			if out := a.Has(Recorded).Outcome; out.ClosureOut != c.closure {
				t.Fatalf("recorded: %+v", out)
			}
			// the closure judge saw the re-run before judging
			cp, err := h.st.Get(a.Invocations[len(a.Invocations)-1].Invocation.Request)
			if err != nil {
				t.Fatal(err)
			}
			// the typed closure request carries the re-runs as its own section
			want := "### regression\n- `make test` in " + work + " passed at step 1 and "
			if !bytes.Contains(cp, []byte(want)) {
				t.Fatalf("closure prompt: %s", cp)
			}
			if c.outcome == regression.Fail && !bytes.Contains(cp, []byte("FAILS at closure (exit 2) — a regression")) {
				t.Fatalf("closure prompt: %s", cp)
			}
			// the re-run's own bytes are on record
			if rr.Stdout == nil {
				t.Fatal("no stdout")
			}
			if b, _ := h.st.Get(*rr.Stdout); (c.outcome == regression.Pass && string(b) != "suite ok\n") || (c.outcome == regression.Fail && string(b) != "1 failed\n") {
				t.Fatalf("stdout %q", b)
			}
			// runs show says it
			var shown string
			for _, l := range Inspect(rs) {
				if strings.HasPrefix(l, "regression (attempt 1, proved at step 1): make test in ") {
					shown = l
				}
			}
			if shown == "" || !strings.HasSuffix(shown, string(c.outcome)+" (exit "+map[regression.Outcome]string{regression.Pass: "0", regression.Fail: "2"}[c.outcome]+")") {
				t.Fatalf("inspect: %q", shown)
			}
			// the fold accepts what the driver wrote (a restart re-derives)
			h.restart()
			if r2 := h.only(); r2.Closure == nil || r2.Closure.Outcome != c.closure || len(r2.Latest().Regression) != 1 {
				t.Fatal("not re-derived after restart")
			}
		})
	}
}

// Nothing becomes an obligation without positive evidence: a shell call
// with no result, an error result, a failure tally in the output, a
// command that is a shell program, a non-shell tool, and a passed runner
// under a step the judge did not find done all leave the ledger empty.
func TestRegressionObligationNeedsPositiveEvidence(t *testing.T) {
	prog := invoke.ScriptedEffect{Op: "Bash", Input: []byte(`{"command": "make test || true"}`), Output: []byte("suite ok")}
	for _, c := range []struct {
		name   string
		effect invoke.ScriptedEffect
		judge1 string
	}{
		{"no result seen", invoke.ScriptedEffect{Op: "Bash", Input: []byte(`{"command": "make test"}`), Unanswered: true}, judgeDone},
		{"error result", invoke.ScriptedEffect{Op: "Bash", Input: []byte(`{"command": "make test"}`), Output: []byte("suite ok"), IsError: true}, judgeDone},
		{"failure tally", makeTestEffect("2 passed, 1 failed"), judgeDone},
		{"empty output", makeTestEffect("  "), judgeDone},
		{"a shell program", prog, judgeDone},
		{"not a shell tool", invoke.ScriptedEffect{Op: "Read", Input: []byte(`{"command": "make test"}`), Output: []byte("suite ok")}, judgeDone},
		{"step not done", makeTestEffect("suite ok"), judgeUnclear},
	} {
		t.Run(c.name, func(t *testing.T) {
			h := open(t)
			work := t.TempDir()
			writeMakefile(t, work, makeFail)
			exec, judge := regressionBackends(c.effect, closureAchievedSure)
			judge.Calls[2].Response = []byte(c.judge1)
			d := h.agenda(exec, judge)
			d.Work = work
			rep, err := d.Run(ctxBg, []byte("Fix the parser and keep the suite green"), DeliveryPolicy{Required: TransportAccepted})
			if err != nil {
				t.Fatal(err)
			}
			if h.count(KindRegressionRerun) != 0 || h.count(verdict.KindObservation) != 0 || rep.Mission.Closure != "achieved" {
				t.Fatalf("reruns=%d observations=%d closure=%s", h.count(KindRegressionRerun), h.count(verdict.KindObservation), rep.Mission.Closure)
			}
			// and the closure prompt is byte-for-byte the one without the block
			a := h.only().Latest()
			cp, _ := h.st.Get(a.Invocations[len(a.Invocations)-1].Invocation.Request)
			if bytes.Contains(cp, []byte("### regression")) {
				t.Fatalf("closure prompt: %s", cp)
			}
		})
	}
}

// Two effects with the same argv+dir are one obligation; the same argv in
// another dir is another; an inconclusive re-run (the dir is not there)
// observes could_not_observe and moves the closure nowhere.
func TestRegressionObligationsDedupAndInconclusive(t *testing.T) {
	h := open(t)
	work := t.TempDir()
	writeMakefile(t, work, makePass)
	exec := &invoke.Scripted{Caps: outwardExec, Calls: []invoke.ScriptedCall{
		{Response: []byte("ran"), Effects: []invoke.ScriptedEffect{
			makeTestEffect("suite ok"),
			makeTestEffect("suite ok again"),
			{Op: "Bash", Input: []byte(`{"command": "cd never-made && make test"}`), Output: []byte("suite ok")},
		}},
		{Response: []byte("edited")},
	}}
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{
		{Response: []byte(intentClear)}, {Response: []byte(planTwo)}, {Response: []byte(judgeDone)}, {Response: []byte(judgeDone)}, {Response: []byte(closureAchievedSure)},
	}}
	d := h.agenda(exec, judge)
	d.Work = work
	rep, err := d.Run(ctxBg, []byte("Keep the suite green"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if len(a.Regression) != 2 || a.Regression[0].Outcome != regression.Pass || a.Regression[1].Outcome != regression.Inconclusive || a.Regression[1].Why == "" {
		t.Fatalf("%+v", a.Regression)
	}
	if o := a.Observations[1]; o.Result != verdict.CouldNotObserve || o.Confidence != 0 {
		t.Fatalf("%+v", *o)
	}
	if rep.Mission.Closure != "achieved" {
		t.Fatalf("%+v", rep.Mission)
	}
}

// A kill between the re-runs and the closure verdict, two obligations
// (the root and a sub dir): the resumed attempt reuses exactly the
// recorded re-runs BY ID (the Makefiles are repaired in between; a second
// run would pass) and re-runs only what was not, and the closure is still
// refuted; the fold re-derives the prompt the resumed judge was shown.
func TestRegressionRerunSurvivesTheKill(t *testing.T) {
	for _, c := range []struct {
		seam   string
		reused int // re-runs made by attempt 1 that attempt 2 shows again
		made   int // re-runs attempt 2 makes itself
	}{{"after_regression#1", 1, 1}, {"after_regression#2", 2, 0}} {
		t.Run(c.seam, func(t *testing.T) {
			h := open(t)
			work := t.TempDir()
			sub := filepath.Join(work, "sub")
			if err := os.MkdirAll(sub, 0o755); err != nil {
				t.Fatal(err)
			}
			writeMakefile(t, work, makeFail)
			writeMakefile(t, sub, makeFail)
			effects := []invoke.ScriptedEffect{makeTestEffect("suite ok"), {Op: "Bash", Input: []byte(`{"command": "cd sub && make test"}`), Output: []byte("sub ok")}}
			backends := func() (*invoke.Scripted, *invoke.Scripted) {
				exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
				exec.Calls[0].Effects = effects
				return exec, judge
			}
			exec, judge := backends()
			d := h.agenda(exec, judge)
			d.Work = work
			d.CrashAt = c.seam
			if _, err := d.Run(ctxBg, []byte("Keep the suite green"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatalf("err=%v", err)
			}
			first := h.only().Latest().Regression
			if len(first) != c.reused {
				t.Fatalf("attempt 1 made %d re-runs", len(first))
			}
			writeMakefile(t, work, makePass)
			writeMakefile(t, sub, makePass)
			h.restart()
			exec2, judge2 := backends()
			d = h.agenda(exec2, judge2)
			d.Work = work
			reps, err := d.Resume(ctxBg)
			if err != nil || len(reps) != 1 {
				t.Fatalf("%v %d", err, len(reps))
			}
			if reps[0].Mission.Closure != "not_achieved" || h.count(KindRegressionRerun) != 2 || h.count(verdict.KindObservation) != 2 {
				t.Fatalf("closure=%s reruns=%d observations=%d", reps[0].Mission.Closure, h.count(KindRegressionRerun), h.count(verdict.KindObservation))
			}
			rs := h.only()
			a := rs.Latest()
			if a.Attempt.Attempt != 2 || len(a.Regression) != c.made {
				t.Fatalf("attempt %d made %d", a.Attempt.Attempt, len(a.Regression))
			}
			// the resolution names the reused observations and the new ones, in obligation order
			if rs.Closure == nil || len(rs.Closure.Observations) != 2 {
				t.Fatalf("resolution %+v", rs.Closure)
			}
			for i, rr := range first {
				if rs.Closure.Observations[i] != rs.Attempts[0].observationOf(rr.ID).ID {
					t.Fatalf("observation %d is not re-run %s's", i, rr.ID)
				}
			}
			// the closure judge saw both, root first — and the fold agreed (the verdict folded)
			cp := judge2.Seen[len(judge2.Seen)-1].Prompt
			root, subline := bytes.Index(cp, []byte("`make test` in "+work+" passed at step 1 and FAILS")), bytes.Index(cp, []byte("`make test` in "+sub+" passed at step 1 and "))
			if root < 0 || subline < 0 || root > subline {
				t.Fatalf("closure prompt: %s", cp)
			}
		})
	}
}

// A NOW execute's passed runner is an obligation too (every closure).
func TestNowRegressionObligation(t *testing.T) {
	h := open(t)
	work := t.TempDir()
	writeMakefile(t, work, makeFail)
	exec := &invoke.Scripted{Caps: outwardExec, Calls: []invoke.ScriptedCall{{Response: []byte("fixed and tested"), Effects: []invoke.ScriptedEffect{makeTestEffect("suite ok")}}}}
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureAchievedSure)}}}
	d := h.driver(exec, nil)
	d.Judge, d.ModelJudge, d.Work = judge, true, work
	rep, err := d.Run(ctxBg, []byte("Fix the parser and keep the suite green"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if rep.Mission.Closure != "not_achieved" || len(a.Regression) != 1 || a.Regression[0].Step != 0 || a.Regression[0].Outcome != regression.Fail {
		t.Fatalf("%+v %+v", rep.Mission, a.Regression)
	}
	cp := judge.Seen[0].Prompt
	if !bytes.Contains(cp, []byte("- `make test` in "+work+" passed at the execute and FAILS at closure")) {
		t.Fatalf("closure prompt: %s", cp)
	}
}

// A NOW attempt that dies after its execute landed: the recovery reuses
// the landed call (attempt 1's), derives the obligation from IT, and the
// fold agrees — the obligation is the judged execute's, whichever
// attempt's list it sits in.
func TestNowRegressionObligationSurvivesTheKill(t *testing.T) {
	h := open(t)
	work := t.TempDir()
	writeMakefile(t, work, makeFail)
	exec := &invoke.Scripted{Caps: outwardExec, Calls: []invoke.ScriptedCall{{Response: []byte("fixed and tested"), Effects: []invoke.ScriptedEffect{makeTestEffect("suite ok")}}}}
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureAchievedSure)}}}
	d := h.driver(exec, nil)
	d.Judge, d.ModelJudge, d.Work = judge, true, work
	d.CrashAt = "after_execute"
	if _, err := d.Run(ctxBg, []byte("Fix the parser and keep the suite green"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("err=%v", err)
	}
	h.restart()
	exec2 := &invoke.Scripted{Caps: outwardExec}
	judge2 := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureAchievedSure)}}}
	d = h.driver(exec2, nil)
	d.Judge, d.ModelJudge, d.Work = judge2, true, work
	reps, err := d.Resume(ctxBg)
	if err != nil || len(reps) != 1 || reps[0].Mission.Closure != "not_achieved" {
		t.Fatalf("%v %+v", err, reps)
	}
	if len(exec2.Seen) != 0 {
		t.Fatal("the landed execute was re-made")
	}
	rs := h.only()
	a := rs.Latest()
	if a.Attempt.Attempt != 2 || len(a.Regression) != 1 || a.Regression[0].Invocation != rs.Attempts[0].Invocations[0].Invocation.ID || a.Regression[0].Step != 0 {
		t.Fatalf("%+v", a.Regression)
	}
	h.restart()
	if r2 := h.only(); r2.Closure == nil || r2.Closure.Outcome != "not_achieved" {
		t.Fatal("not re-derived after restart")
	}
}

// The fold re-derives: a re-run citing an obligation the steps do not
// derive, a re-run whose outcome disagrees with its exit code, and an
// observation that disagrees with its re-run are each refused.
func TestForgedRegressionRerunIsRefused(t *testing.T) {
	setup := func(t *testing.T) (*harness, *RunState, *AttemptState) {
		h := open(t)
		work := t.TempDir()
		writeMakefile(t, work, makePass)
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		d := h.agenda(exec, judge)
		d.Work = work
		d.CrashAt = "after_closure_invoke"
		if _, err := d.Run(ctxBg, []byte("Keep the suite green"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		rs := h.only()
		return h, rs, rs.Latest()
	}
	t.Run("no such obligation", func(t *testing.T) {
		h, rs, a := setup(t)
		real := a.Regression[0]
		forged := *real
		forged.ID, forged.Seq, forged.Argv = record.NewID(), 0, []string{"make", "test", "-k"}
		forged.Invocation = a.Steps[1].Invocation // step 2 ran no shell command
		if err := forge(t, h, "forge/1", &forged); err == nil || !strings.Contains(err.Error(), "cites no obligation") {
			t.Fatalf("err=%v", err)
		}
		_ = rs
	})
	t.Run("outcome disagrees with the exit", func(t *testing.T) {
		h, _, a := setup(t)
		real := a.Regression[0]
		// re-running the same obligation twice is refused before the outcome is read
		dup := *real
		dup.ID, dup.Seq = record.NewID(), 0
		if err := forge(t, h, "forge/2", &dup); err == nil || !strings.Contains(err.Error(), "twice") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a why does not launder failing bytes", func(t *testing.T) {
		h, _, a := setup(t)
		// the setup's re-run is a pass over "suite ok"; a copy claiming
		// inconclusive-with-a-why over the same exit-0 bytes is refused
		// (the bytes say pass) — and so is a fail over exit -1
		forged := *a.Regression[0]
		forged.ID, forged.Seq, forged.Attempt = record.NewID(), 0, 2
		forged.Outcome, forged.Why = regression.Inconclusive, "runner missing"
		h.restart()
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		d := h.agenda(exec, judge)
		d.Work = forged.Dir
		d.CrashAt = "after_executing"
		if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		if err := forge(t, h, "forge/5", &forged); err == nil || !strings.Contains(err.Error(), "does not follow from exit 0") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a truncated flag does not launder failing bytes", func(t *testing.T) {
		h, _, a := setup(t)
		// the flag is not free: over the setup's short "suite ok" bytes it
		// is refused outright (a truncated capture kept MaxCapture bytes)
		short := *a.Regression[0]
		short.ID, short.Seq, short.Attempt = record.NewID(), 0, 2
		short.Truncated, short.Outcome, short.Why = true, regression.Inconclusive, "output exceeded the capture"
		h.restart()
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		d := h.agenda(exec, judge)
		d.Work = short.Dir
		d.CrashAt = "after_executing"
		if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		if err := forge(t, h, "forge/5t", &short); err == nil || !strings.Contains(err.Error(), "claims a truncated capture") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a truncated tail that says failed is a fail", func(t *testing.T) {
		h, _, a := setup(t)
		// a full MaxCapture tail ending in a failure tally, exit 0, flagged
		// truncated and called inconclusive: the bytes say Fail
		tail := append(bytes.Repeat([]byte("x"), regression.MaxCapture-len("\n1 failed\n")), []byte("\n1 failed\n")...)
		ref, err := h.st.Put(thought.Evidence, tail)
		if err != nil {
			t.Fatal(err)
		}
		full := *a.Regression[0]
		full.ID, full.Seq, full.Attempt = record.NewID(), 0, 2
		full.Truncated, full.Stdout, full.Outcome, full.Why = true, &ref, regression.Inconclusive, "output exceeded the capture"
		h.restart()
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		d := h.agenda(exec, judge)
		d.Work = full.Dir
		d.CrashAt = "after_executing"
		if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		if err := forge(t, h, "forge/5f", &full); err == nil || !strings.Contains(err.Error(), "does not follow from exit 0") || !strings.Contains(err.Error(), "(fail)") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a signal death is never a fail", func(t *testing.T) {
		h, _, a := setup(t)
		signal := *a.Regression[0]
		signal.ID, signal.Seq, signal.Attempt, signal.Exit, signal.Outcome = record.NewID(), 0, 2, -1, regression.Fail
		h.restart()
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		d := h.agenda(exec, judge)
		d.Work = signal.Dir
		d.CrashAt = "after_executing"
		if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		if err := forge(t, h, "forge/6", &signal); err == nil || !strings.Contains(err.Error(), "does not follow from exit -1") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("observation cites another effect", func(t *testing.T) {
		h, _, a := setup(t)
		lie := *a.Observations[0]
		lie.ID, lie.Seq = record.NewID(), 0
		lie.Evidence = []record.Ref{lie.Evidence[0], {Kind: invoke.KindToolEffect, ID: "not-the-effect"}}
		if err := forge(t, h, "forge/7", &lie); err == nil || !strings.Contains(err.Error(), "not its re-run's") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("observation cites the re-run alone", func(t *testing.T) {
		h, _, a := setup(t)
		lone := *a.Observations[0]
		lone.ID, lone.Seq = record.NewID(), 0
		lone.Evidence = lone.Evidence[:1]
		if err := forge(t, h, "forge/8", &lone); err == nil || !strings.Contains(err.Error(), "exactly a re-run and its tool effect") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a re-run without its observation has no place in a closure", func(t *testing.T) {
		h := open(t)
		work := t.TempDir()
		sub := filepath.Join(work, "sub")
		if err := os.MkdirAll(sub, 0o755); err != nil {
			t.Fatal(err)
		}
		writeMakefile(t, work, makePass)
		writeMakefile(t, sub, makePass)
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		exec.Calls[0].Effects = append(exec.Calls[0].Effects, invoke.ScriptedEffect{Op: "Bash", Input: []byte(`{"command": "cd sub && make test"}`), Output: []byte("sub ok")})
		d := h.agenda(exec, judge)
		d.Work = work
		d.CrashAt = "after_regression#1" // the root re-run landed with its observation; the sub one did not run
		if _, err := d.Run(ctxBg, []byte("Keep the suite green"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		rs := h.only()
		a := rs.Latest()
		orphan := *a.Regression[0]
		orphan.ID, orphan.Seq, orphan.Dir = record.NewID(), 0, sub
		orphan.Effect = a.Invocations[2].Effects[1].ID
		if err := forge(t, h, "forge/9", &orphan); err != nil {
			t.Fatalf("a re-run alone is a valid record: %v", err)
		}
		led, err := Fold(h.j.Production(), h.st)
		if err != nil {
			t.Fatal(err)
		}
		inv, err := invoke.Fold(h.j.Production())
		if err != nil {
			t.Fatal(err)
		}
		rs = led.Runs[a.Attempt.RunID]
		if _, err := closureReruns(rs, rs.Latest(), inv, h.st); err == nil || !strings.Contains(err.Error(), "has no observation") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a re-run of an obligation an earlier attempt already re-ran", func(t *testing.T) {
		h, _, a := setup(t)
		again := *a.Regression[0]
		again.ID, again.Seq, again.Attempt = record.NewID(), 0, 2
		h.restart()
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		d := h.agenda(exec, judge)
		d.Work = again.Dir
		d.CrashAt = "after_executing"
		if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		if err := forge(t, h, "forge/10", &again); err == nil || !strings.Contains(err.Error(), "already re-ran") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("observation disagrees with its re-run", func(t *testing.T) {
		h, _, a := setup(t)
		real := a.Observations[0]
		lie := *real
		lie.ID, lie.Seq, lie.Result = record.NewID(), 0, verdict.Refuted
		if err := forge(t, h, "forge/3", &lie); err == nil || !strings.Contains(err.Error(), "does not say what its re-run") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a fresh attempt: outcome must follow from exit and output", func(t *testing.T) {
		h, rs, a := setup(t)
		real := a.Regression[0]
		// a second attempt (recovery) that re-runs and lies about the outcome
		forged := *real
		forged.ID, forged.Seq, forged.Attempt, forged.Outcome = record.NewID(), 0, 2, regression.Fail
		// commit the second attempt's start first
		h.restart()
		exec, judge := regressionBackends(makeTestEffect("suite ok"), closureAchievedSure)
		d := h.agenda(exec, judge)
		d.Work = real.Dir
		d.CrashAt = "after_executing"
		if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		if err := forge(t, h, "forge/4", &forged); err == nil || !strings.Contains(err.Error(), "does not follow from exit") {
			t.Fatalf("err=%v", err)
		}
		_ = rs
	})
}

func TestRegressionRerunWire(t *testing.T) {
	ref := thought.Address(thought.Evidence, []byte("suite ok\n"))
	good := func() *RegressionRerun {
		return &RegressionRerun{Header: header(runRef("run-1"), "run-1", 1, "regression_rerun/1"), Step: 1, Invocation: "inv", Effect: "eff", Argv: []string{"make", "test"}, Dir: "/w", Exit: 0, Stdout: &ref, Outcome: regression.Pass}
	}
	if err := good().ValidateWire(); err != nil {
		t.Fatal(err)
	}
	for name, mut := range map[string]func(*RegressionRerun){
		"no argv":                    func(r *RegressionRerun) { r.Argv = nil },
		"no dir":                     func(r *RegressionRerun) { r.Dir = "" },
		"no effect":                  func(r *RegressionRerun) { r.Effect = "" },
		"outcome out of vocabulary":  func(r *RegressionRerun) { r.Outcome = "maybe" },
		"classified with a why":      func(r *RegressionRerun) { r.Why = "hm" },
		"inconclusive without a why": func(r *RegressionRerun) { r.Outcome = regression.Inconclusive },
		"no attempt":                 func(r *RegressionRerun) { r.Attempt = 0 },
		"stdout ref malformed":       func(r *RegressionRerun) { bad := thought.Ref{Hash: "x"}; r.Stdout = &bad },
	} {
		r := good()
		mut(r)
		if r.ValidateWire() == nil {
			t.Errorf("%s accepted", name)
		}
	}
	// the wire round-trips: step 0 (the NOW execute) is on the wire, not omitted
	now := good()
	now.Step = 0
	b, err := json.Marshal(now)
	if err != nil || !bytes.Contains(b, []byte(`"step":0`)) {
		t.Fatalf("%v %s", err, b)
	}
	var back RegressionRerun
	if err := json.Unmarshal(b, &back); err != nil || back.ValidateWire() != nil || !reflect.DeepEqual(back.Argv, now.Argv) || *back.Stdout != *now.Stdout || back.Outcome != now.Outcome {
		t.Fatalf("round trip: %v %+v", err, back)
	}
	for name, mut := range map[string]func(*RegressionRerun){
		"exit out of range":      func(r *RegressionRerun) { r.Exit = 999 },
		"timed out with an exit": func(r *RegressionRerun) { r.TimedOut, r.Exit = true, 0 },
		"stdout not evidence": func(r *RegressionRerun) {
			ref := thought.Address(thought.Prompt, []byte("suite ok\n"))
			r.Stdout = &ref
		},
	} {
		r := good()
		mut(r)
		if r.ValidateWire() == nil {
			t.Errorf("%s accepted", name)
		}
	}
	if o, c := regressionResult(regression.Inconclusive); o != verdict.CouldNotObserve || c != 0 {
		t.Fatal("inconclusive mapping")
	}
}
