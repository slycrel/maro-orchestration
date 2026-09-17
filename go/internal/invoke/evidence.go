package invoke

import (
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Evidence is the recorded execution a judge is shown next to the worker's
// claim: the tool effects an invocation announced and what each returned.
// It is derived only from committed records (tool_effect,
// tool_effect_result, the terminal), so the driver — from the records the
// shell just committed — and the fold — from the same records replayed —
// build byte-identical text through this one function. Nothing here is a
// judgement; it is the record, bounded.
//
// Why a judge needs it: the dev-Mac evaluation of System One judges showed
// that a judge reading a claim alone confidently passes semantically wrong
// work, and that no confidence threshold catches it — the same judge with
// the execution record in its state does not miss. The section is always
// present: an execute that offered no tools, or used none, says so, so an
// absent record is never mistaken for an empty one.
const (
	// EvidenceMaxBytes bounds the whole section. Registered as
	// judgment.evidence.max_bytes: a judge state is one request; past this
	// the evidence is the run, not the step.
	EvidenceMaxBytes = 16 << 10
	// EvidencePerEffectBytes bounds one effect's output tail.
	EvidencePerEffectBytes = 2 << 10
	// EvidenceUnavailable is the text when there is no execution record at
	// all to derive from: a replayed corpus case, or a step whose call
	// cannot be found. It names the situation rather than implying that
	// an execute ran and did nothing.
	EvidenceUnavailable = "no execution record: judge from the result text alone"
)

// GatedEvidence is a step that never ran because a declared prerequisite
// did not end done.
const GatedEvidence = "not executed: gated by a declared prerequisite; no effects"

// ForkEvidence is a parallel step: its members ran as their own runs and
// their effects are recorded on those runs.
func ForkEvidence(members int) string {
	return fmt.Sprintf("parallel step: %d members ran as their own runs; their effects are recorded on those runs, not on this step", members)
}

// Digest renders one invocation's recorded execution. tools says whether
// the request offered tools (a tool-less call cannot have effects, and
// says so). get reads a result's output thought; nil means outputs are
// not readable here and are reported as such. max bounds the text.
func Digest(tools bool, effects []*ToolEffect, results map[int]*ToolEffectResult, term TerminalState, reason string, get func(thought.Ref) ([]byte, error), max int) string {
	if max <= 0 {
		max = EvidenceMaxBytes
	}
	var b strings.Builder
	t := string(term)
	if t == "" {
		t = "unknown"
	}
	fmt.Fprintf(&b, "execute ended %s", t)
	if reason != "" {
		fmt.Fprintf(&b, ": %s", oneLine(reason))
	}
	b.WriteString("\n")
	if !tools {
		b.WriteString("tools: not offered (a tool-less call; no effects possible)\n")
	} else {
		b.WriteString("tools: offered\n")
	}
	if len(effects) == 0 {
		b.WriteString("no recorded effects\n")
		return clipEvidence(b.String(), max)
	}
	for _, e := range effects {
		if e == nil {
			continue
		}
		fmt.Fprintf(&b, "#%d %s [%s]", e.Ordinal, e.Op, e.Class)
		if e.Refused {
			b.WriteString(", refused")
		}
		r := results[e.Ordinal]
		switch {
		case r == nil:
			b.WriteString(": unanswered\n")
			continue
		case r.IsError:
			b.WriteString(": error\n")
		default:
			b.WriteString(": ok\n")
		}
		b.WriteString(outputTail(r, get))
	}
	return clipEvidence(b.String(), max)
}

// outputTail is a result's output, decoded and bounded, each line prefixed
// so it cannot be mistaken for the section's own structure.
func outputTail(r *ToolEffectResult, get func(thought.Ref) ([]byte, error)) string {
	if get == nil {
		return "  | (output not readable here)\n"
	}
	body, err := get(r.Output)
	if err != nil {
		return "  | (output unreadable: " + oneLine(err.Error()) + ")\n"
	}
	raw, err := DecodeEvidence(body)
	if err != nil {
		return "  | (output undecodable: " + oneLine(err.Error()) + ")\n"
	}
	if len(raw) == 0 {
		return "  | (empty output)\n"
	}
	if !utf8.Valid(raw) {
		return fmt.Sprintf("  | <%d bytes, not utf-8>\n", len(raw))
	}
	s := string(raw)
	over := 0
	if len(s) > EvidencePerEffectBytes {
		over = len(s) - EvidencePerEffectBytes
		s = s[:EvidencePerEffectBytes]
		// do not cut a rune in half
		for !utf8.ValidString(s) {
			s = s[:len(s)-1]
			over++
		}
	}
	var b strings.Builder
	for _, line := range strings.Split(strings.TrimRight(s, "\n"), "\n") {
		b.WriteString("  | ")
		b.WriteString(line)
		b.WriteString("\n")
	}
	if over > 0 {
		fmt.Fprintf(&b, "  | …(+%d bytes)\n", over)
	}
	return b.String()
}

func oneLine(s string) string {
	s = strings.TrimSpace(strings.ReplaceAll(s, "\n", " "))
	if len(s) > 400 {
		s = s[:400] + "…"
	}
	return s
}

// clip bounds the section, on a line boundary, with the bound named.
func clipEvidence(s string, max int) string {
	if len(s) <= max {
		return s
	}
	cut := s[:max]
	if i := strings.LastIndex(cut, "\n"); i > 0 {
		cut = cut[:i+1]
	}
	return cut + fmt.Sprintf("(evidence truncated at %d bytes)\n", max)
}
