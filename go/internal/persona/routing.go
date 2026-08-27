package persona

import (
	"context"
	"math"
	"regexp"
	"strings"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// routingRow is one entry of _PERSONA_ROUTING: (keywords, persona, base
// confidence). The table is ordered and the order is behaviour — the scan
// keeps a STRICTLY greater score, so the earliest row wins a tie.
type routingRow struct {
	Keywords   []string
	Persona    string
	Confidence float64
}

// personaRouting is _PERSONA_ROUTING, row for row and keyword for keyword.
var personaRouting = []routingRow{
	{[]string{"inbox", "calendar", "schedule", "daily brief", "morning brief",
		"triage", "to-do", "todo list", "task list", "what's on my plate",
		"appointments", "reminders", "follow up", "follow-up"}, "assistant", 0.85},
	{[]string{"csv", "spreadsheet", "xlsx", "dataset", "dataframe", "sql",
		"pdf table", "extract tables", "data analysis", "analyze data",
		"statistics", "statistical", "chart", "plot", "correlation",
		"aggregate", "pivot"}, "data-analyst", 0.85},
	{[]string{"psychology", "neuroscience", "cognition", "cognitive", "philosophy",
		"enneagram", "mbti", "personality", "memory model", "spaced repetition",
		"grit", "persistence", "learned helplessness", "kahneman", "system 1",
		"tacit knowledge", "expertise", "intrinsic motivation"}, "psyche-researcher", 0.85},
	{[]string{"health", "medical", "clinical", "symptoms", "symptom", "treatment",
		"medication", "disease", "diagnosis", "therapy", "nutrition",
		"exercise", "mental health", "wellness", "sleep", "diet"}, "health-researcher", 0.85},
	{[]string{"legal", "law", "contract", "compliance", "regulation", "liability",
		"gdpr", "privacy", "terms of service", "intellectual property",
		"copyright", "patent", "lawsuit", "jurisdiction", "statute"}, "legal-researcher", 0.85},
	{[]string{"strategy", "strategic", "roadmap", "direction", "prioritize",
		"prioritization", "milestones", "okr", "kpi", "tradeoff", "north star",
		"vision", "long-term", "short-term", "planning", "alignment"}, "strategist", 0.80},
	{[]string{"creative", "content", "narrative", "story", "brand", "voice",
		"copywriting", "headline", "tagline", "marketing copy", "campaign",
		"creative brief", "design direction", "tone of voice"}, "creative-director", 0.80},
	{[]string{"scrape", "scraping", "crawl", "crawling", "web extraction",
		"data extraction", "html", "parse", "playwright", "selenium",
		"beautifulsoup", "site map", "spider"}, "scrapling-adaptive-web-recon", 0.85},
	{[]string{"architecture", "system design", "scalability", "distributed",
		"microservice", "database schema", "data model", "latency",
		"throughput", "capacity", "design pattern", "trade-off analysis"}, "systems-design-architect-coach", 0.80},
	{[]string{"review", "critique", "evaluate", "assess", "quality", "problems",
		"weaknesses", "flaws", "risks", "what's wrong", "failure mode"}, "critic", 0.75},
	{[]string{"simplify", "simplification", "too complex", "over-engineered",
		"delete", "remove", "deprecate", "refactor toward", "reduce complexity",
		"unnecessary", "bloat", "dead code"}, "simplifier", 0.80},
	{[]string{"research", "investigate", "analyse", "analyze", "summarise", "summarize",
		"tweet", "article", "paper", "study", "literature", "findings", "survey",
		"what does", "what is", "how does", "explain", "why does"}, "research-assistant-deep-synth", 0.75},
	{[]string{"build", "implement", "code", "write", "create", "develop", "add feature",
		"fix bug", "refactor", "test", "unit test", "integration", "deploy",
		"function", "class", "module", "api", "endpoint"}, "builder", 0.80},
	{[]string{"monitor", "diagnose", "health", "service", "systemd", "cron", "deploy",
		"restart", "log", "alert", "heartbeat", "disk", "memory usage",
		"process", "daemon", "script", "automation"}, "ops", 0.75},
	{[]string{"polymarket", "market", "trading", "prediction market", "bet", "odds",
		"finance", "investment", "portfolio", "price", "token", "crypto"}, "finance-analyst", 0.80},
	{[]string{"consolidate", "synthesize", "synthesis", "combine outputs", "merge results",
		"write report", "compile findings", "summarize all", "integrate results",
		"final report", "deliverable", "combine sub-agent"}, "reporter", 0.80},
}

// DefaultPersona is _DEFAULT_PERSONA.
const DefaultPersona = "research-assistant-deep-synth"

// routingFallbacks is persona_for_goal's `fallbacks` dict: where to land
// when the keyword winner is not installed. A name that is not a key falls
// straight to [DefaultPersona], and the for/else below turns a
// no-alternative-available into (DefaultPersona, 0.5) rather than into the
// penalized score.
var routingFallbacks = map[string][]string{
	"psyche-researcher": {DefaultPersona},
	"finance-analyst":   {DefaultPersona},
	"health-researcher": {DefaultPersona},
	"legal-researcher":  {DefaultPersona},
	"assistant":         {"reporter"},
	"data-analyst":      {DefaultPersona},
}

// kwRE caches the compiled word-boundary matcher for a single-token
// keyword. Python compiles inside `_kw_match` and relies on re's own cache;
// the table is a module constant, so 200-odd patterns are built once here.
var (
	kwREMu    sync.Mutex
	kwRECache = map[string]*regexp.Regexp{}
)

// kwMatch is `_kw_match`: a multi-word phrase is a plain substring test and
// a single token gets word boundaries.
//
// `\b` is the divergence. Python's is Unicode-aware, Go's is ASCII, so
// `\bmarket\b` matches inside "fümarket" for Go — ü is a non-word byte
// sequence to an ASCII boundary — and does not for CPython, where ü is a
// word character and there is no boundary at all. The direction is
// over-matching: the Go side routes a goal to finance-analyst that CPython
// leaves at the default.
//
// pytext.WordStart/WordEnd are CONSUMING stand-ins, `(?:^|\W)` and
// `(?:$|\W)`, where `\b` is zero width. For a pure "does it match at all"
// question over a pattern whose boundaries sit at both ends of a literal,
// the two are equivalent: RE2 sweeps every start offset, so the character
// a stand-in eats is never needed by an earlier part of the same pattern.
// (It would NOT be equivalent inside a bounded window — see intent's r7
// note, where consuming two characters had to be paid for out of a {0,40}.)
func kwMatch(kw, text string) bool {
	if strings.Contains(kw, " ") {
		return strings.Contains(text, kw)
	}
	return keywordRE(kw).MatchString(text)
}

func keywordRE(kw string) *regexp.Regexp {
	kwREMu.Lock()
	defer kwREMu.Unlock()
	if re, ok := kwRECache[kw]; ok {
		return re
	}
	// `re.escape` and regexp.QuoteMeta escape different sets — Python also
	// escapes '-' and whitespace, Go does not — but both produce a pattern
	// matching the literal text, which is all the escape is for.
	re := regexp.MustCompile(pytext.WordStart + regexp.QuoteMeta(kw) + pytext.WordEnd)
	kwRECache[kw] = re
	return re
}

// SelectorAdapter is the LLM fallback's seam: `adapter.complete([...],
// max_tokens=30, no_tools=True, purpose="persona selection")`.
type SelectorAdapter = llm.Adapter

// ForGoal selects the best persona for a goal, returning (name,
// confidence). Port of persona_for_goal.
//
// reg may be nil (Python's registry=None): the availability check is then
// skipped entirely, so an uninstalled persona is returned as the winner.
// adapter may be nil, which with allowLLMFallback true is Python's `adapter
// is not None` guard failing — the fallback block is simply not entered.
//
// The arithmetic is `min(1.0, base * (1.0 + (hits - 1) * 0.05))` in that
// association order, and it is reproduced operation for operation because
// the result is a float that reaches a durable dispatch-log row: writing it
// as `base + base*(hits-1)*0.05` is algebraically equal and not
// bit-equal, and 0.8925 vs 0.8925000000000001 is a diff in a shared ledger.
func ForGoal(ctx context.Context, goal string, reg *Registry,
	confidenceThreshold float64, allowLLMFallback bool, adapter llm.Adapter) (string, float64) {

	// `str.lower()`, not strings.ToLower: they differ on U+0130 (which
	// lowercases to two characters) and on final sigma.
	goalLower := pytext.Lower(goal)

	bestName := DefaultPersona
	bestConf := 0.0

	for _, row := range personaRouting {
		hits := 0
		for _, kw := range row.Keywords {
			if kwMatch(kw, goalLower) {
				hits++
			}
		}
		if hits == 0 {
			continue
		}
		conf := math.Min(1.0, row.Confidence*(1.0+float64(hits-1)*0.05))
		if conf > bestConf {
			bestConf = conf
			bestName = row.Persona
		}
	}

	if reg != nil && bestName != DefaultPersona {
		available := reg.List()
		if !containsString(available, bestName) {
			alternatives, ok := routingFallbacks[bestName]
			if !ok {
				alternatives = []string{DefaultPersona}
			}
			matched := false
			for _, alt := range alternatives {
				if containsString(available, alt) {
					bestName = alt
					bestConf *= 0.9
					matched = true
					break
				}
			}
			// Python's for/else: no `break` means no alternative was
			// installed either, and the score is REPLACED by 0.5 rather
			// than penalized.
			if !matched {
				bestName = DefaultPersona
				bestConf = 0.5
			}
		}
	}

	if bestConf >= confidenceThreshold {
		return bestName, bestConf
	}

	if allowLLMFallback && adapter != nil {
		var availableNames []string
		if reg != nil {
			availableNames = reg.List()
		} else {
			// `list(gen) + [_DEFAULT_PERSONA]` — the `+` binds tighter
			// than the conditional expression, so the default is appended
			// only on the registry-less branch, and the routing names
			// arrive with their duplicates intact (there are none today).
			for _, row := range personaRouting {
				availableNames = append(availableNames, row.Persona)
			}
			availableNames = append(availableNames, DefaultPersona)
		}
		if name, ok := llmSelect(ctx, adapter, goal, availableNames); ok {
			return name, 0.80
		}
	}

	// `best_name or _DEFAULT_PERSONA` — best_name cannot be empty on any
	// path above, but the guard is what the source writes.
	if bestName == "" {
		bestName = DefaultPersona
	}
	return bestName, math.Max(bestConf, 0.5)
}

// llmSelect is the body of persona_for_goal's `try:` block. Every failure
// inside it — a transport error, an empty reply, a name not on the list —
// is Python's `except Exception: pass` or its `if llm_name in ...` failing,
// and both mean "fall through to the keyword answer".
func llmSelect(ctx context.Context, adapter llm.Adapter, goal string,
	availableNames []string) (string, bool) {

	personasStr := strings.Join(availableNames, ", ")
	prompt := "Available personas: " + personasStr + "\n\n" +
		"Goal: " + clipRunes(goal, 300) + "\n\n" +
		"Which single persona best fits this goal? Reply with ONLY the persona name, nothing else."

	resp, err := adapter.Complete(ctx, []llm.Message{{Role: "user", Content: prompt}},
		llm.Options{MaxTokens: 30, Purpose: "persona selection"})
	if err != nil || resp == nil {
		return "", false
	}
	// `resp.content.strip().lower().split()[0] if resp.content.strip() else ""`
	// — str.split() with no argument, which splits on runs of Python
	// whitespace and drops leading ones, so the guard and the index can
	// never disagree.
	stripped := pytext.Strip(resp.Content)
	if stripped == "" {
		return "", false
	}
	parts := pytext.Split(pytext.Lower(stripped))
	if len(parts) == 0 {
		return "", false
	}
	name := parts[0]
	if containsString(availableNames, name) {
		return name, true
	}
	return "", false
}

// clipRunes is `goal[:300]` — a CODE POINT slice. A byte slice of a goal
// written in anything but ASCII cuts mid-rune and puts U+FFFD in a prompt.
func clipRunes(s string, n int) string {
	rs := []rune(s)
	if len(rs) <= n {
		return s
	}
	return string(rs[:n])
}

func containsString(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}
