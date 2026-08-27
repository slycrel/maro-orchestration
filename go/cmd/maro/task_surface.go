package main

import "flag"

// The ARGUMENT SURFACE of `maro task`, in one place, because it is a
// declaration and not an implementation detail.
//
// # Why this file exists
//
// Three consecutive review rounds found a divergence in this one parser,
// each time a DIFFERENT property of it, and each fix was scoped to the
// property that had just been found:
//
//	r1   flags written after the positional were silently dropped
//	     (argparse interleaves, Go's `flag` stops) -> parseArgs
//	r10  extra positionals were silently ignored rather than refused,
//	     and `--` re-entered flag parsing -> parseArgs grew an arity
//	r11  the four job-id verbs shared one FlagSet, so `complete --error`
//	     was accepted where argparse gives each verb its own subparser
//
// Every one of those was visible in `task_store.py`'s argparse spec the
// whole time. The reason they arrived one per round is that a fix is
// written with the FINDING in mind and the rest of the construct out of
// it — so the review found the next property, and the next, at one round
// each. Cataloguing that as a pattern to watch for did not stop it
// happening twice more.
//
// So the fix is not to look harder. It is to make the whole surface a
// DECLARATION and diff it against the Python's declaration mechanically:
// `task_surface_diff_test.go` extracts argparse's complete spec — every
// subcommand, every option string, every default, every positional count —
// and compares it against this table. A property that differs cannot wait
// for a reviewer to notice it, because nothing is being checked one
// property at a time any more.
//
// Adding a verb here without adding it to `task_store.py` (or the reverse)
// fails that test. That is the point.

// taskFlags carries the parsed values for every flag any task verb
// declares. One struct rather than one per verb: the verbs are a closed
// set, the fields are strings, and a shared struct keeps the construction
// in ONE function, which is what makes the surface introspectable.
type taskFlags struct {
	lane        *string
	source      *string
	reason      *string
	parentJobID *string
	blockedBy   *string
	status      *string
	errText     *string
}

// taskPositionals is each verb's declared positional count, straight off
// `task_store.py`'s subparsers. `claim`, `complete`, `fail` and `archive`
// declare `job_id`; nothing else declares anything.
var taskPositionals = map[string]int{
	"enqueue":  0,
	"claim":    1,
	"complete": 1,
	"fail":     1,
	"list":     0,
	"status":   0,
	"archive":  1,
	"recover":  0,
}

// newTaskFlagSet builds the FlagSet for one verb and returns it alongside
// the parsed-value pointers and the verb's declared positional count.
//
// The runtime and the contract test call THIS function — neither builds a
// FlagSet of its own. A test that constructed its own copy of the surface
// would be comparing the surface against a transcription of itself, which
// is the shape that reports agreement while measuring nothing (L1).
//
// A verb this function does not know gets an empty FlagSet and an arity of
// zero, which is the fail-closed answer: an unknown verb accepting nothing
// is a usage error, and an unknown verb accepting anything is a hole.
func newTaskFlagSet(verb string) (*flag.FlagSet, *taskFlags, int) {
	fs := flag.NewFlagSet(verb, flag.ContinueOnError)
	tf := &taskFlags{errText: new(string), status: new(string)}
	switch verb {
	case "enqueue":
		tf.lane = fs.String("lane", "now", "queue lane")
		// The CLI's source default is "cli", not the library's
		// "task_store" — the difference is how a queue row says whether a
		// human or the runtime put it there. Both defaults are asserted by
		// the contract test, because a default is part of what a flag
		// MEANS and a wrong one writes a wrong row without erroring.
		tf.source = fs.String("source", "cli", "who enqueued this")
		tf.reason = fs.String("reason", "", "why")
		tf.parentJobID = fs.String("parent-job-id", "", "parent job id")
		tf.blockedBy = fs.String("blocked-by", "", "comma-separated job ids")
	case "fail":
		// ONLY `fail`. task_store.py builds four separate subparsers for
		// the job-id verbs and only `p_fail` calls
		// `add_argument("--error")`; the other three declare `job_id`
		// alone. Sharing one FlagSet across the four because they share a
		// code path shared the ARGUMENT SURFACE too, and
		// `task complete <id> --error boom` completed the task where
		// argparse exits 2 (adversarial r11).
		tf.errText = fs.String("error", "", "failure reason")
	case "list":
		tf.status = fs.String("status", "", "filter by status")
	}
	arity, known := taskPositionals[verb]
	if !known {
		arity = 0
	}
	return fs, tf, arity
}
