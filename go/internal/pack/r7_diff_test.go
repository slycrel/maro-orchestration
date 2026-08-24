package pack

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// TestPackJSONIsPythonIndentTwo: pack.json is decoded by the Python
// importer, and it is the ONE manifest a human reads before sealing. Its
// bytes were encoding/json's until r7 — sorted keys, HTML-escaped `<>&`,
// raw UTF-8 — where Python's exporter writes json.dumps(indent=2):
// insertion order, no HTML escaping, ensure_ascii. Nothing read them, so
// r7's battery put MarshalIndent's escaping back and every pack test,
// including the byte-for-byte canonical-JSON ones, stayed green (those
// cover the PAYLOAD hash, which pack.json is deliberately not part of).
//
// Key ORDER stays lost here and is named rather than claimed: the
// manifest is a map[string]any across ~15 call sites including a foreign
// -file decode, so FromPlain sorts it. See archive.go's manifestBytes.
func TestPackJSONIsPythonIndentTwo(t *testing.T) {
	ws := fixtureWorkspace(t)
	out := t.TempDir()
	// The label rides straight into manifest["origin"]["label"] and
	// carries both hazards at once: `>` (escaped by encoding/json, not by
	// json.dumps) and a non-ASCII letter (escaped by json.dumps, not by
	// encoding/json). Either escaping alone would make this test agree
	// with the wrong writer.
	res, err := Export(ExportOpts{Name: "t", Label: "café > tea", Workspace: ws,
		OutDir: out, Denylist: []string{}, Home: "/x", Hostname: "x"})
	if err != nil {
		t.Fatal(err)
	}
	members, err := readArchive(res.PackPath)
	if err != nil {
		t.Fatal(err)
	}
	raw := string(members["pack.json"])
	if raw == "" {
		t.Fatal("no pack.json in the archive")
	}
	if strings.Contains(raw, `\u003e`) {
		t.Fatalf("pack.json is HTML-escaped: no CPython writer produces "+
			"\\u003e for `>`\n%s", raw)
	}
	if !strings.Contains(raw, `caf\u00e9`) {
		t.Fatalf("pack.json is not ensure_ascii: json.dumps escapes é\n%s", raw)
	}
	if !strings.Contains(raw, "\n  \"pack_format\"") &&
		!strings.Contains(raw, "\n  \"name\"") {
		t.Fatalf("pack.json is not indent-2:\n%s", raw)
	}
	// Python's indent mode drops the space from the ITEM separator while
	// keeping it in the key separator — `",\n"` and `": "`.
	if strings.Contains(raw, ", \n") {
		t.Fatalf("indent-2 rows must not carry a trailing item space:\n%s", raw)
	}
	// Whatever the escaping, it must still decode to the same manifest.
	var back map[string]any
	if err := json.Unmarshal([]byte(raw), &back); err != nil {
		t.Fatalf("pack.json no longer parses: %v\n%s", err, raw)
	}
	if back["origin"].(map[string]any)["label"] != "café > tea" {
		t.Fatalf("label did not survive the render: %v", back["origin"])
	}
	// The companion on disk is the same bytes the reader gets.
	if _, err := os.Stat(res.PackPath); err != nil {
		t.Fatal(err)
	}
}
