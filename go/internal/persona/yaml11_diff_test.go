package persona

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// KNOWN DIVERGENCE — YAML 1.1 vs YAML 1.2 in persona frontmatter.
//
// Python reads a persona file with PyYAML (YAML **1.1**) and this port
// reads it with gopkg.in/yaml.v3 (YAML **1.2**). The two libraries resolve
// a bare scalar to a DIFFERENT TYPE, and persona.py then runs every scalar
// field through `str()`, so the type difference becomes a VALUE difference
// in a field that gates behaviour:
//
//	frontmatter          PyYAML 1.1      yaml.v3 1.2     spec.model_tier
//	model_tier: off      bool False      string "off"    "False" / "off"
//	model_tier: on       bool True       string "on"     "True"  / "on"
//	memory_scope: no     bool False      string "no"     "False" / "no"
//	memory_scope: yes    bool True       string "yes"    "True"  / "yes"
//	model_tier: 010      int 8           int 8           "8" / "8"   AGREE
//	model_tier: 08       str "08"        float64 8       "08" / "8.0"
//	model_tier: 1e2      str "1e2"       float64 100     "1e2" / "100.0"
//	role: 2026-01-02     datetime.date   time.Time       "2026-01-02" /
//	                                                     "<unrepresentable>"
//
// This is the SAME seam internal/config pinned for config.yml, and this
// package takes the same posture for the same reason: normalizing means
// re-resolving every scalar under 1.1 rules, which is its own slice.
//
// DIRECTION, and it is not symmetric across the rows. On the bool words the
// Go answer is the RAW SPELLING and the Python answer is a capitalized
// Python bool repr — so a persona written `model_tier: off` gets tier
// "False" in CPython and "off" here. NEITHER is a valid tier, so both fall
// to compose's rank default and to llm's tier map default; the visible
// damage is confined to what an operator reads back and to what
// generate_manifest writes into a shared manifest.json. On the numeric
// rows Go is the side that loses information ("08" -> "8.0"), and on the
// date row the Go side produces a placeholder string that is not a tier at
// all.
//
// The test asserts the divergence EXISTS with measured values on both
// sides. It fails if either engine moves — including if someone later
// normalizes, which is the point: the fix decision is not this file's.
func TestYAML11FrontmatterDivergesFromYAML12(t *testing.T) {
	type row struct {
		name     string
		content  string
		field    func(*Spec) string
		wantPy   string
		wantGo   string
		agreeing bool
	}
	tier := func(s *Spec) string { return s.ModelTier }
	scope := func(s *Spec) string { return s.MemoryScope }
	role := func(s *Spec) string { return s.Role }

	rows := []row{
		{"off", "---\nmodel_tier: off\n---\nb", tier, "False", "off", false},
		{"on", "---\nmodel_tier: on\n---\nb", tier, "True", "on", false},
		{"no", "---\nmemory_scope: no\n---\nb", scope, "False", "no", false},
		{"yes", "---\nmemory_scope: yes\n---\nb", scope, "True", "yes", false},
		{"oct_leading_zero", "---\nmodel_tier: 010\n---\nb", tier, "8", "8", true},
		{"eight_leading_zero", "---\nmodel_tier: 08\n---\nb", tier, "08", "8.0", false},
		{"exponent", "---\nmodel_tier: 1e2\n---\nb", tier, "1e2", "100.0", false},
		{"date", "---\nrole: 2026-01-02\n---\nb", role, "2026-01-02", "<unrepresentable>", false},
		// The controls: quoted spellings that BOTH engines leave alone, so
		// a reader can see the divergence is about resolution and not
		// about the field.
		{"quoted_off", "---\nmodel_tier: 'off'\n---\nb", tier, "off", "off", true},
		{"plain_word", "---\nmodel_tier: power\n---\nb", tier, "power", "power", true},
	}

	dir := t.TempDir()
	paths := make([]string, len(rows))
	for i, r := range rows {
		p := filepath.Join(dir, r.name+".md")
		if err := os.WriteFile(p, []byte(r.content), 0o644); err != nil {
			t.Fatal(err)
		}
		paths[i] = p
	}

	var py []pySpec
	personaProbe(t).RunJSON(t, parseProbeSrc, &py, pyprobe.Arg(t, paths))
	if len(py) != len(rows) {
		t.Fatalf("probe returned %d rows for %d files", len(py), len(rows))
	}

	pyField := []func(pySpec) string{}
	for _, r := range rows {
		switch {
		case r.field(&Spec{ModelTier: "T"}) == "T":
			pyField = append(pyField, func(s pySpec) string { return s.ModelTier })
		case r.field(&Spec{MemoryScope: "S"}) == "S":
			pyField = append(pyField, func(s pySpec) string { return s.MemoryScope })
		default:
			pyField = append(pyField, func(s pySpec) string { return s.Role })
		}
	}

	var diverging, agreeing int
	for i, r := range rows {
		gotPy := pyField[i](py[i])
		if gotPy != r.wantPy {
			t.Errorf("%s: CPython's answer MOVED — recorded %q, measured %q. "+
				"The divergence table in this file is now wrong.",
				r.name, r.wantPy, gotPy)
			continue
		}
		spec, err := ParseFile(paths[i])
		if err != nil {
			t.Fatalf("%s: the port refused a file CPython parsed: %v", r.name, err)
		}
		gotGo := r.field(spec)
		if gotGo != r.wantGo {
			t.Errorf("%s: the PORT's answer moved — recorded %q, measured %q",
				r.name, r.wantGo, gotGo)
			continue
		}
		if r.agreeing {
			agreeing++
			if gotGo != gotPy {
				t.Errorf("%s was recorded as an AGREEING row and is not: go %q py %q",
					r.name, gotGo, gotPy)
			}
		} else {
			diverging++
			if gotGo == gotPy {
				t.Errorf("%s was recorded as a DIVERGING row and both engines "+
					"now answer %q — the gap closed, so this pin is stale",
					r.name, gotGo)
			}
		}
	}
	// Vacuity floors on both halves. A table with no diverging row is not
	// pinning a divergence; a table with no agreeing row cannot show the
	// difference is about scalar RESOLUTION rather than about the parser.
	if diverging < 4 {
		t.Fatalf("only %d diverging rows measured; the pin needs the bool "+
			"words AND at least one non-bool spelling", diverging)
	}
	if agreeing < 2 {
		t.Fatalf("only %d agreeing rows measured", agreeing)
	}
}

// The manifest is where the divergence above stops being cosmetic: it is a
// file both runtimes write and either may read. This pins the WHOLE ROW so
// the blast radius is visible, rather than one field in isolation.
func TestYAML11DivergenceReachesTheManifest(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "flip.md"),
		[]byte("---\nname: flip\nmodel_tier: off\nmemory_scope: yes\n---\nbody"),
		0o644); err != nil {
		t.Fatal(err)
	}

	var py []pySpec
	personaProbe(t).RunJSON(t, parseProbeSrc, &py,
		pyprobe.Arg(t, []string{filepath.Join(dir, "flip.md")}))
	if py[0].ModelTier != "False" || py[0].MemoryScope != "True" {
		t.Fatalf("CLAIM moved: CPython answered tier=%q scope=%q, not False/True",
			py[0].ModelTier, py[0].MemoryScope)
	}

	man := GenerateManifest(NewFromDir(dir))
	if len(man) != 1 {
		t.Fatalf("expected one manifest entry, got %d", len(man))
	}
	if got := man[0].GetString("model_tier"); got != "off" {
		t.Fatalf("the port's manifest tier is %q; the recorded divergence says "+
			"it writes the raw spelling %q where CPython writes %q",
			got, "off", "False")
	}
	if got := man[0].GetString("memory_scope"); got != "yes" {
		t.Fatalf("the port's manifest scope is %q, recorded as %q", got, "yes")
	}
}
