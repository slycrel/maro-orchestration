package process

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"time"
)

// Client is one connection to a running process.
type Client struct {
	c   net.Conn
	r   *bufio.Reader
	enc *json.Encoder
}

// ErrNoServer: nothing is listening on the workspace's socket.
var ErrNoServer = errors.New("process: no maro-go serve on this workspace")

// Dial connects to the workspace's socket.
func Dial(sock string) (*Client, error) {
	c, err := net.DialTimeout("unix", sock, 2*time.Second)
	if err != nil {
		return nil, fmt.Errorf("%w (%s): %v", ErrNoServer, sock, err)
	}
	return &Client{c: c, r: bufio.NewReader(c), enc: json.NewEncoder(c)}, nil
}

// Close ends the connection.
func (cl *Client) Close() error { return cl.c.Close() }

func (cl *Client) send(req Request) error { return cl.enc.Encode(req) }

func (cl *Client) next() (Event, error) {
	line, err := cl.r.ReadBytes('\n')
	if err != nil {
		return Event{}, err
	}
	var ev Event
	if err := json.Unmarshal(line, &ev); err != nil {
		return Event{}, fmt.Errorf("process: malformed event: %w", err)
	}
	if ev.Type == "error" {
		return ev, errors.New(ev.Error)
	}
	return ev, nil
}

// Submit sends a goal and streams the events back (accepted, then
// presentation(s), then done) until the run is terminal or ctx ends.
func (cl *Client) Submit(ctx context.Context, req Request, on func(Event)) error {
	req.Op = "submit"
	if err := cl.send(req); err != nil {
		return err
	}
	if d, ok := ctx.Deadline(); ok {
		cl.c.SetReadDeadline(d)
	}
	for {
		ev, err := cl.next()
		if err != nil {
			return err
		}
		on(ev)
		if ev.Type == "done" {
			return nil
		}
	}
}

// One sends a single request and returns its one event.
func (cl *Client) One(req Request) (Event, error) {
	if err := cl.send(req); err != nil {
		return Event{}, err
	}
	cl.c.SetReadDeadline(time.Now().Add(30 * time.Second))
	return cl.next()
}
