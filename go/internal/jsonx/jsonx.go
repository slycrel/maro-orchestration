// Package jsonx ports the tolerant JSON extraction the Python runtime
// leans on (llm_parse.extract_json): models fence, preface, and trail
// their JSON; the parser's job is to find the payload without ever
// guessing content that isn't there.
//
// Two hardenings ride ahead of the bracket search (adversarial round
// 2026-08-22):
//   - <think>...</think> reasoning traces are stripped first, porting
//     llm_parse.strip_think_blocks — the trace routinely contains
//     hypothetical example JSON the bracket search would grab.
//   - Fenced code blocks are tried before the raw text, so a stray
//     bracket in prose ("see [here](url)") ahead of a ```json fence
//     cannot misdirect the carve.
//
// Honest residual, shared with the Python sibling (llm_parse
//._find_json_bounds): with NO fence present, the first balanced bracket
// in the text wins — prose containing an incidental well-formed bracket
// pair before the real payload returns the wrong span on both runtimes.
package jsonx

import (
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
)

// StringArray extracts a JSON array of strings from model output that may
// wrap it in markdown fences or prose. Non-string array elements are a
// contract violation and error out — a planner step that isn't text is
// nothing this port can execute, and coercing it would hide the model's
// drift from the caller.
func StringArray(text string) ([]string, error) {
	payload, err := extract(text, '[', ']')
	if err != nil {
		return nil, err
	}
	var out []string
	if err := json.Unmarshal([]byte(payload), &out); err != nil {
		var loose []any
		if err2 := json.Unmarshal([]byte(payload), &loose); err2 != nil {
			return nil, fmt.Errorf("array found but unparseable: %w", err)
		}
		return nil, errors.New("array contains non-string elements")
	}
	return out, nil
}

// Object extracts a single JSON object the same way.
func Object(text string) (map[string]any, error) {
	payload, err := extract(text, '{', '}')
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(payload), &out); err != nil {
		return nil, fmt.Errorf("object found but unparseable: %w", err)
	}
	return out, nil
}

var (
	// Ported from llm_parse.strip_think_blocks: closed blocks are removed
	// wholesale; an unclosed <think> (trace truncated by a token budget)
	// drops everything from the tag onward — there is no answer to keep,
	// so the caller's error/default is the fail-safe direction.
	thinkRe     = regexp.MustCompile(`(?is)<think\b[^>]*>.*?</think\s*>`)
	thinkOpenRe = regexp.MustCompile(`(?i)<think\b[^>]*>`)
	fenceRe     = regexp.MustCompile("(?s)```[a-zA-Z]*[ \t]*\n(.*?)```")
)

func stripThinkBlocks(text string) string {
	cleaned := thinkRe.ReplaceAllString(text, "")
	if loc := thinkOpenRe.FindStringIndex(cleaned); loc != nil {
		cleaned = cleaned[:loc[0]]
	}
	return strings.TrimSpace(cleaned)
}

// extract strips reasoning traces, then tries each fenced code block in
// order before falling back to the whole text.
func extract(text string, open, close byte) (string, error) {
	text = stripThinkBlocks(text)
	for _, m := range fenceRe.FindAllStringSubmatch(text, -1) {
		if payload, err := carve(m[1], open, close); err == nil {
			return payload, nil
		}
	}
	return carve(text, open, close)
}

// carve returns the substring from the first `open` to its matching
// `close`, respecting JSON string literals and escapes — a bracket
// inside a quoted step ("use x[0]") must not end the carve, which is
// why this isn't a first-index/last-index slice.
func carve(text string, open, close byte) (string, error) {
	start := strings.IndexByte(text, open)
	if start < 0 {
		return "", fmt.Errorf("no %q found in output", string(open))
	}
	depth := 0
	inString := false
	escaped := false
	for i := start; i < len(text); i++ {
		c := text[i]
		if inString {
			switch {
			case escaped:
				escaped = false
			case c == '\\':
				escaped = true
			case c == '"':
				inString = false
			}
			continue
		}
		switch c {
		case '"':
			inString = true
		case open:
			depth++
		case close:
			depth--
			if depth == 0 {
				return text[start : i+1], nil
			}
		}
	}
	return "", fmt.Errorf("unbalanced %q...%q in output", string(open), string(close))
}

// StripThink exposes the <think>-trace strip for callers that recover
// PROSE (not JSON) from a model reply — internal/now's rationale
// recovery walks the same raw content Object does, and an un-stripped
// trace would otherwise become a durable verdict summary (adversarial
// routing r3; Go-stricter than Python's _now_verdict_rationale, which
// shares the gap).
func StripThink(text string) string { return stripThinkBlocks(text) }
