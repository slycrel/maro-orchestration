package run

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/regression"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// Regression obligations (LoopsBench item 2). A step that ran a test
// runner, saw it pass and was judged done has PROVED something; a later
// step can break it, and the closure judge — shown only the step results
// — would not know. The obligation is carried by derivation, not by a
// ledger: it IS the done step's tool_effect (a shell call whose command
// parses as one test-runner invocation) plus its tool_effect_result (the
// positive pass evidence), both already in the journal. At closure the
// driver re-runs every obligation — the exact argv, no shell, in the
// recorded directory — and commits what it saw as a regression_rerun plus
// an Observation of check regression_rerun against the run's closure: a
// failing re-run is `refuted` at confidence 1 and the resolver (verdict
// §6) turns any `achieved` judge verdict into not_achieved mechanically;
// a passing one is `supported`; a re-run that could not run (directory
// gone, runner missing, timeout) is `could_not_observe` and moves nothing.
// The fold re-derives the obligation set from the same records and
// refuses a re-run that cites none of them, and an observation whose
// result disagrees with its re-run.
//
// The grammar, the tallies and the classifier are the shared spec with the
// Python engine (internal/regression). The Python engine carries the rows
// on its checkpoint and re-validates them on restore; here nothing is
// carried — a recovery derives the same set from the journal — and there
// is no kill switch: an off switch that stays off is a removed feature
// (feedback 2026-08-02), and the re-run only ever runs what a step ran.

// KindRegressionRerun is one closure re-run of a regression obligation.
const KindRegressionRerun record.Kind = "regression_rerun"

// RegressionRerun records what the closure re-run of an obligation saw.
type RegressionRerun struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Step          int                `json:"step"`                // the AGENDA step that proved it; 0 = the NOW execute
	Invocation    record.RecordID    `json:"invocation"`          // the execute whose shell effect passed
	Effect        record.RecordID    `json:"effect"`              // that tool_effect
	Argv          []string           `json:"argv"`                // the exact argv re-run, wrapper included
	Env           []string           `json:"env,omitempty"`       // the command's own NAME=value assignments, sorted
	Dir           string             `json:"dir"`                 // the recorded directory, absolute
	Exit          int                `json:"exit"`                // -1 when the runner never started or timed out
	TimedOut      bool               `json:"timed_out,omitempty"` //
	Truncated     bool               `json:"truncated,omitempty"` // a stream's head was dropped (regression.MaxCapture); the tail is what is stored
	Stdout        *thought.Ref       `json:"stdout,omitempty"`    // absent when the run produced none
	Stderr        *thought.Ref       `json:"stderr,omitempty"`
	Outcome       regression.Outcome `json:"outcome"`
	Why           string             `json:"why,omitempty"` // inconclusive: the verifier-failure class
}

func (r *RegressionRerun) Head() *record.Header { return &r.Header }
func (r *RegressionRerun) Kind() record.Kind    { return KindRegressionRerun }
func (r *RegressionRerun) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("regression_rerun: %w", err)
	}
	if r.Invocation == "" || r.Effect == "" || len(r.Argv) == 0 || r.Dir == "" {
		return errors.New("regression_rerun: invocation, effect, argv and dir are required")
	}
	if !filepath.IsAbs(r.Dir) {
		return fmt.Errorf("regression_rerun: dir %q is not absolute", r.Dir)
	}
	if r.Exit < -1 || r.Exit > 255 {
		return fmt.Errorf("regression_rerun: exit %d out of range", r.Exit)
	}
	if r.TimedOut && r.Exit != -1 {
		return fmt.Errorf("regression_rerun: timed out with exit %d", r.Exit)
	}
	switch r.Outcome {
	case regression.Pass, regression.Fail:
		if r.Why != "" {
			return errors.New("regression_rerun: a classified re-run carries no why")
		}
	case regression.Inconclusive:
		if r.Why == "" {
			return errors.New("regression_rerun: an inconclusive re-run names its class")
		}
	default:
		return fmt.Errorf("regression_rerun: outcome %q out of vocabulary", r.Outcome)
	}
	for _, ref := range []*thought.Ref{r.Stdout, r.Stderr} {
		if ref != nil {
			if err := ref.Validate(); err != nil {
				return err
			}
			if ref.Kind != thought.Evidence {
				return fmt.Errorf("regression_rerun: output thought of kind %q, not evidence", ref.Kind)
			}
		}
	}
	return nil
}

// ToolEnver is a backend that gives its tool calls an environment beyond
// the process's own (the secrets drop path, an injection); a re-run of
// what those tool calls ran gets the same.
type ToolEnver interface{ ToolEnv() []string }

// cmd is the obligation's command as recorded.
func (r *RegressionRerun) cmd() *regression.Command {
	return &regression.Command{Argv: r.Argv, Env: r.Env}
}

func init() {
	record.Register(record.Spec{Kind: KindRegressionRerun, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(RegressionRerun{}), Retention: record.Forever,
		Writer:   "the driver at closure, once per obligation derived from the attempt's done steps' shell effects (before the closure judge)",
		Reader:   "the run fold (re-derives the obligation; checks the outcome against the exit code and output); the closure judge prompt; `maro-go runs show`",
		Decision: "whether what a step proved still holds at closure: a failing re-run refutes an achieved closure through its observation",
	})
}

// obligation is one derived regression obligation: a done step's shell
// effect whose command is a test-runner invocation that passed.
type obligation struct {
	Step   int // 0 for a NOW execute
	Inv    *invoke.Invocation
	Effect *invoke.ToolEffect
	Cmd    *regression.Command
	Dir    string
	Key    string
}

// deriveObligations lists the obligations of a set of settled steps (and,
// for a NOW attempt, its one execute), in step then effect order,
// deduplicated by argv+env+dir. A step contributes only when it ended
// done through an execute (a fork or gated step proved nothing here); an
// execute contributes only its shell effects with a seen, non-error
// result carrying the positive pass evidence. Pure over the journal: the
// driver and the fold call the same function.
// evidenceBytes reads the raw bytes a tool effect's input/output thought
// carries (the shell stores them in a byte-preserving envelope).
func evidenceBytes(store *thought.Store, ref thought.Ref) ([]byte, error) {
	body, err := store.Get(ref)
	if err != nil {
		return nil, err
	}
	return invoke.DecodeEvidence(body)
}

// liveInvocations folds the journal's invocation states so this attempt's
// own execute calls (which the driver's run state, folded at start, does
// not carry) can be read at closure.
func (d *Driver) liveInvocations() (func(record.RecordID) *invoke.State, error) {
	states, err := invoke.Fold(d.J.Production())
	if err != nil {
		return nil, err
	}
	return func(id record.RecordID) *invoke.State { return states[id] }, nil
}

func deriveObligations(store *thought.Store, invocation func(record.RecordID) *invoke.State, steps []*StepDone, nowExec record.RecordID) ([]obligation, error) {
	var out []obligation
	seen := map[string]bool{}
	harvest := func(step int, id record.RecordID) error {
		st := invocation(id)
		if st == nil {
			return nil
		}
		for _, e := range st.Effects {
			if e.Refused || !regression.IsShellOp(e.Op) {
				continue
			}
			in, err := evidenceBytes(store, e.Input)
			if err != nil {
				return err
			}
			cmd, ok := regression.Parse(regression.CommandOf(in))
			if !ok {
				continue
			}
			res := st.Results[e.Ordinal]
			var outb []byte
			if res != nil {
				if outb, err = evidenceBytes(store, res.Output); err != nil {
					return err
				}
			}
			if !regression.Passed(cmd, res != nil, res != nil && res.IsError, outb) {
				continue
			}
			dir := regression.Dir(st.Invocation.Cwd, cmd.CD)
			key := regression.Key(cmd, dir)
			if seen[key] {
				continue
			}
			seen[key] = true
			out = append(out, obligation{Step: step, Inv: st.Invocation, Effect: e, Cmd: cmd, Dir: dir, Key: key})
		}
		return nil
	}
	for _, sd := range steps {
		if sd.Outcome != StepDoneOK || sd.Invocation == "" {
			continue
		}
		if err := harvest(sd.Ordinal, sd.Invocation); err != nil {
			return nil, err
		}
	}
	if nowExec != "" {
		if err := harvest(0, nowExec); err != nil {
			return nil, err
		}
	}
	return out, nil
}

// regress re-runs every obligation at closure and commits, per
// obligation, the regression_rerun and the observation it grounds (one
// journal command: neither exists without the other). A re-run an earlier
// unrecorded attempt already made for the same obligation is reused, not
// run again (the same rule as judge verdicts). Returns the re-runs in
// obligation order and their observations.
func (d *Driver) regress(ctx context.Context, rs *RunState, a *AttemptState, obs []obligation) ([]*RegressionRerun, []*verdict.Observation, error) {
	n := a.Attempt.Attempt
	var reruns []*RegressionRerun
	var observations []*verdict.Observation
	for i, ob := range obs {
		var rr *RegressionRerun
		var o *verdict.Observation
		for _, p := range rs.Attempts {
			if p == a || p.Has(Recorded) != nil {
				continue
			}
			for _, prr := range p.Regression {
				if regression.Key(prr.cmd(), prr.Dir) == ob.Key {
					rr, o = prr, p.observationOf(prr.ID)
				}
			}
		}
		if rr == nil || o == nil {
			var extra []string
			if te, ok := d.Backend.(ToolEnver); ok {
				extra = te.ToolEnv()
			}
			// an obligation is re-run WHERE IT WAS RECORDED or not at all
			// (executor.go): a container-recorded probe re-run on the host
			// is a different experiment wearing the same name, and its
			// green would retire an obligation nothing verified
			res := &regression.Result{Exit: -1, Outcome: regression.Inconclusive, Why: rerunWorld(ob.Inv)}
			if res.Why == "" {
				res = regression.Rerun(ctx, ob.Cmd, ob.Dir, extra, d.Timeout)
			}
			rr = &RegressionRerun{Header: header(runRef(rs.Run), rs.Run, n, "regression_rerun/1"), Step: ob.Step, Invocation: ob.Inv.ID, Effect: ob.Effect.ID, Argv: ob.Cmd.Argv, Env: ob.Cmd.Env, Dir: ob.Dir, Exit: res.Exit, TimedOut: res.TimedOut, Truncated: res.Truncated, Outcome: res.Outcome, Why: res.Why}
			for _, pair := range []struct {
				b   []byte
				ref **thought.Ref
			}{{res.Stdout, &rr.Stdout}, {res.Stderr, &rr.Stderr}} {
				if len(pair.b) == 0 {
					continue
				}
				ref, err := d.Store.Put(thought.Evidence, pair.b)
				if err != nil {
					return nil, nil, err
				}
				*pair.ref = &ref
			}
			claim, err := d.Store.Put(thought.Evidence, []byte(regressionClaim(rr)))
			if err != nil {
				return nil, nil, err
			}
			o = &verdict.Observation{Header: header(runRef(rs.Run), rs.Run, n, "observation/1"), Check: verdict.CheckRegressionRerun, Claim: claim, Evidence: []record.Ref{{Kind: KindRegressionRerun, ID: string(rr.ID)}, {Kind: invoke.KindToolEffect, ID: string(ob.Effect.ID)}}}
			o.Result, o.Confidence = regressionResult(rr.Outcome)
			if err := d.commit(ctx, fmt.Sprintf("regression/%s/%d/%d", rs.Run, n, i), rr, o); err != nil {
				return nil, nil, err
			}
			d.emit(rs, n, "regression", Executing, fmt.Sprintf("step %d: %s → %s", ob.Step, strings.Join(ob.Cmd.Argv, " "), rr.Outcome))
		}
		a.Regression = append(a.Regression, rr)
		a.Observations = append(a.Observations, o)
		reruns = append(reruns, rr)
		observations = append(observations, o)
		if err := d.crash("after_regression"); err != nil {
			return nil, nil, err
		}
	}
	return reruns, observations, nil
}

// regressionClaim is the claim a re-run tests, in words: what the step
// proved and where.
func regressionClaim(rr *RegressionRerun) string {
	where := "the execute"
	if rr.Step > 0 {
		where = fmt.Sprintf("step %d", rr.Step)
	}
	return fmt.Sprintf("`%s` passed at %s in %s and still passes at closure", strings.Join(append(append([]string{}, rr.Env...), rr.Argv...), " "), where, rr.Dir)
}

// regressionResult maps a re-run outcome to the observation it grounds.
// The re-run is deterministic evidence: a fail refutes the claim with
// certainty and a pass supports it; an inconclusive re-run proves nothing
// either way and carries no confidence.
func regressionResult(o regression.Outcome) (verdict.ObsResult, float64) {
	switch o {
	case regression.Fail:
		return verdict.Refuted, 1
	case regression.Pass:
		return verdict.Supported, 1
	}
	return verdict.CouldNotObserve, 0
}

// regressionBlock renders the re-runs for the closure judge: present only
// when the attempt had obligations, so a run without any renders its
// closure prompt byte-for-byte as before.
func regressionBlock(reruns []*RegressionRerun) string {
	if len(reruns) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteString("\n## Regression checks (what a step proved, re-run at closure)\n")
	for _, rr := range reruns {
		where := "the execute"
		if rr.Step > 0 {
			where = fmt.Sprintf("step %d", rr.Step)
		}
		cmd := strings.Join(append(append([]string{}, rr.Env...), rr.Argv...), " ")
		switch rr.Outcome {
		case regression.Fail:
			fmt.Fprintf(&b, "- `%s` in %s passed at %s and FAILS at closure (exit %d) — a regression\n", cmd, rr.Dir, where, rr.Exit)
		case regression.Pass:
			fmt.Fprintf(&b, "- `%s` in %s passed at %s and still passes\n", cmd, rr.Dir, where)
		default:
			fmt.Fprintf(&b, "- `%s` in %s passed at %s; the re-run could not run (%s) — proves nothing either way\n", cmd, rr.Dir, where, rr.Why)
		}
	}
	return b.String()
}

// observationOf finds the observation an attempt committed for a re-run.
func (a *AttemptState) observationOf(rerun record.RecordID) *verdict.Observation {
	for _, o := range a.Observations {
		if o.Check == verdict.CheckRegressionRerun && len(o.Evidence) > 0 && o.Evidence[0].Kind == KindRegressionRerun && o.Evidence[0].ID == string(rerun) {
			return o
		}
	}
	return nil
}

// checkRegressionRerun is the fold's rule: a re-run cites an obligation
// this attempt's settled steps (or its NOW execute) derive, and its
// outcome follows from what it recorded.
// attemptObligations re-derives, at fold time, the obligations the driver
// derived at this attempt's closure: its done steps (AGENDA) or its own
// complete execute (NOW).
func attemptObligations(rs *RunState, a *AttemptState, inv map[record.RecordID]*invoke.State, store *thought.Store) ([]obligation, error) {
	var nowExec record.RecordID
	if rs.Goal != nil && rs.Goal.Lane == LaneNow {
		// the execute the closure judged: the newest complete execute of
		// any attempt up to this one (a recovered attempt reuses an earlier
		// attempt's landed call, which sits in THAT attempt's list)
		for _, p := range rs.Attempts {
			if p.Attempt.Attempt > a.Attempt.Attempt {
				break
			}
			for _, st := range p.Invocations {
				if st.Invocation.Purpose == invoke.PurposeExecute && st.Terminal != nil && st.Terminal.State == invoke.TerminalComplete && st.Receipt != nil {
					nowExec = st.Invocation.ID
				}
			}
		}
	}
	return deriveObligations(store, func(id record.RecordID) *invoke.State { return inv[id] }, a.Steps, nowExec)
}

// closureReruns reconstructs, at fold time, the re-runs the closure judge
// of this attempt was shown: per obligation, an earlier unrecorded
// attempt's re-run of the same command in the same dir (reused, as the
// driver reuses it), else this attempt's own. Obligations with no re-run
// on record are simply absent (the prompt check then fails honestly).
func closureReruns(rs *RunState, a *AttemptState, inv map[record.RecordID]*invoke.State, store *thought.Store) ([]*RegressionRerun, error) {
	obs, err := attemptObligations(rs, a, inv, store)
	if err != nil {
		return nil, err
	}
	var out []*RegressionRerun
	for _, ob := range obs {
		var rr *RegressionRerun
		for _, p := range rs.Attempts {
			if p == a || p.Has(Recorded) != nil {
				continue
			}
			for _, prr := range p.Regression {
				if regression.Key(prr.cmd(), prr.Dir) == ob.Key && p.observationOf(prr.ID) != nil {
					rr = prr
				}
			}
		}
		if rr == nil {
			for _, own := range a.Regression {
				if own.Invocation == ob.Inv.ID && own.Effect == ob.Effect.ID {
					rr = own
				}
			}
		}
		if rr != nil {
			out = append(out, rr)
		}
	}
	// every re-run the judge is shown reaches the resolver through its
	// observation (the driver commits both in one command; this attempt's
	// own re-run without one is a forgery)
	for _, rr := range a.Regression {
		if a.observationOf(rr.ID) == nil {
			return nil, fmt.Errorf("run: %s attempt %d regression re-run %s has no observation", rs.Run, a.Attempt.Attempt, rr.ID)
		}
	}
	return out, nil
}

func checkRegressionRerun(rs *RunState, a *AttemptState, x *RegressionRerun, inv map[record.RecordID]*invoke.State, store *thought.Store) error {
	if a.Current() != Executing {
		return fmt.Errorf("run: %s attempt %d regression re-run out of place (state %s)", x.RunID, x.Attempt, a.Current())
	}
	obs, err := attemptObligations(rs, a, inv, store)
	if err != nil {
		return err
	}
	var found *obligation
	for i := range obs {
		if obs[i].Inv.ID == x.Invocation && obs[i].Effect.ID == x.Effect {
			found = &obs[i]
		}
	}
	if found == nil || found.Step != x.Step || found.Dir != x.Dir || !reflect.DeepEqual(found.Cmd.Argv, x.Argv) || !reflect.DeepEqual(found.Cmd.Env, x.Env) {
		return fmt.Errorf("run: %s attempt %d regression re-run cites no obligation this attempt's done steps derive", x.RunID, x.Attempt)
	}
	for _, prev := range a.Regression {
		if prev.Invocation == x.Invocation && prev.Effect == x.Effect {
			return fmt.Errorf("run: %s attempt %d re-ran the same obligation twice", x.RunID, x.Attempt)
		}
	}
	// the outcome follows from the termination and the stored bytes, in
	// every shape: a runner that did not finish (timeout, cancel, signal,
	// not started: exit -1) decided nothing; one that exited is classified
	// over its own output — a `why` never changes what the bytes say, and
	// neither does the truncated flag (a failing tail is still a Fail)
	want := regression.Inconclusive
	if !x.TimedOut && x.Exit >= 0 {
		var so, se []byte
		for _, pair := range []struct {
			ref *thought.Ref
			b   *[]byte
		}{{x.Stdout, &so}, {x.Stderr, &se}} {
			if pair.ref == nil {
				continue
			}
			b, err := store.Get(*pair.ref)
			if err != nil {
				return err
			}
			*pair.b = b
		}
		if x.Truncated && len(so) != regression.MaxCapture && len(se) != regression.MaxCapture {
			// the flag is not the writer's to set freely: a truncated capture
			// kept exactly MaxCapture bytes of at least one stream
			return fmt.Errorf("run: %s attempt %d regression re-run claims a truncated capture over %d+%d stored bytes", x.RunID, x.Attempt, len(so), len(se))
		}
		want = regression.Decide(found.Cmd.Family, x.Exit, x.Truncated, regression.Output(so, se))
	}
	if x.Outcome != want {
		return fmt.Errorf("run: %s attempt %d regression re-run outcome %s does not follow from exit %d and its output (%s)", x.RunID, x.Attempt, x.Outcome, x.Exit, want)
	}
	// the driver reuses, never repeats, an earlier unrecorded attempt's
	// observed re-run of the same obligation
	for _, p := range rs.Attempts {
		if p == a || p.Has(Recorded) != nil {
			continue
		}
		for _, prr := range p.Regression {
			if regression.Key(prr.cmd(), prr.Dir) == found.Key && p.observationOf(prr.ID) != nil {
				return fmt.Errorf("run: %s attempt %d re-ran an obligation attempt %d already re-ran (%s)", x.RunID, x.Attempt, p.Attempt.Attempt, prr.ID)
			}
		}
	}
	return nil
}

// checkRegressionObservation: an observation of check regression_rerun
// cites a re-run this attempt committed and says what the re-run says.
func checkRegressionObservation(a *AttemptState, x *verdict.Observation) error {
	if len(x.Evidence) != 2 || x.Evidence[0].Kind != KindRegressionRerun || x.Evidence[1].Kind != invoke.KindToolEffect {
		return fmt.Errorf("run: observation %s of check %s does not cite exactly a re-run and its tool effect", x.ID, x.Check)
	}
	var rr *RegressionRerun
	for _, r := range a.Regression {
		if string(r.ID) == x.Evidence[0].ID {
			rr = r
		}
	}
	if rr == nil {
		return fmt.Errorf("run: observation %s cites re-run %s that this attempt did not commit", x.ID, x.Evidence[0].ID)
	}
	if x.Evidence[1].ID != string(rr.Effect) {
		return fmt.Errorf("run: observation %s cites tool effect %s, not its re-run's %s", x.ID, x.Evidence[1].ID, rr.Effect)
	}
	want, conf := regressionResult(rr.Outcome)
	if x.Result != want || x.Confidence != conf || x.Subject != runRef(x.RunID) || x.Claim != thought.Address(thought.Evidence, []byte(regressionClaim(rr))) {
		return fmt.Errorf("run: observation %s does not say what its re-run %s says (%s vs %s)", x.ID, rr.ID, x.Result, rr.Outcome)
	}
	if a.observationOf(rr.ID) != nil {
		return fmt.Errorf("run: re-run %s observed twice", rr.ID)
	}
	return nil
}
