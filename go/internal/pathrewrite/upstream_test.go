package pathrewrite

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The tables in this module are LONG — fifty suffixes, twenty-eight
// system roots — and the differential can only sample them. A fixture
// per entry would be a hundred more scenarios proving one thing each;
// reading the literal out of the Python and comparing the whole set
// proves all of them at once, and keeps proving them when upstream adds
// an entry.
//
// This is the same shape as the preflight tranche's prompt guard, and
// for the same reason: a table is a CONTENT key, and a differential that
// exercises the entries it happens to name cannot tell a complete copy
// from a plausible one.

func upstreamSource(t *testing.T) string {
	t.Helper()
	p := filepath.Join(pyprobe.SrcDir(t, "path_rewrite.py"), "path_rewrite.py")
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// literalSet pulls the single-quoted or double-quoted strings out of the
// assignment named by `name`, up to its closing brace or paren.
func literalSet(t *testing.T, src, name, open, close string) []string {
	t.Helper()
	i := strings.Index(src, "\n"+name+" = "+open)
	if i < 0 {
		t.Fatalf("upstream no longer defines %s = %s…", name, open)
	}
	rest := src[i+len(name)+4:]
	j := strings.Index(rest, close)
	if j < 0 {
		t.Fatalf("unterminated %s literal", name)
	}
	body := rest[:j]
	// Strip comments first: the tables are commented per group and a
	// comment can legitimately contain a quoted word.
	lines := []string{}
	for _, ln := range strings.Split(body, "\n") {
		if k := strings.Index(ln, "#"); k >= 0 {
			ln = ln[:k]
		}
		lines = append(lines, ln)
	}
	out := []string{}
	for _, m := range regexp.MustCompile(`"([^"]*)"|'([^']*)'`).
		FindAllStringSubmatch(strings.Join(lines, "\n"), -1) {
		if m[1] != "" {
			out = append(out, m[1])
		} else {
			out = append(out, m[2])
		}
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
	w := map[string]bool{}
	for _, s := range want {
		w[s] = true
	}
	for _, s := range got {
		if !w[s] {
			t.Errorf("%s: port has %q, upstream does not", what, s)
		}
		delete(w, s)
	}
	for _, s := range want {
		if w[s] {
			t.Errorf("%s: upstream has %q, port does not", what, s)
		}
	}
}

func TestTheSkipSuffixTableMatchesUpstream(t *testing.T) {
	src := upstreamSource(t)
	assertSameSet(t, "_SKIP_SUFFIXES",
		literalSet(t, src, "_SKIP_SUFFIXES", "frozenset({", "})"),
		sortedSetKeys(skipSuffixes))
}

func TestTheSystemRootDenylistMatchesUpstream(t *testing.T) {
	src := upstreamSource(t)
	assertSameSet(t, "_SYSTEM_ROOTS",
		literalSet(t, src, "_SYSTEM_ROOTS", "frozenset({", "})"),
		sortedSetKeys(systemRoots))
}

func TestTheVcsDirectoryTableMatchesUpstream(t *testing.T) {
	src := upstreamSource(t)
	assertSameSet(t, "_SKIP_DIR_PARTS",
		literalSet(t, src, "_SKIP_DIR_PARTS", "frozenset({", "})"),
		sortedSetKeys(skipDirParts))
}

func TestTheRolesTupleMatchesUpstream(t *testing.T) {
	src := upstreamSource(t)
	// ROLES is ORDERED, not a set: build_map iterates it, and the order
	// decides which of two equal-length sources is recorded first.
	i := strings.Index(src, "\nROLES = (")
	if i < 0 {
		t.Fatal("upstream no longer defines ROLES = (…)")
	}
	rest := src[i+len("\nROLES = ("):]
	body := rest[:strings.Index(rest, ")")]
	want := []string{}
	for _, m := range regexp.MustCompile(`"([^"]*)"`).
		FindAllStringSubmatch(body, -1) {
		want = append(want, m[1])
	}
	if strings.Join(want, ",") != strings.Join(Roles, ",") {
		t.Errorf("ROLES order: upstream %v, port %v", want, Roles)
	}
}

// TestTheScalarConstantsMatchUpstream reads each number back out of the
// Python rather than trusting the port's copy. A differential cannot see
// these: _SNIFF_BYTES only shows up in a fixture that straddles it, and
// _DEFAULT_MAX_FILE_BYTES only in a 64 MiB one nobody will write.
func TestTheScalarConstantsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	for _, c := range []struct {
		name string
		want string
	}{
		{"_SNIFF_BYTES", "8192"},
		{"_DEFAULT_MAX_FILE_BYTES", "64 * 1024 * 1024"},
		{"_MIN_ROOT_COMPONENTS", "2"},
		{"_MIN_DEPTH_BELOW_SHARED", "2"},
		{"_TMP_SUFFIX", `".maro-rewrite.tmp"`},
	} {
		line := "\n" + c.name + " = " + c.want + "\n"
		if !strings.Contains(src, line) {
			t.Errorf("upstream no longer says %s = %s", c.name, c.want)
		}
	}
	// …and the port's own values, so the test fails if either side moves.
	if sniffBytes != 8192 || DefaultMaxFileBytes != 64*1024*1024 ||
		minRootComponents != 2 || minDepthBelowShared != 2 ||
		TmpSuffix != ".maro-rewrite.tmp" {
		t.Error("a port constant no longer matches its upstream value")
	}
}

// TestTheBoundaryPatternsMatchUpstream is the guard the hand-written scan
// needs most.
//
// Python's boundaries are one compiled pattern; the port's are two
// functions. Nothing structural ties them together, so if upstream widens
// a character class the differential notices only if some fixture happens
// to contain the new character — and the classes here are exactly the
// thing whose edges nobody thinks to test. Pinning the literals means an
// upstream edit fails HERE, naming the pattern, instead of somewhere
// downstream in a workspace that imported wrong.
func TestTheBoundaryPatternsMatchUpstream(t *testing.T) {
	src := upstreamSource(t)
	const wantRight = "_BOUNDARY = rb\"(?![A-Za-z0-9_-])\""
	const wantLeft = "_LEFT_BOUNDARY = (rb\"(?:(?<![A-Za-z0-9_./-])\"\n" +
		"                  rb\"|(?<=\\\\n)|(?<=\\\\t)|(?<=\\\\r)" +
		"|(?<=\\\\f)|(?<=\\\\v)|(?<=\\\\b))\")"
	if !strings.Contains(src, wantRight) {
		t.Errorf("_BOUNDARY changed upstream; rightBoundaryOK implements "+
			"%q", wantRight)
	}
	if !strings.Contains(src, wantLeft) {
		t.Errorf("_LEFT_BOUNDARY changed upstream; leftBoundaryOK "+
			"implements %q", wantLeft)
	}
	// The escape exemption, spelled out: these are the six LETTERS the
	// lookbehind alternation names, and a literal backslash before them.
	for _, c := range []byte("ntrfvb") {
		if !leftBoundaryOK([]byte{'x', '\\', c, '/'}, 3) {
			t.Errorf("a literal backslash-%c is not a left boundary", c)
		}
	}
	for _, c := range []byte("aeimxz0") {
		if leftBoundaryOK([]byte{'x', '\\', c, '/'}, 3) {
			t.Errorf("a literal backslash-%c must not be a left boundary", c)
		}
	}
}
