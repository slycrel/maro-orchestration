package persona

import (
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The dispatch row is a durable JSONL line both runtimes append to one
// file, so its key ORDER, its separators and its float spelling are the
// contract — not just its content.
func TestRecordDispatchRowMatchesCPython(t *testing.T) {
	type row struct {
		goal     string
		persona  string
		conf     float64
		fallback bool
		handleID string
	}
	rows := []row{
		{"build the thing", "builder", 0.8, false, ""},
		{"build the thing", "builder", 0.8, false, "h-123"},
		// round(x, 3) is HALF-TO-EVEN on the binary value. 0.8925 and
		// 0.0625 are the spellings where half-away-from-zero disagrees.
		{"g", "p", 0.8925, true, ""},
		{"g", "p", 0.0625, true, ""},
		{"g", "p", 0.9199999999999999, true, ""},
		{"g", "p", 1.0, false, ""},
		{"g", "p", 0.0, true, ""},
		// ensure_ascii is ON for a bare json.dumps, so non-ASCII in the
		// goal is \u-escaped — and the goal is sliced at 120 CODE POINTS.
		{strings.Repeat("研", 200), "p", 0.5, false, ""},
		{"café — naïve", "p", 0.5, false, ""},
		{strings.Repeat("a", 121), "p", 0.5, false, ""},
	}
	probeRows := make([]any, len(rows))
	for i, r := range rows {
		probeRows[i] = []any{r.goal, r.persona, r.conf, r.fallback, r.handleID}
	}

	// The probe rebuilds the row Python's record_persona_dispatch builds,
	// with the timestamp pinned so the two sides can be compared at all.
	var want []string
	personaProbe(t).RunJSON(t, `
import json, sys
out = []
for goal, name, conf, fb, hid in json.loads(sys.argv[1]):
    # float() because the JSON transport hands 1.0 over as an int, and
    # round(1, 3) is the INT 1, which json.dumps spells "1" rather than
    # "1.0". record_persona_dispatch is annotated float and is always
    # called with one, so the int path is a fixture artefact, not the
    # behaviour under test.
    conf = float(conf)
    entry = {
        "goal_preview": goal[:120],
        "persona_name": name,
        "confidence": round(conf, 3),
        "is_fallback": fb,
        "dispatched_at": "PINNED",
    }
    if hid:
        entry["handle_id"] = hid
    out.append(json.dumps(entry))
print(json.dumps(out, ensure_ascii=False))
`, &want, pyprobe.Arg(t, probeRows))

	// CLAIM: the separators really carry spaces and the escaping is on.
	if !strings.Contains(want[0], `"goal_preview": "build the thing"`) {
		t.Fatalf("CLAIM moved: json.dumps' default separators no longer carry "+
			"a space (%q)", want[0])
	}
	// The OUTER dumps here is ensure_ascii=False, so a literal 研 would
	// survive transport. It does not appear, because the INNER bare
	// json.dumps — the one record_persona_dispatch calls — escaped it first.
	if strings.Contains(want[7], "研") || !strings.Contains(want[7], `\u7814`) {
		t.Fatalf("CLAIM moved: the inner bare json.dumps is no longer "+
			"\\u-escaping non-ASCII (%q)", want[7][:60])
	}

	// Every row goes through the REAL RecordDispatch and is read back off
	// disk. Rebuilding the row inside the test instead makes the test blind
	// to every decision the function makes -- measured: a version of this
	// test that did exactly that survived four separate mutations of
	// RecordDispatch (round -> math.Round, the json separators, the goal
	// clip, and handle_id becoming unconditional).
	ws := t.TempDir()
	for _, r := range rows {
		if err := RecordDispatch(ws, r.goal, r.persona, r.conf, r.fallback, r.handleID); err != nil {
			t.Fatal(err)
		}
	}
	raw, err := os.ReadFile(DispatchLogPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	if len(lines) != len(rows) {
		t.Fatalf("RecordDispatch wrote %d lines for %d calls", len(lines), len(rows))
	}

	var withHandle, escaped int
	for i, r := range rows {
		// Only the timestamp cannot be pinned across engines, so only the
		// timestamp is substituted -- and the substitution itself asserts
		// the key is spelled and placed the way the probe spells it.
		got := tsRE.ReplaceAllString(lines[i], `"dispatched_at": "PINNED"`)
		if got == lines[i] {
			t.Fatalf("row %d carries no `\"dispatched_at\": \"...\"` field: %s",
				i, lines[i])
		}
		if r.handleID != "" {
			withHandle++
		}
		if got != want[i] {
			t.Errorf("row %d\n  go %s\n  py %s", i, got, want[i])
		}
		if strings.Contains(want[i], `\u`) {
			escaped++
		}
	}
	if withHandle == 0 || escaped == 0 {
		t.Fatalf("vacuity: handle_id rows=%d escaped rows=%d", withHandle, escaped)
	}
}

// tsRE matches the single field whose value cannot be pinned across engines.
var tsRE = regexp.MustCompile(`"dispatched_at": "[^"]*"`)

// The written line, end to end: the path, the lock sidecar, and the row.
func TestRecordDispatchWritesTheLedger(t *testing.T) {
	ws := t.TempDir()
	if err := RecordDispatch(ws, "build the thing", "builder", 0.8925, true, "h-1"); err != nil {
		t.Fatal(err)
	}
	p := DispatchLogPath(ws)
	if p != filepath.Join(ws, "memory", "persona-dispatch-log.jsonl") {
		t.Fatalf("dispatch log path is %q", p)
	}
	raw, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	line := strings.TrimRight(string(raw), "\n")
	obj, derr := pyval.LoadsOrdered(line)
	if derr != nil {
		t.Fatalf("the written line is not JSON: %v (%q)", derr, line)
	}
	o := obj.(pyval.Obj)
	var keys []string
	for _, f := range o {
		keys = append(keys, f.Key)
	}
	want := []string{"goal_preview", "persona_name", "confidence",
		"is_fallback", "dispatched_at", "handle_id"}
	if !reflect.DeepEqual(keys, want) {
		t.Errorf("key order\n  go %v\n  want %v", keys, want)
	}
	// Python takes the same advisory flock on the same sibling.
	if _, err := os.Stat(p + ".lock"); err != nil {
		t.Errorf("no .lock sidecar beside the ledger: %v", err)
	}
}

// ScanGaps drives the real scan_persona_gaps over a log file both engines
// read. The rows are chosen so every branch of the filter and every branch
// of _infer_role fires.
const scanProbeSrc = `
import json, sys
import persona
from pathlib import Path
p, minf, days = json.loads(sys.argv[1])
try:
    out = persona.scan_persona_gaps(min_fallbacks=minf, window_days=days,
                                    log_path=Path(p))
    print(json.dumps({"err": "", "gaps": out}, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"err": "%s: %s" % (type(e).__name__, e), "gaps": []},
                     ensure_ascii=False))
`

type pyGaps struct {
	Err  string `json:"err"`
	Gaps []struct {
		RoleHint      string   `json:"role_hint"`
		FallbackCount int      `json:"fallback_count"`
		SampleGoals   []string `json:"sample_goals"`
		SuggestedSlug string   `json:"suggested_slug"`
	} `json:"gaps"`
}

func TestScanGapsMatchesCPython(t *testing.T) {
	dir := t.TempDir()
	now := pyval.NowISO(time.Now().UTC())
	old := "2000-01-01T00:00:00+00:00"

	row := func(goal any, fb any, ts any) string {
		o := pyval.Obj{}
		if goal != nil {
			o.Set("goal_preview", goal)
		}
		o.Set("persona_name", "x")
		if fb != nil {
			o.Set("is_fallback", fb)
		}
		if ts != nil {
			o.Set("dispatched_at", ts)
		}
		s, err := pyval.DumpsCompactPy(o)
		if err != nil {
			t.Fatal(err)
		}
		return s
	}

	var lines []string
	// "build ..." and "create issue ..." BOTH infer "builder": _ROLE_VERBS
	// iterates in insertion order and "create" precedes "create issue".
	for i := 0; i < 3; i++ {
		lines = append(lines, row("build the widget", true, now))
	}
	lines = append(lines, row("create issue for X", true, now))
	// The word branch, with a non-ASCII word that Python's \W keeps whole.
	for i := 0; i < 4; i++ {
		lines = append(lines, row("café thing here", true, now))
	}
	// A CJK word of TWO characters: len(w) > 2 counts CHARACTERS, so it is
	// dropped and the NEXT word wins.
	for i := 0; i < 3; i++ {
		lines = append(lines, row("研究 the widgets", true, now))
	}
	// Everything-filtered -> "general".
	lines = append(lines, row("", true, now))
	lines = append(lines, row("the a an to for", true, now))
	lines = append(lines, row("x"+nbspR+"y", true, now))
	// A timestamp that is a STRING but not a timestamp. The cutoff test is
	// a plain string comparison, so "NOTATIME" (N = 0x4E) sorts ABOVE an ISO
	// cutoff that starts with a digit (2 = 0x32) and the row is ACCEPTED.
	lines = append(lines, row("build bad ts", true, "NOTATIME"))
	// Excluded rows, each by a different rule.
	lines = append(lines, row("build build build", false, now))                     // not a fallback
	lines = append(lines, row("build outside window", true, old))                   // outside window
	lines = append(lines, row("build no ts", true, nil))                            // absent ts -> ""
	lines = append(lines, `{"is_fallback": true, "dispatched_at": `+"\""+now+"\"}") // absent goal
	lines = append(lines, row("build numeric ts", true, 5))                         // TypeError -> skipped
	lines = append(lines, "not json at all")
	lines = append(lines, "[1,2,3]") // JSON, but not a dict
	lines = append(lines, "")
	lines = append(lines, "   ")
	// A line padded with U+001F. Python's str.strip() removes it (it is
	// whitespace to str.isspace()) and Go's strings.TrimSpace does not, so
	// a TrimSpace port hands json.loads a leading control byte and drops a
	// row CPython counts. U+001F is the one of U+001C..U+001F that
	// splitlines() does NOT also break on, so this measures the strip and
	// nothing else.
	lines = append(lines, "\u001f"+row("build padded", true, now)+"\u001f")
	// A single-member group, so min_fallbacks has something to cut. The
	// form feed in the goal IS a splitlines() separator, but json.dumps
	// escapes it, so this line does not split -- it is the control for
	// the U+2028 row below, which is not escaped and does.
	lines = append(lines, row("deploy thething", true, now))
	// A raw U+2028 inside the JSON. Python's json.loads accepts it inside
	// a string, but read_text().splitlines() breaks on it FIRST, so CPython
	// sees two fragments and parses neither. A port that split on "\n"
	// would parse the whole line and count a SECOND "ops" fallback.
	lines = append(lines, `{"goal_preview": "deploy`+"\u2028"+
		`more", "is_fallback": true, "dispatched_at": "`+now+`"}`)

	p := filepath.Join(dir, "log.jsonl")
	if err := os.WriteFile(p, []byte(strings.Join(lines, "\n")+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	for _, minf := range []int{1, 3, 4} {
		var want pyGaps
		personaProbe(t).RunJSON(t, scanProbeSrc, &want,
			pyprobe.Arg(t, []any{p, minf, 30}))
		if want.Err != "" {
			t.Fatalf("min_fallbacks=%d: CPython raised %s", minf, want.Err)
		}
		got, err := ScanGaps("", p, minf, 30)
		if err != nil {
			t.Fatalf("min_fallbacks=%d: port failed: %v", minf, err)
		}
		if len(got) != len(want.Gaps) {
			t.Fatalf("min_fallbacks=%d: go %d gaps, py %d\n  go %+v\n  py %+v",
				minf, len(got), len(want.Gaps), got, want.Gaps)
		}
		for i := range got {
			w := want.Gaps[i]
			if got[i].RoleHint != w.RoleHint || got[i].FallbackCount != w.FallbackCount ||
				got[i].SuggestedSlug != w.SuggestedSlug ||
				!reflect.DeepEqual(got[i].SampleGoals, w.SampleGoals) {
				t.Errorf("min_fallbacks=%d gap[%d]\n  go %+v\n  py %+v",
					minf, i, got[i], w)
			}
		}
		if minf == 1 {
			// CLAIMS, on the arm where every group survives the filter.
			byRole := map[string]int{}
			var order []string
			for _, g := range want.Gaps {
				byRole[g.RoleHint] = g.FallbackCount
				order = append(order, g.RoleHint)
			}
			// 3 "build the widget" + "create issue for X" (which hits the
			// EARLIER "create" row, not "create issue") + "build bad ts"
			// (accepted because "NOTATIME" > an ISO cutoff as a string)
			// + "build padded" (accepted because str.strip() is Unicode).
			if byRole["builder"] != 6 {
				t.Fatalf("CLAIM moved: \"create issue for X\" no longer infers "+
					"builder, or a non-ISO dispatched_at string stopped sorting "+
					"above the cutoff, or U+001F stopped being stripped "+
					"(builder=%d, roles=%v)",
					byRole["builder"], order)
			}
			if byRole["widgets"] != 3 {
				t.Fatalf("CLAIM moved: the two-character CJK word is no longer "+
					"dropped by len(w) > 2 (roles=%v)", order)
			}
			if byRole["café"] != 4 {
				t.Fatalf("CLAIM moved: Python's \\W no longer keeps \"café\" "+
					"whole (roles=%v)", order)
			}
			if byRole["general"] != 4 {
				t.Fatalf("CLAIM moved: the four all-filtered rows (empty preview, "+
					"all-stopwords, NBSP-joined singles, ABSENT preview) no longer "+
					"land on \"general\" (general=%d, roles=%v)",
					byRole["general"], order)
			}
			// The stable-sort tie-break: café and general are both at 4, and
			// café's first fallback appears EARLIER in the log, so reverse=True
			// must not flip them.
			if len(order) < 3 || order[0] != "builder" ||
				order[1] != "café" || order[2] != "general" {
				t.Fatalf("CLAIM moved: the count-4 tie between café and general "+
					"no longer keeps first-seen order (%v)", order)
			}
			if byRole["ops"] != 1 {
				t.Fatalf("CLAIM moved: ops=%d, not 1 -- the U+2028 line is no "+
					"longer split by splitlines() before json.loads sees it "+
					"(roles=%v)", byRole["ops"], order)
			}
			if len(want.Gaps) < 5 {
				t.Fatalf("vacuity: only %d groups survived min_fallbacks=1",
					len(want.Gaps))
			}
		}
		if minf == 4 && len(want.Gaps) == 0 {
			t.Fatal("min_fallbacks=4 filtered everything, so the threshold " +
				"arm is not comparing anything")
		}
	}
}

// A non-string goal_preview makes CPython raise AttributeError OUT of
// scan_persona_gaps — it is the one failure in this function that is not
// swallowed. The port returns it as an error rather than panicking.
func TestScanGapsNonStringGoalRaisesLikeCPython(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "log.jsonl")
	now := pyval.NowISO(time.Now().UTC())
	if err := os.WriteFile(p, []byte(
		`{"goal_preview": 123, "is_fallback": true, "dispatched_at": "`+now+`"}`+"\n"),
		0o644); err != nil {
		t.Fatal(err)
	}
	var want pyGaps
	personaProbe(t).RunJSON(t, scanProbeSrc, &want, pyprobe.Arg(t, []any{p, 1, 30}))
	if !strings.HasPrefix(want.Err, "AttributeError") {
		t.Fatalf("CLAIM moved: CPython answered %q instead of raising "+
			"AttributeError", want.Err)
	}
	got, err := ScanGaps("", p, 1, 30)
	if err == nil {
		t.Fatalf("the port returned %v with no error where CPython raised %q",
			got, want.Err)
	}
	if err.Error() != want.Err {
		t.Errorf("error text\n  go %q\n  py %q", err.Error(), want.Err)
	}
}

// A missing log is [] and not an error, on both sides.
func TestScanGapsMissingLogIsEmpty(t *testing.T) {
	p := filepath.Join(t.TempDir(), "nope.jsonl")
	var want pyGaps
	personaProbe(t).RunJSON(t, scanProbeSrc, &want, pyprobe.Arg(t, []any{p, 1, 30}))
	if want.Err != "" || len(want.Gaps) != 0 {
		t.Fatalf("CLAIM moved: a missing log answered err=%q gaps=%v",
			want.Err, want.Gaps)
	}
	got, err := ScanGaps("", p, 1, 30)
	if err != nil || len(got) != 0 {
		t.Errorf("missing log: go (%v, %v)", got, err)
	}
}

// A byte-tainted log makes read_text raise inside the OUTER try, so the
// whole scan yields [] rather than a partial answer.
func TestScanGapsByteTaintedLogIsEmpty(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "log.jsonl")
	now := pyval.NowISO(time.Now().UTC())
	body := `{"goal_preview": "build a thing", "is_fallback": true, "dispatched_at": "` +
		now + `"}` + "\n\xff\xfe not utf8\n"
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	var want pyGaps
	personaProbe(t).RunJSON(t, scanProbeSrc, &want, pyprobe.Arg(t, []any{p, 1, 30}))
	if want.Err != "" {
		t.Fatalf("CPython raised out of scan_persona_gaps: %q", want.Err)
	}
	if len(want.Gaps) != 0 {
		t.Fatalf("CLAIM moved: a byte-tainted log now yields %d gaps — the "+
			"read is no longer inside the swallowing try", len(want.Gaps))
	}
	got, err := ScanGaps("", p, 1, 30)
	if err != nil || len(got) != 0 {
		t.Errorf("byte-tainted log: go (%v, %v), py []", got, err)
	}
}
