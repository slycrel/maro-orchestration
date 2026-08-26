package introspect

import (
	"sort"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// LensResult is introspect.LensResult: one analytical lens's output.
//
// NAMED DIVERGENCE on Action. Python's field is `Optional[str]` and the two
// consumers read it differently — `aggregate_lenses` tests TRUTHINESS
// (`if lr.action:`) while `run_lenses` logs `action is not None`. An empty
// string would therefore be "no action" to one and "an action" to the
// other. No built-in lens can produce one: every assignment is either None
// or a non-empty literal, and the one computed assignment
// (`action = diag.recommendation` in the execution lens) feeds a truthiness
// test at its own site. The port carries "" as None, which agrees with the
// consumer that decides anything and differs only in a debug log line.
type LensResult struct {
	LensName   string
	Findings   []string
	Action     string
	Confidence float64
	// Cost is "free" | "cheap" | "expensive". The registry OVERWRITES it
	// after calling the lens (`result.cost = self._costs.get(name, "free")`),
	// so a lens that sets its own cost has it honoured only when called
	// directly. `_quality_lens` does exactly that, which is why the field
	// is here and not just on the registry.
	Cost string
}

// LensFn is Python's `LensFn = Callable[[LoopDiagnosis, List[StepProfile]],
// LensResult]`.
type LensFn func(diag LoopDiagnosis, profiles []StepProfile) LensResult

// LensRegistry is introspect.LensRegistry.
//
// It carries an explicit `order` slice because Python's registry is two
// DICTS, and a dict preserves insertion order. `run_heuristic` and
// `run_all` iterate `self._lenses.items()`, so registration order is the
// order lenses run, the order their findings are concatenated, and — through
// `aggregate_lenses`' `max()` over the category dict — the tie-break that
// decides which action an operator is shown. A Go map would randomise all
// three on every process.
//
// Re-registering an existing name REPLACES the function and keeps the
// original position, because that is what assigning to an existing dict key
// does. A port that appended again would move the lens to the back of the
// run order and change the tie-break.
type LensRegistry struct {
	order  []string
	lenses map[string]LensFn
	costs  map[string]string
}

func NewLensRegistry() *LensRegistry {
	return &LensRegistry{lenses: map[string]LensFn{}, costs: map[string]string{}}
}

// Register is `registry.register(name, fn, cost="free")`.
func (r *LensRegistry) Register(name string, fn LensFn, cost string) {
	if _, seen := r.lenses[name]; !seen {
		r.order = append(r.order, name)
	}
	r.lenses[name] = fn
	r.costs[name] = cost
}

// List is `sorted(self._lenses.keys())`.
//
// Python sorts strings by CODE POINT and Go's sort.Strings by BYTE, and for
// UTF-8 those are the same order — the encoding is designed so that byte
// comparison reproduces code-point comparison. Lens names are ASCII anyway;
// the note is here so the next reader does not have to re-derive that a
// non-ASCII name would still be safe.
func (r *LensRegistry) List() []string {
	out := append([]string(nil), r.order...)
	sort.Strings(out)
	return out
}

// Run is `registry.run(name, ...)`, including the cost overwrite.
//
// Python indexes `self._lenses[name]` directly, so an unknown name is a
// KeyError that propagates. The port returns ok=false and lets the caller
// decide, because the one internal caller (RunHeuristic) already swallows
// lens failures and the only other callers are tests.
func (r *LensRegistry) Run(name string, diag LoopDiagnosis, profiles []StepProfile) (LensResult, bool) {
	fn, ok := r.lenses[name]
	if !ok {
		return LensResult{}, false
	}
	res := fn(diag, profiles)
	// Python writes `self._costs.get(name, "free")`, and that default is
	// DEAD: `register` is the only writer of either map and always writes
	// both, so a name present in `_lenses` is present in `_costs`. The
	// lookup above already proved it present. The port drops the default
	// rather than carry an untestable branch — a mutation battery could
	// name no input that reaches it, which is the tell. If a second writer
	// of one map ever appears, this line is where the two diverge.
	res.Cost = r.costs[name]
	return res, true
}

// RunHeuristic runs every lens registered as "free", in registration order.
//
// Python wraps each call in `try/except Exception` and logs at debug — a
// lens that raises is DROPPED and the rest still run. Go has no exceptions
// to catch here; the built-in lenses are total functions over their inputs.
// The equivalent hazard is a panic on a malformed profile, and the port
// does not recover from those on purpose: a panic in a lens is a bug in the
// lens, and swallowing it is how the Python side has lenses that have been
// silently failing for months with only a debug line to show for it.
func (r *LensRegistry) RunHeuristic(diag LoopDiagnosis, profiles []StepProfile) []LensResult {
	var out []LensResult
	for _, name := range r.order {
		if r.costs[name] != "free" {
			continue
		}
		if res, ok := r.Run(name, diag, profiles); ok {
			out = append(out, res)
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// Built-in heuristic lenses (free — always run)
// ---------------------------------------------------------------------------

// costCheapKeywords is `cheap_keywords` in _cost_lens.
//
// This module holds THREE different word sets — this one, the architecture
// lens's `_filler`, and the forensics lens's stopword list — and they are
// deliberately different from each other and from `noteStopwords`. They are
// not a candidate for unification: each one is tuned to the sentence its own
// lens is reading, and merging them would change three heuristics at once
// to make one of them tidier.
var costCheapKeywords = map[string]bool{
	"classify": true, "verify": true, "format": true, "list": true,
	"check": true, "validate": true, "count": true,
}

// CostLens is introspect._cost_lens: which steps cost the most DOLLARS.
//
// Dollars and not raw tokens, which is the whole point of the lens — a step
// dominated by cache reads bills at ~0.1x and must not read as the loop's
// cost centre just because its token count is large.
func CostLens(diag LoopDiagnosis, profiles []StepProfile) LensResult {
	if len(profiles) == 0 {
		return LensResult{LensName: "cost"}
	}
	total := 0.0
	for _, p := range profiles {
		total += p.CostUSD()
	}
	if total == 0 {
		return LensResult{LensName: "cost", Findings: []string{"No cost recorded"}}
	}

	var findings []string
	action := ""

	// `max(profiles, key=lambda p: p.cost_usd)` returns the FIRST maximum on
	// a tie, so the comparison is strictly greater.
	top := profiles[0]
	topCost := top.CostUSD()
	for _, p := range profiles[1:] {
		if c := p.CostUSD(); c > topCost {
			top, topCost = p, c
		}
	}
	// `(top.cost_usd / total * 100) if total > 0 else 0`. The guard looks
	// dead after the `total == 0` return above and is NOT: a store carrying
	// a negative cost can sum to less than zero, which passes the equality
	// check and lands here. Ported as written.
	topPct := 0.0
	if total > 0 {
		topPct = topCost / total * 100
	}
	if topPct > 50 {
		findings = append(findings, "Step "+strconv.Itoa(top.StepIdx)+
			" drove "+pyval.PercentF(topPct, 0)+"% of loop cost ($"+
			pyval.PercentF(topCost, 3)+" / $"+pyval.PercentF(total, 3)+")")
		action = "Split step " + strconv.Itoa(top.StepIdx) +
			" into smaller substeps or route to cheaper model"
	}

	for _, p := range profiles {
		if p.Tokens <= 10000 {
			continue
		}
		hit := false
		for _, w := range pytext.Split(pytext.Lower(p.Text)) {
			if costCheapKeywords[w] {
				hit = true
				break
			}
		}
		if !hit {
			continue
		}
		findings = append(findings, "Step "+strconv.Itoa(p.StepIdx)+" ("+
			pyval.Clip(p.Text, 50)+") used "+pyval.Grouped(int64(p.Tokens))+
			" tokens but looks like a classify/verify task — should use cheap model")
		// Python tests `action is None`, not truthiness. The two agree
		// because the only earlier assignment is a non-empty literal.
		if action == "" {
			action = "Route simple classify/verify steps to cheap model tier"
		}
	}

	doneCount, doneTokens := 0, 0
	for _, p := range profiles {
		if p.Status == "done" {
			doneCount++
			doneTokens += p.Tokens
		}
	}
	if doneCount > 0 {
		avg := float64(doneTokens) / float64(doneCount)
		findings = append(findings,
			"Average tokens per done step: "+pyval.GroupedF(avg, 0))
	}

	confidence := 0.3
	if action != "" {
		confidence = 0.8
	}
	return LensResult{LensName: "cost", Findings: findings,
		Action: action, Confidence: confidence}
}

// archFiller is `_filler` in _architecture_lens. See costCheapKeywords for
// why it is not shared with the other three word sets in this module.
var archFiller = map[string]bool{
	"the": true, "a": true, "an": true, "and": true, "or": true, "to": true,
	"in": true, "of": true, "for": true, "on": true, "with": true,
	"from": true,
}

// ArchitectureLens is introspect._architecture_lens: were independent steps
// run sequentially, and is the decomposition lopsided?
func ArchitectureLens(diag LoopDiagnosis, profiles []StepProfile) LensResult {
	if len(profiles) < 3 {
		return LensResult{LensName: "architecture"}
	}
	var findings []string
	action := ""

	independentPairs := 0
	for i := 0; i < len(profiles)-1; i++ {
		a := wordSetMinus(profiles[i].Text, archFiller)
		b := wordSetMinus(profiles[i+1].Text, archFiller)
		overlap := 0
		for w := range a {
			if b[w] {
				overlap++
			}
		}
		if overlap <= 1 && profiles[i].Status == "done" &&
			profiles[i+1].Status == "done" {
			independentPairs++
		}
	}
	if independentPairs >= 2 {
		findings = append(findings, strconv.Itoa(independentPairs)+
			" consecutive step pairs appear independent — could have been parallelized")
		action = "Enable parallel_fan_out for independent steps"
	}

	// A SECOND pass over the same pairs rather than a branch inside the
	// first, because Python runs them as two loops and the findings are
	// therefore grouped: every independence line precedes every reordering
	// line. Folding them together would interleave the two kinds and change
	// the evidence an operator reads.
	for i := 0; i < len(profiles)-1; i++ {
		if isBlockedStatus(profiles[i].Status) && profiles[i+1].Status == "done" {
			findings = append(findings, "Step "+strconv.Itoa(profiles[i].StepIdx)+
				" blocked, but step "+strconv.Itoa(profiles[i+1].StepIdx)+
				" succeeded — reordering might avoid the block")
		}
	}

	var tokens []int
	for _, p := range profiles {
		if p.Tokens > 0 {
			tokens = append(tokens, p.Tokens)
		}
	}
	if len(tokens) >= 3 {
		sum := 0
		for _, t := range tokens {
			sum += t
		}
		// `sum(tokens) // len(tokens)` — FLOOR division, and the values are
		// all positive here so it is also truncation. Spelled as FloorDiv
		// anyway: the filter above is `> 0` on a field a foreign writer
		// controls, and the next person to relax it should not have to
		// notice that the division silently changed meaning.
		avg := pyval.FloorDiv(sum, len(tokens))
		outliers := 0
		for _, p := range profiles {
			if float64(p.Tokens) > float64(avg)*TokenExplosionRatio &&
				p.Tokens > 50000 {
				outliers++
			}
		}
		if outliers > 0 {
			findings = append(findings, strconv.Itoa(outliers)+" step(s) are "+
				pyval.PercentF(TokenExplosionRatio, 0)+
				"x+ the average token cost — decomposition is uneven")
			if action == "" {
				action = "Rebalance decomposition — split heavy steps, merge trivial ones"
			}
		}
	}

	confidence := 0.2
	if action != "" {
		confidence = 0.7
	}
	return LensResult{LensName: "architecture", Findings: findings,
		Action: action, Confidence: confidence}
}

// OperatorLens is introspect._operator_lens: where wall-clock time goes.
func OperatorLens(diag LoopDiagnosis, profiles []StepProfile) LensResult {
	if len(profiles) == 0 {
		return LensResult{LensName: "operator"}
	}
	var findings []string
	action := ""

	totalMS, blockedMS := 0, 0
	for _, p := range profiles {
		totalMS += p.ElapsedMS
		if isBlockedStatus(p.Status) {
			blockedMS += p.ElapsedMS
		}
	}
	// Python also computes `done_ms` here and never reads it. Not ported:
	// a dead binding carried across is a claim that something uses it.

	// Every FloorDiv below sits behind a guard that forces both operands
	// non-negative, so at no reachable input does it differ from Go's `/`:
	// the waste line needs blockedMS > 0.3*totalMS > 0, the slow line needs
	// ElapsedMS > 120000, and the progress line needs totalMS > 60000. A
	// battery mutant swapping floor for truncation here is unkillable, and
	// that is a property of the guards rather than of the spelling. The
	// spelling stays because the guards are what a later edit relaxes.
	if totalMS > 0 {
		wastePct := float64(blockedMS) / float64(totalMS) * 100
		if wastePct > 30 {
			findings = append(findings, pyval.PercentF(wastePct, 0)+
				"% of wall-clock time spent on blocked steps ("+
				strconv.Itoa(pyval.FloorDiv(blockedMS, 1000))+"s / "+
				strconv.Itoa(pyval.FloorDiv(totalMS, 1000))+"s)")
			action = "Too much time on blocked steps — improve decomposition or constraint tuning"
		}
	}

	slow := 0
	for _, p := range profiles {
		if p.ElapsedMS > 120000 {
			slow++
			findings = append(findings, "Step "+strconv.Itoa(p.StepIdx)+" took "+
				strconv.Itoa(pyval.FloorDiv(p.ElapsedMS, 1000))+"s: "+
				pyval.Clip(p.Text, 60))
		}
	}
	if slow > 0 && action == "" {
		action = "Break slow steps into smaller units (target < 60s each)"
	}

	if totalMS > 60000 {
		doneCount := 0
		for _, p := range profiles {
			if p.Status == "done" {
				doneCount++
			}
		}
		rate := float64(doneCount) / (float64(totalMS) / 60000)
		findings = append(findings, "Progress rate: "+pyval.PercentF(rate, 1)+
			" steps/min ("+strconv.Itoa(doneCount)+" done in "+
			strconv.Itoa(pyval.FloorDiv(totalMS, 1000))+"s)")
	}

	confidence := 0.3
	if action != "" {
		confidence = 0.8
	}
	return LensResult{LensName: "operator", Findings: findings,
		Action: action, Confidence: confidence}
}

// forensicsStopwords is the set subtracted from the shared keywords of the
// blocked steps. It includes "step", which the other three sets do not.
var forensicsStopwords = map[string]bool{
	"the": true, "a": true, "an": true, "and": true, "or": true, "to": true,
	"in": true, "of": true, "for": true, "step": true,
}

// ForensicsLens is introspect._forensics_lens: what exactly failed, and
// what the last good state was.
func ForensicsLens(diag LoopDiagnosis, profiles []StepProfile) LensResult {
	if len(profiles) == 0 {
		return LensResult{LensName: "forensics"}
	}
	var findings []string
	action := ""

	// LAST done and FIRST block, which is not the same scan: the done index
	// keeps being overwritten and the block index is written once. A single
	// loop with an early break would find the first done, not the last.
	lastDone, firstBlock := -1, -1
	for i, p := range profiles {
		if p.Status == "done" {
			lastDone = i
		} else if isBlockedStatus(p.Status) && firstBlock == -1 {
			firstBlock = i
		}
	}
	if lastDone >= 0 && firstBlock >= 0 {
		findings = append(findings, "Last successful step: #"+
			strconv.Itoa(profiles[lastDone].StepIdx)+" ("+
			pyval.Clip(profiles[lastDone].Text, 60)+")")
		findings = append(findings, "First failure: #"+
			strconv.Itoa(profiles[firstBlock].StepIdx)+" ("+
			pyval.Clip(profiles[firstBlock].Text, 60)+")")
	}

	// `if first_block_idx > 0` — strictly greater, so a loop whose FIRST
	// step is the block never reports a token delta. That is not an
	// oversight to fix: `profiles[:0]` would sum to zero and the ratio
	// would divide by the `max(pre_tokens, 1)` guard, producing a
	// meaningless "0 → n (n.0x)" line for the commonest failure shape.
	if firstBlock > 0 {
		preTokens := 0
		for _, p := range profiles[:firstBlock] {
			preTokens += p.Tokens
		}
		failTokens := profiles[firstBlock].Tokens
		switch {
		case failTokens == 0:
			findings = append(findings,
				"Failure step consumed 0 tokens — blocked before LLM call")
			action = "Check constraint/policy layer or adapter resolution"
		case failTokens > preTokens:
			denom := preTokens
			if denom < 1 {
				denom = 1
			}
			findings = append(findings, "Failure step token jump: "+
				pyval.Grouped(int64(preTokens))+" → "+
				pyval.Grouped(int64(failTokens))+" ("+
				pyval.PercentF(float64(failTokens)/float64(denom), 1)+"x)")
		}
	}

	var blocked []StepProfile
	for _, p := range profiles {
		if isBlockedStatus(p.Status) {
			blocked = append(blocked, p)
		}
	}
	if len(blocked) >= 2 {
		common := wordSetMinus(blocked[0].Text, nil)
		for _, p := range blocked[1:] {
			ws := wordSetMinus(p.Text, nil)
			for w := range common {
				if !ws[w] {
					delete(common, w)
				}
			}
		}
		// The stopwords come off AFTER the intersection, matching Python.
		// The order is NOT observable and saying otherwise would be a
		// claim of coverage this file cannot back: subtraction distributes
		// over intersection, so (A∩B∩C)\S and (A\S)∩B∩C hold exactly the
		// same words for every input, and the gating count is the same
		// either way. A battery mutant that moved the removal earlier was
		// unkillable for that reason and was retired, not counted as a
		// fixture gap. Kept in Python's order because matching the source
		// is free; not because a test could tell.
		for w := range forensicsStopwords {
			delete(common, w)
		}
		if len(common) >= 2 {
			words := make([]string, 0, len(common))
			for w := range common {
				words = append(words, w)
			}
			sort.Strings(words)
			if len(words) > 5 {
				words = words[:5]
			}
			findings = append(findings,
				"Blocked steps share keywords: "+strings.Join(words, ", "))
		}
	}

	confidence := 0.5
	if action != "" {
		confidence = 0.9
	}
	return LensResult{LensName: "forensics", Findings: findings,
		Action: action, Confidence: confidence}
}

// ExecutionLens is introspect._execution_lens: the failure classifier
// wrapped as a lens.
func ExecutionLens(diag LoopDiagnosis, profiles []StepProfile) LensResult {
	var findings []string
	action := ""

	if diag.FailureClass != "healthy" {
		findings = append(findings, diag.Evidence...)
		// The one COMPUTED action in the module, and it can be "": a
		// diagnosis with no recommendation lands here and drops the
		// confidence to 0.3, because `0.9 if action else 0.3` is a
		// truthiness test. That is why LensResult carries "" as None.
		action = diag.Recommendation
	}

	if diag.StepsBlocked > 0 && diag.StepsDone > 0 {
		ratio := float64(diag.StepsBlocked) /
			float64(diag.StepsDone+diag.StepsBlocked)
		if ratio > 0.3 {
			// `{ratio:.0%}` multiplies by 100 BEFORE rounding, so a block
			// rate of 0.125 renders "12%" and 0.375 renders "38%" —
			// rounding first would answer "13%".
			//
			// The denominator printed is steps_TOTAL while the one
			// computed is done+blocked. They differ whenever a step is
			// neither, so the line can read "Block rate: 50% (2/10)".
			// Faithful to Python; named because it looks like a typo.
			findings = append(findings, "Block rate: "+pyval.PercentFmt(ratio, 0)+
				" ("+strconv.Itoa(diag.StepsBlocked)+"/"+
				strconv.Itoa(diag.StepsTotal)+")")
		}
	}

	confidence := 0.3
	if action != "" {
		confidence = 0.9
	}
	return LensResult{LensName: "execution", Findings: findings,
		Action: action, Confidence: confidence}
}

// isBlockedStatus is Python's `status in ("stuck", "blocked")`, which the
// lenses spell four times.
func isBlockedStatus(s string) bool { return s == "stuck" || s == "blocked" }

// wordSetMinus is `set(text.lower().split()) - drop`, with a nil `drop`
// meaning no subtraction. pytext.Lower and pytext.Split are Python's
// str.lower() and str.split(), not Go's per-rune ToLower and Fields.
func wordSetMinus(text string, drop map[string]bool) map[string]bool {
	out := map[string]bool{}
	for _, w := range pytext.Split(pytext.Lower(text)) {
		if drop == nil || !drop[w] {
			out[w] = true
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// Default registry
// ---------------------------------------------------------------------------

// DefaultLensRegistry is introspect.get_lens_registry() without the module
// global. Python memoises the registry in `_default_registry` and hands
// every caller the SAME mutable object, so a test that registers a lens
// changes every later caller's run order for the life of the process. The
// port returns a fresh registry: nothing in the module mutates the shared
// one, and a per-process singleton whose contents tests can edit is a
// cross-test channel rather than a feature.
//
// The two LLM-backed lenses Python registers — "quality" (cost "cheap") and
// "adversarial" (cost "mid") — are NOT here. Both call `build_adapter` and
// spend tokens, and the adapter suite is not ported. They are excluded
// rather than stubbed, because a registered lens that always returns
// nothing is indistinguishable from a working one that found nothing. The
// consequence is bounded and worth stating: RunHeuristic's answer is
// unaffected (it skips non-free lenses anyway), and there is no RunAll.
func DefaultLensRegistry() *LensRegistry {
	r := NewLensRegistry()
	r.Register("execution", ExecutionLens, "free")
	r.Register("cost", CostLens, "free")
	r.Register("architecture", ArchitectureLens, "free")
	r.Register("operator", OperatorLens, "free")
	r.Register("forensics", ForensicsLens, "free")
	return r
}

// RunLenses is introspect.run_lenses with include_llm=False.
//
// The filter is `if r.findings` — a lens with an ACTION but no findings is
// dropped, and no built-in lens can be in that state (every action is set
// alongside a finding). Kept as written because it is the caller-visible
// contract: the returned list is "lenses that found something", not "lenses
// that ran".
func RunLenses(diag LoopDiagnosis, profiles []StepProfile) []LensResult {
	var active []LensResult
	for _, r := range DefaultLensRegistry().RunHeuristic(diag, profiles) {
		if len(r.Findings) > 0 {
			active = append(active, r)
		}
	}
	return active
}
