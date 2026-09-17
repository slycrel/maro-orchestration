package run

import (
	"bytes"
	"errors"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// The gate (LoopsBench item 1): a plan's declared prerequisites are an
// execution contract. Step 3 declares step 1; the judge finds step 1
// unclear; step 2 (no declaration) runs; step 3 is gated — recorded, not
// executed, not judged — and the deliverable says so. The fold accepts
// the gated record (a restart writes nothing). Negative control: with
// step 1 done, step 3 runs.
const (
	planGated    = `{"steps": ["Collect the numbers", "Check the source", {"step": "Write the summary", "after": [1]}]}`
	planChain    = `{"steps": ["Collect the numbers", {"step": "Check the source", "after": [1]}, {"step": "Write the summary", "after": [2]}]}`
	judgeUnclear = `{"outcome": {"type": "choice", "choice": "unclear", "confidence": 0.4, "why": "the result does not show the numbers"}}`
)

func TestAgendaDeclaredPrerequisiteGates(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"Collected: (empty)", "Source checked", "SHOULD NOT RUN"},
		[]string{intentClear, planGated, judgeUnclear, judgeDone, closureUnsure})
	d := h.agenda(exec, judge)
	rep, err := d.Run(ctxBg, []byte("Summarize the quarterly numbers"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if len(exec.Seen) != 2 || len(judge.Seen) != 5 {
		t.Fatalf("exec=%d judge=%d (the gated step ran or was judged)", len(exec.Seen), len(judge.Seen))
	}
	want := "## Step 3: Write the summary\nnot executed: gated by a declared prerequisite that did not end done (step 1 ended unclear)"
	if rep.Mission.Outcome != MissionDelivered || rep.Mission.Closure != "unknown" || !strings.Contains(string(rep.Payload), want) {
		t.Fatalf("%+v\n%s", rep.Mission, rep.Payload)
	}
	rs := h.only()
	a := rs.Latest()
	if len(a.Plan.Edges) != 1 || !equalInts(a.Plan.EdgesAt(3), []int{1}) || a.Plan.EdgesAt(2) != nil {
		t.Fatalf("edges: %+v", a.Plan.Edges)
	}
	if len(a.Steps) != 3 || a.Steps[0].Outcome != StepUnclear || a.Steps[1].Outcome != StepDoneOK || a.Steps[2].Outcome != StepGated || !equalInts(a.Steps[2].GatedBy, []int{1}) || a.Steps[2].Invocation != "" || a.Steps[2].Verdict != "" {
		t.Fatalf("steps: %+v", a.Steps)
	}
	if o := a.Has(Recorded).Outcome; o.Steps != 3 || o.Terminal != invoke.TerminalComplete {
		t.Fatalf("outcome: %+v", o)
	}
	// the executor sees the declaration in the plan it is shown
	var reqs [][]byte
	for _, st := range a.Invocations {
		if st.Invocation.Purpose == invoke.PurposeExecute {
			b, err := h.st.Get(st.Invocation.Request)
			if err != nil {
				t.Fatal(err)
			}
			reqs = append(reqs, b)
		}
	}
	if len(reqs) != 2 || !bytes.Contains(reqs[1], []byte("## Plan\n1. Collect the numbers\n2. Check the source\n3. Write the summary (after 1)\n")) {
		t.Fatalf("step prompt: %q", reqs)
	}
	// the closure judge saw the gap in the step's own words (the typed
	// request renders a step and its result as their own sections)
	if !bytes.Contains(judge.Seen[4].Prompt, []byte("### step 3\nWrite the summary\n")) || !bytes.Contains(judge.Seen[4].Prompt, []byte("### result 3\nnot executed: gated")) {
		t.Fatalf("closure prompt: %q", judge.Seen[4].Prompt)
	}
	// the fold re-derives the gated record: a restart accepts it and writes nothing
	head := h.j.Head()
	h.restart()
	d = h.agenda(exec, judge)
	if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || h.j.Head() != head {
		t.Fatalf("restart over a gated step: %v %d head %d→%d", err, len(reps), head, h.j.Head())
	}
	// negative control: step 1 done → step 3 runs
	h2 := open(t)
	exec2, judge2 := agendaBackends(
		[]string{"Collected 12 rows", "Source checked", "Summary written"},
		[]string{intentClear, planGated, judgeDone, judgeDone, judgeDone, closureYes})
	rep2, err := h2.agenda(exec2, judge2).Run(ctxBg, []byte("Summarize the quarterly numbers"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	a2 := h2.only().Latest()
	if len(exec2.Seen) != 3 || rep2.Mission.Closure != "achieved" || a2.Steps[2].Outcome != StepDoneOK || len(a2.Steps[2].GatedBy) != 0 || !strings.Contains(string(rep2.Payload), "## Step 3: Write the summary\nSummary written") {
		t.Fatalf("control: exec=%d %+v\n%s", len(exec2.Seen), rep2.Mission, rep2.Payload)
	}
}

// A gated step's own dependents gate in turn (it did not end done), and
// the gating text names how each prerequisite ended.
func TestAgendaGateIsTransitive(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"Collected: (empty)", "SHOULD NOT RUN", "SHOULD NOT RUN"},
		[]string{intentClear, planChain, judgeUnclear, closureUnsure})
	d := h.agenda(exec, judge)
	rep, err := d.Run(ctxBg, []byte("Summarize the quarterly numbers"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if len(exec.Seen) != 1 || len(judge.Seen) != 4 || len(a.Steps) != 3 || a.Steps[1].Outcome != StepGated || a.Steps[2].Outcome != StepGated || !equalInts(a.Steps[2].GatedBy, []int{2}) {
		t.Fatalf("exec=%d judge=%d steps=%+v", len(exec.Seen), len(judge.Seen), a.Steps)
	}
	if !strings.Contains(string(rep.Payload), "## Step 2: Check the source\nnot executed: gated by a declared prerequisite that did not end done (step 1 ended unclear)") ||
		!strings.Contains(string(rep.Payload), "## Step 3: Write the summary\nnot executed: gated by a declared prerequisite that did not end done (step 2 ended gated)") {
		t.Fatalf("payload:\n%s", rep.Payload)
	}
}

// Kill matrix over a gated step: a crash right after the gate commits, and
// a crash after the execute that FOLLOWS a gated step. The second is the
// reuse arithmetic: the recovered attempt's in-flight execute is found by
// counting its execute calls, not its steps (a gated step made none) — so
// the landed call is reused, never replayed.
func TestAgendaGatedStepSurvivesTheKill(t *testing.T) {
	plan := `{"steps": ["Collect the numbers", {"step": "Check the source", "after": [1]}, "Write the summary"]}`
	seams := []struct {
		at    string
		execs int
		judge int
	}{
		{"after_gated_step", 2, 5},     // step 1 unclear, step 2 gated (crash), step 3 runs on resume
		{"after_step_execute#2", 2, 5}, // step 3's execute landed before the crash: reused on resume
	}
	for _, s := range seams {
		t.Run(s.at, func(t *testing.T) {
			h := open(t)
			exec, judge := agendaBackends(
				[]string{"Collected: (empty)", "Summary written", "SHOULD NOT RUN"},
				[]string{intentClear, plan, judgeUnclear, judgeDone, closureUnsure, closureUnsure})
			d := h.agenda(exec, judge)
			d.CrashAt = s.at
			_, err := d.Run(ctxBg, []byte("three steps"), DeliveryPolicy{Required: TransportAccepted})
			if !errors.Is(err, ErrCrashed) {
				t.Fatalf("seam did not fire: %v", err)
			}
			h.restart()
			d = h.agenda(exec, judge)
			reps, err := d.Resume(ctxBg)
			if err != nil || len(reps) != 1 || reps[0].Mission.Outcome != MissionDelivered {
				t.Fatalf("resume: %v %+v", err, reps)
			}
			if len(exec.Seen) != s.execs || len(judge.Seen) != s.judge {
				t.Fatalf("exec=%d judge=%d, want %d/%d", len(exec.Seen), len(judge.Seen), s.execs, s.judge)
			}
			rs := h.only()
			a := rs.Latest()
			if len(a.Steps) != 3 || a.Steps[1].Outcome != StepGated || a.Steps[2].Outcome != StepDoneOK || !strings.Contains(string(reps[0].Payload), "Summary written") {
				t.Fatalf("steps %+v payload %q", a.Steps, reps[0].Payload)
			}
			head := h.j.Head()
			if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || h.j.Head() != head {
				t.Fatalf("second resume wrote: %v", err)
			}
		})
	}
}

// The plan boundary refuses malformed prerequisites once; the accepted
// shapes carry them (a parallel step may declare them too).
func TestPlanBoundaryRefusesBadPrerequisites(t *testing.T) {
	bad := []struct{ resp, want string }{
		{`{"steps": [{"step": "a", "after": [1]}]}`, "cannot follow step 1"},
		{`{"steps": ["a", {"step": "b", "after": [2]}]}`, "cannot follow step 2"},
		{`{"steps": ["a", {"step": "b", "after": [0]}]}`, "cannot follow step 0"},
		{`{"steps": ["a", "b", {"step": "c", "after": [2, 1]}]}`, "ascending and distinct"},
		{`{"steps": ["a", {"step": "b", "after": [1, 1]}]}`, "ascending and distinct"},
		{`{"steps": ["a", {"after": [1]}]}`, "neither a text nor a parallel step"},
		{`{"steps": ["a", {"step": "b", "parallel": []}]}`, "both a step text and a parallel spec"},
		{`{"steps": ["a", {"step": "", "parallel": ["x", "y"], "join": "all"}]}`, "both a step text and a parallel spec"},
		{`{"steps": ["a", {"parallel": [], "join": "all"}]}`, "two or more sub-goals"},
		{`{"steps": ["a", {"step": "b", "join": ""}]}`, "both a step text and a parallel spec"},
		{`{"steps": ["a", {"step": "  ", "after": [1]}]}`, "is empty"},
		{`{"steps": ["a", {"step": "b", "parallel": ["x", "y"], "join": "all"}]}`, "both a step text and a parallel spec"},
		{`{"steps": ["a", {"step": "b", "after": ["1"]}]}`, "neither a text nor a parallel step"},
		{`{"steps": ["a", {"step": "b", "after": [1], "join": "all"}]}`, "both a step text and a parallel spec"},
	}
	for _, c := range bad {
		if _, err := ParsePlan([]byte(c.resp)); !errors.Is(err, ErrBoundary) || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: %v (want %q)", c.resp, err, c.want)
		}
	}
	ps, err := ParsePlan([]byte(`{"steps": ["a", {"step": "b", "after": []}, {"step": "c", "after": [1, 2]}, {"parallel": ["x", "y"], "join": "all", "after": [3]}]}`))
	if err != nil || len(ps) != 4 || ps[1].After != nil || !equalInts(ps[2].After, []int{1, 2}) || !equalInts(ps[3].After, []int{3}) || len(ps[3].Parallel) != 2 || ps[1].Text != "b" {
		t.Fatalf("accepted shapes: %+v %v", ps, err)
	}
	// a JSON null is absent (encoding/json reads it so): a text step
	if ps, err := ParsePlan([]byte(`{"steps": [{"step": "b", "parallel": null}]}`)); err != nil || len(ps) != 1 || ps[0].Text != "b" || ps[0].Parallel != nil {
		t.Fatalf("null parallel: %+v %v", ps, err)
	}
}

// Forged gated records are refused at the door (a gated step cites
// nothing and names its prerequisites) and by the fold (the gate is
// re-derived: a gated record whose prerequisite ended done, and an
// executed record whose prerequisite did not).
func TestForgedGatedStepIsRefused(t *testing.T) {
	plan := `{"steps": ["Collect the numbers", {"step": "Write the summary", "after": [1]}]}`
	// door: run a plan whose step 1 ends done, crash before step 2
	h := open(t)
	exec, judge := agendaBackends([]string{"r1", "r2"}, []string{intentClear, plan, judgeDone, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	d.CrashAt = "after_step"
	if _, err := d.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	rs := h.only()
	a := rs.Latest()
	hd := func() record.Header {
		return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: runRef(rs.Run), At: now()}
	}
	step, res, inv := a.Plan.Steps[1], a.Steps[0].Result, a.Steps[0].Invocation
	gatedRes, err := h.st.Put(thought.Response, gatedText([]int{1}, map[int]StepOutcome{1: StepUnclear}))
	if err != nil {
		t.Fatal(err)
	}
	door := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"gated with invocation", &StepDone{Header: hd(), Ordinal: 2, Step: step, Invocation: inv, Terminal: invoke.TerminalComplete, Result: gatedRes, Outcome: StepGated, GatedBy: []int{1}}, "no invocation, fork or verdict"},
		{"gated without gated_by", &StepDone{Header: hd(), Ordinal: 2, Step: step, Terminal: invoke.TerminalComplete, Result: gatedRes, Outcome: StepGated}, "names the prerequisites"},
		{"gated by a later step", &StepDone{Header: hd(), Ordinal: 2, Step: step, Terminal: invoke.TerminalComplete, Result: gatedRes, Outcome: StepGated, GatedBy: []int{2}}, "gated_by"},
		{"executed with gated_by", &StepDone{Header: hd(), Ordinal: 2, Step: step, Invocation: inv, Terminal: invoke.TerminalComplete, Result: res, Outcome: StepUnjudged, GatedBy: []int{1}}, "only a gated step"},
	}
	for _, c := range door {
		c.rec.Head().Schema = "step_done/2"
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: c.name, Epoch: h.j.Epoch(), Records: []record.Record{c.rec}})
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: want refusal containing %q, got %v", c.name, c.want, err)
		}
	}
	// fold: a gated record for a step whose prerequisite ended done
	forgedGated := &StepDone{Header: hd(), Ordinal: 2, Step: step, Terminal: invoke.TerminalComplete, Result: gatedRes, Outcome: StepGated, GatedBy: []int{1}}
	if err := forge(t, h, "gated-over-done", forgedGated); err == nil || !strings.Contains(err.Error(), "does not re-derive") {
		t.Fatalf("gated record over a done prerequisite folded: %v", err)
	}
	// fold: an executed record for a step whose prerequisite ended unclear
	h2 := open(t)
	exec2, judge2 := agendaBackends([]string{"r1", "r2"}, []string{intentClear, plan, judgeUnclear, closureUnsure})
	d2 := h2.agenda(exec2, judge2)
	d2.CrashAt = "after_step"
	if _, err := d2.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	rs2 := h2.only()
	a2 := rs2.Latest()
	ran := &StepDone{Header: record.Header{ID: record.NewID(), RunID: rs2.Run, Attempt: 1, Subject: runRef(rs2.Run), At: now()}, Ordinal: 2, Step: a2.Plan.Steps[1], Invocation: a2.Steps[0].Invocation, Terminal: invoke.TerminalComplete, Result: a2.Steps[0].Result, Outcome: StepUnjudged}
	if err := forge(t, h2, "ran-although-gated", ran); err == nil || !strings.Contains(err.Error(), "ran although its declared prerequisites [1] did not end done") {
		t.Fatalf("executed record over an unclear prerequisite folded: %v", err)
	}
}

// Reuse is by REQUEST, never by counting calls: a second recovery finds
// the landed call wherever it was made — an attempt that reused a call
// and then crashed does not carry it in its own list, and a step committed
// on a reused call must not shift the count for the next one. Every step
// cites a landed call afterwards; two executes and five judge calls in
// total over three attempts.
func TestAgendaReuseSurvivesASecondKill(t *testing.T) {
	seams := []struct{ first, second string }{
		{"after_step_execute", "after_step_execute"},   // attempt 2 crashes right after REUSING step 1's execute: attempt 3 finds attempt 1's call
		{"after_step_execute", "after_step_execute#2"}, // attempt 2 commits step 1 on attempt 1's call, lands step 2's execute, crashes: attempt 3 reuses it
		{"after_step_judge", "after_step_judge#2"},     // the same for the judge call
		{"after_step_judge", "after_closure_invoke"},   // step 1 committed on attempt 1's judge; attempt 2's closure call is attempt 3's
		{"after_step_judge", "after_step_verdict"},     // attempt 2 wrote the verdict from attempt 1's call and crashed: attempt 3 finds THAT verdict, writes no twin
	}
	for _, s := range seams {
		t.Run(s.first+"/"+s.second, func(t *testing.T) {
			h := open(t)
			exec, judge := agendaBackends([]string{"r1", "r2", "SHOULD NOT RUN"}, []string{intentClear, planTwo, judgeDone, judgeDone, closureYes, closureYes, closureYes})
			d := h.agenda(exec, judge)
			d.CrashAt = s.first
			if _, err := d.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatalf("first seam did not fire: %v", err)
			}
			h.restart()
			d = h.agenda(exec, judge)
			d.CrashAt = s.second
			if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
				t.Fatalf("second seam did not fire: %v", err)
			}
			h.restart()
			d = h.agenda(exec, judge)
			reps, err := d.Resume(ctxBg)
			if err != nil || len(reps) != 1 || reps[0].Mission.Outcome != MissionDelivered || reps[0].Mission.Closure != "achieved" {
				t.Fatalf("resume: %v %+v", err, reps)
			}
			if len(exec.Seen) != 2 || len(judge.Seen) != 5 {
				t.Fatalf("exec=%d judge=%d (a landed call was replayed)", len(exec.Seen), len(judge.Seen))
			}
			rs := h.only()
			a := rs.Latest()
			if len(rs.Attempts) != 3 || len(a.Steps) != 2 {
				t.Fatalf("attempts=%d steps=%d", len(rs.Attempts), len(a.Steps))
			}
			for _, sd := range a.Steps {
				if st, _ := rs.invocation(sd.Invocation); st == nil || st.Receipt == nil || sd.Verdict == "" {
					t.Fatalf("step %d does not cite a landed call and its verdict: %+v", sd.Ordinal, sd)
				}
			}
			stepVerdicts := 0
			for _, p := range rs.Attempts {
				for _, v := range p.Verdicts {
					if v.VerdictKind == verdict.KindStep {
						stepVerdicts++
					}
				}
			}
			if stepVerdicts != 2 {
				t.Fatalf("%d step verdicts for 2 steps (a reused call was judged twice)", stepVerdicts)
			}
			head := h.j.Head()
			if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || h.j.Head() != head {
				t.Fatalf("fourth resume wrote: %v", err)
			}
		})
	}
}

// A crash after the execute that FOLLOWS a fork step: the fork made no
// execute call of its own (its children did), so the landed call is the
// next step's and is reused, not replayed. And a crash right after the
// fork's own judge call: the call is reused and the fork step's verdict
// cites it.
func TestAgendaForkThenExecuteSurvivesTheKill(t *testing.T) {
	for _, at := range []string{"after_step_execute#2", "after_fork_judge"} {
		t.Run(at, func(t *testing.T) {
			h := open(t)
			exec := &keyed{Caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, Rules: execRules(), Def: "?"}
			judge := &keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: judgeRules(JoinAll), Def: judgeDone}
			d := h.agenda(exec, judge)
			d.CrashAt = at
			if _, err := d.Run(ctxBg, []byte("two-level"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatalf("seam did not fire: %v", err)
			}
			// the fork judge's landed call, before the restart (the PARENT run:
			// its children are the newest runs)
			var landedJudge record.RecordID
			for _, st := range parentRun(t, h).Latest().Invocations {
				if st.Invocation.Purpose == invoke.PurposeJudge && st.Receipt != nil {
					landedJudge = st.Invocation.ID
				}
			}
			h.restart()
			d = h.agenda(exec, judge)
			reps, err := d.Resume(ctxBg)
			if err != nil || len(reps) != 1 || reps[0].Mission.Outcome != MissionDelivered {
				t.Fatalf("resume: %v %+v", err, reps)
			}
			wrap, forkJudged := 0, 0
			for _, r := range exec.Seen {
				if bytes.Contains(r.Prompt, []byte("## Your step (3 of")) {
					wrap++
				}
			}
			for _, r := range judge.Seen {
				if bytes.Contains(r.Prompt, []byte("### step\nIn parallel (all)")) {
					forkJudged++
				}
			}
			rs := parentRun(t, h)
			a := rs.Latest()
			if wrap != 1 || forkJudged != 1 || len(a.Steps) != 3 || a.Steps[1].Fork == "" || a.Steps[2].Outcome != StepDoneOK {
				t.Fatalf("step 3 executed %d times, fork judged %d times, steps: %+v", wrap, forkJudged, a.Steps)
			}
			if at == "after_fork_judge" {
				var cites record.RecordID
				for _, p := range rs.Attempts {
					for _, v := range p.Verdicts {
						if v.ID == a.Steps[1].Verdict {
							cites = v.Source.Ref
						}
					}
				}
				if landedJudge == "" || cites != landedJudge {
					t.Fatalf("the fork step's verdict cites %s, not the landed judge call %s", cites, landedJudge)
				}
			}
		})
	}
}

// A crash right after a FINAL gated step: the recovered attempt's
// representative call is the latest step's that made one (the last step
// made none), so the closure records with a receipt and delivers.
func TestAgendaFinalGatedStepSurvivesTheKill(t *testing.T) {
	plan := `{"steps": ["Collect the numbers", {"step": "Write the summary", "after": [1]}]}`
	h := open(t)
	exec, judge := agendaBackends([]string{"Collected: (empty)", "SHOULD NOT RUN"}, []string{intentClear, plan, judgeUnclear, closureUnsure, closureUnsure})
	d := h.agenda(exec, judge)
	d.CrashAt = "after_gated_step"
	if _, err := d.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("seam did not fire: %v", err)
	}
	h.restart()
	// the recovery runs on a DIFFERENT executor model: the outcome's model is
	// the landed call's (the fold binds them), not the resumer's
	resumer := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "resumer"}, Calls: []invoke.ScriptedCall{{Response: []byte("SHOULD NOT RUN")}}}
	d = h.agenda(resumer, judge)
	reps, err := d.Resume(ctxBg)
	if err != nil || len(reps) != 1 || reps[0].Mission.Outcome != MissionDelivered {
		t.Fatalf("resume: %v %+v", err, reps)
	}
	if len(exec.Seen) != 1 || len(resumer.Seen) != 0 || len(judge.Seen) != 4 {
		t.Fatalf("exec=%d resumer=%d judge=%d", len(exec.Seen), len(resumer.Seen), len(judge.Seen))
	}
	a := h.only().Latest()
	o := a.Has(Recorded).Outcome
	if len(a.Steps) != 2 || a.Steps[1].Outcome != StepGated || o.Invocation != a.Steps[0].Invocation || o.Receipt == "" || o.Model != "exec" || o.Produced != 1 || o.Steps != 2 || !strings.Contains(string(reps[0].Payload), "not executed: gated") {
		t.Fatalf("steps %+v outcome %+v", a.Steps, o)
	}
}

// A parallel step with a declared prerequisite that did not end done is
// gated, never forked: the driver writes no fork, and the fold refuses a
// forged one (the gate is re-derived at the fork too, not only at the
// step record).
func TestForkAtGatedStepIsRefused(t *testing.T) {
	plan := `{"steps": ["Collect the numbers", {"parallel": ["x: name a prime", "y: name a square"], "join": "all", "after": [1]}]}`
	// the driver: gated, no fork, no child
	h := open(t)
	exec, judge := agendaBackends([]string{"Collected: (empty)", "SHOULD NOT RUN"}, []string{intentClear, plan, judgeUnclear, closureUnsure})
	rep, err := h.agenda(exec, judge).Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if a := h.only().Latest(); len(exec.Seen) != 1 || len(judge.Seen) != 4 || len(a.Steps) != 2 || a.Steps[1].Outcome != StepGated || a.Steps[1].Fork != "" || h.count(KindFork) != 0 || rep.Mission.Outcome != MissionDelivered {
		t.Fatalf("exec=%d judge=%d forks=%d steps=%+v", len(exec.Seen), len(judge.Seen), h.count(KindFork), a.Steps)
	}
	// the fold: a forged fork at the gated step
	h2 := open(t)
	exec2, judge2 := agendaBackends([]string{"Collected: (empty)"}, []string{intentClear, plan, judgeUnclear})
	d2 := h2.agenda(exec2, judge2)
	d2.CrashAt = "after_step"
	if _, err := d2.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	rs := h2.only()
	id := record.NewID()
	f := &Fork{Header: record.Header{ID: id, RunID: rs.Run, Attempt: 1, Subject: record.Ref{Kind: "fork", ID: string(id)}, At: now()}, Step: 2, Policy: JoinAll}
	for i := 0; i < 2; i++ {
		f.Goals = append(f.Goals, record.NewID())
		f.Members = append(f.Members, record.AttemptRef{Run: record.RunID(record.NewID()), Attempt: 1})
	}
	if err := forge(t, h2, "fork-at-gated-step", f); err == nil || !strings.Contains(err.Error(), "gated by its declared prerequisites [1]") {
		t.Fatalf("a fork at a gated step folded: %v", err)
	}
}

// A plan record's edges must be the response's, exactly: a plan committed
// with other prerequisites (moved, dropped, or invented) is refused by the
// fold. The seam after the plan call leaves the call with a receipt and no
// record, which is where a forged plan can be committed.
func TestForgedPlanEdgesAreRefused(t *testing.T) {
	forged := []struct {
		name  string
		edges []StepEdge
		want  string
	}{
		{"moved", []StepEdge{{Ordinal: 3, After: []int{2}}}, "plan step 3: declared prerequisites are not the response's"},
		{"dropped", nil, "plan step 3: declared prerequisites are not the response's"},
		{"invented", []StepEdge{{Ordinal: 2, After: []int{1}}, {Ordinal: 3, After: []int{1}}}, "plan step 2: declared prerequisites are not the response's"},
	}
	for _, c := range forged {
		t.Run(c.name, func(t *testing.T) {
			// a forged record stays in the journal: each forgery gets its own history
			h := open(t)
			exec, judge := agendaBackends([]string{"r1"}, []string{intentClear, planGated})
			d := h.agenda(exec, judge)
			d.CrashAt = "after_plan_invoke"
			if _, err := d.Run(ctxBg, []byte("three steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatalf("seam did not fire: %v", err)
			}
			rs := h.only()
			a := rs.Latest()
			var inv record.RecordID
			for _, st := range a.Invocations {
				if st.Invocation.Purpose == invoke.PurposePlan && st.Receipt != nil {
					inv = st.Invocation.ID
				}
			}
			if inv == "" || a.Plan != nil {
				t.Fatalf("fixture: plan call %q plan %v", inv, a.Plan)
			}
			var steps []thought.Ref
			for _, text := range []string{"Collect the numbers", "Check the source", "Write the summary"} {
				ref, err := h.st.Put(thought.Step, []byte(text))
				if err != nil {
					t.Fatal(err)
				}
				steps = append(steps, ref)
			}
			rec := &Plan{Header: header(runRef(rs.Run), rs.Run, 1, "plan/2"), Invocation: inv, Steps: steps, Edges: c.edges}
			if err := forge(t, h, "forged-plan-"+c.name, rec); err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("a plan with forged edges folded: %v", err)
			}
		})
	}
}

// A rerun's planner is shown the prior plan WITH its declared
// prerequisites (a plan is its edges too); an edge-free prior plan renders
// as before.
func TestRerunContextKeepsDeclaredPrerequisites(t *testing.T) {
	h := open(t)
	goal := []byte("Summarize the quarterly numbers")
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Source checked", "Summary written"}, []string{intentClear, planGated, judgeDone, judgeDone, judgeDone, closureYes})
	if _, err := h.agenda(exec, judge).Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	exec2, judge2 := agendaBackends([]string{"Collected 12 rows", "Source checked", "Summary written"}, []string{landRerun, intentClear, planGated, judgeDone, judgeDone, judgeDone, closureYes})
	if _, err := h.agenda(exec2, judge2).Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rr := h.newestRun(t)
	want := "Its plan (reuse or revise):\n1. Collect the numbers\n2. Check the source\n3. Write the summary (after 1)\n"
	if rr.Landscape == nil || rr.Landscape.Relation != RelationRerun || !strings.Contains(string(rr.Related), want) {
		t.Fatalf("rerun context: %+v %q", rr.Landscape, rr.Related)
	}
	if !bytes.Contains(judge2.Seen[2].Prompt, []byte(want)) {
		t.Fatalf("plan request: %q", judge2.Seen[2].Prompt)
	}
}

// Judge prompts carry no ordinal: two steps with the same text and the
// same result render the same judge request. A step's judge call is the
// one made AFTER its execute — the earlier step's (unjudged) call is not
// reused for the later step, which gets its own judgement.
func TestAgendaJudgeReuseIsOrderedAfterItsExecute(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends([]string{"hi", "hi", "SHOULD NOT RUN"}, []string{intentClear, `{"steps": ["Say hi", "Say hi"]}`, "I cannot judge this", judgeDone, closureYes, closureYes})
	d := h.agenda(exec, judge)
	d.CrashAt = "after_step_execute#2"
	if _, err := d.Run(ctxBg, []byte("twice"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("seam did not fire: %v", err)
	}
	h.restart()
	d = h.agenda(exec, judge)
	reps, err := d.Resume(ctxBg)
	if err != nil || len(reps) != 1 || reps[0].Mission.Outcome != MissionDelivered {
		t.Fatalf("resume: %v %+v", err, reps)
	}
	a := h.only().Latest()
	if len(exec.Seen) != 2 || len(judge.Seen) != 5 || len(a.Steps) != 2 || a.Steps[0].Outcome != StepUnjudged || a.Steps[1].Outcome != StepDoneOK {
		t.Fatalf("exec=%d judge=%d steps=%+v (step 1's unjudged call stood in for step 2's)", len(exec.Seen), len(judge.Seen), a.Steps)
	}
}

// A recovery that does not continue the recovered attempt's recall
// selection (the recall policy changed between the crash and the
// resume) renders a different request, so the landed call is not found
// by request. It is still this step's call: the recovery fails closed,
// naming it, rather than run the step again.
func TestAgendaRecoveryDoesNotReplayUnderAChangedRecallPolicy(t *testing.T) {
	h := open(t)
	h.lesson(t, "Cite sources.", learn.Effective)
	off := h.policy(t, learn.MechRecall, false, learn.Candidate) // a candidate changes nothing yet
	exec, judge := agendaBackends([]string{"r1", "SHOULD NOT RUN"}, []string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	d.CrashAt = "after_step_execute"
	if _, err := d.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("seam did not fire: %v", err)
	}
	if !bytes.Contains(exec.Seen[0].Prompt, []byte("Cite sources.")) {
		t.Fatalf("the landed call was not rendered with the block: %q", exec.Seen[0].Prompt)
	}
	h.stage(t, off, learn.Candidate, learn.Provisional) // recall goes OFF between the crash and its recovery
	h.restart()
	d = h.agenda(exec, judge)
	reps, err := d.Resume(ctxBg)
	if err != nil || len(reps) != 1 {
		t.Fatalf("resume: %v %+v", err, reps)
	}
	rs := h.only()
	a := rs.Latest()
	if a.Attempt.Config.Mechanisms[learn.MechRecall] || a.Recall.Continues != "" {
		t.Fatalf("fixture: the recovery continued the selection or kept recall on: %+v", a.Recall)
	}
	o := a.Has(Recorded).Outcome
	if len(exec.Seen) != 1 || o == nil || o.Terminal != invoke.TerminalFailed || !strings.Contains(o.Reason, "does not continue") || o.Invocation != exec2ID(rs) {
		t.Fatalf("exec=%d outcome %+v", len(exec.Seen), o)
	}
}

// exec2ID is the run's single landed execute call.
func exec2ID(rs *RunState) record.RecordID {
	for _, p := range rs.Attempts {
		for _, st := range p.Invocations {
			if st.Invocation.Purpose == invoke.PurposeExecute && st.Receipt != nil {
				return st.Invocation.ID
			}
		}
	}
	return ""
}

// The NOW lane's in-flight call may be any earlier unrecorded attempt's:
// attempt 2 reuses attempt 1's execute and crashes; attempt 3 reuses it
// too (one dispatch in total over three attempts).
func TestNowReuseSurvivesASecondKill(t *testing.T) {
	for _, second := range []string{"after_execute", "after_judged"} {
		t.Run(second, func(t *testing.T) {
			h := open(t)
			h.lesson(t, "always cite the file", learn.Provisional)
			b := scripted(toolless, invoke.ScriptedCall{Response: []byte("answer")}, invoke.ScriptedCall{Response: []byte("SHOULD NOT RUN")})
			d := h.driver(b, nil)
			d.CrashAt = "after_execute"
			if _, err := d.Run(ctxBg, []byte("What is the answer?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatalf("first seam did not fire: %v", err)
			}
			h.restart()
			d = h.driver(b, nil)
			d.CrashAt = second
			if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
				t.Fatalf("second seam did not fire: %v", err)
			}
			h.restart()
			d = h.driver(b, nil)
			reps, err := d.Resume(ctxBg)
			if err != nil || len(reps) != 1 {
				t.Fatalf("resume: %v (%d reports)", err, len(reps))
			}
			rs := h.only()
			o := rs.Latest().Has(Recorded).Outcome
			if len(rs.Attempts) != 3 || len(b.Seen) != 1 || !rs.Terminal() || o == nil || o.Produced != 1 || o.Invocation != exec2ID(rs) {
				t.Fatalf("attempts=%d dispatches=%d outcome %+v", len(rs.Attempts), len(b.Seen), o)
			}
		})
	}
}

// parentRun is the one run of the harness that is not a fork's child.
func parentRun(t *testing.T, h *harness) *RunState {
	t.Helper()
	var parent *RunState
	for _, rs := range h.ledger().Runs {
		if rs.Goal.Origin != OriginFork && rs.Latest() != nil {
			if parent != nil {
				t.Fatal("more than one parent run")
			}
			parent = rs
		}
	}
	if parent == nil {
		t.Fatal("no parent run")
	}
	return parent
}
