package notify

import (
	"context"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// TestTheHookReadsTheWorkspaceItWasHanded pins which config file decides
// whether an operator's substrate hears anything.
//
// Python cannot get this wrong: config.get resolves through MARO_WORKSPACE,
// which is the same workspace emit is writing. This port's verbs take `ws`
// as an argument, which quietly makes reading one workspace's hook config
// while writing another's ledgers possible — the adversarial-r9 MEDIUM,
// recommitted in a function written after it.
//
// Nothing could have caught it: every existing hook test passes Cfg
// explicitly, so the fallback branch had no coverage at all. **A default
// nobody exercises is a default nobody has read.**
func TestTheHookReadsTheWorkspaceItWasHanded(t *testing.T) {
	ws := t.TempDir()
	ambient := t.TempDir()
	// The ambient workspace registers NO command; the one being written
	// registers one. Under config.Load() the hook is silent.
	if err := os.WriteFile(filepath.Join(ws, "config.yml"),
		[]byte("notify:\n  command: bash notify.sh\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MARO_WORKSPACE", ambient)

	rec := &recorder{}
	if !Emit(context.Background(), ws, "escalation",
		map[string]any{"handle_id": "h-1"},
		Options{Exec: rec.fn, Env: []string{"PATH=/bin"}}) {
		t.Fatal("the hook did not run: emit read the ambient workspace's config, " +
			"not the one it was handed and is writing ledgers into")
	}
	if rec.command != "bash notify.sh" {
		t.Errorf("command = %q", rec.command)
	}
	// And the ledgers really did go to ws, so the two halves genuinely
	// disagree when the config read is wrong.
	if _, err := os.Stat(filepath.Join(ws, "output", "escalations.jsonl")); err != nil {
		t.Errorf("the escalation ledger is not in the workspace that was passed: %v", err)
	}
}

// TestTheHookTimeoutIsFloatedNotAsserted covers the other half of the same
// config read.
//
// `float(_config_get("notify.timeout_seconds", 30))` coerces — a YAML
// `"45"` is 45.0 in Python — and it has NO try around it, so a non-numeric
// value propagates to emit's outer handler: the hook does not run and emit
// returns False, with the two ledger writes above it already done. A
// defaulting read would have run a command Python declined to run, at a
// timeout nobody configured.
func TestTheHookTimeoutIsFloatedNotAsserted(t *testing.T) {
	// The two refusal diagnoses, spelled once. A row naming the wrong one
	// is the bug this column exists to catch: an operator who wrote
	// `timeout_seconds: soon` and an operator who wrote `3e6` need
	// different corrections.
	const notANumber = "notify.timeout_seconds is not a number"
	const outOfRange = "notify.timeout_seconds is out of range"

	for _, c := range []struct {
		name    string
		val     any
		wantRun bool
		want    time.Duration
		// wantLog is the timed-out line CPython writes, or "" for the
		// values it refuses without logging anything. The two are NOT the
		// same outcome and the return value cannot tell them apart: emit
		// answers False either way, and the log is the only surface a
		// headless operator reads.
		wantLog string
		// wantRefusal is the OTHER half, and it is why the "" rows above
		// are not vacuous. CPython logs nothing for a value float() or
		// poll() rejects — it just raises into emit's outer handler — but
		// this port deliberately says so (see Options.Log), and "not a
		// number" and "out of range" are two different diagnoses of two
		// different operator mistakes. Round 8 found these rows asserting
		// only the ABSENCE of a timeout line, which every one of them would
		// satisfy while the port said nothing at all, or said the wrong
		// thing. Empty means the port must stay silent too.
		wantRefusal string
	}{
		{"an int", 45, true, 45 * time.Second, "", ""},
		{"a float", 4.5, true, 4500 * time.Millisecond, "", ""},
		{"a quoted number", "45", true, 45 * time.Second, "", ""},
		{"a quoted float", " 4.5 ", true, 4500 * time.Millisecond, "", ""},
		{"a bool", true, true, time.Second, "", ""},
		{"prose", "soon", false, 0, "", notANumber},
		{"a null", nil, false, 0, "", notANumber},
		{"a list", []any{45}, false, 0, "", notANumber},
		// The bound is poll()'s, not Go's: subprocess hands the remaining
		// time to select/poll as a C int of MILLISECONDS, so anything past
		// INT_MAX ms is OverflowError("timeout is too large") — an escape
		// to emit's outer handler, not a TimeoutExpired. Measured on
		// 3.14.3: 2147483.647 runs, 2147483.648 raises.
		{"the largest timeout that fits", 2147483.647, true,
			time.Duration(2147483.647 * float64(time.Second)), "", ""},
		{"one millisecond past the poll bound", 2147483.648, false, 0, "", outOfRange},
		// The operator idiom this bound actually exists for. Every one of
		// these ran the hook under the round-5 guard, which sat at Go's
		// Duration limit — ~4300x too high.
		{"the never-time-out idiom", 3e6, false, 0, "", outOfRange},
		{"a timeout past the int64 nanosecond range", 1e11, false, 0, "", outOfRange},
		{"an infinite timeout", math.Inf(1), false, 0, "", outOfRange},
		{"a NaN timeout", math.NaN(), false, 0, "", outOfRange},
		// A NEGATIVE is not out of range at all: poll() gets a deadline
		// already past, so the hook is spawned, killed, and reported as
		// timed out — with the CONFIGURED value in the line, spelled the
		// way Python's %.0f spells it. -Inf included (measured).
		{"a negative timeout", -1.0, false, 0,
			"notify.command timed out after -1s for escalation (h-1)", ""},
		{"a negative infinite timeout", math.Inf(-1), false, 0,
			"notify.command timed out after -infs for escalation (h-1)", ""},
		{"a hugely negative timeout", -1e9, false, 0,
			"notify.command timed out after -1000000000s for escalation (h-1)", ""},
		// Zero is a deadline already reached, so it is the same immediate
		// timeout — and its sign survives into the line.
		{"a zero timeout", 0.0, false, 0,
			"notify.command timed out after 0s for escalation (h-1)", ""},
		{"a negative zero timeout", math.Copysign(0, -1), false, 0,
			"notify.command timed out after -0s for escalation (h-1)", ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			rec := &recorder{}
			cfg := map[string]any{"notify": map[string]any{
				"command":         "bash notify.sh",
				"timeout_seconds": c.val,
			}}
			var logged []string
			ran := Emit(context.Background(), t.TempDir(), "escalation",
				map[string]any{"handle_id": "h-1"},
				Options{Cfg: cfg, Exec: rec.fn, Env: []string{"PATH=/bin"},
					Log: func(format string, a ...any) {
						logged = append(logged, fmt.Sprintf(format, a...))
					}})
			if ran != c.wantRun {
				t.Fatalf("emit reported %v, want %v (calls=%d)", ran, c.wantRun, rec.calls)
			}
			if !c.wantRun {
				if rec.calls != 0 {
					t.Errorf("the hook ran %d time(s); float() raises for this value "+
						"and CPython never reaches subprocess.run", rec.calls)
				}
				// The LOG, because "did not run" and "reported a timeout
				// that never happened" look identical from the return
				// value — and the log is the only surface a headless
				// operator sees.
				var timedOut []string
				for _, line := range logged {
					if strings.Contains(line, "timed out") {
						timedOut = append(timedOut, line)
					}
				}
				if c.wantLog == "" {
					if len(timedOut) > 0 {
						t.Errorf("the port logged a timeout CPython never "+
							"reports: %q", timedOut)
					}
					// And the refusal itself. Without this the row is
					// satisfied by a port that logs nothing, which is the
					// failure an operator cannot see: emit answered False
					// and the config is why.
					var said []string
					for _, line := range logged {
						if strings.Contains(line, "notify.timeout_seconds") {
							said = append(said, line)
						}
					}
					if len(said) != 1 ||
						!strings.HasPrefix(said[0], c.wantRefusal) {
						t.Errorf("refusal log:\n  got  %q\n  want one line "+
							"starting %q", said, c.wantRefusal)
					}
					return
				}
				if len(timedOut) != 1 || timedOut[0] != c.wantLog {
					t.Errorf("timed-out log:\n  got  %q\n  want %q",
						timedOut, c.wantLog)
				}
				return
			}
			if rec.timeout != c.want {
				t.Errorf("timeout = %v, want %v", rec.timeout, c.want)
			}
		})
	}
}

// pyEventGateSrc asks CPython whether a given notify.events value selects
// an event, by the only measurement that cannot be argued with: does the
// hook actually run.
const pyEventGateSrc = `
import json, os, sys
import notify
ran = notify.emit(sys.argv[1], {})
print(json.dumps({"ran": bool(ran),
                  "fired": os.path.exists(os.environ["GATE_MARKER"])}))
`

// TestTheEventGateMatchesCPython drives the `notify.events` read through
// CPython for the shapes an operator can actually write in YAML.
//
// The existing selection test states expectations by hand, and by hand is
// exactly where this read goes wrong: `"escalation" in "escalation_only"`
// is a SUBSTRING test that returns True, and `"escalation" in 5` is a
// TypeError that stops the hook entirely. Both were reachable and neither
// was measured — the port had been treating every non-list as "use the
// defaults", which runs a command CPython declines to run (adversarial
// r11 round 3).
func TestTheEventGateMatchesCPython(t *testing.T) {
	for _, c := range []struct {
		name  string
		yaml  string
		event string
	}{
		{"a list", "  events: [escalation]\n", "escalation"},
		{"a list without it", "  events: [run_completed]\n", "escalation"},
		{"an empty list", "  events: []\n", "escalation"},
		{"no events key at all", "", "escalation"},
		// The substring arm. Both directions, because only one of them is
		// surprising and a test with only the surprising one cannot show
		// that the ordinary case still works.
		{"a bare string containing the event", "  events: escalation_only\n", "escalation"},
		{"a bare string equal to the event", "  events: escalation\n", "escalation"},
		{"a bare string not containing it", "  events: run_completed\n", "escalation"},
		// The non-iterable arm: `in` raises TypeError, which emit's outer
		// handler swallows — no hook, and emit returns False.
		{"an integer", "  events: 5\n", "escalation"},
		{"a float", "  events: 2.5\n", "escalation"},
		{"a bool", "  events: true\n", "escalation"},
		// A mapping tests its KEYS.
		{"a mapping with the event as a key", "  events:\n    escalation: yes\n", "escalation"},
		{"a mapping without it", "  events:\n    run_completed: yes\n", "escalation"},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			marker := filepath.Join(t.TempDir(), "fired")
			// The SAME config text on both sides. An earlier cut gave the
			// Go side `command: true` as a harmless no-op and YAML parsed
			// it as a BOOLEAN, so the two runtimes were reading different
			// types and one mutation "failed" for a reason that had nothing
			// to do with the gate. Python actually spawns the command; Go's
			// Exec seam intercepts it, so the marker is Python's alone.
			yaml := []byte("notify:\n  command: touch " + marker + "\n" + c.yaml)
			for _, ws := range []string{pyWS, goWS} {
				if err := os.WriteFile(filepath.Join(ws, "config.yml"),
					yaml, 0o644); err != nil {
					t.Fatal(err)
				}
			}

			var want struct {
				Ran   bool `json:"ran"`
				Fired bool `json:"fired"`
			}
			pyprobe.Probe{
				Marker:    "notify.py",
				Workspace: pyWS,
				UserDir:   t.TempDir(),
			}.RunJSON(t, "import os\nos.environ['GATE_MARKER']="+
				strconvQuote(marker)+"\n"+pyEventGateSrc, &want, c.event)

			// The probe's own floor: emit returning True while the command
			// never ran would mean the measurement is of the return value
			// and not of the hook, and every row would still "pass".
			if want.Ran != want.Fired {
				t.Fatalf("CPython's emit returned %v but the command fired = %v "+
					"— the probe is not measuring what it claims",
					want.Ran, want.Fired)
			}

			rec := &recorder{}
			got := Emit(context.Background(), goWS, c.event,
				map[string]any{"handle_id": "h-1"},
				Options{Exec: rec.fn, Env: []string{"PATH=/bin:/usr/bin"}})
			if got != want.Ran {
				t.Errorf("the hook ran = %v, CPython %v", got, want.Ran)
			}
			if (rec.calls > 0) != want.Fired {
				t.Errorf("the exec seam was reached %d time(s), CPython fired = %v",
					rec.calls, want.Fired)
			}
		})
	}
}

func strconvQuote(s string) string { return strconv.Quote(s) }

// pyCommandTypeSrc reports what CPython would SPAWN, by intercepting the
// spawn rather than performing it. The command is `str(x or "").strip()`,
// so a YAML value that is not a string still produces one — and running it
// for real would only tell us that `42` is not an executable.
const pyCommandTypeSrc = `
import json, os, sys
import notify

seen = {}

class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""

def _fake_run(cmd, **kw):
    seen["cmd"] = cmd
    return _Proc()

notify.subprocess.run = _fake_run
ran = notify.emit(sys.argv[1], {})
print(json.dumps({"ran": bool(ran), "cmd": seen.get("cmd")}))
`

// TestTheHookCommandIsStringifiedNotAsserted covers the OTHER half of the
// read the events gate shares.
//
// `str(_config_get("notify.command", "") or "").strip()` coerces whatever
// YAML parsed. An unquoted port number, a numeric script name, a bare
// `true` — each is a config error, and each one CPython turns into a
// string and hands to a shell, logging the failure. A typed lookup answers
// the empty default for all of them instead, which leaves the operator's
// whole notification lane silent while CPython is at least noisy about it.
func TestTheHookCommandIsStringifiedNotAsserted(t *testing.T) {
	for _, c := range []struct {
		name string
		yaml string
	}{
		{"a quoted string", `  command: "bash notify.sh"` + "\n"},
		{"a bare string", "  command: bash notify.sh\n"},
		{"an unquoted number", "  command: 42\n"},
		{"an unquoted float", "  command: 4.5\n"},
		// YAML's `true` is a BOOLEAN, and Python's str() of it is "True" —
		// which is neither what the operator typed nor an executable, and
		// is still what gets spawned.
		{"an unquoted true", "  command: true\n"},
		// Falsy values take the `or ""` arm: no command, no hook.
		{"an unquoted false", "  command: false\n"},
		{"a zero", "  command: 0\n"},
		{"a null", "  command: ~\n"},
		{"an empty string", `  command: ""` + "\n"},
		{"whitespace only", `  command: "   "` + "\n"},
		// str.strip() and strings.TrimSpace do NOT strip the same set:
		// Python's whitespace includes the four ASCII information
		// separators U+001C-U+001F and Go's unicode.IsSpace does not
		// (measured by sweeping both over the full rune range). A
		// separator-only command is EMPTY to CPython and therefore no
		// hook at all; a Go trim leaves it non-empty and spawns it.
		{"separators only", `  command: "\x1c\x1d\x1e\x1f"` + "\n"},
		{"a command wrapped in separators",
			`  command: "\x1cbash notify.sh\x1f"` + "\n"},
		{"a list", "  command: [a, b]\n"},
		{"no command key at all", ""},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			yaml := []byte("notify:\n" + c.yaml)
			for _, ws := range []string{pyWS, goWS} {
				if err := os.WriteFile(filepath.Join(ws, "config.yml"),
					yaml, 0o644); err != nil {
					t.Fatal(err)
				}
			}

			var want struct {
				Ran bool    `json:"ran"`
				Cmd *string `json:"cmd"`
			}
			pyprobe.Probe{
				Marker:    "notify.py",
				Workspace: pyWS,
				UserDir:   t.TempDir(),
			}.RunJSON(t, pyCommandTypeSrc, &want, "escalation")

			rec := &recorder{}
			got := Emit(context.Background(), goWS, "escalation",
				map[string]any{"handle_id": "h-1"},
				Options{Exec: rec.fn, Env: []string{"PATH=/bin:/usr/bin"}})
			if got != want.Ran {
				t.Errorf("the hook ran = %v, CPython %v", got, want.Ran)
			}
			if (want.Cmd != nil) != (rec.calls > 0) {
				t.Fatalf("the exec seam was reached %d time(s); CPython "+
					"spawned %v", rec.calls, want.Cmd)
			}
			if want.Cmd != nil && rec.command != *want.Cmd {
				t.Errorf("spawned %q, CPython spawns %q", rec.command, *want.Cmd)
			}
		})
	}
}
