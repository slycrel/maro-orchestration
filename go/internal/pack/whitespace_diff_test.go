package pack

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyPackWhitespaceSrc exports and seals the seeded workspace with CPython and
// reports the REVIEW.md it wrote, the sealed digest, and the per-artifact row
// counts.
//
// It drives export_pack and seal_pack rather than the three expressions the
// port got wrong. A probe of the expressions would be evidence about the call
// I made, not about the pack a human receives — and the whole point of these
// fixtures is that the divergence only becomes damage after it has been
// hashed into the seal.
const pyPackWhitespaceSrc = `
import json, sys
from pathlib import Path
import pack

_argv = json.loads(sys.argv[1])
res = pack.export_pack(name="t", label="src",
                       workspace=Path(_argv["ws"]), out_dir=Path(_argv["out"]),
                       denylist=[], home="/nonexistent-home",
                       hostname="nonexistent-host")
path = Path(res["pack_path"])
sealed = pack.seal_pack(path, confirmed=True)

import tarfile
with tarfile.open(path, "r:gz") as tar:
    review = tar.extractfile("REVIEW.md").read().decode("utf-8")
    manifest = json.loads(tar.extractfile("pack.json").read().decode("utf-8"))
    members = {n: tar.extractfile(n).read().decode("utf-8", "replace")
               for n in tar.getnames() if n not in ("pack.json", "REVIEW.md")}

print(json.dumps({
    "review": review,
    "digest": manifest["review"]["review_manifest_sha256"],
    "artifacts": [{"path": a["path"], "rows": a.get("rows"),
                   "sha256": a["sha256"]} for a in manifest["artifacts"]],
    "members": members,
}, sort_keys=True))
`

// seedWhitespaceWorkspace writes a workspace whose content is ordinary except
// for the exotic whitespace, which is the subject.
//
// Every string here is built from explicit \u escapes rather than typed
// literally: a raw control byte in a source file is invisible in a diff, and
// an editor that strips it turns the fixture into one that agrees about
// nothing — which is how the first separator fixtures in this port passed
// while testing nothing at all.
func seedWhitespaceWorkspace(t *testing.T, ws string) {
	t.Helper()
	fs := "\x1c" // FILE SEPARATOR: str.strip() removes it, TrimSpace does not
	// UNIT SEPARATOR. str.strip() removes it and TrimSpace does not, same
	// as FILE SEPARATOR — but str.splitlines() does NOT break on it, and
	// that is why the blank-ish row below uses this one. Spelled with
	// \x1c the line was torn into empty fragments by the splitlines fix
	// before the strip predicate ever saw it, so the fixture agreed for
	// the wrong reason and the row filter went unpinned (mutation P3).
	us := "\x1f"
	vt := "\x0b"   // LINE TABULATION: str.splitlines() breaks on it, "\n" split does not
	nb := "\u00a0" // NO-BREAK SPACE: str.rstrip() removes it, TrimRight(" \t\n") does not

	mem := filepath.Join(ws, "memory")
	for _, d := range []string{filepath.Join(mem, "long"), filepath.Join(ws, "skills")} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	write := func(rel, content string) {
		if err := os.WriteFile(filepath.Join(ws, rel), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// A store whose rows exercise the reader: a real row, a line that is
	// ONLY separators (falsy to Python's ln.strip(), non-empty to
	// TrimSpace, so Python ships one row fewer), and a row carrying a
	// LINE TABULATION inside its JSON string (one row to a "\n" split,
	// two lines to splitlines — which means Python ships a torn half-row
	// the port never even sees as separate).
	write("memory/standing_rules.jsonl",
		`{"rule_id":"r1","rule":"verify before fix","domain":"review",`+
			`"confirmations":7,"contradictions":0}`+"\n"+
			us+us+"\n"+
			`{"rule_id":"r2","rule":"a`+vt+`b","domain":"review",`+
			`"confirmations":3,"contradictions":0}`+"\n")
	write("memory/hypotheses.jsonl",
		`{"hyp_id":"h1","lesson":"throttle test suites","domain":"ops",`+
			`"confirmations":2,"contradictions":0}`+"\n")
	write("memory/long/lessons.jsonl",
		`{"lesson_id":"la","lesson":"diff the sibling's fix history",`+
			`"source_goal":"port","confidence":0.9,"tier":"long","score":1.4,`+
			`"scope":"method","minted_from":"outcome"}`+"\n")

	// A skill whose REDACTED line ends in a separator. The redaction marker
	// is what puts the line in REVIEW.md's "Redacted lines" list, and the
	// list renders `ln.strip()` — so the separator decides how the line is
	// spelled in the document that gets hashed.
	write("skills/probe.md",
		"# Probe\nRead logs bottom-up. sk-ant-secretsecretsecret99 here."+fs+"\n"+
			"tail"+nb+"\n")
}

// createdLine is the one line that cannot agree: the two runtimes seal at
// different instants.
var createdLine = regexp.MustCompile(`(?m)^Created: .*$`)

// TestPackWhitespaceMatchesCPython pins the three sites where a Python
// whitespace operation reaches a hashed artifact.
//
// The assertion is on REVIEW.md's BYTES and on the artifact row counts, not
// on the sealed digest — the digest covers the header's `Created:` line,
// which is a timestamp and cannot match. What the digest does get is a
// self-consistency check on each side: the stamped value must be the sha256
// of the REVIEW.md that side actually wrote, so a port that hashed something
// other than the document it shipped is still caught.
func TestPackWhitespaceMatchesCPython(t *testing.T) {
	pyWS, goWS := t.TempDir(), t.TempDir()
	seedWhitespaceWorkspace(t, pyWS)
	seedWhitespaceWorkspace(t, goWS)

	var want struct {
		Review    string            `json:"review"`
		Digest    string            `json:"digest"`
		Members   map[string]string `json:"members"`
		Artifacts []struct {
			Path   string `json:"path"`
			Rows   *int   `json:"rows"`
			SHA256 string `json:"sha256"`
		} `json:"artifacts"`
	}
	pyOut := t.TempDir()
	pyprobe.Probe{Marker: "pack.py", Workspace: pyWS}.RunJSON(
		t, pyPackWhitespaceSrc, &want,
		pyprobe.Arg(t, map[string]any{"ws": pyWS, "out": pyOut}))

	res, err := Export(ExportOpts{Name: "t", Label: "src", Workspace: goWS,
		OutDir: t.TempDir(), Denylist: []string{},
		Home: "/nonexistent-home", Hostname: "nonexistent-host"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Seal(res.PackPath, true); err != nil {
		t.Fatal(err)
	}
	members, err := readArchive(res.PackPath)
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]any
	if err := json.Unmarshal(members["pack.json"], &manifest); err != nil {
		t.Fatal(err)
	}
	gotReview := string(members["REVIEW.md"])

	// 1. REVIEW.md, byte for byte with only the timestamp line normalized.
	norm := func(s string) string {
		return createdLine.ReplaceAllString(s, "Created: <ts>")
	}
	if norm(gotReview) != norm(want.Review) {
		t.Errorf("REVIEW.md differs.\n--- go ---\n%q\n--- py ---\n%q",
			norm(gotReview), norm(want.Review))
	}

	// 2. The row counts. This is where the reader's two bugs land: a
	// separators-only line Python drops and the port kept, and a row with
	// an embedded LINE TABULATION that Python splits in two.
	gotArts, _ := manifest["artifacts"].([]any)
	if len(gotArts) != len(want.Artifacts) {
		t.Fatalf("artifact count %d, CPython %d", len(gotArts), len(want.Artifacts))
	}
	for i, a := range gotArts {
		m := a.(map[string]any)
		w := want.Artifacts[i]
		if m["path"] != w.Path {
			t.Errorf("artifact %d path %v, CPython %q", i, m["path"], w.Path)
			continue
		}
		gotRows, hasRows := m["rows"].(float64)
		if hasRows != (w.Rows != nil) {
			t.Errorf("%s: rows present = %v, CPython %v", w.Path, hasRows, w.Rows != nil)
		} else if w.Rows != nil && int(gotRows) != *w.Rows {
			t.Errorf("%s: rows = %d, CPython %d", w.Path, int(gotRows), *w.Rows)
		}
		if m["sha256"] != w.SHA256 {
			t.Errorf("%s: payload sha256 = %v, CPython %q — the two runtimes "+
				"shipped different bytes for the same artifact",
				w.Path, m["sha256"], w.SHA256)
		}
	}

	// 3. The member bytes themselves, so a row-count agreement reached by
	// shipping different rows is still a failure.
	for name, py := range want.Members {
		got, present := members[name]
		if !present {
			t.Errorf("member %q missing from the port's pack", name)
			continue
		}
		if string(got) != py {
			t.Errorf("member %q differs.\n go: %q\n py: %q", name, string(got), py)
		}
	}

	// 4. Each side's stamped digest is of the document that side shipped.
	rev, _ := manifest["review"].(map[string]any)
	sum := sha256.Sum256([]byte(gotReview))
	if rev["review_manifest_sha256"] != hex.EncodeToString(sum[:]) {
		t.Errorf("the port stamped %v but shipped a REVIEW.md hashing to %s",
			rev["review_manifest_sha256"], hex.EncodeToString(sum[:]))
	}
	if want.Digest == "" {
		t.Error("CPython stamped no digest — the probe did not seal")
	}
}

// TestSealTailWhitespaceMatchesCPython drives the seal path's OWN two
// whitespace operations, which the export fixture cannot reach: the marker
// test's `.strip()` and the tail's bare `.rstrip()` both read a REVIEW.md a
// human edited before sealing, and the companion file is how that edit
// arrives.
func TestSealTailWhitespaceMatchesCPython(t *testing.T) {
	for _, c := range []struct {
		name string
		tail string
	}{
		{"a tail of no-break space", "hand-written note\u00a0\u00a0"},
		{"a tail of carriage returns", "hand-written note\r\r"},
		{"a tail of vertical tab and form feed", "hand-written note\x0b\x0c"},
		{"a tail of information separators", "hand-written note\x1c\x1d"},
		{"an already-marked review whose marker tail carries a separator",
			"note\n\n---\n\nReviewed payload SHA-256: `deadbeef`\x1c\n"},
		{"an already-marked review with a plain tail",
			"note\n\n---\n\nReviewed payload SHA-256: `deadbeef`\n"},
		{"an ordinary tail (control)", "hand-written note\n"},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS, goWS := t.TempDir(), t.TempDir()
			seedWhitespaceWorkspace(t, pyWS)
			seedWhitespaceWorkspace(t, goWS)

			// The companion is written AFTER export and BEFORE seal, which
			// is the sequence a human editing the review actually produces.
			var want struct {
				Review string `json:"review"`
				Digest string `json:"digest"`
			}
			pyprobe.Probe{Marker: "pack.py", Workspace: pyWS}.RunJSON(
				t, pySealTailSrc, &want,
				pyprobe.Arg(t, map[string]any{
					"ws": pyWS, "out": t.TempDir(), "tail": c.tail}))

			res, err := Export(ExportOpts{Name: "t", Label: "src",
				Workspace: goWS, OutDir: t.TempDir(), Denylist: []string{},
				Home: "/nonexistent-home", Hostname: "nonexistent-host"})
			if err != nil {
				t.Fatal(err)
			}
			// ARCHIVE_SUFFIX is ".maropack.tar.gz", not ".tar.gz" — a
			// companion written beside the wrong name is simply not found,
			// and the seal then hashes the ARCHIVED review instead. That
			// failure looks exactly like a port bug, which is what it looked
			// like for one run.
			companion := strings.TrimSuffix(res.PackPath, ".maropack.tar.gz") +
				".REVIEW.md"
			if err := os.WriteFile(companion, []byte(c.tail), 0o644); err != nil {
				t.Fatal(err)
			}
			if _, err := Seal(res.PackPath, true); err != nil {
				t.Fatal(err)
			}
			members, err := readArchive(res.PackPath)
			if err != nil {
				t.Fatal(err)
			}
			if got := string(members["REVIEW.md"]); got != want.Review {
				t.Errorf("sealed REVIEW.md differs.\n go: %q\n py: %q",
					got, want.Review)
			}
			// Same text on both sides means the same digest, and here the
			// text has no timestamp in it — so this one IS comparable.
			var manifest map[string]any
			if err := json.Unmarshal(members["pack.json"], &manifest); err != nil {
				t.Fatal(err)
			}
			rev, _ := manifest["review"].(map[string]any)
			if rev["review_manifest_sha256"] != want.Digest {
				t.Errorf("sealed digest %v, CPython %q",
					rev["review_manifest_sha256"], want.Digest)
			}
		})
	}
}

const pySealTailSrc = `
import json, sys, tarfile
from pathlib import Path
import pack

_argv = json.loads(sys.argv[1])
res = pack.export_pack(name="t", label="src",
                       workspace=Path(_argv["ws"]), out_dir=Path(_argv["out"]),
                       denylist=[], home="/nonexistent-home",
                       hostname="nonexistent-host")
path = Path(res["pack_path"])
pack._review_companion_path(path).write_text(_argv["tail"], encoding="utf-8")
pack.seal_pack(path, confirmed=True)

with tarfile.open(path, "r:gz") as tar:
    review = tar.extractfile("REVIEW.md").read().decode("utf-8")
    manifest = json.loads(tar.extractfile("pack.json").read().decode("utf-8"))

print(json.dumps({"review": review,
                  "digest": manifest["review"]["review_manifest_sha256"]},
                 sort_keys=True))
`

// pyImportSrc imports an existing pack with CPython and reports the tiered
// lesson rows that landed, so the two runtimes' importers can be compared
// over the SAME pack bytes — which takes the exporter out of the question.
const pyImportSrc = `
import json, sys
from pathlib import Path
import pack

_argv = json.loads(sys.argv[1])
pack.import_pack(Path(_argv["pack"]), label="src", target=Path(_argv["target"]))

out = {}
for rel in ("memory/medium/lessons.jsonl", "memory/long/lessons.jsonl"):
    p = Path(_argv["target"]) / rel
    rows = []
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    out[rel] = [{"lesson": r.get("lesson"), "minted_from": r.get("minted_from"),
                 "merged_variants": r.get("merged_variants")} for r in rows]
print(json.dumps(out, sort_keys=True))
`

// TestImportStampNormalizationMatchesCPython pins the provenance gate's
// normalizer and the variant absorber, over one pack imported twice.
//
// The stamp is the sharper of the two. `minted_from` arrives as untrusted
// JSON and the retrieval quarantine matches the exact string "prompt", so
// the `.strip().lower()` that turns a foreign spelling into it IS the gate.
// A stamp of "prompt" wearing a trailing information separator normalizes to
// "prompt" in CPython and — with strings.TrimSpace — to nothing here, which
// discards the incoming claim and imports the row unquarantined.
//
// Importing ONE pack twice is the point: any exporter difference would show
// up as an import difference and be credited to the wrong function.
func TestImportStampNormalizationMatchesCPython(t *testing.T) {
	ws := t.TempDir()
	seedWhitespaceWorkspace(t, ws)
	// A stamp that is "prompt" wearing a separator, and a variant list
	// carrying the same shape. Neither is EQUAL to "prompt", so the
	// exporter ships both (its quarantine is an exact ==) and the whole
	// question lands on the importer.
	//
	// The separator is a JSON ESCAPE, not a raw byte. A raw control
	// character inside a JSON string is illegal, so the first spelling of
	// this fixture was a row BOTH runtimes failed to parse and both
	// skipped — passing while testing nothing, which is the same trap the
	// mint fixtures fell into in round 10.
	//
	// Three variants, deliberately: one that is ONLY a separator (Python
	// drops it as falsy), one ordinary, and one ENDING in a separator —
	// which is the only one an absorber that strips a single end can get
	// wrong, and the separators-only entry cannot see that because both
	// spellings reduce it to nothing.
	row := `{"lesson_id":"lx","lesson":"a lesson that travels",` +
		`"source_goal":"port","confidence":0.9,"tier":"long","score":1.4,` +
		`"minted_from":"prompt\u001c",` +
		`"merged_variants":["\u001c","kept variant","a trailing separator\u001c"]}` + "\n"
	if err := os.WriteFile(filepath.Join(ws, "memory", "long", "lessons.jsonl"),
		[]byte(row), 0o644); err != nil {
		t.Fatal(err)
	}
	res, err := Export(ExportOpts{Name: "t", Label: "src", Workspace: ws,
		OutDir: t.TempDir(), Denylist: []string{},
		Home: "/nonexistent-home", Hostname: "nonexistent-host"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Seal(res.PackPath, true); err != nil {
		t.Fatal(err)
	}

	pyTarget, goTarget := t.TempDir(), t.TempDir()
	var want map[string][]struct {
		Lesson     string   `json:"lesson"`
		MintedFrom string   `json:"minted_from"`
		Variants   []string `json:"merged_variants"`
	}
	pyprobe.Probe{Marker: "pack.py", Workspace: pyTarget}.RunJSON(
		t, pyImportSrc, &want, pyprobe.Arg(t, map[string]any{
			"pack": res.PackPath, "target": pyTarget}))

	if _, err := Import(ImportOpts{PackPath: res.PackPath, Label: "src",
		Target: goTarget}); err != nil {
		t.Fatal(err)
	}

	for _, rel := range []string{
		"memory/medium/lessons.jsonl", "memory/long/lessons.jsonl"} {
		wantRows := want[rel]
		var gotRows []map[string]any
		raw, rerr := os.ReadFile(filepath.Join(goTarget, filepath.FromSlash(rel)))
		if rerr == nil {
			for _, ln := range strings.Split(string(raw), "\n") {
				if strings.TrimSpace(ln) == "" {
					continue
				}
				var m map[string]any
				if err := json.Unmarshal([]byte(ln), &m); err != nil {
					t.Fatalf("%s: row not JSON: %q", rel, ln)
				}
				gotRows = append(gotRows, m)
			}
		}
		if len(gotRows) != len(wantRows) {
			t.Errorf("%s: %d rows, CPython %d — which tier a row lands in IS "+
				"the quarantine decision", rel, len(gotRows), len(wantRows))
			continue
		}
		for i, w := range wantRows {
			g := gotRows[i]
			if s, _ := g["minted_from"].(string); s != w.MintedFrom {
				t.Errorf("%s row %d: minted_from = %q, CPython %q",
					rel, i, s, w.MintedFrom)
			}
			var gv []string
			if arr, ok := g["merged_variants"].([]any); ok {
				for _, v := range arr {
					s, _ := v.(string)
					gv = append(gv, s)
				}
			}
			if !reflect.DeepEqual(gv, w.Variants) {
				t.Errorf("%s row %d: merged_variants = %q, CPython %q",
					rel, i, gv, w.Variants)
			}
		}
	}
}
