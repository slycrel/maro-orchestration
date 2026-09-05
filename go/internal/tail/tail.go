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
	Every   time.Duration
	Timeout time.Duration
	Tick    <-chan struct{}
	// Events, when set, receives one line per attempt processed.
	Events func(string)
	// backlog is set by a pass that hit MaxPerPass: the next pass runs at once.
	backlog bool
}

const (
	// MaxProposals bounds proposals per attempt. Why 3: a run rarely
	// teaches more than a few things; past that the lens is padding. A
	// constant, not a field: the fold re-renders the lens prompt with it.
	MaxProposals = 3
	// LensTries bounds the diagnose calls for one attempt before the tail
	// closes it signals-only. Why 3: one failure is a blip (a rate limit,
	// a truncated stream) and costs a 20s wait to retry; three in a row is
	// the backend's answer, and a diagnosis with the signals alone is still
	// a diagnosis.
	LensTries = 3
	// MaxPerPass bounds the attempts one pass diagnoses. Why 5: at the 20s
	// cadence that is 15 a minute, more than any real run rate; a backlog
	// (a workspace with many recorded attempts and no tail yet) drains in
	// minutes while the heartbeat moves every pass.
	MaxPerPass = 5
)

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
		for {
			head, err := t.Pass(ctx)
			if err != nil {
				return err
			}
			hb.Progress(ctx, head)
			if !t.backlog || ctx.Err() != nil {
				break
			}
		}
	}
}

// Pass observes → diagnoses → proposes for every recorded attempt without
// a TailDone. Idempotent per (run, attempt). Returns the head processed.
func (t *Tail) Pass(ctx context.Context) (uint64, error) {
	pr := t.J.Production().Pin() // the run fold and the tail fold read ONE prefix
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
	sort.Strings(ids) // run ids are time-ordered: oldest first
	t.backlog = false
	n := 0
	for _, id := range ids {
		rs := led.Runs[record.RunID(id)]
		for _, a := range rs.Attempts {
			if terminalOf(a) == nil || tl.Done[key(rs.Run, a.Attempt.Attempt)] != nil {
				continue
			}
			if n == MaxPerPass {
				t.backlog = true
				return head, nil
			}
			n++
			if err := t.attempt(ctx, led, rs, a); err != nil {
				return 0, err
			}
		}
	}
	return head, nil
}

func key(r record.RunID, n uint32) string { return fmt.Sprintf("%s/%d", r, n) }

// terminalOf is the attempt's terminal transition (delivered or
// delivery_failed), nil while the attempt is still open. The tail reads
// only terminal attempts: the signals (delivery_failed, recovered) and
// the deliverable the lens is shown are not stable before it, and a
// diagnosis over inputs that then move cannot be re-derived from the
// journal (the review round's process test caught exactly that: a
// diagnosis between `recorded` and the prepared delivery).
func terminalOf(a *run.AttemptState) *run.Transition {
	for _, t := range a.Transitions {
		if t.To == run.Delivered || t.To == run.DeliveryFailedS {
			return t
		}
	}
	return nil
}

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
	if r.Class == string(ClassNone) && len(kept) > 0 {
		return r, fmt.Errorf("%w: class none (nothing failed) proposes nothing", run.ErrBoundary)
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

// skipReason is the derived reason a fork member is not learned from; ""
// for a member that is.
func skipReason(state run.ChildState) string {
	if state == run.ChildCancelled || state == run.ChildCompletedLate {
		return "fork member " + string(state) + ": not learned from"
	}
	return ""
}

// SkipReplay: an experiment arm (replay or live) is measured by the
// evaluator, never diagnosed or learned from — a lesson proposed from an
// arm would carry the hypothesis into production by the back door (§9).
const SkipReplay = "replay arm: not learned from"

// attempt is one attempt's tail, committed as one command (diagnosis,
// proposals, tail_done) after the lens call, if any. A failed or unusable
// lens call leaves the attempt open for the next pass until LensTries.
func (t *Tail) attempt(ctx context.Context, led *run.Ledger, rs *run.RunState, a *run.AttemptState) error {
	n := a.Attempt.Attempt
	k := fmt.Sprintf("tail/%s/%d", rs.Run, n)
	hd := func(schema record.SchemaVer) record.Header {
		return record.Header{ID: record.NewID(), Schema: schema, RunID: rs.Run, Attempt: n, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: now()}
	}
	if rs.Goal.Arm != nil || rs.Goal.Origin == run.OriginReplay {
		// a live arm is production for the user and an arm for the
		// evaluator: its treatment carried the hypothesis, so a lesson
		// minted from it would be the hypothesis by the back door
		return t.commit(ctx, k, &TailDone{Header: hd("tail_done/1"), Skipped: SkipReplay})
	}
	// a cancelled or late fork member is not learned from (§3)
	if rs.Goal.Origin == run.OriginFork {
		for _, fs := range led.Forks {
			if ct := fs.Terminals[rs.Run]; ct != nil && skipReason(ct.State) != "" {
				return t.commit(ctx, k, &TailDone{Header: hd("tail_done/1"), Skipped: skipReason(ct.State)})
			}
		}
	}
	unreadable := func(ref thought.Ref, err error) error {
		// the tail cannot judge what it cannot read: the attempt is closed
		// naming the thought, so one unreadable blob starves nothing else
		if t.Events != nil {
			t.Events(fmt.Sprintf("tail %s attempt %d: evidence %s unreadable: %v", run.HandleOf(rs.Run), n, ref.Hash, err))
		}
		return t.commit(ctx, k, &TailDone{Header: hd("tail_done/1"), Unreadable: &ref})
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
			return unreadable(rs.Goal.Text, err)
		}
		var deliverable []byte
		if a.Delivery != nil {
			deliverable, err = t.Store.Get(a.Delivery.Prepared.Payload)
			if err != nil {
				return unreadable(a.Delivery.Prepared.Payload, err)
			}
		}
		allowed := lensAllowed(sig)
		prompt := LensPrompt(goal, deliverable, rec, sig, allowed, MaxProposals)
		want := thought.Address(thought.Prompt, prompt)
		// the diagnose calls so far: one with a receipt that parses and that
		// asked exactly this prompt is reused; every other counts as a try
		var lr *LensResult
		var lensID record.RecordID
		tries, lastWhy := 0, ""
		for _, st := range a.Invocations {
			if st.Invocation.Purpose != invoke.PurposeDiagnose {
				continue
			}
			tries++
			switch {
			case st.Receipt == nil:
				lastWhy = "no receipt"
				if st.Terminal != nil {
					lastWhy = firstLine(st.Terminal.Reason)
				}
			case st.Invocation.Request != want:
				lastWhy = "asked with different evidence"
			default:
				b, err := t.Store.Get(st.Receipt.Response)
				if err != nil {
					return unreadable(st.Receipt.Response, err)
				}
				if r, perr := ParseLens(b, allowed, MaxProposals); perr != nil {
					lastWhy = firstLine(perr.Error())
				} else {
					lr, lensID = &r, st.Invocation.ID
				}
			}
		}
		if lr == nil && tries < LensTries {
			sh := &invoke.Shell{J: t.J, Store: t.Store, Run: rs.Run, Attempt: n}
			o, err := sh.Invoke(ctx, t.Lens, invoke.Request{Purpose: invoke.PurposeDiagnose, Prompt: prompt, Tools: false, Timeout: t.Timeout}, nil)
			if err != nil && !(o != nil && o.Terminal == invoke.TerminalFailed && o.Err == nil) {
				return err
			}
			tries++
			if o.Terminal == invoke.TerminalFailed {
				lastWhy = firstLine(o.Reason)
			} else if r, perr := ParseLens(o.Response, allowed, MaxProposals); perr != nil {
				lastWhy = firstLine(perr.Error())
			} else {
				lr, lensID = &r, o.Invocation
			}
		}
		switch {
		case lr != nil:
			diag.Lens, diag.LensRule, diag.Class, diag.Why = lensID, "lens", FailureClass(lr.Class), lr.Why
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
					Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeGoal(rs.Goal.Root), Family: family, Text: ref, Provenance: learn.Provenance{Source: "tail", Ref: diag.ID, Why: lr.Why}}
				proposals = append(proposals, rev)
				proposalIDs = append(proposalIDs, rev.ID)
			}
		case tries < LensTries:
			// a blip is not the run's fault: leave the attempt open and try
			// again next pass (the failed call stays as evidence)
			if t.Events != nil {
				t.Events(fmt.Sprintf("tail %s attempt %d: lens try %d of %d unusable (%s); again next pass", run.HandleOf(rs.Run), n, tries, LensTries, lastWhy))
			}
			return nil
		default:
			diag.LensRule = "no_lens:" + lastWhy + fmt.Sprintf(" (after %d tries)", tries)
		}
	}
	done := &TailDone{Header: hd("tail_done/1"), Diagnosis: diag.ID, Proposals: proposalIDs}
	recs := append([]record.Record{diag}, proposals...)
	recs = append(recs, done)
	if err := t.commit(ctx, k, recs...); err != nil {
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
// signals alone) establishes, the lens must have been asked THIS
// attempt's diagnose prompt; a tail_done names its diagnosis and the lens
// response's proposals, complete and in order, as tail-provenance
// revisions citing it — or a skip that the fork's terminal for the run
// derives, or an unreadable thought the attempt's evidence names; every
// tail-provenance revision is claimed by a tail_done.
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
	var tailRevs []*learn.LearnedRevision
	claimed := map[record.RecordID]bool{}
	lensProposals := map[record.RecordID][]string{}
	attemptOf := func(kind string, h record.Header, r record.RunID, n uint32) (*run.RunState, *run.AttemptState, error) {
		rs := led.Runs[r]
		if rs == nil || n == 0 || int(n) > len(rs.Attempts) {
			return nil, nil, fmt.Errorf("tail: %s %s for an unknown attempt", kind, h.ID)
		}
		a := rs.Attempts[n-1]
		// a tail record is legal only after the attempt's terminal
		// transition: that is when its inputs stopped moving, so a
		// re-derivation reads what the tail read
		if term := terminalOf(a); term == nil || h.Seq <= term.Seq {
			return nil, nil, fmt.Errorf("tail: %s %s before the attempt was terminal", kind, h.ID)
		}
		return rs, a, nil
	}
	err = pr.Scan(0, func(r record.Record) error {
		switch x := r.(type) {
		case *learn.LearnedRevision:
			revs[x.ID] = x
			if x.Provenance.Source == "tail" {
				tailRevs = append(tailRevs, x)
			}
		case *Diagnosis:
			rs, a, err := attemptOf("diagnosis", x.Header, x.RunID, x.Attempt)
			if err != nil {
				return err
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
				// the lens was asked exactly this attempt's diagnose prompt
				goal, err := store.Get(rs.Goal.Text)
				if err != nil {
					return err
				}
				var deliverable []byte
				if a.Delivery != nil {
					if deliverable, err = store.Get(a.Delivery.Prepared.Payload); err != nil {
						return err
					}
				}
				rec := a.Has(run.Recorded).Outcome
				prompt := LensPrompt(goal, deliverable, rec, want, lensAllowed(want), MaxProposals)
				if st.Invocation.Request != thought.Address(thought.Prompt, prompt) {
					return fmt.Errorf("tail: diagnosis %s cites lens %s that was not asked this attempt's diagnose prompt", x.ID, x.Lens)
				}
				resp, err := store.Get(st.Receipt.Response)
				if err != nil {
					return err
				}
				lr, perr := ParseLens(resp, lensAllowed(want), MaxProposals)
				if perr != nil || lr.Class != string(x.Class) || lr.Why != x.Why {
					return fmt.Errorf("tail: diagnosis %s does not re-derive from its lens response (%v)", x.ID, perr)
				}
				lensProposals[x.ID] = lr.Proposals
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
			rs, a, err := attemptOf("tail_done", x.Header, x.RunID, x.Attempt)
			if err != nil {
				return err
			}
			switch {
			case x.Diagnosis != "":
				d := tl.Diagnoses[x.Diagnosis]
				if d == nil || d.RunID != x.RunID || d.Attempt != x.Attempt {
					return fmt.Errorf("tail: tail_done %s names diagnosis %s that is not this attempt's", x.ID, x.Diagnosis)
				}
				// the proposals are the lens response's: complete, in order, as lessons
				want := lensProposals[d.ID]
				if len(x.Proposals) != len(want) {
					return fmt.Errorf("tail: tail_done %s names %d proposals; the lens response has %d", x.ID, len(x.Proposals), len(want))
				}
				family := ""
				if rs.Family.Family != run.FamilyNone {
					family = string(rs.Family.Family)
				}
				for i, p := range x.Proposals {
					rev := revs[p]
					if rev == nil || rev.Provenance.Source != "tail" || rev.Provenance.Ref != x.Diagnosis || claimed[p] {
						return fmt.Errorf("tail: tail_done %s proposal %s is not a tail revision citing its diagnosis", x.ID, p)
					}
					if rev.LearnedKind != learn.Lesson || rev.Scope != learn.ScopeGoal(rs.Goal.Root) || rev.Family != family || rev.Text != thought.Address(thought.LessonText, []byte(want[i])) {
						return fmt.Errorf("tail: tail_done %s proposal %s is not the lens response's proposal %d as a workspace lesson of the run's family", x.ID, p, i+1)
					}
					claimed[p] = true
				}
			case x.Skipped == SkipReplay:
				if rs.Goal.Origin != run.OriginReplay && rs.Goal.Arm == nil {
					return fmt.Errorf("tail: tail_done %s skips an attempt as an experiment arm, which it is not", x.ID)
				}
			case x.Skipped != "":
				if rs.Goal.Origin != run.OriginFork {
					return fmt.Errorf("tail: tail_done %s skips an attempt that is not a fork member's", x.ID)
				}
				ok := false
				for _, fs := range led.Forks {
					if ct := fs.Terminals[rs.Run]; ct != nil && skipReason(ct.State) == x.Skipped {
						ok = true
					}
				}
				if !ok {
					return fmt.Errorf("tail: tail_done %s skip %q does not derive from the fork's terminal for the run", x.ID, x.Skipped)
				}
			case x.Unreadable != nil:
				ok := *x.Unreadable == rs.Goal.Text || (a.Delivery != nil && *x.Unreadable == a.Delivery.Prepared.Payload)
				for _, is := range a.Invocations {
					if is.Invocation.Purpose == invoke.PurposeDiagnose && is.Receipt != nil && is.Receipt.Response == *x.Unreadable {
						ok = true
					}
				}
				if !ok {
					return fmt.Errorf("tail: tail_done %s names an unreadable thought that is not the attempt's evidence", x.ID)
				}
			}
			tl.Done[k] = x
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	for _, rev := range tailRevs {
		if !claimed[rev.ID] {
			return nil, fmt.Errorf("tail: revision %s claims tail provenance but no tail_done proposes it", rev.ID)
		}
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
