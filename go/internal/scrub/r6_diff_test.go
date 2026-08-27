package scrub

import (
	"encoding/json"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

func srcDirScrub(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// TestSecretsRedactsAllPortedShapes above asserts only that a redaction
// HAPPENED, over seven all-ASCII fixtures. Both runtimes redact all
// seven, so it could not fail on the sixth pattern's `\s`/`\S` fork — a
// corpus that cannot separate (adversarial mission-r6, LENS 1 head 3).
//
// This is the differential. It compares the whole output string, not the
// presence of a marker, because the divergence is Go redacting text
// CPython KEEPS: `\S` in RE2 is the complement of five code points, so
// U+00A0 and U+000B feed the {8,} run. Scrubbed text is what reaches
// closure_verdicts.jsonl, metadata.json and the recorder's captured
// prompts, and a redaction that fires on only one side destroys evidence
// the other side still has.
func TestSecretsMatchesCPython(t *testing.T) {
	cases := []string{
		// THE finding: a separator between the ':' and the value. CPython
		// stops the \s* run at ':' and then \S{8,} cannot start on a
		// space, so nothing matches; Go's \S counted these as non-space.
		"token:\u00a0abcdefgh",
		"token:\u000babcdefgh",
		"token:\u001cabcdefgh",
		"token:\u2028abcdefgh",
		"token:\u3000abcdefgh",
		// ...and the same set BEFORE the separator, where the \s* run is.
		"token\u00a0: abcdefgh",
		"token\u000b= abcdefgh",
		"token\u001c: abcdefgh",
		// A value that legitimately CONTAINS a wide space: CPython's
		// \S{8,} stops at it, so the redaction covers less text.
		"password: hunter2\u00a0hunter2",
		"secret = abcd\u2028efghijkl",
		// The Turkish i, in the KEYWORD half of the pattern. CPython's
		// re.IGNORECASE also matches U+0130/U+0131 for an `i`; Go's `(?i)`
		// does not, so before pytext.PyFoldI the port left the secret in
		// the clear where CPython redacted it:
		//
		//	"ap\u0131_key: ABCDEFGH12345678"
		//	  CPython  "[REDACTED]"
		//	  Go       unchanged
		//
		// A redaction BYPASS reachable with one homoglyph, and the
		// opposite direction from the \S finding above -- that one
		// over-redacts and loses evidence, this one under-redacts and
		// leaks. `authorization` and `api` are the two keywords with an i.
		"ap\u0131_key: ABCDEFGH12345678",
		"AP\u0130_KEY: ABCDEFGH12345678",
		"author\u0131zation: ABCDEFGH12345678",
		"AUTHOR\u0130ZATION: ABCDEFGH12345678",
		// Controls. Without these the four rows above would agree just as
		// well between two engines that had both stopped matching.
		"api_key: ABCDEFGH12345678",
		"authorization: ABCDEFGH12345678",
		// A keyword with no i at all must be untouched by the fold.
		"password: ABCDEFGH12345678",
		// The ASCII lane, which already agreed and must keep agreeing.
		"key sk-ant-abcdefghijklmnop1234 inline",
		"key sk-abcdefghijklmnop1234 inline",
		"tok ghp_abcdefghijklmnopqrst inline",
		"slack xoxb-1234567890-abc inline",
		"aws AKIAABCDEFGHIJKLMNOP inline",
		"password: hunter2hunter2",
		"Api_Key = supersecretvalue",
		"Bearer\tabcdefghijkl",
		"token:\nabcdefghijkl",
		"the word token appears without a value nearby",
		"token: short",
		"",
		// Two matches in one string, so the sub() walk is exercised.
		"a token: abcdefghij and a password=zyxwvutsrq b",
		// A case-folding lane: (?i) over a non-ASCII near-miss.
		"TOKEN: abcdefghij",
		"ToKeN: abcdefghij",
	}
	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"from secret_scrub import scrub\n"+
			"print(json.dumps([scrub(s) for s in json.loads(sys.argv[1])]))",
		string(in), srcDirScrub(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var redacted, kept int
	for i, c := range cases {
		if got := Secrets(c); got != want[i] {
			t.Errorf("scrub diverges — one runtime persists text the other "+
				"destroys\n  in %q\n  go %q\n  py %q", c, got, want[i])
		}
		if strings.Contains(want[i], "[REDACTED]") {
			redacted++
		} else {
			kept++
		}
	}
	if redacted == 0 || kept == 0 {
		t.Fatalf("corpus reaches only one outcome: redacted=%d kept=%d",
			redacted, kept)
	}

	// Anti-vacuity: run the pattern this fix replaced over the same
	// corpus and require it to actually LOSE. Counting fixtures with wide
	// spaces would not prove the corpus discriminates; running the old
	// regex does.
	var oldDiffers int
	for i, c := range cases {
		if oldSecrets(c) != want[i] {
			oldDiffers++
		}
	}
	if oldDiffers < 3 {
		t.Fatalf("the pre-fix pattern matches CPython on all but %d of %d "+
			"cases: this corpus could not have caught the finding",
			oldDiffers, len(cases))
	}
	t.Logf("corpus separates the pre-fix pattern on %d of %d cases",
		oldDiffers, len(cases))
}

// oldSecrets is the sixth pattern as it shipped -- Go's `\s` and `\S`,
// which are the five-code-point ASCII classes, not Python's. It exists
// only so the test above can prove its own corpus discriminates, and
// must never return to production.
func oldSecrets(s string) string {
	return regexp.MustCompile(
		`(?i)(bearer|authorization|api[_-]?key|token|secret|password)\s*[:=]\s*\S{8,}`,
	).ReplaceAllString(s, "[REDACTED]")
}
