package worktree

import (
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyprobeWorktree is the one configured probe this package uses.
//
// Every scenario here WRITES — provision mkdirs under the workspace root,
// merge_back takes a real flock there — so every one of them goes through
// pyprobe's live-workspace refusal. There is no read-only variant on
// purpose: a probe that set MARO_WORKSPACE without the guard is exactly
// the shape that overwrote the live ledgers on 2026-08-16.
func pyprobeWorktree(ws string) pyprobe.Probe {
	return pyprobe.Probe{Marker: "worktree.py", Workspace: ws, Guard: worktreeGuard}
}

// worktreeGuard asserts the RESOLVER's own answer, not the environment
// variable: worktree._worktrees_root() is the path every write in this
// module lands under, and asserting on MARO_WORKSPACE would pass for a
// module whose resolver ignored it.
const worktreeGuard = `
import os as _og
import worktree as _wtg
_root = str(_wtg._worktrees_root())
_livehome = _og.path.realpath(_og.path.expanduser("~/.maro"))
if _root == _livehome or _og.path.realpath(_root).startswith(_livehome + "/"):
    raise SystemExit("pyprobe: worktree._worktrees_root() resolved INSIDE the "
                     "live workspace: %r" % _root)
`

func pyprobeArg(t *testing.T, v any) string {
	t.Helper()
	return pyprobe.Arg(t, v)
}
