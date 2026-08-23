package playbook

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The pins from adversarial r10. Four of the six findings here were
// SURVIVING MUTANTS on correct production code — the code did the right
// thing and nothing in the suite would have noticed it stopping. The
// fifth (newlines) was a real divergence that twelve differentials missed
// for one reason: every fixture in this package is LF-only.
//
// That is the shape worth naming. `curateCorpus` even contains a lone
// carriage return ("a lone carriage return is likewise not a split
// point"), and it pins the truth about `_dedup_text` — which is handed a
// STRING. End to end the claim inverts, because by the time
// curate_playbook reaches that helper the READ has already turned the CR
// into a newline. A corpus that only ever enters through a helper cannot
// see what the file boundary does.

// pyReadNewlines reports what CPython's Path.read_text does to each
// candidate separator, so the Go translation is measured rather than
// assumed.
const pyReadNewlines = `
import json,sys,pathlib
p = pathlib.Path(sys.argv[1])
assert str(p).startswith('/tmp/'), 'REFUSING: '+str(p)
out = {}
for name, raw in json.loads(sys.argv[2]).items():
    b = raw.encode('utf-8', 'surrogatepass')
    p.write_bytes(b)
    out[name] = p.read_text(encoding='utf-8')
print(json.dumps(out))
`

// runPythonAt is runPython for a probe that needs a plain FILE rather
// than a workspace: it never imports playbook, so it carries its own
// /tmp/ assertion in the snippet above instead of the module's.
func runPythonAt(t *testing.T, path, snippet string, out any, args ...any) {
	t.Helper()
	if !strings.HasPrefix(path, "/tmp/") && !strings.HasPrefix(path, os.TempDir()) {
		t.Fatalf("refusing to run a writing probe against %q", path)
	}
	argv := []string{"-c", snippet, path}
	for _, a := range args {
		b, err := json.Marshal(a)
		if err != nil {
			t.Fatal(err)
		}
		argv = append(argv, string(b))
	}
	cmd := exec.Command("python3", argv...)
	res, err := cmd.Output()
	if err != nil {
		// Same rule as runPython: a MISSING interpreter skips, a FAILING
		// probe is fatal. A skip here would hide the /tmp/ assert firing.
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe FAILED (exit %d):\n%s",
				ee.ExitCode(), ee.Stderr)
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

// Universal newlines translate CRLF and a lone CR, and NOTHING else. A Go
// port that "normalised whitespace" instead would diverge in the opposite
// direction, so the negative half of this pin is the load-bearing half.
func TestDecodeTextTranslatesExactlyWhatReadTextDoes(t *testing.T) {
	cases := map[string]string{
		"crlf":             "a\r\nb",
		"lone cr":          "a\rb",
		"cr at eof":        "a\r",
		"double cr":        "a\r\rb",
		"crlf then cr":     "a\r\n\rb",
		"lfcr is two":      "a\n\rb",
		"vertical tab":     "a\vb",
		"form feed":        "a\fb",
		"file separator":   "a\x1cb",
		"group separator":  "a\x1db",
		"record separator": "a\x1eb",
		"unit separator":   "a\x1fb",
		// U+2028/U+2029/U+0085 are line breaks to str.splitlines() but NOT
		// to universal newlines, so they are the sharpest negative cases in
		// the corpus: a port that reused a splitlines-shaped notion of "line
		// break" fails exactly here.
		"line separator":    "a\u2028b",
		"para separator":    "a\u2029b",
		"next line":         "a\u0085b",
		"nbsp":              "a\u00a0b",
		"no separator":      "plain",
		"cr inside a token": "## Cost\r\n\n- x\n",

		// decodeText returns EARLY when the input holds no '\r', so a
		// mutant that also folds some other separator is unreachable
		// through any CR-free case — and every negative case above is
		// CR-free. Mutation caught exactly that: folding "\v" or U+2028
		// survived the corpus until these pairs existed. Each carries a
		// CR (to get past the fast path) AND a second separator that must
		// come back untouched.
		"cr with a vertical tab":   "a\r\nb\vc",
		"cr with a form feed":      "a\rb\fc",
		"cr with a file separator": "a\rb\x1cc",
		"cr with a unit separator": "a\rb\x1fc",
		"cr with a line separator": "a\rb\u2028c",
		"cr with a para separator": "a\rb\u2029c",
		"cr with a next line":      "a\rb\u0085c",
		"cr with an nbsp":          "a\rb\u00a0c",
	}

	dir := t.TempDir()
	if !strings.HasPrefix(dir, "/tmp/") && !strings.HasPrefix(dir, os.TempDir()) {
		t.Fatalf("refusing to write to %q", dir)
	}
	var want map[string]string
	runPythonAt(t, filepath.Join(dir, "nl.txt"), pyReadNewlines, &want, cases)

	if len(want) != len(cases) {
		t.Fatalf("CPython returned %d results for %d cases", len(want), len(cases))
	}
	translated := 0
	for name, raw := range cases {
		got := decodeText([]byte(raw))
		if got != want[name] {
			t.Errorf("%s: go %q, py %q", name, got, want[name])
		}
		if want[name] != raw {
			translated++
		}
	}
	// Anti-vacuity: if CPython translated nothing, decodeText could be
	// `return string(b)` and every case above would agree.
	if translated == 0 {
		t.Fatal("CPython translated none of the corpus; this pin would pass " +
			"against a decodeText that does nothing")
	}
	// ...and the mirror: if it translated EVERYTHING, the negative cases
	// are not actually negative and a whitespace fold would pass too.
	if translated == len(cases) {
		t.Fatal("CPython translated every case; the corpus has no negative " +
			"half, so a decodeText that folds all whitespace would pass")
	}
}

// The end-to-end half. A CR reaching the FILE is what twelve differentials
// missed, and the consequence was structural rather than cosmetic: Go grew
// a second "## Cost" header, because sectionSpan's `[ \t]*$` cannot match
// "## Cost\r" and insertEntry therefore created a section that already
// existed.
func TestACarriageReturnInTheFileDoesNotForkTheDocument(t *testing.T) {
	const crlfSeed = "# Operational Playbook\r\n\r\n## Cost\r\n\r\n" +
		"- watch spend *(from run-a)*\r\n\r\n" +
		"## Quality\r\n\r\n- lone\rsplit bullet\r\n\r\n" +
		"*Last updated: 2020-01-01*\r\n"

	for _, tc := range []struct {
		name string
		doc  string
	}{
		{"a fully CRLF document", crlfSeed},
		{"a lone CR mid-bullet",
			"# P\n\n## Cost\n\n- alpha\r- alpha\n- gamma\n\n*Last updated: 2020-01-01*\n"},
		{"a CR on the section header itself",
			"# P\n\n## Cost\r\n\n- alpha\n\n*Last updated: 2020-01-01*\n"},
		{"a CR at end of file", "# P\n\n## Cost\n\n- alpha\n\n*Last updated: 2020-01-01*\r"},
		{"no CR at all (the control)",
			"# P\n\n## Cost\n\n- alpha\n- alpha\n\n*Last updated: 2020-01-01*\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const entry = "a later note"

			pyWS := curateWorkspaceRaw(t, tc.doc, "playbook:\n  alarm_ttl_days: 14\n")
			var want struct {
				File     string `json:"file"`
				Headers  int    `json:"headers"`
				Section  string `json:"section"`
				Injected string `json:"injected"`
			}
			runPython(t, pyWS, `
import json,sys
doc = json.loads(sys.argv[1])
playbook.append_to_playbook(json.loads(sys.argv[2]), section='Cost', source='evolver:z')
p = playbook._playbook_path()
text = p.read_text(encoding='utf-8')
print(json.dumps({
  'file': text,
  'headers': sum(1 for l in text.split('\n') if l.strip() == '## Cost'),
  'section': playbook.section_text('Cost') or '',
  'injected': playbook.inject_playbook(max_chars=800) or '',
}))
`, &want, tc.doc, entry)

			goWS := curateWorkspaceRaw(t, tc.doc, "playbook:\n  alarm_ttl_days: 14\n")
			if err := Append(goWS, nil, entry, "Cost", "evolver:z", ""); err != nil {
				t.Fatalf("Append: %v", err)
			}
			gotB, err := os.ReadFile(Path(goWS))
			if err != nil {
				t.Fatal(err)
			}
			got := string(gotB)

			if got != want.File {
				t.Errorf("the file differs\n go %q\n py %q", got, want.File)
			}
			headers := 0
			for _, l := range strings.Split(got, "\n") {
				if strings.TrimSpace(l) == "## Cost" {
					headers++
				}
			}
			if headers != want.Headers {
				t.Errorf("## Cost headers: go %d, py %d — a forged section "+
					"re-orders injection and changes _valid_compression's "+
					"occurrence-counted header rule", headers, want.Headers)
			}
			if s := SectionText(goWS, "Cost"); s != want.Section {
				t.Errorf("SectionText: go %q, py %q", s, want.Section)
			}
			if inj := Inject(goWS, 800); inj != want.Injected {
				t.Errorf("Inject: go %q, py %q", inj, want.Injected)
			}
		})
	}
}

// The READ-ONLY verbs, on a CR-bearing file that nothing has rewritten.
//
// TestACarriageReturnInTheFileDoesNotForkTheDocument calls Append FIRST,
// and Append normalises and rewrites the whole document — so every
// assertion after it reads an already-clean file. Mutation proved the
// cost: reverting Load and ExpireStaleAlarms to raw byte reads survived
// the entire suite, because no test ever asked a read verb about a CR
// document it had not just cleaned.
//
// This is the same defect shape as the finding it pins. A fixture whose
// earlier step destroys the condition its later steps test looks like
// thorough coverage and is closer to none.
func TestTheReadVerbsNormaliseWithoutAWriteFirst(t *testing.T) {
	for _, tc := range []struct {
		name string
		doc  string
	}{
		{"a lone CR between two bullets",
			"# P\n\n## Cost\n\n- alpha\r- beta *(from x)*\n\n*Last updated: 2020-01-01*\n"},
		{"a CR on the section header",
			"# P\n\n## Cost\r\n\n- alpha *(from x)*\n\n*Last updated: 2020-01-01*\n"},
		{"a fully CRLF document",
			"# P\r\n\r\n## Cost\r\n\r\n- alpha *(from x)*\r\n\r\n*Last updated: 2020-01-01*\r\n"},
		{"no CR at all (the control)",
			"# P\n\n## Cost\n\n- alpha *(from x)*\n\n*Last updated: 2020-01-01*\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ws := curateWorkspaceRaw(t, tc.doc, "playbook:\n  alarm_ttl_days: 14\n")
			var want struct {
				Loaded   string `json:"loaded"`
				Section  string `json:"section"`
				Injected string `json:"injected"`
			}
			runPython(t, ws, `
import json,sys
print(json.dumps({
  'loaded': playbook.load_playbook() or '',
  'section': playbook.section_text('Cost') or '',
  'injected': playbook.inject_playbook(max_chars=800) or '',
}))
`, &want, tc.doc)

			if got := Load(ws); got != want.Loaded {
				t.Errorf("Load\n go %q\n py %q", got, want.Loaded)
			}
			if got := SectionText(ws, "Cost"); got != want.Section {
				t.Errorf("SectionText\n go %q\n py %q", got, want.Section)
			}
			if got := Inject(ws, 800); got != want.Injected {
				t.Errorf("Inject\n go %q\n py %q", got, want.Injected)
			}

			// The file must be untouched: these are READ verbs, and Load
			// seeds only when the document is absent.
			after, err := os.ReadFile(Path(ws))
			if err != nil {
				t.Fatal(err)
			}
			if string(after) != tc.doc {
				t.Errorf("a read verb rewrote the file\n got %q\n want %q",
					after, tc.doc)
			}
		})
	}
}

// ExpireStaleAlarms REWRITES, so it gets its own CR fixture rather than
// riding the read-only one above. Its raw-read mutant also survived.
func TestExpireStaleAlarmsNormalisesTheDocumentItRewrites(t *testing.T) {
	for _, tc := range []struct {
		name string
		doc  string
	}{
		{"an expirable alarm behind a lone CR",
			"# P\n\n## Signals\n\n- old *(from s · alarm k:a @2001-01-01)*\r" +
				"- fresh *(from s · alarm k:b @2099-01-01)*\n\n" +
				"*Last updated: 2020-01-01*\n"},
		{"a CRLF document with an expirable alarm",
			"# P\r\n\r\n## Signals\r\n\r\n- old *(from s · alarm k:a @2001-01-01)*\r\n\r\n" +
				"*Last updated: 2020-01-01*\r\n"},
		{"no CR at all (the control)",
			"# P\n\n## Signals\n\n- old *(from s · alarm k:a @2001-01-01)*\n\n" +
				"*Last updated: 2020-01-01*\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			pyWS := curateWorkspaceRaw(t, tc.doc, "playbook:\n  alarm_ttl_days: 14\n")
			var want struct {
				N    int    `json:"n"`
				File string `json:"file"`
			}
			runPython(t, pyWS, `
import json,sys
n = playbook.expire_stale_alarms(max_age_days=14)
print(json.dumps({
  'n': n,
  'file': playbook._playbook_path().read_text(encoding='utf-8'),
}))
`, &want, tc.doc)

			goWS := curateWorkspaceRaw(t, tc.doc, "playbook:\n  alarm_ttl_days: 14\n")
			if got := ExpireStaleAlarms(goWS, 14); got != want.N {
				t.Errorf("expired: go %d, py %d", got, want.N)
			}
			after, err := os.ReadFile(Path(goWS))
			if err != nil {
				t.Fatal(err)
			}
			if string(after) != want.File {
				t.Errorf("the file differs\n go %q\n py %q", after, want.File)
			}
		})
	}
}

// Curate's whole-document rewrite over the same CR-bearing inputs. This is
// the destructive verb: Python curated documents Go declined to touch,
// because the two were not reading the same text.
func TestCurateAgreesOnCarriageReturnBearingDocuments(t *testing.T) {
	for _, tc := range []struct {
		name string
		doc  string
	}{
		{"duplicates separated by a lone CR",
			"# P\n\n## Cost\n\n- beta\r- beta\n- gamma\n\n*Last updated: 2020-01-01*\n"},
		{"a CRLF document with a real duplicate",
			"# P\r\n\r\n## Cost\r\n\r\n- twin\r\n- twin\r\n\r\n*Last updated: 2020-01-01*\r\n"},
		{"an expirable alarm behind a CR",
			"# P\n\n## Signals\n\n- x · alarm k:a @2001-01-01\r\n\n*Last updated: 2020-01-01*\n"},
		{"no CR at all (the control)",
			"# P\n\n## Cost\n\n- twin\n- twin\n\n*Last updated: 2020-01-01*\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			const cfg = "playbook:\n  alarm_ttl_days: 14\n  curation_min_chars: 1000000\n"

			pyWS := curateWorkspaceRaw(t, tc.doc, cfg)
			var want struct {
				Stats map[string]any `json:"stats"`
				File  string         `json:"file"`
			}
			runPython(t, pyWS, `
import json,sys
st = playbook.curate_playbook(force=True)
print(json.dumps({
  'stats': st,
  'file': playbook._playbook_path().read_text(encoding='utf-8'),
}))
`, &want, tc.doc)

			goWS := curateWorkspaceRaw(t, tc.doc, cfg)
			got := Curate(context.Background(), goWS, nil, nil, true)

			if (got == nil) != (want.Stats == nil) {
				t.Fatalf("curated: go %v, py %v (nil means the pass declined "+
					"to rewrite)", got != nil, want.Stats != nil)
			}
			afterB, err := os.ReadFile(Path(goWS))
			if err != nil {
				t.Fatal(err)
			}
			if string(afterB) != want.File {
				t.Errorf("the rewritten file differs\n go %q\n py %q",
					afterB, want.File)
			}
			if got == nil {
				return
			}
			if float64(got.RemovedDuplicates) != want.Stats["removed_duplicates"] {
				t.Errorf("removed_duplicates: go %d, py %v",
					got.RemovedDuplicates, want.Stats["removed_duplicates"])
			}
		})
	}
}
