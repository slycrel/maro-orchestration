package playbook

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

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
