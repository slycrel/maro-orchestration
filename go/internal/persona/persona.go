// Package persona ports Python src/persona.py — the persona system:
// composable agent identities stored as `personas/*.md` files with optional
// YAML frontmatter.
//
// The files are a SHARED on-disk surface (workspace personas override repo
// ones; the evolver writes new ones into the workspace), so parsing them is
// a data-parity seam, not an internal. Four things about that parse are
// easy to get wrong in Go and all four are measured against CPython in this
// package's differentials:
//
//   - `Path.read_text(encoding="utf-8")` is UNIVERSAL NEWLINES. A persona
//     file written on a Windows editor has its frontmatter delimited by
//     "\r\n---\r\n"; Python sees "\n---\n" and finds the frontmatter, a
//     byte-literal port does not and hands the whole file over as the
//     system prompt. It is also STRICT: a byte-tainted file raises, and
//     every caller of the parser wraps it in a bare `except`, so such a
//     file is SKIPPED — except in Registry.List, which falls back to the
//     filename stem and therefore advertises a name Load can never return.
//
//   - Every scalar field goes through `str()`, and PyYAML is YAML **1.1**.
//     `model_tier: off` is the BOOLEAN False there, so the field's value is
//     the four-character string "False". gopkg.in/yaml.v3 is YAML 1.2 and
//     leaves it the string "off". See the yaml11 divergence pin in
//     persona_diff_test.go — this package does NOT normalize, matching the
//     posture internal/config took for the same seam.
//
//   - The three LIST fields are not element-coerced. `composes: [1, 2]`
//     survives as two integers into the manifest JSON, which is why they
//     are []any here and not []string. A []string port writes `["1","2"]`
//     into a file the Python runtime also reads.
//
//   - Python truthiness decides the name. `name: 0` and `name: false` are
//     FALSY, so the filename stem wins over an explicitly-set name.
//
// NOT ported, named: spawn_persona. It needs three seams this runtime does
// not have — short-term memory (short_clear/short_set), llm.build_adapter's
// tier map, and agent_loop.run_agent_loop — and half of it would be a
// dry-run branch pretending to be a spawn. SpawnResult goes with it.
package persona

import (
	"errors"
	"fmt"
	"os"
	"strings"
	"unicode/utf8"

	yaml "gopkg.in/yaml.v3"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Spec is a loaded, validated persona specification — the PersonaSpec
// dataclass, field for field and in declaration order, because
// persona_to_dict serialises it with asdict() and the order is the file.
//
// ToolAccess/Hooks/Composes are []any rather than []string on purpose: see
// the package doc's third bullet. An empty list and a nil one are both
// rendered `[]` by pyval, so the distinction is not observable downstream,
// but the parser produces a non-nil empty slice wherever Python produces
// `[]` so a reader never has to know that.
type Spec struct {
	Name               string
	Role               string
	ModelTier          string // "power" | "mid" | "cheap"
	ToolAccess         []any  // empty = all tools allowed
	MemoryScope        string // "session" | "project" | "global"
	CommunicationStyle string
	SystemPrompt       string // rendered markdown body
	Hooks              []any  // hook names to register
	Composes           []any  // other persona names composed into this one
	SourceFile         string // path to source .md file
}

// defaultFrontmatter is _DEFAULT_FRONTMATTER. The list values are rebuilt
// per call rather than shared, because Python's `dict(_DEFAULT_FRONTMATTER)`
// is a SHALLOW copy — it shares the same two list objects with the module
// global — and the only reason that has never mattered is that nothing
// mutates them in place. Sharing them here would make it matter.
func defaultFrontmatter() pyval.Obj {
	return pyval.Obj{
		{Key: "name", Val: ""},
		{Key: "role", Val: "General Assistant"},
		{Key: "model_tier", Val: "mid"},
		{Key: "tool_access", Val: []any{}},
		{Key: "memory_scope", Val: "session"},
		{Key: "communication_style", Val: "direct and concise"},
		{Key: "hooks", Val: []any{}},
		{Key: "composes", Val: []any{}},
	}
}

// listFields are the three fields the coercion loop walks, in Python's
// tuple order. The order is not observable — each is coerced
// independently — but it is one line and the tuple is right there.
var listFields = [...]string{"tool_access", "hooks", "composes"}

// ParseFile ports _parse_persona_file: a persona .md file with optional
// YAML frontmatter.
//
// The error is what Python's `read_text` would have RAISED, so a caller can
// reproduce the two different things Python's callers do with it — skip the
// file (Load, LoadAll) or fall back to the filename stem (List). It is
// never nil-with-a-broken-Spec.
func ParseFile(path string) (*Spec, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		// Python's IsADirectoryError arrives here too: `*.md` globs
		// DIRECTORIES, and a directory named `foo.md` reaches the parser
		// and raises. Measured — Registry.List then advertises "foo".
		return nil, err
	}
	// `encoding="utf-8"` with the default strict error handler. Go's
	// []byte->string conversion is lossless and silent, so the check has
	// to be explicit or a torn file becomes a persona full of U+FFFD.
	if !utf8.Valid(raw) {
		return nil, fmt.Errorf("UnicodeDecodeError: %s is not valid utf-8", path)
	}
	// Universal newlines, applied before ANY index arithmetic: the
	// frontmatter delimiter search below is what depends on it.
	content := pytext.TranslateNewlines(string(raw))

	meta := defaultFrontmatter()
	body := content

	if strings.HasPrefix(content, "---") {
		// `content.find("\n---", 3)` — a CODE POINT index in Python. The
		// byte arithmetic is equivalent here and only here: the first
		// three characters are the ASCII "---" the HasPrefix just proved,
		// so byte offset 3 IS code point offset 3, and the needle is four
		// ASCII bytes, so `end+4` lands identically under both readings.
		if rel := strings.Index(content[3:], "\n---"); rel != -1 {
			end := 3 + rel
			fmText := pytext.Strip(content[3:end])
			body = pytext.Strip(content[end+4:])
			// yaml.safe_load + `if isinstance(parsed, dict)`, with the
			// whole thing under a bare `except: pass` — a malformed
			// frontmatter falls back to the defaults and keeps the body
			// it already sliced. That ordering is load-bearing: the body
			// is assigned BEFORE the parse, so a YAML error does not put
			// the frontmatter text back into the system prompt.
			var parsed any
			if yaml.Unmarshal([]byte(fmText), &parsed) == nil {
				if m, ok := asMapping(parsed); ok {
					for _, f := range m {
						meta.Set(f.Key, f.Val)
					}
				}
			}
		}
	}

	stem := pypath.Stem(pypath.Name(path))

	// `if not meta.get("name")` — TRUTHINESS, so 0, False, "" and an empty
	// container all lose to the filename stem.
	if v, _ := meta.Get("name"); !pyval.Truthy(v) {
		meta.Set("name", stem)
	}

	for _, field := range listFields {
		v, _ := meta.Get(field)
		switch t := v.(type) {
		case string:
			// [s.strip() for s in v.split(",") if s.strip()] — str.split
			// on a single-character separator never collapses runs, and
			// the filter is a truthiness test on the STRIPPED value, so
			// "a,,b" yields two entries and " , " yields none.
			out := []any{}
			for _, part := range strings.Split(t, ",") {
				if s := pytext.Strip(part); s != "" {
					out = append(out, s)
				}
			}
			meta.Set(field, out)
		default:
			// EQUIVALENT MUTANT, recorded so it is not re-derived: deleting
			// this reset changes nothing observable, because copyList's type
			// assertion already answers an empty slice for a non-list. It is
			// kept because it is the statement Python writes, and because the
			// day something reads `meta` for anything but these ten keys the
			// equivalence stops holding.
			if _, isList := v.([]any); !isList {
				meta.Set(field, []any{})
			}
		}
	}

	get := func(key string) any { v, _ := meta.Get(key); return v }
	return &Spec{
		Name:               pyval.Str(get("name")),
		Role:               pyval.Str(get("role")),
		ModelTier:          pyval.Str(get("model_tier")),
		ToolAccess:         copyList(get("tool_access")),
		MemoryScope:        pyval.Str(get("memory_scope")),
		CommunicationStyle: pyval.Str(get("communication_style")),
		SystemPrompt:       body,
		Hooks:              copyList(get("hooks")),
		Composes:           copyList(get("composes")),
		SourceFile:         pypath.Str(path),
	}, nil
}

// copyList is Python's `list(x)` over a value the coercion loop has already
// proved is a list: a NEW slice, so two specs parsed from one file never
// share backing storage the way `meta[field]` would.
func copyList(v any) []any {
	src, _ := v.([]any)
	out := make([]any, len(src))
	copy(out, src)
	return out
}

// asMapping is `isinstance(parsed, dict)` over what yaml.v3 hands back.
//
// It reports false for a sequence, a scalar and a nil document, which is
// exactly the set PyYAML's isinstance rejects. Non-string keys are DROPPED
// rather than refused: Python keeps them (a YAML `1: a` really does become
// the dict key 1) but nothing downstream reads a key that is not one of the
// eight names, so the observable answer is the same and the alternative is
// an `any`-keyed map nothing else in this package wants.
func asMapping(v any) (pyval.Obj, bool) {
	switch m := v.(type) {
	case map[string]any:
		// yaml.v3 gives a Go map, whose iteration order is randomized.
		// The keys are re-read by name, never iterated for output, so the
		// order does not reach a file — but a deterministic order costs
		// one sort and removes the question.
		out := make(pyval.Obj, 0, len(m))
		for k := range m {
			out = append(out, pyval.Field{Key: k, Val: normalizeYAML(m[k])})
		}
		sortObjByKey(out)
		return out, true
	case map[any]any:
		out := make(pyval.Obj, 0, len(m))
		for k, val := range m {
			if ks, ok := k.(string); ok {
				out = append(out, pyval.Field{Key: ks, Val: normalizeYAML(val)})
			}
		}
		sortObjByKey(out)
		return out, true
	}
	return nil, false
}

func sortObjByKey(o pyval.Obj) {
	for i := 1; i < len(o); i++ {
		for j := i; j > 0 && o[j].Key < o[j-1].Key; j-- {
			o[j], o[j-1] = o[j-1], o[j]
		}
	}
}

// normalizeYAML turns yaml.v3's nested containers into the shapes the rest
// of this package understands: []any for a sequence (so the "is it a list?"
// coercion test can be a type switch) and a value otherwise.
//
// Integers arrive as `int` from yaml.v3 and as Python `int` from PyYAML;
// both render identically through pyval, so nothing is converted.
func normalizeYAML(v any) any {
	switch t := v.(type) {
	case []any:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = normalizeYAML(e)
		}
		return out
	case map[string]any:
		o, _ := asMapping(t)
		return o
	}
	return v
}

// ErrNoNames is compose_persona's `ValueError("compose_persona requires at
// least one persona name")`, message text included: it reaches a
// SpawnResult summary in Python and an operator reads it.
var ErrNoNames = errors.New("compose_persona requires at least one persona name")

// NotFoundError is `ValueError(f"Persona not found: {name!r}")`. The name
// is rendered with Python's repr, not Go's %q — they differ on quote
// choice and on every non-ASCII character.
type NotFoundError struct{ Name string }

func (e *NotFoundError) Error() string {
	return "Persona not found: " + pytext.Repr(e.Name)
}
