package invoke

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
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

func reopen(t *testing.T, sh *Shell, l *workspace.Lease) *Shell {
	t.Helper()
	sh.J.Close()
	j, err := journal.Open(l)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { j.Close() })
	return &Shell{J: j, Store: sh.Store, Run: sh.Run, Attempt: sh.Attempt + 1}
}

var toolless = Capabilities{Name: "scripted", Model: "m", ActsOutward: false}
var outward = Capabilities{Name: "scripted", Model: "m", ActsOutward: true, OutwardReconcilable: false}

// The whole chain, in order, each transition a record; the response comes
// back whole and its receipt names the request.
func TestInvokeCommitsEveryTransition(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: outward, Calls: []ScriptedCall{{
		Response: []byte("done: 3 files"),
		Effects: []EffectEvent{
			{Op: "Read", Input: []byte(`{"path":"a"}`), Output: []byte("contents")},
			{Op: "Bash", Input: []byte(`{"cmd":"ls"}`), Output: []byte("a b"), IsError: false},
			{Op: "FrobnicateAPI", Input: []byte(`{}`), Output: []byte("ok")},
		},
		Usage: Usage{InputTokens: 10, OutputTokens: 5, CostUSD: 0.01},
	}}}
	out, err := sh.Invoke(context.Background(), b, Request{Purpose: PurposeExecute, Prompt: []byte("do the thing"), Tools: true}, &Target{Name: "step", Limit: 1000, Why: "measured p90"})
	if err != nil {
		t.Fatal(err)
	}
	if string(out.Response) != "done: 3 files" || out.Terminal != TerminalComplete || len(out.Effects) != 3 || out.Receipt == "" {
		t.Fatalf("%+v", out)
	}
	states, err := Fold(sh.J.Production())
	if err != nil {
		t.Fatal(err)
	}
	s := states[out.Invocation]
	if s.Phase() != "receipt_committed" || !s.Dispatched || s.Terminal.State != TerminalComplete || s.Receipt.Usage.CostUSD != 0.01 {
		t.Fatalf("state %s %+v", s.Phase(), s)
	}
	// effects: classes from the registered table, keys derived, ordered, post-hoc for a non-reconcilable backend
	want := []OperationClass{OpQuery, OpIrreversible, OpIrreversible}
	for i, e := range s.Effects {
		if e.Ordinal != i || e.Class != want[i] || e.Key != DeriveKey(s.Invocation.EffectToken, i) || e.Announced {
			t.Fatalf("effect %d: %+v", i, e)
		}
		if got, err := sh.Store.Get(e.Evidence); err != nil || !strings.Contains(string(got), `"op"`) {
			t.Fatalf("evidence %d: %v", i, err)
		}
	}
	// the request and response are whole thoughts
	if got, _ := sh.Store.Get(s.Invocation.Request); string(got) != "do the thing" {
		t.Fatal("request not stored whole")
	}
	if got, _ := sh.Store.Get(s.Receipt.Response); string(got) != "done: 3 files" {
		t.Fatal("response not stored whole")
	}
	if s.Invocation.TargetWhy == "" || len(b.Seen) != 1 || b.Seen[0].Purpose != PurposeExecute {
		t.Fatalf("target/purpose not carried: %+v %+v", s.Invocation, b.Seen)
	}
}

func TestInvokeRefusals(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: toolless, Calls: []ScriptedCall{{Response: []byte("x")}}}
	if _, err := sh.Invoke(context.Background(), b, Request{Purpose: "dance", Prompt: []byte("p")}, nil); err == nil {
		t.Fatal("bad purpose accepted")
	}
	if _, err := sh.Invoke(context.Background(), b, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, &Target{Name: "t", Limit: 1}); !errors.Is(err, ErrTargetWhy) {
		t.Fatalf("target without why: %v", err)
	}
	if sh.J.Head() != 0 {
		t.Fatal("a refused invoke wrote records")
	}
}

// A backend that fails before dispatch leaves a terminal=failed, never an
// orphan; a failed stream leaves terminal=failed with no receipt; a partial
// stream leaves terminal=partial WITH a receipt.
func TestTerminalStates(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: outward, Calls: []ScriptedCall{
		{FailBefore: true},
		{Terminal: TerminalFailed, Reason: "cli exit 1"},
		{Response: []byte("r"), Terminal: TerminalPartial, Reason: "2 malformed frames"},
	}}
	ctx := context.Background()
	out, err := sh.Invoke(ctx, b, Request{Purpose: PurposeExecute, Prompt: []byte("a"), Tools: true}, nil)
	if !errors.Is(err, ErrBeforeDispatch) || out.Terminal != TerminalFailed {
		t.Fatalf("before-dispatch: %v %+v", err, out)
	}
	out2, err := sh.Invoke(ctx, b, Request{Purpose: PurposeExecute, Prompt: []byte("b"), Tools: true}, nil)
	if err != nil || out2.Terminal != TerminalFailed || out2.Receipt != "" {
		t.Fatalf("failed stream: %v %+v", err, out2)
	}
	out3, err := sh.Invoke(ctx, b, Request{Purpose: PurposeExecute, Prompt: []byte("c"), Tools: true}, nil)
	if err != nil || out3.Terminal != TerminalPartial || out3.Receipt == "" || string(out3.Response) != "r" {
		t.Fatalf("partial stream: %v %+v", err, out3)
	}
	states, _ := Fold(sh.J.Production())
	for _, id := range []record.RecordID{out.Invocation, out2.Invocation, out3.Invocation} {
		if states[id].Terminal == nil {
			t.Fatalf("%s has no terminal", id)
		}
	}
	// nothing is orphaned: reconcile finds no work
	rc, err := Reconcile(ctx, sh)
	if err != nil || len(rc) != 0 {
		t.Fatalf("reconcile: %v %v", rc, err)
	}
}

// Kill points: dispatched-without-terminal on a tool-less backend is
// abandoned (safe to retry); on an outward-capable, non-reconcilable backend
// it is indeterminate — even with zero committed effects, because the frame
// may have died with the process. Reconcile is idempotent.
func TestReconcileDispositions(t *testing.T) {
	sh, l := newShell(t)
	ctx := context.Background()
	mk := func(caps Capabilities, effects int) record.RecordID {
		reqRef, _ := sh.Store.Put(thought.Prompt, []byte("p"))
		tok, _ := NewEffectToken()
		inv := &Invocation{Header: sh.header(record.Ref{Kind: "prompt", ID: reqRef.Hash}), Purpose: PurposeExecute, Request: reqRef, Backend: caps, EffectToken: tok}
		if _, err := sh.commit(ctx, string(inv.ID)+":prepared", inv); err != nil {
			t.Fatal(err)
		}
		if _, err := sh.commit(ctx, string(inv.ID)+":dispatched", &Dispatched{Header: sh.header(subj(inv.ID)), Invocation: inv.ID}); err != nil {
			t.Fatal(err)
		}
		sk := &sink{sh: sh, inv: inv}
		for i := 0; i < effects; i++ {
			if _, err := sk.Effect(ctx, EffectEvent{Op: "Read", Input: []byte(`{}`), Output: []byte("x")}); err != nil {
				t.Fatal(err)
			}
		}
		return inv.ID
	}
	// prepared only (never dispatched): not orphaned, left alone
	reqRef, _ := sh.Store.Put(thought.Prompt, []byte("q"))
	tok, _ := NewEffectToken()
	prepOnly := &Invocation{Header: sh.header(record.Ref{Kind: "prompt", ID: reqRef.Hash}), Purpose: PurposeJudge, Request: reqRef, Backend: toolless, EffectToken: tok}
	sh.commit(ctx, string(prepOnly.ID)+":prepared", prepOnly)
	a := mk(toolless, 0)
	b := mk(outward, 0)
	c := mk(outward, 2)
	// "crash": reopen the journal as a new attempt and reconcile
	sh2 := reopen(t, sh, l)
	rc, err := Reconcile(ctx, sh2)
	if err != nil {
		t.Fatal(err)
	}
	got := map[record.RecordID]Disposition{}
	for _, r := range rc {
		got[r.Invocation] = r.Disposition
	}
	if got[a] != DispositionAbandoned || got[b] != DispositionIndeterminate || got[c] != DispositionIndeterminate || len(got) != 3 {
		t.Fatalf("dispositions %v", got)
	}
	if _, ok := got[prepOnly.ID]; ok {
		t.Fatal("a prepared-only invocation is not an orphan")
	}
	rc2, _ := Reconcile(ctx, sh2)
	if len(rc2) != 0 {
		t.Fatal("reconcile not idempotent")
	}
	states, _ := Fold(sh2.J.Production())
	if states[c].Phase() != "reconciled:indeterminate_external_effect" || len(states[c].Effects) != 2 {
		t.Fatalf("%s", states[c].Phase())
	}
}

// A hung backend is cut by the context and leaves terminal=failed.
func TestTimeoutIsATerminal(t *testing.T) {
	sh, _ := newShell(t)
	b := &Scripted{Caps: toolless, Calls: []ScriptedCall{{Hang: true}}}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	out, err := sh.Invoke(ctx, b, Request{Purpose: PurposeJudge, Prompt: []byte("p")}, nil)
	if err != nil || out.Terminal != TerminalFailed || !strings.Contains(out.Reason, "deadline") {
		t.Fatalf("%v %+v", err, out)
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

// The stream-json parser, against the CLI's event shapes (the same shapes the
// Python adapter reconstructs): tool_use in assistant events, tool_result in
// user events, a result event; noise lines tolerated; a use with no result
// flushed as an error; malformed frames after the result count as partial.
func TestStreamParser(t *testing.T) {
	lines := []string{
		`{"type":"system","subtype":"init"}`,
		`not json at all`,
		`{"type":"assistant","message":{"content":[{"type":"text","text":"hi"},{"type":"tool_use","id":"t1","name":"Read","input":{"path":"a.txt"}}]}}`,
		`{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":[{"type":"text","text":"file body"}],"is_error":false}]}}`,
		`{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t2","name":"Bash","input":{"command":"rm -rf x"}}]}}`,
		`{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}`,
		`{"type":"result","subtype":"success","is_error":false,"result":"all done","total_cost_usd":0.02,"usage":{"input_tokens":100,"output_tokens":20,"cache_read_input_tokens":80}}`,
		`{"type":"garbage" broken`,
	}
	p := newStreamParser()
	var evs []EffectEvent
	for _, l := range lines {
		evs = append(evs, p.feed([]byte(l))...)
	}
	evs = append(evs, p.flush()...)
	if len(evs) != 2 || evs[0].Op != "Read" || string(evs[0].Output) != "file body" || evs[1].Op != "Bash" || !evs[1].IsError {
		t.Fatalf("%+v", evs)
	}
	if p.result == nil || p.result.Result != "all done" || p.result.Usage.CacheRead != 80 || p.malformed != 1 || p.rateLimited {
		t.Fatalf("result %+v malformed=%d", p.result, p.malformed)
	}
	// an error result: subtype success + is_error true is NOT success
	p2 := newStreamParser()
	p2.feed([]byte(`{"type":"result","subtype":"success","is_error":true,"result":"Not logged in"}`))
	if p2.result == nil || !p2.result.IsError {
		t.Fatal("error result not parsed")
	}
	// string tool_result content
	p3 := newStreamParser()
	p3.feed([]byte(`{"type":"assistant","message":{"content":[{"type":"tool_use","id":"x","name":"Glob","input":{}}]}}`))
	ev := p3.feed([]byte(`{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"x","content":"plain"}]}}`))
	if len(ev) != 1 || string(ev[0].Output) != "plain" {
		t.Fatalf("%+v", ev)
	}
}

// The CLI contract: flags per Request shape, prompt on stdin.
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

// A fake "claude" that emits a canned stream proves the whole subprocess
// path without spending tokens: dispatch, stream, effects, result, receipt.
func TestSubprocessWithFakeCLI(t *testing.T) {
	dir := t.TempDir()
	script := `#!/bin/sh
cat >/dev/null
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Write","input":{"path":"out.txt"}}]}}'
echo '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"ok"}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"wrote it","usage":{"input_tokens":5,"output_tokens":2}}'
`
	fake := filepath.Join(dir, "claude")
	if err := os.WriteFile(fake, []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	sh, _ := newShell(t)
	b := &Subprocess{Bin: fake, Model: "sonnet", DefaultTimeout: 10 * time.Second}
	out, err := sh.Invoke(context.Background(), b, Request{Purpose: PurposeExecute, Prompt: []byte("write a file"), Tools: true}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if out.Terminal != TerminalComplete || string(out.Response) != "wrote it" || len(out.Effects) != 1 || out.Usage.InputTokens != 5 {
		t.Fatalf("%+v", out)
	}
	states, _ := Fold(sh.J.Production())
	s := states[out.Invocation]
	if s.Effects[0].Op != "Write" || s.Effects[0].Class != OpWriteLocal || s.Effects[0].Announced || s.Terminal.Transcript == nil {
		t.Fatalf("%+v", s.Effects[0])
	}
	tr, err := sh.Store.Get(*s.Terminal.Transcript)
	if err != nil || !strings.Contains(string(tr), `"type":"result"`) {
		t.Fatalf("transcript not kept: %v", err)
	}
	// a fake that dies mid-stream: terminal=failed, effects kept as evidence, no receipt
	dying := filepath.Join(dir, "claude-dies")
	os.WriteFile(dying, []byte("#!/bin/sh\ncat >/dev/null\necho '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"id\":\"t9\",\"name\":\"Bash\",\"input\":{}}]}}'\nexit 3\n"), 0o755)
	b2 := &Subprocess{Bin: dying, DefaultTimeout: 10 * time.Second}
	out2, err := sh.Invoke(context.Background(), b2, Request{Purpose: PurposeExecute, Prompt: []byte("x"), Tools: true}, nil)
	if err != nil || out2.Terminal != TerminalFailed || out2.Receipt != "" || len(out2.Effects) != 1 || !strings.Contains(out2.Reason, "exit") {
		t.Fatalf("%v %+v", err, out2)
	}
	states, _ = Fold(sh.J.Production())
	if e := states[out2.Invocation].Effects[0]; !e.IsError || e.Class != OpIrreversible {
		t.Fatalf("unanswered tool use must be error evidence: %+v", e)
	}
}

// Live smoke: only when MARO_GO_LIVE=1 and the real CLI is present. Spends
// real tokens; a tool-less judge-purpose call.
func TestLiveSubprocessSmoke(t *testing.T) {
	if !LiveAvailable() {
		t.Skip("set MARO_GO_LIVE=1 with the claude CLI logged in to run the live smoke")
	}
	sh, _ := newShell(t)
	b, err := NewSubprocess("haiku")
	if err != nil {
		t.Fatal(err)
	}
	out, err := sh.Invoke(context.Background(), b, Request{Purpose: PurposeJudge, Prompt: []byte("Reply with exactly the word PONG and nothing else."), Tools: false, Timeout: 3 * time.Minute}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if out.Terminal == TerminalFailed || !strings.Contains(strings.ToUpper(string(out.Response)), "PONG") {
		t.Fatalf("%+v", out)
	}
	raw, _ := json.Marshal(out.Usage)
	t.Logf("live usage: %s", raw)
}
