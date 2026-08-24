package director

import (
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// TestWriteLogIsPythonIndentTwo: the director log is a human-read
// artifact AND a shared-store file — _write_director_log writes it with
// json.dumps(indent=2), and r7 moved it off encoding/json (sorted keys,
// HTML escaping, raw UTF-8) onto pyval. Nothing read its bytes:
// TestWriteLogScrubsProse asserts a secret is ABSENT, which every
// possible renderer satisfies. r7's battery collapsed the log to one
// compact line and both director tests passed.
func TestWriteLogIsPythonIndentTwo(t *testing.T) {
	ws := t.TempDir()
	rec := record.New(ws)
	res := Result{
		DirectorID: "testindent",
		// `>` is escaped by encoding/json and not by json.dumps; é is
		// escaped by json.dumps and not by encoding/json. Both, so that
		// neither escaping alone can make this test agree.
		Directive: "route a > b through the café lane",
		Spec:      "spec: a > b",
		Tickets:   []Ticket{{TicketID: "t1", WorkerType: "ops", Task: "apply a > b"}},
	}
	path, err := writeLog(rec, res)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	s := string(raw)
	if !strings.Contains(s, "\n  \"") {
		t.Fatalf("director log is not indent-2:\n%s", s)
	}
	if strings.Contains(s, `\u003e`) {
		t.Fatalf("director log is HTML-escaped: no CPython writer produces "+
			"\\u003e for `>`\n%s", s)
	}
	if !strings.Contains(s, `caf\u00e9`) {
		t.Fatalf("director log is not ensure_ascii: json.dumps escapes é\n%s", s)
	}
	if strings.Contains(s, ", \n") {
		t.Fatalf("indent-2 rows must not carry a trailing item space:\n%s", s)
	}
	// director_id is written first in _write_director_log, and the whole
	// point of pyval here is that INSERTION order survives; encoding/json
	// would have sorted it after "directive".
	di := strings.Index(s, `"director_id"`)
	dv := strings.Index(s, `"directive"`)
	if di < 0 || dv < 0 || di > dv {
		t.Fatalf("key order is sorted, not insertion order (director_id at "+
			"%d, directive at %d):\n%s", di, dv, s)
	}
}
