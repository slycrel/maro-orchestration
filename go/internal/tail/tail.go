package tail

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/supervise"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// Tail is the supervised lane (stage 2) that learns from recorded runs.
type Tail struct {
	J     *journal.Journal
	Store *thought.Store
	// Lens is the tool-less backend for the diagnose call; nil = signals only.
	Lens invoke.Backend
	// Every is the pass interval. Why 20s: a run's tail is not urgent; the
	// executor wakes nothing here, and a pass over a quiet journal is cheap.
	Every time.Duration
	// MaxProposals bounds proposals per attempt. Why 3: a run rarely
	// teaches more than a few things; past that the lens is padding.
	MaxProposals int
	Timeout      time.Duration
	Tick         <-chan struct{}
	// Events, when set, receives one line per attempt processed.
	Events func(string)
}

func (t *Tail) Name() string          { return "tail" }
func (t *Tail) Stage() int            { return 2 }
func (t *Tail) Expect() time.Duration { return 3 * t.every() }

func (t *Tail) every() time.Duration {
	if t.Every == 0 {
		return 20 * time.Second
	}
	return t.Every
}

func (t *Tail) Run(ctx context.Context, hb *supervise.Heartbeat) error {
	var ticks <-chan time.Time
	if t.Tick == nil {
		tk := time.NewTicker(t.every())
		defer tk.Stop()
		ticks = tk.C
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticks:
		case <-t.Tick:
		}
		head, err := t.Pass(ctx)
		if err != nil {
			return err
		}
		hb.Progress(ctx, head)
	}
}

// Pass observes → diagnoses → proposes for every recorded attempt without
// a TailDone. Idempotent per (run, attempt). Returns the head processed.
func (t *Tail) Pass(ctx context.Context) (uint64, error) {
	pr := t.J.Production()
	head := pr.Head()
	led, err := run.Fold(pr, t.Store)
	if err != nil {
		return 0, err
	}
	tl, err := Fold(pr, t.Store)
	if err != nil {
		return 0, err
	}
	ids := make([]string, 0, len(led.Runs))
	for id := range led.Runs {
		ids = append(ids, string(id))
	}
	sort.Strings(ids)
	for _, id := range ids {
		rs := led.Runs[record.RunID(id)]
		for _, a := range rs.Attempts {
			if a.Has(run.Recorded) == nil || tl.Done[key(rs.Run, a.Attempt.Attempt)] != nil {
				continue
			}
			if err := t.attempt(ctx, led, rs, a); err != nil {
				return 0, err
			}
		}
	}
	return head, nil
}

func key(r record.RunID, n uint32) string { return fmt.Sprintf("%s/%d", r, n) }

// Signals is the deterministic classifier: pure over the folded run.
func Signals(led *run.Ledger, rs *run.RunState, a *run.AttemptState) []Signal {
	var out []Signal
	rec := a.Has(run.Recorded).Outcome
	add := func(s Signal) { out = append(out, s) }
	if a.Intent != nil && !a.Intent.Clear {
		add(SignalUnclearGoal)
	}
	if rec.Terminal == invoke.TerminalFailed {
		switch {
		case strings.Contains(rec.Reason, "cancelled by join") || strings.Contains(rec.Reason, "interrupted at"):
			add(SignalInterrupted)
		case strings.HasPrefix(rec.Reason, "blocked at step"):
			add(SignalBlockedStep)
		case strings.HasPrefix(rec.Reason, "needs clarification"):
		default:
			add(SignalBackendFailed)
		}
	}
	if rec.Terminal == invoke.TerminalPartial {
		add(SignalPartialOutput)
	}
	for _, sd := range a.Steps {
		if sd.Outcome == run.StepBlocked && rec.Terminal != invoke.TerminalFailed {
			add(SignalBlockedStep)
			break
		}
	}
	switch rec.ClosureOut {
	case "unknown":
		add(SignalUnjudged)
	case "not_achieved":
		add(SignalNotAchieved)
	}
	if a.Current() == run.DeliveryFailedS {
		add(SignalDeliveryFailed)
	}
	if a.Attempt.Attempt > 1 {
		add(SignalRecovered)
	}
	if a.Stuck != nil {
		add(SignalStuck)
	}
	for _, st := range a.Invocations {
		for _, e := range st.Effects {
			if e.Refused {
				add(SignalConfined)
				break
			}
		}
	}
	return out
}

// classFromSignals is the class the signals alone establish (the strongest
// first); the lens may only replace `none` with an answer-quality class or
// confirm a signal's class.
func classFromSignals(sig []Signal) FailureClass {
	order := []struct {
		s Signal
		c FailureClass
	}{{SignalBackendFailed, ClassBackendFailure}, {SignalInterrupted, ClassInterrupted}, {SignalStuck, ClassStuck}, {SignalBlockedStep, ClassBlockedStep}, {SignalUnclearGoal, ClassUnclearGoal}, {SignalDeliveryFailed, ClassDeliveryFailed}, {SignalPartialOutput, ClassPartialOutput}, {SignalNotAchieved, ClassNotAchieved}, {SignalUnjudged, ClassUnjudged}}
	has := map[Signal]bool{}
	for _, s := range sig {
		has[s] = true
	}
	for _, o := range order {
		if has[o.s] {
			return o.c
		}
	}
	return ClassNone
}

// lensAllowed says which classes the lens may name given the signals: the
// signal-established class, or (when nothing failed mechanically) `none`,
// `wrong_answer`, `incomplete_answer`.
func lensAllowed(sig []Signal) []string {
	c := classFromSignals(sig)
	if c == ClassNone || c == ClassUnjudged || c == ClassNotAchieved {
		return []string{string(c), "none", "wrong_answer", "incomplete_answer"}
	}
	return []string{string(c)}
}

// LensResult is the diagnose boundary's product.
type LensResult struct {
	Class     string   `json:"class"`
	Why       string   `json:"why"`
	Proposals []string `json:"proposals"` // lesson texts, each one sentence, general enough to help a later run
}

// ParseLens validates the lens response once.
func ParseLens(response []byte, allowed []string, max int) (LensResult, error) {
	var r LensResult
	dec := json.NewDecoder(strings.NewReader(unfence(response)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&r); err != nil {
		return r, fmt.Errorf("%w: %v", run.ErrBoundary, err)
	}
	if dec.More() {
		return r, fmt.Errorf("%w: trailing content", run.ErrBoundary)
	}
	ok := false
	for _, a := range allowed {
		if r.Class == a {
			ok = true
		}
	}
	if !ok {
		return r, fmt.Errorf("%w: class %q not in %v", run.ErrBoundary, r.Class, allowed)
	}
	if strings.TrimSpace(r.Why) == "" {
		return r, fmt.Errorf("%w: a diagnosis carries its why", run.ErrBoundary)
	}
	var kept []string
	for _, p := range r.Proposals {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		kept = append(kept, p)
	}
	if len(kept) > max {
		return r, fmt.Errorf("%w: %d proposals, at most %d", run.ErrBoundary, len(kept), max)
	}
	r.Proposals = kept
	return r, nil
}

func unfence(b []byte) string {
	s := strings.TrimSpace(string(b))
	if strings.HasPrefix(s, "```") {
		s = strings.TrimPrefix(s, "```json")
		s = strings.TrimPrefix(s, "```")
		s = strings.TrimSuffix(strings.TrimSpace(s), "```")
	}
	return strings.TrimSpace(s)
}

// LensPrompt is the diagnose request: the goal, the outcome as recorded,
// the deliverable whole, the signals, and the classes the lens may name.
func LensPrompt(goal, deliverable []byte, rec *run.Outcome, sig []Signal, allowed []string, max int) []byte {
	var b strings.Builder
	b.WriteString("You are the diagnosis lens of an orchestration engine, looking at ONE finished run. Name the single failure class that best describes it, say why in one sentence, and propose up to " + fmt.Sprint(max) + " lessons (none if there is nothing to learn).\n")
	b.WriteString("A lesson is ONE sentence of instruction to the model that will answer a LATER goal like this one, placed verbatim in its request — about how to answer (what to state, check, include, avoid), never about the engine, its judges, its confidence numbers, or what a human should do.\n")
	b.WriteString("Reply with ONE JSON object and nothing else: {\"class\": <one of " + strings.Join(allowed, "|") + ">, \"why\": \"<one sentence>\", \"proposals\": [\"<lesson>\", ...]}\n\n## Goal\n")
	b.Write(goal)
	fmt.Fprintf(&b, "\n\n## Outcome\nterminal: %s\nclosure: %s (source %s, confidence %v)\n", rec.Terminal, rec.ClosureOut, rec.ClosureSrc, rec.ClosureCnf)
	if rec.Reason != "" {
		fmt.Fprintf(&b, "reason: %s\n", rec.Reason)
	}
	fmt.Fprintf(&b, "signals: %v\n\n## Deliverable\n", sig)
	b.Write(deliverable)
	b.WriteString("\n")
	return []byte(b.String())
}

// attempt is one attempt's tail, committed as one command (diagnosis,
// proposals, tail_done) after the lens call, if any.
func (t *Tail) attempt(ctx context.Context, led *run.Ledger, rs *run.RunState, a *run.AttemptState) error {
	n := a.Attempt.Attempt
	hd := func(schema record.SchemaVer) record.Header {
		return record.Header{ID: record.NewID(), Schema: schema, RunID: rs.Run, Attempt: n, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: now()}
	}
	// a cancelled or late fork member is not learned from (§3): no learning from a cancelled arm
	if rs.Goal.Origin == run.OriginFork {
		for _, fs := range led.Forks {
			if ct := fs.Terminals[rs.Run]; ct != nil && (ct.State == run.ChildCancelled || ct.State == run.ChildCompletedLate) {
				return t.commit(ctx, fmt.Sprintf("tail/%s/%d", rs.Run, n), &TailDone{Header: hd("tail_done/1"), Skipped: "fork member " + string(ct.State) + ": not learned from"})
			}
		}
	}
	rec := a.Has(run.Recorded).Outcome
	sig := Signals(led, rs, a)
	diag := &Diagnosis{Header: hd("diagnosis/1"), Signals: sig, Class: classFromSignals(sig), LensRule: "signals_only"}
	diag.Why = "from the signals"
	var proposals []record.Record
	var proposalIDs []record.RecordID
	if t.Lens != nil {
		goal, err := t.Store.Get(rs.Goal.Text)
		if err != nil {
			return err
		}
		var deliverable []byte
		if a.Delivery != nil {
			deliverable, err = t.Store.Get(a.Delivery.Prepared.Payload)
			if err != nil {
				return err
			}
		}
		allowed := lensAllowed(sig)
		max := t.MaxProposals
		if max == 0 {
			max = 3
		}
		// reuse a diagnose call the previous pass left with a receipt
		var o *invoke.Outcome
		for _, st := range a.Invocations {
			if st.Invocation.Purpose == invoke.PurposeDiagnose && st.Receipt != nil {
				b, err := t.Store.Get(st.Receipt.Response)
				if err != nil {
					return err
				}
				o = &invoke.Outcome{Invocation: st.Invocation.ID, Receipt: st.Receipt.ID, Terminal: st.Terminal.State, Response: b}
			}
		}
		if o == nil {
			sh := &invoke.Shell{J: t.J, Store: t.Store, Run: rs.Run, Attempt: n}
			var err error
			o, err = sh.Invoke(ctx, t.Lens, invoke.Request{Purpose: invoke.PurposeDiagnose, Prompt: LensPrompt(goal, deliverable, rec, sig, allowed, max), Tools: false, Timeout: t.Timeout}, nil)
			if err != nil && !(o != nil && o.Terminal == invoke.TerminalFailed && o.Err == nil) {
				return err
			}
		}
		if o.Terminal == invoke.TerminalFailed {
			diag.LensRule = "no_lens:" + firstLine(o.Reason)
		} else {
			lr, perr := ParseLens(o.Response, allowed, max)
			if perr != nil {
				diag.LensRule = "no_lens:" + firstLine(perr.Error())
			} else {
				diag.Lens, diag.LensRule, diag.Class, diag.Why = o.Invocation, "lens", FailureClass(lr.Class), lr.Why
				family := ""
				if rs.Family.Family != run.FamilyNone {
					family = string(rs.Family.Family)
				}
				for _, text := range lr.Proposals {
					ref, err := t.Store.Put(thought.LessonText, []byte(text))
					if err != nil {
						return err
					}
					item := learn.LearnedID(record.NewID())
					rev := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now()},
						Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Family: family, Text: ref, Provenance: learn.Provenance{Source: "tail", Ref: diag.ID, Why: lr.Why}}
					proposals = append(proposals, rev)
					proposalIDs = append(proposalIDs, rev.ID)
				}
			}
		}
	}
	done := &TailDone{Header: hd("tail_done/1"), Diagnosis: diag.ID, Proposals: proposalIDs}
	recs := append([]record.Record{diag}, proposals...)
	recs = append(recs, done)
	if err := t.commit(ctx, fmt.Sprintf("tail/%s/%d", rs.Run, n), recs...); err != nil {
		return err
	}
	if t.Events != nil {
		t.Events(fmt.Sprintf("tail %s attempt %d: %s (%s) %d proposal(s)", run.HandleOf(rs.Run), n, diag.Class, diag.LensRule, len(proposals)))
	}
	return nil
}

func (t *Tail) commit(ctx context.Context, key string, recs ...record.Record) error {
	_, err := t.J.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: t.J.Epoch(), Records: recs})
	return err
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	if len(s) > 120 {
		s = s[:120]
	}
	return s
}

// Ledger is the fold of the tail's records, re-derived: a diagnosis must
// carry exactly the signals the run shows and a class its lens (or the
// signals alone) establishes; a tail_done names its diagnosis and
// proposals that are tail-provenance revisions citing it.
type Ledger struct {
	Done      map[string]*TailDone
	Diagnoses map[record.RecordID]*Diagnosis
}

// Fold folds the tail records against the run and learned folds.
func Fold(pr *journal.ProductionReader, store *thought.Store) (*Ledger, error) {
	pr = pr.Pin() // one prefix for every scan this fold composes
	led, err := run.Fold(pr, store)
	if err != nil {
		return nil, err
	}
	tl := &Ledger{Done: map[string]*TailDone{}, Diagnoses: map[record.RecordID]*Diagnosis{}}
	revs := map[record.RecordID]*learn.LearnedRevision{}
	err = pr.Scan(0, func(r record.Record) error {
		switch x := r.(type) {
		case *learn.LearnedRevision:
			revs[x.ID] = x
		case *Diagnosis:
			rs := led.Runs[x.RunID]
			if rs == nil || int(x.Attempt) > len(rs.Attempts) {
				return fmt.Errorf("tail: diagnosis %s for an unknown attempt", x.ID)
			}
			a := rs.Attempts[x.Attempt-1]
			if a.Has(run.Recorded) == nil {
				return fmt.Errorf("tail: diagnosis %s before the attempt was recorded", x.ID)
			}
			want := Signals(led, rs, a)
			if !sameSignals(want, x.Signals) {
				return fmt.Errorf("tail: diagnosis %s signals %v do not re-derive (%v)", x.ID, x.Signals, want)
			}
			switch {
			case x.Lens != "":
				var st *invoke.State
				for _, is := range a.Invocations {
					if is.Invocation.ID == x.Lens {
						st = is
					}
				}
				if st == nil || st.Invocation.Purpose != invoke.PurposeDiagnose || st.Receipt == nil || st.Invocation.Tools {
					return fmt.Errorf("tail: diagnosis %s cites lens %s that is not a tool-less diagnose call of the attempt with a receipt", x.ID, x.Lens)
				}
				resp, err := store.Get(st.Receipt.Response)
				if err != nil {
					return err
				}
				lr, perr := ParseLens(resp, lensAllowed(want), 1000)
				if perr != nil || lr.Class != string(x.Class) || lr.Why != x.Why {
					return fmt.Errorf("tail: diagnosis %s does not re-derive from its lens response (%v)", x.ID, perr)
				}
			default:
				if x.Class != classFromSignals(want) {
					return fmt.Errorf("tail: diagnosis %s class %s is not the signals' class %s", x.ID, x.Class, classFromSignals(want))
				}
			}
			tl.Diagnoses[x.ID] = x
		case *TailDone:
			k := key(x.RunID, x.Attempt)
			if tl.Done[k] != nil {
				return fmt.Errorf("tail: attempt %s done twice", k)
			}
			if x.Diagnosis != "" {
				d := tl.Diagnoses[x.Diagnosis]
				if d == nil || d.RunID != x.RunID || d.Attempt != x.Attempt {
					return fmt.Errorf("tail: tail_done %s names diagnosis %s that is not this attempt's", x.ID, x.Diagnosis)
				}
				for _, p := range x.Proposals {
					rev := revs[p]
					if rev == nil || rev.Provenance.Source != "tail" || rev.Provenance.Ref != x.Diagnosis {
						return fmt.Errorf("tail: tail_done %s proposal %s is not a tail revision citing its diagnosis", x.ID, p)
					}
				}
			}
			tl.Done[k] = x
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return tl, nil
}

func sameSignals(a, b []Signal) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

var _ = verdict.ResolverVer
var _ = errors.New
