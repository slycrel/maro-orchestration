package invoke

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// ToolPolicy is the operator's say over which of an agentic backend's tools
// a tool-bearing request may use: an allow list (empty = the backend's
// whole set) and a deny list. It is part of the backend's capability
// snapshot — every invocation and attempt config carries the policy the
// call ran under, as one canonical string — so "what could this execute
// reach?" is answered from the record, never from the process that ran it.
type ToolPolicy struct {
	Allow []string
	Deny  []string
}

var toolName = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]*$`)

// DefaultToolPolicy is the policy an operator gets without asking: every
// tool but the network readers. Why: a NOW execute that can fetch the web
// spends and acts outward on the operator's account; the operator turns
// it on (`--deny-tools ""`), it is never on by default.
func DefaultToolPolicy() ToolPolicy { return ToolPolicy{Deny: []string{"WebFetch", "WebSearch"}} }

// ParseToolPolicy reads two comma lists as the CLI gives them. Names are
// checked, trimmed, de-duplicated and sorted; a name on both lists is a
// contradiction and refused.
func ParseToolPolicy(allow, deny string) (ToolPolicy, error) {
	p := ToolPolicy{}
	var err error
	if p.Allow, err = toolList(allow); err != nil {
		return ToolPolicy{}, fmt.Errorf("tool policy: allow: %w", err)
	}
	if p.Deny, err = toolList(deny); err != nil {
		return ToolPolicy{}, fmt.Errorf("tool policy: deny: %w", err)
	}
	for _, a := range p.Allow {
		for _, d := range p.Deny {
			if a == d {
				return ToolPolicy{}, fmt.Errorf("tool policy: %s is both allowed and denied", a)
			}
		}
	}
	return p, nil
}

func toolList(s string) ([]string, error) {
	seen := map[string]bool{}
	var out []string
	for _, raw := range strings.Split(s, ",") {
		n := strings.TrimSpace(raw)
		if n == "" {
			continue
		}
		if !toolName.MatchString(n) {
			return nil, fmt.Errorf("%q is not a tool name", n)
		}
		if !seen[n] {
			seen[n] = true
			out = append(out, n)
		}
	}
	sort.Strings(out)
	return out, nil
}

// String is the canonical form recorded in Capabilities.ToolPolicy:
// `allow=A,B;deny=C` — sorted, each part present only when non-empty,
// empty when the policy says nothing.
func (p ToolPolicy) String() string {
	var parts []string
	if len(p.Allow) > 0 {
		parts = append(parts, "allow="+strings.Join(p.Allow, ","))
	}
	if len(p.Deny) > 0 {
		parts = append(parts, "deny="+strings.Join(p.Deny, ","))
	}
	return strings.Join(parts, ";")
}
