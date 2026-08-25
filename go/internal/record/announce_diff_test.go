package record

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyAnnounceSrc drives jsonl_utils.read_jsonl_announced over a seeded store
// and reports the rows AND the warning sentences.
//
// The warnings are the subject. Every fixture below also drops the same rows
// on both sides, so a rows-only comparison would agree about all of them —
// including the two the port got wrong (announcing nothing at all, and
// counting a clean non-object line as malformed). The sentence is the only
// place the difference is observable, which is the whole reason the helper
// exists.
const pyAnnounceSrc = `
import json, sys, logging
from pathlib import Path
import jsonl_utils

_argv = json.loads(sys.argv[1])
_lines = []

class _Cap(logging.Handler):
    def emit(self, rec):
        _lines.append(rec.getMessage())

_h = _Cap()
logging.getLogger().addHandler(_h)
logging.getLogger().setLevel(logging.WARNING)
try:
    rows = jsonl_utils.read_jsonl_announced(Path(_argv["path"]), "probe")
finally:
    logging.getLogger().removeHandler(_h)

print(json.dumps({"rows": rows, "warnings": _lines}, sort_keys=True))
`

// TestReadAllAnnouncedMatchesCPython pins the announced reader — both what
// it returns and what it says about what it threw away.
func TestReadAllAnnouncedMatchesCPython(t *testing.T) {
	good := `{"id": "a", "n": 1}`
	for _, c := range []struct {
		name string
		// body is written verbatim; nil means "write no file at all",
		// which is the MISSING case and must announce nothing.
		body *string
	}{
		{"a missing file is not loss", nil},
		{"a clean store", str(good + "\n" + `{"id": "b"}` + "\n")},
		{"an empty file", str("")},

		// Framing. Only an empty fragment is framing; a line of spaces is
		// a torn record, and a reader that trimmed it would count one drop
		// fewer than CPython — silently, since the row list is the same.
		{"blank frames are not records", str("\n\n" + good + "\n\n")},
		{"a whitespace-only line is torn, not framing", str(good + "\n \n")},
		{"a line of only unicode separators is torn", str(good + "\n" +
			"\x1c\x1d\x1e\x1f\n")},

		// The three buckets, each alone so the label is unambiguous.
		{"a malformed line", str(good + "\n{not json\n")},
		{"a non-object line", str(good + "\n[1, 2]\n")},
		{"a bare string line", str(good + "\n\"just a string\"\n")},
		{"a byte-tainted line", str(good + "\n{\"id\": \"\xff\xfe\"}\n")},

		// loads_clean's refusals are the reader's refusals — a row the
		// write path will not vouch for is not a record on the read path
		// either. All four land in "malformed", which is CPython's answer
		// because loads_clean signals them as JSONDecodeError.
		{"an escaped lone surrogate", str(good + "\n" + `{"id": "\udcff"}` + "\n")},
		{"a duplicate name", str(good + "\n" + `{"id": "a", "id": "b"}` + "\n")},
		{"trailing data after the object", str(good + "\n{\"id\":\"a\"}{\"id\":\"b\"}\n")},
		{"a NaN token", str(good + "\n" + `{"x": NaN}` + "\n")},

		// MORE THAN ONE BUCKET AT ONCE. The summary joins only the
		// non-zero labels, in a fixed order, and a port that emitted every
		// label (or joined them in map order) would still pass every
		// single-bucket fixture above.
		{"all three buckets at once", str(good + "\n{nope\n[1,2]\n{\"z\":\"\xff\"}\n")},
		{"two of one bucket", str(good + "\n{nope\n{also nope\n")},

		// No trailing newline: the last fragment is still a record.
		{"an unterminated last line", str(good + "\n{\"id\": \"z\"}")},
	} {
		t.Run(c.name, func(t *testing.T) {
			dir := t.TempDir()
			path := filepath.Join(dir, "store.jsonl")
			if c.body != nil {
				if err := os.WriteFile(path, []byte(*c.body), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			var want struct {
				Rows     []map[string]any `json:"rows"`
				Warnings []string         `json:"warnings"`
			}
			pyprobe.Probe{Marker: "jsonl_utils.py"}.RunJSON(
				t, pyAnnounceSrc, &want,
				pyprobe.Arg(t, map[string]any{"path": path}))

			rows, warning := ReadAllAnnounced(path, "probe")

			if len(rows) != len(want.Rows) {
				t.Fatalf("rows = %d, CPython %d (%v vs %v)",
					len(rows), len(want.Rows), rows, want.Rows)
			}
			for i := range rows {
				// Compared through the ids only: CPython hands back ints
				// and Go hands back json.Number, and a deep-equal of the
				// values would be a test of the number representation,
				// which loadsclean_test already pins.
				if idOf(rows[i]) != idOf(want.Rows[i]) {
					t.Errorf("row %d id = %v, CPython %v",
						i, idOf(rows[i]), idOf(want.Rows[i]))
				}
			}

			var got []string
			if warning != "" {
				got = []string{warning}
			}
			// The path is identical on both sides here — the probe reads
			// the very file the port reads — so the sentences compare
			// verbatim, path and all.
			wantW := want.Warnings
			if len(got) == 0 && len(wantW) == 0 {
				return
			}
			if !reflect.DeepEqual(got, wantW) {
				t.Errorf("warnings:\n go: %q\n py: %q", got, wantW)
			}
		})
	}
}

func str(s string) *string { return &s }

func idOf(m map[string]any) any { return m["id"] }

// TestSkipReportSummaryOmitsEmptyBuckets pins the join, without a probe.
//
// It is here because the probe fixtures above can only reach bucket
// combinations a real file can produce, and the one thing most likely to rot
// is the "only non-zero labels, in this order" rule — a refactor that emitted
// all three labels would still produce a plausible-looking sentence.
func TestSkipReportSummaryOmitsEmptyBuckets(t *testing.T) {
	for _, c := range []struct {
		rep  SkipReport
		want string
	}{
		{SkipReport{Malformed: 1}, "dropped 1 line(s): 1 malformed"},
		{SkipReport{NonDict: 2}, "dropped 2 line(s): 2 non-dict"},
		{SkipReport{Undecodable: 3}, "dropped 3 line(s): 3 undecodable"},
		{SkipReport{Undecodable: 1, Malformed: 2, NonDict: 3},
			"dropped 6 line(s): 1 undecodable, 2 malformed, 3 non-dict"},
		{SkipReport{Malformed: 1, Partial: true},
			"dropped 1 line(s) in the scanned tail: 1 malformed"},
		{SkipReport{Unreadable: true},
			"unreadable (0 records returned — not an empty ledger)"},
	} {
		if got := c.rep.Summary(); got != c.want {
			t.Errorf("Summary(%+v) = %q, want %q", c.rep, got, c.want)
		}
	}
	// Missing is a report and NOT a loss — the distinction the Python
	// __bool__ exists to make, and the one a caller that just checked
	// "is there a report" would get backwards.
	if (SkipReport{Missing: true}).Lost() {
		t.Error("a missing file counted as loss")
	}
	if !(SkipReport{NonDict: 1}).Lost() {
		t.Error("a dropped non-dict line did not count as loss")
	}
}

// TestNothingRoutesAroundTheAnnouncedReader is the tripwire the helper's doc
// comment promises.
//
// The failure it guards is not a bug in any function — it is a whole class
// of quiet ones: a new loader that spells the scan out inline (ReadFile,
// split, IsFrameBlank, LoadsClean) gets the right rows and no announcement,
// and no differential over ITS return value can see the difference. The
// helper is only a fix for the callers that reach it (lens 17).
//
// Deliberately scoped to this package's own store readers. The port-wide
// migration is a queued chunk, not a thing to fail the build over today; the
// list below is the ones already converted, and it must not shrink.
func TestNothingRoutesAroundTheAnnouncedReader(t *testing.T) {
	raw, err := os.ReadFile("announce.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), "func ReadAllAnnounced") {
		t.Fatal("ReadAllAnnounced is gone — the callers below are now silent")
	}
	// store.go takes the COUNTED form, because Missing and Unreadable are
	// not recoverable from the announced string and its result type carries
	// that distinction. Same helper, same sentence — the reason both
	// spellings are accepted here is that SkipReport.Announce is what
	// produces the text in either case, so there is still exactly one
	// wording to keep right.
	for _, f := range []string{
		"../skills/testgate.go",
		"../skills/store.go",
	} {
		src, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(src), "record.ReadAllAnnounced(") &&
			!strings.Contains(string(src), "record.ReadAllCounted(") {
			t.Errorf("%s no longer reads its store through ReadAllAnnounced "+
				"— an inline scan returns the same rows and announces "+
				"nothing, which is exactly the loss this helper exists to "+
				"report", f)
		}
		// AND that the warning is not thrown away one frame up. The first
		// version of this tripwire checked only the call above, and passed
		// while `ValidateSkillMutation` did `tests, _ := LoadSkillTests(...)`
		// — announcing into a return value nobody reads, which is the same
		// silence with more code. A helper does not fix a class; it fixes
		// the callers that reach it, and "reach" includes what they do with
		// the answer.
		if strings.Contains(string(src), ", _ := LoadSkillTests(") {
			t.Errorf("%s discards LoadSkillTests' warnings at a call site "+
				"— the announcement stops there and no differential over "+
				"the returned rows can see it", f)
		}
	}
}

// TestReadAllAnnouncedOnAnUnreadableFile is the one report field the fixture
// table cannot reach: a file that EXISTS and cannot be opened.
//
// It is separate because it needs a mode change, and it matters because
// "unreadable" is the only bucket whose sentence says the count is not the
// story — `0 records returned — not an empty ledger`. A reader that folded it
// into "missing" would hand a caller an empty list and call it no data, which
// is the exact confusion the whole report exists to prevent.
func TestReadAllAnnouncedOnAnUnreadableFile(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("running as root: mode 000 does not deny root, so the " +
			"fixture would test the opposite of what it says")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "store.jsonl")
	if err := os.WriteFile(path, []byte(`{"id": "a"}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(path, 0o000); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chmod(path, 0o644) })

	var want struct {
		Rows     []map[string]any `json:"rows"`
		Warnings []string         `json:"warnings"`
	}
	pyprobe.Probe{Marker: "jsonl_utils.py"}.RunJSON(
		t, pyAnnounceSrc, &want, pyprobe.Arg(t, map[string]any{"path": path}))

	rows, warning := ReadAllAnnounced(path, "probe")
	if len(rows) != 0 || len(want.Rows) != 0 {
		t.Fatalf("rows: go %v, py %v — one of them read a file it cannot open",
			rows, want.Rows)
	}
	if got, wantW := []string{warning}, want.Warnings; !reflect.DeepEqual(got, wantW) {
		t.Errorf("warnings:\n go: %q\n py: %q", got, wantW)
	}
}
