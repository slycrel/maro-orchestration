// Package agentloop ports the parts of agent_loop.py that are not the loop:
// its command line (`main`) and its fan-out entry point
// (`run_parallel_loops`).
//
// `run_agent_loop` itself — the 670 lines between them — is NOT ported here.
// It is the loop's whole orchestration and it reaches loop_execute,
// loop_post_step, loop_blocked and the director; it is reached through a
// RunFn so that everything around it can be measured now.
//
// The CLI is built on internal/pyargparse rather than on a hand-rolled flag
// parser, because a hand-rolled one answers differently for every input an
// operator actually mistypes: `--proj x` is an abbreviation CPython accepts,
// `-vp foo` is two options, `--max-steps=x` is exit 2 with a specific
// sentence, and `--` is a terminator whose span the positional may or may
// not cover.
package agentloop

import (
	"fmt"
	"io"
	"strings"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/pyargparse"
)

// Defaults for the two counted options. argparse's `default=` puts these in
// the Namespace when the flag is absent, and it does NOT run `type=` over
// them — a default is only converted when it arrives as a string.
const (
	DefaultMaxSteps      = 6
	DefaultMaxIterations = 20
)

// Backends is the `choices=` list, in declaration order — which is the
// order the invalid-choice message prints them in.
var Backends = []string{"auto", "anthropic", "openrouter", "openai",
	"subprocess", "codex", "xai"}

// UsageText and HelpText are argparse's own output for this parser.
//
// PINNED DIFFERENCE: argparse re-wraps both blocks to
// `shutil.get_terminal_size().columns - 2`. These constants are the
// 80-column rendering — what CPython produces whenever stdout is not a
// terminal (a pipe, a redirect, a CI log, every scripted invocation) and
// what it falls back to when the width cannot be determined. Reproducing
// the wrap would mean porting HelpFormatter's usage-grouping algorithm.
// Measured, not assumed: the differential exports COLUMNS=80.
//
// `--project, -p PROJECT` is the 3.13+ rendering. Older argparse wrote
// `--project PROJECT, -p PROJECT`, so a port checked against an older
// interpreter disagrees here for a reason that has nothing to do with this
// code.
const UsageText = "usage: maro-run [-h] [--project PROJECT] [--model MODEL]\n" +
	"                [--max-steps MAX_STEPS] [--max-iterations MAX_ITERATIONS]\n" +
	"                [--dry-run] [--verbose]\n" +
	"                [--backend {auto,anthropic,openrouter,openai,subprocess,codex,xai}]\n" +
	"                goal [goal ...]\n"

const HelpText = UsageText + `
Run Maro's autonomous loop on a goal

positional arguments:
  goal                  Goal description

options:
  -h, --help            show this help message and exit
  --project, -p PROJECT
                        Project slug (auto-created if not exists)
  --model, -m MODEL     LLM model string (e.g. anthropic/claude-haiku-4-5)
  --max-steps MAX_STEPS
                        Max decomposition steps (default: 6)
  --max-iterations MAX_ITERATIONS
                        Hard cap on LLM calls (default: 20)
  --dry-run             Simulate without LLM API calls
  --verbose, -v         Print progress
  --backend, -b {auto,anthropic,openrouter,openai,subprocess,codex,xai}
                        LLM backend (default: auto-detect; MARO_BACKEND env
                        var also accepted)
`

// Parser is the ArgumentParser `agent_loop.main` builds.
//
// Each option string is its own row because argparse's
// `_option_string_actions` is keyed by string; the pair that shares an
// action shares a Label, which is what an error message prints.
var Parser = &pyargparse.Parser{
	Prog:  "maro-run",
	Usage: UsageText,
	Opts: []pyargparse.Opt{
		{Str: "-h", Dest: "help", NArgs: pyargparse.Flag, Label: "-h/--help", Help: true},
		{Str: "--help", Dest: "help", NArgs: pyargparse.Flag, Label: "-h/--help", Help: true},
		{Str: "--project", Dest: "project", NArgs: pyargparse.Exactly1, Label: "--project/-p"},
		{Str: "-p", Dest: "project", NArgs: pyargparse.Exactly1, Label: "--project/-p"},
		{Str: "--model", Dest: "model", NArgs: pyargparse.Exactly1, Label: "--model/-m"},
		{Str: "-m", Dest: "model", NArgs: pyargparse.Exactly1, Label: "--model/-m"},
		{Str: "--max-steps", Dest: "max_steps", NArgs: pyargparse.Exactly1,
			Label: "--max-steps", Convert: pyargparse.IntValue},
		{Str: "--max-iterations", Dest: "max_iterations", NArgs: pyargparse.Exactly1,
			Label: "--max-iterations", Convert: pyargparse.IntValue},
		{Str: "--dry-run", Dest: "dry_run", NArgs: pyargparse.Flag, Label: "--dry-run"},
		{Str: "--verbose", Dest: "verbose", NArgs: pyargparse.Flag, Label: "--verbose/-v"},
		{Str: "-v", Dest: "verbose", NArgs: pyargparse.Flag, Label: "--verbose/-v"},
		{Str: "--backend", Dest: "backend", NArgs: pyargparse.Exactly1,
			Label: "--backend/-b", Convert: pyargparse.Choices(Backends...)},
		{Str: "-b", Dest: "backend", NArgs: pyargparse.Exactly1,
			Label: "--backend/-b", Convert: pyargparse.Choices(Backends...)},
	},
	Positionals: []pyargparse.Positional{
		// nargs="+", so it IS required: no goal is
		// "the following arguments are required: goal", reported before
		// any unrecognized-argument complaint.
		{Dest: "goal", NArgs: pyargparse.OneOrMore, Label: "goal", Required: true},
	},
}

// Args is the shape `main` hands to run_agent_loop.
//
// Project, Model and Backend are POINTERS because argparse's default for
// all three is None, and None is a different argument from "" at the other
// end — `run_agent_loop(project=None)` picks a project, `project=""` is a
// project slug that is the empty string.
type Args struct {
	Goal          string
	Project       *string
	Model         *string
	Backend       *string
	MaxSteps      int
	MaxIterations int
	DryRun        bool
	Verbose       bool
	Help          bool
}

// ParseArgs is the argparse half of `main`.
//
// The joining is Python's `" ".join(args.goal)`: nargs="+" collects the
// words and main glues them back together, so `maro-run write the thing`
// and `maro-run "write the thing"` are the same goal — and
// `maro-run "a  b"` is NOT, because the double space survives inside one
// token and the join only ever inserts one.
func ParseArgs(argv []string) (Args, error) {
	r, err := Parser.Parse(argv)
	a := Args{
		MaxSteps:      DefaultMaxSteps,
		MaxIterations: DefaultMaxIterations,
		DryRun:        r.Bool("dry_run"),
		Help:          r.Help,
	}
	a.Goal = strings.Join(r.Strings("goal"), " ")
	if v, ok := r.Values["project"]; ok {
		s := v.(string)
		a.Project = &s
	}
	if v, ok := r.Values["model"]; ok {
		s := v.(string)
		a.Model = &s
	}
	if v, ok := r.Values["backend"]; ok {
		s := v.(string)
		a.Backend = &s
	}
	if n := r.IntPtr("max_steps"); n != nil {
		a.MaxSteps = *n
	}
	if n := r.IntPtr("max_iterations"); n != nil {
		a.MaxIterations = *n
	}
	// `verbose=args.verbose or True` at the call site — see Main.
	a.Verbose = r.Bool("verbose")
	return a, err
}

// RunResult is the part of the LoopResult `main` reads back.
type RunResult struct {
	Status  string
	Summary string
}

// Main is `agent_loop.main(argv)`. It returns the process exit code, and
// writes the summary to `out` rather than stdout so a differential can
// compare the whole rendering.
//
// # `--verbose` does nothing, and that is the source
//
// Python calls `run_agent_loop(..., verbose=args.verbose or True)`. The
// `or True` makes the expression True for every input: an unset flag is
// False, and `False or True` is True. So this CLI runs verbose always, and
// `-v` changes nothing it can reach. The port reproduces it — with the
// flag still parsed, because parsing is where a wrong `-v` is refused —
// and the finding is filed Python-side rather than fixed here.
func Main(argv []string, run func(Args) (RunResult, error), out io.Writer) (int, error) {
	a, err := ParseArgs(argv)
	if err != nil {
		return 2, err
	}
	if a.Help {
		// argparse prints help to STDOUT and exits 0 — the one case where a
		// usage block belongs on the rendering stream. Errors go to stderr,
		// which is why UsageError carries its own renderer instead.
		io.WriteString(out, HelpText)
		return 0, nil
	}
	a.Verbose = true
	res, err := run(a)
	if err != nil {
		return 1, err
	}
	fmt.Fprintln(out, res.Summary)
	if res.Status == "done" {
		return 0, nil
	}
	return 1, nil
}

// ErrMaxWorkers is `ValueError("max_workers must be greater than 0")`, which
// ThreadPoolExecutor raises from its constructor.
type ErrMaxWorkers struct{}

func (ErrMaxWorkers) Error() string { return "max_workers must be greater than 0" }

// PyClass reports the exception class CPython raises, for a differential
// that compares the class as well as the message (L22).
func (ErrMaxWorkers) PyClass() string { return "ValueError" }

// RunParallelLoops is `run_parallel_loops`: run several goals concurrently
// and return their results IN INPUT ORDER.
//
// Three behaviours that a straightforward Go rewrite gets wrong:
//
//  1. An empty goal list returns before the pool is built. So
//     `max_workers=0` with no goals is fine and `max_workers=0` with one
//     goal is a ValueError — the guard is the early return, not a check on
//     max_workers.
//  2. `min(max_workers, len(goals))` caps the pool at the work available,
//     so three goals never start four threads.
//  3. Results are read as `[f.result() for f in futures]`, in SUBMISSION
//     order. The first goal's exception propagates even when a later goal
//     failed first, and `with ThreadPoolExecutor(...)` still joins every
//     thread on the way out — the failure does not cancel the rest.
//
// Python takes no copy_context() here, deliberately, and the comment in the
// source says why: each pool thread gets its own root context and
// run_agent_loop sets its own run-scoped ContextVars. Go goroutines have no
// ambient context at all, so there is nothing to decline.
func RunParallelLoops[T any](goals []string, maxWorkers int,
	run func(goal string) (T, error)) ([]T, error) {
	if len(goals) == 0 {
		return []T{}, nil
	}
	effective := maxWorkers
	if len(goals) < effective {
		effective = len(goals)
	}
	if effective <= 0 {
		return nil, ErrMaxWorkers{}
	}

	type slot struct {
		val T
		err error
	}
	out := make([]slot, len(goals))

	// A FIFO queue with `effective` workers, not `len(goals)` goroutines
	// racing for a semaphore. The two agree on everything this function
	// RETURNS, and they disagree on the order tasks START in: a semaphore
	// hands the slot to whichever goroutine the scheduler picks, where
	// ThreadPoolExecutor's work queue is first-in-first-out. Nothing here
	// observes start order — but run() is a caller's function, and a caller
	// whose steps log their own start is entitled to the order Python gives
	// them.
	queue := make(chan int)
	var wg sync.WaitGroup
	for w := 0; w < effective; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range queue {
				out[i].val, out[i].err = run(goals[i])
			}
		}()
	}
	for i := range goals {
		queue <- i
	}
	close(queue)
	// The `with` block joins every worker before the results are read, and
	// so does this: a goal that fails does not cancel its siblings, and a
	// caller that saw only the first error would still be waiting on
	// goroutines writing into `out`.
	wg.Wait()

	results := make([]T, 0, len(goals))
	for i := range out {
		if out[i].err != nil {
			return nil, out[i].err
		}
		results = append(results, out[i].val)
	}
	return results, nil
}
