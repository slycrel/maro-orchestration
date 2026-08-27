package procid

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The scratch-clone sweep decides whether to MERGE AND DELETE a repo
// clone by comparing a token this package computes against one a CPython
// process wrote into a sidecar. If the two runtimes disagree about the
// token for one live PID, the sweep reads a live owner as a dead one and
// removes a working directory out from under it.
//
// So the differential here is not "does the port look like the Python" —
// it is that both engines, asked about the SAME pid on the SAME machine,
// return the SAME string. That is only checkable by running them side by
// side, which is what these do.

const pyIdentitySrc = `
import json, sys
import process_identity as pi
out = []
for pid in json.loads(sys.argv[1]):
    out.append({"pid": pid,
                "token": pi.process_start_token(pid),
                "alive": pi.pid_alive(pid)})
print(json.dumps(out))
`

type identityRow struct {
	PID   int64   `json:"pid"`
	Token *string `json:"token"`
	Alive bool    `json:"alive"`
}

// pidCase is one PID and what CPython is CLAIMED to answer for it,
// written down before either implementation ran. The claim is checked
// FIRST, so a case that quietly stopped exercising its branch — a pid
// that used to be free and is now in use, a /proc that stopped being
// readable — fails instead of passing green while measuring nothing.
type pidCase struct {
	name string
	pid  int64
	// wantToken: "yes" = a 32-hex-character token, "no" = None.
	wantToken string
	wantAlive bool
	why       string
}

func TestProcessStartTokenAgreesWithCPythonForTheSamePID(t *testing.T) {
	cases := []pidCase{
		{"self", int64(os.Getpid()), "yes", true,
			"the test process is running and /proc/self/stat is readable"},
		{"init", 1, "yes", true,
			"pid 1 exists; os.kill raises PermissionError, which reads ALIVE"},
		{"zero", 0, "no", true,
			"os.kill(0, 0) addresses the caller's process GROUP and SUCCEEDS, " +
				"so CPython reads pid 0 as alive; /proc/0 does not exist so there is no token"},
		{"free-high", 1 << 30, "no", false,
			"1073741824 is above the kernel's pid_max, so nothing can hold it"},
		{"past-c-int", 1 << 40, "no", false,
			"os.kill converts the pid with the C `i` code, so this raises " +
				"OverflowError, which pid_alive reads as DEAD"},
	}

	var pids []int64
	for _, c := range cases {
		pids = append(pids, c.pid)
	}
	probe := pyprobe.Probe{Marker: "process_identity.py"}
	var rows []identityRow
	probe.RunJSON(t, pyIdentitySrc, &rows, pyprobe.Arg(t, pids))

	if len(rows) != len(cases) {
		t.Fatalf("probe returned %d rows for %d cases", len(rows), len(cases))
	}
	tokensSeen := 0
	for i, c := range cases {
		row := rows[i]
		if row.PID != c.pid {
			t.Fatalf("%s: probe answered about pid %d, not %d", c.name, row.PID, c.pid)
		}

		// --- the CLAIM about CPython, checked before any comparison ---
		gotTokenKind := "no"
		if row.Token != nil {
			gotTokenKind = "yes"
			if len(*row.Token) != 32 || strings.TrimLeft(*row.Token, "0123456789abcdef") != "" {
				t.Fatalf("%s: CPython's token %q is not 32 hex characters — "+
					"_digest's shape changed and this test's claim is stale",
					c.name, *row.Token)
			}
		}
		if gotTokenKind != c.wantToken {
			t.Fatalf("%s: CPython answered token=%v, the claim was %q (%s). "+
				"Fix the claim or the case — do not compare against a branch "+
				"that stopped being exercised", c.name, row.Token, c.wantToken, c.why)
		}
		if row.Alive != c.wantAlive {
			t.Fatalf("%s: CPython answered alive=%v, the claim was %v (%s)",
				c.name, row.Alive, c.wantAlive, c.why)
		}

		// --- the comparison ---
		goTok, goOK := StartToken(int(c.pid))
		if (row.Token != nil) != goOK {
			t.Errorf("%s: CPython token present=%v, Go present=%v",
				c.name, row.Token != nil, goOK)
		} else if row.Token != nil {
			tokensSeen++
			if *row.Token != goTok {
				t.Errorf("%s: token disagrees.\n  CPython %s\n  Go      %s\n"+
					"A sidecar written by one runtime would be read as a "+
					"DIFFERENT process by the other", c.name, *row.Token, goTok)
			}
		}
		if got := PIDAlive(int(c.pid)); got != row.Alive {
			t.Errorf("%s: alive disagrees: CPython %v, Go %v", c.name, row.Alive, got)
		}
	}

	// Vacuity floor: at least two cases must actually have produced a
	// token, or this whole test is comparing None against ("", false).
	if tokensSeen < 2 {
		t.Fatalf("only %d cases produced a token — the string comparison, "+
			"which is the point of this test, barely ran", tokensSeen)
	}
}

const pyOwnerCurrentSrc = `
import json, sys
import process_identity as pi
out = []
for c in json.loads(sys.argv[1]):
    out.append(pi.owner_is_current(
        c["pid"], c["token"],
        alive=lambda p, _a=c["alive"]: _a,
        token_reader=lambda p, _t=c["current"]: _t))
print(json.dumps(out))
`

// ownerCase is one owner_is_current fixture. want is what CPython is
// CLAIMED to answer.
type ownerCase struct {
	name    string
	alive   bool
	token   any
	current any
	want    bool
}

func TestOwnerIsCurrentMatchesCPython(t *testing.T) {
	cases := []ownerCase{
		{"dead", false, "abc", "abc", false},
		{"match", true, "abc", "abc", true},
		{"mismatch", true, "abc", "xyz", false},
		// The two "retain on ambiguity" arms. Both are TRUTHINESS tests,
		// which is what makes the falsy-but-present rows below differ
		// from a nil check.
		{"no recorded token", true, nil, "abc", true},
		{"empty recorded token", true, "", "abc", true},
		{"zero recorded token", true, json.Number("0"), "abc", true},
		{"false recorded token", true, false, "abc", true},
		{"empty list recorded token", true, []any{}, "abc", true},
		{"empty dict recorded token", true, map[string]any{}, "abc", true},
		// A DEAD pid crossed with each falsy token. These are the rows that
		// pin the ORDER of the two gates: `if not alive(pid): return False`
		// runs FIRST, so a dead owner is dead however ambiguous its token
		// is. With the gates swapped every one of these answers True and a
		// sweep would skip a clone whose owner is gone — which is the
		// forever-stranded direction. (Measured: a mutation swapping the two
		// gates failed nothing until these rows existed, because every other
		// falsy-token row here used a LIVE pid.)
		{"dead with no recorded token", false, nil, "abc", false},
		{"dead with empty recorded token", false, "", "abc", false},
		{"dead with zero recorded token", false, json.Number("0"), "abc", false},
		{"reader returns None", true, "abc", nil, true},
		{"reader returns empty", true, "abc", "", true},
		// str(recorded_token) — a NUMBER in the sidecar is compared by
		// its str(), so 123 matches the token "123".
		{"numeric token spells alike", true, json.Number("123"), "123", true},
		{"numeric token spells unlike", true, json.Number("123"), "0123", false},
		{"true recorded token", true, true, "True", true},
		{"list recorded token", true, []any{"a"}, "['a']", true},
	}

	type wire struct {
		PID     int  `json:"pid"`
		Alive   bool `json:"alive"`
		Token   any  `json:"token"`
		Current any  `json:"current"`
	}
	var payload []wire
	for _, c := range cases {
		payload = append(payload, wire{42, c.alive, c.token, c.current})
	}
	var got []bool
	pyprobe.Probe{Marker: "process_identity.py"}.RunJSON(t, pyOwnerCurrentSrc, &got,
		pyprobe.Arg(t, payload))

	if len(got) != len(cases) {
		t.Fatalf("probe returned %d rows for %d cases", len(got), len(cases))
	}
	trues, falses := 0, 0
	for i, c := range cases {
		if got[i] != c.want {
			t.Fatalf("%s: CPython answered %v, the claim was %v — fix the claim, "+
				"not the comparison", c.name, got[i], c.want)
		}
		if c.want {
			trues++
		} else {
			falses++
		}
		alive := func(int) bool { return c.alive }
		reader := func(int) (string, bool) {
			if c.current == nil {
				return "", false
			}
			return c.current.(string), true
		}
		if g := OwnerIsCurrent(42, c.token, alive, reader); g != got[i] {
			t.Errorf("%s: CPython %v, Go %v", c.name, got[i], g)
		}
	}
	// Vacuity floor: a table that answers one way for every row cannot
	// tell a working predicate from `return true`.
	if trues < 3 || falses < 3 {
		t.Fatalf("the table is one-sided (%d true, %d false)", trues, falses)
	}
}
