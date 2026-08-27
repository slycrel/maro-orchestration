package loopfinalize

// run_post_run_maintenance: the run's maintenance tail, extracted from
// _finalize_loop by the async-tail decree (2026-08-11) so the closure lane
// can defer it past the run_completed notify. The user hears the outcome
// first, then the same process does the same work — deferral moves WHEN,
// not what.
//
// Five blocks, and the interesting thing about them is that they do NOT
// fail the same way. Four carry a bare `except ImportError: pass` above
// their general handler and one does not, so a missing module is SILENT in
// four of them and a debug line in the fifth. Two report at DEBUG and two
// at WARNING. Nothing in the function's shape tells you which is which;
// the differential is what says so.
//
// It never raises. Callers gate dry_run — nothing here should run for a
// dry run. A crash before a deferred drain loses at most one cycle: every
// sub-system is threshold/cadence-based and re-fires on the next run's
// tail, so a lost cycle self-heals.

import (
	"errors"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

const (
	// InspectorStandardPassLimit is the bare `50` in `_limit =
	// DEEP_PASS_LIMIT if _mode == "deep" else 50`. It is a literal in
	// Python with no name and no config key, which is why it is a
	// constant here rather than a Registry row: naming it in Go does not
	// make it configurable, it makes it findable.
	InspectorStandardPassLimit = 50
	// EvolverRunCadenceDefault and InspectorRunCadenceDefault are 0 =
	// off, so a fresh install runs neither. InspectorDeepEveryDefault is
	// 5 — but see maintenance() for what `or 0` does to it.
	EvolverRunCadenceDefault   = 0
	InspectorRunCadenceDefault = 0
	InspectorDeepEveryDefault  = 5
)

// EvolverReport and InspectorReport are read through `getattr(r, name,
// default)`, and every default is the zero value of the field's type. An
// absent attribute and a zero attribute are therefore the same answer
// here, which is why these are plain fields: modelling absence would be
// modelling a distinction the code cannot make.
type EvolverReport struct {
	OutcomesReviewed int
	// Suggestions is read as `len(getattr(r, "suggestions", []) or [])`,
	// so absent, None and empty all count zero.
	Suggestions []any
}

type InspectorReport struct {
	InspectedSessions int
}

// MaintenanceDeps is every module the tail reaches. A nil func is the
// ImportError its import would raise — and WHICH handler catches that is
// the thing under test, so the guards sit where the import statements sit.
type MaintenanceDeps struct {
	// RunSkillMaintenance is evolver.run_skill_maintenance. The adapter is
	// threaded through (arch-04, 2026-07-09): without it the refight_rule
	// half of decay-by-invalidation is structurally unreachable, and this
	// is the only live caller path.
	RunSkillMaintenance func(adapter any) error

	// RunHealthProbes is system_health.run_health_probes — the ONE block
	// with no ImportError arm, so a missing module is a debug line here
	// and silence everywhere else.
	RunHealthProbes func() error

	RunStatisticalScans func(verbose bool) ([]any, error)
	SaveSuggestions     func([]any) error

	// ConfigGet is config.get. It is imported separately by the evolver
	// block and the inspector block, so a fixture can fail one and not
	// the other.
	ConfigGet func(key string, def any) (any, error)

	EvolverCadenceTick func(cadence int) (bool, error)
	// RunEvolver is imported INSIDE the `if evolver_cadence_tick(...)`,
	// which is observable: a missing run_evolver costs nothing at all on
	// a run whose cadence does not fire.
	RunEvolver func(adapter any, verbose bool) (EvolverReport, error)

	InspectorCadenceTick func(cadence, deepEvery int) (string, error)
	RunInspector         func(limit int, adapter any, verbose bool) (InspectorReport, error)
	// DeepPassLimit is inspector.DEEP_PASS_LIMIT, imported on the same
	// statement as the two functions above — a POINTER so a fixture can
	// drop exactly this name and nothing else.
	DeepPassLimit *int

	Info  func(string)
	Warn  func(string)
	Debug func(string)
}

func (d MaintenanceDeps) info(format string, a ...any) {
	if d.Info != nil {
		d.Info(fmt.Sprintf(format, a...))
	}
}

func (d MaintenanceDeps) warnf(format string, a ...any) {
	if d.Warn != nil {
		d.Warn(fmt.Sprintf(format, a...))
	}
}

func (d MaintenanceDeps) debugf(format string, a ...any) {
	if d.Debug != nil {
		d.Debug(fmt.Sprintf(format, a...))
	}
}

// isImportErr is `except ImportError`. It answers yes for the port's own
// import sentinel AND for a CALLEE that raised ImportError of its own —
// Python's handler cannot tell those apart, and neither may this.
func isImportErr(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, errImport) {
		return true
	}
	var pe *pyval.PyErr
	if errors.As(err, &pe) {
		return pe.Class == "ImportError"
	}
	return false
}

// cfgInt is `int(_cfg_get(key, def) or 0)`.
//
// The `or 0` runs FIRST, so a config value of None, "" or 0 never reaches
// int() at all — and a non-empty string that is not a number does, where
// it raises ValueError and takes the whole block to its warning.
func cfgInt(d MaintenanceDeps, key string, def any) (int, error) {
	raw, err := d.ConfigGet(key, def)
	if err != nil {
		return 0, err
	}
	if !pyval.Truthy(raw) {
		return 0, nil
	}
	return pyval.Int(raw)
}

// RunPostRunMaintenance is run_post_run_maintenance.
func RunPostRunMaintenance(adapter any, verbose bool, d MaintenanceDeps) {
	skillMaintenance(adapter, d)
	healthProbes(d)
	statisticalScans(verbose, d)
	evolverCadence(adapter, verbose, d)
	inspectorCadence(adapter, verbose, d)
}

// Phase 32: auto-promote skills that meet threshold, rather than waiting
// for the evolver heartbeat.
func skillMaintenance(adapter any, d MaintenanceDeps) {
	// EQUIVALENT MUTANT: spelling the nil-dep case as `errImport` and
	// spelling it as a plain nil produce the same output on every input,
	// because THIS block has an ImportError arm and both answers are
	// silent. The sentinel is kept because it is the honest name for the
	// state — and three lines into healthProbes the same substitution is
	// NOT equivalent, which is the whole point of the two spellings
	// sitting next to each other. The battery row is kept and marked
	// `equivalent`; a run that KILLS it means this reasoning went stale.
	//
	// The equivalence is with a NIL error specifically. A plain non-nil
	// error is a different mutation and the battery kills it, because
	// isImportErr recognises the sentinel and the PyErr class and nothing
	// else — which is what makes `errImport` the load-bearing spelling
	// here rather than merely the honest one.
	err := errImport
	if d.RunSkillMaintenance != nil {
		err = d.RunSkillMaintenance(adapter)
	}
	if err == nil || isImportErr(err) {
		return
	}
	d.debugf("skill maintenance failed (non-critical): %s", err)
}

// Liveness probes (2026-07-29): the same no-cron cadence decision as skill
// maintenance — health rides goal-run closure. Report-only, and internally
// shielded, but belt-and-braces here too.
//
// This is the block with NO ImportError arm. A missing system_health is
// reported; a missing evolver, three lines up, is not.
func healthProbes(d MaintenanceDeps) {
	err := errImport
	if d.RunHealthProbes != nil {
		err = d.RunHealthProbes()
	}
	if err == nil {
		return
	}
	d.debugf("health probes failed (non-critical): %s", err)
}

// BACKLOG #13 (2026-07-03): the evolver's five statistical scanners, per
// run instead of per heartbeat tick — "app, not OS": no daemon, no LLM
// calls (safe at this cadence), observational only, never auto-applied.
func statisticalScans(verbose bool, d MaintenanceDeps) {
	if err := scans(verbose, d); err != nil && !isImportErr(err) {
		d.debugf("post-run statistical scan failed (non-critical): %s", err)
	}
}

func scans(verbose bool, d MaintenanceDeps) error {
	// Two import statements, both above the call, so either name missing
	// costs the scan itself.
	if d.RunStatisticalScans == nil || d.SaveSuggestions == nil {
		return errImport
	}
	sugg, err := d.RunStatisticalScans(verbose)
	if err != nil {
		return err
	}
	// `if _stat_suggestions:` — an empty list saves nothing and logs
	// nothing, so a scan that found none is indistinguishable from a scan
	// that did not run, in the log.
	if len(sugg) == 0 {
		return nil
	}
	if err := d.SaveSuggestions(sugg); err != nil {
		return err
	}
	d.info("post-run statistical scan: %d suggestion(s) saved", len(sugg))
	return nil
}

// Evolver meta-cycle on run cadence (2026-07-09 supervision decision):
// every N-th real run finalization triggers run_evolver(). The meta-cycle
// rides run completions instead of a timer — no daemon, no self-rearming
// loop, and no runs means no evolver, which is the correct no-op.
//
// Its handler is a WARNING where the three above are debug lines.
func evolverCadence(adapter any, verbose bool, d MaintenanceDeps) {
	if err := evolverCycle(adapter, verbose, d); err != nil &&
		!isImportErr(err) {
		d.warnf("run-cadence evolver cycle failed (non-fatal): %s", err)
	}
}

func evolverCycle(adapter any, verbose bool, d MaintenanceDeps) error {
	if d.ConfigGet == nil || d.EvolverCadenceTick == nil {
		return errImport
	}
	cadence, err := cfgInt(d, "evolver.run_cadence", EvolverRunCadenceDefault)
	if err != nil {
		return err
	}
	fired, err := d.EvolverCadenceTick(cadence)
	if err != nil {
		return err
	}
	if !fired {
		return nil
	}
	// `from evolver import run_evolver` lives HERE, inside the if.
	if d.RunEvolver == nil {
		return errImport
	}
	rep, err := d.RunEvolver(adapter, verbose)
	if err != nil {
		return err
	}
	d.info("run-cadence evolver cycle fired (cadence=%d): reviewed=%d "+
		"suggestions=%d", cadence, rep.OutcomesReviewed,
		len(rep.Suggestions))
	return nil
}

// Inspector on run cadence (decision 1addc859, 2026-08-08): the evolver's
// lane, finally extended to the inspector. Its threshold cluster had live
// inputs but no live caller once the heartbeat daemon stopped, so
// inspection-log.jsonl never existed and the three friction readers
// always saw empty.
func inspectorCadence(adapter any, verbose bool, d MaintenanceDeps) {
	if err := inspectorCycle(adapter, verbose, d); err != nil &&
		!isImportErr(err) {
		d.warnf("run-cadence inspector failed (non-fatal): %s", err)
	}
}

func inspectorCycle(adapter any, verbose bool, d MaintenanceDeps) error {
	if d.ConfigGet == nil || d.InspectorCadenceTick == nil ||
		d.RunInspector == nil || d.DeepPassLimit == nil {
		return errImport
	}
	cadence, err := cfgInt(d, "inspector.run_cadence",
		InspectorRunCadenceDefault)
	if err != nil {
		return err
	}
	// `int(_cfg_get("inspector.deep_every", 5) or 0)` — the default is 5
	// but the `or 0` is not a no-op: a configured 0, "" or None becomes
	// 0, and 0 is passed to the tick as a real value rather than falling
	// back to the default. The default only applies when the key is
	// ABSENT.
	deepEvery, err := cfgInt(d, "inspector.deep_every",
		InspectorDeepEveryDefault)
	if err != nil {
		return err
	}
	// cadence <= 0 short-circuits BEFORE the tick (2026-08-08 review):
	// counting while disabled created the state file on fresh installs
	// and made a later enable fire immediately instead of waiting N
	// enabled runs.
	mode := "none"
	if cadence > 0 {
		mode, err = d.InspectorCadenceTick(cadence, deepEvery)
		if err != nil {
			return err
		}
	}
	if mode == "none" {
		return nil
	}
	limit := InspectorStandardPassLimit
	if mode == "deep" {
		limit = *d.DeepPassLimit
	}
	rep, err := d.RunInspector(limit, adapter, verbose)
	if err != nil {
		return err
	}
	d.info("run-cadence inspector fired (%s pass, cadence=%d, limit=%d): "+
		"%d outcome(s) inspected", mode, cadence, limit,
		rep.InspectedSessions)
	return nil
}
