package run

import (
	"encoding/json"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The summary is the run as another program reads it (the Python shadow
// lane scoring the Go engine): every call the run made, the landscape
// judge included — it ran before the first attempt, so an attempt-only
// census would under-count the run's cost — with cost_reported honest
// about partial sums.
func TestSummaryCountsEveryCallTheRunMade(t *testing.T) {
	h := open(t)
	a, _ := h.now(t, goalQuarterly, false, "Summary: revenue flat")
	sa := Summarize(a)
	if a.Judge != nil || sa.Landscape == nil || sa.Landscape.Rule != LandscapeNoCandidates || sa.Usage.Calls != len(a.Latest().Invocations) || sa.Parent != "" || sa.Root != string(a.Goal.ID) {
		t.Fatalf("first run: judge=%v summary=%+v", a.Judge, sa)
	}
	if sa.Usage.CostReported {
		t.Fatalf("scripted calls report no cost; the sum must say so: %+v", sa.Usage)
	}
	usage := func(in int64, usd float64) invoke.Usage {
		return invoke.Usage{InputTokens: in, OutputTokens: 5, CostUSD: usd, CostReported: true, WallMillis: 10}
	}
	b := scripted(toolless,
		invoke.ScriptedCall{Response: []byte(landRelated), Usage: usage(100, 0.001)},
		invoke.ScriptedCall{Response: []byte("The revenue line"), Usage: usage(200, 0.002)},
		invoke.ScriptedCall{Response: []byte("The revenue line"), Usage: usage(300, 0.003)},
	)
	if _, err := h.driver(b, nil).Run(ctxBg, []byte(goalFollowUp), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs := h.newestRun(t)
	s := Summarize(rs)
	if rs.Judge == nil || s.Landscape == nil || s.Landscape.Relation != RelationRelated || s.Parent != string(a.Goal.ID) {
		t.Fatalf("follow-up did not attach its judge: judge=%v summary=%+v", rs.Judge, s)
	}
	if n := 1 + len(rs.Latest().Invocations); s.Usage.Calls != n || len(s.Calls) != n || s.Calls[0].Purpose != invoke.PurposeLandscape || s.Calls[0].Attempt != 0 || s.Calls[0].Tools {
		t.Fatalf("calls must be the judge then the attempt's, got %d (want %d): %+v", s.Usage.Calls, n, s.Calls)
	}
	var wantUSD float64
	var wantIn int64
	for _, c := range s.Calls {
		if c.Usage == nil {
			t.Fatalf("call %s has no receipt", c.ID)
		}
		wantUSD += c.Usage.CostUSD
		wantIn += c.Usage.InputTokens
	}
	if s.Usage.CostUSD != wantUSD || s.Usage.InputTokens != wantIn || !s.Usage.CostReported || s.Usage.Receipted != s.Usage.Calls || s.Usage.Unreceipted != 0 || s.Usage.CostUSD < 0.003 {
		t.Fatalf("usage does not sum the receipts: %+v", s.Usage)
	}
	if s.Outcome != MissionDelivered || s.Handle != HandleOf(rs.Run) {
		t.Fatalf("mission: %+v", s)
	}
	raw, err := json.Marshal(s)
	if err != nil {
		t.Fatal(err)
	}
	var back map[string]any
	if err := json.Unmarshal(raw, &back); err != nil {
		t.Fatal(err)
	}
	if back["landscape"].(map[string]any)["relation"] != "related" || back["usage"].(map[string]any)["cost_reported"] != true {
		t.Fatalf("json: %s", raw)
	}
}

// A sum that could not include every call is not a cost: a call with no
// receipt (crashed, still in flight) or no calls at all leaves
// cost_reported false even when every receipt it did see reported.
func TestSummaryCostIsNotReportedOverAPartialSum(t *testing.T) {
	reported := &invoke.State{Invocation: &invoke.Invocation{Header: record.Header{Attempt: 1}, Purpose: invoke.PurposeExecute},
		Receipt: &invoke.Receipt{Usage: invoke.Usage{CostUSD: 0.01, CostReported: true}}}
	unreceipted := &invoke.State{Invocation: &invoke.Invocation{Header: record.Header{Attempt: 1}, Purpose: invoke.PurposeExecute}}
	rs := &RunState{Run: "r", Goal: &Goal{}, Attempts: []*AttemptState{{Attempt: &RunAttempt{Header: record.Header{Attempt: 1}}, Invocations: []*invoke.State{reported, unreceipted}}}}
	s := Summarize(rs)
	if s.Usage.Calls != 2 || s.Usage.Receipted != 1 || s.Usage.Unreceipted != 1 || s.Usage.CostReported || s.Usage.CostUSD != 0.01 {
		t.Fatalf("partial sum: %+v", s.Usage)
	}
	if e := Summarize(&RunState{Run: "e", Goal: &Goal{}}); e.Usage.Calls != 0 || e.Usage.CostReported || len(e.Calls) != 0 {
		t.Fatalf("no calls: %+v", e.Usage)
	}
}
