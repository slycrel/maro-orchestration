package introspect

import (
	"fmt"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyRecoverySrc renders each plan through json.dumps of its params dict, so
// the params' KEY ORDER is part of what is compared and not just their
// contents.
const pyRecoverySrc = `
import json, sys
import introspect

out = []
for fc in json.loads(sys.argv[1]):
    d = introspect.LoopDiagnosis(loop_id="L", failure_class=fc,
                                 severity="warning")
    p = introspect.plan_recovery(d)
    every = introspect.plan_recovery_all(d)
    out.append({
        "found": p is not None,
        "action": p.action if p else None,
        "auto_apply": p.auto_apply if p else None,
        "risk": p.risk if p else None,
        "params": json.dumps(p.params) if p else None,
        "count": len(every),
    })
print(json.dumps(out))
`

const pyRecurringSrc = `
import json, sys
import introspect

a = json.loads(sys.argv[1])
out = []
for p in introspect.find_recurring_patterns(
        min_occurrences=a["min"], limit=a["limit"]):
    out.append({"failure_class": p.failure_class, "occurrences": p.occurrences,
                "first_seen": p.first_seen, "last_seen": p.last_seen,
                "recovery_action": p.recovery_action,
                "graduation_candidate": p.graduation_candidate})
print(json.dumps(out))
`

// recoveryClasses is every key of _RECOVERY_TABLE plus the shapes that miss
// it. The table is prose an operator reads, so each entry is compared
// character for character rather than by a spot check on two of them.
func recoveryClasses() []string {
	return []string{
		"decomposition_too_broad", "constraint_false_positive",
		"adapter_timeout", "budget_exhaustion", "empty_model_output",
		"token_explosion", "cost_spike", "retry_churn",
		"tool_feedback_neglect", "tool_recovery_failure",
		"tool_arg_malformed", "tool_hallucination", "setup_failure",
		"integration_drift", "artifact_missing",
		// Misses: the healthy class, a class the classifier can produce but
		// the table has no row for, and the empty string.
		"healthy", "constraint_block", "",
	}
}

func TestRecoveryTableMatchesCPython(t *testing.T) {
	classes := recoveryClasses()

	type answer struct {
		Found     bool    `json:"found"`
		Action    *string `json:"action"`
		AutoApply *bool   `json:"auto_apply"`
		Risk      *string `json:"risk"`
		Params    *string `json:"params"`
		Count     int     `json:"count"`
	}
	var want []answer
	probe := pyprobe.Probe{Marker: "introspect.py"}
	probe.RunJSON(t, pyRecoverySrc, &want, pyprobe.Arg(t, classes))
	if len(want) != len(classes) {
		t.Fatalf("probe returned %d answers for %d classes", len(want), len(classes))
	}

	for i, fc := range classes {
		name := fc
		if name == "" {
			name = "the empty class"
		}
		t.Run(name, func(t *testing.T) {
			diag := LoopDiagnosis{LoopID: "L", FailureClass: fc, Severity: "warning"}
			got, ok := PlanRecovery(diag)
			w := want[i]
			if ok != w.Found {
				t.Fatalf("found: cpython %v, go %v", w.Found, ok)
			}
			if all := PlanRecoveryAll(diag); len(all) != w.Count {
				t.Errorf("plan count: cpython %d, go %d", w.Count, len(all))
			}
			if !ok {
				return
			}
			if got.Action != *w.Action {
				t.Errorf("action\ncpython %q\n     go %q", *w.Action, got.Action)
			}
			if got.AutoApply != *w.AutoApply {
				t.Errorf("auto_apply: cpython %v, go %v", *w.AutoApply, got.AutoApply)
			}
			if got.Risk != *w.Risk {
				t.Errorf("risk: cpython %q, go %q", *w.Risk, got.Risk)
			}
			// json.dumps of the params dict, key order included.
			gotParams, err := pyval.DumpsCompactPy(got.Params)
			if err != nil {
				t.Fatal(err)
			}
			if gotParams != *w.Params {
				t.Errorf("params\ncpython %s\n     go %s", *w.Params, gotParams)
			}
			// The failure_class field on the plan must equal the key it was
			// filed under. Nothing else asserts that, and a copy-paste slip
			// in a fifteen-row table is exactly how it would break.
			if got.FailureClass != fc {
				t.Errorf("plan filed under %q carries failure_class %q",
					fc, got.FailureClass)
			}
		})
	}
}

// TestPlanRecoveryAllReturnsACopy pins the `list(...)` in Python: a caller
// that edits the returned slice must not rewrite the module's table.
func TestPlanRecoveryAllReturnsACopy(t *testing.T) {
	diag := LoopDiagnosis{FailureClass: "retry_churn"}
	all := PlanRecoveryAll(diag)
	if len(all) == 0 {
		t.Fatal("no plans for retry_churn")
	}
	before := all[0].Action
	all[0].Action = "mutated by a caller"
	again, ok := PlanRecovery(diag)
	if !ok || again.Action != before {
		t.Fatalf("table was mutated through the returned slice: %q", again.Action)
	}
}

func TestFindRecurringPatternsMatchesCPython(t *testing.T) {
	diag := func(id, class string) string {
		return `{"loop_id": "` + id + `", "failure_class": "` + class +
			`", "severity": "warning", "recommendation": "r", "evidence": []}`
	}

	// STABILITY AT THIRTEEN. Go's pdqsort delegates to insertion sort below
	// 13 elements and is therefore accidentally stable there, and with every
	// key TIED its partitioning leaves the order alone at any size. So the
	// only shape that separates sort.SliceStable from sort.Slice is twelve
	// tied classes plus one that outranks them — and the leader has to be
	// written OLDEST, so that after load_diagnoses reverses the list it is
	// LAST in `counts` and the sort must actually move it.
	//
	// A battery mutant swapping in sort.Slice survived every fixture above,
	// whose largest had four classes. Same threshold, same trap, second
	// unrelated function in this port (see REVIEW_PATTERNS L42).
	tiedThirteen := func() []string {
		rows := []string{diag("a", "k00"), diag("b", "k00")}
		for i := 1; i <= 12; i++ {
			id := fmt.Sprintf("k%02d", i)
			rows = append(rows, diag("r"+id, id))
		}
		return rows
	}

	cases := []struct {
		name string
		min  int
		lim  int
		rows []string
	}{
		{name: "an empty store", min: 3, lim: 50},
		{name: "one occurrence under the bar", min: 3, lim: 50, rows: []string{
			diag("a", "token_explosion"),
		}},
		{name: "exactly at the bar", min: 3, lim: 50, rows: []string{
			diag("a", "token_explosion"), diag("b", "token_explosion"),
			diag("c", "token_explosion"),
		}},
		// first_seen is the OLDEST and last_seen the newest, which a fixture
		// with one occurrence per class cannot tell apart.
		{name: "first and last are the ends of a run", min: 2, lim: 50,
			rows: []string{
				diag("oldest", "retry_churn"), diag("middle", "retry_churn"),
				diag("newest", "retry_churn"),
			}},
		// healthy rows are skipped, and they must not break the ends.
		{name: "healthy rows interleaved", min: 2, lim: 50, rows: []string{
			diag("h1", "healthy"), diag("oldest", "retry_churn"),
			diag("h2", "healthy"), diag("newest", "retry_churn"),
			diag("h3", "healthy"),
		}},
		// TIED COUNTS. Python's counts dict is ordered by each class's FIRST
		// appearance in the newest-first list, and the sort is stable on
		// count alone — so the tie goes to the most recently seen class.
		// This is the only case that separates it from a Go map's order or
		// from an alphabetical tie-break.
		{name: "two classes tied on count", min: 2, lim: 50, rows: []string{
			diag("a1", "adapter_timeout"), diag("a2", "adapter_timeout"),
			diag("b1", "budget_exhaustion"), diag("b2", "budget_exhaustion"),
		}},
		{name: "three classes tied on count", min: 2, lim: 50, rows: []string{
			diag("a1", "adapter_timeout"), diag("a2", "adapter_timeout"),
			diag("b1", "budget_exhaustion"), diag("b2", "budget_exhaustion"),
			diag("c1", "cost_spike"), diag("c2", "cost_spike"),
		}},
		// A class that outranks the others on count must lead regardless of
		// when it was seen.
		{name: "one class ahead on count", min: 2, lim: 50, rows: []string{
			diag("a1", "adapter_timeout"), diag("a2", "adapter_timeout"),
			diag("a3", "adapter_timeout"),
			diag("b1", "budget_exhaustion"), diag("b2", "budget_exhaustion"),
		}},
		// A class with NO recovery-table row: recovery_action is None.
		{name: "a class the table has no row for", min: 2, lim: 50,
			rows: []string{
				diag("x1", "constraint_block"), diag("x2", "constraint_block"),
			}},
		// min_occurrences of 1 keeps everything, and of 0 keeps everything
		// too — `< 0` is never true for a length.
		{name: "a minimum of one", min: 1, lim: 50, rows: []string{
			diag("a", "token_explosion"), diag("b", "retry_churn"),
		}},
		{name: "a minimum of zero", min: 0, lim: 50, rows: []string{
			diag("a", "token_explosion"),
		}},
		// The limit rides load_diagnoses, including its append-then-check
		// quirk: limit=0 returns ONE row, not zero.
		{name: "a limit of zero still sees one row", min: 1, lim: 0,
			rows: []string{
				diag("a", "token_explosion"), diag("b", "token_explosion"),
			}},
		{name: "thirteen classes with one ahead of twelve tied", min: 1,
			lim: 50, rows: tiedThirteen()},
		{name: "a limit that truncates the scan", min: 2, lim: 2, rows: []string{
			diag("a1", "adapter_timeout"), diag("a2", "adapter_timeout"),
			diag("a3", "adapter_timeout"),
		}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ws := seedStore(t, "diagnoses.jsonl", tc.rows)
			probe := pyprobe.Probe{Marker: "introspect.py", Workspace: ws}
			type answer struct {
				FailureClass   string  `json:"failure_class"`
				Occurrences    int     `json:"occurrences"`
				FirstSeen      string  `json:"first_seen"`
				LastSeen       string  `json:"last_seen"`
				RecoveryAction *string `json:"recovery_action"`
				Graduation     bool    `json:"graduation_candidate"`
			}
			var want []answer
			probe.RunJSON(t, pyRecurringSrc, &want, pyprobe.Arg(t,
				map[string]any{"min": tc.min, "limit": tc.lim}))

			got := FindRecurringPatterns(ws, tc.min, tc.lim)
			if len(got) != len(want) {
				gn := make([]string, len(got))
				for i, p := range got {
					gn[i] = p.FailureClass
				}
				wn := make([]string, len(want))
				for i, p := range want {
					wn[i] = p.FailureClass
				}
				t.Fatalf("patterns: cpython %s, go %s",
					strings.Join(wn, ","), strings.Join(gn, ","))
			}
			for i := range want {
				w, g := want[i], got[i]
				if g.FailureClass != w.FailureClass {
					t.Errorf("%d class: cpython %q, go %q", i, w.FailureClass,
						g.FailureClass)
				}
				if g.Occurrences != w.Occurrences {
					t.Errorf("%d occurrences: cpython %d, go %d", i,
						w.Occurrences, g.Occurrences)
				}
				if g.FirstSeen != w.FirstSeen {
					t.Errorf("%d first_seen: cpython %q, go %q", i, w.FirstSeen,
						g.FirstSeen)
				}
				if g.LastSeen != w.LastSeen {
					t.Errorf("%d last_seen: cpython %q, go %q", i, w.LastSeen,
						g.LastSeen)
				}
				wantAction := ""
				if w.RecoveryAction != nil {
					wantAction = *w.RecoveryAction
				}
				if g.RecoveryAction != wantAction {
					t.Errorf("%d recovery_action: cpython %q, go %q", i,
						wantAction, g.RecoveryAction)
				}
				if g.GraduationCandidate != w.Graduation {
					t.Errorf("%d graduation: cpython %v, go %v", i, w.Graduation,
						g.GraduationCandidate)
				}
			}
		})
	}
}
