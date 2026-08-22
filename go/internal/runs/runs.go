// Package runs ports the run-dir slice of runs.py the Go loop needs:
// create the per-run directory, seed and merge metadata.json, and stamp
// the closure verdict tuple. This is the WRITER half of the contract
// recall.FindPriorAttempts already reads — until now a pure-Go
// workspace degraded to zero prior attempts because nothing on this
// side wrote run metadata (named in PORT.md since the recall tranche).
//
// Deliberately unported (see PORT.md): nicknames, the run-ref index,
// thread brains, dispatch-envelope/attachment landing, the stranded-run
// sweep, and cross-process locking on metadata.json (locked_rmw) — the
// Go v0 loop is metadata.json's only writer in its workspace; writes
// are atomic (temp+rename) so concurrent READERS never see a torn file,
// and the single-writer assumption is named here rather than silently
// relied on.
package runs

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
)

// Dir is the run-dir path for a handle id (does not create it). Python
// appends a human nickname (`<id>-<nickname>`); the Go dir is the bare
// id — readers glob runs/*/metadata.json, so naming is not contract.
func Dir(workspaceDir, handleID string) string {
	return filepath.Join(workspaceDir, "runs", handleID)
}

// Create makes the run-dir with the source/build/artifact skeleton
// (the compile mental model — pre-creating makes "where does this go?"
// obvious mid-run), seeds source/prompt.txt first-wins with the
// verbatim goal, and writes the initial metadata. Idempotent.
func Create(workspaceDir, handleID, prompt string) (string, error) {
	rd := Dir(workspaceDir, handleID)
	for _, sub := range []string{"source", "build", "artifact"} {
		if err := os.MkdirAll(filepath.Join(rd, sub), 0o755); err != nil {
			return "", err
		}
	}
	promptPath := filepath.Join(rd, "source", "prompt.txt")
	if _, err := os.Stat(promptPath); os.IsNotExist(err) {
		if werr := atomicWrite(promptPath, []byte(prompt)); werr != nil {
			return "", werr
		}
	}
	if err := WriteMetadata(rd, map[string]any{
		"handle_id": handleID,
		"prompt":    prompt,
		"status":    "running",
		"pid":       os.Getpid(),
	}); err != nil {
		return "", err
	}
	return rd, nil
}

// WriteMetadata merges fields into metadata.json. started_at is set
// once and preserved thereafter (Python write_metadata parity); an
// existing key survives unless the new fields name it; a nil value
// POPS its key — the tri-state carrier goal_achieved and friends need
// delete semantics, and "set to null" would read as a judged false by
// sloppy consumers. The write is atomic so readers (FindPriorAttempts
// in either runtime) never see a torn file.
func WriteMetadata(runDir string, fields map[string]any) error {
	metaPath := filepath.Join(runDir, "metadata.json")
	existing := map[string]any{}
	if raw, err := os.ReadFile(metaPath); err == nil {
		// A corrupt existing file degrades to a fresh map — the merge
		// must not wedge the run on a torn predecessor.
		_ = json.Unmarshal(raw, &existing)
	}
	if _, ok := existing["started_at"]; !ok {
		existing["started_at"] = time.Now().UTC().Format(time.RFC3339)
	}
	for k, v := range fields {
		if k == "started_at" {
			continue // first writer wins, like Python
		}
		if v == nil {
			delete(existing, k)
			continue
		}
		existing[k] = v
	}
	out, err := json.MarshalIndent(existing, "", "  ")
	if err != nil {
		return err
	}
	return atomicWrite(metaPath, out)
}

// Finalize stamps the run's terminal status and ended_at.
func Finalize(runDir, status string) error {
	return WriteMetadata(runDir, map[string]any{
		"status":   status,
		"ended_at": time.Now().UTC().Format(time.RFC3339),
	})
}

// StampVerdict is THE verdict-tuple replacement, ported from
// _apply_verdict_tuple: one implementation for every verdict writer
// (Python round-15 review: a second hand-maintained field list had
// already drifted). Every member is set or popped; nothing is left to
// a merge. goalAchieved == nil pops the boolean — an UNJUDGED verdict
// stamps nothing, because absence means "not judged" and a false here
// demotes the run everywhere the stamp is read.
func StampVerdict(runDir string, goalAchieved *bool, source, summary string,
	confidence float64, downgradeReason string, gaps []string) error {
	fields := map[string]any{
		"goal_verdict_source":  source,
		"goal_verdict_summary": budget.VerdictProse.Clip(summary),
	}
	fields["goal_verdict_confidence"] = confidence
	if downgradeReason != "" {
		fields["goal_verdict_downgrade_reason"] = budget.VerdictProse.Clip(downgradeReason)
	} else {
		fields["goal_verdict_downgrade_reason"] = nil
	}
	var kept []string
	for _, g := range gaps {
		if g != "" {
			kept = append(kept, budget.Clip(g, 500))
		}
	}
	if len(kept) > 5 {
		// Count cuts announce themselves like char cuts do (Python
		// round-14 review: five-of-seven gaps rendered as complete).
		extra := len(kept) - 5
		kept = append(kept[:5], fmt.Sprintf("(+%d more gap(s) in the closure verdict artifact)", extra))
	}
	if len(kept) > 0 {
		fields["goal_verdict_gaps"] = kept
	} else {
		fields["goal_verdict_gaps"] = nil
	}
	if goalAchieved == nil {
		fields["goal_achieved"] = nil
	} else {
		fields["goal_achieved"] = *goalAchieved
	}
	return WriteMetadata(runDir, fields)
}

// AppendVerdictRow appends one closure outcome row to the run's durable
// build/closure_verdicts.jsonl (persist-the-artifacts decree, Jeremy
// 2026-07-29: every closure outcome — full verdict or named skip —
// leaves a row). Append-only, one JSON object per line.
func AppendVerdictRow(runDir string, row map[string]any) error {
	full := map[string]any{"ts": time.Now().UTC().Format(time.RFC3339)}
	for k, v := range row {
		full[k] = v
	}
	out, err := json.Marshal(full)
	if err != nil {
		return err
	}
	f, err := os.OpenFile(filepath.Join(runDir, "build", "closure_verdicts.jsonl"),
		os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err := f.Write(append(out, '\n')); err != nil {
		return err
	}
	return f.Sync()
}

func atomicWrite(path string, data []byte) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}
