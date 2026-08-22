package intent

import (
	"context"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// TestHeuristicClassifyMatchesCPython pins the fallback classifier
// against intent._heuristic_classify (fixtures generated 2026-08-22
// from the live Python module).
func TestHeuristicClassifyMatchesCPython(t *testing.T) {
	cases := []struct {
		msg  string
		lane string
		conf float64
	}{
		{"what time is it?", "now", 0.80},
		{"write a haiku", "now", 0.80},
		{"translate this to Spanish please my friend, it is very important to me", "now", 0.65},
		{"research winning polymarket strategies", "agenda", 0.65},
		{"build a research report on solar adoption and then summarize it", "agenda", 0.90},
		{"fix the login bug", "now", 0.65},
		{"what's the current BTC price?", "agenda", 0.65},
		{"summarize this paragraph", "now", 0.80},
		{"monitor my competitor pricing and compare it against ours", "agenda", 0.80},
		{"hello", "now", 0.65},
	}
	for _, c := range cases {
		lane, conf, _ := heuristicClassify(c.msg, nil)
		if lane != c.lane || conf < c.conf-0.001 || conf > c.conf+0.001 {
			t.Errorf("heuristicClassify(%q) = (%s, %.2f), want CPython (%s, %.2f)",
				c.msg, lane, conf, c.lane, c.conf)
		}
	}
}

// TestRequiresFileOutputMatchesCPython pins the capability override's
// trigger regex.
func TestRequiresFileOutputMatchesCPython(t *testing.T) {
	cases := []struct {
		msg  string
		want bool
	}{
		{"save the summary to notes.md", true},
		{"write it to a file", true},
		{"export results as csv files", true},
		{"put it in artifacts/report.md", true},
		{"summarize the article", false},
		{"review the file handling code", false},
	}
	for _, c := range cases {
		if got := RequiresFileOutput(c.msg); got != c.want {
			t.Errorf("RequiresFileOutput(%q) = %v, want CPython %v", c.msg, got, c.want)
		}
	}
}

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
