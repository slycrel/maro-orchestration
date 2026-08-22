package now

import (
	"context"
	"encoding/json"
	"os"
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
	res, err := Run(context.Background(), fake, record.New(ws), "what does HTTP 429 mean?", false, "")
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
	res, err := Run(context.Background(), fake, record.New(ws), "where can I get gas near Manti, Utah?", false, "")
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
	res, err := Run(context.Background(), fake, record.New(ws), "quick question?", false, "")
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
	res, err := Run(context.Background(), fake, record.New(ws), "quick question?", false, "")
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
	res, err := Run(context.Background(), fake, record.New(ws), "say something", false, "")
	if err != nil {
		t.Fatal(err)
	}
	if res.Answer != "[no response]" {
		t.Fatalf("empty content must record the placeholder: %q", res.Answer)
	}
}
