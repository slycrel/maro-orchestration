package persona

import (
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// tierRank and scopeRank are compose_persona's two ranking tables. The
// DEFAULTS differ and it is not a typo: an unknown tier ranks 1, which is
// mid — so a persona with `model_tier: bespoke` outranks a cheap one and
// loses to a power one — while an unknown scope ranks 0, the bottom, so it
// loses to everything named. Both measured.
var (
	tierRank  = map[string]int{"power": 2, "mid": 1, "cheap": 0}
	scopeRank = map[string]int{"global": 2, "project": 1, "session": 0}
)

func rankOf(table map[string]int, key string, def int) int {
	if v, ok := table[key]; ok {
		return v
	}
	return def
}

// Compose merges several personas into one spec, left to right.
//
// Signature note: Python's is `compose_persona(*names, registry=None,
// extra_prompt="")`, so the names come first there and last here. Nothing
// else moves.
//
// Five behaviours here are places a plausible Go rewrite answers
// differently, all measured:
//
//   - The SINGLE-NAME FAST PATH returns the registry's own spec object,
//     unchanged — same Composes (empty, NOT [name]), same SourceFile (the
//     file, NOT ""). It fires only when there is exactly one name AND
//     extra_prompt is falsy, so `Compose(reg, "EX", "a")` takes the full
//     path and does set Composes.
//   - `max(..., key=rank, default=...)` returns the FIRST maximal element,
//     and its generator FILTERS FALSY tiers first. Two personas at "power"
//     resolve to the earlier one's string (identical here, but not if one
//     day the strings carry a suffix), and a persona whose tier is "" is
//     not a candidate at all — so composing two tier-less personas falls
//     to the `default=`, which is "mid".
//   - The prompt sections skip a spec whose system_prompt STRIPS to empty,
//     so a body of " " contributes neither text nor a "---" separator.
//   - communication_style is `"; ".join(dict.fromkeys(styles))`: dedup
//     that PRESERVES first-seen order, over the styles that are non-empty.
//   - role is `specs[-1].role` unconditionally — the last persona's role
//     wins even if it is the empty string.
func Compose(reg *Registry, extraPrompt string, names ...string) (*Spec, error) {
	if len(names) == 0 {
		return nil, ErrNoNames
	}

	specs := make([]*Spec, 0, len(names))
	for _, name := range names {
		spec := reg.Load(name)
		if spec == nil {
			return nil, &NotFoundError{Name: name}
		}
		specs = append(specs, spec)
	}

	// `if len(specs) == 1 and not extra_prompt` — a TRUTHINESS test on the
	// raw string, not on its stripped form: extra_prompt=" " does NOT take
	// the fast path, and then contributes nothing because the section
	// filter below strips it. So a one-name compose with a whitespace
	// extra_prompt differs from one with none: same prompt text, but
	// Composes becomes ["a"] and SourceFile is cleared.
	if len(specs) == 1 && extraPrompt == "" {
		return specs[0], nil
	}

	var sections []string
	for _, s := range specs {
		if pytext.Strip(s.SystemPrompt) != "" {
			sections = append(sections, pytext.Strip(s.SystemPrompt))
		}
	}
	if pytext.Strip(extraPrompt) != "" {
		sections = append(sections, pytext.Strip(extraPrompt))
	}

	toolAccess := []any{}
	for _, s := range specs {
		for _, t := range s.ToolAccess {
			if !containsAny(toolAccess, t) {
				toolAccess = append(toolAccess, t)
			}
		}
	}

	hooks := []any{}
	for _, s := range specs {
		for _, h := range s.Hooks {
			if !containsAny(hooks, h) {
				hooks = append(hooks, h)
			}
		}
	}

	// `max(gen, key=..., default=...)`: the generator's FALSY entries never
	// reach max at all, the first maximal candidate wins a tie, and the
	// default applies only when the generator was EMPTY — not when the
	// winner happens to equal it. `found` is that last distinction, and
	// without it two personas at "cheap" would compose to "mid".
	modelTier, found := "mid", false
	for _, s := range specs {
		if s.ModelTier == "" {
			continue
		}
		if !found || rankOf(tierRank, s.ModelTier, 1) > rankOf(tierRank, modelTier, 1) {
			modelTier, found = s.ModelTier, true
		}
	}

	memoryScope, found := "session", false
	for _, s := range specs {
		if s.MemoryScope == "" {
			continue
		}
		if !found || rankOf(scopeRank, s.MemoryScope, 0) > rankOf(scopeRank, memoryScope, 0) {
			memoryScope, found = s.MemoryScope, true
		}
	}

	var styles []string
	seenStyle := map[string]bool{}
	for _, s := range specs {
		if s.CommunicationStyle == "" || seenStyle[s.CommunicationStyle] {
			continue
		}
		seenStyle[s.CommunicationStyle] = true
		styles = append(styles, s.CommunicationStyle)
	}

	nameParts := make([]string, len(specs))
	for i, s := range specs {
		nameParts[i] = s.Name
	}
	composes := make([]any, len(names))
	for i, n := range names {
		composes[i] = n
	}

	return &Spec{
		Name:               strings.Join(nameParts, "+"),
		Role:               specs[len(specs)-1].Role,
		ModelTier:          modelTier,
		ToolAccess:         toolAccess,
		MemoryScope:        memoryScope,
		CommunicationStyle: strings.Join(styles, "; "),
		SystemPrompt:       strings.Join(sections, "\n\n---\n\n"),
		Hooks:              hooks,
		Composes:           composes,
		SourceFile:         "",
	}, nil
}

// containsAny is Python's `x not in list`, which compares with `==`.
//
// pyval.Eq, not Go's `==`. The elements can be any YAML scalar (see the
// package doc on why the list fields are []any), and Go's `==` over `any`
// says `5 != 5.0` where Python says they are equal, and PANICS outright on
// a nested sequence rather than answering.
func containsAny(list []any, want any) bool {
	for _, v := range list {
		if pyval.Eq(v, want) {
			return true
		}
	}
	return false
}
