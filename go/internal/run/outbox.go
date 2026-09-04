package run

import (
	"context"
	"errors"
	"fmt"
	"io"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

var (
	ErrNotPresented = errors.New("run: ack refused: nothing was presented for this delivery")
	ErrStaleAck     = errors.New("run: ack refused: delivery is not the run's current delivery")
	ErrBadToken     = errors.New("run: ack refused: token is not bound to this delivery and payload")
	ErrNoDelivery   = errors.New("run: no such delivery")
)

// drain presents the attempt's delivery until the transport accepts it or
// the bound is reached, committing one DeliveryAttempted per try and the
// run transition that follows. Idempotent by (delivery, n).
func (d *Driver) drain(ctx context.Context, rs *RunState, a *AttemptState) error {
	dl := a.Delivery
	n := a.Attempt.Attempt
	for !dl.Accepted() && len(dl.Attempts) < d.MaxDeliveryAttempts {
		if err := ctx.Err(); err != nil {
			return err
		}
		payload, err := d.Store.Get(dl.Prepared.Payload)
		if err != nil {
			return err
		}
		pres := Presentation{Delivery: dl.Prepared.ID, Run: rs.Run, Handle: HandleOf(rs.Run), Attempt: n, Payload: payload, Ref: dl.Prepared.Payload, Required: dl.Prepared.Required}
		if rec := a.Has(Recorded); rec != nil {
			pres.Closure, pres.Terminal = rec.Outcome.ClosureOut, string(rec.Outcome.Terminal)
		}
		if dl.Prepared.Required == UserAcknowledged {
			pres.Token = TokenFor(dl.Prepared.ID, dl.Prepared.Payload.Hash, dl.Prepared.Nonce)
		}
		perr := d.Origin.Present(ctx, pres)
		if err := d.crash("after_present"); err != nil {
			return err // the user may have seen it; the record did not land — a resume presents again
		}
		at := &DeliveryAttempted{Header: header(record.Ref{Kind: "delivery", ID: string(dl.Prepared.ID)}, rs.Run, n, "delivery_attempted/1"), Delivery: dl.Prepared.ID, N: len(dl.Attempts) + 1, Result: TransportAccepted}
		if perr != nil {
			at.Result, at.Reason = DeliveryFailed, perr.Error()
		}
		if err := d.commit(ctx, fmt.Sprintf("delivery/%s/attempt/%d", dl.Prepared.ID, at.N), at); err != nil {
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
		t := &Transition{Header: header(runRef(rs.Run), rs.Run, n, "run_transition/1"), From: Recorded, To: Delivered, Delivery: state}
		if err := d.commit(ctx, fmt.Sprintf("run/%s/%d/delivered/%s", rs.Run, n, state), t); err != nil {
			return err
		}
		a.Transitions = append(a.Transitions, t)
		d.emit(rs, n, "delivered", Delivered, string(state))
	case !dl.Accepted() && cur == Recorded:
		reason := fmt.Sprintf("%d presentation(s) failed; last: %s", len(dl.Attempts), dl.Attempts[len(dl.Attempts)-1].Reason)
		if err := d.transition(ctx, rs, a, DeliveryFailedS, reason, nil); err != nil {
			return err
		}
	}
	return nil
}

// Ack records a client-generated acknowledgement for a delivery. It is
// accepted only when a presentation was recorded (crash-before-display
// leaves nothing to acknowledge), the delivery is its run's current one
// (a later attempt makes an earlier token stale), and the token is the one
// bound to this delivery and payload. A repeat with the same token replays.
func Ack(ctx context.Context, j *journal.Journal, id record.RecordID, token string) (*DeliveryAcked, bool, error) {
	led, err := Fold(j.Production())
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
			if !dl.Accepted() {
				return nil, false, ErrNotPresented
			}
			if token != TokenFor(id, dl.Prepared.Payload.Hash, dl.Prepared.Nonce) {
				return nil, false, ErrBadToken
			}
			if dl.Ack != nil {
				return dl.Ack, true, nil
			}
			n := a.Attempt.Attempt
			ack := &DeliveryAcked{Header: header(record.Ref{Kind: "delivery", ID: string(id)}, rs.Run, n, "delivery_acked/1"), Delivery: id, Token: token, PayloadHash: dl.Prepared.Payload.Hash}
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
// the client needs to acknowledge. Transport accepted = the write returned.
type CLIOrigin struct{ W io.Writer }

func (CLIOrigin) Name() GoalOrigin { return OriginCLI }

func (o CLIOrigin) Present(ctx context.Context, p Presentation) error {
	if o.W == nil {
		return errors.New("cli origin has no writer")
	}
	if _, err := o.W.Write(p.Payload); err != nil {
		return err
	}
	tail := fmt.Sprintf("\n---\nrun %s attempt %d · terminal %s · closure %s · delivery %s\n", p.Handle, p.Attempt, p.Terminal, p.Closure, p.Delivery)
	if p.Token != "" {
		tail += fmt.Sprintf("acknowledge with: maro-go ack %s %s\n", p.Delivery, p.Token)
	}
	_, err := io.WriteString(o.W, tail)
	return err
}
