package introspect

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyCLISrc runs introspect.main(argv) with stdout captured, so what is
// compared is the WHOLE rendering — every blank line, every pad width,
// every conditional section — rather than a field-by-field reconstruction
// that would agree with itself.
//
// EACH RUNTIME GETS ITS OWN COPY OF THE STORE. `main` diagnoses through
// `diagnose_loop`, whose emit_log_event defaults to True, so CPython
// APPENDS a captain's-log DIAGNOSIS event for every non-healthy class it
// renders. Running both sides against one directory would let the first
// run change what the second one reads — a differential measuring its own
// side effect. The Go side is deliberately not a writer here (see
// DiagnoseLoop), which is exactly why the asymmetry has to be isolated
// rather than assumed harmless.
const pyCLISrc = `
import io, json, os, sys, contextlib
import introspect

args = json.loads(sys.argv[1])
out = []
for c in args:
    os.environ["MARO_WORKSPACE"] = c["ws"]
    import importlib
    importlib.reload(introspect)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            introspect.main(c["argv"])
        out.append({"ok": True, "text": buf.getvalue()})
    except SystemExit as e:
        out.append({"ok": False, "text": buf.getvalue(), "exit": str(e)})
print(json.dumps(out))
`

// copyStore duplicates a seeded workspace so the two runtimes never share
// one. Returns the new root.
func copyStore(t *testing.T, src string) string {
	t.Helper()
	dst := t.TempDir()
	err := filepath.Walk(src, func(p string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, p)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if info.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		return os.WriteFile(target, b, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
	return dst
}

func evt(loopID, kind string, fields string) string {
	s := `{"loop_id": "` + loopID + `", "event_type": "` + kind + `"`
	if fields != "" {
		s += ", " + fields
	}
	return s + "}"
}

func TestCLIMatchesCPython(t *testing.T) {
	// A loop that fails with a token explosion: a heavy step, a blocked
	// step, and enough tokens for the bar to render at a length the `//5000`
	// floor decides. A healthy loop would exercise neither the recovery
	// block nor most of the lens output.
	explosion := []string{
		evt("loop-alpha", "step_done", `"step_idx": 1, "step": "fetch the widget index", "status": "done", "tokens": 12000, "elapsed_ms": 900, "tokens_in": 11000, "tokens_out": 1000, "model": "grok-4.5"`),
		evt("loop-alpha", "step_done", `"step_idx": 2, "step": "verify the parser output", "status": "done", "tokens": 640000, "elapsed_ms": 190000, "tokens_in": 639000, "tokens_out": 1000, "model": "grok-4.5"`),
		evt("loop-alpha", "step_done", `"step_idx": 3, "step": "render report tables", "status": "blocked", "tokens": 0, "elapsed_ms": 140000`),
		evt("loop-alpha", "loop_done", `"status": "stuck", "detail": "token budget"`),
	}
	// A clean loop, so the healthy path (no recovery block, thin lens
	// output) is measured too.
	healthy := []string{
		evt("loop-beta", "step_done", `"step_idx": 1, "step": "alpha", "status": "done", "tokens": 100, "elapsed_ms": 900, "tokens_in": 100, "model": "grok-4.5"`),
		evt("loop-beta", "loop_done", `"status": "done"`),
	}
	// A step whose token count is under 5000 renders an EMPTY bar, and one
	// far over renders a bar the min(50, ...) clamps. Both in one loop.
	bars := []string{
		// `tokens_in + tokens_out` IS the profile's token count. A `"tokens"`
		// key on the event is read by nothing: this fixture carried one for
		// its whole life, every step profiled at zero, and the bar it is
		// named for was never rendered at all. Four bar mutants died anyway —
		// to loop-alpha, which happens to price its steps — so nothing ever
		// reported the gap. The writer's field name and the reader's are two
		// separate claims and only one of them was checked.
		evt("loop-gamma", "step_done", `"step_idx": 1, "step": "tiny", "status": "done", "tokens_in": 4999, "elapsed_ms": 10`),
		evt("loop-gamma", "step_done", `"step_idx": 2, "step": "at one bar", "status": "done", "tokens_in": 4000, "tokens_out": 1000, "elapsed_ms": 10`),
		evt("loop-gamma", "step_done", `"step_idx": 3, "step": "clamped", "status": "blocked", "tokens_in": 900000, "elapsed_ms": 10`),
		evt("loop-gamma", "step_done", `"step_idx": 12, "step": "a two-digit step index", "status": "done", "tokens_in": 0, "elapsed_ms": 10`),
		// A NEGATIVE token count. Python's `"=" * (n // 5000)` yields "" for
		// a negative n; Go's strings.Repeat PANICS on one. The `tokens > 0`
		// guard is Python's and happens to be exactly what Go needs — a
		// claim the port asserts in a comment and, until this row existed,
		// had never once executed. Zero tokens is not the same test: at
		// zero both spellings agree and the guard looks decorative.
		evt("loop-gamma", "step_done", `"step_idx": 13, "step": "a refunded step", "status": "done", "tokens_in": -7000, "elapsed_ms": 10`),
	}

	diagRow := func(id, class, sev string, done, total, tokens int) string {
		return `{"loop_id": "` + id + `", "failure_class": "` + class +
			`", "severity": "` + sev + `", "steps_done": ` +
			itoa(done) + `, "steps_total": ` + itoa(total) +
			`, "total_tokens": ` + itoa(tokens) + `}`
	}

	// Thirty rows, oldest first: three of a class that only exists at the
	// far end, then twenty-seven of another. Reading 50 finds both classes;
	// reading 25 never reaches the first three and reports the second class
	// with a smaller count. So the window size is visible in the rendering
	// twice over.
	patternsPastRow25 := func() []string {
		var rows []string
		for i := 1; i <= 3; i++ {
			rows = append(rows, diagRow("old"+itoa(i), "adapter_timeout",
				"critical", 0, 1, 0))
		}
		for i := 1; i <= 27; i++ {
			rows = append(rows, diagRow("new"+itoa(i), "retry_churn",
				"warning", 0, 1, 0))
		}
		return rows
	}

	cases := []struct {
		name  string
		argv  []string
		store string // "events" | "diagnoses"
		rows  []string
	}{
		{name: "an empty store with no arguments", argv: nil, store: "events"},
		{name: "the latest loop", argv: []string{"--latest"}, store: "events",
			rows: explosion},
		// No argument at all falls through to diagnose_latest, so this must
		// render identically to the case above.
		{name: "no argument falls through to latest", argv: nil,
			store: "events", rows: explosion},
		{name: "a named loop", argv: []string{"loop-alpha"}, store: "events",
			rows: explosion},
		// A loop_id matching nothing is the artifact_missing CLASS, not an
		// absence — it renders a full diagnosis with a recovery plan.
		{name: "a loop id that matches nothing",
			argv: []string{"loop-nonesuch"}, store: "events", rows: explosion},
		{name: "a healthy loop", argv: []string{"loop-beta"}, store: "events",
			rows: healthy},
		{name: "the bar widths", argv: []string{"loop-gamma"}, store: "events",
			rows: bars},
		{name: "the lens block", argv: []string{"loop-alpha", "--lenses"},
			store: "events", rows: explosion},
		{name: "the lens block on a healthy loop",
			argv: []string{"loop-beta", "--lenses"}, store: "events",
			rows: healthy},
		// NO STEP CARRIES ANY TOKENS, so every step costs zero and the cost
		// lens takes its `total == 0` early return: one finding, no action,
		// and a confidence left at the dataclass default of 0.0. That result
		// renders the two fragments this file gets wrong most easily — an
		// EMPTY confidence after an unconditional space (a line ending in
		// whitespace, on purpose) and a findings block with no `->` under
		// it.
		//
		// It is a loop of its own rather than a second view of `bars` for a
		// reason worth keeping: it WAS `bars`, back when those steps carried
		// a `"tokens"` key the profiler never read and therefore priced at
		// nothing. Fixing that field — which is what made the bar fixture
		// real — silently closed this branch again, and the battery caught
		// the regression on the next run. Zero cost and a rendered bar are
		// mutually exclusive, so they cannot be the same fixture.
		{name: "the lens block with nothing priced",
			argv: []string{"loop-eps", "--lenses"}, store: "events",
			rows: []string{
				evt("loop-eps", "step_done", `"step_idx": 1, "step": "unpriced work", "status": "done", "elapsed_ms": 900`),
				evt("loop-eps", "step_done", `"step_idx": 2, "step": "more unpriced work", "status": "blocked", "elapsed_ms": 900`),
				evt("loop-eps", "loop_done", `"status": "stuck"`),
			}},
		// An id matching no events diagnoses as artifact_missing and builds
		// an EMPTY profile list, which silences four of the five lenses —
		// but NOT the execution lens, which reads the diagnosis rather than
		// the profiles and republishes its evidence for any non-healthy
		// class. So this renders exactly one lens.
		{name: "the lens block on a loop that does not exist",
			argv: []string{"loop-nonesuch", "--lenses"}, store: "events",
			rows: explosion},
		// AND HERE IS THE ONE THAT RENDERS NONE. Silencing all five needs
		// both halves at once: no step profiles (which stops the four that
		// read them) and a healthy class with no blocked steps (which stops
		// the fifth). A loop whose only event is its own completion is the
		// smallest thing that is both. Without it the "no notable findings"
		// line is unreachable and could say anything at all.
		{name: "a loop with no steps silences every lens",
			argv: []string{"loop-delta", "--lenses"}, store: "events",
			rows: []string{evt("loop-delta", "loop_done", `"status": "done"`)}},

		// --history
		{name: "history on an empty store", argv: []string{"--history", "5"},
			store: "diagnoses"},
		{name: "history", argv: []string{"--history", "5"}, store: "diagnoses",
			rows: []string{
				diagRow("aaaaaaaabbbb", "healthy", "info", 3, 3, 1234),
				diagRow("ccccccccdddd", "token_explosion", "warning", 1, 4, 987654),
				diagRow("eeeeeeeeffff", "adapter_timeout", "critical", 0, 2, 0),
				// An unknown severity falls back to "?", and a class wider
				// than the 28-column pad must NOT be truncated.
				diagRow("gggg", "a_failure_class_wider_than_the_pad_allows",
					"nightmare", 9, 9, 5),
				// A class 20 RUNES wide but 32 BYTES wide. Python pads to a
				// width counted in code points, so this row pads by 8; a port
				// counting bytes reads it as already over 28 and pads by
				// nothing. Every ASCII fixture above agrees under both
				// spellings, which is precisely why one of these is needed.
				diagRow("レポート-loop-id", "timeout_カーネル_stall",
					"warning", 1, 2, 42),
			}},
		// `if args.history:` is TRUTHINESS, so 0 is falsy and falls through
		// to diagnosing rather than printing an empty history.
		{name: "a history of zero falls through", argv: []string{"--history", "0"},
			store: "events", rows: explosion},

		// --patterns
		{name: "patterns on an empty store", argv: []string{"--patterns"},
			store: "diagnoses"},
		{name: "patterns under the three-occurrence bar",
			argv: []string{"--patterns"}, store: "diagnoses", rows: []string{
				diagRow("a", "retry_churn", "warning", 0, 1, 0),
				diagRow("b", "retry_churn", "warning", 0, 1, 0),
			}},
		// The loop ids here are LONGER than eight characters and differ
		// within their first eight, so `first:`/`last:` are clipped and the
		// two are told apart. Eight-character ids would make Clip the
		// identity and let a port that dropped it — or swapped the pair —
		// render byte-for-byte correctly anyway.
		{name: "patterns at the bar", argv: []string{"--patterns"},
			store: "diagnoses", rows: []string{
				diagRow("oldest01-aaaa", "retry_churn", "warning", 0, 1, 0),
				diagRow("middle01-bbbb", "retry_churn", "warning", 0, 1, 0),
				diagRow("newest01-cccc", "retry_churn", "warning", 0, 1, 0),
			}},
		// A class with no recovery-table row prints no recovery line.
		{name: "patterns for a class with no plan", argv: []string{"--patterns"},
			store: "diagnoses", rows: []string{
				diagRow("a1", "constraint_block", "warning", 0, 1, 0),
				diagRow("a2", "constraint_block", "warning", 0, 1, 0),
				diagRow("a3", "constraint_block", "warning", 0, 1, 0),
			}},
		// --patterns wins over --history: argparse accepts both and
		// `if args.patterns:` is tested first. With only one of the two ever
		// passed, a port that ordered them the other way renders correctly.
		{name: "patterns and history together", store: "diagnoses",
			argv: []string{"--patterns", "--history", "5"}, rows: []string{
				diagRow("b1", "retry_churn", "warning", 0, 1, 0),
				diagRow("b2", "retry_churn", "warning", 0, 1, 0),
				diagRow("b3", "retry_churn", "warning", 0, 1, 0),
			}},
		// The 50-row window is a real cut, not a formality. The three oldest
		// rows are the ONLY occurrences of their class, and they sit past a
		// 25-row read: at limit 50 they form a pattern, at 25 they are not
		// loaded at all. Every other patterns fixture fits in any window.
		{name: "patterns from past the twenty-fifth row",
			argv: []string{"--patterns"}, store: "diagnoses",
			rows: patternsPastRow25()},
	}

	// Build both sides' stores up front: CPython gets its own copies.
	type payload struct {
		WS   string   `json:"ws"`
		Argv []string `json:"argv"`
	}
	goWS := make([]string, len(cases))
	pyArgs := make([]payload, len(cases))
	for i, c := range cases {
		name := "events.jsonl"
		if c.store == "diagnoses" {
			name = "diagnoses.jsonl"
		}
		ws := seedStore(t, name, c.rows)
		goWS[i] = ws
		argv := c.argv
		if argv == nil {
			argv = []string{}
		}
		pyArgs[i] = payload{WS: copyStore(t, ws), Argv: argv}
	}

	type answer struct {
		OK   bool   `json:"ok"`
		Text string `json:"text"`
		Exit string `json:"exit"`
	}
	var want []answer
	probe := pyprobe.Probe{Marker: "introspect.py"}
	probe.RunJSON(t, pyCLISrc, &want, pyprobe.Arg(t, pyArgs))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := want[i]
			if !w.OK {
				t.Fatalf("cpython exited: %s\n%s", w.Exit, w.Text)
			}
			// A rendering that is empty on BOTH sides is the shape L1 names,
			// so say so rather than passing quietly.
			if strings.TrimSpace(w.Text) == "" {
				t.Fatalf("cpython rendered nothing for %q — the fixture "+
					"cannot tell two implementations apart", c.name)
			}
			var buf bytes.Buffer
			if err := Main(goWS[i], c.argv, &buf); err != nil {
				t.Fatalf("go Main: %v", err)
			}
			if got := buf.String(); got != w.Text {
				t.Errorf("rendering differs\n--- cpython ---\n%s\n--- go ---\n%s",
					showWhitespace(w.Text), showWhitespace(got))
			}
		})
	}
}

// showWhitespace makes a trailing-space or missing-blank-line difference
// legible in the failure output, which is most of what this file measures.
func showWhitespace(s string) string {
	lines := strings.Split(s, "\n")
	for i, l := range lines {
		lines[i] = "|" + strings.ReplaceAll(l, " ", "·") + "|"
	}
	return strings.Join(lines, "\n")
}
