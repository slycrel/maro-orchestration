package record

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

// The daily log and MEMORY.md are SHARED files: Python appends to the same
// day's markdown and rewrites the same index. So the pins that matter are
// differential — CPython produces the bytes and Go must match them — and
// the case table below is passed to BOTH runtimes so there is one source of
// truth for what is being compared. A hand-kept expectation table on the Go
// side would drift the moment Python's format string changes, and drift
// silently, which is the failure this whole port keeps re-learning.

type dailyCase struct {
	Name      string `json:"name"`
	Goal      string `json:"goal"`
	Status    string `json:"status"`
	Summary   string `json:"summary"`
	TaskType  string `json:"task_type"`
	TokensIn  int    `json:"tokens_in"`
	TokensOut int    `json:"tokens_out"`
	ElapsedMS int64  `json:"elapsed_ms"`
	Achieved  *bool  `json:"goal_achieved"`
	Project   string `json:"project"`
}

const diffRecordedAt = "2026-08-23T04:05:06.000123+00:00"

// dailyCases covers every branch _append_daily_log has plus the boundaries
// its two slices sit on. Derived from the Python FUNCTION, not from the Go
// diff: the icon's two inputs and their four combinations, both optional
// lines, the 80-rune goal cut, the 400-rune summary cut at 399/400/401, and
// the two places a byte-oriented port would cut a multi-byte rune in half.
var dailyCases = []dailyCase{
	{Name: "done_unjudged", Goal: "Ship the thing", Status: "done",
		Summary: "All good.", TaskType: "loop", TokensIn: 10, TokensOut: 20,
		ElapsedMS: 1234},
	{Name: "done_achieved", Goal: "Ship the thing", Status: "done",
		Summary: "All good.", TaskType: "loop", TokensIn: 10, TokensOut: 20,
		ElapsedMS: 1234, Achieved: boolPtr(true), Project: "maro"},
	{Name: "done_not_achieved", Goal: "Ship the thing", Status: "done",
		Summary: "Nope.", TaskType: "loop", Achieved: boolPtr(false)},
	{Name: "stuck_unjudged", Goal: "Ship", Status: "stuck",
		Summary: "Stopped.", TaskType: "now", TokensIn: 1, TokensOut: 2,
		ElapsedMS: 7},
	// stuck + achieved=true is the combination that separates the icon's two
	// inputs: the status says failure, the verdict says success, and Python
	// requires BOTH for a tick. An icon keyed on either one alone passes
	// every other row in this table.
	{Name: "stuck_achieved", Goal: "Ship", Status: "stuck", Summary: "?",
		TaskType: "loop", Achieved: boolPtr(true)},
	{Name: "goal_cut_at_80", Goal: strings.Repeat("G", 200), Status: "done",
		Summary: "x", TaskType: "loop", Achieved: boolPtr(true)},
	// Astral runes: goal[:80] counts CODE POINTS. A byte cut lands at 80
	// bytes — 20 emoji — and splits the 21st down the middle.
	{Name: "goal_cut_is_runes", Goal: strings.Repeat("🎉", 200), Status: "done",
		Summary: "x", TaskType: "loop", Achieved: boolPtr(true)},
	{Name: "summary_399", Goal: "g", Status: "done",
		Summary: strings.Repeat("z", 399), TaskType: "loop", Achieved: boolPtr(true)},
	{Name: "summary_400", Goal: "g", Status: "done",
		Summary: strings.Repeat("z", 400), TaskType: "loop", Achieved: boolPtr(true)},
	{Name: "summary_401", Goal: "g", Status: "done",
		Summary: strings.Repeat("z", 401), TaskType: "loop", Achieved: boolPtr(true)},
	// Two-byte runes past the cut: both the slice AND the reported total are
	// code-point counts, so a byte-based port gets the number wrong as well
	// as the text.
	{Name: "summary_cut_is_runes", Goal: "g", Status: "done",
		Summary: strings.Repeat("é", 500), TaskType: "loop", Achieved: boolPtr(true)},
	// WIDE BUT SHORT — 250 runes, 500 bytes. This is the shape that
	// separates a rune cut from a byte cut, and the 500-rune case above does
	// NOT: there both counts exceed 400 and both implementations trim. Here
	// Python leaves the summary alone while a byte-length test says "too
	// long" and then slices runes[:400] out of a 250-rune slice. A mutation
	// battery found this hole; nothing else would have.
	{Name: "summary_wide_but_short", Goal: "g", Status: "done",
		Summary: strings.Repeat("é", 250), TaskType: "loop", Achieved: boolPtr(true)},
	// U+001F is whitespace to Python's str.strip() and NOT to Go's
	// strings.TrimSpace — the pytext.IsSpace divergence, on the field whose
	// stripped length decides whether the cut fires at all.
	{Name: "summary_strip_is_pythons", Goal: "g", Status: "done",
		Summary: "\x1f  padded  \x1f", TaskType: "loop", Achieved: boolPtr(true)},
	{Name: "empty_summary", Goal: "g", Status: "done", Summary: "   ",
		TaskType: "loop", Achieved: boolPtr(true)},
}

func boolPtr(b bool) *bool { return &b }

// pythonDaily asks CPython for the exact bytes its _append_daily_log writes
// for each case, and for the MEMORY.md its _update_memory_index renders from
// a seeded store.
func pythonDaily(t *testing.T, cases []dailyCase, ledger []map[string]any,
	days []string, lessons *string) map[string]string {
	t.Helper()
	src, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(src, "memory_ledger.py")); err != nil {
		t.Skipf("Python source tree unavailable: %v", err)
	}
	payload, err := json.Marshal(map[string]any{
		"cases": cases, "recorded_at": diffRecordedAt,
		"ledger": ledger, "days": days, "lessons": lessons,
	})
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", pyDailyProducer)
	cmd.Stdin = strings.NewReader(string(payload))
	cmd.Env = append(os.Environ(), "PYTHONPATH="+src)
	out, err := cmd.Output()
	if err != nil {
		var stderr string
		if ee, ok := err.(*exec.ExitError); ok {
			stderr = string(ee.Stderr)
		}
		t.Skipf("python3 unavailable or failed: %v\n%s", err, stderr)
	}
	var got map[string]string
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	return got
}

const pyDailyProducer = `
import json, os, sys, tempfile
from pathlib import Path
spec = json.load(sys.stdin)
ws = Path(tempfile.mkdtemp(prefix="m1diff-"))
assert str(ws).startswith("/tmp/"), ws
os.environ["MARO_WORKSPACE"] = str(ws)
import memory_ledger as ml
assert str(ml._memory_dir()).startswith("/tmp/"), ml._memory_dir()
ml._memory_dir().mkdir(parents=True, exist_ok=True)
out = {}
for c in spec["cases"]:
    o = ml.Outcome(outcome_id="oid", goal=c["goal"], status=c["status"],
                   summary=c["summary"], task_type=c["task_type"],
                   tokens_in=c["tokens_in"], tokens_out=c["tokens_out"],
                   elapsed_ms=c["elapsed_ms"], goal_achieved=c["goal_achieved"],
                   project=c["project"], recorded_at=spec["recorded_at"],
                   lessons=[])
    p = ml._daily_path()
    if p.exists():
        p.unlink()
    ml._append_daily_log(o)
    out[c["name"]] = p.read_text(encoding="utf-8")
if spec["ledger"] is not None:
    ml._outcomes_path().write_text(
        "".join(json.dumps(r) + "\n" for r in spec["ledger"]), encoding="utf-8")
    for d in spec["days"] or []:
        (ml._memory_dir() / d).write_text("x", encoding="utf-8")
    if spec["lessons"] is not None:
        ml._lessons_path().write_text(spec["lessons"], encoding="utf-8")
    ml._update_memory_index()
    out["__index__"] = ml._memory_index_path().read_text(encoding="utf-8")
print(json.dumps(out))
`

func (c dailyCase) outcome() Outcome {
	return Outcome{
		Goal: c.Goal, Status: c.Status, Summary: c.Summary,
		TokensIn: c.TokensIn, TokensOut: c.TokensOut,
		ElapsedMS: c.ElapsedMS, GoalAchieved: c.Achieved, Project: c.Project,
	}
}

func TestTheDailyEntryIsByteIdenticalToPythons(t *testing.T) {
	want := pythonDaily(t, dailyCases, nil, nil, nil)
	for _, c := range dailyCases {
		t.Run(c.Name, func(t *testing.T) {
			dir := t.TempDir()
			if err := appendDailyLog(dir, c.outcome(), c.TaskType, diffRecordedAt); err != nil {
				t.Fatal(err)
			}
			// Go names the file from the local date and so does Python, so
			// reading the one Go wrote is reading the one Python would.
			raw, err := os.ReadFile(filepath.Join(dir,
				time.Now().Format("2006-01-02")+".md"))
			if err != nil {
				t.Fatal(err)
			}
			if got := string(raw); got != want[c.Name] {
				t.Errorf("entry differs from CPython's\n go: %q\nwant: %q",
					got, want[c.Name])
			}
		})
	}
}

func TestTheMemoryIndexIsByteIdenticalToPythons(t *testing.T) {
	full := func(over map[string]any) map[string]any {
		r := map[string]any{"outcome_id": "o", "goal": "g", "task_type": "loop",
			"status": "done", "summary": "s", "lessons": []any{}}
		for k, v := range over {
			r[k] = v
		}
		return r
	}
	// TWELVE rows, oldest first, because the window is ten. With five rows
	// the filter-then-window order was undetectable — dropping the
	// unloadable row left the same four either way, and a mutant that took
	// the newest ten BEFORE filtering survived the whole battery. Here the
	// unloadable row sits inside the newest ten, so windowing first yields
	// nine loadable rows while filtering first yields ten, pulling an older
	// row in. Python filters first.
	ledger := []map[string]any{
		full(map[string]any{"tokens_in": 7, "tokens_out": 0}),  // oldest
		full(map[string]any{"tokens_in": 11, "tokens_out": 0}), // the row pulled in
		full(map[string]any{"goal_achieved": true, "tokens_in": 1000, "tokens_out": 234}),
		full(map[string]any{"status": "stuck", "goal_achieved": false,
			"tokens_in": 1, "tokens_out": 2}),
		full(nil),
		// A malformed verdict: Python tests `is True` / `is False`, so a
		// string counts as NEITHER and lands in the unjudged remainder. This
		// is the one place the port does not use GoalAchieved's hardened
		// tri-state, which would grade it NOT-achieved and break the
		// invariant that the three buckets sum to the window size.
		full(map[string]any{"goal_achieved": "yes", "tokens_in": 5, "tokens_out": 5}),
		// Missing `lessons` — one of the six Outcome fields with no default,
		// so CPython cannot rehydrate the row and drops it. Its absurd token
		// count is the detector: if either runtime counts it, the totals
		// diverge by six figures.
		{"outcome_id": "o", "goal": "g", "task_type": "loop", "status": "done",
			"summary": "s", "tokens_in": 999999, "tokens_out": 999999},
		full(map[string]any{"status": "stuck", "tokens_in": 3, "tokens_out": 0}),
		full(map[string]any{"goal_achieved": true, "tokens_in": 13, "tokens_out": 0}),
		full(map[string]any{"tokens_in": 17, "tokens_out": 0}),
		full(map[string]any{"goal_achieved": false, "tokens_in": 19, "tokens_out": 0}),
		full(map[string]any{"tokens_in": 23, "tokens_out": 0}), // newest
	}
	// Twelve days so the seven-file cut and the newest-first order both bite.
	var days []string
	for d := 10; d <= 21; d++ {
		days = append(days, fmt.Sprintf("2026-08-%02d.md", d))
	}
	lessons := "{\"a\":1}\n\n{\"b\":2}\n"
	want := pythonDaily(t, dailyCases, ledger, days, &lessons)["__index__"]

	dir := t.TempDir()
	mem := filepath.Join(dir, "memory")
	if err := os.MkdirAll(mem, 0o777); err != nil {
		t.Fatal(err)
	}
	var lines []string
	for _, r := range ledger {
		b, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		lines = append(lines, string(b))
	}
	write(t, filepath.Join(mem, "outcomes.jsonl"), strings.Join(lines, "\n")+"\n")
	for _, d := range days {
		write(t, filepath.Join(mem, d), "x")
	}
	// Python renders the CURRENT day's file into the list too, because
	// _append_daily_log created it above. Match the seed.
	write(t, filepath.Join(mem, time.Now().Format("2006-01-02")+".md"), "x")
	write(t, filepath.Join(mem, "lessons.jsonl"), lessons)
	if err := updateMemoryIndex(mem); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(mem, "MEMORY.md"))
	if err != nil {
		t.Fatal(err)
	}

	// The Auto-updated stamp is a clock read on each side and can straddle a
	// minute boundary, so it is normalised out of the comparison and pinned
	// separately, by shape.
	stamp := regexp.MustCompile(`\*Auto-updated: [^*]*\*`)
	got := string(raw)
	if !stamp.MatchString(got) {
		t.Fatalf("no Auto-updated stamp in the index:\n%s", got)
	}
	if !regexp.MustCompile(
		`\*Auto-updated: \d{4}-\d\d-\d\d \d\d:\d\d UTC\*`).MatchString(got) {
		t.Errorf("Auto-updated is not Python's '%%Y-%%m-%%d %%H:%%M UTC':\n%s", got)
	}
	norm := func(s string) string { return stamp.ReplaceAllString(s, "*STAMP*") }
	if norm(got) != norm(want) {
		t.Errorf("MEMORY.md differs from CPython's\n go:\n%s\nwant:\n%s",
			norm(got), norm(want))
	}
}

func write(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o666); err != nil {
		t.Fatal(err)
	}
}

// ---------------------------------------------------------------------------
// Properties the differential cannot see
// ---------------------------------------------------------------------------

// The differential runs both sides on the same box in the same second, so
// it is blind to the two-clock quirk: the FILENAME is the LOCAL date and
// the HEADING is the row's UTC date. Porting the quirk is the point — the
// index globs by FILENAME, so a "fixed" runtime would file runs where the
// other runtime's reader never looks.
//
// This box sits at UTC-6, so the two dates genuinely disagree here every
// evening between 18:00 and midnight local. A test that took its
// expectation from time.Now() would therefore pass all morning and fail
// after dinner — which is what the first version of it did, and what a
// mutation battery caught by swapping local for UTC and surviving. So the
// zone is CONSTRUCTED rather than observed: an offset that puts local time
// at 23:59:59 of the previous UTC day, which makes the two dates differ no
// matter when the suite runs.
func TestTheFileIsNamedLocallyAndTheHeadingIsTheRowsUTCDate(t *testing.T) {
	utc := time.Now().UTC()
	secs := utc.Hour()*3600 + utc.Minute()*60 + utc.Second() + 1
	saved := time.Local
	time.Local = time.FixedZone("TEST", -secs)
	t.Cleanup(func() { time.Local = saved })

	utcDay := time.Now().UTC().Format("2006-01-02")
	localDay := time.Now().Format("2006-01-02")
	if utcDay == localDay {
		t.Fatalf("the constructed zone did not cross a date boundary "+
			"(utc=%s local=%s); this test would be vacuous", utcDay, localDay)
	}

	dir := t.TempDir()
	o := Outcome{Goal: "g", Status: "done", Summary: "s"}
	// A recorded_at deliberately far from any plausible clock reading, so
	// the heading cannot match by coincidence either.
	if err := appendDailyLog(dir, o, "loop", "1999-12-31T23:59:59.000000+00:00"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, utcDay+".md")); err == nil {
		t.Errorf("the file is named %s.md — the UTC date. Python's "+
			"_daily_path uses date.today(), the LOCAL one, and the index "+
			"globs by filename.", utcDay)
	}
	raw, err := os.ReadFile(filepath.Join(dir, localDay+".md"))
	if err != nil {
		t.Fatalf("no %s.md — the file is not named from the LOCAL date: %v",
			localDay, err)
	}
	if !strings.Contains(string(raw), "## [1999-12-31] ") {
		t.Errorf("the heading is not recorded_at[:10]:\n%s", raw)
	}
}

// Entries ACCUMULATE. Nothing rebuilds this file, so an append that
// truncated — or a second writer that replaced — would erase a day's record
// with no way to notice later.
func TestASecondEntryAppendsRatherThanReplacing(t *testing.T) {
	dir := t.TempDir()
	for _, g := range []string{"first", "second"} {
		if err := appendDailyLog(dir, Outcome{Goal: g, Status: "done"},
			"loop", diffRecordedAt); err != nil {
			t.Fatal(err)
		}
	}
	raw, err := os.ReadFile(filepath.Join(dir, time.Now().Format("2006-01-02")+".md"))
	if err != nil {
		t.Fatal(err)
	}
	got := string(raw)
	if !strings.Contains(got, "] ✓ first\n") || !strings.Contains(got, "] ✓ second\n") {
		t.Errorf("an entry was lost:\n%s", got)
	}
	if n := strings.Count(got, "## ["); n != 2 {
		t.Errorf("%d headings, want 2:\n%s", n, got)
	}
}

// The lock is what makes concurrent appends safe, and the NAME of the lock
// is what makes it safe against PYTHON — file_lock.locked_write takes
// path.name + ".lock" on the daily file itself. A port that locked the
// directory, or the memory dir, or nothing, passes every single-writer
// assertion above.
func TestTheDailyLogTakesPythonsLockFile(t *testing.T) {
	dir := t.TempDir()
	if err := appendDailyLog(dir, Outcome{Goal: "g", Status: "done"},
		"loop", diffRecordedAt); err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(dir, time.Now().Format("2006-01-02")+".md.lock")
	if _, err := os.Stat(want); err != nil {
		entries, _ := os.ReadDir(dir)
		var names []string
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Errorf("no %s — Python's locked_write would not exclude this "+
			"writer. Directory holds: %v", filepath.Base(want), names)
	}
}

// A daily entry is a multi-line block well past a single write's atomicity
// guarantee on a shared file. Under concurrency an unlocked O_APPEND
// interleaves, and the damage looks like a corrupt heading rather than a
// crash.
func TestConcurrentAppendsDoNotTearEachOther(t *testing.T) {
	dir := t.TempDir()
	const n = 12
	var wg sync.WaitGroup
	errs := make([]error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			errs[i] = appendDailyLog(dir, Outcome{
				Goal:    fmt.Sprintf("goal-%02d", i),
				Status:  "done",
				Summary: strings.Repeat("padding ", 800),
			}, "loop", diffRecordedAt)
		}(i)
	}
	wg.Wait()
	for i, err := range errs {
		if err != nil {
			t.Fatalf("writer %d: %v", i, err)
		}
	}
	raw, err := os.ReadFile(filepath.Join(dir, time.Now().Format("2006-01-02")+".md"))
	if err != nil {
		t.Fatal(err)
	}
	got := string(raw)
	if c := strings.Count(got, "## ["); c != n {
		t.Errorf("%d headings, want %d — entries interleaved", c, n)
	}
	for i := 0; i < n; i++ {
		want := fmt.Sprintf("- **Status**: done\n- **Type**: loop\n- **Summary**: padding")
		if !strings.Contains(got, fmt.Sprintf("] ✓ goal-%02d\n%s", i, want)) {
			t.Errorf("entry %d is torn or missing", i)
		}
	}
}

// Both surfaces are written, and a run that appends to neither is the r5
// finding restated. This is the pin at the level production calls.
func TestWritingAnOutcomeAlsoWritesBothHumanSurfaces(t *testing.T) {
	ws := t.TempDir()
	rec := New(ws)
	id, warns, err := rec.WriteOutcomeWithLog(Outcome{
		Goal: "Ship it", Status: "done", Summary: "done", TaskType: "loop",
		TokensIn: 1200, TokensOut: 34, GoalAchieved: boolPtr(true)})
	if err != nil {
		t.Fatal(err)
	}
	if len(warns) != 0 {
		t.Fatalf("warnings on a healthy workspace: %v", warns)
	}
	if id == "" {
		t.Fatal("no outcome id")
	}
	mem := filepath.Join(ws, "memory")
	daily, err := os.ReadFile(filepath.Join(mem, time.Now().Format("2006-01-02")+".md"))
	if err != nil {
		t.Fatalf("no daily log — the run is permanently absent from the day: %v", err)
	}
	if !strings.Contains(string(daily), "] ✓ Ship it\n") {
		t.Errorf("the entry is not this run's:\n%s", daily)
	}
	idx, err := os.ReadFile(filepath.Join(mem, "MEMORY.md"))
	if err != nil {
		t.Fatalf("no MEMORY.md: %v", err)
	}
	// The index must reflect the row THIS call appended — it is rebuilt
	// from the ledger, so rendering it before the append leaves it one run
	// behind the file beside it, forever off by one.
	if !strings.Contains(string(idx), "- Done: 1 | Stuck: 0") {
		t.Errorf("the index does not count the outcome just written:\n%s", idx)
	}
	if !strings.Contains(string(idx), "- Total tokens: 1,234") {
		t.Errorf("token total is missing or ungrouped:\n%s", idx)
	}
}

// Two blocks Python writes are absent because the Go row has nothing to put
// in them, and this test is what makes that a DECISION rather than a
// forgotten line. It fails the day either field starts carrying data, which
// is exactly when the writer above needs a second look.
func TestTheAbsentCostAndLessonBlocksAreStillAbsentForTheSameReason(t *testing.T) {
	ws := t.TempDir()
	rec := New(ws)
	if _, _, err := rec.WriteOutcomeWithLog(Outcome{
		Goal: "g", Status: "done", Summary: "s"}); err != nil {
		t.Fatal(err)
	}
	rows, err := LoadOutcomes(ws, 1)
	if err != nil || len(rows) != 1 {
		t.Fatalf("rows=%d err=%v", len(rows), err)
	}
	if _, ok := rows[0]["cost_usd"]; ok {
		t.Error("the row now carries cost_usd — the daily log must render " +
			"Python's six-decimal ' ($...)' cost suffix for it " +
			"(memory_ledger._append_daily_log)")
	}
	if l, ok := rows[0]["lessons"].([]any); !ok || len(l) != 0 {
		t.Errorf("the row now carries lessons (%v) — the daily log must "+
			"render Python's '- **Lessons**:' block for them", rows[0]["lessons"])
	}
}

// A failure on either surface must be ANNOUNCED, and named for what it
// costs. Python swallows both in a bare except; the port's whole
// justification for the daily log is that its gaps are permanent, so a
// silent one would be self-defeating.
//
// Making them fail takes two different levers, which is itself worth
// recording: a read-only DIRECTORY stops MEMORY.md (AtomicWrite must create
// a temp beside it) but NOT the daily append, because O_APPEND on a file
// that already exists needs no directory write at all. The daily log needs
// its own file made unwritable.
func TestASurfaceThatCannotBeWrittenIsAnnouncedNotSwallowed(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root ignores the mode bits this test relies on")
	}
	ws := t.TempDir()
	mem := filepath.Join(ws, "memory")
	if err := os.MkdirAll(mem, 0o777); err != nil {
		t.Fatal(err)
	}
	// The ledger append must still succeed, or the test proves only that a
	// broken workspace errors. This first call also CREATES both surfaces,
	// which is what lets the second one fail on writing rather than on
	// creating.
	rec := New(ws)
	if _, _, err := rec.WriteOutcomeWithLog(Outcome{Goal: "g", Status: "done"}); err != nil {
		t.Fatal(err)
	}
	daily := filepath.Join(mem, time.Now().Format("2006-01-02")+".md")
	if err := os.Chmod(daily, 0o444); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(daily, 0o666)
	if err := os.Chmod(mem, 0o555); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(mem, 0o777)

	_, warns, err := rec.WriteOutcomeWithLog(Outcome{Goal: "g2", Status: "done"})
	if err == nil && len(warns) == 0 {
		t.Fatal("a read-only memory dir produced neither error nor warning")
	}
	if err != nil {
		// The ledger append itself failed, which is a louder failure than
		// this test is aiming at but not a wrong one.
		return
	}
	joined := strings.Join(warns, "\n")
	if !strings.Contains(joined, "permanently absent") {
		t.Errorf("the daily-log failure does not say the gap is permanent, "+
			"which is the only reason it outranks the index failure:\n%s", joined)
	}
	if !strings.Contains(joined, "MEMORY.md") {
		t.Errorf("the index failure was not announced:\n%s", joined)
	}
}
