package pack

import (
	"strings"
	"testing"
)

// TestExportShipsTrailingGarbageVerbatim — H3, on the export side, where
// the consequence is destructive rather than merely lenient.
//
// `pyval.LoadsOrdered` refused trailing content only when that content was
// itself a valid JSON token, so `{"a":1}TRAILING` parsed clean and the `x`
// vanished. Two reachable consequences on one export, both measured:
//
//   - scrubJSONLLine parses a row and re-emits it. CPython's `json.loads`
//     RAISES on the trailing bytes, so the row is scrubbed as raw TEXT and
//     ships intact; the port parsed it and shipped only the object —
//     silently deleting bytes from a hashed payload row, so the two
//     runtimes produced different sha256s for the same input and neither
//     pack verified in the other.
//   - quarantinedRow reads `minted_from` to decide whether a lesson is
//     Class-A quarantined. A trailing-garbage row is unparseable to
//     CPython (so it ships) and parsed here (so it was dropped).
//
// The fixture puts the trailing bytes on a rule row and a lesson row at
// once, because the two lanes reach the same decoder by different paths and
// a fix at one call site would look like a fix at both.
func TestExportShipsTrailingGarbageVerbatim(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("memory/standing_rules.jsonl",
			`{"rule_id":"a","rule":"keeps its tail"}TRAILING`+"\n"+
				`{"rule_id":"b","rule":"the clean control"}`+"\n")
		w("memory/long/lessons.jsonl",
			`{"lesson_id":"q","lesson":"a quarantine claim","source_goal":"port",`+
				`"tier":"long","score":1.4,"confidence":0.9,`+
				`"minted_from":"prompt"}TRAILING`+"\n"+
				`{"lesson_id":"c","lesson":"the clean control","source_goal":"port",`+
				`"tier":"long","score":1.4,"confidence":0.9,`+
				`"minted_from":"outcome"}`+"\n")
	}
	want, members, err := runExportBoth(t, seed)
	if err != nil {
		t.Fatal(err)
	}
	// Anti-vacuity: CPython must actually be shipping the trailing bytes,
	// or this test agrees with the port about a file neither one wrote.
	rules := want.Members["artifacts/memory/standing_rules.jsonl"]
	if !strings.Contains(rules, "TRAILING") {
		t.Fatalf("CPython did not ship the trailing bytes — the fixture is "+
			"not exercising the case:\n%q", rules)
	}
	assertMembersMatch(t, members, want)
}

// TestExportSkipsANonFileMatchingTheGlob — M3.
//
// Python filters the skills/personas glob with `if f.is_file()`. The port
// never had that guard, and while the read merely `continue`d on error the
// omission was invisible. Making the read propagate — the right fix for a
// different divergence — turned a dormant gap into a fatal one: a DIRECTORY
// named `*.md` now aborted the whole export where CPython exports fine.
//
// A fix is evidence about its siblings, and this is the other direction of
// the same lens: a fix can also make a dormant divergence load-bearing.
// Nothing in the suite covered the case, so the regression shipped green.
func TestExportSkipsANonFileMatchingTheGlob(t *testing.T) {
	seed := func(ws string) {
		w := seedWriter(t, ws)
		w("skills/real.md", "# a real skill\n")
		// A directory whose NAME matches *.md. Created by writing a file
		// inside it, so the fixture cannot be silently flattened.
		w("skills/adir.md/inside.txt", "not a skill\n")
	}
	want, members, err := runExportBoth(t, seed)
	if err != nil {
		t.Fatalf("the port refused an export CPython performs: %v", err)
	}
	if _, ok := want.Members["artifacts/skills/real.md"]; !ok {
		t.Fatalf("CPython did not export the real skill — the fixture is "+
			"not exercising the case: %v", keysOf(want.Members))
	}
	if _, ok := want.Members["artifacts/skills/adir.md"]; ok {
		t.Fatal("CPython exported the directory — the premise has changed")
	}
	assertMembersMatch(t, members, want)
}

func keysOf(m map[string]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
