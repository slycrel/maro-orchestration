package pytext

import (
	"bufio"
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
	"unicode"
)

// The whole rune range, both directions. A table built by measurement can
// still go stale — Go's Unicode version moves with the toolchain and
// CPython's moves with the interpreter — and the failure mode is silent,
// because a supplement that is one entry short looks exactly like a
// supplement that is complete.
//
// This is the guard lowerSupplement has, aimed at Upper: it re-derives the
// answer from the LIVE python3 instead of trusting the generated table, so
// upgrading either runtime fails here by name rather than shipping a
// string of the wrong LENGTH into a filename or an offset.
func TestUpperAgreesWithCPythonOnEveryCodePoint(t *testing.T) {
	if testing.Short() {
		t.Skip("sweeps 0x110000 code points through a python3 subprocess")
	}
	cmd := exec.Command("python3", "-c",
		"import sys\n"+
			"for c in range(0x110000):\n"+
			"    if 0xD800 <= c <= 0xDFFF: continue\n"+
			"    u = chr(c).upper()\n"+
			"    if u != chr(c):\n"+
			"        sys.stdout.write('%d %s\\n' % (c, u.encode('unicode_escape').decode()))\n")
	out, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not start: %v", err)
	}

	mapped := map[rune]bool{}
	rows, bad, expansions := 0, 0, 0
	sc := bufio.NewScanner(out)
	sc.Buffer(make([]byte, 0, 1<<16), 1<<20)
	for sc.Scan() {
		line := sc.Text()
		sp := strings.IndexByte(line, ' ')
		if sp < 0 {
			t.Fatalf("probe line has no separator: %q", line)
		}
		var c int
		if _, err := fmtSscan(line[:sp], &c); err != nil {
			t.Fatalf("probe line %q: %v", line, err)
		}
		want, uerr := unescape(line[sp+1:])
		if uerr != nil {
			t.Fatalf("probe line %q: %v", line, uerr)
		}
		rows++
		mapped[rune(c)] = true
		if len([]rune(want)) > 1 {
			expansions++
		}
		if got := Upper(string(rune(c))); got != want {
			bad++
			if bad <= 12 {
				t.Errorf("U+%04X: Upper gave %q, CPython gives %q", c, got, want)
			}
		}
	}
	if err := sc.Err(); err != nil {
		t.Fatal(err)
	}
	if err := cmd.Wait(); err != nil {
		t.Fatalf("the CPython probe died: %v", err)
	}
	if bad > 12 {
		t.Errorf("...and %d more", bad-12)
	}

	// The OTHER direction, which the probe cannot report: a rune Go
	// uppercases and CPython leaves alone. Nothing hits this today, and
	// that is a measurement rather than an assumption.
	for c := rune(0); c < 0x110000; c++ {
		if c >= 0xD800 && c <= 0xDFFF || mapped[c] {
			continue
		}
		if got := Upper(string(c)); got != string(c) {
			t.Errorf("U+%04X: CPython leaves it alone, Upper gave %q", c, got)
		}
	}

	// Vacuity floors. A probe that emits nothing, or a Python whose
	// upper() stopped expanding, would pass every assertion above.
	if rows < 1400 {
		t.Fatalf("probe reported only %d uppercasing code points; too few "+
			"to be sweeping the range", rows)
	}
	if expansions < 90 {
		t.Fatalf("only %d of %d mappings are multi-rune expansions — the "+
			"SpecialCasing half is what Go structurally cannot do, and a "+
			"sweep that does not reach it is not testing Upper", expansions, rows)
	}
}

// Upper is only ever reached through code that ALSO has a fast path, and a
// fast path is where a table like this gets bypassed. This pins the two
// halves against each other on strings rather than single runes.
func TestUpperFastPathAgreesWithTheSlowOne(t *testing.T) {
	pool := []rune{'a', 'i', 'I', 0x00DF, 0x0130, 0x0131, 0x03A3, 0x03C2,
		0x00B5, 0xFB01, 0x0149, ' ', '-', 'Z'}
	slow := func(s string) string {
		var b strings.Builder
		for _, r := range s {
			if u, ok := upperSupplement[r]; ok {
				b.WriteString(u)
				continue
			}
			b.WriteRune(unicode.ToUpper(r))
		}
		return b.String()
	}
	seed := uint32(2463534242)
	next := func(n int) int {
		seed ^= seed << 13
		seed ^= seed >> 17
		seed ^= seed << 5
		return int(seed % uint32(n))
	}
	var tookFast, tookSlow int
	for i := 0; i < 20000; i++ {
		var b strings.Builder
		for j := 0; j < 1+next(6); j++ {
			b.WriteRune(pool[next(len(pool))])
		}
		s := b.String()
		if strings.ContainsFunc(s, inUpperSupplement) {
			tookSlow++
		} else {
			tookFast++
		}
		if got, want := Upper(s), slow(s); got != want {
			t.Fatalf("Upper(%q) = %q, per-rune path gives %q", s, got, want)
		}
	}
	if tookFast == 0 || tookSlow == 0 {
		t.Fatalf("only one path was exercised: fast=%d slow=%d", tookFast, tookSlow)
	}
}

// The length claim in Upper's comment, made mechanical. If a future
// "simplification" swaps the body for strings.ToUpper, every assertion
// above about single runes still passes for the 27 skew entries — this is
// the one that does not.
func TestUpperChangesLengthWhereToUpperCannot(t *testing.T) {
	// In RUNES, not bytes. "straße" is the trap: ß is two bytes and S is
	// one, so STRASSE and STRAßE are both 7 bytes and a byte-length
	// assertion passes on the unexpanded answer.
	for _, s := range []string{"straße", "ﬁnal", "ŉ"} {
		got, plain := []rune(Upper(s)), []rune(strings.ToUpper(s))
		if len(got) <= len(plain) {
			t.Errorf("%q: Upper gave %q (%d runes), ToUpper gave %q (%d runes) "+
				"— the expansion is gone", s, string(got), len(got),
				string(plain), len(plain))
		}
	}
	// ...and that CPython agrees these three are the expanding kind.
	out, err := exec.Command("python3", "-c",
		"import json,sys;print(json.dumps([s.upper() for s in json.loads(sys.argv[1])]))",
		`["straße","ﬁnal","ŉ"]`).Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatal(err)
	}
	for i, s := range []string{"straße", "ﬁnal", "ŉ"} {
		if got := Upper(s); got != want[i] {
			t.Errorf("%q: go %q py %q", s, got, want[i])
		}
	}
}

func fmtSscan(s string, out *int) (int, error) {
	n := 0
	for _, c := range s {
		if c < '0' || c > '9' {
			return 0, errNotANumber
		}
		n = n*10 + int(c-'0')
	}
	*out = n
	return 1, nil
}

var errNotANumber = errString("probe emitted a non-numeric code point")

type errString string

func (e errString) Error() string { return string(e) }

// unescape reverses Python's `unicode_escape` for the shapes this probe
// can emit: \xNN, \uNNNN, \UNNNNNNNN and a literal backslash. Anything
// else is an error rather than a guess, because a silently mis-decoded
// expected value is a test that passes for the wrong reason.
func unescape(s string) (string, error) {
	var b strings.Builder
	for i := 0; i < len(s); {
		if s[i] != '\\' {
			b.WriteByte(s[i])
			i++
			continue
		}
		if i+1 >= len(s) {
			return "", errString("trailing backslash")
		}
		var width int
		switch s[i+1] {
		case 'x':
			width = 2
		case 'u':
			width = 4
		case 'U':
			width = 8
		case '\\':
			b.WriteByte('\\')
			i += 2
			continue
		default:
			return "", errString("unhandled escape " + s[i:i+2])
		}
		if i+2+width > len(s) {
			return "", errString("short escape " + s[i:])
		}
		var v rune
		for _, c := range s[i+2 : i+2+width] {
			var d rune
			switch {
			case c >= '0' && c <= '9':
				d = c - '0'
			case c >= 'a' && c <= 'f':
				d = c - 'a' + 10
			case c >= 'A' && c <= 'F':
				d = c - 'A' + 10
			default:
				return "", errString("bad hex digit in " + s[i:i+2+width])
			}
			v = v*16 + d
		}
		b.WriteRune(v)
		i += 2 + width
	}
	return b.String(), nil
}
