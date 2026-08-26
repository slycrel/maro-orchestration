package introspect

import (
	"fmt"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyNoteSrc drives _format_decomp_too_broad_note over a LoopDiagnosis
// built directly, so the formatter is measured without load_diagnoses
// underneath it.
const pyNoteSrc = `
import json, sys
import introspect

out = []
for c in json.loads(sys.argv[1]):
    d = introspect.LoopDiagnosis(
        loop_id="L", failure_class="decomposition_too_broad",
        severity="warning", evidence=c["evidence"], project=c["project"])
    out.append(introspect._format_decomp_too_broad_note(d))
print(json.dumps(out))
`

// pyNotesSrc drives find_relevant_failure_notes over a diagnoses.jsonl the
// test writes byte for byte — the whole pipeline, ranking included.
const pyNotesSrc = `
import json, sys
import introspect

a = json.loads(sys.argv[1])
print(json.dumps(introspect.find_relevant_failure_notes(
    a["goal"], limit=a["limit"], lookback=a["lookback"], project=a["project"])))
`

func TestFormatDecompNoteMatchesCPython(t *testing.T) {
	cases := []struct {
		name     string
		evidence []string
		project  string
	}{
		{name: "no evidence at all"},
		{name: "the canonical two-number line", evidence: []string{
			"Step 8 took 534230ms with 277883 tokens"}},
		{name: "the other canonical spelling", evidence: []string{
			"Step 6 consumed 297102 tokens (92887ms)"}},
		{name: "a project tag", project: "widgets", evidence: []string{
			"Step 8 took 534230ms with 277883 tokens"}},
		// The FIRST matching line wins, not the first line.
		{name: "a non-matching line before a matching one", evidence: []string{
			"Loop stuck: max_iterations",
			"Step 8 took 534230ms with 277883 tokens"}},
		// ...and when nothing matches, the first line is used anyway.
		{name: "no line matches the step pattern", evidence: []string{
			"something else entirely", "and another"}},
		// 4-or-more digits is the compression boundary in both patterns.
		{name: "999ms is not compressed", evidence: []string{
			"Step 1 took 999ms with 999 tokens"}},
		{name: "1000ms is compressed", evidence: []string{
			"Step 1 took 1000ms with 1000 tokens"}},
		{name: "several numbers on one line", evidence: []string{
			"Step 1 took 5000ms then 60000ms with 12345 tokens and 9999 tokens"}},
		// `\s*` between the count and the word: Python's \s, not Go's.
		{name: "a non-breaking space before tokens", evidence: []string{
			"Step 1 took 5000ms with 12345\u00a0tokens"}},
		{name: "a U+3000 space before tokens", evidence: []string{
			"Step 1 took 5000ms with 12345\u3000tokens"}},
		{name: "a file separator before tokens", evidence: []string{
			"Step 1 took 5000ms with 12345\x1ctokens"}},
		{name: "no space at all before tokens", evidence: []string{
			"Step 1 took 5000ms with 12345tokens"}},
		// A zero-width space is NOT whitespace to Python's \s, so the
		// pattern does not reach across it.
		{name: "a zero width space before tokens", evidence: []string{
			"Step 1 took 5000ms with 12345\u200btokens"}},
		// Unicode decimal digits: Python's \d matches them and int()
		// converts them. Go's \d does neither.
		// Escaped rather than written literally: Arabic-Indic digits carry
		// RTL bidi, so a source line containing them DISPLAYS in an order
		// its bytes are not in. A fixture whose point is exact byte
		// content must not be readable only by accident of the editor.
		{name: "arabic-indic digits", evidence: []string{
			"Step \u0661 took \u0665\u0660\u0660\u0660ms with " +
				"\u0661\u0662\u0663\u0664\u0665 tokens"}},
		{name: "devanagari digits", evidence: []string{
			"Step \u0967 took \u096b\u0966\u0966\u0966ms with " +
				"\u0967\u0968\u0969\u096a\u096b tokens"}},
		// A digit run far wider than any fixed-width integer.
		{name: "a forty digit count", evidence: []string{
			"Step 1 took " + strings.Repeat("9", 40) + "ms with 5000 tokens"}},
		// The number that divides to zero, and the one that divides to one.
		{name: "1999ms rounds down to 1s", evidence: []string{
			"Step 1 took 1999ms with 1999 tokens"}},
		// Leading zeros: int("0001234") is 1234.
		{name: "leading zeros", evidence: []string{
			"Step 1 took 0001234ms with 0001234 tokens"}},
		// An empty evidence line is falsy in Python's `best if best else`
		// chain — but it can never be `best`, because the pattern needs
		// the literal "Step".
		{name: "an empty first evidence line", evidence: []string{"", "second"}},
		// A newline inside a line: `.` does not cross it in either runtime.
		{name: "a newline between the numbers", evidence: []string{
			"Step 1 took\n534230ms with 277883 tokens"}},
		// A project name that looks like a tag already.
		{name: "a project with punctuation", project: "a (b) c",
			evidence: []string{"Step 1 took 5000ms with 12345 tokens"}},

		// Everything above this line hands the formatter ONE candidate
		// line, or one whose alternative is chosen the same way under
		// every spelling of the pattern — so the pattern's job of
		// SELECTING a line was measured by nothing, and four mutants
		// walked through it. Each case below pairs a non-matching first
		// line with a second line that only Python's pattern reaches:
		// the fallback then renders visibly different text.
		{name: "a unicode digit line after a plain one", evidence: []string{
			"no numbers here",
			"Step \u0661 took \u0665\u0660\u0660\u0660ms with " +
				"\u0661\u0662\u0663\u0664\u0665 tokens"}},
		{name: "a unicode space before ms after a plain line", evidence: []string{
			"no numbers here",
			"Step 1 took 5000\u00a0ms with 12345 tokens"}},
		// Python searches; it does not match from position zero.
		{name: "an indented step line after a plain one", evidence: []string{
			"no numbers here",
			"  Step 3 took 4000ms with 5000 tokens"}},
		// FIRST match wins, so two matching lines must not render alike.
		{name: "two matching lines", evidence: []string{
			"Step 1 took 5000ms with 12345 tokens",
			"Step 2 took 60000ms with 22345 tokens"}},
	}

	type kase struct {
		Evidence []string `json:"evidence"`
		Project  string   `json:"project"`
	}
	var payload []kase
	for _, tc := range cases {
		ev := tc.evidence
		if ev == nil {
			ev = []string{}
		}
		payload = append(payload, kase{ev, tc.project})
	}
	var want []string
	probe := pyprobe.Probe{Marker: "introspect.py"}
	probe.RunJSON(t, pyNoteSrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d notes for %d cases", len(want), len(cases))
	}
	for i, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			d := LoopDiagnosis{
				LoopID: "L", FailureClass: "decomposition_too_broad",
				Severity: "warning", Evidence: tc.evidence, Project: tc.project,
			}
			if got := FormatDecompTooBroadNote(d); got != want[i] {
				t.Errorf("note differs\ncpython %q\n     go %q", want[i], got)
			}
		})
	}
}

func TestFindRelevantFailureNotesMatchesCPython(t *testing.T) {
	diag := func(id, class, project, rec string, evidence ...string) string {
		var b strings.Builder
		b.WriteString(`{"loop_id": "` + id + `", "failure_class": "` + class +
			`", "severity": "warning", "project": "` + project +
			`", "recommendation": ` + quote(rec) + `, "evidence": [`)
		for i, e := range evidence {
			if i > 0 {
				b.WriteString(", ")
			}
			b.WriteString(quote(e))
		}
		b.WriteString(`]}`)
		return b.String()
	}

	// A store big enough that Go's pdqsort stops delegating to insertion
	// sort, with ONE row scoring higher than the rest.
	//
	// Both halves matter, and neither alone measures anything. Under 13
	// elements sort.Slice is insertion sort and therefore accidentally
	// stable, so a small fixture cannot tell it from sort.SliceStable. And
	// with every key TIED, Go's partitioning happens to leave the order
	// alone at any size — measured on this box at n up to 60. It is the
	// combination that permutes: 12 tied rows plus one higher, and the
	// unstable sort returns a scrambled tail while the stable one returns
	// load order. The higher-scoring row is the OLDEST, because
	// load_diagnoses hands them back newest-first and that puts it last in
	// the slice being sorted.
	//
	// The same fixture is the only one where the comparison's DIRECTION is
	// observable: every other case here has a single distinct overlap.
	tied := func() []string {
		rows := []string{
			diag("r00", "token_explosion", "", "rec00", "widget parser"),
		}
		for i := 1; i < 13; i++ {
			id := fmt.Sprintf("r%02d", i)
			rows = append(rows, diag(id, "token_explosion", "", "rec"+id,
				"widget"))
		}
		return rows
	}

	cases := []struct {
		name     string
		goal     string
		limit    int
		lookback int
		project  string
		rows     []string
	}{
		{name: "no diagnoses at all", goal: "build the widget", limit: 3,
			lookback: 50},
		{name: "every diagnosis is healthy", goal: "build the widget",
			limit: 3, lookback: 50, rows: []string{
				diag("a", "healthy", "", "nothing"),
			}},
		// The row above has no evidence, so dropping the healthy filter
		// would still answer with nothing and the filter went unmeasured.
		// This one OVERLAPS the goal: keeping it renders a note.
		{name: "a healthy diagnosis that overlaps is still dropped",
			goal: "build the widget", limit: 3, lookback: 50, rows: []string{
				diag("a", "healthy", "", "all fine", "the widget is fine"),
			}},
		{name: "one overlapping diagnosis", goal: "build the widget parser",
			limit: 3, lookback: 50, rows: []string{
				diag("a", "token_explosion", "", "steer the worker",
					"Step 2: the widget parser exploded"),
			}},
		// Overlap is on the goal's tokens MINUS stopwords. "the" and "to"
		// must not create a match on their own.
		{name: "only stopwords overlap", goal: "the to in of and",
			limit: 3, lookback: 50, rows: []string{
				diag("a", "token_explosion", "", "steer",
					"the parser and the lexer"),
			}},
		// The twentieth stopword. Every other case overlaps on a content
		// word too, so dropping ONE entry from the set changed no answer.
		// Here "by" is the only token the goal and the evidence share.
		{name: "by is a stopword and cannot create a match",
			goal: "written by", limit: 3, lookback: 50, rows: []string{
				diag("a", "token_explosion", "", "rec", "by the parser"),
			}},
		// Same-project entries lead regardless of overlap.
		{name: "same project outranks a better overlap",
			goal: "build the widget parser", limit: 3, lookback: 50,
			project: "proj", rows: []string{
				diag("a", "token_explosion", "other", "overlapping one",
					"widget parser widget parser"),
				diag("b", "setup_failure", "proj", "same project one",
					"nothing in common at all"),
			}},
		// Ties keep load order (newest first), which is the stability the
		// port has to preserve.
		{name: "tied overlaps keep newest-first order",
			goal: "widget", limit: 3, lookback: 50, rows: []string{
				diag("older", "token_explosion", "", "older rec", "widget"),
				diag("newer", "setup_failure", "", "newer rec", "widget"),
			}},
		// A decomposition_too_broad entry renders through the OTHER
		// formatter, so a mixed list exercises both branches.
		{name: "a mixed list uses both note formats",
			goal: "widget", limit: 5, lookback: 50, rows: []string{
				diag("a", "decomposition_too_broad", "p", "ignored",
					"Step 8 took 534230ms with 277883 widget tokens"),
				diag("b", "token_explosion", "", "plain recommendation",
					"widget"),
			}},
		// limit truncates AFTER ordering.
		{name: "limit truncates the ordered list",
			goal: "widget", limit: 1, lookback: 50, rows: []string{
				diag("a", "token_explosion", "", "first", "widget widget"),
				diag("b", "setup_failure", "", "second", "widget"),
			}},
		// lookback is passed to load_diagnoses, so it bounds the SCAN and
		// not the result — and load_diagnoses' own off-by-one applies.
		{name: "lookback of one sees only the newest row",
			goal: "widget", limit: 3, lookback: 1, rows: []string{
				diag("a", "token_explosion", "", "older", "widget"),
				diag("b", "token_explosion", "", "newer", "nothing here"),
			}},
		// A recommendation longer than 120 runes is clipped, and the clip
		// is in RUNES.
		{name: "a long recommendation is clipped at 120 runes",
			goal: "widget", limit: 3, lookback: 50, rows: []string{
				diag("a", "token_explosion", "",
					strings.Repeat("漢", 200), "widget"),
			}},
		{name: "newlines in the recommendation become spaces",
			goal: "widget", limit: 3, lookback: 50, rows: []string{
				diag("a", "token_explosion", "", "line one\nline two", "widget"),
			}},
		// The goal is lowercased before splitting, so case must not
		// decide overlap.
		{name: "case does not decide overlap", goal: "WIDGET Parser",
			limit: 3, lookback: 50, rows: []string{
				diag("a", "token_explosion", "", "rec", "widget PARSER"),
			}},
		// Python's .split() drops empty runs across its full whitespace
		// set, which Go's strings.Fields does not agree with.
		// U+001C is the discriminator and it has to be the ONLY one:
		// Go's strings.Fields splits U+00A0 and U+3000 as well, so a goal
		// separated by those still yields the matching token under both
		// spellings. Python's .split() treats \x1c as whitespace; Go's
		// unicode.IsSpace does not, so "alpha\x1cbeta" is one token there
		// and the overlap disappears.
		{name: "unicode whitespace splits the goal",
			goal: "alpha\x1cbeta", limit: 3, lookback: 50,
			rows: []string{
				diag("a", "token_explosion", "", "rec", "beta gamma"),
			}},
		// The other two whitespace code points, kept as their own case so
		// the one above stays a single-variable test.
		{name: "wide and non-breaking spaces split the goal too",
			goal: "widget\u00a0parser\u3000lexer", limit: 3, lookback: 50,
			rows: []string{
				diag("a", "token_explosion", "", "rec", "lexer something"),
			}},
		{name: "many tied rows and one better overlap",
			goal: "widget parser", limit: 13, lookback: 50, rows: tied()},
		// project="" means the same-project lane is never taken, even for
		// rows whose project is also "".
		{name: "an empty project does not open the same-project lane",
			goal: "nothing in common", limit: 3, lookback: 50, project: "",
			rows: []string{
				diag("a", "token_explosion", "", "rec", "unrelated words"),
			}},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ws := seedStore(t, "diagnoses.jsonl", tc.rows)
			probe := pyprobe.Probe{Marker: "introspect.py", Workspace: ws}
			var want []string
			probe.RunJSON(t, pyNotesSrc, &want, pyprobe.Arg(t, map[string]any{
				"goal": tc.goal, "limit": tc.limit,
				"lookback": tc.lookback, "project": tc.project,
			}))

			got := FindRelevantFailureNotes(ws, tc.goal, tc.limit,
				tc.lookback, tc.project)
			if len(got) != len(want) {
				t.Fatalf("note count: cpython %d %q, go %d %q",
					len(want), want, len(got), got)
			}
			for i := range want {
				if got[i] != want[i] {
					t.Errorf("note %d\ncpython %q\n     go %q",
						i, want[i], got[i])
				}
			}
		})
	}
}
