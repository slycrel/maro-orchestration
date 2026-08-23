package pytext

import (
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
	"unicode"
)

// repr() output is STORED PROSE: it goes into SKILL.md (skills/export_md.go),
// into provenance reasons, into orch error rows. All three are read back by
// Python. So the pin is differential — CPython produces the answer — and the
// case table is shared rather than transcribed.

// pythonRepr asks CPython for repr() of each input.
func pythonRepr(t *testing.T, in []string) []string {
	t.Helper()
	payload, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c",
		"import json,sys; print(json.dumps([repr(s) for s in json.load(sys.stdin)]))")
	cmd.Stdin = strings.NewReader(string(payload))
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	var got []string
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	return got
}

var reprCases = []string{
	"plain",
	"it's",                     // quote flips to "
	`say "hi"`,                 // stays '
	`both ' and "`,             // both present: stays ', ' escaped
	`back\slash`,               // backslash escaped in the ' branch
	"it's a \\ mix",            // THE BUG: " branch must still escape \
	"it's a \x01 control",      // " branch must still escape controls
	"tab\tnl\ncr\r",            // short forms
	"\x00\x07\x0b\x0c\x1b\x1f", // \xXX, not raw
	"\x7f",                     // DEL is Cc
	"nbsp soft­hyph",           // Zs and Cf below 0x100 -> \xXX
	"zwsp​sep",                 // Cf above 0xff -> \uXXXX
	// Non-printables in [0x100, 0x1000): the ONLY shape where \u's
	// zero-padding is observable, since anything smaller takes the \x
	// branch. U+061C is ARABIC LETTER MARK (Cf) and U+0378 is unassigned
	// (Cn); both need four hex digits and "%x" alone yields three. A
	// mutation battery found this — every other non-printable in this table
	// is at or above U+1000, so the padding never mattered.
	"arabic mark ؜ here",
	"unassigned ͸ here",
	"  ",                // Zl and Zp
	"astral \U0001d173", // Cf above 0xffff -> \UXXXXXXXX
	"private ",         // Co
	"emoji 🎉 and é",     // printable, stays raw
	"",
	" ",   // the one printable Zs
	"\\",  // lone backslash
	"''",  // no " present -> stays ', both escaped
	"\"'", // both -> stays '
}

func TestReprIsByteIdenticalToCPythons(t *testing.T) {
	want := pythonRepr(t, reprCases)
	for i, in := range reprCases {
		if got := Repr(in); got != want[i] {
			t.Errorf("Repr(%q)\n got %s\nwant %s (CPython)", in, got, want[i])
		}
	}
}

// The two defects r5 named, as their own assertions, so a regression says
// WHICH one came back rather than just "differs from CPython".
func TestTheDoubleQuoteBranchStillEscapes(t *testing.T) {
	got := Repr("it's a \\ backslash and a \x01 control")
	if !strings.Contains(got, `\\`) {
		t.Errorf("the backslash is bare in the double-quote branch: %s", got)
	}
	if strings.ContainsRune(got, 0x01) {
		t.Errorf("a raw control character reached the output: %q", got)
	}
}

// A raw newline inside a quoted SKILL.md value ends the value early at
// Python's loader. This is the consequence test.
func TestNoRawControlCharacterEverReachesTheOutput(t *testing.T) {
	for r := rune(0); r < 0x400; r++ {
		out := Repr("x" + string(r) + "y")
		for _, o := range out {
			if o < 0x20 || o == 0x7f {
				t.Fatalf("Repr(U+%04X) emitted a raw control character: %q", r, out)
			}
		}
	}
}

// IsPrintable must agree with CPython everywhere except the measured,
// one-directional table skew. Asserting the DIRECTION is the point: Go
// escaping a character Python prints is a spelling difference that parses
// back identically, while Go printing one Python escapes would put a raw
// unassigned code point into a shared file.
func TestPrintabilityDivergesOnlyInTheSafeDirection(t *testing.T) {
	if unicode.Version == "" {
		t.Fatal("no unicode version")
	}
	out, err := exec.Command("python3", "-c",
		"import sys;sys.stdout.write(''.join('1' if chr(c).isprintable() else '0' "+
			"for c in range(0x110000)))").Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	if len(out) != 0x110000 {
		t.Fatalf("got %d flags, want %d", len(out), 0x110000)
	}
	goOnly, pyOnly := 0, 0
	for c := 0; c < 0x110000; c++ {
		py := out[c] == '1'
		switch g := IsPrintable(rune(c)); {
		case g && !py:
			goOnly++
		case !g && py:
			pyOnly++
		}
	}
	if goOnly != 0 {
		t.Errorf("%d code points Go prints raw and CPython escapes — a raw "+
			"unassigned code point in a shared file, not a spelling difference",
			goOnly)
	}
	if pyOnly == 0 {
		t.Errorf("the table skew has closed (Go unicode %s); the divergence "+
			"note in Repr's doc comment is now wrong", unicode.Version)
	}
	t.Logf("Go unicode %s: %d code points escaped by Go and printed by "+
		"CPython (spelling only — \\uXXXX parses back identically)",
		unicode.Version, pyOnly)
}
