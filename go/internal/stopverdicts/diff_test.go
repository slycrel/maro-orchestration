package stopverdicts

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"testing"
)

// stop_verdicts.py is a VOCABULARY module, and a vocabulary port fails in
// a way none of this port's earlier tranches could: silently, in both
// directions, with nothing to observe at the time.
//
// A member this port is MISSING is a value the Python runtime writes and
// the Go runtime's switch falls through on. A member this port INVENTS is
// a value the Go runtime writes and Python's own validator
// (memory_ledger.stamp_outcome_stop_verdict) refuses — so the judgment
// disappears, the row stays unstamped, and every consumer reads the
// status fallback instead, months later, in an aggregate nobody is
// watching at the time.
//
// Neither shape produces a crash, a diff, or a failing test anywhere
// else. So the test is a SET DIFFERENCE against the Python module, run in
// both directions, over every set the module exports — not a spot check
// of the members this port happened to think of.

func srcDir(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p, "stop_verdicts.py")); err != nil {
		t.Skipf("python source tree unavailable: %v", err)
	}
	return p
}

func runPy(t *testing.T, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t))
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("CPython probe failed: %v", err)
	}
	return string(out)
}

// The probe dumps every exported SET as a sorted list and every exported
// MAPPING whole, so a value drift is caught alongside a membership drift
// — REOPEN_CONDITIONS is prose that ships to an operator, and prose is
// this port's recurring bug family.
const pyVocabSrc = `
import json, sys
import stop_verdicts as sv
sys.stdout.write(json.dumps({
    "goal_verdicts": sorted(sv.GOAL_VERDICTS),
    "valid_stop_values": sorted(sv.VALID_STOP_VALUES),
    "interrupt_statuses": sorted(sv.INTERRUPT_STATUSES),
    "paused_statuses": sorted(sv.PAUSED_STATUSES),
    "execution_finished_statuses": sorted(sv.EXECUTION_FINISHED_STATUSES),
    "pause_reasons_operator": sorted(sv.PAUSE_REASONS_OPERATOR),
    "pause_reasons_error": sorted(sv.PAUSE_REASONS_ERROR),
    "valid_pause_reasons": sorted(sv.VALID_PAUSE_REASONS),
    "reopen_conditions": sv.REOPEN_CONDITIONS,
    "pause_reason_by_status": sv.PAUSE_REASON_BY_STATUS,
    "singletons": {
        "OUT_OF_BUDGET": sv.OUT_OF_BUDGET,
        "THESIS_REFUTED": sv.THESIS_REFUTED,
        "NOT_WORTH_IT": sv.NOT_WORTH_IT,
        "LOST_THE_PLOT": sv.LOST_THE_PLOT,
        "EXTERNAL_INTERRUPT": sv.EXTERNAL_INTERRUPT,
        "VERDICT_SOURCE_NEVER_STAMPED": sv.VERDICT_SOURCE_NEVER_STAMPED,
        "VERDICT_SOURCE_RUN_ERRORED": sv.VERDICT_SOURCE_RUN_ERRORED,
        "VERDICT_SOURCE_NO_STEPS_COMPLETED": sv.VERDICT_SOURCE_NO_STEPS_COMPLETED,
        "VERDICT_SOURCE_PENDING_ORPHANED": sv.VERDICT_SOURCE_PENDING_ORPHANED,
        "PAUSE_OP_MANUAL": sv.PAUSE_OP_MANUAL,
        "PAUSE_OP_CLARIFICATION": sv.PAUSE_OP_CLARIFICATION,
        "PAUSE_OP_BUDGET_DECISION": sv.PAUSE_OP_BUDGET_DECISION,
        "PAUSE_ERR_BUSY": sv.PAUSE_ERR_BUSY,
        "PAUSE_ERR_WRITER_DIED": sv.PAUSE_ERR_WRITER_DIED,
        "PAUSE_ERR_LLM_UNREACHABLE": sv.PAUSE_ERR_LLM_UNREACHABLE,
        "PAUSE_ERR_NO_TOKENS": sv.PAUSE_ERR_NO_TOKENS,
        "PAUSE_ERR_DISK_FULL": sv.PAUSE_ERR_DISK_FULL,
    },
}))
`

type pyVocab struct {
	GoalVerdicts              []string          `json:"goal_verdicts"`
	ValidStopValues           []string          `json:"valid_stop_values"`
	InterruptStatuses         []string          `json:"interrupt_statuses"`
	PausedStatuses            []string          `json:"paused_statuses"`
	ExecutionFinishedStatuses []string          `json:"execution_finished_statuses"`
	PauseReasonsOperator      []string          `json:"pause_reasons_operator"`
	PauseReasonsError         []string          `json:"pause_reasons_error"`
	ValidPauseReasons         []string          `json:"valid_pause_reasons"`
	ReopenConditions          map[string]string `json:"reopen_conditions"`
	PauseReasonByStatus       map[string]string `json:"pause_reason_by_status"`
	Singletons                map[string]string `json:"singletons"`
}

func loadPyVocab(t *testing.T) pyVocab {
	t.Helper()
	var v pyVocab
	if err := json.Unmarshal([]byte(runPy(t, pyVocabSrc)), &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func TestEverySetMatchesCPython(t *testing.T) {
	py := loadPyVocab(t)
	for _, c := range []struct {
		name string
		got  map[string]bool
		want []string
	}{
		{"GOAL_VERDICTS", GoalVerdicts, py.GoalVerdicts},
		{"VALID_STOP_VALUES", ValidStopValues, py.ValidStopValues},
		{"INTERRUPT_STATUSES", InterruptStatuses, py.InterruptStatuses},
		{"PAUSED_STATUSES", PausedStatuses, py.PausedStatuses},
		{"EXECUTION_FINISHED_STATUSES", ExecutionFinishedStatuses, py.ExecutionFinishedStatuses},
		{"PAUSE_REASONS_OPERATOR", PauseReasonsOperator, py.PauseReasonsOperator},
		{"PAUSE_REASONS_ERROR", PauseReasonsError, py.PauseReasonsError},
		{"VALID_PAUSE_REASONS", ValidPauseReasons, py.ValidPauseReasons},
	} {
		t.Run(c.name, func(t *testing.T) {
			assertSameSet(t, c.name, c.got, c.want)
		})
	}
}

// The two mappings carry VALUES an operator reads, not just keys. A
// reopen condition whose wording drifted is a sentence the two runtimes
// print differently for the same verdict — the content-key prose family
// this port keeps re-learning.
func TestEveryMappingMatchesCPython(t *testing.T) {
	py := loadPyVocab(t)
	assertSameMap(t, "REOPEN_CONDITIONS", ReopenConditions, py.ReopenConditions)
	assertSameMap(t, "PAUSE_REASON_BY_STATUS", PauseReasonByStatus, py.PauseReasonByStatus)
}

// The constants themselves, by NAME. The set tests above would pass with
// two members swapped between constants; this one would not.
func TestEveryConstantMatchesCPython(t *testing.T) {
	py := loadPyVocab(t)
	got := map[string]string{
		"OUT_OF_BUDGET":                     OutOfBudget,
		"THESIS_REFUTED":                    ThesisRefuted,
		"NOT_WORTH_IT":                      NotWorthIt,
		"LOST_THE_PLOT":                     LostThePlot,
		"EXTERNAL_INTERRUPT":                ExternalInterrupt,
		"VERDICT_SOURCE_NEVER_STAMPED":      VerdictSourceNeverStamped,
		"VERDICT_SOURCE_RUN_ERRORED":        VerdictSourceRunErrored,
		"VERDICT_SOURCE_NO_STEPS_COMPLETED": VerdictSourceNoStepsCompleted,
		"VERDICT_SOURCE_PENDING_ORPHANED":   VerdictSourcePendingOrphaned,
		"PAUSE_OP_MANUAL":                   PauseOpManual,
		"PAUSE_OP_CLARIFICATION":            PauseOpClarification,
		"PAUSE_OP_BUDGET_DECISION":          PauseOpBudgetDecision,
		"PAUSE_ERR_BUSY":                    PauseErrBusy,
		"PAUSE_ERR_WRITER_DIED":             PauseErrWriterDied,
		"PAUSE_ERR_LLM_UNREACHABLE":         PauseErrLLMUnreachable,
		"PAUSE_ERR_NO_TOKENS":               PauseErrNoTokens,
		"PAUSE_ERR_DISK_FULL":               PauseErrDiskFull,
	}
	assertSameMap(t, "constants", got, py.Singletons)
}

// The decree this module exists to encode, pinned as a test rather than
// left to the docstring: EXTERNAL_INTERRUPT is an event marker, not a
// verdict. "The four verdicts are observations about the map; an
// interrupt is an event about the run" (Jeremy, GOAL_BRAIN 2026-07-27
// item 5).
//
// A port that "helpfully" folded the marker into GOAL_VERDICTS would
// still pass every VALID_STOP_VALUES test above — the union is the same
// set — and would make every power cut count as evidence that a goal was
// unreachable.
func TestTheInterruptMarkerIsNotAGoalVerdict(t *testing.T) {
	if IsGoalVerdict(ExternalInterrupt) {
		t.Error("external-interrupt is in GOAL_VERDICTS; it is an event about " +
			"the run, and admitting it makes a power cut into evidence about the goal")
	}
	if !IsValidStopValue(ExternalInterrupt) {
		t.Error("external-interrupt must still be legal in the stop_verdict FIELD")
	}
	if len(GoalVerdicts) != 4 {
		t.Errorf("GOAL_VERDICTS has %d members; the decree says four", len(GoalVerdicts))
	}
}

const pyErrorClassSrc = `
import json, sys
from stop_verdicts import pause_reason_for_error_class, pause_family
out = []
for arg in json.loads(sys.argv[1]):
    out.append([pause_reason_for_error_class(arg), pause_family(arg)])
sys.stdout.write(json.dumps(out))
`

// The module's only two functions with a branch in them. The corpus
// includes every mapped class, the classes the docstring names as
// DELIBERATELY unmapped (auth, budget_runaway), and the empty string —
// Python's `.get(error_class or "", "")` makes None and "" the same
// lookup, so "" must not fall into a mapped arm.
func TestErrorClassAndFamilyMatchCPython(t *testing.T) {
	inputs := []string{
		"billing_actionable", "retry_at", "failover",
		"auth", "budget_runaway", "transient", "",
		// pause_family's own domain, run through the same probe: its
		// argument is a pause reason, and every valid one plus a
		// nonsense one must agree.
		PauseOpManual, PauseOpClarification, PauseOpBudgetDecision,
		PauseErrBusy, PauseErrWriterDied, PauseErrLLMUnreachable,
		PauseErrNoTokens, PauseErrDiskFull,
		"not-a-pause-reason",
	}
	in, err := json.Marshal(inputs)
	if err != nil {
		t.Fatal(err)
	}
	var want [][]string
	if err := json.Unmarshal([]byte(runPy(t, pyErrorClassSrc, string(in))), &want); err != nil {
		t.Fatal(err)
	}
	if len(want) != len(inputs) {
		t.Fatalf("probe returned %d rows for %d inputs", len(want), len(inputs))
	}
	for i, arg := range inputs {
		if got := PauseReasonForErrorClass(arg); got != want[i][0] {
			t.Errorf("PauseReasonForErrorClass(%q) = %q, CPython says %q", arg, got, want[i][0])
		}
		if got := PauseFamily(arg); got != want[i][1] {
			t.Errorf("PauseFamily(%q) = %q, CPython says %q", arg, got, want[i][1])
		}
	}
}

// ReopenCondition is a lookup with a "" default, and the default is the
// point: an unknown verdict has no stated way back, and inventing one
// would be the fabrication §13b exists to prevent.
func TestReopenConditionMatchesCPython(t *testing.T) {
	py := loadPyVocab(t)
	for v, want := range py.ReopenConditions {
		if got := ReopenCondition(v); got != want {
			t.Errorf("ReopenCondition(%q) = %q, CPython says %q", v, got, want)
		}
	}
	for _, unknown := range []string{"", "made-up", "OUT-OF-BUDGET", "out_of_budget"} {
		if got := ReopenCondition(unknown); got != "" {
			t.Errorf("ReopenCondition(%q) = %q; an unknown verdict has no stated "+
				"way back and must not be given one", unknown, got)
		}
	}
}

// PAUSED_STATUSES and INTERRUPT_STATUSES are two decrees over the same
// rows, and Python aliases them with a bare assignment. Writing the
// membership twice is how the two layers drift apart, so the aliasing is
// asserted rather than left to the two set tests above happening to hold
// the same values today.
func TestThePausedAliasIsAnAlias(t *testing.T) {
	if len(PausedStatuses) != len(InterruptStatuses) {
		t.Fatalf("the two sets have %d and %d members", len(PausedStatuses), len(InterruptStatuses))
	}
	for k := range InterruptStatuses {
		if !PausedStatuses[k] {
			t.Errorf("%q is an interrupt status but not a paused status", k)
		}
	}
	// "interrupted" is deliberately absent from PAUSE_REASON_BY_STATUS
	// even though it IS an interrupt status: it covers kill switch,
	// wall-clock timeout and backend death at once, and guessing among
	// them fabricates provenance.
	if _, present := PauseReasonByStatus["interrupted"]; present {
		t.Error(`"interrupted" has a derived pause reason; it covers three ` +
			`different endings and guessing among them fabricates provenance`)
	}
}

func assertSameSet(t *testing.T, name string, got map[string]bool, want []string) {
	t.Helper()
	wantSet := map[string]bool{}
	for _, w := range want {
		wantSet[w] = true
	}
	var missing, extra []string
	for _, w := range want {
		if !got[w] {
			missing = append(missing, w)
		}
	}
	for g := range got {
		if !wantSet[g] {
			extra = append(extra, g)
		}
	}
	sort.Strings(missing)
	sort.Strings(extra)
	if len(missing) > 0 {
		t.Errorf("%s is MISSING %v — Python writes these values and this "+
			"runtime's switches fall through on them", name, missing)
	}
	if len(extra) > 0 {
		t.Errorf("%s has INVENTED %v — Python's validator refuses these, so a "+
			"judgment stamped with one disappears silently", name, extra)
	}
}

func assertSameMap(t *testing.T, name string, got, want map[string]string) {
	t.Helper()
	for k, w := range want {
		g, present := got[k]
		if !present {
			t.Errorf("%s is missing key %q (CPython: %q)", name, k, w)
			continue
		}
		if g != w {
			t.Errorf("%s[%q] = %q, CPython says %q", name, k, g, w)
		}
	}
	for k := range got {
		if _, present := want[k]; !present {
			t.Errorf("%s has invented key %q", name, k)
		}
	}
}
