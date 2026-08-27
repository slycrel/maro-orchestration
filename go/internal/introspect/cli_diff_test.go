package introspect

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"sort"
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

# COLUMNS pins argparse's help formatter, which otherwise re-wraps to the
# terminal width. 80 is what CPython falls back to when stdout is not a
# terminal, so this is the rendering every scripted invocation sees — and
# without it this differential would agree or disagree by terminal size.
os.environ["COLUMNS"] = "80"

args = json.loads(sys.argv[1])
out = []
for c in args:
    os.environ["MARO_WORKSPACE"] = c["ws"]
    import importlib
    importlib.reload(introspect)
    buf, ebuf = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(ebuf):
            introspect.main(c["argv"])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    out.append({"code": code, "out": buf.getvalue(), "err": ebuf.getvalue()})
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

// diagRow builds one diagnoses.jsonl row. Package-level rather than a
// closure so every test in this file seeds its store through one builder —
// a second, hand-rolled row literal is a fixture that can drift from the
// reader it is meant to feed.
func diagRow(id, class, sev string, done, total, tokens int) string {
	return `{"loop_id": "` + id + `", "failure_class": "` + class +
		`", "severity": "` + sev + `", "steps_done": ` +
		itoa(done) + `, "steps_total": ` + itoa(total) +
		`, "total_tokens": ` + itoa(tokens) + `}`
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
		evt("loop-alpha", "step_done", `"step_idx": 1, "step": "fetch the widget index", "status": "done", "elapsed_ms": 900, "tokens_in": 11000, "tokens_out": 1000, "model": "grok-4.5"`),
		evt("loop-alpha", "step_done", `"step_idx": 2, "step": "verify the parser output", "status": "done", "elapsed_ms": 190000, "tokens_in": 639000, "tokens_out": 1000, "model": "grok-4.5"`),
		evt("loop-alpha", "step_done", `"step_idx": 3, "step": "render report tables", "status": "blocked", "elapsed_ms": 140000`),
		evt("loop-alpha", "loop_done", `"status": "stuck", "detail": "token budget"`),
	}
	// A clean loop, so the healthy path (no recovery block, thin lens
	// output) is measured too.
	healthy := []string{
		evt("loop-beta", "step_done", `"step_idx": 1, "step": "alpha", "status": "done", "elapsed_ms": 900, "tokens_in": 100, "model": "grok-4.5"`),
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
		// wantExit is the exit code CPython must produce: 0 when it renders
		// (help included — argparse exits 0 for that), 2 when argparse
		// refuses the argument line.
		//
		// It is DECLARED and then cross-checked against CPython rather than
		// simply read off it. A fixture whose name claims a refusal and whose
		// argument line quietly became legal would otherwise keep passing
		// while measuring nothing, which is exactly the failure L44 names.
		//
		// This field started out a bool meaning "exits somehow", and that
		// shape is why the whole error surface went unmeasured: a case struct
		// with no way to say "refuses with THIS message" cannot hold a
		// fixture that checks one, so six argparse divergences sat outside a
		// battery reporting 72/75.
		wantExit int
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
		// to diagnosing rather than printing an empty history. It is also
		// SUPPLIED, which makes it the one input separating "was it given"
		// from "is it non-zero" — the reason both terms of that guard are
		// written out.
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

		// --- the argument line itself ---------------------------------
		//
		// Everything below was invisible to this file until a reviewer
		// mutated the tree and found the guards unpinned. Each one is a
		// place where argparse and Go's flag package disagree SILENTLY:
		// the wrong reading produces plausible output, not an error.

		// THE HISTORY WINDOW. Six rows read at 2 must render two. The only
		// other history fixture asks for exactly as many rows as it seeded,
		// so replacing `*history` with a constant changed nothing and the
		// limit was never actually passed anywhere.
		{name: "a history window smaller than the store",
			argv: []string{"--history", "2"}, store: "diagnoses",
			rows: []string{
				diagRow("h1", "healthy", "info", 1, 1, 1),
				diagRow("h2", "healthy", "info", 2, 2, 2),
				diagRow("h3", "healthy", "info", 3, 3, 3),
				diagRow("h4", "token_explosion", "warning", 4, 4, 4),
				diagRow("h5", "adapter_timeout", "critical", 5, 5, 5),
				diagRow("h6", "healthy", "info", 6, 6, 6)}},
		// A NEGATIVE N is TRUTHY. `if args.history:` takes the branch, and
		// load_diagnoses breaks on `1 >= -5` after one append — so CPython
		// renders exactly one row where a `> 0` port renders a diagnosis.
		{name: "a negative history is truthy and renders one row",
			argv: []string{"--history", "-5"}, store: "diagnoses",
			rows: []string{
				diagRow("n1", "healthy", "info", 1, 1, 1),
				diagRow("n2", "token_explosion", "warning", 2, 2, 2),
				diagRow("n3", "adapter_timeout", "critical", 3, 3, 3)}},
		{name: "a value attached with an equals sign",
			argv: []string{"--history=5"}, store: "diagnoses",
			rows: []string{diagRow("e1", "healthy", "info", 1, 1, 1)}},
		// `--` ends the options. Everything after it is a positional, even a
		// token spelled exactly like a flag.
		{name: "a double dash makes the next token a loop id",
			argv: []string{"--", "--latest"}, store: "events", rows: explosion},
		// ARGPARSE ABBREVIATES. A unique prefix resolves to its option, so
		// `--lat` diagnoses the latest loop and `--hist 5` prints history.
		// Go's flag package rejects both, which is a silent behaviour change
		// for anything that already types them.
		{name: "an abbreviated option", argv: []string{"--lat"},
			store: "events", rows: explosion},
		{name: "an abbreviated option with a value",
			argv: []string{"--hist", "5"}, store: "diagnoses",
			rows: []string{diagRow("p1", "healthy", "info", 1, 1, 1)}},
		// A LEADING-DASH NEGATIVE NUMBER is a positional, because no option
		// of this parser looks like one. `-5` diagnoses a loop named "-5".
		{name: "a negative number is a loop id", argv: []string{"-5"},
			store: "events", rows: explosion},

		// --- argument lines CPython REFUSES ---------------------------
		{name: "an ambiguous abbreviation", argv: []string{"--l"},
			store: "events", rows: explosion, wantExit: 2},
		{name: "an unknown option", argv: []string{"--bogus"},
			store: "events", rows: explosion, wantExit: 2},
		// A single-dash long name. Go's flag treats -latest and --latest as
		// the same thing; argparse does not have the first at all.
		{name: "a single-dash long name", argv: []string{"-latest"},
			store: "events", rows: explosion, wantExit: 2},
		// A store_true option given an explicit value.
		{name: "a value attached to a boolean flag",
			argv: []string{"--latest=true"}, store: "events",
			rows: explosion, wantExit: 2},
		{name: "a second positional", argv: []string{"a", "b"},
			store: "events", rows: explosion, wantExit: 2},
		{name: "a value-taking option with nothing after it",
			argv: []string{"--history"}, store: "diagnoses", wantExit: 2},

		// --- HELP, which argparse prints to stdout and exits 0 for --------
		{name: "help", argv: []string{"-h"}, store: "events", rows: explosion},
		{name: "help by its long name", argv: []string{"--help"},
			store: "events", rows: explosion},
		// A glued tail on a single-dash flag is argparse re-reading it as
		// more single-dash options. The tail IS read — an earlier comment
		// here claiming the help action exits first was wrong, and `-hh=x`
		// below is the input that disproves it. `-hh` resolves its tail to a
		// second `-h`; `-hx` fails to resolve it and sets `-x` aside; both
		// then run the help action they collected, so both print help. A
		// port that rejected every single-dash long name (this one did)
		// refuses all of them.
		{name: "help with a glued tail", argv: []string{"-hh"},
			store: "events", rows: explosion},
		{name: "help with an unresolvable glued tail", argv: []string{"-hx"},
			store: "events", rows: explosion},
		{name: "help with a glued tail three deep", argv: []string{"-hhh"},
			store: "events", rows: explosion},
		// An unrecognized option is NOT an error where it appears: it is set
		// aside and reported at the end, so a `-h` after it still wins.
		{name: "help wins over a deferred unknown option",
			argv: []string{"--bogus", "-h"}, store: "events", rows: explosion},

		// --- the negative-number rule, which is not what it looks like ----
		// CPython 3.14's matcher is `-\.?\d` applied with `.match`, anchored
		// only at the START. Any token beginning with a dash and a digit is a
		// positional, not just the wholly numeric ones.
		{name: "a dash-digit token is a loop id, not a flag",
			argv: []string{"-1latest"}, store: "events", rows: explosion},
		// `\d` is Unicode-aware in a Python str pattern, so an Arabic-Indic
		// digit takes the same branch. Go's `\d` is ASCII-only and would put
		// this one on the error path.
		{name: "a non-ascii digit counts as a number",
			argv: []string{"-\u0665"}, store: "events", rows: explosion},

		// --- abbreviation, values, and the terminator ---------------------
		// An abbreviation WITH an attached value: the resolved name has to
		// survive the rewrite, which `--history=5` alone cannot check because
		// there the abbreviation and the full name are the same string.
		{name: "an abbreviated option with an attached value",
			argv: []string{"--hist=5"}, store: "diagnoses",
			rows: []string{diagRow("q1", "healthy", "info", 1, 1, 1)}},
		{name: "two abbreviated options", argv: []string{"--lat", "--len"},
			store: "events", rows: explosion},
		// Python's int() strips surrounding whitespace, so " 5" is 5 — where
		// Go's strconv.Atoi refuses it. (pyval.Int carries the whole rule,
		// underscores and all.)
		{name: "an int value with surrounding whitespace",
			argv: []string{"--history", " 5"}, store: "diagnoses",
			rows: []string{diagRow("r1", "healthy", "info", 1, 1, 1)}},
		// The two rows that used to be TestNonASCIIDigitIsAKnownGap. int()
		// reads any Unicode decimal digit, so U+0665 is 5, and the negated
		// form takes the truthy-negative branch above. pyval's int lane
		// grew that on 2026-08-27 and the pin fired the same run; these are
		// its fixtures, in the table where every other row asserts
		// agreement.
		{name: "an arabic-indic history",
			argv: []string{"--history", "\u0665"}, store: "diagnoses",
			rows: []string{
				diagRow("k1", "healthy", "info", 1, 1, 1),
				diagRow("k2", "token_explosion", "warning", 2, 2, 2)}},
		{name: "a NEGATED arabic-indic history",
			argv: []string{"--history", "-\u0665"}, store: "diagnoses",
			rows: []string{
				diagRow("k1", "healthy", "info", 1, 1, 1),
				diagRow("k2", "token_explosion", "warning", 2, 2, 2)}},
		// The other half of the same fix: str.strip() removes U+001F and
		// int() does not, so this stays a usage error on BOTH sides.
		{name: "a unit separator is not whitespace to int()",
			argv: []string{"--history", "\u001f5"}, store: "diagnoses",
			rows:     []string{diagRow("r1", "healthy", "info", 1, 1, 1)},
			wantExit: 2},
		// Only the FIRST `--` terminates; a second one is an ordinary token.
		{name: "a second double dash is a loop id",
			argv: []string{"--", "--"}, store: "events", rows: explosion},

		// --- refusals, compared by their MESSAGE --------------------------
		{name: "an abbreviation ambiguous between help and history",
			argv: []string{"--h"}, store: "events", rows: explosion, wantExit: 2},
		// The message names the token AS TYPED and lists every candidate, so
		// this one reports `--=x` and all five long options.
		{name: "an empty abbreviation with a value",
			argv: []string{"--=x"}, store: "events", rows: explosion, wantExit: 2},
		// The action owns BOTH its option strings, and the message says so:
		// "argument -h/--help", whichever spelling was typed.
		{name: "a value attached to the short help flag",
			argv: []string{"-h=x"}, store: "events", rows: explosion, wantExit: 2},
		// ...and the LONG spelling reports the same label, because the label
		// is the action's, not the token's.
		{name: "a value attached to the long help flag",
			argv: []string{"--help=x"}, store: "events", rows: explosion,
			wantExit: 2},
		// A store_true given an EMPTY explicit value still refuses.
		{name: "an empty value attached to a boolean flag",
			argv: []string{"--latest="}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a triple dash", argv: []string{"---latest"},
			store: "events", rows: explosion, wantExit: 2},
		{name: "an unknown option with an attached value",
			argv: []string{"--bogus=x"}, store: "events", rows: explosion,
			wantExit: 2},
		// The int conversion is argparse's, and its failure is a usage error
		// with its own wording — not a runtime error, and not exit 1.
		{name: "a non-numeric value", argv: []string{"--history", "abc"},
			store: "diagnoses", wantExit: 2},
		// A lone "-" classifies as a POSITIONAL, so it is consumed as the
		// value and fails the int conversion — where an option-looking token
		// is not consumed at all and fails as a missing argument. Two
		// different messages, one character apart in the input.
		{name: "a lone dash as a value", argv: []string{"--history", "-"},
			store: "diagnoses", wantExit: 2},
		{name: "an option-looking token is not a value",
			argv: []string{"--history", "--tory"}, store: "diagnoses",
			wantExit: 2},
		{name: "the terminator is not a value",
			argv: []string{"--history", "--", "5"}, store: "diagnoses",
			wantExit: 2},
		// Extras are reported in the order the TOKENS appeared, mixing
		// unknown options with surplus positionals.
		{name: "a surplus positional and an unknown option",
			argv: []string{"a", "b", "--bogus"}, store: "events",
			rows: explosion, wantExit: 2},
		// ...and a bare token after an unknown option is the loop id, so it
		// is NOT in the extras.
		{name: "an unknown option does not consume the loop id",
			argv: []string{"--patterns", "--bogus", "extra"}, store: "events",
			rows: explosion, wantExit: 2},
		// The terminator is dropped only when the POSITIONAL's matched span
		// covers it. `loop_id` is nargs='?', whose pattern is `(-*A?-*)`, so
		// in `a -- b` the match runs "A-" and swallows the `--`; the extras
		// are just `b`.
		{name: "a surplus positional after the terminator",
			argv: []string{"a", "--", "b"}, store: "events", rows: explosion,
			wantExit: 2},
		// ...but one token further out, the span is only "A" and the
		// terminator STAYS, reported among the extras exactly as typed. The
		// port used to skip every `--` unconditionally, which is right for
		// the case above and wrong for all five of these.
		{name: "the terminator survives in the extras",
			argv: []string{"a", "b", "--", "c"}, store: "events",
			rows: explosion, wantExit: 2},
		{name: "a trailing terminator survives in the extras",
			argv: []string{"a", "b", "--"}, store: "events",
			rows: explosion, wantExit: 2},
		{name: "the terminator survives past an option",
			argv: []string{"a", "--latest", "b", "--", "c"}, store: "events",
			rows: explosion, wantExit: 2},
		{name: "the terminator survives past an unknown option",
			argv: []string{"--bogus", "a", "b", "--", "c"}, store: "events",
			rows: explosion, wantExit: 2},
		{name: "the terminator survives past a consumed value",
			argv:  []string{"--history", "5", "a", "b", "--", "c"},
			store: "diagnoses", wantExit: 2},
		// A glued tail that itself starts with a dash is NOT re-read as more
		// options — it is the one shape of `-h<tail>` argparse refuses.
		{name: "a glued tail starting with a dash",
			argv: []string{"-h-x"}, store: "events", rows: explosion,
			wantExit: 2},
		// The re-read LOOPS, and a refusal on the second pass beats an action
		// collected on the first. `-hh=x` resolves its first tail character
		// to another `-h`, and the `=x` left over is an explicit argument a
		// flag cannot take — so this exits 2 with the help action collected
		// but never run. Every one of these printed help before the loop was
		// ported.
		{name: "a looped re-read refuses on its second pass",
			argv: []string{"-hh=x"}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a looped re-read refuses a dashed tail",
			argv: []string{"-hh-x"}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a looped re-read refuses an empty explicit value",
			argv: []string{"-hh="}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a looped re-read refuses a bare dash tail",
			argv: []string{"-hh-"}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a re-read three deep still refuses",
			argv: []string{"-hhh=x"}, store: "events", rows: explosion,
			wantExit: 2},
		// The single-dash abbreviation test is against the part BEFORE the
		// `=`, not the whole token. `-=x` cuts to `-`, which every option in
		// the table starts with, so it is AMBIGUOUS — six candidates, short
		// and long spellings alike. Testing the whole token instead makes it
		// an unrecognized argument, a different message and a different
		// exit path.
		{name: "a single dash with a value is ambiguous, not unknown",
			argv: []string{"-=x"}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a single dash with an empty value is ambiguous",
			argv: []string{"-="}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a single dash with a numeric value is ambiguous",
			argv: []string{"-=5"}, store: "events", rows: explosion,
			wantExit: 2},
		{name: "a single dash with a doubled equals is ambiguous",
			argv: []string{"-==x"}, store: "events", rows: explosion,
			wantExit: 2},
		// A tail that spells a real long option changes NOTHING: the cut is
		// still at the `=`, so this is the same six-way ambiguity.
		{name: "a single dash with an option-shaped value is ambiguous",
			argv: []string{"-=latest"}, store: "events", rows: explosion,
			wantExit: 2},

		// --- rules that had no fixture at all -----------------------------
		// `--latest` and a loop id TOGETHER. `if args.latest or not
		// args.loop_id:` takes the latest branch, so the named loop is
		// ignored and loop-beta renders. Without this, the guard's first
		// term is unpinned and `args.loopID == ""` alone passes the suite.
		{name: "an explicit latest outranks a named loop",
			argv: []string{"--latest", "loop-alpha"}, store: "events",
			rows: explosion},
		// argparse's own rule: "if it contains a space, it was meant to be a
		// positional". A dash-led token with a space in it is a loop id, not
		// an unknown option — the only input that reaches that branch.
		{name: "a dash-led token with a space is a loop id",
			argv: []string{"-a b"}, store: "events", rows: explosion},
		// The `\.?` in the negative-number matcher. `-.5` starts with a dash,
		// a dot and a digit, so it classifies as a positional; without the
		// optional dot it would be an unrecognized option instead.
		{name: "a leading-dot number is a loop id",
			argv: []string{"-.5"}, store: "events", rows: explosion},

		// --- one place the two runtimes cannot agree exactly -------------
		// Python ints are arbitrary precision, so this is a valid (enormous)
		// limit there and an overflow here. The port saturates rather than
		// inventing a refusal: load_diagnoses runs out of store long before
		// either number matters, so the RENDERING is identical — which is
		// what this case checks.
		{name: "an int value past every machine bound",
			argv:  []string{"--history", "99999999999999999999999"},
			store: "diagnoses",
			rows:  []string{diagRow("s1", "healthy", "info", 1, 1, 1)}},
		// The NEGATIVE bound, which saturates the other way and lands on the
		// one-row branch a plain `-5` takes. Saturating both directions to
		// MaxInt renders the whole store here and passes every other case,
		// so this is what separates the two arms.
		{name: "an int value past the negative machine bound",
			argv:  []string{"--history", "-99999999999999999999999"},
			store: "diagnoses",
			rows: []string{
				diagRow("t1", "healthy", "info", 1, 1, 1),
				diagRow("t2", "token_explosion", "warning", 2, 2, 2),
				diagRow("t3", "adapter_timeout", "critical", 3, 3, 3)}},
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
		Code int    `json:"code"`
		Out  string `json:"out"`
		Err  string `json:"err"`
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
			if w.Code != c.wantExit {
				t.Fatalf("cpython exited %d, fixture declares %d\n"+
					"--- stdout ---\n%s\n--- stderr ---\n%s",
					w.Code, c.wantExit, w.Out, w.Err)
			}
			// Both anti-vacuity checks are the same guard as L1: a case that
			// compares "" against "" agrees without measuring. A rendering
			// case must render, and a refusal must say something.
			if c.wantExit == 0 && strings.TrimSpace(w.Out) == "" {
				t.Fatalf("cpython rendered nothing for %q — the fixture "+
					"cannot tell two implementations apart", c.name)
			}
			if c.wantExit != 0 && strings.TrimSpace(w.Err) == "" {
				t.Fatalf("cpython refused %q silently — nothing to compare",
					c.name)
			}

			// The header above says the Go side is deliberately not a
			// writer, and that claim is why the two runtimes need separate
			// store copies at all. Nothing checked it until round 4:
			// goWS[i] was passed to Main and never looked at again, so a
			// Go-side write would have left the suite green and the
			// chunk's headline divergence pinned on only one of its two
			// sides. When DiagnoseLoop starts emitting its captain's-log
			// event, THIS is what goes red.
			before := snapshotDir(t, goWS[i])

			var buf bytes.Buffer
			err := Main(goWS[i], c.argv, &buf)

			if after := snapshotDir(t, goWS[i]); !reflect.DeepEqual(before, after) {
				t.Errorf("Main wrote to its workspace\nbefore: %v\nafter:  %v",
					keysOf(before), keysOf(after))
			}
			// ExitStatus is PRODUCTION code — the same call `maro
			// introspect` makes. This switch used to be a second copy of
			// that mapping written here in the test, which meant CPython
			// was being compared against the copy: the wrapper could have
			// exited 1, or printed the message without its usage block,
			// and every case below stayed green.
			gotErr, gotCode, handled := ExitStatus(err)
			if !handled {
				t.Fatalf("go Main: %v", err)
			}
			if gotCode != w.Code {
				t.Errorf("exit code: cpython %d, go %d (%v)", w.Code, gotCode, err)
			}
			if got := buf.String(); got != w.Out {
				t.Errorf("stdout differs\n--- cpython ---\n%s\n--- go ---\n%s",
					showWhitespace(w.Out), showWhitespace(got))
			}
			// STDERR IS COMPARED IN FULL, usage block and all. Comparing only
			// "did it refuse" is what let five distinct refusals collapse into
			// one indistinguishable outcome: `-latest` and `--atest` are both
			// exit 2, and only the message says the port cut the token in the
			// wrong place.
			if gotErr != w.Err {
				t.Errorf("stderr differs\n--- cpython ---\n%s\n--- go ---\n%s",
					showWhitespace(w.Err), showWhitespace(gotErr))
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

// snapshotDir maps every file under root to its contents. Comparing two
// snapshots catches a write that ADDS a file, one that appends to an
// existing one, and one that rewrites it in place — where checking the
// file list alone would see only the first.
func snapshotDir(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	err := filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return err
		}
		out[rel] = string(b)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

func keysOf(m map[string]string) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

// TestExitStatus pins ExitStatus's three answers directly, because one of
// them cannot be reached through Main.
//
// Main returns a *UsageError or nil and nothing else today, so the
// `handled=false` arm is a guard that cannot fire from the CLI — a battery
// mutant flipping it to `true` survived the whole suite. The arm is kept
// rather than deleted: without it, a Main that later grows a real error
// return (an unreadable store, say) would have that error silently
// swallowed by the wrapper and reported as a clean exit, which is the
// failure that fails OPEN. Keeping a guard means pinning it, and the input
// it needs is a synthetic error rather than an argument line.
func TestExitStatus(t *testing.T) {
	t.Run("nil is handled and says nothing", func(t *testing.T) {
		stderr, code, handled := ExitStatus(nil)
		if !handled || code != 0 || stderr != "" {
			t.Errorf("ExitStatus(nil) = (%q, %d, %v), want (\"\", 0, true)",
				stderr, code, handled)
		}
	})
	t.Run("a usage error is argparse's block and code 2", func(t *testing.T) {
		err := usagef("unrecognized arguments: %s", "--bogus")
		stderr, code, handled := ExitStatus(err)
		if !handled || code != 2 {
			t.Fatalf("ExitStatus(usage) = (_, %d, %v), want (_, 2, true)",
				code, handled)
		}
		if !strings.HasPrefix(stderr, "usage: maro-introspect") ||
			!strings.Contains(stderr, "maro-introspect: error: unrecognized arguments: --bogus") {
			t.Errorf("stderr is not the argparse block:\n%s", stderr)
		}
	})
	t.Run("any other error is not this function's to answer", func(t *testing.T) {
		stderr, code, handled := ExitStatus(errors.New("the store is unreadable"))
		if handled {
			t.Errorf("a non-usage error was reported as handled, which is how "+
				"the wrapper would exit 0 on a real failure (got %q, %d)",
				stderr, code)
		}
	})
}
