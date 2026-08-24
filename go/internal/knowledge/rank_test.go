package knowledge

import (
	"math"
	"reflect"
	"testing"
)

// fixtureLesson builds the corpus rows the CPython fixture run used
// (scratchpad recall-fixture generator, 2026-08-22): _tfidf_rank_scored
// over five TieredLessons, scores printed via repr().
func fixtureLesson(id, text string, cites []any) TieredLesson {
	return TieredLesson{
		LessonID: id, TaskType: "agenda", Outcome: "done", Lesson: text,
		SourceGoal: "g", Confidence: 0.8, Tier: TierMedium, Score: 1.0,
		LastReinforced: "2026-08-01", EvidenceSources: cites,
	}
}

func rankFixtureCorpus() []TieredLesson {
	return []TieredLesson{
		fixtureLesson("l1", "Use the subprocess adapter with explicit flags when the CLI backend hangs on stdin", nil),
		fixtureLesson("l2", "Verify the resolved workspace path before any write to the memory store", []any{"run:abc"}),
		fixtureLesson("l3", "Deploy scripts must check the git worktree state before landing to main", nil),
		fixtureLesson("l4", "", nil),
		fixtureLesson("l5", "Use the subprocess adapter with explicit flags when the CLI backend hangs on stdin", []any{"run:xyz"}),
	}
}

func TestTokenizeMatchesCPython(t *testing.T) {
	cases := []struct {
		in   string
		want []string
	}{
		{"workspace write path verification before memory store writes",
			[]string{"workspace", "write", "path", "verification", "before", "memory", "store", "writes"}},
		{"subprocess adapter CLI backend stdin",
			[]string{"subprocess", "adapter", "cli", "backend", "stdin"}},
		{"the and of to a is", nil},
		{"", nil},
		// str.lower() EXPANDS U+0130 to i + U+0307 and Go's ToLower folds
		// it to a single 'i'. The combining mark is not [a-z0-9], so
		// CPython SPLITS the word and Go does not. The whole corpus above
		// is ASCII, where the two agree by construction — a differential
		// whose fixtures cannot separate reports agreement and tests
		// nothing (adversarial mission-r7 MEDIUM).
		{"DIFFİCULT case", []string{"diffi", "cult", "case"}},
		// Non-ASCII letters are stripped outright by the [^a-z0-9]+
		// substitution in BOTH runtimes — measured, not assumed: CPython
		// _tokenize("ΣΟΦΟΣ answers") is ["answers"]. Kept as the control
		// for the case above: U+0130 is special because it expands into
		// an ASCII letter plus a mark, not because it is non-ASCII.
		{"ΣΟΦΟΣ answers", []string{"answers"}},
		// Hyphen split, apostrophe split, len<=2 drop ("re", "x9", "ab"),
		// stopword drop, and "before" NOT being a stopword.
		{"Re-Verify: the workspace's PATH (before) memory-store writes!! x9 ab abc",
			[]string{"verify", "workspace", "path", "before", "memory", "store", "writes", "abc"}},
	}
	for _, c := range cases {
		if got := Tokenize(c.in); !reflect.DeepEqual(got, c.want) {
			t.Errorf("Tokenize(%q) = %v, want %v (CPython _tokenize)", c.in, got, c.want)
		}
	}
}

// TestTFIDFRankScoredFrozenSnapshot is a FROZEN SNAPSHOT, and the name
// now says so. It carried "MatchesCPython" while never running python3,
// over an all-ASCII corpus that could not separate on the tokenizer fork
// r7 found — a snapshot wearing a differential's name (adversarial
// mission-r7 MEDIUM). r7_diff_test.go's ...Live is the differential; this
// stays because a cheap in-process pin on the original five-lesson corpus
// still catches a formula change without shelling out.
//
// The tolerance absorbs summation-order differences (Go map iteration vs
// dict order) — anything past 1e-9 is a formula divergence, not noise.
func TestTFIDFRankScoredFrozenSnapshot(t *testing.T) {
	type want struct {
		id    string
		score float64
	}
	cases := []struct {
		query string
		want  []want
	}{
		{"workspace write path verification before memory store writes", []want{
			{"l2", 0.5989241446739629},
			{"l3", 0.05498968167562329},
			{"l1", 0.0}, {"l4", 0.0}, {"l5", 0.0}, // tie keeps input order
		}},
		{"subprocess adapter CLI backend stdin", []want{
			// l5 is the cited twin of l1: same cosine, no 0.90 penalty —
			// the Phase 60 tie-break, pinned numerically.
			{"l5", 0.6802534008167289},
			{"l1", 0.6122280607350561},
			{"l2", 0.0}, {"l3", 0.0}, {"l4", 0.0},
		}},
	}
	for _, c := range cases {
		got := TFIDFRankScored(c.query, rankFixtureCorpus(), -1)
		if len(got) != len(c.want) {
			t.Fatalf("query %q: got %d results, want %d", c.query, len(got), len(c.want))
		}
		for i, w := range c.want {
			if got[i].Lesson.LessonID != w.id {
				t.Errorf("query %q pos %d: got %s (%.6f), want %s",
					c.query, i, got[i].Lesson.LessonID, got[i].Score, w.id)
			}
			if math.Abs(got[i].Score-w.score) > 1e-9 {
				t.Errorf("query %q %s: score %v, want %v (CPython)",
					c.query, w.id, got[i].Score, w.score)
			}
		}
	}
}

// TestTFIDFNoSignalIgnoresTopK pins the deliberate parity quirk: a
// query with no signal tokens returns ALL lessons in input order at
// score 0.0 — Python's no-signal path returns before the top_k slice,
// and only the caller (QueryLessonsScored) bounds the result.
func TestTFIDFNoSignalIgnoresTopK(t *testing.T) {
	for _, q := range []string{"the and of to a is", ""} {
		got := TFIDFRankScored(q, rankFixtureCorpus(), 2)
		if len(got) != 5 {
			t.Fatalf("no-signal query %q: got %d results, want all 5 (topK ignored)", q, len(got))
		}
		for i, id := range []string{"l1", "l2", "l3", "l4", "l5"} {
			if got[i].Lesson.LessonID != id || got[i].Score != 0.0 {
				t.Errorf("no-signal pos %d: got %s/%v, want %s/0.0",
					i, got[i].Lesson.LessonID, got[i].Score, id)
			}
		}
	}
}

func TestTFIDFTopKBounds(t *testing.T) {
	got := TFIDFRankScored("subprocess adapter CLI backend stdin", rankFixtureCorpus(), 2)
	if len(got) != 2 || got[0].Lesson.LessonID != "l5" || got[1].Lesson.LessonID != "l1" {
		t.Fatalf("topK=2 got %+v", got)
	}
}
