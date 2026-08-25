package notify

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The hook command is the half `scans` deferred as a "spend/exec surface".
// Deferring the CALL was defensible; shipping it with no test would not be,
// because a substrate that registered a lane and never heard from it looks
// exactly like a substrate whose events are all filtered out.

// recorder is a fake ExecFn that captures the one invocation it gets.
type recorder struct {
	calls    int
	command  string
	stdin    string
	env      []string
	timeout  time.Duration
	code     int
	timedOut bool
}

func (r *recorder) fn(_ context.Context, command, stdin string, env []string,
	timeout time.Duration) (int, string, bool, error) {
	r.calls++
	r.command, r.stdin, r.env, r.timeout = command, stdin, env, timeout
	return r.code, "boom", r.timedOut, nil
}

func (r *recorder) envOf(key string) (string, bool) {
	for _, e := range r.env {
		if strings.HasPrefix(e, key+"=") {
			return e[len(key)+1:], true
		}
	}
	return "", false
}

func TestHookReceivesTheSubstrateContract(t *testing.T) {
	ws := t.TempDir()
	rec := &recorder{}
	cfg := map[string]any{"notify": map[string]any{
		"command":         "  bash notify.sh  ", // .strip() on the other side
		"timeout_seconds": 5,                    // an INT in YAML, a float in Python
	}}
	ok := Emit(context.Background(), ws, "escalation", map[string]any{
		"handle_id": "h-42",
		"status":    "blocked",
		"reason":    "prefer a > b in the café path → retry",
		"count":     3,
	}, Options{Cfg: cfg, RunDir: "/runs/h-42", Exec: rec.fn, Env: []string{"PATH=/bin"}})

	if !ok {
		t.Fatal("a clean exit 0 must report true")
	}
	if rec.calls != 1 {
		t.Fatalf("hook ran %d times", rec.calls)
	}
	if rec.command != "bash notify.sh" {
		t.Fatalf("command not stripped: %q", rec.command)
	}
	if rec.timeout != 5*time.Second {
		t.Fatalf("timeout: %v (an int in config must read as seconds)", rec.timeout)
	}
	// The four env vars exist for shell dispatch WITHOUT a JSON parser —
	// they are the reason a substrate can be three lines of bash.
	for k, want := range map[string]string{
		"MARO_EVENT_TYPE": "escalation",
		"MARO_HANDLE_ID":  "h-42",
		"MARO_STATUS":     "blocked",
		"MARO_RUN_DIR":    "/runs/h-42",
	} {
		if got, ok := rec.envOf(k); !ok || got != want {
			t.Errorf("%s = %q (present=%v), want %q", k, got, ok, want)
		}
	}
	if got, _ := rec.envOf("PATH"); got != "/bin" {
		t.Errorf("the caller's environment was dropped: PATH=%q", got)
	}

	// stdin is `{"event_type": ..., **payload}` — event_type FIRST, and the
	// payload spelled the way json.dumps spells it, because the hook is a
	// cross-runtime consumer like any other.
	if !strings.HasPrefix(rec.stdin, `{"event_type": "escalation", `) {
		t.Fatalf("stdin does not lead with event_type: %s", rec.stdin)
	}
	// ensure_ascii: the ESCAPE SEQUENCES are what goes on the wire, not the
	// runes — six literal characters, which is why these needles are raw
	// string literals.
	if !strings.Contains(rec.stdin, "caf\\u00e9") ||
		!strings.Contains(rec.stdin, "\\u2192") {
		t.Fatalf("stdin is not ensure_ascii: %s", rec.stdin)
	}
	// And the opposite fork: `>` stays a `>`. encoding/json writes the six
	// characters \u003e here and no CPython writer ever does.
	if strings.Contains(rec.stdin, "\\u003e") {
		t.Fatalf("stdin is HTML-escaped, which json.dumps never is: %s", rec.stdin)
	}
	if !strings.Contains(rec.stdin, `a > b`) {
		t.Fatalf("stdin lost the raw `>`: %s", rec.stdin)
	}
	// It must still PARSE — a hook that json.loads() stdin is the normal
	// case, and ensure_ascii output is valid JSON by construction.
	var back map[string]any
	if err := json.Unmarshal([]byte(rec.stdin), &back); err != nil {
		t.Fatalf("stdin is not valid JSON: %v\n%s", err, rec.stdin)
	}
	if back["reason"] != "prefer a > b in the café path → retry" {
		t.Fatalf("stdin does not round-trip: %v", back["reason"])
	}
	if back["count"] != float64(3) {
		t.Fatalf("stdin lost the int: %v", back["count"])
	}
}

// MARO_RUN_DIR is exported only when there IS a run dir. Python's `if
// run_dir:` guard means a dispatch-lane escalation — which has no run
// directory yet, and is exactly the case an operator's hook has to handle
// differently — arrives with the variable UNSET rather than empty. A hook
// doing `[ -n "$MARO_RUN_DIR" ]` and one doing `[ -v MARO_RUN_DIR ]` agree
// here and disagree if the guard is dropped.
func TestRunDirIsAbsentWhenThereIsNoRunDir(t *testing.T) {
	rec := &recorder{}
	Emit(context.Background(), t.TempDir(), "escalation",
		map[string]any{"handle_id": "h1"},
		Options{Cfg: map[string]any{"notify": map[string]any{"command": "true"}},
			Exec: rec.fn, Env: []string{"PATH=/bin"}})
	if rec.calls != 1 {
		t.Fatalf("hook ran %d times", rec.calls)
	}
	if v, ok := rec.envOf("MARO_RUN_DIR"); ok {
		t.Fatalf("MARO_RUN_DIR is set to %q with no run dir", v)
	}
	// The other three are unconditional, including the empty ones — a hook
	// can read them without checking, which is the point of having them.
	for _, k := range []string{"MARO_EVENT_TYPE", "MARO_HANDLE_ID", "MARO_STATUS"} {
		if _, ok := rec.envOf(k); !ok {
			t.Errorf("%s is not exported", k)
		}
	}
}

// The durable writes come FIRST and are unconditional. A hook that
// succeeds must not shortcut them: the events feed and the escalation
// ledger are what a POLLING substrate reads, and a box can have both a
// hook and a poller. Ordering this the other way round is a natural
// optimisation ("the notification was delivered, why also write it?") and
// it silently empties both files for everyone else.
func TestASucceedingHookStillWritesBothSurfaces(t *testing.T) {
	ws := t.TempDir()
	rec := &recorder{}
	// The ORDER, observed from inside the hook rather than inferred from
	// the files existing afterwards. Round 8's read: this test's own
	// comment says the durable writes come first, and every assertion
	// below it would hold just as well for a port that ran the hook and
	// wrote the files after — which is the exact optimisation the comment
	// warns against. A hook command that reads escalations.jsonl (the
	// documented substrate idiom) is what would break, and only a stat
	// taken DURING the call can see it.
	var sawLedger, sawEvents bool
	seen := func(ctx context.Context, command, stdin string, env []string,
		timeout time.Duration) (int, string, bool, error) {
		_, e1 := os.Stat(EscalationsPath(ws))
		_, e2 := os.Stat(filepath.Join(ws, "memory", "events.jsonl"))
		sawLedger, sawEvents = e1 == nil, e2 == nil
		return rec.fn(ctx, command, stdin, env, timeout)
	}
	if !Emit(context.Background(), ws, "escalation",
		map[string]any{"handle_id": "h1", "reason": "x"},
		Options{Cfg: map[string]any{"notify": map[string]any{"command": "true"}},
			Exec: seen}) {
		t.Fatal("a clean hook must report true")
	}
	if !sawLedger {
		t.Error("the hook ran BEFORE the escalation ledger was written; a " +
			"substrate hook that reads it sees an absent file")
	}
	if !sawEvents {
		t.Error("the hook ran BEFORE the events feed was written")
	}
	if rec.calls != 1 {
		t.Fatalf("hook ran %d times", rec.calls)
	}
	if _, err := os.Stat(EscalationsPath(ws)); err != nil {
		t.Fatalf("a successful hook skipped the escalation ledger: %v", err)
	}
	if _, err := os.Stat(filepath.Join(ws, "memory", "events.jsonl")); err != nil {
		t.Fatalf("a successful hook skipped the events feed: %v", err)
	}
}

// A non-zero exit and a timeout both mean "the lane did not deliver", and
// both must leave the durable surfaces already written. A notification lane
// that can swallow the escalation ledger on its way down is worse than no
// lane at all.
func TestHookFailuresDoNotCostTheDurableSurfaces(t *testing.T) {
	for _, tc := range []struct {
		name string
		rec  recorder
	}{
		{"non-zero exit", recorder{code: 17}},
		{"timeout", recorder{timedOut: true}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ws := t.TempDir()
			rec := tc.rec
			var logged []string
			ok := Emit(context.Background(), ws, "escalation",
				map[string]any{"handle_id": "h1", "reason": "x"},
				Options{
					Cfg:  map[string]any{"notify": map[string]any{"command": "false"}},
					Exec: rec.fn,
					Log:  func(f string, a ...any) { logged = append(logged, f) },
				})
			if ok {
				t.Fatal("a failed hook must report false")
			}
			if len(logged) == 0 {
				t.Fatal("a failed notify lane must be logged, not swallowed")
			}
			if _, err := os.Stat(EscalationsPath(ws)); err != nil {
				t.Fatalf("the escalation ledger did not survive the hook failure: %v", err)
			}
			if _, err := os.Stat(filepath.Join(ws, "memory", "events.jsonl")); err != nil {
				t.Fatalf("the events feed did not survive the hook failure: %v", err)
			}
		})
	}
}

// TestEventSelection covers `notify.events`, including the two readings of
// it that are natural and wrong.
func TestEventSelection(t *testing.T) {
	cfgWith := func(events any) map[string]any {
		n := map[string]any{"command": "true"}
		if events != nil {
			n["events"] = events
		}
		return map[string]any{"notify": n}
	}
	cases := []struct {
		name  string
		cfg   map[string]any
		event string
		want  bool
	}{
		{"absent key falls back to the defaults", cfgWith(nil), "escalation", true},
		{"a listed event runs", cfgWith([]any{"escalation"}), "escalation", true},
		{"an unlisted event does not", cfgWith([]any{"escalation"}), "run_completed", false},
		// The natural-and-wrong reading #1: an empty list looks like
		// "notify me about nothing". Python's `or DEFAULT_EVENTS` makes it
		// mean the opposite.
		{"an EMPTY list means the defaults, not silence", cfgWith([]any{}), "escalation", true},
		// The natural-and-wrong reading #2: run_verdict is not obviously a
		// notification, and dropping it from the defaults would half-deliver
		// the answer-first split — the run_completed goes out with
		// verdict_pending and the verdict never arrives.
		{"run_verdict is default-on", cfgWith(nil), "run_verdict", true},
		{"recursion_checkin is default-on", cfgWith(nil), "recursion_checkin", true},
		{"an unknown event is never in the defaults", cfgWith(nil), "made_up", false},
		// YAML hands lists over as []any of whatever the scalars parsed to.
		{"a non-string member does not break the read",
			cfgWith([]any{5, "escalation"}), "escalation", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			rec := &recorder{}
			Emit(context.Background(), t.TempDir(), c.event,
				map[string]any{}, Options{Cfg: c.cfg, Exec: rec.fn})
			if (rec.calls > 0) != c.want {
				t.Fatalf("hook ran=%v, want %v", rec.calls > 0, c.want)
			}
		})
	}

	// No command at all: the healthy default, and it must not reach the
	// exec seam even to be told the event is selected.
	rec := &recorder{}
	if Emit(context.Background(), t.TempDir(), "escalation", map[string]any{},
		Options{Cfg: map[string]any{}, Exec: rec.fn}) {
		t.Fatal("no command configured must report false")
	}
	if rec.calls != 0 {
		t.Fatal("no command configured must not reach the exec seam")
	}
}

// --- the two enumerations -----------------------------------------------

const pySetsSrc = `
import json, sys
import notify
sys.stdout.write(json.dumps({
    "default": notify.DEFAULT_EVENTS,
    "escalation_file": sorted(notify.ESCALATION_FILE_EVENTS),
}))
`

// DEFAULT_EVENTS and ESCALATION_FILE_EVENTS are ENUMERATIONS, and r8's own
// lesson was that an enumeration is not a class: it does not defend itself,
// it drifts silently, and the only thing that catches the drift is reading
// the other side. Order matters for the defaults because operators copy the
// list out of the docs; membership is what the code reads.
func TestEventSetsMatchCPython(t *testing.T) {
	var py struct {
		Default        []string `json:"default"`
		EscalationFile []string `json:"escalation_file"`
	}
	if err := json.Unmarshal([]byte(runPy(t, pySetsSrc)), &py); err != nil {
		t.Fatal(err)
	}
	if len(py.Default) != len(DefaultEvents) {
		t.Fatalf("DEFAULT_EVENTS has %d entries, DefaultEvents has %d\n py: %v\n go: %v",
			len(py.Default), len(DefaultEvents), py.Default, DefaultEvents)
	}
	for i := range py.Default {
		if py.Default[i] != DefaultEvents[i] {
			t.Errorf("DEFAULT_EVENTS[%d]: go %q, py %q", i, DefaultEvents[i], py.Default[i])
		}
	}
	// Built from the GO set, not by filtering Python's defaults. Sampling
	// IsEscalationClass over py.Default can only ever find members Python
	// already has: a Go-side event absent from DEFAULT_EVENTS was never
	// asked about, so adding one here — writing escalations.jsonl rows
	// CPython never writes — left every assertion in this test green
	// (adversarial r11 round 8, fixture sweep). The old anti-vacuity guard
	// checked ESCALATION_FILE_EVENTS ⊆ DEFAULT_EVENTS, which is a fact
	// about CPython and says nothing about what this runtime does.
	var goEsc []string
	for e := range escalationFileEvents {
		if IsEscalationClass(e) {
			goEsc = append(goEsc, e)
		}
	}
	// The map and the predicate are two statements of one set; if they ever
	// disagree the comparison below would silently narrow.
	if len(goEsc) != len(escalationFileEvents) {
		t.Fatalf("IsEscalationClass rejects %d of its own map's keys",
			len(escalationFileEvents)-len(goEsc))
	}
	sortStrings(goEsc)
	if strings.Join(goEsc, ",") != strings.Join(py.EscalationFile, ",") {
		t.Fatalf("ESCALATION_FILE_EVENTS diverges:\n go: %v\n py: %v",
			goEsc, py.EscalationFile)
	}
	// The two exclusions are load-bearing, so name them: run_completed has
	// a durable home in run_card.json and run_verdict rides it.
	for _, e := range []string{"run_completed", "run_verdict"} {
		if IsEscalationClass(e) {
			t.Errorf("%q must not be escalation-class — it would bury the "+
				"events that need a human", e)
		}
	}
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}
