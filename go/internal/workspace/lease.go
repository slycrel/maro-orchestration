package workspace

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Lease is the admission fact: one process per root (D12). The lease file
// holds the PID, a monotonic process epoch, and the host. Every command a
// later step submits carries the epoch, so a sequencer can refuse commands
// from a stale process even if that process is somehow still alive.
type Lease struct {
	PID     int       `json:"pid"`
	Epoch   uint64    `json:"epoch"`
	Host    string    `json:"host"`
	Started time.Time `json:"started"`
	path    string
}

// ErrLeaseHeld is returned when a live process holds the root.
var ErrLeaseHeld = errors.New("workspace: another process holds this root")

const leaseFile = "lease.json"
const epochFile = "epoch"

// alive reports whether pid is a running process we may signal. Tests inject
// it to simulate a dead holder.
var alive = func(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := syscall.Kill(pid, 0)
	return err == nil || errors.Is(err, syscall.EPERM)
}

// Acquire takes the lease for this process, or returns ErrLeaseHeld. A lease
// whose holder is dead is taken over with epoch+1. The epoch file is the
// durable counter; it only ever increases.
func Acquire(r *Root) (*Lease, error) {
	lp, err := r.Path(leaseFile)
	if err != nil {
		return nil, err
	}
	ep, err := r.Path(epochFile)
	if err != nil {
		return nil, err
	}
	if raw, err := os.ReadFile(lp); err == nil {
		var cur Lease
		if json.Unmarshal(raw, &cur) == nil && alive(cur.PID) && cur.PID != os.Getpid() {
			return nil, fmt.Errorf("%w: pid %d epoch %d since %s", ErrLeaseHeld, cur.PID, cur.Epoch, cur.Started.Format(time.RFC3339))
		}
		// dead holder (or malformed file): fall through and take over
	}
	epoch, err := bumpEpoch(ep)
	if err != nil {
		return nil, err
	}
	host, _ := os.Hostname()
	l := &Lease{PID: os.Getpid(), Epoch: epoch, Host: host, Started: time.Now().UTC(), path: lp}
	raw, _ := json.Marshal(l)
	tmp := lp + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return nil, err
	}
	if err := os.Rename(tmp, lp); err != nil {
		return nil, err
	}
	return l, nil
}

func bumpEpoch(path string) (uint64, error) {
	var cur uint64
	if raw, err := os.ReadFile(path); err == nil {
		if n, err := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64); err == nil {
			cur = n
		}
	}
	next := cur + 1
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(strconv.FormatUint(next, 10)+"\n"), 0o644); err != nil {
		return 0, err
	}
	if err := os.Rename(tmp, path); err != nil {
		return 0, err
	}
	return next, nil
}

// Release removes the lease file if this process still holds it. The epoch
// is never decremented.
func (l *Lease) Release() error {
	raw, err := os.ReadFile(l.path)
	if err != nil {
		return nil
	}
	var cur Lease
	if json.Unmarshal(raw, &cur) == nil && cur.PID == l.PID && cur.Epoch == l.Epoch {
		return os.Remove(l.path)
	}
	return nil
}

// Current reads the lease on disk without acquiring it (for status/doctor).
func Current(r *Root) (*Lease, bool, error) {
	lp, err := r.Path(leaseFile)
	if err != nil {
		return nil, false, err
	}
	raw, err := os.ReadFile(lp)
	if errors.Is(err, os.ErrNotExist) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	var cur Lease
	if err := json.Unmarshal(raw, &cur); err != nil {
		return nil, false, err
	}
	cur.path = lp
	return &cur, alive(cur.PID), nil
}
