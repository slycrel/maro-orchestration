package loop

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// StepEvidence is loop_finalize._step_evidence — what the steps actually
// produced, bounded and honest about the bound.
//
// It feeds lesson extraction, which is why the honesty matters: oldest-first
// eviction and the per-entry cap are announced in the rendered text, so an
// extractor can tell a whole run from a trimmed one instead of silently
// generalising from a fragment.
//
// # The keyword defaults are not the same as zero
//
// Python's signature is `(step_outcomes, *, total_budget=None,
// entry_cap=None)` and the body only forwards the ones that were passed:
//
//	kw = {}
//	if total_budget is not None: kw["total_budget"] = total_budget
//	if entry_cap is not None:    kw["entry_cap"] = entry_cap
//	cb = ContextBudget(**kw)
//
// So None means "ContextBudget's own default", NOT zero — and zero is a real
// value a caller could pass, which would render an empty block. A Go int
// cannot carry that distinction, so this function takes the Accumulator's
// defaults and StepEvidenceBounded takes explicit numbers. Collapsing them
// into one `func(outcomes, total, cap int)` would make 0 ambiguous exactly
// where Python is unambiguous.
func StepEvidence(outcomes []StepOutcome) string {
	return renderStepEvidence(outcomes, budget.NewAccumulator())
}

// StepEvidenceBounded is _step_evidence with both keywords supplied. A zero
// here is a real zero — the caller asking for nothing — not a default.
func StepEvidenceBounded(outcomes []StepOutcome, totalBudget, entryCap int) string {
	acc := budget.NewAccumulator()
	acc.TotalBudget = totalBudget
	acc.EntryCap = entryCap
	return renderStepEvidence(outcomes, acc)
}

func renderStepEvidence(outcomes []StepOutcome, acc *budget.Accumulator) string {
	for _, s := range outcomes {
		// pytext.Strip, not TrimSpace: Python's `(… or "").strip()` removes
		// U+001C-U+001F as well, and a step result is model output that has
		// arrived carrying a separator before now.
		text := pytext.Strip(s.Step)
		result := pytext.Strip(s.Result)
		status := s.Status
		if status == "" {
			// `getattr(s, "status", "") or "?"` — the empty string is
			// FALSY in Python, so a step with a blank status renders "?"
			// rather than "[]". This is not the same as an absent field.
			status = "?"
		}
		if text == "" && result == "" {
			continue
		}
		// The strip is on the JOINED string, not on the parts: Python builds
		// `f"Step {i} [{status}] {text}\n{result}".strip()`, so a step with
		// text and no result loses the trailing newline, and one with a
		// result and no text keeps the space after the bracket. Stripping
		// the parts first and joining would produce neither.
		acc.Add(strings.TrimSpace(fmt.Sprintf("Step %s [%s] %s\n%s",
			stepIndexLabel(s), status, text, result)))
	}
	return acc.Render()
}

// stepIndexLabel is `getattr(s, "index", "?")` rendered into an f-string.
//
// Python's default fires only for an object with no `index` at all, which a
// StepOutcome always has, so "?" is the arm for a NON-StepOutcome — and this
// port's static type makes that unreachable. The label is therefore always a
// number, and the interesting question is which one: see StepOutcome.Index
// for why it is a stored field and not the slice position.
func stepIndexLabel(s StepOutcome) string {
	return fmt.Sprintf("%d", s.Index)
}

// AutoPruneDays is loop_finalize._auto_prune_days — the user-level retention
// knob `artifacts.auto_prune_days`, defaulting to 0, which means NEVER
// DELETE.
//
// The default is the decree (Jeremy, 2026-07-10): the system never decides
// run data is clutter — "the result isn't always just the outcome, it's also
// the path that gets you there." Auto-pruning is strictly user opt-in. The
// old `keep_artifacts` flag it replaced defaulted to DELETE, which is the
// bug class where finalize destroyed its own audit evidence.
//
// # `max(0.0, float(v or 0))` is four coercions, and each one matters
//
// Python's body is one line and no arm of it is decorative:
//
//	return max(0.0, float(_cfg_get("artifacts.auto_prune_days", 0) or 0))
//
// `or 0` turns every FALSY value into 0 before float() sees it, float()
// accepts numeric STRINGS (and strips their whitespace), a non-numeric
// string RAISES into the outer except, and max clamps negatives — and also
// clamps NaN, because `nan > 0.0` is false. Measured across the shapes a
// hand-edited config can hold:
//
//	0 -> 0    3 -> 3      3.5 -> 3.5   "3" -> 3     " 3 " -> 3
//	true -> 1 false -> 0  nil -> 0     "" -> 0      [] -> 0
//	"abc" -> ValueError -> 0           "0x10" -> ValueError -> 0
//	"nan" -> 0 (clamped)   "inf" -> +Inf (NOT clamped)   "-1" -> 0
//
// Infinity survives, which is correct and worth naming: `auto_prune_days:
// inf` disables pruning by putting the grace period past every mtime,
// rather than by being clamped to the delete-everything end.
func AutoPruneDays(ws string) float64 {
	cfg, _ := config.LoadFor(ws)
	v := config.GetRaw(cfg, "artifacts.auto_prune_days", 0)
	f := pyFloatOrZero(v)
	if !(f > 0) {
		// !(f > 0) rather than f <= 0 so a NaN lands here: NaN fails BOTH
		// comparisons, and `f <= 0` would let it through to be multiplied
		// into a grace period that no mtime is ever less than — which
		// silently disables pruning instead of clamping it, and would look
		// identical in every test that only checks "nothing was deleted".
		return 0
	}
	return f
}

// pyFloatOrZero is `float(v or 0)` with Python's ValueError folded into the
// caller's except — which is to say, into 0.
//
// It returned a second `ok` bool for one round, distinguishing "float()
// would have raised" from "the value was falsy and became 0". Nothing read
// it: every failing arm returns 0 as well, so `!ok` was implied by the
// caller's own `!(f > 0)`, and two mutants that flipped the bool both
// survived the differential. A distinction nothing reads is not a
// distinction — it is a second guard making the first one unobservable.
func pyFloatOrZero(v any) float64 {
	switch t := v.(type) {
	case nil:
		return 0
	case bool:
		if t {
			return 1
		}
		return 0
	case int:
		return float64(t)
	case int64:
		return float64(t)
	case float64:
		return t
	case string:
		// Two Python rules land on the same number here, and only one of
		// them is a parse. `"" or 0` is 0 BEFORE float() runs, so the EMPTY
		// string is not a parse failure — it is zero. A WHITESPACE-ONLY
		// string IS one: "  " is truthy, reaches float(), and raises.
		//
		// There was an `if t == "" { return 0 }` here to spell that out, and
		// it was dead: ParseFloat("") fails and this function answers 0 for
		// a failure too. The battery caught it as an undetectable mutant.
		// The rule is worth writing down; a branch that cannot change an
		// answer is not.
		// strconv.ParseFloat and CPython's float() agree on every string
		// shape a config file can hold, including the ones that look like
		// they would disagree. Measured on both sides:
		//
		//	"1_000" -> 1000   "1_0.5" -> 10.5   "infinity" -> +Inf
		//	"+inf"  -> +Inf   "_1" -> error     "1_" -> error
		//	"1__0"  -> error  "0x10" -> error   "  " -> error
		//
		// Underscore grouping is accepted by both and rejected by both at
		// the edges, which is the pair that looked like a divergence and is
		// not. The ONE real difference is the HEX FLOAT: Go parses
		// "0x10p0" as 16 and CPython raises. No valid decimal float string
		// contains "0x", so screening for it costs nothing and removes the
		// only case where a config value would mean two different numbers.
		trimmed := strings.TrimSpace(t)
		if strings.Contains(strings.ToLower(trimmed), "0x") {
			return 0
		}
		f, err := strconv.ParseFloat(trimmed, 64)
		if err != nil {
			return 0
		}
		return f
	default:
		// A list, a map, anything else: `[] or 0` is 0 for the empty ones
		// and a TypeError from float() for the non-empty ones. Both end at
		// 0.0 — the first by the `or`, the second by the except — so the
		// two are not distinguished here.
		return 0
	}
}

// CleanupStepArtifacts is loop_finalize.cleanup_step_artifacts — opt-in
// per-step artifact pruning. It returns the number of files deleted.
//
// NO-OP unless the user set `artifacts.auto_prune_days` > 0. When enabled it
// deletes `loop-*-step-*.md` files in the project's artifact dir older than
// that many days, and never touches excludeLoopID's files: the
// just-finished loop's step artifacts always outlive its verdict and audit
// window, regardless of which lane invoked the loop (BACKLOG #18).
//
// # Every error here is swallowed, and that is the port, not laziness
//
// Python wraps the whole body in a bare `except` that logs at debug and
// returns 0, and wraps each unlink in `except OSError: pass`. A failure to
// prune is not a failure of the run — the artifacts are still there, which
// is the safe direction for a deletion routine. So this function returns
// only a count.
//
// artifactDir is injected rather than computed so the caller supplies
// runs.ArtifactDir bound to its own context; Python reaches for
// `runs.artifact_dir` inside a try and falls back to the inline path, which
// is the same seam one layer down.
func CleanupStepArtifacts(ws, project, excludeLoopID string,
	artifactDir func(project string) (string, error)) int {
	if project == "" {
		return 0
	}
	return pruneStepArtifacts(AutoPruneDays(ws), project, excludeLoopID, artifactDir)
}

// pruneStepArtifacts is cleanup_step_artifacts with the config read already
// done. The split exists so the differential can pin the PRUNING RULE
// without writing a config file — the same seam the CPython probe uses when
// it replaces `_auto_prune_days` with a lambda, and the reason the knob has
// its own separate differential.
func pruneStepArtifacts(days float64, project, excludeLoopID string,
	artifactDir func(project string) (string, error)) int {
	// The days gate lives HERE and the project gate lives in the caller,
	// each in exactly one place. Repeating either would make the other
	// unobservable — which is what happened to the project gate for one
	// round, when the differential's own helper re-checked it and the
	// mutant that deleted the production copy passed.
	if !(days > 0) {
		return 0
	}
	graceS := days * 86400.0
	dir, err := artifactDir(project)
	if err != nil || dir == "" {
		return 0
	}
	// glob("loop-*-step-*.md"), which is fnmatch semantics over the NAMES in
	// one directory — not a recursive walk, and not a shell glob. `*` here
	// matches a path separator in neither runtime because the candidates are
	// single directory entries.
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0
	}
	var excludePrefix string
	if excludeLoopID != "" {
		excludePrefix = "loop-" + excludeLoopID + "-"
	}
	now := time.Now()
	deleted := 0
	for _, e := range entries {
		name := e.Name()
		if !matchStepArtifact(name) {
			continue
		}
		// The exclusion is a PREFIX test on the name, not a parse. A loop id
		// that is a prefix of another loop id would spare both, which is the
		// safe direction and is what Python does.
		if excludePrefix != "" && strings.HasPrefix(name, excludePrefix) {
			continue
		}
		// A DIRECTORY whose name fits the pattern is skipped, and this is
		// not a tidiness check — it is the one place Go would delete
		// something CPython leaves alone. Path.glob returns directories,
		// and `unlink()` on one raises IsADirectoryError, which the
		// `except OSError: pass` swallows. Measured:
		//
		//	>>> (d/"loop-1-step-1.md").mkdir()
		//	>>> [p.name for p in d.glob("loop-*-step-*.md")]
		//	['loop-1-step-1.md']
		//	>>> (d/"loop-1-step-1.md").unlink()
		//	IsADirectoryError
		//
		// os.Remove SUCCEEDS on an empty directory, so without this the
		// port would delete it and count it, where Python skips it every
		// time. A pruning routine is exactly where an extra deletion is
		// the expensive direction.
		if e.IsDir() {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		// `_now - _f.stat().st_mtime < grace_s` — strictly less-than, so a
		// file exactly at the boundary IS deleted. Sub(…).Seconds() rather
		// than a Duration comparison because grace_s is a float number of
		// seconds and days can be fractional (0.5 is a valid setting).
		if now.Sub(info.ModTime()).Seconds() < graceS {
			continue
		}
		if err := os.Remove(filepath.Join(dir, name)); err != nil {
			continue
		}
		deleted++
	}
	return deleted
}

// matchStepArtifact is `glob("loop-*-step-*.md")` for one name.
//
// Spelled out rather than handed to pytext.FnMatch because the pattern is a
// literal with two stars and no character classes, and because the ORDER
// matters: the second `*` must be found AFTER the "-step-" that follows the
// first one. A naive "starts with loop-, contains -step-, ends with .md"
// accepts "loop-step-x.md", which has no loop id at all — fnmatch does not,
// because its first `*` must match at least… nothing, actually. Measured:
//
//	>>> fnmatch.fnmatch("loop--step-.md", "loop-*-step-*.md")
//	True
//	>>> fnmatch.fnmatch("loop-step-x.md", "loop-*-step-*.md")
//	False
//
// So an EMPTY loop id matches and a MISSING one does not, and the rule is
// exactly "the literals appear in order". That is what this does.
func matchStepArtifact(name string) bool {
	rest, ok := strings.CutPrefix(name, "loop-")
	if !ok {
		return false
	}
	rest, ok = strings.CutSuffix(rest, ".md")
	if !ok {
		return false
	}
	// The FIRST "-step-" at or after position 0 leaves the loop id to its
	// left and the step part to its right. Python's glob is greedy on the
	// first star and then backtracks, which finds a match whenever any
	// occurrence works — so Index (first) and LastIndex (last) agree on
	// whether a match exists, and only the split point differs. Nothing here
	// uses the split point.
	return strings.Contains(rest, "-step-")
}
