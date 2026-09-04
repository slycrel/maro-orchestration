package run

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

var (
	ErrConfig    = errors.New("run: driver misconfigured")
	ErrCrashed   = errors.New("run: crashed at test seam")
	ErrEmptyGoal = errors.New("run: a goal needs text")
)

// Event is one lifecycle event per stage boundary (§5, FINDINGS #9). Events
// are a VIEW the driver emits as it commits; the Transition records are the
// durable form. Handle is the shared-spec 8-hex handle_id.
type Event struct {
	Handle  string
	Run     record.RunID
	Goal    record.RecordID
	Attempt uint32
	Stage   string
	State   State
	Detail  string
	At      time.Time
}

// Presentation is what an Origin shows the user: the whole payload, and the
// process lines (handle, delivery id, ack token) kept apart from it.
type Presentation struct {
	Delivery record.RecordID
	Run      record.RunID
	Handle   string
	Attempt  uint32
	Payload  []byte
	Ref      thought.Ref
	Required DeliveryState
	Token    string // shown only when the policy wants user_acknowledged
	Closure  string
	Terminal string
	// MayDuplicate counts earlier presentations of this payload the process
	// died inside; the origin says so, because the user may have seen it.
	MayDuplicate int
}

// Origin is the user-facing edge for a goal's origin. Present returns nil
// once the TRANSPORT took the payload — nothing more is claimed here.
type Origin interface {
	Name() GoalOrigin
	Present(ctx context.Context, p Presentation) error
}

// Driver is the one shell: it makes invocations, submits commits, emits
// events. NOW is a configuration of it (plan cardinality 1, self judge).
type Driver struct {
	J       *journal.Journal
	Store   *thought.Store
	Backend invoke.Backend
	Origin  Origin
	Events  func(Event)
	Timeout time.Duration
	// MaxDeliveryAttempts bounds the outbox. Why 3: a CLI origin fails only
	// when its writer is gone (closed pipe), which a retry never repairs; the
	// bound exists so a dead origin becomes delivery_failed with a reason
	// instead of a busy loop. A remote origin will register its own with
	// its own why.
	MaxDeliveryAttempts int
	// MaxAttempts bounds recovery. Why 3: a crash that repeats identically on
	// every restart (a deterministic backend panic, a refusal the shell
	// cannot record) never repairs itself; past the bound the next attempt
	// records an honest failure naming the loop instead of starting a fourth.
	MaxAttempts int
	// CrashAt stops the driver dead (as if the process died) immediately
	// AFTER the named stage commits; "invoke:<stage>" is forwarded to the
	// invocation shell's seam. Test seam for the kill matrix; production
	// never sets it.
	CrashAt string
}

// Report is what a completed (or crashed) drive hands back.
type Report struct {
	Run      record.RunID
	Handle   string
	Goal     record.RecordID
	Attempt  uint32
	Mission  Mission
	Delivery record.RecordID
	Token    string
	Payload  []byte
}

func (d *Driver) validate() error {
	switch {
	case d.J == nil || d.Store == nil:
		return fmt.Errorf("%w: journal and store are required", ErrConfig)
	case d.Backend == nil:
		return fmt.Errorf("%w: backend is required", ErrConfig)
	case d.Origin == nil || !origins[d.Origin.Name()]:
		return fmt.Errorf("%w: origin is required and must be registered", ErrConfig)
	case d.Backend.Capabilities().Name == "":
		return fmt.Errorf("%w: backend declares no name", ErrConfig)
	}
	if d.MaxDeliveryAttempts < 0 || d.MaxAttempts < 0 {
		return fmt.Errorf("%w: bounds must be positive (0 = default)", ErrConfig)
	}
	if d.MaxDeliveryAttempts == 0 {
		d.MaxDeliveryAttempts = 3
	}
	if d.MaxAttempts == 0 {
		d.MaxAttempts = 3
	}
	return nil
}

func (d *Driver) config() ConfigSnapshot {
	return ConfigSnapshot{Lane: LaneNow, Backend: d.Backend.Capabilities(), Judge: JudgeSelf, PlanCardinality: 1, TimeoutMillis: d.Timeout.Milliseconds(), FamilyRule: FamilyRule, ResolverVer: verdict.ResolverVer}
}

func (d *Driver) crash(stage string) error {
	if d.CrashAt == stage {
		return fmt.Errorf("%w: %s", ErrCrashed, stage)
	}
	return nil
}

func (d *Driver) emit(rs *RunState, n uint32, stage string, st State, detail string) {
	if d.Events == nil {
		return
	}
	var goal record.RecordID
	if rs.Goal != nil {
		goal = rs.Goal.ID
	}
	d.Events(Event{Handle: HandleOf(rs.Run), Run: rs.Run, Goal: goal, Attempt: n, Stage: stage, State: st, Detail: detail, At: now()})
}

func (d *Driver) commit(ctx context.Context, key string, recs ...record.Record) error {
	_, err := d.J.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: d.J.Epoch(), Records: recs})
	return err
}

func header(subject record.Ref, run record.RunID, n uint32, schema record.SchemaVer) record.Header {
	return record.Header{ID: record.NewID(), Schema: schema, RunID: run, Attempt: n, Subject: subject, At: now()}
}

func runRef(run record.RunID) record.Ref { return record.Ref{Kind: "run", ID: string(run)} }

// Run takes a goal from its origin through intake, one attempt, and
// delivery. It returns the report even when the mission is not delivered;
// the error is for a driver that could not continue (crash seam, journal
// refusal), in which case Resume finishes the run on the next start.
func (d *Driver) Run(ctx context.Context, goalText []byte, policy DeliveryPolicy) (*Report, error) {
	if err := d.validate(); err != nil {
		return nil, err
	}
	if !requiredStates[policy.Required] {
		return nil, fmt.Errorf("%w: delivery policy %q", ErrConfig, policy.Required)
	}
	if len(bytes.TrimSpace(goalText)) == 0 {
		return nil, ErrEmptyGoal
	}
	// Intake: store the thought, THEN the records that claim it (§1b).
	ref, err := d.Store.Put(thought.Goal, goalText)
	if err != nil {
		return nil, err
	}
	goal, fam := Intake(goalText, ref, d.Origin.Name(), policy)
	if err := d.commit(ctx, "goal/"+string(goal.ID), goal, fam); err != nil {
		return nil, err
	}
	rs := &RunState{Run: record.RunID(record.NewID()), Goal: goal, Family: fam}
	d.emit(rs, 0, "intake", "", string(fam.Family))
	if err := d.crash("after_intake"); err != nil {
		return nil, err
	}
	return d.drive(ctx, rs, nil, nil)
}

// Intake is pure: the goal record and its treatment-blind assessment. The
// classifier sees the goal bytes and nothing else.
func Intake(text []byte, ref thought.Ref, origin GoalOrigin, policy DeliveryPolicy) (*Goal, *FamilyAssessment) {
	id := record.NewID()
	subj := record.Ref{Kind: "goal", ID: string(id)}
	g := &Goal{Header: record.Header{ID: id, Schema: "goal/1", Subject: subj, At: now()}, Root: id, Text: ref, Origin: origin, Delivery: policy}
	fam, why := Classify(string(text))
	return g, &FamilyAssessment{Header: record.Header{ID: record.NewID(), Schema: "family_assessment/1", Subject: subj, At: now()}, Goal: id, Family: fam, Rule: FamilyRule, Reason: why}
}

// drive runs one attempt of rs from the last committed idempotent stage.
// prev is the attempt being recovered from (nil for attempt 1).
func (d *Driver) drive(ctx context.Context, rs *RunState, prev *AttemptState, forced *Outcome) (*Report, error) {
	n := uint32(len(rs.Attempts) + 1)
	att := &RunAttempt{Header: header(runRef(rs.Run), rs.Run, n, "run_attempt/1"), Goal: rs.Goal.ID, Family: rs.Family.ID, Config: d.config()}
	if prev != nil {
		att.RecoversFrom = prev.Attempt.Attempt
	}
	created := &Transition{Header: header(runRef(rs.Run), rs.Run, n, "run_transition/1"), To: Created}
	if err := d.commit(ctx, fmt.Sprintf("attempt/%s/%d", rs.Run, n), att, created); err != nil {
		return nil, err
	}
	a := &AttemptState{Attempt: att, Transitions: []*Transition{created}}
	rs.Attempts = append(rs.Attempts, a)
	d.emit(rs, n, "attempt", Created, "")
	if err := d.crash("after_start"); err != nil {
		return nil, err
	}
	if err := d.transition(ctx, rs, a, Executing, "", nil); err != nil {
		return nil, err
	}
	if err := d.crash("after_executing"); err != nil {
		return nil, err
	}
	// Execute — from the previous attempt's evidence when it exists (§5a:
	// a new attempt starts from the last committed idempotent stage).
	out, err := d.execute(ctx, rs, n, prev, forced)
	if err != nil {
		return nil, err
	}
	d.emit(rs, n, "execute", Executing, string(out.Terminal))
	if err := d.crash("after_execute"); err != nil {
		return nil, err
	}
	// Judge — the self claim; observations arrive with the checks (step 7).
	self := SelfVerdict(rs.Run, n, out)
	if err := d.commit(ctx, fmt.Sprintf("verdict/%s/%d/self", rs.Run, n), self); err != nil {
		return nil, err
	}
	if err := d.transition(ctx, rs, a, Judged, "", []record.Ref{{Kind: verdict.KindVerdict, ID: string(self.ID)}}); err != nil {
		return nil, err
	}
	if err := d.crash("after_judged"); err != nil {
		return nil, err
	}
	// Record — the resolution, then the execution outcome as a fold.
	res, err := verdict.Commit(ctx, d.J, rs.Run, n, verdict.Candidates{Subject: runRef(rs.Run), VerdictKind: verdict.KindClosure, Verdicts: []*verdict.Verdict{self}}, verdict.DefaultThresholds)
	if err != nil && !errors.Is(err, verdict.ErrAlreadyResolved) {
		return nil, err
	}
	rs.Closure = res
	out.GoalText, out.Closure, out.ClosureOut, out.ClosureCnf = rs.Goal.Text, res.ID, res.Outcome, res.Confidence
	if res.Effective == self.ID {
		out.ClosureSrc = string(self.Source.Standing)
	} else if res.Effective != "" {
		return nil, fmt.Errorf("run: resolution %s names an effective verdict %s this driver did not commit", res.ID, res.Effective)
	}
	if err := d.transition(ctx, rs, a, Recorded, "", nil, out); err != nil {
		return nil, err
	}
	if err := d.crash("after_recorded"); err != nil {
		return nil, err
	}
	return d.deliver(ctx, rs, a)
}

// execute returns the execution outcome for attempt n. Order of evidence:
// a receipt from the recovered attempt is reused (the work happened); an
// indeterminate reconciliation is an honest failure, never a replay; else
// a fresh invocation.
func (d *Driver) execute(ctx context.Context, rs *RunState, n uint32, prev *AttemptState, forced *Outcome) (*Outcome, error) {
	if prev != nil {
		p := prev.Attempt.Attempt
		for i := len(prev.Invocations) - 1; i >= 0; i-- {
			st := prev.Invocations[i]
			if st.Invocation.Purpose != invoke.PurposeExecute {
				continue
			}
			// provenance is the invocation's, never the recovering attempt's
			model := st.Invocation.Backend.Model
			switch {
			case st.Receipt != nil:
				resp := st.Receipt.Response
				return &Outcome{Terminal: st.Terminal.State, Reason: st.Terminal.Reason, Invocation: st.Invocation.ID, Produced: p, Receipt: st.Receipt.ID, Response: &resp, Usage: st.Receipt.Usage, Model: model}, nil
			case st.Terminal != nil && st.Terminal.State == invoke.TerminalFailed:
				return &Outcome{Terminal: invoke.TerminalFailed, Reason: "attempt " + fmt.Sprint(p) + ": " + st.Terminal.Reason, Invocation: st.Invocation.ID, Produced: p, Usage: st.Terminal.Usage, Model: model}, nil
			case st.Reconciled != nil && st.Reconciled.Disposition == invoke.DispositionIndeterminate:
				return &Outcome{Terminal: invoke.TerminalFailed, Reason: string(invoke.DispositionIndeterminate) + ": " + st.Reconciled.Evidence, Invocation: st.Invocation.ID, Produced: p, Model: model}, nil
			}
			// abandoned (or never dispatched): safe to run again
		}
	}
	if forced != nil {
		return forced, nil
	}
	text, err := d.Store.Get(rs.Goal.Text)
	if err != nil {
		return nil, err
	}
	sh := &invoke.Shell{J: d.J, Store: d.Store, Run: rs.Run, Attempt: n, CrashAt: strings.TrimPrefix(d.CrashAt, "invoke:")}
	if !strings.HasPrefix(d.CrashAt, "invoke:") {
		sh.CrashAt = ""
	}
	o, err := sh.Invoke(ctx, d.Backend, invoke.Request{Purpose: invoke.PurposeExecute, Prompt: text, Tools: d.Backend.Capabilities().ActsOutward, Timeout: d.Timeout}, nil)
	var inc *invoke.Incapable
	if errors.As(err, &inc) {
		// a refusal the shell makes BEFORE writing anything (input over the
		// backend's declared maximum): deterministic, so a retry can only
		// repeat it — record it as the attempt's honest failure (D16: the
		// thought is never sliced to fit; the route is what is lacking)
		return &Outcome{Terminal: invoke.TerminalFailed, Reason: "backend_incapable: " + err.Error()}, nil
	}
	if err != nil {
		return nil, err
	}
	if o.Err != nil {
		return nil, o.Err
	}
	out := &Outcome{Terminal: o.Terminal, Reason: o.Reason, Invocation: o.Invocation, Produced: n, Receipt: o.Receipt, Usage: o.Usage, Model: d.Backend.Capabilities().Model}
	if o.Terminal != invoke.TerminalFailed {
		ref, err := receiptResponse(d.J, o.Receipt)
		if err != nil {
			return nil, err
		}
		out.Response = ref
	}
	return out, nil
}

// receiptResponse reads the response ref off the committed receipt: the
// outcome cites what the journal holds, not what the shell returned.
func receiptResponse(j *journal.Journal, id record.RecordID) (*thought.Ref, error) {
	var ref *thought.Ref
	err := j.Production().Scan(0, func(r record.Record) error {
		if rc, ok := r.(*invoke.Receipt); ok && rc.ID == id {
			x := rc.Response
			ref = &x
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if ref == nil {
		return nil, fmt.Errorf("run: receipt %s not in the journal", id)
	}
	return ref, nil
}

// SelfVerdict is the executing agent's own claim, pure over the outcome. A
// complete stream is a claim of achievement, which the resolver may never
// promote; a failed one is a demotion, which stands.
func SelfVerdict(run record.RunID, n uint32, out *Outcome) *verdict.Verdict {
	v := &verdict.Verdict{Header: header(runRef(run), run, n, "verdict/1"), VerdictKind: verdict.KindClosure, Source: verdict.Source{Standing: verdict.StandingSelf}, Direction: verdict.MayDemote, Confidence: 0.5}
	if out.Terminal == invoke.TerminalFailed {
		v.Outcome, v.Confidence = "not_achieved", 0.9
	} else {
		v.Outcome = "achieved"
	}
	if out.Receipt != "" {
		v.Basis = []record.Ref{{Kind: invoke.KindReceipt, ID: string(out.Receipt)}}
	}
	return v
}

// transition is the ONE writer of run transitions in the driver (drain and
// Ack included, except Ack's, which is committed atomically with its ack):
// extra is the outcome (recorded) or the delivery state (delivered).
func (d *Driver) transition(ctx context.Context, rs *RunState, a *AttemptState, to State, reason string, ev []record.Ref, extra ...any) error {
	n := a.Attempt.Attempt
	t := &Transition{Header: header(runRef(rs.Run), rs.Run, n, "run_transition/1"), From: a.Current(), To: to, Reason: reason, Evidence: ev}
	key := fmt.Sprintf("run/%s/%d/%s", rs.Run, n, to)
	for _, x := range extra {
		switch v := x.(type) {
		case *Outcome:
			t.Outcome = v
		case DeliveryState:
			t.Delivery = v
			key += "/" + string(v)
		}
	}
	if err := d.commit(ctx, key, t); err != nil {
		return err
	}
	a.Transitions = append(a.Transitions, t)
	d.emit(rs, n, string(to), to, reason+string(t.Delivery))
	return nil
}

// Render composes the deliverable from the outcome: the whole response
// (D16: never sliced), or an honest failure line.
func Render(out *Outcome, response []byte) []byte {
	switch out.Terminal {
	case invoke.TerminalComplete:
		return response
	case invoke.TerminalPartial:
		return append(append([]byte{}, response...), []byte("\n\n[maro: the backend stream ended partial: "+out.Reason+"]")...)
	}
	return []byte("maro: the run did not produce an answer.\nterminal: failed\nreason: " + out.Reason + "\n")
}

// deliver prepares the delivery for a recorded attempt (idempotent: an
// existing DeliveryPrepared is reused) and drains the outbox for it.
func (d *Driver) deliver(ctx context.Context, rs *RunState, a *AttemptState) (*Report, error) {
	n := a.Attempt.Attempt
	rec := a.Has(Recorded)
	if rec == nil {
		return nil, fmt.Errorf("run: deliver before recorded")
	}
	if a.Delivery == nil {
		var response []byte
		if rec.Outcome.Response != nil {
			b, err := d.Store.Get(*rec.Outcome.Response)
			if err != nil {
				return nil, err
			}
			response = b
		}
		ref, err := d.Store.Put(thought.Deliverable, Render(rec.Outcome, response))
		if err != nil {
			return nil, err
		}
		nonce := make([]byte, 16)
		if _, err := rand.Read(nonce); err != nil {
			return nil, err
		}
		id := record.NewID()
		p := &DeliveryPrepared{Header: record.Header{ID: id, Schema: "delivery_prepared/1", RunID: rs.Run, Attempt: n, Subject: record.Ref{Kind: "delivery", ID: string(id)}, At: now()},
			Payload: ref, Origin: d.Origin.Name(), Required: rs.Goal.Delivery.Required, Nonce: hex.EncodeToString(nonce)}
		if err := d.commit(ctx, fmt.Sprintf("delivery/%s/%d", rs.Run, n), p); err != nil {
			return nil, err
		}
		a.Delivery = &Delivery{Prepared: p}
		d.emit(rs, n, "prepared", Recorded, string(id))
		if err := d.crash("after_prepared"); err != nil {
			return nil, err
		}
	}
	if err := d.drain(ctx, rs, a); err != nil {
		return nil, err
	}
	return d.report(rs, a)
}

func (d *Driver) report(rs *RunState, a *AttemptState) (*Report, error) {
	r := &Report{Run: rs.Run, Handle: HandleOf(rs.Run), Goal: rs.Goal.ID, Attempt: a.Attempt.Attempt, Mission: MissionOf(rs)}
	if dl := a.Delivery; dl != nil {
		r.Delivery = dl.Prepared.ID
		b, err := d.Store.Get(dl.Prepared.Payload)
		if err != nil {
			return nil, err
		}
		r.Payload = b
		if dl.Prepared.Required == UserAcknowledged {
			r.Token = TokenFor(dl.Prepared.ID, dl.Prepared.Payload.Hash, dl.Prepared.Nonce)
		}
	}
	return r, nil
}

// Resume is the restart pass: reconcile invocations, then finish every
// non-terminal run — past `recorded`, continue delivery; before it, mark
// the attempt recoverable and start the next one from the last committed
// idempotent stage (§5a). Idempotent: every commit is keyed.
func (d *Driver) Resume(ctx context.Context) ([]*Report, error) {
	if err := d.validate(); err != nil {
		return nil, err
	}
	if _, _, err := invoke.Reconcile(ctx, &invoke.Shell{J: d.J, Store: d.Store}); err != nil {
		return nil, err
	}
	led, err := Fold(d.J.Production())
	if err != nil {
		return nil, err
	}
	var reports []*Report
	// goals taken in whose run never started: start one (the goal and its
	// assessment are the committed intake; nothing outcome-bearing ran)
	for _, g := range led.Unstarted {
		fam := led.Families[g.ID]
		if fam == nil {
			return reports, fmt.Errorf("run: goal %s has no family assessment", g.ID)
		}
		rs := &RunState{Run: record.RunID(record.NewID()), Goal: g, Family: fam}
		rep, err := d.drive(ctx, rs, nil, nil)
		if err != nil {
			return reports, err
		}
		reports = append(reports, rep)
	}
	ids := make([]string, 0, len(led.Runs))
	for id := range led.Runs {
		ids = append(ids, string(id))
	}
	sort.Strings(ids)
	for _, id := range ids {
		rs := led.Runs[record.RunID(id)]
		if rs.Terminal() {
			continue
		}
		a := rs.Latest()
		var rep *Report
		if a.Has(Recorded) != nil {
			rep, err = d.deliver(ctx, rs, a)
		} else {
			if a.Current() != Recoverable {
				if err := d.transition(ctx, rs, a, Recoverable, "process restarted before recorded (was "+string(a.Current())+")", nil); err != nil {
					return reports, err
				}
			}
			var forced *Outcome
			if len(rs.Attempts) >= d.MaxAttempts {
				forced = &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("attempt bound %d reached: %d attempts died before recorded", d.MaxAttempts, len(rs.Attempts))}
			}
			rep, err = d.drive(ctx, rs, a, forced)
		}
		if err != nil {
			return reports, err
		}
		reports = append(reports, rep)
	}
	return reports, nil
}
