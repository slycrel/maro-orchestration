package skills

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// TestSkillRowIsJSONDumpsSpelled: skills.py:271 is
// `json.dumps(_skill_to_dict(skill), allow_nan=False)`, and the skills
// store is shared — a skill minted by either runtime is read by the
// other, which is the package's own stated contract.
//
// This is the pin that was MISSING: the row emitter went from
// encoding/json's compact spelling to json.dumps' spaced one and the
// whole package suite stayed green, which means the store's bytes were
// never asserted at all. A writer nothing pins is a writer that can
// drift silently — and this one had already half-drifted (mission-r8).
//
// The near-miss worth recording: Skill.MarshalJSON was ALREADY a correct
// json.dumps emitter (ToDict + pyjson.Ordered). The defect was the
// wrapper around it — encoding/json runs compact() over a MarshalJSON
// result, and compact strips the `, ` and `: ` the good emitter had just
// produced. SetEscapeHTML(false) had been set, which fixed one of the
// four forks and made the site look handled.
func TestSkillRowIsJSONDumpsSpelled(t *testing.T) {
	ws := t.TempDir()
	s := newSkill()
	s.ID = "s1"
	s.Name = "compare a > b"
	// Prose, where non-ASCII is routine rather than exotic.
	s.Description = "run the café benchmark first"
	s.CreatedAt = "2026-08-23T00:00:00Z"
	if err := SaveSkill(ws, &s); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(skillsPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	line := strings.TrimRight(string(raw), "\n")
	if strings.Contains(line, "\n") {
		t.Fatalf("one skill, one line:\n%s", line)
	}

	for _, want := range []string{
		`{"id": "s1", "name": "compare a > b"`,               // separators, order, plain `>`
		`"description": "run the caf\u00e9 benchmark first"`, // ensure_ascii
		`"success_rate": 1.0`,                                // the whole float stayed a float
		`"utility_score": 1.0`,                               // and so did this one
		`"use_count": 0`,                                     // an int stayed an int
		`"variant_of": null`,
	} {
		if !strings.Contains(line, want) {
			t.Errorf("skill row is not json.dumps-shaped (missing %s):\n%s", want, line)
		}
	}

	// The row must still be admissible by this package's own reader — the
	// spelling changed, the contract did not.
	if got := LoadSkills(ws); len(got.Skills) != 1 || got.Skills[0].ID != "s1" {
		t.Fatalf("the emitted row is no longer admitted by its own reader: %+v", got)
	}

	// Anti-vacuity: the pre-fix pipeline, replayed — MarshalJSON's correct
	// output run back through the encoder that was wrapping it. It must
	// lose, and it must lose specifically by having the spaces stripped.
	good, err := s.MarshalJSON()
	if err != nil {
		t.Fatal(err)
	}
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(json.RawMessage(good)); err != nil {
		t.Fatal(err)
	}
	old := strings.TrimSuffix(buf.String(), "\n")
	if old == line {
		t.Fatal("the pre-fix pipeline already produces json.dumps' bytes: the " +
			"test cannot show the fork it was written for")
	}
	if !strings.Contains(old, `"id":"s1"`) {
		t.Fatalf("the pre-fix pipeline was expected to strip json.dumps' key "+
			"separator here; it did not, so the fork is untested:\n%s", old)
	}
}
