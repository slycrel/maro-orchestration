package run

import (
	"errors"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/regression"
)

const testID = "sha256:bebebebebebebebebebebebebebebebebebebebebebebebebebebebebebebebe"

const testImage = "maro-executor:2.1.210-r3"

// containerBackend is a scripted backend that runs under `pol` and whose
// tool-bearing calls report a container: the attempt's policy comes from the
// BACKEND now (invoke.Isolated), so a test configures the run by configuring
// the one thing that enforces it.
func containerBackend(pol invoke.ExecutorPolicy, caps invoke.Capabilities, calls ...invoke.ScriptedCall) *invoke.Scripted {
	b := scripted(caps, calls...)
	b.Exec = &invoke.Executor{Kind: invoke.ExecutorContainer, Image: testImage, Digest: testID, Network: "bridge"}
	b.Isolation = pol
	return b
}

// hostBackend runs under `pol` and says its tool-bearing calls ran on the
// host — the degrade, and the forgery under `require`.
func hostBackend(pol invoke.ExecutorPolicy, caps invoke.Capabilities, calls ...invoke.ScriptedCall) *invoke.Scripted {
	b := scripted(caps, calls...)
	b.Exec = &invoke.Executor{Kind: invoke.ExecutorHost}
	b.Isolation = pol
	return b
}

// A run under `require` isolates its tool-bearing call and the record says
// so; the fold then holds every call of the attempt to the policy the
// attempt recorded, so a host call under `require` — the silent degrade
// that decrypted a secrets store on the host in the Python engine — is
// refused as a lie whatever else the record says.
func TestExecutorPolicyOverTheFold(t *testing.T) {
	t.Run("a required container run folds, and says where it ran", func(t *testing.T) {
		h := open(t)
		exec := containerBackend(invoke.ExecutorRequire, outward, okCall)
		d := h.driver(exec, nil)
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		a := h.only().Latest()
		if got := a.Attempt.Config.Executor; got != invoke.ExecutorRequire {
			t.Fatalf("the attempt recorded policy %q", got)
		}
		ex := a.Invocations[0].Invocation.Executor
		if ex == nil || ex.Kind != invoke.ExecutorContainer || ex.Image != testImage {
			t.Fatalf("the call recorded %+v", ex)
		}
		if ex.Digest != testID || ex.Network != "bridge" {
			t.Fatalf("the call recorded no digest or network: %+v", ex)
		}
		v := ExecutorViewOf(a)
		if v.Kind != invoke.ExecutorContainer || v.Image != testImage || v.Degraded {
			t.Fatalf("surface: %+v", v)
		}
		if got := ExecutorLine(a); got != "executes in container "+testImage {
			t.Fatalf("line %q", got)
		}
		h.restart()
		if h.only().Latest().Has(Recorded) == nil {
			t.Fatal("did not fold again from disk")
		}
	})
	// the forgeries: the call's own record against the attempt's policy
	mk := func(t *testing.T, b *invoke.Scripted, mut func(*invoke.Invocation)) error {
		h := open(t)
		d := h.driver(b, nil)
		d.CrashAt = "after_execute"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		real := rs.Latest().Invocations[0]
		twin, recs := invocationTwin(t, rs, real, real.Invocation.Request)
		mut(twin.Invocation)
		return forge(t, h, "forge/ex", recs...)
	}
	for _, c := range []struct {
		name string
		b    func() *invoke.Scripted
		mut  func(*invoke.Invocation)
		want string
	}{
		{"require, a host call", func() *invoke.Scripted { return containerBackend(invoke.ExecutorRequire, outward, okCall) },
			func(i *invoke.Invocation) { i.Executor = &invoke.Executor{Kind: invoke.ExecutorHost} }, "ran on the host"},
		{"require, a call that says nothing", func() *invoke.Scripted { return containerBackend(invoke.ExecutorRequire, outward, okCall) },
			func(i *invoke.Invocation) { i.Executor = nil }, "does not say where it ran"},
		// `on` permits both lanes but is still an explicit ask for
		// isolation: a tool-bearing call that names NO venue is the silent
		// degrade this lane exists to refuse, so the fold refuses it too
		{"on, a call that says nothing", func() *invoke.Scripted { return containerBackend(invoke.ExecutorOn, outward, okCall) },
			func(i *invoke.Invocation) { i.Executor = nil }, "does not say where it ran"},
		{"on, a host call is the honest degrade", func() *invoke.Scripted { return hostBackend(invoke.ExecutorOn, outward, okCall) },
			func(i *invoke.Invocation) {}, ""},
		{"off, a container call", func() *invoke.Scripted { return scripted(outward, okCall) },
			func(i *invoke.Invocation) {
				i.Executor = &invoke.Executor{Kind: invoke.ExecutorContainer, Image: testImage}
			}, "ran in container image"},
		{"the honest twin", func() *invoke.Scripted { return containerBackend(invoke.ExecutorRequire, outward, okCall) },
			func(i *invoke.Invocation) {}, ""},
	} {
		err := mk(t, c.b(), c.mut)
		if c.want == "" {
			if err != nil {
				t.Fatalf("%s: refused: %v", c.name, err)
			}
			continue
		}
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: folded: %v (want %q)", c.name, err, c.want)
		}
	}
	// (A tool-less call in a container is refused at the WIRE DOOR, not by
	// an attempt's policy — the rule holds for every invocation this engine
	// reads, the landscape's included, which is attempt 0 and reaches no
	// attempt's checks at all. Its cases live beside the door:
	// internal/invoke:TestExecutorLaneIsRecordedBeforeDispatch.)
	t.Run("the door: a policy out of vocabulary", func(t *testing.T) {
		h := open(t)
		exec := containerBackend(invoke.ExecutorRequire, outward, okCall)
		d := h.driver(exec, nil)
		d.CrashAt = "after_execute"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		att := *h.only().Latest().Attempt
		att.Config.Executor = "sandbox"
		if err := att.ValidateWire(); err == nil || !strings.Contains(err.Error(), "out of vocabulary") {
			t.Fatalf("door: %v", err)
		}
	})
}

// `on` is the setting that says "isolate when you can", so the fold admits
// both lanes — and the degrade is still visible, derived from the record
// the calls left rather than from a second trail to keep honest.
func TestTheDegradeIsDerivedFromTheRecord(t *testing.T) {
	h := open(t)
	exec := hostBackend(invoke.ExecutorOn, outward, okCall) // the container could not run
	d := h.driver(exec, nil)
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if a.Attempt.Config.Executor != invoke.ExecutorOn {
		t.Fatalf("policy %q", a.Attempt.Config.Executor)
	}
	// a call that said host under `on` IS the degrade, and the surface says
	// so without a second trail to keep honest
	v := ExecutorViewOf(a)
	if v.Kind != invoke.ExecutorHost || v.Image != "" || !v.Degraded {
		t.Fatalf("surface: %+v", v)
	}
	if got := ExecutorLine(a); !strings.Contains(got, "degraded") {
		t.Fatalf("line %q", got)
	}
	// one container call and one host call in the same attempt: the weaker
	// lane is the headline
	a.Invocations[0].Invocation.Executor = &invoke.Executor{Kind: invoke.ExecutorContainer, Image: testImage}
	a.Invocations = append(a.Invocations, &invoke.State{Invocation: &invoke.Invocation{Purpose: invoke.PurposeExecute, Tools: true, Executor: &invoke.Executor{Kind: invoke.ExecutorHost}}})
	if v := ExecutorViewOf(a); v.Kind != invoke.ExecutorHost || !v.Degraded {
		t.Fatalf("mixed attempt: %+v", v)
	}
	// An attempt whose calls have not said where they ran claims NOTHING:
	// reporting the policy's own lane made a `require` attempt that never
	// got as far as a call read as "executes in container " (review r1).
	empty := &AttemptState{Attempt: &RunAttempt{Config: ConfigSnapshot{Executor: invoke.ExecutorRequire}}}
	if v := ExecutorViewOf(empty); v.Kind != "" || v.Image != "" || v.Degraded {
		t.Fatalf("an attempt with no calls: %+v", v)
	}
	if got := ExecutorLine(empty); !strings.Contains(got, "no tool call has run yet") {
		t.Fatalf("line %q", got)
	}
	// two container images in one attempt (a resume under a different
	// --executor-image) is a fact about the attempt, not something to hide
	// behind the first one
	moved := &AttemptState{Attempt: &RunAttempt{Config: ConfigSnapshot{Executor: invoke.ExecutorRequire}}, Invocations: []*invoke.State{
		{Invocation: &invoke.Invocation{Purpose: invoke.PurposeExecute, Tools: true, Executor: &invoke.Executor{Kind: invoke.ExecutorContainer, Image: "maro-executor:old"}}},
		{Invocation: &invoke.Invocation{Purpose: invoke.PurposeExecute, Tools: true, Executor: &invoke.Executor{Kind: invoke.ExecutorContainer, Image: testImage}}},
	}}
	v = ExecutorViewOf(moved)
	if v.Image != testImage || len(v.Images) != 2 {
		t.Fatalf("moved attempt: %+v", v)
	}
	if got := ExecutorLine(moved); !strings.Contains(got, "maro-executor:old") {
		t.Fatalf("line %q hides the earlier image", got)
	}
	// An attempt that went A → B → A used TWO images, not three, and the
	// current one is never one of the "others": the adjacent-duplicate check
	// counted the return trip and then named the current image as an earlier
	// one (review r2).
	inv := func(image string) *invoke.State {
		return &invoke.State{Invocation: &invoke.Invocation{Purpose: invoke.PurposeExecute, Tools: true, Executor: &invoke.Executor{Kind: invoke.ExecutorContainer, Image: image}}}
	}
	back := &AttemptState{Attempt: &RunAttempt{Config: ConfigSnapshot{Executor: invoke.ExecutorRequire}}, Invocations: []*invoke.State{
		inv("maro-executor:a"), inv("maro-executor:b"), inv("maro-executor:a"),
	}}
	v = ExecutorViewOf(back)
	if v.Image != "maro-executor:a" || len(v.Images) != 2 {
		t.Fatalf("A→B→A: %+v", v)
	}
	line := ExecutorLine(back)
	if !strings.Contains(line, "1 other image(s) earlier in this attempt: maro-executor:b") {
		t.Fatalf("line %q", line)
	}
	if strings.Count(line, "maro-executor:a") != 1 {
		t.Fatalf("line %q names the current image as an earlier one", line)
	}
	// and one image used twice in a row is still ONE image
	same := &AttemptState{Attempt: &RunAttempt{Config: ConfigSnapshot{Executor: invoke.ExecutorRequire}}, Invocations: []*invoke.State{inv(testImage), inv(testImage)}}
	if v := ExecutorViewOf(same); len(v.Images) != 1 {
		t.Fatalf("one image twice: %+v", v)
	}
	if got := ExecutorLine(same); strings.Contains(got, "other image") {
		t.Fatalf("line %q", got)
	}
}

// An obligation is re-run WHERE IT WAS RECORDED or not at all: a probe that
// passed inside a container and is re-run on the host is a different
// experiment wearing the same name, so the engine records it inconclusive
// with the reason instead of retiring the obligation on a host green.
func TestAnObligationIsRerunWhereItWasRecorded(t *testing.T) {
	host := &invoke.Invocation{Purpose: invoke.PurposeExecute, Tools: true, Executor: &invoke.Executor{Kind: invoke.ExecutorHost}}
	if why := rerunWorld(host); why != "" {
		t.Fatalf("a host obligation was refused: %q", why)
	}
	if why := rerunWorld(&invoke.Invocation{Purpose: invoke.PurposeExecute, Tools: true}); why != "" {
		t.Fatalf("an obligation that says nothing was refused: %q", why)
	}
	in := &invoke.Invocation{Purpose: invoke.PurposeExecute, Tools: true, Executor: &invoke.Executor{Kind: invoke.ExecutorContainer, Image: testImage}}
	why := rerunWorld(in)
	if why == "" || !strings.Contains(why, testImage) || !strings.Contains(why, "not be the same probe") {
		t.Fatalf("container obligation: %q", why)
	}
	// and it lands as the honest class, not as a pass
	res := &regression.Result{Exit: -1, Outcome: regression.Inconclusive, Why: why}
	if res.Outcome != regression.Inconclusive {
		t.Fatalf("outcome %q", res.Outcome)
	}
}
