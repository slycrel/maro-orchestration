package skills

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestSkillRoundTripsThroughItsOwnDict pins DictToSkill(s.ToDict()).
//
// ToDict emits Tags as []string; NormalizeTags — which DictToSkill runs on
// the way back — enumerated pyval.List and []any and nothing else, so the
// round trip through this package's own two functions silently returned a
// skill with NO tags. Round 9 added []string arms across pyval and did not
// reach here, because this switch is a second enumeration of "list" and an
// enumeration is not a class (lens 2).
//
// It is a round TRIP rather than a direct NormalizeTags([]string{...}) call
// on purpose: the direct call pins the arm, and the trip pins the reason the
// arm has to exist. A later refactor that changes ToDict to emit []any would
// keep the direct test green while removing the only thing that produced the
// shape.
func TestSkillRoundTripsThroughItsOwnDict(t *testing.T) {
	orig := Skill{
		ID: "s1", Name: "n", Description: "d",
		StepsTemplate: []string{"one"},
		Tags:          []string{"alpha", "beta"},
		CreatedAt:     "2026-01-01T00:00:00+00:00",
	}
	back, err := DictToSkill(orig.ToDict())
	if err != nil {
		t.Fatalf("DictToSkill: %v", err)
	}
	if len(back.Tags) != len(orig.Tags) {
		t.Fatalf("tags round-tripped to %v, want %v — ToDict emits []string "+
			"and NormalizeTags must admit it", back.Tags, orig.Tags)
	}
	for i := range orig.Tags {
		if back.Tags[i] != orig.Tags[i] {
			t.Errorf("tag %d = %q, want %q", i, back.Tags[i], orig.Tags[i])
		}
	}
}

// TestNormalizeTagsNormalizesAStringSlice pins the arm directly, including
// that it is not a passthrough: the []string path must strip, lower, drop
// empties and honour the cap exactly like the other two.
func TestNormalizeTagsNormalizesAStringSlice(t *testing.T) {
	got := NormalizeTags([]string{"  Alpha  ", "", "BETA", "  ", "g"}, 2)
	want := []string{"alpha", "beta"}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}

// TestManifestReadUsesTheStrictAdmissionPredicate pins the four doors
// manifestSkillIDs left open before round 10.
//
// This reader is the ONE place in the port where an admitted row becomes a
// skill-stats IDENTITY, so a laundered id mints a permanent counter under a
// name nothing else in either runtime uses. Python reaches it through
// _read_store -> _classify -> loads_clean; the port had hand-rolled the
// ladder with bufio.Scanner + json.Unmarshal.
//
// Each case asserts BOTH halves: which ids came back, and whether the loss
// was counted. A reader that dropped the line silently would satisfy the id
// assertion alone, and a silent drop is the failure being fixed — the
// operator's only signal that a manifest is short is the count.
func TestManifestReadUsesTheStrictAdmissionPredicate(t *testing.T) {
	good := `{"skills": [{"id": "keep"}]}`

	for _, c := range []struct {
		name     string
		line     string
		wantIDs  []string
		wantTorn bool
	}{
		// The decode door — round 9's fix in internal/tasks, unswept here.
		// Go's json decoder substitutes U+FFFD for the raw byte and
		// SUCCEEDS, so the port minted a stats identity CPython never
		// creates. \xff cannot appear in a Go interpreted string constant
		// as a lone byte, so the line is assembled from bytes below.
		{"a byte-tainted line is a loss, not a laundered id",
			`{"skills": [{"id": "a` + string([]byte{0xff}) + `b"}]}`,
			[]string{"keep"}, true},

		// A lone surrogate arriving as a JSON ESCAPE. The line is pure
		// ASCII on the wire, so a UTF-8 validity check cannot see it, and
		// Go's decoder turns it into U+FFFD just the same.
		{"an escaped lone surrogate is a loss",
			`{"skills": [{"id": "a\udcffb"}]}`,
			[]string{"keep"}, true},

		// Both decoders silently keep the LAST value for a duplicated name,
		// so two ids where one is discarded by an implementation detail is
		// a corrupt row.
		{"a duplicate name is a loss",
			`{"skills": [{"id": "x"}], "skills": [{"id": "y"}]}`,
			[]string{"keep"}, true},

		{"trailing data after the object is a loss",
			`{"skills": [{"id": "x"}]}{"skills": [{"id": "y"}]}`,
			[]string{"keep"}, true},

		{"a non-object line is a loss",
			`[{"skills": [{"id": "x"}]}]`,
			[]string{"keep"}, true},

		// THE BLANK RULE. _classify strips the trailing newline and
		// compares to b"" — it does not trim. A line of spaces is
		// therefore NOT blank to Python: it is decoded, refused by
		// loads_clean, and COUNTED. strings.TrimSpace dropped it silently
		// as framing, so the two runtimes announced different numbers of
		// torn lines for one file.
		{"a whitespace-only line is a counted loss, not framing",
			"   ", []string{"keep"}, true},

		// The control: an admissible extra line must NOT be counted, or
		// every assertion above would pass for a reader that counted
		// everything (lens 1).
		{"a second good line is not a loss",
			`{"skills": [{"id": "also"}]}`,
			[]string{"keep", "also"}, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			path := filepath.Join(dir, "skills_manifest.jsonl")
			if err := os.WriteFile(path,
				[]byte(good+"\n"+c.line+"\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			ids, malformed, warns := manifestSkillIDs(path)
			if malformed != 0 {
				t.Errorf("malformed = %d; these cases are torn LINES, not "+
					"entries with a non-string id", malformed)
			}
			if strings.Join(ids, ",") != strings.Join(c.wantIDs, ",") {
				t.Errorf("ids = %v, want %v", ids, c.wantIDs)
			}
			torn := false
			for _, w := range warns {
				if strings.Contains(w, "unparseable") {
					torn = true
				}
			}
			if torn != c.wantTorn {
				t.Errorf("announced-a-torn-line = %v, want %v (warnings: %v)",
					torn, c.wantTorn, warns)
			}
		})
	}
}
