package config

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// GetBool is the first place in this port where the two runtimes read the
// SAME FILE with DIFFERENT YAML libraries: PyYAML is YAML 1.1, where bare
// `on`/`off`/`yes`/`no` are booleans, and gopkg.in/yaml.v3 is YAML 1.2,
// where they are plain strings. A hand-written expectation cannot settle
// that — only running both can. So this drives the real config.get_bool
// over a corpus of real config.yml files and compares the value AND the
// warning text.
//
// The probe only ever reads and writes under t.TempDir(); the resolved
// path is asserted before anything is written, per the standing rule that
// cost a live ledger once (feedback_live_store_probes, 2026-08-16).

func srcDirConfig(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

const pyGetBoolSnippet = `
import json, logging, os, sys

root = sys.argv[2]
cases = json.loads(sys.argv[1])

class Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []
    def emit(self, record):
        self.lines.append(record.getMessage())

out = []
for i, c in enumerate(cases):
    d = os.path.join(root, "case%d" % i)
    os.makedirs(os.path.join(d, "ws"), exist_ok=True)
    # The WORKSPACE tier carries the document; the user tier stays empty so
    # the box's real ~/.maro/config.yml can never leak in.
    with open(os.path.join(d, "ws", "config.yml"), "w") as fh:
        fh.write(c["yaml"])
    os.environ["MARO_USER_DIR"] = d
    os.environ["MARO_WORKSPACE"] = os.path.join(d, "ws")
    for m in list(sys.modules):
        if m == "config":
            del sys.modules[m]
    import config
    log = logging.getLogger("maro.config")
    grab = Grab()
    log.addHandler(grab)
    prev, log.propagate = log.propagate, False
    try:
        val = config.get_bool(c["key"], c["default"])
    finally:
        log.removeHandler(grab)
        log.propagate = prev
    out.append([val, grab.lines[0] if grab.lines else None])
print(json.dumps(out))
`

type boolCase struct {
	Name string `json:"-"`
	YAML string `json:"yaml"`
	Key  string `json:"key"`
	Def  bool   `json:"default"`
}

// boolCorpus is a package var so the anti-vacuity guard reads the SAME
// list the differential does.
var boolCorpus = []boolCase{
	// Native YAML booleans.
	{"a true bool", "flag: true\n", "flag", false},
	{"a false bool", "flag: false\n", "flag", true},
	{"True capitalised", "flag: True\n", "flag", false},

	// THE 1.1-vs-1.2 SEAM. Bare, unquoted, and a boolean to PyYAML but a
	// string to yaml.v3. Both must still land on the same answer.
	{"bare on", "flag: on\n", "flag", false},
	{"bare off", "flag: off\n", "flag", true},
	{"bare yes", "flag: yes\n", "flag", false},
	{"bare no", "flag: no\n", "flag", true},
	{"bare On capitalised", "flag: On\n", "flag", false},
	{"bare OFF upper", "flag: OFF\n", "flag", true},
	// ...and the 1.1 words that are NOT in either string set, where the
	// coincidence runs out and the two libraries can genuinely disagree.
	{"bare y", "flag: y\n", "flag", false},
	{"bare n", "flag: n\n", "flag", true},

	// Quoted strings — the whole reason get_bool exists, since
	// bool("false") is True.
	{"quoted false", "flag: \"false\"\n", "flag", true},
	{"quoted true", "flag: \"true\"\n", "flag", false},
	{"quoted zero", "flag: \"0\"\n", "flag", true},
	{"quoted one", "flag: \"1\"\n", "flag", false},
	{"quoted empty is FALSY, not unrecognized", "flag: \"\"\n", "flag", true},
	{"quoted mixed case", "flag: \"TrUe\"\n", "flag", false},
	{"quoted with padding", "flag: \"  TRUE  \"\n", "flag", false},
	{"quoted nonsense warns", "flag: \"maybe\"\n", "flag", true},
	{"quoted nonsense warns, other default", "flag: \"maybe\"\n", "flag", false},
	// Python's str.lower() is not ASCII lowercasing. A dotted capital I
	// lowers to "i" + U+0307, which is in neither set, so both runtimes
	// must warn — and the warning carries repr() of the ORIGINAL value.
	{"a dotted capital I", "flag: \"\u0130RUE\"\n", "flag", true},
	{"a non-ASCII value", "flag: \"d\u00fcnya\"\n", "flag", false},

	// Numbers: bool(val) for int and float alike.
	{"int zero", "flag: 0\n", "flag", true},
	{"int one", "flag: 1\n", "flag", false},
	{"int two", "flag: 2\n", "flag", false},
	{"int negative", "flag: -1\n", "flag", false},
	{"float zero", "flag: 0.0\n", "flag", true},
	{"float negative zero", "flag: -0.0\n", "flag", true},
	{"float half", "flag: 0.5\n", "flag", false},
	{"a nan", "flag: .nan\n", "flag", false},
	{"an inf", "flag: .inf\n", "flag", false},
	// yaml.v3 types this as float64 while PyYAML keeps an unbounded int;
	// both are nonzero so get_bool agrees, but the types do not.
	{"a huge int", "flag: 99999999999999999999999\n", "flag", false},
	// ...and this one lands in yaml.v3's uint64 arm specifically, which
	// exists only because Go has a width and Python does not. Without it
	// the arm is unreachable and a mutant that deletes it survives.
	{"an integer just past MaxInt64", "flag: 9223372036854775808\n", "flag", false},

	// str.strip() removes U+001C..U+001F and Go's strings.TrimSpace does
	// not, so these separate pytext.Strip from the stdlib. The second is
	// the sharp one: Python strips to "", which is FALSY, while a
	// TrimSpace port would see an unrecognized value and take the default
	// — opposite answers from the same file.
	{"separators around a truthy string", "flag: \"\\x1ctrue\\x1c\"\n", "flag", false},
	{"a separator-only string strips to empty", "flag: \"\\x1c\"\n", "flag", true},

	// Absent, null, and the container types — the warning lane.
	{"the key is absent", "other: 1\n", "flag", true},
	{"the key is absent, default false", "other: 1\n", "flag", false},
	{"an empty document", "", "flag", true},
	{"an explicit null", "flag: null\n", "flag", true},
	{"an explicit null, default false", "flag: null\n", "flag", false},
	{"a bare key with no value", "flag:\n", "flag", true},
	{"a list", "flag: [1, 2]\n", "flag", true},
	// A MAPPING value is deliberately absent here and pinned separately,
	// below, because the two runtimes genuinely disagree on its warning
	// text and burying that in this corpus would hide it.

	// Nested paths, present and absent.
	{"a nested true", "a:\n  b:\n    c: true\n", "a.b.c", false},
	{"a nested quoted false", "a:\n  b:\n    c: \"off\"\n", "a.b.c", true},
	{"a nested path through a scalar", "a: 1\n", "a.b.c", true},
	{"a nested path that stops short", "a:\n  b: 1\n", "a.b.c", false},
}

func pyGetBools(t *testing.T, root string, cases []boolCase) [][]*json.RawMessage {
	t.Helper()
	b, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyGetBoolSnippet, string(b), root)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDirConfig(t))
	out, err := cmd.Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("python3 is present but the get_bool probe could not run: %v\n%s",
			err, out)
	}
	var got [][]*json.RawMessage
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	return got
}

func TestGetBoolMatchesCPython(t *testing.T) {
	root := t.TempDir()
	// The standing rule: assert the resolved path before anything writes.
	if !strings.HasPrefix(root, "/tmp/") && !strings.HasPrefix(root, os.TempDir()) {
		t.Fatalf("refusing to run: probe root %q is not under a temp dir", root)
	}

	want := pyGetBools(t, root, boolCorpus)
	if len(want) != len(boolCorpus) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(boolCorpus))
	}

	for i, c := range boolCorpus {
		t.Run(c.Name, func(t *testing.T) {
			var wantVal bool
			if err := json.Unmarshal(*want[i][0], &wantVal); err != nil {
				t.Fatalf("probe value was not a bool: %v", err)
			}
			var wantWarn string
			if want[i][1] != nil {
				if err := json.Unmarshal(*want[i][1], &wantWarn); err != nil {
					t.Fatalf("probe warning was not a string: %v", err)
				}
			}

			dir := filepath.Join(root, "case"+itoaTest(i))
			t.Setenv("MARO_USER_DIR", dir)
			t.Setenv("MARO_WORKSPACE", filepath.Join(dir, "ws"))
			cfg, warnings := Load()
			if len(warnings) != 0 {
				t.Fatalf("config load warned unexpectedly: %v", warnings)
			}

			gotVal, gotWarn := GetBool(cfg, c.Key, c.Def)
			if gotVal != wantVal {
				t.Errorf("value diverges for %q (default %v)\n    go %v\n    py %v",
					c.YAML, c.Def, gotVal, wantVal)
			}
			if gotWarn != wantWarn {
				t.Errorf("warning diverges for %q (default %v)\n    go %q\n    py %q",
					c.YAML, c.Def, gotWarn, wantWarn)
			}
		})
	}
}

// A corpus that never warns proves only that both runtimes can read a
// bool, and one that never reaches a string never touches the code
// get_bool exists for. Demand all three lanes, and demand that the
// DEFAULT actually decides at least one case in each direction —
// otherwise a port that ignored the default entirely would pass.
func TestTheGetBoolCorpusReachesEveryLane(t *testing.T) {
	var warned, stringLane, defaultTrue, defaultFalse int
	for _, c := range boolCorpus {
		dir := t.TempDir()
		ws := filepath.Join(dir, "ws")
		if err := os.MkdirAll(ws, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(ws, "config.yml"), []byte(c.YAML), 0o644); err != nil {
			t.Fatal(err)
		}
		t.Setenv("MARO_USER_DIR", dir)
		t.Setenv("MARO_WORKSPACE", ws)
		cfg, _ := Load()

		if _, w := GetBool(cfg, c.Key, c.Def); w != "" {
			warned++
		}
		if v, ok := Lookup(cfg, c.Key); ok {
			if _, isStr := v.(string); isStr {
				stringLane++
			}
		}
		// Does the default decide this case? Flip it and see.
		a, _ := GetBool(cfg, c.Key, true)
		b, _ := GetBool(cfg, c.Key, false)
		if a != b {
			if c.Def {
				defaultTrue++
			} else {
				defaultFalse++
			}
		}
	}
	if warned == 0 {
		t.Error("no case warns: the unrecognized-value lane is untested")
	}
	if stringLane == 0 {
		t.Error("no case reaches a string value: get_bool's whole reason is untested")
	}
	if defaultTrue == 0 || defaultFalse == 0 {
		t.Errorf("the default never decides in both directions (true=%d false=%d): "+
			"a port that ignored the default would pass", defaultTrue, defaultFalse)
	}
}

func itoaTest(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

// KNOWN DIVERGENCE, named rather than hidden. A mapping where a boolean
// belongs is an operator error either way, and both runtimes fall back to
// the same default — but the WARNING they emit differs, because Python's
// dict has insertion order and Go's map has none. pyval.Repr refuses to
// invent one:
//
//	py: config.get_bool: unrecognized value for flag: {'a': 1} — using default False
//	go: config.get_bool: unrecognized value for flag: <unordered map: ...> — using default False
//
// This is a capability gap, not a hardening: Go cannot render what it did
// not keep. The fix is to decode the config with yaml.Node into an
// ordered pyval.Obj rather than map[string]any, which would also fix
// every other place a config value is rendered — its own slice, since
// Merge/Get/Lookup all take map[string]any today.
//
// The test asserts the CURRENT divergence in both directions on purpose.
// It fails if Go starts matching, which is the signal to delete it and
// move the case into boolCorpus.
func TestAMappingValueWarnsDifferentlyThanCPython(t *testing.T) {
	root := t.TempDir()
	cases := []boolCase{{Name: "a map", YAML: "flag:\n  a: 1\n", Key: "flag", Def: false}}
	want := pyGetBools(t, root, cases)

	var pyWarn string
	if want[0][1] == nil {
		t.Fatal("CPython did not warn on a mapping value; the divergence has moved")
	}
	if err := json.Unmarshal(*want[0][1], &pyWarn); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(pyWarn, "{'a': 1}") {
		t.Fatalf("CPython's warning no longer renders the dict: %q", pyWarn)
	}

	dir := filepath.Join(root, "case0")
	t.Setenv("MARO_USER_DIR", dir)
	t.Setenv("MARO_WORKSPACE", filepath.Join(dir, "ws"))
	cfg, _ := Load()
	got, gotWarn := GetBool(cfg, "flag", false)

	// The VALUE agrees — only the diagnostic text forks.
	var pyVal bool
	if err := json.Unmarshal(*want[0][0], &pyVal); err != nil {
		t.Fatal(err)
	}
	if got != pyVal {
		t.Fatalf("the value diverges too, which this test does not cover: go %v py %v",
			got, pyVal)
	}
	if !strings.Contains(gotWarn, "unordered map") {
		t.Fatalf("Go no longer refuses to render the map (%q) — if it now matches "+
			"CPython, delete this test and move the case into boolCorpus", gotWarn)
	}
}
