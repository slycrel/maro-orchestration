package pyval

import (
	"strings"
	"testing"
)

// escapeCases is MEASURED, not reasoned: every `want` below is the literal
// output of CPython's `json.dumps(v)` and `json.dumps(v, ensure_ascii=False)`
// on this box, transcribed by a generator rather than typed by hand.
//
// The table exists because ensure_ascii is the one renderer knob whose two
// settings are BOTH live in this workspace — mission.json takes the default
// and escapes, task_store passes ensure_ascii=False and does not — so a
// single-mode port is wrong for half its callers and nothing about the
// resulting file looks wrong until Python reads it back.
var escapeCases = []struct {
	name string
	in   string
	ea   string // json.dumps(v)
	raw  string // json.dumps(v, ensure_ascii=False)
}{
	{"an empty string", "", "\"\"", "\"\""},
	{"plain ascii", "plain ascii", "\"plain ascii\"", "\"plain ascii\""},
	{"DEL, which is ASCII and escaped anyway", "\u007f", "\"\\u007f\"", "\"\u007f\""},
	{"a C1 control", "\u0085", "\"\\u0085\"", "\"\u0085\""},
	{"NBSP", "\u00a0", "\"\\u00a0\"", "\"\u00a0\""},
	{"a Latin-1 letter", "\u00e9", "\"\\u00e9\"", "\"\u00e9\""},
	{"CJK", "\u65e5\u672c\u8a9e", "\"\\u65e5\\u672c\\u8a9e\"", "\"\u65e5\u672c\u8a9e\""},
	{"an astral code point", "\U0001f389", "\"\\ud83c\\udf89\"", "\"\U0001f389\""},
	{"LINE SEPARATOR", "\u2028", "\"\\u2028\"", "\"\u2028\""},
	{"PARAGRAPH SEPARATOR", "\u2029", "\"\\u2029\"", "\"\u2029\""},
	{"the five short escapes", "\u0008\u0009\u000a\u000c\u000d", "\"\\b\\t\\n\\f\\r\"", "\"\\b\\t\\n\\f\\r\""},
	{"a C0 control with no short escape", "\u0001\u001f", "\"\\u0001\\u001f\"", "\"\\u0001\\u001f\""},
	{"the two mandatory escapes", "\"\\", "\"\\\"\\\\\"", "\"\\\"\\\\\""},
	{"a solidus, which json.dumps leaves alone", "/", "\"/\"", "\"/\""},
	{"HTML-ish text, which json.dumps leaves alone", "<a href='x'>&amp;</a>", "\"<a href='x'>&amp;</a>\"", "\"<a href='x'>&amp;</a>\""},
	{"a mix of every class", "caf\u00e9 \u2014 \U0001f600 \u007f end", "\"caf\\u00e9 \\u2014 \\ud83d\\ude00 \\u007f end\"", "\"caf\u00e9 \u2014 \U0001f600 \u007f end\""},
}

func TestStringEscapingMatchesPythonInBothModes(t *testing.T) {
	for _, c := range escapeCases {
		got, err := encodeString(c.in, true)
		if err != nil {
			t.Fatalf("%s: ensure_ascii=True: %v", c.name, err)
		}
		if got != c.ea {
			t.Errorf("%s: ensure_ascii=True\n got %q\nwant %q", c.name, got, c.ea)
		}
		got, err = encodeString(c.in, false)
		if err != nil {
			t.Fatalf("%s: ensure_ascii=False: %v", c.name, err)
		}
		if got != c.raw {
			t.Errorf("%s: ensure_ascii=False\n got %q\nwant %q", c.name, got, c.raw)
		}
	}
}

// EncodeString is the exported spelling and must be the ESCAPING one:
// json.dumps' own default is ensure_ascii=True, and a wrapper that quietly
// picked the other would make every existing caller write different bytes
// without touching a single call site.
func TestEncodeStringIsTheEnsureASCIIOne(t *testing.T) {
	got, err := EncodeString("café")
	if err != nil {
		t.Fatal(err)
	}
	if got != `"caf\u00e9"` {
		t.Errorf("EncodeString should escape:\n got %q\nwant %q", got, `"caf\u00e9"`)
	}
}

// Only strings change between the modes. Structure, indentation, key order,
// separators and number spelling are ensure_ascii's business in neither
// runtime — if a shape difference ever creeps in, it is a bug in this
// renderer and not a Python behaviour worth copying.
func TestOnlyTheStringsChangeBetweenTheTwoModes(t *testing.T) {
	v := Obj{
		{Key: "café", Val: "naïve"},
		{Key: "n", Val: 1.5},
		{Key: "flag", Val: true},
		{Key: "none", Val: nil},
		{Key: "list", Val: List{1, "é", Obj{{Key: "deep", Val: "ü"}}}},
		{Key: "empty", Val: Obj{}},
	}
	ea, err := DumpsIndent2(v)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := DumpsIndent2Raw(v)
	if err != nil {
		t.Fatal(err)
	}
	if ea == raw {
		t.Fatal("the two modes produced identical output; one of them is not doing its job")
	}
	// Fold the escapes back and the two must be the same document.
	folded := strings.NewReplacer(
		`\u00e9`, "é", `\u00ef`, "ï", `\u00fc`, "ü",
	).Replace(ea)
	if folded != raw {
		t.Errorf("the modes differ by more than string escaping:\n ea %s\nraw %s", folded, raw)
	}
	if !strings.Contains(raw, "\"none\": null") || !strings.Contains(raw, "\"n\": 1.5") ||
		!strings.Contains(raw, "\"empty\": {}") {
		t.Errorf("ensure_ascii=False disturbed a non-string spelling:\n%s", raw)
	}
}

// Byte-tainted text is refused in BOTH modes. ensure_ascii=False is the
// mode where a lone surrogate would otherwise be written out raw and make
// the file undecodable, so the refusal matters more there, not less.
func TestTaintedTextIsRefusedInBothModes(t *testing.T) {
	tainted := "ok\xffbad"
	for _, ea := range []bool{true, false} {
		if _, err := encodeString(tainted, ea); err == nil {
			t.Errorf("ensure_ascii=%v accepted byte-tainted text", ea)
		}
	}
	if _, err := DumpsIndent2Raw(Obj{{Key: "k", Val: tainted}}); err == nil {
		t.Error("DumpsIndent2Raw accepted byte-tainted text")
	}
	if _, err := DumpsIndent2Raw(Obj{{Key: tainted, Val: "v"}}); err == nil {
		t.Error("DumpsIndent2Raw accepted a byte-tainted KEY")
	}
}
