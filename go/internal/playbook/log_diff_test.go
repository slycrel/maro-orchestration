package playbook

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The captain's-log rows this package emits are the half of its output
// that is not the playbook file, and until this file existed nothing
// compared them. That is the more dangerous half: the playbook is prose a
// human reads and would notice, while these rows are DATA — a Python
// reader indexes them by event_type, renders `summary` into the operator's
// log, and reads `context` by key. A Go row with a different summary
// spelling or a missing context key is a silently different record in a
// store both runtimes share.
//
// Two events reach here: PLAYBOOK_UPDATED from Append and PLAYBOOK_CURATED
// from Curate.

// logRow is the comparable part of a captain's-log row: everything except
// the fields that are unique per write by construction.
type logRow struct {
	EventType string         `json:"event_type"`
	Subject   string         `json:"subject"`
	Summary   string         `json:"summary"`
	Context   map[string]any `json:"context"`
}

// readGoLog decodes the rows Go wrote, dropping the per-write fields.
func readGoLog(t *testing.T, ws string) []logRow {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if err != nil {
		return nil
	}
	var out []logRow
	for _, line := range splitNonEmpty(string(b)) {
		var r logRow
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			t.Fatalf("decoding a row Go wrote: %v\nrow: %s", err, line)
		}
		out = append(out, r)
	}
	return out
}

func splitNonEmpty(s string) []string {
	var out []string
	start := 0
	for i := 0; i <= len(s); i++ {
		if i == len(s) || s[i] == '\n' {
			if i > start {
				out = append(out, s[start:i])
			}
			start = i + 1
		}
	}
	return out
}

const pyLogSnippet = `
import captains_log
rows=[]
for r in captains_log.load_log(limit=1000):
    rows.append({'event_type':r.get('event_type'),'subject':r.get('subject'),
                 'summary':r.get('summary'),'context':r.get('context')})
print(json.dumps(rows))
`

// normalizeArchived replaces the archive PATH in a curation context with a
// placeholder. It is an absolute path into a per-run temp workspace, so
// two runtimes can never produce the same string — but its presence, its
// key, and its shape all still have to match.
func normalizeArchived(rows []logRow, t *testing.T) {
	t.Helper()
	for _, r := range rows {
		v, ok := r.Context["archived"]
		if !ok {
			continue
		}
		s, ok := v.(string)
		if !ok {
			t.Fatalf("context.archived is %T, not a string", v)
		}
		if s == "" {
			t.Fatal("context.archived is empty; the archive path is the " +
				"only pointer back to the pre-curation version")
		}
		r.Context["archived"] = "<" + archiveShape(filepath.Base(s)) + ">"
	}
}

func TestTheCurationLogRowMatchesPythons(t *testing.T) {
	for _, tc := range []struct {
		name string
		doc  string
	}{
		{"an expired alarm and a duplicate",
			"# Director's Playbook\n\n## Signals\n\n- · alarm disk-full @2001-01-01\n\n" +
				"## Cost\n\n- twin\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"},
		// No alarms at all: expired_alarms must be an EMPTY LIST, not
		// null. Python's comprehension yields [], and a Go nil slice
		// marshals to null — a different value in a row a Python reader
		// iterates.
		{"a duplicate and no alarms",
			"# Director's Playbook\n\n## Cost\n\n- twin\n- twin\n- twin\n\n" +
				"*Last updated: 2020-01-01*\n"},
	} {
		t.Run(tc.name, func(t *testing.T) { curationLogCase(t, tc.doc) })
	}
}

func curationLogCase(t *testing.T, doc string) {
	t.Helper()

	pyWS := curateWorkspace(t, doc, 1<<30)
	var wantCurate any
	runPython(t, pyWS, "playbook.curate_playbook(force=True);"+pyLogSnippet,
		&wantCurate, doc)
	want := decodeRows(t, wantCurate)

	goWS := curateWorkspace(t, doc, 1<<30)
	if got := Curate(context.Background(), goWS, nil, record.New(goWS), true); got == nil {
		t.Fatal("Curate made no change, so no row was emitted and this " +
			"case proved nothing")
	}
	got := readGoLog(t, goWS)

	assertRowsAgree(t, got, want)
}

func TestTheAppendLogRowMatchesPythons(t *testing.T) {
	doc := "# Director's Playbook\n\n## Cost\n\n- existing\n\n*Last updated: 2020-01-01*\n"

	pyWS := curateWorkspace(t, doc, 1<<30)
	var wantAny any
	runPython(t, pyWS,
		"playbook.append_to_playbook('a new insight',section='Cost',"+
			"source='evolver:run-7');"+pyLogSnippet, &wantAny, doc)
	want := decodeRows(t, wantAny)

	goWS := curateWorkspace(t, doc, 1<<30)
	if err := Append(goWS, record.New(goWS), "a new insight", "Cost",
		"evolver:run-7", ""); err != nil {
		t.Fatal(err)
	}
	got := readGoLog(t, goWS)

	assertRowsAgree(t, got, want)
}

// An alarm RE-READ must emit NO captain's-log row. Python's replace
// branch returns from inside the lock, so its trailing log_event is never
// reached — re-firing is the whole point of an alarm, and logging every
// reading would add a row per scan to a shared, rotated, rendered log.
//
// This is the r9 HIGH. Every append fixture in the package used key="",
// so the branch existed with no pin on either side of it.
func TestAnAlarmReReadEmitsNoLogRow(t *testing.T) {
	doc := "# Director's Playbook\n\n## Cost\n\n- seed\n\n*Last updated: 2020-01-01*\n"

	pyWS := curateWorkspace(t, doc, 1<<30)
	var wantAny any
	runPython(t, pyWS,
		"playbook.append_to_playbook('cost high',section='Cost',"+
			"source='scanner',key='cost:obs');"+
			"playbook.append_to_playbook('cost still high',section='Cost',"+
			"source='scanner',key='cost:obs');"+pyLogSnippet, &wantAny, doc)
	want := decodeRows(t, wantAny)

	goWS := curateWorkspace(t, doc, 1<<30)
	rec := record.New(goWS)
	for _, entry := range []string{"cost high", "cost still high"} {
		if err := Append(goWS, rec, entry, "Cost", "scanner", "cost:obs"); err != nil {
			t.Fatal(err)
		}
	}

	// The FILE must show the replacement — otherwise a Go that simply
	// failed the second append would pass the row-count assertion for
	// entirely the wrong reason.
	pb := Load(goWS)
	if !strings.Contains(pb, "cost still high") {
		t.Fatalf("the re-read did not replace the entry:\n%s", pb)
	}
	if strings.Contains(pb, "cost high *") {
		t.Fatalf("the first reading survived beside the second:\n%s", pb)
	}
	if n := strings.Count(pb, "alarm cost:obs"); n != 1 {
		t.Fatalf("want exactly one alarm line, got %d:\n%s", n, pb)
	}

	if len(want) != 1 {
		t.Fatalf("CPython wrote %d rows for one write + one re-read; the "+
			"fixture is not exercising the replace path: %+v", len(want), want)
	}
	assertRowsAgree(t, readGoLog(t, goWS), want)
}

// The captain's-log summary is Python's `entry_line[:200]` — a plain
// code-point slice with NO ellipsis, written one line away from
// `entry[:500] + "…"`, which HAS one. clipNoEllipsis exists only to keep
// those two apart; its doc comment says so.
//
// Its truncating branch had never executed. Every append fixture in the
// package writes an entry far under 200 code points, so the whole branch —
// and the one-character difference it encodes — was carried by a comment
// (adversarial r9 LOW). A mutant that appended "…" survived the suite.
func TestALongAppendSummaryIsClippedWithoutAnEllipsis(t *testing.T) {
	for _, tc := range []struct{ name, entry string }{
		{"an ASCII entry past the summary clip",
			strings.Repeat("cost is high ", 30)},
		{"a three-byte script, where a BYTE slice lands mid-rune",
			strings.Repeat("界", 260)},
		{"an entry that is ITSELF ellipsis-clipped at 500",
			strings.Repeat("λ", 700)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			doc := "# Director's Playbook\n\n## Cost\n\n- seed\n\n" +
				"*Last updated: 2020-01-01*\n"

			pyWS := curateWorkspace(t, doc, 1<<30)
			var wantAny any
			runPython(t, pyWS,
				"playbook.append_to_playbook(json.loads(sys.argv[2]),section='Cost',"+
					"source='evolver:run-7');"+pyLogSnippet,
				&wantAny, doc, tc.entry)
			want := decodeRows(t, wantAny)
			if len(want) != 1 {
				t.Fatalf("CPython wrote %d rows for one append: %+v", len(want), want)
			}

			// CPython's OWN summary must not carry an ellipsis. The port's
			// clipNoEllipsis/clipRunes split rests entirely on that, and if
			// upstream ever changes it this says so instead of the port
			// quietly becoming wrong.
			if strings.HasSuffix(want[0].Summary, "…") {
				t.Fatalf("CPython's summary ends with an ellipsis: %q",
					want[0].Summary)
			}

			goWS := curateWorkspace(t, doc, 1<<30)
			if err := Append(goWS, record.New(goWS), tc.entry, "Cost",
				"evolver:run-7", ""); err != nil {
				t.Fatal(err)
			}
			assertRowsAgree(t, readGoLog(t, goWS), want)

			// The differential above compares two strings. This asserts the
			// fixture actually REACHED the truncating branch: the logged
			// summary must be a strict, shorter prefix of the line that
			// landed in the file. An ellipsis breaks the prefix; a byte
			// slice breaks it with an invalid rune; an equal length means
			// the entry was never long enough and the case proved nothing.
			var line string
			for _, ln := range strings.Split(Load(goWS), "\n") {
				if strings.HasPrefix(ln, "- ") && ln != "- seed" {
					line = ln
					break
				}
			}
			if line == "" {
				t.Fatal("the append wrote no entry line")
			}
			if !strings.HasPrefix(line, want[0].Summary) {
				t.Fatalf("the logged summary is not a prefix of the entry "+
					"line that landed:\n summary: %q\n    line: %q",
					want[0].Summary, line)
			}
			if utf8.RuneCountInString(line) <= utf8.RuneCountInString(want[0].Summary) {
				t.Fatalf("this fixture's entry line is %d code points — not "+
					"longer than the summary, so no truncation happened and "+
					"the case discriminates nothing",
					utf8.RuneCountInString(line))
			}
		})
	}
}

func decodeRows(t *testing.T, v any) []logRow {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out []logRow
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func assertRowsAgree(t *testing.T, got, want []logRow) {
	t.Helper()
	if len(want) == 0 {
		t.Fatal("CPython emitted no rows; this differential proved nothing")
	}
	if len(got) != len(want) {
		t.Fatalf("row count: go %d, py %d\n go: %+v\n py: %+v",
			len(got), len(want), got, want)
	}
	normalizeArchived(got, t)
	normalizeArchived(want, t)
	for i := range want {
		g, err := json.Marshal(got[i])
		if err != nil {
			t.Fatal(err)
		}
		w, err := json.Marshal(want[i])
		if err != nil {
			t.Fatal(err)
		}
		// json.Marshal of a map sorts keys, so this compares the row's
		// VALUES and key SET — not its on-disk key order, which pyjson
		// pins separately and which differs by a named divergence.
		if string(g) != string(w) {
			t.Errorf("row %d differs\n go: %s\n py: %s", i, g, w)
		}
	}
}
