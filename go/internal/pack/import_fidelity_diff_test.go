package pack

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyImportFidelitySrc builds a pack with CPython, then imports it with
// CPython, and reports the importer's own result rows plus the BYTES of the
// stores it wrote.
//
// Building the pack on the CPython side and handing the same file to both
// importers is deliberate: it takes the exporter out of the question. An
// exporter difference would otherwise surface as an import difference and be
// credited to the wrong function — the mistake the round-10 mint fixtures
// made in the other direction.
const pyImportFidelitySrc = `
import json, sys
from pathlib import Path
import pack

_argv = json.loads(sys.argv[1])
res = pack.export_pack(name="t", label="src",
                       workspace=Path(_argv["src"]), out_dir=Path(_argv["out"]),
                       denylist=[], home="/nonexistent-home",
                       hostname="nonexistent-host")
pack_path = res["pack_path"]
# Sealed, because an unsealed pack is refused at the door by both importers
# and the refusal would be the only thing this differential measured.
pack.seal_pack(Path(pack_path), confirmed=True)
report = pack.import_pack(Path(pack_path), label="src",
                          target=Path(_argv["target"]))

stores = {}
for rel in ("memory/medium/lessons.jsonl", "memory/hypotheses.jsonl",
            "memory/imports.jsonl"):
    p = Path(_argv["target"]) / rel
    stores[rel] = p.read_text(encoding="utf-8") if p.exists() else None

def _clean(rows):
    # The pack tag carries a content hash of the archive, and the two
    # runtimes never build byte-identical archives (mtimes, gzip headers),
    # so the ids derived from it cannot match. Everything ELSE in a result
    # row is the importer's decision, which is the subject.
    out = []
    for r in rows:
        out.append({k: v for k, v in r.items() if k != "new_id"})
    return out

print(json.dumps({
    "pack": pack_path,
    "lessons": _clean(report["lessons_imported"]),
    "hypotheses": _clean(report["hypotheses_imported"]),
    "skills_md": report["skills_md"],
    "stores": stores,
}, sort_keys=True))
`

type importProbe struct {
	Pack       string            `json:"pack"`
	Lessons    []map[string]any  `json:"lessons"`
	Hypotheses []map[string]any  `json:"hypotheses"`
	SkillsMD   []map[string]any  `json:"skills_md"`
	Stores     map[string]string `json:"stores"`
}

// runImportBoth seeds an identical SOURCE workspace and an identical TARGET
// on each side, lets CPython build and import the pack, then imports the
// SAME pack file into the Go target.
func runImportBoth(t *testing.T, seedSrc, seedTarget func(ws string)) (
	importProbe, *ImportReport, string) {
	t.Helper()
	src, pyTarget, goTarget := t.TempDir(), t.TempDir(), t.TempDir()
	seedSrc(src)
	if seedTarget != nil {
		seedTarget(pyTarget)
		seedTarget(goTarget)
	}
	var want importProbe
	pyprobe.Probe{Marker: "pack.py", Workspace: pyTarget}.RunJSON(
		t, pyImportFidelitySrc, &want, pyprobe.Arg(t, map[string]any{
			"src": src, "out": t.TempDir(), "target": pyTarget}))

	got, err := Import(ImportOpts{PackPath: want.Pack, Label: "src", Target: goTarget})
	if err != nil {
		t.Fatal(err)
	}
	return want, got, goTarget
}

// dropNewID strips the one key that cannot agree — see the probe's _clean.
func dropNewID(rows []map[string]any) []map[string]any {
	out := make([]map[string]any, 0, len(rows))
	for _, r := range rows {
		c := make(map[string]any, len(r))
		for k, v := range r {
			if k != "new_id" {
				c[k] = v
			}
		}
		out = append(out, c)
	}
	return out
}

// cmpResultRows compares two result lists as JSON, which normalizes number
// types (the Go report holds float64 where CPython holds int) without
// normalizing anything the importer decided.
func cmpResultRows(t *testing.T, what string, got, want []map[string]any) {
	t.Helper()
	g, _ := json.Marshal(dropNewID(got))
	w, _ := json.Marshal(canonRows(t, want))
	if string(g) != string(w) {
		t.Errorf("%s result rows differ.\n go: %s\n py: %s", what, g, w)
	}
}

// canonRows round-trips CPython's rows through Go's encoder so the two sides
// are compared under one serializer.
func canonRows(t *testing.T, rows []map[string]any) []map[string]any {
	t.Helper()
	if rows == nil {
		return []map[string]any{}
	}
	return rows
}

// TestImportReadsRowsBySplitlinesAndStrip is finding #3: the exporter's
// `_read_jsonl_rows` pair, unfixed at the READER for all three trust lanes.
//
// U+001F is the case that makes it asymmetric rather than merely different.
// `str.splitlines()` does NOT break on it, so a row prefixed with one
// survives a CPython export intact as a single member line; then
// `strings.TrimSpace` — which does not know U+001C–U+001F — leaves it on the
// front, the strict decoder refuses the row, and the import drops it
// SILENTLY. Not even a malformed_skipped row to say a lesson went missing.
func TestImportReadsRowsBySplitlinesAndStrip(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			"\x1f"+`{"lesson_id":"lx","lesson":"prefixed by a unit separator",`+
				`"source_goal":"port","confidence":0.9,"tier":"long","score":1.4,`+
				`"minted_from":"outcome"}`+"\n"+
				`{"lesson_id":"ly","lesson":"ordinary","source_goal":"port",`+
				`"confidence":0.9,"tier":"long","score":1.4,`+
				`"minted_from":"outcome"}`+"\n")
	}
	want, got, goTarget := runImportBoth(t, seed, nil)
	if len(want.Lessons) != 2 {
		t.Fatalf("the fixture proves nothing unless CPython imports BOTH "+
			"rows; it reported %d: %+v", len(want.Lessons), want.Lessons)
	}
	cmpResultRows(t, "lessons", got.LessonsImported, want.Lessons)
	cmpStoreBytes(t, goTarget, want, "memory/medium/lessons.jsonl")
}

// TestImportRefusesANullScoreLikeFloatOfNone is finding #5.
//
// `float(row.get("score", 1.0))` defaults only when the key is ABSENT. An
// explicit null is a present key, so CPython gets None, `float(None)` raises,
// the per-row except catches it and the row is reported malformed_skipped.
// The port's nil-means-default read imported a row CPython refuses — into a
// live shared store, with an invented trust value.
func TestImportRefusesANullScoreLikeFloatOfNone(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			`{"lesson_id":"n1","lesson":"null score row","source_goal":"port",`+
				`"score":null,"confidence":0.9,"tier":"long","minted_from":"outcome"}`+"\n"+
				`{"lesson_id":"n2","lesson":"null confidence row","source_goal":"port",`+
				`"score":1.4,"confidence":null,"tier":"long","minted_from":"outcome"}`+"\n"+
				// The ABSENT-key twin, which must still take the default.
				// Without it, "refuse null" and "refuse absent" are
				// indistinguishable and the fix could overshoot silently.
				`{"lesson_id":"n3","lesson":"absent score row","source_goal":"port",`+
				`"confidence":0.9,"tier":"long","minted_from":"outcome"}`+"\n")
	}
	want, got, goTarget := runImportBoth(t, seed, nil)
	var skipped int
	for _, r := range want.Lessons {
		if r["outcome"] == "malformed_skipped" {
			skipped++
		}
	}
	if skipped != 2 {
		t.Fatalf("the fixture proves nothing unless CPython refuses both null "+
			"rows; it skipped %d: %+v", skipped, want.Lessons)
	}
	cmpResultRows(t, "lessons", got.LessonsImported, want.Lessons)
	cmpStoreBytes(t, goTarget, want, "memory/medium/lessons.jsonl")
}

// TestImportKeepsAnyTruthyImportedBlock is finding #8: `if row.get("imported"):`
// is a truthiness test and the value is stored WHATEVER IT IS.
//
// The type assertion made it a shape test only dicts pass, so an incoming
// `"imported": "from-elsewhere"` dropped the provenance chain here and kept
// it there. Both lanes carry the idiom; both are seeded.
func TestImportKeepsAnyTruthyImportedBlock(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/long/lessons.jsonl",
			`{"lesson_id":"p1","lesson":"a string provenance chain",`+
				`"source_goal":"port","confidence":0.9,"tier":"long","score":1.4,`+
				`"minted_from":"outcome","imported":"from-elsewhere"}`+"\n"+
				// A numeric tier, which Python stores RAW into the imported
				// block (row.get("tier", ""), no str(), unlike the
				// task_type/outcome pair beside it).
				`{"lesson_id":"p2","lesson":"a numeric tier","source_goal":"port",`+
				`"confidence":0.9,"tier":5,"score":1.4,"minted_from":"outcome",`+
				`"imported":["a","list"]}`+"\n"+
				// FALSY: the truthiness test must still exclude these.
				`{"lesson_id":"p3","lesson":"an empty provenance chain",`+
				`"source_goal":"port","confidence":0.9,"tier":"long","score":1.4,`+
				`"minted_from":"outcome","imported":{}}`+"\n")
		w("memory/hypotheses.jsonl",
			`{"hyp_id":"h1","lesson":"a hypothesis with a string chain",`+
				`"domain":"ops","confirmations":2,"contradictions":0,`+
				`"imported":"from-elsewhere"}`+"\n")
	}
	want, got, goTarget := runImportBoth(t, seed, nil)
	cmpResultRows(t, "lessons", got.LessonsImported, want.Lessons)
	cmpResultRows(t, "hypotheses", got.HypothesesImported, want.Hypotheses)
	// The stores are the real subject here: the result rows agree either
	// way, and the dropped provenance is only visible in what landed.
	cmpStoreBytes(t, goTarget, want, "memory/medium/lessons.jsonl")
	cmpStoreBytes(t, goTarget, want, "memory/hypotheses.jsonl")
	// The audit trail too. Its rows are dict literals in Python and Go maps
	// here, so they are the same key-order class as the `imported` blocks
	// above — a smaller consequence (nothing reads imports.jsonl back for
	// policy) but the same bytes-differ answer, and comparing it is free.
	cmpStoreBytes(t, goTarget, want, "memory/imports.jsonl")
}

// TestImportComparesLocalMDAfterNewlineTranslation is the import half of
// finding #2. `live_path.read_text(...) == content` translates newlines
// first, so a local skill saved with CRLF is `skipped_identical` to CPython
// and `conflict_quarantined` to a port that compared raw bytes — which also
// writes a quarantine file and a CONFLICTS.md row for a file the other
// runtime calls unchanged.
func TestImportComparesLocalMDAfterNewlineTranslation(t *testing.T) {
	body := "# Shared\nsame content\n"
	seedSrc := func(ws string) {
		w := seedWriter(t, ws)
		w("skills/shared.md", body)
		w("skills/queued.md", "# Queued\nalready here\n")
	}
	seedTarget := func(ws string) {
		w := seedWriter(t, ws)
		// The same text, saved by an editor that writes CRLF.
		w("skills/shared.md", "# Shared\r\nsame content\r\n")
		// A SECOND site for the same rule, and it needs its own fixture:
		// the live-file compare above is in _import_authored_md, while
		// "have I already quarantined exactly this?" is in
		// _write_quarantine, one call down. A skill that is NOT live here
		// but IS already sitting in quarantine with CRLF is
		// `already_quarantined` to CPython and a fresh `quarantined` write
		// to a port that compares raw bytes — a different outcome AND a
		// rewritten file. Without this case the second site was unpinned
		// (mutation I10 MISS) while the first one read as covering it.
		w("imports/src/skills/queued.md", "# Queued\r\nalready here\r\n")
	}
	want, got, goTarget := runImportBoth(t, seedSrc, seedTarget)
	var identical, already int
	for _, r := range want.SkillsMD {
		switch r["outcome"] {
		case "skipped_identical":
			identical++
		case "already_quarantined":
			already++
		}
	}
	if identical != 1 || already != 1 {
		t.Fatalf("the fixture proves nothing unless CPython reaches BOTH "+
			"compare sites; it reported %+v", want.SkillsMD)
	}
	cmpResultRows(t, "skills_md", got.SkillsMD, want.SkillsMD)
	// The audit trail for the Class-A lane, whose rows are the
	// {"class":…,"path":…,"outcome":…} shape rather than the lesson shape —
	// a different dict literal, so a different key order to get wrong.
	cmpStoreBytes(t, goTarget, want, "memory/imports.jsonl")
}

// cmpStoreBytes compares one store the import wrote, byte for byte, with the
// two volatile fields masked.
//
// Not a parsed comparison: key ORDER inside a stored row is part of what a
// shared store carries, and it is exactly what a Go map loses. The masking
// is narrow on purpose — pack tags derive from an archive hash and the two
// runtimes cannot build byte-identical archives, and imported_at is a
// timestamp.
func cmpStoreBytes(t *testing.T, goTarget string, want importProbe, rel string) {
	t.Helper()
	wantText, ok := want.Stores[rel]
	raw, rerr := os.ReadFile(filepath.Join(goTarget, filepath.FromSlash(rel)))
	if !ok || wantText == "" {
		if rerr == nil {
			t.Errorf("%s: the port wrote a store CPython did not:\n%s", rel, raw)
		}
		// A store NEITHER runtime wrote is agreement about nothing. Say so:
		// this helper's early return is exactly the shape that lets a
		// fixture pass while comparing two absent files.
		t.Logf("%s: neither runtime wrote this store", rel)
		return
	}
	if rerr != nil {
		t.Errorf("%s: CPython wrote this store and the port did not (%v):\n%s",
			rel, rerr, wantText)
		return
	}
	if got, w := maskVolatile(string(raw)), maskVolatile(wantText); got != w {
		t.Errorf("%s differs.\n--- go ---\n%s\n--- py ---\n%s", rel, got, w)
	}
}

// maskVolatile blanks the two values that cannot agree between runtimes:
// the pack tag (a content hash of the archive, and gzip mtimes mean the two
// archives are never byte-identical) and any ISO timestamp.
//
// Everything else in the row is compared verbatim, INCLUDING key order,
// because these are durable shared stores and the other runtime reads what
// this one writes.
var (
	volatileTag = regexp.MustCompile(`imported-[0-9a-f]{6,}-|"pack": "[^"]*"|"imported_from": "[^"]*"`)
	volatileTS  = regexp.MustCompile(`\d{4}-\d\d-\d\dT[\d:.+\-]+`)
)

func maskVolatile(s string) string {
	s = volatileTag.ReplaceAllString(s, "<tag>")
	return volatileTS.ReplaceAllString(s, "<ts>")
}
