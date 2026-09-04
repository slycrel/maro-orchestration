package invoke

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

func newShell(t *testing.T) (*Shell, *workspace.Lease) {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, _ := workspace.Resolve()
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	a.Ensure()
	l, err := workspace.Acquire(a)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Release() })
	j, err := journal.Open(l)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { j.Close() })
	st, err := thought.Open(a)
	if err != nil {
		t.Fatal(err)
	}
	return &Shell{J: j, Store: st, Run: "run-1", Attempt: 1}, l
}

// reopen simulates a process restart: the lease is released and taken
// again (a new epoch, as a new process would have), the journal reopened
// under it, and a new attempt begins.
func reopen(t *testing.T, sh *Shell, l *workspace.Lease) *Shell {
	t.Helper()
	sh.J.Close()
	l.Release()
	r, _ := workspace.Resolve()
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	l2, err := workspace.Acquire(a)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l2.Release() })
	j, err := journal.Open(l2)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { j.Close() })
	return &Shell{J: j, Store: sh.Store, Run: sh.Run, Attempt: sh.Attempt + 1}
}

// Reconciliation is for a dead process's calls: a call dispatched under
// the current lease is still being made by some lane of this process and
// is left alone, however long it takes; the same call, seen from the next
// lease, is reconciled.
func TestReconcileLeavesThisEpochsCallsAlone(t *testing.T) {
	sh, l := newShell(t)
	hold := make(chan struct{})
	b := &Keyed{Caps: toolless, Rules: []Rule{{Key: "slow", Block: hold, Answer: "done"}}}
	errc := make(chan error, 1)
	go func() {
		_, err := sh.Invoke(ctxBg, b, execReq("slow question"), nil)
		errc <- err
	}()
	// wait for the dispatch to land
	var id record.RecordID
	for i := 0; i < 200 && id == ""; i++ {
		states, err := Fold(sh.J.Production())
		if err != nil {
			t.Fatal(err)
		}
		for k, s := range states {
			if s.Dispatched {
				id = k
			}
		}
		time.Sleep(10 * time.Millisecond)
	}
	if id == "" {
		t.Fatal("dispatch never landed")
	}
	if rc, fin, err := Reconcile(ctxBg, sh); err != nil || len(rc) != 0 || len(fin) != 0 {
		t.Fatalf("reconciled a live call of this epoch: %v %d %d", err, len(rc), len(fin))
	}
	close(hold)
	if err := <-errc; err != nil {
		t.Fatal(err)
	}
	if s := foldOne(t, sh, id); s.Phase() != "receipt_committed" {
		t.Fatalf("after release: %s", s.Phase())
	}
	// a second call left in flight when the process dies IS reconciled by the next epoch
	hold2 := make(chan struct{})
	b2 := &Keyed{Caps: toolless, Rules: []Rule{{Key: "slow", Block: hold2, Answer: "done"}}}
	go func() { sh.Invoke(ctxBg, b2, execReq("slow again"), nil) }()
	for i := 0; i < 200; i++ {
		states, _ := Fold(sh.J.Production())
		n := 0
		for _, s := range states {
			if s.Dispatched {
				n++
			}
		}
		if n == 2 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	sh2 := reopen(t, sh, l)
	close(hold2)
	rc, _, err := Reconcile(ctxBg, sh2)
	if err != nil || len(rc) != 1 || rc[0].Disposition != DispositionAbandoned {
		t.Fatalf("next epoch: %v %+v", err, rc)
	}
}

var (
	toolless = Capabilities{Name: "scripted", Model: "m", ActsOutward: false}
	outward  = Capabilities{Name: "scripted", Model: "m", ActsOutward: true, OutwardReconcilable: false}
	ctxBg    = context.Background()
)

func execReq(p string) Request {
	return Request{Purpose: PurposeExecute, Prompt: []byte(p), Tools: true}
}

func foldOne(t *testing.T, sh *Shell, id record.RecordID) *State {
	t.Helper()
	states, err := Fold(sh.J.Production())
	if err != nil {
		t.Fatal(err)
	}
	return states[id]
}

// The whole chain, in order, each transition a record; effects observed as
// announced with results as reported; the response whole; the receipt
// derived from the terminal.
func TestInvokeCommitsEveryTransition(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: outward, Calls: []ScriptedCall{{
		Response: []byte("done: 3 files"),
		Effects: []ScriptedEffect{
			{Op: "Read", Input: []byte(`{"path":"a"}`), Output: []byte("contents")},
			{Op: "Bash", Input: []byte(`{"cmd":"ls"}`), Output: []byte("a b")},
			{Op: "FrobnicateAPI", Input: []byte(`{}`), Unanswered: true},
		},
		Usage: Usage{InputTokens: 10, OutputTokens: 5, CostUSD: 0.01, CostReported: true},
	}}}
	out, err := sh.Invoke(ctxBg, b, execReq("do the thing"), &Target{Name: "step", Limit: 1000, Why: "measured p90"})
	if err != nil {
		t.Fatal(err)
	}
	if string(out.Response) != "done: 3 files" || out.Terminal != TerminalComplete || len(out.Effects) != 3 || out.Receipt == "" || out.Err != nil {
		t.Fatalf("%+v", out)
	}
	s := foldOne(t, sh, out.Invocation)
	if s.Phase() != "receipt_committed" || !s.Dispatched || s.Terminal.State != TerminalComplete || s.Receipt.Usage.CostUSD != 0.01 || !s.Receipt.Usage.CostReported {
		t.Fatalf("state %s %+v", s.Phase(), s)
	}
	want := []OperationClass{OpQuery, OpIrreversible, OpIrreversible}
	for i, e := range s.Effects {
		if e.Ordinal != i || e.Class != want[i] || e.Key != DeriveKey(s.Invocation.EffectToken, i) || e.Announced {
			t.Fatalf("effect %d: %+v", i, e)
		}
	}
	if len(s.Results) != 2 || s.Results[0] == nil || s.Results[1] == nil {
		t.Fatalf("results %v", s.Results)
	}
	if un := s.Unanswered(); len(un) != 1 || un[0].Op != "FrobnicateAPI" {
		t.Fatalf("unanswered %v", un)
	}
	if got, _ := sh.Store.Get(s.Invocation.Request); string(got) != "do the thing" {
		t.Fatal("request not stored whole")
	}
	if got, _ := sh.Store.Get(s.Receipt.Response); string(got) != "done: 3 files" {
		t.Fatal("response not stored whole")
	}
	if s.Terminal.Response == nil || s.Terminal.Response.Hash != s.Receipt.Response.Hash {
		t.Fatal("terminal must carry the response the receipt names")
	}
	if s.Invocation.TargetWhy == "" || len(b.Seen) != 1 || b.Seen[0].Purpose != PurposeExecute {
		t.Fatalf("target/purpose not carried: %+v", s.Invocation)
	}
}

// Everything that can be refused is refused BEFORE anything is written.
func TestInvokeRefusesBeforeWriting(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: toolless, Calls: []ScriptedCall{{Response: []byte("x")}}}
	cases := []struct {
		name string
		f    func() error
		want error
	}{
		{"bad purpose", func() error {
			_, e := sh.Invoke(ctxBg, b, Request{Purpose: "dance", Prompt: []byte("p")}, nil)
			return e
		}, ErrRequest},
		{"target without why", func() error {
			_, e := sh.Invoke(ctxBg, b, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, &Target{Name: "t", Limit: 1})
			return e
		}, ErrTargetWhy},
		{"nameless backend", func() error {
			_, e := sh.Invoke(ctxBg, &Scripted{Caps: Capabilities{}}, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, nil)
			return e
		}, ErrBackendContract},
		{"prompt over max", func() error {
			_, e := sh.Invoke(ctxBg, &Scripted{Caps: Capabilities{Name: "tiny", MaxInputBytes: 4}}, Request{Purpose: PurposeJudge, Prompt: []byte("too long")}, nil)
			return e
		}, ErrBackendIncapable},
		{"cancelled context", func() error {
			c, cancel := context.WithCancel(ctxBg)
			cancel()
			_, e := sh.Invoke(c, b, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, nil)
			return e
		}, context.Canceled},
	}
	for _, c := range cases {
		if err := c.f(); !errors.Is(err, c.want) {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	if sh.J.Head() != 0 {
		t.Fatal("a refused invoke wrote records")
	}
	var inc *Incapable
	_, err := sh.Invoke(ctxBg, &Scripted{Caps: Capabilities{Name: "tiny", MaxInputBytes: 4}}, Request{Purpose: PurposeJudge, Prompt: []byte("too long")}, nil)
	if !errors.As(err, &inc) || inc.Actual != 8 || inc.Max != 4 {
		t.Fatalf("typed incapability: %v", err)
	}
	// exactly at the max is delivered whole
	ok := &Scripted{Caps: Capabilities{Name: "tiny", MaxInputBytes: 4}, Calls: []ScriptedCall{{Response: []byte("r")}}}
	if _, err := sh.Invoke(ctxBg, ok, Request{Purpose: PurposeJudge, Prompt: []byte("four")}, nil); err != nil {
		t.Fatal(err)
	}
	if string(ok.Seen[0].Prompt) != "four" {
		t.Fatal("prompt not delivered whole")
	}
}

// Backend contract violations become failed terminals, never panics or
// orphans: nil result, empty terminal, complete without a response, a
// panic, negative usage.
func TestBackendContractViolationsAreFailedTerminals(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: toolless, Calls: []ScriptedCall{
		{NilResult: true},
		{Terminal: "weird", Response: []byte("x")},
		{Terminal: TerminalComplete, Response: nil},
		{Panic: true},
		{Response: []byte("x"), Usage: Usage{InputTokens: -1}},
		{Response: []byte("x"), Usage: Usage{CostUSD: 1}}, // cost without cost_reported
	}}
	for i := 0; i < 6; i++ {
		out, err := sh.Invoke(ctxBg, b, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, nil)
		if out == nil || out.Terminal != TerminalFailed || out.Receipt != "" {
			t.Fatalf("case %d: %+v %v", i, out, err)
		}
		if i != 4 && i != 5 && !errors.Is(err, ErrBackendContract) {
			t.Fatalf("case %d: want ErrBackendContract, got %v", i, err)
		}
		s := foldOne(t, sh, out.Invocation)
		if s.Phase() != "terminal_observed" || s.Terminal.State != TerminalFailed || !strings.Contains(s.Terminal.Reason, "contract") {
			t.Fatalf("case %d: %s %+v", i, s.Phase(), s.Terminal)
		}
	}
	if rc, fin, err := Reconcile(ctxBg, sh); err != nil || len(rc) != 0 || len(fin) != 0 {
		t.Fatalf("nothing to reconcile: %v %v %v", rc, fin, err)
	}
}

// Before-dispatch failure leaves a terminal; a failed stream leaves no
// receipt; a partial stream leaves a receipt.
func TestTerminalStates(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: outward, Calls: []ScriptedCall{
		{FailBefore: true},
		{Terminal: TerminalFailed, Reason: "cli exit 1"},
		{Response: []byte("r"), Terminal: TerminalPartial, Reason: "2 protocol violations"},
	}}
	out, err := sh.Invoke(ctxBg, b, execReq("a"), nil)
	if !errors.Is(err, ErrBeforeDispatch) || out.Terminal != TerminalFailed {
		t.Fatalf("before-dispatch: %v %+v", err, out)
	}
	out2, err := sh.Invoke(ctxBg, b, execReq("b"), nil)
	if err != nil || out2.Terminal != TerminalFailed || out2.Receipt != "" {
		t.Fatalf("failed stream: %v %+v", err, out2)
	}
	out3, err := sh.Invoke(ctxBg, b, execReq("c"), nil)
	if err != nil || out3.Terminal != TerminalPartial || out3.Receipt == "" || string(out3.Response) != "r" {
		t.Fatalf("partial stream: %v %+v", err, out3)
	}
}

// The kill-site matrix, through the REAL Invoke: the process dies after each
// commit; on restart Fold tells the truth and Reconcile disposes or
// finalizes. Never a blind replay.
func TestKillSiteMatrix(t *testing.T) {
	type expect struct {
		phase       string
		reconciled  int
		finalized   int
		disposition Disposition
		phaseAfter  string
	}
	cases := []struct {
		stage string
		caps  Capabilities
		exp   expect
	}{
		{"prepared", outward, expect{"prepared", 0, 0, "", "prepared"}},
		{"dispatched", toolless, expect{"dispatched", 1, 0, DispositionAbandoned, "reconciled:abandoned"}},
		{"dispatched", outward, expect{"dispatched", 1, 0, DispositionIndeterminate, "reconciled:indeterminate_external_effect"}},
		{"effects", toolless, expect{"dispatched", 1, 0, DispositionIndeterminate, "reconciled:indeterminate_external_effect"}}, // a tool-less snapshot with a Bash effect: evidence wins
		{"terminal", outward, expect{"terminal_observed", 0, 1, "", "receipt_committed"}},
	}
	for _, c := range cases {
		t.Run(c.stage+"/"+map[bool]string{true: "outward", false: "toolless"}[c.caps.ActsOutward], func(t *testing.T) {
			sh, l := newShell(t)
			sh.CrashAt = c.stage
			b := &Scripted{Caps: c.caps, Calls: []ScriptedCall{{Response: []byte("resp"), Effects: []ScriptedEffect{{Op: "Bash", Input: []byte(`{}`), Output: []byte("o")}}}}}
			out, err := sh.Invoke(ctxBg, b, execReq("p"), nil)
			if !errors.Is(err, ErrCrashed) || out == nil || out.Invocation == "" {
				t.Fatalf("crash seam: %v %+v", err, out)
			}
			sh2 := reopen(t, sh, l)
			s := foldOne(t, sh2, out.Invocation)
			if s.Phase() != c.exp.phase {
				t.Fatalf("after crash: phase %s, want %s", s.Phase(), c.exp.phase)
			}
			rc, fin, err := Reconcile(ctxBg, sh2)
			if err != nil || len(rc) != c.exp.reconciled || len(fin) != c.exp.finalized {
				t.Fatalf("reconcile: %v %v %v", rc, fin, err)
			}
			if c.exp.reconciled == 1 && rc[0].Disposition != c.exp.disposition {
				t.Fatalf("disposition %s, want %s", rc[0].Disposition, c.exp.disposition)
			}
			s = foldOne(t, sh2, out.Invocation)
			if s.Phase() != c.exp.phaseAfter {
				t.Fatalf("after reconcile: %s, want %s", s.Phase(), c.exp.phaseAfter)
			}
			if c.exp.finalized == 1 {
				if got, _ := sh2.Store.Get(s.Receipt.Response); string(got) != "resp" {
					t.Fatal("finalized receipt does not carry the response")
				}
			}
			// idempotent
			if rc2, fin2, _ := Reconcile(ctxBg, sh2); len(rc2)+len(fin2) != 0 {
				t.Fatal("reconcile not idempotent")
			}
		})
	}
}

// A lost receipt is finalized from the terminal on restart.
func TestReconcileFinalizesLostReceipt(t *testing.T) {
	sh, l := newShell(t)
	sh.CrashAt = "terminal"
	b := &Scripted{Caps: toolless, Calls: []ScriptedCall{{Response: []byte("kept"), Usage: Usage{InputTokens: 3}}}}
	out, _ := sh.Invoke(ctxBg, b, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, nil)
	sh2 := reopen(t, sh, l)
	_, fin, err := Reconcile(ctxBg, sh2)
	if err != nil || len(fin) != 1 || fin[0].Usage.InputTokens != 3 {
		t.Fatalf("%v %v", fin, err)
	}
	s := foldOne(t, sh2, out.Invocation)
	if s.Receipt == nil || s.Receipt.Response.Hash != s.Terminal.Response.Hash {
		t.Fatal("finalized receipt must equal the terminal's response")
	}
}

// A hung backend cut by the caller's deadline still records its terminal.
func TestTimeoutIsATerminal(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: toolless, Calls: []ScriptedCall{{Hang: true}}}
	ctx, cancel := context.WithTimeout(ctxBg, 50*time.Millisecond)
	defer cancel()
	out, err := sh.Invoke(ctx, b, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, nil)
	if err != nil || out.Terminal != TerminalFailed || !strings.Contains(out.Reason, "deadline") {
		t.Fatalf("%v %+v", err, out)
	}
	if foldOne(t, sh, out.Invocation).Phase() != "terminal_observed" {
		t.Fatal("timeout left an orphan")
	}
}

// A dead lease after dispatch: bookkeeping fails, the outcome still carries
// the invocation id and the error, and restart reconciles it.
func TestLeaseLossMidInvokeLeavesRecoverableTruth(t *testing.T) {
	sh, l := newShell(t)
	b := &Scripted{Caps: outward, Calls: []ScriptedCall{{Response: []byte("r")}}}
	// kill the lease from inside the backend: the terminal commit then fails
	killer := &leaseKiller{inner: b, l: l}
	out, err := sh.Invoke(ctxBg, killer, execReq("p"), nil)
	if err == nil || out == nil || out.Invocation == "" || out.Err == nil {
		t.Fatalf("%v %+v", err, out)
	}
	l2, err := workspace.Acquire(l.Root())
	if err != nil {
		t.Fatal(err)
	}
	defer l2.Release()
	sh.J.Close()
	j, _ := journal.Open(l2)
	defer j.Close()
	sh2 := &Shell{J: j, Store: sh.Store, Run: sh.Run, Attempt: 2}
	rc, _, err := Reconcile(ctxBg, sh2)
	if err != nil || len(rc) != 1 || rc[0].Disposition != DispositionIndeterminate {
		t.Fatalf("%v %v", rc, err)
	}
}

type leaseKiller struct {
	inner Backend
	l     *workspace.Lease
}

func (k *leaseKiller) Capabilities() Capabilities { return k.inner.Capabilities() }
func (k *leaseKiller) Complete(ctx context.Context, req Request, sink Sink) (*Result, error) {
	res, err := k.inner.Complete(ctx, req, sink)
	k.l.Release()
	return res, err
}

// The sink is safe under concurrent reporting: ordinals unique and
// contiguous, every reported effect committed.
func TestSinkIsSerialized(t *testing.T) {
	sh, _ := newShell(t)
	b := &concurrentBackend{n: 25}
	out, err := sh.Invoke(ctxBg, b, execReq("p"), nil)
	if err != nil || len(out.Effects) != 25 {
		t.Fatalf("%v %+v", err, out)
	}
	s := foldOne(t, sh, out.Invocation)
	if len(s.Effects) != 25 || len(s.Results) != 25 {
		t.Fatalf("%d effects %d results", len(s.Effects), len(s.Results))
	}
}

type concurrentBackend struct{ n int }

func (c *concurrentBackend) Capabilities() Capabilities { return outward }
func (c *concurrentBackend) Complete(ctx context.Context, req Request, sink Sink) (*Result, error) {
	var wg sync.WaitGroup
	for i := 0; i < c.n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ord, _, err := sink.Observe(EffectEvent{Op: "Read", Input: []byte(`{}`)})
			if err == nil {
				_ = sink.Result(EffectResult{Ordinal: ord, Output: []byte("x")})
			}
		}()
	}
	wg.Wait()
	return &Result{Terminal: TerminalComplete, Response: []byte("ok")}, nil
}

// Fold refuses every forged history.
func TestFoldRefusesForgedHistories(t *testing.T) {
	mk := func(t *testing.T) (*Shell, *Invocation) {
		sh, _ := newShell(t)
		reqRef, _ := sh.Store.Put(thought.Prompt, []byte("p"))
		tok, _ := NewEffectToken()
		inv := &Invocation{Header: sh.header(record.Ref{Kind: "prompt", ID: reqRef.Hash}), Purpose: PurposeExecute, Request: reqRef, Backend: toolless, EffectToken: tok}
		if _, err := sh.commit(ctxBg, string(inv.ID)+":prepared", inv); err != nil {
			t.Fatal(err)
		}
		return sh, inv
	}
	ev, _ := (&Shell{}).Store, 0
	_ = ev
	cases := []struct {
		name  string
		forge func(t *testing.T, sh *Shell, inv *Invocation)
	}{
		{"dispatched twice", func(t *testing.T, sh *Shell, inv *Invocation) {
			for i := 0; i < 2; i++ {
				sh.commit(ctxBg, string(inv.ID)+":d"+string(rune('0'+i)), &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			}
		}},
		{"receipt without terminal", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			sh.commit(ctxBg, "r", &Receipt{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, Response: inv.Request})
		}},
		{"two terminals", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			for i := 0; i < 2; i++ {
				sh.commit(ctxBg, "t"+string(rune('0'+i)), &TerminalObserved{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, State: TerminalFailed, Reason: "x"})
			}
		}},
		{"effect key not derived", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			sh.commit(ctxBg, "e", &ToolEffect{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Ordinal: 0, Op: "Read", Class: OpQuery, Key: strings.Repeat("00", 32), Input: inv.Request})
		}},
		{"effect class forged", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			e := &ToolEffect{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Ordinal: 0, Op: "Bash", Class: OpIrreversible, Key: DeriveKey(inv.EffectToken, 0), Input: inv.Request}
			// ValidateWire refuses class/table disagreement at Submit; forge past it by committing a legal record then checking Fold's own rule
			if _, err := sh.commit(ctxBg, "e", e); err != nil {
				t.Fatal(err)
			}
			bad := &ToolEffect{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Ordinal: 1, Op: "Read", Class: OpQuery, Key: DeriveKey(inv.EffectToken, 5), Input: inv.Request}
			sh.commit(ctxBg, "e2", bad)
		}},
		{"ordinal gap", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			sh.commit(ctxBg, "e", &ToolEffect{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Ordinal: 1, Op: "Read", Class: OpQuery, Key: DeriveKey(inv.EffectToken, 1), Input: inv.Request})
		}},
		{"result for unobserved effect", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			sh.commit(ctxBg, "r", &ToolEffectResult{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Ordinal: 0, Output: inv.Request})
		}},
		{"reconciled after terminal", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			sh.commit(ctxBg, "t", &TerminalObserved{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, State: TerminalFailed, Reason: "x"})
			sh.commit(ctxBg, "rc", &Reconciled{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Disposition: DispositionAbandoned, Evidence: "forged"})
		}},
		{"disposition contradicts evidence", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			sh.commit(ctxBg, "e", &ToolEffect{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Ordinal: 0, Op: "Bash", Class: OpIrreversible, Key: DeriveKey(inv.EffectToken, 0), Input: inv.Request})
			sh.commit(ctxBg, "rc", &Reconciled{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Disposition: DispositionAbandoned, Evidence: "forged"})
		}},
		{"receipt response disagrees with terminal", func(t *testing.T, sh *Shell, inv *Invocation) {
			sh.commit(ctxBg, "d", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID})
			other, _ := sh.Store.Put(thought.Response, []byte("other"))
			sh.commit(ctxBg, "t", &TerminalObserved{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, State: TerminalComplete, Response: &inv.Request})
			sh.commit(ctxBg, "r", &Receipt{Header: sh.header(subj(inv.ID)), Invocation: inv.ID, Attempt: 1, Response: other})
		}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			sh, inv := mk(t)
			c.forge(t, sh, inv)
			if _, err := Fold(sh.J.Production()); !errors.Is(err, ErrFoldCorrupt) {
				t.Fatalf("forged history accepted: %v", err)
			}
		})
	}
}

// Evidence round-trips bytes exactly: invalid JSON input, non-UTF-8 output.
func TestEvidenceIsBytePreserving(t *testing.T) {
	sh, _ := newShell(t)
	in := []byte(`{not json`)
	outb := []byte{0xff, 0x00, 0xfe}
	b := &Scripted{Caps: outward, Calls: []ScriptedCall{{Response: []byte("r"), Effects: []ScriptedEffect{{Op: "Read", Input: in, Output: outb}}}}}
	out, err := sh.Invoke(ctxBg, b, execReq("p"), nil)
	if err != nil {
		t.Fatal(err)
	}
	s := foldOne(t, sh, out.Invocation)
	raw, _ := sh.Store.Get(s.Effects[0].Input)
	got, err := DecodeEvidence(raw)
	if err != nil || !bytes.Equal(got, in) {
		t.Fatalf("input: %v %q", err, got)
	}
	raw, _ = sh.Store.Get(s.Results[0].Output)
	got, err = DecodeEvidence(raw)
	if err != nil || !bytes.Equal(got, outb) {
		t.Fatalf("output: %v %q", err, got)
	}
}

func TestUsageIsValidated(t *testing.T) {
	for _, u := range []Usage{{InputTokens: -1}, {CostUSD: -1, CostReported: true}, {CostUSD: 1}, {WallMillis: -5}} {
		if err := u.Validate(); err == nil {
			t.Fatalf("accepted %+v", u)
		}
	}
	if err := (Usage{InputTokens: 1, CostUSD: 0.5, CostReported: true}).Validate(); err != nil {
		t.Fatal(err)
	}
}

func TestKeysAndClasses(t *testing.T) {
	k1, k2 := DeriveKey("ab", 0), DeriveKey("ab", 1)
	if k1 == k2 || len(k1) != 64 || DeriveKey("ab", 0) != k1 {
		t.Fatal("keys")
	}
	if ClassOf("Read") != OpQuery || ClassOf("Write") != OpWriteLocal || ClassOf("Bash") != OpIrreversible || ClassOf("NeverHeardOfIt") != OpIrreversible {
		t.Fatal("operation table")
	}
}

// The stream-json parser as a protocol state machine.
func TestStreamParser(t *testing.T) {
	rec := &recordingSink{}
	p := newStreamParser(rec)
	lines := []string{
		`{"type":"system","subtype":"init"}`,
		`not json at all`,
		`{"type":"assistant","message":{"content":[{"type":"text","text":"hi"},{"type":"tool_use","id":"t1","name":"Read","input":{"path":"a.txt"}}]}}`,
		`{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":[{"type":"text","text":"file body"}],"is_error":false}]}}`,
		`{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t2","name":"Bash","input":{"command":"rm -rf x"}}]}}`,
		`{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}`,
		`{"type":"result","subtype":"success","is_error":false,"result":"all done","total_cost_usd":0.02,"usage":{"input_tokens":100,"output_tokens":20,"cache_read_input_tokens":80}}`,
	}
	for _, l := range lines {
		p.feed([]byte(l))
	}
	if len(rec.obs) != 2 || rec.obs[0].Op != "Read" || rec.obs[1].Op != "Bash" || len(rec.res) != 1 || string(rec.res[0].Output) != "file body" {
		t.Fatalf("obs %+v res %+v", rec.obs, rec.res)
	}
	if p.result == nil || p.result.Result != "all done" || p.result.Usage.CacheRead != 80 || p.result.CostUSD == nil || p.violations != 0 || p.rateLimited {
		t.Fatalf("result %+v violations=%d", p.result, p.violations)
	}
	// after the result: a frame is a violation; a second result is fatal
	p.feed([]byte(`{"type":"assistant","message":{"content":[]}}`))
	p.feed([]byte(`{"type":"result","subtype":"success","is_error":false,"result":"again"}`))
	if p.violations != 2 || !p.duplicateResult || p.result.Result != "all done" {
		t.Fatalf("post-result: violations=%d dup=%v", p.violations, p.duplicateResult)
	}
	// unmatched tool_result, empty tool name, undecodable JSON frame, absent cost
	p2 := newStreamParser(&recordingSink{})
	p2.feed([]byte(`{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"ghost","content":"x"}]}}`))
	p2.feed([]byte(`{"type":"assistant","message":{"content":[{"type":"tool_use","id":"","name":"","input":{}}]}}`))
	p2.feed([]byte(`{"type":"garbage" broken`))
	p2.feed([]byte(`{"type":"result","subtype":"error_during_execution","is_error":false,"result":"boom"}`))
	if p2.violations != 3 || p2.result == nil || p2.result.Subtype != "error_during_execution" || p2.result.CostUSD != nil {
		t.Fatalf("violations=%d result=%+v", p2.violations, p2.result)
	}
	// object-valued tool_result content stays JSON
	p3 := newStreamParser(&recordingSink{})
	p3.feed([]byte(`{"type":"assistant","message":{"content":[{"type":"tool_use","id":"x","name":"Glob","input":{}}]}}`))
	p3.feed([]byte(`{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"x","content":{"k":1}}]}}`))
	if got := p3.sink.(*recordingSink).res[0].Output; string(got) != `{"k":1}` {
		t.Fatalf("%q", got)
	}
}

type recordingSink struct {
	obs []EffectEvent
	res []EffectResult
}

func (r *recordingSink) Observe(ev EffectEvent) (int, string, error) {
	r.obs = append(r.obs, ev)
	return len(r.obs) - 1, "k", nil
}
func (r *recordingSink) Result(res EffectResult) error { r.res = append(r.res, res); return nil }

func TestSubprocessArgs(t *testing.T) {
	s := &Subprocess{Bin: "claude", Model: "sonnet"}
	a := strings.Join(s.args(Request{Tools: false}), " ")
	if !strings.Contains(a, "--tools ") || strings.Contains(a, "disallowedTools") || !strings.Contains(a, "--output-format stream-json") || !strings.Contains(a, "--model sonnet") {
		t.Fatalf("tool-less args: %s", a)
	}
	b := strings.Join(s.args(Request{Tools: true}), " ")
	if !strings.Contains(b, "--disallowedTools WebFetch,WebSearch") || strings.Contains(b, "--tools ") {
		t.Fatalf("tool args: %s", b)
	}
	caps := s.Capabilities()
	if !caps.ActsOutward || caps.OutwardReconcilable || !caps.ReadsByReference {
		t.Fatalf("caps %+v", caps)
	}
}

func writeFake(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte("#!/bin/sh\n"+body), 0o755); err != nil {
		t.Fatal(err)
	}
	return p
}

// Fake CLIs exercise the real subprocess path: the happy stream; a stream
// that ends after a tool_use (observed, no result, terminal failed, no
// receipt); a subtype error; a result followed by more frames (partial); a
// huge line (failed, no deadlock); stderr-only output (failed, transcript
// kept); the prompt delivered whole on stdin.
func TestSubprocessWithFakeCLI(t *testing.T) {
	dir := t.TempDir()
	ok := writeFake(t, dir, "ok", `cat >/dev/null
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Write","input":{"path":"out.txt"}}]}}'
echo '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"wrote it","total_cost_usd":0.001,"usage":{"input_tokens":5,"output_tokens":2}}'
`)
	sh, _ := newShell(t)
	b := &Subprocess{Bin: ok, Model: "sonnet", DefaultTimeout: 10 * time.Second}
	out, err := sh.Invoke(ctxBg, b, execReq("write a file"), nil)
	if err != nil {
		t.Fatal(err)
	}
	if out.Terminal != TerminalComplete || string(out.Response) != "wrote it" || len(out.Effects) != 1 || out.Usage.InputTokens != 5 || !out.Usage.CostReported {
		t.Fatalf("%+v", out)
	}
	s := foldOne(t, sh, out.Invocation)
	if s.Effects[0].Op != "Write" || s.Effects[0].Class != OpWriteLocal || s.Effects[0].Announced || s.Results[0] == nil || s.Terminal.Transcript == nil {
		t.Fatalf("%+v", s.Effects[0])
	}
	if tr, err := sh.Store.Get(*s.Terminal.Transcript); err != nil || !strings.Contains(string(tr), `"type":"result"`) {
		t.Fatalf("transcript not kept: %v", err)
	}

	dies := writeFake(t, dir, "dies", `cat >/dev/null
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t9","name":"Bash","input":{}}]}}'
exit 3
`)
	out2, err := sh.Invoke(ctxBg, &Subprocess{Bin: dies, DefaultTimeout: 10 * time.Second}, execReq("x"), nil)
	if err != nil || out2.Terminal != TerminalFailed || out2.Receipt != "" || len(out2.Effects) != 1 || !strings.Contains(out2.Reason, "exit") {
		t.Fatalf("%v %+v", err, out2)
	}
	s2 := foldOne(t, sh, out2.Invocation)
	if len(s2.Unanswered()) != 1 || len(s2.Results) != 0 {
		t.Fatal("an unanswered tool_use is observed, outcome unknown — not an error result")
	}

	suberr := writeFake(t, dir, "suberr", `cat >/dev/null
echo '{"type":"result","subtype":"error_max_turns","is_error":false,"result":"gave up"}'
`)
	out3, _ := sh.Invoke(ctxBg, &Subprocess{Bin: suberr, DefaultTimeout: 10 * time.Second}, execReq("x"), nil)
	if out3.Terminal != TerminalFailed || !strings.Contains(out3.Reason, "error_max_turns") {
		t.Fatalf("%+v", out3)
	}

	trailing := writeFake(t, dir, "trailing", `cat >/dev/null
echo '{"type":"result","subtype":"success","is_error":false,"result":"first"}'
echo '{"type":"assistant","message":{"content":[]}}'
`)
	out4, _ := sh.Invoke(ctxBg, &Subprocess{Bin: trailing, DefaultTimeout: 10 * time.Second}, execReq("x"), nil)
	if out4.Terminal != TerminalPartial || string(out4.Response) != "first" || !strings.Contains(out4.Reason, "violation") {
		t.Fatalf("%+v", out4)
	}

	huge := writeFake(t, dir, "huge", `cat >/dev/null
head -c 70000000 /dev/zero | tr '\0' 'x'
echo
echo '{"type":"result","subtype":"success","is_error":false,"result":"late"}'
`)
	out5, _ := sh.Invoke(ctxBg, &Subprocess{Bin: huge, DefaultTimeout: 30 * time.Second}, execReq("x"), nil)
	if out5.Terminal != TerminalFailed || !strings.Contains(out5.Reason, "stream") {
		t.Fatalf("huge line: %+v", out5)
	}

	stderrOnly := writeFake(t, dir, "stderr", `cat >/dev/null
echo 'Not logged in' 1>&2
exit 1
`)
	out6, _ := sh.Invoke(ctxBg, &Subprocess{Bin: stderrOnly, DefaultTimeout: 10 * time.Second}, execReq("x"), nil)
	s6 := foldOne(t, sh, out6.Invocation)
	if out6.Terminal != TerminalFailed || s6.Terminal.Transcript == nil {
		t.Fatalf("%+v", out6)
	}
	if tr, _ := sh.Store.Get(*s6.Terminal.Transcript); !strings.Contains(string(tr), "Not logged in") {
		t.Fatal("stderr not in transcript")
	}

	echo := writeFake(t, dir, "echo", `n=$(wc -c)
echo "{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"result\":\"$n\"}"
`)
	big := bytes.Repeat([]byte("p"), 3<<20)
	out7, _ := sh.Invoke(ctxBg, &Subprocess{Bin: echo, DefaultTimeout: 10 * time.Second}, Request{Purpose: PurposeExecute, Prompt: big, Tools: true}, nil)
	if strings.TrimSpace(string(out7.Response)) != "3145728" {
		t.Fatalf("prompt not delivered whole on stdin: %q", out7.Response)
	}
}

func TestLiveSubprocessSmoke(t *testing.T) {
	if !LiveAvailable() {
		t.Skip("set MARO_GO_LIVE=1 with the claude CLI logged in to run the live smoke")
	}
	sh, _ := newShell(t)
	b, err := NewSubprocess("haiku")
	if err != nil {
		t.Fatal(err)
	}
	out, err := sh.Invoke(ctxBg, b, Request{Purpose: PurposeJudge, Prompt: []byte("Reply with exactly the word PONG and nothing else."), Tools: false, Timeout: 3 * time.Minute}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if out.Terminal == TerminalFailed || !strings.Contains(strings.ToUpper(string(out.Response)), "PONG") {
		t.Fatalf("%+v", out)
	}
	raw, _ := json.Marshal(out.Usage)
	t.Logf("live usage: %s", raw)
}

// Confinement is structural at the shell: a backend that reports an
// effect on a tool-less request has the effect recorded as REFUSED and
// the invocation fails — no receipt, no retry, whatever the backend says.
func TestToolLessRequestsRefuseEffects(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: outward, Calls: []ScriptedCall{{Response: []byte("wrote it"), Effects: []ScriptedEffect{{Op: "Write", Input: []byte(`{"path":"x"}`), Output: []byte("ok")}}}}}
	out, err := sh.Invoke(ctxBg, b, Request{Purpose: PurposeExecute, Prompt: []byte("do"), Tools: false}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if out.Terminal != TerminalFailed || !strings.Contains(out.Reason, "confined") || out.Receipt != "" {
		t.Fatalf("%+v", out)
	}
	s := foldOne(t, sh, out.Invocation)
	if len(s.Effects) != 1 || !s.Effects[0].Refused || s.Invocation.Tools {
		t.Fatalf("effect not recorded as refused: %+v tools=%v", s.Effects, s.Invocation.Tools)
	}
	// a query effect on a tool-less request is refused too: confined means no effects at all
	b2 := &Scripted{Caps: outward, Calls: []ScriptedCall{{Response: []byte("read it"), Effects: []ScriptedEffect{{Op: "Read", Input: []byte(`{}`), Output: []byte("x")}}}}}
	out2, _ := sh.Invoke(ctxBg, b2, Request{Purpose: PurposeExecute, Prompt: []byte("do"), Tools: false}, nil)
	if out2.Terminal != TerminalFailed {
		t.Fatalf("query effect accepted under confinement: %+v", out2)
	}
	// the same backend with tools offered succeeds
	b3 := &Scripted{Caps: outward, Calls: []ScriptedCall{{Response: []byte("wrote it"), Effects: []ScriptedEffect{{Op: "Write", Input: []byte(`{"path":"x"}`), Output: []byte("ok")}}}}}
	out3, _ := sh.Invoke(ctxBg, b3, Request{Purpose: PurposeExecute, Prompt: []byte("do"), Tools: true}, nil)
	if out3.Terminal != TerminalComplete || len(out3.Effects) != 1 {
		t.Fatalf("%+v", out3)
	}
}
