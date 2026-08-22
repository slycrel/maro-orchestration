// Package record writes the runtime's durable records — outcome rows and
// captain's-log events — in the SAME on-disk shapes the Python runtime
// writes, so dev-recall, the viz server, and the learning pipeline read
// both runtimes' history through one lens.
//
// Compatibility contract (checked against live rows 2026-08-21):
//   - <workspace>/memory/outcomes.jsonl — one JSON object per line; the
//     Go rows carry the compatible key subset plus
//     measurement_class="go-port" so analyses can include or fence them.
//   - <workspace>/memory/captains_log.jsonl — {timestamp, event_type,
//     subject, summary, audience, context, loop_id}.
//
// Data-retention doctrine carries over: this package only APPENDS. There
// is no delete, rotate, or compact verb here at all.
package record

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Recorder appends records under one workspace. Construct via New so the
// resolved path is explicit at the call site — the 2026-08-16 live-ledger
// overwrite happened because a writer ASSUMED its store; this type makes
// the store an argument.
type Recorder struct {
	WorkspaceDir string
}

func New(workspaceDir string) *Recorder {
	return &Recorder{WorkspaceDir: workspaceDir}
}

func (r *Recorder) memoryDir() (string, error) {
	dir := filepath.Join(r.WorkspaceDir, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", fmt.Errorf("ensure memory dir: %w", err)
	}
	return dir, nil
}

// Outcome mirrors the Python record_outcome row (compatible subset).
type Outcome struct {
	Goal       string
	Status     string // "done" | "stuck"
	Summary    string
	TaskType   string
	Model      string
	LoopID     string
	Project    string
	TokensIn   int
	TokensOut  int
	ElapsedMS  int64
	DryRun     bool
	FailChain  []string
}

// WriteOutcome appends one outcome row. Field names match the Python
// ledger; zero-valued Python-only fields are written explicitly where
// readers expect the key to exist.
func (r *Recorder) WriteOutcome(o Outcome) (string, error) {
	dir, err := r.memoryDir()
	if err != nil {
		return "", err
	}
	id := newID()
	fail := o.FailChain
	if fail == nil {
		fail = []string{}
	}
	row := map[string]any{
		"outcome_id":        id,
		"goal":              o.Goal,
		"status":            o.Status,
		"summary":           o.Summary,
		"task_type":         orDefault(o.TaskType, "general"),
		"model":             o.Model,
		"loop_id":           o.LoopID,
		"project":           o.Project,
		"tokens_in":         o.TokensIn,
		"tokens_out":        o.TokensOut,
		"elapsed_ms":        o.ElapsedMS,
		"cost_usd":          0.0,
		"dry_run":           o.DryRun,
		"lessons":           []string{},
		"failure_chain":     fail,
		"recovery_steps":    0,
		"recorded_at":       nowISO(),
		"measurement_class": "go-port",
	}
	if err := r.appendJSONL(filepath.Join(dir, "outcomes.jsonl"), row); err != nil {
		return "", err
	}
	return id, nil
}

// Event appends one captain's-log entry. audience follows the Python
// rule's default: system unless the caller says the user should see it.
func (r *Recorder) Event(eventType, subject, summary string, context map[string]any, loopID string) error {
	dir, err := r.memoryDir()
	if err != nil {
		return err
	}
	if context == nil {
		context = map[string]any{}
	}
	entry := map[string]any{
		"timestamp":  nowISO(),
		"event_type": eventType,
		"subject":    subject,
		"summary":    summary,
		"audience":   "system",
		"context":    context,
	}
	if loopID != "" {
		entry["loop_id"] = loopID
	}
	return r.appendJSONL(filepath.Join(dir, "captains_log.jsonl"), entry)
}

func (r *Recorder) appendJSONL(path string, row any) error {
	raw, err := json.Marshal(row)
	if err != nil {
		return fmt.Errorf("marshal row for %s: %w", filepath.Base(path), err)
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("open %s: %w", path, err)
	}
	defer f.Close()
	if _, err := f.Write(append(raw, '\n')); err != nil {
		return fmt.Errorf("append %s: %w", path, err)
	}
	return nil
}

func newID() string {
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		// Timestamp fallback keeps ids unique enough for a single box;
		// the error is not worth failing a record over, but it is not
		// silent either — the id shape says which path made it.
		return fmt.Sprintf("t%x", time.Now().UnixNano())
	}
	return hex.EncodeToString(b[:])
}

func nowISO() string { return time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00") }

func orDefault(s, def string) string {
	if s == "" {
		return def
	}
	return s
}
