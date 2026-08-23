package skills

import (
	"math"
	"regexp"
	"sort"
	"strings"
)

// ---------------------------------------------------------------------------
// Tokenization (MetaClaw steal: lightweight matching without embeddings)
// ---------------------------------------------------------------------------

var skillStopWords = map[string]bool{
	"a": true, "an": true, "the": true, "and": true, "or": true, "but": true,
	"in": true, "on": true, "at": true, "to": true, "for": true, "of": true,
	"with": true, "by": true, "from": true, "is": true, "it": true, "be": true,
	"as": true, "this": true, "that": true, "are": true, "was": true,
	"were": true, "been": true, "have": true, "has": true, "had": true,
	"do": true, "does": true, "did": true, "will": true, "would": true,
	"could": true, "should": true, "may": true, "might": true, "can": true,
	"not": true, "no": true, "so": true, "if": true, "we": true, "i": true,
	"you": true, "he": true, "she": true, "they": true,
}

// stemSuffixes is Python's tuple, IN ORDER. The order is load-bearing:
// longest suffixes first to avoid double-stripping, and the FIRST match
// wins. Duplicates in the Python tuple ("ations" twice) are kept so the
// sequence stays identical — a reordering would change which rule fires.
var stemSuffixes = []struct {
	suffix  string
	minRoot int
}{
	{"ations", 4}, {"ization", 4}, {"isation", 4},
	{"tion", 4}, {"ing", 4}, {"ness", 4}, {"ment", 4},
	{"ers", 4}, {"ings", 4}, {"ations", 4},
	{"ed", 4}, {"er", 4}, {"es", 4}, {"ly", 4}, {"s", 4},
}

// stem ports _stem: strip a common English suffix when the remaining root
// is at least minRoot characters.
func stem(token string) string {
	for _, r := range stemSuffixes {
		if strings.HasSuffix(token, r.suffix) && len(token)-len(r.suffix) >= r.minRoot {
			return token[:len(token)-len(r.suffix)]
		}
	}
	return token
}

// splitNonAlnum matches Python's re.split(r"[^a-z0-9]+", text.lower()).
// The class is ASCII-only on purpose: it is what Python's pattern says, so
// a Unicode-aware split would tokenize differently across runtimes.
var splitNonAlnum = regexp.MustCompile(`[^a-z0-9]+`)

// skillTokens ports _skill_tokens: lowercase, split on non-alphanumerics,
// drop short tokens and stop words, stem what remains.
func skillTokens(text string) []string {
	var out []string
	for _, t := range splitNonAlnum.Split(strings.ToLower(text), -1) {
		if len(t) >= 3 && !skillStopWords[t] {
			out = append(out, stem(t))
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// Island model (FunSearch-inspired diversity mechanism)
// ---------------------------------------------------------------------------

// islandOrder fixes the iteration order Python gets from dict insertion.
// max() over the score map returns the FIRST maximum in iteration order, so
// a tie between islands resolves by this order in both runtimes — Go's map
// iteration is randomized and would otherwise make ties nondeterministic.
var islandOrder = []string{"research", "build", "analysis"}

var islandKeywords = map[string][]string{
	"research": {"research", "fetch", "search", "web", "find", "look",
		"information", "data", "gather", "scrape", "news", "article", "paper"},
	"build": {"build", "code", "write", "implement", "create", "generate",
		"develop", "make", "produce", "draft", "design"},
	"analysis": {"analyz", "check", "inspect", "review", "test", "evaluat",
		"assess", "audit", "verif", "compar", "diagnos", "measure"},
}

// goalIsland scores goal text against the island keyword sets and returns
// the best-scoring island, or "" when nothing matched. Inlined the same way
// Python inlines it inside _tfidf_skill_rank (goal text only, not a skill).
func goalIsland(goal string) string {
	lower := strings.ToLower(goal)
	best, bestScore := "", 0
	for _, isl := range islandOrder {
		score := 0
		for _, kw := range islandKeywords[isl] {
			if strings.Contains(lower, kw) {
				score++
			}
		}
		if score > bestScore { // strict >: first max wins, like Python's max()
			best, bestScore = isl, score
		}
	}
	if bestScore > 0 {
		return best
	}
	return ""
}

// ---------------------------------------------------------------------------
// TF-IDF ranking
// ---------------------------------------------------------------------------

const islandBoost = 0.20 // +20% score bonus for island match (NeMo S4)

// tfidfSkillRank ports _tfidf_skill_rank: cosine similarity over a smoothed
// TF-IDF space, with an island-match boost, returning up to topK skills with
// non-zero similarity. Winners are stamped with the match-tier telemetry the
// attribution path reads — a weak retrieval must be distinguishable from a
// genuine trigger match.
//
// This is a SEPARATE TF-IDF from knowledge.TFIDFRankScored on purpose: the
// tokenizer (stemming + skill stop words) and the document corpus (name +
// description + domain + triggers + tags) differ, and Python keeps them
// separate for the same reason. Sharing the implementation would silently
// change one consumer's ranking.
func tfidfSkillRank(goal string, pool []Skill, topK int) []Skill {
	queryTokens := skillTokens(goal)
	if len(queryTokens) == 0 || len(pool) == 0 {
		return nil
	}
	island := goalIsland(goal)

	docTokens := make([][]string, len(pool))
	for i, s := range pool {
		parts := append([]string{s.Name, s.Description, s.Domain},
			append(append([]string{}, s.TriggerPatterns...), s.Tags...)...)
		docTokens[i] = skillTokens(strings.Join(parts, " "))
	}
	n := float64(len(pool))

	df := map[string]int{}
	for _, tokens := range docTokens {
		seen := map[string]bool{}
		for _, t := range tokens {
			if !seen[t] {
				seen[t] = true
				df[t]++
			}
		}
	}
	// Smooth IDF log((N+1)/(1+df)) — handles small N without zeroing out.
	idf := make(map[string]float64, len(df))
	for t, c := range df {
		idf[t] = math.Log((n + 1) / (1 + float64(c)))
	}
	vec := func(tokens []string) map[string]float64 {
		tf := map[string]int{}
		for _, t := range tokens {
			tf[t]++
		}
		total := float64(len(tokens))
		if total == 0 {
			total = 1
		}
		out := make(map[string]float64, len(tf))
		for t, c := range tf {
			out[t] = (float64(c) / total) * idf[t]
		}
		return out
	}
	cosine := func(a, b map[string]float64) float64 {
		var dot, na, nb float64
		for t, av := range a {
			dot += av * b[t]
		}
		for _, v := range a {
			na += v * v
		}
		for _, v := range b {
			nb += v * v
		}
		na, nb = math.Sqrt(na), math.Sqrt(nb)
		if na == 0 {
			na = 1
		}
		if nb == 0 {
			nb = 1
		}
		return dot / (na * nb)
	}

	qVec := vec(queryTokens)
	type scored struct {
		score float64
		idx   int
	}
	var ranked []scored
	for i, s := range pool {
		sc := cosine(qVec, vec(docTokens[i]))
		if sc > 0 {
			if island != "" && s.Island == island {
				sc = sc * (1.0 + islandBoost)
			}
			ranked = append(ranked, scored{sc, i})
		}
	}
	// Stable sort by score desc: Python's list.sort is stable, so equal
	// scores keep pool order in both runtimes.
	sort.SliceStable(ranked, func(i, j int) bool {
		return ranked[i].score > ranked[j].score
	})
	if len(ranked) > topK {
		ranked = ranked[:topK]
	}
	out := make([]Skill, 0, len(ranked))
	for _, r := range ranked {
		s := pool[r.idx]
		s.MatchMethod = "tfidf_fallback"
		s.MatchScore = round4(r.score)
		out = append(out, s)
	}
	return out
}

func round4(f float64) float64 {
	// Python's round() is half-to-even; the port uses the same rule
	// wherever a rounded value can reach a shared file or a tie.
	return math.RoundToEven(f*10000) / 10000
}

// ---------------------------------------------------------------------------
// Matching
// ---------------------------------------------------------------------------

// MatchTelemetry is the caller-supplied telemetry dict: it turns the old
// binary gap signal ("empty match set") into a graded one. Method is "none"
// when nothing matched.
type MatchTelemetry struct {
	Method      string             `json:"method"`
	NCandidates int                `json:"n_candidates"`
	TopScore    float64            `json:"top_score"`
	Scores      map[string]float64 `json:"scores"`
}

// MatchOptions mirrors find_matching_skills' keyword arguments.
type MatchOptions struct {
	// Project isolation: when non-empty, only skills with project==""
	// (global) or project==this value are considered.
	Project string
	// OnlyIDs restricts candidates to the run's injected manifest (used at
	// attribution time). When set, A/B challengers in the set stay eligible
	// — they were the routed arm. When nil (candidate discovery),
	// challengers are excluded: a challenger is reachable ONLY via its
	// parent's routing.
	OnlyIDs []string
}

// FindMatchingSkills ports find_matching_skills' tier ladder, minus the
// trained-router tier.
//
// NAMED GAP, not a silent one: Python's Phase-17 router tier runs first when
// a trained model is available and falls through to keyword matching on any
// failure. router.py is not ported (no consumer in Go yet), so this is the
// fall-through path Python takes on an untrained workspace — behaviourally
// identical there, and a real difference on a box whose router IS trained.
// The tier stamps ("router"/"mixed") stay defined in the telemetry contract
// so the field means the same thing in both runtimes' records.
func FindMatchingSkills(ws string, goal string, o MatchOptions) ([]Skill, MatchTelemetry) {
	tel := MatchTelemetry{Method: "none", Scores: map[string]float64{}}
	note := func(method string, winners []Skill, scores []float64, nCandidates int) []Skill {
		out := make([]Skill, 0, len(winners))
		for i, sk := range winners {
			sk.MatchMethod = method
			sk.MatchScore = round4(scores[i])
			out = append(out, sk)
		}
		tel.NCandidates = nCandidates
		if len(out) == 0 {
			tel.Method = "none"
			tel.TopScore = 0
			return out
		}
		tel.Method = method
		tel.TopScore = round4(scores[0])
		for i, sk := range out {
			tel.Scores[sk.ID] = round4(scores[i])
		}
		return out
	}

	pool := LoadSkills(ws).Skills
	if len(pool) == 0 {
		return note("none", nil, nil, 0), tel
	}

	if o.Project != "" {
		var kept []Skill
		for _, s := range pool {
			if s.Project == "" || s.Project == o.Project {
				kept = append(kept, s)
			}
		}
		pool = kept
	}

	// Open circuit breaker → not injectable until rewritten/recovered.
	var closed []Skill
	for _, s := range pool {
		if s.CircuitState != "open" {
			closed = append(closed, s)
		}
	}
	pool = closed

	if o.OnlyIDs != nil {
		only := map[string]bool{}
		for _, id := range o.OnlyIDs {
			only[id] = true
		}
		var kept []Skill
		for _, s := range pool {
			if only[s.ID] {
				kept = append(kept, s)
			}
		}
		pool = kept
	} else {
		var kept []Skill
		for _, s := range pool {
			if s.VariantOf == nil || *s.VariantOf == "" {
				kept = append(kept, s)
			}
		}
		pool = kept
	}
	if len(pool) == 0 {
		return note("none", nil, nil, 0), tel
	}

	// Keyword tier: exact trigger-pattern overlap, either direction, plus
	// tags as substring-in-goal only (a tag is a short keyword — the
	// reverse goal-in-tag test would be noise).
	goalLower := strings.ToLower(goal)
	type kwHit struct {
		score int
		skill Skill
	}
	var hits []kwHit
	for _, s := range pool {
		score := 0
		for _, p := range s.TriggerPatterns {
			pl := strings.ToLower(p)
			if strings.Contains(goalLower, pl) || strings.Contains(pl, goalLower) {
				score++
			}
		}
		for _, t := range s.Tags {
			if t != "" && strings.Contains(goalLower, strings.ToLower(t)) {
				score++
			}
		}
		if score > 0 {
			hits = append(hits, kwHit{score, s})
		}
	}
	if len(hits) > 0 {
		sort.SliceStable(hits, func(i, j int) bool { return hits[i].score > hits[j].score })
		if len(hits) > 3 {
			hits = hits[:3]
		}
		winners := make([]Skill, 0, len(hits))
		scores := make([]float64, 0, len(hits))
		for _, h := range hits {
			winners = append(winners, h.skill)
			scores = append(scores, float64(h.score))
		}
		return note("keyword", winners, scores, len(pool)), tel
	}

	// TF-IDF tier: relevance-ranked retrieval when no keyword match fires
	// (selective retrieval — prevents returning stale/irrelevant skills).
	ranked := tfidfSkillRank(goal, pool, 2)
	scores := make([]float64, 0, len(ranked))
	for _, s := range ranked {
		scores = append(scores, s.MatchScore)
	}
	return note("tfidf_fallback", ranked, scores, len(pool)), tel
}

// FormatSkillsForPrompt ports format_skills_for_prompt: the injection block
// prepended to a planning prompt. Empty string when nothing matched.
func FormatSkillsForPrompt(matched []Skill) string {
	if len(matched) == 0 {
		return ""
	}
	lines := []string{"Reusable skills from past successful goals:"}
	for _, s := range matched {
		lines = append(lines, "\nSkill: "+s.Name+" — "+s.Description)
		if s.OptimizationObjective != "" {
			lines = append(lines, "Optimize for: "+s.OptimizationObjective)
		}
		lines = append(lines, "Steps:")
		for _, step := range s.StepsTemplate {
			lines = append(lines, "  - "+step)
		}
	}
	return strings.Join(lines, "\n")
}
