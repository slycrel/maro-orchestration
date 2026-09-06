package run

import "github.com/slycrel/maro-orchestration/go/internal/invoke"

// Summary is a run's readout for another program — the shadow lane on the
// Python side reads it to score the Go engine as a challenger arm. It is
// pure over the fold: the mission, the landscape decision, the lineage,
// and the metered usage of every call the run made — the attempts' calls
// plus the landscape judge, which ran before the first attempt and is
// attached to the run, not to an attempt. Why a struct and not the
// inspect lines: a consumer that parses prose re-derives the fold badly.
type Summary struct {
	Handle   string         `json:"handle"`
	Run      string         `json:"run_id"`
	Goal     string         `json:"goal_id"`
	Attempt  uint32         `json:"attempt"`
	Outcome  MissionOutcome `json:"outcome"`
	Terminal string         `json:"terminal"`
	Closure  string         `json:"closure"`
	Delivery DeliveryState  `json:"delivery"`
	Required DeliveryState  `json:"required"`
	Reason   string         `json:"reason,omitempty"`
	Parent   string         `json:"parent,omitempty"`
	Root     string         `json:"root"`
	// Context is the operator-context thought the goal carried (its hash),
	// "" when the goal was given none — so a consumer can tell a run that
	// saw the operator's docs from one that did not.
	Context string `json:"context,omitempty"`
	// Landscape is the relation decision, nil when the lineage was set at
	// intake (--after, a fork child, a replay arm).
	Landscape *Landscape `json:"landscape,omitempty"`
	// Calls is every call the run made in journal order, the judge first.
	Calls []CallSummary `json:"calls"`
	Usage UsageSummary  `json:"usage"`
	// Result is the delivered payload; the fold does not hold it (it is a
	// thought), so the caller fills it. Empty until a delivery exists.
	Result string `json:"result,omitempty"`
}

// CallSummary is one invocation: what it was for, what it ran on, how it
// ended, and what it cost. Usage is nil for a call with no receipt (a
// crash seam, a call still in flight).
type CallSummary struct {
	ID       string         `json:"id"`
	Attempt  uint32         `json:"attempt"`
	Purpose  invoke.Purpose `json:"purpose"`
	Model    string         `json:"model"`
	Backend  string         `json:"backend"`
	Tools    bool           `json:"tools"`
	Terminal string         `json:"terminal,omitempty"`
	Usage    *invoke.Usage  `json:"usage,omitempty"`
}

// UsageSummary sums the receipted calls. CostReported is true only when
// every receipted call reported its cost — a partial sum is not a cost.
// Unreceipted counts the calls the sum could not include.
type UsageSummary struct {
	Calls        int     `json:"calls"`
	Receipted    int     `json:"receipted"`
	Unreceipted  int     `json:"unreceipted"`
	InputTokens  int64   `json:"input_tokens"`
	OutputTokens int64   `json:"output_tokens"`
	CacheRead    int64   `json:"cache_read_tokens"`
	CostUSD      float64 `json:"cost_usd"`
	CostReported bool    `json:"cost_reported"`
	WallMillis   int64   `json:"wall_ms"`
}

// Summarize is pure over the folded state.
func Summarize(rs *RunState) Summary {
	m := MissionOf(rs)
	s := Summary{Handle: m.Handle, Run: string(rs.Run), Attempt: m.Attempt, Outcome: m.Outcome, Terminal: m.Terminal, Closure: m.Closure,
		Delivery: m.Delivery, Required: m.Required, Reason: m.Reason, Parent: string(rs.Parent), Root: string(rs.Root), Landscape: rs.Landscape}
	if rs.Goal != nil && rs.Goal.Context != nil {
		s.Context = rs.Goal.Context.Hash
	}
	if rs.Goal != nil {
		s.Goal = string(rs.Goal.ID)
	}
	var states []*invoke.State
	if rs.Judge != nil {
		states = append(states, rs.Judge)
	}
	for _, a := range rs.Attempts {
		states = append(states, a.Invocations...)
	}
	s.Usage.CostReported = true
	s.Calls = []CallSummary{}
	for _, st := range states {
		c := CallSummary{ID: string(st.Invocation.ID), Attempt: st.Invocation.Attempt, Purpose: st.Invocation.Purpose,
			Model: st.Invocation.Backend.Model, Backend: st.Invocation.Backend.Name, Tools: st.Invocation.Tools}
		if st.Terminal != nil {
			c.Terminal = string(st.Terminal.State)
		}
		s.Usage.Calls++
		if st.Receipt == nil {
			s.Usage.Unreceipted++
		} else {
			u := st.Receipt.Usage
			c.Usage = &u
			s.Usage.Receipted++
			s.Usage.InputTokens += u.InputTokens
			s.Usage.OutputTokens += u.OutputTokens
			s.Usage.CacheRead += u.CacheRead
			s.Usage.CostUSD += u.CostUSD
			s.Usage.WallMillis += u.WallMillis
			s.Usage.CostReported = s.Usage.CostReported && u.CostReported
		}
		s.Calls = append(s.Calls, c)
	}
	if s.Usage.Receipted == 0 || s.Usage.Unreceipted > 0 {
		s.Usage.CostReported = false
	}
	return s
}
