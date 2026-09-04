package journal

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// Command is what a lane submits. Idempotency: a replayed key returns the
// original Ack and writes nothing. Epoch: must equal the lease epoch.
type Command struct {
	IdempotencyKey string
	Epoch          uint64
	ExpectHead     *uint64 // valid only if the committed head equals this
	Records        []record.Record
}

// Ack is the durable answer to a command.
type Ack struct {
	TxID     string
	FirstSeq uint64
	LastSeq  uint64
	Replayed bool
}

var (
	ErrStaleEpoch   = errors.New("journal: command from a stale epoch")
	ErrPrecondition = errors.New("journal: precondition failed")
	ErrEmptyCommand = errors.New("journal: command has no records")
	ErrNoKey        = errors.New("journal: command has no idempotency key")
	ErrClosed       = errors.New("journal: closed")
	ErrLease        = errors.New("journal: lease is not live")
	ErrCorrupt      = errors.New("journal: log corrupt — refusing to open; nothing was modified")
	ErrPoisoned     = errors.New("journal: a write failed and the commit state is indeterminate — close and reopen to recover")
	ErrIncomplete   = errors.New("journal: scan could not prove coverage through the requested head")
)

// Recovery describes what Open found.
type Recovery struct {
	Frames    int
	Head      uint64
	Discarded int64 // bytes of a genuinely short tail dropped
}

// Journal is the log plus its sequencer. Opened only from a live lease.
type Journal struct {
	lease *workspace.Lease
	root  *workspace.Announced
	path  string
	f     *os.File

	mu       sync.Mutex // the sequencer's critical section; also guards head/acks/offset/poison
	head     uint64
	offset   int64 // known-good end of the last committed frame
	acks     map[string]Ack
	closed   bool
	poisoned error
	rec      Recovery

	cursorsMu sync.Mutex
	cursors   map[string]*Cursor
	laneMu    map[string]*sync.Mutex
}

const logFile = "journal.log"

// Open reads the log under the lease's root, verifies every frame with the
// strict validator, rebuilds the head and the idempotency index, truncates a
// genuinely short tail, and refuses interior corruption untouched.
func Open(lease *workspace.Lease) (*Journal, error) {
	if !lease.Live() {
		return nil, ErrLease
	}
	root := lease.Root()
	dir := root.Path("journal")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	if err := workspace.SyncDir(root.Path()); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, logFile)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, err
	}
	if err := workspace.SyncDir(dir); err != nil { // the directory entry is durable before any ack
		f.Close()
		return nil, err
	}
	j := &Journal{lease: lease, root: root, path: path, f: f, acks: map[string]Ack{}, cursors: map[string]*Cursor{}, laneMu: map[string]*sync.Mutex{}}
	if err := j.recover(); err != nil {
		f.Close()
		return nil, err
	}
	return j, nil
}

func (j *Journal) recover() error {
	if _, err := j.f.Seek(0, io.SeekStart); err != nil {
		return err
	}
	st, err := j.f.Stat()
	if err != nil {
		return err
	}
	size := st.Size()
	r := bufio.NewReader(j.f)
	var good int64
	for {
		payload, rerr := readFrame(r)
		if errors.Is(rerr, io.EOF) {
			break
		}
		if errors.Is(rerr, ErrTorn) {
			// A short read is a tail by definition (ReadFull hit EOF). Only
			// then may bytes be dropped.
			break
		}
		if rerr != nil {
			return fmt.Errorf("%w: at offset %d: %v", ErrCorrupt, good, rerr)
		}
		e, _, verr := decodeEnvelope(payload, j.head+1)
		if verr != nil {
			return fmt.Errorf("%w: at offset %d: %v", ErrCorrupt, good, verr)
		}
		if _, dup := j.acks[e.TxID]; dup {
			return fmt.Errorf("%w: at offset %d: duplicate tx_id %q", ErrCorrupt, good, e.TxID)
		}
		j.head = e.LastSeq
		j.acks[e.TxID] = Ack{TxID: e.TxID, FirstSeq: e.FirstSeq, LastSeq: e.LastSeq, Replayed: true}
		j.rec.Frames++
		good += int64(frameHeader + len(payload))
	}
	if size > good {
		j.rec.Discarded = size - good
		if err := j.f.Truncate(good); err != nil {
			return err
		}
		if err := j.f.Sync(); err != nil {
			return err
		}
	}
	j.offset = good
	j.rec.Head = j.head
	if _, err := j.f.Seek(good, io.SeekStart); err != nil {
		return err
	}
	return nil
}

// Recovered reports what Open found.
func (j *Journal) Recovered() Recovery { return j.rec }

// Head is the committed watermark: the highest Seq any reader may see. It is
// updated only after fsync, under the sequencer's lock, so a Head observed
// during a Submit is the pre-Submit head.
func (j *Journal) Head() uint64 {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.head
}

// Epoch is the lease epoch this journal stamps on frames.
func (j *Journal) Epoch() uint64 { return j.lease.Epoch }

// Root is the journal's workspace root (from the lease).
func (j *Journal) Root() *workspace.Announced { return j.root }

// Submit is the sequencer. Validation happens before any record is touched;
// Seq is stamped only once every record has passed. A write or fsync failure
// poisons the journal: the commit state is indeterminate and every later
// Submit refuses until a reopen recovers the truth.
func (j *Journal) Submit(ctx context.Context, c Command) (Ack, error) {
	if c.IdempotencyKey == "" {
		return Ack{}, ErrNoKey
	}
	if len(c.Records) == 0 {
		return Ack{}, ErrEmptyCommand
	}
	if !j.lease.Live() {
		return Ack{}, ErrLease
	}
	if c.Epoch != j.lease.Epoch {
		return Ack{}, fmt.Errorf("%w: got %d, lease is %d", ErrStaleEpoch, c.Epoch, j.lease.Epoch)
	}
	if err := ctx.Err(); err != nil {
		return Ack{}, err
	}
	// Validate everything before mutating anything.
	for _, r := range c.Records {
		kind, ok := record.KindOf(r)
		if !ok {
			return Ack{}, fmt.Errorf("%w: %T", record.ErrUnregisteredKind, r)
		}
		if kind != r.Kind() {
			return Ack{}, record.ErrImpostor
		}
		if err := record.Validate(r, false); err != nil {
			return Ack{}, err
		}
		if r.Head().Seq != 0 {
			return Ack{}, fmt.Errorf("journal: record %s already carries Seq %d — Seq is allocated here, never by a lane", r.Head().ID, r.Head().Seq)
		}
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.closed {
		return Ack{}, ErrClosed
	}
	if j.poisoned != nil {
		return Ack{}, fmt.Errorf("%w: %v", ErrPoisoned, j.poisoned)
	}
	if a, ok := j.acks[c.IdempotencyKey]; ok {
		a.Replayed = true
		return a, nil
	}
	if c.ExpectHead != nil && *c.ExpectHead != j.head {
		return Ack{}, fmt.Errorf("%w: expected head %d, committed head is %d", ErrPrecondition, *c.ExpectHead, j.head)
	}
	env := Envelope{TxID: c.IdempotencyKey, Epoch: c.Epoch, FirstSeq: j.head + 1}
	seq := j.head
	rollback := func() {
		for _, r := range c.Records {
			r.Head().Seq = 0
		}
	}
	for _, r := range c.Records {
		seq++
		r.Head().Seq = seq
		body, err := json.Marshal(r)
		if err != nil {
			rollback()
			return Ack{}, err
		}
		kind, _ := record.KindOf(r)
		envl, _ := record.EnvelopeOf(kind)
		env.Records = append(env.Records, Encoded{Kind: kind, Envelope: envl.String(), Seq: seq, Body: body})
	}
	env.LastSeq = seq
	payload, err := encodeEnvelope(env)
	if err != nil {
		rollback()
		return Ack{}, err
	}
	if err := writeFrame(j.f, payload); err != nil {
		rollback()
		// Try to erase the partial frame so a reopen finds a clean prefix;
		// whether or not that works, the journal is poisoned until reopen.
		_ = j.f.Truncate(j.offset)
		_ = j.f.Sync()
		j.poisoned = err
		return Ack{}, fmt.Errorf("%w: %v", ErrPoisoned, err)
	}
	if err := j.f.Sync(); err != nil {
		rollback()
		j.poisoned = err
		return Ack{}, fmt.Errorf("%w: %v", ErrPoisoned, err)
	}
	j.head = seq
	j.offset += int64(frameHeader + len(payload))
	a := Ack{TxID: c.IdempotencyKey, FirstSeq: env.FirstSeq, LastSeq: env.LastSeq}
	j.acks[c.IdempotencyKey] = a
	return a, nil
}

// Close stops accepting commands and closes the file. Idempotent.
func (j *Journal) Close() error {
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.closed {
		return nil
	}
	j.closed = true
	return j.f.Close()
}

func (j *Journal) isClosed() bool {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.closed
}

// scanThrough reads committed frames and calls fn for each encoded record
// with Seq in (after, through]. It MUST prove coverage: every frame up to
// `through` must read and validate, or the scan fails with ErrIncomplete —
// a reader never turns corruption into a short but successful result.
func (j *Journal) scanThrough(after, through uint64, fn func(Encoded) error) error {
	if j.isClosed() {
		return ErrClosed
	}
	if through > j.Head() {
		return fmt.Errorf("%w: through %d exceeds committed head %d", ErrIncomplete, through, j.Head())
	}
	f, err := os.Open(j.path)
	if err != nil {
		return err
	}
	defer f.Close()
	r := bufio.NewReader(f)
	var next uint64 = 1
	for next <= through {
		payload, err := readFrame(r)
		if err != nil {
			return fmt.Errorf("%w: at seq %d: %v", ErrIncomplete, next, err)
		}
		e, _, err := decodeEnvelope(payload, next)
		if err != nil {
			return fmt.Errorf("%w: %v", ErrIncomplete, err)
		}
		for _, rec := range e.Records {
			if rec.Seq > through {
				return nil
			}
			if rec.Seq > after {
				if err := fn(rec); err != nil {
					return err
				}
			}
		}
		next = e.LastSeq + 1
	}
	return nil
}

// Decode turns an encoded record into its registered Go type. Kind and
// population come from the registry; the body must agree with the frame on
// Seq and kind, and validate as a stored record.
func Decode(e Encoded) (record.Record, error) {
	spec, ok := record.Lookup(e.Kind)
	if !ok {
		return nil, fmt.Errorf("%w: %q", record.ErrUnregisteredKind, e.Kind)
	}
	if spec.Envelope.String() != e.Envelope {
		return nil, fmt.Errorf("%w: frame says %s but registry says %s for kind %s", ErrEnvelope, e.Envelope, spec.Envelope, e.Kind)
	}
	v := reflect.New(spec.Type).Interface()
	if err := json.Unmarshal(e.Body, v); err != nil {
		return nil, fmt.Errorf("%w: body: %v", ErrEnvelope, err)
	}
	r, ok := v.(record.Record)
	if !ok {
		return nil, fmt.Errorf("%w: %s does not implement Record", ErrEnvelope, spec.Type)
	}
	if err := record.Validate(r, true); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrEnvelope, err)
	}
	if r.Head().Seq != e.Seq {
		return nil, fmt.Errorf("%w: body seq %d != frame seq %d", ErrEnvelope, r.Head().Seq, e.Seq)
	}
	if r.Kind() != e.Kind {
		return nil, fmt.Errorf("%w: body kind %s != frame kind %s", ErrEnvelope, r.Kind(), e.Kind)
	}
	return r, nil
}

// LaneLock returns a process-wide mutex for a named lane, so two actors in
// the lease holder cannot run the same lane's mutation concurrently.
func (j *Journal) LaneLock(name string) *sync.Mutex {
	j.cursorsMu.Lock()
	defer j.cursorsMu.Unlock()
	m, ok := j.laneMu[name]
	if !ok {
		m = &sync.Mutex{}
		j.laneMu[name] = m
	}
	return m
}
