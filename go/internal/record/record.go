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
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
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
	// StopVerdict is Python's typed stop_verdict outcome column
	// (memory.py record_outcome); StuckReason mirrors the loop result's
	// stuck_reason field (loop_artifacts.py — attribution reads it).
	// Grown 2026-08-22 (ladder r2, Skeptic + Expert QA HIGH): the
	// failure-chain text was the ONLY carrier of both, and the per-entry
	// clip could truncate the verdict tag and the do-not-fabricate
	// instruction exactly on the long-reason runs that need them.
	StopVerdict string
	StuckReason string
	// GoalAchieved is TRI-STATE (NOW-lane verify, Python handle.py's
	// _verify_now_outcome): nil writes NO key — absence means "not
	// judged", never failed. VerdictSummary rides only when non-empty.
	GoalAchieved   *bool
	VerdictSummary string
	// GoalVerdictSource names WHO judged (or tried to): a judged row
	// carries the judge's version tag; an errored judge carries an
	// error-family tag with goal_achieved absent — a broken verdict
	// pipe must be distinguishable from a deliberately unjudged run in
	// the DURABLE row, not just on a watched terminal (Python review
	// F7; handle.py stamps now_self_verdict_error the same way).
	GoalVerdictSource string
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
		"stop_verdict":      o.StopVerdict,
		"stuck_reason":      o.StuckReason,
	}
	if o.GoalAchieved != nil {
		row["goal_achieved"] = *o.GoalAchieved
	}
	if o.VerdictSummary != "" {
		row["goal_verdict_summary"] = o.VerdictSummary
	}
	if o.GoalVerdictSource != "" {
		row["goal_verdict_source"] = o.GoalVerdictSource
	}
	if err := r.appendJSONL(filepath.Join(dir, "outcomes.jsonl"), row); err != nil {
		return "", err
	}
	return id, nil
}

// userSurfacedEvents is the Go-emitted subset of Python's
// USER_SURFACED_EVENTS registry (captains_log.py): event types stamped
// audience:"user" so the curated user lane (viz, maro-log --audience
// user, health-lane rendering) surfaces them. Keep this synced with
// Python's frozenset whenever a tranche adds an event type — the
// self-improvement slice-2 events below were mislabeled "system" until
// the r1 parity review caught it. Audience keys on event_type, never on
// caller discretion (the Python rule).
var userSurfacedEvents = map[string]bool{
	"EVOLVER_APPLIED":     true,
	"EVOLVER_REVERTED":    true,
	"EVOLVER_VERDICT":     true,
	"GRADUATION_PROPOSED": true,
	"GRADUATION_VERIFIED": true,
}

// Event appends one captain's-log entry. audience comes from the
// event-type registry above, matching Python log_event's stamping.
func (r *Recorder) Event(eventType, subject, summary string, context map[string]any, loopID string) error {
	dir, err := r.memoryDir()
	if err != nil {
		return err
	}
	if context == nil {
		context = map[string]any{}
	}
	audience := "system"
	if userSurfacedEvents[eventType] {
		audience = "user"
	}
	entry := map[string]any{
		"timestamp":  nowISO(),
		"event_type": eventType,
		"subject":    subject,
		"summary":    summary,
		"audience":   audience,
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

// Verdict-source taxonomy — one spelling per judge, shared by every
// writer (adversarial routing r2: two lanes writing bare literals is
// how a taxonomy drifts).
const (
	SourceNowVerify      = "go_now_verify_v1"
	SourceNowVerifyError = "go_now_verify_error"
	SourceClosure        = "go_closure_v1"
)

// StampOutcomeVerdict merges a closure verdict onto the NEWEST
// outcomes.jsonl row whose loop_id matches — the agenda lane records
// its outcome at loop finalization but the closure verdict is judged
// AFTERWARDS, so the verdict lands on the row post-hoc (Python
// memory_ledger.stamp_outcome_verdict; adversarial routing r2, both
// lenses: without this, every closure-judged loop run reads as
// permanently unjudged on the one ledger the NOW lane just made
// verdict-bearing). Semantics ported:
//   - achieved true/false sets goal_achieved; nil leaves any existing
//     key untouched (an unjudged closure must never erase a prior
//     verdict on the row).
//   - source and goal_verdict_at are always updated; confidence is a
//     MERGE — written when provided, nil leaves any existing key
//     untouched (Python row-stamp parity; runs.StampVerdict's
//     full-replacement metadata stamp is the one where nil POPS).
//
// The read→patch→rename runs under the same flock every appender takes
// (Python parity: locked_write inside the same critical section), so a
// concurrent append cannot be lost to the rewrite.
func (r *Recorder) StampOutcomeVerdict(loopID string, achieved *bool,
	source string, confidence *float64) error {
	if loopID == "" {
		return fmt.Errorf("stamp outcome verdict: empty loop id")
	}
	path := filepath.Join(r.WorkspaceDir, "memory", "outcomes.jsonl")
	return Locked(path, func() error {
		return stampOutcomeVerdictLocked(path, loopID, achieved, source, confidence)
	})
}

func stampOutcomeVerdictLocked(path, loopID string, achieved *bool,
	source string, confidence *float64) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	lines := strings.Split(string(raw), "\n")
	target := -1
	var row map[string]any
	// Newest matching row wins — a restarted goal appends a fresh row
	// per loop.
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		var m map[string]any
		if jerr := json.Unmarshal([]byte(line), &m); jerr != nil {
			continue
		}
		if m["loop_id"] == loopID {
			target, row = i, m
			break
		}
	}
	if target < 0 {
		return fmt.Errorf("stamp outcome verdict: no row for loop %s", loopID)
	}
	// Re-stamp honesty (Jeremy decree 2026-08-10: corrections may flip
	// a verdict "but be honest about it and note they were failures at
	// run time"): overwriting an existing judged verdict preserves the
	// superseded one on the row itself. Only fires on a real re-stamp
	// of a judged row — the first verdict landing writes no history.
	if achieved != nil {
		// Key-presence gate ≈ Python's `is not None` ONLY because every
		// writer (WriteOutcome here, Python's _verdict_row) pops a
		// null before writing. Normalize anyway: a foreign row with an
		// explicit JSON null counts as unjudged, matching Python (r5).
		if prior, judged := row["goal_achieved"]; judged && prior != nil {
			hist, _ := row["verdict_history"].([]any)
			// String fields default to "" like Python's .get(key, "") —
			// a row judged by another writer (a Python row, a direct
			// Outcome.GoalAchieved) may lack them, and a JSON null where
			// Python writes "" is a cross-runtime landmine (r4).
			priorSource, _ := row["goal_verdict_source"].(string)
			priorAt, _ := row["goal_verdict_at"].(string)
			row["verdict_history"] = append(hist, map[string]any{
				"goal_achieved":           prior,
				"goal_verdict_source":     priorSource,
				"goal_verdict_at":         priorAt,
				"goal_verdict_confidence": row["goal_verdict_confidence"],
				"superseded_at":           nowISO(),
				"superseded_by":           source,
			})
		}
		row["goal_achieved"] = *achieved
	}
	row["goal_verdict_source"] = source
	// When the verdict landed: the row's own ts is record time; without
	// this the framing→verdict delay — the flow number the learning
	// pipeline divides by — is unmeasurable (Python chunk B 2026-07-31;
	// adversarial routing r3). Unjudged stamps get it too.
	row["goal_verdict_at"] = nowISO()
	// nil confidence LEAVES an existing key untouched — row-stamp
	// semantics (Python: only writes when provided), deliberately
	// DIFFERENT from runs.StampVerdict's metadata stamp where nil POPS:
	// that one is a full-replacement verdict tuple, this one is a merge.
	if confidence != nil {
		row["goal_verdict_confidence"] = *confidence
	}
	patched, err := json.Marshal(row)
	if err != nil {
		return err
	}
	lines[target] = string(patched)
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(strings.Join(lines, "\n")), 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp) // don't strand the temp beside the intact ledger
		return err
	}
	return nil
}

// LockedTailAppend holds the file's flock while fn inspects a BOUNDED tail
// of the current content and returns the rows to append; the rows land
// under the same lock with torn-tail framing. It exists for check-then-
// append flows (graduation propose) where the check must be atomic with
// the append but the file may be grown without bound by a co-resident
// process — a whole-file LockedRMW read there is the exact OOM lever the
// 8MB tail-read bound closes (r2 review MED-2). tailBytes <= 0 reads the
// whole file. A partial first line from mid-file entry is dropped before
// fn sees the tail.
func LockedTailAppend(path string, tailBytes int64, fn func(tail string) [][]byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return Locked(path, func() error {
		f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o644)
		if err != nil {
			return fmt.Errorf("open %s: %w", path, err)
		}
		defer f.Close()
		st, err := f.Stat()
		if err != nil {
			return err
		}
		off := int64(0)
		if tailBytes > 0 && st.Size() > tailBytes {
			off = st.Size() - tailBytes
		}
		if _, err := f.Seek(off, 0); err != nil {
			return err
		}
		raw, err := io.ReadAll(f)
		if err != nil {
			return err
		}
		// The framing decision reads the file's TRUE last byte — taken from
		// the raw suffix before the torn-first-line trim below.
		needsFrame := st.Size() > 0 && (len(raw) == 0 || raw[len(raw)-1] != '\n')
		if off > 0 {
			if i := bytes.IndexByte(raw, '\n'); i >= 0 {
				raw = raw[i+1:]
			}
		}
		rows := fn(string(raw))
		if len(rows) == 0 {
			return nil
		}
		var buf []byte
		if needsFrame {
			buf = append(buf, '\n')
		}
		for _, r := range rows {
			buf = append(buf, r...)
			buf = append(buf, '\n')
		}
		if _, err := f.Seek(0, io.SeekEnd); err != nil {
			return err
		}
		if _, err := f.Write(buf); err != nil {
			return fmt.Errorf("append %s: %w", path, err)
		}
		return nil
	})
}
