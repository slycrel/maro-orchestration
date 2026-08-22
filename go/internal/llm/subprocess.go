package llm

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
)

// Subprocess drives the `claude` CLI in print mode — no API key needed,
// same backend the Python ClaudeSubprocessAdapter uses. The flag set
// mirrors the Python adapter verbatim (each flag there is
// incident-derived; see src/llm.py comments for the provenance):
//
//	-p --output-format stream-json --verbose
//	--dangerously-skip-permissions --strict-mcp-config --tools ""
//
// v0 scope: utility-style calls only (--tools "" always). The Python
// runtime disables tools for routing/classification because an agentic
// -p session can otherwise ACT on text it was asked to classify (BACKLOG
// #16); this port's first slice runs its whole loop in that safer mode
// and treats tool-bearing worker steps as the next port tranche.
//
// Known gap vs Python's _run_subprocess_safe, deliberately deferred (see
// PORT.md): no liveness/stall kill — a hung-but-silent CLI runs to the
// wall-clock timeout. Output capture IS disk-backed like Python's, so a
// verbose transcript cannot balloon process memory.
type Subprocess struct {
	Bin            string
	DefaultTimeout time.Duration
}

// NewSubprocess resolves the claude binary the same way the Python
// adapter does: CLAUDE_BIN env, PATH, then known install locations.
func NewSubprocess() (*Subprocess, error) {
	bin, err := FindClaudeBin()
	if err != nil {
		return nil, err
	}
	return &Subprocess{Bin: bin, DefaultTimeout: 180 * time.Second}, nil
}

func FindClaudeBin() (string, error) {
	if v := os.Getenv("CLAUDE_BIN"); v != "" {
		return v, nil
	}
	if p, err := exec.LookPath("claude"); err == nil {
		return p, nil
	}
	home, _ := os.UserHomeDir()
	for _, cand := range []string{
		filepath.Join(home, ".local", "bin", "claude"),
		"/usr/local/bin/claude",
		"/opt/homebrew/bin/claude",
	} {
		if st, err := os.Stat(cand); err == nil && !st.IsDir() {
			return cand, nil
		}
	}
	return "", errors.New("claude CLI not found (CLAUDE_BIN, PATH, known locations)")
}

func (a *Subprocess) Name() string { return "subprocess" }

// resultEvent is the final {"type":"result"} stream-json event; its
// payload is what the old --output-format json produced. Subtype rides
// along because the CLI reports some failures as result events without
// is_error — Python _extract_success_result requires subtype=="success"
// AND is_error falsy, and so does this adapter.
type resultEvent struct {
	Type    string `json:"type"`
	Subtype string `json:"subtype"`
	Result  string `json:"result"`
	IsError bool   `json:"is_error"`
	Usage   struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	} `json:"usage"`
}

func (a *Subprocess) Complete(ctx context.Context, msgs []Message, opts Options) (*Response, error) {
	prompt := BuildPrompt(msgs)

	args := []string{"-p", "--output-format", "stream-json", "--verbose",
		"--dangerously-skip-permissions", "--strict-mcp-config",
		"--tools", ""}
	if m := opts.Model; m != "" {
		args = append(args, "--model", m)
	}

	timeout := opts.Timeout
	if timeout == 0 {
		timeout = a.DefaultTimeout
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	// Disk-backed capture, like Python's _run_subprocess_safe: memory use
	// is bounded by the largest single line, never the whole transcript
	// (adversarial round 2026-08-22, Skeptic — the in-memory buffers grew
	// unbounded for the subprocess's whole lifetime).
	outF, err := os.CreateTemp("", "maro-go-claude-*.out")
	if err != nil {
		return nil, fmt.Errorf("create capture file: %w", err)
	}
	defer os.Remove(outF.Name())
	defer outF.Close()
	errF, err := os.CreateTemp("", "maro-go-claude-*.err")
	if err != nil {
		return nil, fmt.Errorf("create capture file: %w", err)
	}
	defer os.Remove(errF.Name())
	defer errF.Close()

	cmd := exec.CommandContext(cctx, a.Bin, args...)
	cmd.Stdin = strings.NewReader(prompt)
	cmd.Stdout = outF
	cmd.Stderr = errF

	runErr := cmd.Run()

	// Parse the capture for the result event regardless of exit status —
	// the Python adapter scans merged output because the CLI has emitted
	// valid results alongside nonzero exits.
	res, parseErr := scanForResult(outF.Name(), errF.Name())
	if res != nil {
		if res.IsError {
			// The whole diagnostic travels; the record boundary applies
			// the marked failure-chain clip. No bare first-line cut here
			// (adversarial round 2026-08-22, Expert QA).
			return nil, &ResultError{
				Msg:       "claude CLI result marked is_error: " + strings.TrimSpace(res.Result),
				TokensIn:  res.Usage.InputTokens,
				TokensOut: res.Usage.OutputTokens,
			}
		}
		if res.Subtype != "" && res.Subtype != "success" {
			return nil, &ResultError{
				Msg: fmt.Sprintf("claude CLI result subtype %q is not success: %s",
					res.Subtype, strings.TrimSpace(res.Result)),
				TokensIn:  res.Usage.InputTokens,
				TokensOut: res.Usage.OutputTokens,
			}
		}
		return &Response{
			Content:   res.Result,
			TokensIn:  res.Usage.InputTokens,
			TokensOut: res.Usage.OutputTokens,
		}, nil
	}
	if cctx.Err() == context.DeadlineExceeded {
		return nil, fmt.Errorf("claude CLI timed out after %s (purpose=%s)",
			timeout, opts.Purpose)
	}
	if runErr != nil {
		return nil, fmt.Errorf("claude CLI failed: %w — stderr: %s",
			runErr, fileText(errF.Name()))
	}
	return nil, fmt.Errorf("no result event in claude CLI output (purpose=%s): %v",
		opts.Purpose, parseErr)
}

// scanForResult walks the captured streams line-wise for the result
// event. Mirrors Python _parse_stream_json: the LAST result event wins
// (not the first — the CLI's output is not trusted to emit exactly one),
// and when no per-line event parsed at all, a whole-text extraction
// fallback covers a pretty-printed single object. A line that LOOKS like
// a result event but fails strict unmarshal is reported in the error
// rather than silently skipped — "no result event" must be
// distinguishable from "a result event we couldn't read" (adversarial
// round 2026-08-22, Expert QA).
func scanForResult(paths ...string) (*resultEvent, error) {
	var found *resultEvent
	var suspect string
	for _, path := range paths {
		f, err := os.Open(path)
		if err != nil {
			return nil, fmt.Errorf("open capture %s: %w", path, err)
		}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
		for sc.Scan() {
			line := bytes.TrimSpace(sc.Bytes())
			if len(line) == 0 || line[0] != '{' {
				continue
			}
			var ev resultEvent
			if err := json.Unmarshal(line, &ev); err != nil {
				if bytes.Contains(line, []byte(`"type":"result"`)) ||
					bytes.Contains(line, []byte(`"type": "result"`)) {
					suspect = fmt.Sprintf("a result-shaped line failed to parse (%v): %.500s", err, line)
				}
				continue
			}
			if ev.Type == "result" {
				e := ev
				found = &e
			}
		}
		scanErr := sc.Err()
		f.Close()
		if scanErr != nil {
			return nil, fmt.Errorf("scanning CLI capture: %w", scanErr)
		}
	}
	if found != nil {
		return found, nil
	}
	if ev := wholeTextResult(paths); ev != nil {
		return ev, nil
	}
	if suspect != "" {
		return nil, errors.New("no parseable {\"type\":\"result\"} event; " + suspect)
	}
	return nil, errors.New("no {\"type\":\"result\"} event")
}

// wholeTextResult is the Python _extract_result_object fallback: a
// pretty-printed single result object that line-scanning cannot see.
// Capped at 8MB per stream — past that, a transcript with no line-parsed
// result event is malformed beyond rescue, not pretty-printed.
func wholeTextResult(paths []string) *resultEvent {
	for _, path := range paths {
		st, err := os.Stat(path)
		if err != nil || st.Size() == 0 || st.Size() > 8*1024*1024 {
			continue
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		obj, err := jsonx.Object(string(raw))
		if err != nil || obj["type"] != "result" {
			continue
		}
		re, err := json.Marshal(obj)
		if err != nil {
			continue
		}
		var ev resultEvent
		if json.Unmarshal(re, &ev) == nil && ev.Type == "result" {
			return &ev
		}
	}
	return nil
}

// fileText reads a capture file whole for an error diagnostic. The
// caller's record boundary applies the marked clip; nothing is cut here.
func fileText(path string) string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Sprintf("(unreadable capture %s: %v)", path, err)
	}
	return strings.TrimSpace(string(raw))
}
