// Package loopinit ports the deterministic half of src/loop_init.py.
//
// PORTED: `_budget_gate` and its `_coerce_cap` helper -- the spend
// circuit-breaker that decides, BEFORE any tokens are spent, what this run
// may cost and whether it may start at all.
//
// NOT PORTED, named here rather than left to be discovered:
//
//   - `_initialize_loop` (Phase A proper). It builds the LLM adapter,
//     assigns a model by role through the conductor, opens the interrupt
//     queue, takes the project admission slot and the run lease, and writes
//     a captain's-log event. Every one of those is orchestration this port
//     does not run.
//   - `_DryRunAdapter`, a test double for that adapter.
//   - `_stamp_refusal_verdict`'s WRITE half. The gate's decision is here;
//     persisting it to run metadata and recording the plan.budget_gate edge
//     belongs with the run-dir machinery.
//
// WHY THE GATE AND NOT THE PHASE. The caps are a standing decree rather
// than an implementation detail: "caps are circuit-breakers, not cost
// optimizers" (2026-07-29), which is why the per-run number is
// max($10, 4 x p90 of successful runs) rather than a tuned figure, and why
// a malformed config value fails CLOSED to the default instead of
// disabling the cap. A port that reproduced the loop and quietly lost that
// ladder would run uncapped on a fresh box.
package loopinit

import (
	"strconv"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Safe-by-default spend caps (2026-07-09, 1.0 posture). A fresh install
// must never run uncapped; config overrides, 0/null disables.
//
// Raised $5 -> $10 by decree 2026-07-29: the breaker is a circuit-breaker
// for pathological runs, not a cost optimizer -- a sanctioned power-tier
// step alone can cost ~$3, and a $2.40 cap once killed a run holding a
// finished memo. When budget.per_run_usd is ABSENT the cap is data-driven:
// max(floor, KILL x p90 of successful runs). History says what "well past
// anything that ever worked" costs; the floors keep a cold-start box sane.
const (
	DefaultPerRunUSD  = 10.0
	DefaultDailyUSD   = 25.0
	WarnFloorUSD      = 2.50 // early-warn floor: "left typical territory"
	KillP90Multiplier = 4.0  // breaker = 4x the p90 successful-run cost
)

// ConfigValue is one `config.get(key, default)` answer.
//
// Absent is NOT the same as a null, and the difference decides the whole
// per-run ladder: Python distinguishes them with a `_CONFIG_ABSENT`
// sentinel object, because an ABSENT key means "auto, scale with history"
// while an explicit null means "uncapped, I meant it". A port that
// collapsed both to nil would turn every fresh box's auto-cap into no cap
// at all.
//
// The consequence for callers: the ZERO VALUE of this struct is the
// dangerous one. `ConfigValue{}` reads as PRESENT-and-null, which is the
// uncapped opt-out, so a BudgetEnv assembled by leaving fields off gets
// no breaker rather than the fresh-box default. Build them with Absent()
// and Present(); a test in this package was written the wrong way once
// and passed for the wrong reason.
type ConfigValue struct {
	Absent bool
	Value  any
}

// Absent is the missing-key answer.
func Absent() ConfigValue { return ConfigValue{Absent: true} }

// Present is an explicit config value, null included (pass nil).
func Present(v any) ConfigValue { return ConfigValue{Value: v} }

// BudgetEnv is everything `_budget_gate` reads from outside itself. It is
// a struct rather than live calls so the ladder can be driven against
// CPython case by case; NewBudgetEnv builds the production one.
type BudgetEnv struct {
	PerRun ConfigValue // config `budget.per_run_usd`
	Warn   ConfigValue // config `budget.warn_usd`
	Daily  ConfigValue // config `budget.daily_usd`

	// P90 is `metrics.successful_run_cost_p90()`. Nil is BOTH of Python's
	// two failure shapes -- the import/call raising, and the function
	// answering None on thin history -- because `if _p90:` treats them
	// identically. So does a p90 of 0.0, which is why the zero VALUE is
	// distinguishable from nil here and still lands on the floor.
	P90 *float64

	// SpendToday is `metrics.spend_today()`. SpendTodayError is the arm
	// where that call raises: Python's except swallows it and the run
	// proceeds UNGATED, which is deliberate (a broken ledger must not
	// strand every run) and is reproduced rather than improved.
	//
	// The error TEXT is carried because the except logs it -- `str(exc)`
	// reaches an operator as "daily cap check failed (non-blocking): ..."
	// and is the only trace that the gate was skipped at all. A port that
	// modelled the failure as a bare bool dropped that line silently,
	// which the differential caught on its first run.
	SpendToday       float64
	SpendTodayFailed bool
	SpendTodayError  string
}

// BudgetDecision is the gate's whole effect: two fields written onto the
// LoopContext, and either a refusal or nothing.
type BudgetDecision struct {
	// CostBudget is ctx.cost_budget AFTER the gate. Nil is Python's None,
	// which the mid-loop hard stop reads as uncapped.
	CostBudget *float64
	// CostWarnUSD is ctx.cost_warn_usd after the gate. Nil is None, which
	// the mid-loop check reads as "gate never ran" and falls back to the
	// legacy 80%-of-budget line. Advisory: enforced nowhere.
	CostWarnUSD *float64

	// Refused is true when the daily cap stops the run before it starts.
	Refused bool
	// Reason is the stuck_reason AND the stop_evidence, byte for byte:
	// it reaches an operator through the notify escalation and through the
	// run record, so its exact text is contract.
	Reason string
	// The reopen payload's numbers (§13b): an out-of-budget stop reopens
	// trivially with budget, so the revisit scanner checks the condition
	// against numbers rather than prose.
	DailyCapUSD float64
	SpentUSD    float64

	// Warnings are the log.warning lines, in order.
	Warnings []string
}

// ReopenPayload is the §13b evidence payload for a daily-cap refusal.
func (d BudgetDecision) ReopenPayload() map[string]any {
	return map[string]any{
		"kind":          "budget-daily",
		"daily_cap_usd": pyval.Round(d.DailyCapUSD, 2),
		"spent_usd":     pyval.Round(d.SpentUSD, 2),
	}
}

// BudgetIn is the caller-supplied half: the loop's own state before the
// gate runs.
type BudgetIn struct {
	// CostBudget is the caller's `cost_budget` argument, already on the
	// context. NON-NIL MEANS THE CALLER WINS -- the whole config ladder is
	// skipped, including an explicit 0.0, which is a caller saying
	// uncapped.
	CostBudget *float64
	// CostWarnUSD is ctx.cost_warn_usd. Non-nil skips the warn ladder.
	CostWarnUSD *float64
	DryRun      bool
}

// BudgetGate is Python `_budget_gate(ctx, goal=, project=, dry_run=)`.
//
// Two layers, and they are independent:
//
//   - PER-RUN: callers rarely pass cost_budget, so an unattended run was
//     uncapped. Config supplies the default; an explicit caller arg still
//     wins. Enforced mid-loop by the cost hard-stop, not here.
//   - DAILY: per-run caps do not stop a substrate burning through runs one
//     under-cap loop at a time, so the cross-run spend ledger is gated
//     BEFORE any tokens are spent.
//
// dry_run skips everything (it burns nothing) and, importantly, writes
// NEITHER field -- a dry run's context comes out exactly as it went in.
//
// NEVER FAILS. Python wraps each of the three blocks in its own
// try/except, so a failure in one leaves the other two to run; the Go
// spelling is that each block's inputs carry their own failure flag and no
// block can panic. The daily block swallowing its error means a broken
// spend ledger lets the run PROCEED, which is the deliberate direction:
// the gate refuses to start a run only on a number it actually read.
func BudgetGate(in BudgetIn, env BudgetEnv) BudgetDecision {
	d := BudgetDecision{CostBudget: in.CostBudget, CostWarnUSD: in.CostWarnUSD}
	if in.DryRun {
		return d
	}
	p90 := env.P90

	// --- per-run -----------------------------------------------------
	if d.CostBudget == nil {
		if env.PerRun.Absent {
			// AUTO. `if _p90:` is a truthiness test, so a p90 of 0.0 --
			// a real answer from a box whose successful runs cost
			// nothing -- takes the floor branch alongside None.
			//
			// EQUIVALENT MUTANT: dropping `&& *p90 != 0` changes no
			// answer, because the branch it would newly take computes
			// maxF(DefaultPerRunUSD, 4*0) = DefaultPerRunUSD, which is
			// what the else arm returns. The absorption is the whole
			// reason, so it is asserted rather than trusted -- see
			// TestTheP90TruthinessTestIsAbsorbedByTheFloor. A negative
			// p90 is truthy on BOTH sides and needs no help; NaN too
			// (NaN != 0 in Go, and a NaN is truthy in CPython).
			if p90 != nil && *p90 != 0 {
				d.CostBudget = ptr(maxF(DefaultPerRunUSD, KillP90Multiplier**p90))
			} else {
				d.CostBudget = ptr(DefaultPerRunUSD)
			}
		} else {
			perRun, warn := coerceCap(env.PerRun, "budget.per_run_usd", DefaultPerRunUSD)
			if warn != "" {
				d.Warnings = append(d.Warnings, warn)
			}
			// `if _per_run > 0` -- so an explicit 0 or null leaves
			// cost_budget as None. That is the uncapped opt-out, and it
			// is NOT the same as the coercion failing, which fails closed
			// to the default above.
			if perRun > 0 {
				d.CostBudget = ptr(perRun)
			}
		}
	}

	// --- the early-warn line -----------------------------------------
	// `if ctx.cost_budget and ...` is TRUTHINESS, so a cost_budget the
	// caller pinned at 0.0 (uncapped, deliberately) skips the warn line
	// too. Nothing to warn about below an absent breaker.
	if d.CostBudget != nil && *d.CostBudget != 0 && d.CostWarnUSD == nil {
		var w float64
		if env.Warn.Absent {
			// `float(_p90 or 0.0)` -- the same truthiness again, so a
			// None p90 and a 0.0 one both give the floor.
			var base float64
			if p90 != nil {
				base = *p90
			}
			w = maxF(WarnFloorUSD, base)
		} else {
			var warn string
			w, warn = coerceCap(env.Warn, "budget.warn_usd", WarnFloorUSD)
			if warn != "" {
				d.Warnings = append(d.Warnings, warn)
			}
		}
		// The warn line must PRECEDE the breaker. A configured warn at or
		// above the cap would never fire before the run died, so it is
		// pulled back to 80% -- and `if _warn and` means a warn of 0
		// (explicitly disabled) is left at 0 rather than becoming 80% of
		// the cap.
		if w != 0 && w >= *d.CostBudget {
			w = *d.CostBudget * 0.8
		}
		d.CostWarnUSD = ptr(w)
	}

	// --- daily -------------------------------------------------------
	dailyCap, warn := coerceCap(env.Daily, "budget.daily_usd", DefaultDailyUSD)
	if warn != "" {
		d.Warnings = append(d.Warnings, warn)
	}
	if dailyCap > 0 {
		if env.SpendTodayFailed {
			// Python's `except` wraps the WHOLE daily block, so a raising
			// spend_today logs and returns None -- the run proceeds. The
			// coercion above has already run and may have logged its own
			// warning, which is why this is a second line and not a
			// replacement for the first.
			d.Warnings = append(d.Warnings,
				"budget gate: daily cap check failed (non-blocking): "+
					env.SpendTodayError)
			return d
		}
		if env.SpendToday >= dailyCap {
			d.Refused = true
			d.DailyCapUSD = dailyCap
			d.SpentUSD = env.SpendToday
			d.Reason = "daily budget exhausted: $" + fixed2(env.SpendToday) +
				" spent today >= budget.daily_usd $" + fixed2(dailyCap) +
				" — refusing to start; resets at UTC midnight"
			d.Warnings = append(d.Warnings, "loop refused to start — "+d.Reason)
		}
	}
	return d
}

// coerceCap is Python's nested `_coerce_cap`: a config value becomes a
// float cap, where 0/null is an explicit uncapped opt-out (0.0).
//
// A MALFORMED VALUE FAILS CLOSED to the default cap, with a warning. A
// typo in budget config must never silently disable the caps -- which is
// the opposite of what `float(raw)` raising into the enclosing except
// would have done, since that except leaves cost_budget None.
//
// `float(raw)` is Python's, not a JSON parse: it accepts a numeric STRING
// ("12.5", " 12.5 ", "1e3", "inf", "nan"), rejects everything else, and
// treats True as 1.0. pyval.Float is that conversion.
func coerceCap(cv ConfigValue, key string, def float64) (float64, string) {
	raw := cv.Value
	if cv.Absent {
		raw = def
	}
	if raw == nil {
		return 0, ""
	}
	if f, ok := pyval.Float(raw); ok {
		return f, ""
	}
	// pyval.Repr renders a Go MAP as a placeholder rather than as
	// `{'a': 1}`, because a Go map has no order to render and inventing
	// one would be a worse answer than refusing. That reaches this line
	// whenever a malformed config value is a MAPPING -- config values
	// arrive as map[string]any from yaml.v3, so there is no ordered type
	// to reach for here.
	//
	// Noted, not reconciled. The DECISION is identical either way (fail
	// closed to the default cap), and the difference is confined to how
	// the offending value is spelled inside one warning line. The
	// differential pins the port's own text and asserts CPython's differs
	// only in that span, so if the rendering ever changes on either side
	// it is a failure rather than a surprise.
	return def, "budget gate: " + key + "=" + pyval.Repr(raw) +
		" is not a number — using default $" + fixed2(def)
}

// fixed2 is Python's `"%.2f"`: two decimals, half-to-even on the exact
// double, and never scientific notation. A cap of 1e21 prints its digits.
func fixed2(f float64) string {
	s := strconv.FormatFloat(f, 'f', 2, 64)
	// Python renders a NaN as "nan" and an infinity as "inf"/"-inf" under
	// %f; Go's FormatFloat gives "NaN" and "+Inf". A malformed config
	// value can reach here as a real float, because float("nan") succeeds.
	switch s {
	case "NaN":
		return "nan"
	case "+Inf":
		return "inf"
	case "-Inf":
		return "-inf"
	}
	return s
}

func maxF(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func ptr(f float64) *float64 { return &f }
