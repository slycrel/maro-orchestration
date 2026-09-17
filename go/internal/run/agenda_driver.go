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
			if st.Receipt != nil && st.Invocation.Purpose != invoke.PurposeDiagnose && st.Invocation.Purpose != invoke.PurposeEvaluate { // the tail's and the evaluator's calls are theirs, not the goal's
				usage = add(usage, st.Receipt.Usage)
			}
		}
	}
	if forced != nil {
		return forced, nil, nil
	}
	// Recall — one selection per attempt, continued from the recovered one
	var continues *learn.RecallSelection
	if prev != nil && prev.Recall != nil && d.sameRecallPolicy(rs, prev, n) {
		continues = prev.Recall
	}
	sel, block, reps, err := d.recall(ctx, rs, n, continues)
	if err != nil {
		return nil, nil, err
	}
	if err := d.crash("after_recall"); err != nil {
		return nil, nil, err
	}
	// provenance of every invocation this attempt makes, for failed()
	made := map[record.RecordID]string{}
	// invoke runs one judge-or-execute call and applies the recall block's
	// revisions to it when the block was in the request
	invoke_ := func(purpose invoke.Purpose, prompt []byte, withBlock bool, tools bool) (*invoke.Outcome, []byte, error) {
		b := d.Backend
		if !tools {
			b = d.judge(a)
		}
		sh := &invoke.Shell{J: d.J, Store: d.Store, Run: rs.Run, Attempt: n, CrashAt: strings.TrimPrefix(d.CrashAt, "invoke:")}
		if !strings.HasPrefix(d.CrashAt, "invoke:") {
			sh.CrashAt = ""
		}
		req := invoke.Request{Purpose: purpose, Prompt: prompt, Tools: tools && b.Capabilities().ActsOutward, Timeout: d.Timeout}
		cwd, err := d.work(req.Tools)
		if err != nil {
			return nil, nil, err
		}
		req.Cwd = cwd
		if purpose == invoke.PurposeJudge {
			// a judge request is rendered under the attempt's lens (§13)
			lr, err := d.lensedRequest(prompt, req.Tools)
			if err != nil {
				return nil, nil, err
			}
			req = lr
		}
		o, err := sh.Invoke(ctx, b, req, nil)
		var inc *invoke.Incapable
		if errors.As(err, &inc) {
			// a deterministic pre-dispatch refusal (the composed prompt is
			// over the backend's maximum): an honest failed call, recorded
			return &invoke.Outcome{Terminal: invoke.TerminalFailed, Reason: "backend_incapable: " + err.Error()}, nil, nil
		}
		if err != nil && !recordedFailure(o, err) {
			return nil, nil, err
		}
		made[o.Invocation] = b.Capabilities().Model
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
			// provenance is the invocation's: this attempt's own (made) or a
			// recovered attempt's (the fold knows which)
			if model, ok := made[inv]; ok {
				o.Invocation, o.Produced, o.Model = inv, n, model
			} else if st, by := rs.invocation(inv); st != nil {
				o.Invocation, o.Produced, o.Model = inv, by, st.Invocation.Backend.Model
			}
		}
		return o
	}
	// reuse looks for an invocation of this purpose that an earlier attempt
	// of the run asked EXACTLY this request and left with a receipt and no
	// committed stage record: the call happened, only the record did not
	// land. It matches the request, never a count of calls: an attempt that
	// reused a call and then crashed does not carry it in its own list (a
	// second recovery must still find it), and a judge whose response did
	// not parse is still that step's judge call. Only attempts that never
	// recorded an outcome are candidates — a recorded failure's calls are
	// its own. Its usage is already in the sum over earlier attempts;
	// nothing is added here. Returns the attempt that made the call.
	//
	// A step's judge call is the one made AFTER that step's execute call:
	// judge prompts carry no ordinal, so two steps with the same text and
	// the same result render the same judge request, and the earlier
	// step's (unjudged) call must not stand in for the later step's. The
	// filter (from, after) admits the calls of attempt `from` after
	// invocation `after`, and every call of a later attempt (which
	// inherited the execute and could only be judging this step).
	reuse := func(purpose invoke.Purpose, prompt []byte, from uint32, after record.RecordID) (*invoke.Outcome, []byte, uint32, error) {
		if prev == nil {
			return nil, nil, 0, nil
		}
		want := prompt
		if purpose == invoke.PurposeJudge {
			lr, err := d.lensedRequest(prompt, false)
			if err != nil {
				return nil, nil, 0, err
			}
			want = lr.Prompt
		}
		ref := thought.Address(thought.Prompt, want)
		for _, p := range rs.Attempts {
			if p == a || p.Has(Recorded) != nil || (from != 0 && p.Attempt.Attempt < from) {
				continue
			}
			admitted := from == 0 || p.Attempt.Attempt > from
			for _, st := range p.Invocations {
				if !admitted {
					admitted = st.Invocation.ID == after
					continue
				}
				if st.Invocation.Purpose != purpose || st.Invocation.Request != ref || st.Receipt == nil {
					continue
				}
				b, err := d.Store.Get(st.Receipt.Response)
				if err != nil {
					return nil, nil, 0, err
				}
				return &invoke.Outcome{Invocation: st.Invocation.ID, Receipt: st.Receipt.ID, Terminal: st.Terminal.State, Response: b}, b, p.Attempt.Attempt, nil
			}
		}
		return nil, nil, 0, nil
	}
	// attemptN is an earlier attempt's state (the recall a reused call was
	// rendered with)
	attemptN := func(by uint32) *AttemptState {
		for _, p := range rs.Attempts {
			if p.Attempt.Attempt == by {
				return p
			}
		}
		return nil
	}
	// verdictOf is the judge-standing verdict some earlier attempt committed
	// FROM a reused judge call — whichever attempt wrote it (an attempt that
	// reused the call, committed the verdict under its own subject, and
	// then crashed is not the attempt that made the call)
	verdictOf := func(inv record.RecordID) *verdict.Verdict {
		for _, p := range rs.Attempts {
			if p == a || p.Has(Recorded) != nil {
				continue
			}
			for _, v := range p.Verdicts {
				if v.Source.Standing == verdict.StandingJudge && v.Source.Ref == inv {
					return v
				}
			}
		}
		return nil
	}
	// uncited is a landed execute call of an earlier, unrecorded attempt
	// that no step record cites: the call happened; its step never landed
	uncited := func() *invoke.State {
		cited := map[record.RecordID]bool{}
		for _, p := range rs.Attempts {
			for _, sd := range p.Steps {
				cited[sd.Invocation] = true
			}
		}
		for _, p := range rs.Attempts {
			if p == a || p.Has(Recorded) != nil {
				continue
			}
			for _, st := range p.Invocations {
				if st.Invocation.Purpose == invoke.PurposeExecute && st.Receipt != nil && !cited[st.Invocation.ID] {
					return st
				}
			}
		}
		return nil
	}

	// Intent
	if intent == nil {
		ip := intentPrompt(goal, rs.riders())
		o, resp, _, err := reuse(invoke.PurposeIntent, ip, 0, "")
		if err != nil {
			return nil, nil, err
		}
		if o == nil {
			o, resp, err = invoke_(invoke.PurposeIntent, ip, false, false)
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
		pp := planPrompt(goal, intent.Interpretation, rs.riders(), block)
		o, resp, by, err := reuse(invoke.PurposePlan, pp, 0, "")
		if err != nil {
			return nil, nil, err
		}
		if o != nil {
			if err := d.apply(ctx, rs, by, attemptN(by).Recall, o.Invocation); err != nil {
				return nil, nil, err
			}
		} else {
			o, resp, err = invoke_(invoke.PurposePlan, pp, len(block) > 0, false)
			if err != nil {
				return nil, nil, err
			}
		}
		if err := d.crash("after_plan_invoke"); err != nil {
			return nil, nil, err
		}
		if o.Terminal == invoke.TerminalFailed {
			return failed("plan: "+o.Reason, o.Invocation), nil, nil
		}
		planned, perr := ParsePlan(resp)
		if perr != nil {
			return failed("plan: "+perr.Error(), o.Invocation), nil, nil
		}
		plan = &Plan{Header: header(runRef(rs.Run), rs.Run, n, "plan/2"), Invocation: o.Invocation}
		for i, st := range planned {
			ref, err := d.Store.Put(thought.Step, []byte(st.Text))
			if err != nil {
				return nil, nil, err
			}
			plan.Steps = append(plan.Steps, ref)
			if len(st.After) > 0 {
				plan.Edges = append(plan.Edges, StepEdge{Ordinal: i + 1, After: st.After})
			}
			if len(st.Parallel) > 0 {
				ps := ParallelStep{Ordinal: i + 1, Policy: st.Policy}
				for _, g := range st.Parallel {
					gref, err := d.Store.Put(thought.Step, []byte(g))
					if err != nil {
						return nil, nil, err
					}
					ps.Goals = append(ps.Goals, gref)
				}
				plan.Parallel = append(plan.Parallel, ps)
			}
		}
		if err := d.commit(ctx, fmt.Sprintf("plan/%s/%d", rs.Run, n), plan); err != nil {
			return nil, nil, err
		}
		a.Plan = plan
		d.emit(rs, n, "plan", Executing, fmt.Sprintf("%d steps, %d with declared prerequisites", len(plan.Steps), len(plan.Edges)))
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
	after := planAfter(plan)
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
	var lastBy uint32    // the attempt that made lastExec
	var lastModel string // the model that made it (the fold binds the outcome's model to the invocation's)
	model := func() string {
		if lastModel != "" {
			return lastModel
		}
		return d.Backend.Capabilities().Model
	}
	if len(done) > 0 {
		last := done[len(done)-1]
		// the representative call is the latest step's that made one: a
		// gated step made none, and a recovery right after a final gated
		// step still records the closure with a receipt
		for i := len(done) - 1; i >= 0; i-- {
			if done[i].Invocation == "" {
				continue
			}
			lastExec, lastResp = done[i].Invocation, &done[i].Result
			if st, by := rs.invocation(lastExec); st != nil && st.Receipt != nil {
				lastReceipt, lastBy, lastModel = st.Receipt.ID, by, st.Invocation.Backend.Model
			}
			break
		}
		if last.Outcome == StepBlocked {
			return &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("blocked at step %d: %s", last.Ordinal, steps[last.Ordinal-1]), Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: model(), Recall: sel.ID, Steps: len(done)}, nil, nil
		}
	}
	resumeAt := len(done) + 1 // only the first new step can have an in-flight invocation to reuse
	for k := resumeAt; k <= len(steps); k++ {
		if io, err := d.interrupted(ctx, rs, a, fmt.Sprintf("before_step_%d", k)); err != nil || io != nil {
			if err != nil {
				return nil, nil, err
			}
			io.Usage, io.Recall, io.Steps = usage, sel.ID, len(done)
			if lastExec != "" {
				io.Invocation, io.Produced, io.Receipt, io.Response, io.Model = lastExec, lastBy, lastReceipt, lastResp, model()
			}
			return io, nil, nil
		}
		if by, outs := gatedBy(plan, k, done); len(by) > 0 {
			// the gate (LoopsBench item 1): a declared prerequisite did not
			// end done, so this step is not executed and not judged — nothing
			// ran. Its record says which prerequisites gated it and its
			// result says so in words (the closure judge and the deliverable
			// see the gap); its own dependents gate in turn.
			text := gatedText(by, outs)
			rref, err := d.Store.Put(thought.Response, text)
			if err != nil {
				return nil, nil, err
			}
			sd := &StepDone{Header: header(runRef(rs.Run), rs.Run, n, "step_done/2"), Ordinal: k, Step: plan.Steps[k-1], Terminal: invoke.TerminalComplete, Result: rref, Outcome: StepGated, GatedBy: by}
			sd.At = now()
			if err := d.commit(ctx, fmt.Sprintf("step/%s/%d/%d", rs.Run, n, k), sd); err != nil {
				return nil, nil, err
			}
			a.Steps = append(a.Steps, sd)
			done = append(done, sd)
			results = append(results, text)
			d.emit(rs, n, "step", Executing, fmt.Sprintf("%d/%d gated by %s", k, len(steps), ordinals(by)))
			if err := d.crash("after_gated_step"); err != nil {
				return nil, nil, err
			}
			continue
		}
		if ps := plan.ParallelAt(k); ps != nil {
			// a parallel step: fork, join, compose — then judge like any step
			var existing *ForkState
			if led, err := Fold(d.J.Production(), d.Store); err == nil {
				existing = led.forkAt(rs.Run, k)
			} else {
				return nil, nil, err
			}
			fs, composed, err := d.forkStep(ctx, rs, a, k, ps, existing)
			if err != nil {
				return nil, nil, err
			}
			rref, err := d.Store.Put(thought.Response, composed)
			if err != nil {
				return nil, nil, err
			}
			sd := &StepDone{Header: header(runRef(rs.Run), rs.Run, n, "step_done/2"), Ordinal: k, Step: plan.Steps[k-1], Fork: fs.Fork.ID, Terminal: invoke.TerminalComplete, Result: rref, Outcome: StepUnjudged}
			// the fork's own judge call (and its verdict, when committed) is
			// reused like any step's: the composition re-derives the same
			jp := stepJudgePrompt(goal, steps[k-1], composed, invoke.TerminalComplete, true)
			jo, jresp, jby, err := reuse(invoke.PurposeJudge, jp, 0, "")
			if err != nil {
				return nil, nil, err
			}
			if jo == nil {
				jo, jresp, err = invoke_(invoke.PurposeJudge, jp, false, false)
				if err != nil {
					return nil, nil, err
				}
				jby = n
			}
			if err := d.crash("after_fork_judge"); err != nil {
				return nil, nil, err
			}
			if jo.Terminal != invoke.TerminalFailed {
				if jr, perr := ParseJudge(jresp, "done", "blocked", "unclear"); perr == nil {
					var v *verdict.Verdict
					if jby != n {
						v = verdictOf(jo.Invocation)
					}
					if v == nil {
						v = &verdict.Verdict{Header: header(stepRef(rs.Run, n, k), rs.Run, n, "verdict/1"), VerdictKind: verdict.KindStep, Outcome: jr.Outcome, Confidence: jr.Confidence, Source: verdict.Source{Standing: verdict.StandingJudge, Ref: jo.Invocation}, Direction: verdict.Both, Basis: []record.Ref{{Kind: invoke.KindReceipt, ID: string(jo.Receipt)}}}
						if err := d.commit(ctx, fmt.Sprintf("verdict/%s/%d/step/%d", rs.Run, n, k), v); err != nil {
							return nil, nil, err
						}
						a.Verdicts = append(a.Verdicts, v)
					}
					sd.Verdict, sd.Outcome = v.ID, StepOutcome(v.Outcome)
				}
			}
			sd.At = now()
			if err := d.commit(ctx, fmt.Sprintf("step/%s/%d/%d", rs.Run, n, k), sd); err != nil {
				return nil, nil, err
			}
			a.Steps = append(a.Steps, sd)
			done = append(done, sd)
			results = append(results, composed)
			d.emit(rs, n, "step", Executing, fmt.Sprintf("%d/%d %s (fork)", k, len(steps), sd.Outcome))
			if err := d.crash("after_step"); err != nil {
				return nil, nil, err
			}
			if sd.Outcome == StepBlocked {
				return &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("blocked at step %d: %s", k, steps[k-1]), Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: model(), Recall: sel.ID, Steps: len(done)}, nil, nil
			}
			continue
		}
		var o *invoke.Outcome
		var resp []byte
		by := n
		sp := stepPrompt(goal, steps, after, k, results, block)
		if k == resumeAt {
			ro, rb, rby, err := reuse(invoke.PurposeExecute, sp, 0, "")
			if err != nil {
				return nil, nil, err
			}
			if ro != nil {
				if err := d.apply(ctx, rs, rby, attemptN(rby).Recall, ro.Invocation); err != nil {
					return nil, nil, err
				}
				o, resp, by = ro, rb, rby
			} else if continues == nil {
				// this attempt renders its own recall block (the policy
				// changed between the crash and its recovery): a call that
				// landed under the earlier block is this step's, and running
				// the step again would replay its effect. Fail closed — an
				// honest stop that names the call — rather than invoke again.
				if st := uncited(); st != nil {
					return failed(fmt.Sprintf("recovery: step %d's call %s landed under a recall selection this attempt does not continue (the recall policy changed between attempts); not run again", k, st.Invocation.ID), st.Invocation.ID), nil, nil
				}
			}
		}
		if o == nil {
			o, resp, err = invoke_(invoke.PurposeExecute, sp, len(block) > 0, true)
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
		// per-step judge (tool-less); a refused output = unjudged, continue.
		// A recovered attempt's judge call for this step (and its verdict,
		// when committed) are reused: the judgement happened once.
		sd := &StepDone{Header: header(runRef(rs.Run), rs.Run, n, "step_done/2"), Ordinal: k, Step: plan.Steps[k-1], Invocation: o.Invocation, Terminal: o.Terminal, Outcome: StepUnjudged}
		rr, err := receiptResponse(d.J, o.Receipt)
		if err != nil {
			return nil, nil, err
		}
		sd.Result = *rr
		var jo *invoke.Outcome
		var jresp []byte
		var jby uint32 = n
		jp := stepJudgePrompt(goal, steps[k-1], resp, o.Terminal, false)
		if by != n { // the execute was reused: so may be its judge — the one made after it
			jo, jresp, jby, err = reuse(invoke.PurposeJudge, jp, by, o.Invocation)
			if err != nil {
				return nil, nil, err
			}
		}
		if jo == nil {
			jo, jresp, err = invoke_(invoke.PurposeJudge, jp, false, false)
			if err != nil {
				return nil, nil, err
			}
			jby = n
		}
		if err := d.crash("after_step_judge"); err != nil {
			return nil, nil, err
		}
		if jo.Terminal != invoke.TerminalFailed {
			if jr, perr := ParseJudge(jresp, "done", "blocked", "unclear"); perr == nil {
				var v *verdict.Verdict
				if jby != n {
					v = verdictOf(jo.Invocation)
				}
				if v == nil {
					v = &verdict.Verdict{Header: header(stepRef(rs.Run, n, k), rs.Run, n, "verdict/1"), VerdictKind: verdict.KindStep, Outcome: jr.Outcome, Confidence: jr.Confidence, Source: verdict.Source{Standing: verdict.StandingJudge, Ref: jo.Invocation}, Direction: verdict.Both, Basis: []record.Ref{{Kind: invoke.KindReceipt, ID: string(jo.Receipt)}}}
					if err := d.commit(ctx, fmt.Sprintf("verdict/%s/%d/step/%d", rs.Run, n, k), v); err != nil {
						return nil, nil, err
					}
					a.Verdicts = append(a.Verdicts, v)
				}
				sd.Verdict, sd.Outcome = v.ID, StepOutcome(v.Outcome)
			} else {
				d.emit(rs, n, "step_unjudged", Executing, perr.Error())
			}
		}
		if err := d.crash("after_step_verdict"); err != nil {
			return nil, nil, err
		}
		sd.At = now() // stamped at commit: the judge ran between construction and here
		if err := d.commit(ctx, fmt.Sprintf("step/%s/%d/%d", rs.Run, n, k), sd); err != nil {
			return nil, nil, err
		}
		a.Steps = append(a.Steps, sd)
		done = append(done, sd)
		results = append(results, resp)
		lastExec, lastReceipt, lastResp, lastBy = o.Invocation, o.Receipt, rr, by
		if m, ok := made[o.Invocation]; ok {
			lastModel = m
		} else if st, _ := rs.invocation(o.Invocation); st != nil {
			lastModel = st.Invocation.Backend.Model
		}
		d.emit(rs, n, "step", Executing, fmt.Sprintf("%d/%d %s", k, len(steps), sd.Outcome))
		if err := d.crash("after_step"); err != nil {
			return nil, nil, err
		}
		if q, err := d.askAfterExecute(ctx, rs, n, k); err != nil {
			return nil, nil, err
		} else if q != nil {
			return &Outcome{Terminal: invoke.TerminalFailed, Reason: NeedsAnswer(q), Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: model(), Recall: sel.ID, Steps: len(done)}, nil, nil
		}
		if sd.Outcome == StepBlocked {
			return &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("blocked at step %d: %s", k, steps[k-1]), Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: model(), Recall: sel.ID, Steps: len(done)}, nil, nil
		}
	}
	// Closure judge (tool-less); a refused output = no judge verdict. The
	// execution is PARTIAL if any step's stream was: never promoted.
	terminal := invoke.TerminalComplete
	partial := make([]bool, len(done))
	for i, sd := range done {
		if sd.Terminal == invoke.TerminalPartial {
			terminal, partial[i] = invoke.TerminalPartial, true
		}
	}
	out := &Outcome{Terminal: terminal, Invocation: lastExec, Produced: lastBy, Receipt: lastReceipt, Response: lastResp, Usage: usage, Model: model(), Recall: sel.ID, Steps: len(done)}
	if terminal == invoke.TerminalPartial {
		out.Reason = "one or more steps ended partial"
	}
	// Regression obligations (LoopsBench item 2): what the done steps
	// proved is re-run before the closure judge sees the results, and the
	// observations reach the closure resolution (finish)
	live, err := d.liveInvocations()
	if err != nil {
		return nil, nil, err
	}
	obligations, err := deriveObligations(d.Store, live, done, "")
	if err != nil {
		return nil, nil, err
	}
	reruns, _, err := d.regress(ctx, rs, a, obligations)
	if err != nil {
		return nil, nil, err
	}
	// an earlier attempt's closure call and verdict are reused: judged once
	var candidates []*verdict.Verdict
	for _, p := range rs.Attempts {
		if p == a || p.Has(Recorded) != nil {
			continue
		}
		if v := priorVerdict(p, verdict.KindClosure, runRef(rs.Run)); v != nil {
			out.Usage = usage
			return out, []*verdict.Verdict{v}, nil
		}
	}
	cp := append(closurePrompt(goal, steps, results, partial), regressionBlock(reruns)...)
	jo, jresp, _, err := reuse(invoke.PurposeJudge, cp, 0, "")
	if err != nil {
		return nil, nil, err
	}
	if jo == nil {
		jo, jresp, err = invoke_(invoke.PurposeJudge, cp, false, false)
		if err != nil {
			return nil, nil, err
		}
	}
	if err := d.crash("after_closure_invoke"); err != nil {
		return nil, nil, err
	}
	out.Usage = usage
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
			a.Verdicts = append(a.Verdicts, v)
			candidates = append(candidates, v)
		} else {
			d.emit(rs, n, "closure_unjudged", Executing, perr.Error())
		}
	}
	if err := d.crash("after_closure_verdict"); err != nil {
		return nil, nil, err
	}
	return out, candidates, nil
}

// priorVerdict finds a judge-standing verdict an earlier attempt
// committed for the subject (a step, or the run's closure).
func priorVerdict(prev *AttemptState, kind verdict.VerdictKind, subject record.Ref) *verdict.Verdict {
	if prev == nil {
		return nil
	}
	for _, v := range prev.Verdicts {
		if v.VerdictKind == kind && v.Source.Standing == verdict.StandingJudge && v.Subject == subject {
			return v
		}
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
