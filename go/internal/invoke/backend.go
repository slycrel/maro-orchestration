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

// ScriptedCall is one scripted turn.
type ScriptedCall struct {
	Response   []byte
	Effects    []ScriptedEffect
	Terminal   TerminalState // "" ⇒ complete
	Reason     string
	Usage      Usage
	FailBefore bool // fail before dispatch (nothing happens)
	Hang       bool // block until ctx is done (simulates a kill/timeout)
	NilResult  bool // return (nil, nil): a contract violation
	Panic      bool // panic inside Complete: a contract violation
}

// Scripted plays back a table of calls in order. Its capabilities are set
// by the test so the same backend can stand in for a tool-less judge or an
// outward-capable executor.
type Scripted struct {
	Caps  Capabilities
	Calls []ScriptedCall
	n     int
	Seen  []Request
}

func (s *Scripted) Capabilities() Capabilities { return s.Caps }

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
