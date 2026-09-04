package run

import (
	"context"
	"errors"
	"fmt"
	"io"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

var (
	ErrNotPresented = errors.New("run: ack refused: no presentation was started for this delivery")
	ErrStaleAck     = errors.New("run: ack refused: delivery is not the run's current delivery")
	ErrBadToken     = errors.New("run: ack refused: token is not bound to this delivery and payload")
	ErrNoDelivery   = errors.New("run: no such delivery")
)

func deliveryRef(id record.RecordID) record.Ref { return record.Ref{Kind: "delivery", ID: string(id)} }

// drain presents the attempt's delivery until the transport accepts it or
// the bound is reached. Each presentation is two records: a start BEFORE
// the outward write and a result after it. A start the process died inside
// is resolved as `unknown` on the next drain (the user may have seen it) and
// the next presentation says so. Idempotent by (delivery, n).
func (d *Driver) drain(ctx context.Context, rs *RunState, a *AttemptState) error {
	dl := a.Delivery
	n := a.Attempt.Attempt
	subj := deliveryRef(dl.Prepared.ID)
	for !dl.Accepted() {
		if err := ctx.Err(); err != nil {
			return err
		}
		if dl.Pending() {
			k := len(dl.Attempts) + 1
			at := &DeliveryAttempted{Header: header(subj, rs.Run, n, "delivery_attempted/1"), Delivery: dl.Prepared.ID, N: k, Result: DeliveryUnknown, Reason: "the process died after presentation " + fmt.Sprint(k) + " started; the user may have seen it"}
			if err := d.commit(ctx, fmt.Sprintf("delivery/%s/attempt/%d", dl.Prepared.ID, k), at); err != nil {
				return err
			}
			dl.Attempts = append(dl.Attempts, at)
			d.emit(rs, n, "presentation_unknown", a.Current(), fmt.Sprint(k))
			continue
		}
		if len(dl.Attempts) >= d.MaxDeliveryAttempts {
			break
		}
		payload, err := d.Store.Get(dl.Prepared.Payload)
		if err != nil {
			return err
		}
		k := len(dl.Started) + 1
		st := &DeliveryStarted{Header: header(subj, rs.Run, n, "delivery_started/1"), Delivery: dl.Prepared.ID, N: k}
		if err := d.commit(ctx, fmt.Sprintf("delivery/%s/start/%d", dl.Prepared.ID, k), st); err != nil {
			return err
		}
		dl.Started = append(dl.Started, st)
		if err := d.crash("after_started"); err != nil {
			return err
		}
		pres := Presentation{Delivery: dl.Prepared.ID, Run: rs.Run, Handle: HandleOf(rs.Run), Attempt: n, Payload: payload, Ref: dl.Prepared.Payload, Required: dl.Prepared.Required, MayDuplicate: dl.Unknown()}
		if rec := a.Has(Recorded); rec != nil {
			pres.Closure, pres.Terminal = rec.Outcome.ClosureOut, string(rec.Outcome.Terminal)
		}
		if dl.Prepared.Required == UserAcknowledged {
			pres.Token = TokenFor(dl.Prepared.ID, dl.Prepared.Payload.Hash, dl.Prepared.Nonce)
		}
		perr := present(ctx, d.Origin, pres)
		if err := d.crash("after_present"); err != nil {
			return err // the user may have seen it; the start is on record, the result is not
		}
		at := &DeliveryAttempted{Header: header(subj, rs.Run, n, "delivery_attempted/1"), Delivery: dl.Prepared.ID, N: k, Result: TransportAccepted}
		if perr != nil {
			at.Result, at.Reason = DeliveryFailed, perr.Error()
		}
		if err := d.commit(ctx, fmt.Sprintf("delivery/%s/attempt/%d", dl.Prepared.ID, k), at); err != nil {
			return err
		}
		dl.Attempts = append(dl.Attempts, at)
		d.emit(rs, n, "presented", a.Current(), string(at.Result))
		if err := d.crash("after_attempted"); err != nil {
			return err
		}
	}
	switch cur := a.Current(); {
	case dl.Accepted() && cur == Recorded:
		state := TransportAccepted
		if dl.Ack != nil {
			state = UserAcknowledged
		}
		if err := d.transition(ctx, rs, a, Delivered, "", nil, state); err != nil {
			return err
		}
	case !dl.Accepted() && cur == Recorded:
		last := "no presentation was possible"
		if len(dl.Attempts) > 0 {
			last = dl.Attempts[len(dl.Attempts)-1].Reason
		}
		if err := d.transition(ctx, rs, a, DeliveryFailedS, fmt.Sprintf("%d presentation(s) failed; last: %s", len(dl.Attempts), last), nil); err != nil {
			return err
		}
	}
	return nil
}

// present contains an origin panic as a failed presentation: the outward
// edge is a boundary component, and its failure is evidence, not a crash.
func present(ctx context.Context, o Origin, p Presentation) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("origin panicked: %v", r)
		}
	}()
	return o.Present(ctx, p)
}

// Ack records a client-generated acknowledgement for a delivery. It is
// accepted only when a presentation was started (crash-before-display
// leaves nothing to acknowledge), the delivery is its run's current one,
// and the token is the one bound to this delivery and payload (checkAck —
// the same rule the fold executes on every journaled ack). A repeat with
// the same token replays. The run's transition to user_acknowledged is
// committed with the ack, from recorded or from delivered:transport.
func Ack(ctx context.Context, j *journal.Journal, st *thought.Store, id record.RecordID, token string) (*DeliveryAcked, bool, error) {
	led, err := Fold(j.Production(), st)
	if err != nil {
		return nil, false, err
	}
	for _, rs := range led.Runs {
		for _, a := range rs.Attempts {
			if a.Delivery == nil || a.Delivery.Prepared.ID != id {
				continue
			}
			dl := a.Delivery
			if a != rs.Latest() {
				return nil, false, ErrStaleAck // unreachable in v1 (one delivery per run); the guard is for the re-deliver verb
			}
			if dl.Ack != nil {
				if token != dl.Ack.Token {
					return nil, false, ErrBadToken
				}
				return dl.Ack, true, nil
			}
			n := a.Attempt.Attempt
			ack := &DeliveryAcked{Header: header(deliveryRef(id), rs.Run, n, "delivery_acked/1"), Delivery: id, Token: token, PayloadHash: dl.Prepared.Payload.Hash}
			if err := dl.checkAck(ack); err != nil {
				return nil, false, err
			}
			if a.Current() == DeliveryFailedS {
				return nil, false, fmt.Errorf("%w: the run gave up on this delivery", ErrNotPresented)
			}
			t := &Transition{Header: header(runRef(rs.Run), rs.Run, n, "run_transition/1"), From: a.Current(), To: Delivered, Delivery: UserAcknowledged}
			_, err := j.Submit(ctx, journal.Command{IdempotencyKey: "delivery/" + string(id) + "/ack", Epoch: j.Epoch(), Records: []record.Record{ack, t}})
			if err != nil {
				return nil, false, err
			}
			return ack, false, nil
		}
	}
	return nil, false, ErrNoDelivery
}

// CLIOrigin presents on a writer: the payload whole, then the process lines
// the client needs to acknowledge — composed once and written once, so a
// short write is one failed presentation, not a torn one. Transport
// accepted = the write returned whole.
type CLIOrigin struct{ W io.Writer }

func (CLIOrigin) Name() GoalOrigin { return OriginCLI }

func (o CLIOrigin) Present(ctx context.Context, p Presentation) error {
	if o.W == nil {
		return errors.New("cli origin has no writer")
	}
	buf := append([]byte{}, p.Payload...)
	buf = append(buf, fmt.Sprintf("\n---\nrun %s attempt %d · terminal %s · closure %s · delivery %s\n", p.Handle, p.Attempt, p.Terminal, p.Closure, p.Delivery)...)
	if p.MayDuplicate > 0 {
		buf = append(buf, fmt.Sprintf("(re-presented: %d earlier presentation(s) ended with the process dying; you may have seen this already)\n", p.MayDuplicate)...)
	}
	if p.Token != "" {
		buf = append(buf, fmt.Sprintf("acknowledge with: maro-go ack %s %s\n", p.Delivery, p.Token)...)
	}
	n, err := o.W.Write(buf)
	if err == nil && n != len(buf) {
		err = io.ErrShortWrite
	}
	return err
}
