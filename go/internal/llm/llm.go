// Package llm defines the adapter seam and the shared prompt flattening.
//
// The Python runtime's adapter suite (src/llm.py, ~3.5k lines) carries
// years of operational hardening; this port starts from the two backends
// that are live on the maro box (backend_order = subprocess+anthropic)
// and keeps the seam identical so per-step routing can carry over.
package llm

import (
	"context"
	"fmt"
	"strings"
	"time"
)

// Message mirrors Python LLMMessage.
type Message struct {
	Role    string // "system" | "user" | "assistant"
	Content string
}

// Response mirrors the fields the loop actually consumes.
type Response struct {
	Content   string
	TokensIn  int
	TokensOut int
}

// Options are per-call knobs. Zero values mean "adapter default".
type Options struct {
	MaxTokens   int
	Temperature float64
	Timeout     time.Duration
	Purpose     string // labels the call in records — every agentic seam is named
	Model       string // "", alias (sonnet/haiku/opus), or a full model id
}

// ResultError reports a backend result that was itself an error, while
// keeping the token usage the failed turn still spent — real spend on a
// blocked step must reach the records (adversarial round 2026-08-22,
// Expert QA: the error branch silently dropped usage the CLI reports).
// Callers salvage it with errors.As.
type ResultError struct {
	Msg       string
	TokensIn  int
	TokensOut int
}

func (e *ResultError) Error() string { return e.Msg }

// Adapter is the backend seam.
type Adapter interface {
	// Complete returns the model's reply or an error. Errors are returned,
	// never swallowed into empty responses — the caller decides what a
	// failure means for its step (no-silent-errors doctrine).
	Complete(ctx context.Context, msgs []Message, opts Options) (*Response, error)
	Name() string
}

// BuildPrompt flattens messages into one prompt string for CLI stdin,
// mirroring the Python _CLIToolMixin._build_prompt layout so both
// runtimes present the same shape to the same CLI.
func BuildPrompt(msgs []Message) string {
	var parts []string
	var system []string
	var rest []Message
	for _, m := range msgs {
		if m.Role == "system" {
			system = append(system, m.Content)
		} else {
			rest = append(rest, m)
		}
	}
	if len(system) > 0 {
		parts = append(parts, "[SYSTEM INSTRUCTIONS]\n"+strings.Join(system, "\n\n"))
	}
	// Unconditional, matching Python _build_prompt exactly: it emits the
	// END marker even with no system block (adversarial round 2026-08-22
	// caught this port emitting it conditionally — a silently different
	// prompt shape for system-less calls).
	parts = append(parts, "[END SYSTEM INSTRUCTIONS]\n")
	for _, m := range rest {
		switch m.Role {
		case "user":
			parts = append(parts, "User: "+m.Content)
		case "assistant":
			parts = append(parts, "Assistant: "+m.Content)
		default:
			// An unknown role is a caller bug; surface it in-band rather
			// than dropping the content on the floor.
			parts = append(parts, fmt.Sprintf("%s: %s", m.Role, m.Content))
		}
	}
	return strings.Join(parts, "\n\n")
}
