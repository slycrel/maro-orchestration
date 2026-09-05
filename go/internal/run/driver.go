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
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

var (
	ErrConfig    = errors.New("run: driver misconfigured")
	ErrCrashed   = errors.New("run: crashed at test seam")
	ErrEmptyGoal = errors.New("run: a goal needs text")
	ErrIntegrity = errors.New("run: journal holds evidence the driver could not have written")
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
	// Health is the supervisor's degraded line (lanes down or stalled).
	Health []string
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
	// Judge is the tool-less backend for intent, plan, and judge invocations
	// (AGENDA). Nil ⇒ Backend, asked to run tool-less.
	Judge invoke.Backend
	// Lane selects the configuration: now (one execute, self judge) or
	// agenda (intent → plan → steps with a model judge each → closure judge).
	Lane    Lane
	Origin  Origin
	Events  func(Event)
	Timeout time.Duration
	// Health, when set, is the supervisor's degraded line; every delivery
	// carries it while it is non-empty (§10).
	Health func() []string
	// Confined: every invocation runs tool-less (fork children).
	Confined bool
	// ChildOf: the fork this driver's run is a member of (child drivers).
	ChildOf record.RecordID
	// ModelJudge: a NOW run asks the closure judge (tool-less) after its
	// execute, so its closure can be established by a judge.
	ModelJudge bool
	// Target is the goal's metering envelope (§11): committed with the goal,
	// measured at delivery, never enforced. Nil = no target.
	Target *TargetSpec
	// Lens is the persona lens every judge request of this driver's runs
	// is rendered under (§13); "" or "neutral" = no prefix. Recorded in
	// the attempt config; the fold checks each judge request begins with it.
	Lens string
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
	// Replay makes this drive one arm of an experiment assignment: the goal
	// is a replay of the unit, the recall and policy selections carry the
	// arm's forced sets, and the origin presents to no one. Set by the
	// experiment package only.
	Replay *ReplayContext
	// Admit is the intake command's narrow assignment capability (§5, §9):
	// called with the goal and its assessment BEFORE the intake commits,
	// it may stamp an arm on the goal and return control records (an
	// experiment assignment) that commit in the SAME command, together
	// with the journal head the decision was made at — the command is
	// refused if the head moved, so admit/stop and an intake cannot race.
	// nil: no live experiment admits goals here.
	Admit AdmitFunc
	// CrashAt stops the driver dead (as if the process died) immediately
	// AFTER the named stage commits; "invoke:<stage>" is forwarded to the
	// invocation shell's seam. Test seam for the kill matrix; production
	// never sets it.
	CrashAt string
}

// AdmitFunc: see Driver.Admit.
type AdmitFunc func(ctx context.Context, g *Goal, fam *FamilyAssessment) (recs []record.Record, head *uint64, err error)

// IntakeCommand commits a goal and its assessment as ONE command with
// whatever the admitter adds. An admission decided at a head that moved
// is refused by the sequencer and decided again (bounded: a third refusal
// is the caller's error to see).
func IntakeCommand(ctx context.Context, j *journal.Journal, admit AdmitFunc, g *Goal, fam *FamilyAssessment, extra ...record.Record) error {
	for try := 0; try < IntakeTries; try++ {
		recs := append([]record.Record{g, fam}, extra...)
		var head *uint64
		if admit != nil && try < IntakeTries-1 {
			g.Arm = nil
			extra, h, err := admit(ctx, g, fam)
			if err != nil {
				return err
			}
			recs, head = append(recs, extra...), h
		} else {
			// the last try commits the goal plain: an admission that lost
			// the head twice is dropped, never the user's goal
			g.Arm = nil
		}
		_, err := j.Submit(ctx, journal.Command{IdempotencyKey: "goal/" + string(g.ID), Epoch: j.Epoch(), ExpectHead: head, Records: recs})
		if err == nil || !errors.Is(err, journal.ErrPrecondition) {
			return err
		}
	}
	return nil
}

// IntakeTries bounds the admission decision: two decisions under a
// precondition, then a plain commit. Why 3: a lost head is unrelated
// traffic landing between fold and commit; two losses in a row are
// rare, and a third decision would only delay the goal for an
// experiment's benefit.
const IntakeTries = 3

// ReplayContext is what an arm forces on its run (§8a): the assignment,
// the arm, the unit goal it replays (parent; root = the unit's root), and
// the forced sets — apply for the treatment of apply_item, withhold for
// the treatment of ablate_item; the control arm forces nothing.
type ReplayContext struct {
	Assignment record.RecordID
	Arm        string
	Unit       record.RecordID
	Root       record.RecordID
	Apply      []learn.ItemRev
	Withhold   []learn.ItemRev
}

func (rc *ReplayContext) arm() *learn.ArmRef {
	return &learn.ArmRef{Assignment: rc.Assignment, Arm: rc.Arm, Apply: rc.Apply, Withhold: rc.Withhold}
}

// ReplayOrigin is the replay arm's origin: nothing is presented; the arm's
// deliverable is read by the evaluator from the journal.
type ReplayOrigin struct{}

func (ReplayOrigin) Name() GoalOrigin                            { return OriginReplay }
func (ReplayOrigin) Present(context.Context, Presentation) error { return nil }

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
	if _, ok := lenses[d.Lens]; d.Lens != "" && !ok {
		return fmt.Errorf("%w: unknown lens %q (known: %v)", ErrConfig, d.Lens, LensNames())
	}
	if d.Replay != nil && (d.Replay.Unit == "" || d.Replay.Root == "" || !learn.Arms[d.Replay.Arm]) {
		return fmt.Errorf("%w: replay context needs unit, root, and an arm", ErrConfig)
	}
	if d.Lane == "" {
		d.Lane = LaneNow
	}
	if !lanes[d.Lane] {
		return fmt.Errorf("%w: lane %q", ErrConfig, d.Lane)
	}
	if d.Judge == nil {
		d.Judge = d.Backend
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

func (d *Driver) config(lane Lane, pol *learn.PolicySelection) (ConfigSnapshot, error) {
	// the lens binding: the name AND the content ref of its text, so the
	// attempt's config says exactly which bytes every lensed request
	// begins with (§13) — the fold checks each invocation against it
	l, _, err := d.lens()
	if err != nil {
		return ConfigSnapshot{}, err
	}
	c := ConfigSnapshot{Lane: lane, Backend: d.Backend.Capabilities(), Judge: JudgeSelf, PlanCardinality: 1, TimeoutMillis: d.Timeout.Milliseconds(), Lens: d.lensName(), FamilyRule: FamilyRule, ResolverVer: verdict.ResolverVer, Confined: d.Confined, Policy: pol.ID, Mechanisms: map[learn.Mechanism]bool{}}
	if l != nil {
		ref := l.Text
		c.LensText = &ref
	}
	for m, on := range pol.Snapshot {
		c.Mechanisms[m] = on
	}
	// the policy boundary: MechModelJudge decides which backend judges;
	// the snapshot records it, the fold checks it, judge(a) consumes it
	judge := d.Judge
	if !c.Mechanisms[learn.MechModelJudge] {
		judge = d.Backend
	}
	if lane == LaneAgenda {
		c.Judge, c.PlanCardinality, c.JudgeBackend = JudgeModel, 0, judge.Capabilities()
	} else if d.ModelJudge {
		c.Judge, c.JudgeBackend = JudgeModel, judge.Capabilities()
	}
	return c, nil
}

// judge is the backend an attempt's judgments run on: the configured judge,
// or the executor itself (tool-less) when policy switched the model judge
// off. The ONE place the mechanism is consumed.
func (d *Driver) judge(a *AttemptState) invoke.Backend {
	if !a.Attempt.Config.Mechanisms[learn.MechModelJudge] {
		return d.Backend
	}
	return d.Judge
}

// policy is the attempt's policy decision: the selection over the learned
// fold as it stands and one application per enabled revision. Committed in
// the attempt's own command, so an attempt never exists without it.
func (d *Driver) policy(ctx context.Context, rs *RunState, n uint32) (*learn.PolicySelection, []record.Record, error) {
	// the harness defaults are data: seeded once per workspace, here,
	// before the first selection that could read them
	led, err := learn.EnsureSeeds(ctx, d.J)
	if err != nil {
		return nil, nil, err
	}
	family := ""
	if rs.Family.Family != FamilyNone {
		family = string(rs.Family.Family)
	}
	pol := learn.SelectPolicy(led, learn.Query{Scope: scope(rs.Goal), Family: family, Standing: learn.Selectable, Arm: rs.Goal.Arm})
	pol.Header = header(runRef(rs.Run), rs.Run, n, "policy_selection/1")
	recs := []record.Record{pol}
	for i, rule := range led.PolicyRules(pol) {
		ir := pol.Enabled[i]
		recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, rs.Run, n, "policy_application/1"), Item: ir.Item, Revision: ir.Revision, Selection: pol.ID, Rule: rule})
	}
	return pol, recs, nil
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
	if err := ValidateIntake(goalText, d.Lane, policy); err != nil {
		return nil, err
	}
	// Intake: store the thought, THEN the records that claim it (§1b).
	ref, err := d.Store.Put(thought.Goal, goalText)
	if err != nil {
		return nil, err
	}
	goal, fam := Intake(goalText, ref, d.Origin.Name(), d.Lane, policy)
	if (d.Origin.Name() == OriginReplay) != (d.Replay != nil) {
		return nil, fmt.Errorf("%w: a replay origin needs a replay context, and only it", ErrConfig)
	}
	var extra []record.Record
	var target *MeteringTarget
	if d.Target != nil {
		target = d.Target.Record(goal.ID)
		extra = append(extra, target)
	}
	if d.Replay != nil {
		goal.Arm = d.Replay.arm()
		goal.Parent, goal.Root = d.Replay.Unit, d.Replay.Root
		if err := d.commit(ctx, "goal/"+string(goal.ID), append([]record.Record{goal, fam}, extra...)...); err != nil {
			return nil, err
		}
	} else if err := IntakeCommand(ctx, d.J, d.Admit, goal, fam, extra...); err != nil {
		return nil, err
	}
	rs := &RunState{Run: record.RunID(record.NewID()), Goal: goal, Family: fam, Target: target}
	d.emit(rs, 0, "intake", "", string(fam.Family))
	if err := d.crash("after_intake"); err != nil {
		return nil, err
	}
	return d.drive(ctx, rs, nil, nil)
}

// ValidateIntake is the one check every intake path runs BEFORE anything
// durable is written: a goal needs text, a registered lane, a registered
// delivery policy.
func ValidateIntake(text []byte, lane Lane, policy DeliveryPolicy) error {
	if len(bytes.TrimSpace(text)) == 0 {
		return ErrEmptyGoal
	}
	if !lanes[lane] {
		return fmt.Errorf("%w: lane %q", ErrConfig, lane)
	}
	if !requiredStates[policy.Required] {
		return fmt.Errorf("%w: delivery policy %q", ErrConfig, policy.Required)
	}
	return nil
}

// Intake is pure: the goal record and its treatment-blind assessment. The
// classifier sees the goal bytes and nothing else.
func Intake(text []byte, ref thought.Ref, origin GoalOrigin, lane Lane, policy DeliveryPolicy) (*Goal, *FamilyAssessment) {
	id := record.NewID()
	subj := record.Ref{Kind: "goal", ID: string(id)}
	g := &Goal{Header: record.Header{ID: id, Schema: "goal/1", Subject: subj, At: now()}, Root: id, Text: ref, Origin: origin, Lane: lane, Delivery: policy}
	fam, why := Classify(string(text))
	return g, &FamilyAssessment{Header: record.Header{ID: record.NewID(), Schema: "family_assessment/1", Subject: subj, At: now()}, Goal: id, Family: fam, Rule: FamilyRule, Reason: why}
}

// drive runs one attempt of rs from the last committed idempotent stage.
// prev is the attempt being recovered from (nil for attempt 1).
func (d *Driver) drive(ctx context.Context, rs *RunState, prev *AttemptState, forced *Outcome) (*Report, error) {
	n := uint32(len(rs.Attempts) + 1)
	pol, papps, err := d.policy(ctx, rs, n)
	if err != nil {
		return nil, err
	}
	cfg, err := d.config(rs.Goal.Lane, pol)
	if err != nil {
		return nil, err
	}
	att := &RunAttempt{Header: header(runRef(rs.Run), rs.Run, n, "run_attempt/1"), Goal: rs.Goal.ID, Family: rs.Family.ID, Config: cfg}
	if prev != nil {
		att.RecoversFrom = prev.Attempt.Attempt
	}
	created := &Transition{Header: header(runRef(rs.Run), rs.Run, n, "run_transition/1"), To: Created}
	if err := d.commit(ctx, fmt.Sprintf("attempt/%s/%d", rs.Run, n), append([]record.Record{att, created}, papps...)...); err != nil {
		return nil, err
	}
	d.emit(rs, n, "policy", Created, fmt.Sprintf("%d of %d policies enabled", len(pol.Enabled), len(pol.Considered)))
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
	// NOW's one boundary: an interrupt pending here stops the run, honestly
	// (AGENDA's boundaries are before each step, in its own loop)
	if rs.Goal.Lane == LaneNow {
		if out, err := d.interrupted(ctx, rs, a, "before_execute"); err != nil || out != nil {
			if err != nil {
				return nil, err
			}
			return d.finish(ctx, rs, a, out, nil)
		}
	}
	var out *Outcome
	var candidates []*verdict.Verdict
	if rs.Goal.Lane == LaneAgenda {
		out, candidates, err = d.agenda(ctx, rs, a, prev, forced)
	} else {
		out, err = d.execute(ctx, rs, n, prev, forced)
	}
	if err != nil {
		return nil, err
	}
	d.emit(rs, n, "execute", Executing, string(out.Terminal))
	if err := d.crash("after_execute"); err != nil {
		return nil, err
	}
	return d.finish(ctx, rs, a, out, candidates)
}

// finish judges, records, and delivers an attempt's execution outcome.
func (d *Driver) finish(ctx context.Context, rs *RunState, a *AttemptState, out *Outcome, candidates []*verdict.Verdict) (*Report, error) {
	n := a.Attempt.Attempt
	if rs.Goal.Lane == LaneNow && a.Attempt.Config.Judge == JudgeModel && out.Terminal != invoke.TerminalFailed && len(candidates) == 0 {
		v, err := d.nowClosureJudge(ctx, rs, a, out)
		if err != nil {
			return nil, err
		}
		if v != nil {
			candidates = append(candidates, v)
		}
	}
	// Judge — the self claim (NOW), or the closure judge's verdict plus the
	// self claim (AGENDA); observations arrive with the deterministic checks.
	self := SelfVerdict(rs.Run, n, out)
	if err := d.commit(ctx, fmt.Sprintf("verdict/%s/%d/self", rs.Run, n), self); err != nil {
		return nil, err
	}
	candidates = append(candidates, self)
	ev := make([]record.Ref, 0, len(candidates))
	for _, v := range candidates {
		ev = append(ev, record.Ref{Kind: verdict.KindVerdict, ID: string(v.ID)})
	}
	if err := d.transition(ctx, rs, a, Judged, "", ev); err != nil {
		return nil, err
	}
	if err := d.crash("after_judged"); err != nil {
		return nil, err
	}
	// Record — the resolution, then the execution outcome as a fold.
	res, err := verdict.Commit(ctx, d.J, rs.Run, n, verdict.Candidates{Subject: runRef(rs.Run), VerdictKind: verdict.KindClosure, Verdicts: candidates}, verdict.DefaultThresholds)
	if err != nil && !errors.Is(err, verdict.ErrAlreadyResolved) {
		return nil, err
	}
	rs.Closure = res
	out.Lane, out.GoalText, out.Closure, out.ClosureOut, out.ClosureCnf = rs.Goal.Lane, rs.Goal.Text, res.ID, res.Outcome, res.Confidence
	if res.Effective != "" {
		found := false
		for _, v := range candidates {
			if v.ID == res.Effective {
				out.ClosureSrc, found = string(v.Source.Standing), true
			}
		}
		if !found {
			return nil, fmt.Errorf("run: resolution %s names an effective verdict %s this driver did not commit", res.ID, res.Effective)
		}
	}
	if err := d.transition(ctx, rs, a, Recorded, "", nil, out); err != nil {
		return nil, err
	}
	// an interrupt still pending now arrived after the last boundary: the
	// execution is settled, so it expires — acknowledged, never left hanging
	if err := d.expireInterrupts(ctx, rs); err != nil {
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
			var out *Outcome
			switch {
			case st.Receipt != nil:
				resp := st.Receipt.Response
				out = &Outcome{Terminal: st.Terminal.State, Reason: st.Terminal.Reason, Invocation: st.Invocation.ID, Produced: p, Receipt: st.Receipt.ID, Response: &resp, Usage: st.Receipt.Usage, Model: model}
			case st.Terminal != nil && st.Terminal.State == invoke.TerminalFailed && !cancelled(st.Terminal.Reason):
				// the backend's own failure is reused; a failure the process's
				// shutdown caused (the context cancelled) is not the backend's
				// answer and the call runs again
				out = &Outcome{Terminal: invoke.TerminalFailed, Reason: "attempt " + fmt.Sprint(p) + ": " + st.Terminal.Reason, Invocation: st.Invocation.ID, Produced: p, Usage: st.Terminal.Usage, Model: model}
			case st.Reconciled != nil && st.Reconciled.Disposition == invoke.DispositionIndeterminate:
				out = &Outcome{Terminal: invoke.TerminalFailed, Reason: string(invoke.DispositionIndeterminate) + ": " + st.Reconciled.Evidence, Invocation: st.Invocation.ID, Produced: p, Model: model}
			}
			if out != nil {
				// the request was rendered from the recovered attempt's recall;
				// its applications may not have landed before the crash — they
				// are derivable from that selection, so commit them now
				if prev.Recall == nil {
					return nil, fmt.Errorf("run: attempt %d invoked without a recall selection", p)
				}
				out.Recall = prev.Recall.ID
				if err := d.apply(ctx, rs, p, prev.Recall, st.Invocation.ID); err != nil {
					return nil, err
				}
				return out, nil
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
	// Recall — one query over the learned fold, committed as the decision
	// BEFORE the request exists; the request is the goal plus the rendered
	// block, and every included revision becomes an Application once the
	// invocation it reached has an id. A recovered attempt that decided
	// but never invoked is CONTINUED: the same selection, not a new one.
	var continues *learn.RecallSelection
	if prev != nil && prev.Recall != nil && !invoked(prev) && d.sameRecallPolicy(rs, prev, n) {
		continues = prev.Recall
	}
	sel, block, reps, err := d.recall(ctx, rs, n, continues)
	if err != nil {
		return nil, err
	}
	if err := d.crash("after_recall"); err != nil {
		return nil, err
	}
	sh := &invoke.Shell{J: d.J, Store: d.Store, Run: rs.Run, Attempt: n, CrashAt: strings.TrimPrefix(d.CrashAt, "invoke:")}
	if !strings.HasPrefix(d.CrashAt, "invoke:") {
		sh.CrashAt = ""
	}
	prompt := append(append([]byte{}, text...), block...)
	o, err := sh.Invoke(ctx, d.Backend, invoke.Request{Purpose: invoke.PurposeExecute, Prompt: prompt, Tools: d.Backend.Capabilities().ActsOutward && !d.Confined, Timeout: d.Timeout}, nil)
	var inc *invoke.Incapable
	if errors.As(err, &inc) {
		// a refusal the shell makes BEFORE writing anything (input over the
		// backend's declared maximum): deterministic, so a retry can only
		// repeat it — record it as the attempt's honest failure (D16: the
		// thought is never sliced to fit; the route is what is lacking)
		return &Outcome{Terminal: invoke.TerminalFailed, Reason: "backend_incapable: " + err.Error(), Recall: sel.ID}, nil
	}
	if err != nil && !recordedFailure(o, err) {
		return nil, err
	}
	if err := d.applications(ctx, rs, n, o.Invocation, reps); err != nil {
		return nil, err
	}
	if err := d.crash("after_applications"); err != nil {
		return nil, err
	}
	out := &Outcome{Terminal: o.Terminal, Reason: o.Reason, Invocation: o.Invocation, Produced: n, Receipt: o.Receipt, Usage: o.Usage, Model: d.Backend.Capabilities().Model, Recall: sel.ID}
	if o.Terminal != invoke.TerminalFailed {
		ref, err := receiptResponse(d.J, o.Receipt)
		if err != nil {
			return nil, err
		}
		out.Response = ref
	}
	return out, nil
}

// sameRecallPolicy: a recovered attempt continues the earlier selection
// only when this attempt's policy says the same about recall; otherwise
// the new attempt decides afresh (the fold refuses a continuation across
// a policy change).
func (d *Driver) sameRecallPolicy(rs *RunState, prev *AttemptState, n uint32) bool {
	return prev.Attempt.Config.Mechanisms[learn.MechRecall] == rs.Attempts[n-1].Attempt.Config.Mechanisms[learn.MechRecall]
}

// scope is the run's memory scope chain: own goal → parents → root →
// workspace (§3). v1 runs one level deep; the chain is still walked.
func scope(g *Goal) []learn.ScopePath {
	chain := []learn.ScopePath{learn.ScopeGoal(g.ID)}
	if g.Parent != "" {
		chain = append(chain, learn.ScopeGoal(g.Parent))
	}
	if g.Root != g.ID && g.Root != g.Parent {
		chain = append(chain, learn.ScopeGoal(g.Root))
	}
	return append(chain, learn.ScopeWorkspace)
}

// interrupted consumes a pending interrupt for the run at a boundary: it
// acknowledges it and returns the honest failed outcome the run stops with.
// Nil when none is pending.
func (d *Driver) interrupted(ctx context.Context, rs *RunState, a *AttemptState, boundary string) (*Outcome, error) {
	var pending *Interrupt
	all, acked := []*Interrupt{}, map[record.RecordID]bool{}
	var cancel *CancellationIssued
	err := d.J.Production().Scan(0, func(r record.Record) error {
		switch x := r.(type) {
		case *Interrupt:
			if x.Target == rs.Run {
				all = append(all, x)
			}
		case *InterruptAck:
			acked[x.Interrupt] = true
		case *CancellationIssued:
			if x.Child.Run == rs.Run && cancel == nil {
				cancel = x
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if cancel != nil {
		// a join decision cancelled this child: it stops here, no ack record
		// (the ChildTerminal{cancelled} is the acknowledgement)
		d.emit(rs, a.Attempt.Attempt, "cancelled", Executing, boundary+": "+cancel.Reason)
		return &Outcome{Terminal: invoke.TerminalFailed, Reason: "cancelled by join at " + boundary + ": " + cancel.Reason}, nil
	}
	for _, it := range all { // the earliest still unacknowledged one
		if !acked[it.ID] {
			pending = it
			break
		}
	}
	if pending == nil {
		return nil, nil
	}
	n := a.Attempt.Attempt
	ack := &InterruptAck{Header: header(record.Ref{Kind: "interrupt", ID: string(pending.ID)}, rs.Run, n, "interrupt_ack/1"), Interrupt: pending.ID, Result: "consumed", Boundary: boundary}
	if err := d.commit(ctx, "interrupt/"+string(pending.ID)+"/ack", ack); err != nil {
		return nil, err
	}
	d.emit(rs, n, "interrupted", Executing, boundary+": "+pending.Why)
	return &Outcome{Terminal: invoke.TerminalFailed, Reason: "interrupted at " + boundary + ": " + pending.Why}, nil
}

// expireInterrupts acknowledges every pending interrupt of the run as
// expired: the run's execution is recorded, nothing is left to stop.
func (d *Driver) expireInterrupts(ctx context.Context, rs *RunState) error {
	var all []*Interrupt
	acked := map[record.RecordID]bool{}
	err := d.J.Production().Scan(0, func(r record.Record) error {
		switch x := r.(type) {
		case *Interrupt:
			if x.Target == rs.Run {
				all = append(all, x)
			}
		case *InterruptAck:
			acked[x.Interrupt] = true
		}
		return nil
	})
	if err != nil {
		return err
	}
	for _, it := range all {
		if acked[it.ID] {
			continue
		}
		ack := &InterruptAck{Header: record.Header{ID: record.NewID(), Schema: "interrupt_ack/1", Subject: record.Ref{Kind: "interrupt", ID: string(it.ID)}, At: now()}, Interrupt: it.ID, Result: "expired"}
		if err := d.commit(ctx, "interrupt/"+string(it.ID)+"/ack", ack); err != nil {
			return err
		}
	}
	return nil
}

// LatestPayload is the newest delivery's payload for a run handle: what a
// client that was gone can still read (`maro-go runs show`).
func LatestPayload(led *Ledger, store *thought.Store, handle string) ([]byte, *Mission, error) {
	for _, rs := range led.Runs {
		if HandleOf(rs.Run) != handle {
			continue
		}
		m := MissionOf(rs)
		a := rs.Latest()
		if a == nil || a.Delivery == nil {
			return nil, &m, fmt.Errorf("run %s has no delivery yet", handle)
		}
		b, err := store.Get(a.Delivery.Prepared.Payload)
		return b, &m, err
	}
	return nil, nil, fmt.Errorf("no run with handle %s", handle)
}

// recordedFailure says whether an Invoke error is the backend's own failure
// with its terminal committed (a recorded fact the run continues from as a
// failed execution), as opposed to a crash seam or a bookkeeping failure.
func recordedFailure(o *invoke.Outcome, err error) bool {
	return o != nil && o.Invocation != "" && o.Terminal == invoke.TerminalFailed && o.Err == nil && !errors.Is(err, invoke.ErrCrashed)
}

// cancelled reports a terminal reason the process's own shutdown produced.
func cancelled(reason string) bool {
	return strings.Contains(reason, "context canceled") || strings.Contains(reason, "context deadline exceeded")
}

// nowClosureJudge asks the closure judge about a NOW run's one response
// (the goal is its own single step) and returns the judge verdict.
func (d *Driver) nowClosureJudge(ctx context.Context, rs *RunState, a *AttemptState, out *Outcome) (*verdict.Verdict, error) {
	n := a.Attempt.Attempt
	if v := priorVerdictOf(a, verdict.KindClosure, runRef(rs.Run)); v != nil {
		return v, nil
	}
	goal, err := d.Store.Get(rs.Goal.Text)
	if err != nil {
		return nil, err
	}
	var resp []byte
	if out.Response != nil {
		resp, err = d.Store.Get(*out.Response)
		if err != nil {
			return nil, err
		}
	}
	sh := &invoke.Shell{J: d.J, Store: d.Store, Run: rs.Run, Attempt: n}
	req, err := d.lensedRequest(closurePrompt(goal, []string{string(goal)}, [][]byte{resp}, []bool{out.Terminal == invoke.TerminalPartial}), false)
	if err != nil {
		return nil, err
	}
	jo, err := sh.Invoke(ctx, d.judge(a), req, nil)
	if err != nil || jo.Err != nil || jo.Terminal == invoke.TerminalFailed {
		return nil, err
	}
	jr, perr := ParseJudge(jo.Response, "achieved", "not_achieved", "unknown")
	if perr != nil {
		d.emit(rs, n, "closure_unjudged", Executing, perr.Error())
		return nil, nil
	}
	v := &verdict.Verdict{Header: header(runRef(rs.Run), rs.Run, n, "verdict/1"), VerdictKind: verdict.KindClosure, Outcome: jr.Outcome, Confidence: jr.Confidence, Source: verdict.Source{Standing: verdict.StandingJudge, Ref: jo.Invocation}, Direction: verdict.Both, Basis: []record.Ref{{Kind: invoke.KindReceipt, ID: string(jo.Receipt)}}}
	for _, f := range jr.Falsifiers {
		if strings.TrimSpace(f) == "" {
			continue
		}
		ref, err := d.Store.Put(thought.Response, []byte(f))
		if err != nil {
			return nil, err
		}
		v.Falsifiers = append(v.Falsifiers, ref)
	}
	if err := d.commit(ctx, fmt.Sprintf("verdict/%s/%d/closure", rs.Run, n), v); err != nil {
		return nil, err
	}
	a.Verdicts = append(a.Verdicts, v)
	return v, nil
}

func priorVerdictOf(a *AttemptState, kind verdict.VerdictKind, subject record.Ref) *verdict.Verdict {
	for _, v := range a.Verdicts {
		if v.VerdictKind == kind && v.Source.Standing == verdict.StandingJudge && v.Subject == subject {
			return v
		}
	}
	return nil
}

// invoked reports whether an attempt made an execute invocation.
func invoked(a *AttemptState) bool {
	for _, st := range a.Invocations {
		if st.Invocation.Purpose == invoke.PurposeExecute {
			return true
		}
	}
	return false
}

// recall runs the one query for attempt n (or continues an earlier
// attempt's committed selection), commits it, and renders the block.
// Idempotent by (run, attempt).
func (d *Driver) recall(ctx context.Context, rs *RunState, n uint32, continues *learn.RecallSelection) (*learn.RecallSelection, []byte, []learn.Rendered, error) {
	led, err := learn.Fold(d.J.Production())
	if err != nil {
		return nil, nil, nil, err
	}
	cfg := rs.Attempts[n-1].Attempt.Config
	var sel *learn.RecallSelection
	if continues != nil {
		c := *continues
		sel = &c
		sel.Continues = continues.ID
	} else {
		family := ""
		if rs.Family.Family != FamilyNone {
			family = string(rs.Family.Family)
		}
		// the policy boundary's second consumer: MechRecall off = every
		// item excluded, the request is the goal alone
		sel = learn.Recall(led, learn.Query{Purpose: string(invoke.PurposeExecute), Scope: scope(rs.Goal), Family: family, Standing: learn.Selectable, Off: !cfg.Mechanisms[learn.MechRecall], Arm: rs.Goal.Arm})
	}
	sel.Header = header(runRef(rs.Run), rs.Run, n, "recall_selection/1")
	sel.Purpose = invoke.PurposeExecute
	sel.Policy = cfg.Policy
	if err := d.commit(ctx, fmt.Sprintf("recall/%s/%d", rs.Run, n), sel); err != nil {
		return nil, nil, nil, err
	}
	block, reps, err := d.render(led, sel)
	if err != nil {
		return nil, nil, nil, err
	}
	d.emit(rs, n, "recall", Executing, fmt.Sprintf("%d included of %d", len(sel.Included), sel.Considered))
	return sel, block, reps, nil
}

func (d *Driver) render(led *learn.Ledger, sel *learn.RecallSelection) ([]byte, []learn.Rendered, error) {
	return learn.Render(sel, func(ir learn.ItemRev) ([]byte, error) {
		it := led.Items[ir.Item]
		for _, r := range it.Revisions {
			if r.ID == ir.Revision {
				return d.Store.Get(r.Text)
			}
		}
		return nil, fmt.Errorf("run: recall names revision %s of %s that the fold does not hold", ir.Revision, ir.Item)
	})
}

// applications commits one Application per rendered revision, citing the
// invocation the representation reached. One command, keyed by invocation.
func (d *Driver) applications(ctx context.Context, rs *RunState, n uint32, inv record.RecordID, reps []learn.Rendered) error {
	if len(reps) == 0 {
		return nil
	}
	recs := make([]record.Record, 0, len(reps))
	for _, r := range reps {
		ref, err := d.Store.Put(thought.LessonText, r.Representation)
		if err != nil {
			return err
		}
		recs = append(recs, &learn.Application{Header: header(record.Ref{Kind: "invocation", ID: string(inv)}, rs.Run, n, "application/1"), Item: r.Item, Revision: r.Revision, Invocation: inv, Representation: ref})
	}
	if err := d.commit(ctx, "applications/"+string(inv), recs...); err != nil {
		return err
	}
	d.emit(rs, n, "applied", Executing, fmt.Sprintf("%d", len(reps)))
	return nil
}

// apply re-derives the applications for a recovered attempt's invocation
// from its committed recall selection (the render is deterministic, so the
// representations are the same bytes) and commits them if absent. Present
// applications must be exactly the re-derivation — item, revision, and
// representation, in order; anything else is journal evidence the driver
// could not have written, refused as such rather than repaired around.
func (d *Driver) apply(ctx context.Context, rs *RunState, n uint32, sel *learn.RecallSelection, inv record.RecordID) error {
	led, err := learn.Fold(d.J.Production())
	if err != nil {
		return err
	}
	_, reps, err := d.render(led, sel)
	if err != nil {
		return err
	}
	have := led.Applications[inv]
	if len(have) == 0 {
		return d.applications(ctx, rs, n, inv, reps)
	}
	if len(have) != len(reps) {
		return fmt.Errorf("%w: invocation %s has %d applications, its selection rendered %d", ErrIntegrity, inv, len(have), len(reps))
	}
	for i, r := range reps {
		if have[i].Item != r.Item || have[i].Revision != r.Revision || have[i].Representation != thought.Address(thought.LessonText, r.Representation) {
			return fmt.Errorf("%w: application %d on invocation %s is not the selection's rendering", ErrIntegrity, i, inv)
		}
	}
	return nil
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
	// the target is measured here, on the recorded usage, before the
	// delivery is prepared: the overage (if any) is a fact the delivery
	// line names, never a reason to stop (§11, D13)
	if err := d.meter(ctx, rs, a, rec); err != nil {
		return nil, err
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
		payload := Render(rec.Outcome, response)
		if rec.Outcome.Lane == LaneAgenda {
			p, err := d.renderAgendaOutcome(rec.Outcome, a)
			if err != nil {
				return nil, err
			}
			payload = p
		}
		if rs.Target != nil {
			payload = append(append(payload, "\n\n"...), MeteringLine(rs.Target, rec.Outcome.Usage, a.Overage)...)
		}
		ref, err := d.Store.Put(thought.Deliverable, payload)
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
	led, err := Fold(d.J.Production(), d.Store)
	if err != nil {
		return nil, err
	}
	var reports []*Report
	// goals taken in whose run never started: start one (the goal and its
	// assessment are the committed intake; nothing outcome-bearing ran)
	for _, g := range led.Unstarted {
		if g.Parent != "" {
			continue // a fork's child goal is started by the parent; a replay arm by the experiment runner
		}
		rep, err := d.StartGoal(ctx, led, g)
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
		if rs.Terminal() || rs.Goal.Parent != "" {
			continue // a child run is driven by its parent's fork step; a replay arm by the experiment runner
		}
		rep, err := d.ResumeRun(ctx, rs)
		if err != nil {
			return reports, err
		}
		reports = append(reports, rep)
	}
	return reports, nil
}

// StartGoal starts the run of a goal taken in whose run never started (the
// goal and its assessment are the committed intake; nothing outcome-bearing
// ran). Validate first.
func (d *Driver) StartGoal(ctx context.Context, led *Ledger, g *Goal) (*Report, error) {
	fam := led.Families[g.ID]
	if fam == nil {
		return nil, fmt.Errorf("run: goal %s has no family assessment", g.ID)
	}
	// the goal's envelope was committed with it: a run started after a
	// restart is measured against it exactly as a run started at intake
	rs := &RunState{Run: record.RunID(record.NewID()), Goal: g, Family: fam, Target: led.Targets[g.ID]}
	return d.drive(ctx, rs, nil, nil)
}

// ResumeRun drives one non-terminal run from its last committed stage: a
// recorded attempt is delivered; anything earlier is marked recoverable and
// a new attempt starts (or, past MaxAttempts, records the loop as its
// honest failure). Validate first.
func (d *Driver) ResumeRun(ctx context.Context, rs *RunState) (*Report, error) {
	a := rs.Latest()
	if a.Has(Recorded) != nil {
		return d.deliver(ctx, rs, a)
	}
	if a.Current() != Recoverable {
		if err := d.transition(ctx, rs, a, Recoverable, "process restarted before recorded (was "+string(a.Current())+")", nil); err != nil {
			return nil, err
		}
	}
	var forced *Outcome
	if len(rs.Attempts) >= d.MaxAttempts {
		forced = &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("attempt bound %d reached: %d attempts died before recorded", d.MaxAttempts, len(rs.Attempts))}
	}
	return d.drive(ctx, rs, a, forced)
}

// Validate checks the driver's configuration (the verbs call it before
// StartGoal/ResumeRun; Run and Resume call it themselves).
func (d *Driver) Validate() error { return d.validate() }
