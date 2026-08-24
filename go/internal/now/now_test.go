package now

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func readRows(t *testing.T, path string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var rows []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var row map[string]any
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, row)
	}
	return rows
}

// TestRunNowFulfilledStampsAchieved: answer → judge fulfilled →
// goal_achieved true on the outcome row AND the run metadata; run dir
// finalized done.
func TestRunNowFulfilledStampsAchieved(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{
		"HTTP 429 means too many requests — the client is being rate limited.",
		`{"fulfilled": true}`,
	}}
	res, err := Run(context.Background(), fake, record.New(ws), "what does HTTP 429 mean?", false, "", 0, 0)
	if err != nil || res.Status != "done" {
		t.Fatalf("run: %v %+v", err, res)
	}
	if res.GoalAchieved == nil || !*res.GoalAchieved {
		t.Fatalf("fulfilled verdict must stamp true: %+v", res)
	}
	rows := readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	row := rows[len(rows)-1]
	if row["task_type"] != "now" || row["goal_achieved"] != true {
		t.Fatalf("outcome row: %v", row)
	}
	raw, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "metadata.json"))
	if rerr != nil {
		t.Fatal(rerr)
	}
	var meta map[string]any
	if err := json.Unmarshal(raw, &meta); err != nil {
		t.Fatal(err)
	}
	if meta["goal_achieved"] != true || meta["goal_verdict_source"] != "go_now_verify_v1" ||
		meta["status"] != "done" {
		t.Fatalf("run metadata: %v", meta)
	}
}

// TestRunNowNonFulfillmentDemotes: the judge's false demotes status to
// incomplete with the why carried — including the NON-ANSWER class.
func TestRunNowNonFulfillmentDemotes(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{
		"You could try searching online map services for gas stations in that area.",
		`{"fulfilled": false, "why": "asked WHERE, response names no place — generic how-to-find-it guidance"}`,
	}}
	res, err := Run(context.Background(), fake, record.New(ws), "where can I get gas near Manti, Utah?", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "incomplete" || res.GoalAchieved == nil || *res.GoalAchieved {
		t.Fatalf("non-answer must demote: %+v", res)
	}
	if !strings.Contains(res.VerdictSummary, "names no place") {
		t.Fatalf("judge's why must be carried: %q", res.VerdictSummary)
	}
	rows := readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	row := rows[len(rows)-1]
	if row["status"] != "incomplete" || row["goal_achieved"] != false ||
		row["goal_verdict_summary"] == nil {
		t.Fatalf("outcome row: %v", row)
	}
}

// errOnCall wraps an adapter and errors from call N on — llm.Fake
// clamps to its last script entry when exhausted rather than erroring,
// so a genuine transport failure needs this wrapper.
type errOnCall struct {
	llm.Adapter
	failFrom int
	calls    int
}

func (e *errOnCall) Complete(ctx context.Context, msgs []llm.Message, opts llm.Options) (*llm.Response, error) {
	e.calls++
	if e.calls >= e.failFrom {
		return nil, context.DeadlineExceeded
	}
	return e.Adapter.Complete(ctx, msgs, opts)
}

// TestRunNowJudgeErrorFailsOpenMarked: an errored judge keeps the done
// status, stamps NOTHING (absence = not judged), and MARKS the error —
// a broken verdict pipe must not look like a deliberately unjudged run
// (Python review F7).
func TestRunNowJudgeErrorFailsOpenMarked(t *testing.T) {
	ws := t.TempDir()
	fake := &errOnCall{Adapter: &llm.Fake{Script: []string{"the answer"}}, failFrom: 2}
	res, err := Run(context.Background(), fake, record.New(ws), "quick question?", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || res.GoalAchieved != nil {
		t.Fatalf("errored judge must fail open unjudged: %+v", res)
	}
	if res.NowVerifyError == "" {
		t.Fatalf("errored judge must be MARKED: %+v", res)
	}
	rows := readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	if _, has := rows[len(rows)-1]["goal_achieved"]; has {
		t.Fatalf("unjudged row must carry no goal_achieved key: %v", rows[len(rows)-1])
	}
}

// TestRunNowUnparseableVerdictUnjudged: judge prose without JSON leaves
// the tri-state absent (Python: extract_json miss → no verdict branch).
func TestRunNowUnparseableVerdictUnjudged(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{"the answer", "Sure! Looks fine to me."}}
	res, err := Run(context.Background(), fake, record.New(ws), "quick question?", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.GoalAchieved != nil || res.Status != "done" || res.NowVerifyError != "" {
		t.Fatalf("no-verdict must stay unjudged and unmarked: %+v", res)
	}
}

// TestRunNowEmptyAnswerRecordsPlaceholder: an empty completion records
// "[no response]" — never an empty result masquerading as an answer.
func TestRunNowEmptyAnswerRecordsPlaceholder(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{"   ", `{"fulfilled": false, "why": "empty response"}`}}
	res, err := Run(context.Background(), fake, record.New(ws), "say something", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Answer != "[no response]" {
		t.Fatalf("empty content must record the placeholder: %q", res.Answer)
	}
}

// --- routing r1 pins (adversarial 2026-08-22) --------------------------

// TestRunNowStampsNoConfidenceKey: the NOW judge measures no
// confidence, and the stamp must POP the key, not write a fabricated 0
// — "verified with zero confidence" is the opposite of a judged-true
// run (r1 Expert QA; Python: confidence=None pops).
func TestRunNowStampsNoConfidenceKey(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{"the answer", `{"fulfilled": true}`}}
	res, err := Run(context.Background(), fake, record.New(ws), "q?", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	raw, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "metadata.json"))
	if rerr != nil {
		t.Fatal(rerr)
	}
	var meta map[string]any
	if err := json.Unmarshal(raw, &meta); err != nil {
		t.Fatal(err)
	}
	if _, has := meta["goal_verdict_confidence"]; has {
		t.Fatalf("NOW stamp must not fabricate a confidence: %v", meta)
	}
	if meta["goal_achieved"] != true {
		t.Fatalf("judged verdict lost: %v", meta)
	}
}

// TestRunNowRowCarriesVerdictSource: the DURABLE row distinguishes a
// judged run ("go_now_verify_v1"), a judge that ERRORED
// ("go_now_verify_error", goal_achieved absent — review F7's broken
// pipe), and an unparseable verdict (NO source — honestly unjudged).
// Before this pin the error and unparseable rows were byte-identical
// on the dimension F7 exists to distinguish (r1 Expert QA).
func TestRunNowRowCarriesVerdictSource(t *testing.T) {
	ws := t.TempDir()
	judged := &llm.Fake{Script: []string{"a", `{"fulfilled": true}`}}
	res, err := Run(context.Background(), judged, record.New(ws), "q?", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	rows := readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	if rows[len(rows)-1]["goal_verdict_source"] != "go_now_verify_v1" {
		t.Fatalf("judged row source: %v", rows[len(rows)-1])
	}

	errored := &errOnCall{Adapter: &llm.Fake{Script: []string{"a"}}, failFrom: 2}
	if _, err := Run(context.Background(), errored, record.New(ws), "q?", false, "", 0, 0); err != nil {
		t.Fatal(err)
	}
	rows = readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	row := rows[len(rows)-1]
	if row["goal_verdict_source"] != "go_now_verify_error" {
		t.Fatalf("errored-judge row must carry the error source: %v", row)
	}
	if _, has := row["goal_achieved"]; has {
		t.Fatalf("errored-judge row must stay unjudged: %v", row)
	}
	if res.GoalAchieved == nil {
		t.Fatalf("sanity: judged run lost its verdict")
	}

	unparseable := &llm.Fake{Script: []string{"a", "Sure! Looks fine."}}
	if _, err := Run(context.Background(), unparseable, record.New(ws), "q?", false, "", 0, 0); err != nil {
		t.Fatal(err)
	}
	rows = readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	if _, has := rows[len(rows)-1]["goal_verdict_source"]; has {
		t.Fatalf("unparseable verdict is unjudged-by-design — no source: %v", rows[len(rows)-1])
	}
}

// TestVerifyNowScrubsWhyAtBoundary: the judge's why is scrubbed where
// the field is SET, so the terminal-bound Result field — not just the
// row and stamp — is clean. r1's HIGH (all three lenses): per-sink
// scrubbing missed the one surface an operator is guaranteed to read.
func TestVerifyNowScrubsWhyAtBoundary(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{
		"done, used the key",
		`{"fulfilled": false, "why": "claims rotation but AKIAIOSFODNN7EXAMPLE was never rotated"}`,
	}}
	res, err := Run(context.Background(), fake, record.New(ws), "rotate the key", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(res.VerdictSummary, "AKIAIOSFODNN7EXAMPLE") {
		t.Fatalf("terminal-bound VerdictSummary must be scrubbed: %q", res.VerdictSummary)
	}
	if res.VerdictSummary == "" {
		t.Fatalf("scrub must redact, not erase, the why")
	}
}

// TestVerifyNowRecoversTrailingRationale: `{"fulfilled": false}` with
// the reason as trailing prose — the ed7cf400 shape — must carry the
// prose, not the false claim "judge gave no rationale" (r1 Skeptic:
// only the 160-token half of the 2113a608 fix was ported).
func TestVerifyNowRecoversTrailingRationale(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{
		"I created the file.",
		`{"fulfilled": false} No Write or Bash tool calls showing the file was created.`,
	}}
	res, err := Run(context.Background(), fake, record.New(ws), "create the file", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(res.VerdictSummary, "No Write or Bash tool calls") {
		t.Fatalf("trailing rationale must be recovered: %q", res.VerdictSummary)
	}
}

// capture records every Complete payload so a test can assert on what
// the judge was actually SHOWN.
type capture struct {
	llm.Adapter
	payloads []string
}

func (c *capture) Complete(ctx context.Context, msgs []llm.Message, opts llm.Options) (*llm.Response, error) {
	c.payloads = append(c.payloads, msgs[len(msgs)-1].Content)
	return c.Adapter.Complete(ctx, msgs, opts)
}

// TestVerifyNowPayloadTruncationMarked: a >2000-char answer reaches the
// judge truncated WITH the visible marker — a judge that cannot tell a
// whole answer from its first 2000 characters reports what it cannot
// see as not delivered (Python _now_verify_payload, the last unmarked
// judge window, fixed 2026-08-03; r1 Minimalist+Architect).
func TestVerifyNowPayloadTruncationMarked(t *testing.T) {
	ws := t.TempDir()
	long := strings.Repeat("x", 2600)
	cap := &capture{Adapter: &llm.Fake{Script: []string{long, `{"fulfilled": true}`}}}
	if _, err := Run(context.Background(), cap, record.New(ws), "q?", false, "", 0, 0); err != nil {
		t.Fatal(err)
	}
	if len(cap.payloads) != 2 {
		t.Fatalf("expected answer + judge calls, got %d", len(cap.payloads))
	}
	judge := cap.payloads[1]
	if !strings.Contains(judge, "[TRUNCATED — first 2000 of 2600 characters; the rest was NOT shown to you]") {
		t.Fatalf("judge window cut must be VISIBLE: %.200s", judge)
	}
	if strings.Count(judge, "x") != 2000 {
		t.Fatalf("judge must see exactly the first 2000 chars, saw %d", strings.Count(judge, "x"))
	}
}

// resultErrJudge fails the judge call with a token-carrying
// llm.ResultError — the refused-but-billed shape.
type resultErrJudge struct {
	llm.Adapter
	calls int
}

func (r *resultErrJudge) Complete(ctx context.Context, msgs []llm.Message, opts llm.Options) (*llm.Response, error) {
	r.calls++
	if r.calls >= 2 {
		return nil, &llm.ResultError{Msg: "refused", TokensIn: 11, TokensOut: 7}
	}
	return r.Adapter.Complete(ctx, msgs, opts)
}

// TestRunNowSalvagesResultErrorUsage: a judge call refused-but-billed
// still lands its spend in the totals (llm.ResultError doctrine — the
// new call sites were the unfixed half of a 3-vs-3 split, r1 QA).
func TestRunNowSalvagesResultErrorUsage(t *testing.T) {
	ws := t.TempDir()
	fake := &resultErrJudge{Adapter: &llm.Fake{Script: []string{"the answer"}}}
	res, err := Run(context.Background(), fake, record.New(ws), "q?", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.NowVerifyError == "" {
		t.Fatalf("errored judge must stay marked: %+v", res)
	}
	if res.TokensIn < 11 || res.TokensOut < 7 {
		t.Fatalf("refused judge call's spend must be salvaged: in=%d out=%d",
			res.TokensIn, res.TokensOut)
	}
}

// TestRunNowSeedTokensReachRow: classify spend seeded by the caller
// lands on the outcome row — the routing call's cost must not vanish
// from the ledger (r1, all four lenses).
func TestRunNowSeedTokensReachRow(t *testing.T) {
	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{"a", `{"fulfilled": true}`}}
	// Seeds far above any natural Fake usage (mutation-M21 lesson: a
	// small seed asserts vacuously against organic token counts).
	if _, err := Run(context.Background(), fake, record.New(ws), "q?", false, "", 1000003, 500001); err != nil {
		t.Fatal(err)
	}
	rows := readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	row := rows[len(rows)-1]
	if row["tokens_in"].(float64) < 1000003 || row["tokens_out"].(float64) < 500001 {
		t.Fatalf("seed spend must reach the row: %v", row)
	}
}

// --- routing r2 pins (adversarial 2026-08-22) --------------------------

// TestVerifyPayloadRuneSafe: the judge-window cut is rune-based —
// Python str slicing is codepoint slicing, a byte cut splits UTF-8
// mid-rune and the marker lies about the count (r2 Skeptic).
func TestVerifyPayloadRuneSafe(t *testing.T) {
	long := strings.Repeat("é", 2100) // 2 bytes per rune: byte cut would split + miscount
	p := verifyPayload("q", long)
	if !strings.Contains(p, "[TRUNCATED — first 2000 of 2100 characters") {
		t.Fatalf("marker must count RUNES, not bytes: %.160s", p)
	}
	if strings.Count(p, "é") != 2000 {
		t.Fatalf("cut must keep exactly 2000 runes, kept %d", strings.Count(p, "é"))
	}
	for _, r := range p {
		if r == '�' {
			t.Fatalf("cut split a rune — invalid UTF-8 in judge window")
		}
	}
}

// The JSON skip is a NAIVE brace counter, blind to string literals, with
// no unbalanced-input lane — handle._now_verdict_rationale's exactly.
// These three assertions used to demand the opposite (a string-aware scan
// that returns "" on truncation), which is the r3 HIGH. Measured against
// CPython 3.14.3 on 2026-08-23; the whole corpus is in
// verdict_diff_test.go and this test names the three consequences at the
// surface.
//
// None of these is the behaviour anyone would design. All three are the
// behaviour the store is shaped by.
func TestVerdictRationaleCountsBracesNaivelyAsPythonDoes(t *testing.T) {
	for _, c := range []struct{ name, raw, want string }{
		// A `}` inside a string value closes the object EARLY, so the
		// tail of the JSON survives as part of the "rationale".
		{"a brace inside a string closes early",
			`{"fulfilled": false, "note": "stray } inside"} the real reason`,
			`inside"} the real reason`},
		// A truncated object never balances, the loop simply ends, and
		// the whole blob comes back. Go used to return "" here, which
		// flipped the caller to the static "judge gave no rationale" —
		// a stored claim that is false, and the exact regression this
		// function was written to prevent.
		{"a truncated object keeps the whole blob",
			`{"fulfilled": false, "why": "truncated mid-obj`,
			`{"fulfilled": false, "why": "truncated mid-obj`},
		// An escaped quote is not special to a counter that never
		// tracked strings in the first place.
		{"an escaped quote is not special",
			`{"a": "esc \" }", "b": 1} after escape`,
			`", "b": 1} after escape`},
	} {
		t.Run(c.name, func(t *testing.T) {
			if got := verdictRationale(c.raw); got != c.want {
				t.Fatalf("diverges from CPython\n    go %q\n    py %q", got, c.want)
			}
		})
	}
}

// TestVerifyNowScrubBeforeClip: the rationale path scrubs BEFORE any
// clip — a clip-first order can cut a credential mid-string and slip
// the fragment past fixed-length secret patterns (r2 Skeptic). The
// secret sits past the VerdictProse cap to prove order.
func TestVerifyNowScrubBeforeClip(t *testing.T) {
	ws := t.TempDir()
	pad := strings.Repeat("x ", 1200) // > VerdictProse cap once collapsed
	fake := &llm.Fake{Script: []string{
		"did it",
		`{"fulfilled": false} ` + pad + "key AKIAIOSFODNN7EXAMPLE never rotated",
	}}
	res, err := Run(context.Background(), fake, record.New(ws), "rotate", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	rows := readRows(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	sum, _ := rows[len(rows)-1]["goal_verdict_summary"].(string)
	if strings.Contains(res.VerdictSummary, "AKIA") || strings.Contains(sum, "AKIA") {
		t.Fatalf("secret survived the scrub: res=%q row=%q", res.VerdictSummary, sum)
	}
}

// A reasoning trace before the JSON is NOT stripped, and the whole reply
// — trace, JSON and prose — comes back as the rationale. That is not the
// behaviour anyone wants; it is the behaviour handle._now_verdict_rationale
// has, and this field is durable (res.VerdictSummary, read by an operator
// and written to outcomes.jsonl), so a Go-only strip forks the store.
//
// Go used to return just "the file was never written" here. Measured
// against CPython 3.14.3 on 2026-08-23:
//
//	'<think>maybe it failed? let me check</think> {"fulfilled": false} the file was never written'
//	  -> the same string back, unchanged
//
// The reason is structural: the recovery only skips a JSON prefix when
// the text STARTS with `{` or a fence, and this one starts with `<`. If
// the strip is wanted it belongs in the Python first (mission-r2 MEDIUM).
func TestVerdictRationaleKeepsAThinkTraceAsPythonDoes(t *testing.T) {
	raw := "<think>maybe it failed? let me check</think>\n" +
		`{"fulfilled": false} the file was never written`
	got := verdictRationale(raw)
	// Python collapses runs of whitespace via " ".join(text.split()), so
	// the newline becomes a single space; nothing else changes.
	want := `<think>maybe it failed? let me check</think> ` +
		`{"fulfilled": false} the file was never written`
	if got != want {
		t.Fatalf("rationale diverges from CPython\n    go %q\n    py %q", got, want)
	}
}

// ...and the shape the recovery DOES handle, unchanged by the above: a
// bare JSON prefix is skipped and only the prose survives.
func TestVerdictRationaleSkipsABareJSONPrefix(t *testing.T) {
	got := verdictRationale(`{"fulfilled": false} the file was never written`)
	if got != "the file was never written" {
		t.Fatalf("bare-JSON prefix recovery broke: %q", got)
	}
}

// Python's handle.py:396 is `content = resp.content.strip()`, and
// str.strip() covers U+001C..U+001F where strings.TrimSpace does not.
// A separator-only reply therefore collapses to "" in CPython and takes
// the "[no response]" sentinel — while a TrimSpace port kept it
// non-empty and stored the separators as the answer (adversarial
// mission-r4 MEDIUM).
//
// This matters twice over: res.Answer is the outcome row's summary AND
// the text handed to the verify judge, so the divergence can flip the
// verdict as well as the stored bytes. The expectation is measured, not
// asserted — the probe runs CPython's own strip.
func TestRunNowAnswerStripsSeparatorsAsPythonDoes(t *testing.T) {
	const content = "\x1c\x1f"

	in, err := json.Marshal(content)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys; print(json.dumps(json.loads(sys.argv[1]).strip()))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var pyStripped string
	if err := json.Unmarshal(out, &pyStripped); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if pyStripped != "" {
		t.Fatalf("CPython no longer strips U+001C..U+001F (%q) — the premise "+
			"of this test has moved", pyStripped)
	}

	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{content,
		`{"fulfilled": false, "why": "empty response"}`}}
	res, err := Run(context.Background(), fake, record.New(ws),
		"say something", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Answer != "[no response]" {
		t.Fatalf("a separator-only reply must strip to empty and take the "+
			"placeholder, as CPython does; got %q", res.Answer)
	}
}

// The same seam with real content around the separators: CPython strips
// the edges and keeps the middle, and the stored summary must match
// byte for byte.
func TestRunNowAnswerKeepsInnerSeparators(t *testing.T) {
	const content = "\x1cThe answer is 42.\x1f"

	ws := t.TempDir()
	fake := &llm.Fake{Script: []string{content,
		`{"fulfilled": true, "why": "answered"}`}}
	res, err := Run(context.Background(), fake, record.New(ws),
		"say something", false, "", 0, 0)
	if err != nil {
		t.Fatal(err)
	}
	if res.Answer != "The answer is 42." {
		t.Fatalf("leading/trailing separators must be stripped as str.strip() "+
			"does; got %q", res.Answer)
	}
}
