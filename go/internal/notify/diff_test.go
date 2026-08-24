package notify

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// This file is the differential the r8 round said every shared emitter owes:
// the port's own bytes and prose, diffed against the CPython module it
// claims to reproduce, over inputs chosen to make the port LOSE if it is
// wrong. `escalation_context.py` is content-key PROSE — the recurring bug
// family of this whole port — so the sentences are compared character for
// character rather than eyeballed against a paraphrase.

func srcDir(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p, "escalation_context.py")); err != nil {
		t.Skipf("python source tree unavailable: %v", err)
	}
	return p
}

func runPy(t *testing.T, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t))
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("CPython probe failed: %v", err)
	}
	return string(out)
}

// --- decision_line ------------------------------------------------------

const pyDecisionSrc = `
import json, sys
from escalation_context import decision_line
out = []
for point, reason, step in json.loads(sys.argv[1]):
    out.append(decision_line(point, reason=reason, step=step))
sys.stdout.write(json.dumps(out))
`

// TestDecisionLineMatchesCPython covers the three templates, the unknown
// point, and every input shape that decides which branch runs.
//
// The whitespace cases are the sharp ones. Python's bare `str.split()`
// splits on 29 code points; strings.Fields splits on 25, and U+001C..U+001F
// are in the gap. They reach a reason through pasted terminal output, and a
// port using strings.Fields renders a DIFFERENT sentence — same words,
// different spacing — into a file a human reads to make a decision.
func TestDecisionLineMatchesCPython(t *testing.T) {
	cases := [][3]string{
		{"blocked_step", "the fetch tool returned 403 for every retry", ""},
		{"dispatch", "the goal names a repo this box cannot reach", ""},
		{"director_escalation", "two plans disagree about what done means", ""},
		{"no_such_point", "something happened", ""},
		// A step prefix, which changes the shape of `short` itself.
		{"blocked_step", "403 on every retry", "fetch the upstream manifest"},
		{"no_such_point", "reason here", "a step"},
		// Empty and whitespace-only reasons both take the `or` fallback,
		// and the fallback is applied AFTER the clip.
		{"blocked_step", "", ""},
		{"director_escalation", "   \t\n  ", ""},
		{"dispatch", "", "the step still shows"},
		// Whitespace RUNS collapse to single spaces, leading and trailing
		// runs vanish.
		{"director_escalation", "  a \t\t b \n\n c  ", ""},
		// The 29-vs-25 code point gap, both in the reason and the step.
		{"director_escalation", "a\x1cb\x1dc\x1ed\x1fe", ""},
		{"blocked_step", "plain", "a\x1cstep\x1fhere"},
		// Non-ASCII survives untouched — this is prose, not JSON.
		{"blocked_step", "the café path is unreachable → retry", ""},
		// Longer than _REASON_MAX: the clip counts RUNES, so a
		// multi-byte reason cuts at a different BYTE offset in each
		// runtime unless the port counts runes too.
		{"director_escalation", strings.Repeat("é", 300), ""},
		{"blocked_step", strings.Repeat("word ", 80), ""},
		// Step longer than 120, same rune-vs-byte question.
		{"dispatch", "short reason", strings.Repeat("é", 200)},
		// A reason whose whitespace collapse takes it from over the limit
		// to under it: the collapse happens BEFORE the clip.
		{"director_escalation", strings.Repeat("a    ", 60), ""},
	}
	blob, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	var want []string
	if err := json.Unmarshal([]byte(runPy(t, pyDecisionSrc, string(blob))), &want); err != nil {
		t.Fatal(err)
	}
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d lines for %d cases", len(want), len(cases))
	}
	for i, c := range cases {
		got := DecisionLine(c[0], c[1], c[2])
		if got != want[i] {
			t.Errorf("case %d %q/%q/%q:\n go: %q\n py: %q",
				i, c[0], c[1], c[2], got, want[i])
		}
		// Anti-vacuity, per case: a decision line is NEVER empty. The
		// pre-§9.6 shape this replaces was an escalation with no ask, and
		// a port that returned "" everywhere would pass a comparison
		// against a probe that also broke.
		if got == "" {
			t.Errorf("case %d produced no decision line at all", i)
		}
	}

	// Anti-vacuity for the whitespace axis specifically: strings.Fields is
	// the wrong-but-plausible implementation, and it must LOSE on the
	// separator cases. If this ever agrees, the cases above stopped
	// exercising the gap and the test is testing nothing.
	naive := func(point, reason, step string) string {
		short := runeHead(strings.Join(strings.Fields(reason), " "), reasonMax)
		if short == "" {
			short = "no reason recorded"
		}
		if step != "" {
			short = runeHead(strings.Join(strings.Fields(step), " "), 120) + " — " + short
		}
		if tmpl, ok := decisionTemplates[point]; ok {
			return fmt.Sprintf(tmpl, short)
		}
		return "Decide: " + short
	}
	lost := false
	for i, c := range cases {
		if naive(c[0], c[1], c[2]) != want[i] {
			lost = true
			break
		}
	}
	if !lost {
		t.Fatal("strings.Fields agrees with CPython on every case — the " +
			"29-vs-25 whitespace gap is no longer exercised, so this test " +
			"cannot fail for the reason it was written")
	}
}

// --- family_roi_line ----------------------------------------------------

const pyROISrc = `
import json, os, sys
os.environ["MARO_WORKSPACE"] = sys.argv[1]
from escalation_context import family_roi_line
sys.stdout.write(json.dumps(family_roi_line(sys.argv[2], window_days=int(sys.argv[3]))))
`

// TestFamilyROILineMatchesCPython drives the real ledger through both
// runtimes: the singular/plural fork, the window clause, the stamped-vs-
// unstamped split, and the three ways "no rows" can mean different things.
func TestFamilyROILineMatchesCPython(t *testing.T) {
	// Far enough back that "now" cannot drift across the boundary between
	// the two processes, and far enough forward that a 30-day window is
	// unambiguous. The recent stamps are relative-safe only if the test
	// writes them relative to now, so they are computed here.
	recentA := isoDaysAgo(2)
	recentB := isoDaysAgo(9)
	old := "2024-01-02T03:04:05+00:00"

	cases := []struct {
		name   string
		rows   []map[string]any
		class  string
		window int
		// touch, when set, writes the file even when rows is empty.
		emptyFile bool
	}{
		{name: "no ledger at all", rows: nil, class: "tool_thrash", window: 30},
		{name: "ledger exists but is empty", rows: nil, class: "tool_thrash",
			window: 30, emptyFile: true},
		{name: "class absent from a populated ledger",
			rows:  []map[string]any{{"failure_class": "other", "recorded_at": recentA}},
			class: "tool_thrash", window: 30},
		{name: "exactly one, recent — the SINGULAR noun",
			rows:  []map[string]any{{"failure_class": "tool_thrash", "recorded_at": recentA}},
			class: "tool_thrash", window: 30},
		{name: "exactly one, old — singular, no window clause",
			rows:  []map[string]any{{"failure_class": "tool_thrash", "recorded_at": old}},
			class: "tool_thrash", window: 30},
		{name: "several, mixed ages",
			rows: []map[string]any{
				{"failure_class": "tool_thrash", "recorded_at": recentA},
				{"failure_class": "tool_thrash", "recorded_at": recentB},
				{"failure_class": "tool_thrash", "recorded_at": old},
				{"failure_class": "other", "recorded_at": recentA},
			},
			class: "tool_thrash", window: 30},
		{name: "unstamped rows count all-time but never recently",
			rows: []map[string]any{
				{"failure_class": "tool_thrash"},
				{"failure_class": "tool_thrash", "recorded_at": ""},
				{"failure_class": "tool_thrash", "recorded_at": "not a date"},
			},
			class: "tool_thrash", window: 30},
		// A naive stamp is read as UTC on both sides (Python replaces the
		// missing tzinfo with timezone.utc). This box runs at UTC-6, and
		// the case is placed 26 hours back with a ONE-day window
		// deliberately: read as UTC the row is outside the window, read as
		// local it is inside. A three-days-ago row with a thirty-day window
		// would agree under either reading and prove nothing.
		{name: "a naive stamp is read as UTC, not local",
			rows: []map[string]any{
				{"failure_class": "tool_thrash", "recorded_at": naiveHoursAgo(26)},
			},
			class: "tool_thrash", window: 1},
		{name: "window narrower than the rows",
			rows: []map[string]any{
				{"failure_class": "tool_thrash", "recorded_at": recentA},
				{"failure_class": "tool_thrash", "recorded_at": recentB},
			},
			class: "tool_thrash", window: 5},
		{name: "healthy is silent",
			rows:  []map[string]any{{"failure_class": "healthy", "recorded_at": recentA}},
			class: "healthy", window: 30},
		{name: "empty class is silent", rows: nil, class: "", window: 30},
		{name: "a class name with a quote in it",
			rows:  []map[string]any{{"failure_class": `it's "odd"`, "recorded_at": old}},
			class: `it's "odd"`, window: 30},
		{name: "an unparseable row still leaves the ledger non-empty",
			rows: nil, class: "tool_thrash", window: 30, emptyFile: true},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ws := t.TempDir()
			p := filepath.Join(ws, "memory", "diagnoses.jsonl")
			if len(c.rows) > 0 || c.emptyFile {
				if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
					t.Fatal(err)
				}
				var b strings.Builder
				for _, r := range c.rows {
					raw, err := json.Marshal(r)
					if err != nil {
						t.Fatal(err)
					}
					b.Write(raw)
					b.WriteByte('\n')
				}
				if err := os.WriteFile(p, []byte(b.String()), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			var want string
			raw := runPy(t, pyROISrc, ws, c.class, fmt.Sprint(c.window))
			if err := json.Unmarshal([]byte(raw), &want); err != nil {
				t.Fatalf("probe output %q: %v", raw, err)
			}
			got := FamilyROILine(ws, c.class, c.window)
			if got != want {
				t.Fatalf("\n go: %q\n py: %q", got, want)
			}
		})
	}
}

// TestFamilyROILineDistinguishesMissingFromUnreadable pins the one case the
// CPython comparison above CANNOT reach: a ledger that exists, is non-empty,
// and yields no readable rows. Python gets there through read_jsonl_tail
// returning nothing; here the rows are unparseable.
//
// "First on record" over a broken ledger is a confident claim built on no
// evidence — exactly the noise this line exists to avoid — so silence is
// the required answer, and it must be told apart from the genuinely-cold
// case, which does speak.
func TestFamilyROILineDistinguishesMissingFromUnreadable(t *testing.T) {
	cold := t.TempDir()
	if got := FamilyROILine(cold, "tool_thrash", 30); got !=
		"Family context: first 'tool_thrash' failure on record." {
		t.Fatalf("a cold install must say 'first on record', got %q", got)
	}

	broken := t.TempDir()
	p := filepath.Join(broken, "memory", "diagnoses.jsonl")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte("{not json at all\nnor is this\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := FamilyROILine(broken, "tool_thrash", 30); got != "" {
		t.Fatalf("an unreadable ledger must stay silent, got %q", got)
	}
}

// --- the escalation ledger row -----------------------------------------

// The payload is spelled out on BOTH sides rather than shipped across as
// JSON, because a JSON round trip is not type-preserving: Go's
// json.Unmarshal turns every number into float64 while Python keeps ints
// int, so a transported `0` would arrive as `0` on one side and `0.0` on
// the other and the test would fail for a reason that has nothing to do
// with the writer. Two independent spellings is what a differential is.
//
// Keys are written in SORTED order here because the Go payload is a map and
// this writer sorts what it is given; a Python-only reordering would be a
// test artifact, not a finding.
const pyEscalationSrc = `
import os, sys
ws = sys.argv[1]
os.environ["MARO_WORKSPACE"] = ws
import notify
payload = {
    "blocking": False,
    "control": "tab\there\nnewline",
    "detail": "d" * 2600,
    "emoji": "\U0001F600",
    "list": [1, "two", None, 3.5],
    "nested": {"a": 2, "b": 1},
    "reason": "r" * 2600,
    "reasoning": "g" * 2600,
    "revert_detail": 12345,
    "success_rate": 1.0,
    "summary": "s" * 2600,
    "summary_for_user": "u" * 2600,
    "target": "prefer a > b in the café path → retry",
    "use_count": 0,
    "variant_of": None,
}
notify._write_escalation_file(sys.argv[2], payload)
sys.stdout.write(open(os.path.join(ws, "output", "escalations.jsonl"),
                      encoding="utf-8").read())
`

// TestEscalationRowMatchesCPythonByteForByte is the differential the private
// copy of this writer never had — which is exactly how it shipped rows with
// alphabetically sorted keys for months.
//
// `ts` is the one field that cannot agree between two processes, so it is
// replaced with a fixed marker in both strings AFTER the shapes are
// compared; everything else, including the separators, the ensure_ascii
// escapes and the clip markers, is compared literally.
func TestEscalationRowMatchesCPythonByteForByte(t *testing.T) {
	payload := map[string]any{
		// The clipped fields, each over 2000, so the ledger's own bound
		// fires and the two runtimes must agree on the CLIP MARKER too.
		"summary":          strings.Repeat("s", 2600),
		"reason":           strings.Repeat("r", 2600),
		"detail":           strings.Repeat("d", 2600),
		"reasoning":        strings.Repeat("g", 2600),
		"summary_for_user": strings.Repeat("u", 2600),
		// A clip-listed key holding a NON-STRING. Python guards the
		// clip with isinstance(entry[k], str), and without a case like
		// this the guard is unreachable in the fixture: str()-ing it
		// would emit the JSON string "12345" where both runtimes must
		// emit the NUMBER 12345.
		"revert_detail": 12345,
		// A clip-listed key holding a NON-string is left alone
		// (`isinstance(entry[k], str)`).
		"blocking": false,
		// The five json.dumps forks, in a single row: HTML chars, non-ASCII
		// (BMP and astral), a whole float, an int, and the separators.
		"target":       "prefer a > b in the café path → retry",
		"success_rate": 1.0,
		"use_count":    0,
		"variant_of":   nil,
		"emoji":        "😀",
		"nested":       map[string]any{"b": 1, "a": 2},
		"list":         []any{1, "two", nil, 3.5},
		"control":      "tab\there\nnewline",
	}

	pyWS := t.TempDir()
	want := strings.TrimRight(runPy(t, pyEscalationSrc, pyWS, "escalation"), "\n")

	goWS := t.TempDir()
	if err := writeEscalationFile(goWS, "escalation", payload, Options{}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(EscalationsPath(goWS))
	if err != nil {
		t.Fatal(err)
	}
	got := strings.TrimRight(string(raw), "\n")

	// Both must LEAD with ts then event_type — checked before the stamp is
	// masked, because masking is what would hide a reordering.
	for name, line := range map[string]string{"go": got, "py": want} {
		if !strings.HasPrefix(line, `{"ts": "`) {
			t.Fatalf("%s row does not lead with ts:\n%s", name, line)
		}
		if !strings.Contains(line, `", "event_type": "escalation", `) {
			t.Fatalf("%s row does not put event_type second:\n%s", name, line)
		}
	}

	// The ts must be the WRITER's clock. A payload that supplies its own
	// `ts` overrides the value — that is what dict-splat does, and it is
	// ported — but no CALLER should be supplying one, and a row stamped
	// years off is indistinguishable from a correct row under a mask.
	// Checked here because masking the field is what would hide it.
	if !strings.HasPrefix(got, `{"ts": "`+time.Now().UTC().Format("2006-01-02")) {
		t.Fatalf("the escalation ts is not today's: %s", got[:40])
	}

	if maskTS(got) != maskTS(want) {
		t.Fatalf("escalation row diverges:\n go: %s\n py: %s",
			firstDiff(maskTS(got), maskTS(want)), firstDiff(maskTS(want), maskTS(got)))
	}

	// Anti-vacuity: encoding/json — the implementation this replaced — must
	// LOSE on this payload. Otherwise the fixture stopped exercising the
	// forks and the comparison above proves nothing.
	naive, err := json.Marshal(map[string]any{"ts": "x", "event_type": "escalation"})
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(naive), `", "`) {
		t.Fatal("encoding/json now emits json.dumps' separators — this test's " +
			"whole premise is gone")
	}
}

// TestEmitWritesBothSurfacesAndRunsNoHookByDefault pins the three-step
// contract, including the part that is easy to get backwards: a false
// return with no hook configured is the HEALTHY case, not a failure, and it
// must not stop either write.
func TestEmitWritesBothSurfacesAndRunsNoHookByDefault(t *testing.T) {
	ws := t.TempDir()
	ranHook := false
	if Emit(context.Background(), ws, "escalation",
		map[string]any{"reason": "the café path is unreachable → retry",
			"handle_id": "h1", "status": "blocked"},
		Options{Cfg: map[string]any{}, Exec: func(context.Context, string, string,
			[]string, time.Duration) (int, string, bool, error) {
			ranHook = true
			return 0, "", false, nil
		}}) {
		t.Fatal("Emit reported a hook ran when none is configured")
	}
	_ = ranHook

	esc, err := os.ReadFile(EscalationsPath(ws))
	if err != nil {
		t.Fatalf("no escalation ledger despite no hook: %v", err)
	}
	if !strings.Contains(string(esc), `caf\u00e9`) {
		t.Fatalf("escalation row is not json.dumps-spelled:\n%s", esc)
	}
	ev, err := os.ReadFile(filepath.Join(ws, "memory", "events.jsonl"))
	if err != nil {
		t.Fatalf("no events row despite no hook: %v", err)
	}
	if !strings.HasPrefix(string(ev), `{"event_type": "escalation", "ts": "`) {
		t.Fatalf("events row key order:\n%s", ev)
	}
	// The events projection takes `goal` from reason, and `status` from the
	// payload — a row that dropped either reads as an anonymous blip.
	if !strings.Contains(string(ev), `"status": "blocked"`) {
		t.Fatalf("events row lost the status:\n%s", ev)
	}

	// run_completed is NOT escalation-class: it has a durable home in
	// run_card.json, and duplicating it into the ledger would bury the
	// events that need a human.
	ws2 := t.TempDir()
	Emit(context.Background(), ws2, "run_completed",
		map[string]any{"handle_id": "h2"}, Options{Cfg: map[string]any{}})
	if _, err := os.Stat(EscalationsPath(ws2)); !os.IsNotExist(err) {
		t.Fatal("run_completed reached the escalation ledger")
	}
	if _, err := os.Stat(filepath.Join(ws2, "memory", "events.jsonl")); err != nil {
		t.Fatalf("run_completed missed the events feed: %v", err)
	}
}

// --- helpers ------------------------------------------------------------

// isoDaysAgo is the stamp shape introspect writes: timezone-aware
// isoformat with a +00:00 offset.
func isoDaysAgo(days int) string {
	return time.Now().UTC().AddDate(0, 0, -days).Format("2006-01-02T15:04:05.000000-07:00")
}

// naiveHoursAgo has no offset at all — the pre-V3 shape both runtimes must
// read as UTC rather than as local time.
func naiveHoursAgo(hours int) string {
	return time.Now().UTC().Add(-time.Duration(hours) * time.Hour).
		Format("2006-01-02T15:04:05.000000")
}

func maskTS(line string) string {
	i := strings.Index(line, `", "event_type"`)
	if i < 0 {
		return line
	}
	return `{"ts": "TS` + line[i:]
}

// firstDiff renders a from the first byte at which it differs from b, with
// a little lead-in — a 6KB row diffed whole is unreadable.
func firstDiff(a, b string) string {
	i := 0
	for i < len(a) && i < len(b) && a[i] == b[i] {
		i++
	}
	start := i - 40
	if start < 0 {
		start = 0
	}
	end := i + 120
	if end > len(a) {
		end = len(a)
	}
	return fmt.Sprintf("…%s… (first difference at byte %d)", a[start:end], i)
}

// --- the events projection, end to end ----------------------------------

const pyEmitSrc = `
import json, os, sys
ws = sys.argv[1]
os.environ["MARO_WORKSPACE"] = ws
import notify
notify.emit(sys.argv[2], json.loads(sys.argv[3]))
sys.stdout.write(open(os.path.join(ws, "memory", "events.jsonl"),
                      encoding="utf-8").read())
`

// TestEventRowMatchesCPython runs BOTH emit() implementations over the same
// payloads and diffs the events.jsonl rows.
//
// The hand-written projection tests state what each branch is supposed to
// do; this one proves it, and it is the reason those expectations are not
// just my arithmetic. The double-clip nesting in particular ("first 200 of
// 343") is the sort of value nobody derives correctly from the source —
// it has to be measured.
//
// Payloads travel as JSON, so numbers arrive as float64 on the Go side and
// int on Python's. That is a REAL divergence for a number that reaches the
// row, so the cases keep numbers out of the projected fields and cover the
// int/float question in the escalation-row test, where the payload is
// spelled on both sides instead of transported.
func TestEventRowMatchesCPython(t *testing.T) {
	cases := []struct {
		name    string
		event   string
		payload map[string]any
	}{
		{"result_excerpt wins", "escalation", map[string]any{
			"result_excerpt": "the excerpt", "summary": "the summary",
			"goal": "g", "status": "blocked"}},
		{"a present nil excerpt projects None", "escalation", map[string]any{
			"result_excerpt": nil, "summary": "the summary"}},
		{"a present empty excerpt beats the summary", "escalation", map[string]any{
			"result_excerpt": "", "summary": "the summary"}},
		{"goal falls back to reason", "escalation", map[string]any{
			"reason": "the reason", "summary": "s"}},
		{"a present nil goal projects None", "escalation", map[string]any{
			"goal": nil, "reason": "the reason"}},
		{"an empty payload", "escalation", map[string]any{}},
		{"the double clip", "escalation", map[string]any{
			"reason": strings.Repeat("g", 500), "summary": strings.Repeat("d", 500)}},
		{"a non-ASCII detail", "escalation", map[string]any{
			"summary": "prefer a > b in the café path → retry",
			"reason":  "the café path 😀"}},
		{"a run_verdict with every clause", "run_verdict", map[string]any{
			"handle_id": "h-9", "goal_achieved": true,
			"goal_verdict_source": "judge", "answer_changed": true,
			"goal_verdict_summary": "the answer now cites the right file"}},
		{"a run_verdict with no clauses", "run_verdict", map[string]any{
			"handle_id": "h-9", "goal_achieved": false,
			"goal_verdict_source": "", "answer_changed": false,
			"goal_verdict_summary": "no change"}},
		// The two optional clauses are gated on Python TRUTHINESS, not on
		// a bool. With real bools every truthiness implementation agrees,
		// including a bare type assertion — which is how the first cut of
		// this port shipped pyval.Bool under a comment claiming bool(),
		// dropping `source=` from every row. These cases are the ones that
		// tell the two apart.
		{"a run_verdict whose clause flags are truthy non-bools", "run_verdict",
			map[string]any{"handle_id": "h-9", "goal_achieved": "yes",
				"goal_verdict_source": "judge", "answer_changed": 1,
				"goal_verdict_summary": "s"}},
		{"a run_verdict whose clause flags are falsy non-bools", "run_verdict",
			map[string]any{"handle_id": "h-9", "goal_achieved": nil,
				"goal_verdict_source": nil, "answer_changed": 0,
				"goal_verdict_summary": "s"}},
		{"a run_verdict missing everything", "run_verdict", map[string]any{
			"handle_id": "h-9"}},
		{"a run_verdict whose summary must not shadow the verdict", "run_verdict",
			map[string]any{"handle_id": "h-9", "summary": "must not win",
				"goal_achieved": true, "goal_verdict_summary": "the verdict"}},
		{"a status that is not a string", "escalation", map[string]any{
			"status": nil, "handle_id": nil, "summary": "s"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			blob, err := json.Marshal(c.payload)
			if err != nil {
				t.Fatal(err)
			}
			want := strings.TrimRight(
				runPy(t, pyEmitSrc, t.TempDir(), c.event, string(blob)), "\n")

			goWS := t.TempDir()
			Emit(context.Background(), goWS, c.event, c.payload,
				Options{Cfg: map[string]any{}})
			raw, err := os.ReadFile(filepath.Join(goWS, "memory", "events.jsonl"))
			if err != nil {
				t.Fatalf("no events row: %v", err)
			}
			got := strings.TrimRight(string(raw), "\n")

			if maskEventTS(got) != maskEventTS(want) {
				t.Fatalf("events row diverges:\n go: %s\n py: %s", got, want)
			}
		})
	}
}

// maskEventTS blanks the one field two processes cannot agree on. The ts is
// the THIRD thing in the row, after event_type, so the mask is anchored to
// both sides of it — a mask that swallowed the tail would hide exactly the
// reordering this comparison exists to catch.
func maskEventTS(line string) string {
	open := strings.Index(line, `"ts": "`)
	if open < 0 {
		return line
	}
	rest := line[open+len(`"ts": "`):]
	close := strings.Index(rest, `"`)
	if close < 0 {
		return line
	}
	return line[:open] + `"ts": "TS` + rest[close:]
}
