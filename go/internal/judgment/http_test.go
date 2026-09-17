package judgment

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

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
