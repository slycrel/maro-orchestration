package record

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyLoadOutcomesSrc drives memory_ledger.load_outcomes over a store the
// test writes byte for byte.
//
// It returns asdict() of each Outcome AND the raw count, because the two
// answer different questions: the count is what the evolver's min_outcomes
// gate reads, and the dicts are what the inspector folds into its report.
const pyLoadOutcomesSrc = `
import json, sys
from dataclasses import asdict
import memory_ledger

_argv = json.loads(sys.argv[1])
rows = memory_ledger.load_outcomes(limit=_argv["limit"])
print(json.dumps({
    "count": len(rows),
    "goals": [r.goal for r in rows],
    "tokens_in": [r.tokens_in for r in rows],
}))
`

// TestLoadOutcomesMatchesCPython pins the reader's SCHEMA FILTER.
//
// Python's load_outcomes is two readers stacked: read_jsonl_announced, then
// a per-row `Outcome(**{k: d[k] for k in fields if k in d})`. The second
// one raises TypeError for a row missing any of the six fields with no
// default, and _rows_as excludes that row and counts it as schema drift.
//
// The port had only the first half, and the difference is not cosmetic: on
// a three-row store whose rows lack outcome_id and lessons, CPython loads
// ZERO and the evolver skips the cycle with "only 0 outcomes (need 3)",
// while the port loaded three and minted suggestions off them. Same shared
// store, same knob, opposite decisions.
//
// It went unseen because every Go fixture in the port was written against
// the tolerant reader — five packages' outcome seeders produced rows the
// Python runtime would never have read.
func TestLoadOutcomesMatchesCPython(t *testing.T) {
	// The six fields with no default, spelled out here rather than taken
	// from outcomeRequiredFields: a fixture that derives its input from the
	// list the code under test consults cannot disagree with it, and this
	// test's whole subject is whether that list is right.
	const full = `{"outcome_id": "%s", "goal": "%s", "task_type": "build", ` +
		`"status": "done", "summary": "s", "lessons": [], "tokens_in": %d}`

	cases := []struct {
		name  string
		lines []string
		limit int
	}{
		{name: "every row complete", limit: 50, lines: []string{
			sprintOutcome(full, "o1", "first", 10),
			sprintOutcome(full, "o2", "second", 20),
			sprintOutcome(full, "o3", "third", 30),
		}},
		// The shape the port got wrong: JSON, well-formed, and rejected by
		// the dataclass.
		{name: "rows missing outcome_id and lessons are excluded", limit: 50, lines: []string{
			`{"goal": "a", "task_type": "build", "status": "done", "summary": "s"}`,
			`{"goal": "b", "task_type": "build", "status": "done", "summary": "s"}`,
		}},
		{name: "a mixed store keeps only the loadable rows", limit: 50, lines: []string{
			sprintOutcome(full, "o1", "kept one", 1),
			`{"goal": "dropped", "status": "done"}`,
			sprintOutcome(full, "o2", "kept two", 2),
		}},
		// The exclusion happens BEFORE the limit slice, so a dropped row
		// must not consume a window slot.
		{name: "drift does not consume a limit slot", limit: 2, lines: []string{
			sprintOutcome(full, "o1", "oldest", 1),
			`{"goal": "dropped"}`,
			sprintOutcome(full, "o2", "middle", 2),
			`{"goal": "dropped too"}`,
			sprintOutcome(full, "o3", "newest", 3),
		}},
		// One field missing is still a missing field.
		{name: "one missing field excludes the row", limit: 50, lines: []string{
			`{"outcome_id": "o1", "goal": "g", "task_type": "build", ` +
				`"status": "done", "summary": "s"}`,
			sprintOutcome(full, "o2", "kept", 5),
		}},
		// A present NULL is a value, not an absence: a dataclass does not
		// enforce its annotations, so the row constructs and is kept.
		{name: "a present null is a value", limit: 50, lines: []string{
			`{"outcome_id": null, "goal": "nulled", "task_type": null, ` +
				`"status": null, "summary": null, "lessons": null}`,
		}},
		// A torn line costs one row and the read survives — the announced
		// reader's contract, unchanged by the filter stacked on top.
		{name: "a torn line costs one row", limit: 50, lines: []string{
			sprintOutcome(full, "o1", "before", 1),
			`{"outcome_id": "torn`,
			sprintOutcome(full, "o2", "after", 2),
		}},
		// An ARRAY row is "non-dict" to the announced reader, a different
		// loss bucket from drift and from corruption.
		{name: "an array row is a non-dict loss", limit: 50, lines: []string{
			`["not", "a", "row"]`,
			sprintOutcome(full, "o1", "kept", 1),
		}},
		{name: "an empty store", limit: 50, lines: nil},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			for _, ws := range []string{pyWS, goWS} {
				writeOutcomesStore(t, ws, c.lines)
			}

			var want struct {
				Count    int      `json:"count"`
				Goals    []string `json:"goals"`
				TokensIn []int    `json:"tokens_in"`
			}
			pyprobe.Probe{Marker: "memory_ledger.py", Workspace: pyWS}.RunJSON(
				t, pyLoadOutcomesSrc, &want,
				pyprobe.Arg(t, map[string]any{"limit": c.limit}))

			rows, err := LoadOutcomes(goWS, c.limit)
			if err != nil {
				t.Fatal(err)
			}
			if len(rows) != want.Count {
				t.Fatalf("loaded %d rows, CPython %d\n go: %v", len(rows), want.Count, rows)
			}
			for i, row := range rows {
				if g, _ := row["goal"].(string); g != want.Goals[i] {
					t.Errorf("row %d goal %q, CPython %q", i, g, want.Goals[i])
				}
				// tokens_in also pins the NUMBER TYPE. CPython's json.loads
				// makes an int of an integral literal and the port's reader
				// used json.Unmarshal, whose every number is a float64 — so
				// a renderer reaching for str() wrote "10.0" against
				// Python's "10" into the same MEMORY.md.
				gotTokens, ok := row["tokens_in"].(int)
				if !ok {
					// An absent tokens_in is Python's dataclass default 0.
					if _, present := row["tokens_in"]; present {
						t.Errorf("row %d tokens_in is %T, not an int — the "+
							"reader is typing numbers differently from "+
							"json.loads", i, row["tokens_in"])
						continue
					}
				}
				if want.TokensIn[i] != gotTokens {
					t.Errorf("row %d tokens_in %d, CPython %d", i, gotTokens, want.TokensIn[i])
				}
			}
		})
	}
}

// TestLoadOutcomesAnnouncesBothLosses is the other half of the reader's
// contract, and the half a row-count comparison cannot see.
//
// Python emits TWO warnings about the same read: read_jsonl_announced's,
// bucketing corruption, and _rows_as's, counting schema drift. The
// docstring says why they stay separate — a row that is not JSON is
// corruption, a row the dataclass rejects is drift, and drift is the one
// that grows quietly as the schema moves. A port that filtered silently
// would pass every count assertion in this file while leaving an operator
// with a shrinking corpus and no sentence about it.
func TestLoadOutcomesAnnouncesBothLosses(t *testing.T) {
	ws := t.TempDir()
	// The two drifted rows are missing DIFFERENT field sets on purpose. If
	// they were missing the same ones the two TypeErrors would be
	// byte-identical, and "quote the FIRST failure" would be
	// indistinguishable from "quote the last" — a mutant that swapped them
	// survived a fixture where both rows drifted the same way.
	writeOutcomesStore(t, ws, []string{
		`{"outcome_id": "o1", "goal": "kept", "task_type": "b", ` +
			`"status": "done", "summary": "s", "lessons": []}`,
		`{"goal": "drifted", "task_type": "b", "status": "done", "summary": "s"}`,
		`{"outcome_id": "o3"}`,
		`{"outcome_id": "torn`,
		`["array row"]`,
	})

	var said []string
	oldWarn := warn
	warn = func(format string, args ...any) {
		said = append(said, fmt.Sprintf(format, args...))
	}
	defer func() { warn = oldWarn }()

	rows, err := LoadOutcomes(ws, 50)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 1 {
		t.Fatalf("one loadable row, got %d", len(rows))
	}
	if len(said) != 2 {
		t.Fatalf("want two warnings — framing loss AND schema drift — got "+
			"%d: %q", len(said), said)
	}
	// The framing warning comes FIRST, because Python's read_jsonl_announced
	// has already logged by the time _rows_as starts building.
	if !strings.Contains(said[0], "load_outcomes:") ||
		!strings.Contains(said[0], "malformed") ||
		!strings.Contains(said[0], "non-dict") {
		t.Errorf("the framing warning does not name its buckets: %q", said[0])
	}
	// The exact sentence, not a substring of it: the count, the file, the
	// kept total, and the FIRST exception verbatim. The first drifted row
	// is missing only outcome_id and lessons; the second is missing four
	// more, so quoting the wrong one changes these bytes.
	wantDrift := fmt.Sprintf("load_outcomes: 2 row(s) in %s are JSON but not "+
		"loadable under the current schema — excluded from the 1 returned "+
		"(first: TypeError: Outcome.__init__() missing 2 required positional "+
		"arguments: 'outcome_id' and 'lessons')",
		filepath.Join(ws, "memory", "outcomes.jsonl"))
	if said[1] != wantDrift {
		t.Errorf("the drift warning is not CPythons sentence\n got: %q\nwant: %q",
			said[1], wantDrift)
	}
}

// TestTheZeroLimitIsANamedDivergence pins the ONE place this reader
// deliberately disagrees with CPython.
//
// Python's load_outcomes ends in `list(reversed(outcomes))[:limit]`, and
// `[:0]` is EMPTY — a caller that passes 0 gets nothing. The port reads
// limit <= 0 as "everything", on the rule that a zero value must degrade to
// all rather than to none (the recall r1 LoadOptions lesson). That is a
// choice, not parity, and this is where it is written down: the port has
// exactly one caller passing 0 (the MEMORY.md renderer, which wants the
// whole ledger), and no Python caller passes 0 at all.
//
// If this ever has to become parity, the fix is at the callers, not here.
func TestTheZeroLimitIsANamedDivergence(t *testing.T) {
	const row = `{"outcome_id": "o%d", "goal": "g%d", "task_type": "build", ` +
		`"status": "done", "summary": "s", "lessons": []}`
	pyWS, goWS := t.TempDir(), t.TempDir()
	lines := []string{fmt.Sprintf(row, 1, 1), fmt.Sprintf(row, 2, 2)}
	for _, ws := range []string{pyWS, goWS} {
		writeOutcomesStore(t, ws, lines)
	}
	var want struct {
		Count int `json:"count"`
	}
	pyprobe.Probe{Marker: "memory_ledger.py", Workspace: pyWS}.RunJSON(
		t, pyLoadOutcomesSrc, &want, pyprobe.Arg(t, map[string]any{"limit": 0}))
	if want.Count != 0 {
		t.Fatalf("CPython load_outcomes(limit=0) returned %d rows — it used "+
			"to return none, and this divergence no longer describes the "+
			"source", want.Count)
	}
	rows, err := LoadOutcomes(goWS, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 {
		t.Fatalf("the port must read limit<=0 as ALL, got %d rows", len(rows))
	}
}

// TestTheMissingArgsMessageMatchesCPython pins the operator-facing prose.
//
// The drift warning quotes the exception that excluded the row, and both
// runtimes warn about the same file. A byte-different sentence about
// identical damage reads as two different problems — the prose-divergence
// class this port keeps finding.
func TestTheMissingArgsMessageMatchesCPython(t *testing.T) {
	const src = `
import json, sys
from dataclasses import dataclass, field
from typing import List

@dataclass
class Outcome:
    outcome_id: str
    goal: str
    task_type: str
    status: str
    summary: str
    lessons: List[str]

out = []
for keep in json.loads(sys.argv[1])["keeps"]:
    try:
        Outcome(**{k: "x" for k in keep})
        out.append("")
    except TypeError as exc:
        out.append("%s: %s" % (type(exc).__name__, exc))
print(json.dumps(out))
`
	all := []string{"outcome_id", "goal", "task_type", "status", "summary", "lessons"}
	keeps := [][]string{
		{},                        // six missing — the Oxford-comma form
		all[:1],                   // five
		all[:3],                   // three
		all[:4],                   // two — "and" with no comma
		all[:5],                   // one — singular "argument"
		{"goal", "task_type"},     // a non-prefix subset, four missing
		{"lessons", "outcome_id"}, // out of declaration order in the row
		{"outcome_id", "goal", "task_type", "status", "summary", "lessons"}, // none
	}
	var want []string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want,
		pyprobe.Arg(t, map[string]any{"keeps": keeps}))

	for i, keep := range keeps {
		row := map[string]any{}
		for _, k := range keep {
			row[k] = "x"
		}
		missing := missingOutcomeFields(row)
		got := ""
		if len(missing) > 0 {
			got = "TypeError: " + PyMissingArgsMessage("Outcome", missing)
		}
		if got != want[i] {
			t.Errorf("case %d (kept %v)\n go: %q\n py: %q", i, keep, got, want[i])
		}
	}
}

func sprintOutcome(format, id, goal string, tokens int) string {
	return fmt.Sprintf(format, id, goal, tokens)
}

func writeOutcomesStore(t *testing.T, ws string, lines []string) {
	t.Helper()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := ""
	for _, l := range lines {
		body += l + "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, "outcomes.jsonl"), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}
