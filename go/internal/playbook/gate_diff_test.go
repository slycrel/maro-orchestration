package playbook

import (
	"context"
	"os"
	"testing"
)

// `playbook.curation_enabled` is a TRUTHINESS test in Python, not a bool
// cast and not config.Get[bool]. r9's own config differential opened by
// declaring that "curation reads three config values, and every one of
// them was ported as a typed getter where Python writes int(...)" — which
// was false for this third one (Python does not wrap it in int() at all)
// and whose table then only varied the two NUMERIC keys. The gate that
// decides whether the destructive pass runs at all went untested.
//
// Every disagreement was in the destructive direction: Go expired alarms,
// collapsed bullets, archived and rewrote the whole document in a
// workspace whose own config.yml said curation was off.
func TestTheCurationEnabledGateMatchesPythonsTruthiness(t *testing.T) {
	// The document must be one curation visibly CHANGES, or "disabled"
	// and "enabled" produce the same file and the fixture discriminates
	// nothing.
	const doc = "# P\n\n## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"

	for _, tc := range []struct {
		name   string
		val    string
		absent bool
	}{
		// ABSENCE is a distinct third state, and it was untested: every
		// case below SETS the key, so a pyBool that ignored `present` and
		// fell through to the falsey-nil branch survived mutation. Python
		// returns the DEFAULT for an absent key and only tests truthiness
		// on a value that is actually there.
		{"an absent key defaults to enabled", "", true},
		{"an explicit false disables", "false", false},
		{"an explicit true enables", "true", false},
		{"the INTEGER zero is falsy and disables", "0", false},
		{"the integer one enables", "1", false},
		{"an explicit null is falsy and disables", "null", false},
		{"an empty string is falsy and disables", `""`, false},
		{"an empty list is falsy and disables", "[]", false},
		{"an empty map is falsy and disables", "{}", false},
		{"the float zero is falsy and disables", "0.0", false},
		// The asymmetry that makes this truthiness and not parsing.
		{"the STRING false is a non-empty string and ENABLES", `"false"`, false},
		{"the string 0 is a non-empty string and enables", `"0"`, false},
		{"a non-empty list enables", "[1]", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := "playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 1000000\n"
			if !tc.absent {
				cfg += "  curation_enabled: " + tc.val + "\n"
			}

			pyWS := curateWorkspaceRaw(t, doc, cfg)
			var want struct {
				Curated bool   `json:"curated"`
				File    string `json:"file"`
			}
			runPython(t, pyWS, `
import json,sys
st = playbook.curate_playbook(force=False)
print(json.dumps({
  'curated': st is not None,
  'file': playbook._playbook_path().read_text(encoding='utf-8'),
}))
`, &want, doc)

			goWS := curateWorkspaceRaw(t, doc, cfg)
			got := Curate(context.Background(), goWS, nil, nil, false)

			if (got != nil) != want.Curated {
				t.Errorf("curated: go %v, py %v — the gate disagrees, and "+
					"every disagreement rewrites a document the operator "+
					"asked us not to touch", got != nil, want.Curated)
			}
			after, err := os.ReadFile(Path(goWS))
			if err != nil {
				t.Fatal(err)
			}
			if string(after) != want.File {
				t.Errorf("the file differs\n go %q\n py %q", after, want.File)
			}
		})
	}
}

// Anti-vacuity for the table above: if `force=False` curation never ran
// for ANY spelling, every case would agree on "not curated" and the whole
// table would pass against a Curate that returns nil unconditionally.
func TestTheCurationGateCorpusReachesBothVerdicts(t *testing.T) {
	const doc = "# P\n\n## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"
	on, off := 0, 0
	for _, val := range []string{"true", "false", "0", `"false"`, "null"} {
		cfg := "playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 1000000\n" +
			"  curation_enabled: " + val + "\n"
		ws := curateWorkspaceRaw(t, doc, cfg)
		var r struct {
			Curated bool `json:"curated"`
		}
		runPython(t, ws, `
import json,sys
print(json.dumps({'curated': playbook.curate_playbook(force=False) is not None}))
`, &r, doc)
		if r.Curated {
			on++
		} else {
			off++
		}
	}
	if on == 0 || off == 0 {
		t.Fatalf("CPython reached only one verdict (on=%d off=%d); this "+
			"corpus cannot see the gate at all", on, off)
	}
}

// int(str) accepts PEP 515 underscore separators — the opposite of what
// pyInt's comment used to claim, and the claim was the justification for
// handing the string straight to strconv.Atoi.
//
// The separators are legal only BETWEEN digits, so the rejecting half of
// this table is as load-bearing as the accepting half: deleting
// underscores unconditionally (the obvious fix) makes Go accept four
// spellings CPython raises on, trading one divergence for its mirror.
func TestPyIntAcceptsUnderscoreSeparatorsExactlyWherePythonDoes(t *testing.T) {
	const doc = "# P\n\n## Signals\n\n- x · alarm k:a @2001-01-01\n\n" +
		"## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"

	for _, tc := range []struct {
		name string
		val  string
	}{
		{"a separated thousand", `"1_000"`},
		{"a single separator", `"1_4"`},
		{"several separators", `"1_2_3"`},
		{"a signed separated number", `"+1_4"`},
		{"a leading zero group", `"0_1"`},
		{"whitespace around a separated number", `"  1_000  "`},
		{"Arabic-Indic digits around a separator", `"١_٠"`},
		// The rejecting half: each has a separator without a digit on
		// both sides, and CPython raises — which abandons the whole pass.
		{"a leading separator raises", `"_14"`},
		{"a trailing separator raises", `"14_"`},
		{"a doubled separator raises", `"1__4"`},
		{"a separator after the sign raises", `"+_14"`},
		{"a lone separator raises", `"_"`},
		// Controls: unchanged behaviour on either side of the fix.
		{"a plain quoted number", `"14"`},
		{"an unquoted number", `14`},
		{"a non-numeric string raises", `"x"`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := "playbook:\n  curation_min_chars: 1000000\n" +
				"  alarm_ttl_days: " + tc.val + "\n"

			pyWS := curateWorkspaceRaw(t, doc, cfg)
			var want struct {
				Curated bool   `json:"curated"`
				File    string `json:"file"`
			}
			runPython(t, pyWS, `
import json,sys
st = playbook.curate_playbook(force=True)
print(json.dumps({
  'curated': st is not None,
  'file': playbook._playbook_path().read_text(encoding='utf-8'),
}))
`, &want, doc)

			goWS := curateWorkspaceRaw(t, doc, cfg)
			got := Curate(context.Background(), goWS, nil, nil, true)

			if (got != nil) != want.Curated {
				t.Errorf("curated: go %v, py %v — an int() that raises "+
					"abandons expiry, dedup, archive and rewrite together",
					got != nil, want.Curated)
			}
			after, err := os.ReadFile(Path(goWS))
			if err != nil {
				t.Fatal(err)
			}
			if string(after) != want.File {
				t.Errorf("the file differs\n go %q\n py %q", after, want.File)
			}
		})
	}
}
