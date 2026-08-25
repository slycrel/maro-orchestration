package outcomepolicy

import (
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyPolicySrc asks CPython both questions about each row, and records the
// EXCEPTION rather than only the answer. The class matters: the frozenset
// membership raises TypeError for an unhashable success_class, and this
// function has no try around it, so the raise is the behaviour a caller
// sees rather than an implementation detail.
const pyPolicySrc = `
import json, sys
import outcome_policy

out = []
for row in json.loads(sys.argv[1]):
    rec = {}
    try:
        rec["learnable"] = outcome_policy.is_learnable_outcome(row)
    except Exception as e:
        rec["learnable"] = None
        rec["cls"] = type(e).__name__
        rec["msg"] = str(e)
    try:
        rec["pending"] = outcome_policy.is_verdict_pending(row)
    except Exception as e:
        rec["pending"] = None
        rec["pcls"] = type(e).__name__
    out.append(rec)
print(json.dumps(out))
`

// TestIsLearnableMatchesCPython pins the leaf against the interpreter.
//
// The table is built from the FUNCTION rather than from the port: every
// branch, every operator whose Go spelling differs, and both sides of each
// identity check. The rows that matter most are the ones where truthiness
// and identity disagree — a numeric 1 for goal_achieved, a numeric 0, the
// string "true" — because those are exactly the values a foreign writer
// produces and the port's obvious spelling gets wrong.
func TestIsLearnableMatchesCPython(t *testing.T) {
	rows := []map[string]any{
		// The raw path, plainly.
		{"status": "done"},
		{"status": "failed"},
		{"status": "done", "goal_achieved": true},
		{"status": "done", "goal_achieved": false},
		{"status": "failed", "goal_achieved": true},
		{},

		// `is not False` is IDENTITY. A numeric 0 is not the False
		// singleton, so it does NOT disqualify a done row — where a
		// truthiness reading would refuse it.
		{"status": "done", "goal_achieved": 0},
		{"status": "done", "goal_achieved": 0.0},
		{"status": "done", "goal_achieved": ""},
		{"status": "done", "goal_achieved": nil},
		// ...and `is True` from the other side: a numeric 1 does not
		// satisfy the verdict-preferred branch.
		{"status": "failed", "goal_achieved": 1},
		{"status": "failed", "goal_achieved": "true"},
		{"status": "failed", "goal_achieved": 1.0},

		// The deferred quarantine: unjudged AND deferred.
		{"status": "done", "lesson_extraction_status": "deferred"},
		{"status": "done", "goal_achieved": nil,
			"lesson_extraction_status": "deferred"},
		{"status": "done", "goal_achieved": false,
			"lesson_extraction_status": "deferred"},
		{"status": "done", "goal_achieved": true,
			"lesson_extraction_status": "deferred"},
		{"status": "done", "lesson_extraction_status": "done"},

		// The audit gates are TRUTHINESS, so these six disagree with each
		// other and a bool-only reading collapses them.
		{"status": "done", "audit_incomplete": true},
		{"status": "done", "audit_incomplete": 1},
		{"status": "done", "audit_incomplete": "false"},
		{"status": "done", "audit_incomplete": 0},
		{"status": "done", "audit_incomplete": ""},
		{"status": "done", "audit_incomplete": nil},
		{"status": "done", "audit_incomplete": []any{}},
		{"status": "done", "audit_incomplete": []any{0}},
		{"status": "done", "audit_repair_required": "x"},

		// The cap gate. `in` over a TUPLE, so an unhashable stop_verdict
		// answers False rather than raising — the opposite of the
		// frozenset below, and the reason the two are spelled differently
		// in the port.
		{"status": "done", "stop_verdict": "out-of-budget"},
		{"status": "done", "stop_verdict": "out-of-budget",
			"goal_achieved": true},
		{"status": "done", "stop_verdict": "out-of-budget",
			"goal_achieved": 1},
		{"status": "done", "stop_verdict": "external-interrupt"},
		{"status": "done", "stop_verdict": "other"},
		{"status": "done", "stop_verdict": nil},
		{"status": "done", "stop_verdict": []any{"out-of-budget"}},
		{"status": "done", "stop_verdict": map[string]any{"a": 1}},

		// The curated path. PRESENCE decides it, so a null class is a
		// curated row that fails closed rather than a raw row.
		{"success_class": "success"},
		{"success_class": "done-unverified"},
		{"success_class": "achieved-not-done"},
		{"success_class": "failure"},
		{"success_class": ""},
		{"success_class": nil},
		{"success_class": nil, "status": "done"},
		{"success_class": "unknown", "status": "done", "goal_achieved": true},
		// A number never matches a str key in a set.
		{"success_class": 1},
		{"success_class": true},
		// ...and an unhashable one RAISES, from a function nothing wraps.
		{"success_class": []any{"success"}},
		{"success_class": map[string]any{"success": 1}},
		// Precedence: the audit gate and the cap gate both run BEFORE the
		// curated branch, so a learnable class does not rescue either.
		{"success_class": "success", "audit_incomplete": true},
		{"success_class": "success", "stop_verdict": "out-of-budget"},
		{"success_class": "success", "stop_verdict": "out-of-budget",
			"goal_achieved": true},
		// ...including the raising one: the cap gate refuses first, so
		// this row answers False rather than raising.
		{"success_class": []any{"x"}, "stop_verdict": "out-of-budget"},
	}

	var want []struct {
		Learnable *bool  `json:"learnable"`
		Cls       string `json:"cls"`
		Msg       string `json:"msg"`
		Pending   *bool  `json:"pending"`
	}
	pyprobe.Probe{Marker: "outcome_policy.py"}.RunJSON(t, pyPolicySrc, &want,
		pyprobe.Arg(t, rows))
	if len(want) != len(rows) {
		t.Fatalf("CPython answered %d of %d rows", len(want), len(rows))
	}

	// Anti-vacuity, from the CPython side: a table that was all-False, or
	// that never reached the raise, would satisfy every assertion below.
	trues, raises := 0, 0
	for _, w := range want {
		if w.Learnable == nil {
			raises++
		} else if *w.Learnable {
			trues++
		}
	}
	if trues < 8 || raises < 2 {
		t.Fatalf("CPython answered %d true and raised %d times; the table "+
			"is not exercising both outcomes", trues, raises)
	}

	for i, r := range rows {
		o := objOf(t, r)
		got, gotErr := IsLearnable(o)
		w := want[i]
		if w.Learnable == nil {
			if gotErr == nil {
				t.Errorf("row %d %v answered %v; CPython raises %s: %s",
					i, r, got, w.Cls, w.Msg)
				continue
			}
			if cls := pyval.ClassOf(gotErr); cls != w.Cls {
				t.Errorf("row %d %v raises %s, CPython raises %s",
					i, r, cls, w.Cls)
			}
			// The MESSAGE too: this sentence changed in 3.12 and the port
			// has now carried the pre-3.12 spelling three separate times.
			if msg := gotErr.Error(); msg != w.Msg {
				t.Errorf("row %d message\n go: %q\n py: %q", i, msg, w.Msg)
			}
			continue
		}
		if gotErr != nil {
			t.Errorf("row %d %v raised %v; CPython answers %v",
				i, r, gotErr, *w.Learnable)
			continue
		}
		if got != *w.Learnable {
			t.Errorf("IsLearnable(row %d) = %v, CPython says %v\n  %v",
				i, got, *w.Learnable, r)
		}
		if w.Pending != nil {
			if p := IsVerdictPending(o); p != *w.Pending {
				t.Errorf("IsVerdictPending(row %d) = %v, CPython says %v\n  %v",
					i, p, *w.Pending, r)
			}
		}
	}
}

// objOf builds the pyval.Obj a caller would have decoded, so the test drives
// the port through the same shape production does rather than through a Go
// map whose iteration order is undefined.
func objOf(t *testing.T, m map[string]any) pyval.Obj {
	t.Helper()
	// Key order is not part of any behaviour here — every read is by name —
	// but it is fixed anyway so a failure message is reproducible.
	order := []string{"status", "goal_achieved", "success_class",
		"stop_verdict", "audit_incomplete", "audit_repair_required",
		"lesson_extraction_status"}
	out := pyval.Obj{}
	for _, k := range order {
		if v, ok := m[k]; ok {
			out = append(out, pyval.Field{Key: k, Val: pyval.FromPlain(v)})
		}
	}
	if len(out) != len(m) {
		t.Fatalf("objOf dropped a key: fixture %v has a name the order list "+
			"does not carry", m)
	}
	return out
}
