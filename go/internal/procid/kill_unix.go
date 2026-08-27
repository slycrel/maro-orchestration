//go:build unix

package procid

import (
	"math"
	"syscall"
)

// maxCInt / minCInt are the bounds Python's os.kill argument clause
// enforces: the pid is converted with the `i` (C int) code, so anything
// outside raises OverflowError before a syscall happens.
const (
	maxCInt = math.MaxInt32
	minCInt = math.MinInt32
)

func kill(pid, sig int) error { return syscall.Kill(pid, syscall.Signal(sig)) }

func isPermission(err error) bool { return err == syscall.EPERM }
