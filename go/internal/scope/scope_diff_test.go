package scope

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The differential for src/scope.py's deterministic half: the two markdown
// parsers, the deliverable-line parser, the preconditions splitter, the
// three renderers, the clarification heuristic and the proxy-response
// parser.
//
// SHAPE. Every row below is an INPUT and a `kind`. There is no expected
// value written down anywhere in this file — CPython is asked at test time,
// through scopePySrc, and the port is asked separately over the same input.
// A table of literal expectations would be a transcription of what CPython
// did the day it was typed.
//
// ONE NORMALISATION, named. CPython's answers arrive as decoded JSON, so the
// port's are round-tripped through encoding/json before comparison — which
// makes every number a float64 and every object a map[string]any on both
// sides. That erases exactly one property worth having, KEY ORDER, and both
// places order is observable emit it as an explicit list instead:
// proxy_resolution's three keys (they reach the captain's log through
// json.dumps, which writes insertion order) and _parse_proxy_response's two.
//
// It does NOT erase nil-versus-empty: a nil Go slice marshals to null and an
// empty one to [], so `preconditions: []` in a CPython dataclass fails
// against a nil slice here. That is deliberate — the first draft of
// parseDeliverableLine had exactly that bug and this is what found it.

const scopePySrc = `
import json, sys
import scope

def deliv(d):
    return {"name": d.name, "description": d.description,
            "preconditions": d.preconditions, "shape": d.shape,
            "line": d.to_markdown_line()}

def sset(s):
    return {"failure_modes": s.failure_modes, "in_scope": s.in_scope,
            "out_of_scope": s.out_of_scope, "raw_text": s.raw_text,
            "to_markdown": s.to_markdown(), "is_empty": s.is_empty()}

out = {}
for c in json.loads(sys.argv[1]):
    k, name = c["kind"], c["name"]
    if k == "split_sections":
        secs = scope._split_sections(c["text"])
        out[name] = {"keys": sorted(secs.keys()),
                     "order": list(secs.keys()),
                     "sections": secs}
    elif k == "split_preconditions":
        out[name] = scope._split_preconditions(c["text"])
    elif k == "parse_deliverable":
        out[name] = deliv(scope._parse_deliverable_line(c["text"]))
    elif k == "parse_scope":
        out[name] = sset(scope._parse_scope_markdown(c["text"]))
    elif k == "parse_intent":
        ri = scope._parse_resolved_intent_markdown(c["text"])
        # An explicit proxy_resolution, when the row carries one, arrives as
        # ORDERED PAIRS so the dict is built in the order the test means.
        pairs = c.get("pairs")
        if pairs is not None:
            ri.scope.proxy_resolution = {p[0]: p[1] for p in pairs}
        out[name] = {"scope": sset(ri.scope),
                     "deliverables": [deliv(d) for d in ri.deliverables],
                     "raw_text": ri.raw_text,
                     "to_markdown": ri.to_markdown(),
                     "is_empty": ri.is_empty()}
    elif k == "clarification":
        out[name] = scope._looks_like_clarification(c["text"])
    elif k == "proxy_response":
        p = scope._parse_proxy_response(c["text"])
        out[name] = None if p is None else {
            "keys": list(p.keys()), "values": [p[x] for x in p]}
    else:
        raise SystemExit("unknown kind %r" % k)
print(json.dumps(out))
`

type scopeCase struct {
	name string
	kind string
	text string
	// pairs is the ordered proxy_resolution a parse_intent row installs
	// before rendering. Nil leaves the dataclass default (an empty dict).
	pairs [][2]any
}

// Characters built from code points rather than typed, because a fixture
// whose subject is WHICH code point it carries cannot be reviewed if the
// answer is "look closely at that space".
var (
	nbsp   = string(rune(0x00A0)) // NO-BREAK SPACE — \s in Python, not in Go
	vtab   = string(rune(0x000B)) // LINE TABULATION — same
	fs28   = string(rune(0x001C)) // FILE SEPARATOR — \s and .isspace() in Python
	ideosp = string(rune(0x3000)) // IDEOGRAPHIC SPACE — same
	dotI   = string(rune(0x0130)) // LATIN CAPITAL I WITH DOT ABOVE
	dotles = string(rune(0x0131)) // LATIN SMALL DOTLESS I
	sigma  = string(rune(0x03A3)) // GREEK CAPITAL SIGMA — lower() is context-sensitive
	cjk    = string(rune(0x6F22)) // CJK UNIFIED IDEOGRAPH — one rune, three bytes
	eacute = string(rune(0x00E9)) // LATIN SMALL E WITH ACUTE — one rune, two bytes
)

// A well-formed four-section response, the shape the prompt asks for. Used
// as the CONTROL beside every exotic row (rule 3), and as the body several
// rows mutate one heading of.
const goodResponse = "## Failure Modes\n" +
	"- the websocket drops and session state is lost\n" +
	"- the binary never builds on the target toolchain\n" +
	"\n## In Scope\n" +
	"- reconnection with session recovery\n" +
	"\n## Out of Scope\n" +
	"- multi-region replication\n" +
	"\n## Deliverables\n" +
	"- cmd/server/main.go: HTTP server serving /ws [preconditions: Go toolchain, gorilla/websocket] [shape: runtime]\n" +
	"- docs/PROTOCOL.md: the wire format [shape: document]\n"

func scopeCases() []scopeCase {
	return []scopeCase{
		// --- split_sections: the control, then every branch of _normalize --
		{name: "the well-formed response", kind: "split_sections", text: goodResponse},
		{name: "empty text", kind: "split_sections", text: ""},
		{name: "whitespace only", kind: "split_sections", text: "   \n  \n"},
		{name: "no headings at all, only bullets", kind: "split_sections",
			text: "- one\n- two\n"},
		// _normalize's four keys and their alternate spellings. A list is
		// not a class: a mistyped member is invisible to a fixture that
		// matches an earlier one.
		{name: "heading Failure Modes", kind: "split_sections", text: "## Failure Modes\n- a\n"},
		{name: "heading failure", kind: "split_sections", text: "## failure\n- a\n"},
		{name: "heading Modes alone", kind: "split_sections", text: "## Modes\n- a\n"},
		{name: "heading Model is a failure mode", kind: "split_sections",
			text: "## Model\n- a\n"},
		{name: "heading Out of Scope", kind: "split_sections", text: "## Out of Scope\n- a\n"},
		{name: "heading Out-of-Scope", kind: "split_sections", text: "## Out-of-Scope\n- a\n"},
		{name: "heading OutOfScope", kind: "split_sections", text: "## OutOfScope\n- a\n"},
		{name: "heading In Scope", kind: "split_sections", text: "## In Scope\n- a\n"},
		{name: "heading In-Scope", kind: "split_sections", text: "## In-Scope\n- a\n"},
		{name: "heading InScope", kind: "split_sections", text: "## InScope\n- a\n"},
		{name: "heading Deliverables", kind: "split_sections", text: "## Deliverables\n- a\n"},
		{name: "heading Artifacts", kind: "split_sections", text: "## Artifacts\n- a\n"},
		{name: "heading unrecognised", kind: "split_sections", text: "## Notes\n- a\n"},
		// The precedence question the four ifs answer, made observable.
		{name: "heading Failure Modes Out of Scope", kind: "split_sections",
			text: "## Failure Modes Out of Scope\n- a\n"},
		{name: "heading Out of Scope Deliverables", kind: "split_sections",
			text: "## Out of Scope Deliverables\n- a\n"},
		{name: "heading In Scope Artifacts", kind: "split_sections",
			text: "## In Scope Artifacts\n- a\n"},
		// Headings that satisfy MORE THAN ONE branch, which is the only
		// thing that can observe the branch ORDER. Without these, moving
		// in-scope above out-of-scope survives the whole corpus -- the
		// order is documented as load-bearing in scope.go and nothing
		// tested it, because every other heading here matches exactly one.
		{name: "both scope branches, out-of-scope wins", kind: "split_sections",
			text: "## Items in scope and out of scope\n- a\n"},
		{name: "the same pair, other word order", kind: "split_sections",
			text: "## Out of scope vs in scope\n- a\n"},
		{name: "failure beats out-of-scope", kind: "split_sections",
			text: "## Out of scope failure modes\n- a\n"},
		{name: "failure beats deliverables", kind: "split_sections",
			text: "## Deliverable failure modes\n- a\n"},
		{name: "the bare 'mode' alternative beats in-scope", kind: "split_sections",
			text: "## In scope model choices\n- a\n"},
		{name: "out-of-scope beats deliverables", kind: "split_sections",
			text: "## Out of scope artifacts\n- a\n"},

		// Heading levels, colons, and the strip/rstrip ORDER. "In Scope : "
		// strips the trailing space FIRST, so the colon is no longer last
		// and rstrip(":") cannot reach it -- it stays and does not match.
		//
		// That reading is WRONG and the fixtures below cannot show it:
		// rstrip(":") on "in scope :" gives "in scope ", which still
		// CONTAINS "in scope", so the heading matches. Every branch is a
		// substring test, so no colon or space can change an answer --
		// see normalizeSectionKey, where the three trimming mutations are
		// recorded as structurally unkillable. These rows pin agreement
		// with CPython on the shapes; they are not evidence about order.
		{name: "one hash", kind: "split_sections", text: "# In Scope\n- a\n"},
		{name: "four hashes", kind: "split_sections", text: "#### In Scope\n- a\n"},
		{name: "five hashes is not a heading level", kind: "split_sections",
			text: "##### In Scope\n- a\n"},
		{name: "trailing colon", kind: "split_sections", text: "## In Scope:\n- a\n"},
		{name: "colon then space", kind: "split_sections", text: "## In Scope: \n- a\n"},
		{name: "space then colon", kind: "split_sections", text: "## In Scope :\n- a\n"},
		{name: "two trailing colons", kind: "split_sections", text: "## In Scope::\n- a\n"},
		{name: "no space after hashes", kind: "split_sections", text: "##In Scope\n- a\n"},
		{name: "hashes alone", kind: "split_sections", text: "####\n- a\n"},
		{name: "one hash alone is not a heading", kind: "split_sections", text: "#\n- a\n"},
		// The unrecognised-heading RESET, and the twice-declared section.
		{name: "unrecognised heading drops the bullets after it", kind: "split_sections",
			text: "## In Scope\n- keep\n## Notes\n- drop\n"},
		{name: "the same section twice keeps the last", kind: "split_sections",
			text: "## In Scope\n- first\n## In Scope\n- second\n"},
		{name: "a section with no bullets", kind: "split_sections",
			text: "## In Scope\n## Out of Scope\n- a\n"},
		// Bullet markers and the lstrip cutset.
		{name: "star bullets", kind: "split_sections", text: "## In Scope\n* a\n"},
		{name: "indented bullets", kind: "split_sections", text: "## In Scope\n   - a\n"},
		{name: "a bullet of only dashes and stars", kind: "split_sections",
			text: "## In Scope\n- -*- \n- real\n"},
		{name: "a bullet whose text starts with a star", kind: "split_sections",
			text: "## In Scope\n- *bold* item\n"},
		{name: "a plus is not a bullet", kind: "split_sections", text: "## In Scope\n+ a\n"},
		{name: "bullets before any heading", kind: "split_sections",
			text: "- orphan\n## In Scope\n- a\n"},
		// The \s divergence, in the heading pattern and in the strip.
		{name: "heading padded with NBSP", kind: "split_sections",
			text: "##" + nbsp + "In Scope" + nbsp + "\n- a\n"},
		{name: "heading padded with VT", kind: "split_sections",
			text: "##" + vtab + "In Scope" + vtab + "\n- a\n"},
		{name: "heading padded with FS", kind: "split_sections",
			text: "##" + fs28 + "In Scope" + fs28 + "\n- a\n"},
		{name: "heading padded with ideographic space", kind: "split_sections",
			text: "##" + ideosp + "In Scope" + ideosp + "\n- a\n"},
		{name: "bullet padded with NBSP", kind: "split_sections",
			text: "## In Scope\n" + nbsp + "-" + nbsp + "a" + nbsp + "\n"},
		// lower()'s Unicode half, reached through the heading normaliser.
		{name: "heading with a dotted capital I", kind: "split_sections",
			text: "## " + dotI + "n Scope\n- a\n"},
		{name: "heading with a dotless i", kind: "split_sections",
			text: "## " + dotles + "n Scope\n- a\n"},
		{name: "heading with a final sigma", kind: "split_sections",
			text: "## MODE" + sigma + "\n- a\n"},
		{name: "heading with a medial sigma", kind: "split_sections",
			text: "## MODE" + sigma + "S\n- a\n"},
		// CRLF: \r is \s in both engines but is not a line terminator to
		// str.split("\n"), so it rides on the end of every line.
		{name: "CRLF line endings", kind: "split_sections",
			text: "## In Scope\r\n- a\r\n"},

		// --- split_preconditions -------------------------------------------
		{name: "pre plain list", kind: "split_preconditions", text: "Go toolchain, curl"},
		{name: "pre empty", kind: "split_preconditions", text: ""},
		{name: "pre only a comma", kind: "split_preconditions", text: ","},
		{name: "pre trailing comma", kind: "split_preconditions", text: "curl,"},
		{name: "pre leading comma", kind: "split_preconditions", text: ",curl"},
		{name: "pre only spaces", kind: "split_preconditions", text: "   "},
		{name: "pre the d2f4e2f4 shape", kind: "split_preconditions",
			text: "standard utilities (grep, cat, wc), curl"},
		{name: "pre nested parens", kind: "split_preconditions",
			text: "a (b (c, d), e), f"},
		{name: "pre unbalanced open swallows the rest", kind: "split_preconditions",
			text: "a (b, c, d"},
		{name: "pre unbalanced close clamps at zero", kind: "split_preconditions",
			text: "a) , b"},
		{name: "pre close before open", kind: "split_preconditions",
			text: "a), b (c, d)"},
		{name: "pre multibyte items", kind: "split_preconditions",
			text: cjk + eacute + ", " + cjk},
		{name: "pre NBSP padding is stripped", kind: "split_preconditions",
			text: nbsp + "curl" + nbsp + "," + nbsp + "jq" + nbsp},
		{name: "pre VT padding is stripped", kind: "split_preconditions",
			text: vtab + "curl" + vtab},

		// --- parse_deliverable_line ----------------------------------------
		{name: "deliv full", kind: "parse_deliverable",
			text: "cmd/server/main.go: HTTP server [preconditions: Go, gorilla] [shape: runtime]"},
		{name: "deliv empty", kind: "parse_deliverable", text: ""},
		{name: "deliv name only", kind: "parse_deliverable", text: "README.md"},
		{name: "deliv name and description", kind: "parse_deliverable",
			text: "README.md: the readme"},
		{name: "deliv shape before preconditions", kind: "parse_deliverable",
			text: "x: y [shape: data] [preconditions: sqlite]"},
		{name: "deliv shape only", kind: "parse_deliverable", text: "x: y [shape: document]"},
		{name: "deliv preconditions only", kind: "parse_deliverable",
			text: "x: y [precondition: curl]"},
		{name: "deliv unknown shape is dropped", kind: "parse_deliverable",
			text: "x: y [shape: binary]"},
		{name: "deliv shape uppercase", kind: "parse_deliverable",
			text: "x: y [shape: RUNTIME]"},
		{name: "deliv shape padded", kind: "parse_deliverable",
			text: "x: y [shape:   runtime   ]"},
		{name: "deliv shape blank", kind: "parse_deliverable", text: "x: y [shape:  ]"},
		{name: "deliv annotation keyword uppercase", kind: "parse_deliverable",
			text: "x: y [PRECONDITIONS: curl] [SHAPE: data]"},
		// The (?i) fold divergence, at both keywords. CPython's IGNORECASE
		// matches U+0130/U+0131 as `i`; Go's (?i) does not.
		{name: "deliv preconditions with a dotless i", kind: "parse_deliverable",
			text: "x: y [precond" + dotles + "t" + dotles + "ons: curl]"},
		{name: "deliv preconditions with a dotted capital I", kind: "parse_deliverable",
			text: "x: y [PRECOND" + dotI + "T" + dotI + "ONS: curl]"},
		{name: "deliv annotation padded with NBSP", kind: "parse_deliverable",
			text: "x: y [preconditions:" + nbsp + "curl" + nbsp + "]"},
		{name: "deliv two colons", kind: "parse_deliverable", text: "a: b: c"},
		{name: "deliv leading colon", kind: "parse_deliverable", text: ": just a description"},
		{name: "deliv trailing colon", kind: "parse_deliverable", text: "name:"},
		{name: "deliv annotation is the whole line", kind: "parse_deliverable",
			text: "[shape: runtime]"},
		{name: "deliv annotation leaves an empty name", kind: "parse_deliverable",
			text: "[preconditions: curl]"},
		{name: "deliv two shape annotations", kind: "parse_deliverable",
			text: "x: y [shape: data] [shape: runtime]"},
		{name: "deliv multibyte offsets", kind: "parse_deliverable",
			text: cjk + eacute + ": " + cjk + " [preconditions: " + cjk + "] [shape: data]"},
		{name: "deliv unclosed bracket", kind: "parse_deliverable",
			text: "x: y [shape: runtime"},
		{name: "deliv nested brackets", kind: "parse_deliverable",
			text: "x: y [preconditions: a [b], c]"},

		// --- parse_scope_markdown ------------------------------------------
		{name: "scope the control", kind: "parse_scope", text: goodResponse},
		{name: "scope empty", kind: "parse_scope", text: ""},
		{name: "scope whitespace keeps its raw text", kind: "parse_scope", text: "   \n "},
		{name: "scope NBSP-only keeps its raw text", kind: "parse_scope", text: nbsp},
		{name: "scope prose with no headings", kind: "parse_scope",
			text: "I need more information about what you want."},
		{name: "scope deliverables only", kind: "parse_scope",
			text: "## Deliverables\n- x: y\n"},
		{name: "scope failure modes only", kind: "parse_scope",
			text: "## Failure Modes\n- a\n"},

		// --- parse_resolved_intent_markdown + the three renderers ----------
		{name: "intent the control", kind: "parse_intent", text: goodResponse},
		{name: "intent empty", kind: "parse_intent", text: ""},
		{name: "intent whitespace", kind: "parse_intent", text: "  "},
		{name: "intent deliverables only", kind: "parse_intent",
			text: "## Deliverables\n- x: y [shape: data]\n"},
		{name: "intent malformed deliverables are dropped", kind: "parse_intent",
			text: "## Deliverables\n- [shape: runtime]\n- real: thing\n"},
		{name: "intent scope only", kind: "parse_intent",
			text: "## In Scope\n- a\n## Out of Scope\n- b\n"},
		// to_markdown's interpretation block: present, present-with-reason,
		// blank, whitespace-only, and a NON-STRING value that str() spells.
		{name: "intent with a proxy interpretation", kind: "parse_intent",
			text: goodResponse,
			pairs: [][2]any{{"interpretation", "Count markdown files under docs/"},
				{"reason", "the goal said docs"}}},
		{name: "intent with an interpretation and no reason", kind: "parse_intent",
			text:  goodResponse,
			pairs: [][2]any{{"interpretation", "Count markdown files"}, {"reason", ""}}},
		{name: "intent with a whitespace interpretation", kind: "parse_intent",
			text:  goodResponse,
			pairs: [][2]any{{"interpretation", "  "}, {"reason", "r"}}},
		{name: "intent with a whitespace reason", kind: "parse_intent",
			text:  goodResponse,
			pairs: [][2]any{{"interpretation", "i"}, {"reason", nbsp}}},
		{name: "intent with an interpretation and empty scope", kind: "parse_intent",
			text:  "no sections here at all",
			pairs: [][2]any{{"interpretation", "do the thing"}, {"reason", "because"}}},
		{name: "intent with a numeric interpretation", kind: "parse_intent",
			text:  goodResponse,
			pairs: [][2]any{{"interpretation", 7}, {"reason", nil}}},
		{name: "intent with a missing interpretation key", kind: "parse_intent",
			text: goodResponse, pairs: [][2]any{{"reason", "orphan"}}},

		// --- looks_like_clarification --------------------------------------
		// The two bounds are CODE POINTS. The 29-rune and 30-rune rows are
		// the floor; the CJK ones make a byte count answer differently.
		{name: "clar empty", kind: "clarification", text: ""},
		{name: "clar short with a question", kind: "clarification", text: "what?"},
		{name: "clar 29 runes with a question", kind: "clarification",
			text: strings.Repeat("a", 28) + "?"},
		{name: "clar 30 runes with a question", kind: "clarification",
			text: strings.Repeat("a", 29) + "?"},
		{name: "clar 30 runes without a question", kind: "clarification",
			text: strings.Repeat("a", 30)},
		{name: "clar 4000 runes with a question", kind: "clarification",
			text: strings.Repeat("a", 3999) + "?"},
		{name: "clar 4001 runes with a question", kind: "clarification",
			text: strings.Repeat("a", 4000) + "?"},
		{name: "clar 29 CJK runes is under the floor in code points", kind: "clarification",
			text: strings.Repeat(cjk, 28) + "?"},
		{name: "clar 30 CJK runes clears the floor", kind: "clarification",
			text: strings.Repeat(cjk, 29) + "?"},
		{name: "clar 4001 CJK runes is over the ceiling", kind: "clarification",
			text: strings.Repeat(cjk, 4000) + "?"},
		{name: "clar padded to length by NBSP that strip removes", kind: "clarification",
			text: strings.Repeat(nbsp, 10) + strings.Repeat("a", 25) + "?" +
				strings.Repeat(nbsp, 10)},
		{name: "clar the real thing", kind: "clarification",
			text: "Which directory should I count markdown files in, and do you " +
				"want nested directories included?"},
		{name: "clar a real scope response has no question mark", kind: "clarification",
			text: goodResponse},

		// --- parse_proxy_response ------------------------------------------
		{name: "proxy both fields", kind: "proxy_response",
			text: "INTERPRETATION: count md files under docs/\nREASON: the goal said docs"},
		{name: "proxy empty", kind: "proxy_response", text: ""},
		{name: "proxy no keyword", kind: "proxy_response", text: "I have no idea"},
		{name: "proxy whitespace only", kind: "proxy_response", text: "  \n  "},
		{name: "proxy keyword with no colon", kind: "proxy_response",
			text: "INTERPRETATION x\nREASON y"},
		{name: "proxy keyword inside a word", kind: "proxy_response",
			text: "MISINTERPRETATIONS are common"},
		{name: "proxy interpretation only", kind: "proxy_response",
			text: "INTERPRETATION: count md files"},
		{name: "proxy lowercase keywords", kind: "proxy_response",
			text: "interpretation: x\nreason: y"},
		{name: "proxy with a preamble", kind: "proxy_response",
			text: "Sure, here goes.\n\nINTERPRETATION: x\nREASON: y"},
		// The comment in scope.py says "find the LAST INTERPRETATION line".
		// re.search finds the first. This row measures which.
		{name: "proxy two interpretations", kind: "proxy_response",
			text: "INTERPRETATION: first\nREASON: r1\n\nINTERPRETATION: second\nREASON: r2"},
		{name: "proxy trailing newline", kind: "proxy_response",
			text: "INTERPRETATION: x\nREASON: y\n"},
		{name: "proxy trailing newlines and spaces", kind: "proxy_response",
			text: "INTERPRETATION: x\nREASON: y\n\n   \n"},
		{name: "proxy multiline interpretation under DOTALL", kind: "proxy_response",
			text: "INTERPRETATION: a\nb\nc"},
		{name: "proxy multiline interpretation then reason", kind: "proxy_response",
			text: "INTERPRETATION: a\nb\nREASON: r"},
		{name: "proxy blank interpretation", kind: "proxy_response",
			text: "INTERPRETATION:\nREASON: y"},
		{name: "proxy blank interpretation and no reason", kind: "proxy_response",
			text: "INTERPRETATION:   "},
		{name: "proxy padded colon", kind: "proxy_response",
			text: "INTERPRETATION   :   x   \nREASON   :   y   "},
		{name: "proxy several newlines before REASON", kind: "proxy_response",
			text: "INTERPRETATION: x\n\n\nREASON: y"},
		{name: "proxy REASON on the same line is part of the interpretation",
			kind: "proxy_response", text: "INTERPRETATION: x REASON: y"},
		{name: "proxy trailing REASON with no body", kind: "proxy_response",
			text: "INTERPRETATION: x\nREASON:"},
		// The (?i) fold, at the keyword CPython folds and Go does not.
		{name: "proxy dotless i in INTERPRETATION", kind: "proxy_response",
			text: "INTERPRETAT" + dotles + "ON: x\nREASON: y"},
		{name: "proxy dotted capital I in INTERPRETATION", kind: "proxy_response",
			text: "interpretat" + dotI + "on: x\nreason: y"},
		{name: "proxy dotless i in REASON is not an i", kind: "proxy_response",
			text: "INTERPRETATION: x\nREASON: y" + dotles},
		// The \s divergence around the colons.
		{name: "proxy NBSP around the colon", kind: "proxy_response",
			text: "INTERPRETATION" + nbsp + ":" + nbsp + "x"},
		{name: "proxy VT before REASON", kind: "proxy_response",
			text: "INTERPRETATION: x\n" + vtab + "REASON: y"},
		{name: "proxy trailing NBSP", kind: "proxy_response",
			text: "INTERPRETATION: x" + nbsp},
	}
}

// goAnswer computes the port's answer for one row, in the shape the probe
// emits. It calls the PRODUCTION functions and does no interpretation of
// their results beyond the JSON round trip the comparison needs.
func goAnswer(c scopeCase) any {
	switch c.kind {
	case "split_sections":
		secs := splitSections(c.text)
		keys := make([]string, 0, len(secs))
		for k := range secs {
			keys = append(keys, k)
		}
		sortStrings(keys)
		// `order` is CPython's insertion order, which a Go map does not
		// have. The port answers with the SORTED keys for both fields and
		// the comparison skips `order` — see
		// TestTheSectionsMapIsOnlyEverReadThroughSectionGet for why that is
		// sound rather than convenient.
		return map[string]any{"keys": keys, "order": nil, "sections": secs}
	case "split_preconditions":
		return splitPreconditions(c.text)
	case "parse_deliverable":
		return delivJSON(parseDeliverableLine(c.text))
	case "parse_scope":
		return setJSON(parseScopeMarkdown(c.text))
	case "parse_intent":
		ri := parseResolvedIntentMarkdown(c.text)
		if c.pairs != nil {
			pr := pyval.Obj{}
			for _, p := range c.pairs {
				pr = append(pr, pyval.Field{Key: p[0].(string), Val: p[1]})
			}
			ri.Scope.ProxyResolution = pr
		}
		ds := []any{}
		for _, d := range ri.Deliverables {
			ds = append(ds, delivJSON(d))
		}
		return map[string]any{
			"scope":        setJSON(ri.Scope),
			"deliverables": ds,
			"raw_text":     ri.RawText,
			"to_markdown":  ri.ToMarkdown(),
			"is_empty":     ri.IsEmpty(),
		}
	case "clarification":
		return looksLikeClarification(c.text)
	case "proxy_response":
		p := parseProxyResponse(c.text)
		if p == nil {
			return nil
		}
		keys := []any{}
		vals := []any{}
		for _, f := range p {
			keys = append(keys, f.Key)
			vals = append(vals, f.Val)
		}
		return map[string]any{"keys": keys, "values": vals}
	}
	panic("unknown kind " + c.kind)
}

// delivJSON renders a Deliverable the way the probe renders its dataclass.
// The ONE mapping: Shape "" stands for CPython's None, so it is emitted as
// nil. The table proves the mapping is not vacuous — "deliv unknown shape
// is dropped" reaches None and "deliv shape only" reaches "document".
func delivJSON(d Deliverable) map[string]any {
	var shape any
	if d.Shape != "" {
		shape = d.Shape
	}
	return map[string]any{
		"name": d.Name, "description": d.Description,
		"preconditions": d.Preconditions, "shape": shape,
		"line": d.ToMarkdownLine(),
	}
}

func setJSON(s Set) map[string]any {
	return map[string]any{
		"failure_modes": s.FailureModes, "in_scope": s.InScope,
		"out_of_scope": s.OutOfScope, "raw_text": s.RawText,
		"to_markdown": s.ToMarkdown(), "is_empty": s.IsEmpty(),
	}
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

// normalize round-trips a Go value through encoding/json so both sides are
// compared as decoded JSON — see the file header for what that erases and
// what it deliberately does not.
func normalize(t *testing.T, v any) any {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshalling the port's answer: %v", err)
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatalf("decoding the port's answer: %v", err)
	}
	return out
}

func TestTheDeterministicHalfAgreesWithCPython(t *testing.T) {
	cases := scopeCases()

	// ANTI-VACUITY (rule 2). A corpus that reaches one outcome cannot tell
	// a working implementation from a broken one, so the floors below say
	// what this table must keep separating. They are counted over the
	// PORT's answers, which is the side a mutation moves.
	var (
		sectionsWithKeys, sectionsEmpty        int
		delivWithShape, delivWithoutShape      int
		delivWithPre, delivWithoutPre          int
		clarTrue, clarFalse                    int
		proxyParsed, proxyNil, proxyWithReason int
		scopeEmpty, scopeNonEmpty              int
		intentWithDeliv, intentWithoutDeliv    int
		preMulti, preSingle                    int
	)
	for _, c := range cases {
		switch c.kind {
		case "split_sections":
			if len(splitSections(c.text)) > 0 {
				sectionsWithKeys++
			} else {
				sectionsEmpty++
			}
		case "split_preconditions":
			if len(splitPreconditions(c.text)) > 1 {
				preMulti++
			} else {
				preSingle++
			}
		case "parse_deliverable":
			d := parseDeliverableLine(c.text)
			if d.Shape != "" {
				delivWithShape++
			} else {
				delivWithoutShape++
			}
			if len(d.Preconditions) > 0 {
				delivWithPre++
			} else {
				delivWithoutPre++
			}
		case "parse_scope":
			if parseScopeMarkdown(c.text).IsEmpty() {
				scopeEmpty++
			} else {
				scopeNonEmpty++
			}
		case "parse_intent":
			if len(parseResolvedIntentMarkdown(c.text).Deliverables) > 0 {
				intentWithDeliv++
			} else {
				intentWithoutDeliv++
			}
		case "clarification":
			if looksLikeClarification(c.text) {
				clarTrue++
			} else {
				clarFalse++
			}
		case "proxy_response":
			p := parseProxyResponse(c.text)
			if p == nil {
				proxyNil++
				continue
			}
			proxyParsed++
			if r, _ := p.Get("reason"); r != nil && r.(string) != "" {
				proxyWithReason++
			}
		}
	}
	floors := []struct {
		what string
		n    int
		min  int
	}{
		{"split_sections rows that found a section", sectionsWithKeys, 25},
		{"split_sections rows that found none", sectionsEmpty, 5},
		{"preconditions rows splitting to more than one item", preMulti, 4},
		{"preconditions rows splitting to one or none", preSingle, 6},
		{"deliverable rows with a shape", delivWithShape, 5},
		{"deliverable rows without one", delivWithoutShape, 8},
		{"deliverable rows with preconditions", delivWithPre, 5},
		{"deliverable rows without", delivWithoutPre, 8},
		{"scope rows that parsed", scopeNonEmpty, 2},
		{"scope rows that did not", scopeEmpty, 4},
		{"intent rows with deliverables", intentWithDeliv, 4},
		{"intent rows without", intentWithoutDeliv, 4},
		{"clarification rows that said yes", clarTrue, 3},
		{"clarification rows that said no", clarFalse, 6},
		{"proxy rows that parsed", proxyParsed, 8},
		{"proxy rows that returned None", proxyNil, 4},
		{"proxy rows carrying a reason", proxyWithReason, 5},
	}
	for _, f := range floors {
		if f.n < f.min {
			t.Fatalf("anti-vacuity: only %d %s (floor %d) — this corpus can "+
				"no longer separate the outcomes it claims to measure",
				f.n, f.what, f.min)
		}
	}

	// Names must be unique: the probe keys its answers by name, and a
	// duplicate silently drops a row.
	seen := map[string]bool{}
	payload := make([]map[string]any, 0, len(cases))
	for _, c := range cases {
		if seen[c.name] {
			t.Fatalf("duplicate fixture name %q — the probe keys by name, so "+
				"one of the two would never be measured", c.name)
		}
		seen[c.name] = true
		row := map[string]any{"kind": c.kind, "name": c.name, "text": c.text}
		if c.pairs != nil {
			pairs := make([][2]any, len(c.pairs))
			copy(pairs, c.pairs)
			row["pairs"] = pairs
		}
		payload = append(payload, row)
	}

	var got map[string]any
	pyprobe.Probe{Marker: "scope.py"}.RunJSON(t, scopePySrc, &got,
		pyprobe.Arg(t, payload))

	if len(got) != len(cases) {
		t.Fatalf("CPython answered %d rows, the table has %d", len(got), len(cases))
	}
	for _, c := range cases {
		py, ok := got[c.name]
		if !ok {
			t.Errorf("%s: CPython returned no answer", c.name)
			continue
		}
		mine := normalize(t, goAnswer(c))
		if c.kind == "split_sections" {
			// See goAnswer: `order` is CPython's dict insertion order, which
			// the port's map cannot carry. Dropped from BOTH sides here so
			// the rest of the row is still compared.
			if pm, ok := py.(map[string]any); ok {
				pm["order"] = nil
			}
		}
		if !reflect.DeepEqual(mine, py) {
			t.Errorf("%s (%s): the port and CPython disagree\n  port:   %#v\n  python: %#v",
				c.name, c.kind, mine, py)
		}
	}
}

// TestTheSystemPromptIsByteIdenticalToCPython pins prompt.go, which is
// GENERATED from scope.py. 2904 code points of prose is not something a
// reviewer can diff by eye, and the prompt decides what the artifact the
// planner and closure both read even looks like.
func TestTheSystemPromptIsByteIdenticalToCPython(t *testing.T) {
	const src = `
import json, scope
print(json.dumps({"prompt": scope._SCOPE_SYSTEM}))
`
	var got struct {
		Prompt string `json:"prompt"`
	}
	pyprobe.Probe{Marker: "scope.py"}.RunJSON(t, src, &got)
	if got.Prompt != SystemPrompt {
		t.Errorf("SystemPrompt has drifted from scope._SCOPE_SYSTEM\n"+
			"  go:     %d bytes / %d runes\n  python: %d bytes / %d runes\n"+
			"  regenerate prompt.go from the Python source rather than "+
			"hand-editing it",
			len(SystemPrompt), len([]rune(SystemPrompt)),
			len(got.Prompt), len([]rune(got.Prompt)))
		// Name the first differing byte — a 2.9KB diff in a failure message
		// is unreadable otherwise.
		n := len(SystemPrompt)
		if len(got.Prompt) < n {
			n = len(got.Prompt)
		}
		for i := 0; i < n; i++ {
			if SystemPrompt[i] != got.Prompt[i] {
				lo := i - 40
				if lo < 0 {
					lo = 0
				}
				t.Errorf("first difference at byte %d:\n  go:     %q\n  python: %q",
					i, SystemPrompt[lo:min(i+20, len(SystemPrompt))],
					got.Prompt[lo:min(i+20, len(got.Prompt))])
				break
			}
		}
	}
	// The raw-string literal's two preconditions, asserted rather than
	// believed: prompt.go splices `bt` at every backtick and would not
	// compile if one were missed, but a carriage return would compile and
	// change the prompt.
	if strings.Contains(SystemPrompt, "\r") {
		t.Error("SystemPrompt contains a carriage return; the generated raw " +
			"string cannot hold one faithfully")
	}
	if n := strings.Count(SystemPrompt, "`"); n != 12 {
		t.Errorf("SystemPrompt carries %d backticks, prompt.go's comment says 12", n)
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
