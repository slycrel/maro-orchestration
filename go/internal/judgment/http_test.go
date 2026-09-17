package judgment

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
)

// one server stands in for both wire providers: the sidecar IS this
// contract, so a test that pins the request it receives pins what the
// sidecar must accept.
func server(t *testing.T, status int, body string, seen *string, auth *string) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		*seen = string(b)
		*auth = r.Header.Get("Authorization")
		if r.URL.Path != Path {
			t.Errorf("path %s, want %s", r.URL.Path, Path)
		}
		w.WriteHeader(status)
		io.WriteString(w, body)
	}))
	t.Cleanup(s.Close)
	return s
}

func TestHTTPProviderSendsTheWireBodyAndReadsUsage(t *testing.T) {
	var seen, auth string
	srv := server(t, 200, liveResponse, &seen, &auth)
	h := NewJev(func() (string, error) { return "sekret", nil })
	h.BaseURL = srv.URL
	if h.Capabilities().ActsOutward || h.Capabilities().OutwardReconcilable {
		t.Fatal("a judgment provider must declare that it cannot act outward")
	}
	req := Ask1(JevModel, Sect("goal", "g"), "verdict", stepQ())
	prompt, err := h.Render(req)
	if err != nil {
		t.Fatal(err)
	}
	res, err := h.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeJudge, Prompt: prompt}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if res.Terminal != invoke.TerminalComplete {
		t.Fatalf("terminal %s: %s", res.Terminal, res.Reason)
	}
	if seen != string(prompt) {
		t.Fatalf("the body sent is not the prompt recorded:\n sent %s\nprompt %s", seen, prompt)
	}
	if auth != "Bearer sekret" {
		t.Fatalf("authorization header %q", auth)
	}
	if res.Usage.InputTokens != 473 || res.Usage.OutputTokens != 71 || res.Usage.WallMillis < 0 {
		t.Fatalf("usage %+v", res.Usage)
	}
	if res.Usage.CostReported {
		t.Fatal("a provider that reports no cost must not claim one")
	}
	parsed, err := h.Parse(res.Response)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Answers["verdict"].Choice != "done" {
		t.Fatalf("parsed %+v", parsed)
	}
}

func TestPCDNeedsNoKey(t *testing.T) {
	var seen, auth string
	srv := server(t, 200, `{"model":"pcd-0","answers":{"verdict":{"type":"choice","choice":"blocked","confidence":0.4}},"usage":{"input_tokens":1,"output_tokens":2}}`, &seen, &auth)
	p := NewPCD(srv.URL)
	prompt, _ := p.Render(Ask1("", Sect("goal", "g"), "verdict", stepQ()))
	if !strings.Contains(string(prompt), `"model":"`+PCDModel+`"`) {
		t.Fatalf("the provider's model is not in the body: %s", prompt)
	}
	res, err := p.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeShadowJudge, Prompt: prompt}, nil)
	if err != nil || res.Terminal != invoke.TerminalComplete {
		t.Fatalf("%v %+v", err, res)
	}
	if auth != "" {
		t.Fatalf("the sidecar was sent an authorization header: %q", auth)
	}
}

func TestHTTPFailuresAreRecordedNotRaised(t *testing.T) {
	var seen, auth string
	srv := server(t, 429, `{"error":"slow down"}`, &seen, &auth)
	h := NewPCD(srv.URL)
	prompt, _ := h.Render(Ask1("", Sect("goal", "g"), "verdict", stepQ()))
	res, err := h.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeShadowJudge, Prompt: prompt}, nil)
	if err != nil {
		t.Fatalf("a 4xx must be a failed terminal, not an error: %v", err)
	}
	if res.Terminal != invoke.TerminalFailed || !strings.Contains(res.Reason, "429") {
		t.Fatalf("terminal %s reason %q", res.Terminal, res.Reason)
	}
}

func TestAToolBearingRequestIsRefusedBeforeDispatch(t *testing.T) {
	h := NewPCD("http://127.0.0.1:1")
	_, err := h.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("{}"), Tools: true}, nil)
	if err == nil {
		t.Fatal("a tool-bearing request was accepted by a tool-less provider")
	}
}

func TestAMissingKeyFailsBeforeDispatch(t *testing.T) {
	h := NewJev(func() (string, error) { return "", nil })
	h.BaseURL = "http://127.0.0.1:1"
	_, err := h.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("{}")}, nil)
	if err == nil || !strings.Contains(err.Error(), JevKeyName) {
		t.Fatalf("want a before-dispatch failure naming the secret: %v", err)
	}
}

// The provider's timeout is a CEILING over the caller's budget. Review r1
// (all four lenses): the executor's 20-minute request timeout rode every
// judge request and overrode the registered one-minute judgment default,
// so a stalled shadow could hold delivery for twenty minutes per call.
func TestTheProviderTimeoutIsACeilingOverTheRequestBudget(t *testing.T) {
	stall := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(5 * time.Second):
		}
	}))
	t.Cleanup(stall.Close)
	h := NewPCD(stall.URL)
	h.Timeout = 50 * time.Millisecond
	prompt, _ := h.Render(Ask1(PCDModel, Sect("goal", "g"), "verdict", stepQ()))
	start := time.Now()
	res, err := h.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeShadowJudge, Prompt: prompt, Timeout: 20 * time.Minute}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if res.Terminal != invoke.TerminalFailed || time.Since(start) > 2*time.Second {
		t.Fatalf("terminal %s after %s: %s", res.Terminal, time.Since(start), res.Reason)
	}
}

// An endpoint (or a proxy in front of it) that echoes the Authorization
// header must not land the key in a recorded reason. Review r1: the scrub
// matched only a literal "bearer " prefix, so `invalid token: <key>`
// survived into the failed terminal's reason.
func TestAnEchoedKeyNeverLeavesTheProvider(t *testing.T) {
	var seen, auth string
	srv := server(t, 401, `{"error":"invalid token: sekret-value-123 (header was Bearer sekret-value-123)"}`, &seen, &auth)
	h := NewJev(func() (string, error) { return "sekret-value-123", nil })
	h.BaseURL = srv.URL
	prompt, _ := h.Render(Ask1(JevModel, Sect("goal", "g"), "verdict", stepQ()))
	res, err := h.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeJudge, Prompt: prompt}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if res.Terminal != invoke.TerminalFailed || strings.Contains(res.Reason, "sekret-value-123") || !strings.Contains(res.Reason, "<redacted>") {
		t.Fatalf("%s: %s", res.Terminal, res.Reason)
	}
	if strings.Contains(string(res.Response), "sekret") || strings.Contains(string(res.Transcript), "sekret") {
		t.Fatal("the key reached a recorded body")
	}
}

// Review r2: the reason was clipped to 200 bytes BEFORE the key was
// scrubbed, so a key straddling the boundary left its prefix behind.
func TestAKeyStraddlingTheClipBoundaryIsStillScrubbed(t *testing.T) {
	key := "sekret-0123456789-0123456789-0123456789-end"
	var seen, auth string
	pad := strings.Repeat("x", 180) // the key begins at byte 190; the marker must survive the 200-byte clip
	srv := server(t, 401, `{"error":"`+pad+key+`"}`, &seen, &auth)
	h := NewJev(func() (string, error) { return key, nil })
	h.BaseURL = srv.URL
	prompt, _ := h.Render(Ask1(JevModel, Sect("goal", "g"), "verdict", stepQ()))
	res, err := h.Complete(context.Background(), invoke.Request{Purpose: invoke.PurposeJudge, Prompt: prompt}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(res.Reason, "sekret-0") || !strings.Contains(res.Reason, "<redacted>") {
		t.Fatalf("reason %q", res.Reason)
	}
}
