package pytext

import (
	"encoding/base64"
	"encoding/json"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The maximal-subpart table is a claim about which ill-formed prefixes
// CPython folds into ONE U+FFFD, and it is a claim two packages already
// hand-wrote before this one existed. Reading it is not checking it: the
// four cases a casual check would try -- b"\xc3", b"\xed\xa0\x80",
// b"\xc0\x80", b"\xff\xfe" -- all agree whether the table knows about the
// surrogate range or not, which is exactly how a wrong table survives.
//
// So: a sweep. Every one- and two-byte sequence is 65,792 inputs, which
// covers every lead byte against every possible second byte -- the
// boundary the table is actually about. The three- and four-byte cases a
// two-byte sweep cannot reach are enumerated after it.
//
// The sweep is ENUMERATED ON BOTH SIDES rather than shipped across, and
// the two enumerations are written to be read together: 65,792 base64
// blobs on a command line is `argument list too long`, and via a temp file
// the order would be the only thing tying the two halves together anyway.
func TestDecodeReplaceMatchesCPython(t *testing.T) {
	extras := [][]byte{
		{0xE2, 0x82}, {0xE2, 0x82, 0xAC}, {0x61, 0xE2, 0x82, 0x62},
		{0xED, 0xA0, 0x80}, {0xED, 0x9F, 0xBF}, {0xED, 0xA0}, {0xED, 0xBF},
		{0xF0, 0x9F, 0x98}, {0xF0, 0x9F, 0x98, 0x81}, {0xF0, 0x8F, 0xBF, 0xBF},
		{0xF4, 0x8F, 0xBF, 0xBF}, {0xF4, 0x90, 0x80, 0x80},
		{0xE0, 0x80, 0x80}, {0xE0, 0xA0, 0x80}, {0xC0, 0x80}, {0xC1, 0xBF},
		{0xF5, 0x80, 0x80, 0x80}, {0xFF, 0xFE, 0xFD},
		{0xE1, 0x80}, {0xEC, 0xBF, 0xBF}, {0xEE, 0x80}, {0xF1, 0x80, 0x80},
		{0xF3, 0xBF, 0xBF}, {0x61, 0xFF, 0xFF, 0x62},
	}
	cases := [][]byte{}
	for b0 := 0; b0 < 256; b0++ {
		cases = append(cases, []byte{byte(b0)})
		for b1 := 0; b1 < 256; b1++ {
			cases = append(cases, []byte{byte(b0), byte(b1)})
		}
	}
	cases = append(cases, extras...)

	blobs := make([]string, 0, len(extras))
	for _, c := range extras {
		blobs = append(blobs, base64.StdEncoding.EncodeToString(c))
	}
	arg, err := json.Marshal(blobs)
	if err != nil {
		t.Fatal(err)
	}

	const src = `
import base64, json, sys
raws = []
for b0 in range(256):
    raws.append(bytes([b0]))
    for b1 in range(256):
        raws.append(bytes([b0, b1]))
for blob in json.loads(sys.argv[1]):
    raws.append(base64.b64decode(blob.encode("ascii")))
print(json.dumps([r.decode("utf-8", errors="replace") for r in raws]))
`
	var want []string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want, string(arg))
	if len(want) != len(cases) {
		t.Fatalf("the probe answered for %d inputs, not %d — the two "+
			"enumerations have drifted and nothing below is measuring "+
			"what it says", len(want), len(cases))
	}
	bad := 0
	for i, c := range cases {
		got := DecodeReplace(c)
		if got != want[i] {
			bad++
			if bad <= 10 {
				t.Errorf("DecodeReplace(% x) = %q, CPython says %q",
					c, got, want[i])
			}
		}
	}
	if bad > 10 {
		t.Errorf("... and %d more", bad-10)
	}
}
