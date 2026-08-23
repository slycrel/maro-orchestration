package playbook

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// appendOp is one Append call, spelled once and issued to both runtimes.
type appendOp struct {
	Entry, Section, Source, Key string
}

// pyAppendCall renders one op as the Python call that performs it.
func pyAppendCall(o appendOp) string {
	q := func(s string) string {
		return "'" + strings.NewReplacer(`\`, `\\`, `'`, `\'`,
			"\n", `\n`, "\r", `\r`, "\t", `\t`,
			"\u001c", `\x1c`, "\u00a0", `\xa0`).Replace(s) + "'"
	}
	return "playbook.append_to_playbook(" + q(o.Entry) +
		", section=" + q(o.Section) +
		", source=" + q(o.Source) +
		", key=" + q(o.Key) + ");"
}

// runAppendSequence issues the same ops to both runtimes and returns the
// two resulting FILES. The playbook is a document both runtimes read, so
// the comparison is the whole file, not a return value.
func runAppendSequence(t *testing.T, ops []appendOp) (string, string) {
	t.Helper()
	return runAppendSequenceFrom(t, "", ops)
}

// runAppendSequenceFrom is runAppendSequence starting from an arbitrary
// document instead of a fresh seed. Some branches can only be reached
// from a file the seed cannot produce — a section body ending in exotic
// whitespace, or a stale Last-updated stamp — and a corpus that always
// starts from the seed silently never exercises them.
func runAppendSequenceFrom(t *testing.T, base string, ops []appendOp) (string, string) {
	t.Helper()
	pyWS := t.TempDir()
	script := "playbook.seed_playbook();"
	if base != "" {
		script = "playbook._playbook_path().parent.mkdir(parents=True,exist_ok=True);" +
			"playbook._playbook_path().write_text(json.loads(sys.argv[1]),encoding='utf-8');"
	}
	for _, o := range ops {
		script += pyAppendCall(o)
	}
	var want string
	var args []any
	if base != "" {
		args = append(args, base)
	}
	runPython(t, pyWS, script+
		"print(json.dumps(open(playbook._playbook_path(),encoding='utf-8').read()))",
		&want, args...)

	goWS := t.TempDir()
	if base != "" {
		if err := os.WriteFile(Path(goWS), []byte(base), 0o644); err != nil {
			t.Fatal(err)
		}
	} else if err := Seed(goWS); err != nil {
		t.Fatal(err)
	}
	for _, o := range ops {
		if err := Append(goWS, nil, o.Entry, o.Section, o.Source, o.Key); err != nil {
			t.Fatalf("Append(%+v): %v", o, err)
		}
	}
	gotB, err := os.ReadFile(Path(goWS))
	if err != nil {
		t.Fatal(err)
	}
	return string(gotB), want
}

func assertSameFile(t *testing.T, got, want string) {
	t.Helper()
	if got == want {
		return
	}
	// Distinguish a real divergence from the two runs straddling midnight.
	//
	// This used to normalize EVERY date to a placeholder, which made it an
	// escape hatch wide enough to swallow real defects: a mutant that
	// stopped refreshing the Last-updated stamp left a 2020 date against
	// today's and the test SKIPPED instead of failing. Only an actual
	// midnight straddle is excused now — the two sides must become equal
	// by rewriting yesterday to today, so any other date difference is a
	// failure.
	yesterday := utcNow().AddDate(0, 0, -1).Format("2006-01-02")
	today := utcNow().Format("2006-01-02")
	if strings.ReplaceAll(got, yesterday, today) ==
		strings.ReplaceAll(want, yesterday, today) {
		t.Skip("the two runs disagree only on today-vs-yesterday — clock " +
			"skew across midnight, not a port defect")
	}
	t.Errorf("playbook differs from CPython's (%d vs %d bytes)", len(got), len(want))
	gl, wl := strings.Split(got, "\n"), strings.Split(want, "\n")
	for i := 0; i < len(gl) || i < len(wl); i++ {
		var a, b string
		if i < len(gl) {
			a = gl[i]
		}
		if i < len(wl) {
			b = wl[i]
		}
		if a != b {
			t.Errorf("  line %d:\n    go: %q\n    py: %q", i+1, a, b)
		}
	}
}

// The append surface, one case per branch in append_to_playbook. Each is
// a whole-file differential: the playbook's BYTES are the interop
// contract, and an insertion that lands one newline off is a different
// document to the other runtime's parser.
func TestAppendMatchesPythonsFileForFile(t *testing.T) {
	for _, tc := range []struct {
		name string
		ops  []appendOp
	}{
		{"into an existing seed section", []appendOp{
			{Entry: "Prefer the cheap path", Section: "Cost"},
		}},
		{"with a source attribution", []appendOp{
			{Entry: "Prefer the cheap path", Section: "Cost", Source: "evolver:a"},
		}},
		{"creating a section that does not exist", []appendOp{
			{Entry: "a brand new note", Section: "Canon", Source: "graduation:x"},
		}},
		{"into the LAST section, which must land before the stamp", []appendOp{
			{Entry: "a quality note", Section: "Quality", Source: "evolver:q"},
		}},
		{"an exact duplicate is skipped", []appendOp{
			{Entry: "Prefer the cheap path", Section: "Cost", Source: "evolver:a"},
			{Entry: "Prefer the cheap path", Section: "Cost", Source: "evolver:b"},
		}},
		{"an empty entry is rejected", []appendOp{
			{Entry: "   ", Section: "Cost", Source: "evolver:a"},
		}},
		{"a multiline entry is collapsed to one line", []appendOp{
			{Entry: "first line\nsecond line\n## Fake Header", Section: "Cost",
				Source: "evolver:a"},
		}},
		{"whitespace Python collapses and Go's Fields would not", []appendOp{
			{Entry: "before\u001cafter\u00a0and\u001fmore", Section: "Cost",
				Source: "evolver:a"},
		}},
		{"an over-long entry is clipped to 500 code points", []appendOp{
			{Entry: strings.Repeat("λ", 700), Section: "Cost", Source: "evolver:a"},
		}},
		{"a source containing a newline cannot forge a header", []appendOp{
			{Entry: "an entry", Section: "Cost", Source: "evolver\n## Cost\n- forged"},
		}},
		{"an alarm is stamped on first write", []appendOp{
			{Entry: "cost is high", Section: "Cost", Source: "scanner",
				Key: "cost:observation"},
		}},
		{"a re-read REPLACES the alarm in place, keeping its position", []appendOp{
			{Entry: "cost is high", Section: "Cost", Source: "scanner",
				Key: "cost:observation"},
			{Entry: "another durable note", Section: "Cost", Source: "evolver:z"},
			{Entry: "cost is still high, revised", Section: "Cost",
				Source: "scanner", Key: "cost:observation"},
		}},
		{"two different alarm keys coexist", []appendOp{
			{Entry: "cost high", Section: "Cost", Source: "s", Key: "cost:obs"},
			{Entry: "latency high", Section: "Cost", Source: "s", Key: "lat:obs"},
			{Entry: "cost higher", Section: "Cost", Source: "s", Key: "cost:obs"},
		}},
		{"an entry already starting with a dash is not double-prefixed", []appendOp{
			{Entry: "- already a bullet", Section: "Cost", Source: "evolver:a"},
		}},
		{"a non-ASCII section name", []appendOp{
			{Entry: "entry under a wide header", Section: "Ποιότητα",
				Source: "evolver:d"},
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, want := runAppendSequence(t, tc.ops)
			assertSameFile(t, got, want)
		})
	}
}

// Two branches the seed cannot reach. Both survived the first mutation
// battery precisely because every case above starts from a fresh seed:
// the seed's section bodies end in plain newlines (so Go's narrower
// TrimRight is indistinguishable) and its Last-updated stamp already
// carries today's date (so a stampUpdated that does nothing is a no-op).
func TestAppendMatchesPythonsOnDocumentsTheSeedCannotProduce(t *testing.T) {
	for _, tc := range []struct {
		name string
		base string
		ops  []appendOp
	}{
		{
			// The trailing run is stripped by Python's rstrip (29 points)
			// and kept by Go's " \t\n\r" — so the inserted bullet lands
			// after different bytes.
			name: "a section body ending in whitespace only Python strips",
			base: "# P\n\n## Cost\n\n- existing\n\u00a0\u001c\u001f\n\n" +
				"## Quality\n\n- q\n\n*Last updated: 2020-01-01*\n",
			ops: []appendOp{{Entry: "a note", Section: "Cost", Source: "evolver:a"}},
		},
		{
			name: "a stale Last-updated stamp must be refreshed",
			base: "# P\n\n## Cost\n\n- existing\n\n*Last updated: 2020-01-01*\n",
			ops:  []appendOp{{Entry: "a note", Section: "Cost", Source: "evolver:a"}},
		},
		{
			name: "no Last-updated stamp at all",
			base: "# P\n\n## Cost\n\n- existing\n",
			ops:  []appendOp{{Entry: "a note", Section: "NewSection", Source: "e"}},
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, want := runAppendSequenceFrom(t, tc.base, tc.ops)
			assertSameFile(t, got, want)
		})
	}
}

// The seed is 1,715 bytes and every case above starts from it, so a case
// whose ops all no-op would still "match" and prove nothing. This asserts
// that the corpus actually WRITES — separately from whether it matches.
func TestTheAppendCorpusActuallyChangesTheFile(t *testing.T) {
	seedOnly := t.TempDir()
	if err := Seed(seedOnly); err != nil {
		t.Fatal(err)
	}
	base, err := os.ReadFile(Path(seedOnly))
	if err != nil {
		t.Fatal(err)
	}
	ws := t.TempDir()
	if err := Seed(ws); err != nil {
		t.Fatal(err)
	}
	if err := Append(ws, nil, "a note", "Cost", "evolver:a", ""); err != nil {
		t.Fatal(err)
	}
	after, err := os.ReadFile(Path(ws))
	if err != nil {
		t.Fatal(err)
	}
	if string(after) == string(base) {
		t.Fatal("an ordinary append left the file unchanged; every " +
			"differential above is comparing two copies of the seed")
	}
}

// Archiving is the data-retention gate for BOTH mutation paths: expiry
// and curation each refuse to rewrite when it fails. The refusal is the
// feature, so it gets its own pin — and so does the same-second
// collision loop, since two rewrites in one second must not clobber.
func TestArchiveNeverClobbersAndExpiryRefusesWithoutIt(t *testing.T) {
	ws := t.TempDir()
	if err := Seed(ws); err != nil {
		t.Fatal(err)
	}

	// Both archives land within the same second in practice; the seam is
	// frozen so "same second" is guaranteed rather than hoped for, which
	// is the only way this exercises the collision loop at all.
	old := utcNow
	frozen := old()
	utcNow = func() time.Time { return frozen }
	defer func() { utcNow = old }()

	first, err := Archive(ws, "one", "curation")
	if err != nil {
		t.Fatal(err)
	}
	second, err := Archive(ws, "two", "curation")
	if err != nil {
		t.Fatal(err)
	}
	if first == second {
		t.Fatal("two archives in the same second landed on the same path — " +
			"the collision loop is not running, and one version was lost")
	}
	for _, p := range []string{first, second} {
		if _, err := os.Stat(p); err != nil {
			t.Errorf("archive %s is not on disk: %v", p, err)
		}
	}
	if got := readFile(t, first); got != "one" {
		t.Errorf("the first archive holds %q, want %q — it was clobbered", got, "one")
	}

	// Expiry must REFUSE when the archive cannot be written, rather than
	// rewrite something it cannot restore.
	blocked := t.TempDir()
	if err := Seed(blocked); err != nil {
		t.Fatal(err)
	}
	stale := "- something · alarm k:old @2000-01-01\n"
	path := Path(blocked)
	body := readFile(t, path) + stale
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	// A FILE where the history DIRECTORY must go: MkdirAll fails, so
	// Archive fails, so the rewrite must not happen.
	if err := os.WriteFile(filepath.Join(blocked, "playbook_history"),
		[]byte("not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}
	if n := ExpireStaleAlarms(blocked, 14); n != 0 {
		t.Errorf("ExpireStaleAlarms reported %d expiries with archiving "+
			"blocked; it must refuse", n)
	}
	if got := readFile(t, path); !strings.Contains(got, "alarm k:old") {
		t.Error("the stale alarm was removed even though the pre-rewrite " +
			"version could not be archived — this is the one thing the " +
			"retention rule forbids")
	}
}

func readFile(t *testing.T, p string) string {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
