// Package jsonx ports the tolerant JSON extraction the Python runtime
// leans on (llm_parse.extract_json): models fence, preface, and trail
// their JSON; the parser's job is to find the payload without ever
// guessing content that isn't there.
package jsonx

import (
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

// StringArray extracts a JSON array of strings from model output that may
// wrap it in markdown fences or prose. Non-string array elements are a
// contract violation and error out — a planner step that isn't text is
// nothing this port can execute, and coercing it would hide the model's
// drift from the caller.
func StringArray(text string) ([]string, error) {
	payload, err := carve(text, '[', ']')
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
	payload, err := carve(text, '{', '}')
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(payload), &out); err != nil {
		return nil, fmt.Errorf("object found but unparseable: %w", err)
	}
	return out, nil
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
