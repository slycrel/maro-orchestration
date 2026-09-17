package defaults

import (
	"os"
	"strings"
	"testing"
)

// docRows reads go/DEFAULTS.md and returns its table rows as
// key -> (value, flag). The doc is the surface someone reads before
// flipping a switch; the registry is what the code runs. Either one
// drifting from the other is the bug this test exists to catch.
func docRows(t *testing.T) map[string][2]string {
	t.Helper()
	b, err := os.ReadFile("../../DEFAULTS.md")
	if err != nil {
		t.Fatal(err)
	}
	rows := map[string][2]string{}
	for _, line := range strings.Split(string(b), "\n") {
		if !strings.HasPrefix(line, "| `") {
			continue
		}
		cells := strings.Split(strings.Trim(line, "|"), "|")
		if len(cells) < 3 {
			continue
		}
		key := strings.Trim(strings.TrimSpace(cells[0]), "`")
		val := strings.Trim(strings.TrimSpace(cells[1]), "`")
		flag := strings.Trim(strings.TrimSpace(cells[2]), "`")
		if flag == "—" {
			flag = ""
		}
		rows[key] = [2]string{val, flag}
	}
	if len(rows) == 0 {
		t.Fatal("go/DEFAULTS.md has no table rows — the census cannot see anything")
	}
	return rows
}

// Forward: a default the code runs must be written down, with the value
// it actually has.
func TestEveryDefaultIsDocumented(t *testing.T) {
	rows := docRows(t)
	for _, d := range List() {
		got, ok := rows[d.Key]
		if !ok {
			t.Errorf("%s is registered but has no row in go/DEFAULTS.md", d.Key)
			continue
		}
		if got[0] != d.Render() {
			t.Errorf("%s: doc says %q, code runs %q", d.Key, got[0], d.Render())
		}
		if got[1] != d.Flag {
			t.Errorf("%s: doc says flag %q, code has %q", d.Key, got[1], d.Flag)
		}
		if strings.TrimSpace(d.Why) == "" {
			t.Errorf("%s: a default without a reason is a default nobody can flip", d.Key)
		}
	}
}

// Reverse: a row in the doc must correspond to something the code reads.
// This is the lane that catches a default removed in code and left in the
// doc — the failure that makes a defaults doc untrustworthy.
func TestEveryDocumentedKeyIsRegistered(t *testing.T) {
	for key := range docRows(t) {
		if _, err := Lookup(key); err != nil {
			t.Errorf("go/DEFAULTS.md documents %s, but nothing registers it: %v", key, err)
		}
	}
}
