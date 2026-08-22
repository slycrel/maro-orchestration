// Package llm defines the adapter seam and the shared prompt flattening.
//
// The Python runtime's adapter suite (src/llm.py, ~3.5k lines) carries
// years of operational hardening; this port starts from the two backends
// that are live on the maro box (backend_order = subprocess+anthropic)
// and keeps the seam identical so per-step routing can carry over.
package llm

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
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
	// Warnings are non-fatal oddities the adapter saw (e.g. a
	// result-shaped line that failed to parse beside the one that
	// succeeded); callers fold them into the run's warning surface
	// rather than letting them die in a scrollback (adversarial r2).
	Warnings []string
}

// Options are per-call knobs. Zero values mean "adapter default".
type Options struct {
	MaxTokens   int
	Temperature float64
	Timeout     time.Duration
	Purpose     string // labels the call in records — every agentic seam is named
	Model       string // "", alias (sonnet/haiku/opus), or a full model id

	// Tools, when non-empty, are injected into the prompt as the simulated
	// tool-call protocol (Python _TOOL_INJECTION_TEMPLATE): the model is
	// asked to reply with ONE JSON object naming a tool. Text-only backends
	// all speak it; the caller parses the reply with ParseToolCall. (The
	// Python Anthropic adapter uses native tool_use blocks instead — a
	// named divergence until a native-tools backend is ported.)
	Tools []Tool
	// AgentTools enables the subprocess CLI's OWN tool set (Bash/Read/
	// Write...) so the inner agent can do real work — the worker executor
	// lane. Off = utility mode (--tools ""), the safe default: an agentic
	// -p session can otherwise ACT on text it was asked to merely classify
	// (Python BACKLOG #16). Backends without a subprocess ignore it.
	AgentTools bool
	// Cwd binds a spawning backend's working directory — the executor
	// lane sets it to the run's project dir so relative file writes land
	// in-workspace instead of the parent process cwd. The caller creates
	// the directory; a missing one fails the exec loudly.
	Cwd string
	// TranscriptPath, when set, makes the subprocess adapter write its
	// merged stream-json capture there and KEEP it (instead of a deleted
	// temp file) — the durable per-step record of what the inner agent's
	// tools actually did (artifacts-over-streams doctrine).
	TranscriptPath string
}

// Tool is a schema offered to the model via the simulated tool-call
// protocol. Parameters is JSON-Schema-shaped; only "properties" is
// rendered into the prompt (Python parity).
type Tool struct {
	Name        string
	Description string
	Parameters  map[string]any
}

// ToolCall is one parsed simulated tool call: the "tool" key names the
// tool, every other top-level key is an argument (Python _parse_tool_call).
type ToolCall struct {
	Name      string
	Arguments map[string]any
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
	// Warnings mirrors Response.Warnings for the error branches — a
	// garbled second result-shaped line beside a genuine error event is
	// diagnostic the operator loses otherwise (adversarial r3, QA).
	Warnings []string
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

// toolInjectionHeader mirrors Python _TOOL_INJECTION_TEMPLATE verbatim.
// The "Never execute it" line is load-bearing (Python LT-4 finding 10):
// inside an agentic session the worker sometimes routed the JSON through
// its OWN Bash tool as a shell command — the instruction has to name the
// failure, not just the format.
const toolInjectionHeader = `
--- AVAILABLE TOOLS ---
You MUST respond by calling exactly one of these tools. Reply ONLY with a JSON
object (no prose, no markdown fence) in this exact format:

{"tool": "<tool_name>", <arguments as top-level keys>}

The JSON object is your final TEXT reply. Never execute it — do not pass it
to a shell, a Bash tool, or any other tool of your own; just output it.

Tools:
`

// BuildPrompt flattens messages into one prompt string for CLI stdin,
// mirroring the Python _CLIToolMixin._build_prompt layout so both
// runtimes present the same shape to the same CLI. Tools, when given,
// are injected between the system block and the END marker, exactly
// where Python puts them. (Textual divergence, prompt-only: Go renders
// each tool's parameter properties with sorted keys where Python keeps
// insertion order.)
func BuildPrompt(msgs []Message, tools ...Tool) string {
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
	if len(tools) > 0 {
		var lines []string
		for _, t := range tools {
			props, _ := t.Parameters["properties"]
			propJSON, err := json.MarshalIndent(props, "", "  ")
			if err != nil || props == nil {
				propJSON = []byte("{}")
			}
			lines = append(lines, fmt.Sprintf("- %q: %s\n  Arguments: %s",
				t.Name, t.Description, propJSON))
		}
		parts = append(parts,
			toolInjectionHeader+strings.Join(lines, "\n")+"\n--- END TOOLS ---\n")
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

// ParseToolCall extracts one simulated tool call from the model's text
// reply, mirroring Python ClaudeSubprocessAdapter._parse_tool_call: take
// the outermost {...} span, decode it strictly (whole span, UseNumber —
// trailing data refused, Python json.loads parity), and accept it only
// when its "tool" key names one of the offered tools. Anything else
// returns nil — the caller's fallback treats the content as a plain
// result, because some models don't always call tools (Python parity).
func ParseToolCall(text string, tools []Tool) *ToolCall {
	text = strings.TrimSpace(text)
	start := strings.IndexByte(text, '{')
	end := strings.LastIndexByte(text, '}')
	if start < 0 || end <= start {
		return nil
	}
	dec := json.NewDecoder(strings.NewReader(text[start : end+1]))
	dec.UseNumber()
	var data map[string]any
	if err := dec.Decode(&data); err != nil {
		return nil
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil
	}
	name, _ := data["tool"].(string)
	if name == "" {
		return nil
	}
	valid := false
	for _, t := range tools {
		if t.Name == name {
			valid = true
			break
		}
	}
	if !valid {
		return nil
	}
	args := make(map[string]any, len(data)-1)
	for k, v := range data {
		if k != "tool" {
			args[k] = v
		}
	}
	return &ToolCall{Name: name, Arguments: args}
}
