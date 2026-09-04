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
// original Ack and writes nothing. Epoch: must equal the journal's lease
// epoch, so a stale process cannot write. Preconditions are checked by the
// sequencer against committed state immediately before allocation.
type Command struct {
	IdempotencyKey string
	Epoch          uint64
	ExpectHead     *uint64 // optional: the command is valid only if the committed head Seq equals this
	Records        []record.Record
}

// Ack is the durable answer to a command.
type Ack struct {
	TxID     string
	FirstSeq uint64
	LastSeq  uint64
	Replayed bool // true when the key had already been committed
}

var (
	ErrStaleEpoch   = errors.New("journal: command from a stale epoch")
	ErrPrecondition = errors.New("journal: precondition failed")
	ErrEmptyCommand = errors.New("journal: command has no records")
	ErrNoKey        = errors.New("journal: command has no idempotency key")
	ErrClosed       = errors.New("journal: closed")
)

// Recovery describes what Open found and discarded.
type Recovery struct {
	Frames    int
	Head      uint64
	Discarded int64 // bytes of torn tail dropped
	Reason    string
}

// Journal is the log plus its sequencer. One per workspace root; the lease
// proves that.
type Journal struct {
	root  *workspace.Announced
	lease *workspace.Lease
	path  string
	f     *os.File
	dir   string

	mu     sync.Mutex // serializes Submit: the sequencer is this lock's critical section
	head   uint64
	acks   map[string]Ack // idempotency index, rebuilt on open
	closed bool
	rec    Recovery
}

const logFile = "journal.log"

// Open reads the log, verifies every frame, rebuilds the head and the
// idempotency index, truncates a torn tail, and returns the journal. Only
// the lease holder may open: the lease's epoch is stamped on every frame.
func Open(root *workspace.Announced, lease *workspace.Lease) (*Journal, error) {
	if lease == nil {
		return nil, errors.New("journal: a lease is required")
	}
	dir := root.Path("journal")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, logFile)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o644)
	if err != nil {
		return nil, err
	}
	j := &Journal{root: root, lease: lease, path: path, f: f, dir: dir, acks: map[string]Ack{}}
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
	r := bufio.NewReader(j.f)
	var good int64
	for {
		payload, err := readFrame(r)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			j.rec.Reason = err.Error()
			break
		}
		e, derr := decodeEnvelope(payload)
		if derr != nil {
			j.rec.Reason = derr.Error()
			break
		}
		if e.FirstSeq != j.head+1 {
			return fmt.Errorf("journal: frame %s has first_seq %d, expected %d — log is not contiguous", e.TxID, e.FirstSeq, j.head+1)
		}
		j.head = e.LastSeq
		j.acks[e.TxID] = Ack{TxID: e.TxID, FirstSeq: e.FirstSeq, LastSeq: e.LastSeq, Replayed: true}
		j.rec.Frames++
		good += int64(frameHeader + len(payload))
	}
	st, err := j.f.Stat()
	if err != nil {
		return err
	}
	if st.Size() > good {
		j.rec.Discarded = st.Size() - good
		if err := j.f.Truncate(good); err != nil {
			return err
		}
		if err := j.f.Sync(); err != nil {
			return err
		}
	}
	j.rec.Head = j.head
	if _, err := j.f.Seek(0, io.SeekEnd); err != nil {
		return err
	}
	return nil
}

// Recovered reports what Open found.
func (j *Journal) Recovered() Recovery { return j.rec }

// Head is the committed watermark: the highest Seq any reader may see.
func (j *Journal) Head() uint64 {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.head
}

// Epoch is the lease epoch this journal stamps on frames.
func (j *Journal) Epoch() uint64 { return j.lease.Epoch }

// Submit is the sequencer. It validates the command, checks its
// preconditions against committed state, allocates contiguous Seq, stamps
// kind and population from the registry, writes one frame, fsyncs, and acks.
// Nothing is visible to any reader until the ack.
func (j *Journal) Submit(ctx context.Context, c Command) (Ack, error) {
	if c.IdempotencyKey == "" {
		return Ack{}, ErrNoKey
	}
	if len(c.Records) == 0 {
		return Ack{}, ErrEmptyCommand
	}
	if c.Epoch != j.lease.Epoch {
		return Ack{}, fmt.Errorf("%w: got %d, lease is %d", ErrStaleEpoch, c.Epoch, j.lease.Epoch)
	}
	if err := ctx.Err(); err != nil {
		return Ack{}, err
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	if j.closed {
		return Ack{}, ErrClosed
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
		seq++
		r.Head().Seq = seq
		body, err := json.Marshal(r)
		if err != nil {
			return Ack{}, err
		}
		envl, _ := record.EnvelopeOf(kind)
		env.Records = append(env.Records, Encoded{Kind: kind, Envelope: envl.String(), Seq: seq, Body: body})
	}
	env.LastSeq = seq
	payload, err := encodeEnvelope(env)
	if err != nil {
		return Ack{}, err
	}
	if err := writeFrame(j.f, payload); err != nil {
		// Leave the file as is: recovery truncates a torn frame.
		for _, r := range c.Records {
			r.Head().Seq = 0
		}
		return Ack{}, err
	}
	if err := j.f.Sync(); err != nil {
		for _, r := range c.Records {
			r.Head().Seq = 0
		}
		return Ack{}, err
	}
	j.head = seq
	a := Ack{TxID: c.IdempotencyKey, FirstSeq: env.FirstSeq, LastSeq: env.LastSeq}
	j.acks[c.IdempotencyKey] = a
	return a, nil
}

// Close stops accepting commands and closes the file. Readers already
// holding the path may continue; new Submits fail with ErrClosed.
func (j *Journal) Close() error {
	j.mu.Lock()
	defer j.mu.Unlock()
	j.closed = true
	return j.f.Close()
}

// scan reads every committed frame up to head (inclusive) and calls fn for
// each encoded record with Seq in (after, head]. It opens its own file
// handle so it never disturbs the writer's offset.
func (j *Journal) scan(after uint64, fn func(Encoded) error) error {
	head := j.Head()
	f, err := os.Open(j.path)
	if err != nil {
		return err
	}
	defer f.Close()
	r := bufio.NewReader(f)
	for {
		payload, err := readFrame(r)
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return nil // a torn tail is never visible; recovery will truncate it
		}
		e, err := decodeEnvelope(payload)
		if err != nil {
			return err
		}
		if e.FirstSeq > head {
			return nil
		}
		for _, rec := range e.Records {
			if rec.Seq <= after {
				continue
			}
			if rec.Seq > head {
				return nil
			}
			if err := fn(rec); err != nil {
				return err
			}
		}
	}
}

// Decode turns an encoded record into its registered Go type. The kind and
// population come from the registry, never from the body.
func Decode(e Encoded) (record.Record, error) {
	spec, ok := record.Lookup(e.Kind)
	if !ok {
		return nil, fmt.Errorf("%w: %q", record.ErrUnregisteredKind, e.Kind)
	}
	if spec.Envelope.String() != e.Envelope {
		return nil, fmt.Errorf("journal: frame says %s but registry says %s for kind %s", e.Envelope, spec.Envelope, e.Kind)
	}
	v := reflect.New(spec.Type).Interface()
	if err := json.Unmarshal(e.Body, v); err != nil {
		return nil, err
	}
	r, ok := v.(record.Record)
	if !ok {
		return nil, fmt.Errorf("journal: %s does not implement Record", spec.Type)
	}
	if err := record.Validate(r, true); err != nil {
		return nil, err
	}
	return r, nil
}
