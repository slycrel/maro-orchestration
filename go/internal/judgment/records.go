package judgment

import (
	"fmt"
	"reflect"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// KindShadow is the shadow arm's record: what a provider that was NOT
// asked to decide anything answered about the same subject.
const KindShadow record.Kind = "shadow_judgment"

// ShadowJudgment is one shadow provider's answer to the question a
// primary verdict already settled. It is a CONTROL record, and that is
// the whole guarantee: the resolver reads production records through a
// *ProductionReader, which cannot return a control row by construction,
// so no shadow answer can change an effective verdict however confident
// it is. It is evidence for the judgment report and nothing else.
type ShadowJudgment struct {
	record.ControlRecord
	record.Header `json:"header"`
	Provider      string          `json:"provider"`             // llm | jev | pcd
	Primary       record.RecordID `json:"primary"`              // the verdict it shadows
	Invocation    record.RecordID `json:"invocation,omitempty"` // the shadow call; absent when it never dispatched
	Question      string          `json:"question"`             // the question id
	Answer        *Answer         `json:"answer,omitempty"`     // absent iff the call failed
	LatencyMillis int64           `json:"latency_ms"`
	Usage         invoke.Usage    `json:"usage"`
	Failed        bool            `json:"failed,omitempty"`
	Reason        string          `json:"reason,omitempty"` // why it failed; a shadow failure never fails the run
}

func (r *ShadowJudgment) Head() *record.Header { return &r.Header }
func (r *ShadowJudgment) Kind() record.Kind    { return KindShadow }
func (r *ShadowJudgment) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	known := false
	for _, n := range Known() {
		if r.Provider == n {
			known = true
		}
	}
	if !known {
		return fmt.Errorf("shadow_judgment: provider %q out of vocabulary %v", r.Provider, Known())
	}
	if err := record.ValidateID(r.Primary); err != nil {
		return fmt.Errorf("shadow_judgment: primary: %w", err)
	}
	if r.Invocation != "" {
		if err := record.ValidateID(r.Invocation); err != nil {
			return fmt.Errorf("shadow_judgment: invocation: %w", err)
		}
	}
	if strings.TrimSpace(r.Question) == "" {
		return fmt.Errorf("shadow_judgment: question id is empty")
	}
	if (r.Answer == nil) != r.Failed {
		return fmt.Errorf("shadow_judgment: exactly one of an answer or a failure")
	}
	if r.Failed && strings.TrimSpace(r.Reason) == "" {
		return fmt.Errorf("shadow_judgment: a failed shadow says why")
	}
	if r.Answer != nil && !kinds[r.Answer.Type] {
		return fmt.Errorf("shadow_judgment: answer type %q out of vocabulary", r.Answer.Type)
	}
	if r.LatencyMillis < 0 {
		return fmt.Errorf("shadow_judgment: negative latency")
	}
	return r.Usage.Validate()
}

func init() {
	record.Register(record.Spec{Kind: KindShadow, Envelope: record.Control, Version: 1, Type: reflect.TypeOf(ShadowJudgment{}),
		Writer:    "the driver's shadow arm, after the primary judgment answered (judgment.shadow names the providers; empty by default)",
		Reader:    "`maro-go judgment report` (agreement, disagreements, latency)",
		Decision:  "none — evidence for the judgment report; a control record cannot reach the resolver, which reads production only",
		Retention: record.Forever})
}
