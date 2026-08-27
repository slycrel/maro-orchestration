package loopinit

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

func srcDirLI(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// The probe drives the REAL `_budget_gate`, with `config` and `metrics`
// stubbed. Both stubs are the point rather than a convenience:
//
//   - config, because reading this box's real ~/.maro/config.yml would make
//     every fixture depend on the machine and on whatever another session
//     had written that hour.
//   - metrics, because `spend_today()` and `successful_run_cost_p90()` read
//     the LIVE workspace ledger under ~/.maro. A differential that touches
//     that is a differential that can damage the thing it measures, and
//     this file must never write there.
//
// `notify.emit` is stubbed for the same reason: the refusal path sends an
// escalation, and a test suite must not.
const budgetProbe = `
import json, sys, types

sys.path.insert(0, sys.argv[1])

state = {}

cfg = types.ModuleType('config')
_ABSENT = object()
def _cfg_get(key, default=_ABSENT):
    v = state['cfg'].get(key, _ABSENT)
    if v is _ABSENT:
        return default
    return v
cfg.get = _cfg_get
sys.modules['config'] = cfg

met = types.ModuleType('metrics')
def _spend_today():
    if state['spend_fails']:
        raise RuntimeError('ledger unreadable')
    return state['spend']
def _p90():
    if state['p90_fails']:
        raise RuntimeError('no history')
    return state['p90']
met.spend_today = _spend_today
met.successful_run_cost_p90 = _p90
sys.modules['metrics'] = met

nfy = types.ModuleType('notify')
nfy.emit = lambda *a, **kw: None
sys.modules['notify'] = nfy

import loop_init as li
import loop_types as lt

# The refusal path persists a verdict to run metadata. That write belongs
# to the run dir, not to the gate, and this port does not carry it -- so
# it is neutralised here rather than allowed to touch the filesystem.
li._stamp_refusal_verdict = lambda *a, **kw: None

warned = []
class _Cap:
    def warning(self, fmt, *args):
        warned.append(fmt % args if args else fmt)
    def info(self, *a, **kw):
        pass
li.log = _Cap()

out = []
for sc in json.loads(sys.stdin.read()):
    state['cfg'] = {k: v for k, v in sc['cfg'].items()}
    state['spend'] = sc['spend']
    state['spend_fails'] = sc['spend_fails']
    state['p90'] = sc['p90']
    state['p90_fails'] = sc['p90_fails']
    del warned[:]

    ctx = lt.LoopContext()
    ctx.loop_id = 'L1'
    ctx.cost_budget = sc['cost_budget']
    ctx.cost_warn_usd = sc['cost_warn_usd']
    res = li._budget_gate(ctx, goal='g', project='p', dry_run=sc['dry_run'])
    # The two float fields go over the wire as repr() STRINGS, not as
    # JSON numbers. float('inf') is a reachable cap -- config
    # budget.per_run_usd: "inf" coerces cleanly -- and json.dumps writes
    # that as the bare token Infinity, which Go's decoder rejects. repr
    # round-trips exactly for every finite double as well, so nothing is
    # lost by spelling all of them this way.
    out.append({
        'cost_budget': None if ctx.cost_budget is None else repr(float(ctx.cost_budget)),
        'cost_warn_usd': None if ctx.cost_warn_usd is None else repr(float(ctx.cost_warn_usd)),
        'refused': res is not None,
        'reason': (res.stuck_reason if res is not None else ''),
        'verdict': (res.stop_verdict if res is not None else ''),
        'warnings': list(warned),
    })
print(json.dumps(out))
`

type budgetScenario struct {
	Cfg        map[string]any `json:"cfg"`
	Spend      float64        `json:"spend"`
	SpendFails bool           `json:"spend_fails"`
	P90        *float64       `json:"p90"`
	P90Fails   bool           `json:"p90_fails"`
	CostBudget *float64       `json:"cost_budget"`
	CostWarn   *float64       `json:"cost_warn_usd"`
	DryRun     bool           `json:"dry_run"`
	// mapRepr marks the row whose warning carries a repr'd MAPPING, which
	// the two runtimes cannot spell the same way. See the row.
	mapRepr bool
	why     string
}

func f(v float64) *float64 { return &v }

// fp is f, spelled apart so a caller-supplied cap reads differently from
// a p90 at a glance.
func fp(v float64) *float64 { return &v }

// cfg spells one config state. A key ABSENT from the map is a missing key,
// which is the sentinel arm; a key mapped to nil is an explicit null.
func cfg(pairs ...any) map[string]any {
	m := map[string]any{}
	for i := 0; i+1 < len(pairs); i += 2 {
		m[pairs[i].(string)] = pairs[i+1]
	}
	return m
}

var budgetScenarios = []budgetScenario{
	// --- the shape a fresh box actually has -------------------------
	{Cfg: cfg(), why: "no config at all, no history: both floors"},
	{Cfg: cfg(), P90: f(1.0), why: "auto, but 4x p90 is under the floor"},
	{Cfg: cfg(), P90: f(4.0), why: "4x p90 exactly meets the floor"},
	{Cfg: cfg(), P90: f(10.0), why: "auto, 4x p90 clears the floor"},
	{Cfg: cfg(), P90: f(0.0), why: "a p90 of ZERO is falsy: floor, not 0"},
	{Cfg: cfg(), P90Fails: true, why: "the p90 call raising is the same as None"},

	// --- dry_run writes NOTHING -------------------------------------
	{Cfg: cfg("budget.daily_usd", 1.0), Spend: 999, DryRun: true,
		why: "a dry run is not gated and its context is untouched"},
	{Cfg: cfg(), P90: f(10.0), DryRun: true, why: "...not even the auto cap"},

	// --- the caller's argument wins ---------------------------------
	{Cfg: cfg("budget.per_run_usd", 3.0), CostBudget: f(1.5),
		why: "an explicit caller cap skips the whole config ladder"},
	{Cfg: cfg(), P90: f(100.0), CostBudget: f(1.5), why: "...including auto"},
	{CostBudget: f(0.0), Cfg: cfg(), P90: f(100.0),
		why: "a caller cap of 0.0 is FALSY: uncapped, and no warn line"},

	// --- the config value, and _coerce_cap --------------------------
	{Cfg: cfg("budget.per_run_usd", 3.0), why: "an explicit number"},
	{Cfg: cfg("budget.per_run_usd", 0), why: "0 is the uncapped opt-out"},
	{Cfg: cfg("budget.per_run_usd", nil), why: "and so is an explicit null"},
	{Cfg: cfg("budget.per_run_usd", -1.0), why: "a negative is not > 0 either"},
	{Cfg: cfg("budget.per_run_usd", "12.5"),
		why: "float() takes a numeric STRING"},
	{Cfg: cfg("budget.per_run_usd", "  12.5  "),
		why: "...and strips it first"},
	{Cfg: cfg("budget.per_run_usd", "1e3"), why: "...and exponents"},
	{Cfg: cfg("budget.per_run_usd", true),
		why: "True is 1.0 to float(), which is a REAL $1 cap"},
	{Cfg: cfg("budget.per_run_usd", false),
		why: "...and False is 0.0, so uncapped"},
	{Cfg: cfg("budget.per_run_usd", "ten dollars"),
		why: "MALFORMED: fails CLOSED to the default, with a warning"},
	{Cfg: cfg("budget.per_run_usd", []any{1}),
		why: "a list is malformed too"},
	// A MAPPING is malformed too, and it is the one row whose warning
	// TEXT cannot agree: Python renders `{'a': 1}`, and pyval.Repr
	// refuses to render a Go map because a Go map has no order to render.
	// The decision is identical; only the span between the prefix and the
	// suffix differs, and mapRepr below is what pins that.
	{Cfg: cfg("budget.per_run_usd", map[string]any{"a": 1}),
		mapRepr: true, why: "a dict: the DECISION agrees, the repr cannot"},
	{Cfg: cfg("budget.per_run_usd", "nan"),
		why: "float('nan') SUCCEEDS, and nan > 0 is False: uncapped"},
	{Cfg: cfg("budget.per_run_usd", "inf"),
		why: "float('inf') succeeds and IS > 0"},

	// --- the early-warn line ----------------------------------------
	{Cfg: cfg("budget.per_run_usd", 10.0), P90: f(1.0),
		why: "warn absent: max(floor, p90) and the floor wins"},
	{Cfg: cfg("budget.per_run_usd", 10.0), P90: f(6.0),
		why: "...and here the p90 does"},
	{Cfg: cfg("budget.per_run_usd", 10.0), P90: f(0.0),
		why: "a zero p90 falls to the floor through `or 0.0`"},
	{Cfg: cfg("budget.per_run_usd", 10.0, "budget.warn_usd", 4.0),
		why: "an explicit warn"},
	{Cfg: cfg("budget.per_run_usd", 10.0, "budget.warn_usd", 0),
		why: "warn 0 is explicitly disabled and stays 0, NOT 80%"},
	{Cfg: cfg("budget.per_run_usd", 10.0, "budget.warn_usd", nil),
		why: "...and so is a null"},
	{Cfg: cfg("budget.per_run_usd", 10.0, "budget.warn_usd", 10.0),
		why: "a warn AT the cap is pulled back to 80%"},
	{Cfg: cfg("budget.per_run_usd", 10.0, "budget.warn_usd", 50.0),
		why: "...and one above it"},
	{Cfg: cfg("budget.per_run_usd", 10.0, "budget.warn_usd", "bogus"),
		why: "a malformed warn fails closed to the floor"},
	{Cfg: cfg("budget.per_run_usd", 2.0), P90: f(9.0),
		why: "a p90 warn above a small cap is pulled back"},
	{Cfg: cfg("budget.per_run_usd", 10.0), CostWarn: f(1.0),
		why: "a warn already on the context is left alone"},
	{Cfg: cfg("budget.per_run_usd", 10.0), CostWarn: f(0.0),
		why: "...INCLUDING an explicit 0, which `is None` does not catch"},
	{Cfg: cfg("budget.per_run_usd", 0), P90: f(5.0),
		why: "no cap means no warn line either"},

	// A NEGATIVE caller cap is nonsense and the code accepts it, because
	// `if ctx.cost_budget` is a truthiness test and -5.0 is truthy. It is
	// the only input that separates `if _warn and _warn >= cap` from a
	// bare `_warn >= cap`: with a warn of 0 and a positive cap the
	// comparison is false either way, and only a negative cap makes
	// `0 >= cap` true. Contrived, and reachable -- cost_budget is a plain
	// caller argument with no validation anywhere between here and it.
	{Cfg: cfg("budget.warn_usd", 0), CostBudget: fp(-5.0),
		why: "warn 0 under a NEGATIVE cap stays 0, not -4"},
	{Cfg: cfg("budget.warn_usd", nil), CostBudget: fp(-5.0),
		why: "...and the null spelling of the same"},
	{Cfg: cfg(), CostBudget: fp(-5.0), P90: f(1.0),
		why: "a non-zero warn under a negative cap IS pulled back"},

	// --- the daily gate ---------------------------------------------
	{Cfg: cfg("budget.daily_usd", 25.0), Spend: 10, why: "under the cap"},
	{Cfg: cfg("budget.daily_usd", 25.0), Spend: 25,
		why: "EXACTLY at it: >= refuses"},
	{Cfg: cfg("budget.daily_usd", 25.0), Spend: 24.999,
		why: "just under"},
	{Cfg: cfg("budget.daily_usd", 25.0), Spend: 99,
		why: "well past it"},
	{Cfg: cfg(), Spend: 25, why: "the DEFAULT daily cap, with no config"},
	{Cfg: cfg(), Spend: 24, why: "...and under it"},
	{Cfg: cfg("budget.daily_usd", 0), Spend: 999,
		why: "0 disables the daily gate entirely"},
	{Cfg: cfg("budget.daily_usd", nil), Spend: 999, why: "...and null"},
	{Cfg: cfg("budget.daily_usd", "bogus"), Spend: 999,
		why: "a malformed daily cap fails CLOSED to $25 and refuses"},
	{Cfg: cfg("budget.daily_usd", 25.0), Spend: 99, SpendFails: true,
		why: "a broken ledger lets the run PROCEED, ungated"},

	// The message is an operator-facing string and its FORMATTING is
	// contract: two decimals, half-to-even on the exact double.
	{Cfg: cfg("budget.daily_usd", 0.005), Spend: 0.005,
		why: "%.2f rounds half-to-even: 0.005 is 0.01 or 0.00, not by taste"},
	{Cfg: cfg("budget.daily_usd", 0.015), Spend: 0.015, why: "the other side"},
	{Cfg: cfg("budget.daily_usd", 1.0/3.0), Spend: 1.0,
		why: "a repeating fraction"},
	{Cfg: cfg("budget.daily_usd", 1e21), Spend: 1e21,
		why: "%f never goes scientific"},
	{Cfg: cfg("budget.daily_usd", 25.0), Spend: 1e21, why: "a huge spend"},

	// --- the three blocks are INDEPENDENT ---------------------------
	{Cfg: cfg("budget.per_run_usd", "bogus", "budget.daily_usd", 25.0), Spend: 99,
		why: "a broken per-run cap does not stop the daily gate refusing"},
	{Cfg: cfg("budget.warn_usd", "bogus", "budget.daily_usd", 25.0), Spend: 99,
		why: "nor a broken warn line"},
	{Cfg: cfg("budget.per_run_usd", "bogus", "budget.warn_usd", "bogus",
		"budget.daily_usd", "bogus"), Spend: 99,
		why: "all three malformed: three warnings, and it still refuses"},
	{Cfg: cfg("budget.per_run_usd", 4.0, "budget.warn_usd", 2.0,
		"budget.daily_usd", 25.0), Spend: 30, P90: f(7.0),
		why: "every layer set, and the daily one still decides"},
}

func TestBudgetGateMatchesCPython(t *testing.T) {
	in, err := json.Marshal(budgetScenarios)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", budgetProbe, srcDirLI(t))
	cmd.Stdin = strings.NewReader(string(in))
	raw, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		if ee, ok := perr.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe failed: %v\n%s", perr, ee.Stderr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var py []struct {
		CostBudget *string  `json:"cost_budget"`
		CostWarn   *string  `json:"cost_warn_usd"`
		Refused    bool     `json:"refused"`
		Reason     string   `json:"reason"`
		Verdict    string   `json:"verdict"`
		Warnings   []string `json:"warnings"`
	}
	if err := json.Unmarshal(raw, &py); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, raw)
	}
	if len(py) != len(budgetScenarios) {
		t.Fatalf("probe returned %d rows for %d scenarios", len(py), len(budgetScenarios))
	}

	var refusals, capped, uncapped, warned int
	for i, sc := range budgetScenarios {
		got := BudgetGate(BudgetIn{
			CostBudget:  sc.CostBudget,
			CostWarnUSD: sc.CostWarn,
			DryRun:      sc.DryRun,
		}, envOf(sc))
		w := py[i]

		if !sameF(got.CostBudget, parseF(t, w.CostBudget)) {
			t.Errorf("row %d (%s) cost_budget -- THE SPEND BREAKER\n  go %s\n  py %s",
				i, sc.why, showF(got.CostBudget), showF(parseF(t, w.CostBudget)))
		}
		if !sameF(got.CostWarnUSD, parseF(t, w.CostWarn)) {
			t.Errorf("row %d (%s) cost_warn_usd\n  go %s\n  py %s",
				i, sc.why, showF(got.CostWarnUSD), showF(parseF(t, w.CostWarn)))
		}
		if got.Refused != w.Refused {
			t.Errorf("row %d (%s) REFUSED: go %v py %v", i, sc.why, got.Refused, w.Refused)
		}
		if got.Reason != w.Reason {
			t.Errorf("row %d (%s) the operator-facing reason\n  go %q\n  py %q",
				i, sc.why, got.Reason, w.Reason)
		}
		if w.Refused && w.Verdict != "out-of-budget" {
			t.Errorf("row %d: CPython refused with verdict %q, not out-of-budget",
				i, w.Verdict)
		}
		if sc.mapRepr {
			// Both sides pinned, and only the value's span left out of the
			// equality -- NOT normalised away. A normaliser here would move
			// the assertion into itself (L51); this states what each side
			// must say instead.
			const pre = "budget gate: budget.per_run_usd="
			const post = " is not a number \u2014 using default $10.00"
			if len(got.Warnings) != 1 || len(w.Warnings) != 1 {
				t.Errorf("row %d (%s): expected exactly one warning a side, "+
					"got %d / %d", i, sc.why, len(got.Warnings), len(w.Warnings))
			} else {
				if got.Warnings[0] != pre+"<unordered map: decode with LoadsOrdered>"+post {
					t.Errorf("row %d: the port's own rendering moved: %q",
						i, got.Warnings[0])
				}
				mid := strings.TrimSuffix(strings.TrimPrefix(w.Warnings[0], pre), post)
				if mid == w.Warnings[0] {
					t.Errorf("row %d: CPython's warning no longer has the "+
						"shape this row assumes: %q", i, w.Warnings[0])
				} else if mid != "{'a': 1}" {
					t.Errorf("row %d: CPython renders the mapping as %q now", i, mid)
				}
			}
		} else if !eqStrsLI(got.Warnings, w.Warnings) {
			t.Errorf("row %d (%s) the log.warning lines\n  go %v\n  py %v",
				i, sc.why, got.Warnings, w.Warnings)
		}

		if w.Refused {
			refusals++
		}
		if w.CostBudget != nil {
			capped++
		} else {
			uncapped++
		}
		warned += len(w.Warnings)
	}
	// A corpus that never refuses, or never leaves a run uncapped, cannot
	// fail in the directions that matter: an uncapped run on a fresh box,
	// and a run refused that should have started.
	if refusals < 5 || capped < 10 || uncapped < 5 || warned < 6 {
		t.Fatalf("the corpus is lopsided: %d refusals, %d capped, %d uncapped, "+
			"%d warnings", refusals, capped, uncapped, warned)
	}
}

func envOf(sc budgetScenario) BudgetEnv {
	pick := func(key string) ConfigValue {
		v, ok := sc.Cfg[key]
		if !ok {
			return Absent()
		}
		return Present(v)
	}
	// A p90 CALL that raises and a p90 of None are the same input to the
	// gate, because Python catches the exception and returns None. The
	// scenario carries both spellings so the corpus says so out loud.
	p90 := sc.P90
	if sc.P90Fails {
		p90 = nil
	}
	return BudgetEnv{
		PerRun:           pick("budget.per_run_usd"),
		Warn:             pick("budget.warn_usd"),
		Daily:            pick("budget.daily_usd"),
		P90:              p90,
		SpendToday:       sc.Spend,
		SpendTodayFailed: sc.SpendFails,
		SpendTodayError:  "ledger unreadable",
	}
}

// parseF turns the probe's repr() string back into a double. repr is
// exact for every finite value and spells the two non-finite ones "inf"
// and "nan", which pyval.ParseFloat reads the same way float() does.
func parseF(t *testing.T, s *string) *float64 {
	t.Helper()
	if s == nil {
		return nil
	}
	v, ok := pyval.ParseFloat(*s)
	if !ok {
		t.Fatalf("the probe emitted %q, which is not a float repr", *s)
	}
	return &v
}

func sameF(a, b *float64) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	// A NaN cap is reachable: float("nan") succeeds, so `budget.per_run_usd:
	// nan` coerces cleanly and then fails `> 0`. Comparing with == would
	// report two NaNs as different and hide the row.
	if *a != *a && *b != *b {
		return true
	}
	return *a == *b
}

func showF(p *float64) string {
	if p == nil {
		return "None (uncapped)"
	}
	return strconv.FormatFloat(*p, 'g', -1, 64)
}

func eqStrsLI(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// The one mutation the differential cannot kill, pinned as an assertion
// rather than as prose. `if _p90:` and `if _p90 is not None:` differ only
// at a p90 of 0.0, and at 0.0 the auto formula collapses to the same
// number the floor branch hands back -- so the guard is unobservable
// THROUGH THIS SURFACE and the mutant is equivalent (L8).
//
// It stops being equivalent the moment the floor drops below what the
// multiplier makes of a zero p90, which is what this asserts. A future
// tuning pass that sets DefaultPerRunUSD to 0 fails here, at the claim,
// instead of quietly making the comment above false the way
// looptypes' ResolveLogLevel comment was false the day it was written
// (docs/REVIEW_PATTERNS.md L52).
func TestTheP90TruthinessTestIsAbsorbedByTheFloor(t *testing.T) {
	if got := maxF(DefaultPerRunUSD, KillP90Multiplier*0); got != DefaultPerRunUSD {
		t.Fatalf("a zero p90 no longer lands on the floor: maxF(%v, %v*0) = %v",
			DefaultPerRunUSD, KillP90Multiplier, got)
	}
	// ...and the same through the real gate, both spellings of "no
	// usable p90", so this cannot pass on arithmetic a refactor has
	// stopped calling.
	zero := 0.0
	// Absent() on all three: the zero VALUE of ConfigValue is "present
	// and nil", which is the uncapped opt-out and would make both sides
	// nil for a reason that has nothing to do with the p90.
	fresh := func(p90 *float64) BudgetEnv {
		return BudgetEnv{PerRun: Absent(), Warn: Absent(), Daily: Absent(), P90: p90}
	}
	withZero := BudgetGate(BudgetIn{}, fresh(&zero))
	withNone := BudgetGate(BudgetIn{}, fresh(nil))
	if withZero.CostBudget == nil || withNone.CostBudget == nil ||
		*withZero.CostBudget != *withNone.CostBudget {
		t.Fatalf("a zero p90 and an absent one now differ: %v vs %v",
			showF(withZero.CostBudget), showF(withNone.CostBudget))
	}
}
