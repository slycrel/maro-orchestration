// TF-IDF relevance ranking — knowledge_web._tfidf_rank_scored ported
// number-for-number (Phase 35 P1 + the Phase 60 citation penalty). Pure
// stdlib on both sides; the Go tests pin scores against CPython-computed
// fixtures. Python's optional hybrid BM25/RRF lane (_USE_HYBRID, rank-bm25
// dependency) is deliberately unported — TF-IDF is Python's own
// always-available fallback, so both runtimes share this floor.
package knowledge

import (
	"math"
	"regexp"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// stopWords is knowledge_web._STOP_WORDS verbatim.
var stopWords = map[string]bool{
	"a": true, "an": true, "the": true, "and": true, "or": true,
	"but": true, "in": true, "on": true, "at": true, "to": true,
	"for": true, "of": true, "with": true, "is": true, "was": true,
	"are": true, "were": true, "be": true, "been": true, "being": true,
	"it": true, "its": true, "this": true, "that": true, "these": true,
	"those": true, "i": true, "we": true, "you": true, "he": true,
	"she": true, "they": true, "what": true, "when": true, "where": true,
	"who": true, "which": true, "how": true, "if": true, "as": true,
	"by": true, "from": true, "not": true, "can": true, "will": true,
	"do": true, "did": true, "does": true, "have": true, "had": true,
	"has": true, "should": true, "would": true, "could": true,
	"may": true, "might": true, "step": true, "goal": true, "task": true,
}

var nonAlnumRe = regexp.MustCompile(`[^a-z0-9]+`)

// Tokenize ports _tokenize: lowercase, split on non-alphanumeric runs,
// drop stop words and tokens of length <= 2. Post-substitution tokens
// are pure ASCII [a-z0-9], so byte length == Python's rune length.
//
// pytext.Lower, not strings.ToLower: str.lower() EXPANDS U+0130 to two
// runes (i + U+0307) and Go's folds it to one. The combining mark is not
// [a-z0-9], so the regex below turns it into a SPLIT — measured,
// knowledge_web._tokenize("DIFFİCULT case") is ['diffi', 'cult', 'case']
// and this returned ["difficult", "case"]. Different tokens are
// different TF-IDF vectors, so the same query ranked lessons differently
// in the two runtimes off one dotted capital (adversarial mission-r7
// MEDIUM).
func Tokenize(text string) []string {
	var out []string
	for _, t := range strings.Fields(nonAlnumRe.ReplaceAllString(pytext.Lower(text), " ")) {
		if len(t) > 2 && !stopWords[t] {
			out = append(out, t)
		}
	}
	return out
}

// TFIDFRankScored ports _tfidf_rank_scored: cosine similarity between
// the query's TF-IDF vector and each lesson's, the corpus being query +
// all lesson texts; uncited lessons (empty evidence_sources) are
// multiplied by CitationPenalty so cited lessons rank higher on ties.
// topK < 0 returns all, ranked. Parity quirk kept deliberately: a query
// with NO signal tokens returns ALL lessons in input order at score 0.0,
// topK ignored — Python's no-signal path slices only at the caller.
// Ties preserve input order (Python's stable sort; SliceStable here).
func TFIDFRankScored(query string, lessons []TieredLesson, topK int) []ScoredLesson {
	if len(lessons) == 0 {
		return nil
	}
	queryTerms := Tokenize(query)
	if len(queryTerms) == 0 {
		out := make([]ScoredLesson, len(lessons))
		for i, l := range lessons {
			out[i] = ScoredLesson{Lesson: l, Score: 0.0}
		}
		return out
	}

	docs := make([][]string, 0, len(lessons)+1)
	docs = append(docs, queryTerms)
	for _, l := range lessons {
		docs = append(docs, Tokenize(l.Lesson))
	}
	nDocs := float64(len(docs)) // includes the query document

	df := map[string]int{}
	for _, doc := range docs {
		seen := map[string]bool{}
		for _, term := range doc {
			if !seen[term] {
				seen[term] = true
				df[term]++
			}
		}
	}
	idf := func(term string) float64 {
		return math.Log(nDocs/float64(df[term]+1)) + 1.0
	}
	tfidfVec := func(terms []string) map[string]float64 {
		tf := map[string]int{}
		for _, t := range terms {
			tf[t]++
		}
		total := float64(len(terms))
		if total < 1 {
			total = 1
		}
		vec := make(map[string]float64, len(tf))
		for t, c := range tf {
			vec[t] = (float64(c) / total) * idf(t)
		}
		return vec
	}
	cosine := func(v1, v2 map[string]float64) float64 {
		dot := 0.0
		for t, x := range v1 {
			dot += x * v2[t]
		}
		norm := func(v map[string]float64) float64 {
			s := 0.0
			for _, x := range v {
				s += x * x
			}
			n := math.Sqrt(s)
			if n == 0 {
				return 1.0 // Python's `or 1.0`
			}
			return n
		}
		return dot / (norm(v1) * norm(v2))
	}

	queryVec := tfidfVec(queryTerms)
	scored := make([]ScoredLesson, 0, len(lessons))
	for i, l := range lessons {
		sim := cosine(queryVec, tfidfVec(docs[i+1]))
		if len(l.EvidenceSources) == 0 {
			sim *= CitationPenalty
		}
		scored = append(scored, ScoredLesson{Lesson: l, Score: sim})
	}
	sort.SliceStable(scored, func(i, j int) bool {
		return scored[i].Score > scored[j].Score
	})
	if topK >= 0 && len(scored) > topK {
		scored = scored[:topK]
	}
	return scored
}
