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
//	--dangerously-skip-permissions --strict-mcp-config
//
// Two lanes, matching the Python adapter's no_tools split:
//
//   - Utility (default): --tools "" — routing/classification/planning
//     calls hold no tool access, because an agentic -p session can
//     otherwise ACT on text it was asked to classify (BACKLOG #16).
//     Output is ceilinged by CLAUDE_CODE_MAX_OUTPUT_TOKENS as a runaway
//     brake (Python _NO_TOOLS_OUTPUT_CEILING).
//   - Executor (Options.AgentTools): the CLI's own tools do the real
//     work; WebFetch/WebSearch are disallowed (Python parity — web
//     ingest goes through capped fetch paths, not raw page loads), the
//     working dir binds to Options.Cwd, and per-tool-call Bash output is
//     capped via BASH_MAX_OUTPUT_LENGTH (bashOutputCapEnv). Unported
//     from Python's executor lane, named in PORT.md: container wrap,
//     executor sessions, fork-master, token-runaway brake.
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

// FindClaudeBin validates every candidate the way Python
// _claude_bin_available does (isfile + X_OK) — a stale CLAUDE_BIN must
// not make the auto backend commit to a broken subprocess and skip the
// anthropic fallback (adversarial r2 2026-08-22, Skeptic).
func FindClaudeBin() (string, error) {
	if v := os.Getenv("CLAUDE_BIN"); v != "" {
		if isExecutableFile(v) {
			return v, nil
		}
		return "", fmt.Errorf("CLAUDE_BIN=%q is not an executable file", v)
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
		if isExecutableFile(cand) {
			return cand, nil
		}
	}
	return "", errors.New("claude CLI not found (CLAUDE_BIN, PATH, known locations)")
}

func isExecutableFile(path string) bool {
	st, err := os.Stat(path)
	return err == nil && st.Mode().IsRegular() && st.Mode().Perm()&0o111 != 0
}

func (a *Subprocess) Name() string { return "subprocess" }

// SupportsAgentTools marks this backend able to run tool-bearing worker
// executor steps (the CLI carries its own tool set). The loop checks
// this before enabling executor mode — a backend without it runs the
// tool-less v0 step path instead, loudly, never a silent degrade.
func (a *Subprocess) SupportsAgentTools() bool { return true }

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

// buildArgsEnv derives the flag set and child-env overrides for one call
// — split out so tests can pin the two lanes' contracts without spawning
// anything.
func buildArgsEnv(opts Options) (args []string, env []envOverride) {
	args = []string{"-p", "--output-format", "stream-json", "--verbose",
		"--dangerously-skip-permissions", "--strict-mcp-config"}
	if opts.AgentTools {
		// Honest scope: this deny-list stops the CLI's NATIVE fetch tools
		// only — Bash stays enabled, so it is NOT an egress boundary (a
		// curl runs fine); the prompt's URL-fetching discipline plus the
		// Bash output cap are containment, not prevention. Python is
		// identical at this line, but there the container lane can be the
		// real boundary; that lane is unported here (PORT.md), so nothing
		// stronger stands behind this flag yet (adversarial exec review
		// 2026-08-22, Architect).
		args = append(args, "--disallowedTools", "WebFetch,WebSearch")
		env = bashOutputCapEnv()
	} else {
		// "" disables the built-in tool set entirely, stronger than
		// denying names (Python parity, BACKLOG #16).
		args = append(args, "--tools", "")
		env = []envOverride{{Key: "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
			Value: fmt.Sprintf("%d", noToolsOutputCeiling), Set: true}}
	}
	if m := opts.Model; m != "" {
		args = append(args, "--model", m)
	}
	return args, env
}

func (a *Subprocess) Complete(ctx context.Context, msgs []Message, opts Options) (*Response, error) {
	prompt := BuildPrompt(msgs, opts.Tools...)
	args, envOv := buildArgsEnv(opts)

	timeout := opts.Timeout
	if timeout == 0 {
		timeout = a.DefaultTimeout
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	// Disk-backed capture, like Python's _run_subprocess_safe: memory use
	// is bounded by the largest single line, never the whole transcript
	// (adversarial round 2026-08-22, Skeptic — the in-memory buffers grew
	// unbounded for the subprocess's whole lifetime). ONE merged file,
	// exactly Python's stdout=combined_f, stderr=subprocess.STDOUT:
	// split-file capture threw away chronology, so a stray stderr
	// result-shaped line always beat the real stdout result (adversarial
	// r2 2026-08-22, Expert QA).
	//
	// With Options.TranscriptPath the capture is written there and KEPT —
	// the caller's durable per-step record of what the inner agent's
	// tools did (artifacts-over-streams). Data-retention doctrine: the
	// kept file is never deleted here, success or failure. A transcript
	// that cannot be created degrades to a temp capture WITH a warning —
	// hard-failing here would block the step over an artifact and leave a
	// record blaming the model for an OS-path error (adversarial exec
	// review 2026-08-22, Expert QA: the mkdir sibling in the loop already
	// degrades softly; the twins must agree).
	var capF *os.File
	var err error
	var transcriptWarn string
	if opts.TranscriptPath != "" {
		capF, err = os.Create(opts.TranscriptPath)
		if err != nil {
			transcriptWarn = fmt.Sprintf(
				"transcript file %s could not be created (%v) — step ran with a temp capture only",
				opts.TranscriptPath, err)
			capF = nil
		} else {
			defer capF.Close()
		}
	}
	if capF == nil {
		capF, err = os.CreateTemp("", "maro-go-claude-*.jsonl")
		if err != nil {
			return nil, fmt.Errorf("create capture file: %w", err)
		}
		defer os.Remove(capF.Name())
		defer capF.Close()
	}

	cmd := exec.CommandContext(cctx, a.Bin, args...)
	cmd.Stdin = strings.NewReader(prompt)
	cmd.Stdout = capF
	cmd.Stderr = capF
	cmd.Env = childEnv(envOv)
	// Bind the executor to the caller's project dir so relative writes
	// land in-workspace (Python step_exec passes cwd=project_dir). The
	// caller creates the directory; exec fails loudly on a missing one.
	cmd.Dir = opts.Cwd

	runErr := cmd.Run()

	// Parse the capture for the result event regardless of exit status —
	// the Python adapter scans merged output because the CLI has emitted
	// valid results alongside nonzero exits.
	res, suspects, parseErr := scanForResult(capF.Name())
	if transcriptWarn != "" {
		// Rides every exit path's warning surface (Response.Warnings on
		// success, ResultError.Warnings on error results) — degraded
		// retention must reach the operator, never die here.
		suspects = append([]string{transcriptWarn}, suspects...)
	}
	if res != nil {
		if res.IsError {
			// The whole diagnostic travels; the record boundary applies
			// the marked failure-chain clip. No bare first-line cut here
			// (adversarial round 2026-08-22, Expert QA).
			return nil, &ResultError{
				Msg:       "claude CLI result marked is_error: " + strings.TrimSpace(res.Result),
				TokensIn:  res.Usage.InputTokens,
				TokensOut: res.Usage.OutputTokens,
				Warnings:  suspects,
			}
		}
		if res.Subtype != "success" {
			// Python _extract_result_success requires the literal equality
			// — a MISSING subtype fails there too, so it fails here
			// (adversarial r2 2026-08-22, Skeptic: "not present-and-wrong"
			// is weaker than "present-and-right").
			return nil, &ResultError{
				Msg: fmt.Sprintf("claude CLI result subtype %q is not success: %s",
					res.Subtype, strings.TrimSpace(res.Result)),
				TokensIn:  res.Usage.InputTokens,
				TokensOut: res.Usage.OutputTokens,
				Warnings:  suspects,
			}
		}
		return &Response{
			Content:   res.Result,
			TokensIn:  res.Usage.InputTokens,
			TokensOut: res.Usage.OutputTokens,
			// A result-shaped line that failed to parse BESIDE the one
			// that succeeded is still worth the operator's eye
			// (adversarial r2: the diagnostic was dropped on success).
			Warnings: suspects,
		}, nil
	}
	if cctx.Err() == context.DeadlineExceeded {
		return nil, fmt.Errorf("claude CLI timed out after %s (purpose=%s)",
			timeout, opts.Purpose)
	}
	if runErr != nil {
		return nil, fmt.Errorf("claude CLI failed: %w — output: %s",
			runErr, fileText(capF.Name()))
	}
	return nil, fmt.Errorf("no result event in claude CLI output (purpose=%s): %v",
		opts.Purpose, parseErr)
}

// scanForResult walks the single merged capture line-wise for the
// result event. Mirrors Python _parse_stream_json over its merged fd:
// the LAST result event in true write order wins (not the first — the
// CLI's output is not trusted to emit exactly one), and when no
// per-line event parsed at all, a whole-text extraction fallback covers
// a pretty-printed single object. Every line that LOOKS like a result
// event but fails strict unmarshal is collected — on failure it rides
// the error, on success it rides the caller's warnings; either way "no
// result event" stays distinguishable from "a result event we couldn't
// read" (adversarial rounds 1+2, Expert QA).
func scanForResult(path string) (*resultEvent, []string, error) {
	var found *resultEvent
	var suspects []string
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, fmt.Errorf("open capture %s: %w", path, err)
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
				suspects = append(suspects, fmt.Sprintf(
					"a result-shaped line failed to parse (%v): %.500s", err, line))
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
		return nil, nil, fmt.Errorf("scanning CLI capture: %w", scanErr)
	}
	if found != nil {
		return found, suspects, nil
	}
	if ev := wholeTextResult(path); ev != nil {
		return ev, suspects, nil
	}
	if len(suspects) > 0 {
		return nil, suspects, errors.New(
			"no parseable {\"type\":\"result\"} event; " + strings.Join(suspects, "; "))
	}
	return nil, nil, errors.New("no {\"type\":\"result\"} event")
}

// wholeTextResult is the Python _extract_result_object fallback: a
// pretty-printed single result object that line-scanning cannot see.
// Capped at 8MB — past that, a transcript with no line-parsed result
// event is malformed beyond rescue, not pretty-printed.
func wholeTextResult(path string) *resultEvent {
	st, err := os.Stat(path)
	if err != nil || st.Size() == 0 || st.Size() > 8*1024*1024 {
		return nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	obj, err := jsonx.Object(string(raw))
	if err != nil || obj["type"] != "result" {
		return nil
	}
	re, err := json.Marshal(obj)
	if err != nil {
		return nil
	}
	var ev resultEvent
	if json.Unmarshal(re, &ev) == nil && ev.Type == "result" {
		return &ev
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
