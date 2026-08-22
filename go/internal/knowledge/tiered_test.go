package knowledge

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func writeTierFile(t *testing.T, ws, tier string, lines ...string) {
	t.Helper()
	dir := filepath.Join(ws, "memory", tier)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := ""
	for _, l := range lines {
		body += l + "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, "lessons.jsonl"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func lessonRow(id, text string, score float64, daysAgo int, extra string) string {
	when := time.Now().UTC().AddDate(0, 0, -daysAgo).Format("2006-01-02")
	row := fmt.Sprintf(`{"lesson_id":%q,"task_type":"agenda","outcome":"done","lesson":%q,"source_goal":"g","confidence":0.8,"score":%g,"last_reinforced":%q`,
		id, text, score, when)
	if extra != "" {
		row += "," + extra
	}
	return row + "}"
}

// TestDecayScoreMatchesCPython pins the read-time decay derivation
// against CPython decay_score (0.85 ** days through libm pow).
func TestDecayScoreMatchesCPython(t *testing.T) {
	cases := []struct {
		days int
		want float64
	}{
		{0, 1.0}, {1, 0.85}, {3, 0.6141249999999999},
		{7, 0.3205770882812499}, {30, 0.0076307595947894815},
	}
	for _, c := range cases {
		if got := DecayScore(1.0, c.days); math.Abs(got-c.want) > 1e-15 {
			t.Errorf("DecayScore(1.0, %d) = %v, want %v", c.days, got, c.want)
		}
	}
	if got := DecayScore(0.73, 12); math.Abs(got-0.10383648270940561) > 1e-15 {
		t.Errorf("DecayScore(0.73, 12) = %v, want CPython 0.10383648270940561", got)
	}
}

// TestLoadDecaysMediumOnly: decay is a read-time derivation on MEDIUM;
// LONG is promoted-permanent and never decays. Raw skips the math.
func TestLoadDecaysMediumOnly(t *testing.T) {
	ws := t.TempDir()
	writeTierFile(t, ws, TierMedium, lessonRow("m1", "medium lesson text here", 1.0, 7, ""))
	writeTierFile(t, ws, TierLong, lessonRow("l1", "long lesson text here", 1.0, 7, ""))
	s := NewStore(ws)

	med, _, err := s.LoadTieredLessons(TierMedium, LoadOptions{Limit: -1})
	if err != nil || len(med) != 1 {
		t.Fatalf("medium load: %v %d", err, len(med))
	}
	want := DecayScore(1.0, 7)
	if math.Abs(med[0].Score-want) > 1e-12 {
		t.Fatalf("medium score %v, want decayed %v", med[0].Score, want)
	}
	long, _, _ := s.LoadTieredLessons(TierLong, LoadOptions{Limit: -1})
	if len(long) != 1 || long[0].Score != 1.0 {
		t.Fatalf("long tier decayed: %+v", long)
	}
	rawMed, _, _ := s.LoadTieredLessons(TierMedium, LoadOptions{Limit: -1, Raw: true})
	if len(rawMed) != 1 || rawMed[0].Score != 1.0 {
		t.Fatalf("raw load applied decay: %+v", rawMed)
	}
}

// TestLoadMinScoreAppliesAfterDecayAndNotOnRaw mirrors Python's exact
// ordering: min_score compares the DECAYED score, and raw loads skip
// the min_score check entirely (it sits behind `not raw`).
func TestLoadMinScoreAppliesAfterDecayAndNotOnRaw(t *testing.T) {
	ws := t.TempDir()
	writeTierFile(t, ws, TierMedium, lessonRow("m1", "fades below threshold", 1.0, 7, ""))
	s := NewStore(ws)
	got, _, _ := s.LoadTieredLessons(TierMedium, LoadOptions{Limit: -1, MinScore: 0.5})
	if len(got) != 0 {
		t.Fatalf("decayed 0.32 survived min_score 0.5: %+v", got)
	}
	raw, _, _ := s.LoadTieredLessons(TierMedium, LoadOptions{Limit: -1, MinScore: 0.5, Raw: true})
	if len(raw) != 1 {
		t.Fatalf("raw load applied min_score: %+v", raw)
	}
}

// TestLoadPerRowGuard: a malformed, byte-torn, or type-drifted row
// skips THAT row through the skipped count — never the tier. Numeric
// strings coerce like Python float()/int(); a fractional int-string
// and a non-numeric score fail their row exactly as int("3.7") /
// float("high") raise inside Python's per-row guard.
func TestLoadPerRowGuard(t *testing.T) {
	ws := t.TempDir()
	today := time.Now().UTC().Format("2006-01-02")
	writeTierFile(t, ws, TierMedium,
		lessonRow("ok1", "healthy row one", 0.9, 0, ""),
		`{not json`,
		"{\"lesson_id\":\"torn\",\"lesson\":\"x\xffy\",\"score\":1.0}",
		`{"lesson_id":"badscore","task_type":"agenda","outcome":"done","lesson":"score is prose","score":"high","last_reinforced":"`+today+`"}`,
		`{"lesson_id":"strnum","task_type":"agenda","outcome":"done","lesson":"numeric string score","score":"0.5","confidence":"0.9","last_reinforced":"`+today+`"}`,
		`{"lesson_id":"fracint","task_type":"agenda","outcome":"done","lesson":"fractional int string","score":0.9,"times_applied":"3.7","last_reinforced":"`+today+`"}`,
		`{"lesson_id":"numlesson","task_type":"agenda","outcome":"done","lesson":42,"score":0.9,"last_reinforced":"`+today+`"}`,
		lessonRow("ok2", "healthy row two", 0.8, 0, `"times_applied":"4"`),
	)
	s := NewStore(ws)
	got, skipped, err := s.LoadTieredLessons(TierMedium, LoadOptions{Limit: -1})
	if err != nil {
		t.Fatal(err)
	}
	ids := map[string]TieredLesson{}
	for _, l := range got {
		ids[l.LessonID] = l
	}
	for _, want := range []string{"ok1", "ok2", "strnum"} {
		if _, ok := ids[want]; !ok {
			t.Errorf("row %s missing from load: %v", want, ids)
		}
	}
	for _, bad := range []string{"badscore", "fracint", "numlesson", "torn"} {
		if _, ok := ids[bad]; ok {
			t.Errorf("broken row %s survived the guard", bad)
		}
	}
	if skipped != 5 { // {not json + torn + badscore + fracint + numlesson
		t.Errorf("skipped = %d, want 5 (short read must be distinguishable from short store)", skipped)
	}
	if ids["strnum"].Score != 0.5 || ids["strnum"].Confidence != 0.9 {
		t.Errorf("numeric-string coercion: %+v", ids["strnum"])
	}
	if ids["ok2"].TimesApplied != 4 {
		t.Errorf("int-string coercion: %+v", ids["ok2"])
	}
}

func TestLoadFiltersSortAndLimit(t *testing.T) {
	ws := t.TempDir()
	writeTierFile(t, ws, TierLong,
		lessonRow("a", "alpha", 0.3, 0, `"lesson_type":"execution"`),
		lessonRow("b", "beta", 0.9, 0, `"lesson_type":"planning"`),
		lessonRow("c", "gamma", 0.6, 0, `"task_type":"x"`),
	)
	s := NewStore(ws)
	all, _, _ := s.LoadTieredLessons(TierLong, LoadOptions{Limit: -1})
	if len(all) != 3 || all[0].LessonID != "b" || all[1].LessonID != "c" || all[2].LessonID != "a" {
		t.Fatalf("sort desc broken: %+v", all)
	}
	// lessonRow stamps task_type agenda; row c overrides via extra? It
	// can't — the first key wins is NOT a JSON guarantee; c carries both
	// and Go's Unmarshal keeps the LAST duplicate key, matching Python
	// json.loads. So c.task_type == "x".
	typed, _, _ := s.LoadTieredLessons(TierLong, LoadOptions{Limit: -1, TaskType: "agenda"})
	if len(typed) != 2 {
		t.Fatalf("task_type filter: %+v", typed)
	}
	lt, _, _ := s.LoadTieredLessons(TierLong, LoadOptions{Limit: -1, LessonType: "planning"})
	if len(lt) != 1 || lt[0].LessonID != "b" {
		t.Fatalf("lesson_type filter: %+v", lt)
	}
	lim, _, _ := s.LoadTieredLessons(TierLong, LoadOptions{Limit: 1})
	if len(lim) != 1 || lim[0].LessonID != "b" {
		t.Fatalf("limit: %+v", lim)
	}
	missing, skipped, err := s.LoadTieredLessons(TierShort, LoadOptions{Limit: -1})
	if missing != nil || skipped != 0 || err != nil {
		t.Fatalf("missing tier should be the normal fresh state: %v %d %v", missing, skipped, err)
	}
}

// TestLoadOptionsZeroValueMeansUnlimited: the zero value must degrade
// to "everything", never to a silent empty read indistinguishable from
// an empty store (adversarial recall r1 2026-08-22, Skeptic HIGH — an
// earlier draft's Limit<0-for-unlimited made LoadOptions{} return
// results[:0] with skipped==0 and err==nil).
func TestLoadOptionsZeroValueMeansUnlimited(t *testing.T) {
	ws := t.TempDir()
	writeTierFile(t, ws, TierLong,
		lessonRow("z1", "first", 0.9, 0, ""),
		lessonRow("z2", "second", 0.8, 0, ""),
	)
	got, skipped, err := NewStore(ws).LoadTieredLessons(TierLong, LoadOptions{})
	if err != nil || skipped != 0 || len(got) != 2 {
		t.Fatalf("zero-value LoadOptions must load the full store: %d rows, skipped %d, err %v",
			len(got), skipped, err)
	}
}

// TestNonFiniteScoreFailsTheRow: strconv.ParseFloat mints NaN/Inf from
// the strings "NaN"/"Infinity", and a NaN score survives EVERY
// MinScore filter (NaN < x is always false) uncounted. Go refuses the
// row — deliberate stricter-than-Python refusal, named in PORT.md.
func TestNonFiniteScoreFailsTheRow(t *testing.T) {
	ws := t.TempDir()
	today := time.Now().UTC().Format("2006-01-02")
	writeTierFile(t, ws, TierLong,
		`{"lesson_id":"nan","task_type":"agenda","outcome":"done","lesson":"nan score","score":"NaN","last_reinforced":"`+today+`"}`,
		`{"lesson_id":"inf","task_type":"agenda","outcome":"done","lesson":"inf score","score":"-Infinity","last_reinforced":"`+today+`"}`,
		`{"lesson_id":"numnan","task_type":"agenda","outcome":"done","lesson":"nan confidence","score":0.9,"confidence":"Inf","last_reinforced":"`+today+`"}`,
		lessonRow("fine", "healthy neighbour", 0.8, 0, ""),
	)
	got, skipped, err := NewStore(ws).LoadTieredLessons(TierLong, LoadOptions{MinScore: 0.5})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].LessonID != "fine" {
		t.Fatalf("non-finite rows survived: %+v", got)
	}
	if skipped != 3 {
		t.Fatalf("skipped = %d, want 3 (refusals must be counted)", skipped)
	}
}

// TestEvidenceSourcesTypeDriftKeepsCitedness: a drifted non-list
// truthy evidence_sources value must stay CITED (no 0.90 penalty),
// matching Python's bool(evidence_sources) truthiness on the duck-typed
// row — the shape-only assertion silently flipped ranking (adversarial
// recall r1, Skeptic + Expert QA independently).
func TestEvidenceSourcesTypeDriftKeepsCitedness(t *testing.T) {
	ws := t.TempDir()
	text := "identical lesson text for the citation tie"
	writeTierFile(t, ws, TierLong,
		lessonRow("drift", text, 1.0, 0, `"evidence_sources":"run:abc"`),
		lessonRow("uncited", text, 1.0, 0, ""),
	)
	got, _, _ := NewStore(ws).QueryLessonsScored("identical lesson text citation tie", 10, "agenda")
	if len(got) != 2 {
		t.Fatalf("want both rows: %+v", got)
	}
	if got[0].Lesson.LessonID != "drift" || got[0].Score <= got[1].Score {
		t.Fatalf("drifted-but-truthy citation lost its no-penalty rank: %+v", got)
	}
}

// TestQueryLessonsScoredFilters: provisional, quarantined (minted_from
// "prompt"), and contested rows leave every injection surface; the pool
// spans LONG + MEDIUM. Python-truthiness pin: provisional:"false" is a
// TRUTHY string and the row stays filtered.
func TestQueryLessonsScoredFilters(t *testing.T) {
	ws := t.TempDir()
	writeTierFile(t, ws, TierMedium,
		lessonRow("keep-m", "deploy the caching layer to production", 1.0, 0, ""),
		lessonRow("prov", "deploy the caching layer to production", 1.0, 0, `"provisional":true`),
		lessonRow("prov-str", "deploy the caching layer to production", 1.0, 0, `"provisional":"false"`),
		lessonRow("quar", "deploy the caching layer to production", 1.0, 0, `"minted_from":"prompt"`),
		lessonRow("cont", "deploy the caching layer to production", 1.0, 0, `"contested":{"reason":"x"}`),
	)
	writeTierFile(t, ws, TierLong,
		lessonRow("keep-l", "deploy the caching layer to production", 1.0, 0, ""),
	)
	s := NewStore(ws)
	got, _, errs := s.QueryLessonsScored("deploy caching layer production", 10, "agenda")
	if len(errs) != 0 {
		t.Fatalf("load errors: %v", errs)
	}
	if len(got) != 2 {
		t.Fatalf("want the 2 clean rows, got %d: %+v", len(got), got)
	}
	ids := map[string]bool{got[0].Lesson.LessonID: true, got[1].Lesson.LessonID: true}
	if !ids["keep-m"] || !ids["keep-l"] {
		t.Fatalf("wrong survivors: %v", ids)
	}
}

// TestQueryLessonsScoredUnreadableTierDegrades: an unreadable tier is
// reported through loadErrs and the other tier still serves — recall's
// degrade direction, without the silent swallow.
func TestQueryLessonsScoredUnreadableTierDegrades(t *testing.T) {
	ws := t.TempDir()
	writeTierFile(t, ws, TierMedium, lessonRow("m", "the one healthy tier", 1.0, 0, ""))
	// A DIRECTORY at the long tier's lessons.jsonl path forces a read
	// error that isn't IsNotExist.
	if err := os.MkdirAll(filepath.Join(ws, "memory", "long", "lessons.jsonl"), 0o755); err != nil {
		t.Fatal(err)
	}
	s := NewStore(ws)
	got, _, errs := s.QueryLessonsScored("healthy tier", 10, "agenda")
	if len(got) != 1 || got[0].Lesson.LessonID != "m" {
		t.Fatalf("healthy tier lost: %+v", got)
	}
	if len(errs) != 1 {
		t.Fatalf("unreadable tier not reported: %v", errs)
	}
}

// TestTruthyNumberParseErrorFailsOpen: a json.Number whose Float64
// errors (out-of-range exponent) reads as TRUTHY — the conservative
// direction for the provisional/contested gates (r2 Skeptic: the safe
// direction was asserted, never proven).
func TestTruthyNumberParseErrorFailsOpen(t *testing.T) {
	if !Truthy(json.Number("1e999999999")) {
		t.Fatal("unparseable number must fail open to truthy")
	}
	if Truthy(json.Number("0")) || !Truthy(json.Number("0.5")) {
		t.Fatal("plain number truthiness broken")
	}
}
