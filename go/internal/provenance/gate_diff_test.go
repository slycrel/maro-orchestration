package provenance

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// GateEnabled's Python counterpart takes no arguments: it reads
// `knowledge.provenance_gate_enabled` out of the merged YAML config and
// normalizes whatever comes back. The port split that in two — config
// reading stayed in internal/config, normalization came here — so the
// only differential that means anything is the COMPOSITION the one
// production caller performs (internal/pack/import.go:273):
//
//	provenance.GateEnabled(config.GetRaw(cfg, "knowledge.provenance_gate_enabled", true))
//
// against the real function over the real file. The subject is therefore
// two things at once, and both are load-bearing: what PyYAML and yaml.v3
// each make of the scalar, and what each side's normalizer does with the
// result. `off` is the case that shows why — PyYAML resolves it to the
// boolean False (YAML 1.1), yaml.v3 leaves it the string "off" (YAML
// 1.2), and the two runtimes only agree because GateEnabled carries a
// string denylist that happens to spell it. That is agreement by
// construction, and it is pinned below as such rather than assumed.
//
// The probe writes nothing. It sets Workspace anyway, at a directory the
// test owns: without it CPython's workspace tier resolves to
// ~/.maro/workspace/config.yml and the operator's own killswitch setting
// becomes a silent input to the comparison.
const pyGateSrc = `
import json
import config
import lesson_provenance as lp

raw = config.get("knowledge.provenance_gate_enabled", True)
print(json.dumps({
    "gate": lp.provenance_gate_enabled(),
    "type": type(raw).__name__,
}))
`

// goKind names a decoded YAML value the way Python's type().__name__
// does, so the two type answers can be compared at all.
func goKind(v any) string {
	switch v.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case string:
		return "str"
	case int, int64, uint64:
		return "int"
	case float64:
		return "float"
	case []any:
		return "list"
	case map[string]any:
		return "dict"
	default:
		return fmt.Sprintf("%T", v)
	}
}

type gateCase struct {
	name string
	// body is the whole user-tier config.yml. A nil body writes no file.
	body *string
	// pyGate and pyType are stated before either implementation runs.
	pyGate bool
	pyType string
	// gateDiff/typeDiff are the port's answers where it disagrees, with a
	// reason. Empty means the two agree.
	gateDiff *bool
	typeDiff string
	why      string
}

func gateCases() []gateCase {
	body := func(s string) *string { return &s }
	key := func(v string) *string {
		return body("knowledge:\n  provenance_gate_enabled: " + v + "\n")
	}
	yes := true
	return []gateCase{
		// --- the key is absent: the caller's own default decides ----------
		{name: "no config file at all", body: nil, pyGate: true, pyType: "bool"},
		{name: "an empty config file", body: body(""), pyGate: true, pyType: "bool"},
		{name: "a knowledge block without the key", pyGate: true, pyType: "bool",
			body: body("knowledge:\n  other: 1\n")},
		// `knowledge` resolving to a scalar is not a dict to walk into;
		// both sides fall back rather than raising.
		{name: "knowledge is a scalar", pyGate: true, pyType: "bool",
			body: body("knowledge: hello\n")},
		{name: "knowledge is a list", pyGate: true, pyType: "bool",
			body: body("knowledge:\n  - a\n")},

		// --- real booleans ------------------------------------------------
		{name: "true", body: key("true"), pyGate: true, pyType: "bool"},
		{name: "false", body: key("false"), pyGate: false, pyType: "bool"},
		{name: "True", body: key("True"), pyGate: true, pyType: "bool"},
		{name: "FALSE", body: key("FALSE"), pyGate: false, pyType: "bool"},

		// --- YAML 1.1 booleans PyYAML resolves and yaml.v3 does not -------
		// Same gate answer, different type on the way there. The string
		// branch of GateEnabled is what closes the gap, and deleting it as
		// dead code would flip every one of these.
		{name: "yes", body: key("yes"), pyGate: true, pyType: "bool",
			typeDiff: "str", why: whyYAMLVersion},
		{name: "no", body: key("no"), pyGate: false, pyType: "bool",
			typeDiff: "str", why: whyYAMLVersion},
		{name: "on", body: key("on"), pyGate: true, pyType: "bool",
			typeDiff: "str", why: whyYAMLVersion},
		{name: "off", body: key("off"), pyGate: false, pyType: "bool",
			typeDiff: "str", why: whyYAMLVersion},
		{name: "Off", body: key("Off"), pyGate: false, pyType: "bool",
			typeDiff: "str", why: whyYAMLVersion},
		// ...and the single letters PyYAML does NOT resolve, which is why
		// the rows above cannot be generalized to "y/n words".
		{name: "y", body: key("y"), pyGate: true, pyType: "str"},
		{name: "n", body: key("n"), pyGate: true, pyType: "str"},

		// --- quoted strings: the case the Python docstring exists for -----
		{name: "quoted false", body: key(`"false"`), pyGate: false, pyType: "str"},
		{name: "quoted FALSE", body: key(`"FALSE"`), pyGate: false, pyType: "str"},
		{name: "quoted OFF with spaces", body: key(`" OFF "`), pyGate: false, pyType: "str"},
		{name: "quoted zero", body: key(`"0"`), pyGate: false, pyType: "str"},
		{name: "quoted no", body: key(`"no"`), pyGate: false, pyType: "str"},
		{name: "quoted true", body: key(`"true"`), pyGate: true, pyType: "str"},
		{name: "an unrecognized string is truthy", body: key(`"anything"`),
			pyGate: true, pyType: "str"},
		// The empty string is FALSY to Python's bool() but this function
		// never calls bool() on a string — it is not in the denylist, so
		// the gate stays ON. Both sides, deliberately.
		{name: "the empty string keeps the gate on", body: key(`""`),
			pyGate: true, pyType: "str"},
		{name: "a whitespace string keeps the gate on", body: key(`"   "`),
			pyGate: true, pyType: "str"},

		// --- what counts as surrounding whitespace to strip ---------------
		// str.strip() and strings.TrimSpace agree on the Unicode spaces...
		{name: "false padded with U+00A0", body: key(`"` + nbsp + `false` + nbsp + `"`),
			pyGate: false, pyType: "str"},
		{name: "false prefixed with U+000B", body: key(`"\x0bfalse"`),
			pyGate: false, pyType: "str"},
		{name: "false prefixed with U+0085", body: key(`"\x85false"`),
			pyGate: false, pyType: "str"},
		{name: "false prefixed with U+2003", body: key(`"` + emsp + `false"`),
			pyGate: false, pyType: "str"},
		{name: "false with a trailing newline", body: key(`"false\n"`),
			pyGate: false, pyType: "str"},
		// ...and disagree on the C0 SEPARATORS, which Python's str.isspace
		// calls whitespace and unicode.IsSpace does not.
		{name: "false prefixed with U+001C", body: key(`"\x1cfalse"`),
			pyGate: false, pyType: "str", gateDiff: &yes, why: whyStripClass},

		// --- numbers ------------------------------------------------------
		{name: "int zero", body: key("0"), pyGate: false, pyType: "int"},
		{name: "int one", body: key("1"), pyGate: true, pyType: "int"},
		{name: "int minus one", body: key("-1"), pyGate: true, pyType: "int"},
		{name: "float zero", body: key("0.0"), pyGate: false, pyType: "float"},
		{name: "float half", body: key("0.5"), pyGate: true, pyType: "float"},

		// --- null ---------------------------------------------------------
		// An explicit null is PRESENT, so neither side takes the default:
		// Python's bool(None) turns the gate OFF and GetRaw + the nil arm
		// of GateEnabled agree. (internal/pack pins the OTHER composition,
		// Get[any], where the nil interface fails the type assertion and
		// the default wins — that path is not the one production takes.)
		{name: "an explicit tilde null", body: key("~"), pyGate: false,
			pyType: "NoneType"},
		{name: "the word null", body: key("null"), pyGate: false,
			pyType: "NoneType"},
		{name: "an empty value", pyGate: false, pyType: "NoneType",
			body: body("knowledge:\n  provenance_gate_enabled:\n")},

		// --- containers ---------------------------------------------------
		// Python's fallthrough is bool(val), and an empty container is
		// FALSY: an operator who writes `provenance_gate_enabled: []`
		// turns the gate off in CPython and leaves it on in the port.
		{name: "an empty list", body: key("[]"), pyGate: false, pyType: "list",
			gateDiff: &yes, why: whyEmptyContainer},
		{name: "a non-empty list", body: key("[1]"), pyGate: true, pyType: "list"},
		{name: "an empty map", body: key("{}"), pyGate: false, pyType: "dict",
			gateDiff: &yes, why: whyEmptyContainer},
		{name: "a non-empty map", body: key("{a: 1}"), pyGate: true, pyType: "dict"},
	}
}

const (
	whyYAMLVersion = "PyYAML resolves the YAML 1.1 boolean words, yaml.v3 " +
		"(YAML 1.2) leaves them strings; GateEnabled's string denylist is " +
		"what makes the verdicts agree anyway"
	whyStripClass = "Python's str.strip() strips the C0 separators U+001C-1F, " +
		"Go's strings.TrimSpace (unicode.IsSpace) does not"
	whyEmptyContainer = "Python's fallthrough is bool(val) and an empty " +
		"list/dict is falsy; GateEnabled's default arm returns true"
)

func TestGateEnabledMatchesCPython(t *testing.T) {
	for _, c := range gateCases() {
		c := c
		t.Run(c.name, func(t *testing.T) {
			if (c.gateDiff != nil || c.typeDiff != "") && c.why == "" {
				t.Fatalf("%q pins a divergence with no stated reason", c.name)
			}
			if c.gateDiff != nil && *c.gateDiff == c.pyGate {
				t.Fatalf("%q pins a gate divergence naming the same answer "+
					"on both sides", c.name)
			}
			if c.typeDiff != "" && c.typeDiff == c.pyType {
				t.Fatalf("%q pins a type divergence naming the same type "+
					"on both sides", c.name)
			}

			userDir := t.TempDir()
			ws := t.TempDir()
			if c.body != nil {
				if err := os.WriteFile(filepath.Join(userDir, "config.yml"),
					[]byte(*c.body), 0o644); err != nil {
					t.Fatal(err)
				}
			}

			var py struct {
				Gate bool   `json:"gate"`
				Type string `json:"type"`
			}
			pyprobe.Probe{
				Marker:    "lesson_provenance.py",
				UserDir:   userDir,
				Workspace: ws,
			}.RunJSON(t, pyGateSrc, &py)

			// The premise first: a fixture whose CPython answer is not
			// what it claims is measuring a different branch than it says.
			if py.Gate != c.pyGate || py.Type != c.pyType {
				t.Fatalf("CPython answered gate=%v type=%s, but the fixture "+
					"claims gate=%v type=%s — the fixture does not exercise "+
					"what it names\nconfig.yml: %q",
					py.Gate, py.Type, c.pyGate, c.pyType, deref(c.body))
			}

			t.Setenv("MARO_USER_DIR", userDir)
			t.Setenv("MARO_WORKSPACE", ws)
			cfg, warnings := config.Load()
			if len(warnings) > 0 {
				t.Fatalf("the port could not read the fixture config: %v", warnings)
			}
			raw := config.GetRaw(cfg, "knowledge.provenance_gate_enabled", true)

			wantType := py.Type
			if c.typeDiff != "" {
				wantType = c.typeDiff
			}
			if k := goKind(raw); k != wantType {
				t.Errorf("the decoded value is %s in the port and %s in "+
					"CPython (%s)\nconfig.yml: %q", k, py.Type,
					pinNote(c.typeDiff, c.why), deref(c.body))
			}

			wantGate := py.Gate
			if c.gateDiff != nil {
				wantGate = *c.gateDiff
			}
			got := GateEnabled(raw)
			if got == wantGate {
				return
			}
			if c.gateDiff == nil {
				t.Fatalf("the killswitch disagrees with CPython: cpython=%v "+
					"go=%v\nconfig.yml: %q", py.Gate, got, deref(c.body))
			}
			t.Fatalf("a PINNED divergence changed: the port answered %v where "+
				"this row pins %v (CPython says %v).\nreason on record: %s\n"+
				"If the port was fixed, drop gateDiff/why from this row.\n"+
				"config.yml: %q", got, *c.gateDiff, py.Gate, c.why, deref(c.body))
		})
	}
}

func pinNote(typeDiff, why string) string {
	if typeDiff == "" {
		return "not a pinned divergence"
	}
	return "pinned: " + why
}

func deref(s *string) string {
	if s == nil {
		return "<no file>"
	}
	return *s
}
