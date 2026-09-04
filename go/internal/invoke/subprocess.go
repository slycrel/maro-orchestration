package invoke

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
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

// Complete dispatches the CLI, streams its NDJSON, reports each tool_use to
// the sink the moment it appears and each tool_result as it arrives, and
// returns the terminal report. The raw stream is captured whole to disk and
// returned as the transcript.
func (s *Subprocess) Complete(ctx context.Context, req Request, sink Sink) (*Result, error) {
	timeout := req.Timeout
	if timeout <= 0 {
		timeout = s.DefaultTimeout
	}
	if timeout <= 0 {
		timeout = 20 * time.Minute
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
	p := newStreamParser(sink)
	res := &Result{Terminal: TerminalFailed}
	scanner := bufio.NewScanner(tee)
	scanner.Buffer(make([]byte, 1<<20), maxLine)
	var scanErr error
	for scanner.Scan() {
		p.feed(scanner.Bytes())
	}
	if scanErr = scanner.Err(); scanErr != nil {
		// A line over the limit: stop the child (it may be blocked on the
		// pipe) and drain so Wait returns; the terminal names the cause.
		cancel()
		io.Copy(io.Discard, stdout)
	}
	waitErr := cmd.Wait()
	var capErr error
	if err := capture.Sync(); err != nil {
		capErr = err
	}
	if err := capture.Close(); err != nil && capErr == nil {
		capErr = err
	}
	tr, rerr := os.ReadFile(capPath)
	if rerr != nil && capErr == nil {
		capErr = rerr
	}
	res.Transcript = tr
	res.Usage.WallMillis = time.Since(start).Milliseconds()
	var reasons []string
	if scanErr != nil {
		reasons = append(reasons, "stream: "+scanErr.Error())
	}
	if capErr != nil {
		reasons = append(reasons, "capture: "+capErr.Error())
	}
	if p.violations > 0 {
		reasons = append(reasons, fmt.Sprintf("%d protocol violation(s): %s", p.violations, strings.Join(p.violationNotes, "; ")))
	}
	if p.rateLimited {
		reasons = append(reasons, "rate limited")
	}
	switch {
	case scanErr != nil:
		res.Terminal = TerminalFailed
	case p.duplicateResult:
		res.Terminal = TerminalFailed
		reasons = append([]string{"duplicate result event"}, reasons...)
	case p.result != nil && p.result.Subtype == "success" && !p.result.IsError:
		res.Response = []byte(p.result.Result)
		res.Usage.InputTokens, res.Usage.OutputTokens = p.result.Usage.InputTokens, p.result.Usage.OutputTokens
		res.Usage.CacheRead = p.result.Usage.CacheRead
		if p.result.CostUSD != nil {
			res.Usage.CostUSD, res.Usage.CostReported = *p.result.CostUSD, true
		}
		if p.violations > 0 || capErr != nil {
			res.Terminal = TerminalPartial
		} else {
			res.Terminal = TerminalComplete
		}
	case p.result != nil:
		res.Terminal = TerminalFailed
		reasons = append([]string{fmt.Sprintf("cli result subtype=%s is_error=%v: %s", p.result.Subtype, p.result.IsError, truncate(p.result.Result, 400))}, reasons...)
	case cctx.Err() != nil:
		res.Terminal = TerminalFailed
		reasons = append([]string{"timeout/cancel: " + cctx.Err().Error()}, reasons...)
	case waitErr != nil:
		res.Terminal = TerminalFailed
		reasons = append([]string{"cli exit: " + waitErr.Error()}, reasons...)
	default:
		res.Terminal = TerminalFailed
		reasons = append([]string{"no result event"}, reasons...)
	}
	res.Reason = strings.Join(reasons, "; ")
	return res, nil
}

// maxLine bounds one NDJSON line. The CLI's frames are small (tool outputs
// are capped by the CLI); a line past this is a protocol failure, not data.
const maxLine = 64 << 20

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// ---- stream-json parser -----------------------------------------------------

type resultEvent struct {
	Type    string   `json:"type"`
	Subtype string   `json:"subtype"`
	Result  string   `json:"result"`
	IsError bool     `json:"is_error"`
	CostUSD *float64 `json:"total_cost_usd"`
	Usage   struct {
		InputTokens  int64 `json:"input_tokens"`
		OutputTokens int64 `json:"output_tokens"`
		CacheRead    int64 `json:"cache_read_input_tokens"`
	} `json:"usage"`
}

// streamParser drives the sink from the CLI's NDJSON: an `assistant` event's
// tool_use blocks are OBSERVED at once; a `user` event's tool_result blocks
// are RESULTS; `result` is terminal and closes the protocol — any semantic
// frame after it, a second result, a tool_result with no matching tool_use,
// an empty tool name, or a JSON-looking frame that does not parse is a
// protocol violation (→ partial, or failed for a duplicate result).
// Non-JSON lines are noise (the CLI's own logging) and are tolerated.
type streamParser struct {
	sink            Sink
	byID            map[string]int // tool_use id → ordinal
	done            map[string]bool
	result          *resultEvent
	closed          bool
	duplicateResult bool
	violations      int
	violationNotes  []string
	rateLimited     bool
}

func newStreamParser(sink Sink) *streamParser {
	return &streamParser{sink: sink, byID: map[string]int{}, done: map[string]bool{}}
}

func (p *streamParser) violate(note string) {
	p.violations++
	if len(p.violationNotes) < 5 {
		p.violationNotes = append(p.violationNotes, note)
	}
}

func (p *streamParser) feed(line []byte) {
	line = bytes.TrimSpace(line)
	if len(line) == 0 || line[0] != '{' {
		return // noise
	}
	var ev map[string]json.RawMessage
	if err := json.Unmarshal(line, &ev); err != nil {
		p.violate("undecodable JSON frame")
		return
	}
	var typ string
	_ = json.Unmarshal(ev["type"], &typ)
	if p.closed && (typ == "assistant" || typ == "user" || typ == "result") {
		if typ == "result" {
			p.duplicateResult = true
		}
		p.violate("frame after result: " + typ)
		return
	}
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
		if err := json.Unmarshal(ev["message"], &msg); err != nil {
			p.violate("assistant frame shape")
			return
		}
		for _, b := range msg.Content {
			if b.Type != "tool_use" {
				continue
			}
			if b.Name == "" || b.ID == "" {
				p.violate("tool_use without name/id")
				continue
			}
			ord, _, err := p.sink.Observe(EffectEvent{Op: b.Name, Input: b.Input, ToolCall: b.ID})
			if err != nil {
				p.violate("observe: " + err.Error())
				continue
			}
			p.byID[b.ID] = ord
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
		if err := json.Unmarshal(ev["message"], &msg); err != nil {
			p.violate("user frame shape")
			return
		}
		for _, b := range msg.Content {
			if b.Type != "tool_result" {
				continue
			}
			ord, ok := p.byID[b.ToolUseID]
			if !ok {
				p.violate("tool_result with no matching tool_use")
				continue
			}
			if p.done[b.ToolUseID] {
				p.violate("duplicate tool_result")
				continue
			}
			p.done[b.ToolUseID] = true
			if err := p.sink.Result(EffectResult{Ordinal: ord, Output: rawOutput(b.Content), IsError: b.IsError}); err != nil {
				p.violate("result: " + err.Error())
			}
		}
	case "result":
		var r resultEvent
		if err := json.Unmarshal(line, &r); err != nil {
			p.violate("result frame shape")
			return
		}
		p.result = &r
		p.closed = true
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
}

// rawOutput keeps a tool_result's content byte-for-byte as the TOOL produced
// it: a JSON string is unquoted; the CLI's envelope of text blocks is
// unwrapped to the concatenated text; anything else stays as its JSON.
func rawOutput(raw json.RawMessage) []byte {
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
		allText := true
		var b strings.Builder
		for _, bl := range blocks {
			if bl.Type != "text" {
				allText = false
				break
			}
			b.WriteString(bl.Text)
		}
		if allText {
			return []byte(b.String())
		}
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
