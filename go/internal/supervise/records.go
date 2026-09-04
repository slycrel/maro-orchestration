// Package supervise owns lanes: registration, heartbeats with progress
// watermarks, panic capture, bounded restart, stall detection, and the
// quiesce order at shutdown (design note §10, §2). Its evidence lives in
// the CONTROL envelope: heartbeats and lane events are about the process,
// never about a run, and no production reader sees them.
package supervise

import (
	"errors"
	"fmt"
	"reflect"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

const (
	KindLaneEvent     record.Kind = "lane_event"
	KindLaneHeartbeat record.Kind = "lane_heartbeat"
)

// LaneEventKind names what happened to a lane.
type LaneEventKind string

const (
	LaneStarted   LaneEventKind = "started"
	LaneStopped   LaneEventKind = "stopped"   // returned nil, or was quiesced
	LaneFailed    LaneEventKind = "failed"    // returned an error
	LanePanicked  LaneEventKind = "panicked"  // recovered from a panic
	LaneRestarted LaneEventKind = "restarted" // a new generation started after a failure/panic
	LaneGaveUp    LaneEventKind = "gave_up"   // the restart bound was reached; the lane stays down
	LaneStalled   LaneEventKind = "stalled"   // no heartbeat within the lane's declared silence
)

var laneEvents = map[LaneEventKind]bool{LaneStarted: true, LaneStopped: true, LaneFailed: true, LanePanicked: true, LaneRestarted: true, LaneGaveUp: true, LaneStalled: true}

// LaneEvent is one lifecycle event of one lane generation.
type LaneEvent struct {
	record.ControlRecord
	record.Header `json:"header"`
	Lane          string        `json:"lane"`
	Event         LaneEventKind `json:"event"`
	Generation    int           `json:"generation"`
	Reason        string        `json:"reason,omitempty"`
}

func (r *LaneEvent) Head() *record.Header { return &r.Header }
func (r *LaneEvent) Kind() record.Kind    { return KindLaneEvent }
func (r *LaneEvent) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := laneSubject(&r.Header, r.Lane, "lane_event"); err != nil {
		return err
	}
	if !laneEvents[r.Event] {
		return fmt.Errorf("lane_event: event %q out of vocabulary", r.Event)
	}
	if r.Generation < 1 {
		return errors.New("lane_event: generation starts at 1")
	}
	switch r.Event {
	case LaneFailed, LanePanicked, LaneGaveUp, LaneStalled:
		if r.Reason == "" {
			return fmt.Errorf("lane_event: %s carries a reason", r.Event)
		}
	}
	return nil
}

// LaneHeartbeat is a lane's progress: the journal watermark it has
// processed through. Written on change, rate-limited by the supervisor.
type LaneHeartbeat struct {
	record.ControlRecord
	record.Header `json:"header"`
	Lane          string `json:"lane"`
	Generation    int    `json:"generation"`
	Watermark     uint64 `json:"watermark"`
}

func (r *LaneHeartbeat) Head() *record.Header { return &r.Header }
func (r *LaneHeartbeat) Kind() record.Kind    { return KindLaneHeartbeat }
func (r *LaneHeartbeat) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := laneSubject(&r.Header, r.Lane, "lane_heartbeat"); err != nil {
		return err
	}
	if r.Generation < 1 {
		return errors.New("lane_heartbeat: generation starts at 1")
	}
	return nil
}

func laneSubject(h *record.Header, lane, what string) error {
	if lane == "" {
		return fmt.Errorf("%s: lane is empty", what)
	}
	if h.Subject.Kind != "lane" || h.Subject.ID != lane {
		return fmt.Errorf("%s: subject must be the lane", what)
	}
	if h.RunID != "" || h.Attempt != 0 {
		return fmt.Errorf("%s: a lane record is process-scoped, never run-scoped", what)
	}
	return nil
}

func now() time.Time { return time.Now().UTC() }

func init() {
	record.Register(record.Spec{Kind: KindLaneEvent, Envelope: record.Control, Version: 1, Type: reflect.TypeOf(LaneEvent{}),
		Writer:   "the supervisor (start, stop, failure, panic, restart, give-up, stall)",
		Reader:   "Supervisor.Health (the degraded line in every delivery); operators (`maro-go lanes`)",
		Decision: "which lanes are down or stalled, and why; whether a restart bound was reached", Retention: record.Bounded})
	record.Register(record.Spec{Kind: KindLaneHeartbeat, Envelope: record.Control, Version: 1, Type: reflect.TypeOf(LaneHeartbeat{}),
		Writer:   "the supervisor, when a lane's watermark moves (rate-limited)",
		Reader:   "stall detection (Supervisor.watch); operators",
		Decision: "whether a lane is making progress; the watermark it resumes from is its cursor, not this", Retention: record.Bounded})
}
