package main

import (
	"flag"
	"fmt"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The whole argv surface of `python3 -m task_store`, extracted from the
// live argparse parser rather than read off the source by a human.
//
// task_store.py builds its parser inside main() and never exposes it, so
// the probe intercepts parse_args: it monkeypatches the method to raise a
// sentinel carrying `self`, calls main(), and catches it. That yields the
// FULLY BUILT parser without executing any command — no store is touched
// and no argv is interpreted.
//
// Extracting rather than transcribing is the point. A transcription is a
// human reading the Python once and writing down what they saw, which is
// exactly the step that has been wrong three rounds running.
const pyTaskSurfaceSrc = `
import argparse, json, sys

class _Got(Exception):
    def __init__(self, parser):
        self.parser = parser

def _grab(self, *a, **kw):
    raise _Got(self)

argparse.ArgumentParser.parse_args = _grab
import task_store
try:
    task_store.main()
    print(json.dumps({"error": "main() returned without parsing"}))
    sys.exit(0)
except _Got as g:
    parser = g.parser

subs = None
for act in parser._actions:
    if isinstance(act, argparse._SubParsersAction):
        subs = act
        break
if subs is None:
    print(json.dumps({"error": "no subparsers found"}))
    sys.exit(0)

out = {}
for name, sp in subs.choices.items():
    flags = {}
    kinds = {}
    positionals = []
    for a in sp._actions:
        if isinstance(a, argparse._HelpAction):
            continue
        if a.option_strings:
            # A store_true default is the bool False, whose str() is
            # "False", where Go's bool flag DefValue is "false". Normalise
            # to Go's spelling so a future bool flag compares on its VALUE
            # rather than failing on Python capitalisation.
            for opt in a.option_strings:
                d = a.default
                if d is None:
                    flags[opt] = ""
                elif isinstance(d, bool):
                    flags[opt] = "true" if d else "false"
                else:
                    flags[opt] = str(d)
            kinds[a.option_strings[0]] = {
                "choices": None if a.choices is None else [str(c) for c in a.choices],
                "type": getattr(a.type, "__name__", None),
                "required": bool(a.required),
                "nargs": a.nargs,
            }
        else:
            positionals.append({"dest": a.dest, "nargs": a.nargs})
    out[name] = {"flags": flags, "kinds": kinds, "positionals": positionals}
print(json.dumps(out))
`

type pySubparser struct {
	Flags map[string]string `json:"flags"`
	// Kinds carries the properties this test does NOT yet compare
	// field-by-field, keyed by the flag's first option string. It is
	// extracted anyway and asserted to be EMPTY of the shapes the Go
	// cannot express, so "not compared" can never quietly become "not
	// present" (L28 — an omission a comment describes decays; one a test
	// enforces does not).
	Kinds map[string]struct {
		Choices  []string `json:"choices"`
		Type     *string  `json:"type"`
		Required bool     `json:"required"`
		Nargs    any      `json:"nargs"`
	} `json:"kinds"`
	Positionals []struct {
		Dest  string `json:"dest"`
		Nargs any    `json:"nargs"`
	} `json:"positionals"`
}

// knownArgvDifferences are the argv-surface differences that were MEASURED,
// DECIDED and FILED rather than fixed (BACKLOG, "pack adopt takes its label
// as a FLAG"). They are listed here so the contract test refuses everything
// else, and each carries its reason so the next reader does not "fix" one
// by accident.
//
// Spelling: Go's `flag` treats `-x` and `--x` as the same flag, so the port
// accepts BOTH spellings of every long option and argparse accepts only the
// double-dash one. That is CLI-wide and single-dash is this CLI's own
// documented idiom (`maro run -backend dry`), so the comparison below is on
// the flag NAME with dashes stripped, and the spelling difference is filed
// rather than asserted per flag.
var knownArgvDifferences = map[string]string{}

func normFlag(opt string) string { return strings.TrimLeft(opt, "-") }

// TestTaskArgvSurfaceMatchesArgparse diffs the WHOLE declared surface, not
// one property of it.
//
// Three consecutive rounds each found a different property of this parser
// wrong — interleaving, then arity, then which verb owns `--error`. Each
// was visible in task_store.py the entire time; each arrived one round
// apart because each fix was scoped to the finding. This test is scoped to
// the DECLARATION, so there is no next property to find one round later.
func TestTaskArgvSurfaceMatchesArgparse(t *testing.T) {
	var py map[string]pySubparser
	pyprobe.Probe{Marker: "task_store.py"}.RunJSON(t, pyTaskSurfaceSrc, &py)

	if len(py) == 0 {
		t.Fatal("argparse extraction returned no subcommands — the probe is " +
			"broken and nothing below it measures anything")
	}
	// Stated before comparing: the eight verbs task_store.py declares. If
	// the Python grows a ninth, this fails HERE with a clear reason rather
	// than silently comparing seven of eight.
	wantVerbs := []string{"archive", "claim", "complete", "enqueue",
		"fail", "list", "recover", "status"}
	var gotVerbs []string
	for k := range py {
		gotVerbs = append(gotVerbs, k)
	}
	sort.Strings(gotVerbs)
	if strings.Join(gotVerbs, ",") != strings.Join(wantVerbs, ",") {
		t.Fatalf("task_store.py declares %v, this test was written against "+
			"%v — the Python's surface changed and the Go's was not "+
			"re-derived", gotVerbs, wantVerbs)
	}

	for _, verb := range wantVerbs {
		t.Run(verb, func(t *testing.T) {
			spec := py[verb]
			fs, _, arity := newTaskFlagSet(verb)

			// --- flags, both directions ---
			goFlags := map[string]string{}
			fs.VisitAll(func(f *flag.Flag) { goFlags[f.Name] = f.DefValue })

			pyFlags := map[string]string{}
			for opt, def := range spec.Flags {
				pyFlags[normFlag(opt)] = def
			}

			for name, pyDef := range pyFlags {
				goDef, ok := goFlags[name]
				if !ok {
					if why := knownArgvDifferences[verb+":"+name]; why != "" {
						t.Logf("[known] %s -%s absent from the Go: %s", verb, name, why)
						continue
					}
					t.Errorf("argparse declares --%s on `%s` and the Go does "+
						"not: an argv the Python accepts is refused here",
						name, verb)
					continue
				}
				if goDef != pyDef {
					t.Errorf("`%s` -%s default = %q, argparse default = %q — "+
						"a default is part of what a flag MEANS, and a wrong "+
						"one writes a wrong row without erroring",
						verb, name, goDef, pyDef)
				}
			}
			for name := range goFlags {
				if _, ok := pyFlags[name]; ok {
					continue
				}
				if why := knownArgvDifferences[verb+":"+name]; why != "" {
					t.Logf("[known] %s -%s absent from argparse: %s", verb, name, why)
					continue
				}
				t.Errorf("the Go declares -%s on `%s` and argparse does not: "+
					"an argv the Python REFUSES is accepted here, which is "+
					"how `complete --error boom` completed a task (r11)",
					name, verb)
			}

			// --- the properties this test does not compare, asserted
			// ABSENT rather than ignored ---
			//
			// argparse can declare things Go's `flag` has no equivalent
			// for: `choices=`, a coercing `type=`, `required=True` on an
			// option, and `nargs` on an option. None of them appear in
			// task_store.py today. If one ever does, the Go cannot
			// silently fail to honour it — it fails HERE, naming the
			// property, so somebody decides how to reproduce it.
			for opt, k := range spec.Kinds {
				if k.Choices != nil {
					t.Errorf("argparse declares choices=%v on `%s %s`, which "+
						"Go's flag cannot express — decide how to reproduce "+
						"it rather than accepting any value", k.Choices, verb, opt)
				}
				if k.Type != nil && *k.Type != "str" {
					t.Errorf("argparse declares type=%s on `%s %s`; the Go "+
						"parses it as a string, so a value the Python would "+
						"reject is accepted here", *k.Type, verb, opt)
				}
				if k.Required {
					t.Errorf("argparse declares required=True on `%s %s`; "+
						"Go's flag has no required options, so omitting it "+
						"is an error there and a default here", verb, opt)
				}
				if k.Nargs != nil {
					t.Errorf("argparse declares nargs=%v on the OPTION `%s %s`; "+
						"Go's flag takes exactly one value", k.Nargs, verb, opt)
				}
			}

			// --- positional arity ---
			wantArity := 0
			for _, p := range spec.Positionals {
				switch p.Nargs {
				case nil: // exactly one
					wantArity++
				case "*", "+":
					wantArity = UnlimitedPositionals
				default:
					t.Fatalf("`%s` positional %q has nargs %v, which this "+
						"test does not know how to score — teach it rather "+
						"than letting it guess", verb, p.Dest, p.Nargs)
				}
			}
			if arity != wantArity {
				t.Errorf("`%s` declared arity = %s, argparse = %s — an extra "+
					"positional is an ERROR to argparse, and ignoring one is "+
					"how `fail <id> -- --error boom` marked a task failed (r10)",
					verb, arityName(arity), arityName(wantArity))
			}
		})
	}
}

func arityName(n int) string {
	if n == UnlimitedPositionals {
		return "unlimited"
	}
	return fmt.Sprintf("%d", n)
}
