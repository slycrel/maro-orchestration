package invoke

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Shell owns the invocation state machine. It is the ONLY way a backend is
// called: every transition is a journal record, so a crash at any point
// leaves evidence the next start can read.
type Shell struct {
	J       *journal.Journal
	Store   *thought.Store
	Run     record.RunID
	Attempt uint32

	// CrashAt, when set, makes Invoke stop dead (as if the process died)
	// immediately AFTER the named stage commits. Test seam for the kill-site
	// matrix; production never sets it.
	CrashAt string
}

// ErrCrashed is what Invoke returns when the CrashAt seam fired.
var ErrCrashed = errors.New("invoke: crashed at test seam")

// Outcome is what Invoke returns to the driver. It is non-nil whenever an
// Invocation record exists, even when a later step failed, so the caller
// always has the identity to investigate.
type Outcome struct {
	Invocation record.RecordID
	Receipt    record.RecordID
	Terminal   TerminalState
	Reason     string
	Response   []byte
	Usage      Usage
	Effects    []record.RecordID
	Err        error // a bookkeeping failure after the invocation existed
}

var (
	ErrTargetWhy = errors.New("invoke: a target needs a why")
	ErrRequest   = errors.New("invoke: bad request")
)

// Incapable is the typed refusal when a prompt exceeds the backend's declared
// whole-input capacity. Nothing is written; the caller routes elsewhere.
type Incapable struct{ Actual, Max int64 }

func (e *Incapable) Error() string {
	return fmt.Sprintf("%v: %d bytes > backend max %d", ErrBackendIncapable, e.Actual, e.Max)
}
func (e *Incapable) Unwrap() error { return ErrBackendIncapable }

// sink serializes effect reporting; a backend may call it from any goroutine.
type sink struct {
	mu      sync.Mutex
	sh      *Shell
	inv     *Invocation
	ctx     context.Context // detached: evidence is committed even after the caller's deadline
	next    int
	effects []record.RecordID
	seen    map[int]bool // ordinals with a committed result
	err     error
}

func (s *sink) Observe(ev EffectEvent) (int, string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.err != nil {
		return 0, "", s.err
	}
	if ev.Op == "" {
		s.err = fmt.Errorf("%w: tool_use with an empty name", ErrBackendContract)
		return 0, "", s.err
	}
	in, err := s.sh.Store.Put(thought.StepResult, evidence("input", ev.Input, ev.ToolCall))
	if err != nil {
		s.err = err
		return 0, "", err
	}
	ord := s.next
	key := DeriveKey(s.inv.EffectToken, ord)
	te := &ToolEffect{Header: s.sh.header(subj(s.inv.ID)), Invocation: s.inv.ID, Ordinal: ord, Op: ev.Op, Class: ClassOf(ev.Op), Key: key, Announced: s.inv.Backend.OutwardReconcilable, Input: in}
	// a tool-less request is CONFINED: whatever the backend reports it
	// did, the effect is recorded as refused and the invocation fails —
	// the record is the evidence, the failure is the enforcement
	te.Refused = !s.inv.Tools
	if _, err := s.sh.commit(s.ctx, fmt.Sprintf("%s:effect:%d", s.inv.ID, ord), te); err != nil {
		s.err = err
		return 0, "", err
	}
	s.effects = append(s.effects, te.ID)
	s.next++
	if te.Refused {
		s.err = fmt.Errorf("%w: effect %s reported on a tool-less request (confined)", ErrBackendContract, ev.Op)
		return 0, "", s.err
	}
	return ord, key, nil
}

func (s *sink) Result(res EffectResult) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.err != nil {
		return s.err
	}
	if res.Ordinal < 0 || res.Ordinal >= s.next {
		s.err = fmt.Errorf("%w: result for unobserved effect %d", ErrBackendContract, res.Ordinal)
		return s.err
	}
	if s.seen[res.Ordinal] {
		s.err = fmt.Errorf("%w: duplicate result for effect %d", ErrBackendContract, res.Ordinal)
		return s.err
	}
	out, err := s.sh.Store.Put(thought.StepResult, evidence("output", res.Output, ""))
	if err != nil {
		s.err = err
		return err
	}
	rr := &ToolEffectResult{Header: s.sh.header(subj(s.inv.ID)), Invocation: s.inv.ID, Ordinal: res.Ordinal, IsError: res.IsError, Output: out}
	if _, err := s.sh.commit(s.ctx, fmt.Sprintf("%s:effect-result:%d", s.inv.ID, res.Ordinal), rr); err != nil {
		s.err = err
		return err
	}
	s.seen[res.Ordinal] = true
	return nil
}

// evidence is byte-preserving: raw bytes base64'd with their role, so
// invalid JSON or non-UTF-8 survives intact and round-trips.
func evidence(role string, raw []byte, toolCall string) []byte {
	b, _ := json.Marshal(map[string]any{"role": role, "b64": base64.StdEncoding.EncodeToString(raw), "tool_call": toolCall})
	return b
}

// DecodeEvidence returns the raw bytes an evidence thought carries.
func DecodeEvidence(body []byte) ([]byte, error) {
	var m struct {
		B64 string `json:"b64"`
	}
	if err := json.Unmarshal(body, &m); err != nil {
		return nil, err
	}
	return base64.StdEncoding.DecodeString(m.B64)
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

func (sh *Shell) crash(stage string) error {
	if sh.CrashAt == stage {
		return ErrCrashed
	}
	return nil
}

// Invoke runs the state machine. Validation (purpose, target, backend
// capabilities, input size against MaxInputBytes, live context) happens
// BEFORE anything is written. Then: store request → prepared → dispatched →
// backend (effects as announced, results as reported) → store response and
// transcript → terminal (carrying their refs and usage) → receipt.
func (sh *Shell) Invoke(ctx context.Context, b Backend, req Request, target *Target) (*Outcome, error) {
	if !purposes[req.Purpose] {
		return nil, fmt.Errorf("%w: purpose %q out of vocabulary", ErrRequest, req.Purpose)
	}
	if target != nil && (target.Name == "" || target.Why == "") {
		return nil, ErrTargetWhy
	}
	caps := b.Capabilities()
	if caps.Name == "" {
		return nil, fmt.Errorf("%w: backend has no name", ErrBackendContract)
	}
	if caps.MaxInputBytes > 0 && int64(len(req.Prompt)) > caps.MaxInputBytes {
		return nil, &Incapable{Actual: int64(len(req.Prompt)), Max: caps.MaxInputBytes}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	token, err := NewEffectToken()
	if err != nil {
		return nil, err
	}
	reqRef, err := sh.Store.Put(thought.Prompt, req.Prompt)
	if err != nil {
		return nil, err
	}
	inv := &Invocation{Header: sh.header(record.Ref{Kind: "prompt", ID: reqRef.Hash}), Purpose: req.Purpose, Request: reqRef, Backend: caps, Tools: req.Tools, EffectToken: token}
	if target != nil {
		inv.TargetName, inv.TargetLimit, inv.TargetWhy = target.Name, target.Limit, target.Why
	}
	if _, err := sh.commit(ctx, string(inv.ID)+":prepared", inv); err != nil {
		return nil, err
	}
	out := &Outcome{Invocation: inv.ID}
	if err := sh.crash("prepared"); err != nil {
		return out, err
	}
	if _, err := sh.commit(ctx, string(inv.ID)+":dispatched", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID}); err != nil {
		out.Err = err
		return out, err
	}
	if err := sh.crash("dispatched"); err != nil {
		return out, err
	}
	// From dispatch on, bookkeeping is ATTEMPTED on a context detached from
	// the caller's: a timeout is a terminal fact to record. It can still fail
	// (dead lease, poisoned journal); then the invocation is an orphan that
	// Reconcile will dispose of conservatively, and the error says so.
	book := context.WithoutCancel(ctx)
	sk := &sink{sh: sh, inv: inv, ctx: book, seen: map[int]bool{}}
	res, berr := safeComplete(ctx, b, req, sk)
	out.Effects = sk.effects
	if err := sh.crash("effects"); err != nil {
		return out, err
	}
	term := &TerminalObserved{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1}
	var contractErr error
	switch {
	case berr != nil && errors.Is(berr, ErrBeforeDispatch):
		term.State, term.Reason = TerminalFailed, "before dispatch: "+berr.Error()
	case berr != nil:
		term.State, term.Reason = TerminalFailed, berr.Error()
	case res == nil:
		berr = fmt.Errorf("%w: nil result without error", ErrBackendContract)
		term.State, term.Reason = TerminalFailed, berr.Error()
	default:
		term.State, term.Reason, term.Usage = res.Terminal, res.Reason, res.Usage
		if err := res.Usage.Validate(); err != nil {
			contractErr = fmt.Errorf("%w: %v", ErrBackendContract, err)
			term.State, term.Reason, term.Usage = TerminalFailed, contractErr.Error(), Usage{}
		}
		switch term.State {
		case TerminalComplete, TerminalPartial:
			if res.Response == nil {
				contractErr = fmt.Errorf("%w: %s without a response", ErrBackendContract, res.Terminal)
				term.State, term.Reason = TerminalFailed, contractErr.Error()
			}
		case TerminalFailed:
		default:
			contractErr = fmt.Errorf("%w: terminal %q", ErrBackendContract, res.Terminal)
			term.State, term.Reason = TerminalFailed, contractErr.Error()
		}
		if sk.err != nil && term.State == TerminalComplete {
			term.State, term.Reason = TerminalPartial, "effect recording failed: "+sk.err.Error()
		}
		if len(res.Transcript) > 0 {
			if tref, err := sh.Store.Put(thought.Response, res.Transcript); err == nil {
				term.Transcript = &tref
			} else {
				term.Reason = joinReason(term.Reason, "transcript_store_failed: "+err.Error())
			}
		}
		if term.State != TerminalFailed {
			// The response is stored BEFORE the terminal is committed: a
			// terminal that says complete always has its bytes.
			rref, err := sh.Store.Put(thought.Response, res.Response)
			if err != nil {
				term.State, term.Reason, term.Usage = TerminalFailed, joinReason(term.Reason, "response_store_failed: "+err.Error()), Usage{}
			} else {
				term.Response = &rref
			}
		}
	}
	if _, err := sh.commit(book, string(inv.ID)+":terminal", term); err != nil {
		out.Err = err
		return out, err
	}
	out.Terminal, out.Reason, out.Usage = term.State, term.Reason, term.Usage
	if err := sh.crash("terminal"); err != nil {
		return out, err
	}
	if term.State == TerminalFailed {
		if berr != nil {
			return out, berr
		}
		return out, contractErr
	}
	rc := receiptFrom(sh, term)
	if _, err := sh.commit(book, string(inv.ID)+":receipt", rc); err != nil {
		out.Err = err
		return out, err
	}
	out.Receipt, out.Response = rc.ID, res.Response
	return out, nil
}

func joinReason(a, b string) string {
	if a == "" {
		return b
	}
	return a + "; " + b
}

// safeComplete contains a backend panic as a contract violation.
func safeComplete(ctx context.Context, b Backend, req Request, sk *sink) (res *Result, err error) {
	defer func() {
		if r := recover(); r != nil {
			res, err = nil, fmt.Errorf("%w: panic: %v", ErrBackendContract, r)
		}
	}()
	return b.Complete(ctx, req, sk)
}

func receiptFrom(sh *Shell, term *TerminalObserved) *Receipt {
	return &Receipt{Header: sh.header(subj(term.Invocation)), Invocation: term.Invocation, Attempt: term.Attempt, Response: *term.Response, Usage: term.Usage}
}

func subj(id record.RecordID) record.Ref { return record.Ref{Kind: KindInvocation, ID: string(id)} }

// State is the fold over one invocation's records.
type State struct {
	Invocation *Invocation
	Dispatched bool
	Effects    []*ToolEffect
	Results    map[int]*ToolEffectResult
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

// Unanswered lists observed effects with no result.
func (s *State) Unanswered() []*ToolEffect {
	var out []*ToolEffect
	for _, e := range s.Effects {
		if _, ok := s.Results[e.Ordinal]; !ok {
			out = append(out, e)
		}
	}
	return out
}

// ErrFoldCorrupt: the journal holds an impossible invocation history.
var ErrFoldCorrupt = errors.New("invoke: invocation history violates the state machine")

// Fold is a VALIDATING reducer: it refuses duplicates, out-of-order
// transitions, effects whose key or class do not derive from the prepared
// invocation and the operation table, receipts without terminals,
// reconciliations after terminals, and dispositions that contradict the
// evidence. A corrupt history is an error, never a plausible phase.
func Fold(pr *journal.ProductionReader) (map[record.RecordID]*State, error) {
	states := map[record.RecordID]*State{}
	get := func(id record.RecordID) *State {
		s, ok := states[id]
		if !ok {
			s = &State{Results: map[int]*ToolEffectResult{}}
			states[id] = s
		}
		return s
	}
	bad := func(id record.RecordID, why string) error { return fmt.Errorf("%w: %s: %s", ErrFoldCorrupt, id, why) }
	err := pr.Scan(0, func(r record.Record) error {
		switch v := r.(type) {
		case *Invocation:
			s := get(v.ID)
			if s.Invocation != nil {
				return bad(v.ID, "duplicate invocation")
			}
			s.Invocation = v
		case *Dispatched:
			s := get(v.Invocation)
			if s.Invocation == nil || s.Dispatched {
				return bad(v.Invocation, "dispatched without prepared, or twice")
			}
			s.Dispatched = true
		case *ToolEffect:
			s := get(v.Invocation)
			if s.Invocation == nil || !s.Dispatched || s.Terminal != nil {
				return bad(v.Invocation, "effect outside the dispatched window")
			}
			if v.Ordinal != len(s.Effects) {
				return bad(v.Invocation, fmt.Sprintf("effect ordinal %d, expected %d", v.Ordinal, len(s.Effects)))
			}
			if v.Key != DeriveKey(s.Invocation.EffectToken, v.Ordinal) {
				return bad(v.Invocation, "effect key does not derive from the invocation token")
			}
			if v.Class != ClassOf(v.Op) {
				return bad(v.Invocation, "effect class disagrees with the operation table")
			}
			s.Effects = append(s.Effects, v)
		case *ToolEffectResult:
			s := get(v.Invocation)
			if v.Ordinal < 0 || v.Ordinal >= len(s.Effects) || s.Terminal != nil {
				return bad(v.Invocation, "result for an unobserved effect or after the terminal")
			}
			if _, dup := s.Results[v.Ordinal]; dup {
				return bad(v.Invocation, "duplicate effect result")
			}
			s.Results[v.Ordinal] = v
		case *TerminalObserved:
			s := get(v.Invocation)
			if s.Invocation == nil || !s.Dispatched || s.Terminal != nil || s.Reconciled != nil {
				return bad(v.Invocation, "terminal without dispatched, twice, or after reconciliation")
			}
			s.Terminal = v
		case *Receipt:
			s := get(v.Invocation)
			if s.Terminal == nil || s.Terminal.State == TerminalFailed || s.Receipt != nil {
				return bad(v.Invocation, "receipt without a non-failed terminal, or twice")
			}
			if s.Terminal.Response == nil || s.Terminal.Response.Hash != v.Response.Hash {
				return bad(v.Invocation, "receipt response disagrees with the terminal")
			}
			s.Receipt = v
		case *Reconciled:
			s := get(v.Invocation)
			if s.Invocation == nil || !s.Dispatched || s.Terminal != nil || s.Reconciled != nil {
				return bad(v.Invocation, "reconciliation without an orphan, or twice")
			}
			if want := dispositionFor(s); v.Disposition != want {
				return bad(v.Invocation, fmt.Sprintf("disposition %s contradicts the evidence (want %s)", v.Disposition, want))
			}
			s.Reconciled = v
		}
		return nil
	})
	return states, err
}

// dispositionFor is the one rule: abandoned only when the backend cannot act
// outward AND no observed effect could have. Observed evidence dominates a
// self-declared capability.
func dispositionFor(s *State) Disposition {
	if s.Invocation.Backend.ActsOutward {
		return DispositionIndeterminate
	}
	for _, e := range s.Effects {
		if e.Class != OpQuery {
			return DispositionIndeterminate
		}
	}
	return DispositionAbandoned
}

// Reconcile is the restart pass, in two parts. (1) Every invocation with a
// non-failed terminal and no receipt is FINALIZED: the receipt is derived
// from the terminal (which carries the response ref and usage). (2) Every
// invocation dispatched without a terminal gets a disposition by
// dispositionFor. Blind replay never happens. Idempotent by invocation id.
func Reconcile(ctx context.Context, sh *Shell) ([]*Reconciled, []*Receipt, error) {
	states, err := Fold(sh.J.Production())
	if err != nil {
		return nil, nil, err
	}
	ids := make([]record.RecordID, 0, len(states))
	for id := range states {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	var recs []*Reconciled
	var fins []*Receipt
	for _, id := range ids {
		s := states[id]
		if s.Invocation == nil || !s.Dispatched || s.Reconciled != nil {
			continue
		}
		if s.Terminal != nil {
			if s.Terminal.State != TerminalFailed && s.Receipt == nil {
				rc := receiptFrom(sh, s.Terminal)
				if _, err := sh.commit(ctx, string(id)+":receipt", rc); err != nil {
					return recs, fins, err
				}
				fins = append(fins, rc)
			}
			continue
		}
		rc := &Reconciled{Header: sh.header(subj(id)), Invocation: id, Disposition: dispositionFor(s)}
		rc.Evidence = fmt.Sprintf("backend %s acts_outward=%v reconcilable=%v; %d effects observed (%d unanswered) before the process died", s.Invocation.Backend.Name, s.Invocation.Backend.ActsOutward, s.Invocation.Backend.OutwardReconcilable, len(s.Effects), len(s.Unanswered()))
		if _, err := sh.commit(ctx, string(id)+":reconciled", rc); err != nil {
			return recs, fins, err
		}
		recs = append(recs, rc)
	}
	return recs, fins, nil
}
