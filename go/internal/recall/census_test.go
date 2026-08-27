package recall

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
)

// This file exists because of a mutation census, not because of a review.
//
// The build lane that wrote `recall_diff_test.go` produced three mutants
// for three tests — one per test, which measures the tests it happened to
// write rather than the file (L9: derive must-detect mutations from the
// FILE, not the diff). A 51-mutant battery derived from `recall.go` itself
// then reported **29 caught, 22 survived**. Each test below closes one or
// more of those survivors, and every one was verified to FAIL against its
// own mutant before it was kept.
//
// TWO survivors are EQUIVALENT mutants and are recorded here rather than
// chased, because a mutant that cannot change an answer is a bad mutant,
// not a test gap (L8):
//
//   - `if len(dirs) > MetadataScanCap` → `>=`. At `len == cap` the slice
//     `dirs[:cap]` is a no-op, so the two spellings agree at every length.
//   - `if len(wa) == 0 || len(wb) == 0` → `&&` in textSimilarity. With one
//     side empty, `inter` is 0 and `union` is the other side's size, so the
//     division already yields 0.0; with both empty the `union == 0` guard
//     returns 0.0 anyway.
//
// Those two were named BEFORE the battery was re-run, and they are exactly
// the two that survived it: 49 of 51 caught, the pair above surviving. A
// re-run whose survivors are the ones the analysis predicted is the only
// version of this result worth reporting — a survivor list that has to be
// explained afterwards is a list of gaps wearing an excuse.
//
// `sort.SliceStable` → `sort.Slice` is NOT one of them; it is closed by
// TestEqualTimestampsKeepScanOrder below, built to P11's construction rule.

// TestScanCapHoldsAtItsOwnBoundary closes four survivors at once: the cap
// comparison, the cap slice, the cap CONSTANT, and the mtime ordering that
// decides WHICH runs the cap keeps.
//
// The ordering is only observable through the cap. Below it every dir is
// read and the final sort re-orders the results anyway, which is why a
// battery mutating `mtime.After` to `mtime.Before` survived every test in
// the package: nothing had ever built a workspace larger than the bound
// (L23 — a limit with no case at its OWN boundary is a limit nothing pins).
//
// The expected count is the LITERAL 200, not `MetadataScanCap`. Spelling
// the expectation with the thing under test is not an assertion (P12): the
// battery's "the cap constant moves" mutant changes 200 to 199, and a test
// that reads the constant moves with it and stays green.
func TestScanCapHoldsAtItsOwnBoundary(t *testing.T) {
	const wantCap = 200 // recall.py _METADATA_SCAN_CAP; NOT MetadataScanCap
	ws := t.TempDir()
	goal := "ship the release notes"

	// One more dir than the cap, every one a matching, in-window run.
	// Dir i is written with mtime now-i minutes, so dir 0 is newest and
	// dir 200 is the single one the cap must drop.
	now := time.Now()
	for i := 0; i <= wantCap; i++ {
		name := "h" + itoa3(i) + "-run"
		writeRunMeta(t, ws, name, map[string]any{
			"handle_id":  "h" + itoa3(i),
			"prompt":     goal,
			"started_at": isoAgo(time.Duration(i) * time.Second),
			"status":     "done",
		})
		mt := now.Add(-time.Duration(i) * time.Minute)
		if err := os.Chtimes(filepath.Join(ws, "runs", name), mt, mt); err != nil {
			t.Fatal(err)
		}
	}

	attempts, skipped, err := FindPriorAttempts(ws, goal, 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	if skipped != 0 {
		t.Fatalf("skipped %d readable run dirs", skipped)
	}
	if len(attempts) != wantCap {
		t.Fatalf("read %d run dirs, want exactly %d — the cap is the whole "+
			"point of this fixture", len(attempts), wantCap)
	}
	// The dropped one must be the OLDEST by mtime, not the newest. A
	// reversed pre-cap sort keeps exactly the wrong 200.
	got := map[string]bool{}
	for _, a := range attempts {
		got[a.HandleID] = true
	}
	if !got["h000"] {
		t.Error("the NEWEST run was dropped by the cap — the pre-cap sort " +
			"is ordering oldest-first")
	}
	if got["h"+itoa3(wantCap)] {
		t.Errorf("the oldest run (h%s) survived the cap while %d dirs were "+
			"read; the cap kept the wrong end", itoa3(wantCap), len(attempts))
	}
}

func itoa3(n int) string {
	digits := []byte{byte('0' + n/100%10), byte('0' + n/10%10), byte('0' + n%10)}
	return string(digits)
}

// TestNearMatchThresholdIsInclusiveAtItsOwnValue closes two survivors: the
// `>=` comparison and the 0.9 constant. Both need a pair sitting EXACTLY on
// the threshold, which no fixture had.
//
// The arithmetic is stated rather than measured: the goal is nine distinct
// words, the near prompt is those nine plus one, so the word sets intersect
// in 9 and union to 10 — Jaccard 9/10, the threshold exactly. The far
// prompt adds a second extra word: 9/11 = 0.818, below. Neither prompt can
// take the EXACT lane, because both differ from the goal after normalize.
func TestNearMatchThresholdIsInclusiveAtItsOwnValue(t *testing.T) {
	const wantThreshold = 0.9 // recall.py _NEAR_MATCH_THRESHOLD; NOT the const
	ws := t.TempDir()
	goal := "alpha bravo charlie delta echo foxtrot golf hotel india"
	onThreshold := goal + " juliett"         // 9/10 = 0.9 exactly
	belowThreshold := goal + " juliett kilo" // 9/11 = 0.818...

	if got := textSimilarity(onThreshold, goal); got != wantThreshold {
		t.Fatalf("the fixture's premise is wrong: similarity is %v, not %v — "+
			"this pair does not sit on the threshold at all", got, wantThreshold)
	}
	if got := textSimilarity(belowThreshold, goal); got >= wantThreshold {
		t.Fatalf("the below-threshold pair scores %v, not below %v", got, wantThreshold)
	}

	writeRunMeta(t, ws, "hon-run", map[string]any{
		"handle_id": "hon", "prompt": onThreshold,
		"started_at": isoAgo(time.Minute), "status": "done",
	})
	writeRunMeta(t, ws, "hbelow-run", map[string]any{
		"handle_id": "hbelow", "prompt": belowThreshold,
		"started_at": isoAgo(2 * time.Minute), "status": "done",
	})

	attempts, _, err := FindPriorAttempts(ws, goal, 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != 1 {
		var names []string
		for _, a := range attempts {
			names = append(names, a.HandleID+"/"+a.Match)
		}
		t.Fatalf("want exactly the on-threshold row, got %v — a strict `>` "+
			"drops it and a lower constant admits the other one", names)
	}
	if attempts[0].HandleID != "hon" || attempts[0].Match != "near" {
		t.Fatalf("matched %q as %q, want hon as near",
			attempts[0].HandleID, attempts[0].Match)
	}
}

// TestProjectLaneNeedsACallerProject closes the survivor where the
// `project != ""` guard is dropped. With no project asked for and a run
// carrying no project field, both sides are "" and the lane matches
// EVERYTHING — every unrelated run in the workspace surfaces as a prior
// attempt at this goal.
func TestProjectLaneNeedsACallerProject(t *testing.T) {
	ws := t.TempDir()
	writeRunMeta(t, ws, "hx-run", map[string]any{
		"handle_id": "hx", "prompt": "something else entirely",
		"started_at": isoAgo(time.Minute), "status": "done",
	})
	attempts, _, err := FindPriorAttempts(ws, "unrelated goal", 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != 0 {
		t.Fatalf("a run with no project matched a caller with no project: %+v",
			attempts)
	}
	// The control: the lane DOES fire when both sides name the same project,
	// so the test above is not passing because the lane is dead.
	writeRunMeta(t, ws, "hy-run", map[string]any{
		"handle_id": "hy", "prompt": "something else entirely",
		"project": "atlas", "started_at": isoAgo(time.Minute), "status": "done",
	})
	attempts, _, err = FindPriorAttempts(ws, "unrelated goal", 24, "atlas", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != 1 || attempts[0].Match != "project" {
		t.Fatalf("the project lane did not fire for a real project: %+v", attempts)
	}
}

// TestVerdictTriStateAtItsThreeInputs closes the JSON-null survivor and
// pins the two neighbours it sits between. The three inputs are different
// questions and the port answers them differently (SF-2):
//
//	absent         -> nil    (unjudged: done is not achieved)
//	JSON null      -> nil    (present but explicitly no verdict)
//	non-bool value -> &false (a corrupt verdict surfaces as a FAILURE)
//
// A mutant dropping the `v != nil` half turns the middle row into &false,
// which reports an unjudged run to the director as one that failed.
func TestVerdictTriStateAtItsThreeInputs(t *testing.T) {
	ws := t.TempDir()
	goal := "same goal"
	rows := []struct {
		handle string
		meta   map[string]any
	}{
		{"habsent", map[string]any{}},
		{"hnull", map[string]any{"goal_achieved": nil}},
		{"hstring", map[string]any{"goal_achieved": "false"}},
		{"htrue", map[string]any{"goal_achieved": true}},
	}
	for i, r := range rows {
		m := map[string]any{
			"handle_id": r.handle, "prompt": goal,
			"started_at": isoAgo(time.Duration(i+1) * time.Minute),
			"status":     "done",
		}
		for k, v := range r.meta {
			m[k] = v
		}
		writeRunMeta(t, ws, r.handle+"-run", m)
	}
	attempts, _, err := FindPriorAttempts(ws, goal, 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	by := map[string]PriorAttempt{}
	for _, a := range attempts {
		by[a.HandleID] = a
	}
	if len(by) != 4 {
		t.Fatalf("read %d rows, want 4: %+v", len(by), attempts)
	}
	check := func(handle string, want *bool) {
		t.Helper()
		got := by[handle].GoalAchieved
		switch {
		case want == nil && got != nil:
			t.Errorf("%s: got %v, want unjudged (nil)", handle, *got)
		case want != nil && got == nil:
			t.Errorf("%s: got unjudged, want %v", handle, *want)
		case want != nil && got != nil && *want != *got:
			t.Errorf("%s: got %v, want %v", handle, *got, *want)
		}
	}
	yes, no := true, false
	check("habsent", nil)
	check("hnull", nil)
	check("hstring", &no)
	check("htrue", &yes)
}

// TestStampedVerdictSurvivesAnInterruptStatus closes the survivor where
// the `sv == ""` guard is dropped from the status-derived fallback. The
// fallback exists for runs that predate break-site stamping; a run that
// WAS stamped must keep its own verdict, or every stranded run reads as an
// external interrupt regardless of what actually stopped it.
func TestStampedVerdictSurvivesAnInterruptStatus(t *testing.T) {
	ws := t.TempDir()
	goal := "same goal"
	writeRunMeta(t, ws, "hstamped-run", map[string]any{
		"handle_id": "hstamped", "prompt": goal, "status": "stranded",
		"stop_verdict": "goal-met", "started_at": isoAgo(time.Minute),
	})
	// The neighbour that makes the fallback observable at all, and the one
	// status the battery could delete from InterruptStatuses unnoticed.
	writeRunMeta(t, ws, "hbusy-run", map[string]any{
		"handle_id": "hbusy", "prompt": goal, "status": "refused_busy",
		"started_at": isoAgo(2 * time.Minute),
	})
	attempts, _, err := FindPriorAttempts(ws, goal, 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	by := map[string]string{}
	for _, a := range attempts {
		by[a.HandleID] = a.StopVerdict
	}
	if by["hstamped"] != "goal-met" {
		t.Errorf("a stamped stop_verdict was overwritten by the status "+
			"fallback: got %q, want %q", by["hstamped"], "goal-met")
	}
	if by["hbusy"] != "external-interrupt" {
		t.Errorf("refused_busy did not derive an interrupt verdict: got %q",
			by["hbusy"])
	}
}

// TestAbsentStatusReadsAsUnknown closes the survivor that removes the
// default. An empty status is not a status: it renders as "N " in the
// context block's breakdown and sorts to the front of it.
func TestAbsentStatusReadsAsUnknown(t *testing.T) {
	ws := t.TempDir()
	writeRunMeta(t, ws, "hns-run", map[string]any{
		"handle_id": "hns", "prompt": "g", "started_at": isoAgo(time.Minute),
	})
	attempts, _, err := FindPriorAttempts(ws, "g", 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != 1 || attempts[0].Status != "unknown" {
		t.Fatalf("absent status read as %+v, want status \"unknown\"", attempts)
	}
}

// TestEmptyExcludeIDExcludesNothing closes the survivor that drops the
// `excludeHandleID != ""` guard.
//
// It looks unreachable — the fallback derives a handle id from the dir
// name, which is never empty — until the dir name STARTS with a dash.
// `strings.SplitN("-abc", "-", 2)[0]` is "", so that row's handle id is
// empty and a dropped guard silently excludes it from every scan that
// passes no exclusion, which is every scan the plain Recall entry point
// makes.
func TestEmptyExcludeIDExcludesNothing(t *testing.T) {
	ws := t.TempDir()
	writeRunMeta(t, ws, "-abc", map[string]any{
		"prompt": "g", "started_at": isoAgo(time.Minute), "status": "done",
	})
	attempts, _, err := FindPriorAttempts(ws, "g", 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != 1 {
		t.Fatalf("a run whose derived handle id is empty was dropped by an "+
			"empty exclusion: %+v", attempts)
	}
	if attempts[0].HandleID != "" {
		t.Fatalf("premise wrong: derived handle id is %q, not empty — this "+
			"fixture no longer reaches the case it was written for",
			attempts[0].HandleID)
	}
}

// TestEqualTimestampsKeepScanOrder closes the sort-stability survivor.
//
// Built to P11's construction rule, which exists because the obvious
// version of this test cannot fail: at least 13 elements, at least two
// distinct key values, INTERLEAVED, and the assertion reads the order
// WITHIN one key group. Asserting the group boundaries only tests the
// comparator, which is not what stability means.
func TestEqualTimestampsKeepScanOrder(t *testing.T) {
	ws := t.TempDir()
	goal := "same goal"
	newer := isoAgo(time.Minute)
	older := isoAgo(2 * time.Minute)
	const n = 16
	now := time.Now()
	var wantNewer, wantOlder []string
	for i := 0; i < n; i++ {
		name := "h" + itoa3(i) + "-run"
		when := newer
		if i%2 == 1 {
			when = older
		}
		writeRunMeta(t, ws, name, map[string]any{
			"handle_id": "h" + itoa3(i), "prompt": goal,
			"started_at": when, "status": "done",
		})
		// mtime strictly decreasing, so the pre-sort scan order is exactly
		// h000, h001, ... and the fixture knows what stability must preserve.
		mt := now.Add(-time.Duration(i) * time.Minute)
		if err := os.Chtimes(filepath.Join(ws, "runs", name), mt, mt); err != nil {
			t.Fatal(err)
		}
		if i%2 == 0 {
			wantNewer = append(wantNewer, "h"+itoa3(i))
		} else {
			wantOlder = append(wantOlder, "h"+itoa3(i))
		}
	}
	attempts, _, err := FindPriorAttempts(ws, goal, 24, "", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(attempts) != n {
		t.Fatalf("read %d rows, want %d", len(attempts), n)
	}
	var gotNewer, gotOlder []string
	for _, a := range attempts {
		switch a.When {
		case newer:
			gotNewer = append(gotNewer, a.HandleID)
		case older:
			gotOlder = append(gotOlder, a.HandleID)
		default:
			t.Fatalf("unexpected timestamp %q", a.When)
		}
	}
	if strings.Join(gotNewer, ",") != strings.Join(wantNewer, ",") {
		t.Errorf("within the newer group the scan order was not preserved:\n"+
			" got %v\nwant %v", gotNewer, wantNewer)
	}
	if strings.Join(gotOlder, ",") != strings.Join(wantOlder, ",") {
		t.Errorf("within the older group the scan order was not preserved:\n"+
			" got %v\nwant %v", gotOlder, wantOlder)
	}
}

// TestParseISOShapes closes two survivors in the timestamp reader: the
// space-separated shape, and the assumption that a naive timestamp is UTC.
//
// The UTC half is only observable where the process's local zone is not
// UTC. That is a real limitation of this fixture and it is stated rather
// than skipped: on a UTC box the second case degenerates into a repeat of
// the first, and the mutant that reads naive timestamps in local time
// survives. The test says so out loud rather than reporting a pass it did
// not earn.
func TestParseISOShapes(t *testing.T) {
	_, offset := time.Now().Zone()
	if offset == 0 {
		t.Log("NOTE: this process runs in UTC, so the local-vs-UTC half of " +
			"this test cannot discriminate. The shape assertions still hold.")
	}
	want := time.Date(2026, 8, 22, 10, 30, 0, 0, time.UTC)
	for _, s := range []string{
		"2026-08-22 10:30:00", // the space-separated shape
		"2026-08-22T10:30:00", // naive, T-separated: assumed UTC
	} {
		got, err := parseISO(s)
		if err != nil {
			t.Fatalf("%q did not parse: %v", s, err)
		}
		if !got.Equal(want) {
			t.Errorf("%q parsed to %v, want %v (naive timestamps are UTC)",
				s, got, want)
		}
	}
	if _, err := parseISO("2026-08-22"); err != nil {
		t.Errorf("the date-only shape no longer parses: %v", err)
	}
	if _, err := parseISO("not a timestamp"); err == nil {
		t.Error("garbage parsed as a timestamp")
	}
}

// TestVerdictLineAppearsWithOneKindOfVerdict closes the survivor that
// turns the `||` into `&&`. Every attempt judged the same way is the
// COMMON case — a goal that has failed three times running — and it is
// exactly the case the `&&` mutant renders without any verdict line.
func TestVerdictLineAppearsWithOneKindOfVerdict(t *testing.T) {
	no := false
	r := Result{PriorAttempts: []PriorAttempt{
		{Status: "stuck", When: "2026-08-22T10:00:00", GoalAchieved: &no},
		{Status: "stuck", When: "2026-08-22T09:00:00", GoalAchieved: &no},
	}}
	got := r.ContextBlock()
	if !strings.Contains(got, "goal verdicts: 0 achieved, 2 NOT achieved") {
		t.Fatalf("two NOT-achieved verdicts rendered no verdict line:\n%s", got)
	}
}

// TestContextBlockReservesRoomForItsMarker closes the survivor that passes
// the full limit to Clip. budget.Clip appends its marker AFTER the cut, so
// Clip(text, limit) returns limit + ~45 runes — over the bound the caller
// is there to enforce. The 64-rune reservation is Python's
// as_context_block(max_chars - 64), not a Go embellishment.
func TestContextBlockReservesRoomForItsMarker(t *testing.T) {
	saved := budget.RecallContext.Limit
	defer func() { budget.RecallContext.Limit = saved }()
	const limit = 400
	budget.RecallContext.Limit = limit

	var attempts []PriorAttempt
	for i := 0; i < 40; i++ {
		attempts = append(attempts, PriorAttempt{
			Status: "status" + itoa3(i), When: "2026-08-22T10:00:00",
		})
	}
	got := (Result{PriorAttempts: attempts}).ContextBlock()
	if n := len([]rune(got)); n > limit {
		t.Fatalf("block ran %d runes against a %d-rune budget — the clip "+
			"marker was not reserved for", n, limit)
	}
	if !strings.Contains(got, "truncated") {
		t.Fatalf("the fixture did not reach the clip path at all; it "+
			"measures nothing:\n%s", got)
	}
}

// TestContextBlockSurvivesANegativeBudget closes the survivor that removes
// the floor. A negative limit reaches `r[:limit]`, and a negative slice
// bound is a PANIC — in the one seam whose whole contract is that a broken
// store never blocks a run.
func TestContextBlockSurvivesANegativeBudget(t *testing.T) {
	saved := budget.RecallContext.Limit
	defer func() { budget.RecallContext.Limit = saved }()
	budget.RecallContext.Limit = -5
	r := Result{PriorAttempts: []PriorAttempt{
		{Status: "stuck", When: "2026-08-22T10:00:00"},
	}}
	if got := r.ContextBlock(); got != "" {
		t.Fatalf("a negative budget returned %q, want the empty string", got)
	}
}

// TestLessonSelectionWindowIsThree closes the survivor that widens the
// window to four. Nothing had ever offered the renderer more than three
// matching lessons, so the bound was unobservable.
func TestLessonSelectionWindowIsThree(t *testing.T) {
	const wantWindow = 3 // recall.py's select-3; NOT read off the code
	ws := t.TempDir()
	goal := "deploy the service"
	var rows []string
	for i := 0; i < 6; i++ {
		rows = append(rows, row("L"+itoa3(i), "agenda", "done",
			"deploy the service carefully number "+itoa3(i), ""))
	}
	writeLessonRows(t, ws, "long", rows...)
	rr := Recall(ws, goal, "")
	lines := 0
	for _, ln := range strings.Split(rr.Lessons, "\n") {
		if strings.HasPrefix(ln, "- ") {
			lines++
		}
	}
	if lines != wantWindow {
		t.Fatalf("rendered %d lesson lines from a store of six, want %d:\n%s",
			lines, wantWindow, rr.Lessons)
	}
}

// TestAgendaTierIsQueriedFirst closes the survivor that swaps the agenda
// query for an untyped one. The whole point of the two-query shape is that
// an agenda-typed lesson gets a seat even when three untyped lessons score
// higher on the goal text; asking untyped first collapses the two queries
// into one and the agenda lesson loses on rank.
func TestAgendaTierIsQueriedFirst(t *testing.T) {
	ws := t.TempDir()
	goal := "deploy the service to production"
	writeLessonRows(t, ws, "long",
		// Scores lowest against the goal — no shared terms at all — so an
		// untyped-first query ranks it out of the top three.
		row("Lagenda", "agenda", "done", "keep the cat off the keyboard", ""),
		row("Lu1", "", "done", "deploy the service to production slowly", ""),
		row("Lu2", "", "done", "deploy the service to production twice", ""),
		row("Lu3", "", "done", "deploy the service to production again", ""),
	)
	rr := Recall(ws, goal, "")
	if !strings.Contains(rr.Lessons, "cat off the keyboard") {
		t.Fatalf("the agenda-typed lesson lost its seat to higher-ranked "+
			"untyped ones — the first query is not agenda-typed:\n%s",
			rr.Lessons)
	}
}

// TestTopUpDoesNotRunWhenTheAgendaQueryFilledTheWindow closes the survivor
// that widens `len(lessons) < 3` to `<= 3`. The extra query returns rows
// the renderer cannot use — the window is already full — but it still
// charges its skipped-row count to Sources, so a caller reading
// `lessons_skipped_rows` sees the malformed row counted twice.
func TestTopUpDoesNotRunWhenTheAgendaQueryFilledTheWindow(t *testing.T) {
	ws := t.TempDir()
	goal := "deploy the service"
	writeLessonRows(t, ws, "long",
		row("La1", "agenda", "done", "deploy the service one", ""),
		row("La2", "agenda", "done", "deploy the service two", ""),
		row("La3", "agenda", "done", "deploy the service three", ""),
		"{not json at all",
	)
	rr := Recall(ws, goal, "")
	got, _ := rr.Sources["lessons_skipped_rows"].(int)
	if got != 1 {
		t.Fatalf("the malformed row was counted %d times, want 1 — a second "+
			"count means the top-up query ran with the window already full",
			got)
	}
}
