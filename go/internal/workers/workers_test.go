package workers

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

func TestDispatchDeliverResult(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		`{"tool": "deliver_result", "result": "the finished artifact", "summary": "done"}`,
	}}
	res := Dispatch(context.Background(), fake, Build, "write the thing", "", false)
	if res.Status != "done" || res.Result != "the finished artifact" {
		t.Fatalf("deliver_result must land as done+result: %+v", res)
	}
	if res.TokensIn != 10 || res.TokensOut != 5 {
		t.Fatalf("usage must be carried: %+v", res)
	}
	if res.BlockedOrigin != "" {
		t.Fatalf("done results carry no blocked origin: %+v", res)
	}
	// The worker call must offer the tool protocol and the persona.
	if len(fake.Opts) != 1 || len(fake.Opts[0].Tools) != 2 {
		t.Fatalf("worker call must offer deliver_result+flag_blocked: %+v", fake.Opts)
	}
	if !strings.Contains(fake.Prompts[0], "Build Worker") {
		t.Fatalf("persona must frame the call: %q", fake.Prompts[0][:120])
	}
}

// TestDispatchDeliverResultNonString: a non-string result argument is
// JSON-encoded, not dropped — and encoded the way Python encodes it.
//
// The comment on this test said "json.dumps parity" while the assertion
// pinned `"k":"v"`, which is encoding/json's spelling and not json.dumps'.
// The production comment said the same thing over the same wrong call.
// This value becomes a worker RESULT and flows onward into prompts, so
// the two runtimes were carrying different bytes (adversarial mission-r8).
func TestDispatchDeliverResultNonString(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		`{"tool": "deliver_result", "result": {"k": "a > b", "z": 1, "a": 2}, "summary": "done"}`,
	}}
	res := Dispatch(context.Background(), fake, General, "t", "", false)
	if res.Status != "done" {
		t.Fatalf("object result must arrive JSON-encoded: %+v", res)
	}
	// json.dumps' separators, no HTML escaping, and sorted keys — which is
	// what FromPlain gives a decoded map, whose Python insertion order was
	// already gone before this code saw it.
	const want = `{"a": 2, "k": "a > b", "z": 1}`
	if res.Result != want {
		t.Fatalf("result is not json.dumps' bytes:\n got %s\nwant %s", res.Result, want)
	}
	// Anti-vacuity: the pre-fix encoder, required to lose on this fixture.
	old, err := json.Marshal(map[string]any{"k": "a > b", "z": 1.0, "a": 2.0})
	if err != nil {
		t.Fatal(err)
	}
	if string(old) == want {
		t.Fatal("encoding/json already agrees on this fixture: the test cannot " +
			"show the fork it was written for")
	}
}

func TestDispatchFlagBlockedIsWorkerAuthored(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		`{"tool": "flag_blocked", "reason": "the input file was not provided", "partial": "started outline"}`,
	}}
	res := Dispatch(context.Background(), fake, Research, "summarize the doc", "", false)
	if res.Status != "blocked" || res.BlockedOrigin != "worker" {
		t.Fatalf("flag_blocked must be blocked(worker): %+v", res)
	}
	if res.StuckReason != "the input file was not provided" || res.Result != "started outline" {
		t.Fatalf("reason+partial must survive: %+v", res)
	}
	if !DelegationGap(res.StuckReason) {
		t.Fatalf("provision-shaped reason must classify as delegation gap")
	}
}

// errAdapter fails every call with a ResultError carrying real usage.
type errAdapter struct{}

func (errAdapter) Name() string { return "err" }
func (errAdapter) Complete(context.Context, []llm.Message, llm.Options) (*llm.Response, error) {
	// Big-seed values: larger than any organic count so the salvage
	// pins can't pass vacuously (routing-tranche M21 lesson).
	return nil, &llm.ResultError{Msg: "boom", TokensIn: 1000003, TokensOut: 500001}
}

// TestDispatchAdapterFailureSalvagesUsage: a failed call is
// blocked(adapter) — never worker-authored — and its spend survives.
func TestDispatchAdapterFailureSalvagesUsage(t *testing.T) {
	res := Dispatch(context.Background(), errAdapter{}, Ops, "restart it", "", false)
	if res.Status != "blocked" || res.BlockedOrigin != "adapter" {
		t.Fatalf("adapter failure must be blocked(adapter): %+v", res)
	}
	if res.TokensIn != 1000003 || res.TokensOut != 500001 {
		t.Fatalf("ResultError usage must be salvaged: %+v", res)
	}
	// The adapter-authored reason pattern-matches the delegation-gap
	// keywords by construction in Python's incident ("LLM call failed:
	// no access…") — origin scoping is what keeps the corpus clean, so
	// the origin must NOT be "worker" no matter the text.
	if !strings.HasPrefix(res.StuckReason, "LLM call failed:") {
		t.Fatalf("adapter failures carry the LLM-call-failed frame: %+v", res)
	}
}

func TestDispatchBareContentFallback(t *testing.T) {
	long := &llm.Fake{Script: []string{"here is a bare prose answer with no tool call at all"}}
	res := Dispatch(context.Background(), long, General, "t", "", false)
	if res.Status != "done" || !strings.Contains(res.Result, "bare prose answer") {
		t.Fatalf(">20-char bare content must land as done: %+v", res)
	}

	short := &llm.Fake{Script: []string{"nope"}}
	res = Dispatch(context.Background(), short, General, "t", "", false)
	if res.Status != "blocked" || res.BlockedOrigin != "empty" {
		t.Fatalf("short bare content must be blocked(empty): %+v", res)
	}
}

func TestDispatchDryAndUnknownType(t *testing.T) {
	res := Dispatch(context.Background(), nil, "sorcerer", "conjure", "", true)
	if res.WorkerType != General {
		t.Fatalf("unknown worker type must coerce to general: %+v", res)
	}
	if res.Status != "done" || !strings.Contains(res.Result, "[dry-run:general]") {
		t.Fatalf("dry worker must stub deterministically: %+v", res)
	}
	if res.TokensIn != 60 || res.TokensOut != 40 {
		t.Fatalf("dry stub usage is part of the Python contract: %+v", res)
	}
}

func TestInferType(t *testing.T) {
	cases := map[string]string{
		"research the market for widgets":   Research,
		"implement the parser in Go":        Build,
		"deploy the service and monitor it": Ops,
		"hello there":                       General,
		// tie between research and build breaks research-first
		// (Python's if-chain order).
		"review and implement the fix": Research,
	}
	for ticket, want := range cases {
		if got := InferType(ticket); got != want {
			t.Fatalf("InferType(%q) = %q, want %q", ticket, got, want)
		}
	}
}

func TestDelegationGapScopes(t *testing.T) {
	if DelegationGap("stack overflow in the parser") {
		t.Fatalf("execution failure must not classify as delegation gap")
	}
	if DelegationGap("") || DelegationGap("   ") {
		t.Fatalf("empty reason is not a gap")
	}
	if !DelegationGap("the URL was not specified anywhere") {
		t.Fatalf("provision-shaped reason must classify")
	}
}

// TestDispatchBareContentGateCountsRunes: the >20 bar counts runes
// (Python len parity) — 8 CJK characters are 24 bytes but 8 runes and
// must be refused as thin content (adversarial director r1, QA:
// byte-counting was lenient on a refusal gate).
func TestDispatchBareContentGateCountsRunes(t *testing.T) {
	fake := &llm.Fake{Script: []string{"日本語出力八文字"}}
	res := Dispatch(context.Background(), fake, General, "t", "", false)
	if res.Status != "blocked" || res.BlockedOrigin != "empty" {
		t.Fatalf("8-rune content must be blocked(empty): %+v", res)
	}
}
