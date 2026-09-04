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
}

// EffectEvent is one tool action as the backend's stream reports it. The
// shell commits a ToolEffect per event, in order, as they arrive.
type EffectEvent struct {
	Op       string
	Input    []byte
	Output   []byte
	IsError  bool
	ToolCall string // the backend's own id for the call
}

// Sink is how a backend reports effects while running. Effect returns the
// derived key the backend MAY pass on to a tool that accepts idempotency
// keys; the claude CLI cannot, so for it the key is evidence only.
type Sink interface {
	Effect(ctx context.Context, ev EffectEvent) (key string, err error)
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
// does. Complete must return a Result (with Terminal set) whenever it got
// far enough to dispatch; an error before dispatch means nothing happened.
type Backend interface {
	Capabilities() Capabilities
	Complete(ctx context.Context, req Request, sink Sink) (*Result, error)
}

var (
	ErrBeforeDispatch = errors.New("invoke: backend failed before dispatch")
	ErrNoResponse     = errors.New("invoke: backend returned no response")
)

// ---- Scripted backend (tests, replay) -------------------------------------

// ScriptedCall is one scripted turn.
type ScriptedCall struct {
	Response   []byte
	Effects    []EffectEvent
	Terminal   TerminalState // "" ⇒ complete
	Reason     string
	Usage      Usage
	FailBefore bool // fail before dispatch (nothing happens)
	Hang       bool // block until ctx is done (simulates a kill/timeout)
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
	if c.Hang {
		<-ctx.Done()
		return &Result{Terminal: TerminalFailed, Reason: ctx.Err().Error()}, nil
	}
	for _, ev := range c.Effects {
		if _, err := sink.Effect(ctx, ev); err != nil {
			return &Result{Terminal: TerminalFailed, Reason: err.Error()}, nil
		}
	}
	term := c.Terminal
	if term == "" {
		term = TerminalComplete
	}
	return &Result{Response: c.Response, Usage: c.Usage, Terminal: term, Reason: c.Reason}, nil
}
