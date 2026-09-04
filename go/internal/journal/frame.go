// Package journal is the workspace's one append-only log of framed
// transactions (design note §2). Lanes submit Commands; the sequencer
// validates preconditions, allocates Seq, writes ONE framed, checksummed
// envelope per command, fsyncs, and acknowledges. Recovery truncates only a
// genuinely short tail; interior corruption refuses to open without touching
// the log. Nothing inside an unacknowledged envelope is ever visible.
package journal

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
	"io"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Envelope is the on-disk unit: one command's records, contiguous Seq.
type Envelope struct {
	TxID     string    `json:"tx_id"`
	Epoch    uint64    `json:"epoch"`
	FirstSeq uint64    `json:"first_seq"`
	LastSeq  uint64    `json:"last_seq"`
	Records  []Encoded `json:"records"`
}

// Encoded is one record as it sits in the log: kind and population stamped
// from the REGISTRY at commit time, never taken from the body.
type Encoded struct {
	Kind     record.Kind     `json:"kind"`
	Envelope string          `json:"envelope"`
	Seq      uint64          `json:"seq"`
	Body     json.RawMessage `json:"body"`
	Epoch    uint64          `json:"-"` // the lease epoch of the frame that committed it (set on scan, not on the wire: the frame carries it once)
}

// Frame: magic(4) | len(4 BE) | crc32(4 BE, IEEE over payload) | payload.
var magic = [4]byte{'M', 'J', 'L', '1'}

const (
	frameHeader = 12
	// MaxPayload bounds one envelope. Records are metadata; bodies (thoughts)
	// live in the thought store by hash, so an envelope is small. A length
	// above this is corruption, not a large command.
	MaxPayload = 16 << 20
)

var (
	ErrTorn     = errors.New("journal: torn frame (short read at end of log)")
	ErrChecksum = errors.New("journal: frame checksum mismatch")
	ErrBadMagic = errors.New("journal: bad frame magic")
	ErrLength   = errors.New("journal: frame length out of bounds")
	ErrEnvelope = errors.New("journal: invalid envelope")
)

func writeFrame(w io.Writer, payload []byte) error {
	if len(payload) == 0 || len(payload) > MaxPayload {
		return fmt.Errorf("%w: %d", ErrLength, len(payload))
	}
	var hdr [frameHeader]byte
	copy(hdr[:4], magic[:])
	binary.BigEndian.PutUint32(hdr[4:8], uint32(len(payload)))
	binary.BigEndian.PutUint32(hdr[8:12], crc32.ChecksumIEEE(payload))
	if _, err := w.Write(hdr[:]); err != nil {
		return err
	}
	_, err := w.Write(payload)
	return err
}

// readFrame reads one frame. io.EOF exactly at a frame boundary is clean.
// A short read is ErrTorn (only ever legal as the LAST thing in the file);
// bad magic, an out-of-bounds length, or a checksum mismatch is corruption.
func readFrame(r io.Reader) ([]byte, error) {
	var hdr [frameHeader]byte
	n, err := io.ReadFull(r, hdr[:])
	if n == 0 && errors.Is(err, io.EOF) {
		return nil, io.EOF
	}
	if err != nil {
		return nil, ErrTorn
	}
	if [4]byte(hdr[:4]) != magic {
		return nil, ErrBadMagic
	}
	ln := binary.BigEndian.Uint32(hdr[4:8])
	if ln == 0 || ln > MaxPayload {
		return nil, fmt.Errorf("%w: %d", ErrLength, ln)
	}
	sum := binary.BigEndian.Uint32(hdr[8:12])
	payload := make([]byte, ln)
	if _, err := io.ReadFull(r, payload); err != nil {
		return nil, ErrTorn
	}
	if crc32.ChecksumIEEE(payload) != sum {
		return nil, ErrChecksum
	}
	return payload, nil
}

func encodeEnvelope(e Envelope) ([]byte, error) { return json.Marshal(e) }

// decodeEnvelope parses and STRICTLY validates an envelope against the
// registry and the expected position: non-empty TxID, epoch > 0, records
// non-empty, LastSeq = FirstSeq+len-1, records contiguous from FirstSeq,
// every stamp registered and matching the registry's envelope, every body
// decoding to its registered type with body Seq == frame Seq and body kind
// == stamp. This is the one validator recovery, scan and Decode share.
func decodeEnvelope(payload []byte, expectFirst uint64) (Envelope, []record.Record, error) {
	var e Envelope
	if err := json.Unmarshal(payload, &e); err != nil {
		return e, nil, fmt.Errorf("%w: %v", ErrEnvelope, err)
	}
	if e.TxID == "" {
		return e, nil, fmt.Errorf("%w: empty tx_id", ErrEnvelope)
	}
	if e.Epoch == 0 {
		return e, nil, fmt.Errorf("%w: epoch 0", ErrEnvelope)
	}
	if len(e.Records) == 0 {
		return e, nil, fmt.Errorf("%w: no records", ErrEnvelope)
	}
	if e.FirstSeq != expectFirst {
		return e, nil, fmt.Errorf("%w: first_seq %d, expected %d", ErrEnvelope, e.FirstSeq, expectFirst)
	}
	if e.LastSeq != e.FirstSeq+uint64(len(e.Records))-1 || e.LastSeq < e.FirstSeq {
		return e, nil, fmt.Errorf("%w: last_seq %d does not match %d records from %d", ErrEnvelope, e.LastSeq, len(e.Records), e.FirstSeq)
	}
	recs := make([]record.Record, 0, len(e.Records))
	for i, enc := range e.Records {
		if enc.Seq != e.FirstSeq+uint64(i) {
			return e, nil, fmt.Errorf("%w: record %d has seq %d, expected %d", ErrEnvelope, i, enc.Seq, e.FirstSeq+uint64(i))
		}
		r, err := Decode(enc)
		if err != nil {
			return e, nil, err
		}
		recs = append(recs, r)
	}
	return e, recs, nil
}
