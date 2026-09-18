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
	"path/filepath"
	"sort"
	"strings"
	"sync"
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
// set entirely); tool calls carry the operator's ToolPolicy as
// --allowedTools / --disallowedTools (default: deny WebFetch/WebSearch).
type Subprocess struct {
	Bin            string
	Model          string
	DefaultTimeout time.Duration
	Lookup         func(string) (string, error) // exec.LookPath seam
	Policy         ToolPolicy                   // the operator's tool policy for tool-bearing requests
	// Env is appended to the child's environment for TOOL-BEARING requests
	// only ("NAME=value"): the secrets store's injection (docs/SECRETS_DESIGN
	// .md) plus the derived-secret drop path. Tool-less calls (judges,
	// intent) never see it.
	Env []string
	// Redact maps injected NAME → value; every value is replaced by
	// [REDACTED:NAME] in the response and the captured transcript of a
	// tool-bearing call, so a goal-driven `env` never persists a secret.
	Redact map[string]string
	// AfterTools runs after every tool-bearing call returns (the drop-file
	// ingest). Nil = nothing.
	AfterTools func()
	// HandOff, when set with Lines, is written as a 0600 file at Path before
	// every TOOL-BEARING call and shredded when the call returns (any path);
	// the child sees only EnvName=Path. This is how injected secret VALUES
	// reach a host worker — never through Env, which every descendant of
	// the child inherits (docs/SECRETS_DESIGN.md §10). On the container
	// lane the same file is bind-mounted read-only at the same path, so the
	// worker reads it exactly as it would on the host.
	HandOff *HandOff
	// Writable are absolute paths a TOOL-BEARING call must be able to write
	// (the operator-question file, the derived-secrets drop): the CLI names
	// them in Env, and on the container lane their directories are bound
	// read-write at the same absolute path, so a containerized worker asks
	// and hands back exactly as a host worker does.
	Writable []string
	// Container is the container launcher tool-bearing calls run through
	// when the policy asks for it; nil = the host lane only.
	Container *Container
	// Isolation is the operator's setting (executor.go): "" or off = host,
	// on = container when it can run, require = container or nothing. This
	// backend is the policy's ONE owner: the attempt records it by asking
	// (ExecutorPolicy), so nothing has to keep two copies equal.
	Isolation ExecutorPolicy
	// Notify is told, once per distinct note, about anything the operator
	// should know about where a call ran: a degrade to the host under `on`,
	// or a container the engine could not end. The record carries the
	// facts, so this is a courtesy, not the evidence. Nil = nothing.
	Notify func(note string)

	noteMu sync.Mutex
	noted  map[string]bool
}

// ExecutorPolicy is the policy every attempt of this backend runs under
// (invoke.Isolated).
func (s *Subprocess) ExecutorPolicy() ExecutorPolicy { return s.Isolation }

// ToolEnv is the environment this backend's tool-bearing calls get beyond
// the process's own (run.ToolEnver): a re-run of what they ran gets it too.
// The per-call secrets hand-off file is not reproducible and is not here.
func (s *Subprocess) ToolEnv() []string { return s.Env }

// HandOff is the per-call secrets file the subprocess backend hands a
// tool-bearing child (see Subprocess.HandOff).
type HandOff struct {
	Path    string
	EnvName string
	Lines   []string // "NAME=value"
}

// write creates the file fresh with mode 0600 (a stale copy is replaced).
func (h *HandOff) write() error {
	if err := os.MkdirAll(filepath.Dir(h.Path), 0o700); err != nil {
		return err
	}
	if err := os.Remove(h.Path); err != nil && !os.IsNotExist(err) {
		return err
	}
	f, err := os.OpenFile(h.Path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	for _, l := range h.Lines {
		if _, err := f.WriteString(l + "\n"); err != nil {
			f.Close()
			return err
		}
	}
	return f.Close()
}

// shred zero-fills and removes the file; a missing file is fine.
func (h *HandOff) shred() {
	st, err := os.Stat(h.Path)
	if err != nil {
		return
	}
	if f, err := os.OpenFile(h.Path, os.O_WRONLY, 0); err == nil {
		f.Write(make([]byte, st.Size()))
		f.Sync()
		f.Close()
	}
	os.Remove(h.Path)
}

const subprocessName = "subprocess"

// NewSubprocess finds the claude binary.
func NewSubprocess(model string) (*Subprocess, error) {
	s := &Subprocess{Model: model, DefaultTimeout: 20 * time.Minute, Lookup: exec.LookPath, Policy: DefaultToolPolicy()}
	bin, err := s.Lookup("claude")
	if err != nil {
		return nil, fmt.Errorf("%w: claude CLI not found: %v", ErrBeforeDispatch, err)
	}
	s.Bin = bin
	return s, nil
}

func (s *Subprocess) Capabilities() Capabilities {
	return Capabilities{Name: subprocessName, Model: s.Model, ActsOutward: true, OutwardReconcilable: false, ReadsByReference: true, ToolPolicy: s.Policy.String()}
}

// launcher is the venue of a call that is about to be dispatched. The venue
// is decided ONCE per call: when the shell has already asked (ExecutorFor)
// and committed the answer on the invocation, the call runs THERE and
// nothing is decided again. Deciding twice is how a record and a call come
// to disagree — the first probe fails and commits `host`, the operator
// starts docker, and the second probe sends the call into a container the
// journal says nothing about (review r1).
func (s *Subprocess) launcher(ctx context.Context, req Request) (Launcher, error) {
	if req.Executor == nil {
		// nobody asked: an unshelled call (a direct Complete) decides here,
		// under the CALLER's context — a preflight that outlived the call
		// that wanted it would hold a cancelled caller for two docker
		// timeouts (review r2)
		return s.decide(ctx, req)
	}
	if req.Executor.Kind != ExecutorContainer {
		return HostLauncher{}, nil
	}
	if s.Container == nil {
		return nil, fmt.Errorf("%w: the call was committed to a container and this backend has none", ErrBeforeDispatch)
	}
	// The committed venue is the WHOLE venue, not just its kind: the record
	// names an image, an id and a network, and a launcher that would run
	// something else is not the venue the invocation was committed to. This
	// cannot normally differ (the choice is made once, and the id is
	// resolved before the record is written) — which is exactly why a
	// difference here means something changed underneath and the call must
	// not go out (review r2).
	if now := s.Container.Executor(); now != *req.Executor {
		return nil, fmt.Errorf("%w: the call was committed to %+v and this launcher would run %+v", ErrBeforeDispatch, *req.Executor, now)
	}
	return s.Container, nil
}

// decide is where the venue is CHOSEN (and the container preflighted): the
// host for every tool-less call (a judge touches nothing, so isolating it
// buys nothing and costs a container per verdict) and for every lane the
// operator left off; otherwise the container. Under `require` a container
// that cannot run refuses the call before dispatch — nothing is recorded
// and nothing ran; under `on` it degrades to the host, which the invocation
// records and Notify announces.
func (s *Subprocess) decide(ctx context.Context, req Request) (Launcher, error) {
	if !req.Tools || s.Isolation == "" || s.Isolation == ExecutorOff {
		return HostLauncher{}, nil
	}
	if s.Container == nil {
		// `require` with nothing to require: refuse before dispatch rather
		// than run on the host and let the fold refuse the record afterwards
		if s.Isolation == ExecutorRequire {
			return nil, fmt.Errorf("%w: %w: this backend has no container to run in", ErrBeforeDispatch, ErrExecutorUnavailable)
		}
		s.notify("ran on the host instead: this backend has no container to run in")
		return HostLauncher{}, nil
	}
	if err := s.Container.Preflight(ctx); err != nil {
		if s.Isolation == ExecutorRequire {
			return nil, fmt.Errorf("%w: %w", ErrBeforeDispatch, err)
		}
		s.notify("ran on the host instead: " + err.Error())
		return HostLauncher{}, nil
	}
	return s.Container, nil
}

// notify tells the operator once per distinct note: the invocation records
// the facts, so this is the notice, not the record.
func (s *Subprocess) notify(note string) {
	s.noteMu.Lock()
	first := !s.noted[note]
	if first {
		if s.noted == nil {
			s.noted = map[string]bool{}
		}
		s.noted[note] = true
	}
	s.noteMu.Unlock()
	if first && s.Notify != nil {
		s.Notify(note)
	}
}

// ExecutorFor answers where a call of this request will run, before the
// invocation is committed (invoke.Executored). It is the ONLY place the
// venue is chosen; Complete runs the call where this answer said.
func (s *Subprocess) ExecutorFor(ctx context.Context, req Request) (Executor, error) {
	l, err := s.decide(ctx, req)
	if err != nil {
		return Executor{}, err
	}
	return l.Executor(), nil
}

func (s *Subprocess) args(req Request) []string {
	a := []string{"-p", "--output-format", "stream-json", "--verbose", "--dangerously-skip-permissions", "--strict-mcp-config"}
	if req.Tools {
		if len(s.Policy.Allow) > 0 {
			a = append(a, "--allowedTools", strings.Join(s.Policy.Allow, ","))
		}
		if len(s.Policy.Deny) > 0 {
			a = append(a, "--disallowedTools", strings.Join(s.Policy.Deny, ","))
		}
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
	// Where this call runs was decided before the invocation was committed
	// (ExecutorFor) and rides on the request: the record and the call are
	// the same decision, not two that happen to agree.
	lr, err := s.launcher(ctx, req)
	if err != nil {
		return nil, err
	}
	l := Launch{Bin: s.Bin, Args: s.args(req), Cwd: req.Cwd}
	if req.Tools {
		l.Env = append(l.Env, s.Env...)
		l.Writable = append(l.Writable, s.Writable...)
		if s.HandOff != nil && len(s.HandOff.Lines) > 0 {
			if err := s.HandOff.write(); err != nil {
				return nil, fmt.Errorf("%w: secrets hand-off: %v", ErrBeforeDispatch, err)
			}
			defer s.HandOff.shred()
			l.Env = append(l.Env, s.HandOff.EnvName+"="+s.HandOff.Path)
			l.Files = append(l.Files, s.HandOff.Path)
		}
	}
	lch, err := lr.Wrap(l)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrBeforeDispatch, err)
	}
	// Ending the work, on EVERY way out of this function.
	//
	// On the container lane the child process is the docker CLIENT, not the
	// worker: the client can die — killed independently, or dropped by the
	// daemon — while the container keeps running, holding the work dir and
	// the mounted channels. The first version of this stopped the container
	// only when the call's own context had been cancelled, which covered the
	// deadline and the operator's ^C and missed exactly the cases where the
	// engine did not know the child was gone (review r2: an independently
	// killed client, and a panic, both leave a live container behind a
	// context that was never cancelled).
	//
	// So: stop unconditionally, once, under a context the cancellation
	// cannot reach, and BEFORE the engine ingests what the worker wrote and
	// shreds what it could read. `--rm` means the normal case is "there is
	// nothing left to end", which the launcher reports as success.
	// One goroutine walks this function, so `done` needs no lock; the
	// second call (the defer, after the inline one) is the no-op.
	done, stopErr := false, error(nil)
	stop := func() error {
		if lch.Stop == nil || done {
			return stopErr
		}
		done = true
		sctx, scancel := context.WithTimeout(context.WithoutCancel(ctx), probeTimeout)
		defer scancel()
		if stopErr = lch.Stop(sctx); stopErr != nil {
			s.notify("could not end the container the call ran in: " + stopErr.Error())
		}
		return stopErr
	}
	defer stop() // a panic, an early return, anything
	cmd := exec.CommandContext(cctx, lch.Argv[0], lch.Argv[1:]...)
	cmd.Stdin = bytes.NewReader(req.Prompt)
	cmd.Env = lch.Env
	cmd.Dir = lch.Dir
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
	// The client has exited; the container is either gone with it (`--rm`)
	// or it outlived it, and either way this is where that is settled —
	// before the terminal is classified, and before AfterTools ingests the
	// worker's drop file.
	stop()
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
	if stopErr != nil {
		reasons = append(reasons, "container not ended: "+stopErr.Error())
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
		if p.violations > 0 || capErr != nil || stopErr != nil {
			// a container the engine could not end is a call whose side
			// effects it cannot say have stopped: complete is too strong
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
	if req.Tools {
		if len(s.Redact) > 0 {
			res.Response = redact(res.Response, s.Redact)
			res.Transcript = redact(res.Transcript, s.Redact)
			res.Reason = string(redact([]byte(res.Reason), s.Redact))
		}
		// The drop file is read only when the work is known to have STOPPED.
		// A container the engine could not end is a worker that can still
		// rewrite the channel this is about to ingest — and ingesting a
		// derived secret written by a call the engine has lost is worse than
		// not ingesting one (review r3). The file stays where it is; the
		// next call's ingest picks it up once the box is sane again.
		switch {
		case stopErr != nil:
			res.Reason = strings.TrimSpace(res.Reason + "; the worker's channels were not read: the container could not be ended")
		case s.AfterTools != nil:
			s.AfterTools()
		}
	}
	return res, nil
}

// redact replaces every value in text with [REDACTED:NAME], longest value
// first so an overlapping shorter secret cannot leave the tail of a longer
// one behind. Kept local (invoke stays a leaf; secrets.Redact is the same
// rule for callers outside the backend).
func redact(text []byte, values map[string]string) []byte {
	type kv struct{ k, v string }
	var items []kv
	for k, v := range values {
		if v != "" {
			items = append(items, kv{k, v})
		}
	}
	sort.Slice(items, func(i, j int) bool {
		if len(items[i].v) != len(items[j].v) {
			return len(items[i].v) > len(items[j].v)
		}
		return items[i].k < items[j].k
	})
	for _, it := range items {
		text = bytes.ReplaceAll(text, []byte(it.v), []byte("[REDACTED:"+it.k+"]"))
	}
	return text
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
