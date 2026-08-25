package pack

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyExportFidelitySrc exports the seeded workspace with CPython and reports
// the member BYTES, the manifest, and REVIEW.md — or, when the export
// refuses, the exception class and message.
//
// The refusal branch is not defensive padding. `read_text(encoding="utf-8")`
// on a skill file is not inside a try in CPython (only the include_runs loop
// catches UnicodeDecodeError, deliberately, because run artifacts may be
// binary), so a byte-tainted workspace ABORTS the whole export there. A
// probe that could only report success would have no way to express that,
// and the port's old `continue` — ship a pack silently short one artifact —
// would look like agreement.
const pyExportFidelitySrc = `
import json, sys, tarfile
from pathlib import Path
import pack

_argv = json.loads(sys.argv[1])
try:
    res = pack.export_pack(name="t", label="src",
                           workspace=Path(_argv["ws"]), out_dir=Path(_argv["out"]),
                           denylist=[], home="/nonexistent-home",
                           hostname="nonexistent-host",
                           include_playbook=True)
except Exception as exc:
    print(json.dumps({"raised": type(exc).__name__}))
    raise SystemExit(0)

path = Path(res["pack_path"])
with tarfile.open(path, "r:gz") as tar:
    members = {}
    for n in tar.getnames():
        b = tar.extractfile(n).read()
        # LATIN-1, not utf-8: this transports the exact BYTES through JSON
        # without the decoder inventing U+FFFD. Comparing "utf-8 with
        # replace" is comparing two lossy renderings, and the losses are
        # exactly what several of these fixtures are about.
        members[n] = b.decode("latin-1")
    manifest = json.loads(tar.extractfile("pack.json").read().decode("utf-8"))

print(json.dumps({
    "members": members,
    "artifacts": [{"path": a["path"], "rows": a.get("rows"),
                   "sha256": a["sha256"]} for a in manifest["artifacts"]],
    "quarantined": sum(a.get("quarantined_rows_skipped", 0)
                       for a in manifest["artifacts"]),
}, sort_keys=True))
`

// exportProbe runs both exporters over identical seeds and hands back the
// CPython side plus the Go archive.
type exportProbe struct {
	Members     map[string]string `json:"members"`
	Raised      string            `json:"raised"`
	Quarantined int               `json:"quarantined"`
	Artifacts   []struct {
		Path   string `json:"path"`
		Rows   *int   `json:"rows"`
		SHA256 string `json:"sha256"`
	} `json:"artifacts"`
}

func runExportBoth(t *testing.T, seed func(ws string)) (exportProbe, map[string][]byte, error) {
	t.Helper()
	pyWS, goWS := t.TempDir(), t.TempDir()
	seed(pyWS)
	seed(goWS)

	var want exportProbe
	pyprobe.Probe{Marker: "pack.py", Workspace: pyWS}.RunJSON(
		t, pyExportFidelitySrc, &want,
		pyprobe.Arg(t, map[string]any{"ws": pyWS, "out": t.TempDir()}))

	res, err := Export(ExportOpts{Name: "t", Label: "src", Workspace: goWS,
		OutDir: t.TempDir(), Denylist: []string{}, IncludePlaybook: true,
		Home: "/nonexistent-home", Hostname: "nonexistent-host"})
	if err != nil {
		return want, nil, err
	}
	members, rerr := readArchive(res.PackPath)
	if rerr != nil {
		t.Fatal(rerr)
	}
	return want, members, nil
}

// seedWriter is the small helper every seed below shares.
func seedWriter(t *testing.T, ws string) func(rel, content string) {
	t.Helper()
	return func(rel, content string) {
		p := filepath.Join(ws, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}

// TestExportKeepsQuarantinedRowsOutWhenTheRowCarriesNaN is the provenance
// gate at the one input that used to defeat it.
//
// `_quarantined_row` decides whether a `minted_from="prompt"` lesson ships.
// It is `json.loads` in CPython, which accepts the bare NaN token — a token
// CPython's own json.dumps WRITES for a non-finite float — and was
// encoding/json here, which refuses it. The refusal returned false, the
// predicate said "not quarantined", and the prompt-derived row shipped: the
// db37d525 contamination class travelling by transport, which is the exact
// thing drop_quarantined exists to prevent.
func TestExportKeepsQuarantinedRowsOutWhenTheRowCarriesNaN(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			`{"lesson_id": "q1", "lesson": "quarantined one", `+
				`"minted_from": "prompt", "score": NaN}`+"\n"+
				// The same stamp behind a DUPLICATE key. json.loads keeps the
				// LAST value and quarantines; a first-wins lookup ships it.
				`{"lesson_id": "q2", "lesson": "quarantined twice", `+
				`"minted_from": "outcome", "minted_from": "prompt"}`+"\n"+
				`{"lesson_id": "ok", "lesson": "ordinary", `+
				`"minted_from": "outcome"}`+"\n")
	}
	want, members, err := runExportBoth(t, seed)
	if err != nil {
		t.Fatal(err)
	}
	assertMembersMatch(t, members, want)
	if want.Quarantined != 2 {
		t.Fatalf("the fixture proves nothing unless CPython quarantines both "+
			"rows; it reported %d", want.Quarantined)
	}
}

// TestExportTranslatesNewlinesLikeReadText: `Path.read_text(encoding="utf-8")`
// opens with newline=None, so a CRLF skill file is LF before the scrubber
// sees it. The port read raw bytes, so the member bytes, the artifact
// sha256, REVIEW.md and the payload digest all differed — a Go-exported pack
// containing any CRLF skill could not verify in Python, and vice versa.
func TestExportTranslatesNewlinesLikeReadText(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("skills/crlf.md", "# Title\r\nbody line\r\n")
		// A LONE \r, which is a separator to read_text and to splitlines
		// but not to anything spelled with "\r\n".
		w("personas/cr.md", "one\rtwo\r")
		w("playbook.md", "step\r\nnext\n")
	}
	want, members, err := runExportBoth(t, seed)
	if err != nil {
		t.Fatal(err)
	}
	assertMembersMatch(t, members, want)
}

// TestExportRefusesAByteTaintedArtifactLikeCPython: a skill file that is not
// UTF-8 aborts the export in CPython. The port used to skip the file and
// ship a pack that LOOKED complete, which is the worse of the two answers —
// an operator cannot see an artifact that is not there.
func TestExportRefusesAByteTaintedArtifactLikeCPython(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("skills/good.md", "# Fine\n")
		w("skills/tainted.md", "caf\xe9 latte\n") // Latin-1 é
	}
	want, _, err := runExportBoth(t, seed)
	if want.Raised != "UnicodeDecodeError" {
		t.Fatalf("the fixture proves nothing unless CPython refuses; it "+
			"reported raised=%q", want.Raised)
	}
	if err == nil {
		t.Fatal("the port exported a workspace CPython refuses — the pack " +
			"ships silently short one artifact")
	}
	if !strings.Contains(err.Error(), "utf-8") {
		t.Errorf("the refusal must name the decode failure, got: %v", err)
	}
}

// TestExportRendersRedactedLinesBySplitlines pins reviewSection's SPLIT, the
// sibling of the strip that was fixed on the line below it.
//
// The separator is INTERIOR on purpose. The existing whitespace fixture puts
// one at the end of the flagged line, where the strip consumes it — so that
// fixture pins the strip and is structurally blind to the split. Here the
// two runtimes flag different text (Python breaks the line, the port keeps
// the tail) and, when a marker lands in each fragment, a different COUNT.
func TestExportRendersRedactedLinesBySplitlines(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("skills/probe.md",
			"intro\nsk-ant-secretsecretsecret99\x0b tail-of-same-line\ndone\n"+
				// Two markers, one per fragment: this is the count half.
				"sk-ant-aaaaaaaaaaaaaaaaaa\x1csk-ant-bbbbbbbbbbbbbbbbbb\n")
	}
	want, members, err := runExportBoth(t, seed)
	if err != nil {
		t.Fatal(err)
	}
	assertMembersMatch(t, members, want)
}

// TestExportNormalizesNumbersLikeALoadsDumpsPair: CPython has no numeric
// literal to preserve across `json.loads` → `json.dumps` — only an int or a
// float — so `1e3` comes back `1000.0`. The port's UseNumber decode kept the
// source text and wrote it back verbatim, which changes the member bytes and
// therefore the artifact sha256 in the hashed manifest.
//
// The 20-digit id is the other half: CPython's int is arbitrary-precision
// and prints exactly, so an integral literal must NOT be routed through
// float64 in the name of normalizing.
func TestExportNormalizesNumbersLikeALoadsDumpsPair(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/standing_rules.jsonl",
			`{"rule_id":"a","n":1e3,"m":0.10,"big":5.00,"z":-0}`+"\n"+
				`{"rule_id":12345678901234567890,"exact":98765432109876543210}`+"\n"+
				`{"rule_id":"c","huge":1e400,"tiny":1e-400,"nan":NaN}`+"\n"+
				`{"rule_id":"d","nested":[1e2,{"k":2.50}]}`+"\n")
	}
	want, members, err := runExportBoth(t, seed)
	if err != nil {
		t.Fatal(err)
	}
	assertMembersMatch(t, members, want)
}

// TestExportCollapsesKeysThatScrubToTheSameString: the scrubber's own
// ordinary case, not a contrived one — two secret-shaped keys both become
// "[REDACTED]". Python's `{scrub(k): scrub(v) for ...}` is a dict and cannot
// hold a key twice; the port's positional walk emitted a JSON object with a
// duplicate key, carrying a value Python discards.
func TestExportCollapsesKeysThatScrubToTheSameString(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		// A PLAIN key between the two collapsing ones. Without it the
		// fixture pins the collapse and not the ORDINAL half its own
		// comment states — "the key keeps the ordinal of its FIRST
		// appearance and the value of its LAST" — because with the two
		// adjacent, first-ordinal and last-ordinal produce the same
		// object. Measured: a remove-and-append mutant in scrub.Walk
		// passed this test AND the whole internal/scrub suite.
		//
		// A test reporting agreement may be testing nothing (lens 1), and
		// the tell here was that the comment claimed more than the fixture
		// could show.
		w("memory/standing_rules.jsonl",
			`{"rule_id":"k","sk-ant-aaaaaaaaaaaaaaaaaa":1,`+
				`"plain":9,`+
				`"sk-ant-bbbbbbbbbbbbbbbbbb":2}`+"\n")
	}
	want, members, err := runExportBoth(t, seed)
	if err != nil {
		t.Fatal(err)
	}
	assertMembersMatch(t, members, want)
}

// assertMembersMatch compares every archive member BYTE FOR BYTE, plus the
// manifest's per-artifact sha256.
//
// pack.json is excluded and REVIEW.md's Created: line normalized: both carry
// the seal instant, and the two runtimes cannot seal at the same one. The
// sha256 list is compared separately so a port that hashed something other
// than what it shipped is still caught.
func assertMembersMatch(t *testing.T, got map[string][]byte, want exportProbe) {
	t.Helper()
	if want.Raised != "" {
		t.Fatalf("CPython refused this export (%s) — the fixture belongs in "+
			"a refusal test, not this one", want.Raised)
	}
	var names []string
	for n := range want.Members {
		if n != "pack.json" {
			names = append(names, n)
		}
	}
	for n := range got {
		if _, ok := want.Members[n]; !ok {
			t.Errorf("the port shipped member %q, CPython did not", n)
		}
	}
	for _, n := range names {
		g, ok := got[n]
		if !ok {
			t.Errorf("CPython shipped member %q, the port did not", n)
			continue
		}
		// latin-1 back to bytes: the probe sent the exact bytes through a
		// one-to-one code-point mapping, so this recovers them.
		wb := make([]byte, 0, len(want.Members[n]))
		for _, r := range want.Members[n] {
			wb = append(wb, byte(r))
		}
		gs, ws := string(g), string(wb)
		if n == "REVIEW.md" {
			gs, ws = createdLine.ReplaceAllString(gs, "Created: <ts>"),
				createdLine.ReplaceAllString(ws, "Created: <ts>")
		}
		if gs != ws {
			t.Errorf("member %s differs.\n--- go ---\n%q\n--- py ---\n%q", n, gs, ws)
		}
	}
	var manifest map[string]any
	if err := json.Unmarshal(got["pack.json"], &manifest); err != nil {
		t.Fatal(err)
	}
	arts, _ := manifest["artifacts"].([]any)
	if len(arts) != len(want.Artifacts) {
		t.Fatalf("artifact count %d, CPython %d", len(arts), len(want.Artifacts))
	}
	for i, a := range arts {
		m, _ := a.(map[string]any)
		w := want.Artifacts[i]
		if m["path"] != w.Path {
			t.Errorf("artifact %d path %v, CPython %q", i, m["path"], w.Path)
			continue
		}
		if m["sha256"] != w.SHA256 {
			t.Errorf("%s: sha256 %v, CPython %q — the two runtimes shipped "+
				"different bytes for one artifact, so neither pack verifies "+
				"in the other runtime", w.Path, m["sha256"], w.SHA256)
		}
		gotRows, hasRows := m["rows"].(float64)
		if hasRows != (w.Rows != nil) {
			t.Errorf("%s: rows present = %v, CPython %v", w.Path, hasRows, w.Rows != nil)
		} else if w.Rows != nil && int(gotRows) != *w.Rows {
			t.Errorf("%s: rows = %d, CPython %d", w.Path, int(gotRows), *w.Rows)
		}
	}
}
