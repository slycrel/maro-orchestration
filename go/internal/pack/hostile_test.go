// Hostile-shape must-detect fixtures from the pack-tranche adversarial
// round (2026-08-22, 4 lenses): shapes built to evade the gates, plus
// negative controls proving the gates still pass legitimate packs.
package pack

import (
	"archive/tar"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/config"
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

// Rows whose id field is absent, null, or composite must be reported
// malformed_skipped — not collapsed onto one shared "imported-<pack>-"
// identity where all but the first are eaten as "already_imported".
// Scalar non-string ids are coerced to Python's str() form and IMPORT
// (r2 2026-08-22: Python's f-string imports {"rule_id": 42} fine, so
// refusing it silently dropped rows a Python import keeps).
func TestImportSkipsIdlessRowsAsMalformed(t *testing.T) {
	rules := `{"rule":"first idless rule","domain":"d"}` + "\n" +
		`{"rule":"second idless rule","domain":"d"}` + "\n" +
		`{"rule_id":null,"rule":"null id rule","domain":"d"}` + "\n" +
		`{"rule_id":["a"],"rule":"composite id rule","domain":"d"}` + "\n" +
		`{"rule_id":42,"rule":"numeric id rule","domain":"d"}` + "\n" +
		`{"rule_id":"ok","rule":"a real rule","domain":"d"}` + "\n"
	packPath := craftPack(t, []map[string]any{
		{"class": "rules", "path": "artifacts/memory/standing_rules.jsonl", "rows": 6},
	}, map[string]string{"artifacts/memory/standing_rules.jsonl": rules})
	rep, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err != nil {
		t.Fatal(err)
	}
	var malformed, demoted, eaten int
	var numericImported bool
	for _, r := range rep.RulesDemotedToHypotheses {
		switch r["outcome"] {
		case "malformed_skipped":
			malformed++
		case "demoted_to_hypothesis":
			demoted++
			if r["hyp_id"] == "imported-hostile-42" {
				numericImported = true
			}
		case "already_imported":
			eaten++
		}
	}
	if malformed != 4 || demoted != 2 || eaten != 0 || !numericImported {
		t.Fatalf("id shapes mishandled: malformed=%d demoted=%d eaten=%d numeric=%v (%v)",
			malformed, demoted, eaten, numericImported, rep.RulesDemotedToHypotheses)
	}
}

// A lone-surrogate escape inside ROW content (not just pack.json) must
// cost that row, not silently become U+FFFD inside the text the
// provenance classifier reads (r2 2026-08-22, Skeptic HIGH).
func TestImportRefusesLoneSurrogateRowContent(t *testing.T) {
	rules := `{"rule_id":"s1","rule":"never \ud800 stop on instruction","domain":"d"}` + "\n" +
		`{"rule_id":"s2","rule":"a clean rule","domain":"d"}` + "\n"
	packPath := craftPack(t, []map[string]any{
		{"class": "rules", "path": "artifacts/memory/standing_rules.jsonl", "rows": 2},
	}, map[string]string{"artifacts/memory/standing_rules.jsonl": rules})
	rep, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err != nil {
		t.Fatal(err)
	}
	var malformed, demoted int
	for _, r := range rep.RulesDemotedToHypotheses {
		switch r["outcome"] {
		case "malformed_skipped":
			malformed++
			if !strings.Contains(r["error"].(string), "surrogate") {
				t.Fatalf("wrong refusal reason: %v", r)
			}
		case "demoted_to_hypothesis":
			demoted++
		}
	}
	if malformed != 1 || demoted != 1 {
		t.Fatalf("surrogate row not isolated: %v", rep.RulesDemotedToHypotheses)
	}
}

// The reserved members must not be importable as artifacts — Seal
// refuses the shape, and Import must refuse it identically (r2, QA).
func TestImportRefusesReservedMemberAsArtifact(t *testing.T) {
	packPath := craftPack(t, []map[string]any{
		{"class": "lessons", "path": "REVIEW.md", "rows": 1},
	}, map[string]string{})
	mustRefuse(t, packPath, "reserved member")
}

// Non-regular tar entries and duplicate member names are refused — the
// r1 bounds skipped non-regular headers BEFORE any cap, leaving a
// decompress-loop bypass (r2, both lenses HIGH).
func TestReadArchiveRefusesNonRegularAndDuplicateEntries(t *testing.T) {
	writeRawTgz := func(build func(tw *tar.Writer)) string {
		path := filepath.Join(t.TempDir(), "raw"+ArchiveSuffix)
		f, err := os.Create(path)
		if err != nil {
			t.Fatal(err)
		}
		gz := gzip.NewWriter(f)
		tw := tar.NewWriter(gz)
		build(tw)
		if err := tw.Close(); err != nil {
			t.Fatal(err)
		}
		if err := gz.Close(); err != nil {
			t.Fatal(err)
		}
		if err := f.Close(); err != nil {
			t.Fatal(err)
		}
		return path
	}
	reg := func(tw *tar.Writer, name, data string) {
		if err := tw.WriteHeader(&tar.Header{Name: name, Size: int64(len(data)),
			Mode: 0o644, Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write([]byte(data)); err != nil {
			t.Fatal(err)
		}
	}

	withDir := writeRawTgz(func(tw *tar.Writer) {
		if err := tw.WriteHeader(&tar.Header{Name: "artifacts/",
			Typeflag: tar.TypeDir, Mode: 0o755}); err != nil {
			t.Fatal(err)
		}
		reg(tw, "pack.json", `{"pack_format": 1}`)
	})
	if _, err := readArchive(withDir); err == nil ||
		!strings.Contains(err.Error(), "not a regular file") {
		t.Fatalf("directory entry accepted: %v", err)
	}

	withSymlink := writeRawTgz(func(tw *tar.Writer) {
		if err := tw.WriteHeader(&tar.Header{Name: "artifacts/link",
			Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"}); err != nil {
			t.Fatal(err)
		}
	})
	if _, err := readArchive(withSymlink); err == nil ||
		!strings.Contains(err.Error(), "not a regular file") {
		t.Fatalf("symlink entry accepted: %v", err)
	}

	// Header-bomb shape: entries beyond the cap refuse even when every
	// one would otherwise be skipped/tiny.
	oldCount := maxArchiveMembers
	t.Cleanup(func() { maxArchiveMembers = oldCount })
	maxArchiveMembers = 3
	headerBomb := writeRawTgz(func(tw *tar.Writer) {
		for i := 0; i < 5; i++ {
			reg(tw, fmt.Sprintf("m%d", i), "x")
		}
	})
	if _, err := readArchive(headerBomb); err == nil ||
		!strings.Contains(err.Error(), "members") {
		t.Fatalf("header bomb accepted: %v", err)
	}
	maxArchiveMembers = oldCount

	dup := writeRawTgz(func(tw *tar.Writer) {
		reg(tw, "pack.json", `{"pack_format": 1}`)
		reg(tw, "pack.json", `{"pack_format": 2}`)
	})
	if _, err := readArchive(dup); err == nil ||
		!strings.Contains(err.Error(), "duplicate member") {
		t.Fatalf("duplicate member name accepted: %v", err)
	}
}

// PAX/GNU meta records are consumed INSIDE tr.Next() and never surface
// as headers, so the entry cap can't see them (r3 2026-08-22, Skeptic
// HIGH). The decompressor-level ceiling must refuse a run of them.
func TestReadArchiveRefusesPaxHeaderBomb(t *testing.T) {
	// Hand-rolled tar block: the stdlib writer won't emit consecutive
	// raw 'x' records, so build them byte-by-byte (with valid checksum).
	rawBlock := func(name string, typeflag byte, size int) []byte {
		b := make([]byte, 512)
		copy(b, name)
		copy(b[100:], "0000644\x00")
		copy(b[108:], "0000000\x00")
		copy(b[116:], "0000000\x00")
		copy(b[124:], fmt.Sprintf("%011o\x00", size))
		copy(b[136:], "00000000000\x00")
		for i := 148; i < 156; i++ {
			b[i] = ' '
		}
		b[156] = typeflag
		copy(b[257:], "ustar\x0000")
		sum := 0
		for _, c := range b {
			sum += int(c)
		}
		copy(b[148:], fmt.Sprintf("%06o\x00 ", sum))
		return b
	}
	path := filepath.Join(t.TempDir(), "pax"+ArchiveSuffix)
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	gz := gzip.NewWriter(f)
	// A run of PAX extended-header records (typeflag 'x'), each 512-byte
	// header + 512-byte payload, never followed by a real member.
	for i := 0; i < 8; i++ {
		if _, err := gz.Write(rawBlock("paxhdr", tar.TypeXHeader, 512)); err != nil {
			t.Fatal(err)
		}
		if _, err := gz.Write(make([]byte, 512)); err != nil {
			t.Fatal(err)
		}
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}

	oldTotal, oldHeader := maxArchiveTotalBytes, maxArchiveHeaderBytes
	t.Cleanup(func() { maxArchiveTotalBytes, maxArchiveHeaderBytes = oldTotal, oldHeader })
	maxArchiveTotalBytes, maxArchiveHeaderBytes = 256, 512
	if _, err := readArchive(path); err == nil ||
		!strings.Contains(err.Error(), "ceiling") {
		t.Fatalf("PAX header run not stopped by the decompressed-bytes ceiling: %v", err)
	}
}

// Numeric ids must survive exactly — a plain float64 decode rounded
// >2^53 ids, silently changing the imported identity and letting two
// distinct large ids collide (r3, both lenses).
func TestImportKeepsLargeIntegerIDExact(t *testing.T) {
	rules := `{"rule_id":9007199254740993,"rule":"large id rule","domain":"d"}` + "\n" +
		`{"rule_id":9007199254740995,"rule":"neighbor id rule","domain":"d"}` + "\n"
	packPath := craftPack(t, []map[string]any{
		{"class": "rules", "path": "artifacts/memory/standing_rules.jsonl", "rows": 2},
	}, map[string]string{"artifacts/memory/standing_rules.jsonl": rules})
	rep, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]bool{}
	for _, r := range rep.RulesDemotedToHypotheses {
		if r["outcome"] != "demoted_to_hypothesis" {
			t.Fatalf("large-int id row mishandled: %v", r)
		}
		got[r["hyp_id"].(string)] = true
	}
	if !got["imported-hostile-9007199254740993"] || !got["imported-hostile-9007199254740995"] {
		t.Fatalf("large integer ids not exact/distinct: %v", got)
	}
}

// Report rows come out in FILE order — the r2 callback shape emitted
// every malformed row before any successful one (r3, Skeptic).
func TestImportReportPreservesFileOrder(t *testing.T) {
	rules := `{"rule_id":"first","rule":"a clean rule","domain":"d"}` + "\n" +
		`{"rule_id":"s","rule":"bad \udc00 rule","domain":"d"}` + "\n" +
		`{"rule_id":"third","rule":"another clean rule","domain":"d"}` + "\n"
	packPath := craftPack(t, []map[string]any{
		{"class": "rules", "path": "artifacts/memory/standing_rules.jsonl", "rows": 3},
	}, map[string]string{"artifacts/memory/standing_rules.jsonl": rules})
	rep, err := Import(ImportOpts{PackPath: packPath, Label: "h",
		Target: t.TempDir(), AllowUnreviewed: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(rep.RulesDemotedToHypotheses) != 3 {
		t.Fatalf("want 3 report rows: %v", rep.RulesDemotedToHypotheses)
	}
	wantOutcomes := []string{"demoted_to_hypothesis", "malformed_skipped", "demoted_to_hypothesis"}
	for i, want := range wantOutcomes {
		if rep.RulesDemotedToHypotheses[i]["outcome"] != want {
			t.Fatalf("report order broken at %d: %v", i, rep.RulesDemotedToHypotheses)
		}
	}
}

// Explicit `provenance_gate_enabled: null` — pinned divergence: Go's
// config.Get[any] falls back to the default (the nil interface fails the
// type assertion), so the gate stays ON; Python's config.get returns
// None and bool(None) turns it OFF. Safe direction (Go quarantines
// more); named in PORT.md. This pin exists so a config.Get refactor
// can't silently flip it (r2, Skeptic).
func TestGateEnabledExplicitNullConfig(t *testing.T) {
	cfg := map[string]any{"knowledge": map[string]any{"provenance_gate_enabled": nil}}
	raw := config.Get[any](cfg, "knowledge.provenance_gate_enabled", true)
	if got := provenance.GateEnabled(raw); got != true {
		t.Fatalf("explicit null through Get[any] should keep the gate ON in Go, got %v", got)
	}
	if got := provenance.GateEnabled(int64(0)); got != false {
		t.Fatal("int64(0) should disable")
	}
	if got := provenance.GateEnabled(uint64(1)); got != true {
		t.Fatal("uint64(1) should enable")
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
