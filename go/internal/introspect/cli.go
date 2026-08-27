package introspect

import (
	"fmt"
	"io"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pyargparse"
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
// side effect of diagnosing rather than part of the answer, and it matters
// here because the CLI is a read path: `maro introspect <loop-id>` mutates
// the event log in Python and does not in Go.
//
// THIS IS AN OPEN GAP, NOT A BLOCKED ONE, and the distinction is the whole
// point of writing it down. The original rationale — "it belongs with the
// captain's-log port" — expired: that port has since landed as
// `record.Recorder.EventNoted`, writing `memory/captains_log.jsonl`, and
// graduation and scans already use it. Nothing stands in the way now
// except the work, which is a differential of its own: the summary
// f-string (`Loop <id>: <class> (<severity>). <n>/<m> steps done.`), the
// four context keys, `note=recommendation[:200]` as a CODE POINT clip with
// empty-to-None, and the bare `except Exception: pass` that makes the
// write best-effort. Filed in BACKLOG; recorded in PORT.md's divergence
// list rather than left as a comment nobody re-reads.
//
// A deferral whose reason has expired reads exactly like a considered
// decision, which is why round 4 found it and three rounds did not.
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

// ---------------------------------------------------------------------------
// The argument line
// ---------------------------------------------------------------------------
//
// What follows is a port of argparse, not a use of Go's `flag`, and the
// reason is that the two libraries disagree in more than a dozen places —
// every one of them silent. `flag` stops at the first positional where
// argparse interleaves; `flag` demands an exact option name where argparse
// resolves any unambiguous prefix; `flag` reads `-5` as an unknown option
// where argparse reads it as a positional; `flag` accepts `-latest` and
// `--latest=true` where argparse refuses both; `flag` writes its complaints
// to its own output and the caller collapses every failure to exit 1, where
// argparse writes usage to stderr and exits 2.
//
// The first version of this port was a pre-pass that normalized argv and
// then handed it to `flag`. That is what a reviewer found: the rendering was
// character-perfect at all thirty print sites and every defect in the chunk
// lived in the argument line. The pre-pass could not express argparse's real
// grammar, so it kept approximating it — and an approximation of a parser is
// a parser that is wrong on inputs nobody enumerated.
//
// Every rule below was MEASURED against CPython 3.14.3 rather than read out
// of the docs or recalled. Several of them are surprising enough that
// reasoning would have got them wrong: `-hh` prints help, `-1latest` is a
// positional, `--history " 5"` is 5, and an ambiguous option is an error at
// the point it is CONSUMED rather than where it is classified — which is why
// `-h --l` prints help while `--l -h` is an error.

// UsageError is an argument line argparse would reject. argparse exits 2 for
// every one of them — a distinct code from a runtime failure, and scripts
// branch on it — so the type exists to carry that code out to the caller
// rather than letting everything collapse to 1.// UsageError, ExitStatus and usagef are pyargparse's, kept under these
// names because they are what `cmd/maro` and this package's differential
// call. The machinery under them moved to internal/pyargparse when a second
// CLI needed it; nothing about the behaviour moved with it, and the
// differential in this package is what says so.
type UsageError = pyargparse.UsageError

// ExitStatus maps a Main error onto the (stderr, exit code) pair argparse
// produces: a usage error is the whole usage block plus `prog: error: msg`
// on stderr and exit 2, and anything else is not this function's to answer.
//
// It exists because there were two copies of that mapping — the `maro
// introspect` wrapper's, and one the differential wrote for itself and
// then asserted against CPython. The test was measuring its own copy, so
// the wrapper could have exited 1, or printed `err.Error()` without the
// usage block, and the suite stayed green. One function, two callers,
// and the differential now pins the one the operator actually runs.
func ExitStatus(err error) (stderr string, code int, handled bool) {
	return pyargparse.ExitStatus(err)
}

func usagef(format string, a ...any) error {
	return introspectParser.Errorf(format, a...)
}

// usageText and helpText are argparse's own output for this parser.
//
// PINNED DIFFERENCE, and the only one left in this file: argparse re-wraps
// both blocks to `shutil.get_terminal_size().columns - 2`. These constants
// are the 80-column rendering — what CPython produces whenever stdout is not
// a terminal (a pipe, a redirect, a CI log, every scripted invocation) and
// what it falls back to when the width cannot be determined. In a terminal
// of some other width CPython rewraps the usage line and the port does not.
// Reproducing the wrap would mean porting HelpFormatter's usage-grouping
// algorithm; the value of that is one cosmetic line at non-default widths,
// against a real risk of getting the common case wrong. Measured, not
// assumed: the differential exports COLUMNS=80 so both sides are pinned.
//
// `options:` is the 3.10+ heading. On 3.9 argparse said `optional
// arguments:`, so a port checked against an older interpreter would disagree
// here for a reason that has nothing to do with this code.
const usageText = "usage: maro-introspect [-h] [--latest] [--lenses] " +
	"[--history N] [--patterns]\n" +
	"                       [loop_id]\n"

const helpText = usageText + `
Diagnose execution traces — classify failures and recommend fixes

positional arguments:
  loop_id      Loop ID to diagnose (or --latest)

options:
  -h, --help   show this help message and exit
  --latest     Diagnose the most recent loop
  --lenses     Also run multi-lens analysis
  --history N  Show last N diagnoses
  --patterns   Show recurring failure patterns (graduation candidates)
`

// introspectParser is the `argparse.ArgumentParser` this CLI builds.
//
// The option table is a dict keyed by option STRING in argparse — so `-h`
// and `--help` are two rows sharing one action, and that is why both spell
// their name `-h/--help` in an error message. Slice order is the parser's
// insertion order, because it is the order the ambiguity message lists
// candidates in.
//
// `loop_id` is nargs="?" and therefore NOT required: `maro-introspect`
// with no arguments diagnoses the latest loop rather than refusing.
var introspectParser = &pyargparse.Parser{
	Prog:  "maro-introspect",
	Usage: usageText,
	Opts: []pyargparse.Opt{
		{Str: "-h", Dest: "help", NArgs: pyargparse.Flag, Label: "-h/--help", Help: true},
		{Str: "--help", Dest: "help", NArgs: pyargparse.Flag, Label: "-h/--help", Help: true},
		{Str: "--latest", Dest: "latest", NArgs: pyargparse.Flag, Label: "--latest"},
		{Str: "--lenses", Dest: "lenses", NArgs: pyargparse.Flag, Label: "--lenses"},
		{Str: "--history", Dest: "history", NArgs: pyargparse.Exactly1,
			Label: "--history", Convert: pyargparse.IntValue},
		{Str: "--patterns", Dest: "patterns", NArgs: pyargparse.Flag, Label: "--patterns"},
	},
	Positionals: []pyargparse.Positional{
		{Dest: "loop_id", NArgs: pyargparse.Optional, Label: "loop_id"},
	},
}

// introspectArgs is the argparse Namespace. `history` is a POINTER because
// argparse's default is None and `main` tests it for truthiness: None and 0
// are both falsy, and a negative N is truthy. An int with a zero default
// answers two of those three correctly and the port would never notice.
type introspectArgs struct {
	loopID   string
	latest   bool
	lenses   bool
	patterns bool
	history  *int
	help     bool
}

// parseIntrospectArgs is `parser.parse_args(argv)`, projected onto the
// struct this CLI reads.
func parseIntrospectArgs(argv []string) (introspectArgs, error) {
	r, err := introspectParser.Parse(argv)
	a := introspectArgs{
		loopID:   r.Str("loop_id"),
		latest:   r.Bool("latest"),
		lenses:   r.Bool("lenses"),
		patterns: r.Bool("patterns"),
		history:  r.IntPtr("history"),
		help:     r.Help,
	}
	return a, err
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
	args, err := parseIntrospectArgs(argv)
	if err != nil {
		return err
	}
	if args.help {
		// argparse prints help to STDOUT and exits 0 — the one case where a
		// usage block belongs on the rendering stream. Errors go to stderr,
		// which is why UsageError carries its own renderer instead.
		io.WriteString(out, helpText)
		return nil
	}

	if args.patterns {
		// find_recurring_patterns() with ITS defaults, not the CLI's — the
		// 3-occurrence bar is what the "need 3+" message reports.
		io.WriteString(out, RenderPatterns(FindRecurringPatterns(ws, 3, 50)))
		return nil
	}

	// `if args.history:` is a truthiness test on an int-or-None, and both
	// halves of the guard are load-bearing for a different input: an
	// unsupplied flag is None and falsy, `--history 0` is 0 and falsy, and
	// `--history -5` is TRUTHY and renders exactly one row (load_diagnoses
	// breaks when `1 >= -5`). A plain `> 0` sends that last one down the
	// wrong branch, which is how this read before it was measured.
	if args.history != nil && *args.history != 0 {
		io.WriteString(out, RenderHistory(LoadDiagnoses(ws, *args.history)))
		return nil
	}

	var diag LoopDiagnosis
	if args.latest || args.loopID == "" {
		d, ok := DiagnoseLatest(ws)
		if !ok {
			io.WriteString(out, "No loop events found.\n")
			return nil
		}
		diag = d
	} else {
		diag = DiagnoseLoop(ws, args.loopID, "")
	}

	io.WriteString(out, RenderDiagnosis(diag))
	if args.lenses {
		profiles := BuildStepProfiles(LoadLoopEvents(ws, diag.LoopID))
		io.WriteString(out, RenderLenses(diag, RunLenses(diag, profiles)))
	}
	io.WriteString(out, RenderRecovery(diag))
	return nil
}
