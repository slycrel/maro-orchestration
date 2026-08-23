package orch

import (
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The `json.dumps(obj, indent=2)` writer used by every project-ledger
// sidecar (mission.json, feature_list.json, the DOING-PID sidecar, the
// provenance records) now lives in internal/pyval. This file is the
// package-local spelling of it and nothing else.
//
// The renderer was originally written HERE, with a note saying it belonged
// in internal/pyjson and was parked because pyjson was under adversarial
// review — moving a file someone is reviewing is how a round's findings
// stop landing against the thing that was reviewed. That review (r4) has
// landed, and the task store made this renderer's third would-be consumer,
// which is well past the point where copying it again is cheap.
//
// It went to a NEW package rather than into pyjson because the two are not
// the same writer. pyjson is the single-line JSONL lane, where Python's
// callers pass compact separators; pyval is the indent=2 sidecar lane, and
// it has to carry ensure_ascii as a per-call choice because Python's
// callers genuinely differ on it — mission.json escapes, task_store does
// not. Folding them into one function would have meant one of those two
// facts getting a default it does not deserve. They still share the scalar
// spelling: pyval defers to pyjson for numbers and bools.
type (
	pyField = pyval.Field
	pyObj   = pyval.Obj
	pyList  = pyval.List
)

// DumpsIndent2 is json.dumps(v, indent=2).
func DumpsIndent2(v any) (string, error) { return pyval.DumpsIndent2(v) }

// DumpsCompactPy is a bare json.dumps(v) — one line, `", "` and `": "`.
func DumpsCompactPy(v any) (string, error) { return pyval.DumpsCompactPy(v) }

// LoadsOrdered is json.loads with key order and number literals kept.
func LoadsOrdered(text string) (any, error) { return pyval.LoadsOrdered(text) }

func pyString(s string) (string, error) { return pyval.EncodeString(s) }
func pyIntOf(v any) int                 { return pyval.IntOf(v) }
func pyFloatOf(v any) float64           { return pyval.FloatOf(v) }
func pyBool(v any) bool                 { return pyval.Bool(v) }
func pyClip(s string, n int) string     { return pyval.Clip(s, n) }
func nowISOPy(t time.Time) string       { return pyval.NowISO(t) }
