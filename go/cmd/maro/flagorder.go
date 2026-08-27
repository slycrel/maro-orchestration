package main

import (
	"flag"
	"fmt"
	"strings"
)

// UnlimitedPositionals is `nargs="*"`: the command takes as many
// positionals as it is given. Anything else is argparse's default, where a
// subparser declares exactly the positionals it wants and every extra one
// is an error.
const UnlimitedPositionals = -1

// parseArgs parses args the way argparse does, in the two respects where
// Go's `flag` package differs and the difference reaches a shared store.
//
// # 1. A flag is a flag no matter where it appears
//
// Go's `flag` stops parsing at the first non-flag argument, so
// `maro task fail <id> --error "boom"` puts `--error` and `boom` in
// `fs.Args()` and leaves the flag at its zero value. Python's argparse
// interleaves, so the same command line records the error message. The two
// runtimes then write DIFFERENT rows to a store they share — measured by
// the write-path comparison harness on its first run:
//
//	python  task fail <id> --error "boom"  ->  "error": "boom"
//	go      task fail <id> --error "boom"  ->  (no error key at all)
//	go      task fail --error "boom" <id>  ->  "error": "boom"
//
// A CLI that silently drops an argument depending on where it was written
// is worse than one that rejects it, because the failure is invisible until
// something reads the row back.
//
// # 2. An extra positional is an ERROR, not something to ignore
//
// argparse knows how many positionals each subcommand declares
// (task_store.py: `fail` has exactly `job_id`), and refuses the rest. The
// first cut of this helper collected positionals without limit and the
// caller read only the first, which re-opened the same hole one layer up
// (adversarial r10, MEDIUM):
//
//	python  task fail <id> -- --error boom  ->  exit 2, task still queued
//	go      task fail <id> -- --error boom  ->  task FAILED, error dropped
//
// The `--` is what makes this reachable: `flag.Parse` consumes it and
// stops, and the loop's next iteration parsed `--error boom` as flags
// again. So `--` is handled HERE — split off before any parsing — and
// everything after it is a positional verbatim, which is what argparse
// does. A second `--` is itself a positional, also argparse's behaviour.
//
// # What this deliberately does NOT reproduce
//
// Two argparse/flag differences are left alone, measured and filed in
// BACKLOG rather than fixed, because they are surface-shape differences
// like `maro task` vs `python3 -m task_store` and not silently dropped
// arguments:
//
//	fail <id> --err boom     python: accepted (argparse abbreviates
//	                                  unambiguous long options)
//	                         go:     rejected, "flag provided but not defined"
//	fail <id> -error boom    python: exit 2, unrecognized arguments
//	                         go:     accepted — Go's own flag idiom, and
//	                                 the one this CLI's help text uses
//	                                 (`-backend`, `-max-steps`, `-limit`)
//
// Rejecting the single-dash spelling would break the port's own documented
// invocation; adding abbreviation would only widen what Go accepts. Both
// were measured on 2026-08-26, both are recorded, neither changes what is
// written when the argv is one both CLIs accept.
func parseArgs(fs *flag.FlagSet, args []string, maxPositional int) ([]string, error) {
	var afterSep []string
	for i, a := range args {
		if a == "--" {
			afterSep = append(afterSep, args[i+1:]...)
			args = args[:i]
			break
		}
	}

	var positional []string
	rest := args
	for {
		if err := fs.Parse(rest); err != nil {
			return nil, err
		}
		if fs.NArg() == 0 {
			break
		}
		positional = append(positional, fs.Arg(0))
		rest = fs.Args()[1:]
	}
	positional = append(positional, afterSep...)

	if maxPositional != UnlimitedPositionals && len(positional) > maxPositional {
		// argparse's own wording, so a script that greps stderr sees the
		// same sentence from either runtime.
		return nil, fmt.Errorf("unrecognized arguments: %s",
			strings.Join(positional[maxPositional:], " "))
	}
	return positional, nil
}

// refuseExtra is parseArgs for a subcommand that declares neither flags nor
// positionals (`maro task status`, `maro task recover`). Those two never
// built a FlagSet, so they ignored trailing argv entirely where argparse
// exits 2 — a `maro task status queued` that looked like a filter and was
// silently a full listing.
func refuseExtra(args []string) error {
	if len(args) == 0 {
		return nil
	}
	return fmt.Errorf("unrecognized arguments: %s", strings.Join(args, " "))
}
