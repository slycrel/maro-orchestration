package invoke

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func chatServer(t *testing.T, handler func(body map[string]any, w http.ResponseWriter)) *httptest.Server {
	t.Helper()
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/chat/completions") {
			t.Errorf("path %s", r.URL.Path)
		}
		b, _ := io.ReadAll(r.Body)
		var m map[string]any
		if err := json.Unmarshal(b, &m); err != nil {
			t.Errorf("body: %v", err)
		}
		handler(m, w)
	}))
	t.Cleanup(s.Close)
	return s
}

// The hosted tier asks at temperature 0 in JSON mode and returns the
// assistant's content as the response; usage is what the endpoint said.
func TestOpenAIChatAsksDeterministicallyAndReportsUsage(t *testing.T) {
	var seen map[string]any
	var auth string
	srv := chatServer(t, func(body map[string]any, w http.ResponseWriter) {
		seen = body
		io.WriteString(w, `{"choices":[{"message":{"content":"{\"outcome\":1}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":11,"completion_tokens":3}}`)
	})
	o := NewOpenAIChat("hosted", srv.URL, "gemini-flash-lite-latest", "GEMINI_API_KEY", func() (string, error) { return "k", nil })
	o.Client = srv.Client()
	oldRT := o.Client.Transport
	o.Client.Transport = headerSpy{oldRT, &auth}
	res, err := o.Complete(context.Background(), Request{Purpose: PurposeJudge, Prompt: []byte("judge this")}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if res.Terminal != TerminalComplete || string(res.Response) != `{"outcome":1}` {
		t.Fatalf("%+v", res)
	}
	if res.Usage.InputTokens != 11 || res.Usage.OutputTokens != 3 {
		t.Fatalf("usage %+v", res.Usage)
	}
	if seen["model"] != "gemini-flash-lite-latest" || seen["temperature"] != float64(0) {
		t.Fatalf("request %v", seen)
	}
	if rf, ok := seen["response_format"].(map[string]any); !ok || rf["type"] != "json_object" {
		t.Fatalf("no json mode: %v", seen)
	}
	if o.Capabilities().ActsOutward {
		t.Fatal("the hosted tier must declare that it cannot act outward")
	}
	if auth != "Bearer k" {
		t.Fatalf("authorization %q", auth)
	}
}

type headerSpy struct {
	rt   http.RoundTripper
	auth *string
}

func (h headerSpy) RoundTrip(r *http.Request) (*http.Response, error) {
	*h.auth = r.Header.Get("Authorization")
	if h.rt == nil {
		return http.DefaultTransport.RoundTrip(r)
	}
	return h.rt.RoundTrip(r)
}

// An endpoint that refuses response_format is asked again without it —
// once, and the answer is held to the same strictness by the caller.
func TestOpenAIChatRetriesWithoutJSONMode(t *testing.T) {
	calls := 0
	srv := chatServer(t, func(body map[string]any, w http.ResponseWriter) {
		calls++
		if _, ok := body["response_format"]; ok {
			w.WriteHeader(http.StatusBadRequest)
			io.WriteString(w, `{"error":{"message":"response_format is not supported"}}`)
			return
		}
		io.WriteString(w, `{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}],"usage":{}}`)
	})
	o := NewOpenAIChat("hosted", srv.URL, "m", "", nil)
	res, err := o.Complete(context.Background(), Request{Purpose: PurposeJudge, Prompt: []byte("x")}, nil)
	if err != nil || res.Terminal != TerminalComplete || string(res.Response) != "ok" || calls != 2 {
		t.Fatalf("%v %+v calls=%d", err, res, calls)
	}
}

// Failures are recorded terminals, never raised errors; a tool-bearing
// request never reaches the wire at all.
func TestOpenAIChatFailuresAreTerminals(t *testing.T) {
	srv := chatServer(t, func(body map[string]any, w http.ResponseWriter) {
		w.WriteHeader(http.StatusTooManyRequests)
		io.WriteString(w, `{"error":"rate limited"}`)
	})
	o := NewOpenAIChat("hosted", srv.URL, "m", "", nil)
	res, err := o.Complete(context.Background(), Request{Purpose: PurposeJudge, Prompt: []byte("x")}, nil)
	if err != nil || res.Terminal != TerminalFailed || !strings.Contains(res.Reason, "429") {
		t.Fatalf("%v %+v", err, res)
	}
	if _, err := o.Complete(context.Background(), Request{Purpose: PurposeExecute, Prompt: []byte("x"), Tools: true}, nil); err == nil {
		t.Fatal("a tool-bearing request was accepted")
	}
	empty := chatServer(t, func(body map[string]any, w http.ResponseWriter) {
		io.WriteString(w, `{"choices":[],"usage":{}}`)
	})
	o2 := NewOpenAIChat("hosted", empty.URL, "m", "", nil)
	res2, err := o2.Complete(context.Background(), Request{Purpose: PurposeJudge, Prompt: []byte("x")}, nil)
	if err != nil || res2.Terminal != TerminalFailed || !strings.Contains(res2.Reason, "no choice") {
		t.Fatalf("%v %+v", err, res2)
	}
}

// A missing key fails before dispatch: nothing happened, and the message
// names the secret, never its value.
func TestOpenAIChatWithoutAKeyFailsBeforeDispatch(t *testing.T) {
	o := NewOpenAIChat("hosted", "http://127.0.0.1:1", "m", "GEMINI_API_KEY", func() (string, error) { return "", nil })
	_, err := o.Complete(context.Background(), Request{Purpose: PurposeJudge, Prompt: []byte("x")}, nil)
	if err == nil || !strings.Contains(err.Error(), "GEMINI_API_KEY") {
		t.Fatalf("%v", err)
	}
}
