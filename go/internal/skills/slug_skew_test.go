package skills

import (
	"fmt"
	"strings"
	"testing"
	"unicode"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
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
	out := []byte(pyprobe.Probe{Stdlib: true}.Run(t,
		"import re,sys;w=re.compile(r'\\w');"+
			"sys.stdout.write(''.join('1' if w.match(chr(c)) else '0' "+
			"for c in range(0x110000)))"))
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
			"extend pytext's wordSupplement", dropped, firstDropped)
	}
	if kept != 0 {
		t.Errorf("%d code points this runtime keeps and CPython strips "+
			"(first: %s) — the divergence has reversed", kept, firstKept)
	}
}

// When Go's table catches up, the supplement is dead code that still looks
// load-bearing. Asserted here as well as in pytext because this package is
// where a stale supplement writes a skill to two different filenames.
func TestTheWordSupplementIsStillCarryingWeight(t *testing.T) {
	live, total := 0, 0
	for _, rg := range pytext.WordSupplementRanges() {
		for r := rg[0]; r <= rg[1]; r++ {
			total++
			if !unicode.IsLetter(r) && !unicode.IsNumber(r) {
				live++
			}
		}
	}
	if live == 0 {
		t.Errorf("Go unicode %s now knows every code point in "+
			"pytext's wordSupplement; the table is dead code and its doc "+
			"comment is wrong", unicode.Version)
	}
	t.Logf("pyWordSupplement covers %d of %d code points Go's table still "+
		"misses (Go unicode %s)", live, total, unicode.Version)
}

// pythonSlugify runs the REAL skill_loader._slugify over each name, so the
// pin is against the code that will read these filenames rather than against
// a re-implementation of it.
func pythonSlugify(t *testing.T, names []string) []string {
	t.Helper()
	var got []string
	pyprobe.Probe{Marker: "skill_loader.py"}.RunJSON(t,
		"import json,sys;from skill_loader import _slugify;"+
			"print(json.dumps([_slugify(n) for n in json.loads(sys.argv[1])]))",
		&got, pyprobe.Arg(t, names))
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
		// Final_Sigma. Slugify lowercases before it slugifies, so a
		// context-sensitive lowercase mapping lands directly on the
		// filename — one skill, two files, one per runtime. Every case
		// above is context-FREE, which is why the whole table was green
		// against a Slugify that got "ΟΔΟΣ" wrong (adversarial r6).
		"ΟΔΟΣ",         // word-final: -> odos, not odoσ
		"ΟΔΟΣ ΜΕΓΑΣ",   // two of them, with a space between
		"ΑΣΣ deploy",   // medial then final in one word
		"aΣ'",          // trailing case-ignorable stays final
		"Σ alone",      // nothing cased before it: stays σ
		"ΑΣ\U00010D50", // the cased supplement decides this one
		// These two need a CASED rune AFTER the mark to discriminate.
		// Without one the scan runs off the end of the string and every
		// spelling of the tables yields ς, so the fixtures were labelled
		// for tables that did not decide their outcome — verified by
		// mutating each table and watching this test stay green
		// (adversarial r8 LOW). The real per-code-point coverage is in
		// pytext's sweep; what was missing here was the composition
		// through Slugify, which is where the divergence lands on a
		// FILENAME.
		"ΑΣࢗΑ",          // case-ignorable supplement: skip the mark, hit Α
		"ΑΣ\U0001171EΑ", // the exclusion: stop at the mark, do not reach Α
	}
	want := pythonSlugify(t, names)
	for i, n := range names {
		if got := Slugify(n); got != want[i] {
			t.Errorf("Slugify(%q)\n got %q\nwant %q (CPython _slugify)",
				n, got, want[i])
		}
	}
}
