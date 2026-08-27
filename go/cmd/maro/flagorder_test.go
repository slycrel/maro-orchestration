package main

import (
	"flag"
	"strings"
	"testing"
)

// The Python side of this is argparse, which interleaves. The whole reason
// parseArgs exists is that `flag` does not, and the divergence is SILENT:
// `maro task fail <id> --error "boom"` wrote a row with no error key where
// `python3 -m task_store fail <id> --error "boom"` wrote one with it. Found
// by the write-path comparison harness on its first run, against a store the
// two runtimes share.
func TestParseArgsTakesFlagsOnEitherSideOfAPositional(t *testing.T) {
	for _, c := range []struct {
		name     string
		argv     []string
		wantErr  string
		wantPos  []string
		wantFlag bool
	}{
		{"flag before the positional",
			[]string{"--error", "boom", "job-1"}, "boom", []string{"job-1"}, true},
		{"flag AFTER the positional — the case that regressed",
			[]string{"job-1", "--error", "boom"}, "boom", []string{"job-1"}, true},
		{"flag on both sides of two positionals",
			[]string{"--verbose", "a", "--error", "boom", "b"},
			"boom", []string{"a", "b"}, true},
		{"no flags at all",
			[]string{"a", "b"}, "", []string{"a", "b"}, false},
		{"nothing at all",
			nil, "", nil, false},
		// `--` is the separator on both runtimes; argparse answers a
		// dash-leading positional the same way.
		{"a dash-leading positional behind the separator",
			[]string{"--error", "boom", "--", "-weird"},
			"boom", []string{"-weird"}, true},
		// Everything after the FIRST `--` is a positional verbatim, so a
		// flag spelling behind it is text — this is the shape that used to
		// be re-parsed as flags, and with an arity it becomes the error
		// below rather than a silently dropped argument.
		{"a flag spelling behind the separator is text",
			[]string{"job-1", "--", "--error", "boom"},
			"", []string{"job-1", "--error", "boom"}, false},
		{"a second separator is itself a positional",
			[]string{"--", "a", "--", "b"},
			"", []string{"a", "--", "b"}, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			fs := flag.NewFlagSet("t", flag.ContinueOnError)
			fs.SetOutput(new(strings.Builder))
			errText := fs.String("error", "", "")
			verbose := fs.Bool("verbose", false, "")
			pos, err := parseArgs(fs, c.argv, UnlimitedPositionals)
			if err != nil {
				t.Fatalf("parse: %v", err)
			}
			if *errText != c.wantErr {
				t.Errorf("-error = %q, want %q — a flag written after the "+
					"positional was dropped", *errText, c.wantErr)
			}
			if strings.Join(pos, "\x00") != strings.Join(c.wantPos, "\x00") {
				t.Errorf("positionals %q, want %q", pos, c.wantPos)
			}
			if *verbose != (c.name == "flag on both sides of two positionals") {
				t.Errorf("-verbose = %v", *verbose)
			}
		})
	}
}

// The arity is the argument, not a sanity check. Every argv below was RUN
// against `python3 -m task_store` on 2026-08-26; the wantErr strings are
// argparse's own sentence, and each case's Python behaviour is quoted.
// Without the limit the CLI accepted all three, read positional[0], and
// wrote a row argparse refused to write.
func TestParseArgsRefusesExtraPositionalsLikeArgparse(t *testing.T) {
	for _, c := range []struct {
		name    string
		argv    []string
		max     int
		wantErr string
	}{
		// python: exit 2, "unrecognized arguments: --error boom", task
		// stays queued. go, before this: exit 0, task marked FAILED with
		// no error text (adversarial r10, MEDIUM).
		{"the separator hole",
			[]string{"job-1", "--", "--error", "boom"}, 1,
			"unrecognized arguments: --error boom"},
		// A plain second job id was always refused by argparse; the port
		// silently acted on the first.
		{"two job ids where one is declared",
			[]string{"job-1", "job-2"}, 1,
			"unrecognized arguments: job-2"},
		{"two job ids with a flag between them",
			[]string{"job-1", "--error", "boom", "job-2"}, 1,
			"unrecognized arguments: job-2"},
		// `enqueue`, `list` declare NO positionals. Plain fs.Parse stopped
		// at the stray word and dropped every flag after it too.
		{"a stray word where none is declared",
			[]string{"--error", "boom", "stray"}, 0,
			"unrecognized arguments: stray"},
		{"a stray word BEFORE the flags", // fs.Parse would drop -error here
			[]string{"stray", "--error", "boom"}, 0,
			"unrecognized arguments: stray"},
	} {
		t.Run(c.name, func(t *testing.T) {
			fs := flag.NewFlagSet("t", flag.ContinueOnError)
			fs.SetOutput(new(strings.Builder))
			fs.String("error", "", "")
			pos, err := parseArgs(fs, c.argv, c.max)
			if err == nil {
				t.Fatalf("parsed cleanly, returning %q — argparse exits 2 with "+
					"%q and writes nothing", pos, c.wantErr)
			}
			if err.Error() != c.wantErr {
				t.Errorf("error = %q, want %q", err, c.wantErr)
			}
		})
	}
}

// The boundary in the other direction: the declared count is ACCEPTED, and
// `adopt`'s genuine nargs="*" stays unlimited. A guard that refused the
// legal shape would be caught here rather than by a user.
func TestParseArgsAcceptsTheDeclaredCount(t *testing.T) {
	for _, c := range []struct {
		name string
		argv []string
		max  int
		want []string
	}{
		{"exactly one where one is declared",
			[]string{"job-1", "--error", "boom"}, 1, []string{"job-1"}},
		{"none where one is declared", // arity is a MAXIMUM; the caller
			[]string{"--error", "boom"}, 1, nil}, // still checks for zero
		{"none where none is declared",
			[]string{"--error", "boom"}, 0, nil},
		{"many where the count is unlimited",
			[]string{"a", "--error", "boom", "b", "c"},
			UnlimitedPositionals, []string{"a", "b", "c"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			fs := flag.NewFlagSet("t", flag.ContinueOnError)
			fs.SetOutput(new(strings.Builder))
			errText := fs.String("error", "", "")
			pos, err := parseArgs(fs, c.argv, c.max)
			if err != nil {
				t.Fatalf("parse: %v — this is the shape argparse ACCEPTS", err)
			}
			if strings.Join(pos, "\x00") != strings.Join(c.want, "\x00") {
				t.Errorf("positionals %q, want %q", pos, c.want)
			}
			if *errText != "boom" {
				t.Errorf("-error = %q, want %q", *errText, "boom")
			}
		})
	}
}

// An unknown flag must still be an ERROR, wherever it appears. The loop
// re-enters Parse, and a version that swallowed the error to keep going
// would turn every typo into a silently ignored argument — a worse bug
// than the one this helper fixes.
func TestParseArgsStillRejectsAnUnknownFlag(t *testing.T) {
	for _, argv := range [][]string{
		{"--nope"},
		{"job-1", "--nope"},
		{"--error", "boom", "job-1", "--nope"},
	} {
		fs := flag.NewFlagSet("t", flag.ContinueOnError)
		fs.SetOutput(new(strings.Builder))
		fs.String("error", "", "")
		if _, err := parseArgs(fs, argv, UnlimitedPositionals); err == nil {
			t.Errorf("%q: an unknown flag parsed cleanly", argv)
		}
	}
}

// `maro task status` / `recover` never built a FlagSet, so trailing argv
// went nowhere. `maro task status queued` looked like a filter and was
// silently a full listing.
func TestRefuseExtraMatchesArgparseOnAFlaglessSubcommand(t *testing.T) {
	if err := refuseExtra(nil); err != nil {
		t.Errorf("no arguments: %v", err)
	}
	if err := refuseExtra([]string{}); err != nil {
		t.Errorf("empty arguments: %v", err)
	}
	for _, c := range []struct {
		argv []string
		want string
	}{
		{[]string{"queued"}, "unrecognized arguments: queued"},
		{[]string{"--status", "queued"}, "unrecognized arguments: --status queued"},
	} {
		err := refuseExtra(c.argv)
		if err == nil {
			t.Fatalf("%q parsed cleanly; argparse exits 2", c.argv)
		}
		if err.Error() != c.want {
			t.Errorf("error = %q, want %q", err, c.want)
		}
	}
}

// `task_store.py` builds FOUR subparsers for claim/complete/fail/archive and
// only `p_fail` calls `add_argument("--error")`. The Go handles the four in
// one case arm, which is fine — until the shared `FlagSet` shares the
// ARGUMENT SURFACE too, and `task complete <id> --error boom` completes the
// task where argparse exits 2 (adversarial r11, MEDIUM).
//
// Derived from task_store.py, not from the diff (L9): the table below is the
// Python's four subparser specs, so a future verb added to the case arm
// without a matching subparser fails here.
func TestOnlyFailDeclaresTheErrorFlag(t *testing.T) {
	for _, c := range []struct {
		verb      string
		declaresE bool
	}{
		{"claim", false},
		{"complete", false},
		{"fail", true},
		{"archive", false},
	} {
		t.Run(c.verb, func(t *testing.T) {
			// Rebuilt exactly as runTask builds it, because the thing under
			// test is WHICH flags the verb registers.
			fs := flag.NewFlagSet(c.verb, flag.ContinueOnError)
			fs.SetOutput(new(strings.Builder))
			if c.verb == "fail" {
				fs.String("error", "", "failure reason")
			}
			_, err := parseArgs(fs, []string{"job-1", "--error", "boom"}, 1)
			if c.declaresE && err != nil {
				t.Fatalf("%s refused -error: %v — task_store.py's p_fail "+
					"declares it", c.verb, err)
			}
			if !c.declaresE && err == nil {
				t.Errorf("%s accepted -error, but task_store.py's p_%s "+
					"declares only job_id, so argparse exits 2 on it",
					c.verb, c.verb)
			}
		})
	}
}
