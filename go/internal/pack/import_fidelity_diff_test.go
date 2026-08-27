package pack

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
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
# include_knowledge, so a seed can reach the QUARANTINE-ONLY lane, whose
# report rows are {"class": cls, **_quarantine_single(...)} — a different
# dict literal from the lesson/hypothesis rows and therefore a different
# key order to get wrong. No seed that omits knowledge_nodes.jsonl is
# affected: the exporter skips artifacts it finds nothing for.
res = pack.export_pack(name="t", label="src",
                       workspace=Path(_argv["src"]), out_dir=Path(_argv["out"]),
                       denylist=[], home="/nonexistent-home",
                       hostname="nonexistent-host", include_knowledge=True)
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
    "rules": _clean(report["rules_demoted_to_hypotheses"]),
    "quarantined": report["quarantined"],
    # KEY ORDER as lists of strings — the envelope's sort_keys=True sorts
    # dicts at every level, so the orders cannot ride the rows above.
    "key_order": {
        "lessons": [list(r) for r in report["lessons_imported"]],
        "hypotheses": [list(r) for r in report["hypotheses_imported"]],
        "skills_md": [list(r) for r in report["skills_md"]],
        "rules": [list(r) for r in report["rules_demoted_to_hypotheses"]],
        "quarantined": [list(r) for r in report["quarantined"]],
    },
    "stores": stores,
}, sort_keys=True))
`

type importProbe struct {
	Pack        string                `json:"pack"`
	Lessons     []map[string]any      `json:"lessons"`
	Hypotheses  []map[string]any      `json:"hypotheses"`
	SkillsMD    []map[string]any      `json:"skills_md"`
	Rules       []map[string]any      `json:"rules"`
	Quarantined []map[string]any      `json:"quarantined"`
	KeyOrder    map[string][][]string `json:"key_order"`
	Stores      map[string]string     `json:"stores"`
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
//
// It also FLATTENS the ordered row to a map, which is the point of the
// separate cmpResultKeyOrder below: this comparison is about the values
// the importer decided, and json.Marshal over a map sorts both sides
// under one serializer. Key order is a different question and it gets
// its own assertion rather than riding silently on this one.
func dropNewID(rows []pyval.Obj) []map[string]any {
	out := make([]map[string]any, 0, len(rows))
	for _, r := range rows {
		c := make(map[string]any, len(r))
		for _, f := range r {
			if f.Key != "new_id" {
				c[f.Key] = f.Val
			}
		}
		out = append(out, c)
	}
	return out
}

// cmpResultRows compares two result lists as JSON, which normalizes number
// types (the Go report holds float64 where CPython holds int) without
// normalizing anything the importer decided.
func cmpResultRows(t *testing.T, what string, got []pyval.Obj, want []map[string]any) {
	t.Helper()
	g, _ := json.Marshal(dropNewID(got))
	w, _ := json.Marshal(canonRows(t, want))
	if string(g) != string(w) {
		t.Errorf("%s result rows differ.\n go: %s\n py: %s", what, g, w)
	}
}

// cmpResultKeyOrder compares the KEY SEQUENCE of each result row against
// the sequence CPython's dict literal produced.
//
// It is separate from cmpResultRows because the probe's envelope is
// dumped with sort_keys=True, which sorts at every level and therefore
// destroys the row order in transport — the value comparison cannot see
// it. The probe sends the orders as explicit lists of strings instead,
// which sort_keys cannot touch.
func cmpResultKeyOrder(t *testing.T, what string, got []pyval.Obj, want [][]string) {
	t.Helper()
	if want == nil {
		t.Fatalf("%s: the probe sent no key orders — this assertion would "+
			"pass over an empty list and prove nothing", what)
	}
	if len(got) != len(want) {
		t.Errorf("%s: %d rows, CPython %d", what, len(got), len(want))
		return
	}
	for i, w := range want {
		var g []string
		for _, f := range got[i] {
			g = append(g, f.Key)
		}
		if len(g) != len(w) {
			t.Errorf("%s row %d keys %v, CPython %v", what, i, g, w)
			continue
		}
		for j := range w {
			if g[j] != w[j] {
				t.Errorf("%s row %d key order %v, CPython %v", what, i, g, w)
				break
			}
		}
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

// TestImportRowKeyOrderMatchesCPythonsDictLiterals reaches the two report
// shapes whose key order actually DIVERGES from alphabetical, which is
// what makes it a fixture rather than a restatement.
//
// The rest of this file's cases happen to be alphabetical —
// {lesson_id, new_id, outcome}, {hyp_id, new_hyp_id, outcome},
// {name, outcome} — so a Go map agreed with CPython there by
// coincidence and every existing imports.jsonl comparison passed while
// being structurally blind to the bug. The two that do not:
//
//	rules       {rule_id, hyp_id, outcome}   — hyp_id sorts BEFORE rule_id
//	quarantined {class, path, outcome}       — outcome sorts BEFORE path
//
// Both land in memory/imports.jsonl, which cmpStoreBytes compares byte
// for byte, so the store assertion below is the load-bearing one and the
// key-order assertions say WHICH row moved when it fails.
func TestImportRowKeyOrderMatchesCPythonsDictLiterals(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/standing_rules.jsonl",
			`{"rule_id":"r1","rule":"verify before fix","domain":"review",`+
				`"confirmations":7,"contradictions":0}`+"\n")
		// A quarantine-only class, for the {class, **rest} spread.
		w("memory/knowledge_nodes.jsonl",
			`{"node_id":"n1","title":"a node","body":"text"}`+"\n")
	}
	want, got, goTarget := runImportBoth(t, seed, nil)
	if len(want.Rules) != 1 || len(want.Quarantined) != 1 {
		t.Fatalf("the fixture proves nothing unless CPython reports one row "+
			"in EACH lane; it reported %d rules and %d quarantined",
			len(want.Rules), len(want.Quarantined))
	}
	// The premise, asserted rather than assumed: if CPython ever stopped
	// writing these in a non-alphabetical order, this whole test would
	// pass against a sorted port and prove nothing.
	if k := want.KeyOrder["rules"][0]; strings.Join(k, ",") == "hyp_id,outcome,rule_id" {
		t.Fatalf("CPython's rules row is alphabetical (%v) — this fixture no "+
			"longer separates insertion order from sorted order", k)
	}
	if k := want.KeyOrder["quarantined"][0]; strings.Join(k, ",") == "class,outcome,path" {
		t.Fatalf("CPython's quarantine row is alphabetical (%v) — same", k)
	}
	cmpResultRows(t, "rules", got.RulesDemotedToHypotheses, want.Rules)
	cmpResultKeyOrder(t, "rules", got.RulesDemotedToHypotheses, want.KeyOrder["rules"])
	cmpResultKeyOrder(t, "quarantined", got.Quarantined, want.KeyOrder["quarantined"])
	cmpStoreBytes(t, goTarget, want, "memory/imports.jsonl")
}
