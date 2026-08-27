package pack

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
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
// Key ORDER used to be a named loss here — the manifest was a
// map[string]any across ~15 call sites including a foreign-file decode,
// so FromPlain sorted it. It is a pyval.Obj end to end now; the CPython
// comparison lives in export_fidelity_diff_test.go's cmpKeyOrder, and
// the seal half in TestSealReplacesReviewInPlace.
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

// TestSealReplacesReviewInPlace: `manifest["review"] = {...}`
// (pack.py:457) is an assignment to an EXISTING key, and a Python dict
// does not move a key it already has. So `review` keeps its ordinal —
// sixth, between `artifacts` and `trust_policy` — and a sealed pack.json
// differs from its unsealed self in exactly four values.
//
// A port that rebuilt the manifest on seal, or that appended the
// replacement, would put `review` last and rewrite every line after it.
// Nothing hashes pack.json, so that rewrite breaks no digest and shows
// up only as "the two runtimes produce different bytes for the same
// pack" — which is the whole reason a byte-comparison harness exists.
//
// The diff is computed rather than spelled out: an assertion that only
// checked the key sequence would pass a seal that also rewrote `origin`.
func TestSealReplacesReviewInPlace(t *testing.T) {
	ws := fixtureWorkspace(t)
	res, err := Export(ExportOpts{Name: "t", Label: "src", Workspace: ws,
		OutDir: t.TempDir(), Denylist: []string{}, Home: "/x", Hostname: "x"})
	if err != nil {
		t.Fatal(err)
	}
	before, err := readArchive(res.PackPath)
	if err != nil {
		t.Fatal(err)
	}
	unsealed, err := decodeManifest(before["pack.json"])
	if err != nil {
		t.Fatal(err)
	}
	sealed, err := Seal(res.PackPath, true)
	if err != nil {
		t.Fatal(err)
	}
	keys := func(o pyval.Obj) []string {
		var out []string
		for _, f := range o {
			out = append(out, f.Key)
		}
		return out
	}
	if a, b := strings.Join(keys(unsealed), ","), strings.Join(keys(sealed), ","); a != b {
		t.Fatalf("seal moved a top-level key.\n unsealed: %s\n sealed:   %s", a, b)
	}
	if got := keys(sealed); len(got) < 7 || got[5] != "review" || got[6] != "trust_policy" {
		t.Fatalf("review is not in build_manifest's sixth position: %v", got)
	}
	// Every field outside `review` must be byte-identical, and `review`
	// itself must keep its own four keys in build_manifest's order.
	for _, f := range sealed {
		u, present := unsealed.Get(f.Key)
		if !present {
			t.Fatalf("seal invented a key: %s", f.Key)
		}
		if f.Key == "review" {
			continue
		}
		gj, _ := pyval.DumpsIndent2(f.Val)
		uj, _ := pyval.DumpsIndent2(u)
		if gj != uj {
			t.Errorf("seal rewrote %s.\n unsealed: %s\n sealed:   %s", f.Key, uj, gj)
		}
	}
	rv, _ := sealed.Get("review")
	rev, ok := rv.(pyval.Obj)
	if !ok {
		t.Fatalf("sealed review is not an ordered object: %T", rv)
	}
	if got := strings.Join(keys(rev), ","); got !=
		"human_reviewed,reviewed_at,review_manifest_sha256,review_payload_sha256" {
		t.Fatalf("sealed review key order: %s", got)
	}
	// And it really did seal — otherwise "nothing changed" would pass
	// every assertion above.
	if hr, _ := rev.Get("human_reviewed"); hr != true {
		t.Fatalf("seal did not stamp human_reviewed: %v", hr)
	}
	if rev.GetString("review_payload_sha256") == "" {
		t.Fatal("seal did not stamp a payload digest")
	}
}
