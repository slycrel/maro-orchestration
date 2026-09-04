package invoke

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Shell owns the invocation state machine. It is the ONLY way a backend is
// called: every transition is a journal command, so a crash at any point
// leaves evidence the next start can read.
type Shell struct {
	J       *journal.Journal
	Store   *thought.Store
	Run     record.RunID
	Attempt uint32
}

// Outcome is what Invoke returns to the driver.
type Outcome struct {
	Invocation record.RecordID
	Receipt    record.RecordID
	Terminal   TerminalState
	Reason     string
	Response   []byte
	Usage      Usage
	Effects    []record.RecordID
}

var (
	ErrTargetWhy = errors.New("invoke: a target needs a why")
)

type sink struct {
	sh      *Shell
	inv     *Invocation
	ctx     context.Context // detached: evidence is committed even after the caller's deadline
	ordinal int
	effects []record.RecordID
	err     error
}

func (s *sink) Effect(_ context.Context, ev EffectEvent) (string, error) {
	if s.err != nil {
		return "", s.err
	}
	ctx := s.ctx
	if ctx == nil {
		ctx = context.Background()
	}
	evidence, _ := json.Marshal(map[string]any{"op": ev.Op, "input": json.RawMessage(nonEmpty(ev.Input)), "output": string(ev.Output), "is_error": ev.IsError, "tool_call": ev.ToolCall})
	ref, err := s.sh.Store.Put(thought.StepResult, evidence)
	if err != nil {
		s.err = err
		return "", err
	}
	key := DeriveKey(s.inv.EffectToken, s.ordinal)
	te := &ToolEffect{Header: s.sh.header(record.Ref{Kind: KindInvocation, ID: string(s.inv.ID)}),
		Invocation: s.inv.ID, Ordinal: s.ordinal, Op: ev.Op, Class: ClassOf(ev.Op), Key: key,
		Announced: s.inv.Backend.OutwardReconcilable, IsError: ev.IsError, Evidence: ref}
	if _, err := s.sh.commit(ctx, fmt.Sprintf("%s:effect:%d", s.inv.ID, s.ordinal), te); err != nil {
		s.err = err
		return "", err
	}
	s.effects = append(s.effects, te.ID)
	s.ordinal++
	return key, nil
}

func nonEmpty(b []byte) []byte {
	if len(b) == 0 {
		return []byte("null")
	}
	return b
}

func (sh *Shell) header(subject record.Ref) record.Header {
	return record.Header{ID: record.NewID(), RunID: sh.Run, Attempt: sh.Attempt, Subject: subject, At: time.Now().UTC()}
}

func (sh *Shell) commit(ctx context.Context, key string, recs ...record.Record) (journal.Ack, error) {
	for _, r := range recs {
		spec, _ := record.Lookup(r.Kind())
		r.Head().Schema = record.SchemaVer(fmt.Sprintf("%s/%d", r.Kind(), spec.Version))
	}
	return sh.J.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: sh.J.Epoch(), Records: recs})
}

// Target is an optional metering target (D13): a name, a limit, and the
// reason it exists. Never a stop.
type Target struct {
	Name  string
	Limit int64
	Why   string
}

// Invoke runs the state machine: store the request → commit prepared →
// commit dispatched → run the backend, committing each effect as reported →
// commit terminal_observed → store the response → commit the receipt.
func (sh *Shell) Invoke(ctx context.Context, b Backend, req Request, target *Target) (*Outcome, error) {
	if !purposes[req.Purpose] {
		return nil, fmt.Errorf("invoke: purpose %q out of vocabulary", req.Purpose)
	}
	if target != nil && (target.Name == "" || target.Why == "") {
		return nil, ErrTargetWhy
	}
	reqRef, err := sh.Store.Put(thought.Prompt, req.Prompt)
	if err != nil {
		return nil, err
	}
	token, err := NewEffectToken()
	if err != nil {
		return nil, err
	}
	inv := &Invocation{Header: sh.header(record.Ref{Kind: "prompt", ID: reqRef.Hash}), Purpose: req.Purpose, Request: reqRef, Backend: b.Capabilities(), EffectToken: token}
	if target != nil {
		inv.TargetName, inv.TargetLimit, inv.TargetWhy = target.Name, target.Limit, target.Why
	}
	if _, err := sh.commit(ctx, string(inv.ID)+":prepared", inv); err != nil {
		return nil, err
	}
	out := &Outcome{Invocation: inv.ID}
	if _, err := sh.commit(ctx, string(inv.ID)+":dispatched", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID}); err != nil {
		return nil, err
	}
	// From dispatch on, the state machine's bookkeeping must complete even if
	// the caller's context dies: a timeout is a terminal FACT to record, not
	// a reason to leave the invocation orphaned.
	book := context.WithoutCancel(ctx)
	sk := &sink{sh: sh, inv: inv, ctx: book}
	res, berr := b.Complete(ctx, req, sk)
	ctx = book
	out.Effects = sk.effects
	if berr != nil {
		// Failed before dispatch (by contract): record the terminal so the
		// invocation does not look orphaned, then report.
		term := &TerminalObserved{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, State: TerminalFailed, Reason: "before dispatch: " + berr.Error()}
		if _, err := sh.commit(ctx, string(inv.ID)+":terminal", term); err != nil {
			return nil, err
		}
		out.Terminal, out.Reason = TerminalFailed, term.Reason
		return out, berr
	}
	if sk.err != nil && res.Terminal == TerminalComplete {
		res.Terminal, res.Reason = TerminalPartial, "effect recording failed: "+sk.err.Error()
	}
	term := &TerminalObserved{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, State: res.Terminal, Reason: res.Reason}
	if len(res.Transcript) > 0 {
		if tref, err := sh.Store.Put(thought.Response, res.Transcript); err == nil {
			term.Transcript = &tref
		}
	}
	if _, err := sh.commit(ctx, string(inv.ID)+":terminal", term); err != nil {
		return nil, err
	}
	out.Terminal, out.Reason, out.Usage = res.Terminal, res.Reason, res.Usage
	if res.Terminal == TerminalFailed {
		return out, nil
	}
	respRef, err := sh.Store.Put(thought.Response, res.Response)
	if err != nil {
		return nil, err
	}
	rc := &Receipt{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, Response: respRef, Usage: res.Usage}
	if _, err := sh.commit(ctx, string(inv.ID)+":receipt", rc); err != nil {
		return nil, err
	}
	out.Receipt, out.Response = rc.ID, res.Response
	return out, nil
}

func subj(id record.RecordID) record.Ref {
	return record.Ref{Kind: KindInvocation, ID: string(id)}
}

// State is the fold over one invocation's records.
type State struct {
	Invocation *Invocation
	Dispatched bool
	Effects    []*ToolEffect
	Terminal   *TerminalObserved
	Receipt    *Receipt
	Reconciled *Reconciled
}

// Phase names the state machine position.
func (s *State) Phase() string {
	switch {
	case s.Invocation == nil:
		return "unknown"
	case s.Reconciled != nil:
		return "reconciled:" + string(s.Reconciled.Disposition)
	case s.Receipt != nil:
		return "receipt_committed"
	case s.Terminal != nil:
		return "terminal_observed"
	case s.Dispatched:
		return "dispatched"
	default:
		return "prepared"
	}
}

// Fold reads every invocation's state from the production journal.
func Fold(pr *journal.ProductionReader) (map[record.RecordID]*State, error) {
	states := map[record.RecordID]*State{}
	get := func(id record.RecordID) *State {
		s, ok := states[id]
		if !ok {
			s = &State{}
			states[id] = s
		}
		return s
	}
	err := pr.Scan(0, func(r record.Record) error {
		switch v := r.(type) {
		case *Invocation:
			get(v.ID).Invocation = v
		case *Dispatched:
			get(v.Invocation).Dispatched = true
		case *ToolEffect:
			s := get(v.Invocation)
			s.Effects = append(s.Effects, v)
		case *TerminalObserved:
			get(v.Invocation).Terminal = v
		case *Receipt:
			get(v.Invocation).Receipt = v
		case *Reconciled:
			get(v.Invocation).Reconciled = v
		}
		return nil
	})
	return states, err
}

// Reconcile is the restart pass: every invocation found dispatched without a
// terminal gets a disposition. A backend that cannot act outward ⇒ abandoned
// (safe to retry). Otherwise ⇒ indeterminate: absence of committed effects is
// not evidence of purity, and a non-reconcilable backend cannot be queried
// by key. Blind replay never happens. Idempotent by invocation id.
func Reconcile(ctx context.Context, sh *Shell) ([]*Reconciled, error) {
	states, err := Fold(sh.J.Production())
	if err != nil {
		return nil, err
	}
	var out []*Reconciled
	for id, s := range states {
		if s.Invocation == nil || !s.Dispatched || s.Terminal != nil || s.Reconciled != nil {
			continue
		}
		rc := &Reconciled{Header: sh.header(subj(id)), Invocation: id}
		caps := s.Invocation.Backend
		switch {
		case !caps.ActsOutward:
			rc.Disposition = DispositionAbandoned
			rc.Evidence = fmt.Sprintf("backend %s cannot act outward; %d effects committed", caps.Name, len(s.Effects))
		default:
			rc.Disposition = DispositionIndeterminate
			rc.Evidence = fmt.Sprintf("backend %s acts outward, reconcilable=%v; %d effects committed before the process died — the frame may have died with it", caps.Name, caps.OutwardReconcilable, len(s.Effects))
		}
		if _, err := sh.commit(ctx, string(id)+":reconciled", rc); err != nil {
			return nil, err
		}
		out = append(out, rc)
	}
	return out, nil
}
