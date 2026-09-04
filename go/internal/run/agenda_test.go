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

// agendaBackend scripts the executor (tool-bearing) and the judge
// (tool-less) as two backends, since the driver routes by purpose.
func agendaBackends(execs []string, judge []string) (*invoke.Scripted, *invoke.Scripted) {
	var ec, jc []invoke.ScriptedCall
	for _, e := range execs {
		ec = append(ec, invoke.ScriptedCall{Response: []byte(e)})
	}
	for _, j := range judge {
		jc = append(jc, invoke.ScriptedCall{Response: []byte(j)})
	}
	return &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: ec},
		&invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: jc}
}

func (h *harness) agenda(exec, judge invoke.Backend) *Driver {
	d := h.driver(exec, nil)
	d.Judge, d.Lane = judge, LaneAgenda
	return d
}

const (
	intentClear   = `{"clear": true, "interpretation": "Collect the numbers, then summarize them.", "question": ""}`
	planTwo       = `{"steps": ["Collect the numbers", "Write the summary"]}`
	judgeDone     = `{"outcome": "done", "confidence": 0.9, "why": "the step's result matches the step"}`
	judgeBlocked  = `{"outcome": "blocked", "confidence": 0.95, "why": "the resource does not exist"}`
	closureYes    = "```json\n" + `{"outcome": "achieved", "confidence": 0.8, "why": "both steps produced what the goal asked", "falsifiers": ["the summary omits the revenue line"]}` + "\n```"
	closureUnsure = `{"outcome": "unknown", "confidence": 0.5, "why": "cannot tell", "falsifiers": []}`
)

// The behavior suite's agenda-happy-path, driven through this engine:
// intent → plan (2 steps) → each step executed and judged → closure judge
// → recorded → delivered. The deliverable carries every step's whole
// result; the closure verdict is a JUDGE's, so the B6 row is judged
// achieved with source `closure`; usage is the sum over every invocation.
func TestAgendaHappyPath(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"Collected 12 rows of numbers", "Summary written: revenue flat"},
		[]string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	rep, err := d.Run(ctxBg, []byte("Summarize the quarterly numbers into a short report"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionDelivered || rep.Mission.Closure != "achieved" || !strings.Contains(string(rep.Payload), "## Step 1: Collect the numbers\nCollected 12 rows of numbers") || !strings.Contains(string(rep.Payload), "Summary written: revenue flat") {
		t.Fatalf("%+v\n%s", rep.Mission, rep.Payload)
	}
	rs := h.only()
	a := rs.Latest()
	if a.Intent == nil || !a.Intent.Clear || a.Plan == nil || len(a.Plan.Steps) != 2 || len(a.Steps) != 2 || a.Steps[1].Outcome != StepDoneOK || a.Steps[0].Verdict == "" {
		t.Fatalf("stages: intent=%+v plan=%+v steps=%d", a.Intent, a.Plan, len(a.Steps))
	}
	if len(a.Invocations) != 7 || len(exec.Seen) != 2 || len(judge.Seen) != 5 || exec.Seen[0].Tools || judge.Seen[0].Tools {
		t.Fatalf("invocations: %d exec=%d judge=%d", len(a.Invocations), len(exec.Seen), len(judge.Seen))
	}
	o := a.Has(Recorded).Outcome
	if o.Steps != 2 || o.ClosureOut != "achieved" || o.ClosureSrc != "judge" || rs.Closure.Rule != "standing:judge" || o.Model != "exec" {
		t.Fatalf("outcome: %+v closure %+v", o, rs.Closure)
	}
	if got := trail(a); got != "created executing judged recorded delivered:transport_accepted" {
		t.Fatalf("states: %s", got)
	}
	row := h.outcomesRow(t, d)
	if row["goal_achieved"] != true || row["goal_verdict_source"] != "closure" || row["task_type"] != "agenda" {
		t.Fatalf("B6 row: %v", row)
	}
	// the closure verdict names its falsifier as a whole thought
	var closure *verdict.Verdict
	for _, v := range h.verdicts(t, rs.Run) {
		if v.VerdictKind == verdict.KindClosure && v.Source.Standing == verdict.StandingJudge {
			closure = v
		}
	}
	if closure == nil || len(closure.Falsifiers) != 1 {
		t.Fatalf("closure verdict: %+v", closure)
	}
	if f, _ := h.st.Get(closure.Falsifiers[0]); string(f) != "the summary omits the revenue line" {
		t.Fatalf("falsifier: %q", f)
	}
	// step prompts carry the goal, the plan, and prior results whole
	req := h.requestOf(t, a) // the first execute request
	if !bytes.Contains(req, []byte("## Plan\n1. Collect the numbers\n2. Write the summary")) || !bytes.Contains(req, []byte("## Your step (1 of 2)")) {
		t.Fatalf("step prompt: %q", req)
	}
}

// A blocked step stops the run honestly: no later step runs, the execution
// is `failed` with the step named, the mission is failed(execution), the
// self demotion makes the row a judged not_achieved, and the deliverable
// says which steps did not run.
func TestAgendaBlockedStepIsHonest(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"Tried the fetch: 404", "SHOULD NOT RUN"},
		[]string{intentClear, planTwo, judgeBlocked, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	rep, err := d.Run(ctxBg, []byte("Access the resource that does not exist"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionFailedExec || !strings.Contains(rep.Mission.Reason, "blocked at step 1") || len(exec.Seen) != 1 || !strings.Contains(string(rep.Payload), "## Step 2: Write the summary\n(not executed)") {
		t.Fatalf("%+v exec=%d\n%s", rep.Mission, len(exec.Seen), rep.Payload)
	}
	rs := h.only()
	if rs.Closure.Outcome != "not_achieved" || rs.Closure.Rule != "standing:self" {
		t.Fatalf("closure: %+v", rs.Closure)
	}
	row := h.outcomesRow(t, d)
	if row["status"] != "stuck" || row["goal_achieved"] != false || row["goal_verdict_source"] != "now_self_verdict" {
		t.Fatalf("B6 row: %v", row)
	}
}

// An unclear goal is not planned: the intent's question is the
// deliverable, honestly, as a failed execution.
func TestAgendaUnclearGoalDeliversTheQuestion(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(nil, []string{`{"clear": false, "interpretation": "", "question": "Which quarter, and for which product line?"}`})
	d := h.agenda(exec, judge)
	rep, err := d.Run(ctxBg, []byte("Summarize the numbers"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionFailedExec || !strings.Contains(rep.Mission.Reason, "needs clarification: Which quarter") || len(exec.Seen) != 0 || !strings.Contains(string(rep.Payload), "Which quarter, and for which product line?") {
		t.Fatalf("%+v\n%s", rep.Mission, rep.Payload)
	}
	if a := h.only().Latest(); a.Intent == nil || a.Intent.Clear || a.Plan != nil {
		t.Fatalf("stages: %+v %+v", a.Intent, a.Plan)
	}
}

// Model outputs are validated ONCE at their boundary: a plan that is not
// the declared shape is a recorded failure naming the boundary; a judge
// output that is not the declared shape is `unjudged` and the run
// continues; the closure judge's refusal leaves the self claim alone
// (resolves unknown).
func TestAgendaBoundariesRefuseMalformedOutputs(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(nil, []string{intentClear, `{"steps": []}`})
	d := h.agenda(exec, judge)
	rep, err := d.Run(ctxBg, []byte("do things"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil || rep.Mission.Outcome != MissionFailedExec || !strings.Contains(rep.Mission.Reason, "plan: ") || !strings.Contains(rep.Mission.Reason, "at least one step") {
		t.Fatalf("%v %+v", err, rep.Mission)
	}
	h2 := open(t)
	exec2, judge2 := agendaBackends([]string{"r1"}, []string{intentClear, `{"steps": ["one"]}`, `not json at all`, `{"outcome": "achieved", "confidence": 7, "why": "x"}`})
	d2 := h2.agenda(exec2, judge2)
	rep2, err := d2.Run(ctxBg, []byte("do one thing"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil || rep2.Mission.Outcome != MissionDelivered || rep2.Mission.Closure != "unknown" {
		t.Fatalf("%v %+v", err, rep2.Mission)
	}
	a := h2.only().Latest()
	if a.Steps[0].Outcome != StepUnjudged || a.Steps[0].Verdict != "" || h2.only().Closure.Rule != "self_cannot_promote" {
		t.Fatalf("step %+v closure %+v", a.Steps[0], h2.only().Closure)
	}
	for _, c := range []struct {
		in   string
		want string
	}{
		{`{"clear": true, "interpretation": "", "question": ""}`, "clear without"},
		{`{"clear": false, "interpretation": "", "question": ""}`, "unclear without"},
		{`{"clear": true, "interpretation": "x", "question": "", "extra": 1}`, "unknown field"},
		{`{"clear": true, "interpretation": "x", "question": ""} trailing`, "trailing"},
	} {
		if _, err := ParseIntent([]byte(c.in)); err == nil || !errors.Is(err, ErrBoundary) || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: %v", c.in, err)
		}
	}
	if _, err := ParseJudge([]byte(`{"outcome": "done", "confidence": 0.5, "why": " "}`), "done"); err == nil {
		t.Fatal("empty why accepted")
	}
}

// Kill matrix across the AGENDA stages: a crash after every committed
// stage resumes to one delivered run with every step executed exactly once
// (an in-flight step invocation with a receipt is reused), the plan and
// intent never remade, and the sum usage honest.
func TestAgendaKillMatrix(t *testing.T) {
	seams := []struct {
		at    string
		execs int // executor calls in total
		judge int // judge calls in total
	}{
		{"after_intent", 2, 5},
		{"after_plan", 2, 5},
		{"after_step_execute", 2, 5}, // step 1 executed, receipt landed, not judged: reused
		{"after_step", 2, 5},         // step 1 done; step 2 runs on resume
		{"invoke:terminal", 2, 5},    // intent's terminal landed without its receipt: reconciled, the receipt reused, the intent not re-asked
	}
	for _, s := range seams {
		t.Run(s.at, func(t *testing.T) {
			h := open(t)
			exec, judge := agendaBackends(
				[]string{"r1", "r2", "r3"},
				[]string{intentClear, planTwo, judgeDone, judgeDone, closureYes, closureYes, closureYes})
			d := h.agenda(exec, judge)
			d.CrashAt = s.at
			_, err := d.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted})
			if !errors.Is(err, ErrCrashed) && !errors.Is(err, invoke.ErrCrashed) {
				t.Fatalf("seam did not fire: %v", err)
			}
			h.restart()
			d = h.agenda(exec, judge)
			reps, err := d.Resume(ctxBg)
			if err != nil || len(reps) != 1 || reps[0].Mission.Outcome != MissionDelivered {
				t.Fatalf("resume: %v %+v", err, reps)
			}
			rs := h.only()
			if len(exec.Seen) != s.execs {
				t.Fatalf("executor calls %d, want %d", len(exec.Seen), s.execs)
			}
			if len(judge.Seen) != s.judge {
				t.Fatalf("judge calls %d, want %d", len(judge.Seen), s.judge)
			}
			a := rs.Latest()
			if len(a.Steps) != 2 && (len(rs.Attempts) < 2 || len(rs.Attempts[0].Steps)+len(a.Steps) < 2) {
				t.Fatalf("steps: %d", len(a.Steps))
			}
			o := a.Has(Recorded).Outcome
			if o.Steps != len(a.Steps) || o.Terminal != invoke.TerminalComplete || !strings.Contains(string(reps[0].Payload), "r2") {
				t.Fatalf("outcome %+v payload %q", o, reps[0].Payload)
			}
			head := h.j.Head()
			if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || h.j.Head() != head {
				t.Fatalf("second resume wrote: %v", err)
			}
		})
	}
}

// The journal executes the AGENDA vocabulary, and the fold executes the
// stage order: an intent citing a non-intent invocation, a plan with no
// steps or before a clear intent, a step out of order or citing a foreign
// invocation, a step after a blocked step.
func TestJournalExecutesAgendaVocabulary(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends([]string{"r1"}, []string{intentClear, planTwo, judgeBlocked})
	d := h.agenda(exec, judge)
	if _, err := d.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs := h.only()
	a := rs.Latest()
	hd := func() record.Header {
		return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: runRef(rs.Run), At: now()}
	}
	step := a.Plan.Steps[1]
	res := a.Steps[0].Result
	door := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"plan no steps", &Plan{Header: hd(), Invocation: a.Plan.Invocation}, "at least one step"},
		{"plan step not a step thought", &Plan{Header: hd(), Invocation: a.Plan.Invocation, Steps: []thought.Ref{res}}, "step thought"},
		{"intent clear without interpretation", &IntentAssessment{Header: hd(), Invocation: a.Intent.Invocation, Clear: true}, "interpretation"},
		{"step ordinal zero", &StepDone{Header: hd(), Ordinal: 0, Step: step, Invocation: a.Steps[0].Invocation, Result: res, Outcome: StepUnjudged}, "ordinal"},
		{"step foreign outcome", &StepDone{Header: hd(), Ordinal: 2, Step: step, Invocation: a.Steps[0].Invocation, Result: res, Outcome: "maybe"}, "out of vocabulary"},
		{"step unjudged with verdict", &StepDone{Header: hd(), Ordinal: 2, Step: step, Invocation: a.Steps[0].Invocation, Result: res, Verdict: record.NewID(), Outcome: StepUnjudged}, "unjudged has no verdict"},
	}
	for _, c := range door {
		spec, _ := record.Lookup(c.rec.Kind())
		c.rec.Head().Schema = record.SchemaVer(string(c.rec.Kind()) + "/1")
		_ = spec
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: c.name, Epoch: h.j.Epoch(), Records: []record.Record{c.rec}})
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: want refusal containing %q, got %v", c.name, c.want, err)
		}
	}
	// fold rules: a plan under attempt 1 twice; a step after the blocked one
	fold_ := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"second plan", &Plan{Header: hd(), Invocation: a.Plan.Invocation, Steps: a.Plan.Steps}, "second plan"},
		{"step after blocked", &StepDone{Header: hd(), Ordinal: 2, Step: step, Invocation: a.Steps[0].Invocation, Result: res, Outcome: StepUnjudged}, "after a blocked step"},
	}
	for _, c := range fold_ {
		h2 := open(t)
		exec2, judge2 := agendaBackends([]string{"r1"}, []string{intentClear, planTwo, judgeBlocked})
		if _, err := h2.agenda(exec2, judge2).Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		rs2 := h2.only()
		a2 := rs2.Latest()
		r := c.rec
		r.Head().RunID = rs2.Run
		r.Head().Subject = runRef(rs2.Run)
		switch x := r.(type) {
		case *Plan:
			x.Invocation, x.Steps = a2.Plan.Invocation, a2.Plan.Steps
		case *StepDone:
			x.Step, x.Invocation, x.Result = a2.Plan.Steps[1], a2.Steps[0].Invocation, a2.Steps[0].Result
		}
		if err := forge(t, h2, c.name, r); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: folded: %v (want %q)", c.name, err, c.want)
		}
	}
	// a plan citing a call that was not a plan call: an attempt that has an
	// intent and no plan yet, and a plan record naming the intent invocation
	h3 := open(t)
	exec3, judge3 := agendaBackends([]string{"r1"}, []string{intentClear, planTwo, judgeBlocked})
	d3 := h3.agenda(exec3, judge3)
	d3.CrashAt = "after_intent"
	if _, err := d3.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	rs3 := h3.only()
	a3 := rs3.Latest()
	bad := &Plan{Header: record.Header{ID: record.NewID(), RunID: rs3.Run, Attempt: 1, Subject: runRef(rs3.Run), At: now()}, Invocation: a3.Intent.Invocation, Steps: []thought.Ref{step}}
	if err := forge(t, h3, "plan-cites-intent", bad); err == nil || !strings.Contains(err.Error(), "not its plan call") {
		t.Fatalf("plan citing the intent call folded: %v", err)
	}
	_ = journal.ErrClosed
	_ = learn.ScopeWorkspace
}
