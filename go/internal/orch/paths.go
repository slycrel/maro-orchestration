// Package orch ports Python orch_items.py — the PROJECT LEDGER both
// runtimes share: the NEXT.md checklist, the DECISIONS/RISKS/PROVENANCE
// section files, and the PRIORITY and lifecycle markers beside them.
//
// Why this one needs more care than a JSONL store: the ledger is
// Markdown, and an item's identity is its LINE NUMBER. Nothing carries an
// id. So "where does a line begin" is not a formatting question here — it
// decides which item a mark flips. Three measured Python behaviours drive
// the shape of this package, all of them places where the obvious Go call
// gives a different answer:
//
//   - str.splitlines() breaks on TEN separators, not one (see pytext).
//     A NEXT.md carrying a form feed is numbered differently by a naive
//     Split(s, "\n"), and every index after it points at the wrong item.
//   - Python re's \s is the full 29-code-point whitespace set; Go's
//     regexp \s is five ASCII characters. The item pattern uses \s four
//     times — for the indent, around the dash, and to trim the text — so
//     an item indented with a non-breaking space parses in Python and
//     vanishes in Go.
//   - len() on the indent counts CHARACTERS. Go's len() counts bytes.
//
// Ported deliberately including its quirks, because the file is shared:
// "- [ ]" with nothing after it is NOT an item (the text group needs one
// character) while "- [ ] " with a single trailing space IS one, with
// empty text. Both runtimes must agree on that or a drain loop and a
// status count disagree about how much work is left.
//
// NOT in this slice: run records, decompose_goal/plan_project (LLM), and
// the sheriff's wider project health. The lifecycle MARKERS are here
// because global selection reads them and skipping that check would make
// this runtime drain a project the operator paused.
package orch

import (
	"crypto/rand"
	"encoding/hex"
	"path/filepath"
	"time"
)

// Item states, verbatim from Python. The state is a single character
// inside the checkbox.
const (
	StateTodo    = " "
	StateDoing   = "~"
	StateDone    = "x"
	StateBlocked = "!"
)

// Lifecycle marker files. Manual: no code path in either runtime writes
// them. An operator touches one to pull a project out of rotation.
const (
	failedMarker = ".maro-failed"
	pausedMarker = ".maro-paused"
)

// ProjectsRoot is Python config.projects_dir(): <workspace>/projects.
//
// Python's orch_items.projects_root() has a second branch for the
// MARO_ORCH_ROOT pin, which repoints the whole ledger at a repo-local
// directory. That variable is NOT read by this port, deliberately and for
// the same reason MARO_HOME is not: the 2026-08-16 live-ledger incident
// was a second store-routing variable disagreeing with the first. One
// resolution order, passed in as an argument.
func ProjectsRoot(ws string) string { return filepath.Join(ws, "projects") }

// MemoryDir is Python orch_items.memory_dir(): <workspace>/memory. Its
// Python twin has three resolution sources ahead of the workspace (a
// ContextVar, MARO_MEMORY_DIR, then config) for the same reason
// ProjectsRoot has MARO_ORCH_ROOT, and this port reads none of them —
// one resolution order, passed in as an argument.
func MemoryDir(ws string) string { return filepath.Join(ws, "memory") }

// OutputRoot is Python config.output_dir(): <workspace>/output.
func OutputRoot(ws string) string { return filepath.Join(ws, "output") }

// RunsRoot is Python orch_items.runs_root(): <workspace>/output/runs.
func RunsRoot(ws string) string { return filepath.Join(OutputRoot(ws), "runs") }

// ProjectDir and the per-project file paths.
func ProjectDir(ws, slug string) string {
	return filepath.Join(ProjectsRoot(ws), slug)
}

func NextPath(ws, slug string) string {
	return filepath.Join(ProjectDir(ws, slug), "NEXT.md")
}

func DecisionsPath(ws, slug string) string {
	return filepath.Join(ProjectDir(ws, slug), "DECISIONS.md")
}

func RisksPath(ws, slug string) string {
	return filepath.Join(ProjectDir(ws, slug), "RISKS.md")
}

func ProvenancePath(ws, slug string) string {
	return filepath.Join(ProjectDir(ws, slug), "PROVENANCE.md")
}

func PriorityPath(ws, slug string) string {
	return filepath.Join(ProjectDir(ws, slug), "PRIORITY")
}

// doingPIDsPath is the sidecar recording which PID set each DOING item.
// It lives BESIDE NEXT.md rather than inside it because the "- [state]"
// line format has many parsers in both runtimes and a sidecar changes
// none of them.
func doingPIDsPath(ws, slug string) string {
	return filepath.Join(ProjectDir(ws, slug), ".doing_pids.json")
}

// NowUTCISO is Python orch_items.now_utc_iso(): second precision, literal
// Z. Note this is a DIFFERENT stamp from the record package's
// microsecond+offset one — the section files carry this spelling and a
// reader that greps for it would miss the other.
func NowUTCISO() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05Z")
}

// NewRunID is Python orch_items.new_run_id(): run-<compact UTC stamp>-<8
// hex>. Python takes the hex from uuid4, i.e. from the OS CSPRNG; this
// reads the same source directly rather than deriving from a clock, so
// two runs starting inside the same second still differ.
func NewRunID() string {
	var b [4]byte
	// crypto/rand.Read is documented never to return an error as of Go
	// 1.24 (the package panics itself if the system source fails), so
	// there is no degraded path to design here — and a degraded path that
	// minted a predictable id would be worse than the panic, since two
	// runs sharing an id overwrite each other's record.
	_, _ = rand.Read(b[:])
	return "run-" + time.Now().UTC().Format("20060102T150405Z") + "-" + hex.EncodeToString(b[:])
}
