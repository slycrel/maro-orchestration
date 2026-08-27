package loopfinalize

// The run-risk mint, plus the two lesson-text helpers and the post-notify
// maintenance registry — the self-contained half of loop_finalize.py's
// second slice.
//
// `_mint_run_risks_to_project` is the piece with arithmetic in it. Jeremy's
// 2026-08-10 ruling: risks and unknowns a run discovered belong in the
// project's RISKS.md, feeding the "RISKS.md as reviewer input" direction.
// Mechanical v1, no LLM, two sources — the run's persisted closure gaps and
// the scope-parse failure sentinel.
//
// Unlike the finalize phase above it, this one calls the PORTED functions
// directly (runs.ResolveRunDir, orch.RisksPath, orch.AppendRisk) rather
// than reaching them through Deps. They exist, they take a workspace, and
// a differential that drives both languages over one temp workspace
// measures the file the operator actually reads.

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pyos"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
)

const (
	// RiskLinesCap is the TOTAL risk lines one run may mint — gaps plus
	// the scope sentinel together. A noise cap, not a gap cap.
	RiskLinesCap = 3
	// RiskGapClip is `clip(gap, 500)`, the per-gap text budget.
	RiskGapClip = 500
)

// RecoveryPlanLessonText is the observation-form mint text for an
// advisor-proposed recovery plan.
//
// What-not-how (2026-08-02, surprise-read M9): the old form minted the
// advisor's procedure bare and the row read as an instruction the next run
// obeys. This form states what happened and marks the plan as an
// unverified proposal, so the planner weighs it as evidence. Deterministic
// per (failureClass, action) on purpose — recurring plans reinforce
// through near-duplicate dedup rather than through fresh text.
func RecoveryPlanLessonText(failureClass, action string) string {
	return "[recovery-plan] " + failureClass + ": this failure class was " +
		"diagnosed on a run; advisor-proposed recovery (unverified): " + action
}

// AutoDiagnosisLessonText is the observation-form mint text for a
// deterministic loop diagnosis.
func AutoDiagnosisLessonText(failureClass, recommendation string) string {
	return "[auto-diagnosis] " + failureClass + ": diagnosed on a run; " +
		"classifier recommendation (unverified): " + recommendation
}

// MaintenanceRegistry is the post-notify maintenance registry (async-tail
// decree).
//
// In Python it is a module-level dict, and the comment above it explains
// why it lives in loop_finalize and not in handle: `handle` is also a
// `python -m handle` entry point, so run that way the module executes as
// __main__ while an `import handle` would load a SECOND copy with its own
// registry — the registrar and the drainer would hold different dicts and
// every deferred callable would strand undrained (3-lens review of
// 707a541, Architect HIGH, module-identity probe).
//
// Go has no equivalent hazard: a package is loaded once per binary, by
// import path, and there is no `__main__` re-entry. The type is exported
// as a VALUE rather than reproduced as package state so a caller can hold
// one per process without the port inheriting a global — and the mutex is
// this port's own addition, since Python's GIL made the dict operations
// atomic and goroutines have no such luck.
type MaintenanceRegistry struct {
	mu  sync.Mutex
	fns map[string][]func() error
}

// DeferPostNotify is `defer_maintenance_post_notify`: register a callable
// to run after the handle's notification has gone out.
func (r *MaintenanceRegistry) DeferPostNotify(handleID string, fn func() error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.fns == nil {
		r.fns = map[string][]func() error{}
	}
	r.fns[handleID] = append(r.fns[handleID], fn)
}

// DrainDeferred is `drain_deferred_maintenance`: run and CLEAR whatever is
// registered for handleID, and return how many ran.
//
// It never fails. A callable that raises is a warning and the rest still
// run — the `pop` happens first, so a raise cannot leave a half-drained
// entry behind for a second drain to re-run.
func (r *MaintenanceRegistry) DrainDeferred(handleID string,
	warn func(string)) int {
	r.mu.Lock()
	fns := r.fns[handleID]
	delete(r.fns, handleID)
	r.mu.Unlock()
	for _, fn := range fns {
		if err := fn(); err != nil {
			warn(fmt.Sprintf("deferred maintenance failed for handle %s: %s",
				handleID, err))
		}
	}
	return len(fns)
}

// MintDeps is the two things the mint cannot reach on its own.
type MintDeps struct {
	// RiskMintEnabled is `config.get("project.risk_mint", True)`. Its
	// error is the config read raising, which the outer except turns into
	// a warning and a zero.
	RiskMintEnabled func() (bool, error)
	Warning         func(msg string)
}

// MintRunRisksToProject is `_mint_run_risks_to_project`.
//
// It NEVER raises: risk minting is observability-grade and must not
// perturb result delivery. Every failure is a warning and a zero, and the
// zero is indistinguishable from "nothing to mint" by design.
//
// Idempotent per loop — it skips when RISKS.md already names this loop_id.
func MintRunRisksToProject(ws, project, loopID string, d MintDeps) int {
	if project == "" || loopID == "" {
		return 0
	}
	n, err := mintRunRisks(ws, project, loopID, d)
	if err != nil {
		d.Warning(fmt.Sprintf("risk mint failed for loop %s: %s", loopID, err))
		return 0
	}
	return n
}

// readTextPy is `Path.read_text(encoding="utf-8")`, whole.
//
// Three halves of it are easy to lose and each one changes what the mint
// does. `errors` defaults to STRICT, so a file that is not valid UTF-8
// RAISES rather than substituting replacement characters — inheriting
// Go's tolerance would turn "the mint warns and returns 0" into "the mint
// reads garbage and writes it into RISKS.md". `newline=None` means
// universal newlines, so a CRLF verdicts file is "\n"-separated before
// anything splits it. And the exception the caller LOGS is an OSError
// whose str() CPython spells `[Errno 21] Is a directory: '<path>'` where
// Go spells it `open <path>: is a directory`.
//
// pyval.ReadText is the same function and is NOT used here for one
// reason: it wraps the decode failure as "<path>: <codec message>", and
// the warning this feeds is `str(exc)`, which carries no path.
func readTextPy(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", errors.New(pyos.ErrorText(path, err))
	}
	s, derr := pyval.DecodeUTF8Strict(raw)
	if derr != nil {
		return "", derr
	}
	// EQUIVALENT MUTANT, for this caller only: both readers of this text
	// (SplitLines, and a substring search for a loop id) already treat a
	// lone CR and a CRLF the way translation would, so no fixture can see
	// it. It stays because read_text DOES translate, and the next caller
	// of this helper is the one that gets bitten — pack shipped a whole
	// tranche on exactly that (see pyval.ReadText).
	return pytext.TranslateNewlines(s), nil
}

// iterGaps is `for gap in (verdict_row.get("gaps") or [])`.
//
// Python iterates whatever it was given. A LIST yields its elements, a
// STRING yields its characters one at a time, and a dict yields its keys —
// so `"gaps": "ab"` mints two risk lines reading `a` and `b`, and an int
// raises TypeError into the outer except. Reproducing that is not
// pedantry: `gaps` is written by an LLM-fed closure verdict, and the shape
// it arrives in is not this function's to trust.
func iterGaps(v any) ([]any, error) {
	switch t := v.(type) {
	case nil:
		return nil, nil
	case []any:
		return t, nil
	case pyval.List:
		return t, nil
	case string:
		out := make([]any, 0, len(t))
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, nil
	case pyval.Obj:
		out := make([]any, 0, len(t))
		for _, f := range t {
			out = append(out, f.Key)
		}
		return out, nil
	case map[string]any:
		out := make([]any, 0, len(t))
		for k := range t {
			out = append(out, k)
		}
		return out, nil
	}
	return nil, fmt.Errorf("'%s' object is not iterable", pyTypeName(v))
}

// pyTypeName is the class name CPython puts in a TypeError.
func pyTypeName(v any) string {
	if n, ok := v.(json.Number); ok {
		// json.loads gives an INT for a token with no fraction and no
		// exponent, and a float otherwise — and the TypeError names the
		// class. UseNumber keeps the token's own spelling, which is the
		// only place that distinction survives: 5 and 5.0 both decode to
		// float64 and only one of them is an int in Python.
		if strings.ContainsAny(string(n), ".eE") {
			return "float"
		}
		return "int"
	}
	switch v.(type) {
	case bool:
		return "bool"
	case float64:
		return "float"
	}
	return "object"
}

func mintRunRisks(ws, project, loopID string, d MintDeps) (int, error) {
	on, err := d.RiskMintEnabled()
	if err != nil {
		return 0, err
	}
	if !on {
		return 0, nil
	}
	rd := runs.ResolveRunDir(ws, loopID)
	if rd == "" {
		return 0, nil
	}

	// The LAST matching row wins: a restart appends another verdict for
	// the same loop and the newest one is the run's real answer.
	var verdictRow pyval.Obj
	var haveRow bool
	vpath := filepath.Join(rd, "build", "closure_verdicts.jsonl")
	if pathExists(vpath) {
		text, err := readTextPy(vpath)
		if err != nil {
			return 0, err
		}
		for _, raw := range pytext.SplitLines(text) {
			// EQUIVALENT MUTANT: deleting this skip changes nothing that
			// any input can observe. A blank or all-whitespace line is
			// not valid JSON, so it would fall one line down into
			// LoadsOrdered, fail, and `continue` from there. It is kept
			// because Python keeps it, and because the two paths stop
			// being the same the moment a decoder that tolerates empty
			// input is substituted.
			if strings.TrimSpace(raw) == "" {
				continue
			}
			parsed, err := pyval.LoadsOrdered(raw)
			if err != nil {
				// json.JSONDecodeError only. A line that is valid JSON but
				// not an object falls through to the isinstance test below.
				continue
			}
			// EQUIVALENT MUTANT in Go, load-bearing in Python. There
			// the guard is `isinstance(row, dict)` and deleting it makes
			// `row.get` an AttributeError on the list/string/number a
			// JSONL line is allowed to be. Here a failed assertion gives
			// a ZERO Obj, whose Get misses every key, so a non-object row
			// falls out of the match one line down instead of here. It
			// stays because it is the readable form of Python's rule and
			// because the day Obj grows a Get that answers for the zero
			// value, the two spellings stop agreeing.
			row, isObj := parsed.(pyval.Obj)
			if !isObj {
				continue
			}
			if v, ok := row.Get("loop_id"); ok {
				if s, isStr := v.(string); isStr && s == loopID {
					verdictRow, haveRow = row, true
				}
			}
		}
	}

	var gaps []string
	if haveRow {
		skipped, _ := verdictRow.Get("skipped")
		if !pyval.Truthy(skipped) {
			raw, _ := verdictRow.Get("gaps")
			if !pyval.Truthy(raw) {
				raw = nil
			}
			items, err := iterGaps(raw)
			if err != nil {
				return 0, err
			}
			for _, g := range items {
				// One physical line per gap: LLM-derived gap text can carry
				// embedded newlines, which would break the markdown bullet
				// and make the <=3 cap a lie (round-2 review 2026-08-10).
				gap := strings.Join(pytext.Split(pyval.Str(g)), " ")
				if gap != "" {
					gaps = append(gaps, gap)
				}
			}
		}
	}

	sentinel := ""
	if pathExists(filepath.Join(rd, "build", "scope-raw-FAILED.txt")) {
		sentinel = fmt.Sprintf("Run %s executed without scope injection "+
			"(scope parse failed; raw output in build/scope-raw-FAILED.txt)",
			loopID)
	}

	// The final selection is computed FIRST and the omission note rides as
	// an annotation describing exactly what survived (round-15 review,
	// 3-lens): a note rendered BEFORE the cap got capped along with the
	// gaps and became a lie — "three below" printed above two survivors,
	// with a middle gap silently dropped.
	gapSlots := RiskLinesCap
	if sentinel != "" {
		gapSlots--
	}
	shown := gaps[:pyval.SliceStop(len(gaps), gapSlots)]

	var lines []string
	for _, gap := range shown {
		lines = append(lines, fmt.Sprintf(
			"Open gap from run %s (closure): %s", loopID,
			budget.Clip(gap, RiskGapClip)))
	}
	if sentinel != "" {
		lines = append(lines, sentinel)
	}
	if len(gaps) > len(shown) {
		lines = append(lines, fmt.Sprintf(
			"(%d more closure gap(s) from run %s not minted — full list in "+
				"the run's closure verdict)", len(gaps)-len(shown), loopID))
	}
	if len(lines) == 0 {
		return 0, nil
	}

	// A cheap pre-check saves the lock in the common already-minted case;
	// the authoritative check is the in-lock dedupe token (TOCTOU fix,
	// adversarial review 2026-08-10).
	// EQUIVALENT MUTANT, for the TEST but not for the READ: AppendRisk
	// does the identical `strings.Contains(existing, token)` inside the
	// lock, so weakening this comparison to equality — or deleting the
	// comparison entirely — changes no answer. What IS observable is that
	// this branch READS the file at all: a RISKS.md that is not UTF-8
	// raises here and warns, where the in-lock check would have appended
	// to it (`precheck-skipped` in the battery). So the branch is pinned
	// and its predicate is not, which is the honest split.
	rp := orch.RisksPath(ws, project)
	if pathExists(rp) {
		text, err := readTextPy(rp)
		if err != nil {
			return 0, err
		}
		if strings.Contains(text, loopID) {
			return 0, nil
		}
	}
	// A race-losing finalizer's in-lock dedupe is a no-op — report it as 0
	// minted, not len(lines) (round-2 review 2026-08-10).
	ok, err := orch.AppendRisk(ws, project, lines, loopID)
	if err != nil {
		return 0, err
	}
	if !ok {
		return 0, nil
	}
	return len(lines), nil
}

// pathExists is `Path.exists()`, which is TRUE for a directory too. That
// matters: a `closure_verdicts.jsonl` that is somehow a directory passes
// the guard and then raises IsADirectoryError out of read_text, which the
// outer except turns into a warning and a zero. A Go helper that answered
// "not a regular file, skip it" would mint the risks anyway.
func pathExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}
