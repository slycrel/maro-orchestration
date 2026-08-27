package pyurl

import (
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
)

// _check_bracketed_netloc's NFKC gate strips FOUR characters from the
// netloc before normalizing — `@`, `:`, `#`, `?` — and only the first two
// can be reached. Mutation says so: deleting either of the other two
// leaves the corpus green.
//
// That is the L8 case, and the answer is not a fixture. A netloc can never
// contain `#` or `?` because urlsplit removes the fragment and the query
// BEFORE it looks for a netloc, and _splitnetloc stops at any of `/?#`. So
// the two ReplaceAlls are dead in CPython too, and the port keeps them for
// the same reason CPython has them: the transcription is the spec.
//
// What is testable is the PREMISE. If CPython ever lets a `#` or a `?`
// through into a netloc, these two lines stop being dead and the port has
// a gap — so the premise is measured here rather than believed, over every
// position the two characters can occupy.
func TestNoNetlocCanCarryAFragmentOrQueryCharacter(t *testing.T) {
	var urls []string
	hosts := []string{"example.com", "u@example.com", "u:p@example.com",
		"[::1]", "example.com:8080", ""}
	for _, h := range hosts {
		for _, mark := range []string{"#", "?"} {
			for i := 0; i <= len(h); i++ {
				urls = append(urls,
					"http://"+h[:i]+mark+h[i:]+"/path",
					"//"+h[:i]+mark+h[i:],
					"http://"+h[:i]+mark+h[i:])
			}
		}
	}
	// The controls: the same hosts with no mark at all, so a probe that
	// returned "" for everything would be caught by the floor below.
	for _, h := range hosts {
		urls = append(urls, "http://"+h+"/path")
	}

	in, err := json.Marshal(urls)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"from urllib.parse import urlsplit\n"+
			"res=[]\n"+
			"for u in json.loads(sys.argv[1]):\n"+
			"    try: res.append(urlsplit(u).netloc)\n"+
			"    except ValueError: res.append(None)\n"+
			"print(json.dumps(res))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var netlocs []*string
	if err := json.Unmarshal(out, &netlocs); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if len(netlocs) != len(urls) {
		t.Fatalf("probe returned %d rows for %d urls", len(netlocs), len(urls))
	}

	var nonEmpty int
	for i, n := range netlocs {
		if n == nil {
			continue
		}
		if *n != "" {
			nonEmpty++
		}
		if strings.ContainsAny(*n, "#?") {
			t.Errorf("CPython put a gate character INTO a netloc: %q -> %q. "+
				"The `#`/`?` strips in the NFKC gate are no longer dead and "+
				"this port has no fixture for them.", urls[i], *n)
		}
	}
	// Vacuity: a probe that answered "" everywhere would satisfy every
	// assertion above while measuring nothing.
	if nonEmpty < len(hosts) {
		t.Fatalf("only %d of %d urls produced a non-empty netloc; the sweep "+
			"is not reaching the parser", nonEmpty, len(urls))
	}
}
