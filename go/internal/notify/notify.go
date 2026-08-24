// Package notify ports notify.py and escalation_context.py — how a
// substrate learns that something notification-worthy happened.
//
// Maro is a program, not an operating system: nothing listens and nothing
// polls. A substrate registers a command in config and Maro invokes it
// inside the run's own lifecycle, handing it the payload as JSON on stdin
// plus four env vars for shell dispatch without a JSON parser.
//
// Emit does three things, in this order, and the order is the contract:
//
//  1. ALWAYS appends a projected row to memory/events.jsonl, even with no
//     hook configured — the durable half a polling substrate reads.
//  2. For the ESCALATION CLASS, appends the full payload to
//     output/escalations.jsonl. This is the decreed headless escalation
//     surface (GOAL_BRAIN Decisions 2026-07-12): the thing an operator
//     checks when no substrate go-between is wired up. It ships whether or
//     not a notify lane exists and whether or not that lane succeeds, so a
//     silent failure here defeats its whole purpose — it is logged LOUD.
//  3. Runs the hook command, if one is configured AND the event is in the
//     configured event list.
//
// Emit never raises, and the boolean it returns means only "the hook
// command ran cleanly" — NOT "the notification was recorded". A false from
// a box with no hook configured is the normal, healthy case.
//
// # What this replaces
//
// A partial port of (1) and (2) lived as private helpers inside `scans`,
// carrying one event type. Those helpers are now this package's, called
// from there: the r8 round's whole finding was that a shared emitter with
// two copies drifts, and two copies of the escalation writer is the same
// shape of mistake one layer up.
//
// # Seams
//
// The hook command is a SPEND-AND-EXEC surface, so it is injected rather
// than reached for: RunOptions.Exec defaults to the real one, and no test
// in this package shells out. The clock is injected for the same reason
// the rest of the port injects it.
package notify

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// DefaultEvents is notify.py's DEFAULT_EVENTS, in its order. Order is not
// decorative: this list is written into config by operators who copy it,
// and a config `notify.events` that reorders it must still behave the same
// — membership is what is read, but the DEFAULT is what gets copied.
//
// Every entry is default-ON for one reason: on a headless box the notify
// channel is the only surface an away-from-keyboard user actually sees.
var DefaultEvents = []string{
	"run_completed",
	"escalation",
	"backend_actionable",
	"stranded_run",
	"resume_refused_busy",
	"resume_lock_unavailable",
	"recursion_checkin",
	"self_improvement_verdict",
	// Async-tail phase 2: the verdict follow-up to an answer-first
	// run_completed that went out with verdict_pending. Default-on —
	// the split is only honest if BOTH halves arrive.
	"run_verdict",
}

// escalationFileEvents is notify.py's ESCALATION_FILE_EVENTS: the events
// that are notify-worthy AND easy to miss with no hook configured.
// run_completed is deliberately absent — it already has a durable home in
// run_card.json.
//
// recursion_checkin rides this file too; consumers tell it apart from a
// park-the-goal escalation by its explicit `"blocking": false` field.
var escalationFileEvents = map[string]bool{
	"escalation":               true,
	"backend_actionable":       true,
	"stranded_run":             true,
	"resume_refused_busy":      true,
	"resume_lock_unavailable":  true,
	"recursion_checkin":        true,
	"self_improvement_verdict": true,
}

// IsEscalationClass reports whether an event lands in the durable ledger.
func IsEscalationClass(eventType string) bool {
	return escalationFileEvents[eventType]
}

// EscalationsPath is output/escalations.jsonl — the durable escalation-class
// log. It exists whether or not a notify.command lane is configured.
func EscalationsPath(ws string) string {
	return filepath.Join(ws, "output", "escalations.jsonl")
}

func eventsPath(ws string) string {
	return filepath.Join(ws, "memory", "events.jsonl")
}

// escalationClipFields are the payload keys bounded at 2000 before the
// escalation ledger is written.
//
// The bound lives HERE and not at the senders because per-sender bounding
// cannot be trusted at a shared boundary: Python's round-14 review found a
// sender putting 5,000 characters of raw navigator reasoning straight to
// disk while the captain's-log copy of the same text was clipped. Round 15
// then found the same hole again through two live ALIASES —
// `reasoning` / `summary_for_user`, which recursion check-ins ride — so
// the list carries the aliases, not just the three canonical names.
//
// A typed per-event schema is the deeper fix if senders keep minting
// aliases. Until then this list must stay synced with Emit's callers, and
// that is a real maintenance debt, stated rather than hidden.
var escalationClipFields = []string{
	"summary", "reason", "detail", "reasoning", "summary_for_user",
	"revert_detail",
}

// ExecFn runs the hook command. Injected so the exec surface is a seam.
// It receives the shell command line, the JSON payload for stdin, the
// environment, and a timeout; it returns the exit code and stderr.
type ExecFn func(ctx context.Context, command, stdin string, env []string,
	timeout time.Duration) (exitCode int, stderr string, timedOut bool, err error)

// LogFn receives the warnings this package refuses to swallow.
type LogFn func(format string, args ...any)

// Options are Emit's seams. The zero value is production behaviour.
type Options struct {
	// Cfg is the merged config. Nil means "read it" — a caller that
	// already has it should pass it rather than pay for a second load.
	Cfg map[string]any
	// RunDir, when set, becomes MARO_RUN_DIR for the hook.
	RunDir string
	// Exec defaults to the real subprocess runner.
	Exec ExecFn
	// Now defaults to time.Now.
	Now func() time.Time
	// Log defaults to stderr. Two things reach it: an escalation-ledger
	// write failure and a non-zero/timed-out hook.
	Log LogFn
	// Env is the base environment for the hook. Nil means os.Environ().
	Env []string
}

func (o *Options) fill() {
	if o.Exec == nil {
		o.Exec = runCommand
	}
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.Log == nil {
		o.Log = func(format string, args ...any) {
			fmt.Fprintf(os.Stderr, "[notify] "+format+"\n", args...)
		}
	}
}

// Emit fires one notification event.
//
// Returns true only when a hook command was configured, the event was in
// the configured list, and the command exited 0. Everything else — no
// hook, event not selected, non-zero exit, timeout — is false, and none of
// those are errors. Never panics: a notification must not be able to take
// down the run that produced it.
func Emit(ctx context.Context, ws, eventType string, payload map[string]any,
	opts Options) (ran bool) {
	defer func() {
		// Python wraps the whole body in `except Exception: return False`.
		// A panic here would be a Go bug rather than a Python-shaped
		// error, but the CONTRACT is "never takes the run down", and that
		// contract is the reason this function is called from finalizers.
		if r := recover(); r != nil {
			opts.Log("emit(%s) panicked: %v", eventType, r)
			ran = false
		}
	}()
	opts.fill()
	if payload == nil {
		payload = map[string]any{}
	}
	handleID := pyval.Str(payload["handle_id"])
	if _, ok := payload["handle_id"]; !ok {
		handleID = ""
	}
	status := ""
	if v, ok := payload["status"]; ok {
		status = pyval.Str(v)
	}

	writeEventRow(ws, eventType, payload, handleID, status, opts)

	if escalationFileEvents[eventType] {
		if err := writeEscalationFile(ws, eventType, payload, opts); err != nil {
			// Warning, not debug: unlike the events.jsonl write above,
			// this file is specifically pitched as "the thing you check
			// when nothing else is configured" (adversarial review
			// 2026-07-12).
			opts.Log("escalation file write failed for %s: %v", eventType, err)
		}
	}

	return runHook(ctx, eventType, payload, handleID, status, opts)
}

// writeEventRow is step 1: the projection every polling substrate reads.
func writeEventRow(ws, eventType string, payload map[string]any,
	handleID, status string, opts Options) {
	// `str(payload.get("result_excerpt", payload.get("summary", "")))`.
	//
	// The default only applies when the key is ABSENT. A present
	// result_excerpt of None projects the string "None", because that is
	// what str(None) is — porting this as "falsy means fall back" would
	// silently change which text reaches the substrate.
	var src any = ""
	if v, ok := payload["result_excerpt"]; ok {
		src = v
	} else if v, ok := payload["summary"]; ok {
		src = v
	}
	detail := budget.Clip(pyval.Str(src), 300)

	if eventType == "run_verdict" {
		// The verdict IS this event's content. The generic projection
		// above dropped it entirely and polling substrates received an
		// empty follow-up (review 2026-08-13). handle_id rides the detail
		// because write_event has no field for it.
		var b strings.Builder
		fmt.Fprintf(&b, "[%s] goal_achieved=%s", handleID,
			pyval.Str(payload["goal_achieved"]))
		if src, ok := payload["goal_verdict_source"]; ok && truthy(src) {
			fmt.Fprintf(&b, " source=%s", pyval.Str(src))
		}
		if truthy(payload["answer_changed"]) {
			b.WriteString(" answer_changed")
		}
		summary := ""
		if v, ok := payload["goal_verdict_summary"]; ok {
			summary = pyval.Str(v)
		}
		b.WriteString("; " + summary)
		detail = budget.Clip(b.String(), 300)
	}

	// `str(payload.get("goal", payload.get("reason", "")))[:200]` — a BARE
	// Python slice, which counts RUNES and announces nothing, not a clip.
	var goalSrc any = ""
	if v, ok := payload["goal"]; ok {
		goalSrc = v
	} else if v, ok := payload["reason"]; ok {
		goalSrc = v
	}
	goal := runeHead(pyval.Str(goalSrc), 200)

	WriteEvent(ws, eventType, goal, status, detail)
}

// WriteEvent appends one observe.write_event-shaped row to
// memory/events.jsonl — the cross-runtime feed maro-observe tails. Rows
// from either runtime must rehydrate in the other, so the field set and
// both breakers match the Python writer. Best-effort, exactly like
// Python's never-raises contract.
func WriteEvent(ws, eventType, goal, status, detail string) {
	dir := filepath.Dir(eventsPath(ws))
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return
	}
	entry := pyval.Obj{
		{Key: "event_type", Val: eventType},
		{Key: "ts", Val: pyval.NowISO(time.Now())},
		// write_event clips goal to 80 with a bare cut (its own contract);
		// the notify caller pre-clips to 200 first — net effect is 80.
		{Key: "goal", Val: runeHead(goal, 80)},
		{Key: "project", Val: ""},
		{Key: "loop_id", Val: ""},
		{Key: "step", Val: ""},
		{Key: "step_idx", Val: 0},
		{Key: "status", Val: status},
		{Key: "tokens_in", Val: 0},
		{Key: "tokens_out", Val: 0},
		{Key: "cache_read_tokens", Val: 0},
		{Key: "model", Val: ""},
		{Key: "elapsed_ms", Val: 0},
		// 200 is load-bearing (PIPE_BUF row atomicity downstream), and the
		// cut announces itself — budget.Clip is a breaker, not a silent
		// truncator.
		{Key: "detail", Val: budget.Clip(detail, 200)},
	}
	line, err := pyval.DumpsCompactPy(entry)
	if err != nil {
		return
	}
	_ = record.AppendRawLine(eventsPath(ws), []byte(line))
}

// writeEscalationFile is step 2: the durable ledger.
//
// Python builds `{"ts": ..., "event_type": ..., **payload}`, so a payload
// carrying its own `ts` or `event_type` OVERRIDES the value but KEEPS the
// leading position — that is what dict-literal-plus-splat does, and the
// two halves of that behaviour are easy to get half-right.
func writeEscalationFile(ws, eventType string, payload map[string]any,
	opts Options) error {
	// Idempotent, and it makes the zero Options usable at this entry point
	// too — a helper that only works when its caller remembered to fill the
	// seams is a helper with an undocumented precondition.
	opts.fill()
	entry := pyval.Obj{
		{Key: "ts", Val: pyval.NowISO(opts.Now())},
		{Key: "event_type", Val: eventType},
	}
	// Payload keys ride after, in sorted order: the payload is a Go map,
	// so Python's insertion order was gone before this function saw it.
	// A named loss, and the only one here — escaping, ensure_ascii and
	// float spelling are all recovered by pyval (mission-r8).
	for _, k := range sortedKeys(payload) {
		v := payload[k]
		if clipField(k) {
			if s, ok := v.(string); ok {
				// The ledger owns its bounds.
				v = budget.Clip(s, 2000)
			}
		}
		entry.Set(k, pyval.FromPlain(v))
	}

	p := EscalationsPath(ws)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return err
	}
	line, err := pyval.DumpsCompactPy(entry)
	if err != nil {
		return err
	}
	return record.AppendRawLine(p, []byte(line))
}

func clipField(k string) bool {
	for _, f := range escalationClipFields {
		if f == k {
			return true
		}
	}
	return false
}

// runHook is step 3.
func runHook(ctx context.Context, eventType string, payload map[string]any,
	handleID, status string, opts Options) bool {
	cfg := opts.Cfg
	if cfg == nil {
		cfg, _ = config.Load()
	}
	command := strings.TrimSpace(config.Get(cfg, "notify.command", ""))
	if command == "" {
		return false // no substrate registered a lane: the normal case
	}
	events := configEvents(cfg)
	if !contains(events, eventType) {
		return false
	}
	timeout := time.Duration(config.Get(cfg, "notify.timeout_seconds", 30.0) *
		float64(time.Second))

	env := opts.Env
	if env == nil {
		env = os.Environ()
	}
	env = append(append([]string{}, env...),
		"MARO_EVENT_TYPE="+eventType,
		"MARO_HANDLE_ID="+handleID,
		"MARO_STATUS="+status)
	if opts.RunDir != "" {
		env = append(env, "MARO_RUN_DIR="+opts.RunDir)
	}

	// `json.dumps({"event_type": event_type, **payload}, default=str)` —
	// event_type first, payload sorted after it for the same reason the
	// ledger sorts.
	stdinObj := pyval.Obj{{Key: "event_type", Val: eventType}}
	for _, k := range sortedKeys(payload) {
		stdinObj.Set(k, pyval.FromPlain(payload[k]))
	}
	stdin, err := pyval.DumpsCompactPy(stdinObj)
	if err != nil {
		return false
	}

	code, stderr, timedOut, err := opts.Exec(ctx, command, stdin, env, timeout)
	if timedOut {
		opts.Log("notify.command timed out after %.0fs for %s (%s)",
			timeout.Seconds(), eventType, handleID)
		return false
	}
	if err != nil {
		opts.Log("notify.command failed for %s (%s): %v", eventType, handleID, err)
		return false
	}
	if code != 0 {
		opts.Log("notify.command exited %d for %s (%s): %s",
			code, eventType, handleID, runeHead(stderr, 200))
		return false
	}
	return true
}

// runCommand is the production ExecFn: `subprocess.run(command, shell=True,
// input=..., capture_output=True, text=True, timeout=..., env=...)`.
func runCommand(ctx context.Context, command, stdin string, env []string,
	timeout time.Duration) (int, string, bool, error) {
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	// shell=True: the config value is a command LINE, and an operator's
	// registered hook is expected to use pipes and redirects.
	cmd := exec.CommandContext(cctx, "/bin/sh", "-c", command)
	cmd.Stdin = strings.NewReader(stdin)
	cmd.Env = env
	var errBuf strings.Builder
	cmd.Stderr = &errBuf
	cmd.Stdout = &strings.Builder{} // capture_output: not the run's stdout
	err := cmd.Run()
	if cctx.Err() == context.DeadlineExceeded {
		return 0, errBuf.String(), true, nil
	}
	if ee, ok := err.(*exec.ExitError); ok {
		return ee.ExitCode(), errBuf.String(), false, nil
	}
	if err != nil {
		return 0, errBuf.String(), false, err
	}
	return 0, errBuf.String(), false, nil
}

// truthy is Python's bool(), which is pyval.Truthy and NOT pyval.Bool.
//
// It was pyval.Bool for one revision, and that is a bare type assertion —
// `goal_verdict_source: "judge"` read as FALSE and the source clause
// vanished from every run_verdict row. pyval.Bool documents itself
// correctly for its own callers; the mistake was mine, and the comment
// asserting "Python's bool()" over it was the load-bearing false claim
// (r2's lens: a claim in a comment is load-bearing).
func truthy(v any) bool { return pyval.Truthy(v) }

// configEvents reads `notify.events`.
//
// Python is `_config_get("notify.events", DEFAULT_EVENTS) or DEFAULT_EVENTS`,
// and there are two behaviours in that line worth naming:
//
//   - An EMPTY list is falsy, so `notify.events: []` gets the DEFAULTS, not
//     silence. Reading it as "opt out of everything" would be the natural
//     guess and it is wrong.
//   - The list is never type-checked. YAML hands it over as a list of
//     whatever the scalars parsed to, and membership is `in`. A list of
//     strings is the only shape any operator writes, so non-strings are
//     rendered with str() here rather than dropped — dropping would make a
//     `notify.events: [run_completed, 5]` silently narrower on this side
//     than on Python's.
//
// The one shape NOT reproduced: a config setting `notify.events` to a bare
// STRING, where Python's `in` becomes a SUBSTRING test ("escalation" in
// "escalation_only" is True). That is a config error either way; here it is
// treated as a one-element list, which is stricter, and it is a named
// divergence rather than an accident.
func configEvents(cfg map[string]any) []string {
	raw, present := config.Lookup(cfg, "notify.events")
	if !present {
		return DefaultEvents
	}
	var out []string
	switch v := raw.(type) {
	case []string:
		out = v
	case []any:
		for _, e := range v {
			out = append(out, pyval.Str(e))
		}
	case string:
		out = []string{v}
	default:
		return DefaultEvents
	}
	if len(out) == 0 {
		return DefaultEvents
	}
	return out
}

func contains(list []string, s string) bool {
	for _, e := range list {
		if e == s {
			return true
		}
	}
	return false
}

func sortedKeys(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// runeHead is a Python string slice `s[:n]` — RUNES, not bytes, and no
// marker. Distinct from budget.Clip, which announces its cut.
func runeHead(s string, n int) string {
	if n <= 0 {
		return ""
	}
	count := 0
	for idx := range s {
		if count == n {
			return s[:idx]
		}
		count++
	}
	return s
}
