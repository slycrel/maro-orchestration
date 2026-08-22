package recall

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
)

func writeRunMeta(t *testing.T, ws, dirName string, meta map[string]any) {
	t.Helper()
	dir := filepath.Join(ws, "runs", dirName)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	raw, err := json.Marshal(meta)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "metadata.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func isoAgo(d time.Duration) string {
	return time.Now().UTC().Add(-d).Format("2006-01-02T15:04:05.000000+00:00")
}

func writeLessonRows(t *testing.T, ws, tier string, rows ...string) {
	t.Helper()
	dir := filepath.Join(ws, "memory", tier)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "lessons.jsonl"),
		[]byte(strings.Join(rows, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func row(id, taskType, outcome, lesson, extra string) string {
	r := fmt.Sprintf(`{"lesson_id":%q,"task_type":%q,"outcome":%q,"lesson":%q,"score":1.0,"last_reinforced":%q`,
		id, taskType, outcome, lesson, time.Now().UTC().Format("2006-01-02"))
	if extra != "" {
		r += "," + extra
	}
	return r + "}"
}

func TestFindPriorAttemptsMatchLanes(t *testing.T) {
	ws := t.TempDir()
	goal := "deploy the widget service to staging"
	writeRunMeta(t, ws, "h1-alpha", map[string]any{
		"handle_id": "h1", "started_at": isoAgo(2 * time.Hour),
		"prompt": "  Deploy the WIDGET service to staging  ", "status": "done",
	})
	writeRunMeta(t, ws, "h2-beta", map[string]any{
		"handle_id": "h2", "started_at": isoAgo(3 * time.Hour),
		// Same word set (punctuation only) → Jaccard 1.0 ≥ 0.9 near.
		"prompt": "deploy, the widget service; to staging!", "status": "stuck",
	})
	writeRunMeta(t, ws, "h3-gamma", map[string]any{
		"handle_id": "h3", "started_at": isoAgo(4 * time.Hour),
		"prompt": "completely unrelated polling work", "status": "done",
		"project": "widgets",
	})
	writeRunMeta(t, ws, "h4-delta", map[string]any{
		"handle_id": "h4", "started_at": isoAgo(5 * time.Hour),
		"prompt": "completely unrelated polling work", "status": "done",
	})

	got, err := FindPriorAttempts(ws, goal, 24.0, "widgets", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("want 3 matches (exact, near, project), got %d: %+v", len(got), got)
	}
	// Newest-first ordering; h4 (no lane) absent.
	wantOrder := []struct{ id, match string }{
		{"h1", "exact"}, {"h2", "near"}, {"h3", "project"},
	}
	for i, w := range wantOrder {
		if got[i].HandleID != w.id || got[i].Match != w.match {
			t.Errorf("pos %d: got %s/%s, want %s/%s",
				i, got[i].HandleID, got[i].Match, w.id, w.match)
		}
	}
}

func TestFindPriorAttemptsWindowExcludeAndFallbacks(t *testing.T) {
	ws := t.TempDir()
	goal := "rebuild the index"
	writeRunMeta(t, ws, "old1-x", map[string]any{
		"handle_id": "old1", "started_at": isoAgo(30 * time.Hour),
		"prompt": goal, "status": "done",
	})
	writeRunMeta(t, ws, "me-x", map[string]any{
		"handle_id": "me", "started_at": isoAgo(time.Hour),
		"prompt": goal, "status": "done",
	})
	writeRunMeta(t, ws, "h5-x", map[string]any{
		// handle_id absent → derived from the dir-name prefix.
		"started_at": isoAgo(time.Hour),
		"prompt":     goal, "status": "stranded",
	})
	writeRunMeta(t, ws, "h6-x", map[string]any{
		"handle_id": "h6", "started_at": isoAgo(time.Hour),
		"prompt": goal, "status": "done", "goal_achieved": false,
		"stop_verdict": "thesis-refuted",
	})
	got, err := FindPriorAttempts(ws, goal, 24.0, "", "me")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("window + exclude: want 2, got %d: %+v", len(got), got)
	}
	byID := map[string]PriorAttempt{}
	for _, a := range got {
		byID[a.HandleID] = a
	}
	// §13b status-derived fallback: stranded + no stamp → external-interrupt.
	if a := byID["h5"]; a.StopVerdict != "external-interrupt" {
		t.Errorf("stranded run without stamp: verdict %q, want external-interrupt", a.StopVerdict)
	}
	// A stamped verdict is never overwritten; SF-2 tri-state survives.
	a6 := byID["h6"]
	if a6.StopVerdict != "thesis-refuted" || a6.GoalAchieved == nil || *a6.GoalAchieved {
		t.Errorf("stamped run mangled: %+v", a6)
	}
}

func TestRecallRendersLessonsWithReceipts(t *testing.T) {
	ws := t.TempDir()
	writeLessonRows(t, ws, "medium",
		row("r1", "agenda", "done", "always check the staging index before a widget deploy",
			`"times_reinforced":3,"sessions_validated":2`),
		row("r2", "agenda", "stuck", "widget deploys fail when the index rebuild races", ""),
	)
	rr := Recall(ws, "deploy the widget service and check the staging index rebuild", "")
	if !strings.Contains(rr.Lessons, "## Lessons from Prior Runs (weigh by their receipts)") {
		t.Fatalf("header missing:\n%s", rr.Lessons)
	}
	if !strings.Contains(rr.Lessons, "✓ always check the staging index before a widget deploy (reinforced 3x, 2 sessions)") {
		t.Errorf("receipted done line wrong:\n%s", rr.Lessons)
	}
	if !strings.Contains(rr.Lessons, "✗ widget deploys fail when the index rebuild races (observed once)") {
		t.Errorf("observed-once stuck line wrong:\n%s", rr.Lessons)
	}
	cited, _ := rr.Sources["lesson_ids_cited"].([]string)
	if len(cited) != 2 {
		t.Errorf("cited ids: %v", rr.Sources["lesson_ids_cited"])
	}
}

// TestLessonBudgetIsALineBreaker: the 1200-char bound drops WHOLE
// lines, never cuts mid-line, and a dropped line's id is NOT cited
// (cited-only-if-rendered — the chunk-6 contradiction-join fix; caps
// are breakers, not truncators, decree 2026-08-21).
func TestLessonBudgetIsALineBreaker(t *testing.T) {
	ws := t.TempDir()
	big := strings.Repeat("staging widget deploy lore ", 17) // ~460 chars each
	writeLessonRows(t, ws, "medium",
		row("b1", "agenda", "done", big+"one", ""),
		row("b2", "agenda", "done", big+"two", ""),
		row("b3", "agenda", "done", big+"three", ""),
	)
	rr := Recall(ws, "staging widget deploy lore", "")
	if rr.Lessons == "" {
		t.Fatal("no render")
	}
	if got := len([]rune(rr.Lessons)); got > budget.LessonInject.Limit {
		t.Fatalf("render exceeds budget: %d > %d", got, budget.LessonInject.Limit)
	}
	if strings.Contains(rr.Lessons, "truncated") {
		t.Fatalf("breaker turned truncator:\n%s", rr.Lessons)
	}
	rendered := strings.Count(rr.Lessons, "- ✓")
	cited, _ := rr.Sources["lesson_ids_cited"].([]string)
	if rendered != 2 || len(cited) != 2 {
		t.Fatalf("want 2 whole lines rendered and 2 cited, got %d rendered / %v cited",
			rendered, cited)
	}
}

// TestUntypedTopUpDedups: agenda matches lead; untyped tiered writers
// top up to 3 without re-adding the agenda lesson (dedup by lesson_id).
func TestUntypedTopUpDedups(t *testing.T) {
	ws := t.TempDir()
	writeLessonRows(t, ws, "medium",
		row("ag1", "agenda", "done", "orchestrate the fleet rollout carefully", ""),
		row("un1", "general", "done", "fleet rollout needs the canary first", ""),
		row("un2", "verify", "done", "rollout verification wants two canaries", ""),
	)
	rr := Recall(ws, "fleet rollout canary orchestration", "")
	cited, _ := rr.Sources["lesson_ids_cited"].([]string)
	if len(cited) != 3 {
		t.Fatalf("top-up missed: cited %v\n%s", cited, rr.Lessons)
	}
	seen := map[string]int{}
	for _, id := range cited {
		seen[id]++
		if seen[id] > 1 {
			t.Fatalf("dedup failed, %s cited twice: %v", id, cited)
		}
	}
}

func TestRecallDegradesToKnowsNothing(t *testing.T) {
	rr := Recall(filepath.Join(t.TempDir(), "does-not-exist"), "any goal at all", "")
	if rr.Lessons != "" || len(rr.PriorAttempts) != 0 {
		t.Fatalf("empty workspace must recall nothing: %+v", rr)
	}
	if rr.ContextBlock() != "" || len(rr.DecomposeExtras()) != 0 {
		t.Fatalf("blocks must be empty on a fresh workspace")
	}
}

func TestContextBlockSummarizesAttempts(t *testing.T) {
	tr, fa := true, false
	r := Result{PriorAttempts: []PriorAttempt{
		{Status: "stuck", When: "2026-08-22T10:00:00", GoalAchieved: &fa},
		{Status: "done", When: "2026-08-22T09:00:00", GoalAchieved: &tr},
		{Status: "stranded", When: "2026-08-22T08:00:00", StopVerdict: "external-interrupt"},
	}}
	got := r.ContextBlock()
	for _, want := range []string{
		"== Recall (what the system already knows) ==",
		"3 runs — 1 done, 1 stranded, 1 stuck",
		"goal verdicts: 1 achieved, 1 NOT achieved, rest unjudged",
		"1 externally interrupted (not goal evidence)",
		"Newest: 2026-08-22T10:00:00 (stuck)",
		"Do not repeat an approach that already failed",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("context block missing %q:\n%s", want, got)
		}
	}
}

// TestDecomposeExtrasOrder: ancestry-channel context precedes the
// lessons block, matching Python's extras order (planner.py:962 —
// skills, ancestry, lessons, cost).
func TestDecomposeExtrasOrder(t *testing.T) {
	r := Result{
		Lessons:       "## Lessons from Prior Runs (weigh by their receipts)\n- ✓ x (observed once)",
		PriorAttempts: []PriorAttempt{{Status: "stuck", When: "2026-08-22T10:00:00"}},
	}
	extras := r.DecomposeExtras()
	if len(extras) != 2 {
		t.Fatalf("extras: %v", extras)
	}
	if !strings.HasPrefix(extras[0], "== Recall") || !strings.HasPrefix(extras[1], "## Lessons") {
		t.Fatalf("order wrong: %q then %q", extras[0][:20], extras[1][:20])
	}
}

func TestTextSimilarityMatchesPython(t *testing.T) {
	// memory_ledger._text_similarity is word-set Jaccard over
	// lowercased [a-z0-9 ] text.
	if got := textSimilarity("Deploy the widget!", "deploy the widget"); got != 1.0 {
		t.Errorf("punctuation-only difference: %v", got)
	}
	if got := textSimilarity("alpha beta gamma delta", "alpha beta gamma epsilon"); got != 0.6 {
		t.Errorf("3/5 overlap: %v", got)
	}
	if got := textSimilarity("", "anything"); got != 0.0 {
		t.Errorf("empty side: %v", got)
	}
}
