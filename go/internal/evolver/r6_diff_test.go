package evolver

import (
	"encoding/json"
	"os/exec"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// _apply_suggestion_action's first five lines are all `.get`, which keys
// on PRESENCE. The Go idiom they were ported to —
//
//	category, _ := d["category"].(string)
//	if category == "" { category = "observation" }
//
// defaults on an EMPTY stored value too, and a bare .(float64) zeroes a
// numeric string. Every one of those fields is then written into
// change_log.jsonl, which is the rollback surface both runtimes read
// (adversarial mission-r6 MEDIUM).
//
// The old suite had no test on these coercions at all: TestApplyObservation
// asserts only suggestion_id/module/hash on the row, all three of which
// agree under either spelling.
func TestApplyFieldCoercionsMatchCPython(t *testing.T) {
	docs := []string{
		// THE finding: present-but-empty is NOT absent.
		`{"category":"","suggestion":"s","target":"","suggestion_id":"","confidence":0.9}`,
		// Absent, which is the arm both spellings already agreed on.
		`{"suggestion":"s"}`,
		`{}`,
		// A stored null: `.get` returns it, so `category` is None and the
		// row carries null, not "observation".
		`{"category":null,"target":null,"suggestion":null,"suggestion_id":null,"confidence":0.9}`,
		// float() coerces a numeric string; a bare .(float64) zeroed it,
		// so a 0.9-confidence suggestion was audited as 0.0.
		`{"category":"observation","suggestion":"s","confidence":"0.9"}`,
		`{"category":"observation","suggestion":"s","confidence":"  0.9  "}`,
		`{"category":"observation","suggestion":"s","confidence":1}`,
		`{"category":"observation","suggestion":"s","confidence":true}`,
		// The NAMED divergence: a bare float() raises TypeError on a
		// stored null and ValueError on prose, straight out of a
		// function documented "Never raises".
		`{"category":"observation","suggestion":"s","confidence":null}`,
		`{"category":"observation","suggestion":"s","confidence":"abc"}`,
		// A non-string category, which `.get` also returns as-is.
		`{"category":7,"suggestion":"s","confidence":0.5}`,
		// The ordinary shape.
		`{"category":"skill_pattern","suggestion":"do the thing","target":"builder",` +
			`"suggestion_id":"s-1","confidence":0.75}`,
	}
	in, err := json.Marshal(docs)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"r=[]\n"+
			"for raw in json.loads(sys.argv[1]):\n"+
			"    d = json.loads(raw)\n"+
			"    row = [d.get('category', 'observation'),\n"+
			"           d.get('suggestion', ''),\n"+
			"           d.get('target', 'all'),\n"+
			"           d.get('suggestion_id', '')]\n"+
			"    try:\n"+
			"        conf = float(d.get('confidence', 0.5))\n"+
			"    except (TypeError, ValueError):\n"+
			"        conf = 'RAISES'\n"+
			"    r.append([[repr(v) for v in row], repr(conf)])\n"+
			"print(json.dumps(r))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want [][]json.RawMessage
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var oldDiffers, raises int
	for i, doc := range docs {
		var d map[string]any
		if err := json.Unmarshal([]byte(doc), &d); err != nil {
			t.Fatal(err)
		}
		f := readApplyFields(d)

		var pyStrs []string
		if err := json.Unmarshal(want[i][0], &pyStrs); err != nil {
			t.Fatal(err)
		}
		var pyConf string
		if err := json.Unmarshal(want[i][1], &pyConf); err != nil {
			t.Fatal(err)
		}

		// The four string fields must agree exactly. `.get` returning a
		// non-string (null, 7) is a shape this port renders as "", which
		// IS a divergence and is asserted as such below rather than
		// silently tolerated here.
		goStrs := []string{f.category, f.text, f.target, f.suggestionID}
		for j, name := range []string{"category", "suggestion", "target", "suggestion_id"} {
			pyIsString := len(pyStrs[j]) > 0 && (pyStrs[j][0] == '\'' || pyStrs[j][0] == '"')
			if !pyIsString {
				// CPython puts a non-string into the audit row; Go's
				// typed field cannot hold one. Pinned as a residual, not
				// asserted as parity.
				continue
			}
			if pyval.Repr(goStrs[j]) != pyStrs[j] {
				t.Errorf("%s diverges — this value is written into "+
					"change_log.jsonl, the rollback surface\n  in %s\n"+
					"  go %s\n  py %s", name, doc, pyval.Repr(goStrs[j]), pyStrs[j])
			}
		}

		if pyConf == "'RAISES'" {
			// Python's bare float() crashes out of a function whose
			// docstring says "Never raises". Go returns the 0.5 default.
			// Named divergence, owed to the Python side.
			raises++
			if f.confidence != 0.5 {
				t.Errorf("a confidence CPython cannot parse must fall back to "+
					"0.5, got %v for %s", f.confidence, doc)
			}
		} else if pyval.Repr(f.confidence) != pyConf {
			t.Errorf("confidence diverges\n  in %s\n  go %s\n  py %s",
				doc, pyval.Repr(f.confidence), pyConf)
		}

		// Anti-vacuity: run the pre-fix spelling and require it to lose.
		oldCat, _ := d["category"].(string)
		if oldCat == "" {
			oldCat = "observation"
		}
		oldTgt, _ := d["target"].(string)
		if oldTgt == "" {
			oldTgt = "all"
		}
		oldConf, _ := d["confidence"].(float64)
		if (pyStrs[0] == pyval.Repr(f.category) && pyval.Repr(oldCat) != pyStrs[0]) ||
			(pyStrs[2] == pyval.Repr(f.target) && pyval.Repr(oldTgt) != pyStrs[2]) ||
			(pyConf != "'RAISES'" && pyval.Repr(oldConf) != pyConf) {
			oldDiffers++
		}
	}
	if raises == 0 {
		t.Fatal("no document reaches Python's float() crash: the named " +
			"divergence is unpinned and could close without anyone noticing")
	}
	if oldDiffers < 3 {
		t.Fatalf("the pre-fix coercions match CPython on all but %d of %d "+
			"documents: this corpus could not have caught the finding",
			oldDiffers, len(docs))
	}
	t.Logf("corpus separates the pre-fix coercions on %d of %d documents",
		oldDiffers, len(docs))
}

// The other half of the finding: changeLogAppend read `d` a SECOND time
// rather than using the locals the action had already coerced, so the
// stored row disagreed with the action it was auditing. One dict, two
// readings, is the defect; this pins that there is now one.
func TestChangeLogRowCarriesTheCoercedFieldsNotTheRawOnes(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)

	s := baseSuggestion("c-1", "observation", "all", "runs usually succeed on retry", 0.9)
	mustSave(t, ws, s)
	// Strip the two fields Python defaults, so the row must show the
	// DEFAULTS and not null. Reaching applyAction directly is what makes
	// this testable: Apply reads a typed suggestion, and the raw-map
	// shape is exactly where the two readings could disagree.
	d := map[string]any{
		"suggestion":    "runs usually succeed on retry",
		"suggestion_id": "c-1",
	}
	if got := applyAction(ws, rec, d); got != actionApplied {
		t.Fatalf("apply outcome: %v", got)
	}

	rows := readAllRows(t, changeLogPath(ws))
	if len(rows) != 1 {
		t.Fatalf("expected exactly one audit row, got %d", len(rows))
	}
	row := rows[0]
	for key, want := range map[string]any{"category": "observation", "target": "all"} {
		if row[key] != want {
			t.Errorf("change_log %s = %v, want %v — the audit row was built "+
				"from the raw dict instead of the coerced locals",
				key, row[key], want)
		}
	}
	conf, ok := row["confidence"].(float64)
	if !ok || conf != 0.5 {
		t.Errorf("change_log confidence = %v (%T), want 0.5: an absent "+
			"confidence must audit as CPython's float(d.get(...,0.5)), "+
			"not as null", row["confidence"], row["confidence"])
	}
}
