package introspect

import (
	"flag"
	"fmt"
	"io"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The `maro-introspect` CLI.
//
// This lives in the package rather than in cmd/maro because the RENDERING
// is the product here — every line is an operator-facing string, and the
// only way to know the port reproduces them is to diff whole stdout against
// CPython's. A renderer assembled in cmd/ would leave the assembly itself
// untested, which is where a missing blank line or a dropped section lives.
// `cmd/maro introspect` is a four-line wrapper over Main.

// DiagnoseLoop is introspect.diagnose_loop with emit_log_event=FALSE.
//
// Python's default is True: diagnosing a non-healthy loop WRITES a
// captain's-log DIAGNOSIS event, inside a bare try/except. That write is a
// side effect of diagnosing rather than part of the answer, and it belongs
// with the captain's-log port — the same decision `DiagnoseLatest` already
// records. It matters here because the CLI is a read path: `maro introspect
// <loop-id>` mutates the event log in Python and does not in Go.
//
// Note what Python does NOT do: a loop_id matching no events is not an
// absence, it is the `artifact_missing` class with its own evidence line.
// `Diagnose` already answers that way, so this is a plain forward.
func DiagnoseLoop(ws, loopID, project string) LoopDiagnosis {
	return Diagnose(LoadLoopEvents(ws, loopID), loopID, project)
}

// severityIcon is Python's
// `{"info": " ", "warning": "!", "critical": "X"}.get(d.severity, "?")`.
// The info icon is a SPACE, not empty — the column stays aligned for a
// healthy row, and a port that used "" would shift every info line left.
func severityIcon(severity string) string {
	switch severity {
	case "info":
		return " "
	case "warning":
		return "!"
	case "critical":
		return "X"
	}
	return "?"
}

// padRight is `f"{s:<28}"` and padLeft is `f"{n:2d}"` / `f"{s:>8}"`.
// Python pads to a width counted in CODE POINTS, so a multibyte failure
// class pads by runes and not by bytes. len() on a Go string would answer
// in bytes and quietly under-pad every non-ASCII row.
func padRight(s string, width int) string {
	if n := len([]rune(s)); n < width {
		return s + strings.Repeat(" ", width-n)
	}
	return s
}

func padLeft(s string, width int) string {
	if n := len([]rune(s)); n < width {
		return strings.Repeat(" ", width-n) + s
	}
	return s
}

// RenderPatterns is the `--patterns` branch.
func RenderPatterns(patterns []RecurringPattern) string {
	var b strings.Builder
	if len(patterns) == 0 {
		b.WriteString("No recurring failure patterns found (need 3+ occurrences of the same failure class).\n")
		return b.String()
	}
	fmt.Fprintf(&b, "%d recurring pattern(s):\n", len(patterns))
	for _, p := range patterns {
		// Always " ** GRADUATION CANDIDATE" in practice: the flag is
		// `len(diags) >= min_occurrences` evaluated after a `continue` on
		// the same test. Ported as a branch because the field is, and a
		// port that hard-coded the marker would hide it if the flag ever
		// starts meaning something.
		grad := ""
		if p.GraduationCandidate {
			grad = " ** GRADUATION CANDIDATE"
		}
		fmt.Fprintf(&b, "\n  %s  (%dx)%s\n", p.FailureClass, p.Occurrences, grad)
		fmt.Fprintf(&b, "    first: %s  last: %s\n",
			pyval.Clip(p.FirstSeen, 8), pyval.Clip(p.LastSeen, 8))
		if p.RecoveryAction != "" {
			fmt.Fprintf(&b, "    recovery: %s\n", p.RecoveryAction)
		}
	}
	return b.String()
}

// RenderHistory is the `--history N` branch.
//
// Note that `tokens=` here is NOT grouped, where the single-diagnosis view
// renders the same field through `{:,}`. Two surfaces, two spellings of one
// number, and the port keeps both.
func RenderHistory(diagnoses []LoopDiagnosis) string {
	if len(diagnoses) == 0 {
		return "No diagnoses recorded yet.\n"
	}
	var b strings.Builder
	// `reversed(diagnoses)` — load_diagnoses returns newest-first and the
	// history prints oldest-first.
	for i := len(diagnoses) - 1; i >= 0; i-- {
		d := diagnoses[i]
		fmt.Fprintf(&b, "  [%s] %s  %s steps=%d/%d tokens=%d\n",
			severityIcon(d.Severity), pyval.Clip(d.LoopID, 8),
			padRight(d.FailureClass, 28), d.StepsDone, d.StepsTotal,
			d.TotalTokens)
	}
	return b.String()
}

// RenderDiagnosis is the main single-loop view.
func RenderDiagnosis(diag LoopDiagnosis) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Loop %s\n", diag.LoopID)
	fmt.Fprintf(&b, "  Class:    %s\n", diag.FailureClass)
	fmt.Fprintf(&b, "  Severity: %s\n", diag.Severity)
	fmt.Fprintf(&b, "  Steps:    %d done / %d blocked / %d total\n",
		diag.StepsDone, diag.StepsBlocked, diag.StepsTotal)
	fmt.Fprintf(&b, "  Tokens:   %s\n", pyval.Grouped(int64(diag.TotalTokens)))
	fmt.Fprintf(&b, "  Elapsed:  %sms\n", pyval.Grouped(int64(diag.TotalElapsedMS)))
	if len(diag.Evidence) > 0 {
		b.WriteString("  Evidence:\n")
		for _, e := range diag.Evidence {
			fmt.Fprintf(&b, "    - %s\n", e)
		}
	}
	if diag.Recommendation != "" {
		fmt.Fprintf(&b, "  Recommendation: %s\n", diag.Recommendation)
	}

	if len(diag.TokenProfile) > 0 {
		b.WriteString("\n  Token profile:\n")
		for _, tp := range diag.TokenProfile {
			step := pyInt(tp, "step")
			tokens := pyInt(tp, "tokens")
			status, _ := tp.Get("status")
			// `"=" * min(50, tokens // 5000) if tokens > 0 else ""`. The
			// `> 0` guard is what keeps strings.Repeat from panicking on a
			// negative count — Python multiplies a string by a negative and
			// gets "", Go panics. The guard is Python's, and it happens to
			// be exactly the one Go needs.
			bar := ""
			if tokens > 0 {
				n := pyval.FloorDiv(tokens, 5000)
				if n > 50 {
					n = 50
				}
				bar = strings.Repeat("=", n)
			}
			icon := "x"
			if pyval.Str(pyval.Plain(status)) == "done" {
				icon = "+"
			}
			fmt.Fprintf(&b, "    step %s [%s] %s  %s\n",
				padLeft(strconv.Itoa(step), 2), icon,
				padLeft(pyval.Grouped(int64(tokens)), 8), bar)
		}
	}
	return b.String()
}

// pyInt reads an int out of a token-profile row. The rows are built by
// this package from StepProfile ints, so a missing key is a bug here rather
// than foreign data; it reads as zero, which is what Python's own rows
// could never be missing either.
func pyInt(o pyval.Obj, key string) int {
	v, ok := o.Get(key)
	if !ok {
		return 0
	}
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	}
	return 0
}

// RenderLenses is the `--lenses` block, synthesis included.
func RenderLenses(diag LoopDiagnosis, results []LensResult) string {
	var b strings.Builder
	if len(results) == 0 {
		b.WriteString("\n  Lens analysis: no notable findings\n")
		return b.String()
	}
	fmt.Fprintf(&b, "\n  Lens analysis (%d active):\n", len(results))
	for _, lr := range results {
		// Two conditional fragments, and the SPACE before the second is
		// unconditional — so a lens with zero confidence renders a line
		// ending in whitespace.
		//
		// That IS reachable, and this comment used to claim it wasn't. The
		// cost lens returns early when nothing in the loop was priced, and
		// that early return names only `lens_name` and `findings`: the
		// confidence falls to the dataclass default of 0.0 and the action to
		// "". So any loop whose steps carry no model and no input tokens
		// renders both the trailing space and a findings block with no `->`
		// under it. Two mutants survived on the strength of the claim before
		// a fixture was written to check it.
		conf := ""
		if lr.Confidence > 0 {
			conf = "confidence=" + pyval.PercentF(lr.Confidence, 1)
		}
		cost := ""
		if lr.Cost != "free" {
			cost = " [" + lr.Cost + "]"
		}
		fmt.Fprintf(&b, "\n  [%s]%s %s\n", lr.LensName, cost, conf)
		for _, f := range lr.Findings {
			fmt.Fprintf(&b, "    %s\n", f)
		}
		if lr.Action != "" {
			fmt.Fprintf(&b, "    -> %s\n", lr.Action)
		}
	}

	agg := AggregateLenses(diag, results)
	b.WriteString("\n  Synthesis:\n")
	fmt.Fprintf(&b, "    Confidence: %s\n", pyval.PercentFmt(agg.Confidence, 0))
	fmt.Fprintf(&b, "    Agreement:  %d lens(es) converge\n", agg.LensAgreement)
	fmt.Fprintf(&b, "    Action:     %s\n", agg.PrimaryAction)
	return b.String()
}

// RenderRecovery is the trailing recovery-plan block.
func RenderRecovery(diag LoopDiagnosis) string {
	if diag.FailureClass == "healthy" {
		return ""
	}
	plan, ok := PlanRecovery(diag)
	if !ok {
		return ""
	}
	auto := "SUGGEST"
	if plan.AutoApply {
		auto = "AUTO"
	}
	var b strings.Builder
	fmt.Fprintf(&b, "\n  Recovery plan [%s] (risk=%s):\n", auto, plan.Risk)
	fmt.Fprintf(&b, "    %s\n", plan.Action)
	// `for k, v in recovery.params.items()` — dict order, which is why
	// Params is an ordered Obj. `{v}` is str(v): an int renders bare and a
	// string renders unquoted.
	for _, f := range plan.Params {
		fmt.Fprintf(&b, "    %s: %s\n", f.Key, pyval.Str(pyval.Plain(f.Val)))
	}
	return b.String()
}

// valueFlags are the options that consume the NEXT token when written
// without an `=`. Only --history takes a value; the rest are store_true.
// Spelled as a set rather than inferred, because getting it wrong turns a
// flag's value into a loop_id and diagnoses a loop named "5".
var valueFlags = map[string]bool{"history": true}

// splitArgs separates options from positionals the way argparse does,
// preserving each group's order.
func splitArgs(argv []string) (flags, positional []string, err error) {
	for i := 0; i < len(argv); i++ {
		a := argv[i]
		if a == "--" {
			// argparse treats everything after `--` as positional.
			positional = append(positional, argv[i+1:]...)
			return flags, positional, nil
		}
		if len(a) < 2 || a[0] != '-' {
			positional = append(positional, a)
			continue
		}
		flags = append(flags, a)
		name := strings.TrimLeft(a, "-")
		if strings.Contains(name, "=") {
			continue
		}
		if valueFlags[name] {
			if i+1 >= len(argv) {
				return nil, nil, fmt.Errorf("flag needs an argument: %s", a)
			}
			i++
			flags = append(flags, argv[i])
		}
	}
	return flags, positional, nil
}

// Main is introspect.main(argv). It writes to `out` rather than stdout so
// the differential can compare the whole rendering.
//
// The `--llm` flag Python reads is NOT a typo here: `main` calls
// `run_lenses(..., include_llm=getattr(args, "llm", False))` and argparse
// never defines `--llm`, so the getattr default is the only value it can
// ever take. The LLM lenses are unreachable from this CLI in Python, which
// is the same place the port leaves them.
func Main(ws string, argv []string, out io.Writer) error {
	fs := flag.NewFlagSet("introspect", flag.ContinueOnError)
	fs.SetOutput(out)
	latest := fs.Bool("latest", false, "diagnose the most recent loop")
	lenses := fs.Bool("lenses", false, "also run multi-lens analysis")
	history := fs.Int("history", 0, "show last N diagnoses")
	patterns := fs.Bool("patterns", false,
		"show recurring failure patterns (graduation candidates)")
	// ARGPARSE INTERLEAVES; GO'S flag DOES NOT. `parse_args` accepts
	// `loop-alpha --lenses` and `--lenses loop-alpha` alike, while Go's
	// flag package stops at the first non-flag token and hands everything
	// after it back as positionals — so `maro introspect <id> --lenses`
	// would silently run without the lens block. Not a hypothetical: it is
	// how this port's first differential failed, on the one case where the
	// flag came second.
	flags, positional, err := splitArgs(argv)
	if err != nil {
		return err
	}
	if err := fs.Parse(flags); err != nil {
		return err
	}
	loopID := ""
	if len(positional) > 0 {
		loopID = positional[0]
	}
	// argparse declares exactly one optional positional and REJECTS a
	// second with exit code 2. The port refuses too; the message is not
	// argparse's, and nothing depends on the text.
	if len(positional) > 1 {
		return fmt.Errorf("introspect takes at most one loop_id (got %q and %q)",
			positional[0], positional[1])
	}

	if *patterns {
		// find_recurring_patterns() with ITS defaults, not the CLI's — the
		// 3-occurrence bar is what the "need 3+" message reports.
		io.WriteString(out, RenderPatterns(FindRecurringPatterns(ws, 3, 50)))
		return nil
	}

	// `if args.history:` is a TRUTHINESS test on an int-or-None, so
	// `--history 0` is falsy and falls through to diagnosing a loop rather
	// than printing an empty history. Ported as `> 0` — Go's flag package
	// has no None, and 0 is the value that reproduces both the unset case
	// and the explicit zero.
	if *history > 0 {
		io.WriteString(out, RenderHistory(LoadDiagnoses(ws, *history)))
		return nil
	}

	var diag LoopDiagnosis
	if *latest || loopID == "" {
		d, ok := DiagnoseLatest(ws)
		if !ok {
			io.WriteString(out, "No loop events found.\n")
			return nil
		}
		diag = d
	} else {
		diag = DiagnoseLoop(ws, loopID, "")
	}

	io.WriteString(out, RenderDiagnosis(diag))
	if *lenses {
		profiles := BuildStepProfiles(LoadLoopEvents(ws, diag.LoopID))
		io.WriteString(out, RenderLenses(diag, RunLenses(diag, profiles)))
	}
	io.WriteString(out, RenderRecovery(diag))
	return nil
}
