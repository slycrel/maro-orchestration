package invoke

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// OpenAIChat is a tool-less backend over an OpenAI-compatible
// chat-completions endpoint: the cheap hosted tier. Gemini's
// OpenAI-compatible surface is the default (the Python engine's
// hosted-free ladder picked gemini-flash-lite first on this same
// corpus, 2026-07-16); groq or any other compatible host is a base
// URL, a model and a key NAME, never a code change.
//
// It cannot act outward and says so: no tools are offered, a
// tool-bearing request is refused before dispatch, and a dispatched
// call found without a terminal reconciles to `abandoned`.
type OpenAIChat struct {
	Name    string // the backend name recorded in the invocation
	BaseURL string // e.g. https://generativelanguage.googleapis.com/v1beta/openai/
	Model   string
	// KeyName is the secrets-store name of the bearer token. The VALUE is
	// never held here: Key resolves it per call and it is never logged.
	KeyName string
	Key     func() (string, error)
	Timeout time.Duration
	Client  *http.Client
	// JSONMode asks for response_format json_object. An endpoint that
	// refuses the field is retried once without it (and the answer is
	// parsed just as strictly either way) — the caller's strictness, not
	// the provider's flag, is what makes a reply usable.
	JSONMode bool
}

// NewOpenAIChat builds the hosted backend.
func NewOpenAIChat(name, baseURL, model, keyName string, key func() (string, error)) *OpenAIChat {
	return &OpenAIChat{Name: name, BaseURL: baseURL, Model: model, KeyName: keyName, Key: key, Timeout: 60 * time.Second, JSONMode: true}
}

func (o *OpenAIChat) Capabilities() Capabilities {
	return Capabilities{Name: o.Name, Model: o.Model, ActsOutward: false, OutwardReconcilable: false, ReadsByReference: false}
}

// URL is the endpoint, safe to print.
func (o *OpenAIChat) URL() string {
	return strings.TrimRight(o.BaseURL, "/") + "/chat/completions"
}

func (o *OpenAIChat) body(prompt []byte, jsonMode bool) []byte {
	m := map[string]any{
		"model":       o.Model,
		"messages":    []any{map[string]string{"role": "user", "content": string(prompt)}},
		"temperature": 0,
	}
	if jsonMode {
		m["response_format"] = map[string]string{"type": "json_object"}
	}
	b, _ := json.Marshal(m)
	return b
}

type chatResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
		FinishReason string `json:"finish_reason"`
	} `json:"choices"`
	Usage struct {
		PromptTokens     int64 `json:"prompt_tokens"`
		CompletionTokens int64 `json:"completion_tokens"`
	} `json:"usage"`
}

// Complete posts one user message and returns the assistant's content.
// Every failure is a recorded terminal, never a raised error, except a
// request that could not be built or a missing key (nothing happened).
func (o *OpenAIChat) Complete(ctx context.Context, req Request, sink Sink) (*Result, error) {
	if req.Tools {
		return nil, fmt.Errorf("%w: %s is tool-less", ErrBackendIncapable, o.Name)
	}
	// the backend's timeout is a CEILING over the caller's budget: one
	// chat completion is never a 20-minute wait
	ceiling := o.Timeout
	if ceiling <= 0 {
		ceiling = 60 * time.Second
	}
	timeout := req.Timeout
	if timeout <= 0 || timeout > ceiling {
		timeout = ceiling
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	var key string
	if o.Key != nil {
		k, err := o.Key()
		if err != nil {
			return nil, fmt.Errorf("%w: %s: no key for %s: %v", ErrBeforeDispatch, o.Name, o.KeyName, err)
		}
		if strings.TrimSpace(k) == "" {
			return nil, fmt.Errorf("%w: %s: %s is empty in the secrets store", ErrBeforeDispatch, o.Name, o.KeyName)
		}
		key = k
	}
	start := time.Now()
	res, status, body, err := o.post(ctx, key, req.Prompt, o.JSONMode)
	if err == nil && status == http.StatusBadRequest && o.JSONMode && mentionsResponseFormat(body) {
		// the endpoint does not take response_format: ask again without
		// it, and hold the answer to the same strictness
		res, status, body, err = o.post(ctx, key, req.Prompt, false)
	}
	wall := time.Since(start).Milliseconds()
	usage := Usage{WallMillis: wall}
	// the resolved key is scrubbed from every byte that leaves here — the
	// reason, the transcript the shell stores, the content itself. post
	// redacts the body BEFORE parsing it, so the parsed content and
	// finish_reason are already clean (review r2: redacting the bytes
	// after the parse left the parsed content raw)
	if err != nil {
		return &Result{Terminal: TerminalFailed, Reason: Redact(fmt.Sprintf("%s: %v", o.Name, err), key), Usage: usage}, nil
	}
	if status < 200 || status > 299 {
		return &Result{Terminal: TerminalFailed, Reason: fmt.Sprintf("%s: HTTP %d: %s", o.Name, status, clip(body)), Usage: usage, Transcript: body}, nil
	}
	if len(res.Choices) == 0 {
		return &Result{Terminal: TerminalFailed, Reason: fmt.Sprintf("%s: the reply carries no choice", o.Name), Usage: usage, Transcript: body}, nil
	}
	usage.InputTokens, usage.OutputTokens = res.Usage.PromptTokens, res.Usage.CompletionTokens
	content := res.Choices[0].Message.Content
	term := TerminalComplete
	reason := ""
	if fr := res.Choices[0].FinishReason; fr != "" && fr != "stop" {
		term, reason = TerminalPartial, fmt.Sprintf("%s: finish_reason %s", o.Name, fr)
	}
	if strings.TrimSpace(content) == "" {
		return &Result{Terminal: TerminalFailed, Reason: fmt.Sprintf("%s: empty content", o.Name), Usage: usage, Transcript: body}, nil
	}
	return &Result{Response: []byte(content), Usage: usage, Terminal: term, Reason: reason, Transcript: body}, nil
}

func (o *OpenAIChat) post(ctx context.Context, key string, prompt []byte, jsonMode bool) (chatResponse, int, []byte, error) {
	var out chatResponse
	hreq, err := http.NewRequestWithContext(ctx, http.MethodPost, o.URL(), bytes.NewReader(o.body(prompt, jsonMode)))
	if err != nil {
		return out, 0, nil, err
	}
	hreq.Header.Set("Content-Type", "application/json")
	if key != "" {
		hreq.Header.Set("Authorization", "Bearer "+key)
	}
	cl := o.Client
	if cl == nil {
		cl = &http.Client{}
	}
	resp, err := cl.Do(hreq)
	if err != nil {
		return out, 0, nil, err
	}
	defer resp.Body.Close()
	body, rerr := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	// redact first, parse second: nothing derived from the body can
	// carry the key
	body = []byte(Redact(string(body), key))
	if rerr != nil {
		return out, resp.StatusCode, body, fmt.Errorf("%s", Redact(rerr.Error(), key))
	}
	_ = json.Unmarshal(body, &out) // a non-2xx body is not a chat response; the status decides
	return out, resp.StatusCode, body, nil
}

func mentionsResponseFormat(body []byte) bool {
	return strings.Contains(strings.ToLower(string(body)), "response_format")
}

// minRedactedKey is the shortest key value Redact replaces by value.
const minRedactedKey = 8

// clip is a bounded, single-line quote of an error body.
func clip(b []byte) string {
	s := strings.TrimSpace(strings.ReplaceAll(string(b), "\n", " "))
	if len(s) > 200 {
		s = s[:200] + "…"
	}
	return s
}

// Redact removes a credential from text that will be recorded or printed:
// every occurrence of the key's VALUE (the only thing that matters) and,
// belt and braces, whatever follows a "Bearer " prefix. An empty key
// redacts only the prefix form; so does a key shorter than
// minRedactedKey — a one-letter "key" is a test artifact, and replacing
// every "k" in a JSON body is a mangled body, not a redaction.
func Redact(s, key string) string {
	if key = strings.TrimSpace(key); len(key) >= minRedactedKey {
		s = strings.ReplaceAll(s, key, "<redacted>")
	}
	const marker = "<redacted>"
	from := 0
	for {
		i := strings.Index(strings.ToLower(s[from:]), "bearer ")
		if i < 0 {
			break
		}
		at := from + i + len("bearer ")
		rest := s[at:]
		if !strings.HasPrefix(rest, marker) {
			end := strings.IndexAny(rest, " \"'\n\r\t,;}")
			if end < 0 {
				end = len(rest)
			}
			s = s[:at] + marker + rest[end:]
		}
		from = at + len(marker)
	}
	return s
}
