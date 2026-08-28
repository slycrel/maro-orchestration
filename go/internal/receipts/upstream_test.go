package receipts

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// The tables and the two regexes in this module are CONTENT keys, and a
// differential can only sample them — it exercises the runners and
// extensions a fixture happens to name and cannot tell a complete copy
// from a plausible one. These tests read the literals back out of the
// Python instead, so an upstream edit fails HERE, naming the thing that
// moved.
//
// Same shape as the preflight tranche's prompt guard and pathrewrite's
// table guards, and for the same reason.

func upstreamSource(t *testing.T) string {
	t.Helper()
	p := filepath.Join(pyprobe.SrcDir(t, "execution_receipts.py"),
		"execution_receipts.py")
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// frozenLiteral pulls the quoted strings out of `name = frozenset({…})`.
func frozenLiteral(t *testing.T, src, name string) []string {
	t.Helper()
	i := strings.Index(src, "\n"+name+" = frozenset({")
	if i < 0 {
		t.Fatalf("upstream no longer defines %s = frozenset({…})", name)
	}
	rest := src[i+len("\n"+name+" = frozenset({"):]
	j := strings.Index(rest, "})")
	if j < 0 {
		t.Fatalf("unterminated %s literal", name)
	}
	out := []string{}
	for _, m := range regexp.MustCompile(`"([^"]*)"`).
		FindAllStringSubmatch(rest[:j], -1) {
		out = append(out, m[1])
	}
	sort.Strings(out)
	return out
}

func sortedSetKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func assertSameSet(t *testing.T, what string, want, got []string) {
	t.Helper()
	if strings.Join(want, "\x00") == strings.Join(got, "\x00") {
		return
	}
	t.Errorf("%s: upstream %q, port %q", what, want, got)
}

func TestTheShellToolTableMatchesUpstream(t *testing.T) {
	// The set with the most riding on it: widening it by one name would
	// let a non-executing tool manufacture process evidence, which is the
	// exact hole finding 3 closed.
	assertSameSet(t, "_SHELL_TOOL_NAMES",
		frozenLiteral(t, upstreamSource(t), "_SHELL_TOOL_NAMES"),
		sortedSetKeys(shellToolNames))
}

func TestTheCaptureBackendTableMatchesUpstream(t *testing.T) {
	assertSameSet(t, "_CAPTURE_BACKENDS",
		frozenLiteral(t, upstreamSource(t), "_CAPTURE_BACKENDS"),
		sortedSetKeys(captureBackends))
}

// TestTheScalarBoundsMatchUpstream reads each number back out of the
// Python. A differential can see MAX_RECEIPTS and OUTPUT_HEAD_CHARS, but
// MAX_SCANNED_FILES costs a thousand files to exercise and MAX_FILE_BYTES
// an eight-megabyte one.
func TestTheScalarBoundsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	for _, c := range []struct{ name, want string }{
		{"MAX_RECEIPTS", "400"},
		{"MAX_SCANNED_FILES", "1_000"},
		{"MAX_FILE_BYTES", "8_000_000"},
		{"OUTPUT_HEAD_CHARS", "240"},
		{"MAX_EVIDENCE_CHARS", "2_400"},
		{"MAX_LISTED_RECEIPTS", "8"},
	} {
		if !strings.Contains(src, "\n"+c.name+" = "+c.want+"\n") {
			t.Errorf("upstream no longer says %s = %s", c.name, c.want)
		}
	}
	if MaxReceipts != 400 || MaxScannedFiles != 1000 ||
		MaxFileBytes != 8_000_000 || OutputHeadChars != 240 ||
		MaxEvidenceChars != 2400 || MaxListedReceipts != 8 {
		t.Error("a port bound no longer matches its upstream value")
	}
}

// upstreamPattern reassembles a multi-line `name = re.compile(...)`
// literal from its adjacent-string pieces, the way Python concatenates
// them.
func upstreamPattern(t *testing.T, src, name string) string {
	t.Helper()
	i := strings.Index(src, "\n"+name+" = re.compile(")
	if i < 0 {
		t.Fatalf("upstream no longer defines %s = re.compile(…)", name)
	}
	rest := src[i+len("\n"+name+" = re.compile("):]
	j := strings.Index(rest, ")\n")
	if j < 0 {
		t.Fatalf("unterminated %s literal", name)
	}
	// The pieces are r"…" raw strings, one per line; a `)` inside one
	// would defeat the scan above, and neither pattern has an unbalanced
	// one at the end of a line.
	var b strings.Builder
	for _, m := range regexp.MustCompile(`r"([^"]*)"`).
		FindAllStringSubmatch(rest[:j+1], -1) {
		b.WriteString(m[1])
	}
	if b.Len() == 0 {
		t.Fatalf("%s literal reassembled to nothing", name)
	}
	return b.String()
}

// TestTheProcessMarkerPatternMatchesUpstream ties the port's pattern to
// the original MECHANICALLY, rather than pinning a copy of it.
//
// The port differs from upstream in exactly two ways and they are both
// forced: Go's `\b` is ASCII where Python's is Unicode, so the boundaries
// go through pytext; and RE2 has no capture-group cost model worth
// paying, so the alternation groups are non-capturing. Applying those two
// substitutions to the upstream literal must reproduce the port's pattern
// character for character — which means adding a runner upstream fails
// here with the diff, instead of silently leaving the port narrower.
func TestTheProcessMarkerPatternMatchesUpstream(t *testing.T) {
	up := upstreamPattern(t, upstreamSource(t), "_PROCESS_MARKERS")
	if !strings.HasPrefix(up, `\b`) || !strings.HasSuffix(up, `\b`) {
		t.Fatalf("_PROCESS_MARKERS no longer opens and closes on \\b: %q", up)
	}
	body := strings.TrimSuffix(strings.TrimPrefix(up, `\b`), `\b`)
	want := pytext.WordStart + strings.ReplaceAll(body, "(", "(?:") +
		pytext.WordEnd
	if got := processMarkers.String(); got != want {
		t.Errorf("process markers diverge from upstream\n  want %q\n  got  %q",
			want, got)
	}
}

// TestThePathTokenPatternMatchesUpstream pins the literal and derives the
// extension table from it.
//
// pathTokenExts is not a set: `re` tries the alternatives left to right
// and takes the first that also clears the trailing `\b`, which is why
// `x.jsonl` matches `json`, fails, and backtracks into `jsonl`. Sorting
// the comparison would throw away the one property that matters.
func TestThePathTokenPatternMatchesUpstream(t *testing.T) {
	src := upstreamSource(t)
	const want = `[\w./-]*/[\w./-]+|\b[\w-]+\.(?:py|md|txt|json|jsonl|sh|` +
		`yml|yaml|html)\b`
	i := strings.Index(src, "\n_PATH_TOKEN = re.compile(r\"")
	if i < 0 {
		t.Fatal("upstream no longer defines _PATH_TOKEN = re.compile(r\"…\")")
	}
	rest := src[i+len("\n_PATH_TOKEN = re.compile(r\""):]
	got := rest[:strings.Index(rest, "\")")]
	if got != want {
		t.Fatalf("_PATH_TOKEN changed upstream:\n  pinned %q\n  actual %q",
			want, got)
	}
	// …and the port's table IS the alternation, in order.
	k := strings.Index(got, `\.(?:`)
	exts := strings.Split(got[k+len(`\.(?:`):strings.Index(got, `)\b`)], "|")
	if strings.Join(exts, ",") != strings.Join(pathTokenExts, ",") {
		t.Errorf("extension order: upstream %v, port %v", exts, pathTokenExts)
	}
	// The order is only load-bearing if the scanner honours it, so say so
	// with the case that proves it.
	if toks := pathToken("read calls.jsonl"); len(toks) != 1 ||
		toks[0] != "calls.jsonl" {
		t.Errorf("prefix-extension backtracking broke: %v", toks)
	}
}
