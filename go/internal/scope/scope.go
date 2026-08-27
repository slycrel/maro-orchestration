// Package scope ports src/scope.py — "scope + resolved intent, the thread
// the driver watches". One LLM call before the planner decomposes turns a
// goal into an inversion-derived ScopeSet plus a deliverable map, and both
// are injected into planning context. It has been live on this box since
// 2026-07-09, so its behaviour is load-bearing rather than theoretical:
// scope.md and resolved_intent.md are read back by reanchor.py,
// navigator_shadow.py and closure_verify.py, and the proxy_resolution dict
// is serialised into the captain's log.
//
// WHAT IS NOT HERE, NAMED (do not mistake absence for coverage):
//
//   - `skill_loader.SkillLoader().load_full("resolve_ambiguity")`. scope.py
//     appends that skill body to the director-proxy system prompt, under its
//     own `except Exception` — so "the loader is not importable" is part of
//     the contract and produces a prompt WITHOUT the block. skill_loader.py
//     is NOT PORTED. GenerateOptions.SkillBody is the seam: a nil func is
//     exactly CPython's failed import, and a caller that has the loader
//     wires it. TestTheSkillSeamReproducesBothCPythonBranches measures both
//     directions against CPython.
//
//   - `knowledge_lens.record_decision`. The proxy's committed interpretation
//     is journalled so future runs of similar goals inherit it, again under
//     its own `except Exception` (log.debug, non-fatal). knowledge_lens.py
//     is NOT PORTED — internal/knowledge carries only the pack importer's
//     Hypothesis half, not the decision journal. GenerateOptions.
//     RecordDecision is the seam, nil = the failed import.
//
//   - `PersonaRegistry()`'s own construction. Python builds it inside
//     resolve_ambiguity_via_proxy, where it reads config.personas_dir() and
//     MKDIRS <workspace>/personas as a side effect of naming it. This port
//     takes the registry as an argument, the same choice internal/director
//     and internal/persona make: one resolution order, and it comes from the
//     caller. A nil Registry IS the branch where that constructor raised.
//
// NO FILESYSTEM, NO SUBPROCESS. Nothing in this package opens a path or
// spawns anything; the only two things that could (the persona registry and
// the skill loader) are arguments. So the empty-root hazard — `str(Path(""))`
// being "." and `git -C <empty>` running in the caller's cwd — has no reachable
// site here, and this package deliberately offers no "build me a registry
// from a workspace string" helper that would create one.
//
// Removed upstream, and deliberately absent here: inject_scope_into_context
// and inject_resolved_intent_into_context, deleted from scope.py 2026-07-02
// (zero production callers, test-only).
package scope

import (
	"context"
	"regexp"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/persona"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// ---------------------------------------------------------------------------
// Set — Python's ScopeSet
// ---------------------------------------------------------------------------

// Set is the scope derived from an inversion pass on a goal.
//
// The three lists are non-nil everywhere this package builds one, matching
// Python's `field(default_factory=list)`: a section that was absent, and a
// section whose heading carried no bullets, are the SAME value there ([]),
// and nothing downstream can tell them apart. A zero-value Go Set has nil
// slices, which is a distinction CPython has no spelling for — construct
// through the parsers.
type Set struct {
	FailureModes []string
	InScope      []string
	OutOfScope   []string
	// RawText is the original LLM output, kept for audit/debug. It is
	// populated even when nothing parsed — that is the point of the branch
	// that returns an empty Set rather than nil.
	RawText string
	// ProxyResolution is set when the first scope pass returned a
	// clarification question and the director-proxy committed to one
	// interpretation before a successful retry. Keys, IN THIS ORDER:
	// "interpretation", "reason", "clarification_question". Nil = no proxy
	// resolution happened (scope parsed on the first try).
	//
	// pyval.Obj rather than map[string]any because handle.py hands this
	// straight into a captain's-log event context, where CPython's
	// json.dumps writes INSERTION order and Go's encoding/json over a map
	// SORTS. A map here forks the on-disk row between the two runtimes.
	ProxyResolution pyval.Obj
}

// ToMarkdown renders the scope as injectable markdown for planner context.
func (s Set) ToMarkdown() string {
	parts := []string{"## Scope (goal bounds)"}
	if len(s.FailureModes) > 0 {
		parts = append(parts, "\n### Failure modes to avoid")
		for _, m := range s.FailureModes {
			parts = append(parts, "- "+m)
		}
	}
	if len(s.InScope) > 0 {
		parts = append(parts, "\n### In scope")
		for _, m := range s.InScope {
			parts = append(parts, "- "+m)
		}
	}
	if len(s.OutOfScope) > 0 {
		parts = append(parts, "\n### Out of scope")
		for _, m := range s.OutOfScope {
			parts = append(parts, "- "+m)
		}
	}
	return strings.Join(parts, "\n")
}

// IsEmpty is True when the scope has no content — treat as not-generated.
func (s Set) IsEmpty() bool {
	return !(len(s.FailureModes) > 0 || len(s.InScope) > 0 || len(s.OutOfScope) > 0)
}

// ---------------------------------------------------------------------------
// Deliverable + ResolvedIntent
// ---------------------------------------------------------------------------

// validShapes is Python's `_VALID_SHAPES` frozenset. Membership only, so
// the iteration order it does not have is not needed.
var validShapes = map[string]bool{"document": true, "runtime": true, "data": true}

// Deliverable is a concrete artifact the goal implies, with any known
// preconditions.
//
// Shape is Python's `Optional[str]`, collapsed to "" for None — and that
// collapse is SOUND rather than convenient: parseDeliverableLine only ever
// stores a value that is in validShapes, and "" is not, so an empty Shape
// can only mean None. Every reader is `if self.shape`, a truthiness test
// that treats None and "" identically anyway.
type Deliverable struct {
	Name          string
	Description   string
	Preconditions []string
	Shape         string
}

// ToMarkdownLine renders one deliverable bullet.
func (d Deliverable) ToMarkdownLine() string {
	pre := ""
	if len(d.Preconditions) > 0 {
		pre = " [preconditions: " + strings.Join(d.Preconditions, ", ") + "]"
	}
	shape := ""
	if d.Shape != "" {
		shape = " [shape: " + d.Shape + "]"
	}
	desc := ""
	if d.Description != "" {
		desc = ": " + d.Description
	}
	return "- " + d.Name + desc + pre + shape
}

// ResolvedIntent is the thread the driver watches — what we know about the
// goal before decompose.
//
// Scope is a VALUE, where Python's is a reference shared with whatever
// generate_scope returned. The aliasing is observable in CPython (mutating
// intent.scope.proxy_resolution changes the caller's ScopeSet too) and is
// not here; no live caller does it — handle.py takes `_resolved_intent.scope`
// and only reads — so this is a named, unexercised divergence rather than a
// silent one.
type ResolvedIntent struct {
	Scope        Set
	Deliverables []Deliverable
	// RawText is the original LLM output — the same payload as Scope.RawText.
	RawText string
}

// ToMarkdown renders the resolved intent as injectable markdown for planner
// context.
func (r ResolvedIntent) ToMarkdown() string {
	var parts []string
	// `self.scope.proxy_resolution or {}` — a nil Obj and an empty one both
	// land on the empty dict, and Get on a nil Obj answers "absent", so the
	// `or {}` needs no spelling of its own.
	resolution := r.Scope.ProxyResolution
	// `str(resolution.get("interpretation", ""))` — str() of whatever is
	// there, NOT a coercion that assumes a string. A caller that put a
	// number in this dict gets CPython's spelling of it.
	//
	// The default fires on an ABSENT KEY ONLY. A key that is present and
	// holds None is `str(None)` — the four characters "None", which are
	// truthy after strip() and RENDER: "- Rationale: None" reaches
	// resolved_intent.md and from there reanchor.py's binding-interpretation
	// read. The first draft of this collapsed a present nil onto "" and the
	// differential's "intent with a numeric interpretation" row caught it.
	interpRaw, ok := resolution.Get("interpretation")
	if !ok {
		interpRaw = ""
	}
	reasonRaw, ok := resolution.Get("reason")
	if !ok {
		reasonRaw = ""
	}
	interpretation := pytext.Strip(pyval.Str(interpRaw))
	reason := pytext.Strip(pyval.Str(reasonRaw))
	if interpretation != "" {
		parts = append(parts, "## Resolved interpretation (binding goal definition)")
		parts = append(parts, "- "+interpretation)
		if reason != "" {
			parts = append(parts, "- Rationale: "+reason)
		}
	}
	if !r.Scope.IsEmpty() {
		parts = append(parts, r.Scope.ToMarkdown())
	}
	if len(r.Deliverables) > 0 {
		parts = append(parts, "\n## Deliverables (concrete artifacts)")
		for _, d := range r.Deliverables {
			parts = append(parts, d.ToMarkdownLine())
		}
	}
	if len(parts) == 0 {
		return ""
	}
	return strings.Join(parts, "\n")
}

// IsEmpty is True when neither scope nor deliverables have content.
func (r ResolvedIntent) IsEmpty() bool {
	return r.Scope.IsEmpty() && len(r.Deliverables) == 0
}

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

// headingRe is `re.compile(r"^#{1,4}\s*(.+?)\s*$", re.MULTILINE)`.
//
// `\s` is pytext.SpaceClass: Python's is 29 code points and Go's is five, and
// the difference decides whether "## In Scope" is a recognised heading
// (it is, in CPython) or a bullet-less line (Go, transcribed).
//
// `(?m)` is re.MULTILINE, and the `$` under it is why: CPython's `$` without
// MULTILINE also matches just before a trailing newline, and Go's plain `$`
// is `\z`. Under `(?m)` both engines mean "end of line". The subject is
// always one element of `text.split("\n")` and so holds no newline at all,
// which is also what makes `^` equivalent to re.match's anchor here.
var headingRe = regexp.MustCompile(`(?m)^#{1,4}` + pytext.SpaceClass +
	`*(.+?)` + pytext.SpaceClass + `*$`)

// normalizeSectionKey is scope.py's inner `_normalize`. Returns "" for
// Python's None — the caller distinguishes it, so the sentinel has to be a
// value no real key can take, and every returned key is a non-empty literal.
func normalizeSectionKey(key string) string {
	// lower() THEN strip() THEN rstrip(":"), transcribed from scope.py.
	// Only the lower() can change an answer, and the other two are dead —
	// here AND upstream. Three mutations say so and the reason is
	// structural, so no fixture can ever kill them (L8):
	//
	//   swapping lower/strip          SURVIVES. No code point in the range
	//     changes its whitespace-ness under lower(), so the two commute
	//     everywhere. Measured, not assumed — an earlier draft of this
	//     comment claimed the order mattered "for the Unicode cases
	//     pytext.Lower handles". It does not.
	//   TrimRight -> TrimSuffix       SURVIVES, and so does deleting either
	//   deleting strip or rstrip        of them outright. Every test below
	//     is a SUBSTRING test against a needle carrying no edge whitespace
	//     and no colon, and every return is a literal. Trimming the ends
	//     removes characters; it cannot create or destroy an interior
	//     match, so nothing downstream can see it.
	//
	// pytext.Lower is the opposite: replacing it with strings.ToLower is
	// killed. "İN SCOPE" lowers to "i" + U+0307 + "n scope" in CPython,
	// which does NOT contain "in scope" — the heading falls through to ""
	// and its bullets are dropped. That is a section silently vanishing
	// from a scope document, and it is why this call is not the stdlib one.
	//
	// The colon and space fixtures in scope_diff_test.go ("trailing
	// colon", "space then colon", "two trailing colons") pin AGREEMENT
	// with CPython on these shapes. They are not evidence about the
	// trimming, and by the paragraph above nothing could be.
	k := strings.TrimRight(pytext.Strip(pytext.Lower(key)), ":")
	// The order of these four is load-bearing. "mode" before anything else
	// means a heading like "## Model" is filed under failure_modes; out-of-
	// scope before in-scope is the shape scope.py committed to.
	if strings.Contains(k, "failure") || strings.Contains(k, "mode") {
		return "failure_modes"
	}
	if strings.Contains(k, "out of scope") || strings.Contains(k, "out-of-scope") ||
		strings.Contains(k, "outofscope") {
		return "out_of_scope"
	}
	if strings.Contains(k, "in scope") || strings.Contains(k, "in-scope") ||
		strings.Contains(k, "inscope") {
		return "in_scope"
	}
	if strings.Contains(k, "deliverable") || strings.Contains(k, "artifact") {
		return "deliverables"
	}
	return ""
}

// splitSections splits a markdown blob into {section_key: [bullet_items]}.
//
// A map, not an ordered Obj, because `sections` is only ever read through
// `.get()` — its insertion order is not observable and nothing serialises it.
//
// Two behaviours a tidier rewrite loses. An UNRECOGNISED heading sets the
// current key to None, so the bullets under it are dropped AND the section
// before it is still flushed. And a section heading that appears TWICE keeps
// the LAST occurrence's items, because each heading assigns the previous
// key's list into the map.
func splitSections(text string) map[string][]string {
	sections := map[string][]string{}
	// currentKey "" is Python's None; hasKey is `current_key is not None`,
	// which is a separate fact because "" is also a falsy string and the
	// Python guard is an identity test, not truthiness.
	currentKey := ""
	hasKey := false
	currentItems := []string{}

	for _, line := range strings.Split(text, "\n") {
		stripped := pytext.Strip(line)
		if m := headingRe.FindStringSubmatch(line); m != nil {
			if hasKey {
				sections[currentKey] = currentItems
			}
			currentKey = normalizeSectionKey(m[1])
			hasKey = currentKey != ""
			currentItems = []string{}
			continue
		}
		if hasKey && (strings.HasPrefix(stripped, "-") || strings.HasPrefix(stripped, "*")) {
			item := pytext.Strip(strings.TrimLeft(stripped, "-* "))
			if item != "" {
				currentItems = append(currentItems, item)
			}
		}
	}
	if hasKey {
		sections[currentKey] = currentItems
	}
	return sections
}

// section is `sections.get(key, [])`: an absent key yields an EMPTY list,
// not a nil one, because that is the only value CPython can produce here.
func section(sections map[string][]string, key string) []string {
	if v, ok := sections[key]; ok {
		return v
	}
	return []string{}
}

// preconditionsRe is `re.compile(r"\[preconditions?:\s*(.+?)\s*\]",
// re.IGNORECASE)`.
//
// PyFoldI is not decoration: "precondition" carries two literal `i`s, and
// CPython's re.IGNORECASE folds U+0130 and U+0131 onto ASCII `i` where Go's
// (?i) does not. `[precondıtions: curl]` is an annotation in CPython
// and prose in an unwrapped Go — which moves the text into the deliverable's
// DESCRIPTION and loses the precondition list closure's pre-flight reads.
var preconditionsRe = regexp.MustCompile(pytext.PyFoldI(
	`(?i)\[preconditions?:` + pytext.SpaceClass + `*(.+?)` +
		pytext.SpaceClass + `*\]`))

// splitPreconditions splits a preconditions annotation on top-level commas
// only.
//
// LLMs routinely emit prose items with parenthesized lists inside —
// `standard utilities (grep, cat, wc)`. A naive split shreds those into
// fragments that closure's pre-flight then misreads as command names (run
// d2f4e2f4's two "inconclusive" checks were `wc)` failing shutil.which).
// An unbalanced open paren swallows the rest into one item, which is the
// safe direction.
//
// CPython iterates CODE POINTS; this iterates BYTES. The two are equivalent
// here and only here: the three characters the loop branches on — `(`, `)`,
// `,` — are ASCII, and no UTF-8 continuation byte can equal an ASCII byte,
// so every multi-byte code point is copied through untouched. Byte
// iteration is additionally the SAFER of the two in Go, because ranging
// over runes would rewrite invalid UTF-8 to U+FFFD — a mutation CPython's
// already-decoded str cannot perform.
func splitPreconditions(raw string) []string {
	var out []string
	var buf strings.Builder
	depth := 0
	for i := 0; i < len(raw); i++ {
		ch := raw[i]
		if ch == '(' {
			depth++
		} else if ch == ')' {
			if depth-1 < 0 {
				depth = 0
			} else {
				depth = depth - 1
			}
		}
		if ch == ',' && depth == 0 {
			out = append(out, pytext.Strip(buf.String()))
			buf.Reset()
		} else {
			buf.WriteByte(ch)
		}
	}
	out = append(out, pytext.Strip(buf.String()))
	kept := []string{}
	for _, p := range out {
		if p != "" {
			kept = append(kept, p)
		}
	}
	return kept
}

// shapeRe is `re.compile(r"\[shape:\s*(.+?)\s*\]", re.IGNORECASE)`.
//
// Deliberately NOT wrapped in PyFoldI: "shape" carries no `i`, so the
// transform is a no-op and a wrapper here would be cargo. The fold census in
// internal/pytext defines exposure as "the remedy would change this
// pattern", so it agrees.
var shapeRe = regexp.MustCompile(`(?i)\[shape:` + pytext.SpaceClass +
	`*(.+?)` + pytext.SpaceClass + `*\]`)

// parseDeliverableLine parses a single deliverable bullet.
//
// Format: `<name>: <description> [preconditions: X, Y] [shape: runtime]`.
// The annotations are lifted out FIRST so they cannot pollute the
// description, and they may appear in either order. An unrecognised shape
// value is dropped — treated the same as no annotation, since a value we
// cannot classify against is not worth pretending to trust.
//
// The two `m.start()`/`m.end()` slices are code-point offsets in CPython and
// byte offsets here, and that is not a divergence: both index the SAME
// string the offsets came from, so the two spellings cut at the same place.
func parseDeliverableLine(item string) Deliverable {
	if item == "" {
		// `Deliverable(name="")` — the dataclass's default_factory still
		// gives preconditions an EMPTY LIST, never None, and a nil slice
		// here would serialise as null where CPython writes [].
		return Deliverable{Name: "", Preconditions: []string{}}
	}
	preconditions := []string{}
	if loc := preconditionsRe.FindStringSubmatchIndex(item); loc != nil {
		preRaw := item[loc[2]:loc[3]]
		preconditions = splitPreconditions(preRaw)
		item = pytext.Strip(item[:loc[0]] + item[loc[1]:])
	}
	shape := ""
	if loc := shapeRe.FindStringSubmatchIndex(item); loc != nil {
		// strip() THEN lower(), the order scope.py wrote.
		shapeRaw := pytext.Lower(pytext.Strip(item[loc[2]:loc[3]]))
		if validShapes[shapeRaw] {
			shape = shapeRaw
		}
		item = pytext.Strip(item[:loc[0]] + item[loc[1]:])
	}
	if strings.Contains(item, ":") {
		name, desc, _ := strings.Cut(item, ":")
		return Deliverable{
			Name:          pytext.Strip(name),
			Description:   pytext.Strip(desc),
			Preconditions: preconditions,
			Shape:         shape,
		}
	}
	return Deliverable{
		Name:          pytext.Strip(item),
		Preconditions: preconditions,
		Shape:         shape,
	}
}

// parseScopeMarkdown parses the LLM's markdown response into a Set.
//
// Tolerates extra whitespace, different heading levels, alternate phrasings.
// Returns an empty Set if nothing parseable — the caller decides whether
// that means "skip injection" or "warn and proceed without scope".
//
// A whitespace-only text takes the early return and STILL keeps its raw
// text: `ScopeSet(raw_text=text or "")` and "   " is truthy, so the
// evidence survives.
//
// The deliverables section, if present, is ignored here; use
// parseResolvedIntentMarkdown to capture it.
func parseScopeMarkdown(text string) Set {
	if text == "" || pytext.Strip(text) == "" {
		return Set{
			FailureModes: []string{},
			InScope:      []string{},
			OutOfScope:   []string{},
			RawText:      text,
		}
	}
	sections := splitSections(text)
	return Set{
		FailureModes: section(sections, "failure_modes"),
		InScope:      section(sections, "in_scope"),
		OutOfScope:   section(sections, "out_of_scope"),
		RawText:      text,
	}
}

// parseResolvedIntentMarkdown parses the LLM's markdown into a
// ResolvedIntent (scope + deliverables).
func parseResolvedIntentMarkdown(text string) ResolvedIntent {
	if text == "" || pytext.Strip(text) == "" {
		return ResolvedIntent{
			Scope: Set{
				FailureModes: []string{},
				InScope:      []string{},
				OutOfScope:   []string{},
				RawText:      text,
			},
			Deliverables: []Deliverable{},
			RawText:      text,
		}
	}
	sections := splitSections(text)
	s := Set{
		FailureModes: section(sections, "failure_modes"),
		InScope:      section(sections, "in_scope"),
		OutOfScope:   section(sections, "out_of_scope"),
		RawText:      text,
	}
	return ResolvedIntent{
		Scope:        s,
		Deliverables: deliverablesFrom(sections),
		RawText:      text,
	}
}

// deliverablesFrom is the two-line comprehension scope.py writes twice —
// parse every bullet, then drop the ones with empty names (malformed
// lines). The result is always non-nil, matching the list CPython builds.
func deliverablesFrom(sections map[string][]string) []Deliverable {
	out := []Deliverable{}
	for _, line := range section(sections, "deliverables") {
		if d := parseDeliverableLine(line); d.Name != "" {
			out = append(out, d)
		}
	}
	return out
}

// ---------------------------------------------------------------------------
// Director-proxy fallback for clarification-style scope responses
// ---------------------------------------------------------------------------

// looksLikeClarification is True when the LLM returned a question instead of
// structured markdown.
//
// Heuristic, intentionally narrow: only prose with a question mark counts.
// Empty responses or garbage without a question are a different failure
// class and must not route through the proxy — they indicate an
// adapter/model problem, not an ambiguity problem.
//
// The two bounds are CODE POINTS. `len()` on a Python str counts characters;
// `len()` on a Go string counts bytes, and a 30-character reply in any
// non-Latin script is over 30 bytes — so a byte count admits texts CPython
// rejects at the low bound and rejects texts CPython admits at the high one.
func looksLikeClarification(rawText string) bool {
	if rawText == "" {
		return false
	}
	text := pytext.Strip(rawText)
	if n := len([]rune(text)); n < 30 || n > 4000 {
		return false
	}
	return strings.Contains(text, "?")
}

// proxyResponseRe is
//
//	re.compile(r"INTERPRETATION\s*:\s*(.+?)\s*(?:\n+REASON\s*:\s*(.+?))?\s*$",
//	           re.IGNORECASE | re.DOTALL)
//
// `(?s)` is re.DOTALL. `$` is CPython's non-MULTILINE `$`, which also
// matches before a single trailing newline where Go's is `\z` — equivalent
// HERE and only because the one call site searches `content.strip()`, which
// cannot end in a newline. Pinned by a fixture whose input ends in "\n".
//
// PyFoldI because both keywords are full of literal `I`s.
var proxyResponseRe = regexp.MustCompile(pytext.PyFoldI(
	`(?is)INTERPRETATION` + pytext.SpaceClass + `*:` + pytext.SpaceClass +
		`*(.+?)` + pytext.SpaceClass + `*(?:\n+REASON` + pytext.SpaceClass +
		`*:` + pytext.SpaceClass + `*(.+?))?` + pytext.SpaceClass + `*$`))

// parseProxyResponse extracts INTERPRETATION / REASON from director-proxy
// output. Nil is Python's None.
//
// scope.py's comment says "Find the LAST INTERPRETATION: line — proxies
// sometimes preamble despite instructions, and the commitment is always at
// the end." The code is `re.search`, which finds the FIRST. The behaviour is
// ported, not the comment; the fixture table carries a two-INTERPRETATION
// input that measures which one CPython actually returns.
//
// `(m.group(2) or "")` collapses an unmatched optional group (None) onto "",
// which is also what Go's FindStringSubmatch gives for a group that did not
// participate — so the two spellings cannot disagree. A MATCHED empty group
// would be a third case, and `(.+?)` cannot produce one.
func parseProxyResponse(content string) pyval.Obj {
	if content == "" {
		return nil
	}
	m := proxyResponseRe.FindStringSubmatch(pytext.Strip(content))
	if m == nil {
		return nil
	}
	interp := pytext.Strip(m[1])
	reason := pytext.Strip(m[2])
	if interp == "" {
		return nil
	}
	return pyval.Obj{
		{Key: "interpretation", Val: interp},
		{Key: "reason", Val: reason},
	}
}

// ---------------------------------------------------------------------------
// Options
// ---------------------------------------------------------------------------

// GenerateOptions carries generate_scope's / generate_resolved_intent's
// keyword arguments plus the four things CPython reaches for by importing.
//
// BUILD IT WITH Defaults(). The Go zero value is NOT CPython's default set —
// MaxTokens 0 against 1200, AllowProxyFallback false against True — and a
// caller who fills in only Adapter gets a materially different call with
// nothing to say so.
type GenerateOptions struct {
	// Adapter is scope.py's positional `adapter`. Nil is the None that
	// makes both generators return None before doing anything.
	Adapter llm.Adapter

	MaxTokens          int
	Temperature        float64
	AncestryContext    string
	AllowProxyFallback bool
	DecisionDomain     string

	// Registry is the director-proxy persona registry. NIL IS THE BRANCH
	// WHERE `PersonaRegistry()` RAISED — see the package doc for why the
	// construction is the caller's.
	Registry *persona.Registry

	// Template is handed to persona.BuildSystemPrompt. Its nil funcs are
	// CPython's failing lazy imports, which render "(unavailable)".
	Template persona.TemplateContext

	// SkillBody is the skill_loader seam. NIL IS THE FAILED IMPORT: no
	// block is appended, and CPython logs at debug. A non-nil func
	// returning an error is the loader raising; returning "" is
	// load_full's None (a falsy body is not appended either).
	SkillBody func(name string) (string, error)

	// RecordDecision is the knowledge_lens seam, called once when the
	// proxy's interpretation binds. NIL IS THE FAILED IMPORT. An error is
	// CPython's `except Exception` around the call — logged at debug,
	// never fatal, because a decision must not take a run down.
	RecordDecision func(decision, rationale, domain, goalContext string) error

	// Log receives the same lines CPython's module logger does, already
	// %-formatted, with the logging levelname that emitted them ("INFO",
	// "WARNING", "DEBUG"). Optional.
	//
	// The level is carried, where internal/director's sibling seam drops it,
	// because scope.py's log lines ARE its observable side effect — the four
	// `[scope-deferred]` markers are the record of what this minimal version
	// punts on, and whether the decision-journal failure is a debug line or a
	// warning decides whether an operator ever sees it. A trace that carries
	// the level is comparable against CPython's LogRecord end to end; one
	// that does not is a partial claim.
	Log func(level, msg string)
}

// Defaults returns scope.py's keyword defaults: max_tokens=1200,
// temperature=0.3, allow_proxy_fallback=True.
func Defaults() GenerateOptions {
	return GenerateOptions{
		MaxTokens:          1200,
		Temperature:        0.3,
		AllowProxyFallback: true,
	}
}

func (o GenerateOptions) logf(level, msg string) {
	if o.Log != nil {
		o.Log(level, msg)
	}
}

// ---------------------------------------------------------------------------
// resolve_ambiguity_via_proxy
// ---------------------------------------------------------------------------

// noAncestry is the placeholder scope.py substitutes for an empty ancestry
// block. The em-dash is U+2014 and is part of the prompt.
const noAncestry = "(no ancestry available — CLI or top-level goal)"

// ResolveAmbiguityViaProxy asks the director-proxy persona to commit to one
// interpretation.
//
// Returns {"interpretation": ..., "reason": ...} on success, or nil if the
// proxy persona is not available, the LLM call fails, or the response does
// not parse. Callers treat nil as "proceed without scope."
func ResolveAmbiguityViaProxy(ctx context.Context, goal, clarificationText,
	ancestryContext string, o GenerateOptions) pyval.Obj {

	if goal == "" || clarificationText == "" || o.Adapter == nil {
		return nil
	}
	// `from persona import ...` cannot fail in a compiled binary, so the
	// first `except` arm of CPython's two has no Go counterpart and is not
	// invented here. The SECOND — PersonaRegistry() raising — is what a nil
	// Registry means.
	if o.Registry == nil {
		o.logf("WARNING", "scope.proxy: PersonaRegistry failed: no registry supplied")
		return nil
	}
	spec := o.Registry.Load("director-proxy")
	if spec == nil {
		o.logf("WARNING", "scope.proxy: director-proxy persona not found")
		return nil
	}

	systemPrompt := persona.BuildSystemPrompt(spec, goal, o.Template)

	// Append the resolve_ambiguity skill body so the how-to is in context.
	// NOT PORTED — see the package doc; a nil seam is the failed import.
	if o.SkillBody != nil {
		if skillBody, err := o.SkillBody("resolve_ambiguity"); err != nil {
			o.logf("DEBUG", "scope.proxy: could not load resolve_ambiguity skill: "+err.Error())
		} else if skillBody != "" {
			systemPrompt = systemPrompt + "\n\n---\n\n" + skillBody
		}
	} else {
		o.logf("DEBUG", "scope.proxy: could not load resolve_ambiguity skill: "+
			"skill_loader is not ported")
	}

	ancestryBlock := pytext.Strip(ancestryContext)
	if ancestryBlock == "" {
		ancestryBlock = noAncestry
	}
	userMsg := "Goal (verbatim):\n" + goal + "\n\n" +
		"The scope generator returned a clarification question instead of " +
		"committing to an interpretation. Its full response:\n\n" +
		pytext.Strip(clarificationText) + "\n\n" +
		"Context / ancestry:\n" + ancestryBlock + "\n\n" +
		"Commit to one interpretation now. Emit exactly:\n" +
		"INTERPRETATION: <one imperative sentence>\n" +
		"REASON: <one justification sentence>"

	resp, err := o.Adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: systemPrompt},
		{Role: "user", Content: userMsg},
	}, llm.Options{
		MaxTokens:   300,
		Temperature: 0.2,
		Purpose:     "scope",
		// no_tools=True: no Tools, so nothing is injected into the prompt.
	})
	if err != nil {
		o.logf("WARNING", "scope.proxy: adapter.complete failed: "+err.Error())
		return nil
	}

	// content_or_empty's own `except` arm has no Go counterpart: it is
	// getattr + str + strip, none of which can fail on a *Response.
	content := llm.ContentOrEmpty(resp)

	parsed := parseProxyResponse(content)
	if parsed == nil {
		o.logf("WARNING", "scope.proxy: response did not match INTERPRETATION/REASON "+
			"format; raw="+pytext.Repr(pyval.Clip(content, 200)))
		return nil
	}
	interp, _ := parsed.Get("interpretation")
	reason, _ := parsed.Get("reason")
	o.logf("INFO", "scope.proxy: committed interpretation="+
		pytext.Repr(pyval.Clip(interp.(string), 120))+" (reason="+
		pytext.Repr(pyval.Clip(reason.(string), 120))+")")
	return parsed
}

// ---------------------------------------------------------------------------
// Generator
// ---------------------------------------------------------------------------

// GenerateResolvedIntent generates a resolved intent (scope + deliverable
// map) for goal. One LLM call. Nil on any failure.
//
// If the scope sections parse but deliverables do not, the caller still gets
// a ResolvedIntent with an empty deliverables list — inject scope alone and
// let the planner proceed.
func GenerateResolvedIntent(ctx context.Context, goal string, o GenerateOptions) *ResolvedIntent {
	if goal == "" || o.Adapter == nil {
		return nil
	}
	s := GenerateScope(ctx, goal, o)
	if s == nil {
		return nil
	}
	// Pick deliverables out of the same raw response scope came from. Cheap:
	// no extra LLM round-trip. The Set is kept AS-IS rather than re-parsed —
	// in CPython that is what lets a test double patched over generate_scope
	// flow its values through unchanged; here it is simply the shape.
	sections := map[string][]string{}
	if s.RawText != "" {
		sections = splitSections(s.RawText)
	}
	intent := &ResolvedIntent{
		Scope:        *s,
		Deliverables: deliverablesFrom(sections),
		RawText:      s.RawText,
	}
	if len(intent.Deliverables) > 0 {
		o.logf("INFO", "resolved_intent: parsed "+itoa(len(intent.Deliverables))+
			" deliverable(s) alongside scope")
	} else {
		o.logf("INFO", "resolved_intent: no deliverables parsed; scope-only intent "+
			"(prompt may need tightening or goal is unusual)")
	}
	return intent
}

// GenerateScope generates a scope for goal via a single-call inversion pass.
//
// Non-fatal: nil on any failure. Never blocks the caller.
//
// The call is single-persona (generalist) — the triad
// (PM/engineer/architect) is deferred until A/B signal justifies the 3x
// cost.
//
// The underlying prompt asks for four sections (failure modes, in/out of
// scope, deliverables). This returns only the scope view — deliverables are
// silently dropped; callers who want the full thread use
// GenerateResolvedIntent.
func GenerateScope(ctx context.Context, goal string, o GenerateOptions) *Set {
	if goal == "" || o.Adapter == nil {
		return nil
	}

	// [scope-deferred] markers: record what this minimal version skips, so
	// expanding the implementation later can grep for these to find all the
	// decisions we punted on. They fire on EVERY call, the proxy retry
	// included — the retry is a second generate_scope, so a run that
	// escalates logs all four twice.
	o.logf("INFO", "[scope-deferred] triad: using single generalist inversion, "+
		"multi-persona rotation deferred")
	o.logf("INFO", "[scope-deferred] lifecycle: scope immutable after set, "+
		"director revise/except/break deferred")
	o.logf("INFO", "[scope-deferred] retrieval: scope fully injected as block, "+
		"per-step relevance deferred")
	o.logf("INFO", "[scope-deferred] memory: scope recorded but no cross-goal "+
		"retrieval, Phase D deferred")

	resp, err := o.Adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: SystemPrompt},
		{Role: "user", Content: "Goal: " + goal},
	}, llm.Options{
		MaxTokens:   o.MaxTokens,
		Temperature: o.Temperature,
		Purpose:     "scope",
	})
	if err != nil {
		o.logf("WARNING", "scope: adapter.complete failed: "+err.Error())
		return nil
	}

	content := llm.ContentOrEmpty(resp)

	if content == "" || pytext.Strip(content) == "" {
		o.logf("WARNING", "scope: LLM returned empty content, skipping scope injection")
		return nil
	}

	s := parseScopeMarkdown(content)
	if s.IsEmpty() {
		// Parse failure. If the response looks like the LLM asked for
		// clarification rather than producing garbage, route it to the
		// director-proxy persona to commit to one interpretation, then retry
		// scope with that interpretation baked into the goal context.
		if o.AllowProxyFallback && looksLikeClarification(content) {
			o.logf("INFO", "scope: response looks like clarification, escalating to director-proxy")
			resolution := ResolveAmbiguityViaProxy(ctx, goal, content, o.AncestryContext, o)
			if resolution != nil {
				interp, _ := resolution.Get("interpretation")
				interpS, _ := interp.(string)
				// Retry scope with the committed interpretation. Disable the
				// proxy fallback on the retry so we cannot recurse if the LLM
				// keeps punting.
				augmentedGoal := goal + "\n\n" +
					"(Interpretation committed by director-proxy: " + interpS + ")"
				retryOpts := o
				retryOpts.AllowProxyFallback = false
				retry := GenerateScope(ctx, augmentedGoal, retryOpts)
				if retry != nil && !retry.IsEmpty() {
					pr := pyval.Obj{}
					for _, f := range resolution {
						pr = append(pr, f)
					}
					pr.Set("clarification_question",
						pyval.Clip(pytext.Strip(content), 800))
					retry.ProxyResolution = pr
					// This interpretation is BINDING: the planner sees it via
					// ResolvedIntent and closure judges against it. A
					// commitment that shapes both planning and the
					// goal-achieved verdict is a design decision — journal it
					// so future runs of similar goals inherit it.
					if o.RecordDecision != nil {
						reasonRaw, _ := resolution.Get("reason")
						reasonS, _ := reasonRaw.(string)
						rationale := pytext.Strip(reasonS)
						if rationale == "" {
							rationale = "director-proxy resolution of an ambiguous goal; " +
								"binding for planning and closure"
						}
						if derr := o.RecordDecision(
							"Goal interpreted as: "+pyval.Clip(interpS, 300),
							rationale,
							// Scoped to the project when the caller knows it:
							// a one-off interpretation is not a global decree,
							// and blank-domain rows inject into EVERY
							// project's recall.
							o.DecisionDomain,
							pyval.Clip(goal, 300),
						); derr != nil {
							o.logf("DEBUG", "scope: decision journal write failed: "+derr.Error())
						}
					} else {
						o.logf("DEBUG", "scope: decision journal write failed: "+
							"knowledge_lens is not ported")
					}
					o.logf("INFO", "scope: director-proxy resolved ambiguity, retry produced "+
						itoa(len(retry.FailureModes))+" failure modes, "+
						itoa(len(retry.InScope))+" in-scope, "+
						itoa(len(retry.OutOfScope))+" out-of-scope")
					return retry
				}
				o.logf("WARNING", "scope: retry after proxy resolution still did not parse")
			}
		}

		// Return the empty Set (with RawText populated) so the caller can
		// persist the raw LLM output for debugging. IsEmpty() still flags
		// "don't inject into planner context" — this is about keeping the
		// evidence, not about changing injection behaviour.
		o.logf("WARNING", "scope: LLM response had no parseable sections, returning raw for debug")
		return &s
	}

	o.logf("INFO", "scope: generated "+itoa(len(s.FailureModes))+" failure modes, "+
		itoa(len(s.InScope))+" in-scope, "+itoa(len(s.OutOfScope))+
		" out-of-scope items")
	return &s
}

// itoa is %d for a non-negative count. Every caller passes a len(), so the
// negative branch strconv would carry is unreachable and is not written.
func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}
