package invoke

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// Subprocess runs the claude CLI headless with stream-json output. It is
// outward-capable (Bash) and NOT outward-reconcilable: the CLI performs its
// tool calls itself and cannot ask for a key before acting, so every effect
// it reports is post-hoc evidence (Announced=false) and a dispatched call
// with no terminal reconciles to indeterminate on restart.
//
// The flag set is taken from the Python adapter's contract with the same
// CLI (kept because the CONTRACT with the CLI forces it, not because Python
// had it): -p, stream-json, --verbose, --dangerously-skip-permissions,
// --strict-mcp-config; tool-less calls pass --tools "" (disables the built-in
// set entirely); tool calls deny WebFetch/WebSearch.
type Subprocess struct {
	Bin            string
	Model          string
	DefaultTimeout time.Duration
	Lookup         func(string) (string, error) // exec.LookPath seam
}

const subprocessName = "subprocess"

// NewSubprocess finds the claude binary.
func NewSubprocess(model string) (*Subprocess, error) {
	s := &Subprocess{Model: model, DefaultTimeout: 20 * time.Minute, Lookup: exec.LookPath}
	bin, err := s.Lookup("claude")
	if err != nil {
		return nil, fmt.Errorf("%w: claude CLI not found: %v", ErrBeforeDispatch, err)
	}
	s.Bin = bin
	return s, nil
}

func (s *Subprocess) Capabilities() Capabilities {
	return Capabilities{Name: subprocessName, Model: s.Model, ActsOutward: true, OutwardReconcilable: false, ReadsByReference: true}
}

func (s *Subprocess) args(req Request) []string {
	a := []string{"-p", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions", "--strict-mcp-config"}
	if req.Tools {
		a = append(a, "--disallowedTools", "WebFetch,WebSearch")
	} else {
		a = append(a, "--tools", "")
	}
	if s.Model != "" {
		a = append(a, "--model", s.Model)
	}
	return a
}

// Complete dispatches the CLI, streams its NDJSON, reports each tool call
// to the sink as its result arrives, and returns the terminal report. The
// raw stream is captured whole (bounded by disk, never memory) and returned
// as the transcript.
func (s *Subprocess) Complete(ctx context.Context, req Request, sink Sink) (*Result, error) {
	timeout := req.Timeout
	if timeout == 0 {
		timeout = s.DefaultTimeout
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	capture, err := os.CreateTemp("", "maro-go-stream-*.ndjson")
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrBeforeDispatch, err)
	}
	capPath := capture.Name()
	defer os.Remove(capPath)
	cmd := exec.CommandContext(cctx, s.Bin, s.args(req)...)
	cmd.Stdin = bytes.NewReader(req.Prompt)
	if req.Cwd != "" {
		cmd.Dir = req.Cwd
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		capture.Close()
		return nil, fmt.Errorf("%w: %v", ErrBeforeDispatch, err)
	}
	cmd.Stderr = capture // merged capture, chronological
	start := time.Now()
	if err := cmd.Start(); err != nil {
		capture.Close()
		return nil, fmt.Errorf("%w: %v", ErrBeforeDispatch, err)
	}
	// From here on, the CLI is running: everything is a Result, never an error.
	tee := io.TeeReader(stdout, capture)
	p := newStreamParser()
	res := &Result{Terminal: TerminalFailed}
	scanner := bufio.NewScanner(tee)
	scanner.Buffer(make([]byte, 1<<20), 64<<20)
	for scanner.Scan() {
		line := scanner.Bytes()
		for _, ev := range p.feed(line) {
			if _, err := sink.Effect(cctx, ev); err != nil {
				res.Reason = "sink: " + err.Error()
			}
		}
	}
	waitErr := cmd.Wait()
	capture.Sync()
	capture.Close()
	res.Transcript, _ = os.ReadFile(capPath)
	res.Usage.WallMillis = time.Since(start).Milliseconds()
	// Effects whose results never arrived are still evidence (is_error).
	for _, ev := range p.flush() {
		_, _ = sink.Effect(cctx, ev)
	}
	switch {
	case p.result != nil && p.result.Subtype == "success" && !p.result.IsError:
		res.Response = []byte(p.result.Result)
		res.Usage.InputTokens, res.Usage.OutputTokens = p.result.Usage.InputTokens, p.result.Usage.OutputTokens
		res.Usage.CacheRead = p.result.Usage.CacheRead
		res.Usage.CostUSD = p.result.CostUSD
		if p.malformed > 0 {
			res.Terminal, res.Reason = TerminalPartial, fmt.Sprintf("%d malformed frames after the result", p.malformed)
		} else {
			res.Terminal = TerminalComplete
		}
	case p.result != nil:
		res.Terminal, res.Reason = TerminalFailed, fmt.Sprintf("cli result subtype=%s is_error=%v: %s", p.result.Subtype, p.result.IsError, truncate(p.result.Result, 400))
	case cctx.Err() != nil:
		res.Terminal, res.Reason = TerminalFailed, "timeout/cancel: "+cctx.Err().Error()
	case waitErr != nil:
		res.Terminal, res.Reason = TerminalFailed, "cli exit: "+waitErr.Error()
	default:
		res.Terminal, res.Reason = TerminalFailed, "no result event"
	}
	if p.rateLimited {
		res.Reason = strings.TrimSpace(res.Reason + " (rate limited)")
	}
	return res, nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// ---- stream-json parser -----------------------------------------------------

type resultEvent struct {
	Type    string  `json:"type"`
	Subtype string  `json:"subtype"`
	Result  string  `json:"result"`
	IsError bool    `json:"is_error"`
	CostUSD float64 `json:"total_cost_usd"`
	Usage   struct {
		InputTokens  int64 `json:"input_tokens"`
		OutputTokens int64 `json:"output_tokens"`
		CacheRead    int64 `json:"cache_read_input_tokens"`
	} `json:"usage"`
}

type pendingUse struct {
	id    string
	name  string
	input json.RawMessage
}

// streamParser reconstructs tool events from the CLI's NDJSON: an
// `assistant` event carries tool_use blocks; a `user` event carries the
// tool_result blocks that answer them; `result` is terminal. Non-JSON lines
// are noise (tolerated); JSON frames after the result that do not parse are
// counted as malformed (→ partial).
type streamParser struct {
	uses        []pendingUse
	byID        map[string]int
	done        map[string]bool
	result      *resultEvent
	malformed   int
	rateLimited bool
}

func newStreamParser() *streamParser {
	return &streamParser{byID: map[string]int{}, done: map[string]bool{}}
}

func (p *streamParser) feed(line []byte) []EffectEvent {
	line = bytes.TrimSpace(line)
	if len(line) == 0 || line[0] != '{' {
		return nil
	}
	var ev map[string]json.RawMessage
	if err := json.Unmarshal(line, &ev); err != nil {
		if p.result != nil {
			p.malformed++
		}
		return nil
	}
	var typ string
	_ = json.Unmarshal(ev["type"], &typ)
	switch typ {
	case "assistant":
		var msg struct {
			Content []struct {
				Type  string          `json:"type"`
				ID    string          `json:"id"`
				Name  string          `json:"name"`
				Input json.RawMessage `json:"input"`
			} `json:"content"`
		}
		_ = json.Unmarshal(ev["message"], &msg)
		for _, b := range msg.Content {
			if b.Type == "tool_use" {
				p.byID[b.ID] = len(p.uses)
				p.uses = append(p.uses, pendingUse{id: b.ID, name: b.Name, input: b.Input})
			}
		}
	case "user":
		var msg struct {
			Content []struct {
				Type      string          `json:"type"`
				ToolUseID string          `json:"tool_use_id"`
				Content   json.RawMessage `json:"content"`
				IsError   bool            `json:"is_error"`
			} `json:"content"`
		}
		_ = json.Unmarshal(ev["message"], &msg)
		var out []EffectEvent
		for _, b := range msg.Content {
			if b.Type != "tool_result" {
				continue
			}
			i, ok := p.byID[b.ToolUseID]
			if !ok || p.done[b.ToolUseID] {
				continue
			}
			p.done[b.ToolUseID] = true
			u := p.uses[i]
			out = append(out, EffectEvent{Op: u.name, Input: u.input, Output: stringify(b.Content), IsError: b.IsError, ToolCall: u.id})
		}
		return out
	case "result":
		var r resultEvent
		if err := json.Unmarshal(line, &r); err == nil {
			p.result = &r
		}
	case "rate_limit_event":
		var rl struct {
			Info struct {
				Status string `json:"status"`
			} `json:"rate_limit_info"`
		}
		_ = json.Unmarshal(line, &rl)
		if rl.Info.Status != "" && rl.Info.Status != "allowed" {
			p.rateLimited = true
		}
	}
	return nil
}

// flush returns tool uses that never received a result, as error effects.
func (p *streamParser) flush() []EffectEvent {
	var out []EffectEvent
	for _, u := range p.uses {
		if !p.done[u.id] {
			p.done[u.id] = true
			out = append(out, EffectEvent{Op: u.name, Input: u.input, Output: []byte("(no tool_result received)"), IsError: true, ToolCall: u.id})
		}
	}
	return out
}

// stringify renders a tool_result content block: a string stays a string;
// a list of text blocks is joined; anything else is its JSON.
func stringify(raw json.RawMessage) []byte {
	if len(raw) == 0 {
		return nil
	}
	var s string
	if json.Unmarshal(raw, &s) == nil {
		return []byte(s)
	}
	var blocks []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	}
	if json.Unmarshal(raw, &blocks) == nil && len(blocks) > 0 {
		var b strings.Builder
		for _, bl := range blocks {
			if bl.Type == "text" {
				b.WriteString(bl.Text)
			}
		}
		return []byte(b.String())
	}
	return raw
}

// LiveAvailable reports whether a live subprocess smoke can run here: the
// binary exists and MARO_GO_LIVE=1 opts in (it spends real tokens).
func LiveAvailable() bool {
	if os.Getenv("MARO_GO_LIVE") != "1" {
		return false
	}
	_, err := exec.LookPath("claude")
	return err == nil
}

var _ = errors.New
var _ = filepath.Join
