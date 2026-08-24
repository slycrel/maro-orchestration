// Package jsonx ports the tolerant JSON extraction the Python runtime
// leans on (llm_parse.extract_json): models fence, preface, and trail
// their JSON; the parser's job is to find the payload without ever
// guessing content that isn't there.
//
// The pipeline is llm_parse.extract_json's, verb for verb:
// strip_think_blocks, then strip_markdown_fences, then the bracket
// search. <think>...</think> traces go first because they routinely
// contain hypothetical example JSON the bracket search would grab.
//
// Honest residual, shared with the Python sibling: the fence strip only
// fires when the fence is the WHOLE message, and with prose either side
// of it the first balanced bracket in the text wins — so "see the docs
// [here](url)" ahead of a ```json fence returns the wrong span. On both
// runtimes. That is worth fixing; it is not worth fixing HERE, alone.
//
// TWO REJECTED HARDENINGS, and the reason is the whole doctrine of this
// port (adversarial mission-r1 HIGH).
//
// The first: carve used to track string literals, so a brace inside a
// quoted value could not end the span. Python's depth counter does NOT,
// and the comment above claimed the residual was "shared" when the two
// had in fact diverged. Measured, on the same model reply:
//
//	{"passed": false, "reason": "the } thing is broken"}
//	  CPython -> bounds end at the } INSIDE the string -> unparseable
//	             -> _validate_milestone defaults to PASS
//	  Go (old) -> carves the whole object -> passes=false -> FAIL
//
// The same input produced a passed milestone under one runtime and a
// failed one under the other, both persisted to the same mission.json;
// decompose forked the same way, heuristic vs the model's real plan. A
// hardening that changes which record gets written to a shared store is
// not a hardening, it is a fork. Parity wins; if the naive scan is ever
// to be fixed it has to be fixed on BOTH sides, in the Python first.
//
// The second, found by applying that rule to the rest of this file:
// extract used to try each fenced block BEFORE the raw text, so a stray
// bracket in prose could not misdirect the carve. Python's
// strip_markdown_fences only strips a fence that is the ENTIRE stripped
// message (_FENCE_RE.match), and does nothing at all when prose
// surrounds it. Measured 2026-08-23:
//
//	See the docs [here](url) for context.
//	```json
//	["step one", "step two"]
//	```
//	  CPython -> no fence stripped -> carve hits [here] -> [] (the default)
//	  Go (old) -> reads the fence -> ["step one", "step two"]
//
// Ten production callers reach this function and every one of them writes
// what it parses into the shared workspace. The better behaviour belongs
// in llm_parse.strip_markdown_fences, where both runtimes inherit it;
// until then this matches. Pinned case-by-case in fence_diff_test.go.
package jsonx

import (
	"errors"
	"fmt"
	"regexp"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// StringArray extracts a JSON array of strings from model output that may
// wrap it in markdown fences or prose. Non-string array elements are a
// contract violation and error out — a planner step that isn't text is
// nothing this port can execute, and coercing it would hide the model's
// drift from the caller.
//
// It decodes through pyval.LoadsOrdered, not encoding/json, and so does
// Object below. The r1 MEDIUM that taught ObjectOrdered to mask bare
// NaN/Infinity/-Infinity left both siblings on the raw decoder for three
// more rounds, and the rejection kills the WHOLE document, not one field
// (adversarial mission-r4 HIGH). Measured on a reply the intent
// classifier's own prompt asks for:
//
//	{"lane": "now", "confidence": NaN}
//	  CPython -> {'lane': 'now', 'confidence': nan} -> routed as NOW
//	  Go (old) -> error -> heuristicClassify -> a DIFFERENT lane,
//	              and a different outcome shape in the store
//
// Eleven production call sites reach these two functions and three of
// them prompt the model for a float.
func StringArray(text string) ([]string, error) {
	payload, err := extract(text, '[', ']')
	if err != nil {
		return nil, err
	}
	v, err := pyval.LoadsOrdered(payload)
	if err != nil {
		return nil, fmt.Errorf("array found but unparseable: %w", err)
	}
	list, ok := pyval.Plain(v).([]any)
	if !ok {
		return nil, fmt.Errorf("array found but it decoded as %T", v)
	}
	out := make([]string, len(list))
	for i, e := range list {
		s, ok := e.(string)
		if !ok {
			return nil, errors.New("array contains non-string elements")
		}
		out[i] = s
	}
	return out, nil
}

// Object extracts a single JSON object the same way.
func Object(text string) (map[string]any, error) {
	payload, err := extract(text, '{', '}')
	if err != nil {
		return nil, err
	}
	v, err := pyval.LoadsOrdered(payload)
	if err != nil {
		return nil, fmt.Errorf("object found but unparseable: %w", err)
	}
	out, ok := pyval.Plain(v).(map[string]any)
	if !ok {
		return nil, fmt.Errorf("object found but it decoded as %T", v)
	}
	return out, nil
}

var (
	// Ported from llm_parse.strip_think_blocks: closed blocks are removed
	// wholesale; an unclosed <think> (trace truncated by a token budget)
	// drops everything from the tag onward — there is no answer to keep,
	// so the caller's error/default is the fail-safe direction.
	// `\s` is NOT transcribable: Go's regexp reads it as five code points
	// and Python's re reads it as twenty-nine. pytext.SpaceClass is the
	// measured Python set, and its own doc warns about precisely this —
	// the helper existed and this pattern was not using it (adversarial
	// mission-r2 MEDIUM).
	//
	// It matters because the failure is DESTRUCTIVE rather than partial.
	// On `<think>musing {"decoy":1}</think\u00a0>\n{"real":2}` — a
	// non-breaking space inside the closing tag — CPython removes the
	// block and carves {"real":2}. Go's thinkRe failed to match, so
	// thinkOpenRe fired and truncated EVERYTHING from the tag onward,
	// leaving "" and an error. Downstream that is not "no answer": it is
	// decomposeViaLLM writing the heuristic mission where CPython writes
	// the model's real plan, and ValidateMilestone defaulting to PASS
	// where CPython reads the model's actual verdict.
	//
	// RESIDUAL, measured and deliberately NOT patched, and it runs in
	// BOTH directions \u2014 the note used to give only the harmless one
	// (adversarial mission-r4 LOW). `\b` is ASCII-only in RE2 and
	// Unicode-aware in Python.
	//
	// The DESTRUCTIVE direction is a non-ASCII character reached INSIDE
	// the tag name through case folding. Both engines fold `k` to
	// U+212A KELVIN SIGN under (?i); only Python's `\b` then treats
	// U+212A as a word character:
	//
	//	<thin\u212a>musing {"decoy":1}</think>\n{"real":2}
	//	  CPython -> both patterns match -> trace stripped
	//	             -> carves {"real":2}, the real answer
	//	  Go      -> NEITHER matches -> trace kept
	//	             -> carves {"decoy":1}, the model's hypothetical
	//
	// That is the same shape the `\s` fix above was about: Go writing
	// the model's example JSON into the shared store where CPython
	// writes its actual answer.
	//
	// The harmless direction, and the one this note originally gave
	// alone: a non-ASCII word character AFTER the tag name. On
	// `<think\u00e9>`
	// — a PRECOMPOSED é, or any other letter or digit outside ASCII
	// straight after the tag name; the decomposed spelling `<thinke\u0301>`
	// does NOT demonstrate this, because the character right after the
	// tag name there is an ASCII "e" and both engines then agree
	// (mission-r3 LOW: a "measured" example that measures nothing is
	// what lets a residual note stop being checked) —
	// Python finds no boundary, matches NEITHER pattern, leaves the trace
	// in place and carves the hypothetical; Go finds a boundary and
	// strips. Expressing Python's `\w` here needs a word class, and Go
	// ships Unicode 15.0 against CPython's 16.0 on this box — the exact
	// skew pytext.digitSupplementBody exists to paper over. A class that
	// is *nearly* Python's would read as fixed while still forking, so
	// this stays written down until a measured Word class exists.
	// `<think\b[^>]*>` — the boundary is INTERIOR (there is pattern after
	// it), so it cannot be WordEnd, which consumes. Folded into what
	// follows instead: either the tag closes immediately (`>` is itself a
	// non-word character, so the boundary holds) or the next character is
	// a non-word one that is not `>`. thinkBody is that fold.
	//
	// The residual the old comment named is real but is NOT a reason to
	// leave Go's ASCII `\b` in place: pytext.WordClass is measured against
	// CPython (0 false positives, 5004 false negatives, all astral or
	// newly-added ranges), and an ASCII-only boundary forks on every Latin
	// accent — "<thinké>" strips here and does not in CPython. Trading a
	// 5004-code-point skew for a whole-Latin-1 skew was the wrong side of
	// the trade (adversarial mission-r7 LOW).
	thinkBody   = `(?:` + pytext.NotWordClassPlus(">") + `[^>]*)?>`
	thinkRe     = regexp.MustCompile(`(?is)<think` + thinkBody + `.*?</think` + pytext.SpaceClass + `*>`)
	thinkOpenRe = regexp.MustCompile(`(?i)<think` + thinkBody)
	// EQUIVALENT-MUTANT NOTE. Making `(.*?)` greedy survives the whole
	// battery, and it is genuinely equivalent HERE but not in general:
	// over 3810 generated fence documents the two spellings never differ
	// after stripMarkdownFences' strip, and differ in 1578 of them
	// before it. `$` pins where the match ends, so the only thing the
	// quantifier controls is how much trailing whitespace lands inside
	// the capture -- and the strip on the next line eats exactly that.
	// The laziness is load-bearing only if that strip ever goes away.
	//
	// llm_parse._FENCE_RE verbatim: r"^```[a-zA-Z]*\n?(.*?)\n?```$" with
	// DOTALL, applied with .match() — so it fires only when the fence is
	// the whole (stripped) message. Python's `$` also matches before a
	// trailing newline where Go's does not, which cannot separate them
	// here: the subject is stripped first, so it never ends in one.
	fenceRe = regexp.MustCompile("(?s)^```[a-zA-Z]*\n?(.*?)\n?```$")
)

func stripThinkBlocks(text string) string {
	cleaned := thinkRe.ReplaceAllString(text, "")
	if loc := thinkOpenRe.FindStringIndex(cleaned); loc != nil {
		cleaned = cleaned[:loc[0]]
	}
	// Python's strip_think_blocks ends `return cleaned.strip()`, and
	// str.strip() covers four code points strings.TrimSpace does not
	// (U+001C..U+001F). Invisible through extract, which re-strips with
	// pytext.Strip — but StripThink used to be exported and its caller
	// re-stripped with TrimSpace, so the gap was reachable (r2 LOW).
	return pytext.Strip(cleaned)
}

// stripMarkdownFences is llm_parse.strip_markdown_fences: unwrap a fence
// that is the ENTIRE message, and otherwise leave the text alone.
func stripMarkdownFences(text string) string {
	stripped := pytext.Strip(text)
	if m := fenceRe.FindStringSubmatch(stripped); m != nil {
		return pytext.Strip(m[1])
	}
	return stripped
}

// extract is extract_json's preamble: strip traces, unwrap a whole-message
// fence, carve. See the second rejected hardening at the top of this file
// for why it does not go looking for fences inside prose.
//
// EQUIVALENT-MUTANT NOTE. Swapping these two verbs survives the battery,
// and 672 generated documents -- think blocks open and closed, inside and
// outside the fence, with and without decoy brackets -- produce no
// separator. The reason is structural, not luck: stripMarkdownFences only
// ever removes backticks, language-tag letters, and whitespace, and carve
// reacts to nothing but bracket characters, so whichever order runs, the
// bracket sequence it finally scans is the same.
//
// The order still matters and is still pinned -- one column of
// fence_diff_test.go compares strip(think(x)) against CPython -- it just
// cannot be observed THROUGH carve. Keep the Python order anyway: the
// equivalence is a property of today's carve, not a licence.
func extract(text string, open, close byte) (string, error) {
	return carve(stripMarkdownFences(stripThinkBlocks(text)), open, close)
}

// carve returns the substring from the first `open` to the `close` that
// returns the depth counter to zero — a TRANSCRIPTION of Python's
// llm_parse._find_json_bounds, deliberately including its blindness to
// string literals.
//
// Do not "fix" this by tracking quotes. It used to, and the divergence it
// caused is written up at the top of this file: a brace inside a quoted
// value ends the span in Python and did not in Go, so the two runtimes
// wrote different missions from the same model reply. A BALANCED bracket
// pair inside a string ("use x[0] to index") is carved identically by
// both algorithms, which is why the difference hid for so long — only an
// UNBALANCED one separates them.
//
// The ONE remaining difference from Python is the failure spelling:
// _find_json_bounds returns (-1, -1) where this returns an error, and
// every caller treats the two the same (default / fall through).
//
// The scan starts at index 0 and lets `depth == 0` pick the start, which
// is NOT the same as jumping to the first `open`: a stray CLOSE ahead of
// the payload drives depth negative and Python then finds no bounds at
// all. `x } y {"b":2} z` is (-1, -1) in CPython; an IndexByte start
// carves `{"b":2}` out of it. That is exactly the mismatched-mission fork
// described above, in a second spelling, and it lived in this function
// under a comment asserting the two were equivalent (adversarial
// mission-r1 follow-up, caught by the CPython differential).
func carve(text string, open, close byte) (string, error) {
	depth := 0
	start := -1
	sawOpen := false
	for i := 0; i < len(text); i++ {
		switch text[i] {
		case open:
			sawOpen = true
			if depth == 0 {
				start = i
			}
			depth++
		case close:
			depth--
			if depth == 0 && start >= 0 {
				return text[start : i+1], nil
			}
		}
	}
	if !sawOpen {
		return "", fmt.Errorf("no %q found in output", string(open))
	}
	return "", fmt.Errorf("unbalanced %q...%q in output", string(open), string(close))
}

// ObjectOrdered is Object with the key order and the number LITERALS
// kept — the same carve, decoded through pyval.LoadsOrdered.
//
// Two callers need it and a map cannot serve either: rendering an
// LLM-supplied value through Python's `str()` depends on insertion order
// (`str({'b':1,'a':2})` starts with 'b'), and telling `1` from `1.0`
// depends on the literal, which json.Unmarshal into `any` throws away by
// making both float64.
func ObjectOrdered(text string) (pyval.Obj, error) {
	payload, err := extract(text, '{', '}')
	if err != nil {
		return nil, err
	}
	v, err := pyval.LoadsOrdered(payload)
	if err != nil {
		return nil, fmt.Errorf("object found but unparseable: %w", err)
	}
	o, ok := v.(pyval.Obj)
	if !ok {
		return nil, fmt.Errorf("object found but it decoded as %T", v)
	}
	return o, nil
}
