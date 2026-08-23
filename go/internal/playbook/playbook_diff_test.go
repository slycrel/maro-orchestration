package playbook

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf8"
)

// Every pin in this file is a DIFFERENTIAL: it runs CPython's own
// playbook.py against the same input and compares. Reconstructing the
// expectation in Go would only pin my reading of the Python, which is the
// exact failure mode this port keeps producing.

// srcDir is the Python package this module is a port of.
func srcDir(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p, "playbook.py")); err != nil {
		t.Skipf("Python source not available at %s: %v", p, err)
	}
	return p
}

// runPython drives CPython's playbook module against workspace ws and
// decodes whatever JSON the snippet prints.
//
// The workspace guard is not ceremony. MARO_WORKSPACE is the store
// override, and a probe that resolves to the live ~/.maro/workspace would
// rewrite the operator's real playbook — which has happened on this box
// before, to a different ledger. The snippet asserts the resolved path
// before it is allowed to write anything.
func runPython(t *testing.T, ws, snippet string, out any, args ...any) {
	t.Helper()
	if !strings.HasPrefix(ws, os.TempDir()) && !strings.HasPrefix(ws, "/tmp/") {
		t.Fatalf("refusing to run a writing probe against %q", ws)
	}
	guard := "import json,os,sys;import playbook;" +
		"_p=str(playbook._playbook_path());" +
		"assert _p.startswith('/tmp/'), 'REFUSING: resolved to '+_p;"
	argv := []string{"-c", guard + snippet}
	for _, a := range args {
		b, err := json.Marshal(a)
		if err != nil {
			t.Fatal(err)
		}
		argv = append(argv, string(b))
	}
	cmd := exec.Command("python3", argv...)
	cmd.Env = append(os.Environ(),
		"MARO_WORKSPACE="+ws,
		"PYTHONPATH="+srcDir(t),
	)
	res, err := cmd.Output()
	if err != nil {
		// A MISSING interpreter is a skip: this box has one, another
		// might not, and a differential cannot run without both sides.
		//
		// A FAILING probe is not. It used to be, and that turned ten of
		// twelve differentials — including the seed-bytes pin this
		// package's doc calls "the only honest pin" — into tests that
		// reported green while running nothing. The failure modes it
		// swallowed are exactly the ones that matter: a renamed Python
		// helper, a changed signature, or the /tmp/ safety assert
		// firing (adversarial r9 LOW).
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe FAILED (exit %d). This is not a "+
				"missing interpreter — the differential cannot report "+
				"green.\nstderr:\n%s", ee.ExitCode(), ee.Stderr)
		}
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("python3 is present but the probe could not run: %v", err)
	}
	if err := json.Unmarshal(res, out); err != nil {
		t.Fatalf("decoding CPython output: %v\nraw: %s", err, res)
	}
}

// The seed is the initial BYTES of a file both runtimes read, so "close"
// is not a thing. This compares the whole file, not a prefix or a length.
func TestTheSeedIsByteIdenticalToPythons(t *testing.T) {
	ws := t.TempDir()
	var want string
	runPython(t, ws, "playbook.seed_playbook();"+
		"print(json.dumps(open(playbook._playbook_path(),encoding='utf-8').read()))",
		&want)

	goWS := t.TempDir()
	if err := Seed(goWS); err != nil {
		t.Fatal(err)
	}
	gotB, err := os.ReadFile(Path(goWS))
	if err != nil {
		t.Fatal(err)
	}
	got := string(gotB)
	if got == want {
		return
	}
	// The date is the one legitimately-varying field; if that is the ONLY
	// difference the clocks disagree, which is a test bug, not a port bug.
	t.Errorf("seed differs from CPython's (%d vs %d bytes)", len(got), len(want))
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

// entryCore is a DEDUP KEY on a shared file: a divergence means the two
// runtimes keep a different set of bullets. The corpus is built from the
// hazards, one per nasty case, not from happy-path text.
func TestEntryCoreMatchesPythons(t *testing.T) {
	lines := []string{
		"- Prefer the cheap path",
		"-  Prefer THE cheap path *(from evolver:x)*",
		"   - indented bullet",
		"- ΟΔΟΣ ΜΕΓΑΣ",                        // casefold vs lower: final sigma
		"- Straße *(from x)*",                 // ß → ss
		"- ﬄuent",                             // ligature expansion
		"- İstanbul",                          // two-rune lower
		"--- leading dashes and spaces",       // lstrip("- ") is a CHAR SET
		"- trailing attribution *(from a·b)*", // middle dot inside attribution
		"- entry\u00a0*(from nbsp)*",          // NBSP before attribution: \s
		"- entry\u001c*(from fs)*",            // U+001C mid-line
		// U+001C at the EDGE, which is where the leading strip decides:
		// Python's strip removes it, Go's TrimSpace does not, and the
		// leftover then survives lstrip("- ") and changes the key.
		"\u001c- entry *(from x)*",
		"- entry *(from x)*\u001c",
		"- *(from only-attribution)*", // core empties out
		"-",
		"- ",
		"",
		"not a bullet",
		"- ʰΣ",                    // cased+case-ignorable before sigma
		"- MIXED Case ENTRY Here", // plain fold
	}
	var want []string
	runPython(t, t.TempDir(),
		"print(json.dumps([playbook._entry_core(l) for l in json.loads(sys.argv[1])]))",
		&want, lines)

	if len(want) != len(lines) {
		t.Fatalf("CPython returned %d cores for %d lines", len(want), len(lines))
	}
	var differ int
	for i, l := range lines {
		if got := entryCore(l); got != want[i] {
			differ++
			t.Errorf("entryCore(%q)\n got %q\nwant %q", l, got, want[i])
		}
	}
	// Anti-vacuity: if every core came back "" the corpus proves nothing.
	var nonEmpty int
	for _, w := range want {
		if w != "" {
			nonEmpty++
		}
	}
	if nonEmpty < len(lines)/2 {
		t.Fatalf("only %d of %d corpus lines produce a non-empty core; the "+
			"corpus is not exercising the function", nonEmpty, len(lines))
	}
}

// AlarmKey decides whether a re-read REPLACES an entry in place or gets
// appended beside its twin. The Unicode-digit cases are the measured
// divergence this port's DigitClass exists to close.
func TestAlarmKeyMatchesPythons(t *testing.T) {
	lines := []string{
		"- cost high · alarm cost:obs @2026-08-01",
		"- cost high · alarm cost:obs @٢٠٢٦-٠١-٠١", // Arabic-Indic digits
		"- cost high · alarm cost:obs @𑵰𑵱𑵲𑵳-𑵰𑵱-𑵰𑵱", // unicode-16 Nd (Garay)
		"- cost high ·alarm cost:obs @2026-08-01",  // no space after dot
		"- cost high ·\u00a0alarm k @2026-08-01",   // NBSP: Python \s, not Go's
		"- cost high · alarm k\u00a0@2026-08-01",   // NBSP inside the key run
		"- cost high · alarm cost:obs @2026-8-1",   // not \d{2}
		"- durable insight, no alarm at all",
		"- cost · alarm  @2026-08-01", // empty key
		"- x · alarm a)b @2026-08-01", // ) excluded from the key
		// Decisive for the key class: with Python's \s the key run stops
		// at the NBSP and the date never follows, so there is NO alarm.
		// With Go's narrower \s the NBSP is a legal key character and the
		// whole thing matches — opposite verdicts, not a near miss.
		"- x · alarm a\u00a0b @2026-08-01",
		"",
	}
	var want []string
	runPython(t, t.TempDir(),
		"print(json.dumps([playbook.alarm_key(l) for l in json.loads(sys.argv[1])]))",
		&want, lines)

	var matched int
	for i, l := range lines {
		got := AlarmKey(l)
		if got != want[i] {
			t.Errorf("AlarmKey(%q)\n got %q\nwant %q", l, got, want[i])
		}
		if want[i] != "" {
			matched++
		}
	}
	if matched == 0 {
		t.Fatal("no line in this corpus is an alarm at all; the pin proves nothing")
	}
}

// ParseEntries feeds both the injection ranking and the dedup pass, so a
// divergence in `learned` silently reorders what reaches every prompt.
func TestParseEntriesMatchesPythons(t *testing.T) {
	const doc = `# Playbook

## Decomposition

- seed bullet one
- learned bullet *(from evolver:a)*
` + "- attribution then NBSP *(from evolver:b)*\u00a0\n" +
		"- attribution then FS *(from evolver:c)*\u001c\n" + `

## Signals

- a proposed goal

## Custom Section

- bare bullet in a non-seed section

- orphan bullet before any header
`
	type pyEntry struct {
		Section  string `json:"section"`
		Line     string `json:"line"`
		Core     string `json:"core"`
		Learned  bool   `json:"learned"`
		Position int    `json:"position"`
	}
	var want []pyEntry
	runPython(t, t.TempDir(),
		"print(json.dumps(playbook.parse_entries(json.loads(sys.argv[1]))))",
		&want, doc)

	got := ParseEntries(doc)
	if len(got) != len(want) {
		t.Fatalf("got %d entries, CPython found %d", len(got), len(want))
	}
	if len(want) == 0 {
		t.Fatal("CPython parsed no entries; the fixture is not a playbook")
	}
	var learnedSeen bool
	for i := range want {
		if got[i].Section != want[i].Section || got[i].Line != want[i].Line ||
			got[i].Core != want[i].Core || got[i].Learned != want[i].Learned ||
			got[i].Position != want[i].Position {
			t.Errorf("entry %d differs\n go: %+v\n py: %+v", i, got[i], want[i])
		}
		if want[i].Learned {
			learnedSeen = true
		}
	}
	// Both provenance verdicts must appear or the `learned` rule is untested.
	if !learnedSeen {
		t.Fatal("no entry in the fixture is learned; the provenance rule is untested")
	}
}

// Inject's budget is documented as a real contract and every length in it
// is a CODE-POINT count. This sweeps a range of budgets rather than a few
// hand-picked ones: the first version used five fixed values, and adding
// two entries to the fixture silently moved the byte-vs-rune divergence
// out from under all five. A pin whose coverage depends on the fixture
// happening to land on a boundary is not a pin.
func TestInjectMatchesPythons(t *testing.T) {
	ws := t.TempDir()
	// Entries chosen so the two rankings and the two length units are
	// distinguishable: a learned PAIR in one section (so reversing the
	// learned list is visible), a long non-ASCII entry, a non-ASCII
	// SECTION NAME (header cost in runes != bytes), and a Signals entry
	// that must never be injected.
	const setup = "playbook.seed_playbook();" +
		"playbook.append_to_playbook('Prefer the cheap path', section='Cost', source='evolver:a');" +
		"playbook.append_to_playbook('Second cost note, appended later', section='Cost', source='evolver:a2');" +
		"playbook.append_to_playbook('ΟΔΟΣ ΜΕΓΑΣ — a long Greek entry that costs more runes than bytes', section='Quality', source='evolver:b');" +
		"playbook.append_to_playbook('entry under a wide header', section='Ποιότητα', source='evolver:d');" +
		"playbook.append_to_playbook('a proposed goal', section='Signals', source='evolver:c');"

	// The low end is not decoration: Go's Inject once substituted a default
	// budget for any max_chars <= 0, which Python does not do (adversarial
	// r9 LOW). The sweep started at 40 and never saw it.
	budgets := []int{-1000, -1, 0, 1, 2, 3, 5, 10, 20, 30, 39}
	for b := 40; b <= 1400; b += 10 {
		budgets = append(budgets, b)
	}

	var want []string
	runPython(t, ws, setup+
		"print(json.dumps([playbook.inject_playbook(max_chars=b) "+
		"for b in json.loads(sys.argv[1])]))", &want, budgets)
	if len(want) != len(budgets) {
		t.Fatalf("CPython returned %d results for %d budgets", len(want), len(budgets))
	}

	text, err := os.ReadFile(filepath.Join(ws, "playbook.md"))
	if err != nil {
		t.Fatal(err)
	}
	goWS := t.TempDir()
	if err := os.WriteFile(filepath.Join(goWS, "playbook.md"), text, 0o644); err != nil {
		t.Fatal(err)
	}

	var bad, nonEmpty, distinct int
	seen := map[string]bool{}
	for i, b := range budgets {
		got := Inject(goWS, b)
		if got != want[i] {
			bad++
			if bad <= 5 {
				t.Errorf("Inject at %d chars differs from CPython's\n got %q\nwant %q",
					b, got, want[i])
			}
		}
		if want[i] != "" {
			nonEmpty++
		}
		if !seen[want[i]] {
			seen[want[i]] = true
			distinct++
		}
		// The budget is a contract in BOTH runtimes; asserting it on the
		// shared expectation means a wrong-but-agreeing pair still fails.
		// A non-positive budget is exempt: nothing can fit in it, so the
		// comparison is vacuously true and says nothing either way. What
		// those budgets are here to pin is the EQUALITY above.
		if n := runeLen(want[i]); n > b && b > 0 {
			t.Errorf("CPython itself overflowed the budget: %d runes > %d — "+
				"the contract this pin asserts does not hold", n, b)
		}
	}
	if bad > 5 {
		t.Errorf("... and %d more budgets differ", bad-5)
	}
	// Anti-vacuity, both directions: the sweep must actually produce
	// output, and must produce DIFFERENT output as the budget moves —
	// otherwise every comparison is the same comparison.
	if nonEmpty < len(budgets)/2 {
		t.Fatalf("only %d of %d budgets produced any injection; the fixture "+
			"is not exercising the selector", nonEmpty, len(budgets))
	}
	if distinct < 5 {
		t.Fatalf("the sweep produced only %d distinct injections across %d "+
			"budgets; it is not crossing any selection boundary",
			distinct, len(budgets))
	}
	t.Logf("%d budgets, %d distinct injections", len(budgets), distinct)
}

func runeLen(s string) int { return utf8.RuneCountInString(s) }
