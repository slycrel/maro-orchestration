package main

import (
	"flag"
	"strings"
	"testing"
)

// The Python side of this is argparse, which interleaves. The whole reason
// parseInterleaved exists is that `flag` does not, and the divergence is
// SILENT: `maro task fail <id> --error "boom"` wrote a row with no error
// key where `python3 -m task_store fail <id> --error "boom"` wrote one with
// it. Found by the write-path comparison harness on its first run, against
// a store the two runtimes share.
func TestParseInterleavedTakesFlagsOnEitherSideOfAPositional(t *testing.T) {
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
	} {
		t.Run(c.name, func(t *testing.T) {
			fs := flag.NewFlagSet("t", flag.ContinueOnError)
			fs.SetOutput(new(strings.Builder))
			errText := fs.String("error", "", "")
			verbose := fs.Bool("verbose", false, "")
			pos, err := parseInterleaved(fs, c.argv)
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

// An unknown flag must still be an ERROR, wherever it appears. The loop
// re-enters Parse, and a version that swallowed the error to keep going
// would turn every typo into a silently ignored argument — a worse bug
// than the one this helper fixes.
func TestParseInterleavedStillRejectsAnUnknownFlag(t *testing.T) {
	for _, argv := range [][]string{
		{"--nope"},
		{"job-1", "--nope"},
		{"--error", "boom", "job-1", "--nope"},
	} {
		fs := flag.NewFlagSet("t", flag.ContinueOnError)
		fs.SetOutput(new(strings.Builder))
		fs.String("error", "", "")
		if _, err := parseInterleaved(fs, argv); err == nil {
			t.Errorf("%q: an unknown flag parsed cleanly", argv)
		}
	}
}
