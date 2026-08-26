// Package metrics ports the pure half of src/metrics.py — the pricing
// table, cache-aware cost estimation, and the step-type classifier.
//
// This is the dependency introspect's cost_spike check reads through
// (StepProfile.cost_usd calls estimate_cost), and nothing in the port had
// it: metrics.py appeared in exactly two Go comments and no code. The
// ledger half (step-costs.jsonl, spend_today, the p90 card) is a separate
// slice — it writes, and a writer belongs with its store.
package metrics

import (
	"unicode"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// ModelRate is one row of COST_BY_MODEL: USD per 1M tokens.
type ModelRate struct {
	Input  float64
	Output float64
}

// CostByModel is metrics.COST_BY_MODEL, verbatim.
//
// The keys are three different naming systems on purpose — full versioned
// IDs, the short-form aliases the subprocess adapter emits, and the tier
// constants config resolves to — because the model string on a step event
// is whatever the adapter that ran it wrote. A port that carried only one
// system would silently reprice every step the other two produced, and the
// fallback is Sonnet, so the error is quiet in both directions: Opus steps
// priced at a fifth, Haiku steps at nearly four times.
var CostByModel = map[string]ModelRate{
	// Claude 4.x (current) — full versioned IDs
	"claude-opus-4-6":           {Input: 15.00, Output: 75.00},
	"claude-sonnet-4-6":         {Input: 3.00, Output: 15.00},
	"claude-haiku-4-5":          {Input: 0.80, Output: 4.00},
	"claude-haiku-4-5-20251001": {Input: 0.80, Output: 4.00},
	// Short-form aliases (as used by subprocess adapter)
	"opus":   {Input: 15.00, Output: 75.00},
	"sonnet": {Input: 3.00, Output: 15.00},
	"haiku":  {Input: 0.80, Output: 4.00},
	// Tier constants
	"cheap": {Input: 0.80, Output: 4.00},
	"mid":   {Input: 3.00, Output: 15.00},
	"power": {Input: 15.00, Output: 75.00},
	// xAI Grok (backend "xai") — priced from the live /v1/language-models
	// endpoint 2026-08-08 (units there are USD per 10^10 tokens; ÷10⁴ = $/M)
	"grok-4.20-0309-non-reasoning": {Input: 1.25, Output: 2.50},
	"grok-4.20-0309-reasoning":     {Input: 1.25, Output: 2.50},
	"grok-4.3":                     {Input: 1.25, Output: 2.50},
	"grok-4.5":                     {Input: 2.00, Output: 6.00},
	"grok-build-0.1":               {Input: 1.00, Output: 2.00},
}

// The default fallback — mid-tier (Sonnet) when the model is unknown.
const (
	CostPerMInput  = 3.00
	CostPerMOutput = 15.00
	// CacheReadMultiplier is Anthropic's prompt-cache read rate: cached
	// input bills at ~0.1x fresh input.
	CacheReadMultiplier = 0.1
)

// EstimateCost is metrics.estimate_cost.
//
// tokensIn is the TOTAL input volume INCLUDING cache reads (the
// cross-adapter contract on LLMResponse.input_tokens), and cacheReadTokens
// says how many of those were served from cache.
//
// The arithmetic is spelled in Python's own term order rather than
// factored, because float addition is not associative and this number is
// compared against named dollar thresholds (_STEP_COST_WARN_USD = 0.50).
// A regrouped expression agrees to fifteen digits and can still land on
// the other side of a boundary.
func EstimateCost(tokensIn, tokensOut int, model string, cacheReadTokens int) float64 {
	costIn, costOut := CostPerMInput, CostPerMOutput
	// `COST_BY_MODEL.get(model or "", {})` — an empty model string and a
	// missing one take the same branch, and then `.get("input", default)`
	// falls back per-KEY. A rate row carrying only "input" would keep the
	// default output rate; no row in the table does, but the port must not
	// turn a partial row into a wholly defaulted one.
	if r, ok := CostByModel[model]; ok {
		costIn, costOut = r.Input, r.Output
	}
	// `max(0, min(cache_read_tokens, tokens_in))` — clamped, so a stamp
	// claiming more cache reads than input cannot make fresh_in negative
	// and credit the caller money.
	cacheRead := cacheReadTokens
	if cacheRead > tokensIn {
		cacheRead = tokensIn
	}
	if cacheRead < 0 {
		cacheRead = 0
	}
	freshIn := tokensIn - cacheRead
	return (float64(freshIn) * costIn / 1_000_000) +
		(float64(cacheRead) * costIn * CacheReadMultiplier / 1_000_000) +
		(float64(tokensOut) * costOut / 1_000_000)
}

// stepTypePattern is one entry of _STEP_TYPE_PATTERNS. Python spells them
// as regexes, but every one is the same shape — `\b(a|b|c)\b` over literal
// words — so the port carries the words and does the boundary test
// directly. See wordBoundaryContains for why that is not a shortcut.
type stepTypePattern struct {
	stepType string
	words    []string
	// extra is for the one alternative that is not a literal. It reports
	// the length of a match starting at index i.
	extra func(text []rune, i int) (int, bool)
}

// stepTypePatterns is _STEP_TYPE_PATTERNS, in order. The order IS the
// classification: classify_step_type returns the first entry that matches,
// so a step reading "research and implement X" is research, not implement.
var stepTypePatterns = []stepTypePattern{
	{stepType: "research", extra: matchesLookUp,
		words: []string{"research", "investigate", "find", "search", "fetch", "retrieve"}},
	{stepType: "summarize",
		words: []string{"summarize", "summarise", "compile", "distill", "condense", "aggregate"}},
	{stepType: "analyze",
		words: []string{"analyze", "analyse", "assess", "evaluate", "compare", "review", "examine"}},
	{stepType: "write",
		words: []string{"write", "draft", "create", "generate", "compose", "produce", "document"}},
	{stepType: "verify",
		words: []string{"verify", "check", "confirm", "validate", "test", "ensure", "prove"}},
	{stepType: "implement",
		words: []string{"implement", "build", "code", "develop", "refactor", "fix", "add feature"}},
	{stepType: "plan",
		words: []string{"plan", "design", "architect", "outline", "decompose", "structure"}},
}

// matchesLookUp is the one member of the table that is not a plain
// literal: Python writes `look\s*up`, which matches "lookup", "look up"
// and any run of whitespace between. It reports the rune length of a match
// starting at i.
//
// It is spelled out rather than regexp-compiled because Python's `\s` for
// str patterns is Unicode-aware while RE2's is the six ASCII spellings.
// Measured on this box: `re.match(r"look\s*up", ...)` matches across
// \x1c, \x1d, \x1e, \x1f, \xa0, \u2007 and \u3000 — exactly the set
// str.isspace() accepts — and does NOT match across \u200b (which is
// whitespace to neither runtime). pytext.IsSpace is str.isspace(); Go's
// unicode.IsSpace omits the four \x1c–\x1f separators.
func matchesLookUp(text []rune, i int) (int, bool) {
	const head, tail = "look", "up"
	if !hasAt(text, i, head) {
		return 0, false
	}
	j := i + len([]rune(head))
	for j < len(text) && pytext.IsSpace(text[j]) {
		j++
	}
	if !hasAt(text, j, tail) {
		return 0, false
	}
	return j + len([]rune(tail)) - i, true
}

func hasAt(text []rune, i int, lit string) bool {
	lr := []rune(lit)
	if i < 0 || i+len(lr) > len(text) {
		return false
	}
	for k, r := range lr {
		if text[i+k] != r {
			return false
		}
	}
	return true
}

// isWordRune is Python's `\w` for str patterns: "alphanumeric characters
// (as defined by str.isalnum()) as well as the underscore".
//
// This is the whole reason the port does not hand these patterns to
// regexp. Go's RE2 supports `\b`, but its `\b` is ASCII-only, so the step
// text "fixé" has a word boundary after "fix" in Go and none in Python —
// Go calls it implement, CPython calls it general, and the two runtimes
// bucket the same step's cost under different keys in a shared store.
func isWordRune(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r)
}

// wordBoundaryContains reports whether lit occurs in text delimited by
// Python's `\b` on both sides.
func wordBoundaryContains(text []rune, lit string) bool {
	lr := []rune(lit)
	if len(lr) == 0 {
		return false
	}
	for i := 0; i+len(lr) <= len(text); i++ {
		if !hasAt(text, i, lit) {
			continue
		}
		if boundedAt(text, i, len(lr)) {
			return true
		}
	}
	return false
}

// boundedAt applies `\b` at both ends of the run [i, i+n). A `\b` holds
// where a word rune abuts a non-word one — including the ends of the
// string, which count as non-word.
func boundedAt(text []rune, i, n int) bool {
	if isWordRune(text[i]) && i > 0 && isWordRune(text[i-1]) {
		return false
	}
	end := i + n
	if isWordRune(text[end-1]) && end < len(text) && isWordRune(text[end]) {
		return false
	}
	return true
}

// ClassifyStepType is metrics.classify_step_type: a step text bucketed
// into one of research, summarize, analyze, write, verify, implement,
// plan, general. The evolver groups step costs by this key.
func ClassifyStepType(stepText string) string {
	text := []rune(pytext.Lower(stepText))
	for _, p := range stepTypePatterns {
		for _, w := range p.words {
			if wordBoundaryContains(text, w) {
				return p.stepType
			}
		}
		if p.extra != nil {
			for i := range text {
				if n, ok := p.extra(text, i); ok && boundedAt(text, i, n) {
					return p.stepType
				}
			}
		}
	}
	return "general"
}
