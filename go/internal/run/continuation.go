package run

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Continuation (successor audit 2026-09-17, §4.2). A run STOPS when it
// ends short of its goal and the record says so: its execution failed
// ("needs answer", a backend that exited, the attempt bound, a refused
// continuation) or ended partial, or its closure resolved not_achieved. A
// complete execution whose closure is unknown FINISHED: the self claim
// cannot promote it (verdict rule 4), but nothing says it fell short, and
// a judge-less NOW run ends that way every time — a follow of it is a
// follow, never a claim. A later run that follows a stopped run is
// CONTINUING it, and says so in a record the fold checks
// BEFORE its first attempt — the contract the Python engine's resume
// closed in checkpoint chunks 6, 7 and 9:
//
//   - the source is claimed durably before anything executes; the claim
//     names the source, how the run came to follow it (`--after`, which
//     `answer` uses; or the landscape's own `rerun` decision) and the
//     journal head the claim was decided over — which is the head it is
//     appended to (the commit expects that head, so two drivers deciding
//     over the same head cannot both claim: the second re-decides);
//   - one continuation per source: a second is refused while the first is
//     live, and after the first ends the chain moves forward — a source
//     whose continuation finished is done (follow the continuation); one
//     whose continuation stopped is continued THROUGH it (continue the
//     continuation, which carries the source's lineage and context);
//   - a refused continuation is recorded too, and the run ends on it as an
//     honest failed terminal ("refusals end like finished runs"); a refused
//     run holds no work, so it cannot itself be continued — the refusal
//     names the run to follow or continue instead;
//   - the source is SETTLED by the continuation's own end: the fold derives
//     live / finished / stopped from the continuation run's outcome, so
//     there is no settlement record to forget or to forge.
//
// A run that follows a FINISHED run is a plain follow (lineage, related
// context): nothing to continue, no record. The continuation's requests
// carry a "## Continues prior run" block — where the source stopped, its
// goal, answer and plan with each step's outcome — re-derived by the fold
// like the landscape's related block.

const KindContinuation record.Kind = "continuation"

// How a continuation came to follow its source.
const (
	ContinuedAfter = "after" // the operator named the run (--after; `answer`)
	ContinuedRerun = "rerun" // the landscape decided rerun and the chosen run had stopped
)

var continuationHows = map[string]bool{ContinuedAfter: true, ContinuedRerun: true}

// continuationRefusedPrefix opens the failed terminal's reason when the
// claim was refused; the fold binds the two.
const continuationRefusedPrefix = "continuation refused: "

// Continuation is the claim: run-scoped, before attempt 1 (attempt 0),
// subject the run. It carries the goal so a run that died between the
// claim and its first attempt still binds to its goal on the fold.
type Continuation struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Goal          record.RecordID `json:"goal"`
	Source        record.RunID    `json:"source"`
	How           string          `json:"how"`
	AsOf          uint64          `json:"as_of"`             // the journal head the claim was decided over
	Refused       string          `json:"refused,omitempty"` // the claim was refused for this reason: attempt 1 ends failed with it
}

func (r *Continuation) Head() *record.Header { return &r.Header }
func (r *Continuation) Kind() record.Kind    { return KindContinuation }
func (r *Continuation) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt != 0 || r.Subject.Kind != "run" || r.Subject.ID != string(r.RunID) {
		return errors.New("continuation: subject must be the run, before its first attempt")
	}
	if err := record.ValidateID(r.Goal); err != nil {
		return fmt.Errorf("continuation: goal: %w", err)
	}
	if r.Source == "" || r.Source == r.RunID {
		return errors.New("continuation: source is another run")
	}
	if !continuationHows[r.How] {
		return fmt.Errorf("continuation: how %q out of vocabulary", r.How)
	}
	if r.AsOf == 0 {
		return errors.New("continuation: as_of names the journal head the claim was decided over")
	}
	if strings.TrimSpace(r.Refused) != r.Refused {
		return errors.New("continuation: refused is trimmed")
	}
	return nil
}

// stoppedOutcome reports a recorded outcome that fell short: execution
// failed or partial, or a closure resolved not_achieved. Complete with
// closure achieved or unknown finished.
func stoppedOutcome(o *Outcome) bool {
	return o.Terminal != invoke.TerminalComplete || o.ClosureOut == "not_achieved"
}

// recordedOutcome is the run's latest recorded outcome, nil before one.
func recordedOutcome(rs *RunState) *Outcome {
	a := rs.Latest()
	if a == nil {
		return nil
	}
	rec := a.Has(Recorded)
	if rec == nil {
		return nil
	}
	return rec.Outcome
}

// terminalAsOf reports whether the run was terminal as of a journal head.
func terminalAsOf(rs *RunState, asOf uint64) bool {
	return rs.TerminalAt != 0 && rs.TerminalAt <= asOf
}

// Stopped reports a run that ended short of its goal (see stoppedOutcome).
func Stopped(rs *RunState) bool {
	o := recordedOutcome(rs)
	return rs.TerminalAt != 0 && o != nil && stoppedOutcome(o)
}

// Now is the as-of that means "everything folded".
const Now = ^uint64(0)

// ContinuationState is what became of a continuation as of a journal head:
// live (its run has not ended), finished, or stopped.
func ContinuationState(led *Ledger, c *Continuation, asOf uint64) string {
	crs := led.Runs[c.RunID]
	if crs == nil || !terminalAsOf(crs, asOf) {
		return "live"
	}
	if Stopped(crs) {
		return "stopped"
	}
	return "finished"
}

// Continuable decides, as of a journal head, whether a new run following
// source would continue it: (false, nil) when the source finished (a
// plain follow); (true, nil) when it stopped and nothing continues it;
// an error naming why otherwise — the text is the recorded refusal.
func Continuable(led *Ledger, source record.RunID, asOf uint64) (bool, error) {
	rs := led.Runs[source]
	if rs == nil {
		return false, fmt.Errorf("no run %s", HandleOf(source))
	}
	if !terminalAsOf(rs, asOf) {
		state := "not started"
		if a := rs.Latest(); a != nil {
			state = fmt.Sprintf("attempt %d %s", a.Attempt.Attempt, a.Current())
		}
		return false, fmt.Errorf("run %s has not stopped (%s)", HandleOf(source), state)
	}
	if !Stopped(rs) {
		return false, nil
	}
	if c := rs.Continuation; c != nil && c.Refused != "" {
		// it ended on a refusal, not on work: nothing to pick up
		return false, fmt.Errorf("run %s is a refused continuation (%s)", HandleOf(source), c.Refused)
	}
	if c := led.Continued[source]; c != nil && c.Seq <= asOf {
		by := HandleOf(c.RunID)
		switch ContinuationState(led, c, asOf) {
		case "live":
			return false, fmt.Errorf("run %s is being continued by %s (live)", HandleOf(source), by)
		case "finished":
			return false, fmt.Errorf("run %s was continued by %s, which finished: follow %s instead", HandleOf(source), by, by)
		default:
			return false, fmt.Errorf("run %s was continued by %s, which stopped: continue %s instead", HandleOf(source), by, by)
		}
	}
	return true, nil
}

// continuationSource is the run a new run would continue, and how: the
// run of the goal it follows (--after), or the landscape's chosen run
// when the relation is rerun. A related landscape is a tangent, not a
// continuation; a fork child or replay arm continues nothing.
func continuationSource(rs *RunState, runs map[record.RunID]*RunState) (record.RunID, string) {
	if rs.Goal == nil {
		return "", ""
	}
	if rs.Goal.Origin == OriginFork || rs.Goal.Origin == OriginReplay {
		return "", ""
	}
	if rs.Goal.Parent != "" {
		for id, p := range runs {
			if p.Goal != nil && p.Goal.ID == rs.Goal.Parent {
				return id, ContinuedAfter
			}
		}
		return "", ""
	}
	if ls := rs.Landscape; ls != nil && ls.Relation == RelationRerun {
		return ls.Chosen, ContinuedRerun
	}
	return "", ""
}

// refusalOutcome is the honest failed terminal a refused continuation
// ends on; nil for a claim.
func refusalOutcome(c *Continuation) *Outcome {
	if c == nil || c.Refused == "" {
		return nil
	}
	return &Outcome{Terminal: invoke.TerminalFailed, Reason: continuationRefusedPrefix + c.Refused}
}

// stopLine says where the source stopped, from its recorded outcome.
func stopLine(src *RunState) string {
	a := src.Latest()
	if a == nil {
		return "it never started an attempt"
	}
	rec := a.Has(Recorded)
	if rec == nil || rec.Outcome == nil {
		return "it never recorded an outcome"
	}
	o := rec.Outcome
	if o.Terminal == invoke.TerminalFailed {
		return "execution failed: " + o.Reason
	}
	return fmt.Sprintf("execution %s, closure %s (confidence %.2f)", o.Terminal, o.ClosureOut, o.ClosureCnf)
}

// ContinuationContext renders the block that rides into a continuation's
// requests after the related block: where the source stopped; and, unless
// the landscape's rerun block already carried them, its goal, answer and
// plan; then each planned step's outcome. The fold re-derives it.
func ContinuationContext(rs *RunState, runs map[record.RunID]*RunState, get func(thought.Ref) ([]byte, error)) ([]byte, error) {
	c := rs.Continuation
	if c == nil || c.Refused != "" {
		return nil, nil
	}
	src := runs[c.Source]
	if src == nil || src.Goal == nil {
		return nil, fmt.Errorf("continuation: source run %s is not in the ledger", c.Source)
	}
	var b bytes.Buffer
	fmt.Fprintf(&b, "\n\n## Continues prior run (%s, %s)\nIt stopped: %s\n", HandleOf(c.Source), c.How, stopLine(src))
	a := src.Latest()
	if rs.Landscape == nil || rs.Landscape.Relation != RelationRerun {
		text, err := get(src.Goal.Text)
		if err != nil {
			return nil, err
		}
		fmt.Fprintf(&b, "Its goal: %s\n", text)
		head, err := deliveredHead(src, get)
		if err != nil {
			return nil, err
		}
		if len(head) > 0 {
			fmt.Fprintf(&b, "Its answer:\n%s\n", head)
		} else {
			b.WriteString("It recorded no answer.\n")
		}
		if a != nil && a.Plan != nil {
			b.WriteString("Its plan (reuse or revise):\n")
			listing, err := planListing(a, get)
			if err != nil {
				return nil, err
			}
			b.Write(listing)
		}
	}
	if a != nil && a.Plan != nil {
		b.WriteString("Where its steps ended: ")
		var parts []string
		for i := range a.Plan.Steps {
			out := "not reached"
			if i < len(a.Steps) && a.Steps[i] != nil {
				out = string(a.Steps[i].Outcome)
			}
			parts = append(parts, fmt.Sprintf("%d %s", i+1, out))
		}
		b.WriteString(strings.Join(parts, ", ") + "\n")
	}
	if q := askedQuestion(src); q != "" {
		fmt.Fprintf(&b, "It asked the operator: %s\n", q)
	}
	return b.Bytes(), nil
}

// askedQuestion is the operator question the source ended on, if any.
func askedQuestion(src *RunState) string {
	for _, a := range src.Attempts {
		if a == nil {
			continue
		}
		if a.Question != nil {
			return a.Question.Question
		}
		if a.Intent != nil && !a.Intent.Clear && a.Intent.Question != "" {
			return a.Intent.Question
		}
	}
	return ""
}

// planListing renders an attempt's plan as the numbered lines the related
// block shows ("N. step (after …)").
func planListing(a *AttemptState, get func(thought.Ref) ([]byte, error)) ([]byte, error) {
	var b bytes.Buffer
	after := planAfter(a.Plan) // its declared prerequisites are part of the plan
	for i, ref := range a.Plan.Steps {
		st, err := get(ref)
		if err != nil {
			return nil, err
		}
		if len(after[i]) > 0 {
			fmt.Fprintf(&b, "%d. %s (after %s)\n", i+1, st, ordinals(after[i]))
			continue
		}
		fmt.Fprintf(&b, "%d. %s\n", i+1, st)
	}
	return b.Bytes(), nil
}

// continuationRetries bounds the re-decisions a driver makes when the
// journal moved under its claim.
const continuationRetries = 8

// continuation is the driver stage after the lineage is settled and before
// attempt 1: the claim (or its refusal), idempotent by run. It returns the
// forced outcome a refused run ends on. The decision is made over ONE
// journal prefix and appended to exactly that head: the fold is pinned at
// the head read, and the commit expects it — a record landing in between
// (another driver's claim on the same source) fails the precondition and
// the decision is made again over the new prefix.
func (d *Driver) continuation(ctx context.Context, rs *RunState) (*Outcome, error) {
	var (
		c   *Continuation
		led *Ledger
	)
	for try := 0; ; try++ {
		asOf := d.J.Head()
		var err error
		led, err = Fold(d.J.Production().PinAt(asOf), d.Store)
		if err != nil {
			return nil, err
		}
		if prior := led.Runs[rs.Run]; prior != nil && prior.Continuation != nil {
			rs.Continuation, rs.Related, rs.SourceWork = prior.Continuation, prior.Related, prior.SourceWork
			return refusalOutcome(prior.Continuation), nil
		}
		source, how := continuationSource(rs, led.Runs)
		if source == "" {
			return nil, nil
		}
		stopped, cerr := Continuable(led, source, asOf)
		if cerr == nil && !stopped {
			return nil, nil // a plain follow of a finished run
		}
		c = &Continuation{Header: header(runRef(rs.Run), rs.Run, 0, "continuation/1"), Goal: rs.Goal.ID, Source: source, How: how, AsOf: asOf}
		if cerr != nil {
			c.Refused = cerr.Error()
		}
		_, err = d.J.Submit(ctx, journal.Command{IdempotencyKey: "continuation/" + string(rs.Run), Epoch: d.J.Epoch(), ExpectHead: &asOf, Records: []record.Record{c}})
		if err == nil {
			break
		}
		if !errors.Is(err, journal.ErrPrecondition) || try >= continuationRetries {
			return nil, err
		}
		d.emit(rs, 0, "continuation", "", fmt.Sprintf("the journal moved past %d: deciding again", asOf))
	}
	rs.Continuation = c
	if c.Refused == "" {
		rs.SourceWork = workOf(led.Runs[c.Source])
		block, err := ContinuationContext(rs, led.Runs, d.Store.Get)
		if err != nil {
			return nil, err
		}
		rs.Related = append(rs.Related, block...)
		d.emit(rs, 0, "continuation", "", fmt.Sprintf("continues %s (%s)", HandleOf(c.Source), c.How))
	} else {
		d.emit(rs, 0, "continuation", "", "refused: "+c.Refused)
	}
	if err := d.crash("after_continuation"); err != nil {
		return nil, err
	}
	return refusalOutcome(c), nil
}

// ContinuedBy is the source-side line: what continues this run, and how
// that went. "" when nothing does.
func ContinuedBy(led *Ledger, rs *RunState) string {
	c := led.Continued[rs.Run]
	if c == nil {
		return ""
	}
	return fmt.Sprintf("continued by %s: %s", HandleOf(c.RunID), ContinuationState(led, c, Now))
}
