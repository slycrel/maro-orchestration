package judgment

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
)

// HTTP is a System One provider over HTTP: TypeSafe's Jev at
// api.typesafe.ai, or the local PCD sidecar, which speaks the same wire
// shape with no auth. One implementation serves both; only the base URL,
// the model name and whether a key is sent differ.
//
// It is tool-less by construction and declares it: ActsOutward false, so
// a dispatched call found without a terminal on restart reconciles to
// `abandoned` (safe to retry) rather than indeterminate — it cannot act
// on the world, it can only answer.
type HTTP struct {
	Provider string // jev | pcd
	BaseURL  string
	Model    string
	// KeyName is the secrets-store name of the bearer token ("" = no
	// auth, the sidecar's case). The VALUE is never held on this struct
	// and never logged: Key resolves it per call from the store.
	KeyName string
	Key     func() (string, error)
	Timeout time.Duration
	Client  *http.Client
}

// NewJev builds the TypeSafe provider. key resolves the bearer token from
// the secrets store (never the environment alone).
func NewJev(key func() (string, error)) *HTTP {
	return &HTTP{Provider: ProviderJev, BaseURL: JevBaseURL, Model: JevModel, KeyName: JevKeyName, Key: key, Timeout: DefaultTimeout}
}

// NewPCD builds the local sidecar provider: same wire, no auth.
func NewPCD(url string) *HTTP {
	if strings.TrimSpace(url) == "" {
		url = DefaultPCDURL
	}
	return &HTTP{Provider: ProviderPCD, BaseURL: url, Model: PCDModel, Timeout: DefaultTimeout}
}

func (h *HTTP) Name() string { return h.Provider }
func (h *HTTP) Wire() bool   { return true }

func (h *HTTP) Capabilities() invoke.Capabilities {
	return invoke.Capabilities{Name: h.Provider, Model: h.Model, ActsOutward: false, OutwardReconcilable: false, ReadsByReference: false}
}

// Render is the wire body itself: what is recorded is exactly what is
// sent.
func (h *HTTP) Render(r Request) ([]byte, error) {
	if r.Model == "" {
		r.Model = h.Model
	}
	return EncodeRequest(r)
}

// Parse reads the System One response.
func (h *HTTP) Parse(b []byte) (Response, error) { return DecodeResponse(b) }

// URL is the endpoint, safe to print.
func (h *HTTP) URL() string { return strings.TrimRight(h.BaseURL, "/") + Path }

// Complete posts the request body. A non-2xx status, an unreadable body
// or a timeout is an honest failed terminal with the status in the
// reason; the key never appears in a reason, a response or a log.
func (h *HTTP) Complete(ctx context.Context, req invoke.Request, sink invoke.Sink) (*invoke.Result, error) {
	if req.Tools {
		return nil, fmt.Errorf("%w: %s cannot take a tool-bearing request", invoke.ErrBackendIncapable, h.Provider)
	}
	// the provider's timeout is a CEILING: a caller's longer budget (the
	// executor's 20 minutes rides every request) never turns one small
	// wire request into a 20-minute hang
	ceiling := h.Timeout
	if ceiling <= 0 {
		ceiling = DefaultTimeout
	}
	timeout := req.Timeout
	if timeout <= 0 || timeout > ceiling {
		timeout = ceiling
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	hreq, err := http.NewRequestWithContext(ctx, http.MethodPost, h.URL(), bytes.NewReader(req.Prompt))
	if err != nil {
		return nil, fmt.Errorf("%w: %v", invoke.ErrBeforeDispatch, err)
	}
	hreq.Header.Set("Content-Type", "application/json")
	var key string
	if h.Key != nil {
		k, err := h.Key()
		if err != nil {
			return nil, fmt.Errorf("%w: %s: no key for %s: %v", invoke.ErrBeforeDispatch, h.Provider, h.KeyName, err)
		}
		if strings.TrimSpace(k) == "" {
			return nil, fmt.Errorf("%w: %s: %s is empty in the secrets store", invoke.ErrBeforeDispatch, h.Provider, h.KeyName)
		}
		key = k
		hreq.Header.Set("Authorization", "Bearer "+key)
	}
	// everything that leaves this function is scrubbed of the resolved
	// key itself, not just of a "Bearer " prefix: an endpoint or proxy
	// that echoes the header would otherwise land it in a record
	scrub := func(s string) string { return invoke.Redact(s, key) }
	cl := h.Client
	if cl == nil {
		cl = &http.Client{}
	}
	start := time.Now()
	resp, err := cl.Do(hreq)
	if err != nil {
		return &invoke.Result{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("%s: %v", h.Provider, scrub(err.Error())), Usage: invoke.Usage{WallMillis: time.Since(start).Milliseconds()}}, nil
	}
	defer resp.Body.Close()
	body, rerr := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	wall := time.Since(start).Milliseconds()
	if rerr != nil {
		return &invoke.Result{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("%s: reading the response: %v", h.Provider, scrub(rerr.Error())), Usage: invoke.Usage{WallMillis: wall}}, nil
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		// scrub BEFORE clipping: a key that straddles the clip boundary
		// would otherwise leave its prefix in the reason (review r2)
		return &invoke.Result{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("%s: HTTP %d: %s", h.Provider, resp.StatusCode, snippet([]byte(scrub(string(body))))), Usage: invoke.Usage{WallMillis: wall}}, nil
	}
	body = []byte(scrub(string(body)))
	usage := invoke.Usage{WallMillis: wall}
	var u struct {
		Usage Usage `json:"usage"`
	}
	if err := json.Unmarshal(body, &u); err == nil {
		usage.InputTokens, usage.OutputTokens = u.Usage.InputTokens, u.Usage.OutputTokens
	}
	return &invoke.Result{Response: body, Usage: usage, Terminal: invoke.TerminalComplete}, nil
}

// snippet is a bounded, single-line quote of an error body.
func snippet(b []byte) string {
	s := strings.TrimSpace(strings.ReplaceAll(string(b), "\n", " "))
	if len(s) > 200 {
		s = s[:200] + "…"
	}
	return s
}
