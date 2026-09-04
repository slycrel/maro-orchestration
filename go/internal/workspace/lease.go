package workspace

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Lease is the admission fact: one process per root (D12). Exclusivity is an
// OS advisory lock (flock) on lease.lock held for the process lifetime — the
// kernel releases it when the holder dies, so "stale" is not something this
// code infers from PIDs or timestamps. lease.json is the human-readable
// record of who holds it; epoch is a monotonic counter every later command
// carries, so a sequencer can refuse commands from a stale process.
type Lease struct {
	PID     int       `json:"pid"`
	Epoch   uint64    `json:"epoch"`
	Host    string    `json:"host"`
	Started time.Time `json:"started"`

	lock      *os.File
	root      *Announced
	Recovered string // non-empty when a prior lease.json was malformed and was replaced under the lock
}

var (
	// ErrLeaseHeld: a live process holds the root.
	ErrLeaseHeld = errors.New("workspace: another process holds this root")
	// ErrLeaseCorrupt: lease or epoch state is unreadable in a way the lock
	// cannot resolve (a directory at the path, a permission error, an epoch
	// that does not parse). Refuse; never guess.
	ErrLeaseCorrupt = errors.New("workspace: lease state corrupt or unreadable")
)

const (
	lockFile  = "lease.lock"
	leaseFile = "lease.json"
	epochFile = "epoch"
)

// Acquire takes the exclusive lock for this process or returns ErrLeaseHeld.
// Under the lock it bumps the epoch durably and publishes lease.json.
func Acquire(a *Announced) (*Lease, error) {
	lf, err := os.OpenFile(a.Path(lockFile), os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrLeaseCorrupt, err)
	}
	if err := syscall.Flock(int(lf.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lf.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			cur, _ := readLeaseFile(a.Path(leaseFile))
			if cur != nil {
				return nil, fmt.Errorf("%w: pid %d epoch %d on %s since %s", ErrLeaseHeld, cur.PID, cur.Epoch, cur.Host, cur.Started.Format(time.RFC3339))
			}
			return nil, ErrLeaseHeld
		}
		return nil, fmt.Errorf("%w: flock: %v", ErrLeaseCorrupt, err)
	}
	// We hold the lock: any prior lease.json belongs to a dead process.
	l := &Lease{lock: lf, root: a}
	if prior, perr := readLeaseFile(a.Path(leaseFile)); perr != nil && !errors.Is(perr, os.ErrNotExist) {
		if errors.Is(perr, errMalformed) {
			l.Recovered = perr.Error()
		} else {
			lf.Close()
			return nil, fmt.Errorf("%w: %v", ErrLeaseCorrupt, perr)
		}
	} else if prior != nil {
		_ = prior // dead holder; superseded below
	}
	epoch, err := bumpEpoch(a.Path(epochFile))
	if err != nil {
		lf.Close()
		return nil, err
	}
	host, _ := os.Hostname()
	l.PID, l.Epoch, l.Host, l.Started = os.Getpid(), epoch, host, time.Now().UTC()
	raw, _ := json.Marshal(l)
	if err := WriteFileDurable(a.Path(leaseFile), raw, 0o644); err != nil {
		lf.Close()
		return nil, err
	}
	return l, nil
}

var errMalformed = errors.New("lease.json malformed")

// readLeaseFile distinguishes: absent (os.ErrNotExist), malformed
// (errMalformed — the lock decides what that means), and unreadable (any
// other error: a directory, a permission problem).
func readLeaseFile(path string) (*Lease, error) {
	st, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if st.IsDir() {
		return nil, fmt.Errorf("%s is a directory", path)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.DisallowUnknownFields()
	var cur Lease
	if err := dec.Decode(&cur); err != nil || cur.PID <= 0 || cur.Epoch == 0 || cur.Started.IsZero() {
		return nil, fmt.Errorf("%w: %s", errMalformed, path)
	}
	return &cur, nil
}

// bumpEpoch reads the durable counter strictly, refuses anything it cannot
// parse or that would wrap, writes next durably, and reads it back.
func bumpEpoch(path string) (uint64, error) {
	var cur uint64
	raw, err := os.ReadFile(path)
	switch {
	case errors.Is(err, os.ErrNotExist):
		cur = 0
	case err != nil:
		return 0, fmt.Errorf("%w: epoch: %v", ErrLeaseCorrupt, err)
	default:
		n, perr := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
		if perr != nil {
			return 0, fmt.Errorf("%w: epoch file %q does not parse — refusing to reset a monotonic counter", ErrLeaseCorrupt, strings.TrimSpace(string(raw)))
		}
		cur = n
	}
	if cur == math.MaxUint64 {
		return 0, fmt.Errorf("%w: epoch at MaxUint64 cannot advance", ErrLeaseCorrupt)
	}
	next := cur + 1
	if err := WriteFileDurable(path, []byte(strconv.FormatUint(next, 10)+"\n"), 0o644); err != nil {
		return 0, err
	}
	back, err := os.ReadFile(path)
	if err != nil {
		return 0, err
	}
	got, err := strconv.ParseUint(strings.TrimSpace(string(back)), 10, 64)
	if err != nil || got != next {
		return 0, fmt.Errorf("%w: epoch read-back %q != %d", ErrLeaseCorrupt, strings.TrimSpace(string(back)), next)
	}
	return next, nil
}

// Root is the announced root this lease protects. A journal opened from a
// lease inherits its root; there is no way to open a journal on another.
func (l *Lease) Root() *Announced { return l.root }

// Live reports whether this process still holds the lock. Released leases
// are dead; every writer re-checks this before acting.
func (l *Lease) Live() bool { return l != nil && l.lock != nil && l.Epoch > 0 }

// Release removes lease.json if it still describes this lease and drops the
// lock. Errors are returned with their path; the epoch is never decremented.
func (l *Lease) Release() error {
	if l.lock == nil {
		return nil
	}
	var first error
	path := l.root.Path(leaseFile)
	cur, err := readLeaseFile(path)
	switch {
	case err == nil && cur.PID == l.PID && cur.Epoch == l.Epoch:
		if rerr := os.Remove(path); rerr != nil {
			first = fmt.Errorf("workspace: release %s: %w", path, rerr)
		}
	case err != nil && !errors.Is(err, os.ErrNotExist):
		first = fmt.Errorf("workspace: release could not establish ownership of %s: %w", path, err)
	}
	if err := syscall.Flock(int(l.lock.Fd()), syscall.LOCK_UN); err != nil && first == nil {
		first = fmt.Errorf("workspace: unlock: %w", err)
	}
	if err := l.lock.Close(); err != nil && first == nil {
		first = err
	}
	l.lock = nil
	return first
}

// Status reports the lease on disk and whether it is live. Liveness is asked
// of the lock, not inferred from the PID: a non-blocking lock attempt that
// fails means a live holder.
func Status(a *Announced) (lease *Lease, live bool, err error) {
	lease, err = readLeaseFile(a.Path(leaseFile))
	if errors.Is(err, os.ErrNotExist) {
		lease, err = nil, nil
	} else if err != nil && !errors.Is(err, errMalformed) {
		return nil, false, err
	} else if errors.Is(err, errMalformed) {
		err = nil
	}
	lf, oerr := os.OpenFile(a.Path(lockFile), os.O_CREATE|os.O_RDWR, 0o644)
	if oerr != nil {
		return lease, false, oerr
	}
	defer lf.Close()
	if ferr := syscall.Flock(int(lf.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); ferr != nil {
		if errors.Is(ferr, syscall.EWOULDBLOCK) {
			return lease, true, nil
		}
		return lease, false, ferr
	}
	_ = syscall.Flock(int(lf.Fd()), syscall.LOCK_UN)
	return lease, false, nil
}
