package provenance

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The provenance gate is a QUARANTINE decision made once, at mint and at
// pack import, and never revisited: a lesson the classifier calls
// "outcome" is injectable into every future run forever. Until now this
// package's only evidence that the port agrees with
// src/lesson_provenance.py was that the two regex strings look alike —
// which is a claim about ASCII, and both engines were only ever fed
// ASCII by the existing tests.
//
// They are not the same engine. Python's `re` is Unicode-aware by
// default: `\s` is 29 code points, `\w` is 142,940, and IGNORECASE folds
// through a table that carries the Turkish i. Go's regexp spells all
// three ASCII-only (`\s` is 5, `\w` is 63). Every fixture below that
// carries a non-ASCII (or C0) character is there because that difference
// decides whether a lesson is quarantined, and the direction is not
// uniformly safe — see the `goDiff` rows.
//
// classify_lesson_provenance takes three strings and returns one of two
// strings, so ONE probe can carry the whole table: there is no per-case
// interpreter state, and 90 python3 spawns on this box buys nothing.
const pyClassifySrc = `
import json, sys
import lesson_provenance as lp

out = {}
for c in json.loads(sys.argv[1]):
    out[c["name"]] = lp.classify_lesson_provenance(
        c["lesson"], c["goal"], c["evidence"])
print(json.dumps(out))
`

// Built from code points rather than written as literals. Every one of
// these is invisible or near-invisible in a source file, and a fixture
// whose whole subject is WHICH code point it carries cannot be reviewed
// if the answer is "look closely at that space".
var (
	nbsp   = string(rune(0x00A0)) // NO-BREAK SPACE
	vtab   = string(rune(0x000B)) // LINE TABULATION — \s in Python, not in Go
	emsp   = string(rune(0x2003)) // EM SPACE
	ideosp = string(rune(0x3000)) // IDEOGRAPHIC SPACE
	nel    = string(rune(0x0085)) // NEXT LINE
	nul    = string(rune(0x0000)) // NULL — \s in neither
	dotI   = string(rune(0x0130)) // LATIN CAPITAL LETTER I WITH DOT ABOVE
	dotles = string(rune(0x0131)) // LATIN SMALL LETTER DOTLESS I
	longS  = string(rune(0x017F)) // LATIN SMALL LETTER LONG S
	nbhyph = string(rune(0x2011)) // NON-BREAKING HYPHEN
	cyrYer = string(rune(0x042B)) // CYRILLIC CAPITAL LETTER YERU
	eacute = string(rune(0x00E9)) // LATIN SMALL LETTER E WITH ACUTE
	cjk    = string(rune(0x6F22)) // CJK UNIFIED IDEOGRAPH-6F22
	ldquo  = string(rune(0x201C)) // LEFT DOUBLE QUOTATION MARK
)

// provCase is one classification fixture.
//
// py is what CPython is CLAIMED to answer, written down before either
// implementation ran; the test checks the claim before it compares
// anything, so a fixture that quietly stopped exercising its branch (a
// typo'd keyword, a goal text that no longer echoes the lesson) is a
// failure rather than a green row measuring nothing. Two rows in the
// first draft of this table were exactly that.
//
// goDiff is empty when the port agrees. When it is set, the port answers
// that INSTEAD, and the row is a pinned divergence with a stated reason:
// the test then requires the disagreement to persist, so closing one of
// them fails here and the ledger has to be updated deliberately.
type provCase struct {
	name     string
	lesson   string
	goal     string
	evidence string
	py       string
	goDiff   string
	why      string
}

// The four `why` strings the pinned rows share. Named, so the ledger is
// countable and a row cannot invent a private wording for a cause that
// already exists.
const (
	whySpace = "Python's \\s is 29 code points, Go's is 5: the separator " +
		"inside the phrase is one of the 24 Go does not honour"
	whyBoundary = "Python's \\w is Unicode, Go's is [0-9A-Za-z_]: a non-ASCII " +
		"letter touching the keyword suppresses \\b in Python and not in Go"
	whyFold = "Python's IGNORECASE folds U+0130/U+0131 to i, Go's " +
		"unicode.SimpleFold does not"
)

func classifyCases() []provCase {
	// The scaffolding window is `[^.]{0,60}`, counted in code points. The
	// gap includes the space on both sides, so 58 filler runes is exactly
	// 60 and 59 is one past it.
	gap := func(fill string, n int) string {
		return "cannot use " + strings.Repeat(fill, n) + " as an excuse"
	}
	return []provCase{
		// --- the shapes tests/test_lesson_provenance.py pins --------------
		{name: "db37d525 the origin lesson", py: "prompt",
			lesson: "When a prompt explicitly says 'do not escalate/stop merely " +
				"because a linked page cannot be accessed' treat that as a hard constraint"},
		{name: "bae0851f output contract stays clean", py: "outcome",
			lesson: "a task specifies an exact output contract, verify against it before returning"},
		{name: "a domain hard constraint stays clean", py: "outcome",
			lesson: "rate limits are a hard constraint on parallelism"},
		{name: "an ordinary outcome lesson", py: "outcome",
			lesson: "Jina Reader returns cleaner text than raw HTML for LLM consumption"},

		// --- absence, and the truthiness of an empty source ---------------
		{name: "everything empty", py: "outcome"},
		{name: "an empty lesson with a scaffolded goal", py: "outcome",
			lesson: "", goal: "do not escalate"},
		{name: "a whitespace-only lesson", py: "outcome", lesson: "   "},

		// --- prompt authority: every noun and every verb ------------------
		// A list is not a class: an alternation member mistyped in the port
		// is invisible to any fixture that matches an earlier one.
		{name: "noun prompt", py: "prompt", lesson: "the prompt says x"},
		{name: "noun instruction", py: "prompt", lesson: "the instruction says x"},
		{name: "noun instructions", py: "prompt", lesson: "the instructions say x"},
		{name: "noun directive", py: "prompt", lesson: "the directive says x"},
		{name: "noun directives", py: "prompt", lesson: "the directives say x"},
		// `prompts` is NOT in the alternation — only the singular is.
		{name: "noun prompts is not a member", py: "outcome",
			lesson: "the prompts say x"},
		{name: "verb say", py: "prompt", lesson: "the prompt say x"},
		{name: "verb says", py: "prompt", lesson: "the prompt says x"},
		{name: "verb said", py: "prompt", lesson: "the prompt said x"},
		{name: "verb state", py: "prompt", lesson: "the prompt state x"},
		{name: "verb states", py: "prompt", lesson: "the prompt states x"},
		{name: "verb stated", py: "prompt", lesson: "the prompt stated x"},
		{name: "verb instruct", py: "prompt", lesson: "the prompt instruct x"},
		{name: "verb instructs", py: "prompt", lesson: "the prompt instructs x"},
		{name: "verb tell", py: "prompt", lesson: "the prompt tell x"},
		{name: "verb tells", py: "prompt", lesson: "the prompt tells x"},
		{name: "verb told", py: "prompt", lesson: "the prompt told x"},
		{name: "verb demand", py: "prompt", lesson: "the prompt demand x"},
		{name: "verb demands", py: "prompt", lesson: "the prompt demands x"},
		{name: "verb forbid", py: "prompt", lesson: "the prompt forbid x"},
		{name: "verb forbids", py: "prompt", lesson: "the prompt forbids x"},
		{name: "verb prohibit", py: "prompt", lesson: "the prompt prohibit x"},
		{name: "verb prohibits", py: "prompt", lesson: "the prompt prohibits x"},
		{name: "a verb outside the alternation", py: "outcome",
			lesson: "the prompt implies x"},
		// The determiner group is optional and the adverb group admits ONE
		// member, not a sequence.
		{name: "determiner the", py: "prompt", lesson: "the prompt says x"},
		{name: "determiner a", py: "prompt", lesson: "a prompt says x"},
		{name: "determiner your", py: "prompt", lesson: "your prompt says x"},
		{name: "determiner absent", py: "prompt", lesson: "prompt says x"},
		{name: "an unlisted determiner still matches on the noun", py: "prompt",
			lesson: "our prompt says x"},
		{name: "adverb explicitly", py: "prompt", lesson: "the prompt explicitly says x"},
		{name: "adverb clearly", py: "prompt", lesson: "the prompt clearly says x"},
		{name: "an unlisted adverb breaks the phrase", py: "outcome",
			lesson: "the prompt loudly says x"},
		{name: "two adverbs break the phrase", py: "outcome",
			lesson: "the prompt explicitly clearly says x"},

		// --- obedience: every alternation branch --------------------------
		{name: "obedience treat that", py: "prompt",
			lesson: "treat that as a hard constraint"},
		{name: "obedience treat this plural", py: "prompt",
			lesson: "treat this as hard constraints"},
		{name: "obedience treat it with no article", py: "prompt",
			lesson: "treat it as hard constraint"},
		{name: "obedience treat them shouted", py: "prompt",
			lesson: "TREAT THEM AS A HARD CONSTRAINT"},
		// The pronoun object is the whole point of the branch: a domain
		// noun in that slot must stay clean.
		{name: "obedience needs a pronoun object", py: "outcome",
			lesson: "treat the deadline as a hard constraint"},
		{name: "obedience must be obeyed", py: "prompt",
			lesson: "operator directives must be obeyed"},
		{name: "obedience non-negotiable", py: "prompt",
			lesson: "the deadline is to be treated as non-negotiable"},
		// A NON-BREAKING HYPHEN is not the ASCII hyphen the pattern spells,
		// and neither engine pretends otherwise.
		{name: "obedience with a unicode hyphen", py: "outcome",
			lesson: "treated as non" + nbhyph + "negotiable"},
		{name: "obedience follow them exactly", py: "prompt",
			lesson: "if given steps, follow them exactly"},
		{name: "obedience follow it to the letter", py: "prompt",
			lesson: "follow it to the letter"},
		{name: "obedience follow needs a pronoun", py: "outcome",
			lesson: "follow the recipe exactly"},

		// --- scaffolding: the echo rule -----------------------------------
		// The lesson alone is not enough — a source must carry it too.
		{name: "scaffolding with no source", py: "outcome",
			lesson: "do not escalate on a fetch failure"},
		{name: "scaffolding echoed by the goal", py: "prompt",
			lesson: "do not escalate on a fetch failure",
			goal:   "fetch the page; please do not stop early"},
		{name: "scaffolding echoed by the evidence", py: "prompt",
			lesson: "do not escalate on a fetch failure", goal: "",
			evidence: "worker said: do not give up"},
		{name: "a source that carries no scaffolding", py: "outcome",
			lesson: "do not escalate", goal: "an unrelated goal"},
		// The goal is consulted first, but both legs answer the same, so
		// the ORDER is unobservable; what is observable is that an empty
		// goal does not stop the evidence leg from being reached.
		{name: "an empty goal does not shadow the evidence", py: "prompt",
			lesson: "do not escalate", goal: "", evidence: "do not abandon the task"},
		{name: "scaffolding verb abandon", py: "prompt",
			lesson: "do not abandon the run", goal: "do not abandon the run"},
		{name: "scaffolding verb refuse", py: "prompt",
			lesson: "do not refuse the task", goal: "do not refuse the task"},
		{name: "scaffolding verb give up", py: "prompt",
			lesson: "do not give up", goal: "do not give up"},
		{name: "scaffolding verb stop", py: "prompt",
			lesson: "do not stop", goal: "do not stop"},
		{name: "a contraction is not the pattern", py: "outcome",
			lesson: "don't escalate", goal: "don't escalate"},
		{name: "scaffolding as an excuse to stop", py: "prompt",
			lesson: "read that as an excuse to stop",
			goal:   "treat that as an excuse to stop"},
		{name: "scaffolding as an excuse to escalate", py: "prompt",
			lesson: "used as an excuse to escalate",
			goal:   "used as an excuse to escalate"},
		// The branch needs the LEADING "as": the bare noun phrase is the
		// shape that made two rows of this table green while measuring
		// nothing, until the CPython premise check caught them.
		{name: "an excuse to stop without the leading as", py: "outcome",
			lesson: "an excuse to stop", goal: "an excuse to stop"},
		// The cannot-use window, at its own boundary.
		{name: "the excuse window at exactly 60", py: "prompt",
			lesson: gap("y", 58), goal: gap("y", 58)},
		{name: "the excuse window at 61", py: "outcome",
			lesson: gap("y", 59), goal: gap("y", 59)},
		// ...counted in CODE POINTS, not bytes: 58 CJK runes is 174 bytes,
		// and a port that counted bytes would call this one clean.
		{name: "the excuse window counts runes", py: "prompt",
			lesson: gap(cjk, 58), goal: gap(cjk, 58)},
		{name: "the excuse window at 61 runes", py: "outcome",
			lesson: gap(cjk, 59), goal: gap(cjk, 59)},
		// `[^.]` excludes the period and nothing else — a sentence break
		// closes the window, a newline does not.
		{name: "a period closes the excuse window", py: "outcome",
			lesson: "cannot use this. as an excuse", goal: "cannot use this. as an excuse"},
		{name: "a newline does not close the excuse window", py: "prompt",
			lesson: "cannot use this\nas an excuse", goal: "cannot use this\nas an excuse"},
		{name: "cannot-use needs a boundary after use", py: "outcome",
			lesson: "you cannot useas an excuse", goal: "you cannot useas an excuse"},

		// --- ASCII whitespace inside the phrases --------------------------
		{name: "a tab between the words", py: "prompt", lesson: "the prompt\tsays x"},
		{name: "a newline between the words", py: "prompt", lesson: "the prompt\nsays x"},
		{name: "a CRLF between the words", py: "prompt", lesson: "the prompt\r\nsays x"},
		{name: "a form feed between the words", py: "prompt", lesson: "the prompt\fsays x"},
		{name: "several spaces between the words", py: "prompt",
			lesson: "the   prompt   says x"},

		// --- DIVERGENCE 1: Python's \s is Unicode, Go's is 5 ASCII bytes --
		// Direction is UNSAFE: CPython quarantines, the port does not, and
		// an unquarantined lesson is injectable into every later run. A
		// model that emits a non-breaking space, or a lesson extracted from
		// text pasted out of a rendered page, reaches this without anyone
		// trying; U+000B needs no Unicode at all.
		{name: "a vertical tab between the words", py: "prompt",
			lesson: "the prompt" + vtab + "says x",
			goDiff: "outcome", why: whySpace},
		{name: "a non-breaking space between the words", py: "prompt",
			lesson: "the prompt" + nbsp + "says x",
			goDiff: "outcome", why: whySpace},
		{name: "an em space between the words", py: "prompt",
			lesson: "the prompt" + emsp + "says x",
			goDiff: "outcome", why: whySpace},
		{name: "an ideographic space between the words", py: "prompt",
			lesson: "the prompt" + ideosp + "says x",
			goDiff: "outcome", why: whySpace},
		{name: "a NEL between the words", py: "prompt",
			lesson: "the prompt" + nel + "says x",
			goDiff: "outcome", why: whySpace},
		// The same hole in the other two regexes, and — the one that
		// matters most — in the SOURCE leg, where the text is the
		// untrusted dispatch prompt itself.
		//
		// A separator after the DETERMINER is not one of them, and the
		// reason is worth a row of its own: the determiner group is
		// optional, so Go falls through to the bare noun and \b holds
		// there BECAUSE U+00A0 is not a word character to it. Two
		// ASCII-vs-Unicode differences cancelling is not agreement that
		// generalizes, and the fixture that assumed a divergence here was
		// wrong.
		{name: "a non-breaking space after the determiner", py: "prompt",
			lesson: "the" + nbsp + "prompt says x"},
		{name: "a non-breaking space in the obedience phrase", py: "prompt",
			lesson: "treat that as a hard" + nbsp + "constraint",
			goDiff: "outcome", why: whySpace},
		{name: "a vertical tab in must-be-obeyed", py: "prompt",
			lesson: "operator rules must" + vtab + "be obeyed",
			goDiff: "outcome", why: whySpace},
		{name: "a non-breaking space in the lesson's scaffolding", py: "prompt",
			lesson: "do" + nbsp + "not escalate", goal: "do not stop",
			goDiff: "outcome", why: whySpace},
		{name: "a non-breaking space in the goal's scaffolding", py: "prompt",
			lesson: "do not escalate", goal: "do" + nbsp + "not stop",
			goDiff: "outcome", why: whySpace},
		{name: "a vertical tab in the goal's scaffolding", py: "prompt",
			lesson: "do not escalate", goal: "do" + vtab + "not stop",
			goDiff: "outcome", why: whySpace},

		// --- DIVERGENCE 2: Python's \w is Unicode, Go's is [0-9A-Za-z_] ---
		// Direction is SAFE: a non-ASCII letter touching the keyword is a
		// word character to Python (so no \b, and no match) and a non-word
		// character to Go (so \b holds and the port quarantines).
		{name: "a Cyrillic letter before the noun", py: "outcome",
			lesson: cyrYer + "prompt says x",
			goDiff: "prompt", why: whyBoundary},
		{name: "an accented letter before the noun", py: "outcome",
			lesson: eacute + "prompt says x",
			goDiff: "prompt", why: whyBoundary},
		{name: "a CJK character before the noun", py: "outcome",
			lesson: cjk + "prompt says x",
			goDiff: "prompt", why: whyBoundary},
		{name: "a Cyrillic letter after the verb", py: "outcome",
			lesson: "the prompt says" + cyrYer + " x",
			goDiff: "prompt", why: whyBoundary},
		{name: "an accented letter after the verb", py: "outcome",
			lesson: "the prompt says" + eacute + " x",
			goDiff: "prompt", why: whyBoundary},
		// The ASCII controls for the same seam — both engines agree here,
		// and they are what keeps the rows above honest: a port that had
		// simply dropped the \b would match all of these too.
		{name: "an ASCII letter before the noun", py: "outcome",
			lesson: "xprompt says x"},
		{name: "a digit before the noun", py: "outcome", lesson: "1prompt says x"},
		{name: "an underscore before the noun", py: "outcome", lesson: "_prompt says x"},
		{name: "a hyphen before the noun", py: "prompt", lesson: "-prompt says x"},
		{name: "a curly quote before the noun", py: "prompt",
			lesson: ldquo + "prompt says x"},

		// --- DIVERGENCE 3: IGNORECASE folds the Turkish i in Python -------
		// Direction is UNSAFE, and this one is reachable on purpose: a
		// dotless i is a one-character homoglyph edit that walks an
		// instruction-derived lesson past the port's gate and not past
		// CPython's.
		{name: "a dotless i in the verb", py: "prompt",
			lesson: "the prompt " + dotles + "nstructs x",
			goDiff: "outcome", why: whyFold},
		{name: "a dotted capital I in the verb", py: "prompt",
			lesson: "the prompt " + dotI + "NSTRUCTS x",
			goDiff: "outcome", why: whyFold},
		{name: "a dotted capital I in the noun", py: "prompt",
			lesson: "the " + dotI + "NSTRUCTIONS say x",
			goDiff: "outcome", why: whyFold},
		// The other special fold an ASCII pattern can reach — LATIN SMALL
		// LETTER LONG S against `s` — DOES agree, which is why the rows
		// above name the Turkish i specifically rather than "unicode
		// folding" generally.
		{name: "a long s in the verb folds in both", py: "prompt",
			lesson: "the prompt " + longS + "ays x"},
		{name: "shouted", py: "prompt", lesson: "THE PROMPT SAYS X"},
		{name: "alternating case", py: "prompt", lesson: "The PrOmPt SaYs x"},

		// --- ordering between the three checks ----------------------------
		{name: "authority and obedience together", py: "prompt",
			lesson: "the prompt says it must be obeyed"},
		{name: "obedience without authority", py: "prompt",
			lesson: "treat that as a hard constraint: do not stop", goal: "do not stop"},
		// A NUL is not whitespace to either engine.
		{name: "a NUL between the words", py: "outcome",
			lesson: "the prompt" + nul + "says x"},
	}
}

func TestClassifyMatchesCPython(t *testing.T) {
	cases := classifyCases()

	seen := map[string]bool{}
	args := make([]map[string]string, 0, len(cases))
	for _, c := range cases {
		if seen[c.name] {
			t.Fatalf("duplicate fixture name %q — one row would overwrite the "+
				"other in the probe's result map and go unmeasured", c.name)
		}
		seen[c.name] = true
		if c.py != MintedFromPrompt && c.py != MintedFromOutcome {
			t.Fatalf("fixture %q claims CPython answers %q, which is neither "+
				"verdict", c.name, c.py)
		}
		if c.goDiff != "" {
			if c.goDiff == c.py {
				t.Fatalf("fixture %q is marked divergent but names the same "+
					"answer on both sides", c.name)
			}
			if c.why == "" {
				t.Fatalf("fixture %q pins a divergence with no stated reason", c.name)
			}
		}
		args = append(args, map[string]string{
			"name": c.name, "lesson": c.lesson,
			"goal": c.goal, "evidence": c.evidence,
		})
	}

	var got map[string]string
	pyprobe.Probe{Marker: "lesson_provenance.py"}.
		RunJSON(t, pyClassifySrc, &got, pyprobe.Arg(t, args))
	if len(got) != len(cases) {
		t.Fatalf("the probe answered %d of %d fixtures", len(got), len(cases))
	}

	for _, c := range cases {
		c := c
		t.Run(c.name, func(t *testing.T) {
			cp, ok := got[c.name]
			if !ok {
				t.Fatalf("the probe returned no answer for %q", c.name)
			}
			// The premise, checked before anything is compared: a fixture
			// whose CPython answer is not what it claims is not exercising
			// the branch it names, whatever the port says about it.
			if cp != c.py {
				t.Fatalf("CPython classified this %q, but the fixture claims "+
					"%q — the fixture no longer exercises what it names\n"+
					"lesson: %q\ngoal: %q\nevidence: %q",
					cp, c.py, c.lesson, c.goal, c.evidence)
			}
			want := cp
			if c.goDiff != "" {
				want = c.goDiff
			}
			g := Classify(c.lesson, c.goal, c.evidence)
			if g == want {
				return
			}
			if c.goDiff == "" {
				t.Fatalf("the port disagrees with CPython\n"+
					"cpython: %q\n     go: %q\nlesson: %q\ngoal: %q\nevidence: %q",
					cp, g, c.lesson, c.goal, c.evidence)
			}
			t.Fatalf("a PINNED divergence changed: the port answered %q where "+
				"this row pins %q (CPython says %q).\nreason on record: %s\n"+
				"If the port was fixed, drop goDiff/why from this row and "+
				"update the count in TestClassifyDivergenceLedger — the ledger "+
				"must not go stale.\nlesson: %q\ngoal: %q\nevidence: %q",
				g, c.goDiff, cp, c.why, c.lesson, c.goal, c.evidence)
		})
	}
}

// TestWhitespaceClassMatchesCPython generalizes the \s rows above from
// samples to the WHOLE class.
//
// A handful of fixtures proves the port loses U+00A0; it does not say
// which OTHER code points it loses, and that is the width of the hole.
// This walks every code point either engine could plausibly treat as a
// separator through the real classifier on both sides and pins the exact
// disagreeing set.
func TestWhitespaceClassMatchesCPython(t *testing.T) {
	// Every code point Python's re calls \s, plus controls and zero-width
	// characters neither engine does. The negatives are not padding:
	// without them, a port that treated every non-ASCII character as a
	// separator would look identical to a correct one here.
	candidates := []rune{
		0x00, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1b,
		0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x180e,
		0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
		0x2007, 0x2008, 0x2009, 0x200a, 0x200b, 0x2028, 0x2029, 0x202f,
		0x205f, 0x3000, 0xfeff,
	}
	// Stated ahead of both runs: these are the code points CPython treats
	// as \s inside "the prompt<R>says x". Every other candidate must leave
	// the lesson clean on the CPython side.
	pySpace := map[rune]bool{}
	for _, r := range []rune{
		0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20,
		0x85, 0xa0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004,
		0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029,
		0x202f, 0x205f, 0x3000,
	} {
		pySpace[r] = true
	}
	// ...and these are the five Go's regexp does.
	goSpace := map[rune]bool{0x09: true, 0x0a: true, 0x0c: true, 0x0d: true, 0x20: true}

	args := make([]map[string]string, 0, len(candidates))
	names := map[rune]string{}
	for _, r := range candidates {
		n := fmt.Sprintf("U+%04X", r)
		if _, dup := names[r]; dup {
			t.Fatalf("%s appears twice in the candidate list", n)
		}
		names[r] = n
		args = append(args, map[string]string{
			"name": n, "lesson": "the prompt" + string(r) + "says x",
			"goal": "", "evidence": "",
		})
	}
	var got map[string]string
	pyprobe.Probe{Marker: "lesson_provenance.py"}.
		RunJSON(t, pyClassifySrc, &got, pyprobe.Arg(t, args))

	var pyDisagrees, portDisagrees []string
	for _, r := range candidates {
		n := names[r]
		cp, ok := got[n]
		if !ok {
			t.Fatalf("the probe returned no answer for %s", n)
		}
		wantPy := MintedFromOutcome
		if pySpace[r] {
			wantPy = MintedFromPrompt
		}
		if cp != wantPy {
			pyDisagrees = append(pyDisagrees, fmt.Sprintf("%s: %s", n, cp))
			continue
		}
		wantGo := MintedFromOutcome
		if goSpace[r] {
			wantGo = MintedFromPrompt
		}
		if g := Classify("the prompt"+string(r)+"says x", "", ""); g != wantGo {
			portDisagrees = append(portDisagrees,
				fmt.Sprintf("%s: %s (expected %s)", n, g, wantGo))
		}
	}
	if len(pyDisagrees) > 0 {
		sort.Strings(pyDisagrees)
		t.Fatalf("the stated CPython whitespace class is wrong at %d code "+
			"points — every conclusion below it is unmeasured: %v",
			len(pyDisagrees), pyDisagrees)
	}
	if len(portDisagrees) > 0 {
		sort.Strings(portDisagrees)
		t.Fatalf("the port's whitespace class moved at %d code points: %v",
			len(portDisagrees), portDisagrees)
	}
	// The gap is the finding, so it is counted out loud rather than left
	// implied by two maps: 24 separators CPython honours inside the
	// pattern and the port does not.
	gap := 0
	for r := range pySpace {
		if !goSpace[r] {
			gap++
		}
	}
	if gap != 24 {
		t.Fatalf("the \\s gap between the two engines is %d code points, "+
			"not the 24 on record", gap)
	}
	for r := range goSpace {
		if !pySpace[r] {
			t.Fatalf("U+%04X is a separator to the port and not to CPython — "+
				"the gap is supposed to be one-directional", r)
		}
	}
}

// TestRegexSourceMatchesCPython pins the two pattern strings against each
// other.
//
// The behavioural table can only exercise the branches its fixtures name.
// A calibration change on the Python side — one more verb, a widened
// window — would leave every fixture green while the two runtimes
// quarantined different lessons, and this package's own comment claims
// the two files are "a shared contract". This is what makes that claim
// checkable.
func TestRegexSourceMatchesCPython(t *testing.T) {
	const pyPatternSrc = `
import json, re
import lesson_provenance as lp

def spec(rx):
    return {"pattern": rx.pattern,
            "ignorecase": bool(rx.flags & re.IGNORECASE),
            "other_flags": rx.flags & ~(re.IGNORECASE | re.UNICODE)}

print(json.dumps({
    "prompt_authority": spec(lp._PROMPT_AUTHORITY_RE),
    "obedience": spec(lp._OBEDIENCE_RE),
    "scaffolding": spec(lp._SCAFFOLDING_RE),
    "minted_from_prompt": lp.MINTED_FROM_PROMPT,
    "minted_from_outcome": lp.MINTED_FROM_OUTCOME,
}))
`
	type spec struct {
		Pattern    string `json:"pattern"`
		IgnoreCase bool   `json:"ignorecase"`
		OtherFlags int    `json:"other_flags"`
	}
	var py struct {
		PromptAuthority   spec   `json:"prompt_authority"`
		Obedience         spec   `json:"obedience"`
		Scaffolding       spec   `json:"scaffolding"`
		MintedFromPrompt  string `json:"minted_from_prompt"`
		MintedFromOutcome string `json:"minted_from_outcome"`
	}
	pyprobe.Probe{Marker: "lesson_provenance.py"}.RunJSON(t, pyPatternSrc, &py)

	if py.MintedFromPrompt != MintedFromPrompt || py.MintedFromOutcome != MintedFromOutcome {
		t.Fatalf("the stamp values differ: cpython (%q, %q), go (%q, %q) — a "+
			"lesson stamped by one runtime would read as unstamped to the "+
			"other", py.MintedFromPrompt, py.MintedFromOutcome,
			MintedFromPrompt, MintedFromOutcome)
	}

	for _, c := range []struct {
		name string
		want spec
		got  string
	}{
		{"_PROMPT_AUTHORITY_RE", py.PromptAuthority, promptAuthorityRe.String()},
		{"_OBEDIENCE_RE", py.Obedience, obedienceRe.String()},
		{"_SCAFFOLDING_RE", py.Scaffolding, scaffoldingRe.String()},
	} {
		if !c.want.IgnoreCase {
			t.Errorf("%s is no longer compiled with re.IGNORECASE — the port "+
				"spells the flag inline and would go on folding", c.name)
		}
		if c.want.OtherFlags != 0 {
			t.Errorf("%s carries flags the port does not spell: %d",
				c.name, c.want.OtherFlags)
		}
		// The port's one licensed edit to the pattern text is hoisting
		// IGNORECASE into an inline (?i), which is how Go spells a flag
		// there being no flags argument to MustCompile.
		stripped := strings.TrimPrefix(c.got, "(?i)")
		if stripped == c.got {
			t.Errorf("%s: the port's pattern carries no inline (?i) to stand "+
				"in for re.IGNORECASE: %q", c.name, c.got)
			continue
		}
		if stripped != c.want.Pattern {
			t.Errorf("%s pattern text differs\ncpython: %q\n     go: %q",
				c.name, c.want.Pattern, stripped)
		}
	}
}

// TestClassifyDivergenceLedger guards the table rather than the port.
//
// The divergence count reported to the host is only as honest as the
// number of rows carrying a goDiff, and a row quietly losing one would
// shrink the ledger without failing anything.
func TestClassifyDivergenceLedger(t *testing.T) {
	byWhy := map[string]int{}
	for _, c := range classifyCases() {
		if c.goDiff != "" {
			byWhy[c.why]++
		}
	}
	total := 0
	for _, n := range byWhy {
		total += n
	}
	want := map[string]int{whySpace: 10, whyBoundary: 5, whyFold: 3}
	if total != 18 {
		t.Errorf("the table pins %d divergent rows, not the 18 on record", total)
	}
	for why, n := range want {
		if byWhy[why] != n {
			t.Errorf("cause %q covers %d rows, not the %d on record: %s",
				shortWhy(why), byWhy[why], n, why)
		}
	}
	if len(byWhy) != len(want) {
		t.Errorf("the pinned rows name %d causes, not the %d on record",
			len(byWhy), len(want))
	}
}

func shortWhy(s string) string {
	if i := strings.Index(s, ":"); i > 0 {
		return s[:i]
	}
	return s
}
