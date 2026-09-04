package run

import (
	"encoding/json"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// OutcomesView projects every `recorded` transition to one row of the
// shared outcomes ledger (CONTRACTS B6, `memory/outcomes.jsonl`), EXACT to
// the wire contract: required keys present, `goal_achieved` ABSENT when
// unjudged (rule A6, never null), closed vocabularies honoured, no keys B6
// does not define (`lane` is a B3 key; B6 carries the lane in `task_type`).
// It is a lossy view of a richer source (contracts/VIEWS.md).
type OutcomesView struct{ Store *thought.Store }

func (OutcomesView) Name() string                { return "outcomes.jsonl" }
func (OutcomesView) Population() record.Envelope { return record.Production }
func (OutcomesView) Accepts(r record.Record) bool {
	t, ok := r.(*Transition)
	return ok && t.To == Recorded
}

// outcomeRow is the B6 shape. Field order is the contract's.
type outcomeRow struct {
	OutcomeID  string   `json:"outcome_id"`
	Goal       string   `json:"goal"`
	Summary    string   `json:"summary"`
	TaskType   string   `json:"task_type"`
	Status     string   `json:"status"`
	Lessons    []string `json:"lessons"`
	TokensIn   int64    `json:"tokens_in"`
	TokensOut  int64    `json:"tokens_out"`
	ElapsedMS  int64    `json:"elapsed_ms"`
	CostUSD    float64  `json:"cost_usd"`
	Model      string   `json:"model"`
	RecordedAt string   `json:"recorded_at"`
	HandleID   string   `json:"handle_id"`
	// tri-state verdict: ABSENT when unjudged
	GoalAchieved   *bool    `json:"goal_achieved,omitempty"`
	VerdictSource  string   `json:"goal_verdict_source,omitempty"`
	VerdictConf    *float64 `json:"goal_verdict_confidence,omitempty"`
	MeasurementCls string   `json:"measurement_class,omitempty"`
}

func (v OutcomesView) Line(r record.Record) ([]byte, error) {
	t := r.(*Transition)
	o := t.Outcome
	row := outcomeRow{OutcomeID: HandleOf(record.RunID(t.ID)), TaskType: string(LaneNow), Lessons: []string{},
		TokensIn: o.Usage.InputTokens, TokensOut: o.Usage.OutputTokens, ElapsedMS: o.Usage.WallMillis, CostUSD: o.Usage.CostUSD, Model: o.Model,
		RecordedAt: t.At.UTC().Format("2006-01-02T15:04:05.000000Z"), HandleID: HandleOf(t.RunID)}
	// the goal text is a thought; the view reads it whole
	goal, err := v.goalText(t)
	if err != nil {
		return nil, err
	}
	row.Goal = goal
	switch o.Terminal {
	case "complete", "partial":
		row.Status = "done"
	default:
		row.Status = "stuck"
	}
	row.Summary = fmt.Sprintf("%s: terminal %s, closure %s", LaneNow, o.Terminal, o.ClosureOut)
	if o.Reason != "" {
		row.Summary += " (" + o.Reason + ")"
	}
	// closure `unknown` is unjudged: key absent. achieved / not_achieved
	// are judged; the source is the resolution's rule family.
	switch o.ClosureOut {
	case "achieved", "not_achieved":
		b := o.ClosureOut == "achieved"
		c := o.ClosureCnf
		row.GoalAchieved, row.VerdictConf = &b, &c
		// B6 vocabulary (src/memory_ledger.py:66): a self-stamped NOW verdict
		// is `now_self_verdict`; anything a judge/operator/check established
		// is `closure`.
		if o.ClosureSrc == "self" {
			row.VerdictSource = "now_self_verdict"
		} else {
			row.VerdictSource = "closure"
		}
	}
	return json.Marshal(row)
}

// goalText reads the goal thought the recorded outcome names, whole.
func (v OutcomesView) goalText(t *Transition) (string, error) {
	if v.Store == nil {
		return "", fmt.Errorf("outcomes view: no thought store")
	}
	if t.Outcome == nil {
		return "", fmt.Errorf("outcomes view: recorded transition %s carries no outcome", t.ID)
	}
	b, err := v.Store.Get(t.Outcome.GoalText)
	if err != nil {
		return "", err
	}
	return string(b), nil
}
