package metrics

import (
	"encoding/base64"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyDecodeSrc decodes each fixture with errors="replace" and hands back the
// result base64'd, because the answer is a string containing replacement
// characters and round-tripping it through JSON as text would let an
// encoding difference in the TRANSPORT hide one in the subject.
const pyDecodeSrc = `
import base64, json, sys
out = []
for b64 in json.loads(sys.argv[1]):
    raw = base64.b64decode(b64)
    s = raw.decode("utf-8", errors="replace")
    out.append({"text": base64.b64encode(s.encode("utf-8")).decode(),
                "chars": len(s)})
print(json.dumps(out))
`

// TestDecodeReplaceMatchesCPython sweeps the ill-formed-UTF-8 space against
// the interpreter.
//
// The COUNT is the thing under test, not just the text. decodeReplace feeds
// spend_today's `line[:60]` window, which is sixty CODE POINTS, and a miss
// there does not skip a row — it ends the backward scan. So a decoder that
// produces the right characters in the wrong number moves the window and
// silently stops counting the day early.
//
// The port used strings.ToValidUTF8 here for a round. It collapses runs, so
// it answered one character where CPython answers two, and the divergence was
// documented as harmless on the grounds that torn rows are skipped anyway.
// They are; their LENGTHS are not (metrics r1 battery, M13/M14).
func TestDecodeReplaceMatchesCPython(t *testing.T) {
	var cases []string
	var labels []string
	add := func(label string, b []byte) {
		cases = append(cases, base64.StdEncoding.EncodeToString(b))
		labels = append(labels, label)
	}

	add("valid ascii", []byte("hello"))
	add("valid multibyte", []byte("アルファ"))
	// Every single byte on its own: this alone covers the lead-byte table.
	for i := 0; i < 256; i++ {
		add("single byte", []byte{byte(i)})
	}
	// A RUN of the same invalid byte — the case ToValidUTF8 collapses.
	add("two invalid bytes", []byte{0xff, 0xff})
	add("three invalid bytes", []byte{0xff, 0xfe, 0xfd})
	add("run of continuations", []byte{0x80, 0x80, 0x80})
	// TRUNCATED well-formed prefixes, which fold to ONE replacement each.
	add("truncated 3-byte", []byte{0xe2, 0x82})
	add("truncated 4-byte", []byte{0xf0, 0x9f, 0x92})
	add("truncated 2-byte", []byte{0xc3})
	// The narrowed continuation ranges: E0 wants A0..BF, ED wants 80..9F,
	// F0 wants 90..BF, F4 wants 80..8F. A byte just outside each range ends
	// the subpart early and is then re-examined on its own.
	add("E0 with a low continuation", []byte{0xe0, 0x9f, 0xbf})
	add("E0 with a good continuation", []byte{0xe0, 0xa0, 0xbf})
	add("ED with a high continuation", []byte{0xed, 0xa0, 0x80})
	add("ED with a good continuation", []byte{0xed, 0x9f, 0xbf})
	add("F0 with a low continuation", []byte{0xf0, 0x8f, 0xbf, 0xbf})
	add("F4 with a high continuation", []byte{0xf4, 0x90, 0x80, 0x80})
	// Overlongs and out-of-range leads.
	add("overlong C0", []byte{0xc0, 0x80})
	add("overlong C1", []byte{0xc1, 0xbf})
	add("lead F5", []byte{0xf5, 0x80, 0x80, 0x80})
	// Invalid bytes EMBEDDED in valid text, which is the realistic shape.
	add("embedded", []byte("a\xffb\xffc"))
	add("embedded run", []byte("a\xff\xffb"))
	add("multibyte then torn", append([]byte("アル"), 0xe3, 0x82))
	// A JSON row torn mid-rune, the actual failure this reader survives.
	add("torn row", append([]byte(`{"recorded_at": "2026-08-26T01`), 0xe3, 0x81))

	type answer struct {
		Text  string `json:"text"`
		Chars int    `json:"chars"`
	}
	var want []answer
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyDecodeSrc, &want, pyprobe.Arg(t, cases))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	// L1: the sweep must actually contain ill-formed input, or it compares
	// two identity functions.
	replaced := 0
	for _, w := range want {
		dec, _ := base64.StdEncoding.DecodeString(w.Text)
		for _, r := range string(dec) {
			if r == '�' {
				replaced++
				break
			}
		}
	}
	if replaced == 0 {
		t.Fatal("no fixture decoded to a replacement character — the sweep " +
			"never reached the branch it names")
	}

	for i := range cases {
		raw, err := base64.StdEncoding.DecodeString(cases[i])
		if err != nil {
			t.Fatal(err)
		}
		wantText, err := base64.StdEncoding.DecodeString(want[i].Text)
		if err != nil {
			t.Fatal(err)
		}
		got := decodeReplace(string(raw))
		if got != string(wantText) {
			t.Errorf("%s %x: cpython %q, go %q", labels[i], raw, wantText, got)
			continue
		}
		// The count, separately and explicitly, because it is what the
		// sixty-code-point window actually consumes.
		if n := len([]rune(got)); n != want[i].Chars {
			t.Errorf("%s %x: cpython %d chars, go %d", labels[i], raw,
				want[i].Chars, n)
		}
	}
}
