package knowledge

import (
	"encoding/json"
	"math"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// pySrc is this repo's Python tree, relative to a Go package directory.
// The other differentials in this port ask CPython about a STDLIB
// function and can do it with a self-contained `python3 -c`; ranking has
// no stdlib equivalent, so the probe below imports the real
// knowledge_web and calls the real _tfidf_rank_scored. Re-expressing
// TF-IDF inside the probe would be re-deriving the thing under test —
// a bug shared by both transcriptions reports agreement.
const pySrc = "../../../src"

const rankProbe = `
import json, sys
sys.path.insert(0, sys.argv[1])
import knowledge_web as kw
req = json.loads(sys.stdin.read())
lessons = [
    kw.TieredLesson(
        lesson_id=l["lesson_id"], task_type="agenda", outcome="done",
        lesson=l["lesson"], source_goal="g", confidence=0.8,
        tier=kw.MemoryTier.MEDIUM, score=1.0, last_reinforced="2026-08-01",
        evidence_sources=l["evidence_sources"],
    )
    for l in req["lessons"]
]
out = []
for q in req["queries"]:
    ranked = kw._tfidf_rank_scored(q, lessons, top_k=None)
    out.append([[l.lesson_id, s] for l, s in ranked])
print(json.dumps(out))
`

// r7RankCorpus is rankFixtureCorpus plus the rows that make the ranking
// SEPARABLE. Every text in the original fixture is ASCII, where
// strings.ToLower and str.lower() agree by construction, so the frozen
// snapshot it fed could report agreement while the tokenizer diverged.
func r7RankCorpus() []TieredLesson {
	return append(rankFixtureCorpus(),
		// U+0130: str.lower() expands it to i + U+0307 and the tokenizer's
		// [^a-z0-9]+ substitution turns the mark into a split, so CPython
		// indexes "diffi"/"cult" where Go indexed "difficult".
		fixtureLesson("l6", "DIFFİCULT stdin cases in the subprocess adapter", nil),
		fixtureLesson("l7", "diffi cult stdin handling", []any{"run:q"}),
	)
}

// TestTFIDFRankScoredMatchesCPythonLive replaces a frozen snapshot that
// carried "MatchesCPython" in its name and never ran python3. Two things
// were wrong with it: the numbers could only ever re-assert whatever the
// generator produced once, and its corpus was all-ASCII, so it could not
// have separated on the tokenizer difference this round found
// (adversarial mission-r7 MEDIUM — false-green head 1 and head 3 in one
// test).
func TestTFIDFRankScoredMatchesCPythonLive(t *testing.T) {
	if _, err := os.Stat(pySrc); err != nil {
		t.Skipf("python tree not beside this checkout: %v", err)
	}
	corpus := r7RankCorpus()
	queries := []string{
		"workspace write path verification before memory store writes",
		"subprocess adapter CLI backend stdin",
		"DIFFİCULT stdin",
		"diffi cult stdin",
		"the and of to a is",
	}
	type wire struct {
		LessonID        string `json:"lesson_id"`
		Lesson          string `json:"lesson"`
		EvidenceSources []any  `json:"evidence_sources"`
	}
	req := map[string]any{"queries": queries}
	rows := make([]wire, 0, len(corpus))
	for _, l := range corpus {
		src := l.EvidenceSources
		if src == nil {
			src = []any{}
		}
		rows = append(rows, wire{l.LessonID, l.Lesson, src})
	}
	req["lessons"] = rows
	in, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", rankProbe, pySrc)
	cmd.Stdin = strings.NewReader(string(in))
	out, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want [][][2]any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output: %v (%s)", err, out)
	}
	if len(want) != len(queries) {
		t.Fatalf("probe returned %d rows for %d queries", len(want), len(queries))
	}

	// Anti-vacuity: replay the PRE-FIX tokenizer — strings.ToLower rather
	// than pytext.Lower — through the same ranking and require it to
	// lose. A corpus that cannot separate reports agreement and proves
	// nothing about either implementation.
	oldLost := 0
	for qi, q := range queries {
		got := rankWith(strings.ToLower, q, corpus)
		if !sameRanking(got, want[qi], 1e-9) {
			oldLost++
		}
	}
	if oldLost == 0 {
		t.Fatal("the pre-fix tokenizer ranks this corpus exactly as CPython " +
			"does: these fixtures could not have caught the finding")
	}

	for qi, q := range queries {
		got := TFIDFRankScored(q, corpus, -1)
		if len(got) != len(want[qi]) {
			t.Fatalf("query %q: %d results, want %d", q, len(got), len(want[qi]))
		}
		for i, w := range want[qi] {
			id, _ := w[0].(string)
			score, _ := w[1].(float64)
			if got[i].Lesson.LessonID != id {
				t.Fatalf("query %q pos %d: got %s (%.6f), want %s",
					q, i, got[i].Lesson.LessonID, got[i].Score, id)
			}
			if math.Abs(got[i].Score-score) > 1e-9 {
				t.Fatalf("query %q %s: score %v, want %v (CPython)",
					q, id, got[i].Score, score)
			}
		}
	}
}

// rankWith runs the ranking with a substituted lowercaser, so the
// pre-fix implementation can be replayed without keeping a second copy
// of the ranking itself.
func rankWith(lower func(string) string, query string, lessons []TieredLesson) []ScoredLesson {
	swapped := make([]TieredLesson, len(lessons))
	for i, l := range lessons {
		l.Lesson = preFold(lower, l.Lesson)
		swapped[i] = l
	}
	return TFIDFRankScored(preFold(lower, query), swapped, -1)
}

// preFold applies a lowercaser up front so the production Tokenize's own
// (now correct) one is a no-op on the result — an already-lowercased
// ASCII string is a fixed point of both.
//
// This replay is only meaningful because the corpus contains l7, whose
// text is the literal "diffi cult". Cosine similarity is invariant under
// renaming a token CONSISTENTLY across query and corpus, so a fixture
// where the only non-ASCII text is the query would score identically
// under both tokenizers however differently it split — the first draft
// of this test did exactly that and the guard below caught it.
func preFold(lower func(string) string, s string) string { return lower(s) }

func sameRanking(got []ScoredLesson, want [][2]any, tol float64) bool {
	if len(got) != len(want) {
		return false
	}
	for i, w := range want {
		id, _ := w[0].(string)
		score, _ := w[1].(float64)
		if got[i].Lesson.LessonID != id || math.Abs(got[i].Score-score) > tol {
			return false
		}
	}
	return true
}
