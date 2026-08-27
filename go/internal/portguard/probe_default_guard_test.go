// Package portguard holds checks that span the whole module — invariants
// about how the port is BUILT rather than about what any one package does.
//
// A check lands here when a review lens has fired twice on the same
// surface. The standing rule (2026-08-27): lenses get CLOSED, not
// re-found. A pattern that recurs is a check waiting to be written.
package portguard

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// TestProbeReadsCannotMaskAnOmittedField closes L6 — "a fixture that
// agrees with itself" — at the one seam where it kept recurring.
//
// # The mechanism
//
// A differential's scenario struct is the WIRE FORMAT to a CPython probe.
// Go marshals it, Python reads it back. `omitempty` deletes a field whose
// value is the Go zero, and the probe then supplies its own default for
// the missing key. When that default is not the Go zero value, the fixture
// cannot express the zero AT ALL:
//
//	Summary string `json:"summary,omitempty"`   // Go: ""
//	b.get("summary", "SUMMARY")                 // Python: "SUMMARY"
//
// A scenario named "empty summary" then tests the non-empty one, and it
// passes, because both sides quietly agree on a value neither was given.
// That shipped once. The other half shipped in the same file: a field
// omitted by `omitempty` and read as `sc["goals"]`, which raises KeyError
// for exactly the empty-list scenarios that were the point.
//
// # The rule
//
// In any directory holding a CPython probe, a `_test.go` field tagged
// `omitempty` may not have its key read by that probe as either
//
//	<var>["key"]                — KeyError when the field is omitted
//	<var>.get("key", <truthy>)  — a default that is not the Go zero
//
// A subscript GUARDED by a truthiness test on the same key is fine; that
// is the standard `if beh.get("x"): raise Boom(beh["x"])` shape, where the
// absent case is the tested one.
//
// # The fix, when this fires
//
// Not an allowlist row by reflex. Fill the default in the SCENARIO BUILDER
// — one place, on the Go side, where both languages then read the same
// value — and delete the default from the probe. A default that exists in
// two languages is a divergence waiting for someone to edit one of them.
var probeDefaultAllowlist = map[string]string{
	// "internal/foo/foo_diff_test.go:sleep": "reason this default cannot lie",
}

// falsy is every literal a probe may safely default to: each marshals from,
// and unmarshals to, a Go zero value.
var falsy = map[string]bool{
	"None": true, "''": true, `""`: true, "0": true, "0.0": true,
	"False": true, "[]": true, "{}": true, "()": true,
}

// scenarioVar is the receiver of a scenario read. Records built BY the
// probe reuse the same key names, so an unbound match would flag every
// `rec["status"] = ...` in the file; binding to the names a scenario
// actually travels under is what keeps the check specific.
const scenarioVar = `\b(?:sc|scen|scenario|case|spec|beh|fx)`

func modRoot(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Dir(p) // .../go
}

// omitemptyFields returns (json key, line) for every struct field in a Go
// file whose tag carries omitempty.
func omitemptyFields(t *testing.T, path string) map[string]int {
	t.Helper()
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, path, nil, 0)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	out := map[string]int{}
	ast.Inspect(f, func(n ast.Node) bool {
		st, ok := n.(*ast.StructType)
		if !ok {
			return true
		}
		for _, fld := range st.Fields.List {
			if fld.Tag == nil {
				continue
			}
			tag := strings.Trim(fld.Tag.Value, "`")
			m := regexp.MustCompile(`json:"([^",]+),([^"]*)"`).FindStringSubmatch(tag)
			if m == nil || !strings.Contains(","+m[2]+",", ",omitempty,") {
				continue
			}
			out[m[1]] = fset.Position(fld.Pos()).Line
		}
		return true
	})
	return out
}

// probeSourceFor returns the probe text a given _test.go file is checked
// against: the probes it NAMES, or every probe in the directory when it
// names none.
//
// The pairing is not cosmetic. internal/loopfinalize holds three probes
// and three differentials; concatenating them made every omitempty field
// in one spec struct answerable by a subscript in an unrelated probe, and
// the guard reported two fields that no probe reading them could ever
// see. A check whose blast radius is the DIRECTORY reports findings the
// code does not have, which is the fastest way to teach a reader to
// allowlist it (L53).
//
// The fallback is deliberate: a differential that builds its probe path
// some other way still gets checked, conservatively, against everything.
func probeSourceFor(testFile string, probes map[string]string) (string, error) {
	b, err := os.ReadFile(testFile)
	if err != nil {
		return "", err
	}
	test := string(b)
	var named []string
	for name, body := range probes {
		if strings.Contains(test, name) {
			named = append(named, body)
		}
	}
	if len(named) == 0 {
		for _, body := range probes {
			named = append(named, body)
		}
	}
	return strings.Join(named, "\n"), nil
}

func TestProbeReadsCannotMaskAnOmittedField(t *testing.T) {
	root := modRoot(t)
	var failures []string

	err := filepath.WalkDir(filepath.Join(root, "internal"),
		func(path string, d os.DirEntry, err error) error {
			if err != nil || !d.IsDir() {
				return err
			}
			ents, err := os.ReadDir(path)
			if err != nil {
				return err
			}
			probes := map[string]string{}
			var tests []string
			for _, e := range ents {
				switch {
				case strings.HasSuffix(e.Name(), ".py.tpl"):
					b, err := os.ReadFile(filepath.Join(path, e.Name()))
					if err != nil {
						return err
					}
					probes[e.Name()] = string(b)
				case strings.HasSuffix(e.Name(), "_test.go"):
					tests = append(tests, filepath.Join(path, e.Name()))
				}
			}
			if len(probes) == 0 {
				return nil
			}
			for _, tf := range tests {
				rel, _ := filepath.Rel(root, tf)
				src, err := probeSourceFor(tf, probes)
				if err != nil {
					return err
				}
				for key, line := range omitemptyFields(t, tf) {
					q := regexp.QuoteMeta(key)
					if probeDefaultAllowlist[rel+":"+key] != "" {
						continue
					}
					guarded := regexp.MustCompile(
						scenarioVar + `\.get\(\s*["']` + q + `["']\s*\)`).MatchString(src)
					if !guarded && regexp.MustCompile(
						scenarioVar+`\[\s*["']`+q+`["']\s*\]`).MatchString(src) {
						failures = append(failures, fmt.Sprintf(
							"%s:%d  %q is omitempty and the probe subscripts it: "+
								"an omitted field is a KeyError, not a zero value",
							rel, line, key))
					}
					for _, m := range regexp.MustCompile(
						scenarioVar+`\.get\(\s*["']`+q+`["']\s*,\s*([^)]+)\)`).
						FindAllStringSubmatch(src, -1) {
						if d := strings.TrimSpace(m[1]); !falsy[d] {
							failures = append(failures, fmt.Sprintf(
								"%s:%d  %q is omitempty and the probe defaults it to %s: "+
									"a scenario setting the Go zero gets %s on BOTH sides, "+
									"so the zero case is untestable",
								rel, line, key, d, d))
						}
					}
				}
			}
			return nil
		})
	if err != nil {
		t.Fatal(err)
	}

	if len(failures) > 0 {
		t.Errorf("%d probe default(s) can mask an omitted scenario field "+
			"(L6). Fill the default in the scenario BUILDER and delete it "+
			"from the probe:\n  %s",
			len(failures), strings.Join(failures, "\n  "))
	}
}
