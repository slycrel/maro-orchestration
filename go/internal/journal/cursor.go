package journal

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// Cursor is a lane's durable position in the journal: the last Seq it has
// fully processed. Overload means the cursor lags, never memory growth; a
// restart resumes from it. Advance never moves backwards and never past the
// committed head.
type Cursor struct {
	lane string
	path string
	seq  uint64
	j    *Journal
}

var ErrCursor = errors.New("journal: cursor")

// OpenCursor reads (or creates at 0) the lane's cursor.
func (j *Journal) OpenCursor(lane string) (*Cursor, error) {
	if lane == "" || strings.ContainsAny(lane, "/\\ ") {
		return nil, fmt.Errorf("%w: bad lane name %q", ErrCursor, lane)
	}
	dir := j.root.Path("cursors")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	c := &Cursor{lane: lane, path: j.root.Path("cursors", lane), j: j}
	raw, err := os.ReadFile(c.path)
	switch {
	case errors.Is(err, os.ErrNotExist):
		c.seq = 0
	case err != nil:
		return nil, err
	default:
		n, perr := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
		if perr != nil {
			return nil, fmt.Errorf("%w: %s does not parse — refusing to guess a lane position", ErrCursor, c.path)
		}
		if n > j.Head() {
			return nil, fmt.Errorf("%w: %s is at %d but the journal head is %d — the cursor is ahead of a recovered log", ErrCursor, c.path, n, j.Head())
		}
		c.seq = n
	}
	return c, nil
}

// Seq is the last processed Seq.
func (c *Cursor) Seq() uint64 { return c.seq }

// Advance durably records that everything up to and including seq is
// processed. Backwards or past-head is refused.
func (c *Cursor) Advance(seq uint64) error {
	if seq < c.seq {
		return fmt.Errorf("%w: %s cannot move backwards %d → %d", ErrCursor, c.lane, c.seq, seq)
	}
	if seq > c.j.Head() {
		return fmt.Errorf("%w: %s cannot advance past the committed head (%d > %d)", ErrCursor, c.lane, seq, c.j.Head())
	}
	if err := workspace.WriteFileDurable(c.path, []byte(strconv.FormatUint(seq, 10)+"\n"), 0o644); err != nil {
		return err
	}
	c.seq = seq
	return nil
}
