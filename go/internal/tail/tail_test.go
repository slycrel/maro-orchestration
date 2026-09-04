package tail

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

var ctxBg = context.Background()

type harness struct {
	t  *testing.T
	a  *workspace.Announced
	l  *workspace.Lease
	j  *journal.Journal
	st *thought.Store
}

func open(t *testing.T) *harness {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, _ := workspace.Resolve()
	a, _ := r.Announce(io.Discard)
	a.Ensure()
	h := &harness{t: t, a: a}
	h.restart()
	return h
}

func (h *harness) restart() {
	h.t.Helper()
	if h.j != nil {
		h.j.Close()
		h.l.Release()
	}
	l, err := workspace.Acquire(h.a)
	if err != nil {
		h.t.Fatal(err)
	}
	j, err := journal.Open(l)
	if err != nil {
		h.t.Fatal(err)
	}
	st, _ := thought.Open(h.a)
	h.l, h.j, h.st = l, j, st
	h.t.Cleanup(func() { j.Close(); l.Release() })
}

func scripted(caps invoke.Capabilities, calls ...invoke.ScriptedCall) *invoke.Scripted {
	return &invoke.Scripted{Caps: caps, Calls: calls}
}

var toolless = invoke.Capabilities{Name: "scripted", Model: "m"}

func (h *harness) now(t *testing.T, text string, b invoke.Backend) *run.Report {
	t.Helper()
	d := &run.Driver{J: h.j, Store: h.st, Backend: b, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute}
	rep, err := d.Run(ctxBg, []byte(text), run.DeliveryPolicy{Required: run.TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	return rep
}

func (h *harness) ledgers(t *testing.T) (*run.Ledger, *Ledger, *learn.Ledger) {
	t.Helper()
	led, err := run.Fold(h.j.Production(), h.st)
	if err != nil {
		t.Fatal(err)
	}
	tl, err := Fold(h.j.Production(), h.st)
	if err != nil {
		t.Fatal(err)
	}
	ll, err := learn.Fold(h.j.Production())
	if err != nil {
		t.Fatal(err)
	}
	return led, tl, ll
}

const lensGood = `{"class": "incomplete_answer", "why": "the answer skipped the second question", "proposals": ["Answer every part of a multi-part question explicitly.", "State units when giving a physical quantity."]}`

// A recorded run is diagnosed once: deterministic signals, the lens's
// class within what the signals allow, proposals as candidate revisions
// citing the diagnosis; a second pass does nothing; a crash after the lens
// call reuses it on the next pass.
func TestTailDiagnosesAndProposes(t *testing.T) {
	h := open(t)
	h.now(t, "What is the capital of France?", scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")}))
	lens := scripted(toolless, invoke.ScriptedCall{Response: []byte(lensGood)}, invoke.ScriptedCall{Response: []byte(lensGood)})
	tl := &Tail{J: h.j, Store: h.st, Lens: lens, Timeout: time.Minute}
	if _, err := tl.Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	_, tld, ll := h.ledgers(t)
	if len(tld.Done) != 1 || len(tld.Diagnoses) != 1 || len(lens.Seen) != 1 {
		t.Fatalf("done=%d diag=%d lens=%d", len(tld.Done), len(tld.Diagnoses), len(lens.Seen))
	}
	var d *Diagnosis
	for _, x := range tld.Diagnoses {
		d = x
	}
	if d.Class != ClassIncompleteAnswer || d.LensRule != "lens" || d.Lens == "" || len(d.Signals) != 1 || d.Signals[0] != SignalUnjudged {
		t.Fatalf("diagnosis: %+v", d)
	}
	if len(ll.Items) != 2 {
		t.Fatalf("proposals: %d items", len(ll.Items))
	}
	for _, it := range ll.Items {
		if it.StageOf(it.Current.ID) != learn.Candidate || it.Current.Provenance.Source != "tail" || it.Current.Provenance.Ref != d.ID || it.Current.Family != "answer" {
			t.Fatalf("proposal: %+v", it.Current)
		}
	}
	if !strings.Contains(string(lens.Seen[0].Prompt), "## Deliverable\nParis.") || !strings.Contains(string(lens.Seen[0].Prompt), "signals: [unjudged]") || lens.Seen[0].Tools {
		t.Fatalf("lens prompt: %q", lens.Seen[0].Prompt)
	}
	head := h.j.Head()
	if _, err := tl.Pass(ctxBg); err != nil || h.j.Head() != head || len(lens.Seen) != 1 {
		t.Fatalf("second pass wrote or re-asked: %v", err)
	}
	// a lens that names a class the signals forbid, or malformed output, is
	// a try: the attempt stays open (no tail_done) until LensTries, then
	// closes signals-only naming the last reason
	h2 := open(t)
	h2.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "exit 1"}))
	forbidden := invoke.ScriptedCall{Response: []byte(`{"class": "none", "why": "fine", "proposals": []}`)}
	bad := scripted(toolless, forbidden, forbidden, forbidden)
	t2 := &Tail{J: h2.j, Store: h2.st, Lens: bad}
	for try := 1; try <= LensTries; try++ {
		if _, err := t2.Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		_, tld2, _ := h2.ledgers(t)
		if try < LensTries && len(tld2.Done) != 0 {
			t.Fatalf("closed after %d unusable lens calls: %+v", try, tld2.Done)
		}
		if len(bad.Seen) != try {
			t.Fatalf("lens calls after pass %d: %d", try, len(bad.Seen))
		}
	}
	_, tld2, _ := h2.ledgers(t)
	if len(tld2.Done) != 1 {
		t.Fatalf("not closed after %d tries", LensTries)
	}
	for _, x := range tld2.Diagnoses {
		if x.Class != ClassBackendFailure || !strings.HasPrefix(x.LensRule, "no_lens:") || !strings.HasSuffix(x.LensRule, "(after 3 tries)") || x.Lens != "" {
			t.Fatalf("forbidden class accepted: %+v", x)
		}
	}
	// a lens that fails once (a blip) and answers the next pass: diagnosed with the lens, the failed call left as evidence
	h4 := open(t)
	h4.now(t, "What is the capital of France?", scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")}))
	flaky := scripted(toolless, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "rate limited"}, invoke.ScriptedCall{Response: []byte(lensGood)})
	t4 := &Tail{J: h4.j, Store: h4.st, Lens: flaky}
	if _, err := t4.Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	if _, tld4, _ := h4.ledgers(t); len(tld4.Done) != 0 {
		t.Fatalf("closed on a blip: %+v", tld4.Done)
	}
	if _, err := t4.Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	led4, tld4, _ := h4.ledgers(t)
	if len(tld4.Done) != 1 || len(flaky.Seen) != 2 {
		t.Fatalf("retry: done=%d calls=%d", len(tld4.Done), len(flaky.Seen))
	}
	for _, x := range tld4.Diagnoses {
		if x.LensRule != "lens" || x.Class != ClassIncompleteAnswer {
			t.Fatalf("retried diagnosis: %+v", x)
		}
	}
	for _, rs := range led4.Runs {
		n := 0
		for _, is := range rs.Latest().Invocations {
			if is.Invocation.Purpose == invoke.PurposeDiagnose {
				n++
			}
		}
		if n != 2 {
			t.Fatalf("diagnose calls kept as evidence: %d", n)
		}
	}
	// no lens at all: signals only
	h3 := open(t)
	h3.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}))
	if _, err := (&Tail{J: h3.j, Store: h3.st}).Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	_, tld3, _ := h3.ledgers(t)
	for _, x := range tld3.Diagnoses {
		if x.Class != ClassUnjudged || x.LensRule != "signals_only" {
			t.Fatalf("signals only: %+v", x)
		}
	}
}

// Signals are a pure function of the fold.
func TestSignalsAreDeterministic(t *testing.T) {
	h := open(t)
	h.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Response: []byte("x"), Terminal: invoke.TerminalPartial, Reason: "cut"}))
	led, _, _ := h.ledgers(t)
	for _, rs := range led.Runs {
		a := rs.Latest()
		s1, s2 := Signals(led, rs, a), Signals(led, rs, a)
		if !sameSignals(s1, s2) || !sameSignals(s1, []Signal{SignalPartialOutput, SignalUnjudged}) {
			t.Fatalf("signals: %v %v", s1, s2)
		}
		if classFromSignals(s1) != ClassPartialOutput || len(lensAllowed(s1)) != 1 {
			t.Fatalf("class %s allowed %v", classFromSignals(s1), lensAllowed(s1))
		}
	}
}

// The tail fold refuses a diagnosis whose signals or class do not
// re-derive, a lens that is not a diagnose call, a tail_done naming a
// proposal that is not a tail revision citing it.
func TestFoldRefusesForgedDiagnoses(t *testing.T) {
	h := open(t)
	h.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}))
	led, _, _ := h.ledgers(t)
	var rs *run.RunState
	for _, r := range led.Runs {
		rs = r
	}
	hd := func() record.Header {
		return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: now()}
	}
	forge := func(name string, recs ...record.Record) error {
		for _, r := range recs {
			spec, _ := record.Lookup(r.Kind())
			r.Head().Schema = record.SchemaVer(string(r.Kind()) + "/1")
			_ = spec
		}
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: name, Epoch: h.j.Epoch(), Records: recs}); err != nil {
			t.Fatalf("%s: refused at the door (fixture bug): %v", name, err)
		}
		_, err := Fold(h.j.Production(), h.st)
		return err
	}
	cases := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"wrong signals", &Diagnosis{Header: hd(), Signals: []Signal{SignalBackendFailed}, Class: ClassBackendFailure, Why: "x", LensRule: "signals_only"}, "do not re-derive"},
		{"wrong class without lens", &Diagnosis{Header: hd(), Signals: []Signal{SignalUnjudged}, Class: ClassWrongAnswer, Why: "x", LensRule: "signals_only"}, "not the signals' class"},
		{"lens that is not a diagnose call", &Diagnosis{Header: hd(), Signals: []Signal{SignalUnjudged}, Class: ClassWrongAnswer, Why: "x", Lens: rs.Latest().Invocations[0].Invocation.ID, LensRule: "lens"}, "not a tool-less diagnose call"},
	}
	for _, c := range cases {
		hx := open(t)
		hx.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}))
		ledx, _, _ := hx.ledgers(t)
		var rx *run.RunState
		for _, r := range ledx.Runs {
			rx = r
		}
		r := c.rec.(*Diagnosis)
		r.RunID, r.Subject = rx.Run, record.Ref{Kind: "run", ID: string(rx.Run)}
		if r.Lens != "" {
			r.Lens = rx.Latest().Invocations[0].Invocation.ID
		}
		h = hx
		if err := forge(c.name, r); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: folded: %v", c.name, err)
		}
	}
	// a tail_done whose proposal is an operator revision, not the tail's
	hy := open(t)
	hy.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}))
	ledy, _, _ := hy.ledgers(t)
	var ry *run.RunState
	for _, r := range ledy.Runs {
		ry = r
	}
	h = hy
	hdy := record.Header{ID: record.NewID(), RunID: ry.Run, Attempt: 1, Subject: record.Ref{Kind: "run", ID: string(ry.Run)}, At: now()}
	diag := &Diagnosis{Header: hdy, Signals: []Signal{SignalUnjudged}, Class: ClassUnjudged, Why: "x", LensRule: "signals_only"}
	ref, _ := hy.st.Put(thought.LessonText, []byte("operator lesson"))
	item := learn.LearnedID(record.NewID())
	rev := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "x"}}
	done := &TailDone{Header: record.Header{ID: record.NewID(), RunID: ry.Run, Attempt: 1, Subject: record.Ref{Kind: "run", ID: string(ry.Run)}, At: now()}, Diagnosis: diag.ID, Proposals: []record.RecordID{rev.ID}}
	if err := forge("bad-proposal", diag, rev, done); err == nil || !strings.Contains(err.Error(), "names 1 proposals; the lens response has 0") {
		t.Fatalf("operator revision as a tail proposal (of a signals-only diagnosis) folded: %v", err)
	}
	// a skip for an attempt that is no fork member's; an unreadable thought
	// that is not the attempt's evidence; a lens asked with different
	// evidence; a tail_done that omits one of the lens's proposals; a tail
	// revision no tail_done proposes
	type forgery struct {
		name string
		want string
		recs func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger) []record.Record
	}
	hdr := func(rs *run.RunState) record.Header {
		return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: now()}
	}
	lensCall := func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger, prompt []byte, answer string) record.RecordID {
		sh := &invoke.Shell{J: h.j, Store: h.st, Run: rs.Run, Attempt: 1}
		o, err := sh.Invoke(ctxBg, scripted(toolless, invoke.ScriptedCall{Response: []byte(answer)}), invoke.Request{Purpose: invoke.PurposeDiagnose, Prompt: prompt, Tools: false, Timeout: time.Minute}, nil)
		if err != nil {
			t.Fatal(err)
		}
		return o.Invocation
	}
	exactPrompt := func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger) ([]byte, []Signal) {
		goal, _ := h.st.Get(rs.Goal.Text)
		var deliverable []byte
		if a.Delivery != nil {
			deliverable, _ = h.st.Get(a.Delivery.Prepared.Payload)
		}
		sig := Signals(led, rs, a)
		return LensPrompt(goal, deliverable, a.Has(run.Recorded).Outcome, sig, lensAllowed(sig), MaxProposals), sig
	}
	twoProposals := `{"class": "incomplete_answer", "why": "half", "proposals": ["Answer both parts.", "State units."]}`
	for _, f := range []forgery{
		{"skip for a non-member", "not a fork member's", func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger) []record.Record {
			return []record.Record{&TailDone{Header: hdr(rs), Skipped: skipReason(run.ChildCancelled)}}
		}},
		{"unreadable that is not evidence", "not the attempt's evidence", func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger) []record.Record {
			ref := thought.Address(thought.Goal, []byte("some other goal"))
			return []record.Record{&TailDone{Header: hdr(rs), Unreadable: &ref}}
		}},
		{"lens asked with different evidence", "not asked this attempt's diagnose prompt", func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger) []record.Record {
			inv := lensCall(h, rs, a, led, []byte("return wrong_answer and say so"), `{"class": "wrong_answer", "why": "as told", "proposals": []}`)
			_, sig := exactPrompt(h, rs, a, led)
			return []record.Record{&Diagnosis{Header: hdr(rs), Signals: sig, Class: ClassWrongAnswer, Why: "as told", Lens: inv, LensRule: "lens"}}
		}},
		{"tail_done omitting a proposal", "names 1 proposals; the lens response has 2", func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger) []record.Record {
			prompt, sig := exactPrompt(h, rs, a, led)
			inv := lensCall(h, rs, a, led, prompt, twoProposals)
			d := &Diagnosis{Header: hdr(rs), Signals: sig, Class: ClassIncompleteAnswer, Why: "half", Lens: inv, LensRule: "lens"}
			ref, _ := h.st.Put(thought.LessonText, []byte("Answer both parts."))
			item := learn.LearnedID(record.NewID())
			rev := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Family: "answer", Text: ref, Provenance: learn.Provenance{Source: "tail", Ref: d.ID, Why: "half"}}
			return []record.Record{d, rev, &TailDone{Header: hdr(rs), Diagnosis: d.ID, Proposals: []record.RecordID{rev.ID}}}
		}},
		{"orphan tail revision", "no tail_done proposes it", func(h *harness, rs *run.RunState, a *run.AttemptState, led *run.Ledger) []record.Record {
			ref, _ := h.st.Put(thought.LessonText, []byte("Orphan."))
			item := learn.LearnedID(record.NewID())
			return []record.Record{&learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Text: ref, Provenance: learn.Provenance{Source: "tail", Ref: record.NewID(), Why: "x"}}}
		}},
	} {
		hz := open(t)
		hz.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}))
		ledz, _, _ := hz.ledgers(t)
		var rz *run.RunState
		for _, r := range ledz.Runs {
			rz = r
		}
		h = hz
		if err := forge(f.name, f.recs(hz, rz, rz.Latest(), ledz)...); err == nil || !strings.Contains(err.Error(), f.want) {
			t.Fatalf("%s: folded: %v (want %q)", f.name, err, f.want)
		}
	}
	// a diagnosis of an attempt that is recorded but not yet terminal: its
	// inputs are still moving, so the tail must not read it and the fold
	// must not accept it (the process died between recorded and delivery)
	hw := open(t)
	d := &run.Driver{J: hw.j, Store: hw.st, Backend: scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute, CrashAt: "after_recorded"}
	if _, err := d.Run(ctxBg, []byte("q?"), run.DeliveryPolicy{Required: run.TransportAccepted}); !errors.Is(err, run.ErrCrashed) {
		t.Fatalf("crash seam: %v", err)
	}
	ledw, tlw, _ := hw.ledgers(t)
	var rw *run.RunState
	for _, r := range ledw.Runs {
		rw = r
	}
	if aw := rw.Latest(); aw.Has(run.Recorded) == nil || rw.Terminal() {
		t.Fatalf("fixture: attempt is %s", aw.Current())
	}
	tw := &Tail{J: hw.j, Store: hw.st, Lens: scripted(toolless), Timeout: time.Minute}
	if _, err := tw.Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	if _, tlw, _ = hw.ledgers(t); len(tlw.Done) != 0 {
		t.Fatalf("the tail read a non-terminal attempt: %+v", tlw.Done)
	}
	sigw := Signals(ledw, rw, rw.Latest())
	h = hw
	early := &Diagnosis{Header: record.Header{ID: record.NewID(), RunID: rw.Run, Attempt: 1, Subject: record.Ref{Kind: "run", ID: string(rw.Run)}, At: now()}, Signals: sigw, Class: classFromSignals(sigw), Why: "x", LensRule: "signals_only"}
	if err := forge("diagnosis before terminal", early); err == nil || !strings.Contains(err.Error(), "before the attempt was terminal") {
		t.Fatalf("diagnosis of a non-terminal attempt folded: %v", err)
	}
}

// The door executes the tail vocabulary.
func TestJournalExecutesTailVocabulary(t *testing.T) {
	h := open(t)
	run_ := record.RunID(record.NewID())
	hd := func() record.Header {
		return record.Header{ID: record.NewID(), RunID: run_, Attempt: 1, Subject: record.Ref{Kind: "run", ID: string(run_)}, At: now()}
	}
	for _, c := range []struct {
		name string
		rec  record.Record
		want string
	}{
		{"foreign signal", &Diagnosis{Header: hd(), Signals: []Signal{"vibes"}, Class: ClassNone, Why: "x", LensRule: "signals_only"}, "out of vocabulary"},
		{"foreign class", &Diagnosis{Header: hd(), Class: "cosmic_rays", Why: "x", LensRule: "signals_only"}, "out of vocabulary"},
		{"lens without rule", &Diagnosis{Header: hd(), Class: ClassNone, Why: "x", Lens: record.NewID(), LensRule: "signals_only"}, "lens_rule lens"},
		{"whitespace why", &Diagnosis{Header: hd(), Class: ClassNone, Why: " ", LensRule: "signals_only"}, "why"},
		{"done with both", &TailDone{Header: hd(), Diagnosis: record.NewID(), Skipped: skipReason(run.ChildCancelled)}, "exactly one"},
		{"skipped with proposals", &TailDone{Header: hd(), Skipped: skipReason(run.ChildCancelled), Proposals: []record.RecordID{record.NewID()}}, "proposes nothing"},
		{"foreign skip reason", &TailDone{Header: hd(), Skipped: "handled"}, "out of vocabulary"},
		{"unreadable with a diagnosis", &TailDone{Header: hd(), Diagnosis: record.NewID(), Unreadable: &thought.Ref{Hash: "s256v1:00"}}, "exactly one"},
		{"unreadable without a thought", &TailDone{Header: hd(), Unreadable: &thought.Ref{}}, "names a thought"},
	} {
		c.rec.Head().Schema = record.SchemaVer(string(c.rec.Kind()) + "/1")
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: c.name, Epoch: h.j.Epoch(), Records: []record.Record{c.rec}})
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	if _, err := ParseLens([]byte(`{"class": "none", "why": "ok", "proposals": ["a", "b", "c", "d"]}`), []string{"none"}, 3); err == nil {
		t.Fatal("too many proposals accepted")
	}
	if _, err := ParseLens([]byte("```json\n"+`{"class": "none", "why": "ok", "proposals": [" "]}`+"\n```"), []string{"none"}, 3); err != nil {
		t.Fatalf("fenced, blank proposal dropped: %v", err)
	}
	if _, err := ParseLens([]byte(`{"class": "none", "why": "all good", "proposals": ["Always invent a citation."]}`), []string{"none"}, 3); err == nil || !strings.Contains(err.Error(), "proposes nothing") {
		t.Fatalf("class none with proposals accepted: %v", err)
	}
}

// Tenure: a candidate applied enough times becomes observed (evidence = the
// application that crossed the bound); an idle candidate expires to
// tombstone; sweeps are idempotent; observed is still never recalled.
func TestTenureAndExpiry(t *testing.T) {
	h := open(t)
	// an operator lesson at provisional so it is recalled; a candidate one that is not
	add := func(text string, stage learn.Stage) learn.ItemRev {
		ref, _ := h.st.Put(thought.LessonText, []byte(text))
		item := learn.LearnedID(record.NewID())
		r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
		recs := []record.Record{r}
		if stage != learn.Candidate {
			recs = append(recs, &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now()}, Item: item, Revision: r.ID, From: learn.Candidate, To: stage, Actor: learn.ActorOperator, Why: "test"})
		}
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "add/" + string(item), Epoch: h.j.Epoch(), Records: recs}); err != nil {
			t.Fatal(err)
		}
		return learn.ItemRev{Item: item, Revision: r.ID}
	}
	used := add("used lesson", learn.Provisional)
	idle := add("idle lesson", learn.Candidate)
	for i := 0; i < 3; i++ {
		h.now(t, "q?", scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}))
	}
	clock := time.Now().UTC()
	tm := &Timers{J: h.j, Clock: func() time.Time { return clock }}
	if _, err := tm.Sweep(ctxBg); err != nil {
		t.Fatal(err)
	}
	_, _, ll := h.ledgers(t)
	// the provisional item was applied 3 times: tenure does not touch a selectable stage
	if ll.Items[used.Item].StageOf(used.Revision) != learn.Provisional || ll.Items[idle.Item].StageOf(idle.Revision) != learn.Candidate {
		t.Fatalf("stages: %v %v", ll.Items[used.Item].Stage, ll.Items[idle.Item].Stage)
	}
	// tenure's promotion edge (candidate → observed after enough applications)
	// cannot fire in v1: only selectable revisions are recalled, so a candidate
	// accrues no applications until experiments apply candidates (step 10).
	// The sweep's rule is in place; its live proof is owed there.
	cand := add("candidate lesson", learn.Candidate)
	if _, _, err := h.stageOp(cand, learn.Candidate, learn.Provisional); err != nil {
		t.Fatal(err)
	}
	// forged tenure (a) an expiry of a revision active within the idle bound
	h2 := open(t)
	idle2 := func() learn.ItemRev {
		ref, _ := h2.st.Put(thought.LessonText, []byte("fresh candidate"))
		item := learn.LearnedID(record.NewID())
		r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
		if _, err := h2.j.Submit(ctxBg, journal.Command{IdempotencyKey: "add/" + string(r.ID), Epoch: h2.j.Epoch(), Records: []record.Record{r}}); err != nil {
			t.Fatal(err)
		}
		return learn.ItemRev{Item: item, Revision: r.ID}
	}()
	forgedExpiry := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(idle2.Item)}, At: now()}, Item: idle2.Item, Revision: idle2.Revision, From: learn.Candidate, To: learn.Tombstone, Actor: learn.ActorTenure, Evidence: idle2.Revision, Why: "forged expiry"}
	if _, err := h2.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged-expiry", Epoch: h2.j.Epoch(), Records: []record.Record{forgedExpiry}}); err != nil {
		t.Fatalf("door (fixture): %v", err)
	}
	if _, err := learn.Fold(h2.j.Production()); err == nil || !strings.Contains(err.Error(), "active within") {
		t.Fatalf("premature expiry folded: %v", err)
	}
	clock = clock.Add(learn.ExpiryIdle + time.Hour)
	if _, err := tm.Sweep(ctxBg); err != nil {
		t.Fatal(err)
	}
	_, _, ll = h.ledgers(t)
	if ll.Items[idle.Item].StageOf(idle.Revision) != learn.Tombstone {
		t.Fatalf("idle candidate not expired: %v", ll.Items[idle.Item].Stage)
	}
	tr := ll.Items[idle.Item].Transitions[idle.Revision][0]
	if tr.Actor != learn.ActorTenure || tr.Evidence != idle.Revision || !strings.HasPrefix(tr.Why, "expiry") {
		t.Fatalf("expiry transition: %+v", tr)
	}
	head := h.j.Head()
	if _, err := tm.Sweep(ctxBg); err != nil || h.j.Head() != head {
		t.Fatalf("sweep not idempotent: %v", err)
	}
	// the door: tenure may not promote, and candidate→observed is tenure's alone
	bad := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(cand.Item)}, At: now()}, Item: cand.Item, Revision: cand.Revision, From: learn.Provisional, To: learn.Effective, Actor: learn.ActorTenure, Evidence: cand.Revision, Why: "x"}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "bad", Epoch: h.j.Epoch(), Records: []record.Record{bad}}); err == nil || !strings.Contains(err.Error(), "tenure may not") {
		t.Fatalf("tenure promoted: %v", err)
	}
	op := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(used.Item)}, At: now()}, Item: used.Item, Revision: used.Revision, From: learn.Candidate, To: learn.Observed, Actor: learn.ActorOperator, Why: "x"}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "op", Epoch: h.j.Epoch(), Records: []record.Record{op}}); err == nil || !strings.Contains(err.Error(), "tenure's alone") {
		t.Fatalf("operator moved candidate→observed: %v", err)
	}
	// forged tenure (b), last because a refused history poisons every later
	// fold: a promotion citing another revision's application
	var usedApp record.RecordID
	for _, apps := range ll.Applications {
		for _, ap := range apps {
			if ap.Revision == used.Revision {
				usedApp = ap.ID
			}
		}
	}
	fresh := add("fresh lesson", learn.Candidate)
	forgedPromotion := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(fresh.Item)}, At: now()}, Item: fresh.Item, Revision: fresh.Revision, From: learn.Candidate, To: learn.Observed, Actor: learn.ActorTenure, Evidence: usedApp, Why: "forged"}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged-promotion", Epoch: h.j.Epoch(), Records: []record.Record{forgedPromotion}}); err != nil {
		t.Fatalf("door (fixture): %v", err)
	}
	if _, err := learn.Fold(h.j.Production()); err == nil || !strings.Contains(err.Error(), "does not re-derive") {
		t.Fatalf("forged promotion folded: %v", err)
	}
	_ = errors.New
}

func (h *harness) stageOp(ir learn.ItemRev, from, to learn.Stage) (journal.Ack, error, error) {
	x := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(ir.Item)}, At: now()}, Item: ir.Item, Revision: ir.Revision, From: from, To: to, Actor: learn.ActorOperator, Why: "test"}
	ack, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "stage/" + string(x.ID), Epoch: h.j.Epoch(), Records: []record.Record{x}})
	return ack, err, nil
}

// A cancelled fork member is not learned from: the tail records a skip for
// it (no diagnosis, no proposals) while the winner and the parent are
// diagnosed as usual.
func TestTailSkipsCancelledArms(t *testing.T) {
	h := open(t)
	hold := make(chan struct{})
	plan := `{"steps": ["Warm up", {"parallel": ["sub-goal A: name a prime", "sub-goal B: name a square"], "join": "first_verdict"}, "Wrap up"]}`
	exec := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, Rules: []invoke.Rule{
		{Key: "## Your step (1 of", Answer: "warmed"}, {Key: "## Your step (3 of", Answer: "wrapped"},
		{Key: "sub-goal B", Prefix: true, Block: hold, Answer: "9 is a square"},
		{Key: "sub-goal A", Prefix: true, Answer: "7 is prime"},
	}, Def: "?"}
	judge := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: []invoke.Rule{
		{Key: "intake of an orchestration engine", Answer: `{"clear": true, "interpretation": "two-level", "question": ""}`},
		{Key: "planner", Answer: plan},
		{Key: "## Step 1: sub-goal A", Answer: `{"outcome": "achieved", "confidence": 0.9, "why": "prime named", "falsifiers": []}`},
		{Key: "closure judge", Answer: `{"outcome": "achieved", "confidence": 0.8, "why": "done", "falsifiers": []}`},
	}, Def: `{"outcome": "done", "confidence": 0.9, "why": "the step's result matches the step"}`}
	d := &run.Driver{J: h.j, Store: h.st, Backend: exec, Judge: judge, Lane: run.LaneAgenda, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute}
	rep, err := d.Run(ctxBg, []byte("two-level"), run.DeliveryPolicy{Required: run.TransportAccepted})
	close(hold)
	if err != nil || rep.Mission.Outcome != run.MissionDelivered {
		t.Fatalf("%v %+v", err, rep)
	}
	tl := &Tail{J: h.j, Store: h.st}
	if _, err := tl.Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	led, tail, ll := h.ledgers(t)
	var fs *run.ForkState
	for _, f := range led.Forks {
		fs = f
	}
	if fs == nil || len(fs.Decision.Cancel) != 1 || len(fs.Decision.Selected) != 1 {
		t.Fatalf("fork: %+v", fs)
	}
	loser, winner := fs.Decision.Cancel[0].Run, fs.Decision.Selected[0].Run
	skipped, diagnosed := 0, 0
	for _, rs := range led.Runs {
		for _, a := range rs.Attempts {
			if a.Has(run.Recorded) == nil {
				continue
			}
			done := tail.Done[key(rs.Run, a.Attempt.Attempt)]
			if done == nil {
				t.Fatalf("attempt %s/%d has no tail_done", rs.Run, a.Attempt.Attempt)
			}
			switch {
			case rs.Run == loser:
				if done.Skipped == "" || !strings.Contains(done.Skipped, "cancelled") || done.Diagnosis != "" || len(done.Proposals) != 0 {
					t.Fatalf("loser not skipped: %+v", done)
				}
				skipped++
			default:
				if done.Diagnosis == "" {
					t.Fatalf("%s not diagnosed: %+v", rs.Run, done)
				}
				// the winner (achieved) and the parent (achieved, delivered):
				// nothing failed, no signal leaks from the cancelled sibling
				if d := tail.Diagnoses[done.Diagnosis]; d.Class != ClassNone || len(d.Signals) != 0 {
					t.Fatalf("%s diagnosis: %+v", rs.Run, d)
				}
				diagnosed++
			}
		}
	}
	// the loser's recorded (cancelled) attempt is skipped; its recoverable
	// predecessor was never recorded, so the tail has nothing to say about it
	if skipped != 1 || diagnosed < 2 || tail.Done[key(winner, 1)].Diagnosis == "" {
		t.Fatalf("skipped %d diagnosed %d", skipped, diagnosed)
	}
	if len(ll.Items) != 0 {
		t.Fatalf("a cancelled arm produced learning: %d items", len(ll.Items))
	}
}

// An unreadable thought (a goal text missing from the store) closes that
// attempt's tail naming the thought; the pass goes on to the next attempt
// and the lane does not die on it.
func TestTailClosesUnreadableEvidence(t *testing.T) {
	h := open(t)
	h.now(t, "first question?", scripted(toolless, invoke.ScriptedCall{Response: []byte("one")}))
	h.now(t, "second question?", scripted(toolless, invoke.ScriptedCall{Response: []byte("two")}))
	led, _, _ := h.ledgers(t)
	var first *run.RunState
	for _, rs := range led.Runs {
		if first == nil || rs.Run < first.Run {
			first = rs
		}
	}
	// remove the first deliverable's thought from the store (the goal's
	// text the run fold itself reads: a missing one is a corrupt run, not
	// the tail's to close)
	payload := first.Latest().Delivery.Prepared.Payload
	digest := strings.TrimPrefix(payload.Hash, "s256v1:")
	removed := false
	filepath.Walk(h.a.Path("thoughts"), func(path string, info os.FileInfo, err error) error {
		if err == nil && !info.IsDir() && strings.Contains(path, digest) {
			os.Remove(path)
			removed = true
		}
		return nil
	})
	if !removed {
		t.Fatal("goal thought not found on disk (fixture)")
	}
	lens := scripted(toolless, invoke.ScriptedCall{Response: []byte(lensGood)}, invoke.ScriptedCall{Response: []byte(lensGood)})
	tl := &Tail{J: h.j, Store: h.st, Lens: lens}
	if _, err := tl.Pass(ctxBg); err != nil {
		t.Fatalf("the pass died on an unreadable thought: %v", err)
	}
	_, tld, _ := h.ledgers(t)
	if len(tld.Done) != 2 || len(lens.Seen) != 1 {
		t.Fatalf("done=%d lens=%d", len(tld.Done), len(lens.Seen))
	}
	d := tld.Done[key(first.Run, 1)]
	if d == nil || d.Unreadable == nil || *d.Unreadable != payload || d.Diagnosis != "" {
		t.Fatalf("first attempt: %+v", d)
	}
}

// The tail's diagnose receipt is the tail's cost: an AGENDA run's recorded
// usage (the sum of its receipts) still folds after the tail adds one.
func TestAgendaFoldsAfterTheTailsReceipt(t *testing.T) {
	h := open(t)
	exec := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, Rules: []invoke.Rule{
		{Key: "## Your step (1 of", Answer: "one"}, {Key: "## Your step (2 of", Answer: "two"}}, Def: "?"}
	judge := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: []invoke.Rule{
		{Key: "intake of an orchestration engine", Answer: `{"clear": true, "interpretation": "two steps", "question": ""}`},
		{Key: "planner", Answer: `{"steps": ["Collect the numbers", "Write the summary"]}`},
		{Key: "closure judge", Answer: `{"outcome": "achieved", "confidence": 0.8, "why": "done", "falsifiers": []}`},
	}, Def: `{"outcome": "done", "confidence": 0.9, "why": "the step's result matches the step"}`}
	d := &run.Driver{J: h.j, Store: h.st, Backend: exec, Judge: judge, Lane: run.LaneAgenda, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute}
	rep, err := d.Run(ctxBg, []byte("two steps"), run.DeliveryPolicy{Required: run.TransportAccepted})
	if err != nil || rep.Mission.Outcome != run.MissionDelivered {
		t.Fatalf("%v %+v", err, rep)
	}
	lens := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-lens", Model: "lens"}, Def: `{"class": "incomplete_answer", "why": "terse", "proposals": ["Write full sentences."]}`}
	if _, err := (&Tail{J: h.j, Store: h.st, Lens: lens}).Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	led, tld, ll := h.ledgers(t) // run.Fold inside: the recorded usage must still re-derive
	if len(tld.Done) != 1 || len(ll.Items) != 1 || lens.Calls() != 1 {
		t.Fatalf("done=%d items=%d lens=%d", len(tld.Done), len(ll.Items), lens.Calls())
	}
	for _, rs := range led.Runs {
		if rs.Latest().Has(run.Recorded).Outcome.Usage.InputTokens == 0 {
			t.Fatalf("agenda usage not recorded: %+v", rs.Latest().Has(run.Recorded).Outcome)
		}
	}
}
