package introspect

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"math"
	"regexp"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
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
// rather than letting everything collapse to 1.
type UsageError struct{ msg string }

func (e *UsageError) Error() string { return e.msg }

// Stderr is the whole block argparse writes to STDERR before exiting 2:
// the usage summary, then `prog: error: <message>`. Rendering it here rather
// than at the call site keeps the two halves of the divergence together —
// the code and the text a script greps for.
func (e *UsageError) Stderr() string {
	return usageText + "maro-introspect: error: " + e.msg + "\n"
}

// ExitStatus maps a Main error onto the (stderr, exit code) pair argparse
// produces: a usage error is the whole usage block plus `prog: error: msg`
// on stderr and exit 2, and anything else is not this function's to
// answer.
//
// It exists because there were two copies of that mapping — the `maro
// introspect` wrapper's, and one the differential wrote for itself and
// then asserted against CPython. The test was measuring its own copy, so
// the wrapper could have exited 1, or printed `err.Error()` without the
// usage block, and the suite stayed green. One function, two callers,
// and the differential now pins the one the operator actually runs.
func ExitStatus(err error) (stderr string, code int, handled bool) {
	if err == nil {
		return "", 0, true
	}
	var ue *UsageError
	if errors.As(err, &ue) {
		return ue.Stderr(), 2, true
	}
	return "", 0, false
}

func usagef(format string, a ...any) error {
	return &UsageError{msg: fmt.Sprintf(format, a...)}
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

// introspectOpt is one row of argparse's `_option_string_actions`, which is
// a dict keyed by option STRING — so `-h` and `--help` are two rows sharing
// one action, and that is why both spell their name `-h/--help` in an error
// message. The slice order is the parser's insertion order, because it is
// the order the ambiguity message lists candidates in.
type introspectOpt struct {
	str        string // the option string as typed
	dest       string // the action it feeds
	takesValue bool   // argparse nargs: 1 for --history, 0 for the flags
	label      string // _get_action_name: every option string of the action
}

var introspectOpts = []introspectOpt{
	{"-h", "help", false, "-h/--help"},
	{"--help", "help", false, "-h/--help"},
	{"--latest", "latest", false, "--latest"},
	{"--lenses", "lenses", false, "--lenses"},
	{"--history", "history", true, "--history"},
	{"--patterns", "patterns", false, "--patterns"},
}

// How an explicit argument was attached to an option string. argparse keeps
// these three states apart and BRANCHES on the difference: `--latest=x` (an
// equals sign) is always an error on a flag, while `-hx` (glued to a
// single-dash option) is argparse re-reading the tail as more single-dash
// options. A port that collapsed them would reject `-hx`, which prints help.
const (
	sepNone   = iota // no explicit argument at all
	sepConcat        // glued to a single-dash option: `-hx`
	sepEquals        // `--history=5`
)

// optTuple is one candidate reading of a token: which option it names, which
// option string matched (an abbreviation resolves to the full one), and any
// argument attached to it.
type optTuple struct {
	opt      int // index into introspectOpts, or -1 for an unknown option
	str      string
	sep      int
	explicit string
}

// negativeNumber is argparse's `_negative_number_matcher`, and it is NOT the
// anchored pattern it looks like it should be. CPython 3.14 spells it
// `-\.?\d` and applies it with `.match`, which anchors only at the START —
// so every token beginning with a dash and a digit is a positional, not just
// the ones that are entirely numeric. `-1latest` is a loop id.
//
// `\d` in a Python str pattern is Unicode-aware, so an Arabic-Indic digit
// counts; Go's `\d` is ASCII-only, hence the explicit category class.
//
// This is version-dependent in a way worth writing down, and the boundary
// is NOT where an earlier version of this comment put it. Measured on this
// box: `python3.12` still carries `^-\d+$|^-\d*\.\d+$`, anchored at both
// ends, under which `-1latest`, `-.5` and `-٥` are all unrecognized
// arguments. `python3.14` carries `-\.?\d` applied with `.match`, anchored
// only at the start. The change landed in 3.13, not 3.12.
//
// That matters here rather than being trivia: python3.12 IS installed on
// this machine, and pyprobe invokes bare `python3` off PATH. The port is
// correct for whichever interpreter PATH resolves to today (3.14.3) and
// would fail its own differential under the other one — so if this
// differential ever goes red on the negative-number cases, check
// `python3 --version` before checking the code.
var negativeNumber = regexp.MustCompile(`^-\.?[\p{Nd}]`)

// parseOptional is argparse's `_parse_optional`: given one token, either nil
// (it is a positional) or the candidate options it could be naming. It never
// reports an error — ambiguity is raised later, by the consumer.
func parseOptional(arg string) []optTuple {
	if arg == "" || arg[0] != '-' {
		return nil
	}
	for i, o := range introspectOpts {
		if o.str == arg {
			return []optTuple{{opt: i, str: arg, sep: sepNone}}
		}
	}
	// A lone "-" is a positional. It is also, once through `--history -`, an
	// invalid int value rather than a missing argument.
	if len(arg) == 1 {
		return nil
	}
	// An exact option string before the `=`. This block is LOAD-BEARING, and
	// an earlier comment here claiming no input could tell it from the prefix
	// search below was simply wrong: `-h=x` is exact here and comes out
	// sepEquals, which is what makes the consumer refuse it. Delete the block
	// and the same token resolves through optionTuples' single-dash arm as
	// sepConcat instead, and a refusal turns into a help screen.
	if before, after, found := strings.Cut(arg, "="); found {
		for i, o := range introspectOpts {
			if o.str == before {
				return []optTuple{{opt: i, str: before, sep: sepEquals, explicit: after}}
			}
		}
	}
	if tuples := optionTuples(arg); len(tuples) > 0 {
		return tuples
	}
	if negativeNumber.MatchString(arg) {
		return nil
	}
	// argparse's own comment: "if it contains a space, it was meant to be a
	// positional". `maro introspect "-a b"` diagnoses a loop with a space in
	// its id rather than failing on an unknown option.
	if strings.Contains(arg, " ") {
		return nil
	}
	return []optTuple{{opt: -1, str: arg, sep: sepNone}}
}

// optionTuples is argparse's `_get_option_tuples` — the abbreviation rule,
// which works differently on the two dash forms.
//
// A double-dash token is a PREFIX of a long option: `--lat` is `--latest`,
// `--l` is ambiguous between latest and lenses, and `--h` is ambiguous
// between help and history. A single-dash token instead offers its first two
// characters as a whole option and the rest as a glued argument, which is how
// `-hx` reaches the help action.
func optionTuples(arg string) []optTuple {
	var out []optTuple
	if len(arg) >= 2 && arg[1] == '-' {
		prefix, explicit, found := strings.Cut(arg, "=")
		sep := sepNone
		if !found {
			explicit = ""
		} else {
			sep = sepEquals
		}
		for i, o := range introspectOpts {
			if strings.HasPrefix(o.str, prefix) {
				out = append(out, optTuple{opt: i, str: o.str, sep: sep, explicit: explicit})
			}
		}
		return out
	}
	// Both halves of the single-dash arm cut on `=`, and they cut for
	// different reasons. The abbreviation test is against the part BEFORE the
	// `=` — not the whole token — which is how `-=x` matches every option in
	// the table (they all start with `-`) and comes out ambiguous rather than
	// unrecognized. The glued-argument test is against the first two code
	// points regardless of any `=`.
	prefix, explicit, found := strings.Cut(arg, "=")
	sep := sepNone
	if found {
		sep = sepEquals
	} else {
		explicit = ""
	}
	shortPrefix, shortExplicit := cutRunes(arg, 2)
	for i, o := range introspectOpts {
		switch {
		case o.str == shortPrefix:
			out = append(out, optTuple{opt: i, str: o.str,
				sep: sepConcat, explicit: shortExplicit})
		case strings.HasPrefix(o.str, prefix):
			out = append(out, optTuple{opt: i, str: o.str,
				sep: sep, explicit: explicit})
		}
	}
	return out
}

// cutRunes splits s after its first n code points. Python slices strings by
// code point, so `option_string[:2]` of `-٥x` is the dash and the digit —
// where a byte slice would cut the digit in half.
func cutRunes(s string, n int) (string, string) {
	i := 0
	for ; n > 0 && i < len(s); n-- {
		_, w := utf8.DecodeRuneInString(s[i:])
		i += w
	}
	return s[:i], s[i:]
}

// firstRune splits s into its first code point and the rest, the pair
// argparse spells `explicit_arg[0]` and `explicit_arg[1:]`.
func firstRune(s string) (string, string) { return cutRunes(s, 1) }

// runeAt is `s[i]` by code point, or "" if s is shorter. argparse tests
// `option_string[1]` to tell a single-dash option from a double-dash one.
func runeAt(s string, i int) string {
	_, rest := cutRunes(s, i)
	first, _ := cutRunes(rest, 1)
	return first
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

// parseIntrospectArgs is `parser.parse_args(argv)`.
//
// Two passes, because argparse takes two: every token is classified first,
// and only then are they consumed left to right. The order is observable.
// Classification never fails, so `--bogus -h` prints help instead of
// complaining, and `-h --l` prints help even though `--l` is ambiguous —
// while `--l -h` is an error, because consumption reaches the ambiguity
// first. An unrecognized option is not an error where it appears either: it
// is set aside and reported at the very end, after any error the rest of the
// line produces.
func parseIntrospectArgs(argv []string) (introspectArgs, error) {
	p := &parseState{
		argv:    argv,
		pattern: make([]byte, len(argv)),
		optIdx:  map[int][]optTuple{},
		// The one optional positional this parser declares, `loop_id`.
		positionalsLeft: 1,
	}
	err := p.parse()
	if errors.Is(err, errHelpAction) {
		// The help action is a SystemExit in CPython — it unwinds the parser
		// from wherever it fired, which is why nothing later on the line is
		// examined and why extras already collected go unreported.
		return p.a, nil
	}
	return p.a, err
}

// parseState is the local state of `_parse_known_args`: the classification
// string, the options found in it, the actions still unfilled, and the
// tokens nothing claimed.
type parseState struct {
	argv    []string
	pattern []byte // one byte per token: 'A', 'O', or '-'
	optIdx  map[int][]optTuple
	extras  []string

	positionalsLeft int
	a               introspectArgs
}

// actionTuple is one entry of argparse's `action_tuples` — an action and the
// strings it consumed, held until the whole option token is understood.
type actionTuple struct {
	opt  int
	args []string
}

// errHelpAction stands in for the SystemExit argparse's help action raises.
var errHelpAction = errors.New("help action")

func (p *parseState) parse() error {
	// Pass one: classify every token. argparse builds a STRING of one
	// character per token and every later decision matches against that
	// string rather than against the tokens themselves.
	terminated := false
	for i, arg := range p.argv {
		switch {
		case terminated:
			p.pattern[i] = 'A'
		case arg == "--":
			// Only the FIRST `--` is the terminator. A second one after it is
			// an ordinary positional, and `maro introspect -- --` diagnoses a
			// loop whose id is two dashes.
			p.pattern[i] = '-'
			terminated = true
		default:
			if t := parseOptional(arg); t != nil {
				p.pattern[i], p.optIdx[i] = 'O', t
			} else {
				p.pattern[i] = 'A'
			}
		}
	}

	// Pass two: alternate between the positionals preceding the next option
	// and the option itself, left to right. Classification never fails, so
	// `--bogus -h` prints help and `-h --l` prints help even though `--l` is
	// ambiguous — while `--l -h` is an error, because consumption reaches
	// the ambiguity first.
	maxOption := -1
	for i := range p.optIdx {
		if i > maxOption {
			maxOption = i
		}
	}
	start := 0
	for start <= maxOption {
		nextOption := start
		for nextOption <= maxOption {
			if _, ok := p.optIdx[nextOption]; ok {
				break
			}
			nextOption++
		}
		if start != nextOption {
			end := p.consumePositionals(start)
			if end > start {
				start = end
				continue
			}
			start = end
		}
		if _, ok := p.optIdx[start]; !ok {
			// Positionals nothing could take. They are set aside rather than
			// refused here, and reported at the very end.
			p.extras = append(p.extras, p.argv[start:nextOption]...)
			start = nextOption
		}
		var err error
		if start, err = p.consumeOptional(start); err != nil {
			return err
		}
	}

	stop := p.consumePositionals(start)
	p.extras = append(p.extras, p.argv[stop:]...)

	if len(p.extras) > 0 {
		return usagef("unrecognized arguments: %s", strings.Join(p.extras, " "))
	}
	return nil
}

// consumeOptional is argparse's function of the same name. One token can name
// SEVERAL actions — `-hh` is `-h -h` — so it loops, collecting actions and
// re-reading the tail, and takes none of them until the token is fully
// understood. That deferral is observable: `-hh=x` collects one help action
// and then refuses the `=x`, so it exits 2 instead of printing help. An
// earlier port took the help action the moment it was found and got every
// input of that shape wrong.
func (p *parseState) consumeOptional(startIndex int) (int, error) {
	tuples := p.optIdx[startIndex]
	if len(tuples) > 1 {
		names := make([]string, len(tuples))
		for j, t := range tuples {
			names[j] = t.str
		}
		// The message names the token AS TYPED, not the prefix it was cut
		// down to, so `--=x` reports `--=x` and lists every long option it
		// could have meant.
		return 0, usagef("ambiguous option: %s could match %s",
			p.argv[startIndex], strings.Join(names, ", "))
	}
	t := tuples[0]
	opt, optionString, sep, explicit := t.opt, t.str, t.sep, t.explicit
	// `explicit_arg is not None` is a different question from whether it is
	// empty: `--latest=` HAS an explicit argument, the empty string, and
	// argparse refuses it.
	hasExplicit := sep != sepNone

	var actions []actionTuple
	stop := 0
loop:
	for {
		if opt < 0 {
			// No action matched. The token is set aside whole and reported at
			// the end, after any error the rest of the line produces.
			p.extras = append(p.extras, p.argv[startIndex])
			return startIndex + 1, nil
		}
		o := introspectOpts[opt]

		if !hasExplicit {
			// No glued argument, so the values are the FOLLOWING tokens.
			s := startIndex + 1
			n, err := matchArgument(o, p.pattern[s:])
			if err != nil {
				return 0, err
			}
			stop = s + n
			actions = append(actions, actionTuple{opt: opt, args: p.argv[s:stop]})
			break loop
		}

		argCount := 0
		if o.takesValue {
			argCount = 1
		}
		switch {
		case argCount == 0 && runeAt(optionString, 1) != "-" && explicit != "":
			// A single-dash flag with a tail. argparse re-reads the tail as
			// more single-dash options — this is why `-hh` is `-h -h` — but
			// only when the tail could BE options: an `=` separator, or a
			// leading dash, means the user meant it as a value, and a value
			// is what a flag cannot take.
			//
			// Python's `sep` is '' for a glued tail and '=' for an explicit
			// one, so its `if sep or ...` tests for the `=` form alone. The
			// constants here are numbers and would ALL be truthy, so the test
			// has to name the case.
			head, tail := firstRune(explicit)
			if sep == sepEquals || head == "-" {
				return 0, usagef("argument %s: ignored explicit argument %s",
					o.label, pyval.Repr(explicit))
			}
			actions = append(actions, actionTuple{opt: opt})
			dash, _ := firstRune(optionString)
			optionString = dash + head
			next := lookupOption(optionString)
			if next < 0 {
				// The tail names no option: it joins the extras whole, and
				// the actions collected so far still run. `-hx` prints help
				// and never reports the `-x`.
				p.extras = append(p.extras, dash+explicit)
				stop = startIndex + 1
				break loop
			}
			opt, explicit = next, tail
			switch {
			case explicit == "":
				sep, hasExplicit = sepNone, false
			case strings.HasPrefix(explicit, "="):
				sep = sepEquals
				_, explicit = firstRune(explicit)
			default:
				sep = sepConcat
			}
		case argCount == 1:
			// The glued text IS the value: `--history=5`.
			stop = startIndex + 1
			actions = append(actions, actionTuple{opt: opt, args: []string{explicit}})
			break loop
		default:
			return 0, usagef("argument %s: ignored explicit argument %s",
				o.label, pyval.Repr(explicit))
		}
	}

	for _, at := range actions {
		if err := p.takeAction(at); err != nil {
			return 0, err
		}
	}
	return stop, nil
}

// matchArgument is `_match_argument` for the two nargs this parser uses. For
// an OPTIONAL, `_get_nargs_pattern` yields a pattern with no `-` in it at
// all, so a flag matches the empty string and takes nothing while
// `--history` matches exactly one POSITIONAL token.
//
// HOW it yields that differs by interpreter, and only 3.14's is described
// here: it selects `'([A])' if option else '(-*A-*)'` directly, and
// `'([AO]{0})'` for a zero-nargs optional. Through 3.12 the positional
// pattern was built first and then stripped with two `.replace` calls. Same
// result for these two nargs; a different mechanism, and python3.12 is on
// this box. The absence of `-` from the pattern is why `--history -- 5` is a missing
// argument rather than 5: the terminator classifies as '-', which the
// stripped pattern cannot consume — and so are `--history --tory` and
// `--history -x`, which classify as 'O'.
func matchArgument(o introspectOpt, pattern []byte) (int, error) {
	if !o.takesValue {
		return 0, nil
	}
	if len(pattern) == 0 || pattern[0] != 'A' {
		return 0, usagef("argument %s: expected one argument", o.label)
	}
	return 1, nil
}

// consumePositionals is argparse's function of the same name, narrowed to the
// single optional positional this parser declares. Its nargs pattern is
// `(-*A?-*)`: any terminator, then at most one token, then any terminator.
//
// The trailing `-*` is why the terminator cannot simply be dropped. `--` is
// removed only when a positional's matched span COVERS it, so `a -- b`
// matches "A-" and reports `unrecognized arguments: b`, while `a b -- c`
// matches only "A" and reports `b -- c` — terminator included. An earlier
// port skipped every `--` unconditionally and disagreed with CPython on the
// whole second shape.
func (p *parseState) consumePositionals(startIndex int) int {
	if p.positionalsLeft == 0 {
		return startIndex
	}
	i := startIndex
	for i < len(p.pattern) && p.pattern[i] == '-' {
		i++
	}
	if i < len(p.pattern) && p.pattern[i] == 'A' {
		i++
	}
	for i < len(p.pattern) && p.pattern[i] == '-' {
		i++
	}
	args := p.argv[startIndex:i]
	if bytes.IndexByte(p.pattern[startIndex:i], '-') >= 0 {
		// `args.remove('--')` — the first occurrence only.
		kept := make([]string, 0, len(args))
		dropped := false
		for _, s := range args {
			if !dropped && s == "--" {
				dropped = true
				continue
			}
			kept = append(kept, s)
		}
		args = kept
	}
	// The positional is consumed even when it matched NOTHING, which is what
	// makes a second bare token an extra rather than a loop id.
	p.positionalsLeft = 0
	if len(args) > 0 {
		p.a.loopID = args[0]
	}
	return i
}

// takeAction runs one collected action. argparse converts a value here, at
// the point of consumption, so an invalid `--history` is reported in the
// order the tokens appeared rather than after the whole line is read.
func (p *parseState) takeAction(at actionTuple) error {
	o := introspectOpts[at.opt]
	switch o.dest {
	case "help":
		p.a.help = true
		return errHelpAction
	case "latest":
		p.a.latest = true
	case "lenses":
		p.a.lenses = true
	case "patterns":
		p.a.patterns = true
	case "history":
		n, err := historyValue(o, at.args[0])
		if err != nil {
			return err
		}
		p.a.history = &n
	}
	return nil
}

// lookupOption is the `option_string in self._option_string_actions` test.
func lookupOption(s string) int {
	for i, o := range introspectOpts {
		if o.str == s {
			return i
		}
	}
	return -1
}

// historyValue is `type=int` — argparse's `_get_value`, which reports the
// exception's own text as `invalid int value: <repr>`.
func historyValue(o introspectOpt, raw string) (int, error) {
	n, err := pyval.Int(raw)
	if err == nil {
		return n, nil
	}
	if !errors.Is(err, pyval.ErrIntTooLarge) {
		return 0, usagef("argument %s: invalid int value: %s",
			o.label, pyval.Repr(raw))
	}
	// Python ints are arbitrary precision, so `--history` followed by forty
	// digits is a perfectly good (enormous) limit there and an overflow here.
	// Saturating keeps the BEHAVIOUR identical — load_diagnoses stops at the
	// end of the store either way, and the negative side takes the same
	// one-row branch as -5 — where reporting an invalid value would invent a
	// refusal CPython does not make.
	if strings.HasPrefix(pytext.Strip(raw), "-") {
		return math.MinInt, nil
	}
	return math.MaxInt, nil
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
