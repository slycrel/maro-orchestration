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
//     subject, summary, audience, context, loop_id}, plus the two keys
//     Python emits only when non-empty: note and related_ids.
//
// Data-retention doctrine carries over: nothing here DELETES a row.
// rotate.go does carry a rotation verb (Python's captains_log
// ._maybe_rotate), and it does not delete: the head is written to
// captains_log.<UTC-stamp>.jsonl and only then is the active file
// rewritten with the last captains_log.rotate_keep lines, so every row
// survives one of the two files. There is no delete or compact verb
// (adversarial r7 MEDIUM: this block used to claim "no delete, rotate,
// or compact verb here at all" while rotate.go sat in the same package).
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

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
)

// lockTimeout matches Python file_lock's default deadline (30s).
const lockTimeout = 30 * time.Second

// Recorder appends records under one workspace. Construct via New so the
// resolved path is explicit at the call site — the 2026-08-16 live-ledger
// overwrite happened because a writer ASSUMED its store; this type makes
// the store an argument.
type Recorder struct {
	WorkspaceDir string

	// LoopID is the ambient loop attribution, the port of Python's
	// _current_loop_id contextvar. log_event() reads it whenever a caller
	// passes no explicit loop_id, which is how call sites deep in the
	// execution stack — skills.py, evolver.py, knowledge_lens.py, and
	// rotation — land on the right run without threading the id through
	// every signature. Without it those rows are written with NO loop_id at
	// all, and on a shared store that is a row Python can attribute and this
	// runtime cannot (adversarial r6).
	//
	// It is a FIELD, not a goroutine-local, and that is a deliberate
	// divergence: contextvars are per-task, so Python can hold one global
	// and stay correct under concurrency. The Go equivalent that preserves
	// that property is WithLoopID below, which copies the Recorder rather
	// than mutating it — so two concurrent runs hold two Recorders and
	// neither can see the other's id. Setting this field directly on a
	// Recorder that is shared across runs would cross-attribute; don't.
	LoopID string
}

func New(workspaceDir string) *Recorder {
	return &Recorder{WorkspaceDir: workspaceDir}
}

// WithLoopID returns a copy of the Recorder that attributes otherwise
// unattributed events to loopID — the port of Python's loop_id_scope,
// spelled as a value rather than a scope because a copy cannot leak across
// goroutines the way a set/restore pair can. An explicit loopID argument at
// the call site still wins, exactly as the kwarg does there.
func (r *Recorder) WithLoopID(loopID string) *Recorder {
	c := *r
	c.LoopID = loopID
	return &c
}

func (r *Recorder) memoryDir() (string, error) {
	dir := filepath.Join(r.WorkspaceDir, "memory")
	if err := os.MkdirAll(dir, NewDirMode); err != nil {
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
	id, _, err := r.WriteOutcomeWithLog(o)
	return id, err
}

// WriteOutcomeWithLog is WriteOutcome plus the two human-readable surfaces
// Python's record_outcome also maintains: today's daily markdown log and
// the MEMORY.md index. It returns the warnings those raised.
//
// They are warnings, not errors, and only the LEDGER append can fail the
// call — that is Python's split too (its index update is wrapped in a bare
// except). What Go does not copy is the silence: a person whose MEMORY.md
// has been stale for months should be told, so the failures come back to
// the caller instead of being swallowed here.
//
// The order is Python's: ledger append, then daily log, then index. The
// index READS the ledger, so it must run after the append or it renders a
// stats block that is one run behind the file it sits next to.
func (r *Recorder) WriteOutcomeWithLog(o Outcome) (string, []string, error) {
	dir, err := r.memoryDir()
	if err != nil {
		return "", nil, err
	}
	id := NewID()
	fail := o.FailChain
	if fail == nil {
		fail = []string{}
	}
	taskType := orDefault(o.TaskType, "general")
	recordedAt := nowISO()
	row := map[string]any{
		"outcome_id":        id,
		"goal":              o.Goal,
		"status":            o.Status,
		"summary":           o.Summary,
		"task_type":         taskType,
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
		"recorded_at":       recordedAt,
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
	if err := r.appendJSONL(filepath.Join(dir, "outcomes.jsonl"), row,
		outcomeKeyOrder); err != nil {
		return "", nil, err
	}
	var warns []string
	if err := appendDailyLog(dir, o, taskType, recordedAt); err != nil {
		// Named precisely: this hole is PERMANENT. The daily log is
		// append-per-outcome, so unlike MEMORY.md nothing ever rebuilds it
		// and no later run heals the gap.
		warns = append(warns, "daily log NOT written for outcome "+id+
			" — this run is permanently absent from the day's record: "+err.Error())
	}
	if err := updateMemoryIndex(dir); err != nil {
		warns = append(warns, "MEMORY.md index NOT updated after outcome "+id+
			" (the next successful outcome rewrites it in full): "+err.Error())
	}
	return id, warns, nil
}

// outcomeKeyOrder is the order this row is built above, which is the
// order Python's record_outcome builds its own — the literal order a
// dict then hands to json.dumps.
var outcomeKeyOrder = []string{
	"outcome_id", "goal", "status", "summary", "task_type", "model",
	"loop_id", "project", "tokens_in", "tokens_out", "elapsed_ms",
	"dry_run", "lessons", "failure_chain", "recovery_steps", "recorded_at",
	"measurement_class", "stop_verdict", "stuck_reason",
	"goal_achieved", "goal_verdict_summary", "goal_verdict_source",
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
	// Skill lifecycle decisions. Added in the r3 fixes: slice 3b/3c
	// introduced five emitters and did not extend this map, so every
	// promotion, demotion, circuit trip and island cull this runtime
	// recorded was stamped "system" and dropped from `maro-log --audience
	// user`, the viz activity section and the health lane — the decision
	// ported faithfully and its announcement did not.
	//
	// SKILL_CIRCUIT_HALF_OPEN is deliberately ABSENT, matching Python:
	// half-open is a probation state, not a decision, and the trip and
	// the recovery that bracket it are both here.
	"SKILL_PROMOTED":       true,
	"SKILL_DEMOTED":        true,
	"SKILL_CIRCUIT_OPEN":   true,
	"SKILL_CIRCUIT_CLOSED": true,
	"ISLAND_CULLED":        true,
	"EVOLVER_APPLIED":      true,
	"EVOLVER_REVERTED":     true,
	"EVOLVER_VERDICT":      true,
	"GRADUATION_PROPOSED":  true,
	// The playbook CURATION pass is user-surfaced; a single append
	// (PLAYBOOK_UPDATED) is not. Different verbs, different audiences.
	"PLAYBOOK_CURATED":    true,
	"GRADUATION_VERIFIED": true,

	// LOG_ROTATED is deliberately ABSENT, verified against the live
	// frozenset rather than read off the source: it appears in Python's
	// EVENT_TYPES (the set of valid names) and NOT in USER_SURFACED_EVENTS,
	// so captains_log.event_audience stamps it "system". Rotation is
	// bookkeeping about the store, not a decision anyone has to act on.
	//
	// This entry read `true` for one commit on a misread of that list. Two
	// things caught it: the audience census tripwire, which refused an
	// emitted type it had never been told about, and the differential
	// against the row Python's own _maybe_rotate writes. A hand-built
	// expectation would have agreed with the mistake.
}

// Event appends one captain's-log entry. audience comes from the
// event-type registry above, matching Python log_event's stamping.
func (r *Recorder) Event(eventType, subject, summary string, context map[string]any, loopID string) error {
	return r.EventRelated(eventType, subject, summary, context, loopID, nil)
}

// EventRelated is Event with log_event's related_ids — the linkage a reader
// uses to find every row about one subject ("skill:<id>", "lesson:<id>").
// It is a SEPARATE field from loop_id: passing a subject linkage as the
// loop id would file the row under a run that does not exist, and the
// linkage itself would be lost.
func (r *Recorder) EventRelated(eventType, subject, summary string, context map[string]any,
	loopID string, relatedIDs []string) error {
	return r.EventNoted(eventType, subject, summary, context, "", loopID, relatedIDs)
}

// EventNoted is EventRelated with log_event's `note` — a free-text
// diagnostic that rides as a TOP-LEVEL row key.
//
// The key placement is the whole point. Python's render_entry reads
// `entry.get("note")` and prints a "Note:" line under the summary;
// nothing renders context values. Filing a failure reason under
// context["note"] therefore made it survive query_log's search (which
// flattens context) and vanish from every human-facing render — so a
// SKILL_CIRCUIT_OPEN row arrived without the one piece of evidence a
// circuit event exists to carry (adversarial r3 2026-08-23, M2).
func (r *Recorder) EventNoted(eventType, subject, summary string, context map[string]any,
	note, loopID string, relatedIDs []string) error {
	dir, err := r.memoryDir()
	if err != nil {
		return err
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
	}
	// Python writes `if context:` — an EMPTY context is omitted, not
	// emitted as {}. Same for the note and the optional linkages.
	if len(context) > 0 {
		entry["context"] = context
	}
	if note != "" {
		entry["note"] = note
	}
	// `if loop_id is None: loop_id = _current_loop_id.get()` — the explicit
	// argument wins, the ambient one fills in, and only a genuinely empty
	// result omits the key.
	if loopID == "" {
		loopID = r.LoopID
	}
	if loopID != "" {
		entry["loop_id"] = loopID
	}
	if len(relatedIDs) > 0 {
		entry["related_ids"] = relatedIDs
	}
	path := filepath.Join(dir, "captains_log.jsonl")
	if err := r.appendJSONL(path, entry, captainsLogKeyOrder); err != nil {
		return err
	}
	// Size-gated rotation rides on the append, exactly as Python's
	// log_event calls _maybe_rotate. It never fails the event it was
	// triggered by — the entry is already durable at this point — so its
	// problems go to stderr the way Python's logger.warning does rather
	// than up this return, which ~40 call sites share.
	r.maybeRotateCaptainsLog(path)
	return nil
}

// captainsLogKeyOrder is the order Python's log_event builds its dict,
// which is the order json.dumps then emits. handle_id sits between
// audience and context there; this runtime has no handle-id ContextVar
// yet, so the slot is simply never filled — a key this port does not
// write still rides in its Python position if another runtime's row is
// ever re-emitted through pyjson.Ordered's unknown-key tail.
var captainsLogKeyOrder = []string{
	"timestamp", "event_type", "subject", "summary", "audience",
	"handle_id", "context", "note", "loop_id", "related_ids",
}

// appendJSONL appends one row under the same advisory flock protocol as
// Python file_lock.locked_append: exclusive flock on "<name>.lock",
// bounded wait, fail-closed on timeout. It also frames a torn tail — a
// prior crash can leave the file without a final LF, and appending
// straight onto that fragment fuses two rows into one malformed line;
// an LF first strands the fragment as its own row and keeps ours
// readable (ported from locked_append's 2026-08 hardening).
// It emits through pyjson, not encoding/json: both ledgers it writes are
// read by the Python runtime, and the generic encoder alphabetizes keys,
// HTML-escapes the "->" in every transition summary, and spells a whole
// float without its ".0" (which changes the type json.loads parses). The
// r2 fixes closed exactly this class on the skills-manifest rail and
// recorded it as "the one writer still on plain json.Marshal" — it was
// one of two, and the other carries every skill-lifecycle event
// (adversarial r3 2026-08-23, L2).
func (r *Recorder) appendJSONL(path string, row map[string]any, keyOrder []string) error {
	line, err := pyjson.Ordered(row, keyOrder)
	if err != nil {
		return fmt.Errorf("marshal row for %s: %w", filepath.Base(path), err)
	}
	return AppendRawLine(path, []byte(line))
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
// Locked runs fn while holding the store's advisory lock.
//
// NOT REENTRANT. It flocks a FRESH descriptor per call, and flock is
// per-open-file-description, so a nested Locked on the same path from the
// same process blocks against ITSELF until the lock deadline and then
// fails. A caller that needs one critical section spanning several locked
// helpers must take the lock once and call their in-lock cores.
func Locked(path string, fn func() error) error {
	lockPath := path + ".lock"
	// Python's acquisition mkdirs the lock's parent unconditionally
	// (file_lock.py:144), which is why a locked write into a cold
	// workspace works there. Go's did not, so the first write to any store
	// in a fresh workspace failed on the missing directory — a hole
	// orch/mission.go had already papered over at ONE call site with a
	// comment saying it belonged down here and that "every direct
	// AppendRawLine caller has the same hole until it moves". It has moved.
	if err := os.MkdirAll(filepath.Dir(lockPath), 0o755); err != nil {
		return fmt.Errorf("create lock dir for %s: %w", lockPath, err)
	}
	lf, err := os.OpenFile(lockPath, os.O_CREATE|os.O_WRONLY, 0o666)
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

		f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
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

// AppendUnlockedLine appends one pre-serialized row with a single O_APPEND
// write and NO lock, NO lock file, and NO torn-tail framing.
//
// This is not a shortcut; it is the contract of exactly one ledger.
// observe.write_event carries the rationale in Python, and all three
// clauses are load-bearing:
//
//   - Atomicity comes from the row's SIZE, not from a lock. Every field
//     write_event emits is length-capped (goal 80, step 120, detail 200,
//     three pathologies at 40/160), so the line stays far under PIPE_BUF
//     and a single O_APPEND write of that size is atomic on Linux.
//   - It must never block. It is on the hot path after every step, and a
//     bounded-but-loud stall in the surface that REPORTS stalls is the
//     wrong trade in the one place it is.
//   - It must never re-enter the lock machinery. Python's file-lock
//     timeout reporter calls write_event; taking a lock to report a lock
//     timeout recurses. Go has no such reporter yet, which makes this the
//     cheap moment to keep the door shut rather than the expensive one.
//
// The visible consequence of getting this wrong is not subtle and is also
// not loud: routing this ledger through AppendRawLine leaves a
// `events.jsonl.lock` sidecar in every workspace that Python never
// creates — a file that differential harnesses tend to skip by name.
//
// Callers get Python's semantics: best-effort, errors are the caller's to
// swallow.
func AppendUnlockedLine(path string, raw []byte) error {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o666)
	if err != nil {
		return fmt.Errorf("open %s: %w", path, err)
	}
	defer f.Close()
	if _, err := f.Write(append(append([]byte{}, raw...), '\n')); err != nil {
		return fmt.Errorf("append %s: %w", path, err)
	}
	return nil
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
// It RETURNS the row as written. Python's stamp_outcome_verdict ends by
// handing that row to two side-effects that ride on the verdict —
// contradiction candidates and skill-injection attribution — and both
// decide from the row's own fields (verdict_trust reads them). A caller
// that reconstructed the row from the arguments it passed in would be
// deciding from its intent rather than from what landed, and would drift
// the moment this function's merge rules change. Go's import graph forbids
// calling those side-effects from here (skills imports record, not the
// other way round), so the row travels to the caller instead; see
// skills.StampVerdictWithAttribution, which is what production calls.
func (r *Recorder) StampOutcomeVerdict(loopID string, achieved *bool,
	source string, confidence *float64) (map[string]any, error) {
	if loopID == "" {
		return nil, fmt.Errorf("stamp outcome verdict: empty loop id")
	}
	path := filepath.Join(r.WorkspaceDir, "memory", "outcomes.jsonl")
	var row map[string]any
	err := Locked(path, func() error {
		var lerr error
		row, lerr = stampOutcomeVerdictLocked(path, loopID, achieved, source, confidence)
		return lerr
	})
	if err != nil {
		return nil, err
	}
	return row, nil
}

func stampOutcomeVerdictLocked(path, loopID string, achieved *bool,
	source string, confidence *float64) (map[string]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	lines := strings.Split(string(raw), "\n")
	target := -1
	var row map[string]any
	var rowKeys []string
	// Newest matching row wins — a restarted goal appends a fresh row
	// per loop.
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" {
			continue
		}
		var m map[string]any
		// UseNumber keeps every stored literal EXACTLY as written. Plain
		// Unmarshal turns each into a float64, so re-emitting the row
		// re-types every number on it — a foreign row's `cost: 1.0` came
		// back as `1`, and an integer counter would have come back as
		// `42.0`. This function patches three keys; it must not rewrite
		// the rest of someone else's row (adversarial r3, L3).
		dec := json.NewDecoder(strings.NewReader(line))
		dec.UseNumber()
		if jerr := dec.Decode(&m); jerr != nil {
			continue
		}
		if m["loop_id"] == loopID {
			target, row = i, m
			rowKeys = orderedKeysOf(line)
			break
		}
	}
	if target < 0 {
		return nil, fmt.Errorf("stamp outcome verdict: no row for loop %s", loopID)
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
	// The row keeps the key ORDER it had on disk, with keys this function
	// added riding after it in the order the code above assigns them —
	// which is what Python's dict does when json.loads' order is extended
	// by assignment. Without it a patched foreign row comes back
	// alphabetized, a whole-file rewrite of someone else's formatting.
	//
	// Residual, recorded: entries INSIDE verdict_history are nested maps,
	// so they render with sorted keys — the same nested-order divergence
	// already accepted elsewhere in this port. Byte-level only; every
	// value and type is preserved.
	patched, err := pyjson.Ordered(row, append(rowKeys,
		"verdict_history", "goal_achieved", "goal_verdict_source",
		"goal_verdict_at", "goal_verdict_confidence"))
	if err != nil {
		return nil, err
	}
	lines[target] = patched
	// AtomicWrite, not WriteFile+Rename: this rewrites the WHOLE outcomes
	// ledger, and it was the fourth copy of the un-fsynced temp+rename
	// pattern the r2 fixes were supposed to have collapsed. It also
	// widened an operator's 0600 ledger to 0644 on every stamp, where
	// Python's atomic_write re-applies the target's existing mode
	// (adversarial r3, L3).
	if err := AtomicWrite(path, []byte(strings.Join(lines, "\n"))); err != nil {
		return nil, err
	}
	return row, nil
}

// orderedKeysOf recovers a JSON object's key order from its source text,
// which decoding into a map throws away.
func orderedKeysOf(line string) []string {
	dec := json.NewDecoder(strings.NewReader(line))
	tok, err := dec.Token()
	if err != nil {
		return nil
	}
	if d, ok := tok.(json.Delim); !ok || d != '{' {
		return nil
	}
	var keys []string
	for dec.More() {
		keyTok, err := dec.Token()
		if err != nil {
			return keys
		}
		key, ok := keyTok.(string)
		if !ok {
			return keys
		}
		keys = append(keys, key)
		var skip json.RawMessage
		if err := dec.Decode(&skip); err != nil {
			return keys
		}
	}
	return keys
}

// LockedTailAppend holds the file's flock while fn inspects a BOUNDED tail
// of the current content and returns the rows to append; the rows land
// under the same lock with torn-tail framing. It exists for check-then-
// append flows whose check is a TAIL-N predicate (graduation propose):
// there, a whole-file LockedRMW read pays unbounded memory for a bounded
// question (r2 review MED-2). The surface-wide rule (r3 review): bounded
// tail where the semantics are tail-N; whole-file where the semantics are
// whole-file (keyed merges, full-history dedup — bounding those would
// silently DROP rows, worse than the read cost). tailBytes <= 0 reads the
// whole file. A partial first line from mid-file entry is dropped before
// fn sees the tail; a tail cut exactly on a line boundary conservatively
// drops one whole line, and a single line longer than tailBytes reaches
// fn as an unparseable fragment.
func LockedTailAppend(path string, tailBytes int64, fn func(tail string) [][]byte) error {
	if err := os.MkdirAll(filepath.Dir(path), NewDirMode); err != nil {
		return err
	}
	return Locked(path, func() error {
		f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o666)
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
