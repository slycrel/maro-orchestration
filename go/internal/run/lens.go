package run

import (
	"fmt"
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Lenses (§13): a persona is a lens over judgement and rendering, never over
// what the work is. A lens is the exact text a judge/render request is
// prefixed with; the invocation carries the lens by name and by the
// content-addressed text, and the fold checks the request bytes begin with
// it. v1 ships the neutral lens (no text: a judge request under it is
// byte-identical to one made with no lens at all) and one non-trivial lens.
const (
	LensNeutral = "neutral"
	LensSkeptic = "skeptic"
)

// lenses maps a lens name to its text. Neutral is the empty text. The
// table is not exported: a lens is bound to a run by name AND content ref
// in the attempt's config, so nothing may rewrite a name's text under a
// running attempt.
var lenses = map[string]string{
	LensNeutral: "",
	LensSkeptic: skepticLens,
}

// LensText is the text of a named lens (neutral: empty, true).
func LensText(name string) (string, bool) {
	t, ok := lenses[name]
	return t, ok
}

const skepticLens = `You are judging as a sceptic. A claim of success is not success: look for the named fact, the concrete artifact, or the executed check that would make the result true, and treat a confident answer that cites none of them as not established. Prefer "unknown" to a verdict you cannot ground in the material shown to you.`

// LensNames lists the lenses this binary knows, sorted.
func LensNames() []string {
	names := make([]string, 0, len(lenses))
	for n := range lenses {
		names = append(names, n)
	}
	sort.Strings(names)
	return names
}

// Lensed renders a request under a lens text: the text, a blank line, the
// request. An empty text leaves the request unchanged — the neutral lens is
// the absence of a prefix, not a different prefix.
func Lensed(lensText, prompt []byte) []byte {
	if len(lensText) == 0 {
		return prompt
	}
	out := make([]byte, 0, len(lensText)+2+len(prompt))
	out = append(out, lensText...)
	out = append(out, '\n', '\n')
	return append(out, prompt...)
}

// lensName is the configured lens as the attempt config records it: the
// neutral lens is recorded as absent.
func (d *Driver) lensName() string {
	if d.Lens == LensNeutral {
		return ""
	}
	return d.Lens
}

// lens is the driver's lens for a judge request: nil (and no text) for
// neutral; otherwise the lens ref, its text stored once (content-addressed).
func (d *Driver) lens() (*invoke.Lens, []byte, error) {
	name := d.lensName()
	if name == "" {
		return nil, nil, nil
	}
	text, ok := lenses[name]
	if !ok {
		return nil, nil, fmt.Errorf("%w: unknown lens %q (known: %v)", ErrConfig, name, LensNames())
	}
	ref, err := d.Store.Put(thought.LensText, []byte(text))
	if err != nil {
		return nil, nil, err
	}
	return &invoke.Lens{Name: name, Text: ref}, []byte(text), nil
}

// lensedRequest is a judge request under the driver's lens.
func (d *Driver) lensedRequest(prompt []byte, tools bool) (invoke.Request, error) {
	l, text, err := d.lens()
	if err != nil {
		return invoke.Request{}, err
	}
	return invoke.Request{Purpose: invoke.PurposeJudge, Prompt: Lensed(text, prompt), Tools: tools, Timeout: d.Timeout, Lens: l}, nil
}
