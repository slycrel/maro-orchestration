package judgment

import (
	"context"
	"fmt"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
)

// A Provider answers judgment Requests. It is an invoke.Backend first —
// the invocation state machine, its receipts, usage and the fold's parity
// checks apply to a judgment call exactly as they do to any other call
// (design §4: "others attach through the same seam"). The extra two
// methods are the seam itself: how a Request becomes the prompt bytes
// that are recorded, and how the response bytes become answers.
//
// Render must be a pure, deterministic function of the Request: the run
// fold re-derives every judge request byte-for-byte.
type Provider interface {
	invoke.Backend
	// Name is the provider name (llm | jev | pcd), not the backend name.
	Name() string
	// Wire says the prompt bytes are the System One wire body. A wire
	// provider cannot carry a prose persona lens (the lens would not be
	// JSON); the driver refuses that combination rather than mangling it.
	Wire() bool
	Render(Request) ([]byte, error)
	Parse(response []byte) (Response, error)
}

// Provider names.
const (
	ProviderLLM = "llm"
	ProviderJev = "jev"
	ProviderPCD = "pcd"
)

// Defaults (registered in go/DEFAULTS.md).
const (
	// DefaultProvider is the primary judgment provider: the existing
	// generative judge over the existing backend, so the default
	// behaviour of the engine is unchanged by this seam.
	DefaultProvider = ProviderLLM
	// JevBaseURL / JevModel / JevKeyName: TypeSafe's System One.
	JevBaseURL = "https://api.typesafe.ai"
	JevModel   = "jev-latest"
	JevKeyName = "TYPESAFE_API_KEY"
	// DefaultPCDURL is the local sidecar (no auth, same wire shape).
	DefaultPCDURL = "http://192.168.0.50:8765"
	PCDModel      = "pcd-latest"
	// DefaultTimeout bounds a wire judgment call.
	DefaultTimeout = 60 * time.Second
	// Path is the System One endpoint both wire providers speak.
	Path = "/v1/systemone"
)

// DefaultShadow is the shadow provider list: EMPTY. A shadow arm spends
// money and reaches the network; it is never on because the code shipped.
func DefaultShadow() []string { return nil }

// Known lists the provider names this binary can build.
func Known() []string { return []string{ProviderLLM, ProviderJev, ProviderPCD} }

// IsWire says whether a provider name is a wire (System One) provider.
// The fold uses it to choose which rendering to re-derive.
func IsWire(name string) bool { return name == ProviderJev || name == ProviderPCD }

// Ask is the one way a judgment call is made: through invoke.Shell, so it
// is prepared, dispatched, terminal-observed and receipted like every
// other call. It returns the parsed response, the outcome (always non-nil
// once an invocation exists), and the error.
func Ask(ctx context.Context, sh *invoke.Shell, p Provider, purpose invoke.Purpose, req Request, timeout time.Duration) (Response, *invoke.Outcome, error) {
	prompt, err := p.Render(req)
	if err != nil {
		return Response{}, nil, err
	}
	o, err := sh.Invoke(ctx, p, invoke.Request{Purpose: purpose, Prompt: prompt, Tools: false, Timeout: timeout}, nil)
	if err != nil {
		return Response{}, o, err
	}
	if o.Terminal == invoke.TerminalFailed {
		return Response{}, o, fmt.Errorf("judgment: %s call failed: %s", p.Name(), o.Reason)
	}
	res, err := p.Parse(o.Response)
	if err != nil {
		return Response{}, o, err
	}
	if err := res.Validate(req); err != nil {
		return Response{}, o, err
	}
	return res, o, nil
}
