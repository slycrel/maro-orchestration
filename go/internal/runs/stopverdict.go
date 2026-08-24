package runs

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// stopEvidenceCap is the stuck-reason-family 800 every stop writer clips
// to. Named rather than inlined at its two sites because the two must
// not drift: metadata and the ledger row carry the same evidence, and a
// reader that finds them different has to decide which lied.
const stopEvidenceCap = 800

// StopTupleOptions is the argument shape of StampRunStopVerdict, ported
// from stamp_run_stop_verdict's keyword-only signature.
//
// It is a struct rather than positional parameters for the reason Python
// made those keywords: the call has three strings in a row that are easy
// to transpose, and a transposed StopVerdict/StopEvidence writes an
// off-vocabulary verdict with a real sentence in it — which no type
// checker and no test that does not read the file would catch.
type StopTupleOptions struct {
	// StopVerdict is a stop_verdicts value. EMPTY is meaningful: it
	// CLEARS the tuple (see ApplyStopTuple).
	StopVerdict string
	// StopEvidence is the prose behind the verdict, clipped to 800.
	StopEvidence string
	// PauseReason, when non-empty, is written. When EMPTY it leaves any
	// existing value untouched — a resumed run's fresh context has no
	// pause reason, and an unconditional clear would erase the stranded
	// sweep's post-hoc writer-died stamp (Python slice-1 review #2).
	PauseReason string
	// RunDir is the run to stamp. Required here, where Python defaults to
	// the process's active run: the Go port has no ambient current run,
	// and inventing one would make the "stamp a run that ended elsewhere"
	// case — which is the case the director's close IS — silently target
	// the wrong directory.
	RunDir string
	// RefineNote appends " [refines: <prior>]" to the evidence when a
	// DIFFERENT nonempty verdict is being replaced, inside the lock.
	RefineNote bool
	// EvidenceOut, when non-nil, receives the final written evidence,
	// captured INSIDE the lock.
	//
	// This exists because reading it back after the lock released was a
	// probe-confirmed HIGH in Python's own round-2 review: a concurrent
	// writer in that window silently substituted ITS content into the
	// caller's ledger row — the exact drift class the tuple owner exists
	// to end. A Go caller that wants the value must take it from here,
	// not re-read metadata.json.
	EvidenceOut *string
	// ReopenPayload is §13b evidence-SPECIFIC reopen data — which budget,
	// which cost estimate. An empty or nil payload POPS any stored one.
	ReopenPayload pyval.Obj
}

// ApplyStopTuple is THE stop-tuple replacement, mutating obj in place.
//
// A nonempty verdict sets both members (evidence clipped at 800); an
// EMPTY verdict pops both — this ending has no stop verdict, and a stale
// predecessor's must not stand. That is the same replace-whole-or-not-at-
// all doctrine StampVerdict follows for the goal tuple, and it is the
// reason both live behind one function instead of at each writer: in
// Python three call sites and a retry stamp each hand-rolled this pair,
// and they had already drifted.
//
// ReopenPayload rides the same doctrine. A new verdict WITHOUT a payload
// pops any stale one — even for the SAME verdict — because the payload
// always describes the stamp that wrote it, and a predecessor's numbers
// standing beside fresher evidence is a lie that reads as data. Anything
// that is not a nonempty object is dropped rather than persisted.
func ApplyStopTuple(obj *pyval.Obj, stopVerdict, stopEvidence string, reopenPayload pyval.Obj) {
	if stopVerdict != "" {
		obj.Set("stop_verdict", stopVerdict)
		obj.Set("stop_evidence", budget.Clip(stopEvidence, stopEvidenceCap))
		if len(reopenPayload) > 0 {
			obj.Set("stop_reopen_payload", reopenPayload)
		} else {
			obj.Pop("stop_reopen_payload")
		}
		return
	}
	obj.Pop("stop_verdict")
	obj.Pop("stop_evidence")
	obj.Pop("stop_reopen_payload")
}

// StampRunStopVerdict replaces a run's stop-verdict tuple in one locked
// write, and returns the metadata path it wrote.
//
// This is the writer that made metadata.json's cross-process lock stop
// being an acceptable omission in this package. Every other writer here
// targets the ACTIVE run, where the Go process is the only writer;
// this one is called with an explicit run dir precisely because the run
// belongs to someone else — the director's escalation close stamps a run
// that ended in another process, possibly in the other runtime. So it
// takes record.Locked on metadata.json+".lock", which is the same file
// Python's locked_rmw takes.
//
// NOTE, deliberately: there is NO vocabulary check here. Python does not
// have one either, and adding it would be an improvement that changes
// behaviour — the validation lives at the ledger stamp, where an
// off-vocabulary value fails to UNSTAMPED so status fallbacks apply. A
// metadata file and a ledger row that disagree about whether a verdict
// was accepted is worse than either rule applied consistently.
//
// The error is returned rather than logged-and-swallowed. Python logs a
// WARNING and returns None, under a comment recording why ("round-16
// review, 3-lens: every caller ignored the None and a failed write left
// the superseded state standing with zero trace"). Go can do better than
// a log: an ignored error is visible at the call site as `_ =`.
func StampRunStopVerdict(o StopTupleOptions) (string, error) {
	if o.RunDir == "" {
		return "", fmt.Errorf("runs: stop-verdict stamp needs a run dir")
	}
	metaPath := filepath.Join(o.RunDir, "metadata.json")
	err := record.LockedRMW(metaPath, func(old string) string {
		var existing pyval.Obj
		if old != "" {
			// A corrupt or non-object metadata degrades to a fresh
			// object rather than wedging the stamp, exactly as Python's
			// bare `except: existing = {}` does. The superseded state is
			// lost either way; refusing to write would additionally lose
			// the NEW verdict.
			if v, perr := pyval.LoadsOrdered(old); perr == nil {
				if obj, ok := v.(pyval.Obj); ok {
					existing = obj
				}
			}
		}
		evidence := o.StopEvidence
		if o.RefineNote && o.StopVerdict != "" {
			// The refinement convention: a later, more specific verdict
			// records what it refined instead of silently overwriting
			// it. Composed BEFORE the clip — clipping first and then
			// concatenating would push the note past the cap and lose
			// it, which is the failure Python's fixpoint round found the
			// other way round (it clipped, then sliced the composed
			// value, stripping the marker and usually the note too).
			// `existing.get("stop_verdict") or ""` — StrOrEmpty, not Str:
			// Str(nil) is "None", which would make every first stamp
			// read as refining a prior verdict called None.
			prior := pyval.StrOrEmpty(mustGet(existing, "stop_verdict"))
			if prior != "" && prior != o.StopVerdict {
				evidence = fmt.Sprintf("%s [refines: %s]", o.StopEvidence, prior)
			}
		}
		ApplyStopTuple(&existing, o.StopVerdict, evidence, o.ReopenPayload)
		if o.EvidenceOut != nil {
			// Read back from the OBJECT, not from the inputs: what the
			// caller needs is the value that was actually written, which
			// is the clipped-and-noted one, and which is "" after a
			// clearing stamp popped it.
			// GetString, which is `existing.get("stop_evidence", "")`.
			// pyval.Str over a popped key hands the caller the string
			// "None" and it lands in a ledger row as evidence.
			*o.EvidenceOut = existing.GetString("stop_evidence")
		}
		if o.PauseReason != "" {
			existing.Set("pause_reason", o.PauseReason)
		}
		// Publish the reference mappings from INSIDE the lock, the way
		// Python does. A stamp is a published metadata mutation, and the
		// index has to see it or the run can become unreachable by a ref
		// this write introduced.
		IndexRunDir(o.RunDir, pyval.Plain(existing).(map[string]any))
		out, derr := pyval.DumpsIndent2(existing)
		if derr != nil {
			// Returning `old` is the honest failure: the file keeps its
			// previous contents rather than being replaced by a partial
			// or empty render. The caller still gets an error below.
			return old
		}
		return out
	})
	if err != nil {
		return "", fmt.Errorf("runs: stop-verdict stamp FAILED — metadata may hold superseded state: %w", err)
	}
	if _, serr := os.Stat(metaPath); serr != nil {
		return "", fmt.Errorf("runs: stop-verdict stamp wrote nothing to %s: %w", metaPath, serr)
	}
	return metaPath, nil
}

// mustGet is Obj.Get without the ok — for the one read above where an
// absent key and a nil value are the same answer ("no prior verdict").
// Its result goes to StrOrEmpty, never to Str.
func mustGet(o pyval.Obj, key string) any {
	v, _ := o.Get(key)
	return v
}
