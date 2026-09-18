package run

import (
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
)

// The executor policy over an attempt (invoke/executor.go holds the lane
// itself). Three rules, and they are all the fold's:
//
//   - a tool-bearing call under `require` ran in a container and SAYS SO.
//     A host call, or a call that names no executor at all, is refused: a
//     required isolation with nothing in the record to show it is the lie
//     the policy exists to catch (Python main, 2026-09-12: the degrade was
//     silent and the secrets store was decrypted on the host).
//   - a tool-bearing call under `off` ran on the host. A container call is
//     a call this engine would not have made.
//   - a tool-bearing call under `on` SAYS where it ran. `on` permits both
//     lanes, but it is still an explicit request for isolation, and an
//     invocation that names no venue is the silent degrade this lane
//     exists to refuse. (No journal older than this field can carry `on`:
//     the policy and the field were born together.)
//
// The third rule — a tool-less call (a judge, an intent read, the
// landscape) runs on the host whatever the policy says — is not here: it
// holds for every invocation regardless of any attempt's policy, so it is
// enforced at the wire door (invoke.Invocation.ValidateWire). The landscape
// call is attempt 0 and reaches no attempt's checks at all.
//
// The policy is per ATTEMPT, not per run: an operator who turns isolation
// on (or fixes the image and turns `require` back on) between attempts is
// making a decision about the next attempt, and each attempt is held to
// the policy its own config records. That is the difference between this
// and the work dir, which is a fact about where the run's files ARE and so
// cannot change under it (work.go).
//
// `on` permits both lanes: it is the setting that says "isolate when you
// can". The degrade is never invisible even so — the invocation records
// the host, and ExecutorOf derives the degrade for the run's surface, from
// the record, without a second bookkeeping trail to keep honest.

// executorPolicy is the policy an attempt ran under; ABSENT = off, which
// is what every journal before the field recorded.
func executorPolicy(a *AttemptState) invoke.ExecutorPolicy {
	if a == nil || a.Attempt.Config.Executor == "" {
		return invoke.ExecutorOff
	}
	return a.Attempt.Config.Executor
}

// checkExecutor executes the policy over one invocation as it attaches.
func checkExecutor(rs *RunState, a *AttemptState, is *invoke.State) error {
	inv := is.Invocation
	pol := executorPolicy(a)
	ex := inv.Executor
	if !inv.Tools {
		return nil // the door holds every tool-less call to the host
	}
	switch pol {
	case invoke.ExecutorRequire:
		if ex == nil {
			return fmt.Errorf("run: %s attempt %d requires a container but invocation %s does not say where it ran", rs.Run, a.Attempt.Attempt, inv.ID)
		}
		if ex.Kind != invoke.ExecutorContainer {
			return fmt.Errorf("run: %s attempt %d requires a container but invocation %s ran on the %s", rs.Run, a.Attempt.Attempt, inv.ID, ex.Kind)
		}
	case invoke.ExecutorOn:
		if ex == nil {
			return fmt.Errorf("run: %s attempt %d asks for a container when one can run, but invocation %s does not say where it ran", rs.Run, a.Attempt.Attempt, inv.ID)
		}
	case invoke.ExecutorOff:
		if ex != nil && ex.Kind == invoke.ExecutorContainer {
			return fmt.Errorf("run: %s attempt %d runs on the host but invocation %s ran in container image %s", rs.Run, a.Attempt.Attempt, inv.ID, ex.Image)
		}
	}
	return nil
}

// ExecutorView is where an attempt's tool-bearing calls ran, for the run's
// surface — all derived from the record, never from a second trail.
type ExecutorView struct {
	// Policy is what the attempt's config asked for.
	Policy invoke.ExecutorPolicy
	// Kind is where the calls ran; EMPTY when no tool-bearing call has said
	// where it ran yet. A view that reported the policy's lane instead
	// would claim "executes in container" for a `require` attempt that
	// never got as far as a call (review r1).
	Kind invoke.ExecutorKind
	// Image is the last container image the attempt used, and Images every
	// distinct one in order: more than one means the attempt moved between
	// images (a resume under a different `--executor-image`), which is a
	// fact about the attempt, not something to hide behind the first.
	Image  string
	Images []string
	// Degraded: the attempt asked for a container when one could run, and a
	// call ran on the host instead.
	Degraded bool
}

// ExecutorViewOf derives the view from the attempt's own invocations.
func ExecutorViewOf(a *AttemptState) ExecutorView {
	v := ExecutorView{Policy: executorPolicy(a)}
	if a == nil {
		return v
	}
	host := false
	seen := map[string]bool{}
	for _, is := range a.Invocations {
		if !is.Invocation.Tools || is.Invocation.Executor == nil {
			continue
		}
		ex := is.Invocation.Executor
		if ex.Kind == invoke.ExecutorContainer {
			v.Kind, v.Image = invoke.ExecutorContainer, ex.Image
			// every DISTINCT image, in first-appearance order: an attempt
			// that moved A → B → A used two images, not three, and the
			// adjacent-duplicate check counted the return trip (review r2)
			if !seen[ex.Image] {
				seen[ex.Image] = true
				v.Images = append(v.Images, ex.Image)
			}
			continue
		}
		host = true
		v.Kind = ex.Kind
	}
	if host {
		// a run that degraded mid-attempt: the weaker lane is the honest
		// headline, and Degraded says the rest
		v.Kind, v.Image = invoke.ExecutorHost, ""
	}
	v.Degraded = v.Policy == invoke.ExecutorOn && v.Kind == invoke.ExecutorHost
	return v
}

// ExecutorLine renders the view for an operator ("" when there is nothing
// worth saying: the host lane nobody asked to change).
func ExecutorLine(a *AttemptState) string {
	v := ExecutorViewOf(a)
	line := ""
	switch {
	case v.Kind == invoke.ExecutorContainer:
		line = "executes in container " + v.Image
		// the others are the distinct images that are not the current one:
		// slicing off the last entry named the CURRENT image as an "other"
		// whenever the attempt came back to an image it had used before
		// (review r2)
		var others []string
		for _, im := range v.Images {
			if im != v.Image {
				others = append(others, im)
			}
		}
		if len(others) > 0 {
			line += fmt.Sprintf(" (and %d other image(s) earlier in this attempt: %s)", len(others), strings.Join(others, ", "))
		}
	case v.Degraded:
		line = "executes on the host (degraded: the container could not run)"
	case v.Kind == "":
		// nothing has run yet: say what was asked for, not what happened
		switch v.Policy {
		case invoke.ExecutorRequire:
			line = "requires a container; no tool call has run yet"
		case invoke.ExecutorOn:
			line = "isolates when it can; no tool call has run yet"
		}
	case v.Policy == invoke.ExecutorOff:
		line = ""
	default:
		line = "executes on the host"
	}
	return line
}

// rerunWorld: where a recorded obligation may be re-run. An obligation is
// re-run WHERE IT WAS RECORDED or not at all — a command that passed
// inside a container and is re-run on the host is a different experiment
// wearing the same name, and a green from it would retire an obligation
// nothing verified. "" = the host rerun is faithful; otherwise the reason
// the probe could not run (regression.Inconclusive), for the record.
//
// Re-running INSIDE the recorded container is owed (it needs the
// container's own kill path, so a timed-out probe cannot strand one).
func rerunWorld(inv *invoke.Invocation) string {
	if inv == nil || inv.Executor == nil || inv.Executor.Kind != invoke.ExecutorContainer {
		return ""
	}
	return fmt.Sprintf("recorded inside container %s; this engine can only re-run it on the host, which would not be the same probe", inv.Executor.Image)
}
