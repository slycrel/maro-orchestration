package run

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// agenda is the AGENDA configuration's execute: intent → plan → one step
// at a time (execute, then judge) → closure judge. Every stage is a
// committed record with a keyed command; a restart continues after the
// last committed one (intent, plan, step k) and reuses any invocation the
// previous attempt left with a receipt. It returns the execution outcome
// and the closure judge's verdict (when one was made) as a candidate.
func (d *Driver) agenda(ctx context.Context, rs *RunState, a *AttemptState, prev *AttemptState, forced *Outcome) (*Outcome, []*verdict.Verdict, error) {
	n := a.Attempt.Attempt
	goal, err := d.Store.Get(rs.Goal.Text)
	if err != nil {
		return nil, nil, err
	}
	// what the recovered attempt already committed is this attempt's start
	var intent *IntentAssessment
	var plan *Plan
	var done []*StepDone
	var usage invoke.Usage
	if prev != nil {
		// the recovered attempt's committed stages are this attempt's start
		// (the fold inherits them the same way)
		intent, plan, done = prev.Intent, prev.Plan, prev.Steps
		a.Intent, a.Plan, a.Steps = prev.Intent, prev.Plan, append([]*StepDone{}, prev.Steps...)
	}
	for _, p := range rs.Attempts {
		if p == a {
			continue
		}
		for _, st := range p.Invocations {
			if st.Receipt != nil {
				usage = add(usage, st.Receipt.Usage)
			}
		}
	}
	if forced != nil {
		return forced, nil, nil
	}
	// Recall — one selection per attempt, continued from the recovered one
	var continues *learn.RecallSelection
	if prev != nil && prev.Recall != nil {
		continues = prev.Recall
	}
	sel, block, reps, err := d.recall(ctx, rs, n, continues)
	if err != nil {
		return nil, nil, err
	}
	if err := d.crash("after_recall"); err != nil {
		return nil, nil, err
	}
	// invoke runs one judge-or-execute call and applies the recall block's
	// revisions to it when the block was in the request
	invoke_ := func(purpose invoke.Purpose, prompt []byte, withBlock bool, tools bool) (*invoke.Outcome, []byte, error) {
		b := d.Backend
		if !tools {
			b = d.Judge
		}
		sh := &invoke.Shell{J: d.J, Store: d.Store, Run: rs.Run, Attempt: n, CrashAt: strings.TrimPrefix(d.CrashAt, "invoke:")}
		if !strings.HasPrefix(d.CrashAt, "invoke:") {
			sh.CrashAt = ""
		}
		o, err := sh.Invoke(ctx, b, invoke.Request{Purpose: purpose, Prompt: prompt, Tools: tools && b.Capabilities().ActsOutward, Timeout: d.Timeout}, nil)
		if err != nil {
			return nil, nil, err
		}
		if o.Err != nil {
			return nil, nil, o.Err
		}
		usage = add(usage, o.Usage)
		if withBlock {
			if err := d.applications(ctx, rs, n, o.Invocation, reps); err != nil {
				return nil, nil, err
			}
		}
		return o, o.Response, nil
	}
	failed := func(reason string, inv record.RecordID) *Outcome {
		o := &Outcome{Terminal: invoke.TerminalFailed, Reason: reason, Usage: usage, Recall: sel.ID, Steps: len(done)}
		if inv != "" {
			o.Invocation, o.Produced, o.Model = inv, n, d.Judge.Capabilities().Model
		}
		return o
	}
	// reuse looks for an invocation of the purpose the recovered attempt
	// left with a receipt and no committed stage record: the call happened,
	// only the record did not land
	reuse := func(purpose invoke.Purpose, ordinal int) (*invoke.Outcome, []byte, error) {
		if prev == nil {
			return nil, nil, nil
		}
		st := inflight(prev, purpose, ordinal)
		if st == nil || st.Receipt == nil {
			return nil, nil, nil
		}
		b, err := d.Store.Get(st.Receipt.Response)
		if err != nil {
			return nil, nil, err
		}
		usage = add(usage, st.Receipt.Usage)
		return &invoke.Outcome{Invocation: st.Invocation.ID, Receipt: st.Receipt.ID, Terminal: st.Terminal.State, Response: b}, b, nil
	}
	// Intent
	if intent == nil {
		o, resp, err := reuse(invoke.PurposeIntent, 0)
		if err != nil {
			return nil, nil, err
		}
		if o == nil {
			o, resp, err = invoke_(invoke.PurposeIntent, intentPrompt(goal), false, false)
			if err != nil {
				return nil, nil, err
			}
		}
		if o.Terminal == invoke.TerminalFailed {
			return failed("intent: "+o.Reason, o.Invocation), nil, nil
		}
		ir, perr := ParseIntent(resp)
		if perr != nil {
			return failed("intent: "+perr.Error(), o.Invocation), nil, nil
		}
		intent = &IntentAssessment{Header: header(runRef(rs.Run), rs.Run, n, "intent_assessment/1"), Invocation: o.Invocation, Clear: ir.Clear, Interpretation: ir.Interpretation, Question: ir.Question}
		if err := d.commit(ctx, fmt.Sprintf("intent/%s/%d", rs.Run, n), intent); err != nil {
			return nil, nil, err
		}
		a.Intent = intent
		d.emit(rs, n, "intent", Executing, fmt.Sprintf("clear=%v", intent.Clear))
		if err := d.crash("after_intent"); err != nil {
			return nil, nil, err
		}
	}
	if !intent.Clear {
		// an honest stop: the question IS the deliverable
		return failed("needs clarification: "+intent.Question, intent.Invocation), nil, nil
	}
	// Plan
	if plan == nil {
		o, resp, err := reuse(invoke.PurposePlan, 0)
		if err != nil {
			return nil, nil, err
		}
		if o != nil {
			if err := d.apply(ctx, rs, prev.Attempt.Attempt, prev.Recall, o.Invocation); err != nil {
				return nil, nil, err
			}
		} else {
			o, resp, err = invoke_(invoke.PurposePlan, planPrompt(goal, intent.Interpretation, block), len(block) > 0, false)
			if err != nil {
				return nil, nil, err
			}
		}
		if o.Terminal == invoke.TerminalFailed {
			return failed("plan: "+o.Reason, o.Invocation), nil, nil
		}
		steps, perr := ParsePlan(resp)
		if perr != nil {
			return failed("plan: "+perr.Error(), o.Invocation), nil, nil
		}
		plan = &Plan{Header: header(runRef(rs.Run), rs.Run, n, "plan/1"), Invocation: o.Invocation}
		for _, st := range steps {
			ref, err := d.Store.Put(thought.Step, []byte(st))
			if err != nil {
				return nil, nil, err
			}
			plan.Steps = append(plan.Steps, ref)
		}
		if err := d.commit(ctx, fmt.Sprintf("plan/%s/%d", rs.Run, n), plan); err != nil {
			return nil, nil, err
		}
		a.Plan = plan
		d.emit(rs, n, "plan", Executing, fmt.Sprintf("%d steps", len(plan.Steps)))
		if err := d.crash("after_plan"); err != nil {
			return nil, nil, err
		}
	}
	steps := make([]string, len(plan.Steps))
	for i, ref := range plan.Steps {
		b, err := d.Store.Get(ref)
		if err != nil {
			return nil, nil, err
		}
		steps[i] = string(b)
	}
	results := make([][]byte, 0, len(steps))
	for _, sd := range done {
		b, err := d.Store.Get(sd.Result)
		if err != nil {
			return nil, nil, err
		}
		results = append(results, b)
	}
	// Steps — continue after the last committed one; stop at blocked
	var lastExec record.RecordID
	var lastReceipt record.RecordID
	var lastResp *thought.Ref
	var lastBy uint32 // the attempt that made lastExec
	if len(done) > 0 {
		last := done[len(done)-1]
		lastExec, lastResp = last.Invocation, &last.Result
		if st, by := rs.invocation(last.Invocation); st != nil && st.Receipt != nil {
			lastReceipt, lastBy = st.Receipt.ID, by
		}
		if last.Outcome == StepBlocked {
			return &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("blocked at step %d: %s", last.Ordinal, steps[last.Ordinal-1]), Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: d.Backend.Capabilities().Model, Recall: sel.ID, Steps: len(done)}, nil, nil
		}
	}
	resumeAt := len(done) + 1 // only the first new step can have an in-flight invocation to reuse
	for k := resumeAt; k <= len(steps); k++ {
		var o *invoke.Outcome
		var resp []byte
		by := n
		if k == resumeAt {
			ro, rb, err := reuse(invoke.PurposeExecute, len(prevSteps(prev)))
			if err != nil {
				return nil, nil, err
			}
			if ro != nil {
				if err := d.apply(ctx, rs, prev.Attempt.Attempt, prev.Recall, ro.Invocation); err != nil {
					return nil, nil, err
				}
				o, resp, by = ro, rb, prev.Attempt.Attempt
			}
		}
		if o == nil {
			o, resp, err = invoke_(invoke.PurposeExecute, stepPrompt(goal, steps, k, results, block), len(block) > 0, true)
			if err != nil {
				return nil, nil, err
			}
		}
		if o.Terminal == invoke.TerminalFailed {
			return failed(fmt.Sprintf("step %d: %s", k, o.Reason), o.Invocation), nil, nil
		}
		if err := d.crash("after_step_execute"); err != nil {
			return nil, nil, err
		}
		// per-step judge (tool-less); a refused output = unjudged, continue
		sd := &StepDone{Header: header(runRef(rs.Run), rs.Run, n, "step_done/1"), Ordinal: k, Step: plan.Steps[k-1], Invocation: o.Invocation, Outcome: StepUnjudged}
		rr, err := receiptResponse(d.J, o.Receipt)
		if err != nil {
			return nil, nil, err
		}
		sd.Result = *rr
		jo, jresp, err := invoke_(invoke.PurposeJudge, stepJudgePrompt(goal, steps[k-1], resp), false, false)
		if err != nil {
			return nil, nil, err
		}
		if jo.Terminal != invoke.TerminalFailed {
			if jr, perr := ParseJudge(jresp, "done", "blocked", "unclear"); perr == nil {
				v := &verdict.Verdict{Header: header(record.Ref{Kind: "step", ID: fmt.Sprintf("%s/%d/%d", rs.Run, n, k)}, rs.Run, n, "verdict/1"), VerdictKind: verdict.KindStep, Outcome: jr.Outcome, Confidence: jr.Confidence, Source: verdict.Source{Standing: verdict.StandingJudge, Ref: jo.Invocation}, Direction: verdict.Both, Basis: []record.Ref{{Kind: invoke.KindReceipt, ID: string(jo.Receipt)}}}
				if err := d.commit(ctx, fmt.Sprintf("verdict/%s/%d/step/%d", rs.Run, n, k), v); err != nil {
					return nil, nil, err
				}
				sd.Verdict, sd.Outcome = v.ID, StepOutcome(jr.Outcome)
			} else {
				d.emit(rs, n, "step_unjudged", Executing, perr.Error())
			}
		}
		if err := d.commit(ctx, fmt.Sprintf("step/%s/%d/%d", rs.Run, n, k), sd); err != nil {
			return nil, nil, err
		}
		a.Steps = append(a.Steps, sd)
		done = append(done, sd)
		results = append(results, resp)
		lastExec, lastReceipt, lastResp, lastBy = o.Invocation, o.Receipt, rr, by
		d.emit(rs, n, "step", Executing, fmt.Sprintf("%d/%d %s", k, len(steps), sd.Outcome))
		if err := d.crash("after_step"); err != nil {
			return nil, nil, err
		}
		if sd.Outcome == StepBlocked {
			return &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("blocked at step %d: %s", k, steps[k-1]), Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: d.Backend.Capabilities().Model, Recall: sel.ID, Steps: len(done)}, nil, nil
		}
	}
	// Closure judge (tool-less); a refused output = no judge verdict
	out := &Outcome{Terminal: invoke.TerminalComplete, Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: d.Backend.Capabilities().Model, Recall: sel.ID, Steps: len(done)}
	jo, jresp, err := invoke_(invoke.PurposeJudge, closurePrompt(goal, steps, results), false, false)
	if err != nil {
		return nil, nil, err
	}
	out.Usage = usage
	var candidates []*verdict.Verdict
	if jo.Terminal != invoke.TerminalFailed {
		if jr, perr := ParseJudge(jresp, "achieved", "not_achieved", "unknown"); perr == nil {
			v := &verdict.Verdict{Header: header(runRef(rs.Run), rs.Run, n, "verdict/1"), VerdictKind: verdict.KindClosure, Outcome: jr.Outcome, Confidence: jr.Confidence, Source: verdict.Source{Standing: verdict.StandingJudge, Ref: jo.Invocation}, Direction: verdict.Both, Basis: []record.Ref{{Kind: invoke.KindReceipt, ID: string(jo.Receipt)}}}
			for _, f := range jr.Falsifiers {
				if strings.TrimSpace(f) == "" {
					continue
				}
				ref, err := d.Store.Put(thought.Response, []byte(f))
				if err != nil {
					return nil, nil, err
				}
				v.Falsifiers = append(v.Falsifiers, ref)
			}
			if err := d.commit(ctx, fmt.Sprintf("verdict/%s/%d/closure", rs.Run, n), v); err != nil {
				return nil, nil, err
			}
			candidates = append(candidates, v)
		} else {
			d.emit(rs, n, "closure_unjudged", Executing, perr.Error())
		}
	}
	return out, candidates, nil
}

// prevSteps counts the steps the recovered attempt itself executed (its
// inherited ones came from the attempt before it).
func prevSteps(prev *AttemptState) []*StepDone {
	if prev == nil {
		return nil
	}
	own := 0
	for _, sd := range prev.Steps {
		if sd.Attempt == prev.Attempt.Attempt {
			own++
		}
	}
	return prev.Steps[len(prev.Steps)-own:]
}

// inflight finds the recovered attempt's invocation of a purpose by
// ordinal: the (ordinal+1)-th such invocation it made.
func inflight(prev *AttemptState, purpose invoke.Purpose, done int) *invoke.State {
	k := 0
	for _, st := range prev.Invocations {
		if st.Invocation.Purpose != purpose {
			continue
		}
		if k == done {
			return st
		}
		k++
	}
	return nil
}

func add(a, b invoke.Usage) invoke.Usage {
	a.InputTokens += b.InputTokens
	a.OutputTokens += b.OutputTokens
	a.CacheRead += b.CacheRead
	a.CostUSD += b.CostUSD
	a.CostReported = a.CostReported || b.CostReported
	a.WallMillis += b.WallMillis
	return a
}

// renderAgendaOutcome composes the AGENDA deliverable: the question when
// the goal was unclear; otherwise every step's whole result in order, the
// unexecuted ones named, and the failure line when the execution failed.
func (d *Driver) renderAgendaOutcome(o *Outcome, a *AttemptState) ([]byte, error) {
	if a.Intent != nil && !a.Intent.Clear {
		return []byte(a.Intent.Question + "\n"), nil
	}
	if a.Plan == nil {
		return Render(o, nil), nil
	}
	steps := make([]string, len(a.Plan.Steps))
	for i, ref := range a.Plan.Steps {
		b, err := d.Store.Get(ref)
		if err != nil {
			return nil, err
		}
		steps[i] = string(b)
	}
	var results [][]byte
	for _, sd := range a.Steps {
		b, err := d.Store.Get(sd.Result)
		if err != nil {
			return nil, err
		}
		results = append(results, b)
	}
	out := renderAgenda(steps, results)
	if o.Terminal == invoke.TerminalFailed {
		out = append(out, []byte("\nmaro: the run did not complete.\nreason: "+o.Reason+"\n")...)
	}
	return out, nil
}

var errAgenda = errors.New("run: agenda")
