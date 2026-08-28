package preflight

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pfReviewer is one candidate the fixture hands `_build_reviewers`.
//
// Mode says how it behaves, and the four modes are the four ways the
// original can fail to get an answer without any of them being an error
// the caller sees: the call raises, the response has no content, the
// content is not text, and the generator itself blows up.
type pfReviewer struct {
	Name string `json:"name"`
	// Mode is "ok" | "raise" | "no_content" | "generator_raises".
	Mode    string `json:"mode"`
	Content any    `json:"content"`
}

// pfSpec is one scenario, sent to both engines. Kind selects which
// function is under test; the rest of the fields are that kind's fixture.
type pfSpec struct {
	Name string `json:"name"`
	Kind string `json:"kind"`

	Steps []string `json:"steps"`

	Scope     string  `json:"scope"`
	ScopeNote any     `json:"scope_note"`
	Flags     [][]any `json:"flags"`
	Milestone []int   `json:"milestone"`
	Raw       string  `json:"raw"`

	Goal           string       `json:"goal"`
	Verbose        bool         `json:"verbose"`
	DropLLMMessage bool         `json:"drop_llm_message"`
	Reviewers      []pfReviewer `json:"reviewers"`

	Dir            string `json:"dir"`
	WriteFile      bool   `json:"write_file"`
	Body           string `json:"body"`
	WriteBytes     []int  `json:"write_bytes"`
	WriteDir       bool   `json:"write_dir"`
	MemoryDirFails bool   `json:"memory_dir_fails"`
	CalPathKind    string `json:"cal_path_kind"`
}

// pfLog stands in for pre_flight's module-level `log`, recording what the
// probe's Rec records: the LEVEL and the message logging would have built.
type pfLog struct{ lines []any }

func (l *pfLog) add(level, format string, args []any) {
	msg := format
	if len(args) > 0 {
		msg = fmt.Sprintf(format, args...)
	}
	l.lines = append(l.lines, []any{level, msg})
}

func (l *pfLog) Info(f string, a ...any)  { l.add("INFO", f, a) }
func (l *pfLog) Debug(f string, a ...any) { l.add("DEBUG", f, a) }
func (l *pfLog) Log(level, f string, a ...any) {
	l.add(level, f, a)
}

// pvVal mirrors the probe's pv(): a rendering both engines can produce.
//
// Floats carry their repr because Go writes float64(1) as `1` and CPython
// writes `1.0`; dicts become pair lists because their ORDER is part of
// the answer and because a scope_breakdown key need not be a string.
func pvVal(v any) any {
	switch t := v.(type) {
	case nil:
		return nil
	case bool:
		return t
	case string:
		return t
	case int:
		return t
	case float64:
		return []any{"<float>", pyval.Str(t)}
	case json.Number:
		if strings.ContainsAny(string(t), ".eE") {
			f, err := t.Float64()
			if err == nil {
				return []any{"<float>", pyval.Str(f)}
			}
		}
		// An integer literal marshals back as itself, so a value past
		// int64 survives the round trip the way Python's unbounded int
		// does.
		return t
	case pyval.Obj:
		out := []any{}
		for _, f := range t {
			out = append(out, []any{f.Key, pvVal(f.Val)})
		}
		return out
	case pyval.List:
		out := []any{}
		for _, x := range t {
			out = append(out, pvVal(x))
		}
		return out
	case ScopeBreakdown:
		out := []any{}
		for _, b := range t {
			out = append(out, []any{pvVal(b.Scope), []any{
				[]any{"count", b.Count},
				[]any{"stuck", b.Stuck},
				[]any{"done", b.Done},
			}})
		}
		return out
	case []any:
		out := []any{}
		for _, x := range t {
			out = append(out, pvVal(x))
		}
		return out
	}
	return fmt.Sprintf("<%T>", v)
}

func pvReview(r *Review) any {
	if r == nil {
		return nil
	}
	flags := []any{}
	for _, f := range r.Flags {
		flags = append(flags, []any{f.Kind, f.Step, pvVal(f.Message), f.Severity})
	}
	ms := []any{}
	for _, m := range r.MilestoneStepIndices {
		ms = append(ms, m)
	}
	return map[string]any{
		"scope": r.Scope, "scope_note": pvVal(r.ScopeNote),
		"flags": flags, "milestone": ms, "raw": r.Raw,
	}
}

func buildReview(sc pfSpec) Review {
	flags := []Flag{}
	for _, f := range sc.Flags {
		step, err := pyval.Int(f[1])
		if err != nil {
			panic(err)
		}
		flags = append(flags, Flag{
			Kind:     f[0].(string),
			Step:     step,
			Message:  f[2],
			Severity: f[3].(string),
		})
	}
	ms := []int{}
	ms = append(ms, sc.Milestone...)
	return Review{Scope: sc.Scope, ScopeNote: sc.ScopeNote, Flags: flags,
		MilestoneStepIndices: ms, Raw: sc.Raw}
}

// dropImportMsg is CPython's ImportError for a name the module no longer
// has. The file path CPython appends is stripped from both sides (see
// canonImportPath), so only the sentence is compared.
const dropImportMsg = "cannot import name 'LLMMessage' from 'llm'"

func goPreflightRecord(t *testing.T, root string, sc pfSpec) map[string]any {
	t.Helper()
	lg := &pfLog{lines: []any{}}
	rec := map[string]any{"name": sc.Name, "cls": "", "msg": ""}

	switch sc.Kind {
	case "review_system":
		rec["text"] = reviewSystem

	case "heuristic":
		rec["scope"] = HeuristicScope(sc.Steps)

	case "review_obj":
		r := buildReview(sc)
		rec["has_concerns"] = r.HasConcerns()
		rec["summary"] = r.Summary()
		rec["format_for_log"] = r.FormatForLog()

	case "parse":
		rev, _ := ParseReview(sc.Raw, lg)
		rec["review"] = pvReview(rev)

	case "review_plan":
		calls := []any{}
		var buf bytes.Buffer
		idx := 0
		deps := ReviewDeps{
			ImportLLMMessage: func() error {
				if sc.DropLLMMessage {
					return errors.New(dropImportMsg)
				}
				return nil
			},
			NextReviewer: func() (Reviewer, bool, error) {
				if idx >= len(sc.Reviewers) {
					return Reviewer{}, false, nil
				}
				spec := sc.Reviewers[idx]
				idx++
				if spec.Mode == "generator_raises" {
					calls = append(calls, map[string]any{
						"call": "generator_raises", "name": spec.Name})
					return Reviewer{}, false,
						errors.New(pyval.Str(spec.Content))
				}
				calls = append(calls, map[string]any{
					"call": "yield", "name": spec.Name})
				return Reviewer{Name: spec.Name, Complete: func(
					messages []Message, kw pyval.Obj) (any, error) {
					ms := []any{}
					for _, m := range messages {
						ms = append(ms, []any{m.Role, m.Content})
					}
					kws := []any{}
					for _, f := range kw {
						kws = append(kws, []any{f.Key, pvVal(f.Val)})
					}
					calls = append(calls, map[string]any{
						"call": "complete", "name": spec.Name,
						"messages": ms, "kw": kws})
					switch spec.Mode {
					case "raise":
						return nil, errors.New(pyval.Str(spec.Content))
					case "no_content":
						// `resp.content` on a response that has none.
						// The class name is the probe's fixture class.
						return nil, &pyval.PyErr{Class: "AttributeError",
							Msg: "'Resp' object has no attribute 'content'"}
					}
					return spec.Content, nil
				}}, true, nil
			},
			Log: lg, Stderr: &buf,
		}
		r := ReviewPlan(sc.Goal, sc.Steps, sc.Verbose, deps)
		rec["review"] = pvReview(&r)
		rec["calls"] = calls
		rec["stderr"] = buf.String()

	case "stats":
		res, err := goStats(t, root, sc)
		if err != nil {
			rec["cls"] = pyval.ClassOf(err)
			rec["msg"] = err.Error()
		} else {
			rec["stats"] = pvVal(res)
		}

	default:
		t.Fatalf("unknown kind %q", sc.Kind)
	}

	rec["log"] = lg.lines
	return rec
}

func goStats(t *testing.T, root string, sc pfSpec) (pyval.Obj, error) {
	t.Helper()
	dir := filepath.Join(root, sc.Dir)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	def := filepath.Join(dir, "preflight_calibration.jsonl")
	if sc.WriteFile {
		if err := os.WriteFile(def, []byte(sc.Body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if len(sc.WriteBytes) > 0 {
		b := make([]byte, len(sc.WriteBytes))
		for i, n := range sc.WriteBytes {
			b[i] = byte(n)
		}
		if err := os.WriteFile(def, b, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	adir := filepath.Join(dir, "adir")
	if sc.WriteDir {
		if err := os.MkdirAll(adir, 0o755); err != nil {
			t.Fatal(err)
		}
	}

	var calPath any
	switch sc.CalPathKind {
	case "none":
		calPath = nil
	case "default":
		calPath = def
	case "missing":
		calPath = filepath.Join(dir, "nope.jsonl")
	case "empty":
		calPath = ""
	case "dir":
		calPath = adir
	case "int":
		calPath = 123
	default:
		t.Fatalf("unknown cal_path_kind %q", sc.CalPathKind)
	}

	return CalibrationStats(calPath, StatsDeps{
		MemoryDir: func() (string, error) {
			if sc.MemoryDirFails {
				return "", errors.New("no memory_dir")
			}
			return dir, nil
		},
	})
}

// goodJSON is a complete, well-formed reviewer answer. Several scenarios
// vary one thing about it, so it is written once.
const goodJSON = `{"scope": "wide",` +
	` "scope_note": "the plan hides a census",` +
	` "assumptions": [{"step": 2, "issue": "assumes the key works"}],` +
	` "milestone_candidates": [{"step": 3, "reason": "a whole project"}],` +
	` "unknown_unknowns": ["what the corpus looks like"],` +
	` "class_gaps": [{"step": 0, "issue": "names a class, touches one"}]}`

func preflightScenarios() []pfSpec {
	parse := func(name, raw string) pfSpec {
		return pfSpec{Name: name, Kind: "parse", Raw: raw}
	}
	heur := func(name string, steps ...string) pfSpec {
		if steps == nil {
			// A nil slice marshals as `null`, and `len(None)` is a
			// TypeError on the other side. The empty PLAN is a real
			// fixture; a missing argument is not.
			steps = []string{}
		}
		return pfSpec{Name: name, Kind: "heuristic", Steps: steps}
	}
	rp := func(name string, f func(*pfSpec)) pfSpec {
		sc := pfSpec{Name: name, Kind: "review_plan",
			Goal:  "ship the reviewer",
			Steps: []string{"one", "two", "three"}}
		f(&sc)
		return sc
	}
	st := func(name string, f func(*pfSpec)) pfSpec {
		sc := pfSpec{Name: name, Kind: "stats", CalPathKind: "default"}
		f(&sc)
		return sc
	}
	rows := []pfSpec{
		// The prompt itself. A critic prompt that has quietly lost a
		// dimension still returns valid-looking JSON forever, so the
		// constant is compared before anything that uses it.
		{Name: "the critic prompt is byte-identical", Kind: "review_system"},

		// ---------------- _heuristic_scope ----------------
		heur("no steps at all is narrow"),
		heur("three plain steps are narrow", "one", "two", "three"),
		heur("install contains all, so three steps are wide",
			"install the package", "run it", "done"),
		heur("eight narrow steps are still wide, because count wins first",
			"read a", "read b", "read c", "read d",
			"read e", "read f", "read g", "read h"),
		heur("five steps with a narrow word are narrow",
			"check the log", "a", "b", "c", "d"),
		heur("six plain steps are medium", "a", "b", "c", "d", "e", "f"),
		heur("the keyword match is case-folded", "DEPLOY the thing"),
		heur("budget contains get, so four steps are narrow",
			"target the budget", "a", "b", "c"),
		heur("seven steps with a narrow word are medium",
			"get the file", "a", "b", "c", "d", "e", "f"),
		heur("a final sigma folds the way CPython folds it",
			"ΟΔΟΣ", "a", "b", "c", "d", "e"),
		// str.lower() is FULL case mapping and Go's strings.ToLower is
		// simple: "LİST" folds to "li̇st" in CPython, with a combining
		// dot between the i and the s, and to "list" in Go. The narrow
		// keyword therefore fires on one side and not the other, and
		// this is the fixture that makes the difference reach an ANSWER
		// rather than an intermediate string nothing returns.
		heur("a dotted capital I does not fold into the list keyword",
			"LİST the files", "a", "b", "c"),
		heur("a keyword split across two steps does not join up",
			"dep", "loy the app", "x"),
		heur("six steps with a narrow word are still medium",
			"check it", "a", "b", "c", "d", "e"),
		heur("a wide keyword beats a narrow one in the same text",
			"read the plan then rewrite it"),

		// ---------------- PlanReview rendering ----------------
		{Name: "an empty review renders without extras", Kind: "review_obj",
			Scope: "narrow", ScopeNote: "", Flags: [][]any{},
			Milestone: []int{}},
		{Name: "milestones render as a Python list", Kind: "review_obj",
			Scope: "medium", ScopeNote: "fine", Milestone: []int{1, 3},
			Flags: [][]any{
				{"milestone", 1, "a whole project", "warn"},
				{"unknown", 0, "the corpus", "info"},
			}},
		{Name: "a wide scope has concerns with no warn flags",
			Kind: "review_obj", Scope: "wide", ScopeNote: "big",
			Flags:     [][]any{{"unknown", 0, "nothing", "info"}},
			Milestone: []int{}},
		{Name: "a falsy scope_note skips its line", Kind: "review_obj",
			Scope: "narrow", ScopeNote: 0, Flags: [][]any{},
			Milestone: []int{}},
		{Name: "a non-string message renders through the f-string",
			Kind: "review_obj", Scope: "narrow", ScopeNote: nil,
			Flags:     [][]any{{"assumption", 2, 5, "warn"}},
			Milestone: []int{}},
		{Name: "a single milestone still gets the brackets",
			Kind: "review_obj", Scope: "wide", ScopeNote: "x",
			Milestone: []int{7}, Flags: [][]any{}},

		// ---------------- _parse_review ----------------
		parse("an empty answer is no answer", ""),
		parse("whitespace is not empty, and does not parse", "   "),
		parse("a complete answer parses", goodJSON),
		parse("a fenced answer parses",
			"```json\n"+goodJSON+"\n```"),
		parse("a fence with no closing fence still loses its first line",
			"```json\n"+goodJSON),
		parse("prose after the closing fence keeps the fence and fails",
			"```json\n"+goodJSON+"\n```\nhope that helps"),
		parse("a missing scope is off-vocabulary",
			`{"scope_note": "x"}`),
		parse("a scope with a trailing space is off-vocabulary",
			`{"scope": "narrow "}`),
		parse("a null scope is off-vocabulary", `{"scope": null}`),
		parse("a numeric scope is off-vocabulary", `{"scope": 1}`),
		parse("garbage is a JSONDecodeError", "not json at all"),
		parse("a JSON list has no .get", `["scope"]`),
		parse("a JSON string has no .get", `"narrow"`),
		parse("assumptions as an object iterates its keys",
			`{"scope": "narrow", "assumptions": {"a": 1}}`),
		parse("assumptions as a string iterates its characters",
			`{"scope": "narrow", "assumptions": "ab"}`),
		parse("assumptions as a number is not iterable",
			`{"scope": "narrow", "assumptions": 3}`),
		parse("unknown_unknowns as a string becomes one flag per character",
			`{"scope": "narrow", "unknown_unknowns": "ab"}`),
		parse("unknown_unknowns as an object becomes one flag per key",
			`{"scope": "narrow", "unknown_unknowns": {"k": 1, "j": 2}}`),
		parse("unknown_unknowns as a number is not iterable",
			`{"scope": "narrow", "unknown_unknowns": 3}`),
		parse("a numeric string step is coerced",
			`{"scope": "narrow", "milestone_candidates": [{"step": "3"}]}`),
		parse("a non-numeric string step is a ValueError",
			`{"scope": "narrow", "milestone_candidates": [{"step": "x"}]}`),
		parse("a null step is a TypeError",
			`{"scope": "narrow", "milestone_candidates": [{"step": null}]}`),
		parse("a float step truncates toward zero",
			`{"scope": "narrow", "milestone_candidates": [{"step": 2.7}]}`),
		parse("a negative float step truncates toward zero",
			`{"scope": "narrow", "milestone_candidates": [{"step": -2.7}]}`),
		parse("a missing step defaults to the whole plan",
			`{"scope": "narrow", "assumptions": [{"issue": "x"}]}`),
		parse("a missing issue defaults to the empty string",
			`{"scope": "narrow", "assumptions": [{"step": 1}]}`),
		parse("a non-string issue passes through",
			`{"scope": "narrow", "assumptions": [{"step": 1, "issue": 5}]}`),
		parse("a missing scope_note is the empty string",
			`{"scope": "medium"}`),
		parse("a non-string scope_note passes through",
			`{"scope": "medium", "scope_note": 5}`),
		parse("an assumption that is not an object has no .get",
			`{"scope": "narrow", "assumptions": [7]}`),
		parse("class_gaps land as class flags",
			`{"scope": "narrow", "class_gaps": [{"step": 4, "issue": "c"}]}`),
		parse("raw keeps the fence-stripped text, not the original",
			"```\n"+`{"scope": "narrow"}`+"\n```"),
		parse("two backticks are not a fence",
			"``\n"+`{"scope": "narrow"}`),
		parse("a fence around pretty-printed JSON keeps its newlines",
			"```json\n{\n \"scope\": \"narrow\",\n \"scope_note\": \"x\"\n}\n```"),
		parse("a closing fence on the same line as the JSON",
			"```json\n"+`{"scope": "narrow"}`+"```"),
		parse("a trailing newline after the closing fence",
			"```json\n"+goodJSON+"\n```\n"),
		parse("a non-string unknown is not coerced",
			`{"scope": "narrow", "unknown_unknowns": [5]}`),
		parse("a string of unknowns iterates characters, not bytes",
			`{"scope": "narrow", "unknown_unknowns": "é"}`),

		// ---------------- review_plan ----------------
		rp("no steps is the only unknown that is not a failure",
			func(s *pfSpec) { s.Steps = []string{} }),
		rp("the first working reviewer is the only one built",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "ok", goodJSON},
					{"anthropic", "ok", goodJSON},
				}
			}),
		rp("a reviewer that raises is followed by the next",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "raise", "connection refused"},
					{"anthropic", "ok", goodJSON},
				}
			}),
		rp("a reviewer that answers garbage is followed by the next",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "ok", "sorry, I cannot"},
					{"anthropic", "ok", goodJSON},
				}
			}),
		rp("no reviewer at all degrades to the heuristic",
			func(s *pfSpec) { s.Reviewers = []pfReviewer{} }),
		rp("every reviewer failing degrades to the heuristic",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "raise", "dead key"},
					{"anthropic", "ok", "{}"},
				}
			}),
		rp("a generator that raises lands on the outer handler",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "ok", "nope"},
					{"anthropic", "generator_raises", "adapter build blew up"},
				}
			}),
		rp("a missing LLMMessage never reaches a reviewer",
			func(s *pfSpec) {
				s.DropLLMMessage = true
				s.Reviewers = []pfReviewer{{"hosted-free", "ok", goodJSON}}
			}),
		rp("a falsy content is the empty string, not an error",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "ok", nil},
					{"anthropic", "ok", goodJSON},
				}
			}),
		rp("a zero content is falsy too",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{{"hosted-free", "ok", 0}}
			}),
		rp("a truthy non-string content cannot be stripped",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "ok", 5},
					{"anthropic", "ok", goodJSON},
				}
			}),
		rp("a response with no content attribute fails like a raise",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "no_content", nil},
					{"anthropic", "ok", goodJSON},
				}
			}),
		rp("surrounding whitespace is stripped before parsing",
			func(s *pfSpec) {
				s.Reviewers = []pfReviewer{
					{"hosted-free", "ok", "\n  " + goodJSON + "  \n"},
				}
			}),
		// str.strip() strips UNICODE whitespace, which a Go
		// strings.Trim(" \t\n") does not: a reviewer whose answer is
		// wrapped in non-breaking spaces parses on one and not the other.
		rp("unicode whitespace is stripped too", func(s *pfSpec) {
			s.Reviewers = []pfReviewer{
				{"hosted-free", "ok", "\u00a0" + goodJSON + "\u00a0"},
			}
		}),
		rp("verbose prints the summary, the scope warning and the warns",
			func(s *pfSpec) {
				s.Verbose = true
				s.Reviewers = []pfReviewer{{"hosted-free", "ok", goodJSON}}
			}),
		rp("verbose on a narrow plan prints no scope warning",
			func(s *pfSpec) {
				s.Verbose = true
				s.Reviewers = []pfReviewer{{"hosted-free", "ok",
					`{"scope": "narrow", "scope_note": "fine",` +
						` "unknown_unknowns": ["a"]}`}}
			}),
		rp("verbose over a heuristic estimate prints nothing",
			func(s *pfSpec) {
				s.Verbose = true
				s.Reviewers = []pfReviewer{}
			}),
		rp("the step list the reviewer sees is 1-based and joined",
			func(s *pfSpec) {
				s.Goal = "a goal with\na newline"
				s.Steps = []string{"first", "second"}
				s.Reviewers = []pfReviewer{{"hosted-free", "ok", goodJSON}}
			}),

		// ---------------- preflight_calibration_stats ----------------
		st("a file of real entries", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"true_positive": true, "scope_predicted": "wide", "actual_status": "stuck"}` + "\n" +
				`{"false_positive": true, "scope_predicted": "wide", "actual_status": "done"}` + "\n" +
				`{"false_negative": true, "scope_predicted": "narrow", "actual_status": "stuck"}` + "\n" +
				`{"true_negative": true, "scope_predicted": "narrow", "actual_status": "done"}` + "\n"
		}),
		st("no cal_path asks orch_items", func(s *pfSpec) {
			s.CalPathKind = "none"
			s.WriteFile = true
			s.Body = `{"true_positive": 1, "scope_predicted": "wide", "actual_status": "stuck"}` + "\n"
		}),
		st("a memory_dir that cannot be resolved answers with a dict",
			func(s *pfSpec) {
				s.CalPathKind = "none"
				s.MemoryDirFails = true
			}),
		st("a missing file is not an error", func(s *pfSpec) {
			s.CalPathKind = "missing"
		}),
		st("a file of blank and garbled lines has no valid entries",
			func(s *pfSpec) {
				s.WriteFile = true
				s.Body = "\n   \nnot json\n{oops\n\n"
			}),
		st("a garbled line is dropped and does not shrink the denominator",
			func(s *pfSpec) {
				s.WriteFile = true
				s.Body = `{"scope_predicted": "wide", "actual_status": "stuck"}` + "\n" +
					"garbage\n" +
					`{"scope_predicted": "wide", "actual_status": "done"}` + "\n"
			}),
		st("no positives leaves precision and recall as None",
			func(s *pfSpec) {
				s.WriteFile = true
				s.Body = `{"true_negative": true, "scope_predicted": "narrow"}` + "\n"
			}),
		st("a perfect run rounds to a whole float", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"true_positive": true, "scope_predicted": "wide", "actual_status": "stuck"}` + "\n" +
				`{"true_positive": true, "scope_predicted": "wide", "actual_status": "stuck"}` + "\n"
		}),
		st("a repeating recall is rounded to three places",
			func(s *pfSpec) {
				s.WriteFile = true
				s.Body = `{"true_positive": true, "scope_predicted": "wide"}` + "\n" +
					`{"true_positive": true, "scope_predicted": "wide"}` + "\n" +
					`{"false_negative": true, "scope_predicted": "narrow"}` + "\n"
			}),
		st("falsy flags do not count", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"true_positive": 0, "false_positive": "", "false_negative": [], "true_negative": null, "scope_predicted": "medium"}` + "\n"
		}),
		st("the breakdown keeps arrival order", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"scope_predicted": "wide", "actual_status": "stuck"}` + "\n" +
				`{"scope_predicted": "narrow", "actual_status": "done"}` + "\n" +
				`{"scope_predicted": "wide", "actual_status": "running"}` + "\n"
		}),
		st("a missing scope_predicted is unknown", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"actual_status": "stuck"}` + "\n" + `{}` + "\n"
		}),
		st("numeric and boolean scopes share one bucket", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"scope_predicted": 1, "actual_status": "stuck"}` + "\n" +
				`{"scope_predicted": 1.0, "actual_status": "done"}` + "\n" +
				`{"scope_predicted": true, "actual_status": "stuck"}` + "\n" +
				`{"scope_predicted": "1", "actual_status": "done"}` + "\n"
		}),
		st("a precision that repeats is rounded to three places too",
			func(s *pfSpec) {
				s.WriteFile = true
				s.Body = `{"true_positive": true, "scope_predicted": "wide"}` + "\n" +
					`{"true_positive": true, "scope_predicted": "wide"}` + "\n" +
					`{"false_positive": true, "scope_predicted": "wide"}` + "\n"
			}),
		st("a string scope does not collide with a numeric one",
			func(s *pfSpec) {
				s.WriteFile = true
				s.Body = `{"scope_predicted": "f:1"}` + "\n" +
					`{"scope_predicted": 1}` + "\n" +
					`{"scope_predicted": "n:"}` + "\n" +
					`{"scope_predicted": null}` + "\n"
			}),
		st("a line indented with a non-breaking space still parses",
			func(s *pfSpec) {
				s.WriteFile = true
				s.Body = `{"scope_predicted": "wide", "actual_status": "stuck"}` + "\n" +
					"\u00a0" + `{"scope_predicted": "narrow"}` + "\n"
			}),
		st("a lone continuation byte is not a start byte", func(s *pfSpec) {
			s.WriteBytes = []int{0x7b, 0x7d, 0x0a, 0x80, 0x0a}
		}),
		st("a null scope is its own bucket", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"scope_predicted": null}` + "\n" +
				`{"scope_predicted": 0}` + "\n"
		}),
		st("an entry that is not an object has no .get", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"scope_predicted": "wide"}` + "\n" + `[1, 2]` + "\n"
		}),
		st("a scalar entry has no .get either", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = "3\n"
		}),
		st("an unhashable scope cannot be a dict key", func(s *pfSpec) {
			s.WriteFile = true
			s.Body = `{"scope_predicted": ["wide"]}` + "\n"
		}),
		st("an empty cal_path is the current directory", func(s *pfSpec) {
			s.CalPathKind = "empty"
		}),
		st("a directory cannot be read as text", func(s *pfSpec) {
			s.CalPathKind = "dir"
			s.WriteDir = true
		}),
		st("a cal_path that is not a path at all", func(s *pfSpec) {
			s.CalPathKind = "int"
		}),
		st("a stray byte does not decode", func(s *pfSpec) {
			s.WriteBytes = []int{0x7b, 0x7d, 0x0a, 0xff, 0x0a}
		}),
		st("a truncated sequence at the end of the file", func(s *pfSpec) {
			s.WriteBytes = []int{0x7b, 0x7d, 0x0a, 0xe2, 0x82}
		}),
		st("a bad continuation byte names the start of the sequence",
			func(s *pfSpec) {
				s.WriteBytes = []int{0xe2, 0x28, 0xa1, 0x0a}
			}),
	}
	// Every stats scenario writes fixtures, and both engines must see the
	// SAME tree — the probe runs all of them before the Go side runs any,
	// so they cannot share a filename.
	for i := range rows {
		rows[i].Dir = fmt.Sprintf("case%02d", i)
	}
	return rows
}

func TestPreflightMatchesCPython(t *testing.T) {
	root := t.TempDir()
	scs := preflightScenarios()
	py := runPreflightProbe(t, root, scs)
	if len(py) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(py), len(scs))
	}
	for i, sc := range scs {
		t.Run(sc.Name, func(t *testing.T) {
			canonImportPath(py[i])
			canonUnportableMessage(py[i])
			got := goPreflightRecord(t, root, sc)
			canonImportPath(got)
			canonUnportableMessage(got)
			a, _ := json.MarshalIndent(canonPreflight(got), "", " ")
			b, _ := json.MarshalIndent(py[i], "", " ")
			if string(a) != string(b) {
				t.Errorf("record differs\n go: %s\n py: %s", a, b)
			}
		})
	}
}

// canonImportPath drops the module FILE CPython appends to an
// ImportError — "cannot import name 'X' from 'llm' (/…/src/llm.py)".
//
// The suffix is the path the probe was pointed at, not a decision either
// engine makes, and it differs on every machine. It appears inside a
// scope_note as well as in a log line, so the whole record is rewritten
// rather than one field.
func canonImportPath(rec map[string]any) {
	walkStrings(rec, func(s string) string {
		i := strings.Index(s, dropImportMsg)
		if i < 0 {
			return s
		}
		rest := s[i+len(dropImportMsg):]
		if !strings.HasPrefix(rest, " (") {
			return s
		}
		j := strings.Index(rest, ")")
		if j < 0 {
			return s
		}
		return s[:i+len(dropImportMsg)] + rest[j+1:]
	})
}

// canonUnportableMessage blanks the two exception messages this port does
// not spell out.
//
// `Path(123)` and the codec's own wording are CPython-version text, not
// behaviour: the class is what callers branch on and the class is still
// compared. Everything else — the AttributeError sentences, the OSError
// errno lines — is reproduced exactly and would still fail here.
func canonUnportableMessage(rec map[string]any) {
	msg, _ := rec["msg"].(string)
	if strings.HasPrefix(msg, "argument should be a str") {
		rec["msg"] = "<TypeError from Path()>"
	}
	walkStrings(rec, func(s string) string {
		return cpythonJSONError.ReplaceAllString(s, "${1}<JSONDecodeError>)")
	})
}

// cpythonJSONError matches the one sentence this port does not reproduce:
// json.JSONDecodeError's scanner position. Nothing else is touched — the
// AttributeError and TypeError sentences from the same handler are
// spelled out exactly and a divergence in any of them still fails here.
var cpythonJSONError = regexp.MustCompile(
	`^(pre_flight: reviewer response unparseable \().*: ` +
		`line \d+ column \d+ \(char \d+\)\)$`)

func walkStrings(v any, f func(string) string) {
	switch t := v.(type) {
	case map[string]any:
		for k, x := range t {
			if s, ok := x.(string); ok {
				t[k] = f(s)
			} else {
				walkStrings(x, f)
			}
		}
	case []any:
		for i, x := range t {
			if s, ok := x.(string); ok {
				t[i] = f(s)
			} else {
				walkStrings(x, f)
			}
		}
	}
}

// canonPreflight round-trips the Go record through the same decoder the
// probe's output goes through, so the comparison is of DECODED values on
// both sides rather than of Go's types against JSON's.
func canonPreflight(rec map[string]any) map[string]any {
	blob, err := json.Marshal(rec)
	if err != nil {
		panic(err)
	}
	var out map[string]any
	if err := json.Unmarshal(blob, &out); err != nil {
		panic(err)
	}
	return out
}

func runPreflightProbe(t *testing.T, root string, scs []pfSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	specPath := filepath.Join(dir, "preflight-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("preflight_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// Workspace is named because the probe imports pre_flight, which
	// imports llm and config on the way past — "read-only" is not a
	// judgement this file gets to make about modules it does not own.
	out := pyprobe.Probe{Marker: "pre_flight.py", Workspace: t.TempDir()}.
		Run(t, string(src), pyprobe.SrcDir(t, "pre_flight.py"), specPath, root)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

// TestTheReviewListsAreEmptyNotNil pins what the differential cannot
// see: Python's dataclass defaults are `field(default_factory=list)`, so
// flags and milestone_step_indices are ALWAYS lists there. A nil slice
// ranges zero times and renders identically through the probe's pv(), but
// it marshals as `null` — and a Review crosses this package's boundary to
// callers that will serialise it.
func TestTheReviewListsAreEmptyNotNil(t *testing.T) {
	lg := &pfLog{lines: []any{}}
	check := func(what string, r Review) {
		t.Helper()
		if r.Flags == nil {
			t.Errorf("%s: Flags is nil, want an empty slice", what)
		}
		if r.MilestoneStepIndices == nil {
			t.Errorf("%s: MilestoneStepIndices is nil, want an empty slice",
				what)
		}
	}
	rev, err := ParseReview(`{"scope": "narrow"}`, lg)
	if err != nil || rev == nil {
		t.Fatalf("ParseReview: %v", err)
	}
	check("a parsed review with no lists", *rev)
	check("the empty-plan answer",
		ReviewPlan("g", nil, false, ReviewDeps{Log: lg}))
	check("the no-reviewer heuristic", ReviewPlan("g", []string{"a"}, false,
		ReviewDeps{
			ImportLLMMessage: func() error { return nil },
			NextReviewer: func() (Reviewer, bool, error) {
				return Reviewer{}, false, nil
			},
			Log: lg, Stderr: &bytes.Buffer{},
		}))
	check("the failed-review heuristic", ReviewPlan("g", []string{"a"}, false,
		ReviewDeps{
			ImportLLMMessage: func() error { return errors.New("no llm") },
			Log:              lg, Stderr: &bytes.Buffer{},
		}))
}

func TestPreflightScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, sc := range preflightScenarios() {
		if seen[sc.Name] {
			t.Errorf("duplicate scenario name %q — a battery row that "+
				"names it would be ambiguous", sc.Name)
		}
		seen[sc.Name] = true
	}
}

// TestTheCriticPromptStillNamesEveryDimension is a Go-only guard on the
// one thing the differential cannot catch: both engines losing the same
// text. reviewSystem was transplanted byte-for-byte, so a mutation that
// edits the Python and the Go alike still compares equal.
func TestTheCriticPromptStillNamesEveryDimension(t *testing.T) {
	for _, want := range []string{
		"1. SCOPE:", "2. ASSUMPTIONS:", "3. MILESTONE CANDIDATES:",
		"4. UNKNOWN UNKNOWNS:", "5. CLASS COVERAGE:",
		`"scope": "narrow" | "medium" | "wide"`,
		`"assumptions":`, `"milestone_candidates":`,
		`"unknown_unknowns":`, `"class_gaps":`,
	} {
		if !strings.Contains(reviewSystem, want) {
			t.Errorf("the critic prompt no longer contains %q", want)
		}
	}
	// The prompt asks for five dimensions and the parser reads four
	// lists. Both counts are stated in the prose above; a prompt that
	// grew a sixth dimension with no parser arm is the failure this
	// pins.
	if n := strings.Count(reviewSystem, "\n"); n != 43 {
		t.Errorf("the critic prompt has %d newlines, want 43 — if the "+
			"prompt genuinely changed, change it in pre_flight.py first "+
			"and re-transplant", n)
	}
	if !strings.HasPrefix(reviewSystem, "You are a plan critic.") ||
		strings.HasSuffix(reviewSystem, "\n") {
		t.Error("the prompt is textwrap.dedent(...).strip(): no leading " +
			"indentation and no trailing newline")
	}
}

// TestTheDeadAdapterParameterIsNotResurrected pins the one thing the port
// deliberately dropped. review_plan's Python signature takes `adapter`
// and its body names it zero times; the differential passes None for it
// and therefore cannot tell whether it matters.
func TestTheDeadAdapterParameterIsNotResurrected(t *testing.T) {
	src, err := os.ReadFile(filepath.Join(
		pyprobe.SrcDir(t, "pre_flight.py"), "pre_flight.py"))
	if err != nil {
		t.Fatal(err)
	}
	body := string(src)
	start := strings.Index(body, "def review_plan(")
	if start < 0 {
		t.Fatal("review_plan is gone from pre_flight.py")
	}
	end := strings.Index(body[start:], "\ndef ")
	if end < 0 {
		t.Fatal("could not find the end of review_plan")
	}
	fn := body[start : start+end]
	sig := fn[:strings.Index(fn, ")")]
	rest := fn[strings.Index(fn, ")"):]
	if !strings.Contains(sig, "adapter") {
		t.Skip("the parameter is gone from Python too; drop this test")
	}
	if strings.Contains(rest, "adapter") {
		t.Error("review_plan's body now uses `adapter` — the Go port " +
			"dropped the parameter on the grounds that it was dead, and " +
			"it no longer is")
	}
}

// TestTheUnportedHalvesStillExistUpstream keeps the package doc honest.
//
// It names two functions as deliberately not ported. If either one is
// deleted or renamed in Python, that note becomes a claim about something
// that no longer exists, and the next reader has no way to tell a
// deliberate omission from a stale sentence.
func TestTheUnportedHalvesStillExistUpstream(t *testing.T) {
	src, err := os.ReadFile(filepath.Join(
		pyprobe.SrcDir(t, "pre_flight.py"), "pre_flight.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"_build_reviewers", "_preflight_stats_main"} {
		if !strings.Contains(string(src), "def "+name+"(") {
			t.Errorf("pre_flight.py no longer defines %s, which this "+
				"package's doc comment says was left unported", name)
		}
	}
}
