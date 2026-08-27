package loopfinalize

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/loop"
	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// _finalize_loop returns nothing and raises nothing. Everything it does is
// a CALL or a LOG LINE, so the differential records both, in order, with
// arguments — and the LOG LEVEL is part of the record, because the levels
// are the spec. Eight try blocks fail five different ways, and the whole
// point of the 2026-04-26 NameError (a dead introspection block that
// nobody saw for six weeks, because its handler was `log.debug`) is that
// a port which got the levels right-but-different would hide the same
// class of bug in the same way.
//
// Every module the function reaches is a recorder on both sides.
// `_step_evidence` and `_crystallize_and_synthesize` are the exceptions:
// they stay REAL, so the seams between the three ported pieces are
// covered rather than stubbed away.

type finStep struct {
	Text   string `json:"text"`
	Status string `json:"status"`
	Result string `json:"result"`
}

type finDiag struct {
	FailureClass   string `json:"failure_class"`
	Recommendation string `json:"recommendation"`
	Summary        string `json:"summary"`
}

type finLens struct {
	LensName string `json:"lens_name"`
	Action   string `json:"action"`
}

type finAgg struct {
	Confidence    float64 `json:"confidence"`
	LensAgreement int     `json:"lens_agreement"`
	PrimaryAction string  `json:"primary_action"`
}

type finRecovery struct {
	AutoApply bool   `json:"auto_apply"`
	Risk      string `json:"risk"`
	Action    string `json:"action"`
}

// finSpec is one fixture. Every field is present in the JSON (no
// omitempty): the probe subscripts each key, and an absent one would be a
// KeyError rather than a default — which is deliberate, since a default
// silently agreed on by both sides tests nothing.
type finSpec struct {
	Name       string    `json:"name"`
	LoopID     string    `json:"loop_id"`
	Goal       string    `json:"goal"`
	Project    string    `json:"project"`
	LoopStatus string    `json:"loop_status"`
	Steps      []finStep `json:"steps"`
	Adapter    string    `json:"adapter"`
	ModelKey   string    `json:"model_key"`

	DryRun           bool     `json:"dry_run"`
	Verbose          bool     `json:"verbose"`
	TokensIn         int      `json:"tokens_in"`
	TokensOut        int      `json:"tokens_out"`
	ElapsedMS        int      `json:"elapsed_ms"`
	HadNoMatching    bool     `json:"had_no_matching"`
	FailureChain     []string `json:"failure_chain"`
	RecoverySteps    int      `json:"recovery_steps"`
	DeferLearning    bool     `json:"defer_learning"`
	DeferMaintenance bool     `json:"defer_maintenance"`
	MeasurementClass string   `json:"measurement_class"`
	HandleID         string   `json:"handle_id"`
	StopVerdict      string   `json:"stop_verdict"`
	StopEvidence     string   `json:"stop_evidence"`
	PauseReason      string   `json:"pause_reason"`

	// NoLoopID and NilFailureChain say "this field is genuinely absent",
	// which the zero value cannot: add() fills a blank LoopID with a
	// default and a nil FailureChain with an empty slice, so without
	// these two flags no fixture can reach the `if not loop_id` and
	// `failure_chain or []` branches at all.
	NoLoopID        bool `json:"no_loop_id"`
	NilFailureChain bool `json:"nil_failure_chain"`

	Diag             finDiag      `json:"diag"`
	DiagRaise        string       `json:"diag_raise"`
	SaveDiagRaise    string       `json:"save_diag_raise"`
	EventsRaise      string       `json:"events_raise"`
	ProfilesRaise    string       `json:"profiles_raise"`
	Lenses           []finLens    `json:"lenses"`
	LensesRaise      string       `json:"lenses_raise"`
	Agg              finAgg       `json:"agg"`
	AggRaise         string       `json:"agg_raise"`
	Recovery         *finRecovery `json:"recovery"`
	RecoveryRaise    string       `json:"recovery_raise"`
	TieredRaise      string       `json:"tiered_raise"`
	StoreLessonRaise string       `json:"store_lesson_raise"`

	Grounding      [][]any `json:"grounding"`
	GroundingRaise string  `json:"grounding_raise"`

	ReflectRaise     string `json:"reflect_raise"`
	ReflectNone      bool   `json:"reflect_none"`
	OutcomeID        string `json:"outcome_id"`
	TraceRaise       string `json:"trace_raise"`
	StepLessonsRaise string `json:"steplessons_raise"`

	Weighting      bool   `json:"weighting"`
	WeightingRaise string `json:"weighting_raise"`
	RefreshRaise   string `json:"refresh_raise"`

	RecordMaint      bool   `json:"record_maint"`
	RecordMaintRaise string `json:"record_maint_raise"`
	DeferRaise       string `json:"defer_raise"`
	ScopeRaise       string `json:"scope_raise"`
	Drain            bool   `json:"drain"`

	TelegramRaise string `json:"telegram_raise"`

	Existing  []string `json:"existing"`
	Extracted []string `json:"extracted"`

	// DropNames removes INDIVIDUAL names from the modules below, which
	// the Missing* flags cannot express: Python imports several names per
	// statement, so losing one loses the whole statement — and the
	// handler that catches it is the statement's, not the call site's.
	DropNames []string `json:"drop_names"`

	MissingIntrospect    bool `json:"missing_introspect"`
	MissingMemory        bool `json:"missing_memory"`
	MissingMintGrounding bool `json:"missing_mint_grounding"`
	MissingPortability   bool `json:"missing_portability"`
	MissingTailJobs      bool `json:"missing_tailjobs"`
	MissingTelegram      bool `json:"missing_telegram"`
}

// finAdapter is the probe's `Obj(...)`: a plain object with no __bool__,
// which CPython calls truthy and pyval.Truthy answers the same way
// through its "unknown object" arm. It carries model_key only when the
// fixture gives it one, so `getattr(adapter, "model_key", "")` has both
// answers to give.
type finAdapter struct {
	modelKey string
	hasKey   bool
}

func finTag(a any) string {
	if a == nil {
		return "none"
	}
	if pyval.Truthy(a) {
		return "truthy"
	}
	return "falsy"
}

func finScenarios() []finSpec {
	var out []finSpec
	add := func(s finSpec) {
		if s.LoopID == "" {
			s.LoopID = "loop-ff004411"
		}
		if s.Goal == "" {
			s.Goal = "ship the thing"
		}
		if s.LoopStatus == "" {
			s.LoopStatus = "done"
		}
		if s.Adapter == "" {
			s.Adapter = "truthy"
		}
		if s.Diag.FailureClass == "" {
			s.Diag = finDiag{FailureClass: "healthy",
				Recommendation: "keep going", Summary: "all fine"}
		}
		if s.Agg == (finAgg{}) {
			s.Agg = finAgg{Confidence: 0.75, LensAgreement: 2,
				PrimaryAction: "retry"}
		}
		if s.OutcomeID == "" {
			s.OutcomeID = "oc-1234"
		}
		if s.Steps == nil {
			s.Steps = []finStep{}
		}
		if s.FailureChain == nil {
			s.FailureChain = []string{}
		}
		if s.Lenses == nil {
			s.Lenses = []finLens{}
		}
		if s.Grounding == nil {
			s.Grounding = [][]any{{"loop:x"}}
		}
		if s.Existing == nil {
			s.Existing = []string{}
		}
		if s.Extracted == nil {
			s.Extracted = []string{}
		}
		if s.DropNames == nil {
			s.DropNames = []string{}
		}
		if s.NoLoopID {
			s.LoopID = ""
		}
		if s.NilFailureChain {
			s.FailureChain = nil
		}
		out = append(out, s)
	}
	done := func(text, result string) finStep {
		return finStep{Text: text, Status: "done", Result: result}
	}
	blocked := func(text, result string) finStep {
		return finStep{Text: text, Status: "blocked", Result: result}
	}
	sick := finDiag{FailureClass: "stalled",
		Recommendation: "split the step", Summary: "3 steps, none moved"}
	twoSteps := []finStep{done("a", "ra"), blocked("b", "rb")}

	// --- the loop_end header ------------------------------------------
	// done/blocked are counted separately and "skipped" is neither, so a
	// three-step run reports 1/1 and the third is invisible.
	add(finSpec{Name: "header-counts-done-and-blocked",
		Steps: []finStep{done("a", "ra"), blocked("b", "rb"),
			{Text: "c", Status: "skipped", Result: "rc"}}})
	add(finSpec{Name: "header-sums-both-token-counters",
		TokensIn: 1200, TokensOut: 340, ElapsedMS: 98765,
		Steps: twoSteps})
	add(finSpec{Name: "header-with-no-steps"})

	// --- introspect ----------------------------------------------------
	add(finSpec{Name: "diagnosis-healthy-stops-after-save"})
	add(finSpec{Name: "diagnosis-unhealthy-full-path", Diag: sick,
		Lenses: []finLens{{"budget", "cut the fan-out"},
			{"stall", "re-decompose"}},
		Recovery: &finRecovery{AutoApply: true, Risk: "low",
			Action: "retry with a hint"}})
	add(finSpec{Name: "diagnose-loop-raises",
		DiagRaise: "diagnose exploded"})
	add(finSpec{Name: "save-diagnosis-raises",
		SaveDiagRaise: "save exploded"})
	add(finSpec{Name: "missing-introspect-module",
		MissingIntrospect: true, Diag: sick})
	// A failure inside the unhealthy branch aborts the outer try, so the
	// diagnosis lesson below it is never reached. That is the difference
	// between these and the recovery-lesson failure further down.
	add(finSpec{Name: "load-events-raises-kills-the-diagnosis-lesson",
		Diag: sick, EventsRaise: "no event log"})
	add(finSpec{Name: "build-profiles-raises", Diag: sick,
		ProfilesRaise: "bad frame"})
	add(finSpec{Name: "run-lenses-raises", Diag: sick,
		LensesRaise: "lens exploded"})
	add(finSpec{Name: "empty-lens-results-skip-the-aggregate",
		Diag: sick, Lenses: []finLens{}})
	// `if _lr.action` — a lens that fired with no action is silent.
	add(finSpec{Name: "lens-with-empty-action-is-not-logged", Diag: sick,
		Lenses: []finLens{{"quiet", ""}, {"loud", "do a thing"}}})
	add(finSpec{Name: "aggregate-raises", Diag: sick,
		Lenses: []finLens{{"a", "x"}}, AggRaise: "agg exploded"})
	// %.0f rounds half to EVEN in both languages: 12.5 → 12, 13.5 → 14.
	add(finSpec{Name: "confidence-half-rounds-to-even-down", Diag: sick,
		Lenses: []finLens{{"a", "x"}},
		Agg:    finAgg{Confidence: 0.125, LensAgreement: 1, PrimaryAction: "wait"}})
	add(finSpec{Name: "confidence-half-rounds-to-even-up", Diag: sick,
		Lenses: []finLens{{"a", "x"}},
		Agg:    finAgg{Confidence: 0.135, LensAgreement: 3, PrimaryAction: "wait"}})
	add(finSpec{Name: "confidence-zero", Diag: sick,
		Lenses: []finLens{{"a", "x"}},
		Agg:    finAgg{Confidence: 0, LensAgreement: 0, PrimaryAction: ""}})
	add(finSpec{Name: "plan-recovery-raises", Diag: sick,
		RecoveryRaise: "planner exploded"})
	add(finSpec{Name: "no-recovery-plan-no-recovery-lesson", Diag: sick,
		Recovery: nil})
	add(finSpec{Name: "recovery-needs-review-tag", Diag: sick,
		Recovery: &finRecovery{AutoApply: false, Risk: "high",
			Action: "ask a human"}})
	// THE one: the recovery-plan lesson fails at DEBUG and the diagnosis
	// lesson below still runs. A port that shared one handler would drop
	// it silently.
	add(finSpec{Name: "recovery-lesson-failure-leaves-diagnosis-lesson-reachable",
		Diag:        sick,
		Recovery:    &finRecovery{AutoApply: true, Risk: "low", Action: "retry"},
		TieredRaise: "tiered store is down"})
	add(finSpec{Name: "store-lesson-raises-is-a-warning", Diag: sick,
		StoreLessonRaise: "lesson store is down"})
	add(finSpec{Name: "missing-memory-module", MissingMemory: true,
		Diag:     sick,
		Recovery: &finRecovery{AutoApply: true, Risk: "low", Action: "retry"},
		Steps:    []finStep{blocked("a", "ra")}, LoopStatus: "blocked"})
	add(finSpec{Name: "source-goal-clipped-at-120", Diag: sick,
		Goal:     strings.Repeat("g", 200),
		Recovery: &finRecovery{AutoApply: true, Risk: "low", Action: "retry"}})
	add(finSpec{Name: "source-goal-clip-is-code-points", Diag: sick,
		Goal:     strings.Repeat("é", 140),
		Recovery: &finRecovery{AutoApply: true, Risk: "low", Action: "retry"}})

	// --- grounding ------------------------------------------------------
	add(finSpec{Name: "grounding-skipped-without-loop-id", Diag: sick,
		LoopID:   " ",
		Recovery: &finRecovery{AutoApply: true, Risk: "low", Action: "retry"}})
	add(finSpec{Name: "grounding-raises-is-empty-not-absent", Diag: sick,
		GroundingRaise: "grounding is down",
		Recovery:       &finRecovery{AutoApply: true, Risk: "low", Action: "retry"}})
	// `ground_lessons_for_run(...)[0]` on an empty list is an IndexError,
	// which the same bare except turns into [].
	add(finSpec{Name: "grounding-empty-return-is-an-index-error", Diag: sick,
		Grounding: [][]any{},
		Recovery:  &finRecovery{AutoApply: true, Risk: "low", Action: "retry"}})
	add(finSpec{Name: "grounding-value-rides-into-the-lesson", Diag: sick,
		Grounding: [][]any{{"frame:1", "frame:2"}, {"ignored"}},
		Recovery:  &finRecovery{AutoApply: true, Risk: "low", Action: "retry"}})
	add(finSpec{Name: "missing-mint-grounding-module", Diag: sick,
		MissingMintGrounding: true,
		Recovery:             &finRecovery{AutoApply: true, Risk: "low", Action: "retry"}})

	// --- verified recovery ----------------------------------------------
	add(finSpec{Name: "verified-recovery-happy", RecoverySteps: 2,
		FailureChain: []string{"retrying step 2 after a timeout"},
		Steps:        twoSteps})
	add(finSpec{Name: "verified-recovery-needs-done", LoopStatus: "blocked",
		RecoverySteps: 2, FailureChain: []string{"retry"}, Steps: twoSteps})
	add(finSpec{Name: "verified-recovery-needs-recovery-steps",
		RecoverySteps: 0, FailureChain: []string{"retry"}, Steps: twoSteps})
	add(finSpec{Name: "verified-recovery-needs-a-chain",
		RecoverySteps: 3, FailureChain: []string{}, Steps: twoSteps})
	// The kinds are a SET then sorted: alphabetical, deduped across
	// entries, and "split"/"retry" are substring matches.
	add(finSpec{Name: "verified-recovery-kinds-sorted-and-deduped",
		RecoverySteps: 1,
		FailureChain: []string{"retrying after a split",
			"re-decomposing the plan", "another retry"},
		Steps: twoSteps})
	add(finSpec{Name: "verified-recovery-no-marker-falls-back",
		RecoverySteps: 1,
		FailureChain:  []string{"something else entirely"}, Steps: twoSteps})
	add(finSpec{Name: "verified-recovery-head-clipped-at-100",
		RecoverySteps: 1,
		FailureChain: []string{"retry " + strings.Repeat("z", 140),
			"unused second entry"},
		Steps: twoSteps})
	add(finSpec{Name: "verified-recovery-record-raises-is-a-debug-line",
		RecoverySteps: 1, FailureChain: []string{"retry it"},
		TieredRaise: "tiered store is down", Steps: twoSteps})

	// --- reflexion -------------------------------------------------------
	// The two evidence strings come from the SAME helper with different
	// budgets, so a run with long results renders differently in each.
	add(finSpec{Name: "reflect-two-budgets-differ",
		Steps: []finStep{done("first", strings.Repeat("A", 900)),
			done("second", strings.Repeat("B", 900)),
			blocked("third", strings.Repeat("C", 900))}})
	add(finSpec{Name: "reflect-with-no-steps"})
	add(finSpec{Name: "reflect-raises-is-a-warning",
		ReflectRaise: "recorder is down", Steps: twoSteps})
	add(finSpec{Name: "reflect-returns-none-skips-the-trace",
		ReflectNone: true, Steps: twoSteps})
	add(finSpec{Name: "record-step-trace-raises-is-a-debug-line",
		TraceRaise: "trace store is down", Steps: twoSteps})
	add(finSpec{Name: "model-key-present", ModelKey: "anthropic:opus",
		Steps: twoSteps})
	// A falsy-but-not-None adapter has no model_key attribute, and is
	// still what reflect_and_record is handed.
	add(finSpec{Name: "falsy-adapter-has-no-model-key", Adapter: "falsy",
		Steps: twoSteps})
	add(finSpec{Name: "adapter-none", Adapter: "none", Steps: twoSteps})
	add(finSpec{Name: "every-passthrough-field",
		Project: "proj-x", TokensIn: 7, TokensOut: 9, ElapsedMS: 4200,
		MeasurementClass: "clean-room", HandleID: "h-99",
		StopVerdict: "stop", StopEvidence: "budget", PauseReason: "waiting",
		DeferLearning: true, RecoverySteps: 4,
		FailureChain: []string{"one", "two"}, Steps: twoSteps})

	// --- portability -----------------------------------------------------
	add(finSpec{Name: "portability-weighting-on", Weighting: true,
		Steps: twoSteps})
	add(finSpec{Name: "portability-weighting-off", Weighting: false,
		Steps: twoSteps})
	add(finSpec{Name: "portability-weighting-raises",
		WeightingRaise: "config is down", Steps: twoSteps})
	add(finSpec{Name: "portability-refresh-raises", Weighting: true,
		RefreshRaise: "cache write failed", Steps: twoSteps})
	add(finSpec{Name: "missing-portability-module",
		MissingPortability: true, Steps: twoSteps})

	// --- step lessons -----------------------------------------------------
	add(finSpec{Name: "step-lessons-run-for-a-blocked-run",
		LoopStatus: "blocked", Steps: twoSteps})
	add(finSpec{Name: "step-lessons-skipped-for-done", Steps: twoSteps})
	add(finSpec{Name: "step-lessons-skipped-with-no-steps",
		LoopStatus: "blocked"})
	add(finSpec{Name: "step-lessons-raise-is-a-debug-line",
		LoopStatus: "blocked", StepLessonsRaise: "extractor is down",
		Steps: twoSteps})

	// --- the crystallize seam ----------------------------------------------
	add(finSpec{Name: "crystallize-runs-for-a-done-run",
		Extracted: []string{"alpha"}, Verbose: true,
		HadNoMatching: true, Steps: twoSteps})
	add(finSpec{Name: "crystallize-deferred-with-the-lessons",
		DeferLearning: true, Extracted: []string{"alpha"},
		HadNoMatching: true, Steps: twoSteps})
	add(finSpec{Name: "crystallize-skipped-with-no-steps",
		Extracted: []string{"alpha"}, HadNoMatching: true})
	add(finSpec{Name: "crystallize-skipped-when-not-done",
		LoopStatus: "restart", Extracted: []string{"alpha"},
		HadNoMatching: true, Steps: twoSteps})
	add(finSpec{Name: "crystallize-existing-skill-is-skipped",
		Existing: []string{"alpha"}, Extracted: []string{"alpha", "beta"},
		Steps: twoSteps})

	// --- the maintenance ladder ---------------------------------------------
	add(finSpec{Name: "maintenance-inline-by-default", Steps: twoSteps})
	add(finSpec{Name: "maintenance-durable-record-wins",
		DeferMaintenance: true, HandleID: "h-1", RecordMaint: true,
		Steps: twoSteps})
	// A record that returns FALSE is not a failure — there was nowhere
	// durable to write — and falls to the in-process tier with no log
	// line at all.
	add(finSpec{Name: "maintenance-record-false-falls-to-in-process",
		DeferMaintenance: true, HandleID: "h-1", RecordMaint: false,
		Steps: twoSteps})
	add(finSpec{Name: "maintenance-record-raises-falls-to-in-process",
		DeferMaintenance: true, HandleID: "h-1",
		RecordMaintRaise: "no run dir", Steps: twoSteps})
	add(finSpec{Name: "maintenance-defer-raises-runs-inline",
		DeferMaintenance: true, HandleID: "h-1",
		RecordMaintRaise: "no run dir", DeferRaise: "registry is down",
		Steps: twoSteps})
	add(finSpec{Name: "maintenance-defer-then-drain",
		DeferMaintenance: true, HandleID: "h-1", Drain: true,
		Steps: twoSteps})
	// The deferred closure captures verbose BY VALUE (Python binds it as
	// a default argument), and every other drain fixture leaves verbose
	// false — where "captured" and "hardcoded false" are the same call.
	add(finSpec{Name: "maintenance-defer-then-drain-verbose",
		DeferMaintenance: true, HandleID: "h-1", Drain: true,
		Verbose: true, Steps: twoSteps})
	// The drain re-enters the loop's captains-log scope, and a scope that
	// raises means the maintenance never runs — reported by the DRAIN's
	// own warning, not by anything in _finalize_loop.
	add(finSpec{Name: "maintenance-drain-scope-raises",
		DeferMaintenance: true, HandleID: "h-1", Drain: true,
		ScopeRaise: "captains log is down", Steps: twoSteps})
	add(finSpec{Name: "maintenance-defer-flag-without-handle-id",
		DeferMaintenance: true, HandleID: "", Steps: twoSteps})
	add(finSpec{Name: "maintenance-handle-id-without-defer-flag",
		DeferMaintenance: false, HandleID: "h-1", Steps: twoSteps})
	add(finSpec{Name: "maintenance-drain-with-nothing-registered",
		HandleID: "h-1", Drain: true, Steps: twoSteps})
	add(finSpec{Name: "missing-tail-jobs-module", MissingTailJobs: true,
		DeferMaintenance: true, HandleID: "h-1", Steps: twoSteps})
	add(finSpec{Name: "maintenance-passes-verbose-through",
		Verbose: true, Steps: twoSteps})

	// --- the restart ping ------------------------------------------------
	add(finSpec{Name: "restart-ping", LoopStatus: "restart",
		Project: "proj-x", Steps: twoSteps})
	add(finSpec{Name: "restart-ping-falls-back-to-a-40-char-goal",
		LoopStatus: "restart", Goal: strings.Repeat("g", 90),
		Steps: twoSteps})
	add(finSpec{Name: "restart-ping-raises-is-a-debug-line",
		LoopStatus: "restart", TelegramRaise: "telegram is down",
		Steps: twoSteps})
	add(finSpec{Name: "missing-telegram-module", LoopStatus: "restart",
		MissingTelegram: true, Steps: twoSteps})
	add(finSpec{Name: "no-ping-when-not-a-restart", LoopStatus: "blocked",
		Steps: twoSteps})

	// --- dry run ------------------------------------------------------------
	// Dry run reaches the diagnosis and the reflect row and NOTHING else:
	// no recovery lesson, no portability, no step lessons, no
	// crystallisation, no maintenance, no ping.
	add(finSpec{Name: "dry-run-silences-almost-everything", DryRun: true,
		Diag:             sick,
		Recovery:         &finRecovery{AutoApply: true, Risk: "low", Action: "retry"},
		LoopStatus:       "restart",
		RecoverySteps:    2,
		FailureChain:     []string{"retry it"},
		DeferMaintenance: true, HandleID: "h-1",
		Weighting: true, HadNoMatching: true,
		Extracted: []string{"alpha"}, Steps: twoSteps})
	add(finSpec{Name: "dry-run-hands-the-recorder-no-adapter", DryRun: true,
		ModelKey: "anthropic:opus", Steps: twoSteps})

	// --- one name at a time -------------------------------------------
	// Python's import statements bundle names, so losing ONE loses the
	// whole statement — and the phase fails at the top of its try, before
	// the first call it would have made. Dropping the seventh introspect
	// name means diagnose_loop is never reached at all, which no
	// whole-module fixture can show.
	for _, n := range []string{"diagnose_loop", "save_diagnosis",
		"_load_loop_events", "_build_step_profiles", "run_lenses",
		"aggregate_lenses", "plan_recovery"} {
		add(finSpec{Name: "drop-" + n, DropNames: []string{n},
			Diag:   sick,
			Lenses: []finLens{{"budget", "cut the fan-out"}},
			Recovery: &finRecovery{AutoApply: true, Risk: "low",
				Action: "retry"},
			Steps: twoSteps})
	}
	// record_step_trace shares its import with reflect_and_record, so
	// dropping it fails the phase with the WARNING and no row is written
	// at all — not the debug line its own call site would produce.
	add(finSpec{Name: "drop-record_step_trace",
		DropNames: []string{"record_step_trace"}, Steps: twoSteps})
	add(finSpec{Name: "drop-reflect_and_record",
		DropNames: []string{"reflect_and_record"}, Steps: twoSteps})
	// record_tiered_lesson is imported at TWO sites with two different
	// handlers, and both are debug lines.
	add(finSpec{Name: "drop-record_tiered_lesson",
		DropNames: []string{"record_tiered_lesson"}, Diag: sick,
		Recovery: &finRecovery{AutoApply: true, Risk: "low",
			Action: "retry"},
		RecoverySteps: 2, FailureChain: []string{"retry it"},
		Steps: twoSteps})
	add(finSpec{Name: "drop-_store_lesson",
		DropNames: []string{"_store_lesson"}, Diag: sick, Steps: twoSteps})
	add(finSpec{Name: "drop-extract_step_lessons",
		DropNames: []string{"extract_step_lessons"}, LoopStatus: "blocked",
		Steps: twoSteps})
	add(finSpec{Name: "drop-ground_lessons_for_run",
		DropNames: []string{"ground_lessons_for_run"}, Diag: sick,
		Recovery: &finRecovery{AutoApply: true, Risk: "low",
			Action: "retry"},
		Steps: twoSteps})
	add(finSpec{Name: "drop-weighting_enabled",
		DropNames: []string{"weighting_enabled"}, Steps: twoSteps})
	add(finSpec{Name: "drop-refresh_cache", Weighting: true,
		DropNames: []string{"refresh_cache"}, Steps: twoSteps})
	add(finSpec{Name: "drop-record_maintenance",
		DropNames: []string{"record_maintenance"}, DeferMaintenance: true,
		HandleID: "h-1", Drain: true, Steps: twoSteps})
	// loop_id_scope is imported INSIDE the deferred closure, so it fails
	// at DRAIN time and is reported by the drain's warning.
	add(finSpec{Name: "drop-loop_id_scope",
		DropNames: []string{"loop_id_scope"}, DeferMaintenance: true,
		HandleID: "h-1", Drain: true, Steps: twoSteps})
	add(finSpec{Name: "drop-telegram_notify",
		DropNames: []string{"telegram_notify"}, LoopStatus: "restart",
		Steps: twoSteps})

	// --- what the defaults hid ------------------------------------------
	// Every one of these was a battery survivor: the fixture set agreed
	// with the mutation because add()'s defaults had quietly closed the
	// input space.
	//
	// An empty loop_id is the only input that separates the grounding
	// guard, the evidence-sources guard and their absence.
	add(finSpec{Name: "no-loop-id-at-all", NoLoopID: true, Diag: sick,
		Recovery: &finRecovery{AutoApply: true, Risk: "low",
			Action: "retry"},
		RecoverySteps: 1, FailureChain: []string{"retry it"},
		Steps: twoSteps})
	// The recovery-plan lesson's outcome is the LOOP STATUS, and every
	// other recovery fixture is a "done" run, where the two spellings
	// agree.
	add(finSpec{Name: "recovery-plan-lesson-on-a-blocked-run",
		LoopStatus: "blocked", Diag: sick,
		Recovery: &finRecovery{AutoApply: false, Risk: "medium",
			Action: "ask a human"},
		Steps: twoSteps})
	// A dry run that is otherwise a perfect verified recovery: done, with
	// recovery steps and a chain. Without it, dry_run is masked by the
	// status test that follows it.
	add(finSpec{Name: "verified-recovery-is-off-in-a-dry-run",
		DryRun: true, RecoverySteps: 2,
		FailureChain: []string{"retrying step 2"}, Steps: twoSteps})
	add(finSpec{Name: "verified-recovery-goal-is-clipped",
		Goal: strings.Repeat("v", 200), RecoverySteps: 1,
		FailureChain: []string{"retry it"}, Steps: twoSteps})
	// `failure_chain or []` — a nil chain is written as an empty list,
	// which is not the same value as None in the recorded row.
	add(finSpec{Name: "nil-failure-chain-is-recorded-as-empty",
		NilFailureChain: true, Steps: twoSteps})
	// defer_lessons is the flag verbatim, NOT gated on the status.
	add(finSpec{Name: "defer-learning-on-a-blocked-run",
		LoopStatus: "blocked", DeferLearning: true, Steps: twoSteps})

	// A grounding call that returns [None] is not the same as one that
	// returns []: the first subscript succeeds and hands None to the
	// lesson row, which the port normalises to the empty list.
	add(finSpec{Name: "grounding-first-entry-is-null", Diag: sick,
		Grounding: [][]any{nil},
		Recovery: &finRecovery{AutoApply: true, Risk: "low",
			Action: "retry"}})

	return out
}

// goFinalizeRecord runs the Go port over one fixture and renders the same
// record the probe renders.
func goFinalizeRecord(s finSpec) map[string]any {
	calls := []map[string]any{}
	logs := []map[string]any{}
	var stderr strings.Builder

	rec := func(kv map[string]any) { calls = append(calls, kv) }
	at := func(level string) func(string) {
		return func(m string) {
			logs = append(logs, map[string]any{"level": level, "msg": m})
		}
	}
	fail := func(msg string) error {
		if msg == "" {
			return nil
		}
		return errors.New(msg)
	}

	var adapter any
	switch s.Adapter {
	case "truthy":
		adapter = finAdapter{modelKey: s.ModelKey, hasKey: s.ModelKey != ""}
	case "falsy":
		// The probe's FalsyAdapter. An empty pyval.List is the value
		// pyval.Truthy says no to; a named Go type would fall through to
		// the "unknown object" arm and silently test the truthy path.
		adapter = pyval.List{}
	}

	d := FinalizeDeps{
		Info:  at("info"),
		Warn:  at("warning"),
		Debug: at("debug"),
		ModelKey: func(a any) string {
			if o, ok := a.(finAdapter); ok && o.hasKey {
				return o.modelKey
			}
			return ""
		},
		StepEvidence: func(steps []looptypes.StepOutcome) string {
			return loop.StepEvidence(toLoopOutcomes(steps))
		},
		StepEvidenceBounded: func(steps []looptypes.StepOutcome,
			total, entry int) string {
			return loop.StepEvidenceBounded(toLoopOutcomes(steps), total, entry)
		},
	}

	dropped := map[string]bool{}
	for _, n := range s.DropNames {
		dropped[n] = true
	}
	// gone is "this name cannot be imported": the whole module is
	// missing, or this one name was dropped from it.
	gone := func(module bool, name string) bool {
		return module || dropped[name]
	}

	{
		d.DiagnoseLoop = func(loopID, project string) (Diagnosis, error) {
			rec(map[string]any{"call": "diagnose_loop", "loop_id": loopID,
				"project": project})
			if err := fail(s.DiagRaise); err != nil {
				return Diagnosis{}, err
			}
			return Diagnosis{FailureClass: s.Diag.FailureClass,
				Recommendation: s.Diag.Recommendation,
				Summary:        s.Diag.Summary}, nil
		}
		d.SaveDiagnosis = func(dg Diagnosis) error {
			rec(map[string]any{"call": "save_diagnosis",
				"failure_class": dg.FailureClass})
			return fail(s.SaveDiagRaise)
		}
		d.LoadLoopEvents = func(loopID string) (any, error) {
			rec(map[string]any{"call": "_load_loop_events",
				"loop_id": loopID})
			if err := fail(s.EventsRaise); err != nil {
				return nil, err
			}
			return []any{"ev"}, nil
		}
		d.BuildStepProfiles = func(events any) (any, error) {
			rec(map[string]any{"call": "_build_step_profiles",
				"events": events})
			if err := fail(s.ProfilesRaise); err != nil {
				return nil, err
			}
			return []any{"prof"}, nil
		}
		d.RunLenses = func(dg Diagnosis, profiles any) ([]LensResult, error) {
			rec(map[string]any{"call": "run_lenses",
				"failure_class": dg.FailureClass, "profiles": profiles})
			if err := fail(s.LensesRaise); err != nil {
				return nil, err
			}
			out := make([]LensResult, 0, len(s.Lenses))
			for _, l := range s.Lenses {
				out = append(out, LensResult{LensName: l.LensName,
					Action: l.Action})
			}
			return out, nil
		}
		d.AggregateLenses = func(dg Diagnosis, rs []LensResult) (Aggregate,
			error) {
			rec(map[string]any{"call": "aggregate_lenses", "n": len(rs)})
			if err := fail(s.AggRaise); err != nil {
				return Aggregate{}, err
			}
			return Aggregate{Confidence: s.Agg.Confidence,
				LensAgreement: s.Agg.LensAgreement,
				PrimaryAction: s.Agg.PrimaryAction}, nil
		}
		d.PlanRecovery = func(dg Diagnosis, useAdvisor bool) (*Recovery,
			error) {
			rec(map[string]any{"call": "plan_recovery",
				"failure_class": dg.FailureClass,
				"use_advisor":   useAdvisor})
			if err := fail(s.RecoveryRaise); err != nil {
				return nil, err
			}
			if s.Recovery == nil {
				return nil, nil
			}
			return &Recovery{AutoApply: s.Recovery.AutoApply,
				Risk: s.Recovery.Risk, Action: s.Recovery.Action}, nil
		}
	}

	{
		d.RecordTieredLesson = func(l TieredLesson) error {
			rec(map[string]any{"call": "record_tiered_lesson",
				"lesson_text": l.LessonText, "task_type": l.TaskType,
				"outcome": l.Outcome, "source_goal": l.SourceGoal,
				"confidence": l.Confidence, "lesson_type": l.LessonType,
				"evidence_sources": l.EvidenceSources,
				"grounding":        l.Grounding})
			return fail(s.TieredRaise)
		}
		d.StoreLesson = func(l StoredLesson) error {
			rec(map[string]any{"call": "_store_lesson",
				"task_type": l.TaskType, "outcome": l.Outcome,
				"lesson": l.Lesson, "source_goal": l.SourceGoal,
				"confidence": l.Confidence})
			return fail(s.StoreLessonRaise)
		}
		d.ReflectAndRecord = func(in ReflectIn) (*OutcomeRec, error) {
			rec(map[string]any{"call": "reflect_and_record",
				"goal": in.Goal, "status": in.Status,
				"result_summary":  in.ResultSummary,
				"lesson_evidence": in.LessonEvidence,
				"task_type":       in.TaskType, "project": in.Project,
				"tokens_in": in.TokensIn, "tokens_out": in.TokensOut,
				"elapsed_ms": in.ElapsedMS, "model": in.Model,
				"adapter": finTag(in.Adapter), "dry_run": in.DryRun,
				"failure_chain":  in.FailureChain,
				"recovery_steps": in.RecoverySteps, "loop_id": in.LoopID,
				"defer_lessons":     in.DeferLessons,
				"measurement_class": in.MeasurementClass,
				"handle_id":         in.HandleID,
				"stop_verdict":      in.StopVerdict,
				"stop_evidence":     in.StopEvidence,
				"pause_reason":      in.PauseReason})
			if err := fail(s.ReflectRaise); err != nil {
				return nil, err
			}
			if s.ReflectNone {
				return nil, nil
			}
			return &OutcomeRec{OutcomeID: s.OutcomeID}, nil
		}
		d.RecordStepTrace = func(outcomeID, goal string,
			steps []looptypes.StepOutcome, taskType string) error {
			rec(map[string]any{"call": "record_step_trace",
				"outcome_id": outcomeID, "goal": goal,
				"nsteps": len(steps), "task_type": taskType})
			return fail(s.TraceRaise)
		}
		d.ExtractStepLessons = func(goal string,
			steps []looptypes.StepOutcome, taskType string, ad any,
			loopID string) error {
			rec(map[string]any{"call": "extract_step_lessons",
				"goal": goal, "nsteps": len(steps), "task_type": taskType,
				"adapter": finTag(ad), "loop_id": loopID})
			return fail(s.StepLessonsRaise)
		}
	}

	{
		d.GroundLessons = func(texts []string, loopID string) ([][]any,
			error) {
			rec(map[string]any{"call": "ground_lessons_for_run",
				"texts": texts, "loop_id": loopID})
			if err := fail(s.GroundingRaise); err != nil {
				return nil, err
			}
			return s.Grounding, nil
		}
	}

	{
		d.WeightingEnabled = func() (bool, error) {
			rec(map[string]any{"call": "weighting_enabled"})
			if err := fail(s.WeightingRaise); err != nil {
				return false, err
			}
			return s.Weighting, nil
		}
		d.RefreshCache = func() error {
			rec(map[string]any{"call": "refresh_cache"})
			return fail(s.RefreshRaise)
		}
	}

	{
		d.RecordMaintenance = func(handleID, loopID string, ad any,
			verbose bool) (bool, error) {
			rec(map[string]any{"call": "record_maintenance",
				"handle_id": handleID, "loop_id": loopID,
				"adapter": finTag(ad), "verbose": verbose})
			if err := fail(s.RecordMaintRaise); err != nil {
				return false, err
			}
			return s.RecordMaint, nil
		}
	}

	reg := &MaintenanceRegistry{}
	d.DeferMaintenance = func(handleID string, fn func() error) error {
		rec(map[string]any{"call": "defer_maintenance_post_notify",
			"handle_id": handleID})
		if err := fail(s.DeferRaise); err != nil {
			return err
		}
		reg.DeferPostNotify(handleID, fn)
		return nil
	}
	d.RunPostRunMaintenance = func(ad any, verbose bool) {
		rec(map[string]any{"call": "run_post_run_maintenance",
			"adapter": finTag(ad), "verbose": verbose})
	}
	d.LoopIDScope = func(loopID string) (func(), error) {
		rec(map[string]any{"call": "loop_id_scope", "loop_id": loopID})
		if err := fail(s.ScopeRaise); err != nil {
			return nil, err
		}
		return func() {}, nil
	}

	{
		d.TelegramNotify = func(msg string) error {
			rec(map[string]any{"call": "telegram_notify", "msg": msg})
			return fail(s.TelegramRaise)
		}
	}

	// The crystallize seam: real code on both sides, recorders under it.
	d.Crystallize = CrystallizeDeps{
		Warn:   at("warning"),
		Stderr: func(l string) { stderr.WriteString(l + "\n") },
		LoadSkills: func() ([]Skill, error) {
			rec(map[string]any{"call": "load_skills"})
			out := make([]Skill, 0, len(s.Existing))
			for _, n := range s.Existing {
				out = append(out, Skill{Name: n})
			}
			return out, nil
		},
		ExtractSkills: func(outcomes []pyval.Obj, ad any) ([]Skill, error) {
			o := outcomes[0]
			get := func(k string) any {
				for _, f := range o {
					if f.Key == k {
						return f.Val
					}
				}
				return nil
			}
			steps, _ := get("steps").(pyval.List)
			rec(map[string]any{"call": "extract_skills",
				"n": len(outcomes), "adapter": finTag(ad),
				"goal": get("goal"), "status": get("status"),
				"task_type": get("task_type"), "summary": get("summary"),
				"nsteps": len(steps), "project": get("project")})
			out := make([]Skill, 0, len(s.Extracted))
			for _, n := range s.Extracted {
				out = append(out, Skill{Name: n})
			}
			return out, nil
		},
		SaveSkill: func(sk Skill) error {
			rec(map[string]any{"call": "save_skill", "name": sk.Name})
			return nil
		},
		SynthesizeSkill: func(goal, summary, loopID string, ad any,
			verbose bool) error {
			rec(map[string]any{"call": "synthesize_skill", "goal": goal,
				"outcome_summary": summary, "source_loop_id": loopID,
				"adapter": finTag(ad), "verbose": verbose})
			return nil
		},
	}

	// The import surface, name by name. A name that cannot be imported is
	// a nil func — the port's stand-in for the ImportError — and it lands
	// in whatever handler wraps the STATEMENT that would have imported
	// it, which is the thing under test.
	for _, x := range []struct {
		module bool
		name   string
		clear  func()
	}{
		{s.MissingIntrospect, "diagnose_loop", func() { d.DiagnoseLoop = nil }},
		{s.MissingIntrospect, "save_diagnosis", func() { d.SaveDiagnosis = nil }},
		{s.MissingIntrospect, "_load_loop_events", func() { d.LoadLoopEvents = nil }},
		{s.MissingIntrospect, "_build_step_profiles", func() { d.BuildStepProfiles = nil }},
		{s.MissingIntrospect, "run_lenses", func() { d.RunLenses = nil }},
		{s.MissingIntrospect, "aggregate_lenses", func() { d.AggregateLenses = nil }},
		{s.MissingIntrospect, "plan_recovery", func() { d.PlanRecovery = nil }},
		{s.MissingMemory, "record_tiered_lesson", func() { d.RecordTieredLesson = nil }},
		{s.MissingMemory, "_store_lesson", func() { d.StoreLesson = nil }},
		{s.MissingMemory, "reflect_and_record", func() { d.ReflectAndRecord = nil }},
		{s.MissingMemory, "record_step_trace", func() { d.RecordStepTrace = nil }},
		{s.MissingMemory, "extract_step_lessons", func() { d.ExtractStepLessons = nil }},
		{s.MissingMintGrounding, "ground_lessons_for_run", func() { d.GroundLessons = nil }},
		{s.MissingPortability, "weighting_enabled", func() { d.WeightingEnabled = nil }},
		{s.MissingPortability, "refresh_cache", func() { d.RefreshCache = nil }},
		{s.MissingTailJobs, "record_maintenance", func() { d.RecordMaintenance = nil }},
		{false, "loop_id_scope", func() { d.LoopIDScope = nil }},
		{s.MissingTelegram, "telegram_notify", func() { d.TelegramNotify = nil }},
	} {
		if gone(x.module, x.name) {
			x.clear()
		}
	}

	outcomes := make([]looptypes.StepOutcome, 0, len(s.Steps))
	for i, st := range s.Steps {
		outcomes = append(outcomes, looptypes.StepOutcome{Index: i + 1,
			Text: st.Text, Status: st.Status, Result: st.Result})
	}

	FinalizeLoop(FinalizeArgs{
		LoopID: s.LoopID, Goal: s.Goal, Project: s.Project,
		LoopStatus: s.LoopStatus, StepOutcomes: outcomes, Adapter: adapter,
		DryRun: s.DryRun, Verbose: s.Verbose, TotalTokensIn: s.TokensIn,
		TotalTokensOut: s.TokensOut, ElapsedMS: s.ElapsedMS,
		HadNoMatching: s.HadNoMatching, FailureChain: s.FailureChain,
		RecoverySteps: s.RecoverySteps, DeferLearning: s.DeferLearning,
		DeferMaintenance: s.DeferMaintenance,
		MeasurementClass: s.MeasurementClass, HandleID: s.HandleID,
		StopVerdict: s.StopVerdict, StopEvidence: s.StopEvidence,
		PauseReason: s.PauseReason}, d)

	drained := -1
	if s.Drain {
		drained = reg.DrainDeferred(s.HandleID, at("warning"))
	}

	return map[string]any{"name": s.Name, "calls": calls, "logs": logs,
		"stderr": stderr.String(), "drained": drained}
}

// toLoopOutcomes crosses the package seam: internal/loop owns the
// StepOutcome that _step_evidence reads, and it is a different struct
// from looptypes'. Only the four fields the evidence renderer touches
// carry over — a wider copy would be claiming this seam depends on
// fields it does not.
func toLoopOutcomes(in []looptypes.StepOutcome) []loop.StepOutcome {
	out := make([]loop.StepOutcome, 0, len(in))
	for _, s := range in {
		out = append(out, loop.StepOutcome{Index: s.Index, Step: s.Text,
			Status: s.Status, Result: s.Result})
	}
	return out
}

func runFinalizeProbe(t *testing.T, dir string, scs []finSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "fin-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "finalize_probe.py.tpl", srcDirLF(t),
		specPath)
	out, err := cmd.Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			t.Fatalf("probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("probe failed: %v", err)
	}
	var recs []map[string]any
	if err := json.Unmarshal(out, &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestFinalizeLoopMatchesCPython(t *testing.T) {
	scs := finScenarios()
	pyRecs := runFinalizeProbe(t, t.TempDir(), scs)
	if len(pyRecs) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(pyRecs), len(scs))
	}
	for i, s := range scs {
		t.Run(s.Name, func(t *testing.T) {
			got, want := canonMint(goFinalizeRecord(s)), canonMint(pyRecs[i])
			if want["name"] != s.Name {
				t.Fatalf("record %d is %v, want %s", i, want["name"], s.Name)
			}
			a, _ := json.MarshalIndent(got, "", "  ")
			b, _ := json.MarshalIndent(want, "", "  ")
			if string(a) != string(b) {
				t.Errorf("go:\n%s\npy:\n%s", a, b)
			}
		})
	}
}

func TestFinalizeScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range finScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}

// The two evidence strings are supposed to be DIFFERENT — one prompt-grade
// and wide, one store-grade and clipped. A port that wired both to the
// same budget would pass every scenario whose results are short, so this
// asserts the split directly on a fixture built to exceed the store
// budget.
func TestStoreBudgetActuallyClipsRelativeToThePromptOne(t *testing.T) {
	var steps []looptypes.StepOutcome
	for i := 0; i < 6; i++ {
		steps = append(steps, looptypes.StepOutcome{Index: i + 1,
			Text:   fmt.Sprintf("step %d", i+1),
			Status: "done", Result: strings.Repeat("x", 900)})
	}
	wide := loop.StepEvidence(toLoopOutcomes(steps))
	narrow := loop.StepEvidenceBounded(toLoopOutcomes(steps),
		budget.StoreTotalBudget, budget.StoreEntryCap)
	if len(narrow) >= len(wide) {
		t.Fatalf("store-grade evidence (%d) is not shorter than "+
			"prompt-grade (%d)", len(narrow), len(wide))
	}
	if budget.StoreEntryCap != 500 || budget.StoreTotalBudget != 4000 {
		t.Fatalf("store budgets drifted: entry=%d total=%d",
			budget.StoreEntryCap, budget.StoreTotalBudget)
	}
}

// The in-process deferral guard has no CPython counterpart to compare
// against: `defer_maintenance_post_notify` is a module-level function in
// loop_finalize itself, not an import, so Python's version of "this name
// is missing" does not exist. The Go Deps struct permits nil anyway, and
// the behaviour it must have is the ladder's: an unavailable tier falls
// through to the next one, never silently counts as deferred.
//
// A battery row alone reported this as SURVIVED, correctly — no fixture
// can express it. The check belongs here instead, where it is about the
// Go API rather than about the port.
func TestNilDeferMaintenanceFallsBackInline(t *testing.T) {
	var inline int
	var debug []string
	reg := &MaintenanceRegistry{}
	d := FinalizeDeps{
		Debug:                 func(m string) { debug = append(debug, m) },
		RunPostRunMaintenance: func(any, bool) { inline++ },
		LoopIDScope:           func(string) (func(), error) { return func() {}, nil },
	}
	maintenancePhase(FinalizeArgs{DeferMaintenance: true, HandleID: "h-1"}, d)
	if inline != 1 {
		t.Errorf("inline maintenance ran %d times, want 1", inline)
	}
	if len(debug) != 2 {
		t.Fatalf("want a debug line per skipped tier, got %v", debug)
	}
	for _, want := range []string{"maintenance record failed",
		"maintenance defer failed"} {
		found := false
		for _, m := range debug {
			if strings.Contains(m, want) {
				found = true
			}
		}
		if !found {
			t.Errorf("no debug line mentioning %q in %v", want, debug)
		}
	}
	if n := reg.DrainDeferred("h-1", func(string) {}); n != 0 {
		t.Errorf("registry holds %d callables, want 0", n)
	}
}
