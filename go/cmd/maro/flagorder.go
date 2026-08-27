package main

import "flag"

// parseInterleaved parses args the way argparse does: a flag is a flag no
// matter where it appears, including AFTER a positional.
//
// Go's `flag` package stops parsing at the first non-flag argument, so
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
// The loop is the standard idiom: parse, take one positional, parse the
// rest, repeat. It returns the positionals in the order they appeared.
//
// What this does NOT do, deliberately: it does not make `--` transparent
// beyond flag's own handling, and it does not turn a leading-dash
// POSITIONAL into a value. argparse has the same problem and answers it the
// same way — with `--`. A goal or directive that genuinely starts with a
// dash needs the separator on both runtimes.
func parseInterleaved(fs *flag.FlagSet, args []string) ([]string, error) {
	var positional []string
	rest := args
	for {
		if err := fs.Parse(rest); err != nil {
			return nil, err
		}
		if fs.NArg() == 0 {
			return positional, nil
		}
		positional = append(positional, fs.Arg(0))
		rest = fs.Args()[1:]
	}
}
