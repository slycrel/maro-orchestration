package intent

import (
	"context"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// TestHeuristicClassifyMatchesCPython WAS HERE and has been replaced by
// the live differential in r5_diff_test.go (adversarial mission-r5
// follow-up). Its comment described its own weakness precisely —
// "fixtures generated 2026-08-22 from the live Python module" — a
// SNAPSHOT of CPython, which by construction cannot notice CPython
// moving, and cannot notice the port drifting toward a stale copy of it.
//
// It was green while heuristicClassify used strings.ToLower/TrimSpace/
// Fields against Python's str.lower/.strip/.split, which is a LANE flip
// on a Turkish dotted capital I. All ten of its cases were folded into
// heuristicCorpus, which runs the real _heuristic_classify.

// TestRequiresFileOutputMatchesCPython used to live here with six
// all-ASCII fixtures and hardcoded booleans. It could not fail: Go's
// ASCII `\w`/`\b` and Python's Unicode classes agree on every ASCII
// input, so the corpus could not separate the two engines — and the
// name told three review rounds the boundary was covered while
// `save the summary to caf\u00e9.md` routed to a different LANE on the
// two runtimes (adversarial mission-r6 MEDIUM, found by the LENS-1
// census of tests that cannot fail).
//
// The real differential is TestRequiresFileOutputMatchesCPython in
// r6_diff_test.go, which drives _requires_file_output itself.

// TestFileOutputOverrideWinsOverNowClassification: capability beats
// opinion — the burn-in batch-3 lesson (a file-deliverable goal
// answered inline is an honest negative in the wrong lane).
func TestFileOutputOverrideWinsOverNowClassification(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		`{"lane":"now","confidence":0.95,"reason":"looks trivial","needs_live_data":false,"introspects_self":false}`,
	}}
	r := Classify(context.Background(), fake, "summarize what comm does, saved to artifacts/comm-examples.md", false)
	if r.Lane != "agenda" || r.Confidence < 0.8 {
		t.Fatalf("file-deliverable NOW must flip to agenda: %+v", r)
	}
}

// TestLiveDataOverrideNoURLExemption: Go-stricter divergence, named in
// the package doc — nothing pre-fetches here, so a carried URL must NOT
// keep a live-data ask in the NOW lane (Python exempts it because its
// NOW lane fetches the link).
func TestLiveDataOverrideNoURLExemption(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		`{"lane":"now","confidence":0.9,"reason":"quick look","needs_live_data":true,"introspects_self":false}`,
	}}
	r := Classify(context.Background(), fake,
		"is this worth my time? https://example.com/post", false)
	if r.Lane != "agenda" || !r.NeedsLiveData {
		t.Fatalf("live-data ask with URL must still escalate (no pre-fetch): %+v", r)
	}
}

// TestClassifyFieldsFailOpen: string-typed booleans read correctly;
// malformed lane/confidence degrade to safe defaults.
func TestClassifyFieldsFailOpen(t *testing.T) {
	fake := &llm.Fake{Script: []string{
		`{"lane":"NOW","confidence":"0.85","reason":"r","needs_live_data":"false","introspects_self":"true"}`,
	}}
	r := Classify(context.Background(), fake, "what does HTTP 429 mean?", false)
	if r.Lane != "now" || r.Confidence != 0.85 {
		t.Fatalf("case/string drift must normalize: %+v", r)
	}
	if r.NeedsLiveData || !r.IntrospectsSelf {
		t.Fatalf("string bools must parse is-true-or-'true': %+v", r)
	}
	bad := &llm.Fake{Script: []string{
		`{"lane":"maybe","confidence":"NaN","needs_live_data":1}`,
	}}
	r2 := Classify(context.Background(), bad, "what does HTTP 429 mean?", false)
	if r2.Lane != "agenda" || r2.Confidence != 0.7 || r2.NeedsLiveData {
		t.Fatalf("malformed fields must fall to agenda/0.7/false: %+v", r2)
	}
}

// TestClassifyLLMFailureFallsBackToHeuristic: an errored classify call
// degrades to the keyword path, never to a crash or a silent agenda.
func TestClassifyLLMFailureFallsBackToHeuristic(t *testing.T) {
	r := Classify(context.Background(), &llm.Fake{}, "what time is it?", false)
	if r.Lane != "now" {
		t.Fatalf("exhausted-script adapter must fall back to heuristic: %+v", r)
	}
	r2 := Classify(context.Background(), nil, "research polymarket strategies", false)
	if r2.Lane != "agenda" {
		t.Fatalf("nil adapter must classify heuristically: %+v", r2)
	}
}

// resultErrAdapter refuses every call with a token-carrying
// llm.ResultError — the refused-but-billed shape.
type resultErrAdapter struct{}

func (resultErrAdapter) Complete(ctx context.Context, msgs []llm.Message, opts llm.Options) (*llm.Response, error) {
	return nil, &llm.ResultError{Msg: "refused", TokensIn: 13, TokensOut: 5}
}
func (resultErrAdapter) Name() string { return "resultErr" }

// TestClassifyRefusedCallSalvagesUsage: a refused-but-billed classify
// call still reports its spend on the heuristic-fallback Result
// (llm.ResultError doctrine; adversarial routing r1 2026-08-22, QA —
// intent was the unfixed half of a 3-vs-3 call-site split).
func TestClassifyRefusedCallSalvagesUsage(t *testing.T) {
	r := Classify(context.Background(), resultErrAdapter{}, "what time is it?", false)
	if r.Lane != "now" {
		t.Fatalf("refused call must fall back to heuristic: %+v", r)
	}
	if r.TokensIn != 13 || r.TokensOut != 5 {
		t.Fatalf("refused call's spend must be salvaged: %+v", r)
	}
}
