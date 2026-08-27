package worktree

import "github.com/slycrel/maro-orchestration/go/internal/procid"

// The three process_identity names worktree.py imports, kept behind
// one-line aliases so the call sites read the way the Python does
// (`from process_identity import owner_is_current, pid_alive as
// process_pid_alive, process_start_token`) and so a test can see at a
// glance which of them the sweep's injectable parameters replace.

func pidAlive(pid int) bool { return procid.PIDAlive(pid) }

func startToken(pid int) (string, bool) { return procid.StartToken(pid) }

func ownerIsCurrent(pid int, recordedToken any,
	alive func(int) bool, tokenReader func(int) (string, bool)) bool {
	return procid.OwnerIsCurrent(pid, recordedToken, alive, tokenReader)
}
