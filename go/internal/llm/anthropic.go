package llm

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// Anthropic is the direct Messages-API backend. Second in the box's
// backend order after subprocess; needs ANTHROPIC_API_KEY.
//
// It deliberately does NOT implement llm.AgentToolsCapable, and its
// Complete ignores Options.Tools/AgentTools/Cwd/TranscriptPath — it has
// no agent, no subprocess, and no native tool_use wiring yet. Do not add
// SupportsAgentTools here without consuming those fields (native tools,
// or the simulated protocol via BuildPrompt): claiming the capability
// while dropping them would fabricate "tool-bearing" runs silently
// (adversarial exec review 2026-08-22, Skeptic).
type Anthropic struct {
	APIKey         string
	Model          string // default model id when Options.Model is empty
	BaseURL        string
	HTTPClient     *http.Client
	DefaultTimeout time.Duration
}

const anthropicDefaultModel = "claude-sonnet-4-6"

func NewAnthropic() (*Anthropic, error) {
	key := os.Getenv("ANTHROPIC_API_KEY")
	if key == "" {
		return nil, errors.New("ANTHROPIC_API_KEY not set")
	}
	return &Anthropic{
		APIKey:         key,
		Model:          anthropicDefaultModel,
		BaseURL:        "https://api.anthropic.com",
		HTTPClient:     &http.Client{},
		DefaultTimeout: 120 * time.Second,
	}, nil
}

func (a *Anthropic) Name() string { return "anthropic" }

func (a *Anthropic) Complete(ctx context.Context, msgs []Message, opts Options) (*Response, error) {
	model := opts.Model
	if model == "" {
		model = a.Model
	}
	maxTokens := opts.MaxTokens
	if maxTokens == 0 {
		maxTokens = 2048
	}

	var system string
	apiMsgs := make([]map[string]string, 0, len(msgs))
	for _, m := range msgs {
		if m.Role == "system" {
			if system != "" {
				system += "\n\n"
			}
			system += m.Content
			continue
		}
		apiMsgs = append(apiMsgs, map[string]string{"role": m.Role, "content": m.Content})
	}

	body := map[string]any{
		"model":       model,
		"max_tokens":  maxTokens,
		"messages":    apiMsgs,
		"temperature": opts.Temperature,
	}
	if system != "" {
		body["system"] = system
	}
	payload, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	timeout := opts.Timeout
	if timeout == 0 {
		timeout = a.DefaultTimeout
	}
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(cctx, http.MethodPost,
		a.BaseURL+"/v1/messages", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("content-type", "application/json")
	req.Header.Set("x-api-key", a.APIKey)
	req.Header.Set("anthropic-version", "2023-06-01")

	resp, err := a.HTTPClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("anthropic request (purpose=%s): %w", opts.Purpose, err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if err != nil {
		return nil, fmt.Errorf("read anthropic response: %w", err)
	}
	if resp.StatusCode != http.StatusOK {
		// The whole API error body travels (it is JSON, small, and the
		// diagnostic); the record boundary applies the marked clip.
		return nil, fmt.Errorf("anthropic HTTP %d: %s", resp.StatusCode,
			strings.TrimSpace(string(raw)))
	}

	var parsed struct {
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
		Usage struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("parse anthropic response: %w", err)
	}
	var text string
	for _, block := range parsed.Content {
		if block.Type == "text" {
			text += block.Text
		}
	}
	return &Response{
		Content:   text,
		TokensIn:  parsed.Usage.InputTokens,
		TokensOut: parsed.Usage.OutputTokens,
	}, nil
}
