package journal

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// Cursor is a lane's durable position: the last Seq it fully processed.
// There is ONE Cursor object per lane per journal (OpenCursor returns the
// same one), its methods are serialized, and Advance re-reads the durable
// value inside the critical section, so no path can move a lane backwards.
// Lanes are not renamed: a new name is a new lane starting at zero.
type Cursor struct {
	lane string
	path string
	mu   sync.Mutex
	seq  uint64
	j    *Journal
}

var ErrCursor = errors.New("journal: cursor")

// OpenCursor returns the lane's cursor, reading it from disk the first time.
func (j *Journal) OpenCursor(lane string) (*Cursor, error) {
	if j.isClosed() {
		return nil, ErrClosed
	}
	if lane == "" || strings.ContainsAny(lane, "/\\ ") {
		return nil, fmt.Errorf("%w: bad lane name %q", ErrCursor, lane)
	}
	j.cursorsMu.Lock()
	defer j.cursorsMu.Unlock()
	if c, ok := j.cursors[lane]; ok {
		return c, nil
	}
	dir := j.root.Path("cursors")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	c := &Cursor{lane: lane, path: j.root.Path("cursors", lane), j: j}
	n, err := c.readDurable()
	if err != nil {
		return nil, err
	}
	c.seq = n
	j.cursors[lane] = c
	return c, nil
}

func (c *Cursor) readDurable() (uint64, error) {
	raw, err := os.ReadFile(c.path)
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	n, perr := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
	if perr != nil {
		return 0, fmt.Errorf("%w: %s does not parse — refusing to guess a lane position", ErrCursor, c.path)
	}
	if n > c.j.Head() {
		return 0, fmt.Errorf("%w: %s is at %d but the journal head is %d — the cursor is ahead of a recovered log", ErrCursor, c.path, n, c.j.Head())
	}
	return n, nil
}

// Seq is the last processed Seq.
func (c *Cursor) Seq() uint64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.seq
}

// Advance durably records that everything up to and including seq is
// processed. Backwards (against memory OR disk) or past-head is refused.
func (c *Cursor) Advance(seq uint64) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.j.isClosed() {
		return ErrClosed
	}
	onDisk, err := c.readDurable()
	if err != nil {
		return err
	}
	if seq < c.seq || seq < onDisk {
		return fmt.Errorf("%w: %s cannot move backwards (memory %d, disk %d → %d)", ErrCursor, c.lane, c.seq, onDisk, seq)
	}
	if seq > c.j.Head() {
		return fmt.Errorf("%w: %s cannot advance past the committed head (%d > %d)", ErrCursor, c.lane, seq, c.j.Head())
	}
	if seq == onDisk {
		c.seq = seq
		return nil
	}
	if err := workspace.WriteFileDurable(c.path, []byte(strconv.FormatUint(seq, 10)+"\n"), 0o644); err != nil {
		return err
	}
	c.seq = seq
	return nil
}
