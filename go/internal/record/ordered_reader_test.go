package record

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// readerCorpus is one line per thing a JSONL store can contain, including
// every shape the three loss buckets exist to separate.
//
// Built from escapes rather than typed, and each entry says what it is for
// — a corpus whose entries are unlabelled decays into a list nobody dares
// to change.
var readerCorpus = []string{
	`{"a":1,"b":2}`,           // ordinary
	`{"b":2,"a":1}`,           // same keys, other order
	``,                        // blank frame: not a record, not a loss
	`   `,                     // whitespace-only: NOT a blank frame
	`[1,2,3]`,                 // clean JSON, wrong shape → non_dict
	`"just a string"`,         // ditto
	`null`,                    // ditto
	`{"a":1}x`,                // trailing content → malformed
	`{"a":1,"a":2}`,           // duplicate name → malformed
	`{"a": Infinity}`,         // bare non-finite → malformed
	`{"a": NaN}`,              // ditto
	`{"a":1e400}`,             // out-of-range float, CLEAN
	`{"a":` + hugeInt() + `}`, // >4300-digit int → malformed
	// NESTING DEPTH, both sides of encoding/json's 10000 limit. These are
	// the two lines the corpus was missing when it first claimed the two
	// readers agree: `Decode` enforces the limit in the scanner and
	// `Token()` does not, so the ordered reader admitted 10001 — and kept
	// recursing, into a `fatal error: stack overflow` past ~2M. A
	// detector that cannot see the case you already have is agreeing, not
	// measuring.
	nest(9999),                          // deep but legal on both
	nest(10001),                         // refused by both → malformed
	"{\"a\":\"\xff\xfe\"}",              // byte-tainted → undecodable
	`{"a":"\ud800"}`,                    // lone surrogate → malformed
	`{"nested":{"z":1,"a":2},"t":true}`, // nesting, for the order check
	`{"n":3.0,"i":7,"big":12345678901234567890}`, // number shapes
}

// nest builds `{"a":[[[…]]]}` with n levels of array under the object.
func nest(n int) string {
	return `{"a":` + strings.Repeat("[", n) + strings.Repeat("]", n) + `}`
}

func hugeInt() string {
	b := make([]byte, 4400)
	for i := range b {
		b[i] = '1'
	}
	return string(b)
}

// TestOrderedReaderClassifiesLikeThePlainOne is the promise made in
// ReadAllCountedOrdered's doc comment.
//
// The two readers reach their verdict through DIFFERENT decoders — a bare
// json.Decoder for LoadsClean, pyval.LoadsOrdered for LoadsCleanOrdered —
// so nothing structural stops them drifting. If they ever disagree about
// which lines are records, a caller's choice of row TYPE silently changes
// which rows it sees, and that is a bug no caller could reasonably be
// expected to suspect.
//
// Same records, same count, same per-bucket loss numbers. Not "same
// values": the number types differ by design (json.Number either way here,
// but the projection is not guaranteed) and that is what the second half
// of this file pins instead.
func TestOrderedReaderClassifiesLikeThePlainOne(t *testing.T) {
	path := filepath.Join(t.TempDir(), "corpus.jsonl")
	body := ""
	for _, line := range readerCorpus {
		body += line + "\n"
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}

	plain, prep := ReadAllCounted(path)
	ordered, orep := ReadAllCountedOrdered(path)

	if prep != orep {
		t.Errorf("skip reports differ:\n  plain:   %+v\n  ordered: %+v", prep, orep)
	}
	if len(plain) != len(ordered) {
		t.Fatalf("plain kept %d records, ordered kept %d", len(plain), len(ordered))
	}
	for i := range plain {
		if len(plain[i]) != len(ordered[i]) {
			t.Errorf("record %d: plain has %d keys, ordered has %d",
				i, len(plain[i]), len(ordered[i]))
			continue
		}
		for _, f := range ordered[i] {
			if _, ok := plain[i][f.Key]; !ok {
				t.Errorf("record %d: ordered has key %q the plain reader dropped",
					i, f.Key)
			}
		}
	}
	// Anti-vacuity: a corpus that lost nothing would make the report
	// comparison above pass by being empty on both sides.
	if !prep.Lost() || prep.Malformed == 0 || prep.Undecodable == 0 || prep.NonDict == 0 {
		t.Fatalf("the corpus must exercise all three loss buckets, got %+v", prep)
	}
}

// TestOrderedReaderPreservesFileKeyOrder is the property the ordered
// reader exists for, stated directly.
//
// Two rows with the SAME keys in different orders must come back
// different, and re-emitting either must reproduce its own line. A map
// makes both rows identical and both re-emissions alphabetical, which is
// how the divergence stayed invisible: every consumer json.loads the row,
// so nothing ever failed — the shared ledger just stopped being written
// the same way by the two runtimes.
func TestOrderedReaderPreservesFileKeyOrder(t *testing.T) {
	path := filepath.Join(t.TempDir(), "order.jsonl")
	lines := []string{
		`{"zebra": 1, "apple": 2, "middle": 3}`,
		`{"apple": 2, "middle": 3, "zebra": 1}`,
	}
	body := ""
	for _, l := range lines {
		body += l + "\n"
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	rows, _ := ReadAllCountedOrdered(path)
	if len(rows) != 2 {
		t.Fatalf("want 2 rows, got %d", len(rows))
	}
	for i, row := range rows {
		got, err := pyval.DumpsCompactPy(row)
		if err != nil {
			t.Fatal(err)
		}
		if got != lines[i] {
			t.Errorf("row %d re-emitted as %s, want %s", i, got, lines[i])
		}
	}
	// Setting an EXISTING key keeps its ordinal; a NEW key lands at the
	// tail. That pair is what makes a stamped row diff as one changed
	// field instead of a full rewrite.
	rows[0].Set("apple", 99)
	rows[0].Set("brand_new", true)
	got, _ := pyval.DumpsCompactPy(rows[0])
	const want = `{"zebra": 1, "apple": 99, "middle": 3, "brand_new": true}`
	if got != want {
		t.Errorf("after Set:\n  got  %s\n  want %s", got, want)
	}
}
