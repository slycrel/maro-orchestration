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
)

// Subprocess drives the `claude` CLI in print mode — no API key needed,
// same backend the Python ClaudeSubprocessAdapter uses. The flag set
// mirrors the Python adapter verbatim (each flag there is
// incident-derived; see src/llm.py comments for the provenance):
//
//   -p --output-format stream-json --verbose
//   --dangerously-skip-permissions --strict-mcp-config --tools ""
//
// v0 scope: utility-style calls only (--tools "" always). The Python
// runtime disables tools for routing/classification because an agentic
// -p session can otherwise ACT on text it was asked to classify (BACKLOG
// #16); this port's first slice runs its whole loop in that safer mode
// and treats tool-bearing worker steps as the next port tranche.
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
// payload is what the old --output-format json produced.
type resultEvent struct {
	Type    string `json:"type"`
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

	cmd := exec.CommandContext(cctx, a.Bin, args...)
	cmd.Stdin = strings.NewReader(prompt)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	runErr := cmd.Run()

	// Parse the stream for the result event regardless of exit status —
	// the Python adapter scans merged output because the CLI has emitted
	// valid results alongside nonzero exits.
	res, parseErr := scanForResult(stdout.Bytes(), stderr.Bytes())
	if res != nil {
		if res.IsError {
			return nil, fmt.Errorf("claude CLI result marked is_error: %s",
				firstLine(res.Result))
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
			runErr, firstLine(stderr.String()))
	}
	return nil, fmt.Errorf("no result event in claude CLI output (purpose=%s): %v",
		opts.Purpose, parseErr)
}

// scanForResult walks stdout then stderr line-wise for the result event.
func scanForResult(streams ...[]byte) (*resultEvent, error) {
	for _, raw := range streams {
		sc := bufio.NewScanner(bytes.NewReader(raw))
		sc.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
		for sc.Scan() {
			line := bytes.TrimSpace(sc.Bytes())
			if len(line) == 0 || line[0] != '{' {
				continue
			}
			var ev resultEvent
			if err := json.Unmarshal(line, &ev); err != nil {
				continue
			}
			if ev.Type == "result" {
				return &ev, nil
			}
		}
		if err := sc.Err(); err != nil {
			return nil, fmt.Errorf("scanning CLI output: %w", err)
		}
	}
	return nil, errors.New("no {\"type\":\"result\"} event")
}

func firstLine(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}
