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
	"os"
	"path/filepath"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
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

// EnsureMemoryDir and EnsureProjectsRoot are the mkdir-ING twins of the
// two joins above, and they exist because in Python the mkdir lives
// INSIDE the name.
//
//	config.memory_dir():    p = workspace_root() / "memory"
//	                        p.mkdir(parents=True, exist_ok=True)
//	                        return p
//
// Five of config.py's path helpers do this — memory, output, projects,
// skills, personas — while secrets_dir and playbook_path do not. So
// "resolve the memory directory" is not a pure function in the original:
// it CREATES, and it can FAIL, and both are observable. syshealth r3
// rated the first instance HIGH, because CPython aborts `run_and_persist`
// before running a single probe on a workspace whose memory/ cannot be
// created, while the port ran every probe and reported them all healthy.
// 47 fixtures shared the unstated assumption that memory/ already existed.
//
// The pure joins are KEPT rather than replaced. Not every Go site stands
// for a Python line that called the creating helper — some are building a
// path to hand to a writer that mkdirs for itself, and giving those the
// side effect would be a divergence in the other direction. The rule for
// choosing is the Python line, not the Go convenience: if the line it
// ports says `memory_dir()`, it wants this one.
//
// NewDirMode is 0o777, which is `Path.mkdir()`'s default — the umask
// narrows it, and reproducing the umask is the point (0o775 here, and
// 0o755 under a service with umask 022, exactly as Python would).
//
// RESIDUAL, named and deliberate: Python's `orch_items.memory_dir()`
// wraps the config call in a try and, when the mkdir raises, RELOCATES
// the whole memory store to orch_root()/memory and then to cwd/memory.
// This port does not relocate — it returns the error. Porting the
// fallback means porting MARO_ORCH_ROOT, which this package declines for
// the reason ProjectsRoot's comment gives: one resolution order, passed
// in as an argument, after the 2026-08-16 live-ledger incident. The
// divergence is reachable only on a workspace whose memory/ cannot be
// created, where Python's answer is to silently write somewhere else —
// which is the behaviour that incident was about. Pinned as a knowngap
// rather than left unsaid.
func EnsureMemoryDir(ws string) (string, error) {
	dir := MemoryDir(ws)
	if err := os.MkdirAll(dir, record.NewDirMode); err != nil {
		return "", err
	}
	return dir, nil
}

// EnsureProjectsRoot is config.projects_dir(), which mkdirs the same way.
// Note what it does NOT create: `projects_dir() / slug` is a plain join in
// the Python too, so a per-project directory is created by whatever
// writes into it, not by resolving its path.
func EnsureProjectsRoot(ws string) (string, error) {
	dir := ProjectsRoot(ws)
	if err := os.MkdirAll(dir, record.NewDirMode); err != nil {
		return "", err
	}
	return dir, nil
}

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
