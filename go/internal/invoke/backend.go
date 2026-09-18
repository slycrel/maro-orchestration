package invoke

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// Request is what the shell hands a backend: the exact prompt bytes (the
// same bytes hashed into Invocation.Request), the purpose, and whether the
// backend's agent tools are wanted at all.
type Request struct {
	Purpose Purpose
	Prompt  []byte
	Tools   bool          // false ⇒ the backend must run tool-less (judges, intent)
	Timeout time.Duration // 0 ⇒ backend default
	Cwd     string        // working directory for an agentic backend ("" ⇒ backend default)
	// Lens is the persona lens the prompt was rendered under (judge/render
	// purposes only); the prompt MUST begin with the lens text. Nil = neutral.
	Lens *Lens
	// Executor is the venue this call was COMMITTED to run in: the shell
	// asked the backend (Executored.ExecutorFor) before it wrote the
	// invocation, and the backend must run the call THERE. Nil = nobody
	// asked (a backend that answers for no executor, or a direct Complete),
	// and then the backend decides. It is not part of the prompt and so not
	// part of the exposure the fold re-derives; the INVOCATION is where it
	// is held (run/executor.go).
	Executor *Executor
}

// EffectEvent is a tool action as the backend's stream announces it (a
// tool_use). The shell commits a ToolEffect at once.
type EffectEvent struct {
	Op       string
	Input    []byte
	ToolCall string // the backend's own id for the call
}

// EffectResult is the tool's answer, reported when it arrives.
type EffectResult struct {
	Ordinal int
	Output  []byte
	IsError bool
}

// Sink is how a backend reports effects while running. Observe commits the
// announced action and returns its ordinal and derived key (the key a
// backend MAY pass to a tool that accepts idempotency keys; the claude CLI
// cannot, so for it the key is evidence only). Result commits the answer.
// Both are safe to call from any goroutine.
type Sink interface {
	Observe(ev EffectEvent) (ordinal int, key string, err error)
	Result(res EffectResult) error
}

// Result is a backend's terminal report.
type Result struct {
	Response   []byte
	Usage      Usage
	Terminal   TerminalState
	Reason     string // for partial/failed
	Transcript []byte // the raw captured stream, when the backend keeps one
}

// Backend is an effectful boundary component. It never records; the shell
// does. Complete must return a non-nil Result with Terminal set whenever it
// got far enough to dispatch; an error before dispatch means nothing
// happened. Any other shape is a contract violation the shell records as a
// failed terminal.
type Backend interface {
	Capabilities() Capabilities
	Complete(ctx context.Context, req Request, sink Sink) (*Result, error)
}

var (
	ErrBeforeDispatch   = errors.New("invoke: backend failed before dispatch")
	ErrBackendContract  = errors.New("invoke: backend violated its contract")
	ErrBackendIncapable = errors.New("invoke: backend cannot take this request whole")
)

// ---- Scripted backend (tests, replay) -------------------------------------

// ScriptedEffect is a scripted tool action; Unanswered leaves it observed
// with no result.
type ScriptedEffect struct {
	Op         string
	Input      []byte
	Output     []byte
	IsError    bool
	Unanswered bool
}

// ScriptedExecutor, on a Scripted backend, is the executor its calls
// report (invoke.Executored): the seam a test uses to put a scripted run
// on the container lane. Nil ⇒ the backend answers for no executor and the
// record says nothing, as every scripted journal before the field did.

// ScriptedCall is one scripted turn.
type ScriptedCall struct {
	Response   []byte
	Effects    []ScriptedEffect
	Terminal   TerminalState // "" ⇒ complete
	Reason     string
	Usage      Usage
	FailBefore bool          // fail before dispatch (nothing happens)
	Hang       bool          // block until ctx is done (simulates a kill/timeout)
	NilResult  bool          // return (nil, nil): a contract violation
	Panic      bool          // panic inside Complete: a contract violation
	Do         func(Request) // runs before the call answers: a worker's side effect (an ask file written)
}

// Scripted plays back a table of calls in order. Its capabilities are set
// by the test so the same backend can stand in for a tool-less judge or an
// outward-capable executor.
type Scripted struct {
	Caps  Capabilities
	Calls []ScriptedCall
	// Exec, when set, is the executor this backend's TOOL-BEARING calls
	// report (Executored): the seam a test uses to put a scripted run on
	// the container lane. Nil ⇒ it answers for no executor and the record
	// says nothing, as every scripted journal before the field did.
	Exec *Executor
	// Isolation is the policy this backend reports to a driver
	// (invoke.Isolated): what the ATTEMPT records. A scripted backend keeps
	// no container, so it is the test's way of saying which policy the run
	// was configured with, and Exec is what its calls then claim — the two
	// disagreeing is exactly what the fold is there to catch.
	Isolation ExecutorPolicy
	n         int
	Seen      []Request
}

// ExecutorPolicy is the policy every attempt of this backend runs under.
func (s *Scripted) ExecutorPolicy() ExecutorPolicy { return s.Isolation }

func (s *Scripted) Capabilities() Capabilities { return s.Caps }

// ExecutorFor reports Exec for a tool-bearing call and the host for a
// tool-less one (the engine never containerizes a call that touches
// nothing); nothing at all when Exec is unset.
func (s *Scripted) ExecutorFor(_ context.Context, req Request) (Executor, error) {
	if s.Exec == nil {
		return Executor{}, nil
	}
	if !req.Tools {
		return Executor{Kind: ExecutorHost}, nil
	}
	return *s.Exec, nil
}

func (s *Scripted) Complete(ctx context.Context, req Request, sink Sink) (*Result, error) {
	s.Seen = append(s.Seen, req)
	if s.n >= len(s.Calls) {
		return nil, fmt.Errorf("%w: scripted backend exhausted after %d calls", ErrBeforeDispatch, s.n)
	}
	c := s.Calls[s.n]
	s.n++
	if c.FailBefore {
		return nil, fmt.Errorf("%w: scripted", ErrBeforeDispatch)
	}
	if c.NilResult {
		return nil, nil
	}
	if c.Panic {
		panic("scripted backend panic")
	}
	if c.Do != nil {
		c.Do(req)
	}
	if c.Hang {
		<-ctx.Done()
		return &Result{Terminal: TerminalFailed, Reason: ctx.Err().Error()}, nil
	}
	for i, ev := range c.Effects {
		ord, _, err := sink.Observe(EffectEvent{Op: ev.Op, Input: ev.Input, ToolCall: fmt.Sprintf("s%d", i)})
		if err != nil {
			return &Result{Terminal: TerminalFailed, Reason: err.Error()}, nil
		}
		if !ev.Unanswered {
			if err := sink.Result(EffectResult{Ordinal: ord, Output: ev.Output, IsError: ev.IsError}); err != nil {
				return &Result{Terminal: TerminalFailed, Reason: err.Error()}, nil
			}
		}
	}
	term := c.Terminal
	if term == "" {
		term = TerminalComplete
	}
	return &Result{Response: c.Response, Usage: c.Usage, Terminal: term, Reason: c.Reason}, nil
}
