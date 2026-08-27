package artifactcheck

import (
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The differential for src/artifact_check.py slice 1.
//
// SHAPE, and why it is this shape. Every fixture below is one INPUT and
// nothing else — there is no expected value written down anywhere in this
// file. CPython is asked for the answer at test time, over the same input,
// through acPySrc; the port is asked separately; the two are compared. A
// fixture table with literal expectations would be a transcription of what
// CPython did on the day it was written, and would keep passing after
// CPython changed underneath it.
//
// ISOLATION. Each row gets its OWN directory on the Go side, and the probe
// rebuilds its tree from scratch (rmtree + mkdir) for each row on the
// Python side. That is not tidiness: a shared tree is how one row's setup
// pays for another row's assertion, and this package's layer-1 check turns
// on whether the workspace diff is EMPTY — a single leftover file from the
// previous row silently converts a fabrication verdict into a clean one.
//
// ONE NORMALISATION, named. The port's answers are Go values and CPython's
// arrive as decoded JSON, so they are compared after a round trip through
// encoding/json — which makes every number a float64 and every map a
// map[string]any on both sides. That erases exactly one property worth
// having: the KEY ORDER of a rendered verdict. It is therefore asserted
// separately and directly, against `dataclasses.fields(ArtifactVerdict)`,
// in TestTheVerdictRendersItsFieldsInTheDataclassOrder. A normalisation
// that nothing else covers has moved the assertion into the normaliser;
// this one is covered.

type acCase struct {
	name string
	in   map[string]any
	// answer computes the port's answer. base is a directory this harness
	// has already built from in["files"] / in["mtimes"], or "" for the
	// rows that take no tree.
	answer func(t *testing.T, c acCase, base string) any
}

// --- harness --------------------------------------------------------------

func acStr(c acCase, k string) string {
	if v, ok := c.in[k].(string); ok {
		return v
	}
	return ""
}

func acStrs(c acCase, k string) []string {
	raw, _ := c.in[k].([]string)
	return raw
}

func acBoolDefault(c acCase, k string, def bool) bool {
	if v, ok := c.in[k].(bool); ok {
		return v
	}
	return def
}

// acDir is `str(base) if c.get("with_dir", True) else None` — the two
// values Python's Optional admits, collapsed to the one Go has for
// "absent" on a string parameter.
func acDir(c acCase, base string) string {
	if !acBoolDefault(c, "with_dir", true) {
		return ""
	}
	return base
}

// acMktree mirrors the probe's mktree exactly, including that a nil value
// makes a DIRECTORY rather than an empty file, and that mtimes are applied
// after every file exists.
func acMktree(t *testing.T, base string, files map[string]any, mtimes map[string]float64) {
	t.Helper()
	if err := os.MkdirAll(base, 0o777); err != nil {
		t.Fatal(err)
	}
	rels := make([]string, 0, len(files))
	for rel := range files {
		rels = append(rels, rel)
	}
	sort.Strings(rels)
	for _, rel := range rels {
		p := filepath.Join(base, rel)
		if files[rel] == nil {
			if err := os.MkdirAll(p, 0o777); err != nil {
				t.Fatal(err)
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(files[rel].(string)), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	for rel, ts := range mtimes {
		// os.utime(path, (ts, ts)) with float seconds. The nanosecond
		// conversion has to be exact for W9, whose whole subject is a
		// difference of one epsilon.
		tm := time.Unix(0, int64(ts*1e9))
		if err := os.Chtimes(filepath.Join(base, rel), tm, tm); err != nil {
			t.Fatal(err)
		}
	}
}

// acSetMtimeParts is the probe's `os.utime(p, ns=(...))` arm: an EXACT
// timestamp given as whole seconds plus nanoseconds.
//
// os.Chtimes cannot express one. It builds its syscall argument from
// `t.UnixNano()`, which is undefined outside 1678-09-21..2262-04-11 and
// reports no error when it wraps — so asking it for a year-2400 mtime
// silently writes something near 1901, and the two sides of the
// differential would then be comparing different trees rather than
// different code. syscall.UtimesNano takes the seconds directly.
func acSetMtimeParts(t *testing.T, base string, parts map[string][]int64) {
	t.Helper()
	for rel, sn := range parts {
		ts := syscall.Timespec{Sec: sn[0], Nsec: sn[1]}
		if err := syscall.UtimesNano(filepath.Join(base, rel),
			[]syscall.Timespec{ts, ts}); err != nil {
			t.Fatalf("setting mtime of %s to %d.%09d: %v", rel, sn[0], sn[1], err)
		}
	}
}

func acFiles(c acCase) map[string]any {
	f, _ := c.in["files"].(map[string]any)
	return f
}

func acMtimes(c acCase) map[string]float64 {
	m, _ := c.in["mtimes"].(map[string]float64)
	return m
}

func acBefore(c acCase) map[string]float64 {
	m, _ := c.in["before"].(map[string]float64)
	out := map[string]float64{}
	for k, v := range m {
		out[k] = v
	}
	return out
}

// acSortedKeys is `sorted(snap.keys())`, the shape every snapshot row
// compares in — the map itself has no order to compare.
func acSortedKeys(m map[string]float64) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func acSortedSet(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// acObjMap flattens a rendered verdict for the value comparison. The key
// ORDER it drops is asserted on its own; see the file header.
func acObjMap(v Verdict) map[string]any {
	out := map[string]any{}
	for _, f := range v.ToDict() {
		out[f.Key] = f.Val
	}
	return out
}

// acGeneric puts a Go answer into the same shape a decoded JSON value has,
// so the two sides are compared as values rather than as types.
func acGeneric(t *testing.T, v any) any {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshalling the port's answer: %v", err)
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatalf("re-decoding the port's answer: %v", err)
	}
	return out
}

// acScriptedInert is the Go half of the probe's `_fake`: it identifies a
// candidate by CONTENT, not by path, because the real `_python_is_inert`
// only ever sees source text.
//
// Both sides walk the script in the same order — the probe reads a JSON
// object whose keys arrive in the order Go's encoder emitted them, which
// is sorted — so a script naming two files with IDENTICAL content would
// still answer the same on both sides. It would nonetheless be a fixture
// whose answer depends on something the fixture does not state, so
// acAssertDistinctScriptedContent refuses one.
func acScriptedInert(t *testing.T, base string, script map[string]any) InertFunc {
	names := make([]string, 0, len(script))
	for n := range script {
		names = append(names, n)
	}
	sort.Strings(names)
	return func(source string) (bool, bool) {
		for _, name := range names {
			b, err := os.ReadFile(filepath.Join(base, name))
			if err != nil {
				continue
			}
			if string(b) != source {
				continue
			}
			v := script[name]
			if v == nil {
				return false, false // Python's None: cannot tell
			}
			return v.(bool), true
		}
		return false, false
	}
}

// acAssertDistinctScriptedContent is the pre-check that keeps an
// inert-script fixture from having a second, unstated input: if two named
// files hold the same bytes, "which verdict applies" is decided by
// iteration order rather than by the fixture.
func acAssertDistinctScriptedContent(t *testing.T, c acCase) {
	t.Helper()
	script, ok := c.in["inert"].(map[string]any)
	if !ok || len(script) < 2 {
		return
	}
	seen := map[string]string{}
	files := acFiles(c)
	for name := range script {
		body, ok := files[name].(string)
		if !ok {
			continue
		}
		if prev, dup := seen[body]; dup {
			t.Fatalf("fixture %q scripts %q and %q with identical content — "+
				"which verdict applies is then decided by iteration order, "+
				"not by the fixture", c.name, prev, name)
		}
		seen[body] = name
	}
}

// --- the fixture table ----------------------------------------------------

func acCases() []acCase {
	// The exec-transcript builder, matching the probe's `B` lambda.
	B := func(err any, cmd string) any {
		return map[string]any{"name": "Bash", "is_error": err,
			"input": map[string]any{"command": cmd}}
	}
	bt := "`"

	var cs []acCase
	add := func(name string, in map[string]any, answer func(*testing.T, acCase, string) any) {
		cs = append(cs, acCase{name: name, in: in, answer: answer})
	}

	// --- constants and the dataclass -------------------------------------
	add("Z1 the constants", map[string]any{"kind": "consts"},
		func(t *testing.T, c acCase, base string) any {
			skips := make([]string, 0, len(skipDirs))
			for d := range skipDirs {
				skips = append(skips, d)
			}
			sort.Strings(skips)
			tools := make([]string, 0, len(executionTools))
			for d := range executionTools {
				tools = append(tools, d)
			}
			sort.Strings(tools)
			return map[string]any{
				"MAX_FILES": MaxFiles, "MTIME_EPS": MtimeEps,
				"SKIP_DIRS": skips, "EXECUTION_TOOLS": tools,
			}
		})
	add("Z2 an ArtifactVerdict's defaults", map[string]any{"kind": "verdict_default"},
		func(t *testing.T, c acCase, base string) any { return acObjMap(newVerdict()) })

	// --- _clean_token -----------------------------------------------------
	for _, row := range [][2]string{
		{"T1 nothing to clean", "out.txt"},
		{"T2 surrounding whitespace", "  out.txt  "},
		{"T3 a backtick pair", bt + "out.txt" + bt},
		{"T4 mixed wrappers", "\"'(out.txt)'\""},
		{"T5 trailing sentence punctuation", "out.txt.,;:"},
		{"T6 the order matters: strip() runs BEFORE the wrapper strip", " " + bt + "out.txt" + bt + " "},
		{"T7 the order matters: rstrip runs AFTER the wrapper strip", "(out.txt.)"},
		{"T8 a token that is only wrappers", bt + "'\"()"},
		{"T9 an inner quote survives", "a'b.txt"},
		{"T10 a trailing dot is removed, so the extension goes with it", "out."},
		{"T11 empty", ""},
		{"T12 non-ascii", " «café.txt» "},
	} {
		add(row[0], map[string]any{"kind": "clean_token", "tok": row[1]},
			func(t *testing.T, c acCase, base string) any { return cleanToken(acStr(c, "tok")) })
	}

	// --- extract_write_claims --------------------------------------------
	for _, row := range [][2]string{
		{"E1 the plain shape", "wrote to out.txt"},
		{"E2 saved/into", "saved the report into report.md"},
		{"E3 created/at", "created a stub at src/thing.py"},
		{"E4 generated/as", "generated it as data.json"},
		{"E5 the irregular past tense is its own alternative", "I wrote everything to a.txt"},
		{"E6 writ-stem forms", "writing the notes to b.txt and written to c.txt"},
		{"E7 no extension is dropped", "saved to memory"},
		{"E8 a six-char extension is the limit", "wrote to a.abcdef"},
		{"E9 a seven-char extension is too long", "wrote to a.abcdefg"},
		{"E10 a bare slash is dropped", "wrote to /"},
		{"E11 a dot-slash is dropped", "wrote to ./"},
		{"E12 a trailing slash is dropped", "wrote to outdir/"},
		{"E13 dedup keeps first-seen order", "wrote to b.txt, saved to a.txt, wrote to b.txt"},
		{"E14 the period stops the span", "wrote it. to a.txt"},
		{"E15 a newline stops the span", "wrote it\nto a.txt"},
		{"E16 case insensitivity", "WROTE TO A.TXT"},
		{"E17 a quote opens the path", "saved to " + bt + "dir/a.txt" + bt},
		{"E18 a paren opens the path", "created at (a.txt)"},
		{"E19 the verb must be word-bounded", "rewrote to a.txt"},
		{"E20 'at' inside a word does not count", "wrote that a.txt"},
		{"E21 empty text", ""},
		{"E22 no verb at all", "the file a.txt is nice"},
		{"E23 non-greedy: the FIRST to wins", "wrote to a.txt to b.txt"},
		{"E24 a unicode word char after the verb", "wroteé to a.txt"},
		{"E25 a unicode stem", "savé to a.txt"},
		{"E26 a unicode path", "wrote to café.txt"},
		{"E27 output* is a verb here", "outputs to a.txt"},
		// U+0345 COMBINING GREEK YPOGEGRAMMENI is not a word character to
		// CPython and IS one to Go's `(?i)\p{L}`, because Go expands a
		// class's case-folds before negating it and U+0345's fold orbit
		// contains iota. It is the ONLY code point in the whole space where
		// the two disagree, and it inverted this function's answer: CPython
		// extracts the claim, the port extracted nothing, and a fabrication
		// check that finds no claim reports no fabrication. (artifactcheck
		// r2; the class-level pin is in internal/pytext.)
		{"E28 a fold-only word char before the verb", "x\u0345wrote to a.txt"},
		{"E29 the same character after the verb", "wrote\u0345 to a.txt"},
		// The zero-width-window decision's actual pin: the verb runs
		// straight into the preposition, so a translation that kept an
		// empty branch would extract a.txt where CPython extracts nothing.
		{"E30 the verb runs straight into the preposition", "wroteto a.txt"},
		{"E31 the same with a different preposition", "wroteat a.txt"},
		{"E28 dump", "dumped to a.json"},
		{"E29 export", "exported as a.csv"},
		{"E30 store", "stored at a.db"},
		{"E31 a claim with an absolute path", "wrote to /tmp/x/a.txt"},
		{"E32 a claim with a tilde path", "wrote to ~/a.txt"},
		{"E33 an extension with digits", "wrote to a.mp3"},
		{"E34 an extension with a trailing newline in the token", "wrote to a.txt\n"},
		{"E35 several claims across lines", "wrote to a.txt\nsaved into b.md\ncreated at c.py"},
		{"E36 a version number between verb and preposition kills the claim", "wrote v1.2 config to a.txt"},
		{"E37 a trailing sentence period is stripped off the token", "wrote the file to a.txt."},
		{"E38 a parenthesised suffix defeats the extension test", "wrote to a.txt(1)"},
		{"E39 a dotfile with no extension still passes the extension test", "wrote to .hidden"},
		{"E40 a bare trailing dot does not", "wrote to a."},
		{"E41 a comma after the token is stripped, the next path is not a claim", "stored as a.txt, then b.txt"},
		{"E42 a double extension keeps only the last for the test", "wrote to a.tar.gz"},
		{"E43 an absolute path ending a sentence", "generated at /tmp/o.json."},
	} {
		add(row[0], map[string]any{"kind": "claims", "text": row[1]},
			func(t *testing.T, c acCase, base string) any {
				return ExtractWriteClaims(acStr(c, "text"))
			})
	}

	// --- _claims_concrete_stdout -----------------------------------------
	for _, row := range [][2]string{
		{"S1 prints plus a digit", "it prints 42"},
		{"S2 a verb with no concrete content", "it prints things"},
		{"S3 concrete content with no verb", "the value is 42"},
		{"S4 a quoted literal counts as concrete", "prints 'Fizz'"},
		{"S5 a backtick span counts as concrete", "prints " + bt + "Fizz" + bt},
		{"S6 output followed by 'file' is excluded", "output file has 3 rows"},
		{"S7 output followed by 'to' is excluded", "output to 3 places"},
		{"S8 output followed by 'path' is excluded", "output path is /tmp/1"},
		{"S9 output followed by 'dir' is excluded", "output dir has 2 entries"},
		{"S10 output followed by anything else is NOT excluded", "output was 42"},
		{"S11 outputs (the s form) with the lookahead", "outputs file 3"},
		{"S10a the exclusion is a PREFIX: 'files' matches 'file'", "outputs files 3"},
		{"S10b 'tomorrow' matches the 'to' exclusion", "output tomorrow: 1"},
		{"S10c 'directory' matches the 'dir' exclusion", "output directory 1"},
		{"S10d 'pathology' matches the 'path' exclusion", "output pathology 1"},
		{"S10e the gap is \\s+, so a tab excludes too", "output\tfile 1"},
		{"S10f and a newline", "output\nfile 1"},
		{"S10g and more than one space", "output  file 1"},
		{"S10h every spelling of the verb is excluded, not just the bare one", "outputting file 3"},
		{"S10i and the -ted spelling", "outputted file 3"},
		{"S12 verified the output", "verified the output: 1,2,3"},
		{"S13 confirmed output", "confirmed output 7"},
		{"S14 the output is", "the output is 5"},
		{"S15 produces the output", "produces the output 9"},
		{"S16 when you run", "when you run it you get 1"},
		{"S17 running it", "running it gives 1"},
		{"S18 running the", "running the script gives 1"},
		{"S19 'returns' is deliberately not a stdout verb", "returns 42"},
		{"S20 stdout alone", "stdout was 42"},
		{"S21 empty text", ""},
		{"S22 a double-quoted literal", `prints "Buzz"`},
		{"S23 an empty quoted literal is not concrete", "prints ''"},
		// The missing trailing boundary, actually pinned: S20 has a space
		// after "stdout" and cannot see it. S26 is the control on the two
		// alternatives that DO carry a boundary internally.
		{"S24 stdout matches inside a longer word", "stdoutish was 42"},
		{"S25 prints matches inside a longer word", "printsomething 42"},
		{"S26 output* carries its own boundary and does not", "outputsomething 42"},
	} {
		add(row[0], map[string]any{"kind": "stdout_claim", "text": row[1]},
			func(t *testing.T, c acCase, base string) any {
				return claimsConcreteStdout(acStr(c, "text"))
			})
	}

	// --- _claims_clean_success -------------------------------------------
	for _, row := range [][2]string{
		{"K1 tests passed", "all tests passed"},
		{"K2 passed with an acknowledged failure", "tests passed but one error remained"},
		{"K3 succeeded", "the build succeeded"},
		{"K4 works", "it works"},
		{"K5 no errors", "no errors"},
		{"K6 'no errors' also matches the failure ack", "no errors at all"},
		{"K7 zero fail", "0 failures"},
		{"K8 exit code 0", "exit code 0"},
		{"K9 completed successfully", "completed successfully"},
		{"K10 built cleanly", "built cleanly"},
		{"K11 a check mark", "done ✓"},
		{"K12 a green check emoji", "done ✅"},
		{"K13 a failure ack alone", "it failed"},
		{"K14 didn't", "it didn't work"},
		{"K15 exit code 1", "exit code 1"},
		{"K16 non-zero", "non-zero exit"},
		{"K17 empty", ""},
		{"K18 nothing either way", "I looked at the file"},
		{"K19 traceback", "passes, though there was a traceback"},
		{"K20 'unable'", "works, but was unable to finish"},
		// The other direction of the same skew: CPython's `\b` before
		// "failed" holds because U+0345 is a NON-word character there, so
		// the ack fires and the text is not a clean-success claim. Go's
		// folded `[^\p{L}...]` refused to consume it, the ack was lost, and
		// CheckExecutionClaim flagged a contradiction CPython never emits.
		{"K21 a fold-only word char before a failure ack", "all tests passed \u0345failed"},
	} {
		add(row[0], map[string]any{"kind": "clean_success", "text": row[1]},
			func(t *testing.T, c acCase, base string) any {
				return claimsCleanSuccess(acStr(c, "text"))
			})
	}

	// --- _tool_failed -----------------------------------------------------
	for _, row := range []struct {
		name string
		te   map[string]any
	}{
		{"F1 is_error true", map[string]any{"is_error": true}},
		{"F2 is_error false", map[string]any{"is_error": false}},
		{"F3 is_error absent", map[string]any{"name": "Bash"}},
		{"F4 is_error is a truthy string", map[string]any{"is_error": "yes"}},
		{"F5 is_error is an empty string", map[string]any{"is_error": ""}},
		{"F6 is_error is 0", map[string]any{"is_error": 0}},
		{"F7 is_error is a non-empty list", map[string]any{"is_error": []any{1}}},
	} {
		add(row.name, map[string]any{"kind": "tool_failed", "te": row.te},
			func(t *testing.T, c acCase, base string) any {
				te, _ := c.in["te"].(map[string]any)
				return toolFailed(ToolEvent(te))
			})
	}

	// --- check_execution_claim -------------------------------------------
	for _, row := range []struct {
		name   string
		text   string
		events any
	}{
		{"X1 no transcript at all", "all tests passed", nil},
		{"X2 an empty transcript", "all tests passed", []any{}},
		{"X3 no Bash in the transcript", "all tests passed",
			[]any{map[string]any{"name": "Read", "is_error": true}}},
		{"X4 every command failed and success was claimed", "all tests passed",
			[]any{B(true, "pytest"), B(true, "make")}},
		{"X5 every command failed but the result admits it", "tests failed",
			[]any{B(true, "pytest")}},
		{"X6 one succeeded, so no contradiction", "all tests passed",
			[]any{B(true, "pytest"), B(false, "pytest")}},
		{"X7 all succeeded", "all tests passed", []any{B(false, "pytest")}},
		{"X8 failed commands but no success claim", "I ran some things",
			[]any{B(true, "pytest")}},
		{"X9 the command string is clipped at 80", "all tests passed",
			[]any{B(true, strings.Repeat("x", 200))}},
		{"X10 a failed event with no input falls back to the tool name", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true}}},
		{"X11 a failed event whose input is null", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true, "input": nil}}},
		{"X12 a non-dict element in the transcript", "all tests passed",
			[]any{"nope", B(true, "pytest")}},
		{"X13 several failures are all listed", "all tests passed",
			[]any{B(true, "a"), B(true, "b"), B(true, "c")}},

		// `(te.get("input") or {}).get(...)`. The `or {}` rescues a FALSY
		// input; a TRUTHY non-dict keeps its own value and `.get` on it is
		// an AttributeError, which the blanket except turns into the
		// fail-open verdict — fabricated=False, judged=False. A port that
		// quietly took the name default here would answer fabricated=TRUE,
		// confidently, on adversary-shaped JSON, which is the one direction
		// the module's docstring forbids. X14/X15 are the two spellings a
		// transcript can actually carry.
		{"X14 a failed event whose input is a truthy STRING", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": "rm -rf /"}}},
		{"X15 a failed event whose input is a truthy LIST", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": []any{"pytest"}}}},
		// The falsy side of the same line, one row per shape that reaches
		// the `or {}`. X17 is the one a Go type switch gets wrong on its
		// own: pyval.Truthy matches `map[string]any` by identity, so an
		// EMPTY ToolEvent falls to its "unknown object is truthy" default
		// and takes the branch `{} or {}` cannot take.
		{"X16 a failed event whose input is the number 0", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true, "input": 0}}},
		{"X17 a failed event whose input is an EMPTY dict", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": map[string]any{}}}},
		{"X18 a failed event whose input is a dict with no command key",
			"all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": map[string]any{"timeout": 5}}}},
	} {
		add(row.name,
			map[string]any{"kind": "exec_claim", "text": row.text, "tool_events": row.events},
			func(t *testing.T, c acCase, base string) any {
				raw, _ := c.in["tool_events"].([]any)
				events := make([]any, 0, len(raw))
				for _, e := range raw {
					if m, ok := e.(map[string]any); ok {
						events = append(events, ToolEvent(m))
					} else {
						events = append(events, e)
					}
				}
				if c.in["tool_events"] == nil {
					events = nil
				}
				return acObjMap(CheckExecutionClaim(acStr(c, "text"), events))
			})
	}

	// The rows above all reach CheckExecutionClaim as []any of ToolEvent,
	// because the harness converts every map on the way in. That
	// conversion is a REAL narrowing of what the fixtures can see: Python's
	// filter is `isinstance(te, dict)` and Go has two spellings of one
	// Python dict, so a transcript straight out of json.Unmarshal arrives
	// as map[string]any at every level. Before asMapping existed, that
	// spelling made the whole transcript invisible — every event skipped,
	// the check silently answering "no execution in transcript" — and X12
	// could not see it, because X12 proves WHERE the filter runs, not what
	// it accepts. A correct assertion covering the wrong blast radius.
	//
	// These rows send the maps through unconverted. Same inputs, other
	// spelling, same required answers.
	for _, row := range []struct {
		name   string
		text   string
		events []any
	}{
		{"X19 a transcript of raw map[string]any, as json.Unmarshal yields it",
			"all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": map[string]any{"command": "pytest"}}}},
		{"X20 a raw-map transcript with nothing to flag", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": false,
				"input": map[string]any{"command": "pytest"}}}},
		{"X21 a raw-map transcript of a non-execution tool", "all tests passed",
			[]any{map[string]any{"name": "Read", "is_error": true}}},
		// `te.get("name") in _EXECUTION_TOOLS` RAISES on an unhashable
		// name, and json.loads produces exactly those two types. X22's
		// first event is the adversarial one and its second is a real
		// failure, so a port that skipped the first would answer a
		// confident execution-contradiction where CPython fails open.
		{"X22 an unhashable LIST name ahead of a real failure",
			"all tests passed",
			[]any{map[string]any{"name": []any{"Bash"}, "is_error": true},
				map[string]any{"name": "Bash", "is_error": true,
					"input": map[string]any{"command": "pytest"}}}},
		{"X23 an unhashable DICT name on its own", "all tests passed",
			[]any{map[string]any{"name": map[string]any{"x": 1.0},
				"is_error": true}}},
		// The control: a name that is not a string but IS hashable answers
		// False from the `in` and never raises, which is what the comment
		// this fix replaced claimed was true of every non-string.
		{"X24 a hashable non-string name is skipped, not raised on",
			"all tests passed",
			[]any{map[string]any{"name": 7.0, "is_error": true}}},
		// `str(command)` is Python's str, so the VALUE TYPE is part of the
		// answer: str(5) is "5" and str(5.0) is "5.0". These rows carry the
		// Python-typed Go spellings (int, float64, []any) and pin that
		// pyval.Str reproduces each. A caller that decoded the transcript
		// with encoding/json would hand this function float64(5) for a JSON
		// `5` and render "5.0" where CPython renders "5" — see asMapping's
		// doc, and BACKLOG.
		{"X25 an integer command renders without a decimal point",
			"all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": map[string]any{"command": 5}}}},
		{"X26 a genuine float command keeps its point", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": map[string]any{"command": 5.5}}}},
		{"X27 a list command renders in Python repr", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": map[string]any{"command": []any{1, "x"}}}}},
		{"X28 a bool command", "all tests passed",
			[]any{map[string]any{"name": "Bash", "is_error": true,
				"input": map[string]any{"command": true}}}},
	} {
		add(row.name,
			map[string]any{"kind": "exec_claim", "text": row.text, "tool_events": row.events},
			func(t *testing.T, c acCase, base string) any {
				raw, _ := c.in["tool_events"].([]any)
				return acObjMap(CheckExecutionClaim(acStr(c, "text"), raw))
			})
	}

	// The walk's OWN joins, which A10 and P8c did not reach: the fix for
	// pathlib's `/` had four sites in one function and the class had more
	// outside it. This one feeds both snapshots, so getting it wrong empties
	// the diff and layer 1 reports a fabrication on a file that is there.
	add("W21 a snapshot rooted at a path that walks '..' through a symlink",
		map[string]any{"kind": "snapshot_via_symlinked_dir",
			"files": map[string]any{"keep.txt": "k"}},
		func(t *testing.T, c acCase, base string) any {
			outside := base + "-svsd"
			if err := os.MkdirAll(filepath.Join(outside, "proj"), 0o777); err != nil {
				t.Fatal(err)
			}
			for p, body := range map[string]string{
				"a.txt": "a", "proj/deep.txt": "d"} {
				if err := os.WriteFile(filepath.Join(outside, p),
					[]byte(body), 0o666); err != nil {
					t.Fatal(err)
				}
			}
			link := filepath.Join(base, "slink")
			_ = os.Remove(link)
			if err := os.Symlink(filepath.Join(outside, "proj"), link); err != nil {
				t.Fatal(err)
			}
			return acSortedKeys(SnapshotDir(pyJoin(link, "..")))
		})

	// --- decode_replace: one U+FFFD per MAXIMAL SUBPART, not per byte -----
	//
	// The four cases a casual check reaches for -- a lone lead byte, a
	// surrogate encoding, an overlong NUL, and two invalid bytes -- agree
	// under either rule, which is exactly why the per-byte spelling
	// survived. The rows that separate them are the TRUNCATED ones.
	//
	// The second property here is subtler and the first draft of this
	// corpus could not see it: after the SECOND byte the acceptable
	// continuation range widens back to 80-BF, and only a FOUR-byte lead
	// has a third byte to widen for. The battery's F4d mutant (drop the
	// reset) survived the truncated-emoji row because F0 9F 98 happens to
	// be all >= 0x90 -- the narrowed F0 range accepts it by luck. The rows
	// that separate the two spellings need a continuation byte the LEAD's
	// own range would have rejected: 0x80-0x8F after F0, or 0x90-0xBF
	// after F4. A near-miss row is not coverage (L9).
	{
		seqs := [][]int{
			{0x61, 0xE2, 0x82, 0x62}, // a + truncated 3-byte + b: ONE FFFD
			{0xF0, 0x9F, 0x98},       // truncated 4-byte: ONE FFFD
			{0xE2, 0x82},             // truncated at end of input
			{0xC3},                   // lone lead byte
			{0xED, 0xA0, 0x80},       // surrogate: ill-formed at byte 2
			{0xC0, 0x80},             // overlong
			{0xFF, 0xFE},             // never valid
			{0xF4, 0x90, 0x80, 0x80}, // past U+10FFFF
			{0xE0, 0x80, 0x80},       // overlong 3-byte
			{0x61, 0xE2, 0x82, 0xAC}, // valid euro, for the control
			// The continuation-range reset, both lead bytes that narrow:
			{0xF0, 0x90, 0x80},       // 0x80 is BELOW F0's own 90-BF floor
			{0xF0, 0x90, 0x80, 0x41}, // ...and again with a trailing ASCII
			{0xF4, 0x80, 0xA0, 0x41}, // 0xA0 is ABOVE F4's own 80-8F ceiling
			{0xF0, 0x90, 0x8F, 0x41}, // the boundary byte, just under 0x90
		}
		vals := make([]any, 0, len(seqs))
		for _, sq := range seqs {
			row := make([]any, 0, len(sq))
			for _, b := range sq {
				row = append(row, b)
			}
			vals = append(vals, row)
		}
		// U1, not D1: there is already a D1 downstream (the layer-ordering
		// fabrication row), and two fixtures sharing a prefix make every
		// "caught by D1" line in a battery log ambiguous about which one.
		add("U1 errors=replace substitutes maximal subparts",
			map[string]any{"kind": "decode_replace", "values": vals},
			func(t *testing.T, c acCase, base string) any {
				var out []any
				for _, sq := range seqs {
					b := make([]byte, len(sq))
					for i, v := range sq {
						b[i] = byte(v)
					}
					d := decodeReplace(b)
					out = append(out, []any{d, len([]rune(d))})
				}
				return out
			})
	}

	// --- the naive .timestamp() branch across a DST boundary --------------
	//
	// Discovered, not hardcoded: the transitions come from the live zone by
	// bisection, so this fixture is a test of whatever TZ the box has and
	// says so out loud when that zone has no transitions at all. A UTC box
	// would otherwise pass it vacuously, which is the exact shape L1 warns
	// about.
	dstStamps, dstReal := acDSTStamps()
	dstName := "W20 naive local stamps around both DST transitions"
	if !dstReal {
		// L44: a fixture's name is a coverage claim. On a zone with no
		// transitions this row still exercises the naive branch and proves
		// nothing at all about DST, and the name has to say so.
		dstName = "W20 naive local stamps (INERT HERE: this zone has no DST transitions)"
	}
	{
		stamps := dstStamps
		add(dstName,
			map[string]any{"kind": "dst_transitions", "values": stamps},
			func(t *testing.T, c acCase, base string) any {
				var out []any
				for _, v := range acStrs(c, "values") {
					f, ok := parseISO(v)
					if !ok {
						out = append(out, nil)
						continue
					}
					out = append(out, f)
				}
				// Last element, not a sibling key: see the probe.
				zone, _ := time.Now().In(time.Local).Zone()
				return append(out, zone)
			})
	}

	// --- snapshot_dir / changed_since / files_modified_since -------------
	tree := map[string]any{"a.txt": "a", "sub/b.txt": "b", ".git/c.txt": "c",
		"__pycache__/d.txt": "d", "node_modules/e.txt": "e",
		".mypy_cache/f.txt": "f", ".pytest_cache/g.txt": "g",
		"sub/.git/h.txt": "h", ".hidden.txt": "i"}
	snapAnswer := func(t *testing.T, c acCase, base string) any {
		return acSortedKeys(SnapshotDir(base))
	}
	add("W1 the walk skips the cache dirs at every level",
		map[string]any{"kind": "snapshot", "files": tree}, snapAnswer)
	add("W2 an empty root",
		map[string]any{"kind": "snapshot", "files": map[string]any{}}, snapAnswer)

	missingAnswer := func(t *testing.T, c acCase, base string) any {
		return acSortedKeys(SnapshotDir(acStr(c, "root")))
	}
	add("W3 a root that is a FILE, not a directory",
		map[string]any{"kind": "snapshot_missing", "root": "/etc/hostname"}, missingAnswer)
	add("W4 a root that does not exist",
		map[string]any{"kind": "snapshot_missing", "root": "/nonexistent/xyzzy"}, missingAnswer)
	add("W5 an empty-string root",
		map[string]any{"kind": "snapshot_missing", "root": ""}, missingAnswer)
	add("W6 a null root",
		map[string]any{"kind": "snapshot_missing", "root": nil}, missingAnswer)

	changedAnswer := func(t *testing.T, c acCase, base string) any {
		return acSortedSet(ChangedSince(acBefore(c), base))
	}
	add("W7 a new file is changed", map[string]any{"kind": "changed",
		"files":  map[string]any{"a.txt": "a", "b.txt": "b"},
		"before": map[string]float64{"a.txt": 1.0}}, changedAnswer)
	add("W8 an mtime that advanced past the epsilon", map[string]any{"kind": "changed",
		"files": map[string]any{"a.txt": "a"}, "mtimes": map[string]float64{"a.txt": 2000.0},
		"before": map[string]float64{"a.txt": 1999.0}}, changedAnswer)
	add("W9 an mtime advance of exactly the epsilon is NOT a change", map[string]any{"kind": "changed",
		"files": map[string]any{"a.txt": "a"}, "mtimes": map[string]float64{"a.txt": 2000.0},
		"before": map[string]float64{"a.txt": 2000.0 - 1e-4}}, changedAnswer)
	// W9 does not do what its name says, and the mutation battery is what
	// noticed: flipping `>` to `>=` at the epsilon comparison left the whole
	// suite green. `2000.0 - (2000.0 - 1e-4)` is not 1e-4 in float64 —
	// catastrophic cancellation at that magnitude lands just BELOW the
	// epsilon, so W9 tests the under-side of the boundary twice and the
	// boundary itself never.
	//
	// This one lands on it exactly. The mtime is 1e-4 seconds and the prior
	// is 0.0, so the subtraction is the identity and the difference is the
	// float64 nearest 1e-4 — bit for bit the constant it is compared
	// against. `>` says not-changed and `>=` says changed, and CPython
	// decides which is right.
	// W8/W9/W9b all sit at magnitudes 0-2000, where
	// `float64(UnixNano())/1e9` and `Unix() + 1e-9*Nanosecond()` are
	// bit-identical — so none of them can see WHICH formula produces the
	// mtime. These two sit where they differ. W9c is an ordinary
	// present-day timestamp whose nanoseconds make the two formulas one ulp
	// apart, with the prior set so the difference straddles the epsilon;
	// W9d is past 2262, where UnixNano silently wraps int64 and reports a
	// NEGATIVE mtime for a file that exists.
	partsAnswer := func(t *testing.T, c acCase, base string) any {
		raw, _ := c.in["mtime_parts"].(map[string][]int64)
		acSetMtimeParts(t, base, raw)
		return acSortedSet(ChangedSince(acBefore(c), base))
	}
	add("W9c an ordinary present-day mtime, at the ulp where the two float formulas differ",
		map[string]any{"kind": "changed_parts",
			"files":       map[string]any{"a.txt": "a"},
			"mtime_parts": map[string][]int64{"a.txt": {1785053848, 249024353}},
			"before":      map[string]float64{"a.txt": 1785053848.2489243}}, partsAnswer)
	add("W9d an mtime past 2262, where UnixNano wraps and reports a negative time",
		map[string]any{"kind": "changed_parts",
			"files":       map[string]any{"a.txt": "a"},
			"mtime_parts": map[string][]int64{"a.txt": {13569465600, 0}},
			"before":      map[string]float64{"a.txt": 0.0}}, partsAnswer)
	add("W9b an mtime advance of EXACTLY the epsilon, at a magnitude where the subtraction is exact",
		map[string]any{"kind": "changed",
			"files": map[string]any{"a.txt": "a"}, "mtimes": map[string]float64{"a.txt": 1e-4},
			"before": map[string]float64{"a.txt": 0.0}}, changedAnswer)
	add("W10 an mtime that went BACKWARDS is not a change", map[string]any{"kind": "changed",
		"files": map[string]any{"a.txt": "a"}, "mtimes": map[string]float64{"a.txt": 1000.0},
		"before": map[string]float64{"a.txt": 2000.0}}, changedAnswer)
	add("W11 a deleted file is not reported", map[string]any{"kind": "changed",
		"files":  map[string]any{"a.txt": "a"},
		"before": map[string]float64{"a.txt": 1.0, "gone.txt": 1.0}}, changedAnswer)

	modAnswer := func(t *testing.T, c acCase, base string) any {
		limit := 20
		if v, ok := c.in["limit"].(int); ok {
			limit = v
		}
		return FilesModifiedSince(base, acStr(c, "since"), limit)
	}
	add("W12 modified_since takes files at or after the stamp", map[string]any{
		"kind": "modified_since", "files": map[string]any{"a.txt": "a", "b.txt": "b"},
		"mtimes": map[string]float64{"a.txt": 1000.0, "b.txt": 3000.0},
		"since":  "1970-01-01T00:33:20+00:00"}, modAnswer)
	add("W13 modified_since sorts and then limits", map[string]any{
		"kind":   "modified_since",
		"files":  map[string]any{"c.txt": "c", "a.txt": "a", "b.txt": "b"},
		"mtimes": map[string]float64{"a.txt": 3000.0, "b.txt": 3000.0, "c.txt": 3000.0},
		"since":  "1970-01-01T00:00:01+00:00", "limit": 2}, modAnswer)
	add("W14 an unparseable stamp gives nothing", map[string]any{
		"kind": "modified_since", "files": map[string]any{"a.txt": "a"},
		"since": "not a date"}, modAnswer)
	add("W15 a naive stamp is interpreted as LOCAL time", map[string]any{
		"kind": "modified_since", "files": map[string]any{"a.txt": "a"},
		"mtimes": map[string]float64{"a.txt": 3000.0},
		"since":  "1970-01-01T00:00:01"}, modAnswer)
	add("W16 a zero limit", map[string]any{
		"kind": "modified_since", "files": map[string]any{"a.txt": "a"},
		"mtimes": map[string]float64{"a.txt": 3000.0},
		"since":  "1970-01-01T00:00:01+00:00", "limit": 0}, modAnswer)

	add("W17 a symlinked dir is an entry, not a subtree; a symlinked file is followed",
		map[string]any{"kind": "snapshot_links", "files": map[string]any{"real.txt": "r"}},
		func(t *testing.T, c acCase, base string) any {
			outside := base + "-target"
			if err := os.MkdirAll(outside, 0o777); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(outside, "hidden.txt"), []byte("h"), 0o666); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(outside, filepath.Join(base, "linkdir")); err != nil {
				t.Fatal(err)
			}
			if err := os.Symlink(filepath.Join(outside, "hidden.txt"),
				filepath.Join(base, "linkfile.txt")); err != nil {
				t.Fatal(err)
			}
			return acSortedKeys(SnapshotDir(base))
		})

	// `Path(base) / claim` leaves ".." for the kernel; `filepath.Join`
	// removes it textually first. The two name the same file only when
	// every component of base is a real directory, so the fixture makes
	// base a SYMLINK — which is not an exotic setup: on macOS /tmp and
	// /var are symlinks, so every project dir beneath them has this shape,
	// and the port's answer was a false `missing-artifact` on a file
	// plainly on disk.
	add("A10 a '..' claim resolved through a SYMLINKED project dir",
		map[string]any{"kind": "exists_via_symlinked_dir",
			"files": map[string]any{"keep.txt": "k"}, "claim": "../x.py"},
		func(t *testing.T, c acCase, base string) any {
			outside := base + "-real"
			if err := os.MkdirAll(filepath.Join(outside, "proj"), 0o777); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(outside, "x.py"),
				[]byte("x = 1"), 0o666); err != nil {
				t.Fatal(err)
			}
			link := filepath.Join(base, "link")
			_ = os.Remove(link)
			if err := os.Symlink(filepath.Join(outside, "proj"), link); err != nil {
				t.Fatal(err)
			}
			return existsAnywhere(acStr(c, "claim"), link)
		})

	// --- _exists_anywhere -------------------------------------------------
	existsAnswer := func(t *testing.T, c acCase, base string) any {
		arg := acStr(c, "claim")
		if acBoolDefault(c, "abs", false) {
			arg = filepath.Join(base, arg)
		}
		return existsAnywhere(arg, acDir(c, base))
	}
	add("A1 a relative claim that exists", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": "a.txt"}, existsAnswer)
	add("A2 a relative claim that does not exist", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": "b.txt"}, existsAnswer)
	add("A3 a nested claim matched by BASENAME", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": "deep/nested/a.txt"}, existsAnswer)
	add("A4 an absolute claim that exists", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": "a.txt", "abs": true}, existsAnswer)
	add("A5 an absolute claim with no project dir", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": "a.txt",
		"abs": true, "with_dir": false}, existsAnswer)
	add("A6 a relative claim with no project dir", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": "a.txt", "with_dir": false}, existsAnswer)
	add("A7 a DIRECTORY counts as existing", map[string]any{"kind": "exists",
		"files": map[string]any{"d": nil}, "claim": "d"}, existsAnswer)
	add("A8 an empty claim is the project dir itself", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": ""}, existsAnswer)
	add("A9 a claim with a null byte", map[string]any{"kind": "exists",
		"files": map[string]any{"a.txt": "a"}, "claim": "a\x00.txt"}, existsAnswer)

	// --- check_fabrication, layer 1 only (the seam is nil) ----------------
	fabAnswer := func(t *testing.T, c acCase, base string) any {
		return acObjMap(CheckFabrication(acStr(c, "text"), acDir(c, base), acBefore(c), nil))
	}
	add("C1 a claim with nothing on disk and no diff", map[string]any{"kind": "fabrication",
		"text": "wrote to gone.txt", "files": map[string]any{}}, fabAnswer)
	add("C2 a claim that exists on disk", map[string]any{"kind": "fabrication",
		"text": "wrote to a.txt", "files": map[string]any{"a.txt": "a"}}, fabAnswer)
	add("C3 a claim missing but the workspace changed", map[string]any{"kind": "fabrication",
		"text": "wrote to gone.txt", "files": map[string]any{"other.txt": "o"}}, fabAnswer)
	add("C4 no claims at all", map[string]any{"kind": "fabrication",
		"text": "I thought about it", "files": map[string]any{}}, fabAnswer)
	add("C5 two claims, only one missing", map[string]any{"kind": "fabrication",
		"text":  "wrote to a.txt and saved to gone.txt",
		"files": map[string]any{"a.txt": "a"}}, fabAnswer)
	add("C6 two claims, both missing, empty diff", map[string]any{"kind": "fabrication",
		"text": "wrote to x.txt and saved to y.txt", "files": map[string]any{}}, fabAnswer)
	add("C7 a null project dir", map[string]any{"kind": "fabrication",
		"text": "wrote to a.txt", "files": map[string]any{}, "with_dir": false}, fabAnswer)
	add("C8 an unchanged workspace whose files were already in the snapshot",
		map[string]any{"kind": "fabrication", "text": "wrote to gone.txt",
			"files": map[string]any{"a.txt": "a"}, "mtimes": map[string]float64{"a.txt": 1000.0},
			"before": map[string]float64{"a.txt": 1000.0}}, fabAnswer)

	// --- _python_candidates -----------------------------------------------
	candAnswer := func(t *testing.T, c acCase, base string) any {
		changed := map[string]bool{}
		for _, r := range acStrs(c, "changed") {
			changed[r] = true
		}
		var out []string
		for _, p := range pythonCandidates(acStrs(c, "claims"), acDir(c, base), changed) {
			rel, err := filepath.Rel(base, p)
			if err != nil {
				rel = p
			}
			out = append(out, rel)
		}
		if out == nil {
			out = []string{}
		}
		return out
	}
	add("P1 a claimed .py that exists", map[string]any{"kind": "candidates",
		"claims": []string{"a.py"}, "files": map[string]any{"a.py": "x = 1"}}, candAnswer)
	add("P2 a claimed .py that does NOT exist", map[string]any{"kind": "candidates",
		"claims": []string{"gone.py"}, "files": map[string]any{}}, candAnswer)
	add("P3 a claim that is not a .py is skipped", map[string]any{"kind": "candidates",
		"claims": []string{"a.txt"}, "files": map[string]any{"a.txt": "t"}}, candAnswer)
	add("P4 a changed .py with no claim", map[string]any{"kind": "candidates",
		"claims": []string{}, "changed": []string{"b.py"},
		"files": map[string]any{"b.py": "y = 2"}}, candAnswer)
	add("P5 claims come BEFORE changed files", map[string]any{"kind": "candidates",
		"claims": []string{"a.py"}, "changed": []string{"b.py"},
		"files": map[string]any{"a.py": "x = 1", "b.py": "y = 2"}}, candAnswer)
	add("P6 the same file claimed AND changed appears once", map[string]any{"kind": "candidates",
		"claims": []string{"a.py"}, "changed": []string{"a.py"},
		"files": map[string]any{"a.py": "x = 1"}}, candAnswer)
	add("P7 a changed path that is not a .py is skipped", map[string]any{"kind": "candidates",
		"claims": []string{}, "changed": []string{"b.txt"},
		"files": map[string]any{"b.txt": "t"}}, candAnswer)
	add("P8 a DIRECTORY named like a .py is not a candidate", map[string]any{"kind": "candidates",
		"claims": []string{"d.py"}, "files": map[string]any{"d.py": nil}}, candAnswer)
	// P8 covers the DIRECTORY half of `p.is_file()`. `is_file()` is S_ISREG,
	// so it also excludes sockets, FIFOs and device nodes — and the port's
	// "exists and is not a directory" admitted every one of them. The cost
	// is not cosmetic: an admitted socket makes os.ReadFile fail, which
	// takes inertOutputVerdict's fail-open exit and DROPS a real
	// inert-output verdict the other files had earned.
	add("P8b a SOCKET named like a .py is not a candidate either",
		map[string]any{"kind": "candidates_nonregular",
			"files": map[string]any{"a.py": "x = 1"}, "sock": "s.py",
			"claims": []string{"a.py", "s.py"}},
		func(t *testing.T, c acCase, base string) any {
			// Bound in a SHORT directory and symlinked into place: a unix
			// socket path is capped near 108 bytes and a temp dir named
			// after this fixture is already past that. os.Stat follows the
			// link and still reports a socket, which is the property.
			short, err := os.MkdirTemp("", "s")
			if err != nil {
				t.Fatal(err)
			}
			defer os.RemoveAll(short)
			target := filepath.Join(short, "s")
			l, err := net.Listen("unix", target)
			if err != nil {
				t.Fatal(err)
			}
			defer l.Close()
			if err := os.Symlink(target, filepath.Join(base, acStr(c, "sock"))); err != nil {
				t.Fatal(err)
			}
			changed := map[string]bool{}
			var out []string
			for _, p := range pythonCandidates(acStrs(c, "claims"), base, changed) {
				rel, rerr := filepath.Rel(base, p)
				if rerr != nil {
					rel = p
				}
				out = append(out, rel)
			}
			if out == nil {
				out = []string{}
			}
			return out
		})
	// The first loop's join is pinned by A10; the SECOND one -- the changed
	// -path loop -- was not, and reverting it to filepath.Join survived the
	// whole suite. Walk-derived keys never carry "..", so the divergence has
	// to come from the project DIR, which is not exotic: "<dir>/link/.." is
	// what a caller composing paths by hand produces, and on macOS /tmp and
	// /var are themselves symlinks.
	add("P8c a CHANGED path joined onto a project dir that walks '..' through a symlink",
		map[string]any{"kind": "candidates_via_symlinked_dir",
			"files":  map[string]any{"keep.txt": "k"},
			"claims": []string{}, "changed": []string{"a.py"}},
		func(t *testing.T, c acCase, base string) any {
			outside := base + "-cvsd"
			if err := os.MkdirAll(filepath.Join(outside, "proj"), 0o777); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(outside, "a.py"),
				[]byte("x = 1"), 0o666); err != nil {
				t.Fatal(err)
			}
			link := filepath.Join(base, "clink")
			_ = os.Remove(link)
			if err := os.Symlink(filepath.Join(outside, "proj"), link); err != nil {
				t.Fatal(err)
			}
			proj := pyJoin(link, "..")
			changed := map[string]bool{}
			for _, r := range acStrs(c, "changed") {
				changed[r] = true
			}
			out := []string{}
			for _, p := range pythonCandidates(acStrs(c, "claims"), proj, changed) {
				rel, rerr := filepath.Rel(proj, p)
				if rerr != nil {
					rel = p
				}
				out = append(out, rel)
			}
			return out
		})
	add("P9 no project dir drops every relative claim", map[string]any{"kind": "candidates",
		"claims": []string{"a.py"}, "files": map[string]any{"a.py": "x = 1"},
		"with_dir": false}, candAnswer)
	add("P10 a nested changed path keeps its directory", map[string]any{"kind": "candidates",
		"claims": []string{}, "changed": []string{"sub/c.py"},
		"files": map[string]any{"sub/c.py": "z = 3"}}, candAnswer)
	add("P11 a claim naming a nested path is NOT basename-matched here",
		map[string]any{"kind": "candidates", "claims": []string{"c.py"},
			"files": map[string]any{"sub/c.py": "z = 3"}}, candAnswer)

	// --- _inert_output_verdict, the predicate scripted --------------------
	inertAnswer := func(t *testing.T, c acCase, base string) any {
		acAssertDistinctScriptedContent(t, c)
		script, _ := c.in["inert"].(map[string]any)
		changed := map[string]bool{}
		for _, r := range acStrs(c, "changed") {
			changed[r] = true
		}
		v, ok := inertOutputVerdict(acStr(c, "text"), acDir(c, base),
			acStrs(c, "claims"), changed, acScriptedInert(t, base, script))
		if !ok {
			return nil
		}
		return acObjMap(v)
	}
	inertFiles := map[string]any{"a.py": "def f():\n    pass\n"}
	add("I1 an inert candidate under a concrete stdout claim is fabrication",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"}, "files": inertFiles,
			"text": "prints 1, 2, Fizz", "inert": map[string]any{"a.py": true}}, inertAnswer)
	add("I2 a NON-inert candidate is no opinion",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"}, "files": inertFiles,
			"text": "prints 1, 2, Fizz", "inert": map[string]any{"a.py": false}}, inertAnswer)
	add("I3 an UNPARSEABLE candidate is no opinion",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"}, "files": inertFiles,
			"text": "prints 1, 2, Fizz", "inert": map[string]any{"a.py": nil}}, inertAnswer)
	add("I4 no stdout verb, so the gate never opens",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"}, "files": inertFiles,
			"text": "the function returns 1, 2, Fizz", "inert": map[string]any{"a.py": true}}, inertAnswer)
	add("I5 a stdout verb with nothing concrete does not open it either",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"}, "files": inertFiles,
			"text": "it prints the sequence", "inert": map[string]any{"a.py": true}}, inertAnswer)
	add("I6 no candidates at all",
		map[string]any{"kind": "inert_verdict", "claims": []string{"gone.py"},
			"files": map[string]any{}, "text": "prints 1, 2, Fizz",
			"inert": map[string]any{}}, inertAnswer)
	add("I7 two candidates, one non-inert, kills the verdict",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"},
			"changed": []string{"b.py"},
			"files":   map[string]any{"a.py": "def f():\n    pass\n", "b.py": "print(1)\n"},
			"text":    "prints 1, 2, Fizz",
			"inert":   map[string]any{"a.py": true, "b.py": false}}, inertAnswer)
	add("I8 two inert candidates name BOTH files in the reason",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"},
			"changed": []string{"sub/b.py"},
			"files": map[string]any{"a.py": "def f():\n    pass\n",
				"sub/b.py": "def g():\n    pass\n"},
			"text":  "prints 1, 2, Fizz",
			"inert": map[string]any{"a.py": true, "sub/b.py": true}}, inertAnswer)
	add("I9 the second candidate being unparseable kills it even after an inert one",
		map[string]any{"kind": "inert_verdict", "claims": []string{"a.py"},
			"changed": []string{"b.py"},
			"files":   map[string]any{"a.py": "def f():\n    pass\n", "b.py": "def g("},
			"text":    "prints 1, 2, Fizz",
			"inert":   map[string]any{"a.py": true, "b.py": nil}}, inertAnswer)

	// --- check_fabrication with layer 2 reachable ------------------------
	fab2Answer := func(t *testing.T, c acCase, base string) any {
		acAssertDistinctScriptedContent(t, c)
		script, _ := c.in["inert"].(map[string]any)
		return acObjMap(CheckFabrication(acStr(c, "text"), acDir(c, base), acBefore(c),
			acScriptedInert(t, base, script)))
	}
	add("D1 layer 1 fires FIRST even when layer 2 would also fire",
		map[string]any{"kind": "fabrication2", "text": "wrote to gone.py and it prints 1, 2, 3",
			"claims": []string{}, "files": map[string]any{},
			"inert": map[string]any{}}, fab2Answer)
	add("D2 the file exists, so layer 1 passes and layer 2 fires",
		map[string]any{"kind": "fabrication2", "text": "wrote to a.py; it prints 1, 2, Fizz",
			"files": inertFiles, "inert": map[string]any{"a.py": true}}, fab2Answer)
	add("D3 no fabrication signal at all",
		map[string]any{"kind": "fabrication2", "text": "wrote to a.py",
			"files": map[string]any{"a.py": "x = 1"},
			"inert": map[string]any{"a.py": true}}, fab2Answer)

	// --- the reason strings embed repr of a LIST OF STR -------------------
	add("R1 an apostrophe truncates the claim before repr ever sees it",
		map[string]any{"kind": "fabrication", "text": "wrote to it's.txt",
			"files": map[string]any{}}, fabAnswer)
	add("R5 a real filename with an apostrophe DOES reach repr's double quotes",
		map[string]any{"kind": "inert_verdict", "claims": []string{},
			"changed": []string{"it's.py"},
			"files":   map[string]any{"it's.py": "def f():\n    pass\n"},
			"text":    "prints 1, 2, Fizz",
			"inert":   map[string]any{"it's.py": true}}, inertAnswer)
	add("R2 a non-ascii claimed name stays raw in repr",
		map[string]any{"kind": "fabrication", "text": "wrote to café.txt",
			"files": map[string]any{}}, fabAnswer)
	add("R3 a backslash in a claimed name is escaped",
		map[string]any{"kind": "fabrication", "text": `wrote to a\b.txt`,
			"files": map[string]any{}}, fabAnswer)
	add("R4 three missing claims render with repr's comma-space separator",
		map[string]any{"kind": "fabrication",
			"text":  "wrote to a.txt, saved to b.txt, dumped to c.txt",
			"files": map[string]any{}}, fabAnswer)

	return cs
}

// --- the differential -----------------------------------------------------

func TestTheArtifactCheckAgreesWithCPythonOnEveryFixture(t *testing.T) {
	cases := acCases()

	ws := t.TempDir()
	probe := pyprobe.Probe{Marker: "artifact_check.py", Workspace: ws}

	payload := make([]map[string]any, 0, len(cases))
	for _, c := range cases {
		payload = append(payload, c.in)
	}

	var got []map[string]any
	probe.RunJSON(t, acPySrc, &got, pyprobe.Arg(t, payload))

	if len(got) != len(cases) {
		t.Fatalf("the probe answered %d cases for %d fixtures — the two lists "+
			"are positionally aligned, so a length mismatch means some "+
			"fixture's answer belongs to a different fixture", len(got), len(cases))
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			res := got[i]
			if e, bad := res["err"]; bad {
				t.Fatalf("CPython could not answer this fixture: %v\n"+
					"An unanswered case is not a passing case — it is a row "+
					"the comparison skipped.", e)
			}
			want, ok := res["ok"]
			if !ok {
				t.Fatalf("the probe returned neither ok nor err: %v", res)
			}

			base := ""
			if _, needsTree := c.in["files"]; needsTree {
				base = filepath.Join(t.TempDir(), "acwork")
				acMktree(t, base, acFiles(c), acMtimes(c))
			}

			have := acGeneric(t, c.answer(t, c, base))
			if !reflect.DeepEqual(have, want) {
				t.Errorf("port and CPython disagree\n  input:   %s\n"+
					"  CPython: %#v\n  port:    %#v", acJSON(t, c.in), want, have)
			}
		})
	}
}

func acJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		return "<unmarshalable>"
	}
	return string(b)
}

// TestTheVerdictRendersItsFieldsInTheDataclassOrder is the assertion the
// value comparison above cannot make.
//
// Every verdict fixture goes through encoding/json on the way to
// reflect.DeepEqual, and a map has no order there — so ToDict could emit
// its seven fields in any sequence at all and 30-odd fixtures would stay
// green. The order is observable wherever a verdict is serialised, and the
// source of truth for it is the dataclass's own field list, not the
// probe's hand-written vdict() helper, which is a transcription that could
// itself be out of order.
func TestTheVerdictRendersItsFieldsInTheDataclassOrder(t *testing.T) {
	// The probe resolves ROOT from MARO_WORKSPACE at module level, before it
	// looks at a single case, so even a case that touches no tree needs a
	// workspace. A read-only-looking probe is not necessarily one.
	probe := pyprobe.Probe{Marker: "artifact_check.py", Workspace: t.TempDir()}
	var got []map[string]any
	probe.RunJSON(t, acPySrc, &got,
		pyprobe.Arg(t, []map[string]any{{"kind": "field_order"}}))
	if len(got) != 1 {
		t.Fatalf("expected one answer, got %d", len(got))
	}
	if e, bad := got[0]["err"]; bad {
		t.Fatalf("CPython could not report the field order: %v", e)
	}
	raw, _ := got[0]["ok"].([]any)
	want := make([]string, 0, len(raw))
	for _, r := range raw {
		want = append(want, r.(string))
	}
	if len(want) == 0 {
		t.Fatal("CPython reported no fields for ArtifactVerdict — an empty " +
			"expectation is not an assertion")
	}

	var have []string
	for _, f := range newVerdict().ToDict() {
		have = append(have, f.Key)
	}
	if !reflect.DeepEqual(have, want) {
		t.Errorf("ToDict's key order is not the dataclass's field order\n"+
			"  dataclass: %v\n  ToDict:    %v", want, have)
	}
}

// TestTheFreshCandidateOrderIsThePortsOwnGuaranteeNotCPythons pins a
// property no differential can, and says which kind of property it is.
//
// `_python_candidates` walks `changed`, which is a Python SET, so the order
// two fresh .py files contribute in is hash order — not a function of the
// input, and different between runs under PYTHONHASHSEED. The order is
// nonetheless OBSERVABLE: it reaches the `names` list in the inert-output
// reason string, which an operator reads. So CPython has no answer to
// compare against and the port still has to have one.
//
// The port sorts. That is a decision, not a port of anything, and it is
// recorded as such in pythonCandidates' doc. What this test asserts is only
// that the decision holds — that the order is stable and sorted across
// repeated calls — because a Go map range would give a different answer
// every run and no fixture above would notice: they all have at most one
// fresh candidate.
//
// The mutation battery is what found that hole: commenting out the sort
// left all 192 fixtures green.
func TestTheFreshCandidateOrderIsThePortsOwnGuaranteeNotCPythons(t *testing.T) {
	base := t.TempDir()
	// Names chosen so sorted order and creation order disagree, or the
	// assertion would hold for an unsorted implementation too.
	for _, n := range []string{"zulu.py", "alpha.py", "mike.py"} {
		if err := os.WriteFile(filepath.Join(base, n), []byte("x = 1\n"), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	changed := map[string]bool{"zulu.py": true, "alpha.py": true, "mike.py": true}
	want := []string{"alpha.py", "mike.py", "zulu.py"}

	// Repeated, because a Go map's range order is randomised PER RANGE:
	// one call agreeing with sorted order proves nothing.
	for i := 0; i < 64; i++ {
		var got []string
		for _, p := range pythonCandidates(nil, base, changed) {
			got = append(got, filepath.Base(p))
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("call %d returned %v, want %v — the fresh-candidate "+
				"order is not stable, so the inert-output reason string "+
				"names the same files in a different order between runs",
				i, got, want)
		}
	}
}

// acLocalTransitions finds every UTC instant in `year` at which time.Local
// changes offset, by hourly scan and then bisection to the second. It is
// DISCOVERY rather than a table because a hardcoded transition date stops
// being a transition when a zone's rules change, and the fixture would go
// on passing while testing an ordinary Sunday.
func acLocalTransitions(year int) []time.Time {
	start := time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC)
	end := time.Date(year+1, 1, 1, 0, 0, 0, 0, time.UTC)
	var out []time.Time
	_, prevOff := start.In(time.Local).Zone()
	prev := start
	for t := start.Add(time.Hour); t.Before(end); t = t.Add(time.Hour) {
		_, off := t.In(time.Local).Zone()
		if off != prevOff {
			lo, hi := prev, t
			for hi.Sub(lo) > time.Second {
				mid := lo.Add(hi.Sub(lo) / 2)
				if _, m := mid.In(time.Local).Zone(); m == prevOff {
					lo = mid
				} else {
					hi = mid
				}
			}
			out = append(out, hi)
			prevOff = off
		}
		prev = t
	}
	return out
}

// acDSTStamps returns naive ISO stamps stepping across every transition in
// the local zone -- the gap on one and the repeat hour on the other, without
// needing to know which is which. The bool says whether any transition was
// actually found; on a zone with none, the stamps are ordinary times and the
// fixture that uses them must not claim otherwise.
func acDSTStamps() ([]string, bool) {
	trs := acLocalTransitions(2026)
	if len(trs) == 0 {
		return []string{"2026-03-08T02:30:00", "2026-11-01T01:30:00"}, false
	}
	var out []string
	for _, tr := range trs {
		base := tr.Add(-time.Second).In(time.Local)
		// Carried in UTC so Add() steps wall-clock minutes and does not
		// re-apply the very offset change under test.
		w := time.Date(base.Year(), base.Month(), base.Day(), base.Hour(),
			0, 0, 0, time.UTC)
		for m := -30; m <= 90; m += 15 {
			out = append(out, w.Add(time.Duration(m)*time.Minute).
				Format("2006-01-02T15:04:05"))
		}
	}
	return out, true
}
