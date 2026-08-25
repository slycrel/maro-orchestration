package pyval

import (
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyUTF8Src decodes each byte string CPython's way and reports the message
// verbatim. The bytes ride as a list of ints because JSON has no byte type
// and a round-trip through a string is exactly the lossy step under test.
const pyUTF8Src = `
import json, sys

out = []
for row in json.loads(sys.argv[1]):
    b = bytes(row)
    try:
        b.decode("utf-8")
        out.append({"ok": True})
    except UnicodeDecodeError as e:
        out.append({"ok": False, "msg": str(e), "start": e.start,
                    "end": e.end, "reason": e.reason})
print(json.dumps(out))
`

// TestDecodeUTF8StrictMatchesCPython pins the decoder against the
// interpreter over every shape of malformed input the rules distinguish.
//
// The table is built from the DECODER, not from the port: each lead-byte
// class, each narrowed first-continuation range, truncation at EOF versus
// the same truncation mid-buffer, and both message widths. The pairs that
// matter most are the ones a hand-written decoder gets wrong in the same
// direction — an overlong C0 reported as a continuation error rather than a
// start error, a surrogate accepted, a truncated sequence reported with the
// wrong span.
func TestDecodeUTF8StrictMatchesCPython(t *testing.T) {
	cases := []struct {
		name string
		b    []byte
	}{
		{"empty", []byte{}},
		{"pure ascii", []byte(`{"a":"b"}`)},
		{"valid two-byte", []byte("é")},
		{"valid three-byte", []byte("€")},
		{"valid four-byte", []byte("💩")},
		{"valid max scalar", []byte{0xF4, 0x8F, 0xBF, 0xBF}},

		// Invalid START bytes, each class.
		{"lone continuation 0x80", []byte{'X', 0x80}},
		{"lone continuation 0xBF", []byte{'X', 0xBF}},
		{"overlong lead 0xC0", []byte{'X', 0xC0, 0xAF}},
		{"overlong lead 0xC1", []byte{'X', 0xC1, 0xBF}},
		{"out of range 0xF5", []byte{'X', 0xF5, 0x80, 0x80, 0x80}},
		{"0xFE", []byte{'X', 0xFE}},
		{"0xFF", []byte{'X', 0xFF}},

		// The narrowed first-continuation ranges — the four leads that
		// reject overlongs, surrogates and > U+10FFFF.
		{"E0 overlong", []byte{'X', 0xE0, 0x80, 0x80}},
		{"E0 lowest legal", []byte{'X', 0xE0, 0xA0, 0x80}},
		{"ED surrogate", []byte{'X', 0xED, 0xA0, 0x80}},
		{"ED highest legal", []byte{'X', 0xED, 0x9F, 0xBF}},
		{"F0 overlong", []byte{'X', 0xF0, 0x8F, 0x80, 0x80}},
		{"F4 above max", []byte{'X', 0xF4, 0x90, 0x80, 0x80}},

		// Bad continuations at each depth — the WIDTH of the report is the
		// number of bytes already accepted, so these three differ.
		{"two-byte bad cont", []byte{'X', 0xC3, 0x28}},
		{"three-byte bad 2nd", []byte{'X', 0xE2, 0x28, 0x41}},
		{"three-byte bad 3rd", []byte{'X', 0xE2, 0x82, 0x28}},
		{"four-byte bad 3rd", []byte{'X', 0xF0, 0x9F, 0x28}},
		{"four-byte bad 4th", []byte{'X', 0xF0, 0x9F, 0x92, 0x28}},

		// The SAME truncation, at EOF and mid-buffer: 'unexpected end of
		// data' versus 'invalid continuation byte'. A decoder that treats
		// the two alike passes one of these and fails the other.
		{"C3 at EOF", []byte{'X', 0xC3}},
		{"C3 mid-buffer", []byte{'X', 0xC3, '"'}},
		{"E2 at EOF", []byte{'X', 0xE2}},
		{"E2 82 at EOF", []byte{'X', 0xE2, 0x82}},
		{"F0 at EOF", []byte{'X', 0xF0}},
		{"F0 9F at EOF", []byte{'X', 0xF0, 0x9F}},
		{"F0 9F 92 at EOF", []byte{'X', 0xF0, 0x9F, 0x92}},

		// The offset is reported in BYTES, and a preceding multi-byte rune
		// is what separates a byte offset from a rune offset.
		{"bad byte after a multibyte rune", append([]byte("héllo"), 0xFF)},
		{"a realistic task row", []byte(`{"job_id":"j1","note":"` +
			string(rune(0)) + `"}`)},
		{"a task row with a bad byte late",
			append([]byte(`{"job_id":"j1","note":"`), 0xFF, '"', '}')},
	}

	rows := make([][]int, len(cases))
	for i, c := range cases {
		rows[i] = []int{}
		for _, x := range c.b {
			rows[i] = append(rows[i], int(x))
		}
	}

	var want []struct {
		OK     bool   `json:"ok"`
		Msg    string `json:"msg"`
		Start  int    `json:"start"`
		End    int    `json:"end"`
		Reason string `json:"reason"`
	}
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyUTF8Src, &want,
		pyprobe.Arg(t, rows))
	if len(want) != len(cases) {
		t.Fatalf("CPython answered %d of %d rows", len(want), len(cases))
	}

	// Anti-vacuity, and specific about it: a table that never reached a
	// range-form message, or never separated the two truncation reasons,
	// would satisfy every assertion below while testing a third of the
	// decoder.
	var oks, ranges int
	reasons := map[string]int{}
	for _, w := range want {
		if w.OK {
			oks++
			continue
		}
		reasons[w.Reason]++
		if w.End-w.Start > 1 {
			ranges++
		}
	}
	if oks < 4 || ranges < 2 || len(reasons) != 3 {
		t.Fatalf("CPython produced %d clean decodes, %d range-form errors and "+
			"reasons %v; the table is not exercising the decoder", oks, ranges,
			reasons)
	}

	for i, c := range cases {
		w := want[i]
		got, err := DecodeUTF8Strict(c.b)
		if w.OK {
			if err != nil {
				t.Errorf("%s: raised %v; CPython decodes it", c.name, err)
			} else if got != string(c.b) {
				t.Errorf("%s: decoded to %q, want %q", c.name, got, c.b)
			}
			continue
		}
		if err == nil {
			t.Errorf("%s: decoded clean; CPython raises %s", c.name, w.Msg)
			continue
		}
		if cls := ClassOf(err); cls != "UnicodeDecodeError" {
			t.Errorf("%s: raises %s, CPython raises UnicodeDecodeError",
				c.name, cls)
		}
		if err.Error() != w.Msg {
			t.Errorf("%s message\n go: %q\n py: %q", c.name, err.Error(), w.Msg)
		}
	}
}
