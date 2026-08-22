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
//
// Concurrency: appends take the same flock on the same sibling
// "<name>.lock" file the Python file_lock module uses, so Go and Python
// writers sharing one workspace are mutually exclusive (adversarial
// round 2026-08-22, Skeptic: Linux O_APPEND atomicity is not a given for
// multi-KB JSON rows, and Python's own concurrency-hardening arc
// reversed fail-open for exactly this reason). Fail-closed with a
// bounded wait; flock is kernel-released if the holder dies.
package record

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// lockTimeout matches Python file_lock's default deadline (30s).
const lockTimeout = 30 * time.Second

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
	Goal      string
	Status    string // "done" | "stuck"
	Summary   string
	TaskType  string
	Model     string
	LoopID    string
	Project   string
	TokensIn  int
	TokensOut int
	ElapsedMS int64
	DryRun    bool
	FailChain []string
}

// WriteOutcome appends one outcome row. Field names match the Python
// ledger; zero-valued Python-only fields are written explicitly where
// readers expect the key to exist. cost_usd is deliberately ABSENT: the
// Python estimator isn't ported yet, and a hardcoded 0.0 is a record
// that lies about spend (adversarial round 2026-08-22, Expert QA) —
// missing != zero, and the Python Outcome dataclass defaults the field
// on load.
func (r *Recorder) WriteOutcome(o Outcome) (string, error) {
	dir, err := r.memoryDir()
	if err != nil {
		return "", err
	}
	id := NewID()
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

// Event appends one captain's-log entry. audience is "system" always in
// v0: the only event types Go emits are system-lane in Python's
// USER_SURFACED_EVENTS registry too. When a later tranche adds a
// user-surfaced event type, port that registry — do not add a bare
// audience parameter (the Python rule keys on event_type, not caller
// discretion).
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

// appendJSONL appends one row under the same advisory flock protocol as
// Python file_lock.locked_append: exclusive flock on "<name>.lock",
// bounded wait, fail-closed on timeout. It also frames a torn tail — a
// prior crash can leave the file without a final LF, and appending
// straight onto that fragment fuses two rows into one malformed line;
// an LF first strands the fragment as its own row and keeps ours
// readable (ported from locked_append's 2026-08 hardening).
func (r *Recorder) appendJSONL(path string, row any) error {
	raw, err := json.Marshal(row)
	if err != nil {
		return fmt.Errorf("marshal row for %s: %w", filepath.Base(path), err)
	}
	return AppendRawLine(path, raw)
}

// Locked runs fn while holding the Python-compatible advisory flock for
// path ("<path>.lock" sibling, bounded wait, fail-closed). Exported for
// the pack importer's read-modify-write surfaces (quarantine files,
// CONFLICTS.md, tiered-lesson rewrites) so every cross-runtime writer
// shares the one lock protocol.
//
// Lock-ordering invariant (deadlock freedom is by convention, not
// structure): the pack-import gate (memory/.pack-import) is always the
// OUTERMOST lock; per-store locks (hypotheses.jsonl, <tier>/lessons.jsonl,
// quarantine files) nest inside it and never the other way. Any future
// caller that holds a store lock must not then take the gate.
//
// Two named divergences from Python's file_lock, both deliberately
// stricter: there is no MARO_FILELOCK_FAIL_OPEN escape hatch and no
// proceed-unlocked-with-warning fallback for an uncreatable lock file —
// Go always fails closed. Corollary: mutual exclusion with a Python
// writer holds only while the Python side is not configured fail-open,
// which is outside this runtime's control.
func Locked(path string, fn func() error) error {
	lockPath := path + ".lock"
	lf, err := os.OpenFile(lockPath, os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return fmt.Errorf("open lock %s: %w", lockPath, err)
	}
	defer lf.Close()
	if err := flockWithDeadline(int(lf.Fd()), lockTimeout); err != nil {
		return fmt.Errorf("lock %s: %w", lockPath, err)
	}
	defer syscall.Flock(int(lf.Fd()), syscall.LOCK_UN)
	return fn()
}

// AppendRawLine appends one pre-serialized row under the flock protocol,
// with the torn-tail framing appendJSONL documents.
func AppendRawLine(path string, raw []byte) error {
	return Locked(path, func() error {
		needsFrame := false
		if st, err := os.Stat(path); err == nil && st.Size() > 0 {
			tail, err := readLastByte(path)
			if err != nil {
				// Fail closed: if the tail cannot be known framed, appending
				// may fuse onto a torn fragment (Python r17 lesson).
				return fmt.Errorf("append %s: cannot inspect existing tail: %w", path, err)
			}
			needsFrame = tail != '\n'
		}

		f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return fmt.Errorf("open %s: %w", path, err)
		}
		defer f.Close()
		buf := raw
		if needsFrame {
			buf = append([]byte{'\n'}, raw...)
		}
		if _, err := f.Write(append(buf, '\n')); err != nil {
			return fmt.Errorf("append %s: %w", path, err)
		}
		return nil
	})
}

// flockWithDeadline mirrors Python locked_write's acquisition loop:
// non-blocking attempts with mild backoff until the deadline, then a
// fail-closed error (corruption of the learning ledgers is permanent and
// silent; a bounded loud stall is neither).
func flockWithDeadline(fd int, deadline time.Duration) error {
	start := time.Now()
	sleep := 50 * time.Millisecond
	for {
		err := syscall.Flock(fd, syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return nil
		}
		if err != syscall.EWOULDBLOCK && err != syscall.EAGAIN {
			return err
		}
		if time.Since(start) >= deadline {
			return fmt.Errorf("could not acquire flock within %s (holder alive?)", deadline)
		}
		time.Sleep(sleep)
		if sleep < 500*time.Millisecond {
			sleep *= 2
		}
	}
}

func readLastByte(path string) (byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	var b [1]byte
	if _, err := f.Seek(-1, 2); err != nil {
		return 0, err
	}
	if _, err := f.Read(b[:]); err != nil {
		return 0, err
	}
	return b[0], nil
}

// NewID returns a short random id (crypto/rand, hex), the same shape
// Python's uuid4().hex[:8] join keys carry. Exported so loop ids share
// this generator instead of inventing a weaker one (adversarial round
// 2026-08-22, Architect: a wall-clock-modulus loop_id collides across
// runs at sub-second cadence).
func NewID() string {
	var b [4]byte
	if _, err := rand.Read(b[:]); err != nil {
		// Timestamp fallback keeps ids unique enough for a single box;
		// the error is not worth failing a record over, but it is not
		// silent either — the 't' prefix says which path made it, while
		// the total stays 8 chars so join-key consumers keyed on the
		// uuid4().hex[:8] shape still parse it (adversarial r2, QA).
		return fmt.Sprintf("t%07x", time.Now().UnixNano()&0xFFFFFFF)
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
