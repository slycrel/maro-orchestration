package skills

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyProvLoadSrc seeds a provenance directory with EXACT BYTES (so a fixture
// can be malformed or byte-tainted) and reports what load_skill_provenance
// returns for each name asked.
const pyProvLoadSrc = `
import json, os, sys
import skills
from orch_items import memory_dir

# Capture the module logger's warnings: the unreadable COUNT is reported
# only there, and without it two runtimes that both return zero records
# look identical while disagreeing about how many files they could not
# read. That was a real MISS — skipping directories dropped them from the
# count and no assertion could see it.
import logging
_seen = []
class _Cap(logging.Handler):
    def emit(self, r):
        _seen.append(r.getMessage())
logging.getLogger("skills").addHandler(_Cap())
logging.getLogger("skills").setLevel(logging.WARNING)

_argv = json.loads(sys.argv[1])
d = memory_dir() / "skill_provenance"
d.mkdir(parents=True, exist_ok=True)
(d / _argv["dir_entry"]).mkdir(exist_ok=True)
for fn, body in _argv["files"].items():
    # A LIST OF BYTE VALUES, not a string. The tainted fixture carries a
    # byte that is not valid UTF-8, and a JSON string cannot transport it —
    # the first cut sent one and Go's encoder replaced it with U+FFFD
    # before CPython ever saw it, which is the very laundering under test.
    (d / fn).write_bytes(bytes(body))
# The same problem one level up: a JSON object KEY cannot carry a byte that
# is not valid UTF-8 either, so a file whose NAME is byte-tainted arrives as
# a [name_bytes, body_bytes] pair instead. os.fsdecode is exactly what
# pathlib holds internally — the surrogateescape decoding whose sort order
# this fixture exists to pin.
for _pair in _argv["byte_files"]:
    (d / os.fsdecode(bytes(_pair[0]))).write_bytes(bytes(_pair[1]))

out = {}
warns = {}
for name in _argv["names"]:
    _seen.clear()
    out[name] = skills.load_skill_provenance(name)
    warns[name] = list(_seen)
print(json.dumps({"records": out, "warnings": warns}, sort_keys=True))
`

// TestLoadSkillProvenanceMatchesCPython pins the reader against the
// interpreter: which files it selects, in what ORDER, and which it drops.
//
// The table's centre of gravity is the glob, because the pattern is built
// by interpolating a skill NAME that nothing escapes. Those rows are not
// hypothetical robustness cases — they are the documented behaviour of a
// shared store, and the port reproduces the leakage on purpose. If someone
// later "fixes" the port by escaping the name, these fail and the change
// has to be argued for instead of appearing quietly.
func TestLoadSkillProvenanceMatchesCPython(t *testing.T) {
	rec := func(name, decision string) string {
		b, _ := json.Marshal(map[string]any{
			"skill_name": name, "decision": decision, "reason": "r",
		})
		return string(b)
	}

	body := func(s string) []int {
		out := make([]int, 0, len(s))
		for i := 0; i < len(s); i++ {
			out = append(out, int(s[i]))
		}
		return out
	}
	files := map[string][]int{
		// Two stamps for one skill: the reverse sort has to put the LATER
		// stamp first, and a forward sort passes every single-file case.
		"skill_x_20260101T000000.json": body(rec("skill_x", "promote")),
		"skill_x_20260202T000000.json": body(rec("skill_x", "demote")),
		"skill_x_20260303T000000.json": body(rec("skill_x", "rewrite")),
		// A different skill, to prove the pattern excludes as well as it
		// includes.
		"other_20260101T000000.json": body(rec("other", "promote")),
		// Names that make the metacharacter cases interesting.
		"ab_20260101T000000.json":   body(rec("ab", "promote")),
		"axb_20260101T000000.json":  body(rec("axb", "promote")),
		"a_20260101T000000.json":    body(rec("a", "promote")),
		"a[b]_20260101T000000.json": body(rec("a[b]", "promote")),
		// Near misses on the SUFFIX: the pattern ends "_*.json", so
		// neither of these may be selected for the name "ab".
		"ab_20260101T000000.txt": body(rec("ab", "promote")),
		"ab.json":                body(rec("ab", "promote")),
		// Malformed and byte-tainted rows, which drive the unreadable
		// count. The tainted one is the NAMED DIVERGENCE: CPython reads it
		// through surrogateescape and returns it; the port counts it.
		"torn_20260101T000000.json":    body(`{"skill_name": "torn"`),
		"torn_20260202T000000.json":    body(rec("torn", "promote")),
		"tainted_20260101T000000.json": body("{\"skill_name\": \"\xff\"}"),
		// A sidecar that is valid JSON but not a mapping.
		"scalar_20260101T000000.json": body(`5`),
		// The two files that separate Python's fnmatch from Go's
		// filepath.Match. Neither existed in the first cut, and swapping
		// the matcher for filepath.Match was a MISS against twelve names.
		//
		//   name `a[b`  — fnmatch reads an unclosed `[` as a LITERAL, so it
		//                 matches this file. filepath.Match returns
		//                 ErrBadPattern and matches nothing.
		//   name `a\b`   — fnmatch reads `\` as a literal backslash and
		//                 matches this file. filepath.Match treats it as an
		//                 ESCAPE, so its pattern is `ab_*.json` and it
		//                 matches the WRONG file.
		"a[b_20260101T000000.json":  body(rec("a[b", "promote")),
		"a\\b_20260101T000000.json": body(rec("a\\b", "promote")),
	}
	// Two sidecars for ONE skill whose names byte order and code-point order
	// disagree about — the surrogateescape rule in the one place it is
	// observable, and the only shape that can tell the two sorts apart.
	//
	//   sk_\x80.json      → pathlib holds "sk_\udc80.json", U+DC80 = 56448
	//   sk_\xc3\xa9.json  → pathlib holds "sk_é.json",      U+00E9 =   233
	//
	// CPython compares code points, so é sorts FIRST and reverse=True puts
	// the bad byte first. A raw-byte sort compares 0x80 against 0xC3 and
	// puts it last. Both orders are self-consistent; only one is CPython's.
	// This is the fixture for the r6 MEDIUM — the site shipped as
	// sort.Sort(sort.Reverse(sort.StringSlice(...))) and no test could see it.
	byteFiles := [][]any{
		{[]int{'s', 'k', '_', 0x80, '.', 'j', 's', 'o', 'n'}, body(rec("sk", "bad-byte"))},
		{[]int{'s', 'k', '_', 0xC3, 0xA9, '.', 'j', 's', 'o', 'n'}, body(rec("sk", "e-acute"))},
	}

	names := []string{
		"skill_x", "other", "nope", "a", "ab", "torn", "scalar",
		// The surrogateescape order case; see byteFiles.
		"sk",
		// See the fixture note: these two discriminate the matcher.
		`a\b`, "adir",
		// The metacharacter names. Each one reads records that are not its
		// own, and `a[b]` misses the file named after it.
		"a*b", "a?b", "a[b]", "a[b", "*",
	}

	// A matching DIRECTORY. Python's glob selects it and read_text raises
	// IsADirectoryError into the bare except, so it lands in the unreadable
	// count rather than being invisible. Both sides create it.
	dirName := "adir_20260101T000000.json"
	arg := map[string]any{"files": files, "names": names, "dir_entry": dirName,
		"byte_files": byteFiles}

	pyWS, goWS := t.TempDir(), t.TempDir()
	var want struct {
		Records  map[string][]json.RawMessage `json:"records"`
		Warnings map[string][]string          `json:"warnings"`
	}
	pyprobe.Probe{
		Marker:    "skills.py",
		Workspace: pyWS,
	}.RunJSON(t, pyProvLoadSrc, &want, pyprobe.Arg(t, arg))

	// Anti-vacuity, twice over. If the ordinary lookup returned nothing,
	// every comparison below would hold for a reader that always answers
	// empty; and if the metacharacter lookup did NOT leak, the rows this
	// test exists for never happened.
	if got := want.Records["skill_x"]; len(got) != 3 {
		t.Fatalf("CPython returned %d records for skill_x, want 3 — the "+
			"fixture never loaded and nothing below is evidence", len(got))
	}
	if got := want.Records["a*b"]; len(got) < 2 {
		t.Fatalf(`CPython returned %d records for the name "a*b"; the glob `+
			`leakage this test pins did not occur`, len(got))
	}
	// The order fixture is only evidence if BOTH byte-named sidecars were
	// selected — with one, every sort agrees and the row is inert.
	if got := want.Records["sk"]; len(got) != 2 {
		t.Fatalf("CPython returned %d records for sk, want 2 — the byte-named "+
			"sidecars did not both land and the sort order is unpinned", len(got))
	}
	if !strings.Contains(string(want.Records["sk"][0]), "bad-byte") {
		t.Fatalf("CPython put %s first for sk; this fixture is written from "+
			"the claim that code-point order puts the LONE SURROGATE first "+
			"under reverse=True, and that claim is what failed",
			want.Records["sk"][0])
	}

	// Seed the Go side with the same bytes.
	dir := filepath.Join(goWS, "memory", "skill_provenance")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(dir, dirName), 0o755); err != nil {
		t.Fatal(err)
	}
	toBytes := func(vs []int) []byte {
		raw := make([]byte, 0, len(vs))
		for _, b := range vs {
			raw = append(raw, byte(b))
		}
		return raw
	}
	for fn, bs := range files {
		if err := os.WriteFile(filepath.Join(dir, fn), toBytes(bs), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// Go strings hold arbitrary bytes, so the byte-tainted NAMES need no
	// decoding step on this side — which is the whole asymmetry under test.
	for _, pair := range byteFiles {
		name := string(toBytes(pair[0].([]int)))
		if err := os.WriteFile(filepath.Join(dir, name),
			toBytes(pair[1].([]int)), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	for _, name := range names {
		t.Run(name, func(t *testing.T) {
			got := LoadSkillProvenance(goWS, name)
			w := want.Records[name]

			// The byte-tainted sidecar is the one named divergence: CPython
			// returns it with a lone surrogate, which no Go string holds.
			// Every name whose match set includes it expects one FEWER
			// record here, and saying so per-name keeps the exemption from
			// covering anything else.
			expect := len(w)
			if matchesTainted(name) {
				expect--
			}
			if len(got.Records) != expect {
				t.Fatalf("%d records, CPython returned %d (expected %d after "+
					"the surrogate divergence)\n go: %v\n py: %v",
					len(got.Records), len(w), expect, got.Records, w)
			}

			// The WARNING, which is where the unreadable COUNT lives.
			// Without this the directory case is invisible: both runtimes
			// return zero records for it and disagree only about how many
			// files they failed to read. It also checks the claim that the
			// port reproduces Python's sentence verbatim — a claim the
			// code asserted in a comment and nothing verified.
			var pyWarn string
			for _, w := range want.Warnings[name] {
				if strings.Contains(w, "load_skill_provenance") {
					pyWarn = w
				}
			}
			gotWarn := got.Warning
			// The port ADDS a glob-metacharacter line, which Python has no
			// counterpart for. Strip it before comparing, and assert
			// separately that it appears exactly when it should.
			meta := strings.ContainsAny(name, "*?[")
			lines := strings.Split(gotWarn, "\n")
			var kept []string
			metaSeen := false
			for _, l := range lines {
				if strings.Contains(l, "carries a glob metacharacter") {
					metaSeen = true
					continue
				}
				if l != "" {
					kept = append(kept, l)
				}
			}
			if metaSeen != meta {
				t.Errorf("metacharacter warning present=%v for name %q, want %v",
					metaSeen, name, meta)
			}
			gotWarn = strings.Join(kept, "\n")
			// The port's dir path is its own workspace's, so compare the
			// sentence with each runtime's directory elided.
			norm := func(x, d string) string { return strings.ReplaceAll(x, d, "<dir>") }
			wantWarn := norm(pyWarn, pyDir(t, pyWS))
			if matchesTainted(name) && wantWarn != "" {
				// The SAME named divergence as the record count, applied to
				// the count in the sentence: CPython reads the byte-tainted
				// sidecar through surrogateescape and returns it, where the
				// port counts it unreadable. Bumping CPython's number here
				// keeps the exemption to exactly one file and leaves every
				// other word of the sentence having to match.
				wantWarn = bumpCount(t, wantWarn)
			}
			if matchesTainted(name) && wantWarn == "" {
				t.Fatalf("CPython logged no warning for %q, so there is no "+
					"count to reconcile against the port's", name)
			}
			if norm(gotWarn, dir) != wantWarn {
				t.Errorf("warning\n go: %q\n py: %q", norm(gotWarn, dir), wantWarn)
			}

			// ORDER and CONTENT, record by record. The comparison walks
			// CPython's list and skips only the tainted entry, so a port
			// that returned the right records in the wrong order fails.
			gi := 0
			for _, wr := range w {
				if isTaintedRecord(wr) {
					continue
				}
				gj, err := pyval.DumpsCompactPy(got.Records[gi])
				if err != nil {
					t.Fatal(err)
				}
				if !sameJSON(t, []byte(gj), wr) {
					t.Errorf("record %d\n go: %s\n py: %s", gi, gj, wr)
				}
				gi++
			}
		})
	}
}

// matchesTainted reports whether the tainted fixture is in this name's
// match set. Spelled out rather than derived, so the exemption is a listed
// set of names and not a rule that could quietly widen.
func matchesTainted(name string) bool {
	return name == "tainted" || name == "*"
}

// isTaintedRecord spots the record CPython returns for the byte-tainted
// sidecar — its skill_name is a lone surrogate, which is the one thing no
// well-formed fixture here contains.
func isTaintedRecord(raw json.RawMessage) bool {
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return false
	}
	s, _ := m["skill_name"].(string)
	for _, r := range s {
		if r == 0xFFFD || (r >= 0xDC80 && r <= 0xDCFF) {
			return true
		}
	}
	return false
}

// pyDir is the provenance directory inside the CPython probe's workspace —
// the same path skills.py resolves, so the two warning sentences can be
// compared with each runtime's own directory elided.
func pyDir(t *testing.T, ws string) string {
	t.Helper()
	return filepath.Join(ws, "memory", "skill_provenance")
}

// bumpCount adds one to the file count in a load_skill_provenance warning,
// which is how the surrogate divergence shows up in the sentence.
func bumpCount(t *testing.T, msg string) string {
	t.Helper()
	m := warnCountRe.FindStringSubmatch(msg)
	if m == nil {
		t.Fatalf("cannot find the file count in %q", msg)
	}
	n, err := strconv.Atoi(m[1])
	if err != nil {
		t.Fatal(err)
	}
	return warnCountRe.ReplaceAllString(msg, ": "+strconv.Itoa(n+1)+" provenance")
}

var warnCountRe = regexp.MustCompile(`: (\d+) provenance`)
