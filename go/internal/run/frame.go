package run

import (
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// The execute frame. A NOW execute request handed bare to an agentic
// backend is answered as whatever that backend is by default (the claude
// CLI introduces itself as a coding assistant and declines a question
// about gas stations as out of scope). The frame is the process's own
// instruction — carry out this goal, with tools, and hand back the result
// — prefixed to the goal the way a lens is prefixed to a judgement, and
// bound the same way: the attempt config records the frame text's content
// ref and the fold re-derives every NOW execute request as
// frame + goal + rendering. It is NOT a persona: it says nothing about
// how to judge, and a lens never touches it (§13: a lens is over
// judgement and rendering, never over what the work is).
const DefaultFrame = `You are carrying out a goal on behalf of its owner, who is not present to answer questions. Do what the goal asks, using the tools you have whenever it needs the world, the web, or the file system; a goal is never out of your scope. Reply with the result itself, stated plainly, and say what you could not do.`

// frame is the driver's execute frame: nil (and no text) when the driver
// has none; otherwise the content ref of the text, stored once.
func (d *Driver) frame() (*thought.Ref, []byte, error) {
	if d.Frame == "" {
		return nil, nil, nil
	}
	ref, err := d.Store.Put(thought.FrameText, []byte(d.Frame))
	if err != nil {
		return nil, nil, err
	}
	return &ref, []byte(d.Frame), nil
}

// frameText reads the frame bound in an attempt's config (empty for none).
func frameText(a *AttemptState, get func(thought.Ref) ([]byte, error)) ([]byte, error) {
	if a == nil || a.Attempt.Config.Frame == nil {
		return nil, nil
	}
	return get(*a.Attempt.Config.Frame)
}

// FrameOf is the frame text an attempt ran under, as a string ("" for
// none): what a faithful replay of that attempt must use.
func FrameOf(a *AttemptState, store *thought.Store) (string, error) {
	b, err := frameText(a, store.Get)
	return string(b), err
}
