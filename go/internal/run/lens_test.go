package run

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// §13: a lens is a prefix over judgement, never over the work. Two NOW runs
// of the same goal with the same response, one neutral and one under the
// skeptic lens: the execute requests hash identically; the judge requests
// differ by exactly the lens text; the invocation carries the lens by name
// and content-addressed text; the fold re-derives the lensed verdict. A
// lens on an execute request, a lens whose text is not a lens_text
// thought, and an unknown lens name are refused before anything is written.
func TestLensSwapOnTheSameFacts(t *testing.T) {
	h := open(t)
	h.policy(t, learn.MechModelJudge, true, learn.Provisional)
	exec := scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")}, invoke.ScriptedCall{Response: []byte("Paris.")})
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureYes)}, {Response: []byte(closureYes)}}}
	goal := []byte("What is the capital of France?")

	d1 := h.driver(exec, nil)
	d1.Judge, d1.ModelJudge = judge, true
	if _, err := d1.Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	d2 := h.driver(exec, nil)
	d2.Judge, d2.ModelJudge, d2.Lens = judge, true, LensSkeptic
	if _, err := d2.Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	led := h.ledger()
	if len(led.Runs) != 2 {
		t.Fatalf("runs: %d", len(led.Runs))
	}
	var neutral, skeptic *AttemptState
	for _, rs := range led.Runs {
		a := rs.Latest()
		if a.Attempt.Config.Lens == "" {
			neutral = a
		} else {
			skeptic = a
		}
	}
	if neutral == nil || skeptic == nil || skeptic.Attempt.Config.Lens != LensSkeptic {
		t.Fatalf("configs: neutral %v skeptic %v", neutral != nil, skeptic != nil)
	}
	pick := func(a *AttemptState, p invoke.Purpose) *invoke.Invocation {
		for _, is := range a.Invocations {
			if is.Invocation.Purpose == p {
				return is.Invocation
			}
		}
		t.Fatalf("no %s invocation", p)
		return nil
	}
	// the work is untouched: same execute request under both lenses
	if pick(neutral, invoke.PurposeExecute).Request != pick(skeptic, invoke.PurposeExecute).Request {
		t.Fatal("the lens changed the execute request")
	}
	nj, sj := pick(neutral, invoke.PurposeJudge), pick(skeptic, invoke.PurposeJudge)
	if nj.Lens != nil || sj.Lens == nil || sj.Lens.Name != LensSkeptic || sj.Lens.Text.Kind != thought.LensText || nj.Request == sj.Request {
		t.Fatalf("judge invocations: neutral %+v skeptic %+v", nj.Lens, sj.Lens)
	}
	nb, err := h.st.Get(nj.Request)
	if err != nil {
		t.Fatal(err)
	}
	sb, err := h.st.Get(sj.Request)
	if err != nil {
		t.Fatal(err)
	}
	lt, err := h.st.Get(sj.Lens.Text)
	if err != nil {
		t.Fatal(err)
	}
	if want, _ := LensText(LensSkeptic); string(lt) != want || string(sb) != string(Lensed(lt, nb)) {
		t.Fatalf("the skeptic judge request is not the lens over the neutral one:\n%q\n%q", sb, nb)
	}
	if string(Lensed(nil, nb)) != string(nb) {
		t.Fatal("the neutral lens is a prefix")
	}
	// both closures were judged and re-derived by the fold under their lens
	for _, a := range []*AttemptState{neutral, skeptic} {
		if a.Has(Recorded).Outcome.ClosureOut != "achieved" || a.Has(Recorded).Outcome.ClosureSrc != "judge" {
			t.Fatalf("closure: %+v", a.Has(Recorded).Outcome)
		}
	}
	// the operator surface says which lens decided
	var lines []string
	for _, rs := range led.Runs {
		if rs.Latest() == skeptic {
			lines = Inspect(rs)
		}
	}
	joined := strings.Join(lines, "\n")
	if !strings.Contains(joined, "lens: skeptic") || !strings.Contains(joined, "lens=skeptic") || !strings.Contains(joined, "request="+sj.Request.Hash) {
		t.Fatalf("inspect:\n%s", joined)
	}
	if names := LensNames(); len(names) != 2 || names[0] != LensNeutral || names[1] != LensSkeptic {
		t.Fatalf("lenses: %v", names)
	}

	// refusals: nothing written
	head := h.j.Head()
	d3 := h.driver(exec, nil)
	d3.Lens = "poet"
	if _, err := d3.Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrConfig) || !strings.Contains(err.Error(), "unknown lens") {
		t.Fatalf("unknown lens: %v", err)
	}
	if h.j.Head() != head {
		t.Fatal("an unknown lens wrote records")
	}
	sh := &invoke.Shell{J: h.j, Store: h.st, Run: record.RunID(record.NewID()), Attempt: 1}
	lensRef := thought.Address(thought.LensText, []byte(skepticLens))
	if _, err := sh.Invoke(ctxBg, exec, invoke.Request{Purpose: invoke.PurposeExecute, Prompt: []byte("x"), Lens: &invoke.Lens{Name: LensSkeptic, Text: lensRef}}, nil); err == nil || !strings.Contains(err.Error(), "cannot carry a lens") {
		t.Fatalf("lens on execute: %v", err)
	}
	if _, err := sh.Invoke(ctxBg, exec, invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("x"), Lens: &invoke.Lens{Name: LensSkeptic, Text: nj.Request}}, nil); err == nil || !strings.Contains(err.Error(), "not lens_text") {
		t.Fatalf("lens over a prompt thought: %v", err)
	}
	if _, err := sh.Invoke(ctxBg, exec, invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("x"), Lens: &invoke.Lens{Name: "", Text: lensRef}}, nil); err == nil || !strings.Contains(err.Error(), "without a name") {
		t.Fatalf("nameless lens: %v", err)
	}
	if h.j.Head() != head {
		t.Fatal("a refused lens wrote records")
	}
	// the door: the same shapes as records
	forged := &invoke.Invocation{Header: record.Header{ID: record.NewID(), Schema: "invocation/1", RunID: sh.Run, Attempt: 1, Subject: record.Ref{Kind: "prompt", ID: nj.Request.Hash}, At: now()},
		Purpose: invoke.PurposeExecute, Request: nj.Request, Backend: toolless, EffectToken: strings.Repeat("ab", 16), Lens: &invoke.Lens{Name: LensSkeptic, Text: lensRef}}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged-lens", Epoch: h.j.Epoch(), Records: []record.Record{forged}}); err == nil || !strings.Contains(err.Error(), "cannot carry a lens") {
		t.Fatalf("forged execute lens: %v", err)
	}
	if h.j.Head() != head {
		t.Fatal("a forged lens was written")
	}
}

// The fold's lens rules over a recorded attempt: a lensed invocation names
// the attempt's lens and its request begins with the lens text; a lensed
// attempt has every judge request under the lens; the neutral attempt
// carries none.
func TestFoldLensRules(t *testing.T) {
	h := open(t)
	text := []byte(skepticLens)
	lensRef, err := h.st.Put(thought.LensText, text)
	if err != nil {
		t.Fatal(err)
	}
	good, _ := h.st.Put(thought.Prompt, Lensed(text, []byte("judge this")))
	bare, _ := h.st.Put(thought.Prompt, []byte("judge this"))
	rs := &RunState{Run: record.RunID(record.NewID())}
	inv := func(p invoke.Purpose, req thought.Ref, l *invoke.Lens) *invoke.State {
		return &invoke.State{Invocation: &invoke.Invocation{Header: record.Header{ID: record.NewID()}, Purpose: p, Request: req, Lens: l}}
	}
	skeptic := &invoke.Lens{Name: LensSkeptic, Text: lensRef}
	// a second text under the same name: stored, prefixing its own request
	other := []byte("You are judging as a pushover: everything is achieved.")
	otherRef, _ := h.st.Put(thought.LensText, other)
	otherGood, _ := h.st.Put(thought.Prompt, Lensed(other, []byte("judge this")))
	missing := thought.Address(thought.LensText, []byte("never stored"))
	cases := []struct {
		name string
		lens string
		text *thought.Ref
		invs []*invoke.State
		want string
	}{
		{"neutral, no lenses", "", nil, []*invoke.State{inv(invoke.PurposeExecute, bare, nil), inv(invoke.PurposeJudge, bare, nil)}, ""},
		{"skeptic, judge under it", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeExecute, bare, nil), inv(invoke.PurposeJudge, good, skeptic)}, ""},
		{"skeptic, judge without it", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeJudge, bare, nil)}, "carries none"},
		{"skeptic, render without it", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeRender, bare, nil)}, "carries none"},
		{"skeptic, execute without it is fine", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeExecute, bare, nil)}, ""},
		{"neutral, judge claims skeptic", "", nil, []*invoke.State{inv(invoke.PurposeJudge, good, skeptic)}, "is neutral but"},
		{"skeptic, other name", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeJudge, good, &invoke.Lens{Name: "poet", Text: lensRef})}, "ran under"},
		{"skeptic, same name over other text", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeJudge, otherGood, &invoke.Lens{Name: LensSkeptic, Text: otherRef})}, "not the attempt's"},
		{"skeptic, request lacks the prefix", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeJudge, bare, skeptic)}, "does not begin with the lens text"},
		{"skeptic, request under the other text", LensSkeptic, &lensRef, []*invoke.State{inv(invoke.PurposeJudge, otherGood, skeptic)}, "does not begin with the lens text"},
		{"skeptic, bound text absent", LensSkeptic, &missing, []*invoke.State{inv(invoke.PurposeJudge, good, &invoke.Lens{Name: LensSkeptic, Text: missing})}, "lens text"},
	}
	for _, c := range cases {
		a := &AttemptState{Attempt: &RunAttempt{Config: ConfigSnapshot{Lens: c.lens, LensText: c.text}}, Invocations: c.invs}
		err := checkLenses(rs, a, h.st)
		if (c.want == "") != (err == nil) || (err != nil && !strings.Contains(err.Error(), c.want)) {
			t.Fatalf("%s: want %q, got %v", c.name, c.want, err)
		}
	}
}

// The fold executes the lens binding over history, not only the driver
// over its own requests: after a real run under the skeptic lens, a forged
// judge invocation that carries no lens, one that carries another text
// under the same name, and an unlensed render request are each refused by
// Fold (the record passes the door: it is well-formed); an attempt record
// that names a lens without binding its text is refused at the door.
func TestFoldRefusesLensSwapsInHistory(t *testing.T) {
	type forge func(h *harness, rs *RunState, req thought.Ref) record.Record
	lensed := func(t *testing.T) (*harness, *RunState, thought.Ref) {
		t.Helper()
		h := open(t)
		h.policy(t, learn.MechModelJudge, true, learn.Provisional)
		exec := scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")})
		judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureYes)}}}
		d := h.driver(exec, nil)
		d.Judge, d.ModelJudge, d.Lens = judge, true, LensSkeptic
		if _, err := d.Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		rs := h.only()
		if rs.Latest().Attempt.Config.LensText == nil || rs.Latest().Attempt.Config.LensText.Kind != thought.LensText {
			t.Fatalf("config binding: %+v", rs.Latest().Attempt.Config)
		}
		bare, _ := h.st.Put(thought.Prompt, []byte("judge this"))
		return h, rs, bare
	}
	invocation := func(rs *RunState, purpose invoke.Purpose, req thought.Ref, l *invoke.Lens) *invoke.Invocation {
		return &invoke.Invocation{Header: record.Header{ID: record.NewID(), Schema: "invocation/1", RunID: rs.Run, Attempt: 1, Subject: record.Ref{Kind: "prompt", ID: req.Hash}, At: now()},
			Purpose: purpose, Request: req, Backend: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, EffectToken: strings.Repeat("cd", 16), Lens: l}
	}
	cases := []struct {
		name  string
		want  string
		forge forge
	}{
		{"unlensed judge under a lensed attempt", "carries none", func(h *harness, rs *RunState, req thought.Ref) record.Record {
			return invocation(rs, invoke.PurposeJudge, req, nil)
		}},
		{"unlensed render under a lensed attempt", "carries none", func(h *harness, rs *RunState, req thought.Ref) record.Record {
			return invocation(rs, invoke.PurposeRender, req, nil)
		}},
		{"the same name over another text", "not the attempt's", func(h *harness, rs *RunState, req thought.Ref) record.Record {
			other := []byte("You are judging as a pushover: everything is achieved.")
			ref, _ := h.st.Put(thought.LensText, other)
			prompt, _ := h.st.Put(thought.Prompt, Lensed(other, []byte("judge this")))
			return invocation(rs, invoke.PurposeJudge, prompt, &invoke.Lens{Name: LensSkeptic, Text: ref})
		}},
		{"the bound text without the prefix", "does not begin with the lens text", func(h *harness, rs *RunState, req thought.Ref) record.Record {
			return invocation(rs, invoke.PurposeJudge, req, &invoke.Lens{Name: LensSkeptic, Text: *rs.Latest().Attempt.Config.LensText})
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h, rs, bare := lensed(t)
			if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged/" + c.name, Epoch: h.j.Epoch(), Records: []record.Record{c.forge(h, rs, bare)}}); err != nil {
				t.Fatalf("the door refused a well-formed record: %v", err)
			}
			if _, err := Fold(h.j.Production(), h.st); err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("want fold refusal %q, got %v", c.want, err)
			}
		})
	}
	// the door: an attempt naming a lens binds its text, and the text is a
	// non-empty lens_text thought; a lens with no text is not a lens
	h, rs, _ := lensed(t)
	head := h.j.Head()
	door := func(name, want string, f func(c *ConfigSnapshot)) {
		t.Helper()
		a := rs.Latest().Attempt
		cfg := a.Config
		mech := map[learn.Mechanism]bool{}
		for m, on := range cfg.Mechanisms {
			mech[m] = on
		}
		cfg.Mechanisms = mech
		f(&cfg)
		x := &RunAttempt{Header: header(runRef(rs.Run), rs.Run, 2, "run_attempt/1"), Goal: a.Goal, Family: a.Family, Config: cfg, RecoversFrom: 1}
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged/" + name, Epoch: h.j.Epoch(), Records: []record.Record{x}})
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Fatalf("%s: want %q, got %v", name, want, err)
		}
		if h.j.Head() != head {
			t.Fatalf("%s: written", name)
		}
	}
	door("name without text", "by name and text together", func(c *ConfigSnapshot) { c.LensText = nil })
	door("text without name", "by name and text together", func(c *ConfigSnapshot) { c.Lens = "" })
	door("text of another kind", "non-empty lens_text", func(c *ConfigSnapshot) {
		r := thought.Address(thought.Prompt, []byte("x"))
		r.Bytes = 1
		c.LensText = &r
	})
	door("empty text", "non-empty lens_text", func(c *ConfigSnapshot) {
		r := thought.Address(thought.LensText, nil)
		c.LensText = &r
	})
	empty := thought.Address(thought.LensText, nil)
	sh := &invoke.Shell{J: h.j, Store: h.st, Run: record.RunID(record.NewID()), Attempt: 1}
	if _, err := sh.Invoke(ctxBg, scripted(toolless), invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("x"), Lens: &invoke.Lens{Name: LensSkeptic, Text: empty}}, nil); err == nil || !strings.Contains(err.Error(), "no text") {
		t.Fatalf("empty lens text: %v", err)
	}
	if _, err := sh.Invoke(ctxBg, scripted(toolless), invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("x"), Lens: &invoke.Lens{Name: "Dr. Skeptic", Text: *rs.Latest().Attempt.Config.LensText}}, nil); err == nil || !strings.Contains(err.Error(), "not a name") {
		t.Fatalf("lens name: %v", err)
	}
	if h.j.Head() != head {
		t.Fatal("a refused lens wrote records")
	}
}

// The workspace's working directory: every tool-bearing execute starts in
// the driver's Work (created on first use, absolute), recorded on the
// invocation and shown by Inspect; a tool-less request (the judge) has
// none; a confined fork child has none. The process's own cwd is never
// what an execute inherits.
func TestExecutesRunInTheWorkspaceWorkDir(t *testing.T) {
	h := open(t)
	h.policy(t, learn.MechModelJudge, true, learn.Provisional)
	exec := scripted(outward, invoke.ScriptedCall{Response: []byte("Paris.")})
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureYes)}}}
	work := filepath.Join(t.TempDir(), "ws", "work") // does not exist yet
	d := h.driver(exec, nil)
	d.Judge, d.ModelJudge, d.Work = judge, true, work
	if _, err := d.Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if st, err := os.Stat(work); err != nil || !st.IsDir() {
		t.Fatalf("work dir not created: %v", err)
	}
	if len(exec.Seen) != 1 || exec.Seen[0].Cwd != work || !exec.Seen[0].Tools {
		t.Fatalf("execute request: %+v", exec.Seen)
	}
	if len(judge.Seen) != 1 || judge.Seen[0].Cwd != "" || judge.Seen[0].Tools {
		t.Fatalf("judge request: %+v", judge.Seen)
	}
	rs := h.only()
	var ex, jd *invoke.Invocation
	for _, is := range rs.Latest().Invocations {
		switch is.Invocation.Purpose {
		case invoke.PurposeExecute:
			ex = is.Invocation
		case invoke.PurposeJudge:
			jd = is.Invocation
		}
	}
	if ex == nil || ex.Cwd != work || jd == nil || jd.Cwd != "" {
		t.Fatalf("recorded: execute %+v judge %+v", ex, jd)
	}
	if lines := strings.Join(Inspect(rs), "\n"); !strings.Contains(lines, "cwd="+work) {
		t.Fatalf("inspect:\n%s", lines)
	}
	// no Work configured: the backend's default, nothing recorded
	h2 := open(t)
	exec2 := scripted(outward, invoke.ScriptedCall{Response: []byte("Paris.")})
	if _, err := h2.driver(exec2, nil).Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if exec2.Seen[0].Cwd != "" {
		t.Fatalf("default: %+v", exec2.Seen[0])
	}
	// a relative Work is made absolute before it is recorded (the door
	// refuses a relative cwd)
	h3 := open(t)
	exec3 := scripted(outward, invoke.ScriptedCall{Response: []byte("Paris.")})
	d3 := h3.driver(exec3, nil)
	d3.Work = "relative-work"
	if _, err := d3.Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if !filepath.IsAbs(exec3.Seen[0].Cwd) || filepath.Base(exec3.Seen[0].Cwd) != "relative-work" {
		t.Fatalf("relative work: %q", exec3.Seen[0].Cwd)
	}
	os.RemoveAll("relative-work")
}

// The execute frame: a NOW execute request is frame + goal + rendering,
// the frame bound in the attempt config as a frame_text ref and re-derived
// by the fold; a lens never touches it; a forged unframed request under a
// framed attempt is refused by Fold; a config binding a frame of another
// kind is refused at the door; Inspect shows the binding.
func TestExecuteFrameIsBoundAndReDerived(t *testing.T) {
	h := open(t)
	h.lesson(t, "cite the file", learn.Provisional)
	exec := scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")})
	goal := []byte("What is the capital of France?")
	d := h.driver(exec, nil)
	d.Frame = DefaultFrame
	if _, err := d.Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs := h.only()
	a := rs.Latest()
	if a.Attempt.Config.Frame == nil || a.Attempt.Config.Frame.Kind != thought.FrameText {
		t.Fatalf("config: %+v", a.Attempt.Config.Frame)
	}
	req, err := h.st.Get(a.Invocations[0].Invocation.Request)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(req), DefaultFrame+"\n\n"+string(goal)) || !strings.Contains(string(req), "cite the file") {
		t.Fatalf("request:\n%s", req)
	}
	if ft, _ := FrameOf(a, h.st); ft != DefaultFrame {
		t.Fatalf("FrameOf: %q", ft)
	}
	if lines := strings.Join(Inspect(rs), "\n"); !strings.Contains(lines, "frame: "+a.Attempt.Config.Frame.Hash) {
		t.Fatalf("inspect:\n%s", lines)
	}
	// no frame: the bare goal, and Inspect says so
	h2 := open(t)
	exec2 := scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")})
	if _, err := h2.driver(exec2, nil).Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs2 := h2.only()
	if rs2.Latest().Attempt.Config.Frame != nil || !strings.Contains(strings.Join(Inspect(rs2), "\n"), "frame: none") {
		t.Fatal("bare goal run carries a frame")
	}
	if string(exec2.Seen[0].Prompt) != string(goal) {
		t.Fatalf("bare request: %q", exec2.Seen[0].Prompt)
	}

	// the fold's rule, executed directly on the framed run's own facts: the
	// real invocation re-derives; the same invocation claiming the bare
	// goal+rendering (the pre-frame shape) is refused — a frame dropped
	// from the re-derivation would let it through
	led := h.ledger()
	real := a.Invocations[0].Invocation
	if err := checkExposure(rs, a.Recall, h.st.Get, led.Learned, h.st.Get, real.ID, real, 1, false); err != nil {
		t.Fatalf("honest exposure refused: %v", err)
	}
	bareBody := append(append([]byte{}, goal...), req[len(DefaultFrame)+2+len(goal):]...)
	bare, _ := h.st.Put(thought.Prompt, bareBody)
	forged := *real
	forged.Request = bare
	if err := checkExposure(rs, a.Recall, h.st.Get, led.Learned, h.st.Get, real.ID, &forged, 1, false); err == nil || !strings.Contains(err.Error(), "frame+goal+recall") {
		t.Fatalf("unframed request under a framed attempt: %v", err)
	}

	// the door: a frame of another kind, an empty frame
	head := h.j.Head()
	cfg := a.Attempt.Config
	for _, c := range []struct {
		name string
		ref  thought.Ref
	}{{"prompt kind", thought.Address(thought.Prompt, []byte("x"))}, {"empty", thought.Address(thought.FrameText, nil)}} {
		f := c.ref
		if c.name == "prompt kind" {
			f.Bytes = 1
		}
		cfg.Frame = &f
		x := &RunAttempt{Header: header(runRef(rs.Run), rs.Run, 2, "run_attempt/1"), Goal: a.Attempt.Goal, Family: a.Attempt.Family, Config: cfg, RecoversFrom: 1}
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged/frame-" + c.name, Epoch: h.j.Epoch(), Records: []record.Record{x}}); err == nil || !strings.Contains(err.Error(), "non-empty frame_text") {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	if h.j.Head() != head {
		t.Fatal("a refused frame was written")
	}
}

// recordForged records an outcome for an attempt that crashed after
// execute, citing the given invocation: the judged and recorded
// transitions a driver would write, submitted as forged records so the
// fold — not the driver — is what accepts or refuses the history.
func recordForged(t *testing.T, h *harness, rs *RunState, a *AttemptState, inv *invoke.State) error {
	t.Helper()
	res, err := verdict.Commit(ctxBg, h.j, rs.Run, 1, verdict.Candidates{Subject: runRef(rs.Run), VerdictKind: verdict.KindClosure, Verdicts: h.verdicts(t, rs.Run)}, verdict.DefaultThresholds)
	if err != nil {
		t.Fatal(err)
	}
	resp := inv.Receipt.Response
	o := &Outcome{Lane: LaneNow, Terminal: inv.Terminal.State, Invocation: inv.Invocation.ID, Produced: 1, Recall: a.Recall.ID, Receipt: inv.Receipt.ID, Response: &resp, Usage: inv.Receipt.Usage, Model: inv.Invocation.Backend.Model, GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: res.Outcome, ClosureCnf: res.Confidence}
	hd := func() record.Header {
		return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: runRef(rs.Run), At: time.Now().UTC()}
	}
	if err := forge(t, h, "judged", &Transition{Header: hd(), From: Executing, To: Judged}); err != nil {
		t.Fatalf("judged transition refused: %v", err)
	}
	return forge(t, h, "rec", &Transition{Header: hd(), From: Judged, To: Recorded, Outcome: o})
}

// invocationTwin is a wire-valid copy of a finished invocation under a new
// id: the invocation, its dispatch, terminal and receipt — the records a
// driver writes for one call — with the request (and so the subject)
// swapped for another. Everything else is the original's.
func invocationTwin(t *testing.T, rs *RunState, is *invoke.State, request thought.Ref) (*invoke.State, []record.Record) {
	t.Helper()
	f := *is.Invocation
	f.ID, f.Seq, f.Request = record.NewID(), 0, request
	f.Subject = record.Ref{Kind: "prompt", ID: request.Hash}
	tok, err := invoke.NewEffectToken()
	if err != nil {
		t.Fatal(err)
	}
	f.EffectToken = tok
	sub := record.Ref{Kind: "invocation", ID: string(f.ID)}
	disp := &invoke.Dispatched{Header: record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: sub, At: time.Now().UTC()}, Invocation: f.ID}
	tm := *is.Terminal
	tm.ID, tm.Seq, tm.Subject, tm.Invocation = record.NewID(), 0, sub, f.ID
	rc := *is.Receipt
	rc.ID, rc.Seq, rc.Subject, rc.Invocation = record.NewID(), 0, sub, f.ID
	return &invoke.State{Invocation: &f, Terminal: &tm, Receipt: &rc}, []record.Record{&f, disp, &tm, &rc}
}

// End to end through Fold and Resume: under a framed attempt, a recorded
// outcome that cites an execute whose request is the bare goal (the
// pre-frame shape) is refused by the exposure rule — the rule wired at
// Recorded, not the helper alone. The twin invocation folds on its own
// (an invocation is a fact; the outcome carries the exposure claim), and
// the honest one records.
func TestFoldRefusesUnframedExecuteUnderFramedAttempt(t *testing.T) {
	mk := func() (*harness, *RunState, *AttemptState) {
		h := open(t)
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")}), nil)
		d.Frame, d.CrashAt = DefaultFrame, "after_execute"
		if _, err := d.Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		return h, rs, rs.Latest()
	}
	h, rs, a := mk()
	if err := recordForged(t, h, rs, a, a.Invocations[0]); err != nil {
		t.Fatalf("honest framed history refused: %v", err)
	}
	h2, rs2, a2 := mk()
	real := a2.Invocations[0]
	req, err := h2.st.Get(real.Invocation.Request)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(string(req), DefaultFrame+"\n\n") {
		t.Fatalf("request not framed:\n%s", req)
	}
	bare, err := h2.st.Put(thought.Prompt, req[len(DefaultFrame)+2:])
	if err != nil {
		t.Fatal(err)
	}
	twin, recs := invocationTwin(t, rs2, real, bare)
	if err := forge(t, h2, "twin", recs...); err != nil {
		t.Fatalf("a bare invocation alone must fold: %v", err)
	}
	rs2 = h2.only()
	a2 = rs2.Latest()
	if len(a2.Invocations) != 2 || a2.Invocations[1].Invocation.ID != twin.Invocation.ID || a2.Invocations[1].Receipt == nil {
		t.Fatalf("twin not attached: %+v", a2.Invocations)
	}
	if err := recordForged(t, h2, rs2, a2, a2.Invocations[1]); err == nil || !strings.Contains(err.Error(), "frame+goal+recall") {
		t.Fatalf("unframed execute recorded under a framed attempt: %v", err)
	}
	d2 := h2.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")}), nil)
	d2.Frame = DefaultFrame
	if _, err := d2.Resume(ctxBg); err == nil || !strings.Contains(err.Error(), "frame+goal+recall") {
		t.Fatalf("resume papered over the unframed execute: %v", err)
	}
}

// An invocation's backend snapshot is bound to the attempt's config: a
// history whose execute claims a tool policy (or any capability) the
// attempt was not configured with is refused as it attaches; a judge call
// on the executor under a model-judge config likewise; and the door
// refuses an attempt whose snapshot carries a non-canonical policy.
func TestFoldRefusesBackendSwapInHistory(t *testing.T) {
	mk := func() (*harness, *RunState, *AttemptState, *invoke.State) {
		h := open(t)
		h.policy(t, learn.MechModelJudge, true, learn.Provisional)
		exec := scripted(outward, invoke.ScriptedCall{Response: []byte("Paris.")})
		judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureYes)}}}
		d := h.driver(exec, nil)
		d.Judge, d.ModelJudge, d.CrashAt = judge, true, "after_execute"
		if _, err := d.Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		a := rs.Latest()
		if a.Attempt.Config.JudgeBackend.Name != "scripted-judge" || a.Attempt.Config.Backend.Name != "scripted-agent" {
			t.Fatalf("config: %+v", a.Attempt.Config)
		}
		return h, rs, a, a.Invocations[0]
	}
	cases := []struct {
		name string
		mut  func(f *invoke.Invocation)
	}{
		{"policy", func(f *invoke.Invocation) { f.Backend.ToolPolicy = "deny=Bash" }},
		{"outward", func(f *invoke.Invocation) { f.Backend.ActsOutward = false }},
		{"model", func(f *invoke.Invocation) { f.Backend.Model = "other" }},
		{"judge on executor", func(f *invoke.Invocation) { f.Purpose = invoke.PurposeJudge; f.Tools = false; f.Cwd = "" }},
	}
	for _, c := range cases {
		h, rs, _, real := mk()
		twin, recs := invocationTwin(t, rs, real, real.Invocation.Request)
		c.mut(twin.Invocation)
		if err := forge(t, h, "swap", recs...); err == nil || !strings.Contains(err.Error(), "ran on backend") {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	// the same twin with the attempt's own snapshot folds: the rule is
	// about the snapshot, not about a second invocation
	h, rs, a, real := mk()
	_, recs := invocationTwin(t, rs, real, real.Invocation.Request)
	if err := forge(t, h, "same", recs...); err != nil {
		t.Fatalf("honest twin refused: %v", err)
	}
	// the door: a non-canonical policy on an attempt's snapshot
	att := *a.Attempt
	att.ID, att.Seq, att.Attempt, att.RecoversFrom = record.NewID(), 0, 2, 1
	att.Config.Backend.ToolPolicy = "deny=Bash;allow=Bash"
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "att2", Epoch: h.j.Epoch(), Records: []record.Record{&att}}); err == nil || !strings.Contains(err.Error(), "tool policy") {
		t.Fatalf("non-canonical policy on an attempt accepted: %v", err)
	}
}
