package playbook

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Curation differentials. Same rule as the rest of this package: the
// expectation is whatever CPython's playbook.py actually does, produced by
// running it, never by reconstructing it in Go.

// curateCorpus is shared by the dedup and validity pins. Every entry is
// here because it separates a correct port from a plausible one; a corpus
// of ordinary ASCII bullets would pass against half a dozen wrong
// implementations.
var curateCorpus = []struct {
	name string
	text string
}{
	{"plain duplicates", "## Cost\n\n- reuse the cache\n- reuse the cache\n- ship it\n"},
	{"attribution differs, core does not",
		"## Cost\n\n- reuse the cache *(from run-1)*\n- reuse the cache *(from run-2)*\n"},
	{"casefold, not lower: the sharp s expands",
		"## Quality\n\n- STRASSE rules\n- straße rules\n"},
	{"casefold, not lower: final sigma has no context rule",
		"## Quality\n\n- ΟΔΟΣ matters\n- οδος matters\n- οδοσ matters\n"},
	{"a bullet indented with U+001F is a bullet to lstrip and not to Go",
		"## Execution\n\n\u001f- indented twin\n- indented twin\n"},
	{"a bullet indented with an ordinary space",
		"## Execution\n\n  - spaced twin\n- spaced twin\n"},
	{"U+2028 is a line break to splitlines and not to split(\"\\n\")",
		"## Cost\n\n- alpha\u2028- alpha\n- alpha\n"},
	{"a lone carriage return is likewise not a split point",
		"## Cost\n\n- beta\r- beta\n- beta\n"},
	{"non-bullet lines pass through untouched, duplicates included",
		"## Notes\n\nsame prose\nsame prose\n\n- x\n- x\n"},
	{"an empty core does not seed the seen set",
		"## Cost\n\n- *(from a)*\n- *(from b)*\n- real\n"},
	{"the dash prefix needs its space",
		"## Cost\n\n-nospace\n-nospace\n- withspace\n- withspace\n"},
	{"an empty document", ""},
	{"a document that is only a newline", "\n"},
}

func TestDedupTextMatchesPythons(t *testing.T) {
	ws := t.TempDir()
	type pyResult struct {
		Text    string `json:"text"`
		Removed int    `json:"removed"`
	}
	changed := 0
	for _, tc := range curateCorpus {
		t.Run(tc.name, func(t *testing.T) {
			var want pyResult
			runPython(t, ws,
				"text=json.loads(sys.argv[1]);"+
					"t,r=playbook._dedup_text(text);"+
					"print(json.dumps({'text':t,'removed':r}))",
				&want, tc.text)

			gotText, gotRemoved := dedupText(tc.text)
			if gotText != want.Text {
				t.Errorf("text differs\n go: %q\n py: %q", gotText, want.Text)
			}
			if gotRemoved != want.Removed {
				t.Errorf("removed: go %d, py %d", gotRemoved, want.Removed)
			}
			if want.Removed > 0 {
				changed++
			}
		})
	}
	// A corpus where nothing is ever removed would pass against a dedup
	// that does nothing at all.
	if changed < 6 {
		t.Fatalf("only %d corpus entries actually removed a bullet; this "+
			"corpus does not exercise dedup", changed)
	}
}

// validityCorpus pairs an old document with a candidate rewrite. The
// interesting axis is WHICH rule rejects, so both accepted and rejected
// candidates are here and the pin counts each kind.
var validityCorpus = []struct {
	name    string
	old     string
	cand    string
	rejects bool
}{
	{"a genuine tightening is accepted",
		"## Cost\n\n- one long verbose bullet about caching\n- another long verbose bullet\n- a third\n",
		"## Cost\n\n- cache\n- reuse\n", false},
	{"an empty candidate is rejected", "## Cost\n\n- a\n", "", true},
	{"a whitespace-only candidate is rejected", "## Cost\n\n- a\n", "   \n\t ", true},
	{"a candidate that grows past 1.1x is rejected",
		"## Cost\n\n- a\n", "## Cost\n\n- a\n- padding padding padding padding\n", true},
	{"a candidate exactly at the 1.1x boundary",
		strings.Repeat("- x\n", 25), strings.Repeat("- x\n", 27), false},
	{"a dropped header is rejected",
		"## Cost\n\n- a\n\n## Quality\n\n- b\n", "## Cost\n\n- a\n- b\n", true},
	{"a header renamed to a superstring does not preserve it",
		"## Cost\n\n- a\n- b\n- c\n", "## Costly\n\n- a\n- b\n", true},
	{"a duplicated header may not collapse",
		"## Cost\n\n- a\n\n## Cost\n\n- b\n- c\n", "## Cost\n\n- a\n- b\n", true},
	{"a dropped attribution is rejected",
		"## Cost\n\n- a *(from run-1)*\n- b\n- c\n", "## Cost\n\n- a\n- b\n", true},
	{"two identical attributions must both survive",
		"## Cost\n\n- a *(from r)*\n- b *(from r)*\n- c\n",
		"## Cost\n\n- a *(from r)*\n- c\n", true},
	{"bullets may fall to the ceil floor but no further",
		"## Cost\n\n- a\n- b\n- c\n", "## Cost\n\n- a\n- b\n", false},
	{"three bullets down to one is below the floor",
		"## Cost\n\n- a\n- b\n- c\n", "## Cost\n\n- a\n", true},
	{"one bullet down to one is at the floor",
		"## Cost\n\n- a\n", "## Cost\n\n- a\n", false},
	// Measured, and surprising enough to be worth a fixture: a playbook
	// with NO bullets can never pass compression. The floor is
	// max(1, ceil(0*0.6)) = 1, and the candidate has zero bullets, so an
	// identical rewrite of a prose-only document is rejected.
	{"a prose-only document cannot pass the floor at all",
		"## Cost\n\nprose\n", "## Cost\n\nprose\n", true},
	// Both directions of the code-point-vs-byte ceiling, chosen so each
	// one FLIPS if the lengths are measured in bytes. The CJK candidate is
	// 135 code points against a 150-code-point original (accepted) but 243
	// bytes against 150 (a byte ceiling rejects it); the ASCII candidate is
	// 169 code points against 144 (rejected) but 169 bytes against 396 (a
	// byte ceiling accepts it).
	{"the ceiling counts code points: a CJK rewrite that byte-length rejects",
		strings.Repeat("- ab\n", 30), strings.Repeat("- 日本\n", 27), false},
	{"the ceiling counts code points: an ASCII rewrite that byte-length accepts",
		strings.Repeat("- 日本語テキストです\n", 12),
		strings.Repeat("- abcdefghij\n", 13), true},
	{"an indented U+001F bullet counts on the Python side",
		"## Cost\n\n- a\n- b\n- c\n- d\n- e\n",
		"## Cost\n\n\u001f- a\n\u001f- b\n\u001f- c\n", false},
	// The header counter strips each LINE before testing the prefix, with
	// Python's 29-code-point strip. Put the U+001F on the CANDIDATE's
	// header: a 25-code-point strip stops counting it, the original's
	// header then has no survivor, and the rewrite is rejected where
	// Python accepts it.
	//
	// The indented header must NOT be the first line. _valid_compression
	// opens with `new.strip()`, which eats a leading U+001F outright — a
	// first-line fixture exercises that strip and never reaches the header
	// counter at all. (It did not, for one battery round; the mutant
	// survived and the fixture read as if it covered this.)
	{"a candidate header indented with U+001F still counts",
		"# Top\n\n## Cost\n\n- a\n", "# Top\n\n\u001f## Cost\n\n- a\n", false},
}

func TestValidCompressionMatchesPythons(t *testing.T) {
	ws := t.TempDir()
	accepted, rejected := 0, 0
	for _, tc := range validityCorpus {
		t.Run(tc.name, func(t *testing.T) {
			var want bool
			runPython(t, ws,
				"old=json.loads(sys.argv[1]);new=json.loads(sys.argv[2]);"+
					"print(json.dumps(playbook._valid_compression(old,new)))",
				&want, tc.old, tc.cand)

			if want != !tc.rejects {
				t.Fatalf("the fixture's own label is wrong: CPython says "+
					"valid=%v, the table says rejects=%v", want, tc.rejects)
			}
			if got := validCompression(tc.old, tc.cand); got != want {
				t.Errorf("valid: go %v, py %v", got, want)
			}
			if want {
				accepted++
			} else {
				rejected++
			}
		})
	}
	// A corpus that only rejects passes against `return false`, and one
	// that only accepts passes against `return true`.
	if accepted < 4 || rejected < 4 {
		t.Fatalf("corpus is one-sided: %d accepted, %d rejected",
			accepted, rejected)
	}
}

// curateWorkspace lays down a workspace both runtimes will resolve to,
// with a config that pins the two knobs curation reads so the test does
// not inherit whatever this box's real config says.
func curateWorkspace(t *testing.T, doc string, minChars int) string {
	t.Helper()
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", filepath.Join(t.TempDir(), "user"))
	cfg := "playbook:\n  curation_enabled: true\n  alarm_ttl_days: 14\n" +
		"  curation_min_chars: " + itoa(minChars) + "\n"
	if err := os.WriteFile(filepath.Join(ws, "config.yml"), []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ws, "playbook.md"), []byte(doc), 0o644); err != nil {
		t.Fatal(err)
	}
	return ws
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		return "-" + string(b)
	}
	return string(b)
}

// pyCurateResult mirrors curate_playbook's return dict plus the resulting
// file, so one probe pins both the value the caller sees and the bytes the
// next reader sees.
type pyCurateResult struct {
	Stats *struct {
		RemovedDuplicates int      `json:"removed_duplicates"`
		ExpiredAlarms     []string `json:"expired_alarms"`
		LLMCompressed     bool     `json:"llm_compressed"`
		Archived          string   `json:"archived"`
		CharsBefore       int      `json:"chars_before"`
		CharsAfter        int      `json:"chars_after"`
	} `json:"stats"`
	File     string   `json:"file"`
	Archives []string `json:"archives"`
}

const pyCurateSnippet = `
doc=json.loads(sys.argv[1])
import pathlib
p=playbook._playbook_path()
st=playbook.curate_playbook(force=True)
if st is not None:
    st=dict(st); st['archived']=pathlib.Path(st['archived']).name
d=playbook._history_dir()
arch=sorted(x.name for x in d.iterdir()) if d.exists() else []
print(json.dumps({'stats':st,
                  'file':p.read_text(encoding='utf-8'),
                  'archives':[ (d/a).read_text(encoding='utf-8') for a in arch ]}))
`

// The deterministic path — no adapter on either side — is the one that
// runs on every dream cycle, and it rewrites the whole document. It is
// compared byte for byte, including the archive it must leave behind.
func TestCurateMatchesPythonsFileForFile(t *testing.T) {
	docs := []struct {
		name string
		doc  string
	}{
		{"duplicates across two sections",
			"# Director's Playbook\n\n## Cost\n\n- reuse the cache\n- reuse the cache\n\n" +
				"## Quality\n\n- verify twice\n- verify twice\n- verify twice\n\n" +
				"*Last updated: 2020-01-01*\n"},
		{"nothing to do returns None and does not archive",
			"# Director's Playbook\n\n## Cost\n\n- only one\n\n*Last updated: 2020-01-01*\n"},
		{"a stale alarm expires and a duplicate collapses in the same pass",
			"# Director's Playbook\n\n## Signals\n\n- · alarm disk-full @2001-01-01\n\n" +
				"## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"},
		{"an alarm inside its TTL survives",
			"# Director's Playbook\n\n## Signals\n\n- · alarm disk-full @2999-01-01\n\n" +
				"## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"},
		{"a document with no Last-updated line is not given one",
			"# Director's Playbook\n\n## Cost\n\n- twin\n- twin\n"},
		{"attribution-only differences collapse and keep the FIRST source",
			"# Director's Playbook\n\n## Cost\n\n- lesson *(from run-b)*\n" +
				"- lesson *(from run-a)*\n\n*Last updated: 2020-01-01*\n"},
		{"a casefold twin collapses",
			"# Director's Playbook\n\n## Quality\n\n- ΟΔΟΣ\n- οδος\n\n*Last updated: 2020-01-01*\n"},
	}

	for _, tc := range docs {
		t.Run(tc.name, func(t *testing.T) {
			// Python first, in its own workspace.
			pyWS := curateWorkspace(t, tc.doc, 1<<30)
			var want pyCurateResult
			runPython(t, pyWS, pyCurateSnippet, &want, tc.doc)

			goWS := curateWorkspace(t, tc.doc, 1<<30)
			got := Curate(context.Background(), goWS, nil, nil, true)

			assertCurateAgrees(t, goWS, got, want)
		})
	}
}

// assertCurateAgrees compares the stats, the rewritten document, and the
// archive directory. The archive is compared by CONTENT, not filename: the
// names carry a UTC second stamp and the two runs are seconds apart, so
// comparing names would fail on timing and prove nothing about retention.
func assertCurateAgrees(t *testing.T, goWS string, got *CurateStats, want pyCurateResult) {
	t.Helper()

	if (got == nil) != (want.Stats == nil) {
		t.Fatalf("one runtime curated and the other did not: go=%v py=%v",
			got != nil, want.Stats != nil)
	}

	gotFileB, err := os.ReadFile(Path(goWS))
	if err != nil {
		t.Fatal(err)
	}
	if string(gotFileB) != want.File {
		t.Errorf("playbook differs\n go: %q\n py: %q", gotFileB, want.File)
	}

	goArch := readArchives(t, goWS)
	if len(goArch) != len(want.Archives) {
		t.Fatalf("archive count: go %d, py %d", len(goArch), len(want.Archives))
	}
	for i := range goArch {
		if goArch[i] != want.Archives[i] {
			t.Errorf("archive %d differs\n go: %q\n py: %q",
				i, goArch[i], want.Archives[i])
		}
	}

	if got == nil {
		return
	}
	if got.RemovedDuplicates != want.Stats.RemovedDuplicates {
		t.Errorf("removed_duplicates: go %d, py %d",
			got.RemovedDuplicates, want.Stats.RemovedDuplicates)
	}
	if strings.Join(got.ExpiredAlarms, "|") !=
		strings.Join(want.Stats.ExpiredAlarms, "|") {
		t.Errorf("expired_alarms: go %v, py %v",
			got.ExpiredAlarms, want.Stats.ExpiredAlarms)
	}
	if got.LLMCompressed != want.Stats.LLMCompressed {
		t.Errorf("llm_compressed: go %v, py %v",
			got.LLMCompressed, want.Stats.LLMCompressed)
	}
	if got.CharsBefore != want.Stats.CharsBefore {
		t.Errorf("chars_before: go %d, py %d",
			got.CharsBefore, want.Stats.CharsBefore)
	}
	if got.CharsAfter != want.Stats.CharsAfter {
		t.Errorf("chars_after: go %d, py %d",
			got.CharsAfter, want.Stats.CharsAfter)
	}
	if base := filepath.Base(got.Archived); base != want.Stats.Archived {
		// Names carry a second stamp; only the SHAPE can be compared.
		if archiveShape(base) != archiveShape(want.Stats.Archived) {
			t.Errorf("archive name shape: go %q, py %q",
				base, want.Stats.Archived)
		}
	}
}

// archiveShape replaces the timestamp run with a placeholder so two names
// minted seconds apart compare equal while a changed PREFIX or REASON
// still fails.
func archiveShape(name string) string {
	parts := strings.SplitN(name, "-", 3)
	if len(parts) != 3 {
		return name
	}
	return parts[0] + "-<ts>-" + parts[2]
}

func readArchives(t *testing.T, ws string) []string {
	t.Helper()
	dir := filepath.Join(ws, "playbook_history")
	ents, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	names := make([]string, 0, len(ents))
	for _, e := range ents {
		names = append(names, e.Name())
	}
	sortStrings(names)
	out := make([]string, 0, len(names))
	for _, n := range names {
		b, err := os.ReadFile(filepath.Join(dir, n))
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, string(b))
	}
	return out
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

// TestTheCurateCorpusActuallyRewritesTheFile is the anti-vacuity guard for
// the differential above: if Curate became `return nil`, every case with a
// non-nil expectation would still compare two unchanged files and pass.
func TestTheCurateCorpusActuallyRewritesTheFile(t *testing.T) {
	doc := "# P\n\n## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"
	ws := curateWorkspace(t, doc, 1<<30)
	got := Curate(context.Background(), ws, nil, nil, true)
	if got == nil {
		t.Fatal("Curate reported no change on a document with an exact duplicate")
	}
	after, err := os.ReadFile(Path(ws))
	if err != nil {
		t.Fatal(err)
	}
	if string(after) == doc {
		t.Fatal("Curate returned stats but the file is unchanged")
	}
	if len(readArchives(t, ws)) != 1 {
		t.Fatal("the pre-curation version was not archived")
	}
	if got.CharsBefore <= got.CharsAfter {
		t.Fatalf("dedup did not shrink the document: %d → %d",
			got.CharsBefore, got.CharsAfter)
	}
}

// The compare-and-swap exists because the lock is deliberately dropped
// across the LLM round trip. If another writer lands in that window, this
// pass must be discarded rather than clobber their entry.
//
// The window is staged through the afterSnapshot seam rather than with
// goroutines: a pin that races two writers tests the scheduler, and it
// passes on a machine where the guard is missing but the timing never
// lines up. This one fires the intrusion at exactly the moment the guard
// exists to survive.
func TestCurateDiscardsItsPassWhenTheFileChangedUnderneath(t *testing.T) {
	doc := "# P\n\n## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"
	intruded := doc + "\n- someone else's entry\n"

	ws := curateWorkspace(t, doc, 1<<30)
	old := afterSnapshot
	afterSnapshot = func() {
		if err := os.WriteFile(Path(ws), []byte(intruded), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	defer func() { afterSnapshot = old }()

	if got := Curate(context.Background(), ws, nil, nil, true); got != nil {
		t.Fatalf("curation rewrote a file that changed under it: %+v", got)
	}
	after, err := os.ReadFile(Path(ws))
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != intruded {
		t.Fatalf("the intruder's entry was clobbered\n got: %q\nwant: %q",
			after, intruded)
	}
	if n := len(readArchives(t, ws)); n != 0 {
		t.Fatalf("a discarded pass archived %d version(s); it must not "+
			"touch the history directory at all", n)
	}

	// Control: with no intrusion the same document IS curated, so the
	// assertions above are reporting the guard and not an inert Curate.
	afterSnapshot = old
	ws2 := curateWorkspace(t, doc, 1<<30)
	if got := Curate(context.Background(), ws2, nil, nil, true); got == nil {
		t.Fatal("control arm: an unintruded document was not curated")
	}
}

// Curation must ABORT when it cannot archive: never rewrite what you
// cannot restore. Blocking the history directory with a regular FILE is
// the cheapest way to make mkdir fail for real, rather than faking the
// failure through a seam that the production path might not use.
func TestCurationRefusesToRewriteWhenItCannotArchive(t *testing.T) {
	doc := "# P\n\n## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"
	ws := curateWorkspace(t, doc, 1<<30)
	if err := os.WriteFile(filepath.Join(ws, "playbook_history"),
		[]byte("not a directory"), 0o644); err != nil {
		t.Fatal(err)
	}

	if got := Curate(context.Background(), ws, nil, nil, true); got != nil {
		t.Errorf("curation reported success with no archive: %+v", got)
	}
	after, err := os.ReadFile(Path(ws))
	if err != nil {
		t.Fatal(err)
	}
	if string(after) != doc {
		t.Errorf("the document was rewritten with no restorable copy\n"+
			" got: %q\nwant: %q", after, doc)
	}

	// Control: the same document with the history directory unblocked IS
	// curated, so the assertions above report the refusal and not an
	// inert Curate.
	ws2 := curateWorkspace(t, doc, 1<<30)
	if got := Curate(context.Background(), ws2, nil, nil, true); got == nil {
		t.Fatal("control arm: an unblocked workspace was not curated")
	}
}

// force overrides the config gate in both runtimes — the evolver's manual
// "curate now" path must not be silently ignored on a box that has
// curation switched off.
func TestForceOverridesTheCurationGateTheWayPythonsDoes(t *testing.T) {
	doc := "# P\n\n## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"
	disabled := "playbook:\n  curation_enabled: false\n  curation_min_chars: 1073741824\n"

	for _, tc := range []struct {
		name  string
		force bool
	}{
		{"forced, gate off: curates", true},
		{"unforced, gate off: does nothing", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			pyWS := curateWorkspace(t, doc, 1<<30)
			if err := os.WriteFile(filepath.Join(pyWS, "config.yml"),
				[]byte(disabled), 0o644); err != nil {
				t.Fatal(err)
			}
			var want pyCurateResult
			runPython(t, pyWS,
				"doc=json.loads(sys.argv[1])\nimport pathlib\n"+
					"p=playbook._playbook_path()\n"+
					"st=playbook.curate_playbook(force="+
					map[bool]string{true: "True", false: "False"}[tc.force]+")\n"+
					"if st is not None:\n"+
					"    st=dict(st); st['archived']=pathlib.Path(st['archived']).name\n"+
					"d=playbook._history_dir()\n"+
					"arch=sorted(x.name for x in d.iterdir()) if d.exists() else []\n"+
					"print(json.dumps({'stats':st,"+
					"'file':p.read_text(encoding='utf-8'),"+
					"'archives':[(d/a).read_text(encoding='utf-8') for a in arch]}))",
				&want, doc)

			goWS := curateWorkspace(t, doc, 1<<30)
			if err := os.WriteFile(filepath.Join(goWS, "config.yml"),
				[]byte(disabled), 0o644); err != nil {
				t.Fatal(err)
			}
			got := Curate(context.Background(), goWS, nil, nil, tc.force)

			// The two arms must DISAGREE, or the gate is not being read at
			// all and both assertions below are vacuous.
			if tc.force != (want.Stats != nil) {
				t.Fatalf("CPython ignored the gate: force=%v curated=%v",
					tc.force, want.Stats != nil)
			}
			assertCurateAgrees(t, goWS, got, want)
		})
	}
}
