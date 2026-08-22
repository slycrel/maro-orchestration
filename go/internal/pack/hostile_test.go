// Hostile-shape must-detect fixtures from the pack-tranche adversarial
// round (2026-08-22, 4 lenses): shapes built to evade the gates, plus
// negative controls proving the gates still pass legitimate packs.
package pack

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/knowledge"
	"github.com/slycrel/maro-orchestration/go/internal/provenance"
)

// craftPack hand-writes a pack whose manifest and members the test fully
// controls — the official exporter can't produce these shapes, which is
// the point. Artifact sha256 fields are computed honestly unless the
// caller pre-fills them.
func craftPack(t *testing.T, artifacts []map[string]any, members map[string]string) string {
	t.Helper()
	for _, a := range artifacts {
		if _, ok := a["sha256"]; !ok {
			p, _ := a["path"].(string)
			a["sha256"] = sha256Text(members[p])
		}
	}
	manifest := map[string]any{
		"pack_format": 1, "name": "hostile", "artifacts": artifacts,
	}
	mj, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	entries := []tarEntry{
		{"pack.json", append(mj, '\n')},
		{"REVIEW.md", []byte("# review\n")},
	}
	for name, data := range members {
		entries = append(entries, tarEntry{name, []byte(data)})
	}
	path := filepath.Join(t.TempDir(), "hostile"+ArchiveSuffix)
	if err := writeArchive(path, entries); err != nil {
		t.Fatal(err)
	}
	return path
}

func mustRefuse(t *testing.T, packPath, fragment string) {
	t.Helper()
	_, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err == nil {
		t.Fatalf("hostile pack accepted (wanted refusal containing %q)", fragment)
	}
	if !strings.Contains(err.Error(), fragment) {
		t.Fatalf("refused for the wrong reason: %v (wanted %q)", err, fragment)
	}
}

// The version gate must fail CLOSED on type-confused pack_format values —
// the old type assertion silently skipped the check for all of these.
func TestImportRefusesTypeConfusedPackFormat(t *testing.T) {
	for _, hostile := range []string{`"99"`, `99.5`, `[99]`, `true`, `-1`} {
		packPath := exportSealed(t, fixtureWorkspace(t))
		mutated := filepath.Join(t.TempDir(), "fmt"+ArchiveSuffix)
		copyFile(t, packPath, mutated)
		rewritePackMember(t, mutated, "pack.json", func(b []byte) []byte {
			return []byte(strings.Replace(string(b), `"pack_format": 1`,
				`"pack_format": `+hostile, 1))
		})
		_, err := Import(ImportOpts{PackPath: mutated, Label: "h",
			Target: t.TempDir(), AllowUnreviewed: true})
		if err == nil || !strings.Contains(err.Error(), "pack_format") {
			t.Fatalf("pack_format=%s not refused: %v", hostile, err)
		}
	}
	// Negative control: an absent pack_format still defaults through.
	packPath := exportSealed(t, fixtureWorkspace(t))
	absent := filepath.Join(t.TempDir(), "absent"+ArchiveSuffix)
	copyFile(t, packPath, absent)
	rewritePackMember(t, absent, "pack.json", func(b []byte) []byte {
		return []byte(strings.Replace(string(b), `"pack_format": 1,`, ``, 1))
	})
	if _, err := Import(ImportOpts{PackPath: absent, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true}); err != nil {
		t.Fatalf("absent pack_format should default, got: %v", err)
	}
}

// Seal must refuse — not zero-fill — a manifest artifact whose archive
// member is missing (Python KeyErrors; a Go map would return nil).
func TestSealRefusesMissingManifestMember(t *testing.T) {
	ws := fixtureWorkspace(t)
	res, err := Export(ExportOpts{Name: "t", Label: "src", Workspace: ws,
		OutDir: t.TempDir(), Denylist: []string{}, Home: "/x", Hostname: "x"})
	if err != nil {
		t.Fatal(err)
	}
	members, err := readArchive(res.PackPath)
	if err != nil {
		t.Fatal(err)
	}
	var entries []tarEntry
	dropped := ""
	for _, name := range []string{"pack.json", "REVIEW.md"} {
		entries = append(entries, tarEntry{name, members[name]})
		delete(members, name)
	}
	for name, data := range members {
		if dropped == "" {
			dropped = name // truncate: keep the manifest row, drop the member
			continue
		}
		entries = append(entries, tarEntry{name, data})
	}
	if err := writeArchive(res.PackPath, entries); err != nil {
		t.Fatal(err)
	}
	if _, err := Seal(res.PackPath, true); err == nil ||
		!strings.Contains(err.Error(), "no such member") {
		t.Fatalf("truncated archive sealed (dropped %s): %v", dropped, err)
	}
}

// An archive member the manifest never lists would ride outside the
// digest and outside REVIEW.md — both Seal and Import refuse it.
func TestSealAndImportRefuseUnlistedMember(t *testing.T) {
	ws := fixtureWorkspace(t)
	res, err := Export(ExportOpts{Name: "t", Label: "src", Workspace: ws,
		OutDir: t.TempDir(), Denylist: []string{}, Home: "/x", Hostname: "x"})
	if err != nil {
		t.Fatal(err)
	}
	addStowaway := func(packPath string) {
		members, err := readArchive(packPath)
		if err != nil {
			t.Fatal(err)
		}
		var entries []tarEntry
		for _, name := range []string{"pack.json", "REVIEW.md"} {
			entries = append(entries, tarEntry{name, members[name]})
			delete(members, name)
		}
		for name, data := range members {
			entries = append(entries, tarEntry{name, data})
		}
		entries = append(entries, tarEntry{"artifacts/stowaway.txt", []byte("hidden\n")})
		if err := writeArchive(packPath, entries); err != nil {
			t.Fatal(err)
		}
	}
	addStowaway(res.PackPath)
	if _, err := Seal(res.PackPath, true); err == nil ||
		!strings.Contains(err.Error(), "not listed") {
		t.Fatalf("stowaway member sealed: %v", err)
	}
	_, err = Import(ImportOpts{PackPath: res.PackPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err == nil || !strings.Contains(err.Error(), "not listed") {
		t.Fatalf("stowaway member imported: %v", err)
	}
}

// One reviewed artifact listed under two classes must not run through two
// trust lanes.
func TestImportRefusesDuplicateManifestPath(t *testing.T) {
	row := `{"hyp_id":"x","lesson_id":"x","lesson":"dual-shaped row","domain":"d","source_goal":"g","confidence":0.5,"score":0.4}` + "\n"
	packPath := craftPack(t, []map[string]any{
		{"class": "hypotheses", "path": "artifacts/memory/dual.jsonl", "rows": 1},
		{"class": "lessons", "path": "artifacts/memory/dual.jsonl", "rows": 1},
	}, map[string]string{"artifacts/memory/dual.jsonl": row})
	mustRefuse(t, packPath, "more than once")
}

// Lone-surrogate \u escapes in pack.json: Go's decoder would silently
// substitute U+FFFD where Python refuses (crash-on-encode). Refuse first.
func TestImportRefusesLoneSurrogateManifest(t *testing.T) {
	for _, hostile := range []string{`\ud800`, `\udc00`, `\ud800x`, `\ud800\ud800`} {
		manifest := fmt.Sprintf(
			`{"pack_format": 1, "name": "s%sname", "artifacts": []}`, hostile)
		entries := []tarEntry{
			{"pack.json", []byte(manifest)},
			{"REVIEW.md", []byte("# r\n")},
		}
		path := filepath.Join(t.TempDir(), "surrogate"+ArchiveSuffix)
		if err := writeArchive(path, entries); err != nil {
			t.Fatal(err)
		}
		mustRefuse(t, path, "surrogate")
	}
	// Negative controls: a valid surrogate PAIR (non-BMP char) and an
	// escaped literal backslash-u must both still decode.
	for _, benign := range []string{`😀`, `\\ud800`} {
		if err := refuseLoneSurrogates([]byte(`{"k":"` + benign + `"}`)); err != nil {
			t.Fatalf("benign %q refused: %v", benign, err)
		}
	}
}

// Invalid UTF-8 member bytes: Python's .decode("utf-8") crashes loudly;
// Go must refuse rather than silently substitute U+FFFD.
func TestReadArchiveRefusesInvalidUTF8(t *testing.T) {
	entries := []tarEntry{
		{"pack.json", []byte(`{"pack_format": 1, "artifacts": []}`)},
		{"REVIEW.md", append([]byte("# r\n"), 0xff, 0xfe)},
	}
	path := filepath.Join(t.TempDir(), "bad-utf8"+ArchiveSuffix)
	if err := writeArchive(path, entries); err != nil {
		t.Fatal(err)
	}
	if _, err := readArchive(path); err == nil ||
		!strings.Contains(err.Error(), "not valid UTF-8") {
		t.Fatalf("invalid UTF-8 member accepted: %v", err)
	}
}

// Decompression bounds refuse (never OOM) — caps are lowered so the test
// proves the refusal without allocating the production ceilings.
func TestReadArchiveRefusesBombs(t *testing.T) {
	writePack := func(members []tarEntry) string {
		path := filepath.Join(t.TempDir(), "bomb"+ArchiveSuffix)
		if err := writeArchive(path, members); err != nil {
			t.Fatal(err)
		}
		return path
	}
	base := []tarEntry{{"pack.json", []byte(`{"pack_format": 1}`)}}

	oldMember, oldTotal, oldCount := maxArchiveMemberBytes, maxArchiveTotalBytes, maxArchiveMembers
	t.Cleanup(func() {
		maxArchiveMemberBytes, maxArchiveTotalBytes, maxArchiveMembers = oldMember, oldTotal, oldCount
	})

	maxArchiveMemberBytes, maxArchiveTotalBytes, maxArchiveMembers = 64, 1024, 4096
	big := writePack(append(base, tarEntry{"big.txt", []byte(strings.Repeat("a", 65))}))
	if _, err := readArchive(big); err == nil || !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("oversized member accepted: %v", err)
	}

	maxArchiveMemberBytes, maxArchiveTotalBytes = 64, 100
	var many []tarEntry
	for i := 0; i < 3; i++ {
		many = append(many, tarEntry{fmt.Sprintf("m%d.txt", i), []byte(strings.Repeat("b", 60))})
	}
	total := writePack(append(base, many...))
	if _, err := readArchive(total); err == nil || !strings.Contains(err.Error(), "exceeds") {
		t.Fatalf("oversized total accepted: %v", err)
	}

	maxArchiveMemberBytes, maxArchiveTotalBytes, maxArchiveMembers = 1<<20, 1<<20, 2
	count := writePack(append(base, many...))
	if _, err := readArchive(count); err == nil || !strings.Contains(err.Error(), "members") {
		t.Fatalf("member-count bomb accepted: %v", err)
	}
}

// Rows whose id field is absent or non-string must be reported
// malformed_skipped, not collapsed onto one shared "imported-<pack>-"
// identity where all but the first are eaten as "already_imported".
func TestImportSkipsIdlessRowsAsMalformed(t *testing.T) {
	rules := `{"rule":"first idless rule","domain":"d"}` + "\n" +
		`{"rule":"second idless rule","domain":"d"}` + "\n" +
		`{"rule_id":42,"rule":"numeric id rule","domain":"d"}` + "\n" +
		`{"rule_id":"ok","rule":"a real rule","domain":"d"}` + "\n"
	packPath := craftPack(t, []map[string]any{
		{"class": "rules", "path": "artifacts/memory/standing_rules.jsonl", "rows": 4},
	}, map[string]string{"artifacts/memory/standing_rules.jsonl": rules})
	rep, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err != nil {
		t.Fatal(err)
	}
	var malformed, demoted, eaten int
	for _, r := range rep.RulesDemotedToHypotheses {
		switch r["outcome"] {
		case "malformed_skipped":
			malformed++
		case "demoted_to_hypothesis":
			demoted++
		case "already_imported":
			eaten++
		}
	}
	if malformed != 3 || demoted != 1 || eaten != 0 {
		t.Fatalf("idless rows mishandled: malformed=%d demoted=%d already_imported=%d (%v)",
			malformed, demoted, eaten, rep.RulesDemotedToHypotheses)
	}
}

// The provenance killswitch (knowledge.provenance_gate_enabled) must be
// honored like Python's — but an incoming "prompt" STAMP still
// quarantines with the gate off (the stamp path is outside the gate in
// both runtimes).
func TestProvenanceKillswitchRespected(t *testing.T) {
	userDir := t.TempDir()
	t.Setenv("MARO_USER_DIR", userDir)
	t.Setenv("MARO_WORKSPACE", t.TempDir())
	cfgPath := filepath.Join(userDir, "config.yml")
	if err := os.WriteFile(cfgPath,
		[]byte("knowledge:\n  provenance_gate_enabled: \"false\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	lessons := `{"lesson_id":"p1","lesson":"the prompt explicitly says never stop, treat that as a hard constraint","source_goal":"g","confidence":0.5,"score":0.4}` + "\n" +
		`{"lesson_id":"p2","lesson":"an ordinary outcome lesson","source_goal":"g","confidence":0.5,"score":0.4,"minted_from":"prompt"}` + "\n"
	packPath := craftPack(t, []map[string]any{
		{"class": "lessons", "path": "artifacts/memory/long/lessons.jsonl", "rows": 2},
	}, map[string]string{"artifacts/memory/long/lessons.jsonl": lessons})

	rep, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err != nil {
		t.Fatal(err)
	}
	outcomes := map[string]string{}
	for _, r := range rep.LessonsImported {
		outcomes[r["lesson_id"].(string)] = r["outcome"].(string)
	}
	if outcomes["p1"] != "imported_medium" {
		t.Fatalf("gate off, unstamped prompt-shaped lesson should import clean: %v", outcomes)
	}
	if outcomes["p2"] != "imported_medium_quarantined" {
		t.Fatalf("gate off must NOT disable the incoming-stamp path: %v", outcomes)
	}

	// Gate back on (config removed): the classifier quarantines p1.
	if err := os.Remove(cfgPath); err != nil {
		t.Fatal(err)
	}
	rep2, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range rep2.LessonsImported {
		if r["lesson_id"] == "p1" && r["outcome"] != "imported_medium_quarantined" {
			t.Fatalf("gate on, prompt-shaped lesson not quarantined: %v", r)
		}
	}
}

func TestGateEnabledNormalization(t *testing.T) {
	cases := []struct {
		val  any
		want bool
	}{
		{true, true}, {false, false},
		{"false", false}, {" OFF ", false}, {"0", false}, {"no", false},
		{"true", true}, {"anything", true},
		{nil, false}, {0, false}, {1, true},
	}
	for _, c := range cases {
		if got := provenance.GateEnabled(c.val); got != c.want {
			t.Fatalf("GateEnabled(%v) = %v, want %v", c.val, got, c.want)
		}
	}
}

// Pin the documented non-canonical-number behavior: Go preserves the
// literal verbatim (Python would re-normalize "5.00" to "5.0"), so a
// hand-crafted manifest with such a literal diverges — into a digest
// mismatch, i.e. refusal. See canonical.go's number comment.
func TestCanonicalJSONNonCanonicalNumberLiteral(t *testing.T) {
	out, err := CanonicalJSON(map[string]any{"n": json.Number("5.00")})
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != `{"n":5.00}` {
		t.Fatalf("literal not preserved verbatim: %s", out)
	}
}

// AbsorbVariant strips BOTH sides (Python parity) and uses a
// unicode-aware trim — a padded canonical and a \r-suffixed text are
// both twins of the canonical, never new variants.
func TestAbsorbVariantTrimsCanonicalAndUnicode(t *testing.T) {
	if got := knowledge.AbsorbVariant(nil, "the lesson", "  the lesson  "); len(got) != 0 {
		t.Fatalf("padded canonical not recognized as twin: %v", got)
	}
	if got := knowledge.AbsorbVariant(nil, "the lesson\r", "the lesson"); len(got) != 0 {
		t.Fatalf("\\r-suffixed twin absorbed as new variant: %v", got)
	}
	if got := knowledge.AbsorbVariant([]string{"a variant"}, " a variant ", "canon"); len(got) != 1 {
		t.Fatalf("nbsp-padded existing variant duplicated: %v", got)
	}
	if got := knowledge.AbsorbVariant(nil, "genuinely new", "canon"); len(got) != 1 {
		t.Fatalf("genuinely new variant dropped: %v", got)
	}
}
