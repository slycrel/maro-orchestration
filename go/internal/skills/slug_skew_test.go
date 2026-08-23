package skills

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"unicode"
)

// Slugify turns a skill NAME into a FILENAME. Two runtimes disagreeing about
// one code point means the same skill lands in two different files — the
// case the function's own doc calls worse than not writing it at all. So the
// word class is swept against CPython's \w rather than reasoned about, and
// the end-to-end slug is compared against Python's real _slugify.

// pythonWordFlags returns, for every code point, whether CPython's \w
// matches it.
func pythonWordFlags(t *testing.T) []bool {
	t.Helper()
	out, err := exec.Command("python3", "-c",
		"import re,sys;w=re.compile(r'\\w');"+
			"sys.stdout.write(''.join('1' if w.match(chr(c)) else '0' "+
			"for c in range(0x110000)))").Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	if len(out) != 0x110000 {
		t.Fatalf("got %d flags, want %d", len(out), 0x110000)
	}
	flags := make([]bool, 0x110000)
	for c, b := range out {
		flags[c] = b == '1'
	}
	return flags
}

// The sweep. A code point survives slugDropRE iff it is a word character, a
// space character or "-"; the space class is transcribed from CPython in
// pySpaceClass and already pinned elsewhere, so this isolates the word half.
func TestTheSlugWordClassIsCPythonsWordClass(t *testing.T) {
	want := pythonWordFlags(t)
	var dropped, kept int
	var firstDropped, firstKept string
	for c := 0; c < 0x110000; c++ {
		if c >= 0xd800 && c <= 0xdfff {
			continue // not encodable
		}
		r := rune(c)
		if !want[c] {
			// Non-word: only the space class and "-" may survive, and those
			// are not this test's subject.
			if !slugDropRE.MatchString(string(r)) && r != '-' &&
				!strings.ContainsRune(" \t\n\v\f\r", r) &&
				!unicode.IsSpace(r) && !(r >= 0x1c && r <= 0x1f) {
				kept++
				if firstKept == "" {
					firstKept = fmt.Sprintf("U+%04X", c)
				}
			}
			continue
		}
		if slugDropRE.MatchString(string(r)) {
			dropped++
			if firstDropped == "" {
				firstDropped = fmt.Sprintf("U+%04X", c)
			}
		}
	}
	if dropped != 0 {
		t.Errorf("%d code points CPython keeps in a slug and this runtime "+
			"strips (first: %s) — the same skill name yields two filenames; "+
			"extend pyWordSupplement", dropped, firstDropped)
	}
	if kept != 0 {
		t.Errorf("%d code points this runtime keeps and CPython strips "+
			"(first: %s) — the divergence has reversed", kept, firstKept)
	}
}

// When Go's table catches up, pyWordSupplement is dead code that still looks
// load-bearing.
func TestTheWordSupplementIsStillCarryingWeight(t *testing.T) {
	live, total := 0, 0
	for _, rg := range pyWordSupplement {
		for r := rg[0]; r <= rg[1]; r++ {
			total++
			if !unicode.IsLetter(r) && !unicode.IsNumber(r) {
				live++
			}
		}
	}
	if live == 0 {
		t.Errorf("Go unicode %s now knows every code point in "+
			"pyWordSupplement; the table is dead code and its doc comment "+
			"is wrong", unicode.Version)
	}
	t.Logf("pyWordSupplement covers %d of %d code points Go's table still "+
		"misses (Go unicode %s)", live, total, unicode.Version)
}

// pythonSlugify runs the REAL skill_loader._slugify over each name, so the
// pin is against the code that will read these filenames rather than against
// a re-implementation of it.
func pythonSlugify(t *testing.T, names []string) []string {
	t.Helper()
	src, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(src, "skill_loader.py")); err != nil {
		t.Skipf("Python source tree not present: %v", err)
	}
	payload, err := json.Marshal(names)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c",
		"import json,sys;from skill_loader import _slugify;"+
			"print(json.dumps([_slugify(n) for n in json.load(sys.stdin)]))")
	cmd.Stdin = strings.NewReader(string(payload))
	cmd.Env = append(os.Environ(), "PYTHONPATH="+src)
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("python3 / skill_loader unavailable: %v", err)
	}
	var got []string
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	return got
}

// End to end, on names rather than code points, because the slug is the
// composition of Lower, the drop class and the space collapse — and the two
// table supplements have to compose with each other.
func TestSlugifyIsByteIdenticalToPythons(t *testing.T) {
	names := []string{
		"Plain Skill Name",
		"café-résumé!",
		"日本語 skill",
		"emoji 🎉 here",
		"a.b/c",
		"  leading and trailing  ",
		"under_scores  and   spaces",
		"---dashes---",
		"!!!",                     // slugs to nothing -> unnamed_skill
		"İstanbul deploy",         // U+0130: the two-rune lowercase
		"GARAY \U00010D50 skill",  // Unicode 16 uppercase: lower + word class
		"todhri \U000105C0 skill", // Unicode 16 letter, word class only
		"cjk-ext-i \U0002EBF0",    // the 622-code-point block
		"hieroglyph \U00013460",   // the 3,995-code-point block
		"ol onal \U0001E5D0",
		"sunuwar \U00011BC0 and digits \U00011BF0",
		"nbsp separated",
		"infoseparator",
	}
	want := pythonSlugify(t, names)
	for i, n := range names {
		if got := Slugify(n); got != want[i] {
			t.Errorf("Slugify(%q)\n got %q\nwant %q (CPython _slugify)",
				n, got, want[i])
		}
	}
}
